"""The contract the compute pipeline is built on.

Cancellation, staleness, error propagation and shutdown are decisions, not
emergent behaviour, so each of them is pinned here.  These tests need no
window: a `TaskManager`, a `ComputeWorker` and a synthetic trace chain are
the whole apparatus.
"""

from __future__ import annotations

import time

import numpy as np
import pytest
from scipy.signal import sosfilt

from audian.bufferedfilter import BufferedFilter
from audian.bufferedspectrogram import BufferedSpectrogram
from audian.tasks.compute import ComputeJob, ComputeWorker, plan_chain, run_job
from audian.tasks.manager import TaskManager
from audian.tasks.tokens import CancelToken

RATE = 20000.0
CHANNELS = 2
NFRAMES = 80000


class FakeSource:
    def __init__(self, nframes=NFRAMES, channels=CHANNELS):
        self.rate = RATE
        self.channels = channels
        self.frames = nframes
        self.offset = 0
        self.bufferframes = nframes
        self.backframes = 0
        self.ampl_min = -1.0
        self.ampl_max = 1.0
        self.unit = "V"
        self.dests = []
        rng = np.random.default_rng(11)
        self.buffer = rng.standard_normal((nframes, channels))


def build_chain(with_spectrogram: bool = True):
    source = FakeSource()
    filt = BufferedFilter()
    filt.open(source)
    filt.bufferframes = NFRAMES
    filt.need_update = True
    filt.highpass_cutoff = 300.0
    filt.lowpass_cutoff = 8000.0
    filt.prepare_update()
    spec = None
    if with_spectrogram:
        spec = BufferedSpectrogram()
        spec.open(filt)
        spec.need_update = True
    # settle the buffers once, the way opening a recording does: a plan is
    # made against the buffer that is there, and there is none until then
    filt.recompute_all()
    return source, filt, spec


def pump(app, seconds: float, until=None) -> None:
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        app.processEvents()
        if until is not None and until():
            return


@pytest.fixture
def qt_app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def manager(qt_app):
    tasks = TaskManager()
    tasks.add_worker("compute", ComputeWorker(tasks))
    yield tasks
    tasks.shutdown()


# --- planning ---------------------------------------------------------


def test_the_plan_is_what_recompute_all_would_have_done():
    _, filt, spec = build_chain()
    steps = plan_chain(filt)
    assert [s.trace for s in steps] == [filt, spec]

    # a trace nobody draws stops the walk, exactly as recompute_all does
    filt.need_update = False
    assert plan_chain(filt) == []


def test_a_job_produces_what_a_synchronous_recompute_would_have():
    source, filt, spec = build_chain()
    job = ComputeJob(1, CancelToken(), tuple(plan_chain(filt)))
    result = run_job(job)
    assert result.error is None and not result.cancelled
    assert [u.trace for u in result.updates] == [filt, spec]

    reference = sosfilt(filt.sos, source.buffer, axis=0).astype(filt.dtype)
    got = result.updates[0].buffer
    n = min(len(got), len(reference))
    assert np.array_equal(got[:n], reference[:n])


def test_a_worker_never_writes_into_a_live_buffer():
    """The GUI paints from `trace.buffer` for the whole time a job runs."""
    _, filt, _spec = build_chain()
    before = filt.buffer
    result = run_job(ComputeJob(1, CancelToken(), tuple(plan_chain(filt))))
    assert filt.buffer is before
    assert result.updates[0].buffer is not before


# --- cancellation and staleness ---------------------------------------


def test_a_cancelled_job_comes_back_cancelled_and_empty():
    _, filt, _spec = build_chain()
    token = CancelToken()
    token.cancel()
    result = run_job(ComputeJob(7, token, tuple(plan_chain(filt))))
    assert result.cancelled and result.updates == () and result.epoch == 7


def test_bumping_supersedes_the_epoch_and_the_token(manager):
    epoch1, token1 = manager.bump()
    assert manager.is_current(epoch1)
    epoch2, token2 = manager.bump()
    assert token1.cancelled, "the previous job must be told to stop"
    assert not token2.cancelled
    assert manager.is_current(epoch2) and not manager.is_current(epoch1)


def test_a_failing_kernel_becomes_a_result_not_a_crash():
    _, filt, _spec = build_chain(with_spectrogram=False)

    def boom(*args, **kwargs):
        raise ValueError("kernel says no")

    filt.process = boom
    result = run_job(ComputeJob(3, CancelToken(), tuple(plan_chain(filt))))
    assert result.error is not None and "kernel says no" in result.error
    assert result.updates == ()


# --- threading, joining and shutdown ----------------------------------


def test_a_submitted_job_runs_on_the_worker_thread(qt_app, manager):
    from PySide6.QtCore import QThread

    _, filt, _spec = build_chain()
    seen = []
    manager.worker("compute").sigResultReady.connect(seen.append)

    threads = []
    original = filt.process

    def note(*args, **kwargs):
        threads.append(QThread.currentThread())
        return original(*args, **kwargs)

    filt.process = note
    epoch, token = manager.bump()
    manager.submit(ComputeJob(epoch, token, tuple(plan_chain(filt))))
    pump(qt_app, 10.0, until=lambda: bool(seen))
    assert seen and seen[0].error is None
    assert threads and threads[0] is not QThread.currentThread()


def test_wait_idle_still_waits_for_a_job_that_has_only_been_queued(manager):
    """The in-flight count is taken when the job is posted, not picked up."""
    _, filt, _spec = build_chain()
    epoch, token = manager.bump()
    manager.submit(ComputeJob(epoch, token, tuple(plan_chain(filt))))
    assert manager.busy
    assert manager.wait_idle(10000)
    assert not manager.busy


def test_cancel_and_wait_returns_promptly(qt_app, manager):
    _, filt, _spec = build_chain()
    epoch, token = manager.bump()
    manager.submit(ComputeJob(epoch, token, tuple(plan_chain(filt))))
    start = time.perf_counter()
    assert manager.cancel_and_wait(10000)
    elapsed = time.perf_counter() - start
    assert not manager.busy
    # chunked kernels: the join is bounded by one chunk, not by the job
    assert elapsed < 1.0, f"join took {elapsed * 1000:.0f} ms"


def test_shutdown_joins_every_thread_and_is_idempotent(qt_app):
    tasks = TaskManager()
    thread = tasks.add_worker("compute", ComputeWorker(tasks))
    assert thread.isRunning()
    assert tasks.shutdown() == []
    assert thread.isFinished()
    assert tasks.shutdown() == []


def test_shutdown_stops_a_running_job(qt_app):
    tasks = TaskManager()
    tasks.add_worker("compute", ComputeWorker(tasks))
    _, filt, _spec = build_chain()
    epoch, token = tasks.bump()
    tasks.submit(ComputeJob(epoch, token, tuple(plan_chain(filt))))
    start = time.perf_counter()
    assert tasks.shutdown(5000) == []
    assert time.perf_counter() - start < 2.0
