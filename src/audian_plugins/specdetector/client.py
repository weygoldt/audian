"""The audian side of `modelrunner`: one child process, as Qt signals.

Only `PySide6.QtCore` here, no widgets.  The shape is `eodsorter.runner.RunnerClient`'s
and deliberately not an import of it: plugins do not depend on each other, so that
each can leave this tree alone.

The panel holds two of these.  One runs what takes minutes, the other runs SAM, so
that a reader can go on labelling while a model trains.
"""

from __future__ import annotations

import collections
import json
import os
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

#: The script the runner process runs.
MODELRUNNER = os.fspath(Path(__file__).with_name("modelrunner.py"))

#: The protocol version this client speaks (``hello.protocol``).
PROTOCOL = 1

#: Stderr lines kept for error reports.
STDERR_LINES = 200

#: How long `cancel` waits after SIGTERM before SIGKILL.
KILL_AFTER_MS = 2000


class RunnerError(RuntimeError):
    """`submit` cannot start a job: busy, or still stopping."""


class RunnerClient(QObject):
    """One runner process, started on the first job and kept between jobs.

    Every signal is emitted on this object's thread, the GUI thread.  A job ends in
    exactly one of `sigResult` and `sigError`.  ``kind`` is one of ``bad_request``,
    ``input``, ``oom``, ``exception``, ``startup``, ``protocol`` or ``cancelled``.

    `program` and `script` replace the interpreter and the script, for tests only.
    """

    sigState = Signal(str)
    sigHello = Signal(dict)
    sigProgress = Signal(str, str, int, int, str)
    #: ``(job, point)`` after each epoch of a training: the numbers of its curve
    sigMetrics = Signal(str, dict)
    sigResult = Signal(str, dict)
    sigError = Signal(str, str, str)
    sigLog = Signal(str, str)

    def __init__(
        self,
        weights_dir,
        parent: QObject | None = None,
        *,
        program: str | None = None,
        script: str | None = None,
    ):
        super().__init__(parent)
        self.weights_dir = os.fspath(weights_dir)
        self._program = program or sys.executable
        self._script = script or MODELRUNNER
        self.state = "stopped"
        self.hello: dict | None = None
        self.job: str | None = None
        self.last_error: dict | None = None
        self._pending: str | None = None
        self._proc: QProcess | None = None
        self._out = b""
        self._err = b""
        self._tail: collections.deque = collections.deque(maxlen=STDERR_LINES)
        self._counter = 0
        self._cancelling = False
        self._reported = False
        self._kill_timer = QTimer(self)
        self._kill_timer.setSingleShot(True)
        self._kill_timer.timeout.connect(self._kill)

    # -- public --------------------------------------------------------

    def ensure_started(self) -> None:
        """Start the runner if it is not running.  A failure arrives as `sigError`."""
        if self._proc is not None:
            return
        proc = QProcess(self)
        proc.setProgram(self._program)
        proc.setArguments(["-u", self._script, "--weights", self.weights_dir])
        proc.readyReadStandardOutput.connect(self._read_stdout)
        proc.readyReadStandardError.connect(self._read_stderr)
        proc.finished.connect(self._finished)
        proc.errorOccurred.connect(self._process_error)
        self._proc = proc
        self._out = b""
        self._err = b""
        self._tail.clear()
        self._cancelling = False
        self._reported = False
        self.hello = None
        self._set_state("starting")
        proc.start()

    def submit(self, op: str, **params) -> str:
        """Start a job and return its id.  `RunnerError` when one is running."""
        if self.job is not None:
            raise RunnerError("the model runner is busy with another job")
        if self._proc is not None and self._cancelling:
            raise RunnerError("the model runner is still stopping")
        self.ensure_started()
        if self._proc is None:  # failed synchronously; already reported
            error = self.last_error or {}
            raise RunnerError(error.get("message") or "cannot start the model runner")
        self._counter += 1
        job = f"j{self._counter}"
        self.job = job
        self.last_error = None
        line = json.dumps({**params, "id": job, "op": op}) + "\n"
        if self.hello is None:
            self._pending = line
        else:
            self._write(line)
        self._set_state("busy")
        return job

    def busy(self) -> bool:
        return self.job is not None

    def cancel(self) -> None:
        """Terminate the runner (SIGTERM, then SIGKILL).

        The running job ends with a ``cancelled`` error; the next job starts a new
        process.  There is no gentler way to stop a training epoch from outside, and
        a job writes nothing a kill could leave half-finished.
        """
        if self._proc is None:
            return
        self._cancelling = True
        self._pending = None
        self._proc.terminate()
        self._kill_timer.start(KILL_AFTER_MS)

    def shutdown(self, timeout_ms: int = 2000) -> None:
        """Ask the runner to exit, and wait for it; kill it after `timeout_ms`.

        Called on quit: a `QProcess` left to its destructor at interpreter exit has
        crashed this application before (`eodsorter.panel.about_to_flush_labels`).
        """
        proc = self._proc
        if proc is None:
            return
        self._cancelling = True
        if proc.state() == QProcess.ProcessState.Running:
            try:
                proc.write(b'{"op": "shutdown"}\n')
                proc.closeWriteChannel()
            except RuntimeError:
                pass
            if self.job is not None:
                proc.terminate()
            if not proc.waitForFinished(timeout_ms):
                proc.kill()
                proc.waitForFinished(1000)
        elif proc.state() == QProcess.ProcessState.Starting:
            proc.kill()
            proc.waitForFinished(1000)
        if self._proc is proc:
            self._finished(proc.exitCode(), proc.exitStatus())

    def stderr_tail(self) -> str:
        return "\n".join(self._tail)

    def is_running(self) -> bool:
        return self._proc is not None

    # -- process plumbing ----------------------------------------------

    def _set_state(self, state: str) -> None:
        if state != self.state:
            self.state = state
            self.sigState.emit(state)

    def _write(self, line: str) -> None:
        if self._proc is not None:
            self._proc.write(line.encode("utf-8"))

    def _kill(self) -> None:
        if (
            self._proc is not None
            and self._proc.state() != QProcess.ProcessState.NotRunning
        ):
            self._proc.kill()

    def _read_stdout(self) -> None:
        proc = self._proc
        if proc is None:
            return
        self._out += bytes(proc.readAllStandardOutput())
        *lines, self._out = self._out.split(b"\n")
        for raw in lines:
            text = raw.decode("utf-8", "replace").strip()
            if not text:
                continue
            try:
                message = json.loads(text)
            except ValueError:
                message = None
            if not isinstance(message, dict):
                self.sigLog.emit("warning", text)
                continue
            self._dispatch(message)

    def _read_stderr(self) -> None:
        proc = self._proc
        if proc is None:
            return
        self._err += bytes(proc.readAllStandardError())
        *lines, self._err = self._err.split(b"\n")
        for raw in lines:
            text = raw.decode("utf-8", "replace").rstrip()
            self._tail.append(text)
            if text:
                self.sigLog.emit("debug", text)

    def _dispatch(self, message: dict) -> None:
        kind = message.get("type")
        if kind == "hello":
            first = self.hello is None
            if message.get("protocol") != PROTOCOL:
                self._fail(
                    self.job or "",
                    "protocol",
                    f"the model runner speaks protocol {message.get('protocol')}, "
                    f"this panel speaks {PROTOCOL}",
                )
                self._cancelling = True
                if self._proc is not None:
                    self._proc.kill()
                return
            self.hello = message
            self.sigHello.emit(message)
            if first:
                if self._pending is not None:
                    line, self._pending = self._pending, None
                    self._write(line)
                elif self.job is None:
                    self._set_state("idle")
        elif kind == "progress":
            total = message.get("total")
            self.sigProgress.emit(
                str(message.get("id") or ""),
                str(message.get("stage") or ""),
                int(message.get("done") or 0),
                int(total if total is not None else -1),
                str(message.get("text") or ""),
            )
        elif kind == "metrics":
            values = message.get("values")
            if isinstance(values, dict):
                point = {
                    str(k): v for k, v in values.items() if isinstance(v, (int, float))
                }
                point["epochs"] = int(message.get("epochs") or 0)
                self.sigMetrics.emit(str(message.get("id") or ""), point)
        elif kind == "log":
            self.sigLog.emit(
                str(message.get("level") or "info"), str(message.get("text") or "")
            )
        elif kind == "result":
            job = str(message.get("id") or "")
            if self.job is not None and job == self.job:
                self.job = None
                self._set_state("idle")
            self.sigResult.emit(job, message)
        elif kind == "error":
            job = message.get("id")
            if job is None and message.get("kind") == "startup":
                self._fail(
                    self.job or "", "startup", str(message.get("message")), message
                )
                return
            job = str(job or "")
            message = {**message, "stderr_tail": self.stderr_tail()}
            self.last_error = message
            if self.job is not None and job == self.job:
                self.job = None
                self._set_state("idle")
            self.sigError.emit(
                job, str(message.get("kind")), str(message.get("message"))
            )
        else:
            self.sigLog.emit("warning", f"unknown runner message: {message}")

    def _fail(self, job: str, kind: str, text: str, message: dict | None = None):
        """An error that ends the process: startup, protocol, crash."""
        if self._reported:
            return
        self._reported = True
        self.last_error = {
            **(message or {}),
            "type": "error",
            "id": job,
            "kind": kind,
            "message": text,
            "stderr_tail": self.stderr_tail(),
        }
        self.job = None
        self._pending = None
        self._set_state("failed")
        self.sigError.emit(job, kind, text)

    def _process_error(self, error) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            proc = self._proc
            path = proc.program() if proc is not None else "?"
            self._fail(
                self.job or "",
                "startup",
                f"cannot start {path}: {proc.errorString() if proc else error}",
            )
            self._reset_process()

    def _finished(self, code: int, status) -> None:
        proc = self._proc
        if proc is None:
            return
        self._read_stdout()
        self._read_stderr()
        if self._err:
            self._tail.append(self._err.decode("utf-8", "replace"))
            self._err = b""
        self._kill_timer.stop()
        job = self.job
        if self._reported:
            pass
        elif self._cancelling:
            if job is not None:
                self.last_error = {
                    "type": "error",
                    "id": job,
                    "kind": "cancelled",
                    "message": "cancelled",
                    "stderr_tail": self.stderr_tail(),
                }
                self.job = None
                self.sigError.emit(job, "cancelled", "cancelled")
        elif job is not None or self.hello is None:
            crashed = status == QProcess.ExitStatus.CrashExit
            what = "crashed" if crashed else f"exited with code {code}"
            tail = self.stderr_tail().strip().splitlines()[-5:]
            text = f"the model runner {what}" + (
                ": " + " / ".join(tail) if tail else ""
            )
            self._fail(
                job or "", "startup" if self.hello is None else "exception", text
            )
        failed = self.state == "failed"
        self._reset_process()
        if not failed:
            self._set_state("stopped")

    def _reset_process(self) -> None:
        proc, self._proc = self._proc, None
        self.job = None
        self._pending = None
        self._cancelling = False
        self._reported = False
        if proc is not None:
            for signal, slot in (
                (proc.readyReadStandardOutput, self._read_stdout),
                (proc.readyReadStandardError, self._read_stderr),
                (proc.finished, self._finished),
                (proc.errorOccurred, self._process_error),
            ):
                try:
                    signal.disconnect(slot)
                except (RuntimeError, TypeError):
                    pass
            proc.deleteLater()
