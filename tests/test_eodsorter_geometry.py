"""The wavetracker plugin's geometry: polylines, thinning and hit tests.

Pure numpy, no window.  Every hit test is checked against a brute-force
version on random data at several zoom levels, because these are the
functions every pointer event runs and an off-by-one here is a click on the
wrong fish.

The track set is a stand-in with only the read side `geometry` uses, so this
file does not depend on `model` (and stays fast).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from audian_plugins.eodsorter import geometry as G  # noqa: E402


class Stub:
    """The read side of a `TrackSet`, from plain arrays."""

    def __init__(self, fund, idx, ident, times):
        self.fund = np.asarray(fund, dtype=np.float64)
        self.idx = np.asarray(idx, dtype=np.int64)
        self.ident = np.asarray(ident, dtype=np.float64)
        self.times = np.asarray(times, dtype=np.float64)
        self.revision = 0
        self._index()

    @property
    def n(self):
        return len(self.fund)

    def _index(self):
        self.by_frame = np.lexsort((np.arange(self.n), self.idx))
        self._frames = self.idx[self.by_frame]

    def rows_in_frames(self, k0, k1):
        a = np.searchsorted(self._frames, k0, side="left")
        b = np.searchsorted(self._frames, k1, side="left")
        return self.by_frame[a:b]

    def rows_of(self, ident):
        rows = np.flatnonzero(self.ident == ident)
        return rows[np.argsort(self.idx[rows], kind="stable")]

    def ids(self):
        ids = self.ident[np.isfinite(self.ident)]
        return np.unique(ids)

    def relabel(self, rows, new):
        self.ident = self.ident.copy()
        self.ident[rows] = new
        self.revision += 1


def random_set(seed=0, n_tracks=30, n_frames=2000, n_noise=500, step=0.1):
    rng = np.random.default_rng(seed)
    fund, idx, ident = [], [], []
    for i in range(n_tracks):
        k0 = int(rng.integers(0, n_frames - 50))
        k1 = int(rng.integers(k0 + 2, min(n_frames, k0 + 800)))
        frames = np.arange(k0, k1)
        frames = frames[rng.random(len(frames)) > 0.1]  # dropouts
        f = 500 + 600 * rng.random() + np.cumsum(rng.normal(0, 0.3, len(frames)))
        fund.append(f)
        idx.append(frames)
        ident.append(np.full(len(frames), float(i * 3)))  # sparse ids, id 0 too
    fund.append(500 + 600 * rng.random(n_noise))
    idx.append(rng.integers(0, n_frames, n_noise))
    ident.append(np.full(n_noise, np.nan))
    fund = np.concatenate(fund)
    idx = np.concatenate(idx)
    ident = np.concatenate(ident)
    order = np.argsort(idx, kind="stable")
    times = 0.05 + step * np.arange(n_frames)
    return Stub(fund[order], idx[order], ident[order], times)


def views(ts):
    t_end = float(ts.times[-1])
    yield G.View(0.0, t_end, 400.0, 1200.0, 900.0, 300.0)  # all of it
    yield G.View(30.0, 60.0, 500.0, 1100.0, 900.0, 300.0)
    yield G.View(41.0, 43.0, 600.0, 900.0, 600.0, 200.0)  # very close


# ------------------------------------------------------------------- slots


def test_slot_of_uses_every_slot_and_is_stable():
    slots = G.slot_of(np.arange(10.0))
    assert sorted(slots.tolist()) == list(range(10))
    assert G.slot_of([12.0])[0] == G.slot_of([12.0])[0] == (12 * 7) % 10
    # consecutive ids land three slots apart
    assert abs(int(G.slot_of([5.0])[0]) - int(G.slot_of([4.0])[0])) % 10 in (3, 7)
    assert G.slot_of([np.nan])[0] == -1
    assert G.slot_of([0.0])[0] == 0, "id 0 is an id"


# --------------------------------------------------------------- polylines


def _pieces(x, y):
    """A NaN-broken line as a list of (x, y) pieces."""
    out, cur = [], []
    for a, b in zip(x, y):
        if np.isnan(a):
            if cur:
                out.append(cur)
            cur = []
        else:
            cur.append((a, b))
    if cur:
        out.append(cur)
    return out


def test_polylines_break_at_id_changes_and_gaps():
    times = np.arange(20) * 0.1
    # id 0 at frames 0-4 and 10-12 (gap of 6 frames), id 10 at 5-8: both slot 0
    idx = np.array([0, 1, 2, 3, 4, 10, 11, 12, 5, 6, 7, 8])
    ident = np.array([0.0] * 8 + [10.0] * 4)
    fund = np.arange(12) * 1.0 + 500
    ts = Stub(fund, idx, ident, times)
    view = G.View(0.0, 2.0, 0.0, 1000.0, 1000.0, 100.0)
    lines = G.polylines(ts, np.arange(12), view, gap_frames=3)
    assert set(lines) == {0}
    pieces = _pieces(*lines[0])
    lengths = sorted(len(p) for p in pieces)
    assert lengths == [3, 4, 5], "id 0 split at its gap, id 10 separate"
    # without a gap limit, id 0 is one piece
    lines = G.polylines(ts, np.arange(12), view, gap_frames=np.inf)
    assert sorted(len(p) for p in _pieces(*lines[0])) == [4, 8]


def test_polylines_ignore_unassigned_rows():
    ts = Stub([1.0, 2.0, 3.0], [0, 1, 2], [np.nan, np.nan, np.nan], [0, 1, 2])
    assert G.polylines(ts, np.arange(3), G.View(0, 2, 0, 5, 100, 100), 5) == {}


def test_every_detection_is_a_vertex_when_zoomed_in():
    ts = random_set(1)
    view = G.View(40.0, 50.0, 0.0, 2000.0, 2000.0, 300.0)
    rows = G.visible_rows(ts, view)
    lines = G.polylines(ts, rows, view, gap_frames=np.inf)
    got = sorted(
        (round(a, 9), round(b, 9))
        for x, y in lines.values()
        for a, b in zip(x, y)
        if not np.isnan(a)
    )
    arows = rows[np.isfinite(ts.ident[rows])]
    want = sorted(
        (round(a, 9), round(b, 9))
        for a, b in zip(ts.times[ts.idx[arows]], ts.fund[arows])
    )
    assert got == want


def test_binned_path_with_bins_of_one_frame_equals_the_exact_path():
    ts = random_set(2)
    rows = G.visible_rows(ts, G.View(0, 300, 0, 2000, 900, 300))
    rows = rows[np.isfinite(ts.ident[rows])]
    k = ts.idx[rows]
    exact = G.track_order(ts, rows)
    binned = G.binned_order(ts, rows, int(k.min()), int(k.max()) + 1, 1)
    assert np.array_equal(exact, binned)


def test_binned_path_keeps_the_same_vertex_set_as_the_exact_path_for_stride_1():
    """Force the binned branch (max_rows=0) while bins are one frame."""
    ts = random_set(3)
    view = G.View(40.0, 60.0, 0.0, 2000.0, 4000.0, 300.0)
    rows = G.visible_rows(ts, view)
    a = G.polylines(ts, rows, view, gap_frames=5)
    b = G.polylines(ts, rows, view, gap_frames=5, max_rows=0)
    assert set(a) == set(b)
    for s in a:
        assert np.array_equal(a[s][0], b[s][0], equal_nan=True)
        assert np.array_equal(a[s][1], b[s][1], equal_nan=True)


def test_zoomed_out_vertices_are_bounded_by_the_lane_width():
    ts = random_set(4, n_tracks=40, n_frames=20000)
    view = G.View(0.0, float(ts.times[-1]), 0.0, 2000.0, 500.0, 300.0)
    rows = G.visible_rows(ts, view)
    lines = G.polylines(ts, rows, view, gap_frames=5)
    vertices = sum(int(np.isfinite(x).sum()) for x, _ in lines.values())
    assert vertices <= 40 * (500 * G.PIXEL_DENSITY + 2)
    # and every track still has at least one vertex
    xs = np.concatenate([x for x, _ in lines.values()])
    assert vertices > 0 and np.isfinite(xs).any()
    ids_drawn = set()
    for x, y in lines.values():
        for a, b in zip(x, y):
            if np.isfinite(a):
                hit = np.flatnonzero(
                    (ts.times[ts.idx] == a) & (ts.fund == b) & np.isfinite(ts.ident)
                )
                ids_drawn.add(float(ts.ident[hit[0]]))
    assert ids_drawn == set(ts.ids().tolist())


# ------------------------------------------------------------------ points


def test_thin_points_keeps_exactly_one_point_per_cell():
    rng = np.random.default_rng(5)
    px = rng.random(5000) * 50
    py = rng.random(5000) * 20
    mask = G.thin_points(px, py, 50, 20)
    cells = np.floor(px).astype(int) * 100 + np.floor(py).astype(int)
    assert mask.sum() == len(np.unique(cells))
    assert len(np.unique(cells[mask])) == mask.sum()
    # the kept point of each cell is the first in array order
    for c in np.unique(cells)[:50]:
        assert mask[np.flatnonzero(cells == c)[0]]


def test_thin_points_drops_points_off_the_lane():
    mask = G.thin_points([-1.0, 5.0, 50.0], [1.0, 1.0, 1.0], 10, 10)
    assert mask.tolist() == [False, True, False]


# -------------------------------------------------------------- hit tests


def _brute_nearest(ts, view, x, y, r):
    rows = np.flatnonzero(np.isfinite(ts.ident))
    px, py = view.to_px(ts.times[ts.idx[rows]], ts.fund[rows])
    d2 = (px - x) ** 2 + (py - y) ** 2
    inside = d2 <= r * r
    if not inside.any():
        return -1, set()
    best = rows[inside][np.argmin(d2[inside])]
    return int(best), set(ts.ident[rows[inside]].tolist())


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_nearest_matches_brute_force(seed):
    ts = random_set(seed)
    rng = np.random.default_rng(100 + seed)
    for view in views(ts):
        rows = G.visible_rows(ts, view)
        arows = rows[np.isfinite(ts.ident[rows])]
        # pointers on and near points, and at random
        px, py = view.to_px(ts.times[ts.idx[arows]], ts.fund[arows])
        pick = rng.choice(len(arows), size=min(30, len(arows)), replace=False)
        pointers = [(px[i] + rng.normal(0, 4), py[i] + rng.normal(0, 4)) for i in pick]
        pointers += [
            (rng.random() * view.w_px, rng.random() * view.h_px) for _ in range(20)
        ]
        for x, y in pointers:
            row, ids = G.nearest(ts, view, x, y, 10.0)
            want_row, want_ids = _brute_nearest(ts, view, x, y, 10.0)
            if want_row < 0:
                assert row == -1 and ids == []
                continue
            # ties are broken differently; compare distances
            dpx = view.to_px(
                ts.times[ts.idx[[row, want_row]]], ts.fund[[row, want_row]]
            )
            d = np.hypot(dpx[0] - x, dpx[1] - y)
            assert d[0] == pytest.approx(d[1])
            assert set(ids) == want_ids
            assert ids[0] == ts.ident[row], "nearest id first, for Tab"


def test_nearest_respects_visible_and_hidden_ids():
    ts = random_set(7)
    view = G.View(0, float(ts.times[-1]), 0, 2000, 800, 300)
    rows = np.flatnonzero(np.isfinite(ts.ident))
    r = rows[len(rows) // 2]
    x, y = view.to_px(ts.times[ts.idx[r]], ts.fund[r])
    row, ids = G.nearest(ts, view, float(x), float(y), 10.0)
    assert row >= 0
    first = ids[0]
    _row, ids2 = G.nearest(ts, view, float(x), float(y), 10.0, hidden_ids={first})
    assert first not in ids2
    _row, ids3 = G.nearest(ts, view, float(x), float(y), 10.0, visible_ids=[first])
    assert ids3 == [first]


def _brute_brush(ts, view, p0, p1, r, ids=None, unassigned=False):
    rows = np.arange(ts.n)
    nan = np.isnan(ts.ident)
    keep = ~nan | (unassigned & (ids is None))
    if ids is not None:
        keep &= np.isin(ts.ident, list(ids))
    rows = rows[keep]
    px, py = view.to_px(ts.times[ts.idx[rows]], ts.fund[rows])
    d = G.segment_distance(px, py, p0, p1)
    return set(rows[d <= r].tolist())


@pytest.mark.parametrize("seed", [0, 3])
def test_brush_segment_matches_brute_force(seed):
    ts = random_set(seed)
    rng = np.random.default_rng(200 + seed)
    for view in views(ts):
        for _ in range(25):
            p0 = (rng.random() * view.w_px, rng.random() * view.h_px)
            p1 = (p0[0] + rng.normal(0, 30), p0[1] + rng.normal(0, 30))
            r = float(rng.uniform(3, 40))
            got = set(G.brush_segment(ts, view, p0, p1, r).tolist())
            assert got == _brute_brush(ts, view, p0, p1, r)
            got = set(
                G.brush_segment(ts, view, p0, p1, r, include_unassigned=True).tolist()
            )
            assert got == _brute_brush(ts, view, p0, p1, r, unassigned=True)
            some = ts.ids()[:3]
            got = set(G.brush_segment(ts, view, p0, p1, r, ids=some).tolist())
            assert got == _brute_brush(ts, view, p0, p1, r, ids=some)


def _brute_crossings(ts, view, p0, p1, gap):
    out = set()
    for i in ts.ids():
        rows = ts.rows_of(i)
        px, py = view.to_px(ts.times[ts.idx[rows]], ts.fund[rows])
        for j in range(len(rows) - 1):
            if ts.idx[rows[j + 1]] - ts.idx[rows[j]] > gap:
                continue
            a, b = (px[j], py[j]), (px[j + 1], py[j + 1])

            def orient(p, q, r):
                return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

            o1, o2 = orient(p0, p1, a), orient(p0, p1, b)
            o3, o4 = orient(a, b, p0), orient(a, b, p1)
            if o1 * o2 <= 0 and o3 * o4 <= 0 and (o1 != 0 or o2 != 0):
                t = 0.5 * (ts.times[ts.idx[rows[j]]] + ts.times[ts.idx[rows[j + 1]]])
                out.add((float(i), round(float(t), 9)))
    return out


@pytest.mark.parametrize("seed", [0, 5])
def test_line_crossings_match_brute_force(seed):
    ts = random_set(seed, n_tracks=15, n_noise=0)
    rng = np.random.default_rng(300 + seed)
    for view in views(ts):
        for _ in range(15):
            x = rng.random() * view.w_px
            p0 = (x, rng.random() * view.h_px * 0.2)
            p1 = (x + rng.normal(0, 20), view.h_px * (0.8 + 0.2 * rng.random()))
            for gap in (1, 5):
                got = G.line_crossings(ts, view, p0, p1, gap_frames=gap)
                got = {(c.id, round(c.t, 9)) for c in got}
                assert got == _brute_crossings(ts, view, p0, p1, gap)


def test_line_crossings_skip_gaps():
    times = np.arange(10) * 1.0
    ts = Stub([500.0, 500.0, 500.0], [0, 1, 8], [4.0, 4.0, 4.0], times)
    view = G.View(0, 9, 0, 1000, 900, 100)
    x = float(view.to_px(4.5, 500)[0])
    p0, p1 = (x, 0.0), (x, 100.0)
    assert len(G.line_crossings(ts, view, p0, p1)) == 1
    assert G.line_crossings(ts, view, p0, p1, gap_frames=3) == []


def test_cut_time_is_between_two_detections():
    ts = Stub([1.0, 2.0, 3.0], [0, 2, 4], [7.0, 7.0, 7.0], np.arange(5) * 1.0)
    assert G.cut_time(ts, 7.0, 2.6) == pytest.approx(3.0)
    assert G.cut_time(ts, 7.0, -1.0) is None
    assert G.cut_time(ts, 7.0, 4.5) is None


# ------------------------------------------------------------------ cache


def test_render_cache_reuses_and_partially_recomputes():
    ts = random_set(8)
    scene = G.SceneState(ts=ts)
    view = G.View(0, float(ts.times[-1]), 0, 2000, 900, 300)
    cache = G.RenderCache()
    g1 = cache.get(ts, scene, view)
    assert cache.get(ts, scene, view) is g1 and cache.hits == 1
    # relabel one track and tell the cache what changed
    old = 3.0
    rows = ts.rows_of(old)
    ts.relabel(rows, 1000.0)

    class Change:
        revision = ts.revision
        ids = np.array([old, 1000.0])
        unassigned = False
        appended = 0

    cache.note_change(Change)
    g2 = cache.get(ts, scene, view)
    assert cache.partial == 1
    full = G.compute_geometry(ts, scene, view)
    assert set(g2.slots) == set(full.slots)
    for s in full.slots:
        assert np.array_equal(g2.slots[s][0], full.slots[s][0], equal_nan=True)
        assert np.array_equal(g2.slots[s][1], full.slots[s][1], equal_nan=True)


def test_unassigned_points_hide_above_the_limit_unless_forced():
    ts = random_set(9, n_noise=3000)
    view = G.View(0, float(ts.times[-1]), 0, 2000, 900, 300)
    scene = G.SceneState(ts=ts, unassigned_auto_limit=1000)
    g = G.compute_geometry(ts, scene, view)
    assert g.unassigned_hidden and len(g.unassigned[0]) == 0
    scene.unassigned_forced = True
    g = G.compute_geometry(ts, scene, view)
    assert len(g.unassigned[0]) > 0


def test_geometry_imports_no_qt():
    """The `sys.modules` probe `test_thread_boundary.py` uses."""
    src = Path(__file__).resolve().parent.parent / "src"
    code = (
        "import sys; sys.path.insert(0, %r); "
        "import audian_plugins.eodsorter.geometry; "
        "bad = [m for m in sys.modules if m.startswith(('PySide6', 'pyqtgraph', 'audian.'))]; "
        "print(bad); sys.exit(1 if bad else 0)" % str(src)
    )
    run = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert run.returncode == 0, run.stdout + run.stderr


def test_partial_recompute_redraws_everything_when_exactness_flips(monkeypatch):
    """Review 12: an edit that moves the visible row count across
    MAX_EXACT_ROWS must not leave slots drawn the other way."""
    ts = random_set(8)
    scene = G.SceneState(ts=ts, point_px=3)
    view = G.View(0, float(ts.times[200]), 0, 2000, 4000, 300)
    n = len(G.visible_rows(ts, view))
    monkeypatch.setattr(G, "MAX_EXACT_ROWS", 10)
    g1 = G.compute_geometry(ts, scene, view)
    assert not g1.exact and g1.points == {}
    # the edit took the view back under the limit: every slot needs points
    monkeypatch.setattr(G, "MAX_EXACT_ROWS", n + 5)
    g2 = G.compute_geometry(ts, scene, view, only_slots={0}, previous=g1)
    full = G.compute_geometry(ts, scene, view)
    assert g2.exact and len(full.points) > 1
    assert set(g2.points) == set(full.points)
    assert set(g2.slots) == set(full.slots)
