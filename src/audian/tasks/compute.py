"""The derived-trace pipeline, off the GUI thread.

This is the commit that pays for the whole exercise.  Refiltering a
16 channel, 20 kHz, 27 s buffer runs `sosfilt` over 70 MB and then a
spectrogram over the result: 407 ms after chunking, 513 ms before it.  On
the GUI thread that is 407 ms in which a 16 ms `QTimer` fires **once** --
the window is not slow, it is gone.  On a `QThread` the same chain leaves
that timer firing 35 times at a 16.0 ms median, because scipy and numpy
release the GIL for the whole of it.

Two things make that safe rather than merely asynchronous.

**The worker allocates its own output.**  It never fills a trace's live
buffer, because the GUI thread goes on painting from that buffer for the
whole time the worker runs.  The result is a set of new arrays, and the GUI
thread swaps them in with one assignment each.

**The GUI joins before it touches shared memory.**  The first step of the
chain reads the loader's raw buffer, and a scroll shifts that array in
place.  So the browser calls `TaskManager.cancel_and_wait()` before it
moves a buffer or changes a filter parameter.  That is a real stall, and it
is bounded by one chunk -- about 11 ms -- against the 407 ms it replaces.
"""

from __future__ import annotations

import traceback
from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import QObject, Signal, Slot

from .tokens import Cancelled, CancelToken, ComputeResult, TraceUpdate


@dataclass(frozen=True)
class TraceStep:
    """One trace's share of a recompute, planned on the GUI thread.

    The shape is decided here rather than in the worker because deciding it
    is what `BufferedData.allocate_buffer` does, and that clamps
    `bufferframes` -- a write to the trace, which belongs on the thread that
    owns it.  The array itself is allocated in the worker, so a job that is
    already superseded when it is picked up allocates nothing at all.
    """

    trace: object
    offset: int
    shape: tuple
    dtype: object
    #: the source's live buffer, used unless an earlier step produced it
    source_buffer: object
    source_offset: int


@dataclass(frozen=True)
class ComputeJob:
    epoch: int
    cancel: CancelToken
    steps: tuple
    #: the browser that asked, echoed back so the others ignore the result
    owner: object = None


def plan_chain(trace) -> list[TraceStep]:
    """The steps `BufferedData.recompute_all()` would have run, as data.

    Walks exactly the same way it does -- a trace that does not need an
    update stops the walk, so nothing derived from something invisible is
    computed.
    """
    steps: list[TraceStep] = []

    def walk(t):
        if not getattr(t, "need_update", False):
            return
        nframes = t.planned_frames()
        if nframes > 0:
            steps.append(
                TraceStep(
                    trace=t,
                    offset=t.offset,
                    shape=(nframes,) + tuple(t.shape[1:]),
                    dtype=t.dtype,
                    source_buffer=t.source.buffer,
                    source_offset=t.source.offset,
                )
            )
        for d in t.dests:
            walk(d)

    walk(trace)
    return steps


def run_job(job: ComputeJob) -> ComputeResult:
    """Compute every step of `job`. Never raises; failures become results."""
    produced: dict[int, tuple] = {}
    updates: list[TraceUpdate] = []
    try:
        for step in job.steps:
            job.cancel.check()
            trace = step.trace
            source = produced.get(id(trace.source))
            if source is None:
                source_buffer, source_offset = step.source_buffer, step.source_offset
            else:
                source_buffer, source_offset = source
            dest = np.empty(step.shape, dtype=step.dtype)
            i0, i1, nbefore = trace.source_window(
                step.offset, step.shape[0], source_offset, len(source_buffer)
            )
            extra = trace.process(
                source_buffer[i0:i1], dest, nbefore, job.cancel
            )
            produced[id(trace)] = (dest, step.offset)
            updates.append(TraceUpdate(trace, dest, step.offset, extra or {}))
    except Cancelled:
        return ComputeResult(job.epoch, cancelled=True, owner=job.owner)
    except Exception:  # noqa: BLE001 - a worker may not raise past here
        return ComputeResult(
            job.epoch, error=traceback.format_exc(), owner=job.owner
        )
    return ComputeResult(job.epoch, tuple(updates), owner=job.owner)


class ComputeWorker(QObject):
    """Runs `ComputeJob`s on the thread it was moved to, one at a time.

    Serial on purpose.  The kernels do release the GIL, but they are
    DRAM-bandwidth-bound at 16 channels -- four threads measured 1.44x,
    1.07x and 0.92x on this machine for `sosfilt`, `spectrogram` and
    `decibel`.  The win being bought is event-loop availability, not
    throughput; a pool sized to cores would buy jitter and memory for
    nothing.  If someone later "optimises" this into a `QThreadPool`, that
    measurement is why it should be reverted.
    """

    sigResultReady = Signal(object)

    def __init__(self, manager, parent=None):
        super().__init__(parent)
        self._manager = manager

    @Slot(object)
    def run(self, job) -> None:
        try:
            result = run_job(job)
        finally:
            self._manager.job_finished()
        self.sigResultReady.emit(result)
