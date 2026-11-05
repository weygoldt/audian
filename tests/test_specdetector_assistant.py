"""SAM as audian's label assistant: what it asks the runner, and in which order.

The runner is a fake that records each job and answers when the test says so, so
these pin the requests and the queue, not SAM.  `test_specdetector_app` runs the
real model through the real label tool.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from PySide6.QtCore import QObject, Signal  # noqa: E402

from audian.pluginapi import Answer, Drawn  # noqa: E402
from audian_plugins.specdetector import models  # noqa: E402
from audian_plugins.specdetector.assistant import (  # noqa: E402
    MAX_VIEW_SAMPLES,
    SamAssistant,
)
from audian_plugins.specdetector.client import RunnerError  # noqa: E402

PATHS = ("/data/rec-0.wav", "/data/rec-1.wav")
VIEW = (10.0, 14.0, 500.0, 1500.0)


class FakeClient(QObject):
    sigResult = Signal(str, dict)
    sigError = Signal(str, str, str)

    def __init__(self):
        super().__init__()
        self.jobs = []
        self.job = None
        self.stopped = False

    def busy(self):
        return self.job is not None

    def submit(self, op, **params):
        if self.job is not None:
            raise RunnerError("busy")
        self.job = f"j{len(self.jobs)}"
        self.jobs.append((op, params))
        return self.job

    def answer(self, message):
        job, self.job = self.job, None
        self.sigResult.emit(job, message)

    def fail(self, kind, text):
        job, self.job = self.job, None
        self.sigError.emit(job, kind, text)

    def shutdown(self):
        self.stopped = True


@pytest.fixture
def weights(tmp_path):
    (tmp_path / models.SAM_WEIGHTS).write_bytes(b"")
    return tmp_path


@pytest.fixture
def sam(weights):
    client = FakeClient()
    browser = SimpleNamespace(data=SimpleNamespace(rate=20000.0, channels=2))
    return SamAssistant(browser, lambda: weights, client=client), client


def mask(t0, t1, f0, f1):
    polygon = [[t0, f0], [0.5 * (t0 + t1), f1], [t1, f0]]
    return {"mask": {"box": [t0, t1, f0, f1], "polygon": polygon}}


def test_it_says_how_to_get_sam_when_the_weights_are_missing(tmp_path):
    assistant = SamAssistant(SimpleNamespace(), lambda: tmp_path, client=FakeClient())
    assert models.SAM_WEIGHTS in assistant.unavailable()
    assert "Get models" in assistant.unavailable()
    (tmp_path / models.SAM_WEIGHTS).write_bytes(b"")
    assert assistant.unavailable() == ""


def test_a_drag_asks_with_its_box_on_its_own_lane_and_gets_box_and_outline(sam):
    assistant, client = sam
    answers = []
    assistant.refine(
        Drawn(PATHS, 1, VIEW, (11.0, 12.0, 700.0, 900.0)), lambda *a: answers.append(a)
    )
    assert len(client.jobs) == 1
    op, params = client.jobs[0]
    assert op == "segment"
    assert params["paths"] == list(PATHS)
    assert params["view"] == dict(t0=10.0, t1=14.0, f0=500.0, f1=1500.0)
    assert (params["channels"], params["reduction"]) == (None, "1")
    assert params["box"] == [11.0, 12.0, 700.0, 900.0]
    assert params["points"] == []
    client.answer(mask(11.2, 11.8, 720.0, 880.0))
    assert answers == [
        (
            Answer(
                (11.2, 11.8, 720.0, 880.0),
                ((11.2, 720.0), (11.5, 880.0), (11.8, 720.0)),
            ),
            "",
        )
    ]


def test_the_mean_lane_is_read_as_the_mean_of_its_channels(sam):
    assistant, client = sam
    assistant.refine(
        Drawn(PATHS, [0, 1], VIEW, (11.0, 12.0, 700.0, 900.0)), lambda *a: None
    )
    params = client.jobs[0][1]
    assert (params["channels"], params["reduction"]) == ([0, 1], "mean")


def test_each_drag_gets_its_own_answer_in_the_order_drawn(sam):
    """Three, so that one is waiting when the next arrives: each is a label."""
    assistant, client = sam
    got = []
    for name, t0 in (("first", 11.0), ("second", 12.0), ("third", 13.0)):
        assistant.refine(
            Drawn(PATHS, 0, VIEW, (t0, t0 + 0.5, 700.0, 900.0)),
            lambda a, m, name=name: got.append((name, a)),
        )
    assert len(client.jobs) == 1
    for t0 in (11.0, 12.0, 13.0):
        assert client.jobs[-1][1]["box"] == [t0, t0 + 0.5, 700.0, 900.0]
        client.answer(mask(t0 + 0.1, t0 + 0.4, 710.0, 890.0))
    assert [(name, a.box[0]) for name, a in got] == [
        ("first", 11.1),
        ("second", 12.1),
        ("third", 13.1),
    ]


def test_a_resting_view_is_encoded_once_and_a_drag_waits_for_it(sam):
    assistant, client = sam
    assistant.prepare(Drawn(PATHS, 0, VIEW))
    assert len(client.jobs) == 1
    assert "box" not in client.jobs[0][1]
    got = []
    assistant.refine(
        Drawn(PATHS, 0, VIEW, (11.0, 12.0, 700.0, 900.0)), lambda a, m: got.append(a)
    )
    assert len(client.jobs) == 1
    client.answer({"mask": None, "encode_ms": 38.0})
    assert got == []
    assert client.jobs[1][1]["box"] == [11.0, 12.0, 700.0, 900.0]
    client.answer(mask(11.1, 11.9, 710.0, 890.0))
    assert len(got) == 1
    # the same view again is already encoded
    assistant.prepare(Drawn(PATHS, 0, VIEW))
    assert len(client.jobs) == 2


def test_a_view_too_long_to_read_is_refused_at_once(sam):
    assistant, client = sam
    seconds = MAX_VIEW_SAMPLES / (20000.0 * 2) + 1.0
    got = []
    assistant.refine(
        Drawn(PATHS, 0, (0.0, seconds, 500.0, 1500.0), (1.0, 2.0, 700.0, 900.0)),
        lambda a, m: got.append((a, m)),
    )
    assert client.jobs == []
    assert got == [(None, "this view is too long to read: zoom in to label")]


def test_a_failure_is_reported_to_its_drag_and_the_next_one_goes(sam):
    assistant, client = sam
    got = []
    assistant.refine(
        Drawn(PATHS, 0, VIEW, (11.0, 12.0, 700.0, 900.0)),
        lambda a, m: got.append((a, m)),
    )
    assistant.refine(
        Drawn(PATHS, 0, VIEW, (12.5, 13.0, 700.0, 900.0)),
        lambda a, m: got.append((a, m)),
    )
    client.fail("oom", "CUDA out of memory")
    assert got == [(None, "failed: CUDA out of memory")]
    assert len(client.jobs) == 2
    client.fail("cancelled", "stopped")
    assert len(got) == 1


def test_nothing_is_answered_after_close(sam):
    assistant, client = sam
    got = []
    assistant.refine(
        Drawn(PATHS, 0, VIEW, (11.0, 12.0, 700.0, 900.0)), lambda a, m: got.append(a)
    )
    assistant.refine(
        Drawn(PATHS, 0, VIEW, (12.5, 13.0, 700.0, 900.0)), lambda a, m: got.append(a)
    )
    assistant.close()
    assert client.stopped
    client.answer(mask(11.1, 11.9, 710.0, 890.0))
    assert got == []
    assert len(client.jobs) == 1
