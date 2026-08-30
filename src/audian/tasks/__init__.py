"""Work that does not belong on the GUI thread.

The one measurement this package exists for: a refilter of a 16 channel,
20 kHz, 27 s buffer costs 0.51 s of `sosfilt` and `spectrogram` (0.75 s with
an envelope trace), and on the GUI thread that is 0.51 s in which a 16 ms
`QTimer` fires **once**.  The same chain on a `QThread` leaves it firing 35
times at a 16.0 ms median -- the kernels release the GIL well enough that
the event loop never notices.

What this package is *not* is a thread pool.  At 16 channels each buffer is
34-68 MB and the kernels are DRAM-bandwidth-bound, so four threads measured
1.44x / 1.07x / 0.92x on this machine.  The win being bought here is
event-loop availability, not throughput; sizing a pool to cores would buy
jitter and memory for nothing.
"""

from .tokens import Cancelled, CancelToken, ComputeResult, TraceUpdate

__all__ = ["CancelToken", "Cancelled", "ComputeResult", "TraceUpdate"]
