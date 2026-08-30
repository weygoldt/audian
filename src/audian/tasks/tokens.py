"""Cancellation, and the units of work that carry it.

Nothing here imports a widget or a plotting library -- see
`tests/test_thread_boundary.py`.  These are the plain values that cross the
boundary between the GUI thread and a worker.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field


class Cancelled(Exception):
    """Raised out of a kernel whose `CancelToken` was set."""


class CancelToken:
    """A one-way flag a worker polls between chunks.

    Deliberately a `threading.Event` and not a Qt object: it is read from a
    worker thread and set from the GUI thread, and it must stay usable when
    no event loop is running (at interpreter shutdown, for instance).
    """

    __slots__ = ("_event",)

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def check(self) -> None:
        """Raise `Cancelled` if this job has been superseded."""
        if self._event.is_set():
            raise Cancelled


#: A token that is never set, so a synchronous caller need not build one.
NEVER = CancelToken()


@dataclass(frozen=True)
class TraceUpdate:
    """One trace's freshly computed buffer, ready to be swapped in.

    The worker allocates its own output rather than filling the trace's
    live buffer, because the GUI thread goes on painting from that buffer
    for the whole time the worker is running.  The swap happens in one
    statement on the GUI thread; see `BufferedData.apply_update`.
    """

    #: the `BufferedData` (or the raw loader) this update belongs to
    trace: object
    buffer: object
    offset: int
    #: trace-specific scalars the kernel also derived, applied with the swap
    extra: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ComputeResult:
    """What a compute job hands back to the GUI thread.

    `epoch` is what makes a superseded result cheap to discard: the manager
    bumps its epoch on every new request, and a result whose epoch is no
    longer current is dropped without being applied.  `error` carries a
    formatted traceback rather than the exception, so nothing that crossed
    a thread keeps a frame alive.
    """

    epoch: int
    updates: tuple = ()
    cancelled: bool = False
    error: str | None = None
    #: which browser asked; results are ignored by everyone else
    owner: object = None
