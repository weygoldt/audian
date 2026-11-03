"""The model runner as a process, and the client that talks to it.

No torch and no weights: a stub `ultralytics` is put on the runner's path, so these
run anywhere.  What the real models do is `test_specdetector_app.py`.
"""

from __future__ import annotations

import ast
import csv
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
PACKAGE = SRC / "audian_plugins" / "specdetector"
RUNNER = PACKAGE / "modelrunner.py"
sys.path.insert(0, str(SRC))

from audian_plugins.specdetector.boxes import Box  # noqa: E402
from audian_plugins.specdetector.recording import Recording  # noqa: E402


def stub_ultralytics(directory: Path) -> Path:
    """An `ultralytics` that only knows what `models.configure` asks of it."""
    package = directory / "stubs" / "ultralytics"
    utils = package / "utils"
    utils.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (utils / "__init__.py").write_text(
        "import json, os, sys\n"
        "class _Settings(dict):\n"
        "    def update(self, *a, **k):\n"
        "        super().update(*a, **k)\n"
        "        path = os.path.join(os.environ['YOLO_CONFIG_DIR'], 'stub-settings.json')\n"
        "        with open(path, 'w') as fp:\n"
        "            json.dump({**self, 'sys_path': sys.path, 'env': {k: v for k, v in\n"
        "                       os.environ.items() if k.startswith('YOLO_')}}, fp)\n"
        "SETTINGS = _Settings()\n"
    )
    return directory / "stubs"


def env_with(stubs: Path) -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(stubs), str(SRC)])
    return env


class Process:
    """The runner, driven the way the client drives it."""

    def __init__(self, weights: Path, stubs: Path):
        self.proc = subprocess.Popen(
            [sys.executable, "-u", str(RUNNER), "--weights", str(weights)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env_with(stubs),
        )

    def read(self) -> dict:
        line = self.proc.stdout.readline()
        assert line, self.proc.stderr.read()
        return json.loads(line)

    def ask(self, **request) -> dict:
        self.proc.stdin.write(json.dumps(request) + "\n")
        self.proc.stdin.flush()
        return self.read()

    def close(self) -> int:
        self.proc.stdin.close()
        code = self.proc.wait(timeout=20)
        self.proc.stdout.close()
        self.proc.stderr.close()
        return code


@pytest.fixture()
def process(tmp_path):
    runner = Process(tmp_path / "weights", stub_ultralytics(tmp_path))
    yield runner
    if runner.proc.poll() is None:
        runner.proc.kill()
        runner.proc.wait(timeout=10)
        runner.proc.stdin.close()
        runner.proc.stdout.close()
        runner.proc.stderr.close()


def test_it_says_hello_on_stdout_and_nothing_else_goes_there(process):
    message = process.read()
    assert message["type"] == "hello"
    assert message["protocol"] == 1
    assert "weights_dir" in message
    process.ask(id="a", op="nothing")
    process.proc.stdin.close()
    # Whatever is left on the protocol channel has to be protocol.
    for line in process.proc.stdout:
        assert json.loads(line)["type"] in {"result", "error", "progress", "log"}
    process.proc.wait(timeout=20)
    process.proc.stdout.close()
    process.proc.stderr.close()


def test_its_own_directory_is_not_on_its_path(process, tmp_path):
    """`import tools` would find the plugin's Qt module there."""
    process.read()
    process.ask(id="a", op="nothing")
    settings = json.loads((tmp_path / "weights" / "stub-settings.json").read_text())
    assert str(PACKAGE) not in settings["sys_path"]
    assert str(SRC) in settings["sys_path"]


def test_an_unknown_op_is_one_error_and_the_runner_keeps_serving(process):
    process.read()
    answer = process.ask(id="a", op="teleport")
    assert answer == {
        "type": "error",
        "id": "a",
        "kind": "bad_request",
        "message": "unknown op 'teleport'",
    }
    assert process.ask(id="b", op="segment")["kind"] == "bad_request"


def test_a_line_that_is_not_json_is_an_error_and_not_a_crash(process):
    process.read()
    process.proc.stdin.write("{ not json\n")
    process.proc.stdin.flush()
    assert process.read()["kind"] == "bad_request"
    assert process.proc.poll() is None


def test_end_of_stdin_ends_it_cleanly(process):
    process.read()
    assert process.close() == 0


def test_it_keeps_ultralytics_home_and_offline(process, tmp_path):
    """No analytics, no pip at run time, no downloads, nothing under ~/.config."""
    process.read()
    process.ask(id="a", op="nothing")  # configure has run by the time this answers
    settings = json.loads((tmp_path / "weights" / "stub-settings.json").read_text())
    assert settings["sync"] is False
    assert settings["env"]["YOLO_AUTOINSTALL"] == "false"
    assert settings["env"]["YOLO_OFFLINE"] == "true"
    assert settings["env"]["YOLO_CONFIG_DIR"] == str(tmp_path / "weights")
    assert settings["weights_dir"] == str(tmp_path / "weights")
    for integration in ("clearml", "comet", "dvc", "mlflow", "raytune", "wandb"):
        assert settings[integration] is False


def test_nothing_in_the_runner_can_reach_audian_or_qt():
    """Importing `audian` brings up the whole window, beside OpenCV's own Qt."""
    for name in (
        "modelrunner",
        "models",
        "tiling",
        "dataset",
        "boxes",
        "review",
        "project",
        "recording",
    ):
        tree = ast.parse((PACKAGE / f"{name}.py").read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                roots = [(node.module or "").split(".")[0]]
            else:
                continue
            for root in roots:
                assert root not in {"audian", "PySide6", "pyqtgraph"}, (name, root)


def test_a_missing_weight_names_the_file_and_the_cure(tmp_path, monkeypatch):
    from audian_plugins.specdetector import models

    monkeypatch.chdir(tmp_path)
    with pytest.raises(models.MissingWeights, match="sam2.1_t.pt.*Get models"):
        models.weights_path("sam2.1_t.pt")


def test_the_gpu_refusing_is_recognised_in_both_of_its_shapes():
    sys.path.insert(0, str(PACKAGE))
    import modelrunner

    class OutOfMemoryError(RuntimeError):
        pass

    assert modelrunner._out_of_memory(OutOfMemoryError("whatever"))
    assert modelrunner._out_of_memory(RuntimeError("CUDA error: out of memory"))
    assert not modelrunner._out_of_memory(RuntimeError("something else"))
    assert not modelrunner._out_of_memory(ValueError("out of memory"))


def test_detections_are_written_as_labels_audian_can_read(tmp_path):
    sys.path.insert(0, str(PACKAGE))
    import modelrunner

    from audian.labels import LabelSet

    path = tmp_path / "found.csv"
    shape = ((1.0, 100.0), (1.125, 200.0), (1.25, 100.0))
    modelrunner.write_detections(
        path,
        [
            Box(1.0, 1.25, 100.0, 200.0, 1, 0.75, polygon=shape),
            Box(2.0, 2.5, 300.0, 400.0, 0, 0.5),
        ],
        ["chirp", "rise"],
        " (found)",
    )
    read = LabelSet()
    report = read.read(path)
    assert report.read == 2 and report.dropped == 0
    first = read.labels[0]
    assert first.category == "rise (found)"
    assert first.t0 == 1.0 and first.t1 == 1.25
    assert first.f0 == 100.0 and first.f1 == 200.0
    assert first.note == "score=0.7500"
    assert first.channel is None
    # A segmenting model's outline arrives as the label's, as SAM's would.
    assert first.outline == shape
    assert read.labels[1].outline is None


def test_a_split_recording_opens_as_audian_opens_it(tmp_path):
    """The bare loader drops a session's short last file; this must not, ever.

    On a recording stamped as a TASCAM stamps it, because that is the case the bare
    loader gets wrong and `audian.data.open_files` exists to get right.
    """
    pytest.importorskip("soundfile")
    sys.path.insert(0, str(REPO / "tests"))
    from test_session import write_split_recording

    from audian.data import open_files

    split = write_split_recording(tmp_path)
    paths = [str(p) for p in split.paths]
    assert len(paths) > 2
    theirs = open_files(paths, 10.0, 0.0)
    try:
        with Recording(paths) as ours:
            assert ours.frames == len(theirs) > 0
            assert ours.rate == theirs.rate
            assert ours.channels == theirs.channels
            # across the last join, which is the one a bare loader never reaches
            join = int(theirs.end_indices[-2])
            np.testing.assert_allclose(
                ours.read(join - 100, join + 100),
                np.asarray(theirs[join - 100 : join + 100]).reshape(200, -1),
                atol=1e-6,
            )
    finally:
        theirs.close()


def test_a_read_past_the_end_is_short_and_not_an_error(tmp_path):
    soundfile = pytest.importorskip("soundfile")
    path = tmp_path / "a.wav"
    soundfile.write(path, np.zeros((100, 1)), 8000)
    with Recording(path) as recording:
        assert len(recording.read(90, 200)) == 10
        assert len(recording.read(200, 300)) == 0
        assert recording.read(90, 200).shape == (10, 1)


# ------------------------------------------------------------------- the client


FAKE_RUNNER = """
import json, os, sys, time
send = lambda m: (sys.stdout.write(json.dumps(m) + "\\n"), sys.stdout.flush())
print("noise on stderr", file=sys.stderr, flush=True)
send({"type": "hello", "protocol": int(os.environ.get("FAKE_PROTOCOL", "1"))})
for line in sys.stdin:
    req = json.loads(line)
    if req["op"] == "shutdown":
        break
    if req["op"] == "work":
        send({"type": "progress", "id": req["id"], "stage": "train", "done": 1, "total": 2, "text": "x"})
        send({"type": "result", "id": req["id"], "answer": req["value"] * 2})
    elif req["op"] == "slow":
        time.sleep(60)
    elif req["op"] == "die":
        os._exit(3)
    elif req["op"] == "fail":
        send({"type": "error", "id": req["id"], "kind": "input", "message": "bad", "traceback": "tb"})
"""


@pytest.fixture()
def client(app, tmp_path, monkeypatch):
    from audian_plugins.specdetector.client import RunnerClient

    script = tmp_path / "fake.py"
    script.write_text(FAKE_RUNNER)
    made = RunnerClient(tmp_path, script=str(script))
    yield made
    made.shutdown(500)


def pump_until(condition, timeout=20.0):
    from PySide6.QtWidgets import QApplication

    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QApplication.processEvents()
        if condition():
            return True
        time.sleep(0.005)
    return False


def record(client):
    results, errors, progress = [], [], []
    client.sigResult.connect(lambda job, message: results.append((job, message)))
    client.sigError.connect(lambda job, kind, text: errors.append((job, kind, text)))
    client.sigProgress.connect(lambda *values: progress.append(values))
    return results, errors, progress


def test_a_job_sent_before_hello_is_delivered_after_it(client):
    results, errors, progress = record(client)
    job = client.submit("work", value=21)
    assert client.busy()
    assert pump_until(lambda: results)
    assert results[0][0] == job and results[0][1]["answer"] == 42
    assert progress[0][1:] == ("train", 1, 2, "x")
    assert not client.busy()
    assert errors == []


def test_a_second_job_is_refused_while_one_runs(client):
    from audian_plugins.specdetector.client import RunnerError

    client.submit("slow")
    with pytest.raises(RunnerError, match="busy"):
        client.submit("work", value=1)


def test_cancelling_ends_the_job_with_cancelled_and_the_next_job_starts_afresh(client):
    results, errors, _ = record(client)
    client.submit("slow")
    assert pump_until(lambda: client.hello is not None)
    client.cancel()
    assert pump_until(lambda: errors)
    assert errors[0][1] == "cancelled"
    assert pump_until(lambda: not client.is_running())
    client.submit("work", value=2)
    assert pump_until(lambda: results)
    assert results[0][1]["answer"] == 4


def test_a_crash_is_reported_with_what_stderr_said(client):
    _, errors, _ = record(client)
    client.submit("die")
    assert pump_until(lambda: errors)
    assert errors[0][1] == "exception"
    assert "exited with code 3" in errors[0][2]
    assert "noise on stderr" in errors[0][2]
    assert client.last_error["kind"] == "exception"


def test_an_error_from_a_job_leaves_the_runner_serving(client):
    results, errors, _ = record(client)
    client.submit("fail")
    assert pump_until(lambda: errors)
    assert errors[0][1:] == ("input", "bad")
    assert client.last_error["traceback"] == "tb"
    client.submit("work", value=3)
    assert pump_until(lambda: results)


def test_another_protocol_is_refused_by_name(client, monkeypatch):
    monkeypatch.setenv("FAKE_PROTOCOL", "99")
    _, errors, _ = record(client)
    client.submit("work", value=1)
    assert pump_until(lambda: errors)
    assert errors[0][1] == "protocol"
    assert "99" in errors[0][2]


def test_an_interpreter_that_will_not_start_says_so(app, tmp_path):
    from audian_plugins.specdetector.client import RunnerClient

    made = RunnerClient(tmp_path, program=str(tmp_path / "no-such-python"))
    _, errors, _ = record(made)
    made.ensure_started()
    assert pump_until(lambda: errors)
    assert errors[0][1] == "startup"
    made.shutdown(100)


def test_shutdown_leaves_no_process_behind(client):
    results, _, _ = record(client)
    client.submit("work", value=1)
    assert pump_until(lambda: results)
    process = client._proc
    assert process is not None
    pid = process.processId()
    assert pid > 0
    client.shutdown(2000)
    assert not client.is_running()
    # The process itself, and not only the client's opinion of it.  Signal 0 asks
    # whether it exists; a zombie not yet reaped still would, so give it a moment.
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        if Path(f"/proc/{pid}/stat").exists():
            stat = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
            if stat[0] == "Z":
                return
        time.sleep(0.02)
    pytest.fail(f"process {pid} is still running")


# Nothing may be written where Ultralytics would write by itself.
def test_csv_columns_match_the_sidecar_exactly(tmp_path):
    sys.path.insert(0, str(PACKAGE))
    import modelrunner

    from audian.labels import COLUMNS

    path = tmp_path / "found.csv"
    modelrunner.write_detections(path, [], ["x"], "")
    with open(path, newline="") as fp:
        assert tuple(next(csv.reader(fp))) == tuple(COLUMNS)
