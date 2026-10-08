"""What the overlay draws and what the pointer is over, in numpy only.

Everything here is a pure function of a track set, a view and a few display
options, so it is tested in the fast suite and costs nothing to call from a
hover handler.  No Qt, no pyqtgraph, no `audian`: `overlay`, `tools` and
`panel` turn these arrays into items and gestures.

The track set is read through a handful of attributes only -- ``fund``,
``idx``, ``ident``, ``times``, ``rows_in_frames``, ``rows_of``, ``ids`` and,
when it has them, ``revision`` and ``next_id`` -- so a stand-in with those is
enough to test against (`tests/test_eodsorter_geometry.py` uses one).

Screen pixels, not data units
-----------------------------

Every tolerance is in screen pixels: a pick radius of 10 px means 10 px at
every zoom, on a lane showing four hours or four seconds.  `View` is the one
conversion; ``px`` grows to the right and ``py`` grows *down* the lane, as in
the view box's own local coordinates, so a `ToolSurface` hands its pointer
position here unconverted.

Bounded by the screen, not by the data
--------------------------------------

`polylines` draws at most `PIXEL_DENSITY` vertices per pixel column per
track, and `thin_points` at most one point per pixel cell, so what reaches
pyqtgraph is bounded by the lane's size whatever the zoom.  Zoomed in (one
frame per bin) the result is exact: every detection is a vertex.  Zoomed out
the frames are binned and each track keeps the first detection in each bin,
found without sorting the rows (`_first_per_key`), which is what keeps a
full zoom-out of a million detections inside the 30 ms the design allows.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import NamedTuple, Optional

import numpy as np

#: Colour slots of the per-id palette (design 5.7).
N_SLOTS = 10

#: Multiplier spreading consecutive ids over the slots: coprime to
#: `N_SLOTS`, so every slot is used, and consecutive ids -- which a tracker
#: hands to neighbouring fragments -- land three slots apart.
SLOT_STRIDE = 7

#: Most vertices per pixel column per track.  Two rather than one: a track
#: that steps within a pixel column has two frequencies there.
PIXEL_DENSITY = 2.0

#: Above this many visible rows the polylines are always binned (and the
#: points layer is not drawn: at that density the lines are the picture).
MAX_EXACT_ROWS = 150_000

#: Largest dense ``(id, bin)`` table `_first_per_key` will allocate before
#: falling back to a sort.  32 M int32 = 128 MB is too much for a redraw;
#: 16 M entries is 64 MB of short-lived scratch, which numpy allocates lazily.
MAX_TABLE = 16_000_000

#: Pick radius, in screen pixels (design 5.4).
PICK_PX = 10.0


# ------------------------------------------------------------------ the view


@dataclass(frozen=True)
class View:
    """A lane's data range and its size in pixels.

    ``px = (t - x0) * w / (x1 - x0)`` and ``py = (y1 - f) * h / (y1 - y0)``:
    pixel 0 is the left and the *top* of the lane, as in the view box's local
    coordinates.
    """

    x0: float
    x1: float
    y0: float
    y1: float
    w_px: float
    h_px: float

    @property
    def sx(self) -> float:
        """Pixels per second."""
        return self.w_px / max(self.x1 - self.x0, 1e-12)

    @property
    def sy(self) -> float:
        """Pixels per hertz."""
        return self.h_px / max(self.y1 - self.y0, 1e-12)

    def to_px(self, t, f) -> tuple[np.ndarray, np.ndarray]:
        t = np.asarray(t, dtype=np.float64)
        f = np.asarray(f, dtype=np.float64)
        return (t - self.x0) * self.sx, (self.y1 - f) * self.sy

    def to_data(self, px, py) -> tuple[float, float]:
        return (self.x0 + float(px) / self.sx, self.y1 - float(py) / self.sy)

    def dt(self, px: float) -> float:
        """`px` pixels as a duration."""
        return float(px) / self.sx

    def df(self, px: float) -> float:
        """`px` pixels as a frequency span."""
        return float(px) / self.sy

    def key(self) -> tuple:
        return (
            round(self.x0, 9),
            round(self.x1, 9),
            round(self.y0, 6),
            round(self.y1, 6),
            int(round(self.w_px)),
            int(round(self.h_px)),
        )


# --------------------------------------------------------------------- slots


def slot_of(ids) -> np.ndarray:
    """The colour slot of each id: ``(int(id) * 7) % 10``.

    Stable for an id for its whole life and across sessions.  NaN (an
    unassigned row) has no slot and comes back as -1.
    """
    ids = np.asarray(ids, dtype=np.float64)
    out = np.full(ids.shape, -1, dtype=np.int64)
    good = np.isfinite(ids)
    out[good] = (ids[good].astype(np.int64) * SLOT_STRIDE) % N_SLOTS
    return out


# ------------------------------------------------------------------- framing


def frame_window(times: np.ndarray, t0: float, t1: float) -> tuple[int, int]:
    """Frames whose centre lies in ``[t0, t1]``: ``(k0, k1)``, k1 exclusive."""
    times = np.asarray(times)
    k0 = int(np.searchsorted(times, t0, side="left"))
    k1 = int(np.searchsorted(times, t1, side="right"))
    return k0, max(k0, k1)


def view_frames(ts, view: View, pad_frames: int = 1) -> tuple[int, int]:
    """The frames a view shows, plus `pad_frames` each side.

    The padding is what lets a line leave the lane at its edge rather than
    stopping one frame short of it.
    """
    n = len(ts.times)
    k0, k1 = frame_window(ts.times, view.x0, view.x1)
    return max(0, k0 - pad_frames), min(n, k1 + pad_frames)


def frame_step(times: np.ndarray) -> float:
    """Seconds between frames (the median spacing), or 1.0 for < 2 frames."""
    times = np.asarray(times, dtype=np.float64)
    if len(times) < 2:
        return 1.0
    return float(np.median(np.diff(times[: min(len(times), 10_001)]))) or 1.0


def gap_frames_for(times: np.ndarray, gap_break_s: float) -> float:
    """`gap_break_s` in frames; a line is broken where frames jump more."""
    if gap_break_s <= 0:
        return np.inf
    return max(1.0, float(gap_break_s) / frame_step(times))


# ------------------------------------------------------------------- helpers


def _first_per_key(key: np.ndarray, size: int) -> np.ndarray:
    """Index of the first occurrence of each distinct key, in key order.

    ``key`` holds integers in ``[0, size)``.  Done with a scatter into a
    dense table rather than a sort: writing indices in reverse leaves the
    smallest index in each cell, and reading the occupied cells in order
    returns them sorted by key.  O(n + size), which is the difference between
    10 ms and 150 ms on a million rows.
    """
    n = len(key)
    if n == 0:
        return np.zeros(0, dtype=np.int64)
    if size <= MAX_TABLE and n < 2**31:
        # int32: half the memory traffic of int64, which is most of the cost
        table = np.full(size, -1, dtype=np.int32)
        table[key[::-1]] = np.arange(n - 1, -1, -1, dtype=np.int32)
        return table[table >= 0].astype(np.int64)
    _uniq, first = np.unique(key, return_index=True)
    return first.astype(np.int64)


def _insert_breaks(x: np.ndarray, y: np.ndarray, brk: np.ndarray):
    """``x, y`` with NaN inserted after every index where `brk` is set.

    `brk` has ``len(x) - 1`` entries: ``brk[i]`` breaks between ``i`` and
    ``i + 1``.  `pg.PlotCurveItem(connect="finite")` draws a NaN as a gap.
    """
    at = np.flatnonzero(brk) + 1
    if len(at) == 0:
        return x, y
    return np.insert(x, at, np.nan), np.insert(y, at, np.nan)


def assigned(ts, rows: np.ndarray) -> np.ndarray:
    rows = np.asarray(rows, dtype=np.int64)
    return rows[np.isfinite(ts.ident[rows])]


def unassigned(ts, rows: np.ndarray) -> np.ndarray:
    rows = np.asarray(rows, dtype=np.int64)
    return rows[np.isnan(ts.ident[rows])]


def filter_ids(ts, rows: np.ndarray, ids=None, exclude=None) -> np.ndarray:
    """Rows whose id is in `ids` (when given) and not in `exclude`."""
    rows = np.asarray(rows, dtype=np.int64)
    if ids is not None:
        ids = np.asarray(list(ids), dtype=np.float64)
        rows = rows[np.isin(ts.ident[rows], ids)]
    if exclude:
        ex = np.asarray(list(exclude), dtype=np.float64)
        rows = rows[~np.isin(ts.ident[rows], ex)]
    return rows


# ----------------------------------------------------------------- polylines


def track_order(ts, rows: np.ndarray) -> np.ndarray:
    """`rows` sorted by ``(id, frame, row)``."""
    rows = np.asarray(rows, dtype=np.int64)
    if len(rows) == 0:
        return rows
    order = np.lexsort((rows, ts.idx[rows], ts.ident[rows]))
    return rows[order]


def track_breaks(ts, ordered: np.ndarray, gap_frames: float) -> np.ndarray:
    """Where a sequence in `track_order` must not be joined by a line."""
    if len(ordered) < 2:
        return np.zeros(0, dtype=bool)
    ids = ts.ident[ordered]
    k = ts.idx[ordered]
    return (ids[1:] != ids[:-1]) | (np.diff(k) > gap_frames)


def track_line(ts, rows: np.ndarray, gap_frames: float):
    """One or more tracks as a NaN-broken ``(x, y)`` line, undecimated."""
    ordered = track_order(ts, assigned(ts, rows))
    x = ts.times[ts.idx[ordered]]
    y = ts.fund[ordered]
    return _insert_breaks(x, y, track_breaks(ts, ordered, gap_frames))


def bin_size(k0: int, k1: int, w_px: float) -> int:
    """Frames per bin, so that at most `PIXEL_DENSITY` bins fall in a pixel."""
    allowed = max(2.0, float(w_px) * PIXEL_DENSITY)
    return max(1, int(np.ceil((k1 - k0) / allowed)))


class IdCodes:
    """A dense ``id -> 0..n-1`` lookup, for the binned path's table.

    Built from ``ts.ids()``; a lookup table when the ids are small integers
    (the usual case: ids are handed out from a counter), a `searchsorted`
    otherwise.
    """

    def __init__(self, ids: np.ndarray) -> None:
        self.ids = np.asarray(ids, dtype=np.float64)
        self.n = len(self.ids)
        self.lut = None
        if self.n and self.ids[0] >= 0 and self.ids[-1] < 4 * max(self.n, 1_000_000):
            self.lut = np.full(int(self.ids[-1]) + 1, -1, dtype=np.int64)
            self.lut[self.ids.astype(np.int64)] = np.arange(self.n)

    def __call__(self, ident: np.ndarray) -> np.ndarray:
        if self.lut is not None:
            return self.lut[ident.astype(np.int64)]
        return np.searchsorted(self.ids, ident)


def polylines(
    ts,
    rows: np.ndarray,
    view: View,
    gap_frames: float,
    max_rows: int = MAX_EXACT_ROWS,
    codes: Optional[IdCodes] = None,
    assigned_only: bool = False,
) -> dict:
    """``{slot: (x, y)}``: every track among `rows`, NaN-joined per slot.

    `rows` are the candidate rows (normally `visible_rows`); unassigned ones
    are ignored.  A line breaks where the id changes and where two
    consecutive detections of a track are more than `gap_frames` apart.

    While the view shows no more than `PIXEL_DENSITY` frames per pixel and
    there are at most `max_rows` rows, every detection is a vertex.  Beyond
    either, frames are binned (`bin_size`) and each track keeps the first
    detection in each bin, so the vertex count is bounded by the lane's width
    times the visible tracks, not by the detections.
    """
    if not assigned_only:
        rows = assigned(ts, rows)
    if len(rows) == 0:
        return {}
    # bins are aligned to the view, not to the rows given, so drawing a
    # subset (one colour slot after an edit) bins exactly like the whole
    k0, k1 = view_frames(ts, view)
    # rows outside the view's frames (a selection, say) widen the window;
    # rows are in frame order within an id, so the ends bound them
    ends = ts.idx[rows[[0, -1]]]
    k0, k1 = min(k0, int(ends.min())), max(k1, int(ends.max()) + 1)
    s = bin_size(*view_frames(ts, view), view.w_px)
    if s == 1 and len(rows) <= max_rows:
        ordered = track_order(ts, rows)
        brk = track_breaks(ts, ordered, gap_frames)
    else:
        ordered = binned_order(ts, rows, k0, k1, s, codes)
        ids = ts.ident[ordered]
        kk = ts.idx[ordered]
        limit = max(gap_frames, 2 * s) if s > 1 else gap_frames
        brk = (ids[1:] != ids[:-1]) | (np.diff(kk) > limit)
    return split_slots(ts, ordered, brk)


def binned_order(ts, rows, k0: int, k1: int, s: int, codes=None) -> np.ndarray:
    """The first row per ``(id, bin of s frames)``, sorted by id then bin.

    `rows` must be in frame order within each id (`rows_in_frames` is).
    """
    if codes is None:
        codes = IdCodes(ts.ids())
    code = codes(ts.ident[rows])
    nbins = (k1 - k0 + s - 1) // s
    small = max(1, codes.n) * nbins < 2**31
    dtype = np.int32 if small else np.int64
    b = np.clip((ts.idx[rows] - k0).astype(dtype) // dtype(s), 0, nbins - 1)
    key = code.astype(dtype) * dtype(nbins) + b
    first = _first_per_key(key, max(1, codes.n) * nbins)
    return rows[first]


def split_slots(ts, ordered: np.ndarray, brk: np.ndarray) -> dict:
    """Rows in track order and their breaks, as ``{slot: (x, y)}``."""
    if len(ordered) == 0:
        return {}
    slots = slot_of(ts.ident[ordered])
    x = ts.times[ts.idx[ordered]]
    y = ts.fund[ordered]
    by_slot = np.argsort(slots, kind="stable")
    slots_sorted = slots[by_slot]
    xs, ys = x[by_slot], y[by_slot]
    ids = ts.ident[ordered][by_slot]
    # rows of one id stay contiguous and in frame order under the stable
    # sort, so between two rows of the same id the original break applies;
    # between different ids there is always one
    brk_full = np.append(np.asarray(brk, dtype=bool), True)
    brk2 = (ids[1:] != ids[:-1]) | brk_full[by_slot[:-1]]
    out = {}
    edges = np.flatnonzero(np.diff(slots_sorted)) + 1
    starts = np.concatenate(([0], edges))
    stops = np.concatenate((edges, [len(slots_sorted)]))
    for a0, a1 in zip(starts, stops):
        px, py = _insert_breaks(xs[a0:a1], ys[a0:a1], brk2[a0 : a1 - 1])
        out[int(slots_sorted[a0])] = (px, py)
    return out


# -------------------------------------------------------------------- points


def thin_points(px, py, w_px: Optional[float] = None, h_px=None) -> np.ndarray:
    """A mask keeping exactly one point per integer pixel cell.

    The first point (in array order) of each cell is kept.  Points outside
    ``[0, w) x [0, h)`` (when the size is given) are dropped.
    """
    px = np.asarray(px, dtype=np.float64)
    py = np.asarray(py, dtype=np.float64)
    n = len(px)
    mask = np.zeros(n, dtype=bool)
    if n == 0:
        return mask
    ix = np.floor(px)
    iy = np.floor(py)
    good = np.isfinite(ix) & np.isfinite(iy)
    if w_px is not None:
        good &= (ix >= 0) & (ix < np.ceil(w_px))
    if h_px is not None:
        good &= (iy >= 0) & (iy < np.ceil(h_px))
    idx = np.flatnonzero(good)
    if len(idx) == 0:
        return mask
    ix = ix[idx].astype(np.int64)
    iy = iy[idx].astype(np.int64)
    if w_px is None or h_px is None:
        ix -= ix.min()
        iy -= iy.min()
        width = int(ix.max()) + 1
        height = int(iy.max()) + 1
    else:
        width = int(np.ceil(w_px))
        height = int(np.ceil(h_px))
    key = ix * height + iy
    first = _first_per_key(key, width * height)
    mask[idx[first]] = True
    return mask


# ---------------------------------------------------------------- hit tests


def _candidates(ts, view: View, t0: float, t1: float) -> np.ndarray:
    k0, k1 = frame_window(ts.times, t0, t1)
    if k1 <= k0:
        return np.zeros(0, dtype=np.int64)
    return np.asarray(ts.rows_in_frames(k0, k1), dtype=np.int64)


def nearest(
    ts,
    view: View,
    x_px: float,
    y_px: float,
    r_px: float = PICK_PX,
    visible_ids=None,
    hidden_ids=None,
) -> tuple[int, list]:
    """The assigned row nearest the pointer within `r_px`, and every id near.

    Returns ``(row, ids)``: ``row`` is -1 when nothing is in range; ``ids``
    lists each distinct id with a detection inside the radius, nearest first
    (that is the order ``Tab`` cycles through).  `visible_ids`, when given,
    limits the search to those ids (an isolated view); `hidden_ids` excludes.
    """
    if ts is None or len(ts.times) == 0:
        return -1, []
    t0, _ = view.to_data(x_px - r_px, 0.0)
    t1, _ = view.to_data(x_px + r_px, 0.0)
    rows = assigned(ts, _candidates(ts, view, t0, t1))
    rows = filter_ids(ts, rows, visible_ids, hidden_ids)
    if len(rows) == 0:
        return -1, []
    px, py = view.to_px(ts.times[ts.idx[rows]], ts.fund[rows])
    d2 = (px - x_px) ** 2 + (py - y_px) ** 2
    inside = d2 <= r_px * r_px
    if not inside.any():
        return -1, []
    rows, d2 = rows[inside], d2[inside]
    order = np.argsort(d2, kind="stable")
    ids = ts.ident[rows[order]]
    _u, first = np.unique(ids, return_index=True)
    near = [float(ids[i]) for i in sorted(first)]
    return int(rows[order[0]]), near


def nearest_of_id(ts, view: View, x_px: float, y_px: float, ident: float) -> int:
    """The row of track `ident` nearest the pointer (no radius), or -1."""
    rows = ts.rows_of(ident)
    if len(rows) == 0:
        return -1
    px, py = view.to_px(ts.times[ts.idx[rows]], ts.fund[rows])
    return int(rows[np.argmin((px - x_px) ** 2 + (py - y_px) ** 2)])


def segment_distance(px, py, p0, p1) -> np.ndarray:
    """Distance of each point to the segment ``p0 -> p1``, in pixels."""
    px = np.asarray(px, dtype=np.float64)
    py = np.asarray(py, dtype=np.float64)
    ax, ay = float(p0[0]), float(p0[1])
    bx, by = float(p1[0]), float(p1[1])
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy
    if length2 <= 0:
        return np.hypot(px - ax, py - ay)
    u = np.clip(((px - ax) * dx + (py - ay) * dy) / length2, 0.0, 1.0)
    return np.hypot(px - (ax + u * dx), py - (ay + u * dy))


def brush_segment(
    ts,
    view: View,
    p0,
    p1,
    r_px: float,
    ids=None,
    *,
    include_assigned: bool = True,
    include_unassigned: bool = False,
    hidden_ids=None,
) -> np.ndarray:
    """Rows within `r_px` of the segment ``p0 -> p1`` (pixel coordinates).

    Candidates are the rows in the frames the segment spans, plus the radius
    each side, so a pointer move costs in proportion to the points near it
    and never to the stroke's length.  `ids` limits the hit to those ids (a
    sticky brush); `hidden_ids` excludes.
    """
    if ts is None or len(ts.times) == 0:
        return np.zeros(0, dtype=np.int64)
    xa = min(float(p0[0]), float(p1[0])) - r_px
    xb = max(float(p0[0]), float(p1[0])) + r_px
    t0, _ = view.to_data(xa, 0.0)
    t1, _ = view.to_data(xb, 0.0)
    rows = _candidates(ts, view, t0, t1)
    if len(rows) == 0:
        return rows
    nan = np.isnan(ts.ident[rows])
    keep = np.zeros(len(rows), dtype=bool)
    if include_assigned:
        keep |= ~nan
    if include_unassigned and ids is None:
        keep |= nan
    rows = rows[keep]
    if ids is not None or hidden_ids:
        unassigned_rows = rows[np.isnan(ts.ident[rows])]
        rows = np.concatenate(
            (
                filter_ids(ts, rows[np.isfinite(ts.ident[rows])], ids, hidden_ids),
                unassigned_rows if ids is None else unassigned_rows[:0],
            )
        )
    if len(rows) == 0:
        return rows
    px, py = view.to_px(ts.times[ts.idx[rows]], ts.fund[rows])
    d = segment_distance(px, py, p0, p1)
    return np.sort(rows[d <= r_px])


#: How far beyond a cut line's ends `line_crossings` looks for track
#: segments when lines are never broken at gaps.
CROSS_PAD_FRAMES = 64


class Crossing(NamedTuple):
    """Where a straight cut line crosses a track.

    ``t`` is the cut time: the midpoint between the two detections of the
    track either side of the crossing, so ``plan_cut(id, t)`` cuts exactly
    between them.  ``x, y`` is the crossing in data coordinates, for the
    preview's ✕.
    """

    id: float
    t: float
    x: float
    y: float


def line_crossings(ts, view: View, p0, p1, gap_frames: float = np.inf) -> list:
    """Every crossing of the segment ``p0 -> p1`` (pixels) with a track.

    For each visible track, consecutive detections form segments; the sign
    of the cross product of each segment's ends against the line changes
    where it crosses, and vice versa.  Segments across a gap longer than
    `gap_frames` are not lines on screen and are not crossed.  Sorted by id,
    then time.
    """
    if ts is None or len(ts.times) == 0:
        return []
    xa = min(float(p0[0]), float(p1[0]))
    xb = max(float(p0[0]), float(p1[0]))
    t0, _ = view.to_data(xa, 0.0)
    t1, _ = view.to_data(xb, 0.0)
    k0, k1 = frame_window(ts.times, t0, t1)
    # a segment shorter than the gap limit has both ends within that many
    # frames of the line; longer ones are not drawn and so not crossed.  With
    # no limit the search is widened by `CROSS_PAD_FRAMES`, which misses only
    # a segment bridging a longer dropout -- not something a reader sees.
    pad = int(np.ceil(gap_frames)) + 1 if np.isfinite(gap_frames) else CROSS_PAD_FRAMES
    k0 = max(0, k0 - pad)
    k1 = min(len(ts.times), k1 + pad)
    rows = assigned(ts, np.asarray(ts.rows_in_frames(k0, k1), dtype=np.int64))
    if len(rows) < 2:
        return []
    ordered = track_order(ts, rows)
    brk = track_breaks(ts, ordered, gap_frames)
    px, py = view.to_px(ts.times[ts.idx[ordered]], ts.fund[ordered])
    ax, ay, bx, by = px[:-1], py[:-1], px[1:], py[1:]
    keep = ~brk
    cx, cy = float(p0[0]), float(p0[1])
    dx, dy = float(p1[0]) - cx, float(p1[1]) - cy

    def side(x, y):
        return dx * (y - cy) - dy * (x - cx)

    s_a = side(ax, ay)
    s_b = side(bx, by)
    ex, ey = bx - ax, by - ay
    s_c = ex * (cy - ay) - ey * (cx - ax)
    s_d = ex * (cy + dy - ay) - ey * (cx + dx - ax)
    hit = keep & (s_a * s_b <= 0) & (s_c * s_d <= 0) & ((s_a != 0) | (s_b != 0))
    out = []
    for i in np.flatnonzero(hit):
        denom = s_a[i] - s_b[i]
        u = 0.5 if denom == 0 else s_a[i] / denom
        ra, rb = ordered[i], ordered[i + 1]
        ta, tb = ts.times[ts.idx[ra]], ts.times[ts.idx[rb]]
        fa, fb = ts.fund[ra], ts.fund[rb]
        out.append(
            Crossing(
                float(ts.ident[ra]),
                float(0.5 * (ta + tb)),
                float(ta + u * (tb - ta)),
                float(fa + u * (fb - fa)),
            )
        )
    # one cut per track per gap between two detections
    seen = set()
    unique = []
    for c in sorted(out, key=lambda c: (c.id, c.t)):
        if (c.id, c.t) in seen:
            continue
        seen.add((c.id, c.t))
        unique.append(c)
    return unique


def cut_time(ts, ident: float, t: float) -> Optional[float]:
    """The midpoint between the two detections of `ident` around `t`.

    None when `t` is before the track's first or after its last detection
    (a cut there would leave one side empty).
    """
    rows = ts.rows_of(ident)
    if len(rows) < 2:
        return None
    times = ts.times[ts.idx[rows]]
    i = int(np.searchsorted(times, t, side="right"))
    if i <= 0 or i >= len(times):
        return None
    return float(0.5 * (times[i - 1] + times[i]))


def track_ends(ts, ident: float) -> Optional[tuple]:
    """``((t, f) first, (t, f) last)`` of a track, or None when empty."""
    rows = ts.rows_of(ident)
    if len(rows) == 0:
        return None
    a, b = rows[0], rows[-1]
    return (
        (float(ts.times[ts.idx[a]]), float(ts.fund[a])),
        (float(ts.times[ts.idx[b]]), float(ts.fund[b])),
    )


def nearest_ends(ts, a: float, b: float) -> Optional[tuple]:
    """The closest pair of ends of tracks `a` and `b`, as two ``(t, f)``.

    The merge connector is drawn between them: anchor's end nearest the
    hovered track, to the hovered track's end nearest the anchor.
    """
    ea, eb = track_ends(ts, a), track_ends(ts, b)
    if ea is None or eb is None:
        return None
    best = None
    for pa in ea:
        for pb in eb:
            d = abs(pa[0] - pb[0])
            if best is None or d < best[0]:
                best = (d, pa, pb)
    return best[1], best[2]


def rows_in_view(ts, rows: np.ndarray, view: View, pad_frames: int = 1):
    """The subset of `rows` whose frame is in the view (plus padding)."""
    rows = np.asarray(rows, dtype=np.int64)
    if len(rows) == 0:
        return rows
    k0, k1 = view_frames(ts, view, pad_frames)
    k = ts.idx[rows]
    return rows[(k >= k0) & (k < k1)]


def track_in_view(ts, ident: float, view: View, pad_frames: int = 1) -> np.ndarray:
    """Rows of one track inside the view, by `searchsorted` on its frames."""
    rows = ts.rows_of(ident)
    if len(rows) == 0:
        return rows
    k0, k1 = view_frames(ts, view, pad_frames)
    k = ts.idx[rows]
    a = int(np.searchsorted(k, k0, side="left"))
    b = int(np.searchsorted(k, k1, side="left"))
    return rows[a:b]


# ------------------------------------------------------------- scene state


@dataclass
class Hover:
    """What the pointer is over: a row, its id, and every id within reach."""

    row: int
    id: float
    candidates: list
    cycle: int = 0
    t: float = 0.0
    f: float = 0.0


@dataclass
class Marks:
    """Preview decorations a tool asks the overlays to draw.

    All in data coordinates, all optional; the tools fill them, the
    overlays draw them, nothing else reads them.
    """

    #: ``[(rows, colour)]``: rows redrawn in a colour (a track recoloured to
    #: the anchor's, the part a cut gives the new id, a selection in the
    #: assign target's colour).  colour is a hex string or ``("slot", n)``.
    recolour: list = field(default_factory=list)
    #: rows the edit would unassign because of a conflict: red ✕
    drop_rows: np.ndarray = field(default_factory=lambda: np.zeros(0, np.int64))
    #: rows an erase stroke has captured: hollow dim rings
    ring_rows: np.ndarray = field(default_factory=lambda: np.zeros(0, np.int64))
    #: ``(t0, f0, t1, f1)``: the dashed merge connector
    connector: Optional[tuple] = None
    #: ``(t, f)``: a cut marker, drawn ±`CUT_MARK_PX` around f
    cut_marker: Optional[tuple] = None
    #: ``[(t, f)]``: crossings of a cut line, drawn as ✕
    crosses: list = field(default_factory=list)
    #: Add tool: candidate frequencies ``(t, f, skipped, pending)``
    add_t: np.ndarray = field(default_factory=lambda: np.zeros(0))
    add_f: np.ndarray = field(default_factory=lambda: np.zeros(0))
    add_skip: np.ndarray = field(default_factory=lambda: np.zeros(0, bool))
    add_pending: bool = False
    add_colour: Optional[object] = None
    #: the merge anchor or assign target: outlined
    outline_id: Optional[float] = None
    #: ``(t0, t1, f0, f1)``: a span to outline (history hover, an issue)
    span: Optional[tuple] = None


@dataclass
class SceneState:
    """Shared by every lane's overlay and by the tools (design 7.3).

    `revision` is bumped by `touch` on any change, which is what the
    overlays compare before redrawing their preview layers.  The base layer
    (the tracks) is keyed on the model's revision and the display options
    instead, so a hover never rebuilds it.
    """

    ts: object = None
    snippet: object = None
    selection: np.ndarray = field(default_factory=lambda: np.zeros(0, np.int64))
    hidden_ids: frozenset = frozenset()
    isolate: bool = False
    show_unassigned: bool = True
    point_px: int = 3
    gap_break_s: float = 0.5
    show_ids: bool = True
    #: ids that look like a harmonic of another id, with the label suffix
    #: that says so (``{57.0: "×2 of 12"}``); labelled whatever `show_ids`
    #: says, while the finding stands (design 5.13)
    harmonic_marks: dict = field(default_factory=dict)
    #: veil the spectrogram under the tracks (drawing only, not geometry)
    dim_spec: bool = False
    #: the tracking range ``(start, stop)`` in seconds (stop None: to the
    #: end), or None; drawn as two lines with the time outside it dimmed
    track_range: Optional[tuple] = None
    hover: Optional[Hover] = None
    preview: object = None
    stroke_rows: np.ndarray = field(default_factory=lambda: np.zeros(0, np.int64))
    stroke_mode: str = ""
    marks: Marks = field(default_factory=Marks)
    #: ``(t0, t1, f0, f1)`` outlined independently of the tool's marks: the
    #: hovered history entry, the current issue
    span_outline: Optional[tuple] = None
    flash_rows: np.ndarray = field(default_factory=lambda: np.zeros(0, np.int64))
    #: id of the tool whose preview colour applies to `stroke_rows`
    tool_colour: Optional[str] = None
    unassigned_auto_limit: int = 200_000
    unassigned_forced: Optional[bool] = None
    revision: int = 0
    #: bumped when anything the *base* layer depends on changes
    display_revision: int = 0
    selection_revision: int = 0

    def touch(self) -> None:
        self.revision += 1

    def touch_display(self) -> None:
        self.display_revision += 1
        self.revision += 1

    def set_selection(self, rows) -> None:
        rows = np.unique(np.asarray(rows, dtype=np.int64))
        self.selection = rows
        self.selection_revision += 1
        self.revision += 1

    def selected_ids(self) -> np.ndarray:
        ts = self.ts
        if ts is None or len(self.selection) == 0:
            return np.zeros(0)
        ids = ts.ident[self.selection]
        return np.unique(ids[np.isfinite(ids)])

    def visible_ids(self):
        """Ids allowed on screen: None for all, else the isolated set."""
        if self.isolate:
            return self.selected_ids()
        return None

    def display_key(self) -> tuple:
        return (
            self.isolate,
            self.selection_revision if self.isolate else -1,
            tuple(sorted(self.hidden_ids)),
            self.show_unassigned,
            self.unassigned_forced,
            int(self.point_px),
            round(float(self.gap_break_s), 6),
            bool(self.show_ids),
            tuple(sorted(self.harmonic_marks.items())),
        )


# ------------------------------------------------------------- the cache


@dataclass
class Geometry:
    """Everything the base layer of one lane draws, in data coordinates."""

    slots: dict  # slot -> (x, y)
    points: dict  # slot -> (x, y), thinned
    unassigned: tuple  # (x, y), thinned
    unassigned_hidden: bool  # hidden by the auto limit
    labels: list  # [(id, t, f)]
    n_rows: int  # assigned rows in view
    n_unassigned: int
    n_vertices: int
    n_ids: int
    exact: bool  # every detection is a vertex


MAX_ID_LABELS = 40


class RenderCache:
    """Base-layer geometry, computed once per view and shared by lanes.

    Keyed by ``(track set, model revision, snippet, view, display
    options)``.  Lanes share the time axis and, usually, the frequency range,
    so four lanes cost one computation.  A lane zoomed differently in
    frequency gets its own entry; a few are kept.

    After an edit, `note_change` records which ids changed; the next `get`
    at the new revision and an unchanged view recomputes only the colour
    slots of those ids and reuses the others (design 6.2).
    """

    MAX_ENTRIES = 6

    def __init__(self) -> None:
        self._entries: dict = {}
        self._order: list = []
        self._codes = None
        self._codes_key = None
        self._changes: dict = {}  # revision -> (ids, unassigned)
        self.hits = 0
        self.misses = 0
        self.partial = 0

    def clear(self) -> None:
        self._entries.clear()
        self._order.clear()
        self._codes = None
        self._codes_key = None
        self._changes.clear()

    def note_change(self, change) -> None:
        """Remember what one model step changed, for a partial recompute."""
        if change is None:
            return
        ids = np.asarray(getattr(change, "ids", ()), dtype=np.float64)
        self._changes[int(change.revision)] = (
            ids,
            bool(getattr(change, "unassigned", True)),
            int(getattr(change, "appended", 0)),
        )
        if len(self._changes) > 64:
            for key in sorted(self._changes)[:-64]:
                del self._changes[key]

    def codes(self, ts) -> IdCodes:
        key = (id(ts), getattr(ts, "revision", 0))
        if self._codes_key != key:
            self._codes = IdCodes(ts.ids())
            self._codes_key = key
        return self._codes

    def get(self, ts, scene: SceneState, view: View) -> Geometry:
        base = (id(ts), view.key(), scene.display_key())
        rev = getattr(ts, "revision", 0)
        entry = self._entries.get(base)
        if entry is not None and entry[0] == rev:
            self.hits += 1
            return entry[1]
        geometry = None
        if entry is not None and entry[0] < rev:
            geometry = self._partial(ts, scene, view, entry[0], rev, entry[1])
        if geometry is None:
            self.misses += 1
            geometry = compute_geometry(ts, scene, view, self.codes(ts))
        else:
            self.partial += 1
        self._entries[base] = (rev, geometry)
        if base in self._order:
            self._order.remove(base)
        self._order.append(base)
        while len(self._order) > self.MAX_ENTRIES:
            self._entries.pop(self._order.pop(0), None)
        return geometry

    def _partial(self, ts, scene, view, old_rev, rev, old: Geometry):
        changed = []
        unassigned_changed = False
        for r in range(old_rev + 1, rev + 1):
            got = self._changes.get(r)
            if got is None or got[2] != 0:
                return None
            changed.append(got[0])
            unassigned_changed |= got[1]
        if not changed:
            return None
        ids = np.unique(np.concatenate(changed)) if changed else np.zeros(0)
        slots = set(int(s) for s in slot_of(ids))
        return compute_geometry(
            ts,
            scene,
            view,
            self.codes(ts),
            only_slots=slots,
            previous=old,
            unassigned_changed=unassigned_changed,
        )


def visible_rows(ts, view: View, pad_frames: int = 1) -> np.ndarray:
    k0, k1 = view_frames(ts, view, pad_frames)
    if k1 <= k0:
        return np.zeros(0, dtype=np.int64)
    return np.asarray(ts.rows_in_frames(k0, k1), dtype=np.int64)


def compute_geometry(
    ts,
    scene: SceneState,
    view: View,
    codes: Optional[IdCodes] = None,
    only_slots: Optional[set] = None,
    previous: Optional[Geometry] = None,
    unassigned_changed: bool = True,
) -> Geometry:
    """The base layer for one view (see `RenderCache`)."""
    empty = Geometry({}, {}, (np.zeros(0), np.zeros(0)), False, [], 0, 0, 0, 0, True)
    if ts is None or len(ts.times) == 0 or ts.n == 0:
        return empty
    rows = visible_rows(ts, view)
    ident = ts.ident[rows]
    nan = np.isnan(ident)
    arows = rows[~nan]
    urows = rows[nan]
    allowed = scene.visible_ids()
    if allowed is not None:
        arows = filter_ids(ts, arows, allowed)
    if scene.hidden_ids:
        arows = filter_ids(ts, arows, None, scene.hidden_ids)
    gap = gap_frames_for(ts.times, scene.gap_break_s)
    exact = (
        bin_size(*view_frames(ts, view), view.w_px) == 1
        and len(arows) <= MAX_EXACT_ROWS
    )
    if previous is not None and previous.exact != exact:
        # an edit moved the visible row count across MAX_EXACT_ROWS: the
        # untouched slots were drawn the other way, so redraw them all
        only_slots = None

    if only_slots is not None and previous is not None:
        slots_of_rows = slot_of(ts.ident[arows])
        sub = arows[np.isin(slots_of_rows, list(only_slots))]
        fresh = polylines(ts, sub, view, gap, codes=codes, assigned_only=True)
        lines = {k: v for k, v in previous.slots.items() if k not in only_slots}
        lines.update(fresh)
    else:
        lines = polylines(ts, arows, view, gap, codes=codes, assigned_only=True)

    points: dict = {}
    if scene.point_px > 0 and exact and len(arows):
        if only_slots is not None and previous is not None:
            points = {k: v for k, v in previous.points.items() if k not in only_slots}
            prow = arows[np.isin(slot_of(ts.ident[arows]), list(only_slots))]
        else:
            prow = arows
        if len(prow):
            t = ts.times[ts.idx[prow]]
            f = ts.fund[prow]
            px, py = view.to_px(t, f)
            keep = thin_points(px, py, view.w_px + 1, view.h_px + 1) | (
                (px < 0) | (px > view.w_px)
            )
            # points slightly outside the lane horizontally are kept so the
            # padding frame draws; thin_points dropped them
            slots = slot_of(ts.ident[prow])
            for s in np.unique(slots[keep]):
                m = keep & (slots == s)
                points[int(s)] = (t[m], f[m])

    show_u = scene.show_unassigned
    hidden_by_limit = False
    if scene.unassigned_forced is not None:
        show_u = scene.unassigned_forced
    elif show_u and len(urows) > scene.unassigned_auto_limit:
        show_u = False
        hidden_by_limit = True
    if not show_u or len(urows) == 0:
        unassigned_xy = (np.zeros(0), np.zeros(0))
    elif previous is not None and not unassigned_changed:
        unassigned_xy = previous.unassigned
    else:
        t = ts.times[ts.idx[urows]]
        f = ts.fund[urows]
        px, py = view.to_px(t, f)
        keep = thin_points(px, py, view.w_px + 1, view.h_px + 1)
        unassigned_xy = (t[keep], f[keep])

    labels = []
    n_ids = 0
    if len(arows):
        ids_here = ids_in_frames(ts, arows, view, allowed, scene.hidden_ids)
        n_ids = len(ids_here)
        marked = scene.harmonic_marks
        if scene.show_ids and n_ids <= MAX_ID_LABELS:
            label_ids = ids_here
        else:
            label_ids = [i for i in ids_here if float(i) in marked]
        if len(label_ids):
            for i in label_ids:
                r = track_in_view(ts, i, view, pad_frames=0)
                r = r[(ts.fund[r] >= view.y0) & (ts.fund[r] <= view.y1)]
                if len(r) == 0:
                    continue
                labels.append(
                    (float(i), float(ts.times[ts.idx[r[0]]]), float(ts.fund[r[0]]))
                )
    n_vertices = sum(len(x) for x, _y in lines.values())
    return Geometry(
        lines,
        points,
        unassigned_xy,
        hidden_by_limit,
        labels,
        int(len(arows)),
        int(len(urows)),
        int(n_vertices),
        int(n_ids),
        bool(exact),
    )


def ids_in_frames(ts, rows, view: View, allowed=None, hidden=None) -> np.ndarray:
    """Ids with a detection in the view's frames.

    From the model's per-id statistics when it has them (a 2,000-entry
    comparison instead of a pass over a million rows); a track whose first
    and last detection bracket the view but which has none inside it is
    rare and only costs a label.
    """
    stats = getattr(ts, "stats", None)
    if stats is None or len(rows) <= MAX_EXACT_ROWS:
        return ident_set(ts, rows)
    st = stats()
    k0, k1 = view_frames(ts, view, 0)
    ids = st["id"][(st["k_first"] < k1) & (st["k_last"] >= k0)]
    if allowed is not None:
        ids = ids[np.isin(ids, np.asarray(allowed, dtype=np.float64))]
    if hidden:
        ids = ids[~np.isin(ids, np.asarray(list(hidden), dtype=np.float64))]
    return ids


def ident_set(ts, rows: np.ndarray) -> np.ndarray:
    """Distinct ids among `rows`, sorted; via a bincount when ids are small."""
    ids = ts.ident[rows]
    ids = ids[np.isfinite(ids)]
    if len(ids) == 0:
        return np.zeros(0)
    if len(ids) > 50_000 and ids.min() >= 0 and ids.max() < 10_000_000:
        counts = np.bincount(ids.astype(np.int64))
        return np.flatnonzero(counts).astype(np.float64)
    return np.unique(ids)


def with_view_size(view: View, w_px: float, h_px: float) -> View:
    return replace(view, w_px=float(w_px), h_px=float(h_px))


__all__ = [
    "MAX_EXACT_ROWS",
    "N_SLOTS",
    "PICK_PX",
    "Crossing",
    "Geometry",
    "Hover",
    "Marks",
    "RenderCache",
    "SceneState",
    "View",
    "brush_segment",
    "compute_geometry",
    "cut_time",
    "line_crossings",
    "nearest",
    "nearest_ends",
    "polylines",
    "slot_of",
    "thin_points",
    "track_line",
]
