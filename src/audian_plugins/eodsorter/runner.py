"""Running wavetracker out of process: interpreter, client, snippet export.

wavetracker holds the GIL for its whole run and pulls in torch, so it never
runs in audian's process.  `wtrunner.py`, next to this module, is executed
by a Python interpreter that has wavetracker installed and talks JSON lines
(docs/eodsorter-design.md, section 4.4).  This module is the audian side:

* `find_interpreter` picks that interpreter (setting, this process, or
  ``WAVETRACKER_PYTHON``);
* `RunnerClient` owns the runner process (a `QProcess`) and turns its
  messages into Qt signals;
* `SnippetExporter` writes the visible span of the recording, read through
  audian's own `open_files`, into a WAV file the runner can track;
* `detect_job`, `peaks_job` and `cleanup_job` build the requests, to be
  passed as ``client.submit(**job)``.

Only `PySide6.QtCore` is imported at module level (no widgets); the
`SnippetExporter` imports `audian.pluginapi.open_files` and `soundfile` when
it runs.

Additions to the interface of section 7.2 (nothing there is changed):

* error kinds besides those of 4.4: ``startup`` (the interpreter could not
  be started, or cannot import numpy/wavetracker), ``protocol`` (the runner
  speaks another protocol version) and ``cancelled``;
* ``RunnerClient.last_error`` holds the last error message as a dict, with
  the runner's ``traceback`` and the client's ``stderr_tail``;
  `RunnerClient.stderr_tail()` returns the last 200 stderr lines;
* ``RunnerClient.job`` is the id of the running job (or None);
* helpers `default_output_dir`, `partial_dir`, `move_aside`,
  `make_tmpdir`, `default_mem_limit` and `WTRUNNER` (the script's path);
* `SnippetExporter` writes ``PCM_32`` instead of ``FLOAT`` when the samples
  fit in (-1, 1) (design 4.3 says FLOAT): wavetracker reads through
  audioio, which without soundfile falls back to `wave` and cannot stream
  float WAV ("unknown format: 3") -- measured with wavetracker's own venv.
  The quantisation step is 2**-31 of full scale (2.3e-10), far below any
  recording's noise; data outside (-1, 1) still go out as FLOAT;
* the runner removes its ``*.partial-*`` output directory when a whole run
  fails or is terminated, and the client removes it as well after a cancel
  or a crash, so the panel need not.
"""

from __future__ import annotations

import collections
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

#: the script the interpreter runs
WTRUNNER = str(Path(__file__).with_name("wtrunner.py"))

#: the protocol version this client speaks (``hello.protocol``)
PROTOCOL = 1

#: snippets longer than this are refused (design 4.3)
MAX_SNIPPET_S = 300.0

#: stderr lines kept for error reports
STDERR_LINES = 200

#: how long `cancel` waits after SIGTERM before SIGKILL
KILL_AFTER_MS = 2000

STATES = ("stopped", "starting", "idle", "busy", "failed")


# ------------------------------------------------------------ interpreter


@dataclass(frozen=True)
class Interpreter:
    path: str
    source: str  # "setting" | "sys" | "env" | "path"


def script_interpreter(script: str) -> str | None:
    """The Python a console script such as ``wavetracker`` runs with.

    pip and uv write the interpreter into the script's first line: either
    ``#!/abs/python`` or, for paths too long for a shebang, a ``/bin/sh``
    preamble whose second line is ``'''exec' "/abs/python" "$0" "$@"``.
    ``#!/usr/bin/env python3`` is looked up on PATH.  None when the file is
    not such a script (a binary, a Windows launcher, unreadable).
    """
    try:
        with open(script, "rb") as fh:
            head = fh.read(1024).decode("utf-8", "replace").splitlines()
    except OSError:
        return None
    if not head or not head[0].startswith("#!"):
        return None
    words = head[0][2:].strip().split()
    if not words:
        return None
    name = os.path.basename(words[0])
    if name == "env":
        args = [w for w in words[1:] if not w.startswith("-")]
        return shutil.which(args[0]) if args else None
    if name in ("sh", "bash"):
        for line in head[1:4]:
            if line.startswith("'''exec'"):
                parts = line.split('"')
                if len(parts) > 1 and parts[1]:
                    return parts[1]
        return None
    return words[0]


def find_interpreter(explicit: str | None) -> Interpreter | None:
    """The Python that runs `wtrunner.py`, or None.

    1. `explicit` (the panel setting), unless empty or "Automatic";
    2. this process's interpreter, if it can import wavetracker (checked
       with `importlib.util.find_spec`, which does not import it);
    3. the environment variable ``WAVETRACKER_PYTHON``;
    4. the interpreter of a ``wavetracker`` command on PATH, read from its
       first line: a wavetracker installed into its own venv (or with
       ``uv tool``/``pipx``) is found without any setting.

    The candidate is not trusted: the runner's ``hello`` confirms it.
    """
    if explicit is not None:
        text = str(explicit).strip()
        if text and text.lower() != "automatic":
            return Interpreter(os.path.expanduser(text), "setting")
    try:
        found = importlib.util.find_spec("wavetracker") is not None
    except (ImportError, ValueError):
        found = False
    if found:
        return Interpreter(sys.executable, "sys")
    env = os.environ.get("WAVETRACKER_PYTHON", "").strip()
    if env:
        return Interpreter(os.path.expanduser(env), "env")
    script = shutil.which("wavetracker")
    if script:
        python = script_interpreter(script)
        if python and os.path.isfile(python):
            return Interpreter(python, "path")
    return None


# ------------------------------------------------------------ paths


def default_output_dir(first_path: str) -> str:
    """``<dir of first file>/<stem of first file>-wavetracker`` (4.2)."""
    p = Path(first_path)
    return str(p.with_name(f"{p.stem}-wavetracker"))


def partial_dir(final_dir: str) -> str:
    """Where a run writes before it is renamed to `final_dir`."""
    return f"{os.fspath(final_dir)}.partial-{os.getpid()}"


def move_aside(final_dir: str) -> str:
    """Rename an existing results directory to ``<name>.old-<stamp>``; never
    deletes.  Returns the new name."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = f"{os.fspath(final_dir)}.old-{stamp}"
    n = 1
    while os.path.exists(target):
        target = f"{os.fspath(final_dir)}.old-{stamp}-{n}"
        n += 1
    os.replace(final_dir, target)
    return target


def make_tmpdir() -> str:
    """A fresh temporary directory for snippet runs and cleanup."""
    return tempfile.mkdtemp(prefix="audian-wt-")


def default_mem_limit() -> int | None:
    """Half the physical memory in bytes (the cleanup default), or None."""
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") // 2
    except (ValueError, OSError, AttributeError):
        return None


# ------------------------------------------------------------ jobs


def _input(paths):
    if isinstance(paths, (list, tuple)):
        paths = [os.fspath(p) for p in paths]
        return paths[0] if len(paths) == 1 else paths
    return os.fspath(paths)


def detect_job(
    input,
    output_dir,
    final_dir,
    config,
    config_path,
    start,
    duration,
    device,
    track=True,
) -> dict:
    """A ``detect`` request: detect, track (if `track`), save into
    `output_dir`, and rename it to `final_dir` (if given)."""
    return {
        "op": "detect",
        "input": _input(input),
        "output_dir": os.fspath(output_dir),
        "final_dir": None if final_dir is None else os.fspath(final_dir),
        "config_path": None if config_path is None else os.fspath(config_path),
        "config": dict(config or {}),
        "start": float(start or 0.0),
        "duration": None if duration is None else float(duration),
        "device": device or "auto",
        "track": bool(track),
    }


def peaks_job(input, grid, channels, frames, fmin, fmax, out, device) -> dict:
    """A ``peaks`` request on the frames of `grid` (a `FrameGrid` or anything
    with ``nfft``, ``step`` and ``s0``)."""

    def floats(a):
        return [float(x) for x in a]

    return {
        "op": "peaks",
        "input": _input(input),
        "nfft": int(grid.nfft),
        "step": int(grid.step),
        "s0": int(grid.s0),
        "channels": None if channels is None else [int(c) for c in channels],
        "frames": [int(k) for k in frames],
        "fmin": floats(fmin),
        "fmax": floats(fmax),
        "out": os.fspath(out),
        "device": device or "auto",
    }


def cleanup_job(dir, n_fish, params, mem_limit_bytes) -> dict:
    """A ``cleanup`` request; run it with a ``oneshot`` client."""
    return {
        "op": "cleanup",
        "dir": os.fspath(dir),
        "n_fish": None if n_fish is None else int(n_fish),
        "params": dict(params or {}),
        "mem_limit_bytes": None if mem_limit_bytes is None else int(mem_limit_bytes),
    }


# ------------------------------------------------------------ client


class RunnerError(RuntimeError):
    """`submit` cannot start a job (busy, or no interpreter)."""


class RunnerClient(QObject):
    """One runner process, started on the first job and kept between jobs.

    Every signal is emitted on the thread this object lives on (the GUI
    thread).  Errors of a job arrive as `sigError`; ``kind`` is one of
    ``bad_request``, ``input``, ``oom``, ``exception``, ``startup``,
    ``protocol`` or ``cancelled``.  An error that belongs to no job (the
    runner failed to start) has job ``""``.
    """

    sigState = Signal(str)
    sigHello = Signal(dict)
    sigProgress = Signal(str, str, int, int, str)
    sigResult = Signal(str, dict)
    sigError = Signal(str, str, str)
    sigLog = Signal(str, str)

    def __init__(
        self,
        interpreter: Callable[[], Interpreter | None],
        oneshot: bool = False,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self._interpreter = interpreter
        self.oneshot = oneshot
        self.state = "stopped"
        self.hello: dict | None = None
        self.job: str | None = None
        self.last_error: dict | None = None
        self._params: dict = {}
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
        """Start the runner if it is not running; ``hello`` arrives as
        `sigHello`.  A failure to start is reported through `sigError`."""
        if self._proc is not None:
            return
        interp = self._interpreter()
        if interp is None:
            raise RunnerError(
                "No Python with wavetracker: set it in the panel, or set "
                "WAVETRACKER_PYTHON"
            )
        proc = QProcess(self)
        proc.setProgram(interp.path)
        args = ["-u", WTRUNNER]
        if self.oneshot:
            args.append("--oneshot")
        proc.setArguments(args)
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
        """Start a job; returns its id.  Raises `RunnerError` when a job is
        running or there is no interpreter."""
        if self.job is not None:
            raise RunnerError("wavetracker is busy with another job")
        if self._proc is not None and self._cancelling:
            raise RunnerError("wavetracker is still stopping")
        self.ensure_started()
        if self._proc is None:  # failed synchronously; already reported
            err = self.last_error or {}
            raise RunnerError(err.get("message") or "cannot start wavetracker")
        self._counter += 1
        job = f"j{self._counter}"
        msg = {**params, "id": job, "op": op}
        self.job = job
        self._params = msg
        self.last_error = None
        line = json.dumps(msg) + "\n"
        if self.hello is None:
            self._pending = line
        else:
            self._write(line)
        self._set_state("busy")
        return job

    def cancel(self) -> None:
        """Terminate the runner (SIGTERM, then SIGKILL after 2 s).  The
        running job ends with a ``cancelled`` error; the next job starts a
        new process."""
        if self._proc is None:
            return
        self._cancelling = True
        self._pending = None
        self._proc.terminate()
        self._kill_timer.start(KILL_AFTER_MS)

    def shutdown(self, timeout_ms: int = 2000) -> None:
        """Ask the runner to exit, and wait for it (kill after `timeout_ms`)."""
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
            if not proc.waitForFinished(timeout_ms):
                proc.kill()
                proc.waitForFinished(1000)
        elif proc.state() == QProcess.ProcessState.Starting:
            proc.kill()
            proc.waitForFinished(1000)
        # waitForFinished emits finished synchronously; make sure we are reset
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
                msg = json.loads(text)
            except ValueError:
                msg = None
            if not isinstance(msg, dict):
                self.sigLog.emit("warning", text)
                continue
            self._dispatch(msg)

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

    def _dispatch(self, msg: dict) -> None:
        kind = msg.get("type")
        if kind == "hello":
            first = self.hello is None
            if msg.get("protocol") != PROTOCOL:
                self._fail(
                    self.job or "",
                    "protocol",
                    f"the wavetracker runner speaks protocol {msg.get('protocol')}, "
                    f"this audian speaks {PROTOCOL}: update claudian or wavetracker",
                )
                self._cancelling = True
                if self._proc is not None:
                    self._proc.kill()
                return
            self.hello = msg
            self.sigHello.emit(msg)
            if first:
                if self._pending is not None:
                    line, self._pending = self._pending, None
                    self._write(line)
                elif self.job is None:
                    self._set_state("idle")
        elif kind == "progress":
            self.sigProgress.emit(
                str(msg.get("id") or ""),
                str(msg.get("stage") or ""),
                int(msg.get("done") or 0),
                int(msg.get("total") if msg.get("total") is not None else -1),
                str(msg.get("text") or ""),
            )
        elif kind == "log":
            self.sigLog.emit(
                str(msg.get("level") or "info"), str(msg.get("text") or "")
            )
        elif kind == "result":
            job = str(msg.get("id") or "")
            if self.job is not None and job == self.job:
                self.job = None
                self._params = {}
                self._set_state("idle")
            self.sigResult.emit(job, msg)
        elif kind == "error":
            job = msg.get("id")
            if job is None and msg.get("kind") == "startup":
                self._fail(self.job or "", "startup", str(msg.get("message")), msg)
                return
            job = str(job or "")
            msg = {**msg, "stderr_tail": self.stderr_tail()}
            self.last_error = msg
            if self.job is not None and job == self.job:
                self.job = None
                self._params = {}
                self._set_state("idle")
            self.sigError.emit(job, str(msg.get("kind")), str(msg.get("message")))
        else:
            self.sigLog.emit("warning", f"unknown runner message: {msg}")

    def _fail(self, job: str, kind: str, message: str, msg: dict | None = None):
        """An error that ends the process (startup, protocol, crash)."""
        if self._reported:
            return
        self._reported = True
        self.last_error = {
            **(msg or {}),
            "type": "error",
            "id": job,
            "kind": kind,
            "message": message,
            "stderr_tail": self.stderr_tail(),
        }
        self._remove_partial()
        self.job = None
        self._params = {}
        self._pending = None
        self._set_state("failed")
        self.sigError.emit(job, kind, message)

    def _remove_partial(self) -> None:
        """Remove the output of an unfinished whole-recording run."""
        out = self._params.get("output_dir")
        final = self._params.get("final_dir")
        if out and final and out != final and ".partial-" in os.path.basename(out):
            shutil.rmtree(out, ignore_errors=True)

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
        # drain what is left
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
                self._remove_partial()
                self.last_error = {
                    "type": "error",
                    "id": job,
                    "kind": "cancelled",
                    "message": "cancelled",
                    "stderr_tail": self.stderr_tail(),
                }
                self.job = None
                self._params = {}
                self.sigError.emit(job, "cancelled", "cancelled")
        elif job is not None or self.hello is None:
            crashed = status == QProcess.ExitStatus.CrashExit
            what = "crashed" if crashed else f"exited with code {code}"
            tail = self.stderr_tail().strip().splitlines()[-5:]
            message = f"wavetracker runner {what}" + (
                ": " + " / ".join(tail) if tail else ""
            )
            self._fail(
                job or "", "startup" if self.hello is None else "exception", message
            )
        failed = self.state == "failed"
        self._reset_process()
        if not failed:
            self._set_state("stopped")

    def _reset_process(self) -> None:
        proc, self._proc = self._proc, None
        self.job = None
        self._params = {}
        self._pending = None
        self._cancelling = False
        self._reported = False
        if proc is not None:
            for sig, slot in (
                (proc.readyReadStandardOutput, self._read_stdout),
                (proc.readyReadStandardError, self._read_stderr),
                (proc.finished, self._finished),
                (proc.errorOccurred, self._process_error),
            ):
                try:
                    sig.disconnect(slot)
                except (RuntimeError, TypeError):
                    pass
            proc.deleteLater()


# ------------------------------------------------------------ snippet export


class SnippetExporter(QObject):
    """Write samples ``[s0, s1)`` of a recording into a WAV file.

    The file is 32-bit integer PCM when every sample lies in (-1, 1) and
    32-bit float otherwise (`subtype` says which, after `run`).

    Reads through audian's `open_files` -- its own loader, never
    ``browser.data`` -- so the snippet is exactly the audio audian shows,
    including multi-file joins.  `run` is called on the panel's worker
    thread; it polls `token` between 10 s chunks.  On error or cancel the
    output file is removed and `sigDone` carries the reason ("cancelled"
    for a cancel).
    """

    sigProgress = Signal(int, int)  # samples written, total
    sigDone = Signal(str, str)  # wav path, error ("" on success)

    CHUNK_S = 10.0

    def __init__(self, paths, sample_range, out_path, token, parent=None):
        super().__init__(parent)
        if isinstance(paths, (str, os.PathLike)):
            paths = [paths]
        self.paths = [os.fspath(p) for p in paths]
        self.sample_range = (int(sample_range[0]), int(sample_range[1]))
        self.out_path = os.fspath(out_path)
        self.token = token
        self.subtype: str | None = None

    def run(self) -> None:
        try:
            self._export()
        except Exception as e:
            self._remove()
            name = type(e).__name__
            if name == "Cancelled":
                self.sigDone.emit(self.out_path, "cancelled")
            else:
                self.sigDone.emit(self.out_path, f"{name}: {e}" if str(e) else name)
            return
        self.sigDone.emit(self.out_path, "")

    def _remove(self) -> None:
        try:
            os.remove(self.out_path)
        except OSError:
            pass

    def _export(self) -> None:
        import numpy as np
        import soundfile as sf

        from audian.pluginapi import open_files

        s0, s1 = self.sample_range
        paths = self.paths[0] if len(self.paths) == 1 else self.paths
        loader = open_files(paths, self.CHUNK_S, 0.0)
        try:
            rate = float(loader.rate)
            n = len(loader)
            if not 0 <= s0 < s1 <= n:
                raise ValueError(f"samples {s0}-{s1} are outside the recording (0-{n})")
            if abs(rate - round(rate)) > 1e-6:
                raise ValueError(
                    f"the sampling rate {rate} Hz is not an integer; a WAV "
                    "snippet cannot hold it"
                )
            chunk = max(1, int(self.CHUNK_S * rate))
            total = s1 - s0
            # 32-bit integer PCM when the samples fit: every audio library
            # reads it (a wavetracker environment without soundfile cannot
            # read float WAV), and its 2**-31 step leaves the values, and so
            # absolute dB thresholds, unchanged for all practical purposes.
            peak = 0.0
            for a in range(s0, s1, chunk):
                self.token.check()
                block = np.asarray(loader[a : min(s1, a + chunk)])
                if block.size:
                    peak = max(peak, float(np.nanmax(np.abs(block))))
            self.subtype = "PCM_32" if peak < 1.0 else "FLOAT"
            written = 0
            with sf.SoundFile(
                self.out_path,
                "w",
                samplerate=int(round(rate)),
                channels=int(loader.channels),
                subtype=self.subtype,
                format="WAV",
            ) as f:
                for a in range(s0, s1, chunk):
                    self.token.check()
                    b = min(s1, a + chunk)
                    block = loader[a:b]
                    f.write(block.reshape(b - a, -1))
                    written += b - a
                    self.sigProgress.emit(written, total)
            self.token.check()
        finally:
            loader.close()
