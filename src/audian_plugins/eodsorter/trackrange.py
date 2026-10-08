"""The tracking range: where in the recording "Track recording" runs.

Pure helpers (numpy and the model's `FrameGrid` only) for the Range row of
the session header (design 4.2 and 5.8):

* times as the reader types and reads them (``hh:mm:ss.s``, ``mm:ss``,
  seconds, ``end``);
* a range clamped to the recording (`clamp_range`);
* the frames and the ``start``/``duration`` a range is run with
  (`detect_window`), snapped to the frame grid of a whole-recording run so
  the detections of a range run sit on the same frames, at the same
  absolute times, as those of a run over everything;
* the range a results directory was made with (`range_of_grid`);
* the key a range is remembered under (`range_key`).

A range is ``(start, stop)`` in seconds from the start of the recording;
``stop`` is None for "to the end".  No range (None) is the whole recording.
"""

from __future__ import annotations

import math
import os
from typing import NamedTuple, Optional, Sequence

from .model import FrameGrid, step_size


class TrackRange(NamedTuple):
    start: float
    stop: Optional[float]  # None: to the end of the recording


class RangeError(ValueError):
    """Text that is not a time, or a range that is empty."""


# ------------------------------------------------------------------ text


def parse_time(text: str) -> float:
    """Seconds from ``1:02:03.4``, ``02:03``, ``123.5`` (blanks ignored).

    Raises `RangeError` for anything else, negative times included."""
    s = str(text).strip().replace(" ", "")
    if not s:
        raise RangeError("no time given")
    parts = s.split(":")
    if len(parts) > 3:
        raise RangeError(f"{text!r} is not a time (hh:mm:ss.s)")
    try:
        values = [float(p) for p in parts]
    except ValueError:
        raise RangeError(f"{text!r} is not a time (hh:mm:ss.s)") from None
    if any(not math.isfinite(v) or v < 0 for v in values):
        raise RangeError(f"{text!r} is not a time (hh:mm:ss.s)")
    if len(parts) > 1 and (any(v != int(v) for v in values[:-1]) or values[-1] >= 60):
        raise RangeError(f"{text!r} is not a time (hh:mm:ss.s)")
    if len(parts) == 3 and values[1] >= 60:
        raise RangeError(f"{text!r} is not a time (hh:mm:ss.s)")
    t = 0.0
    for v in values:
        t = 60.0 * t + v
    return t


def parse_stop(text: str) -> Optional[float]:
    """Like `parse_time`, but empty text or ``end`` is None (the end)."""
    s = str(text).strip().lower()
    if s in ("", "end"):
        return None
    return parse_time(s)


def format_time(t: float, hours: bool = True) -> str:
    """``00:04:30.0``: hours, minutes, seconds and tenths; ``04:30.0``
    without `hours` (minutes then count on past 59)."""
    tenths = int(round(max(0.0, float(t)) * 10))
    s, d = divmod(tenths, 10)
    m, s = divmod(s, 60)
    if not hours:
        return f"{m:02d}:{s:02d}.{d}"
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}.{d}"


def needs_hours(duration: Optional[float]) -> bool:
    """Whether times in a recording this long are written with hours."""
    return duration is None or duration >= 3600.0


def format_clock(t: float, hours: bool = True) -> str:
    """``00:04:30`` (``04:30`` without `hours`): to the second."""
    s = int(round(max(0.0, float(t))))
    m, s = divmod(s, 60)
    if not hours:
        return f"{m:02d}:{s:02d}"
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def range_label(r: Optional[TrackRange], duration: Optional[float] = None) -> str:
    """The Track button's text for a range (None: "Track recording").

    ``Track 00:04:30–00:58:10``; on a recording shorter than an hour the
    hours are left out (``Track 04:30–58:10``), which the narrow panel's
    half-width button has room for."""
    if r is None:
        return "Track recording"
    hours = needs_hours(duration)
    stop = "end" if r.stop is None else format_clock(r.stop, hours)
    return f"Track {format_clock(r.start, hours)}–{stop}"


# ------------------------------------------------------------------ ranges


def clamp_range(
    start: Optional[float], stop: Optional[float], duration: Optional[float]
) -> Optional[TrackRange]:
    """`start`..`stop` clamped to a recording of `duration` seconds.

    A missing start is 0, a stop at or past the end is None ("end"), and a
    range that is the whole recording is None.  Raises `RangeError` when
    nothing is left (start at or past stop, or past the end)."""
    t0 = 0.0 if start is None else max(0.0, float(start))
    t1 = None if stop is None else max(0.0, float(stop))
    if duration is not None and duration > 0:
        if t0 >= duration:
            raise RangeError(
                f"from {format_time(t0)} is past the end of the recording "
                f"({format_time(duration)})"
            )
        if t1 is not None and t1 >= duration:
            t1 = None
    if t1 is not None and t1 <= t0:
        raise RangeError(
            f"from {format_time(t0)} must come before to {format_time(t1)}"
        )
    if t0 <= 0.0 and t1 is None:
        return None
    return TrackRange(t0, t1)


def recording_grid(
    rate: float,
    n_samples: int,
    nfft: int,
    overlap_frac: float,
    session: Optional[FrameGrid] = None,
) -> FrameGrid:
    """The frames a run over the whole recording has.

    That is `FrameGrid.for_recording`, unless a session is loaded whose grid
    does not start on that one (results of a run that began at an odd
    sample): then the session's frames, extended over the whole recording,
    so a range lines up with the session's frames as a snippet does."""
    if session is not None:
        step = int(session.step)
        nfft = int(session.nfft)
        rate = float(session.rate)
        s0 = int(session.s0) % step
    else:
        step = step_size(int(nfft), float(overlap_frac))
        s0 = 0
    n = int(n_samples) - s0
    n_frames = 0 if n < nfft else (n - nfft) // step + 1
    return FrameGrid(float(rate), int(nfft), int(step), s0, int(n_frames))


class DetectWindow(NamedTuple):
    k0: int  # first frame of the whole-recording grid
    k1: int  # one past the last
    start: float  # seconds, wavetracker's ``start``
    duration: Optional[float]  # seconds, wavetracker's ``duration`` (None: to the end)


def detect_window(grid: FrameGrid, r: Optional[TrackRange]) -> DetectWindow:
    """What a range run asks wavetracker for, on `grid` (`recording_grid`).

    The frames are those whose centre lies in the range, as for a snippet
    (`FrameGrid.frame_range`); ``start`` is the first sample of frame `k0`
    and ``duration`` reaches the last sample of frame ``k1 - 1``, so
    wavetracker's frame ``j`` is the whole-recording frame ``k0 + j`` and
    its times are the same absolute times.  To the end of the recording,
    ``duration`` is None (wavetracker's frames then end where a whole run's
    do).  Raises `RangeError` with fewer than two frames."""
    n = grid.n_frames
    if r is None:
        k0, k1 = 0, n
    else:
        stop = math.inf if r.stop is None else r.stop
        k0, k1 = grid.frame_range(r.start, stop)
    if k1 - k0 < 2:
        need = (grid.nfft + grid.step) / grid.rate
        raise RangeError(
            f"the range is too short: wavetracker needs at least two FFT "
            f"windows, {need:.1f} s here"
        )
    s0, s1 = grid.sample_range(k0, k1)
    start = s0 / grid.rate
    duration = None if k1 == n else (s1 - s0) / grid.rate
    return DetectWindow(int(k0), int(k1), float(start), duration)


def range_of_grid(results: FrameGrid, whole: FrameGrid) -> Optional[TrackRange]:
    """The range a results directory covers, if it is not the whole
    recording: from its first frame's centre to its last's (to the end when
    its last frame is the whole run's last), which `detect_window` maps back
    onto exactly these frames.  None for results of a whole run."""
    if results.n_frames == 0 or results.step != whole.step:
        return None
    times = results.times()
    first, last = float(times[0]), float(times[-1])
    w = whole.times()
    if not len(w):
        return None
    at_start = first <= float(w[0]) + 0.5 / results.rate
    at_end = last >= float(w[-1]) - 0.5 / results.rate
    if at_start and at_end:
        return None
    return TrackRange(0.0 if at_start else first, None if at_end else last)


def range_key(paths: Sequence[str]) -> str:
    """One key per recording: its file, or its files in order."""
    return "\n".join(os.path.abspath(os.fspath(p)) for p in paths)
