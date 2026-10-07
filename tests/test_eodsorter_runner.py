"""The wavetracker runner: its protocol, the client, and the snippet export.

Nothing here needs torch or wavetracker: `tests/data/fake_wavetracker` is a
stub package put on ``PYTHONPATH`` of the runner process.  The fast tests
talk to `wtrunner.py` through a plain subprocess; the slow ones drive
`RunnerClient` (a `QProcess`) and `SnippetExporter` on the `app` fixture.
"""

from __future__ import annotations

import ast
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from audian_plugins.eodsorter import runner
from audian_plugins.eodsorter.runner import (
    RunnerClient,
    RunnerError,
    cleanup_job,
    detect_job,
    peaks_job,
)

STUB = Path(__file__).resolve().parent / "data" / "fake_wavetracker"
WTRUNNER = runner.WTRUNNER


def stub_env(**extra) -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(STUB)] + [p for p in [env.get("PYTHONPATH")] if p]
    )
    env.update({k: str(v) for k, v in extra.items()})
    return env


class Proc:
    """`wtrunner.py` in a subprocess, with a reader thread per pipe."""

    def __init__(self, *args, env=None):
        self.p = subprocess.Popen(
            [sys.executable, "-u", WTRUNNER, *args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env if env is not None else stub_env(),
        )
        self.out: queue.Queue = queue.Queue()
        self.err: list[str] = []
        threading.Thread(target=self._pump_out, daemon=True).start()
        threading.Thread(target=self._pump_err, daemon=True).start()

    def _pump_out(self):
        for line in self.p.stdout:
            self.out.put(line)
        self.out.put(None)

    def _pump_err(self):
        for line in self.p.stderr:
            self.err.append(line)

    def send(self, msg):
        self.p.stdin.write((msg if isinstance(msg, str) else json.dumps(msg)) + "\n")
        self.p.stdin.flush()

    def next(self, timeout=20.0) -> dict | None:
        line = self.out.get(timeout=timeout)
        return None if line is None else json.loads(line)

    def until(self, job, timeout=20.0) -> list[dict]:
        """Every message up to and including the result or error of `job`."""
        msgs = []
        deadline = time.monotonic() + timeout
        while True:
            msg = self.next(max(0.1, deadline - time.monotonic()))
            assert msg is not None, "runner exited: " + "".join(self.err)
            msgs.append(msg)
            if msg.get("type") in ("result", "error") and msg.get("id") == job:
                return msgs

    def close(self, timeout=10.0) -> int:
        try:
            self.send({"op": "shutdown"})
            self.p.stdin.close()
        except (BrokenPipeError, OSError, ValueError):
            pass
        return self.p.wait(timeout)


@pytest.fixture
def proc():
    p = Proc()
    yield p
    if p.p.poll() is None:
        p.p.kill()
        p.p.wait()


def detect_request(tmp_path, job="j1", **over):
    final = tmp_path / "rec-wavetracker"
    req = detect_job(
        input=str(tmp_path / "rec.wav"),
        output_dir=str(final) + ".partial-1",
        final_dir=str(final),
        config={"harmonic_groups": {"min_freq": 400.0, "max_freq": 1200.0}},
        config_path=None,
        start=0.0,
        duration=None,
        device="cpu",
    )
    req.update(id=job, **over)
    return req


@pytest.fixture
def rec(tmp_path):
    (tmp_path / "rec.wav").write_bytes(b"stub recording")
    return tmp_path


# ------------------------------------------------------------ protocol


def test_hello_comes_first(proc):
    hello = proc.next()
    assert hello["type"] == "hello"
    assert hello["protocol"] == 1
    assert hello["wavetracker"] == "0.0.0-stub"
    assert {"detect", "peaks", "cleanup", "multi_input"} <= set(hello["capabilities"])
    assert hello["devices"] == ["auto", "cpu"]
    assert hello["default_config"]["harmonic_groups"]["max_freq"] == 2400.0
    assert proc.close() == 0


def test_detect_streams_progress_then_result(proc, rec):
    proc.next()
    proc.send(detect_request(rec))
    msgs = proc.until("j1")
    kinds = [m["type"] for m in msgs]
    assert kinds[-1] == "result", msgs
    detect = [m for m in msgs if m["type"] == "progress" and m["stage"] == "detect"]
    assert [m["done"] for m in detect if m["total"] > 0] == [10, 20, 30]
    assert any(m["type"] == "progress" and m["stage"] == "track" for m in msgs)
    assert any(m["type"] == "log" and "stub detect" in m["text"] for m in msgs)
    result = msgs[-1]
    final = rec / "rec-wavetracker"
    assert result["output_dir"] == str(final)
    assert result["n_detections"] == 6 and result["n_ids"] == 2
    assert "total" in result["timings"]
    # renamed: the partial directory is gone, the final one is complete
    assert not (rec / "rec-wavetracker.partial-1").exists()
    ident = np.load(final / "ident_v.npy")
    assert np.isnan(ident[-1]) and ident[0] == 0.0
    meta = json.loads((final / "wavetracker.json").read_text())
    assert meta["config"]["harmonic_groups"]["min_freq"] == 400.0
    assert proc.close() == 0


def test_list_input_and_no_final_dir(proc, rec):
    proc.next()
    (rec / "b.wav").write_bytes(b"x")
    out = rec / "snippet-run"
    proc.send(
        detect_request(
            rec,
            input=[str(rec / "rec.wav"), str(rec / "b.wav")],
            output_dir=str(out),
            final_dir=None,
            track=False,
        )
    )
    msgs = proc.until("j1")
    assert msgs[-1]["type"] == "result" and msgs[-1]["output_dir"] == str(out)
    assert msgs[-1]["n_ids"] == 0
    assert not any(m.get("stage") == "track" for m in msgs)
    assert "b.wav" in json.loads((out / "wavetracker.json").read_text())["input"]


def test_prints_go_to_stderr_never_to_the_protocol(proc, rec):
    proc.next()
    proc.send(detect_request(rec))
    proc.until("j1")
    assert proc.close() == 0
    # every stdout line was JSON (or `next` would have raised), and the print
    # landed on stderr
    assert any("stub detect says hello on stdout" in line for line in proc.err)


def test_exception_is_one_error_and_the_runner_keeps_serving(rec):
    p = Proc(env=stub_env(FAKE_WT_RAISE="boom in detect"))
    try:
        p.next()
        p.send(detect_request(rec))
        msgs = p.until("j1")
        errors = [m for m in msgs if m["type"] == "error"]
        assert len(errors) == 1
        err = errors[0]
        assert err["kind"] == "exception"
        assert "boom in detect" in err["message"]
        assert "Traceback" in err["traceback"] and "pipeline.py" in err["traceback"]
        # no half directory where results are expected, and none beside it
        assert not (rec / "rec-wavetracker").exists()
        assert not (rec / "rec-wavetracker.partial-1").exists()
        # still serving
        p.send({"id": "j2", "op": "nonsense"})
        msgs = p.until("j2")
        assert msgs[-1]["kind"] == "bad_request"
        assert p.close() == 0
    finally:
        if p.p.poll() is None:
            p.p.kill()


def test_unknown_config_keys_are_a_bad_request(proc, rec):
    proc.next()
    proc.send(detect_request(rec, config={"harmonic_groups": {"bogus": 1}}))
    err = proc.until("j1")[-1]
    assert err["type"] == "error" and err["kind"] == "bad_request"
    assert "bogus" in err["message"]
    proc.send(detect_request(rec, job="j2", config={"nosuchsection": {}}))
    assert proc.until("j2")[-1]["kind"] == "bad_request"


def test_bad_requests(proc, rec):
    proc.next()
    proc.send("this is not json")
    msg = proc.next()
    assert msg["type"] == "error" and msg["kind"] == "bad_request" and msg["id"] is None
    proc.send({"id": "j1", "op": "detect"})  # no input
    assert proc.until("j1")[-1]["kind"] == "bad_request"
    proc.send(detect_request(rec, job="j2", input=str(rec / "missing.wav")))
    err = proc.until("j2")[-1]
    assert err["kind"] == "input" and "missing.wav" in err["message"]


def test_existing_final_dir_is_refused(proc, rec):
    proc.next()
    (rec / "rec-wavetracker").mkdir()
    proc.send(detect_request(rec))
    err = proc.until("j1")[-1]
    assert err["kind"] == "input" and "already exists" in err["message"]


def test_too_short_is_an_input_error(proc, rec):
    proc.next()
    # the stub recording has 100,000 samples at 1 kHz; two windows of 256
    # samples with step 128 need 0.384 s
    proc.send(detect_request(rec, duration=0.2))
    err = proc.until("j1")[-1]
    assert err["kind"] == "input" and "two FFT windows" in err["message"]


def test_cleanup_oneshot(rec):
    folder = rec / "clean"
    folder.mkdir()
    np.save(folder / "fund_v.npy", np.array([500.0, 501.0, 700.0]))
    np.save(folder / "idx_v.npy", np.array([0, 1, 0]))
    np.save(folder / "ident_v.npy", np.array([0.0, 0.0, 3.0]))
    p = Proc("--oneshot")
    try:
        p.next()
        req = cleanup_job(folder, 2, {"stride_minutes": 10}, 2**34)
        p.send({**req, "id": "c1"})
        msgs = p.until("c1")
        res = msgs[-1]
        assert res["type"] == "result", msgs
        assert res["ident_path"] == str(folder / "ident_v_cleaned_n2.npy")
        assert res["n_ids"] == 1
        steps = [
            (m["done"], m["total"], m["text"])
            for m in msgs
            if m["type"] == "progress" and m["total"] > 0
        ]
        assert steps == [
            (0, 1000, "window 1/1"),
            (250, 1000, "window 1/1"),
            (600, 1000, "joining overlapping tracks"),
            (1000, 1000, "done"),
        ]
        assert p.p.wait(10) == 0  # one-shot: exits after the job
    finally:
        if p.p.poll() is None:
            p.p.kill()
    assert any("cleanup prints a lot" in line for line in p.err)


def test_cleanup_that_changes_frequencies_is_an_error(rec):
    folder = rec / "clean"
    folder.mkdir()
    np.save(folder / "fund_v.npy", np.array([500.0, 501.0]))
    np.save(folder / "idx_v.npy", np.array([0, 1]))
    np.save(folder / "ident_v.npy", np.array([1.0, 1.0]))
    p = Proc("--oneshot", env=stub_env(FAKE_WT_CLEANUP_BREAK=1))
    try:
        p.next()
        p.send({**cleanup_job(folder, 2, {}, None), "id": "c1"})
        err = p.until("c1")[-1]
        assert err["type"] == "error" and "fund_v" in err["message"]
        p.send({**cleanup_job(folder, 2, {"nope": 1}, None), "id": "c2"})
    finally:
        p.p.wait(10)


def test_missing_wavetracker_is_a_startup_error(tmp_path):
    # wavetracker is installed with audian; a package that fails to import
    # stands in for a broken install
    broken = tmp_path / "wavetracker"
    broken.mkdir()
    (broken / "__init__.py").write_text("raise ImportError('broken on purpose')\n")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(tmp_path)
    p = Proc(env=env)
    msg = p.next()
    assert msg["type"] == "error" and msg["kind"] == "startup"
    assert msg["message"].startswith("wavetracker is not installed in this environment")
    assert p.p.wait(10) == 3


def test_idle_exit(tmp_path):
    p = Proc("--idle", "0.3")
    assert p.next()["type"] == "hello"
    assert p.p.wait(10) == 0


def test_end_of_stdin_exits(proc):
    proc.next()
    proc.p.stdin.close()
    assert proc.p.wait(10) == 0


def test_sigterm_mid_run_removes_the_partial_directory(rec):
    p = Proc(env=stub_env(FAKE_WT_SLEEP=30))
    try:
        p.next()
        p.send(detect_request(rec))
        while True:
            msg = p.next()
            if msg["type"] == "progress" and msg["stage"] == "detect" and msg["done"]:
                break
        assert (rec / "rec-wavetracker.partial-1").is_dir()
        p.p.terminate()
        assert p.p.wait(10) != 0
    finally:
        if p.p.poll() is None:
            p.p.kill()
    assert not (rec / "rec-wavetracker.partial-1").exists()
    assert not (rec / "rec-wavetracker").exists()


# ------------------------------------------------------------ pure helpers


def test_wtrunner_imports_neither_audian_nor_qt():
    tree = ast.parse(Path(WTRUNNER).read_text())
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            roots.add(node.module.split(".")[0])
    allowed = set(sys.stdlib_module_names) | {"numpy", "wavetracker", "torch"}
    assert roots <= allowed, roots - allowed
    assert not roots & {"audian", "audian_plugins", "PySide6", "pyqtgraph"}


def test_runner_imports_only_qtcore():
    code = (
        "import sys; import audian_plugins.eodsorter.runner; "
        "bad = [m for m in sys.modules if m.startswith(('PySide6.QtWidgets', "
        "'PySide6.QtGui', 'pyqtgraph', 'wavetracker', 'torch', 'audian.'))]; "
        "print(bad)"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(Path(runner.__file__).parents[2])},
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "[]"


def test_deep_merge():
    sys.path.insert(0, str(Path(WTRUNNER).parent))
    try:
        import wtrunner
    finally:
        sys.path.pop(0)
    base = {"a": {"x": 1, "y": 2}, "b": 3}
    assert wtrunner.deep_merge(base, {"a": {"y": 5}, "c": 1}) == {
        "a": {"x": 1, "y": 5},
        "b": 3,
        "c": 1,
    }
    assert base == {"a": {"x": 1, "y": 2}, "b": 3}


def test_wavetracker_is_installed_alongside_and_cheap_to_check():
    """wavetracker is a dependency of audian: this interpreter has it, and
    asking for its version does not pull torch or numba into the GUI."""
    code = (
        "import sys\n"
        "from audian_plugins.eodsorter.runner import wavetracker_status\n"
        "version, error = wavetracker_status()\n"
        "assert error is None and version, error\n"
        "assert 'torch' not in sys.modules and 'numba' not in sys.modules\n"
    )
    env = dict(os.environ)
    src = str(Path(runner.__file__).resolve().parents[2])
    env["PYTHONPATH"] = src
    subprocess.run([sys.executable, "-c", code], check=True, env=env)


def test_wavetracker_status_names_a_broken_install(monkeypatch, tmp_path):
    pkg = tmp_path / "wavetracker"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("raise ImportError('broken on purpose')\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "wavetracker", raising=False)
    version, error = runner.wavetracker_status()
    assert version is None
    assert error == (
        "wavetracker is not installed in this environment: "
        "ImportError: broken on purpose"
    )
    monkeypatch.delitem(sys.modules, "wavetracker", raising=False)


def test_job_builders():
    job = detect_job(
        ["/a.wav"], Path("/o.partial-1"), "/o", None, None, None, None, None
    )
    assert job["input"] == "/a.wav" and job["output_dir"] == "/o.partial-1"
    assert job["config"] == {} and job["device"] == "auto" and job["track"] is True
    assert detect_job(["/a", "/b"], "/o", None, {}, None, 1, 2, "cpu")["input"] == [
        "/a",
        "/b",
    ]

    class Grid:
        nfft, step, s0 = 32768, 3276, 0

    pj = peaks_job(
        "/a.wav", Grid(), np.arange(4), np.array([5, 6]), [1, 2], [3, 4], "/p", "cpu"
    )
    assert pj["frames"] == [5, 6] and pj["channels"] == [0, 1, 2, 3]
    assert pj["nfft"] == 32768 and pj["step"] == 3276 and pj["s0"] == 0
    json.dumps(pj)
    cj = cleanup_job("/d", 2, None, 10)
    assert cj == {
        "op": "cleanup",
        "dir": "/d",
        "n_fish": 2,
        "params": {},
        "mem_limit_bytes": 10,
    }


def test_paths(tmp_path):
    assert runner.default_output_dir("/data/rec.wav") == "/data/rec-wavetracker"
    assert runner.partial_dir("/d/x").startswith("/d/x.partial-")
    d = tmp_path / "res"
    d.mkdir()
    (d / "f").write_text("1")
    aside = runner.move_aside(str(d))
    assert not d.exists() and Path(aside, "f").read_text() == "1"
    assert ".old-" in aside


# ------------------------------------------------------------ RunnerClient


def wait_for(cond, timeout=20.0):
    from PySide6.QtCore import QCoreApplication

    deadline = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > deadline:
            raise AssertionError("timed out")
        QCoreApplication.processEvents()
        time.sleep(0.005)


class Recorder:
    def __init__(self, client: RunnerClient):
        self.events: list = []
        client.sigState.connect(lambda s: self.events.append(("state", s)))
        client.sigHello.connect(lambda h: self.events.append(("hello", h)))
        client.sigProgress.connect(lambda *a: self.events.append(("progress", a)))
        client.sigResult.connect(lambda j, m: self.events.append(("result", j, m)))
        client.sigError.connect(lambda *a: self.events.append(("error", *a)))
        client.sigLog.connect(lambda *a: self.events.append(("log", a)))

    def of(self, kind):
        return [e for e in self.events if e[0] == kind]

    def states(self):
        return [e[1] for e in self.of("state")]


@pytest.fixture
def stub_path(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", str(STUB))
    monkeypatch.delenv("FAKE_WT_SLEEP", raising=False)
    monkeypatch.delenv("FAKE_WT_RAISE", raising=False)


def make_client(path=None, **kw):
    return RunnerClient(program=path, **kw)


def test_the_runner_is_this_interpreter(app):
    assert RunnerClient()._program == sys.executable


def test_client_runs_a_job_and_stays_alive(app, stub_path, rec):
    client = make_client()
    rec_ = Recorder(client)
    job = client.submit(**detect_request(rec, job=None))
    assert client.state in ("starting", "busy")
    wait_for(lambda: rec_.of("result") or rec_.of("error"))
    assert not rec_.of("error"), rec_.events
    (_, jid, msg) = rec_.of("result")[0]
    assert jid == job and msg["output_dir"] == str(rec / "rec-wavetracker")
    assert client.state == "idle" and client.job is None
    assert client.hello["wavetracker"] == "0.0.0-stub"
    progress = [e[1] for e in rec_.of("progress") if e[1][1] == "detect"]
    assert [p[2] for p in progress if p[3] > 0] == [10, 20, 30]
    assert all(p[0] == job for p in progress)
    assert any(e[1][0] == "debug" and "says hello" in e[1][1] for e in rec_.of("log"))
    assert rec_.states()[:2] == ["starting", "busy"] and rec_.states()[-1] == "idle"
    # second job on the same process
    pid = client._proc.processId()
    job2 = client.submit(
        **detect_request(rec, job=None, output_dir=str(rec / "o2"), final_dir=None)
    )
    assert job2 != job
    wait_for(lambda: len(rec_.of("result")) == 2)
    assert client._proc.processId() == pid
    client.shutdown()
    assert client.state == "stopped" and not client.is_running()


def test_client_refuses_a_second_job_while_busy(app, stub_path, rec, monkeypatch):
    monkeypatch.setenv("FAKE_WT_SLEEP", "5")
    client = make_client()
    client.submit(**detect_request(rec, job=None))
    with pytest.raises(RunnerError):
        client.submit(**detect_request(rec, job=None))
    client.cancel()
    wait_for(lambda: not client.is_running())


def test_client_cancel_terminates_and_restarts(app, stub_path, rec, monkeypatch):
    monkeypatch.setenv("FAKE_WT_SLEEP", "30")
    client = make_client()
    rec_ = Recorder(client)
    job = client.submit(**detect_request(rec, job=None))
    wait_for(lambda: any(e[1][1] == "detect" and e[1][2] for e in rec_.of("progress")))
    assert (rec / "rec-wavetracker.partial-1").is_dir()
    t0 = time.monotonic()
    client.cancel()
    wait_for(lambda: rec_.of("error"))
    assert time.monotonic() - t0 < 3.0
    assert rec_.of("error")[0][1:] == (job, "cancelled", "cancelled")
    assert client.state == "stopped" and client.job is None
    assert not (rec / "rec-wavetracker.partial-1").exists()
    assert not (rec / "rec-wavetracker").exists()
    # the next job starts a new process
    monkeypatch.setenv("FAKE_WT_SLEEP", "0")
    job2 = client.submit(**detect_request(rec, job=None))
    wait_for(lambda: rec_.of("result"))
    assert rec_.of("result")[0][1] == job2
    client.shutdown()


def test_client_reports_job_errors_and_keeps_the_process(app, stub_path, rec):
    client = make_client()
    rec_ = Recorder(client)
    job = client.submit(**detect_request(rec, job=None, config={"x": {}}))
    wait_for(lambda: rec_.of("error"))
    assert rec_.of("error")[0][1:3] == (job, "bad_request")
    assert client.state == "idle" and client.is_running()
    assert client.last_error["kind"] == "bad_request"
    assert "traceback" in client.last_error and "stderr_tail" in client.last_error
    client.shutdown()


def test_client_wrong_interpreter(app, tmp_path):
    client = make_client(str(tmp_path / "no-such-python"))
    rec_ = Recorder(client)
    try:
        job = client.submit(op="detect", input="x", output_dir="y")
    except RunnerError:
        job = ""
    wait_for(lambda: rec_.of("error"))
    _, jid, kind, message = rec_.of("error")[0]
    assert kind == "startup" and "no-such-python" in message
    assert jid == job
    assert client.state == "failed" and client.job is None


def test_client_with_a_broken_wavetracker(app, monkeypatch, rec, tmp_path):
    broken = tmp_path / "broken" / "wavetracker"
    broken.mkdir(parents=True)
    (broken / "__init__.py").write_text("raise ImportError('broken on purpose')\n")
    monkeypatch.setenv("PYTHONPATH", str(broken.parent))
    client = make_client()
    rec_ = Recorder(client)
    job = client.submit(**detect_request(rec, job=None))
    wait_for(lambda: rec_.of("error"))
    _, jid, kind, message = rec_.of("error")[0]
    assert (jid, kind) == (job, "startup")
    assert "wavetracker is not installed in this environment" in message
    assert "broken on purpose" in message
    wait_for(lambda: not client.is_running())
    assert client.state == "failed"


def test_client_not_python_at_all(app, tmp_path):
    fake = tmp_path / "python"
    fake.write_text("#!/bin/sh\necho 'not python' >&2\nexit 7\n")
    fake.chmod(0o755)
    client = make_client(str(fake))
    rec_ = Recorder(client)
    job = client.submit(op="detect", input="x", output_dir="y")
    wait_for(lambda: rec_.of("error"))
    _, jid, kind, message = rec_.of("error")[0]
    assert (jid, kind) == (job, "startup")
    assert "code 7" in message and "not python" in message
    assert client.state == "failed"


def test_client_protocol_mismatch(app, stub_path, tmp_path, monkeypatch):
    script = tmp_path / "old_runner.py"
    script.write_text(
        "import json, sys\n"
        "print(json.dumps({'type': 'hello', 'protocol': 99}), flush=True)\n"
        "sys.stdin.read()\n"
    )
    monkeypatch.setattr(runner, "WTRUNNER", str(script))
    client = make_client()
    rec_ = Recorder(client)
    client.ensure_started()
    wait_for(lambda: rec_.of("error"))
    assert rec_.of("error")[0][2] == "protocol"
    assert "update" in rec_.of("error")[0][3]
    wait_for(lambda: not client.is_running())
    assert client.state == "failed"


def test_client_oneshot_exits_after_the_job(app, stub_path, rec):
    folder = rec / "clean"
    folder.mkdir()
    np.save(folder / "fund_v.npy", np.array([500.0, 501.0]))
    np.save(folder / "idx_v.npy", np.array([0, 1]))
    np.save(folder / "ident_v.npy", np.array([1.0, 1.0]))
    client = make_client(oneshot=True)
    rec_ = Recorder(client)
    client.submit(**cleanup_job(folder, 1, {}, None))
    wait_for(lambda: rec_.of("result"))
    wait_for(lambda: client.state == "stopped")
    assert not rec_.of("error")


# ------------------------------------------------------------ SnippetExporter


def test_snippet_exporter_reads_across_a_join(app, tmp_path):
    import soundfile as sf
    from audioio import write_audio

    from audian.pluginapi import CancelToken

    rate = 2000
    rng = np.random.default_rng(0)
    a = (0.1 * rng.standard_normal((30_000, 3))).astype(np.float32)
    b = (0.1 * rng.standard_normal((25_000, 3))).astype(np.float32)
    pa, pb = tmp_path / "a.wav", tmp_path / "b.wav"
    # thunderlab joins files only with start times; the second one is off by
    # an hour, which audian's `open_files` must not drop
    for path, data, stamp in ((pa, a, "10:00:00"), (pb, b, "11:00:00")):
        md = {"INFO": {"DateTimeOriginal": f"2022-05-10T{stamp}"}}
        write_audio(str(path), data, rate, metadata=md, encoding="FLOAT")
    joined = np.vstack([a, b])
    out = tmp_path / "snippet.wav"
    exporter = runner.SnippetExporter(
        [str(pa), str(pb)], (12_345, 47_890), str(out), CancelToken()
    )
    progress, done = [], []
    exporter.sigProgress.connect(lambda w, t: progress.append((w, t)))
    exporter.sigDone.connect(lambda p, e: done.append((p, e)))
    exporter.run()
    assert done == [(str(out), "")]
    data, r = sf.read(out, dtype="float64")
    assert r == rate and data.shape == (47_890 - 12_345, 3)
    assert exporter.subtype == "PCM_32" == sf.info(out).subtype
    np.testing.assert_allclose(data, joined[12_345:47_890], rtol=0, atol=2**-31)
    assert progress[-1] == (47_890 - 12_345, 47_890 - 12_345)
    assert len(progress) == 2  # 10 s chunks at 2 kHz

    # samples outside (-1, 1) are written as float, exactly
    loud = tmp_path / "loud.wav"
    write_audio(str(loud), 20 * a, rate, encoding="FLOAT")
    out_loud = tmp_path / "loud-snippet.wav"
    ex_loud = runner.SnippetExporter([str(loud)], (5, 25_005), out_loud, CancelToken())
    ex_loud.run()
    assert ex_loud.subtype == "FLOAT"
    np.testing.assert_array_equal(
        sf.read(out_loud, dtype="float32")[0], 20 * a[5:25_005]
    )

    # cancelled: no file left behind
    token = CancelToken()
    token.cancel()
    done.clear()
    out2 = tmp_path / "cancelled.wav"
    ex2 = runner.SnippetExporter([str(pa), str(pb)], (0, 100), str(out2), token)
    ex2.sigDone.connect(lambda p, e: done.append((p, e)))
    ex2.run()
    assert done == [(str(out2), "cancelled")] and not out2.exists()

    # out of range: an error, no file
    done.clear()
    ex3 = runner.SnippetExporter(
        [str(pa)], (0, 10**9), str(tmp_path / "x.wav"), CancelToken()
    )
    ex3.sigDone.connect(lambda p, e: done.append((p, e)))
    ex3.run()
    assert done[0][1] and "outside" in done[0][1]
    assert not (tmp_path / "x.wav").exists()
