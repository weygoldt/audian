"""Criterion 16, as a number rather than as an opinion.

"Core interactions remain responsive" is the one item on the migration's
definition of done with no mechanical check, so this is it: run a real
refilter, and count the ticks of a 16 ms `QTimer` that survive it.

The assertions are on **tick count and inter-tick interval**, never on how
long the refilter took.  A wall-clock assertion turns a correctness test
into a benchmark that fails when the machine is busy; the event loop either
gets its turns or it does not, and that is what is being defended.

Measured on the machine this was written on, 16 channels at 20 kHz over a
27 s buffer:

    GUI thread     0 ticks in 352 ms
    worker thread  25 ticks in 412 ms, median 16.0 ms, p95 16.2 ms, max 16.4

The zero is not an artefact of a slow machine.  A `sosfilt` over 70 MB is
one C call; while it runs there is no turn of the event loop for a timer to
fire in, so the count is exactly zero however fast the CPU is.
"""

from __future__ import annotations

import time

import numpy as np
import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication

from audian.bufferedfilter import BufferedFilter
from audian.bufferedspectrogram import BufferedSpectrogram
from audian.tasks.compute import ComputeJob, ComputeWorker, plan_chain
from audian.tasks.manager import TaskManager

RATE = 20000.0
CHANNELS = 8
NFRAMES = 600000

#: how often the timer under test wants to fire
TICK_MS = 16

#: The refilter has to be long enough for the question to mean anything.
#:
#: Sized against the *warm* process, not the cold one.  Run on its own this
#: chain took 163 ms, but inside the full suite -- where scipy's caches are
#: already populated by every test before it -- the same chain took 119 ms,
#: which failed a guard set at 120 from a standalone measurement.  So the
#: floor is set well under the warm number rather than just below the cold
#: one, and `NFRAMES` carries the rest of the margin.  Four tick intervals is
#: still far more than enough for the event loop to show itself if it is
#: running at all, which is the only thing this guard is protecting.
MIN_JOB_MS = 4 * TICK_MS


class FakeSource:
    def __init__(self):
        self.rate = RATE
        self.channels = CHANNELS
        self.frames = NFRAMES
        self.offset = 0
        self.bufferframes = NFRAMES
        self.backframes = 0
        self.ampl_min = -1.0
        self.ampl_max = 1.0
        self.unit = "V"
        self.dests = []
        rng = np.random.default_rng(3)
        self.buffer = rng.standard_normal((NFRAMES, CHANNELS))


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def chain():
    source = FakeSource()
    filt = BufferedFilter()
    filt.open(source)
    spec = BufferedSpectrogram()
    spec.open(filt)
    filt.need_update = spec.need_update = True
    filt.highpass_cutoff = 300.0
    filt.lowpass_cutoff = 8000.0
    filt.update()
    return filt


class Ticker:
    """A 16 ms timer, and what happened to it between two instants."""

    def __init__(self):
        self.times: list[float] = []
        self.timer = QTimer()
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.setInterval(TICK_MS)
        self.timer.timeout.connect(lambda: self.times.append(time.perf_counter()))

    def start(self, app):
        app.processEvents()
        self.times.clear()
        self.timer.start()

    def between(self, t0: float, t1: float) -> list[float]:
        self.timer.stop()
        return [t for t in self.times if t0 <= t <= t1]


def test_a_refilter_on_the_gui_thread_stops_the_event_loop_dead(app, chain):
    """The measurement the threading exists to remove."""
    ticker = Ticker()
    ticker.start(app)
    chain.highpass_cutoff = 500.0
    t0 = time.perf_counter()
    chain.update()
    t1 = time.perf_counter()
    elapsed_ms = (t1 - t0) * 1000
    assert elapsed_ms > MIN_JOB_MS, (
        f"the synthetic chain only took {elapsed_ms:.0f} ms; make it bigger "
        f"or this test is measuring nothing"
    )
    assert ticker.between(t0, t1) == [], (
        "a whole-buffer sosfilt is one C call, so the event loop cannot run "
        "inside it -- if this ever passes, the chain stopped being the chain"
    )


def test_the_event_loop_keeps_its_turns_through_a_threaded_refilter(app, chain):
    tasks = TaskManager()
    tasks.add_worker("compute", ComputeWorker(tasks))
    try:
        results = []
        tasks.worker("compute").sigResultReady.connect(results.append)

        ticker = Ticker()
        ticker.start(app)
        chain.highpass_cutoff = 500.0
        chain.prepare_update()
        epoch, token = tasks.bump()
        t0 = time.perf_counter()
        tasks.submit(ComputeJob(epoch, token, tuple(plan_chain(chain))))
        deadline = t0 + 30.0
        while not results and time.perf_counter() < deadline:
            app.processEvents()
        t1 = time.perf_counter()
        ticks = ticker.between(t0, t1)
    finally:
        assert tasks.shutdown() == []

    assert results and results[0].error is None, "the job did not finish cleanly"
    elapsed_ms = (t1 - t0) * 1000
    assert elapsed_ms > MIN_JOB_MS, (
        f"the job only took {elapsed_ms:.0f} ms; too short to prove anything"
    )

    # Half the ticks the interval would allow is a wide margin -- the
    # reference run gets essentially all of them -- but it stays a statement
    # about the event loop rather than about the machine's load.
    expected = elapsed_ms / TICK_MS
    assert len(ticks) >= 0.5 * expected, (
        f"{len(ticks)} ticks in {elapsed_ms:.0f} ms, expected at least "
        f"{0.5 * expected:.0f}"
    )
    intervals = np.diff(ticks) * 1000
    assert np.percentile(intervals, 95) <= 40.0, (
        f"p95 inter-tick interval {np.percentile(intervals, 95):.1f} ms; "
        f"the loop is being starved even though it is not blocked"
    )
