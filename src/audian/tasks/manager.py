"""Who owns the worker threads, and what "superseded" means.

Cancellation, staleness and shutdown are one contract, so they live in one
object rather than being reinvented by each worker.

**Epoch.**  Every request bumps a counter and hands out a fresh
`CancelToken`, cancelling the previous one.  A result carries the epoch it
was computed under; if that is no longer the current epoch it is dropped
without being applied, and its buffers are freed.  That is the whole of the
stale-result policy -- there is no queue to inspect and no result to match
up, just an integer compare on the GUI thread.

**Shutdown.**  `shutdown()` cancels, then `quit()` and `wait()` on each
thread with a bounded timeout, and reports which threads failed to stop.  It
never calls `terminate()`: killing a thread mid-`sosfilt` leaves numpy's
allocator in whatever state it was in.  It is idempotent, because it is
called from a close event that Qt is allowed to deliver twice.

**Joining before touching shared memory.**  The compute worker reads the
loader's raw buffer, and `BufferedArray._recycle_buffer` shifts that same
array *in place* on a scroll.  Rather than copy 70 MB per job, the GUI
thread calls `cancel_and_wait()` before it moves or reallocates anything a
worker might be reading.  The kernels are chunked, so that wait is bounded
by one chunk -- about 11 ms at 16 channels, against the 407 ms it replaces.
"""

from __future__ import annotations

import threading
import time

from PySide6.QtCore import QObject, Qt, QThread, Signal

from .tokens import CancelToken


class TaskManager(QObject):
    """Owns the worker threads, the epoch and the in-flight count."""

    #: Queued into every worker's `run` slot. There is one worker, on
    #: purpose -- the pipeline is serial because the kernels do not
    #: parallelise, see `compute.ComputeWorker`.
    sigJob = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._epoch = 0
        self._token = CancelToken()
        self._threads: list[tuple[str, QThread, QObject]] = []
        self._lock = threading.Lock()
        self._idle = threading.Condition(self._lock)
        self._inflight = 0
        self._shut_down = False

    # --- threads ---------------------------------------------------------

    def add_worker(self, name: str, worker: QObject) -> QThread:
        """Give `worker` a thread of its own and start it.

        The worker is parented to nothing on purpose -- a `QObject` moved to
        another thread may not have a parent living on this one -- so the
        manager holds the only reference and `shutdown()` is what releases
        it.  `smoke_test --census` counts parentless *widgets*, not these.
        """
        thread = QThread()
        thread.setObjectName(f"audian-{name}")
        worker.moveToThread(thread)
        run = getattr(worker, "run", None)
        if run is not None:
            self.sigJob.connect(run, Qt.ConnectionType.QueuedConnection)
        thread.start()
        self._threads.append((name, thread, worker))
        return thread

    def submit(self, job) -> None:
        """Queue `job` to the worker, counting it as in flight from now.

        The count is taken here rather than when the worker picks the job
        up, so that `wait_idle()` called on the next line still waits.
        """
        self.job_posted()
        self.sigJob.emit(job)

    def worker(self, name: str):
        for wname, _, obj in self._threads:
            if wname == name:
                return obj
        return None

    # --- epoch -----------------------------------------------------------

    @property
    def epoch(self) -> int:
        return self._epoch

    def bump(self) -> tuple[int, CancelToken]:
        """Supersede everything in flight and open a new generation."""
        self._token.cancel()
        self._epoch += 1
        self._token = CancelToken()
        return self._epoch, self._token

    def is_current(self, epoch: int) -> bool:
        return epoch == self._epoch

    def cancel_all(self) -> None:
        """Abandon everything in flight. Results already computed go stale."""
        self.bump()

    # --- in-flight accounting -------------------------------------------

    def job_posted(self) -> None:
        """Called on the GUI thread when a job is queued to a worker."""
        with self._lock:
            self._inflight += 1

    def job_finished(self) -> None:
        """Called on the worker thread when a job stops, however it stopped."""
        with self._idle:
            if self._inflight > 0:
                self._inflight -= 1
            if self._inflight == 0:
                self._idle.notify_all()

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._inflight > 0

    def wait_idle(self, timeout_ms: int = 5000) -> bool:
        """Block until nothing is in flight. False on timeout."""
        deadline = time.monotonic() + timeout_ms / 1000.0
        with self._idle:
            while self._inflight > 0:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._idle.wait(remaining)
        return True

    def cancel_and_wait(self, timeout_ms: int = 5000) -> bool:
        """Abandon what is running and wait for the worker to notice.

        The bounded stall the whole design rests on: call this before
        touching any array a worker could be reading.
        """
        self.cancel_all()
        return self.wait_idle(timeout_ms)

    # --- shutdown --------------------------------------------------------

    def shutdown(self, timeout_ms: int = 2000) -> list[str]:
        """Stop every thread. Returns the names of those that did not stop.

        A `QThread` nobody joins is a crash at exit, so this is called from
        `Audian.teardown()` before any browser lets go of its recording --
        the worker is reading that recording's buffers.
        """
        if self._shut_down:
            return []
        self._shut_down = True
        self.cancel_all()
        stuck = []
        for name, thread, _ in self._threads:
            try:
                thread.quit()
                if not thread.wait(timeout_ms):
                    stuck.append(name)
            except RuntimeError:
                # Qt already destroyed the thread under the wrapper
                pass
        self._threads.clear()
        return stuck
