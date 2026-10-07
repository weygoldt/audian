"""Harmonics tracked as fish: is a track the h-th harmonic of another?

Design section 5.13.  Nothing stops a reader from annotating a harmonic by
hand (Add along the stripe at 2x or 3x a fish), and the tracker itself
sometimes follows one.  A harmonic follows its fundamental exactly,
f_hi(t) = h * f_lo(t), so on the frames two tracks have in common

* ``h = round(f_hi / f_lo)`` with 2 <= h <= `max_harmonic`, and
* ``offset = median(f_hi - h * f_lo)`` is zero up to the frequency
  estimation error, while a different fish near h * f has an offset and
  drifts on its own.

This is the logic of wavetracker's own ``wavetracker/comodulation.py``
(`score_pairs` / `classify`), re-implemented with numpy so the model stays
free of pandas and wavetracker (importing it costs 0.12 s and pandas).  Its
constants are mirrored in `HarmonicRule` and pinned by a test.  Two things
differ, because hand-annotated strokes are often only a few seconds long:

* **Short overlaps** (less than `min_overlap`, wavetracker's 10 s) are
  judged by the offset alone, and wavetracker's fixed 0.25 Hz is replaced by
  a tolerance in units of the session's frequency resolution (the frame
  spectrum's bin, rate / nfft), growing with h: the error of h * f_lo is h
  times the fundamental's, so the error of ``f_hi - h * f_lo`` grows as
  sqrt(1 + h**2).  ``|offset| <= offset_bins * bin * sqrt(1 + h**2)``.
  Besides the median, most common frames must match on their own
  (``|f_hi - h f_lo| <= frame_bins * bin * sqrt(1 + h**2)`` in at least
  `min_match` of them), so a track that only crosses h * f somewhere is not
  a harmonic, and at least `min_frames` common frames are needed at all.
* **Long overlaps** (at least `min_overlap`) additionally need fast
  frequency co-modulation, as wavetracker requires: each trace minus its
  running median over `timescale`, correlated, at least `min_freq_corr`.
  Interference lines (exact multiples of each other, but flat) and two fish
  that drift together with temperature fail it.

Entry points: `find_harmonics` (all pairs of a session, or every pair one
of a set of ids takes part in, both directions) and `check_track` (a
candidate track that is not in the session yet: the Add preview).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Callable, Optional

import numpy as np


@dataclass(frozen=True)
class HarmonicRule:
    """The thresholds; see the module docstring.  The first four mirror
    ``wavetracker.comodulation.ComodulationConfig`` (a test pins them)."""

    max_harmonic: int = 5  # ComodulationConfig.max_harmonic
    min_overlap: float = 10.0  # ComodulationConfig.min_overlap [s]
    timescale: float = 30.0  # ComodulationConfig.timescale [s]
    min_freq_corr: float = 0.5  # ComodulationConfig.min_freq_corr
    #: |median offset| bound, in bins times sqrt(1 + h**2)
    offset_bins: float = 0.1
    #: a single frame matches within this, in bins times sqrt(1 + h**2)
    frame_bins: float = 0.3
    #: fraction of the common frames that must match
    min_match: float = 0.7
    #: fewer common frames than this are not judged
    min_frames: int = 5

    def offset_tol(self, h, bin_hz: float):
        return self.offset_bins * bin_hz * np.sqrt(1.0 + np.square(h))

    def frame_tol(self, h, bin_hz: float):
        return self.frame_bins * bin_hz * np.sqrt(1.0 + np.square(h))


@dataclass(frozen=True)
class Harmonic:
    """`harmonic_id` looks like the `h`-th harmonic of `fundamental_id`."""

    harmonic_id: float
    fundamental_id: float
    h: int
    offset_hz: float  # median(f_hi - h f_lo) on the common frames
    n_common: int  # common frames
    overlap_s: float  # n_common times the frame step
    freq_corr: Optional[float]  # fast co-modulation; None for a short overlap
    match: float  # fraction of common frames within the frame tolerance
    t0: float  # first and last common frame [s]
    t1: float
    f: float  # the harmonic's median frequency on the common frames

    def ordinal(self) -> str:
        return ordinal(self.h)


def ordinal(h: int) -> str:
    h = int(h)
    suffix = (
        "th" if 10 <= h % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(h % 10, "th")
    )
    return f"{h}{suffix}"


# --------------------------------------------------------------------------
# fast co-modulation (wavetracker's _highpass and _corr)

#: the running median is evaluated at most at this many (points x window)
#: values; longer traces are evaluated at a stride and interpolated
_MEDIAN_BUDGET = 2_000_000


def highpass(k: np.ndarray, x: np.ndarray, width: int) -> np.ndarray:
    """``x`` minus its centred running median over `width` frames, the
    window counted on the frame grid ``k`` (sorted, unique), so gaps shrink
    it as pandas' time-based rolling window does in wavetracker."""
    k = np.asarray(k, dtype=np.int64)
    x = np.asarray(x, dtype=np.float64)
    n = len(x)
    if n == 0:
        return x.copy()
    half = max(0, int(width) // 2)
    grid = np.full(int(k[-1] - k[0]) + 1 + 2 * half, np.nan)
    pos = k - k[0] + half
    grid[pos] = x
    w = 2 * half + 1
    stride = max(1, int(math.ceil(n * w / _MEDIAN_BUDGET)))
    at = np.arange(0, n, stride)
    if at[-1] != n - 1:
        at = np.r_[at, n - 1]
    win = np.lib.stride_tricks.sliding_window_view(grid, w)[pos[at] - half]
    # nanmedian, vectorised: NaN sorts last, so the valid values of each
    # window lead its sorted row
    srt = np.sort(win, axis=1)
    cnt = np.sum(np.isfinite(win), axis=1)
    i = np.arange(len(at))
    trend = 0.5 * (srt[i, (cnt - 1) // 2] + srt[i, cnt // 2])
    if len(at) < n:
        trend = np.interp(np.arange(n), at, trend)
    return x - trend


def corr(x: np.ndarray, y: np.ndarray) -> float:
    sx, sy = x.std(), y.std()
    if sx == 0 or sy == 0 or not (np.isfinite(sx) and np.isfinite(sy)):
        return float("nan")
    return float(np.mean((x - x.mean()) * (y - y.mean())) / (sx * sy))


# --------------------------------------------------------------------------
# judging one pair


def judge(
    k_lo: np.ndarray,
    f_lo: np.ndarray,
    k_hi: np.ndarray,
    f_hi: np.ndarray,
    frame_dt: float,
    bin_hz: float,
    rule: HarmonicRule = HarmonicRule(),
    h: Optional[int] = None,
) -> Optional[tuple]:
    """Whether the track ``(k_hi, f_hi)`` is a harmonic of ``(k_lo, f_lo)``
    (each sorted by frame, one detection per frame).  Returns
    ``(h, offset, n_common, freq_corr, match, k_first, k_last, f_median)``
    or None."""
    _, a, b = np.intersect1d(k_lo, k_hi, assume_unique=True, return_indices=True)
    n = len(a)
    if n < rule.min_frames:
        return None
    x, y = f_lo[a], f_hi[b]
    if h is None:
        h = int(round(float(np.median(y)) / float(np.median(x))))
    if h < 2 or h > rule.max_harmonic:
        return None
    resid = y - h * x
    offset = float(np.median(resid))
    if abs(offset) > rule.offset_tol(h, bin_hz):
        return None
    match = float(np.mean(np.abs(resid) <= rule.frame_tol(h, bin_hz)))
    if match < rule.min_match:
        return None
    kc = k_lo[a]
    fc = None
    if n * frame_dt >= rule.min_overlap:
        width = int(round(rule.timescale / frame_dt))
        fc = corr(highpass(kc, h * x, width), highpass(kc, y, width))
        if not fc >= rule.min_freq_corr:  # NaN fails too
            return None
    return h, offset, n, fc, match, int(kc[0]), int(kc[-1]), float(np.median(y))


# --------------------------------------------------------------------------
# finding candidate pairs, vectorised


#: candidates within one tolerance window looked up by gathers before
#: falling back to a second binary search
_GATHER = 4


def _near(q_k, c_key, scale, target, tol):
    """Pairs (query, candidate) with the candidate in the query's frame and
    within ``target +- tol`` of frequency; candidates sorted by
    ``key = k * scale + f``, queries by the same key (sorted needles make
    numpy's binary search cheap)."""
    base = q_k * scale
    upper = base + (target + tol)
    lo = np.searchsorted(c_key, base + (target - tol), "left")
    # a window holds a handful of detections at most: count them by
    # looking at the next few keys instead of a second binary search
    n = len(c_key)
    cnt = np.zeros(len(lo), np.int64)
    open_ = np.ones(len(lo), bool)
    for j in range(_GATHER):
        at = lo + j
        inside = open_ & (at < n)
        inside[inside] = c_key[at[inside]] <= upper[inside]
        cnt += inside
        open_ = inside
    if open_.any():
        w = np.flatnonzero(open_)
        cnt[w] = np.searchsorted(c_key, upper[w], "right") - lo[w]
    keep = np.flatnonzero(cnt > 0)
    if not len(keep):
        return np.zeros(0, np.int64), np.zeros(0, np.int64)
    cnt = cnt[keep]
    qi = np.repeat(keep, cnt)
    first = np.repeat(lo[keep] - (np.cumsum(cnt) - cnt), cnt)
    ci = first + np.arange(len(qi))
    return qi, ci


def _candidates(q_k, q_f, q_id, c_k, c_f, c_id, bin_hz, rule, both=None):
    """Frame-level matches between query rows and candidate rows, grouped
    into (id_lo, id_hi, h) with their match count and median offset.
    ``both``: the query ids; also look for the query as the harmonic
    (f / h) of a candidate that is not one of them (a query candidate is
    found the other way round already)."""
    same = q_k is c_k and q_f is c_f
    cmax = float(c_f.max())
    fmax = max(cmax, float(q_f.max()))
    tmax = float(rule.frame_tol(rule.max_harmonic, bin_hz))
    # one float key per detection, frame-major: k * scale + f (exact to
    # about 1e-6 Hz for a million frames)
    scale = rule.max_harmonic * fmax + 4 * tmax + 1.0
    c_key = c_k * scale + c_f
    o = np.argsort(c_key)
    c_key, c_k, c_f, c_id = c_key[o], c_k[o], c_f[o], c_id[o]
    if same:
        q_k, q_f, q_id = c_k, c_f, c_id
    else:
        o = np.argsort(q_k * scale + q_f)
        q_k, q_f, q_id = q_k[o], q_f[o], q_id[o]
    los, his, hs, offs = [], [], [], []
    for h in range(2, rule.max_harmonic + 1):
        tol = float(rule.frame_tol(h, bin_hz))
        # query as the fundamental: a candidate near h * f
        ok = np.flatnonzero(h * q_f <= cmax + tol)
        if len(ok):
            qi, ci = _near(q_k[ok], c_key, scale, h * q_f[ok], tol)
            qi = ok[qi]
            los.append(q_id[qi])
            his.append(c_id[ci])
            hs.append(np.full(len(qi), h, np.int64))
            offs.append(c_f[ci] - h * q_f[qi])
        if both is not None:
            # query as the harmonic: a candidate near f / h
            qi, ci = _near(q_k, c_key, scale, q_f / h, tol / h)
            other = ~np.isin(c_id[ci], both)
            qi, ci = qi[other], ci[other]
            los.append(c_id[ci])
            his.append(q_id[qi])
            hs.append(np.full(len(qi), h, np.int64))
            offs.append(q_f[qi] - h * c_f[ci])
    if not los:
        return None
    lo = np.concatenate(los)
    hi = np.concatenate(his)
    hh = np.concatenate(hs)
    off = np.concatenate(offs)
    ok = lo != hi
    lo, hi, hh, off = lo[ok], hi[ok], hh[ok], off[ok]
    if not len(lo):
        return None
    o = np.lexsort((off, hh, hi, lo))
    lo, hi, hh, off = lo[o], hi[o], hh[o], off[o]
    new = np.r_[True, (lo[1:] != lo[:-1]) | (hi[1:] != hi[:-1]) | (hh[1:] != hh[:-1])]
    start = np.flatnonzero(new)
    cnt = np.diff(np.r_[start, len(lo)])
    med = 0.5 * (off[start + (cnt - 1) // 2] + off[start + cnt // 2])
    return lo[start], hi[start], hh[start], cnt, med


def _rows_by_id(ident, rows):
    """``rows_of`` for arrays: rows of each id sorted by frame."""
    order = rows[np.argsort(ident[rows], kind="stable")]
    ids = ident[order]
    cut = np.flatnonzero(np.r_[True, ids[1:] != ids[:-1], True])
    table = {float(ids[a]): order[a:b] for a, b in zip(cut[:-1], cut[1:])}
    return lambda i: table.get(float(i), np.zeros(0, np.int64))


def _one_per_frame(k, f):
    """The first detection per frame, sorted by frame (rows of an id come
    sorted by frame from the model, so this is usually a diff)."""
    k = np.asarray(k)
    if len(k) > 1 and not np.all(k[1:] >= k[:-1]):
        o = np.argsort(k, kind="stable")
        k, f = k[o], f[o]
    first = np.r_[True, k[1:] != k[:-1]] if len(k) else np.zeros(0, bool)
    return k[first], f[first]


def find_harmonics(
    fund: np.ndarray,
    idx: np.ndarray,
    ident: np.ndarray,
    times: np.ndarray,
    bin_hz: float,
    ids=None,
    rule: HarmonicRule = HarmonicRule(),
    rows_of: Optional[Callable[[float], np.ndarray]] = None,
    rows: Optional[np.ndarray] = None,
) -> list[Harmonic]:
    """Pairs of ids in harmonic relation.

    ``ids=None``: every pair in the session (the issue sweep).  Otherwise
    every pair one of `ids` takes part in, as the harmonic of another id or
    as the fundamental of another.  `rows_of(id)` (rows sorted by frame) is
    the model's index; `rows` the assigned rows (both computed from
    `ident` when not given).  One finding per harmonic id: the best
    fundamental (one that is not itself a harmonic, then the most matching
    frames)."""
    fund = np.asarray(fund, dtype=np.float64)
    idx = np.asarray(idx, dtype=np.int64)
    ident = np.asarray(ident, dtype=np.float64)
    times = np.asarray(times, dtype=np.float64)
    if rows is None:
        rows = np.flatnonzero(np.isfinite(ident))
    if not len(rows) or not bin_hz > 0:
        return []
    if ids is None:
        q = c = rows
        both = None
    else:
        want = np.unique(np.asarray(ids, dtype=np.float64))
        want = want[np.isfinite(want)]
        if rows_of is None:
            rows_of = _rows_by_id(ident, rows)
        parts = [rows_of(i) for i in want]
        q = np.concatenate(parts) if parts else np.zeros(0, np.int64)
        if not len(q):
            return []
        # every assigned row in the frames the ids occupy
        in_frames = np.isin(idx[rows], np.unique(idx[q]))
        c = rows[in_frames]
        both = want
    got = _candidates(
        idx[q], fund[q], ident[q], idx[c], fund[c], ident[c], bin_hz, rule, both
    )
    if got is None:
        return []
    lo, hi, hh, cnt, med = got
    keep = (cnt >= rule.min_frames) & (np.abs(med) <= rule.offset_tol(hh, bin_hz))
    lo, hi, hh, cnt = lo[keep], hi[keep], hh[keep], cnt[keep]
    if not len(lo):
        return []
    if rows_of is None:
        rows_of = _rows_by_id(ident, rows)
    frame_dt = float(np.median(np.diff(times))) if len(times) > 1 else 1.0
    tracks = {}

    def track(i):
        if i not in tracks:
            r = rows_of(i)
            tracks[i] = _one_per_frame(idx[r], fund[r])
        return tracks[i]

    found = []
    for a, b, h in zip(lo.tolist(), hi.tolist(), hh.tolist()):
        ka, fa = track(a)
        kb, fb = track(b)
        j = judge(ka, fa, kb, fb, frame_dt, bin_hz, rule, h=h)
        if j is None:
            continue
        h, off, n, fc, match, k0, k1, f = j
        found.append(
            Harmonic(
                harmonic_id=float(b),
                fundamental_id=float(a),
                h=int(h),
                offset_hz=off,
                n_common=int(n),
                overlap_s=n * frame_dt,
                freq_corr=fc,
                match=match,
                t0=float(times[k0]),
                t1=float(times[k1]),
                f=f,
            )
        )
    return best_per_harmonic(found)


def best_per_harmonic(found: list[Harmonic]) -> list[Harmonic]:
    """One finding per harmonic id, as wavetracker's `find_harmonics`
    keeps one row per ``high``: prefer a fundamental that is not itself a
    harmonic, then the most matching common frames, then the lowest
    offset."""
    harm = {x.harmonic_id for x in found}
    best: dict[float, Harmonic] = {}
    for x in found:
        key = (
            x.fundamental_id not in harm,
            x.match * x.n_common,
            -abs(x.offset_hz),
        )
        cur = best.get(x.harmonic_id)
        if cur is None or key > (
            cur.fundamental_id not in harm,
            cur.match * cur.n_common,
            -abs(cur.offset_hz),
        ):
            best[x.harmonic_id] = x
    return sorted(best.values(), key=lambda x: (x.t0, x.harmonic_id))


def check_track(
    frames: np.ndarray,
    freqs: np.ndarray,
    fund: np.ndarray,
    idx: np.ndarray,
    ident: np.ndarray,
    times: np.ndarray,
    bin_hz: float,
    rows: np.ndarray,
    rule: HarmonicRule = HarmonicRule(),
    exclude=(),
) -> list[Harmonic]:
    """A candidate track not in the session yet (``frames``, ``freqs``)
    against the session's assigned `rows` in those frames (the Add
    preview).  Findings name the candidate as id NaN; `exclude` ids are not
    compared (the track the stroke extends)."""
    frames = np.asarray(frames, dtype=np.int64)
    freqs = np.asarray(freqs, dtype=np.float64)
    ok = np.isfinite(freqs)
    frames, freqs = frames[ok], freqs[ok]
    if len(frames) < rule.min_frames or not len(rows):
        return []
    frames, freqs = _one_per_frame(frames, freqs)
    ident = np.asarray(ident, dtype=np.float64)
    rows = np.asarray(rows, dtype=np.int64)
    rows = rows[np.isfinite(ident[rows])]
    if len(exclude):
        rows = rows[~np.isin(ident[rows], np.asarray(exclude, dtype=np.float64))]
    if not len(rows):
        return []
    # the candidate gets an id no row has: one below the smallest
    pseudo = float(np.min(ident[rows])) - 1.0
    f_all = np.concatenate([np.asarray(fund, dtype=np.float64)[rows], freqs])
    k_all = np.concatenate([np.asarray(idx, dtype=np.int64)[rows], frames])
    i_all = np.concatenate([ident[rows], np.full(len(frames), pseudo)])
    out = find_harmonics(f_all, k_all, i_all, times, bin_hz, ids=[pseudo], rule=rule)
    nan = float("nan")
    fixed = []
    for x in out:
        if x.harmonic_id == pseudo:
            fixed.append(_replace(x, harmonic_id=nan))
        elif x.fundamental_id == pseudo:
            fixed.append(_replace(x, fundamental_id=nan))
    return fixed


@dataclass(frozen=True)
class BandHit:
    """A brushed band runs along a harmonic relation with an existing id:
    ``h * f_id`` lies in the band (`as_harmonic`: what the stroke adds would
    be id's h-th harmonic) or ``f_id / h`` does (id would be the h-th
    harmonic of what the stroke adds), in `frac` of `n` frames."""

    id: float
    h: int
    as_harmonic: bool
    frac: float
    n: int


def band_harmonics(
    frames: np.ndarray,
    lo: np.ndarray,
    hi: np.ndarray,
    fund: np.ndarray,
    idx: np.ndarray,
    ident: np.ndarray,
    rows: np.ndarray,
    rule: HarmonicRule = HarmonicRule(),
    exclude=(),
) -> list[BandHit]:
    """Existing ids whose h-th multiple (or h-th fraction) runs inside a
    brushed band ``[lo, hi]`` per frame: the Add stroke's preview, before
    any frequency is known.  An id counts when it has at least
    `min_frames` frames in the stroke and the band covers the multiple in at
    least `min_match` of them.  Best first (most covered frames)."""
    frames = np.asarray(frames, dtype=np.int64)
    lo = np.asarray(lo, dtype=np.float64)
    hi = np.asarray(hi, dtype=np.float64)
    rows = np.asarray(rows, dtype=np.int64)
    if not len(frames) or not len(rows):
        return []
    rows = rows[np.isfinite(ident[rows])]
    if len(exclude):
        rows = rows[~np.isin(ident[rows], np.asarray(exclude, dtype=np.float64))]
    o = np.argsort(frames, kind="stable")
    frames, lo, hi = frames[o], lo[o], hi[o]
    pos = np.searchsorted(frames, idx[rows])
    pos = np.minimum(pos, len(frames) - 1)
    there = (frames[pos] == idx[rows]) & np.isfinite(lo[pos]) & np.isfinite(hi[pos])
    rows, pos = rows[there], pos[there]
    if not len(rows):
        return []
    ids, inv, present = np.unique(ident[rows], return_inverse=True, return_counts=True)
    f = fund[rows]
    out = []
    for h in range(2, rule.max_harmonic + 1):
        for as_harmonic, x in ((True, h * f), (False, f / h)):
            inside = (x >= lo[pos]) & (x <= hi[pos])
            cnt = np.bincount(inv[inside], minlength=len(ids))
            ok = (present >= rule.min_frames) & (cnt >= rule.min_match * present)
            for j in np.flatnonzero(ok):
                out.append(
                    BandHit(
                        float(ids[j]),
                        h,
                        as_harmonic,
                        float(cnt[j] / present[j]),
                        int(present[j]),
                    )
                )
    out.sort(key=lambda b: (-b.frac * b.n, b.h))
    return out


def _replace(x: Harmonic, **kw) -> Harmonic:
    return replace(x, **kw)


__all__ = [
    "BandHit",
    "Harmonic",
    "HarmonicRule",
    "band_harmonics",
    "best_per_harmonic",
    "check_track",
    "corr",
    "find_harmonics",
    "highpass",
    "judge",
    "ordinal",
]
