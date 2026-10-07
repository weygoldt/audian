"""The EOD sorter's data model: wavetracker's arrays, edits, history, files.

numpy and the standard library only (no Qt, no `audian`, no `wavetracker`).
Design: ``docs/eodsorter-design.md`` sections 3, 5.11, 7.1 and 8.  The disk
layer lives in `model_io` and its public names are re-exported here.

Deviations from the spec's interface (all additive):

* `Plan` has two extra fields with defaults, ``meta`` (label/note changes,
  ``((key, id), old, new)`` triples) and ``next_id`` (the counter value the
  plan needs).  `Command` has the matching extra fields plus ``n_before``.
* `TrackSet.load` takes optional ``recording_paths`` and ``duration`` to
  produce the recording-related complaints of 8.5.
* Extra methods/functions: `TrackSet.plan_recover(autosave)` (8.3 recovery
  as one command), `TrackSet.n_tracked`, `discard_autosave(folder)`,
  `local_median_losers(...)` (the conflict rule, exposed for tests).
* `save` calls `mark_saved` itself and deletes the autosave.
* ``plan_add``: "several candidates in one frame" keep the one the
  local-median rule keeps (the stroke's own frequency course), since the
  signature carries no stroke centre.  With no electrode count known yet
  and no ``sign`` given, the session gets one electrode (NaN power).
* ``dropped`` lists every conflict loser, including those that keep their
  old id (``plan_new_id``, ``plan_assign``), as the table in 3.4 says.
* Rows appended from a snippet have ``tracked`` = NaN like hand-added rows
  (they are not the whole run's output; "Revert to tracker output"
  unassigns them, which keeps the revert free of conflicts).
* ``sign``/``cplx`` keep their on-disk float precision, so a rewrite is
  byte-identical for existing rows.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from .model_io import (
    AUTOSAVE_FILE,
    IDENT_FILE,
    META_FILE,
    SORTER_FILE,
    SORTER_VERSION,
    TRACKED_FILE,
    Append,
    Autosave,
    ResultsError,
    check_ident,
    commit_files,
    discard_autosave,
    finish_interrupted_save,
    ident_mtime,
    ident_variants,
    json_writer,
    now_iso,
    npy_rows,
    npy_writer,
    read_autosave,
    read_results,
    write_autosave_file,
)

__all__ = [
    "AUTOSAVE_FILE",
    "Append",
    "Autosave",
    "Change",
    "CleanupSetup",
    "Command",
    "EditRejected",
    "FrameGrid",
    "History",
    "Issue",
    "Plan",
    "ResultsError",
    "Snippet",
    "TrackSet",
    "cleanup_setup",
    "discard_autosave",
    "finish_interrupted_save",
    "ident_variants",
    "load_snippet",
    "local_median_losers",
    "read_autosave",
    "step_size",
]

#: wavetracker's `resolve_duplicates` window [frames].
WINDOW = 30
#: wavetracker's `TrackingConfig` defaults, used when a session has no config.
DEFAULT_MAX_DT = 10.0
DEFAULT_FREQ_TOLERANCE = 2.5

_EMPTY = np.empty(0, np.int64)
_EMPTY.flags.writeable = False


class EditRejected(ValueError):
    """An edit that cannot be done; `.args[0]` is the reader-facing reason."""


def step_size(nfft: int, overlap_frac: float) -> int:
    """`wavetracker.spectrogram.step_size`, copied."""
    return max(1, int(nfft * (1.0 - overlap_frac)))


def fmt_time(t: float) -> str:
    """01:12.4 or 1:02:03.4."""
    if not np.isfinite(t):
        return "?"
    neg = t < 0
    t = abs(float(t))
    m, s = divmod(t, 60.0)
    h, m = divmod(int(m), 60)
    text = f"{h}:{m:02d}:{s:04.1f}" if h else f"{m:02d}:{s:04.1f}"
    return "-" + text if neg else text


def _folder_key(folder) -> str:
    """One spelling per directory, to tell "the folder we loaded" apart."""
    try:
        return str(Path(folder).resolve())
    except OSError:
        return str(Path(folder).absolute())


def _fid(i) -> str:
    return str(int(i))


def _readonly(a: np.ndarray) -> np.ndarray:
    v = a.view()
    v.flags.writeable = False
    return v


# --------------------------------------------------------------------------
# frame grid


@dataclass(frozen=True)
class FrameGrid:
    rate: float  # Hz
    nfft: int  # samples
    step: int  # samples, = max(1, int(nfft * (1 - overlap_frac)))
    s0: int  # first sample of frame 0
    n_frames: int

    def _centre(self, k):
        return (self.s0 + np.asarray(k) * self.step + self.nfft / 2) / self.rate

    def times(self) -> np.ndarray:
        k = np.arange(self.n_frames)
        return (self.s0 + k * self.step + self.nfft / 2) / self.rate

    def frame_range(self, t0: float, t1: float) -> tuple[int, int]:
        """Frames whose centre lies in [t0, t1]: (k0, k1), k1 exclusive,
        clipped to [0, n_frames)."""
        n = self.n_frames
        if n == 0 or not t1 >= t0:
            return (0, 0)
        a = (t0 * self.rate - self.s0 - self.nfft / 2) / self.step
        b = (t1 * self.rate - self.s0 - self.nfft / 2) / self.step
        k0 = int(np.clip(np.ceil(a), 0, n)) if np.isfinite(a) else (0 if a < 0 else n)
        k1 = (
            int(np.clip(np.floor(b) + 1, 0, n))
            if np.isfinite(b)
            else (0 if b < 0 else n)
        )
        while k0 > 0 and self._centre(k0 - 1) >= t0:
            k0 -= 1
        while k0 < n and self._centre(k0) < t0:
            k0 += 1
        while k1 < n and self._centre(k1) <= t1:
            k1 += 1
        while k1 > 0 and self._centre(k1 - 1) > t1:
            k1 -= 1
        return (k0, max(k0, k1))

    def sample_range(self, k0: int, k1: int) -> tuple[int, int]:
        """Samples a run of frames k0..k1-1 needs:
        [s0 + k0*step, s0 + (k1-1)*step + nfft)."""
        return (self.s0 + k0 * self.step, self.s0 + (k1 - 1) * self.step + self.nfft)

    @classmethod
    def for_recording(cls, rate, n_samples, nfft, overlap_frac) -> FrameGrid:
        """The grid wavetracker would use for the whole recording (s0 = 0)."""
        step = step_size(int(nfft), float(overlap_frac))
        n = int(n_samples)
        n_frames = 0 if n < nfft else (n - int(nfft)) // step + 1
        return cls(float(rate), int(nfft), step, 0, n_frames)

    @classmethod
    def from_results(cls, meta: dict, times: np.ndarray) -> FrameGrid | None:
        """From wavetracker.json (rate, start, frame_step,
        config.spectrogram.nfft).  None when `times` does not match the
        formula within 1e-6 s at every frame."""
        try:
            rate = float(meta["rate"])
            spec = meta["config"]["spectrogram"]
            nfft = int(spec["nfft"])
            s0 = int(round(float(meta.get("start", 0.0)) * rate))
            if "frame_step" in meta:
                step = int(round(float(meta["frame_step"]) * rate))
            else:
                step = step_size(nfft, float(spec["overlap_frac"]))
        except (KeyError, TypeError, ValueError):
            return None
        if rate <= 0 or nfft < 1 or step < 1:
            return None
        times = np.asarray(times, dtype=np.float64)
        grid = cls(rate, nfft, step, s0, len(times))
        if len(times) and np.max(np.abs(grid.times() - times)) > 1e-6:
            return None
        return grid


# --------------------------------------------------------------------------
# wavetracker's cleanup, fitted to the session


#: wavetracker cleanup's own defaults (cleanup_config_default.cfg), used as
#: they are on a session long enough for them.
CLEANUP_STRIDE_MIN = 10.0
CLEANUP_TIME_TOLERANCE_MIN = 5.0
CLEANUP_OVERLAP = 0.2


@dataclass
class CleanupSetup:
    """What `cleanup` should run on for this session, and its defaults.

    wavetracker's cleanup walks windows of `stride` from time 0 and keeps
    the ids whose frequency density in a window beats a threshold scaled by
    the number of frames in the first window.  A session built from a short
    snippet keeps the whole recording's frame grid, so with the default
    10-minute stride the threshold counts minutes of frames the snippet
    never had, and every id fails ("no identity passed the
    frequency-density selection").  Hence: the input is cut to the frames
    that hold detections (`k0`, `k1`, times from 0), and the stride is at
    most that span.
    """

    k0: int
    k1: int  # exclusive
    span_s: float
    n_fish: int
    stride_minutes: float
    time_tolerance_minutes: float
    #: why cleanup cannot run here, or None
    reason: str | None = None

    def arrays(self, ts) -> dict:
        """The five arrays cleanup reads, cut to frames [k0, k1)."""
        times = np.asarray(ts.times, dtype=np.float64)[self.k0 : self.k1]
        return {
            "fund_v": np.asarray(ts.fund),
            "idx_v": np.asarray(ts.idx) - self.k0,
            "ident_v": np.asarray(ts.ident),
            "sign_v": np.asarray(ts.sign),
            "times": times - (times[0] if len(times) else 0.0),
        }


def typical_concurrent_ids(idx, ident) -> int:
    """The median number of ids with a detection in a frame, over the
    frames that have any: how many fish the tracker sees at once."""
    idx = np.asarray(idx)
    ident = np.asarray(ident, dtype=np.float64)
    m = np.isfinite(ident)
    if not m.any():
        return 0
    pairs = np.unique(np.stack([idx[m].astype(np.float64), ident[m]], axis=1), axis=0)
    _, per_frame = np.unique(pairs[:, 0], return_counts=True)
    return max(1, int(round(float(np.median(per_frame)))))


def cleanup_setup(ts) -> CleanupSetup:
    """Fit wavetracker's cleanup to `ts` (see `CleanupSetup`)."""
    n = ts.n
    idx = np.asarray(ts.idx)
    times = np.asarray(ts.times, dtype=np.float64)
    if n == 0 or not len(times):
        return CleanupSetup(0, 0, 0.0, 0, 0.0, 0.0, "there are no detections")
    k0, k1 = int(idx.min()), int(idx.max()) + 1
    span = float(times[k1 - 1] - times[k0])
    stride_s = min(CLEANUP_STRIDE_MIN * 60.0, span)
    tol_s = min(CLEANUP_TIME_TOLERANCE_MIN * 60.0, stride_s / 2)
    n_fish = typical_concurrent_ids(idx, ts.ident)
    reason = None
    step = int(stride_s * (1 - CLEANUP_OVERLAP))
    if n_fish == 0:
        reason = "no detection has an id"
    elif step < 1:  # cleanup would step its windows by 0 s
        reason = (
            f"the tracks span {span:.1f} s: too short for cleanup's windows "
            "(run it on a longer stretch)"
        )
    return CleanupSetup(k0, k1, span, n_fish, stride_s / 60.0, tol_s / 60.0, reason)


# --------------------------------------------------------------------------
# the local-median rule


def _row_median(vals: np.ndarray, cnt: np.ndarray) -> np.ndarray:
    """Median of the first `cnt` entries of each sorted row (np.median's
    arithmetic: the middle value, or the mean of the two middle values)."""
    r = np.arange(len(vals))
    c = np.maximum(cnt, 1)
    a = vals[r, (c - 1) // 2]
    b = vals[r, c // 2]
    return np.where(c % 2 == 1, a, (a + b) / 2)


def local_median_losers(
    fund, idx, ident, order=None, window: int = WINDOW
) -> np.ndarray:
    """Boolean mask of the rows the local-median rule drops.

    Wherever several rows share an id and a frame, keep the one closest to
    the median frequency of that id's rows in the other frames within
    ±`window` frames (or of the group itself when there are none); ties go
    to the smallest `order`.  Exactly wavetracker's `resolve_duplicates`
    (``stitching.py``), vectorised."""
    fund = np.asarray(fund, dtype=np.float64)
    idx = np.asarray(idx, dtype=np.int64)
    ident = np.asarray(ident, dtype=np.float64)
    m = len(fund)
    out = np.zeros(m, dtype=bool)
    if m < 2:
        return out
    order = np.arange(m) if order is None else np.asarray(order)
    valid = np.flatnonzero(~np.isnan(ident))
    if len(valid) < 2:
        return out
    srt = valid[np.lexsort((order[valid], idx[valid], ident[valid]))]
    i, k = ident[srt], idx[srt]
    same = (i[1:] == i[:-1]) & (k[1:] == k[:-1])
    if not same.any():
        return out
    gstart = np.flatnonzero(np.r_[True, ~same])
    gend = np.r_[gstart[1:], len(srt)]
    dup = np.flatnonzero(gend - gstart > 1)
    gs, ge = gstart[dup], gend[dup]
    _, rank = np.unique(i, return_inverse=True)
    kk = k - k.min()
    span = int(kk.max()) + 2 * window + 2
    key = rank.astype(np.int64) * span + kk + window
    lo = np.searchsorted(key, key[gs] - window, "left")
    hi = np.searchsorted(key, key[gs] + window, "right")
    f = fund[srt]
    last = len(f) - 1
    ref = np.empty(len(dup))
    width = int((hi - lo).max())
    chunk = max(1, 4_000_000 // max(width, 1))
    ar = np.arange(width)
    for c0 in range(0, len(dup), chunk):
        sl = slice(c0, c0 + chunk)
        pos = lo[sl, None] + ar
        own = (pos >= gs[sl, None]) & (pos < ge[sl, None])
        use = (pos < hi[sl, None]) & ~own
        vals = np.where(use, f[np.minimum(pos, last)], np.nan)
        vals.sort(axis=1)
        cnt = use.sum(axis=1)
        med = _row_median(vals, cnt)
        none = cnt == 0
        if none.any():
            vo = np.where(own[none], f[np.minimum(pos[none], last)], np.nan)
            vo.sort(axis=1)
            med[none] = _row_median(vo, own[none].sum(axis=1))
        ref[sl] = med
    lens = ge - gs
    g = np.repeat(np.arange(len(dup)), lens)
    elem = np.repeat(gs, lens) + (
        np.arange(lens.sum()) - np.repeat(np.cumsum(lens) - lens, lens)
    )
    dist = np.abs(f[elem] - ref[g])
    o = np.lexsort((elem, dist, g))
    first = np.r_[True, g[o][1:] != g[o][:-1]]
    losers = elem[o][~first]
    out[srt[losers]] = True
    return out


# --------------------------------------------------------------------------
# plans, changes, history


@dataclass(frozen=True)
class Plan:
    label: str  # one line for the history
    rows: np.ndarray  # int64, rows whose ident changes, sorted
    new: np.ndarray  # float64, their new ident (NaN = unassign)
    dropped: np.ndarray  # int64, conflict losers
    append: Append | None  # rows to add
    created: tuple[int, ...]  # ids this plan creates
    span: tuple[float, float, float, float]  # t0, t1, f0, f1 touched
    revision: int  # the model revision the plan was made at
    meta: tuple | None = None  # (((key, id), old, new), ...) for label/note edits
    next_id: int | None = None  # next_id needed after this plan


@dataclass(frozen=True)
class Change:
    revision: int
    ids: np.ndarray  # every id whose rows changed (old and new), no NaN
    rows: np.ndarray  # rows whose ident changed
    appended: int  # rows added (>0) or removed by undo (<0)
    frames: tuple[int, int]  # [k0, k1) touched
    unassigned: bool  # whether the unassigned pool changed
    label: str


@dataclass
class Command:
    label: str
    rows: np.ndarray  # int32 if n < 2**31, else int64
    old: np.ndarray  # float64
    new: np.ndarray | float  # scalar when every row got the same id
    append: Append | None
    meta: tuple | None
    span: tuple
    time: float
    n_before: int = 0
    created: tuple = ()
    next_id: int | None = None

    @property
    def nbytes(self) -> int:
        b = self.rows.nbytes + self.old.nbytes
        b += self.new.nbytes if isinstance(self.new, np.ndarray) else 8
        return b + (self.append.nbytes if self.append is not None else 0) + 200


class History:
    MAX_DEPTH = 200
    MAX_BYTES = 256 * 2**20

    def __init__(self, max_depth: int | None = None, max_bytes: int | None = None):
        if max_depth is not None:
            self.MAX_DEPTH = max_depth
        if max_bytes is not None:
            self.MAX_BYTES = max_bytes
        self.entries: list[Command] = []
        self.position = 0
        #: entry count matching the file on disk; None: no longer reachable
        self.saved_position: int | None = 0
        #: how many of the oldest entries were dropped, and the time of the
        #: newest dropped one ("history starts at ...")
        self.dropped = 0
        self.dropped_until: float | None = None
        self.nbytes = 0

    def can_undo(self) -> bool:
        return self.position > 0

    def can_redo(self) -> bool:
        return self.position < len(self.entries)

    def labels(self) -> list[str]:
        return [c.label for c in self.entries]

    def push(self, cmd: Command) -> None:
        for c in self.entries[self.position :]:
            self.nbytes -= c.nbytes
        del self.entries[self.position :]
        if self.saved_position is not None and self.saved_position > self.position:
            self.saved_position = None
        self.entries.append(cmd)
        self.nbytes += cmd.nbytes
        self.position += 1
        while len(self.entries) > self.MAX_DEPTH or (
            self.nbytes > self.MAX_BYTES and len(self.entries) > 1
        ):
            old = self.entries.pop(0)
            self.nbytes -= old.nbytes
            self.position -= 1
            self.dropped += 1
            self.dropped_until = old.time
            if self.saved_position is not None:
                self.saved_position = (
                    None if self.saved_position == 0 else self.saved_position - 1
                )


# --------------------------------------------------------------------------
# snippets and issues


@dataclass(frozen=True)
class Snippet:
    k0: int  # session frames covered: [k0, k1)
    k1: int
    fund: np.ndarray
    idx: np.ndarray  # already in session frames
    ident: np.ndarray  # the snippet's own ids (local; remapped on accept)
    sign: np.ndarray
    cplx: np.ndarray | None
    meta: dict = field(default_factory=dict)


def load_snippet(run_dir, grid: FrameGrid, k0: int) -> Snippet:
    """Read a run's directory, add k0 to idx_v, and check that the run's
    times + offset equal grid.times()[k0:k0+len(times)] within 1e-6 s.
    Raises ResultsError if not."""
    d = read_results(run_dir)
    times = d["times"]
    m = len(times)
    k0 = int(k0)
    if k0 < 0 or k0 + m > grid.n_frames:
        raise ResultsError(
            f"the snippet run has {m} frames from frame {k0}; the session has {grid.n_frames}"
        )
    offset = (grid.s0 + k0 * grid.step) / grid.rate
    expect = grid.times()[k0 : k0 + m]
    if m and np.max(np.abs(times + offset - expect)) > 1e-6:
        raise ResultsError(
            "the snippet's frames do not line up with the session's "
            f"(off by up to {np.max(np.abs(times + offset - expect)):.3g} s); "
            "check the sampling rate and FFT settings"
        )
    return Snippet(
        k0=k0,
        k1=k0 + m,
        fund=d["fund"],
        idx=d["idx"] + k0,
        ident=d["ident"],
        sign=d["sign"],
        cplx=d["cplx"],
        meta=d["meta"],
    )


@dataclass(frozen=True)
class Issue:
    kind: str
    t: float
    f: float
    ids: tuple[float, ...]
    text: str
    suggestion: Callable[[TrackSet], Plan] | None


STATS_DTYPE = np.dtype(
    [
        ("id", "f8"),
        ("n", "i8"),
        ("k_first", "i8"),
        ("k_last", "i8"),
        ("t_first", "f8"),
        ("t_last", "f8"),
        ("f_min", "f8"),
        ("f_median", "f8"),
        ("f_max", "f8"),
    ]
)


# --------------------------------------------------------------------------
# the track set


def _dup_groups(ident, idx, rows) -> list[np.ndarray]:
    """Groups of `rows` sharing an id and a frame."""
    rows = np.asarray(rows, dtype=np.int64)
    if len(rows) < 2:
        return []
    i, k = ident[rows], idx[rows]
    ok = ~np.isnan(i)
    rows, i, k = rows[ok], i[ok], k[ok]
    o = np.lexsort((rows, k, i))
    rows, i, k = rows[o], i[o], k[o]
    same = (i[1:] == i[:-1]) & (k[1:] == k[:-1])
    if not same.any():
        return []
    starts = np.flatnonzero(np.r_[True, ~same])
    ends = np.r_[starts[1:], len(rows)]
    return [rows[a:b] for a, b in zip(starts, ends) if b - a > 1]


class TrackSet:
    """wavetracker's per-detection arrays plus identities, edited through
    plans and a history (design section 3)."""

    def __init__(self):
        raise TypeError("use TrackSet.empty, TrackSet.from_arrays or TrackSet.load")

    # ---------------------------------------------------------------- build

    @classmethod
    def _new(cls) -> TrackSet:
        return object.__new__(cls)

    @classmethod
    def empty(cls, grid: FrameGrid, meta: dict | None = None) -> TrackSet:
        ts = cls._new()
        ts._setup(
            np.empty(0),
            np.empty(0, np.int64),
            np.empty(0),
            np.empty(0),
            None,
            None,
            grid.times(),
            dict(meta or {}),
            grid,
            None,
        )
        return ts

    @classmethod
    def from_arrays(
        cls,
        fund,
        idx,
        ident,
        sign,
        times,
        cplx=None,
        meta=None,
        tracked=None,
        next_id=None,
        grid=None,
    ) -> TrackSet:
        fund = np.asarray(fund, dtype=np.float64).ravel()
        n = len(fund)
        idx = np.asarray(idx)
        if idx.shape != (n,):
            raise ResultsError(f"idx has shape {idx.shape}, fund has {n} rows")
        if not np.issubdtype(idx.dtype, np.integer):
            if np.any(idx != np.round(idx)):
                raise ResultsError("idx holds non-integer frame indices")
        idx = idx.astype(np.int64)
        times = np.asarray(times, dtype=np.float64)
        if n and (idx.min() < 0 or idx.max() >= len(times)):
            raise ResultsError("idx points outside times")
        ident = check_ident(ident, n, "ident")
        tracked = (
            ident.copy() if tracked is None else check_ident(tracked, n, "tracked")
        )
        sign = np.asarray(sign)
        if not np.issubdtype(sign.dtype, np.floating):
            sign = sign.astype(np.float32)
        if sign.ndim != 2 or len(sign) != n:
            raise ResultsError(
                f"sign has shape {sign.shape}, expected ({n}, electrodes)"
            )
        if cplx is not None:
            cplx = np.asarray(cplx)
            if not np.iscomplexobj(cplx):
                cplx = cplx.astype(np.complex64)
            if cplx.shape != sign.shape:
                raise ResultsError(
                    f"cplx has shape {cplx.shape}, sign has {sign.shape}"
                )
        meta = dict(meta or {})
        if grid is None and meta:
            grid = FrameGrid.from_results(meta, times)
        if grid is not None and grid.n_frames != len(times):
            raise ResultsError("the frame grid does not match times")
        ts = cls._new()
        ts._setup(fund, idx, ident, tracked, sign, cplx, times, meta, grid, next_id)
        return ts

    def _setup(self, fund, idx, ident, tracked, sign, cplx, times, meta, grid, next_id):
        n = len(fund)
        self._n = n
        self._fund = np.array(fund, dtype=np.float64)
        self._idx = np.array(idx, dtype=np.int64)
        self._ident = np.array(ident, dtype=np.float64)
        self._tracked = np.array(tracked, dtype=np.float64)
        self._c = None if sign is None else int(sign.shape[1])
        self._c0 = self._c
        self._sign = None if sign is None else np.array(sign)
        self._has_cplx = cplx is not None
        self._cplx = None if cplx is None else np.array(cplx)
        self._times = _readonly(np.array(times, dtype=np.float64))
        self.grid: FrameGrid | None = grid
        self.meta: dict = meta
        self.labels: dict[int, str] = {}
        self.notes: dict[int, str] = {}
        seen = [int(next_id)] if next_id is not None else []
        for a in (self._ident, self._tracked):
            if n and not np.all(np.isnan(a)):
                seen.append(int(np.nanmax(a)) + 1)
        self.next_id: int = max(seen) if seen else 0
        self.revision = 0
        self.history = History()
        self.n_tracked = n
        self._n_saved = n
        #: bumped whenever rows are appended or truncated: the per-detection
        #: arrays on disk are current only for the `_saved_rows` stamp
        self._rows_rev = 0
        #: (resolved folder, `_rows_rev`) of the last load or save, None for
        #: a session that has never been on disk
        self._saved_rows: tuple[str, int] | None = None
        #: mtime of ident_v.npy when it was last loaded or saved: what an
        #: autosave is based on (8.3)
        self._base_mtime = 0.0
        self._dtypes: dict = {}
        self._snippet_meta: dict | None = None
        self._meta_dirty = False
        # duplicates present in the input are left alone (8.5); remember them
        self._allowed: list[tuple[np.ndarray, np.ndarray]] = []
        for a in (self._ident, self._tracked):
            groups = _dup_groups(a, self._idx, np.arange(n))
            if groups:
                lab = np.concatenate([np.full(len(g), j) for j, g in enumerate(groups)])
                r = np.concatenate(groups)
                o = np.argsort(r)
                self._allowed.append((r[o], lab[o]))
        self._n_input_dups = sum(
            len(g) - 1 for g in _dup_groups(self._ident, self._idx, np.arange(n))
        )
        # indexes
        self._by_frame = np.argsort(self._idx, kind="stable").astype(np.int64)
        self._bf_k = self._idx[self._by_frame]
        self._rows_of: dict[float, np.ndarray] = {}
        self._ids_cache: np.ndarray | None = None
        self._stats_cache: np.ndarray | None = None
        self._stats_dirty: set[float] = set()
        valid = np.flatnonzero(~np.isnan(self._ident))
        self._index_rows(valid)

    # ------------------------------------------------------------ read side

    @property
    def n(self) -> int:
        return self._n

    @property
    def n_channels(self) -> int | None:
        return self._c

    @property
    def has_cplx(self) -> bool:
        return self._has_cplx

    @property
    def fund(self) -> np.ndarray:
        return _readonly(self._fund[: self._n])

    @property
    def idx(self) -> np.ndarray:
        return _readonly(self._idx[: self._n])

    @property
    def ident(self) -> np.ndarray:
        return _readonly(self._ident[: self._n])

    @property
    def tracked(self) -> np.ndarray:
        return _readonly(self._tracked[: self._n])

    @property
    def sign(self) -> np.ndarray:
        if self._sign is None:
            return _readonly(np.empty((0, 0), np.float32))
        return _readonly(self._sign[: self._n])

    @property
    def cplx(self) -> np.ndarray | None:
        return None if self._cplx is None else _readonly(self._cplx[: self._n])

    @property
    def times(self) -> np.ndarray:
        return self._times

    def is_dirty(self) -> bool:
        h = self.history
        return h.saved_position != h.position or self._meta_dirty

    def frame_time(self, k) -> np.ndarray:
        return self._times[np.asarray(k, dtype=np.int64)]

    def ids(self) -> np.ndarray:
        if self._ids_cache is None:
            self._ids_cache = _readonly(
                np.array(sorted(self._rows_of), dtype=np.float64)
            )
        return self._ids_cache

    def rows_of(self, id: float) -> np.ndarray:
        if id is None or np.isnan(id):
            return _EMPTY
        return self._rows_of.get(float(id), _EMPTY)

    def rows_in_frames(self, k0: int, k1: int) -> np.ndarray:
        a = np.searchsorted(self._bf_k, k0, "left")
        b = np.searchsorted(self._bf_k, k1, "left")
        return _readonly(self._by_frame[a:b])

    def _rows_of_ids(self, ids) -> np.ndarray:
        parts = [self.rows_of(i) for i in ids]
        return np.concatenate(parts) if parts else _EMPTY

    def _t(self, rows) -> np.ndarray:
        return self._times[self._idx[rows]]

    def stats(self, ids=None) -> np.ndarray:
        """Structured array (`STATS_DTYPE`), one entry per id with points,
        sorted by id."""
        if self._stats_cache is None:
            self._stats_cache = self._compute_stats(self.ids())
            self._stats_dirty.clear()
        elif self._stats_dirty:
            dirty = np.array(sorted(self._stats_dirty), dtype=np.float64)
            keep = self._stats_cache[~np.isin(self._stats_cache["id"], dirty)]
            fresh = self._compute_stats(dirty[np.isin(dirty, self.ids())])
            allst = np.concatenate([keep, fresh])
            self._stats_cache = allst[np.argsort(allst["id"], kind="stable")]
            self._stats_dirty.clear()
        st = self._stats_cache
        if ids is None:
            return st.copy()
        want = np.atleast_1d(np.asarray(ids, dtype=np.float64))
        return st[np.isin(st["id"], want)]

    def _compute_stats(self, ids) -> np.ndarray:
        ids = [i for i in np.asarray(ids, dtype=np.float64) if len(self.rows_of(i))]
        out = np.zeros(len(ids), dtype=STATS_DTYPE)
        if not ids:
            return out
        parts = [self.rows_of(i) for i in ids]
        lens = np.array([len(p) for p in parts])
        rows = np.concatenate(parts)
        starts = np.cumsum(lens) - lens
        ends = starts + lens - 1
        f = self._fund[rows]
        k = self._idx[rows]
        lab = np.repeat(np.arange(len(ids)), lens)
        fs = f[np.lexsort((f, lab))]
        med = (fs[starts + (lens - 1) // 2] + fs[starts + lens // 2]) / 2
        out["id"] = ids
        out["n"] = lens
        out["k_first"] = k[starts]
        out["k_last"] = k[ends]
        out["t_first"] = self._times[k[starts]]
        out["t_last"] = self._times[k[ends]]
        out["f_min"] = np.minimum.reduceat(f, starts)
        out["f_median"] = med
        out["f_max"] = np.maximum.reduceat(f, starts)
        return out

    def check_invariant(self, ids=None) -> None:
        """At most one detection per id and frame, except duplicates that
        were already in the input.  Raises AssertionError naming id and
        frame."""
        if ids is None:
            rows = np.flatnonzero(~np.isnan(self._ident[: self._n]))
        else:
            ids = np.asarray(ids, dtype=np.float64).ravel()
            rows = self._rows_of_ids(ids[~np.isnan(ids)])
        for g in _dup_groups(self._ident, self._idx, rows):
            if not self._is_allowed(g):
                raise AssertionError(
                    f"id {_fid(self._ident[g[0]])} has {len(g)} detections in "
                    f"frame {int(self._idx[g[0]])} (rows {g.tolist()})"
                )

    def _is_allowed(self, group: np.ndarray) -> bool:
        for r, lab in self._allowed:
            pos = np.searchsorted(r, group)
            pos = np.minimum(pos, len(r) - 1)
            if np.all(r[pos] == group) and np.all(lab[pos] == lab[pos[0]]):
                return True
        return False

    # ----------------------------------------------------------- tracking cfg

    def _tracking(self, name: str, default: float) -> float:
        for path in (("config", "tracking"), ("tracking_config",)):
            d = self.meta
            for p in path:
                d = d.get(p, {}) if isinstance(d, dict) else {}
            if isinstance(d, dict) and name in d:
                try:
                    return float(d[name])
                except (TypeError, ValueError):
                    pass
        return default

    def _frame_step(self) -> float:
        if self.grid is not None:
            return self.grid.step / self.grid.rate
        if "frame_step" in self.meta:
            return float(self.meta["frame_step"])
        if len(self._times) > 1:
            return float(np.median(np.diff(self._times)))
        return 1.0

    def _freq_bin(self) -> float:
        if "freq_resolution" in self.meta:
            return float(self.meta["freq_resolution"])
        if self.grid is not None:
            return self.grid.rate / self.grid.nfft
        return 1.0

    # -------------------------------------------------------------- issues

    def issues(
        self, kinds=("join", "gap", "short", "crossing"), **params
    ) -> list[Issue]:
        """Places a reader should look at, sorted by time (design 5.11).
        Params: max_dt, freq_tolerance (default: session config),
        gap_break_s (0.5), min_points (10), bin_hz (frequency resolution).
        Cached per model revision."""
        key = (self.revision, tuple(kinds), tuple(sorted(params.items())))
        cached = getattr(self, "_issues_cache", None)
        if cached is not None and cached[0] == key:
            return list(cached[1])
        out = self._issues(kinds, params)
        self._issues_cache = (key, out)
        return list(out)

    def _issues(self, kinds, params) -> list[Issue]:
        out: list[Issue] = []
        st = self.stats()
        if "join" in kinds and len(st) > 1:
            max_dt = float(
                params.get("max_dt", self._tracking("max_dt", DEFAULT_MAX_DT))
            )
            tol = float(
                params.get(
                    "freq_tolerance",
                    self._tracking("freq_tolerance", DEFAULT_FREQ_TOLERANCE),
                )
            )
            f_end = self._fund[[self.rows_of(i)[-1] for i in st["id"]]]
            f_start = self._fund[[self.rows_of(i)[0] for i in st["id"]]]
            o = np.argsort(st["t_first"], kind="stable")
            ts_sorted = st["t_first"][o]
            for a in range(len(st)):
                lo = np.searchsorted(ts_sorted, st["t_last"][a], "right")
                hi = np.searchsorted(ts_sorted, st["t_last"][a] + max_dt, "right")
                cand = o[lo:hi]
                cand = cand[st["k_first"][cand] > st["k_last"][a]]
                if not len(cand):
                    continue
                df = np.abs(f_start[cand] - f_end[a])
                ok = df <= tol
                if not ok.any():
                    continue
                cand, df = cand[ok], df[ok]
                b = cand[np.argmin(df)]
                ia, ib = float(st["id"][a]), float(st["id"][b])
                gap = st["t_first"][b] - st["t_last"][a]
                out.append(
                    Issue(
                        "join",
                        float(st["t_last"][a]),
                        float(f_end[a]),
                        (ia, ib),
                        f"possible join: {_fid(ia)} → {_fid(ib)}, gap {gap:.1f} s, "
                        f"Δf {df.min():.1f} Hz — Enter merges",
                        lambda ts, ia=ia, ib=ib: ts.plan_merge([ia, ib], into=ia),
                    )
                )
        if "gap" in kinds and len(st):
            gap_s = float(params.get("gap_break_s", 0.5))
            rows = self._rows_of_ids(st["id"])
            t = self._t(rows)
            i = self._ident[rows]
            dt = np.diff(t)
            hit = np.flatnonzero((i[1:] == i[:-1]) & (dt > gap_s))
            for h in hit:
                out.append(
                    Issue(
                        "gap",
                        float(t[h]),
                        float(self._fund[rows[h]]),
                        (float(i[h]),),
                        f"gap in {_fid(i[h])}: {dt[h]:.1f} s",
                        None,
                    )
                )
        if "short" in kinds and len(st):
            min_points = int(params.get("min_points", 10))
            for s in st[st["n"] < min_points]:
                ii = float(s["id"])
                out.append(
                    Issue(
                        "short",
                        float(s["t_first"]),
                        float(s["f_median"]),
                        (ii,),
                        f"short track {_fid(ii)}: {int(s['n'])} points — Enter unassigns",
                        lambda ts, ii=ii: ts.plan_delete_ids([ii]),
                    )
                )
        if "crossing" in kinds and len(st):
            out.extend(self._crossings(float(params.get("bin_hz", self._freq_bin()))))
        out.sort(key=lambda x: (x.t, x.kind))
        return out

    def _crossings(self, bin_hz: float) -> list[Issue]:
        n = self._n
        rows = np.flatnonzero(~np.isnan(self._ident[:n]))
        if len(rows) < 2:
            return []
        k, f = self._idx[rows], self._fund[rows]
        o = np.lexsort((f, k))
        rows, k, f = rows[o], k[o], f[o]
        i = self._ident[rows]
        hit = np.flatnonzero((k[1:] == k[:-1]) & (np.abs(f[1:] - f[:-1]) <= bin_hz))
        if not len(hit):
            return []
        a, b = i[hit], i[hit + 1]
        lo, hi = np.minimum(a, b), np.maximum(a, b)
        kh = k[hit]
        o = np.lexsort((kh, hi, lo))
        lo, hi, kh, hit = lo[o], hi[o], kh[o], hit[o]
        new = np.r_[
            True, (lo[1:] != lo[:-1]) | (hi[1:] != hi[:-1]) | (np.diff(kh) > WINDOW)
        ]
        out = []
        for j in np.flatnonzero(new):
            x, y, kk = float(lo[j]), float(hi[j]), int(kh[j])
            if x == y:
                text = (
                    f"duplicate detections of {_fid(x)} at {fmt_time(self._times[kk])}"
                )
                ids = (x,)
            else:
                text = f"{_fid(x)} and {_fid(y)} cross at {fmt_time(self._times[kk])}"
                ids = (x, y)
            out.append(
                Issue(
                    "crossing",
                    float(self._times[kk]),
                    float(f[hit[j]]),
                    ids,
                    text,
                    None,
                )
            )
        return out

    # ---------------------------------------------------------- plan helpers

    def _id_arg(self, x) -> float:
        try:
            v = float(x)
        except (TypeError, ValueError):
            raise EditRejected(f"{x!r} is not an id") from None
        if not np.isfinite(v) or v != round(v):
            raise EditRejected(f"{x!r} is not an id")
        return v

    def _existing(self, x) -> float:
        v = self._id_arg(x)
        if not len(self.rows_of(v)):
            raise EditRejected(f"id {_fid(v)} has no points")
        return v

    def _rows_arg(self, rows) -> np.ndarray:
        rows = np.asarray(rows if rows is not None else [], dtype=np.int64).ravel()
        if not len(rows):
            raise EditRejected("nothing selected")
        rows = np.unique(rows)
        if rows[0] < 0 or rows[-1] >= self._n:
            raise EditRejected("the selection holds rows that do not exist")
        return rows

    def _span(self, rows, append: Append | None = None):
        t = [self._t(rows)]
        f = [self._fund[rows]]
        if append is not None and len(append):
            t.append(self._times[append.idx])
            f.append(append.fund)
        t, f = np.concatenate(t), np.concatenate(f)
        if not len(t):
            return (np.nan, np.nan, np.nan, np.nan)
        return (
            float(t.min()),
            float(t.max()),
            float(np.nanmin(f)),
            float(np.nanmax(f)),
        )

    def _plan(
        self,
        label,
        rows,
        new,
        dropped=_EMPTY,
        append=None,
        created=(),
        meta=None,
        next_id=None,
        nothing="nothing would change",
    ) -> Plan:
        rows = np.asarray(rows, dtype=np.int64)
        new = np.broadcast_to(np.asarray(new, dtype=np.float64), rows.shape)
        cur = self._ident[rows]
        diff = ~((cur == new) | (np.isnan(cur) & np.isnan(new)))
        rows, new = rows[diff], new[diff]
        o = np.argsort(rows, kind="stable")
        rows, new = rows[o], np.array(new[o])
        if len(np.unique(rows)) != len(rows):
            raise AssertionError("a plan changes one row twice")
        if not len(rows) and append is None and not meta:
            raise EditRejected(nothing)
        created = tuple(int(c) for c in created)
        if created:
            next_id = max(next_id or 0, max(created) + 1)
        return Plan(
            label=label,
            rows=_readonly(rows),
            new=_readonly(new),
            dropped=_readonly(np.unique(np.asarray(dropped, dtype=np.int64))),
            append=append,
            created=created,
            span=self._span(rows, append),
            revision=self.revision,
            meta=meta,
            next_id=next_id,
        )

    def _make_append(self, fund, idx, ident, tracked, sign, cplx) -> Append:
        m = len(fund)
        c = self._c
        if sign is None:
            sign = np.full(
                (m, c if c is not None else 1), np.nan, dtype=self._sign_dtype()
            )
        else:
            sign = np.asarray(sign)
            if sign.ndim == 1 and c in (None, 1) and len(sign) == m:
                sign = sign[:, None]
            if (
                sign.ndim != 2
                or len(sign) != m
                or (c is not None and sign.shape[1] != c)
            ):
                want = f"({m}, {c})" if c is not None else f"({m}, electrodes)"
                raise EditRejected(
                    f"electrode power has shape {sign.shape}, expected {want}"
                )
            sign = sign.astype(self._sign_dtype())
        cc = sign.shape[1]
        has_cplx = self._has_cplx if c is not None else cplx is not None
        if not has_cplx:
            cplx = None
        elif cplx is None:
            cplx = np.full((m, cc), np.nan + 1j * np.nan, dtype=self._cplx_dtype())
        else:
            cplx = np.asarray(cplx)
            if cplx.shape != (m, cc):
                raise EditRejected(
                    f"complex spectra have shape {cplx.shape}, expected {(m, cc)}"
                )
            cplx = cplx.astype(self._cplx_dtype())
        return Append(
            fund=_readonly(np.asarray(fund, dtype=np.float64)),
            idx=_readonly(np.asarray(idx, dtype=np.int64)),
            ident=_readonly(np.asarray(ident, dtype=np.float64)),
            tracked=_readonly(np.asarray(tracked, dtype=np.float64)),
            sign=_readonly(sign),
            cplx=None if cplx is None else _readonly(cplx),
        )

    def _sign_dtype(self):
        return np.float32 if self._sign is None else self._sign.dtype

    def _cplx_dtype(self):
        return np.complex64 if self._cplx is None else self._cplx.dtype

    # ----------------------------------------------------------------- plans

    def plan_unassign(self, rows) -> Plan:
        rows = self._rows_arg(rows)
        rows = rows[~np.isnan(self._ident[rows])]
        return self._plan(
            f"Unassign {len(rows)} point{'s' if len(rows) != 1 else ''}",
            rows,
            np.nan,
            nothing="nothing to unassign: the selected points are unassigned",
        )

    def plan_delete_ids(self, ids) -> Plan:
        ids = [
            self._existing(i) for i in np.unique(np.atleast_1d(np.asarray(ids, float)))
        ]
        if not ids:
            raise EditRejected("nothing selected")
        rows = self._rows_of_ids(ids)
        label = (
            f"Delete id {_fid(ids[0])}"
            if len(ids) == 1
            else f"Delete {len(ids)} ids ({', '.join(_fid(i) for i in ids[:5])}"
            + (", …)" if len(ids) > 5 else ")")
        )
        return self._plan(label, rows, np.nan)

    def plan_new_id(self, rows) -> Plan:
        sel = self._rows_arg(rows)
        fresh = self.next_id
        lose = local_median_losers(
            self._fund[sel], self._idx[sel], np.full(len(sel), float(fresh)), order=sel
        )
        win = sel[~lose]
        nd = int(lose.sum())
        label = f"New id {fresh} from {len(win)} point{'s' if len(win) != 1 else ''}"
        if nd:
            label += f" ({nd} conflict{'s' if nd != 1 else ''} kept their id)"
        return self._plan(label, win, float(fresh), dropped=sel[lose], created=(fresh,))

    def plan_assign(self, rows, target: float) -> Plan:
        sel = self._rows_arg(rows)
        target = self._existing(target)
        fr = self._idx[sel]
        trows = self.rows_of(target)
        displaced = trows[np.isin(self._idx[trows], fr) & ~np.isin(trows, sel)]
        rest = np.setdiff1d(trows, np.concatenate([displaced, sel]), assume_unique=True)
        track = np.concatenate([rest, sel])
        lose = local_median_losers(
            self._fund[track],
            self._idx[track],
            np.full(len(track), target),
            order=track,
        )
        losers = np.intersect1d(track[lose], sel)
        winners = np.setdiff1d(sel, losers)
        lost_target = losers[self._ident[losers] == target]
        change_rows = np.concatenate([winners, displaced, lost_target])
        new = np.concatenate(
            [
                np.full(len(winners), target),
                np.full(len(displaced) + len(lost_target), np.nan),
            ]
        )
        nmove = int(np.sum(self._ident[winners] != target))
        label = f"Assign {nmove} point{'s' if nmove != 1 else ''} to {_fid(target)}"
        extra = []
        if len(displaced):
            extra.append(f"{len(displaced)} displaced")
        if len(losers):
            extra.append(f"{len(losers)} conflict{'s' if len(losers) != 1 else ''}")
        if extra:
            label += f" ({', '.join(extra)})"
        return self._plan(
            label,
            change_rows,
            new,
            dropped=np.concatenate([displaced, losers]),
            nothing=f"the selected points already belong to {_fid(target)}",
        )

    def plan_merge(self, ids, into: float) -> Plan:
        ids = sorted(
            {self._id_arg(i) for i in np.atleast_1d(np.asarray(ids, dtype=float))}
        )
        into = self._id_arg(into)
        if into not in ids:
            raise EditRejected(
                f"the merge target {_fid(into)} is not one of the merged ids"
            )
        if len(ids) < 2:
            raise EditRejected("nothing to merge: pick a second track")
        for i in ids:
            self._existing(i)
        rows = self._rows_of_ids(ids)
        lose = local_median_losers(
            self._fund[rows], self._idx[rows], np.full(len(rows), into), order=rows
        )
        new = np.full(len(rows), into)
        new[lose] = np.nan
        others = [i for i in ids if i != into]
        nc = int(lose.sum())
        label = f"Merge {', '.join(_fid(i) for i in others[:5])}"
        label += (", …" if len(others) > 5 else "") + f" into {_fid(into)}"
        if nc:
            label += f" ({nc} conflict{'s' if nc != 1 else ''} unassigned)"
        return self._plan(label, rows, new, dropped=rows[lose])

    def plan_cut(self, id: float, t: float, new_part: str = "after") -> Plan:
        if new_part not in ("after", "before"):
            raise ValueError(f"new_part must be 'after' or 'before', not {new_part!r}")
        i = self._existing(id)
        rows = self.rows_of(i)
        after = self._t(rows) >= t
        if not after.any():
            raise EditRejected(
                f"cut at {fmt_time(t)} would leave id {_fid(i)} empty after the cut"
            )
        if after.all():
            raise EditRejected(
                f"cut at {fmt_time(t)} would leave id {_fid(i)} empty before the cut"
            )
        part = rows[after] if new_part == "after" else rows[~after]
        fresh = self.next_id
        return self._plan(
            f"Cut {_fid(i)} at {fmt_time(t)} ({new_part} → {fresh})",
            part,
            float(fresh),
            created=(fresh,),
        )

    def plan_swap_after(self, a: float, b: float, t: float) -> Plan:
        a, b = self._id_arg(a), self._id_arg(b)
        if a == b:
            raise EditRejected("swap needs two different tracks")
        ra, rb = self.rows_of(a), self.rows_of(b)
        ra = ra[self._t(ra) >= t]
        rb = rb[self._t(rb) >= t]
        if not len(ra) and not len(rb):
            raise EditRejected(
                f"neither {_fid(a)} nor {_fid(b)} has points after {fmt_time(t)}"
            )
        rows = np.concatenate([ra, rb])
        new = np.concatenate([np.full(len(ra), b), np.full(len(rb), a)])
        return self._plan(
            f"Swap {_fid(a)} and {_fid(b)} after {fmt_time(t)}", rows, new
        )

    def plan_add(
        self, frames, freqs, target: float | None = None, sign=None, cplx=None
    ) -> Plan:
        frames = np.asarray(frames).ravel()
        freqs = np.asarray(freqs, dtype=np.float64).ravel()
        m = len(frames)
        if not m:
            raise EditRejected("nothing to add")
        if len(freqs) != m:
            raise ValueError("frames and freqs differ in length")
        if np.any(frames != np.round(frames)):
            raise EditRejected("frames must be frame indices")
        frames = frames.astype(np.int64)
        if frames.min() < 0 or frames.max() >= len(self._times):
            raise EditRejected("the stroke reaches outside the analysed frames")
        # frames without a frequency (the peak search found none there, or
        # the frame lies past the recording's end) are skipped, not fatal
        finite = np.isfinite(freqs)
        if not finite.any():
            raise EditRejected("no frequency was found in any frame of the stroke")
        if target is not None:
            target = self._existing(target)
            base = self.rows_of(target)
            keep = ~np.isin(frames, self._idx[base]) & finite
            if not keep.any():
                raise EditRejected(
                    f"id {_fid(target)} already has a point in every frame of the stroke"
                )
            tid = target
        else:
            base = _EMPTY
            keep = finite
            tid = float(self.next_id)
        sel = np.flatnonzero(keep)
        cf, ck = freqs[sel], frames[sel]
        lose = local_median_losers(
            np.concatenate([self._fund[base], cf]),
            np.concatenate([self._idx[base], ck]),
            np.full(len(base) + len(sel), tid),
            order=np.concatenate([base, self._n + sel]),
        )[len(base) :]
        sel = sel[~lose]
        sel = sel[np.argsort(frames[sel], kind="stable")]
        side_sign = None if sign is None else np.asarray(sign)[sel]
        side_cplx = None if cplx is None else np.asarray(cplx)[sel]
        app = self._make_append(
            freqs[sel],
            frames[sel],
            np.full(len(sel), tid),
            np.full(len(sel), np.nan),
            side_sign,
            side_cplx,
        )
        skipped = m - len(sel)
        label = f"Add {len(sel)} point{'s' if len(sel) != 1 else ''} to "
        label += _fid(tid) if target is not None else f"new id {_fid(tid)}"
        if skipped:
            label += f" ({skipped} skipped)"
        created = () if target is not None else (int(tid),)
        return self._plan(label, _EMPTY, np.nan, append=app, created=created)

    def plan_replace_span(self, snippet: Snippet, stitch: bool = True) -> Plan:
        k0, k1 = int(snippet.k0), int(snippet.k1)
        if not 0 <= k0 <= k1 <= len(self._times):
            raise EditRejected("the snippet lies outside the session's frames")
        s_f = np.asarray(snippet.fund, dtype=np.float64)
        s_k = np.asarray(snippet.idx, dtype=np.int64)
        s_i = np.asarray(snippet.ident, dtype=np.float64)
        m = len(s_f)
        if s_k.shape != (m,) or s_i.shape != (m,) or len(snippet.sign) != m:
            raise EditRejected("the snippet's arrays are not parallel")
        if m and (s_k.min() < k0 or s_k.max() >= k1):
            raise EditRejected("the snippet has detections outside its frames")
        if self._c is not None and m and np.asarray(snippet.sign).shape[1] != self._c:
            raise EditRejected(
                f"the snippet has {np.asarray(snippet.sign).shape[1]} electrodes, "
                f"the session {self._c}"
            )
        span_rows = self.rows_in_frames(k0, k1)
        span_rows = span_rows[~np.isnan(self._ident[span_rows])]

        sids = np.unique(s_i[~np.isnan(s_i)])
        tracks = {}
        for s in sids:
            r = np.flatnonzero(s_i == s)
            r = r[np.argsort(s_k[r], kind="stable")]
            tracks[float(s)] = r
        final: dict[float, float] = {}
        relabel: dict[float, float] = {}
        joined = unjoined = 0
        if stitch and tracks and self._n:
            g = max(
                1,
                int(
                    round(self._tracking("max_dt", DEFAULT_MAX_DT) / self._frame_step())
                ),
            )
            tol = self._tracking("freq_tolerance", DEFAULT_FREQ_TOLERANCE)
            left = self._edge_matches(tracks, s_f, s_k, k0 - g, k0, k0 + g, tol, True)
            right = self._edge_matches(tracks, s_f, s_k, k1, k1 + g, k1 - g, tol, False)
            for s, a in left.items():
                final[s] = a
                joined += 1
            for s, b in right:
                if s in final:
                    a = final[s]
                    if a == b:
                        joined += 1
                        continue
                    a_after = self.rows_of(a)
                    a_after = a_after[self._idx[a_after] >= k1]
                    if len(a_after) or a in relabel.values():
                        unjoined += 1
                    else:
                        relabel[b] = a
                        joined += 1
                else:
                    taken = [x for x, y in final.items() if y == b]
                    clash = any(
                        len(np.intersect1d(s_k[tracks[x]], s_k[tracks[s]]))
                        for x in taken
                    )
                    if clash or b in relabel:
                        unjoined += 1
                    else:
                        final[s] = b
                        joined += 1
        created = []
        nxt = self.next_id
        for s in tracks:
            if s not in final:
                final[s] = float(nxt)
                created.append(nxt)
                nxt += 1
        new_ident = np.full(m, np.nan)
        for s, r in tracks.items():
            new_ident[r] = final[s]
        # the tracker's own output may hold two detections of one id in a
        # frame (wavetracker resolves them only while stitching): keep the
        # one closer to the local median, as every other edit does
        losers = local_median_losers(s_f, s_k, new_ident)
        n_dups = int(losers.sum())
        new_ident[losers] = np.nan
        o = np.argsort(s_k, kind="stable")
        app = None
        if m:
            app = self._make_append(
                s_f[o],
                s_k[o],
                new_ident[o],
                np.full(m, np.nan),  # not the whole run's output: like hand-added rows
                np.asarray(snippet.sign)[o],
                None if snippet.cplx is None else np.asarray(snippet.cplx)[o],
            )
        rel_rows, rel_new = [_EMPTY], [np.empty(0)]
        for b, a in relabel.items():
            rb = self.rows_of(b)
            rb = rb[self._idx[rb] >= k1]
            rel_rows.append(rb)
            rel_new.append(np.full(len(rb), a))
        rows = np.concatenate([span_rows, *rel_rows])
        new = np.concatenate([np.full(len(span_rows), np.nan), *rel_new])
        t0 = self._times[k0] if k1 > k0 else np.nan
        t1 = self._times[k1 - 1] if k1 > k0 else np.nan
        label = f"Accept snippet {fmt_time(t0)}–{fmt_time(t1)} ({len(tracks)} tracks"
        if joined:
            label += f", {joined} edge{'s' if joined != 1 else ''} joined"
        if unjoined:
            label += f", {unjoined} edge{'s' if unjoined != 1 else ''} left unjoined: ambiguous"
        if n_dups:
            label += (
                f", {n_dups} duplicate detection{'s' if n_dups != 1 else ''} unassigned"
            )
        label += ")"
        meta = None
        if not self.meta and self._snippet_meta is None:
            meta = ((("snippet_meta", 0), None, dict(snippet.meta)),)
        return self._plan(
            label,
            rows,
            new,
            append=app,
            created=created,
            meta=meta,
            nothing="the snippet has no detections and the span has no tracks",
        )

    def _edge_matches(self, tracks, s_f, s_k, w0, w1, near, tol, left):
        """Greedy one-to-one matches of snippet tracks to session ids with a
        row in frames [w0, w1).  Left edge: a track starting before `near`
        against each id's last frequency in the window; right edge: a track
        ending at or after `near` against each id's first frequency."""
        rows = self.rows_in_frames(max(w0, 0), max(w1, 0))
        rows = rows[~np.isnan(self._ident[rows])]
        if not len(rows):
            return {} if left else []
        sess: dict[float, float] = {}
        for r in (
            rows if left else rows[::-1]
        ):  # by frame; keep last (left) / first (right)
            sess[float(self._ident[r])] = float(self._fund[r])
        pairs = []
        for s, r in tracks.items():
            if left and s_k[r[0]] >= near:
                continue
            if not left and s_k[r[-1]] < near:
                continue
            fs = s_f[r[0]] if left else s_f[r[-1]]
            for i, fi in sess.items():
                df = abs(fi - fs)
                if df <= tol:
                    pairs.append((df, s, i))
        pairs.sort()
        used_s, used_i = set(), set()
        out = []
        for df, s, i in pairs:
            if s in used_s or i in used_i:
                continue
            used_s.add(s)
            used_i.add(i)
            out.append((s, i))
        return dict(out) if left else out

    def plan_apply_ident(self, ident: np.ndarray, label: str) -> Plan:
        try:
            ident = check_ident(np.asarray(ident), self._n, "the identities")
        except ResultsError as exc:
            raise EditRejected(exc.args[0]) from None
        n = self._n
        lose = local_median_losers(self._fund[:n], self._idx[:n], ident)
        nd = int(lose.sum())
        if nd:
            ident = ident.copy()
            ident[lose] = np.nan
            label = f"{label} ({nd} duplicate{'s' if nd != 1 else ''} unassigned)"
        new_ids = np.unique(ident[~np.isnan(ident)])
        created = tuple(int(i) for i in new_ids[new_ids >= self.next_id])
        rows = np.arange(n)
        return self._plan(
            label, rows, ident, dropped=np.flatnonzero(lose), created=created
        )

    def plan_revert(self, rows=None) -> Plan:
        n = self._n
        if rows is None:
            cur, tr = self._ident[:n], self._tracked[:n]
            diff = np.flatnonzero(~((cur == tr) | (np.isnan(cur) & np.isnan(tr))))
            return self._plan(
                "Revert to tracker output",
                diff,
                tr[diff],
                nothing="the tracks already are the tracker's output",
            )
        sel = self._rows_arg(rows)
        cur, tr = self._ident[sel], self._tracked[sel]
        sel = sel[~((cur == tr) | (np.isnan(cur) & np.isnan(tr)))]
        if not len(sel):
            raise EditRejected("the selected points already have the tracker's ids")
        tr = self._tracked[sel]
        keep = np.ones(len(sel), dtype=bool)
        displaced = [_EMPTY]
        for T in np.unique(tr[~np.isnan(tr)]):
            mine = sel[tr == T]
            trows = self.rows_of(T)
            disp = trows[
                np.isin(self._idx[trows], self._idx[mine]) & ~np.isin(trows, sel)
            ]
            displaced.append(disp)
            rest = np.setdiff1d(trows, disp)
            track = np.concatenate([rest, mine])
            lose = local_median_losers(
                self._fund[track], self._idx[track], np.full(len(track), T), order=track
            )
            keep[np.isin(sel, track[lose])] = False
        displaced = np.concatenate(displaced)
        win = sel[keep]
        rows_all = np.concatenate([win, displaced])
        new = np.concatenate([self._tracked[win], np.full(len(displaced), np.nan)])
        return self._plan(
            f"Revert {len(win)} point{'s' if len(win) != 1 else ''} to tracker ids",
            rows_all,
            new,
            dropped=np.concatenate([displaced, sel[~keep]]),
        )

    def _plan_meta(self, kind: str, id, text: str) -> Plan:
        i = int(self._id_arg(id))
        store = self.labels if kind == "label" else self.notes
        if not len(self.rows_of(i)) and i not in store:
            raise EditRejected(f"id {i} has no points")
        old = store.get(i)
        new = (text or "").strip() or None
        if old == new:
            raise EditRejected("unchanged")
        what = "name" if kind == "label" else "note"
        label = (
            f"Remove {what} of {i}" if new is None else f"Set {what} of {i}: {new!r}"
        )
        return self._plan(label, _EMPTY, np.nan, meta=(((kind, i), old, new),))

    def plan_set_label(self, id: float, text: str) -> Plan:
        return self._plan_meta("label", id, text)

    def plan_set_note(self, id: float, text: str) -> Plan:
        return self._plan_meta("note", id, text)

    def plan_recover(self, autosave: Autosave) -> Plan:
        """The autosave's edits as one command "Recovered unsaved edits"."""
        n = self._n
        if autosave.n_base != n:
            raise EditRejected(
                "the autosave belongs to a different state of these results"
            )
        new = np.asarray(autosave.ident[:n], dtype=np.float64)
        if len(new) < n:
            # saved appended rows were undone before the autosave: the rows
            # stay on disk, so they come back unassigned
            new = np.concatenate([new, np.full(n - len(new), np.nan)])
        app = None
        if autosave.append is not None and len(autosave.append):
            a = autosave.append
            app = self._make_append(a.fund, a.idx, a.ident, a.tracked, a.sign, a.cplx)
        meta = []
        for kind, store, saved in (
            ("label", self.labels, autosave.labels),
            ("note", self.notes, autosave.notes),
        ):
            for i in sorted(set(store) | set(saved)):
                if store.get(i) != saved.get(i):
                    meta.append(((kind, i), store.get(i), saved.get(i)))
        return self._plan(
            "Recovered unsaved edits",
            np.arange(n),
            new,
            append=app,
            meta=tuple(meta) or None,
            next_id=max(autosave.next_id, self.next_id),
            nothing="nothing to recover",
        )

    # -------------------------------------------------------------- mutation

    def apply(self, plan: Plan) -> Change:
        if plan.revision != self.revision:
            raise EditRejected(
                "the tracks changed since this edit was previewed; try again"
            )
        rows = plan.rows
        if len(rows) and rows[-1] >= self._n:
            raise EditRejected("the edit refers to rows that no longer exist")
        new = plan.new
        if len(new) and np.all(new == new[0]):
            new = float(new[0])
        elif len(new) and np.all(np.isnan(new)):
            new = np.nan
        else:
            new = np.array(new)
        cmd = Command(
            label=plan.label,
            rows=rows.astype(np.int32 if self._n < 2**31 else np.int64),
            old=self._ident[rows].copy(),
            new=new,
            append=plan.append,
            meta=plan.meta,
            span=plan.span,
            time=time.time(),
            n_before=self._n,
            created=plan.created,
            next_id=plan.next_id,
        )
        change = self._forward(cmd)
        try:
            self.check_invariant(change.ids)
        except AssertionError:
            self._backward(cmd)
            raise
        self.history.push(cmd)
        return change

    def undo(self) -> Change | None:
        h = self.history
        if not h.can_undo():
            return None
        h.position -= 1
        return self._backward(h.entries[h.position])

    def redo(self) -> Change | None:
        h = self.history
        if not h.can_redo():
            return None
        cmd = h.entries[h.position]
        h.position += 1
        return self._forward(cmd)

    def jump(self, position: int) -> Change | None:
        h = self.history
        position = int(np.clip(position, 0, len(h.entries)))
        changes = []
        while h.position > position:
            changes.append(self.undo())
        while h.position < position:
            changes.append(self.redo())
        if not changes:
            return None
        if len(changes) == 1:
            return changes[0]
        ids = np.unique(np.concatenate([c.ids for c in changes]))
        rows = np.unique(np.concatenate([c.rows for c in changes]))
        frames = [c.frames for c in changes if c.frames[1] > c.frames[0]]
        return Change(
            revision=self.revision,
            ids=ids,
            rows=rows[rows < self._n],
            appended=sum(c.appended for c in changes),
            frames=(min(f[0] for f in frames), max(f[1] for f in frames))
            if frames
            else (0, 0),
            unassigned=any(c.unassigned for c in changes),
            label=f"Jump to entry {position}",
        )

    def _forward(self, cmd: Command) -> Change:
        rows = cmd.rows.astype(np.intp)
        before = self._ident[rows].copy()
        self._ident[rows] = cmd.new
        appended = 0
        app_ids = np.empty(0)
        if cmd.append is not None and len(cmd.append):
            self._append_rows(cmd.append)
            appended = len(cmd.append)
            app_ids = np.asarray(cmd.append.ident)
        for (kind, i), _old, new in cmd.meta or ():
            self._set_meta(kind, i, new)
        nid = [self.next_id]
        if cmd.next_id is not None:
            nid.append(cmd.next_id)
        if cmd.created:
            nid.append(max(cmd.created) + 1)
        self.next_id = max(nid)
        return self._after(cmd, rows, before, self._ident[rows], app_ids, appended)

    def _backward(self, cmd: Command) -> Change:
        rows = cmd.rows.astype(np.intp)
        before = self._ident[rows].copy()
        self._ident[rows] = cmd.old
        appended = 0
        app_ids = np.empty(0)
        if cmd.append is not None and len(cmd.append):
            app_ids = np.asarray(cmd.append.ident)
            appended = -len(cmd.append)
            self._truncate(cmd.n_before)
        for (kind, i), old, _new in cmd.meta or ():
            self._set_meta(kind, i, old)
        return self._after(cmd, rows, before, self._ident[rows], app_ids, appended)

    def _after(self, cmd, rows, before, after, app_ids, appended) -> Change:
        touched = np.concatenate([before, after, app_ids])
        meta_ids = [
            float(i) for (k, i), _o, _n in cmd.meta or () if k in ("label", "note")
        ]
        if meta_ids:
            touched = np.concatenate([touched, meta_ids])
        touched = np.unique(touched[~np.isnan(touched)])
        extra = rows.astype(np.int64)
        if appended > 0:
            extra = np.concatenate([extra, np.arange(self._n - appended, self._n)])
        self._index_ids(touched, extra)
        self._stats_dirty.update(touched.tolist())
        self.revision += 1
        app = cmd.append
        ks = [self._idx[rows]]
        if app is not None and len(app):
            ks.append(np.asarray(app.idx))
        ks = np.concatenate(ks)
        frames = (int(ks.min()), int(ks.max()) + 1) if len(ks) else (0, 0)
        unassigned = bool(
            np.isnan(before).any()
            or np.isnan(after).any()
            or (app is not None and np.isnan(np.asarray(app.ident)).any())
        )
        return Change(
            revision=self.revision,
            ids=_readonly(touched),
            rows=_readonly(rows.astype(np.int64)),
            appended=appended,
            frames=frames,
            unassigned=unassigned,
            label=cmd.label,
        )

    def _set_meta(self, kind, i, value) -> None:
        if kind == "snippet_meta":
            self._snippet_meta = value
            return
        store = self.labels if kind == "label" else self.notes
        if value is None:
            store.pop(int(i), None)
        else:
            store[int(i)] = value

    # -------------------------------------------------------- buffers/index

    def _reserve(self, need: int) -> None:
        cap = len(self._fund)
        if need <= cap:
            return
        cap = max(need, int(cap * 1.5) + 16)
        n = self._n

        def grow(a):
            b = np.empty((cap,) + a.shape[1:], dtype=a.dtype)
            b[:n] = a[:n]
            return b

        self._fund = grow(self._fund)
        self._idx = grow(self._idx)
        self._ident = grow(self._ident)
        self._tracked = grow(self._tracked)
        if self._sign is not None:
            self._sign = grow(self._sign)
        if self._cplx is not None:
            self._cplx = grow(self._cplx)

    def _append_rows(self, app: Append) -> None:
        m, n = len(app), self._n
        if self._c is None:
            self._c = int(app.sign.shape[1])
            self._sign = np.empty((n, self._c), dtype=app.sign.dtype)
            self._has_cplx = app.cplx is not None
            if self._has_cplx:
                self._cplx = np.empty((n, self._c), dtype=app.cplx.dtype)
        self._reserve(n + m)
        s = slice(n, n + m)
        self._fund[s] = app.fund
        self._idx[s] = app.idx
        self._ident[s] = app.ident
        self._tracked[s] = app.tracked
        self._sign[s] = app.sign
        if self._cplx is not None:
            self._cplx[s] = app.cplx
        self._n = n + m
        self._rows_rev += 1
        new_rows = np.arange(n, n + m, dtype=np.int64)
        o = np.argsort(app.idx, kind="stable")
        k = np.asarray(app.idx)[o]
        pos = np.searchsorted(self._bf_k, k, "right")
        self._by_frame = np.insert(self._by_frame, pos, new_rows[o])
        self._bf_k = np.insert(self._bf_k, pos, k)

    def _truncate(self, n: int) -> None:
        keep = self._by_frame < n
        self._by_frame = self._by_frame[keep]
        self._bf_k = self._bf_k[keep]
        self._n = n
        self._rows_rev += 1
        if n == 0 and self._c0 is None:
            self._c = None
            self._sign = None
            self._cplx = None
            self._has_cplx = False

    def _index_rows(self, rows: np.ndarray) -> None:
        """(Re)build the per-id entries for the ids of `rows` (all of each id's
        rows must be among `rows`)."""
        ident = self._ident[rows]
        o = np.lexsort((rows, self._idx[rows], ident))
        rows, ident = rows[o], ident[o]
        bounds = np.flatnonzero(ident[1:] != ident[:-1]) + 1
        starts = np.r_[0, bounds] if len(rows) else np.empty(0, np.int64)
        for a, part in zip(starts, np.split(rows, bounds) if len(rows) else []):
            self._rows_of[float(ident[a])] = _readonly(part.astype(np.int64))
        self._ids_cache = None

    def _index_ids(self, ids: np.ndarray, extra_rows: np.ndarray) -> None:
        if not len(ids):
            return
        parts = [self._rows_of.pop(float(i), _EMPTY) for i in ids]
        parts.append(extra_rows)
        cand = np.unique(np.concatenate(parts))
        cand = cand[cand < self._n]
        cand = cand[np.isin(self._ident[cand], ids)]
        self._index_rows(cand)
        self._ids_cache = None

    # ----------------------------------------------------------- persistence

    @classmethod
    def load(
        cls,
        folder,
        ident_file: str = IDENT_FILE,
        recording_paths: list[str] | None = None,
        duration: float | None = None,
    ) -> tuple[TrackSet, list[str]]:
        folder = Path(folder)
        complaints: list[str] = []
        if finish_interrupted_save(folder):
            complaints.append("completed an interrupted save")
        d = read_results(folder)
        complaints += d["complaints"]
        sorter = d["sorter"] or {}
        tracked = d["tracked"] if d["tracked"] is not None else d["ident"].copy()
        try:
            next_id = int(sorter["next_id"]) if "next_id" in sorter else None
        except (TypeError, ValueError):
            next_id = None
        ts = cls.from_arrays(
            d["fund"],
            d["idx"],
            d["ident"],
            d["sign"],
            d["times"],
            cplx=d["cplx"],
            meta=d["meta"],
            tracked=tracked,
            next_id=next_id,
        )
        ts._dtypes = d["dtypes"]
        ts._saved_rows = (_folder_key(folder), ts._rows_rev)
        ts._base_mtime = ident_mtime(folder)
        try:
            ts.n_tracked = int(
                np.clip(int(sorter.get("n_tracked_rows", ts.n)), 0, ts.n)
            )
        except (TypeError, ValueError):
            pass
        for key, store in (("labels", ts.labels), ("notes", ts.notes)):
            for k, v in (sorter.get(key) or {}).items():
                try:
                    store[int(k)] = str(v)
                except (TypeError, ValueError):
                    pass
        if ts.grid is None:
            complaints.append(
                "the frame times are not a regular wavetracker grid; "
                "snippet runs are disabled for these results"
            )
        if ts._n_input_dups:
            complaints.append(
                f"{ts._n_input_dups} duplicate detection(s) per id and frame in the "
                "input; left as they are (see the crossing issues)"
            )
        if recording_paths:
            files = ts.meta.get("files", ts.meta.get("input"))
            if files is not None:
                files = [files] if isinstance(files, str) else list(files)
                theirs = [Path(str(p)).name for p in files]
                ours = [Path(str(p)).name for p in recording_paths]
                if theirs != ours:
                    complaints.append(
                        f"these results were made from {', '.join(theirs)}, "
                        f"not from {', '.join(ours)}"
                    )
        if duration is not None and len(ts.times) and ts.times[-1] > duration + 1e-6:
            complaints.append(
                f"the results reach {fmt_time(ts.times[-1])}, beyond the "
                f"recording's {fmt_time(duration)}"
            )
        if ident_file != IDENT_FILE:
            path = folder / Path(ident_file).name
            if not path.exists():
                raise ResultsError(f"{ident_file} does not exist in {folder}")
            try:
                variant = np.load(path, allow_pickle=False)
            except (ValueError, OSError) as exc:
                raise ResultsError(f"cannot read {ident_file}: {exc}") from exc
            variant = check_ident(variant, ts.n, ident_file)
            try:
                ts.apply(ts.plan_apply_ident(variant, f"Load {Path(ident_file).name}"))
            except EditRejected:
                complaints.append(f"{ident_file} equals {IDENT_FILE}")
        return ts, complaints

    def _disk(self, name: str, arr: np.ndarray, default) -> np.ndarray:
        return np.ascontiguousarray(arr, dtype=self._dtypes.get(name, default))

    def save(self, folder, recording_paths: list[str] | None = None) -> None:
        """Write the session into `folder` (8.1).

        Only `ident_v.npy` and `eodsorter.json` change on an ordinary save.
        The per-detection arrays are rewritten whenever the rows may differ
        from what is on disk -- rows were appended or undone since this
        folder was last loaded or saved, or `folder` is another directory
        ("Save as") -- and never judged by the row count alone: undo an Add
        and Add as many other points, and the count matches while every
        appended row differs.  Into another directory everything is written,
        `times.npy` and `wavetracker.json` included, so results already
        there cannot survive next to these identities.
        """
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        n = self._n
        key = _folder_key(folder)
        same_folder = self._saved_rows is not None and self._saved_rows[0] == key
        rows_current = same_folder and self._saved_rows[1] == self._rows_rev
        items = [(IDENT_FILE, npy_writer(self.ident.copy()))]
        m = npy_rows(folder / TRACKED_FILE) if same_folder else None
        if not same_folder or m != n or not rows_current:
            out = self.tracked.copy()
            if m is not None:
                # the tracker's own identities of the rows it found are
                # whatever is on disk; appended rows are ours
                disk = np.load(folder / TRACKED_FILE, allow_pickle=False)
                k = min(m, n, self.n_tracked)
                out[:k] = disk[:k]
            items.append((TRACKED_FILE, npy_writer(out)))
        arrays = [
            ("fund_v", self.fund, np.float64),
            ("idx_v", self.idx, np.int64),
            (
                "sign_v",
                self.sign if self._c is not None else np.empty((n, 0)),
                np.float32,
            ),
        ]
        if self._has_cplx:
            arrays.append(("cplx_v", self.cplx, np.complex64))
        for name, arr, default in arrays:
            path = folder / f"{name}.npy"
            m_disk = npy_rows(path) if same_folder else None
            if not rows_current or m_disk != n:
                out = self._disk(name, arr, default)
                if m_disk and name in ("sign_v", "cplx_v"):
                    # read as float32/complex64; the tracker's own rows go
                    # back as they were on disk, at their full precision
                    k = min(m_disk, n, self.n_tracked)
                    try:
                        disk = np.load(path, mmap_mode="r", allow_pickle=False)
                        if disk.shape[1:] == out.shape[1:] and disk.dtype == out.dtype:
                            out = out.copy()
                            out[:k] = disk[:k]
                    except (OSError, ValueError):
                        pass
                items.append((f"{name}.npy", npy_writer(out)))
        if not same_folder or not (folder / "times.npy").exists():
            t = self.grid.times() if self.grid is not None else np.array(self._times)
            items.append(("times.npy", npy_writer(t)))
        if not same_folder or not (folder / META_FILE).exists():
            items.append((META_FILE, json_writer(self._merged_meta(recording_paths))))
        h = self.history
        sorter = {
            "version": SORTER_VERSION,
            "n_tracked_rows": int(self.n_tracked),
            "next_id": int(self.next_id),
            "labels": {str(k): v for k, v in sorted(self.labels.items())},
            "notes": {str(k): v for k, v in sorted(self.notes.items())},
            "recording": list(recording_paths or []),
            "saved_at": now_iso(),
            "history": [
                time.strftime("%H:%M:%S ", time.localtime(c.time)) + c.label
                for c in h.entries[: h.position]
            ],
        }
        items.append((SORTER_FILE, json_writer(sorter)))
        commit_files(folder, items)
        discard_autosave(folder)
        self._n_saved = n
        self._saved_rows = (key, self._rows_rev)
        self._base_mtime = ident_mtime(folder)
        self.mark_saved()

    def _merged_meta(self, recording_paths) -> dict:
        meta = json.loads(
            json.dumps(self.meta or self._snippet_meta or {}, default=str)
        )
        g = self.grid
        if g is not None:
            meta["rate"] = g.rate
            meta["start"] = g.s0 / g.rate
            meta["frame_step"] = g.step / g.rate
            end = (g.s0 + max(g.n_frames - 1, 0) * g.step + g.nfft) / g.rate
            meta["duration"] = end - g.s0 / g.rate
            cfg = meta.setdefault("config", {})
            if isinstance(cfg, dict):
                cfg.setdefault("spectrogram", {})["nfft"] = g.nfft
        if recording_paths:
            paths = [str(p) for p in recording_paths]
            meta["input"] = paths[0] if len(paths) == 1 else paths
            meta["files"] = meta["input"]
        return meta

    def moved_to(self, folder) -> None:
        """The results directory this session was loaded from or saved into
        was renamed to `folder`: what is on disk there is still current."""
        if self._saved_rows is not None:
            self._saved_rows = (_folder_key(folder), self._saved_rows[1])

    def mark_saved(self) -> None:
        self.history.saved_position = self.history.position
        self._meta_dirty = False

    def write_autosave(self, folder, recording: str | None = None) -> None:
        n, nb = self._n, self._n_saved
        h = self.history
        g = self.grid
        info = {
            "grid": [g.rate, g.nfft, g.step, g.s0, g.n_frames] if g else None,
            "meta": json.loads(
                json.dumps(self.meta or self._snippet_meta or {}, default=str)
            ),
            "recording": recording,
            "n_base": nb,
            "next_id": int(self.next_id),
            "labels": {str(k): v for k, v in self.labels.items()},
            "notes": {str(k): v for k, v in self.notes.items()},
            "history": [c.label for c in h.entries[: h.position]],
            "base_mtime": self._base_mtime or ident_mtime(folder),
            "time": time.time(),
        }
        payload = {"ident": np.array(self.ident), "info": np.array(json.dumps(info))}
        if n > nb:
            s = slice(nb, n)
            payload.update(
                a_fund=self._fund[s].copy(),
                a_idx=self._idx[s].copy(),
                a_tracked=self._tracked[s].copy(),
                a_sign=self._sign[s].copy(),
            )
            if self._cplx is not None:
                payload["a_cplx"] = self._cplx[s].copy()
        write_autosave_file(folder, payload)
