"""The wavetracker plugin's Qt half: panel, overlays, tools, keys.

One real `Audian` window for the module,
on a two-channel synthetic recording, with a wavetracker-format results
directory written by numpy (no wavetracker needed).  Each test opens the
Tracks tab, loads a fresh copy of the results and closes the tab again.

Gestures are driven through the tool methods (``press/move/release/click``)
with pixel positions computed from data coordinates, because that is what
the design promises tests can do (5.10); one test sends real mouse events
through the graphics view to prove the Qt path reaches the tools, and that a
middle drag still reaches audian's view box.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

RATE = 8000
NFFT = 2048
OVERLAP = 0.9
STEP = max(1, int(NFFT * (1 - OVERLAP)))
DURATION = 20.0


# ------------------------------------------------------------- test data


def fish(duration=DURATION):
    t = np.arange(0, duration, 0.01)
    return t, [
        600 + 50 * t / duration,  # these two cross
        650 - 50 * t / duration,
        760 + 6 * np.sin(2 * np.pi * t / 9),
        820 + 4 * np.sin(2 * np.pi * t / 13),
        905 + 3 * np.sin(2 * np.pi * t / 7),
    ]


def write_recording(path, duration=DURATION, channels=2):
    soundfile = pytest.importorskip("soundfile")
    n = int(RATE * duration)
    tt = np.arange(n) / RATE
    t, curves = fish(duration)
    rng = np.random.default_rng(1)
    sig = np.zeros((n, channels), np.float32)
    for i, f in enumerate(curves):
        phase = 2 * np.pi * np.cumsum(np.interp(tt, t, f)) / RATE
        for c in range(channels):
            sig[:, c] += 0.05 * (1 + 0.5 * np.sin(i + c)) * np.sin(phase)
    sig += 0.01 * rng.standard_normal(sig.shape).astype(np.float32)
    soundfile.write(path, sig, RATE)
    return n


def write_results(folder, n_samples, duration=DURATION, channels=2, noise=200, seed=2):
    """Five fish, each broken into fragments with their own ids."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    n_frames = (n_samples - NFFT) // STEP + 1
    times = (np.arange(n_frames) * STEP + NFFT / 2) / RATE
    t, curves = fish(duration)
    rng = np.random.default_rng(seed)
    fund, idx, ident = [], [], []
    next_id = 0
    for i, f in enumerate(curves):
        fk = np.interp(times, t, f) + rng.normal(0, 0.1, n_frames)
        frames = np.flatnonzero(rng.random(n_frames) > 0.05)
        cut = n_frames // 2 + 40 * (i - 2)
        for a, b in ((0, cut), (cut, n_frames)):
            m = (frames >= a) & (frames < b)
            fund.append(fk[frames[m]])
            idx.append(frames[m])
            ident.append(np.full(m.sum(), float(next_id)))
            next_id += 1
    fund.append(560 + 380 * rng.random(noise))
    idx.append(rng.integers(0, n_frames, noise))
    ident.append(np.full(noise, np.nan))
    fund, idx, ident = map(np.concatenate, (fund, idx, ident))
    o = np.argsort(idx, kind="stable")
    np.save(folder / "fund_v.npy", fund[o])
    np.save(folder / "idx_v.npy", idx[o].astype(np.int64))
    np.save(folder / "ident_v.npy", ident[o])
    np.save(folder / "sign_v.npy", rng.random((len(o), channels)).astype(np.float32))
    np.save(folder / "times.npy", times)
    meta = {
        "rate": RATE,
        "start": 0.0,
        "frame_step": STEP / RATE,
        "freq_resolution": RATE / NFFT,
        "low_threshold": 10.0,
        "high_threshold": 20.0,
        "config": {
            "spectrogram": {"nfft": NFFT, "overlap_frac": OVERLAP},
            "harmonic_groups": {"min_freq": 500.0, "max_freq": 1000.0},
            "tracking": {"freq_tolerance": 2.5, "max_dt": 10.0},
        },
    }
    (folder / "wavetracker.json").write_text(json.dumps(meta))
    return folder


# ------------------------------------------------------------- fixtures


def pump(seconds):
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QApplication

    end = time.monotonic() + seconds
    application = QApplication.instance()
    while time.monotonic() < end:
        application.processEvents()
        application.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        time.sleep(0.005)


@pytest.fixture(scope="module")
def world(app, tmp_path_factory):
    import audian.audian as audian_app
    from audian import theme
    from audian.plugins import Plugins

    tmp = tmp_path_factory.mktemp("eodsorter-ui")
    recording = tmp / "rec.wav"
    n = write_recording(recording)
    pristine = write_results(tmp / "pristine", n)
    theme.apply(app)
    plugins = Plugins()
    plugins.load_plugins()
    window = audian_app.Audian(
        [str(recording)], {}, plugins, [], 0, None, False, 0, None
    )
    window.resize(1400, 900)
    window.show()
    pump(1.5)
    view = window.browser()
    # Whether the side panel opens is a remembered setting, and an earlier
    # module (test_actioninventory's action sweep) can leave it hidden; the
    # plugin's tab lives there, and a hidden panel is never shown.
    view.set_side_panel(True)
    view.set_panels(traces=0, specs=1)
    pump(0.8)
    yield {"window": window, "browser": view, "pristine": pristine, "tmp": tmp, "n": n}
    window.close()
    window.setParent(None)
    window.deleteLater()
    pump(0.3)


LABEL = "Wavetracker"


@pytest.fixture
def panel(world, tmp_path):
    """The Tracks tab, open, on a fresh copy of the results, edit mode on."""
    browser = world["browser"]
    folder = tmp_path / "rec-wavetracker"
    shutil.copytree(world["pristine"], folder)
    assert browser.open_plugin_panel(LABEL)
    pump(0.3)
    p = browser.plugin_panels[LABEL]
    p.ask = lambda title, text, buttons: "Discard"
    if p.ts is not None:
        p.set_session(None)
    assert p.open_results(folder, ask=False)
    browser.set_ranges("f", 540.0, 960.0)
    browser.set_times(4.0, 8.0)
    pump(0.3)
    p.set_edit_mode(True)
    p.redraw_now()
    pump(0.05)
    yield p
    p.ask = lambda title, text, buttons: "Discard"
    browser.close_plugin_panel(LABEL)
    pump(0.2)


def lane(p, i=0):
    return p.surfaces[i]


def px(surface, t, f):
    x, y = surface.view().to_px(t, f)
    return float(x), float(y)


def point_of(p, ident, frac=0.5):
    """A detection of track `ident` inside the view: (t, f, row)."""
    ts = p.ts
    v = lane(p).view()
    rows = ts.rows_of(ident)
    t = ts.times[ts.idx[rows]]
    rows = rows[(t > v.x0 + 0.3) & (t < v.x1 - 0.3)]
    r = int(rows[int(len(rows) * frac)])
    return float(ts.times[ts.idx[r]]), float(ts.fund[r]), r


def ids_in_view(p, min_points=20):
    from audian_plugins.eodsorter import geometry as G

    ts = p.ts
    v = lane(p).view()
    return [i for i in ts.ids() if len(G.track_in_view(ts, i, v)) > min_points]


def hover(p, t, f, surface=None):
    surface = surface or lane(p)
    p.controller.hover_at(surface, px(surface, t, f))
    p.controller.flush()


def stroke(p, points, mods=None, release=True, surface=None):
    """Press, move through `points` (data coordinates), release."""
    surface = surface or lane(p)
    c = p.controller
    pts = [px(surface, t, f) for t, f in points]
    c.hover_at(surface, pts[0])
    c.flush()
    c.press(surface, pts[0], mods)
    for q in pts[1:]:
        c.move(surface, q, mods)
    if release:
        c.release(surface, pts[-1], mods)


def committed(p):
    return len(p.ts.history.entries)


# ------------------------------------------------------------ the plugin


def test_the_plugin_is_discovered_with_a_tip(world):
    from audian_plugins.eodsorter import audian_wavetracker_panel

    assert LABEL in world["browser"].plugin_labels()
    assert audian_wavetracker_panel.menu_path == ("Wavetracker",)
    assert "wavetracker" in audian_wavetracker_panel.menu_tip


def test_one_overlay_and_surface_per_lane_and_closing_removes_them(world, tmp_path):
    browser = world["browser"]
    lanes = browser.spectrogram_axes()
    before = [len(ax.items) for ax in lanes]
    children = [len(ax.getViewBox().childItems()) for ax in lanes]
    assert browser.open_plugin_panel(LABEL)
    pump(0.3)
    p = browser.plugin_panels[LABEL]
    p.ask = lambda *a: "Discard"
    folder = tmp_path / "r"
    shutil.copytree(world["pristine"], folder)
    p.open_results(folder, ask=False)
    pump(0.2)
    assert len(p.overlays) == len(lanes) == len(p.surfaces)
    assert all(len(ax.items) > b for ax, b in zip(lanes, before))
    browser.close_plugin_panel(LABEL)
    pump(0.3)
    assert [len(ax.items) for ax in lanes] == before
    assert [len(ax.getViewBox().childItems()) for ax in lanes] == children


def test_items_per_lane_do_not_grow_with_the_number_of_ids(panel):
    from audian_plugins.eodsorter import model as M

    counts = [o.item_count() for o in panel.overlays]
    ts = panel.ts
    # the same detections, every one its own id: thousands of ids
    many = np.where(np.isfinite(ts.ident), np.arange(ts.n, dtype=float), np.nan)
    big = M.TrackSet.from_arrays(ts.fund, ts.idx, many, ts.sign, ts.times, meta=ts.meta)
    panel.set_session(big)
    panel.redraw_now()
    assert len(big.ids()) > 3000
    assert [o.item_count() for o in panel.overlays] == counts


def test_the_tracks_are_drawn_with_bounded_vertices(panel):
    o = panel.overlays[0]
    g = o.geometry
    assert g is not None and g.n_rows > 0
    drawn = sum(len(c.xData) for c in o.curves if c.xData is not None)
    assert drawn > 0
    assert g.labels, "ids are labelled while few tracks are visible"


# ------------------------------------------------------------------ hover


def test_hover_highlights_the_track_and_shows_the_label_box(panel):
    ident = ids_in_view(panel)[0]
    t, f, _r = point_of(panel, ident)
    hover(panel, t, f)
    panel.redraw_now()
    h = panel.scene.hover
    assert h is not None and h.id == ident
    o = panel.overlays[0]
    assert o.hover.xData is not None and len(o.hover.xData) > 10
    assert len(o.halo.xData) == len(o.hover.xData)
    s = lane(panel)
    assert s.box.isVisible()
    lines = s.label_text()
    assert (
        lines[0].startswith(f"id {int(ident)}")
        and "Hz" in lines[0]
        and "pts" in lines[0]
    )
    assert "click" in lines[1]
    # the brush ring follows the pointer for a brush tool
    assert s.ring.isVisible()
    # away from every track: no hover
    hover(panel, t, f + 60)
    assert panel.scene.hover is None


def test_tab_cycles_overlapping_tracks(panel):
    ts = panel.ts
    # where the two crossing fish meet (t = 10 s at 625 Hz)
    panel.browser.set_times(9.0, 2.0)
    pump(0.2)
    best = None
    for i in ts.ids():
        rows = ts.rows_of(i)
        t = ts.times[ts.idx[rows]]
        m = np.abs(t - 10.0) < 0.05
        if m.any():
            best = (10.0, float(np.mean(ts.fund[rows[m]])))
            break
    hover(panel, *best)
    h = panel.scene.hover
    if h is None or len(h.candidates) < 2:
        pytest.skip("no overlap under the pointer in this synthetic set")
    first = h.id
    assert panel.controller.cycle_hover()
    assert panel.scene.hover.id != first
    assert "(Tab)" in panel.controller.label_lines()[0]


# ------------------------------------------------------------------ tools


def test_select_click_selects_a_track_and_empty_click_clears(panel):
    ident = ids_in_view(panel)[0]
    t, f, _r = point_of(panel, ident)
    hover(panel, t, f)
    panel.controller.click(lane(panel), px(lane(panel), t, f), None)
    assert np.array_equal(panel.scene.selection, np.sort(panel.ts.rows_of(ident)))
    assert "selected" in panel.selinfow.text()
    hover(panel, t, f + 80)
    panel.controller.click(lane(panel), px(lane(panel), t, f + 80), None)
    assert len(panel.scene.selection) == 0


def test_brush_select_replaces_adds_and_removes(panel):
    from audian_plugins.eodsorter.tools import Mods

    a, b = ids_in_view(panel)[:2]
    ta, fa, _ = point_of(panel, a, 0.3)
    stroke(panel, [(ta, fa), (ta + 0.5, fa)])
    sel1 = panel.scene.selection.copy()
    assert len(sel1) > 3
    tb, fb, _ = point_of(panel, b, 0.3)
    stroke(panel, [(tb, fb), (tb + 0.5, fb)], Mods(shift=True))
    assert len(panel.scene.selection) > len(sel1)
    stroke(panel, [(tb, fb), (tb + 0.5, fb)], Mods(ctrl=True))
    assert np.array_equal(panel.scene.selection, sel1)
    assert committed(panel) == 0, "selection is not an edit"


def test_erase_stroke_unassigns_points_with_one_history_entry(panel):
    ident = ids_in_view(panel)[0]
    panel.controller.set_tool("E")
    t, f, _ = point_of(panel, ident, 0.3)
    stroke(panel, [(t, f), (t + 0.3, f), (t + 0.6, f)], release=False)
    s = lane(panel)
    # live feedback mid-stroke: the painted path, the ring, captured rings
    assert s.stroke.isVisible() and s.stroke.path().elementCount() >= 3
    assert s.stroke.pen().widthF() == pytest.approx(2 * panel.controller.brush_px)
    assert s.ring.isVisible()
    captured = panel.scene.stroke_rows.copy()
    assert len(captured) > 3
    assert np.array_equal(panel.scene.marks.ring_rows, captured)
    panel.redraw_now()
    assert len(panel.overlays[0].rings.data) == len(captured)
    assert "stuck to" in panel.hintw.text()
    panel.controller.release(s, px(s, t + 0.6, f), None)
    assert committed(panel) == 1
    assert np.isnan(panel.ts.ident[captured]).all()
    assert "Unassign" in panel.historyw.item(1).text()
    pump(0.25)
    assert not s.stroke.isVisible(), "the stroke fades out after release"
    panel.undo()
    assert (panel.ts.ident[captured] == ident).all()
    panel.redo()
    assert np.isnan(panel.ts.ident[captured]).all()


def test_sticky_brush_spares_the_crossing_track_and_alt_frees_it(panel):
    from audian_plugins.eodsorter.tools import Mods

    ts = panel.ts
    panel.browser.set_times(9.0, 2.0)
    pump(0.2)
    panel.controller.set_tool("E")
    panel.controller.set_brush(30)
    a = None
    for i in ts.ids():
        rows = ts.rows_of(i)
        t = ts.times[ts.idx[rows]]
        if ((t > 9.5) & (t < 10.5)).sum() > 10 and 600 < np.median(ts.fund[rows]) < 650:
            a = i
            break
    t, f, _ = point_of(panel, a, 0.5)
    stroke(panel, [(t - 0.2, f), (t + 0.2, f)], release=False)
    stuck = panel.controller.tool._stroke.stuck
    assert stuck is not None
    ids = set(ts.ident[panel.scene.stroke_rows].tolist())
    assert ids == {stuck}, "a sticky stroke touches only the track it started on"
    panel.controller.escape()
    assert committed(panel) == 0, "Esc discards the stroke"
    stroke(panel, [(t - 0.2, f), (t + 0.2, f)], Mods(alt=True), release=False)
    assert panel.controller.tool._stroke.stuck is None, "Alt frees the brush"
    panel.controller.escape()


def test_erase_click_unassigns_the_hovered_point(panel):
    ident = ids_in_view(panel)[0]
    panel.controller.set_tool("E")
    t, f, r = point_of(panel, ident)
    hover(panel, t, f)
    row = panel.scene.hover.row
    panel.controller.click(lane(panel), px(lane(panel), t, f), None)
    assert np.isnan(panel.ts.ident[row])
    assert committed(panel) == 1


def test_cut_click_previews_and_commits_the_same_plan(panel):
    ident = ids_in_view(panel)[0]
    panel.controller.set_tool("C")
    t, f, _ = point_of(panel, ident, 0.5)
    hover(panel, t, f)
    preview = panel.scene.preview
    assert preview is not None and preview.created
    marks = panel.scene.marks
    assert marks.cut_marker is not None
    assert marks.recolour and np.array_equal(marks.recolour[0][0], preview.rows)
    new = preview.created[0]
    assert f"| {new}" in panel.hintw.text() or f"{new}" in panel.hintw.text()
    panel.controller.click(lane(panel), px(lane(panel), t, f), None)
    plan = panel.controller.last_commit
    assert np.array_equal(plan.rows, preview.rows)
    assert np.array_equal(plan.new, preview.new)
    assert (panel.ts.ident[preview.rows] == new).all()
    assert committed(panel) == 1


def test_cut_line_cuts_every_track_it_crosses_in_one_entry(panel):
    panel.controller.set_tool("C")
    v = lane(panel).view()
    t = 0.5 * (v.x0 + v.x1)
    before = len(panel.ts.ids())
    stroke(panel, [(t, v.y1 - 1), (t + 0.01, v.y0 + 1)], release=False)
    crosses = len(panel.scene.marks.crosses)
    assert crosses >= 3
    assert str(crosses) in panel.hintw.text() or "cut" in panel.hintw.text()
    preview = panel.scene.preview
    lane(panel).ctl.release(lane(panel), px(lane(panel), t + 0.01, v.y0 + 1), None)
    assert committed(panel) == 1
    assert len(panel.ts.ids()) == before + len(preview.created)
    panel.ts.check_invariant()


def test_merge_anchor_then_click_merges_with_preview(panel):
    ts = panel.ts
    # the two fragments of one fish: ids 4 and 5 (fish 2)
    a, b = 4.0, 5.0
    panel.browser.set_times(0.0, DURATION)
    pump(0.2)
    panel.controller.set_tool("M")
    ta, fa, _ = point_of(panel, a)
    hover(panel, ta, fa)
    panel.controller.click(lane(panel), px(lane(panel), ta, fa), None)
    assert panel.controller.tool.anchor == a
    assert committed(panel) == 0
    tb, fb, _ = point_of(panel, b)
    hover(panel, tb, fb)
    marks = panel.scene.marks
    assert marks.connector is not None, "dashed connector between the ends"
    assert marks.outline_id == a
    assert marks.recolour and marks.recolour[0][1] == ("id", a)
    assert f"merge {int(b)} into {int(a)}" in panel.hintw.text()
    preview = panel.scene.preview
    panel.controller.click(lane(panel), px(lane(panel), tb, fb), None)
    assert np.array_equal(panel.controller.last_commit.rows, preview.rows)
    assert len(ts.rows_of(b)) == 0
    assert panel.controller.tool.anchor == a, "the anchor stays for the next piece"
    # Esc ladder: anchor, then the tool
    assert panel.controller.escape() == "anchor"
    assert panel.controller.escape() == "tool"
    assert panel.controller.tool.key == "V"


def test_merge_of_a_track_with_itself_is_not_offered(panel):
    panel.controller.set_tool("M")
    a = ids_in_view(panel)[0]
    t, f, _ = point_of(panel, a)
    hover(panel, t, f)
    panel.controller.click(lane(panel), px(lane(panel), t, f), None)
    hover(panel, t, f)
    assert "anchor" in panel.hintw.text()
    panel.controller.click(lane(panel), px(lane(panel), t, f), None)
    assert committed(panel) == 0


def test_assign_selection_to_clicked_track(panel):
    ts = panel.ts
    unassigned = np.flatnonzero(np.isnan(ts.ident))[:5]
    panel.controller.set_selection(unassigned)
    panel.controller.set_tool("A")
    target = ids_in_view(panel)[0]
    t, f, _ = point_of(panel, target)
    hover(panel, t, f)
    preview = panel.scene.preview
    assert preview is not None
    assert panel.scene.marks.recolour[0][1] == ("id", target)
    panel.controller.click(lane(panel), px(lane(panel), t, f), None)
    assert np.isin(unassigned, preview.rows).any()
    assert len(panel.scene.selection) == 0
    ts.check_invariant()


def test_assign_brush_onto_a_target(panel):
    ts = panel.ts
    panel.controller.set_tool("A")
    a, b = ids_in_view(panel)[:2]
    t, f, _ = point_of(panel, a)
    hover(panel, t, f)
    panel.controller.click(lane(panel), px(lane(panel), t, f), None)
    assert panel.controller.tool.target == a
    tb, fb, _ = point_of(panel, b, 0.2)
    # Alt: not stuck to b, so the stroke can take b's points
    from audian_plugins.eodsorter.tools import Mods

    stroke(panel, [(tb, fb), (tb + 0.3, fb)], Mods(alt=True))
    assert committed(panel) == 1
    ts.check_invariant()


@pytest.mark.parametrize("how", ["ridge off", "ctrl"])
def test_add_stroke_appends_detections_as_a_new_track(panel, how):
    """Literal painting: with "Track ridge in brush" off, or for one
    Ctrl+stroke with it on."""
    from audian_plugins.eodsorter.tools import Mods

    ts = panel.ts
    n0 = ts.n
    panel.ridgew.setChecked(how == "ctrl")
    mods = Mods(ctrl=True) if how == "ctrl" else None
    panel.controller.set_tool("F")
    v = lane(panel).view()
    f = 700.0  # nothing there
    t0 = v.x0 + 1.0
    stroke(panel, [(t0, f), (t0 + 0.4, f + 1), (t0 + 0.8, f)], mods, release=False)
    marks = panel.scene.marks
    assert len(marks.add_t) > 10, "candidate dots while painting"
    assert not marks.add_skip.any()
    lane(panel).ctl.release(lane(panel), px(lane(panel), t0 + 0.8, f), mods)
    assert ts.n > n0
    new = ts.ident[n0:]
    assert len(np.unique(new)) == 1
    assert committed(panel) == 1
    panel.undo()
    assert ts.n == n0


def test_selection_actions_new_id_merge_unassign(panel):
    ts = panel.ts
    c = panel.controller
    a, b = ids_in_view(panel)[:2]
    c.select_ids([a, b])
    assert c.selection_order == [a, b]
    c.merge_selected()
    assert len(ts.rows_of(b)) == 0 and committed(panel) == 1
    rows = ts.rows_of(a)[:10]
    c.set_selection(rows)
    c.new_id_from_selection()
    assert committed(panel) == 2
    assert ts.ident[rows[0]] != a
    c.set_selection(ts.rows_of(a)[:3])
    c.unassign_selected()
    assert committed(panel) == 3
    c.set_selection([])
    c.unassign_selected()  # rejected, not an edit
    assert committed(panel) == 3
    assert "nothing is selected" in panel.hintw.text()


def test_rejected_edits_explain_why(panel):
    panel.controller.set_tool("C")
    ident = ids_in_view(panel)[0]
    rows = panel.ts.rows_of(ident)
    t = float(panel.ts.times[panel.ts.idx[rows[0]]])
    f = float(panel.ts.fund[rows[0]])
    panel.browser.set_times(max(0.0, t - 1.0), 4.0)
    pump(0.2)
    hover(panel, t - 0.001, f)
    if panel.scene.hover is None:
        pytest.skip("first point not hoverable")
    panel.controller.click(lane(panel), px(lane(panel), t - 0.001, f), None)
    assert committed(panel) == 0
    assert "empty" in panel.hintw.text()


# --------------------------------------------------------------- history


def test_history_list_jumps_and_greys_the_redo_branch(panel):
    c = panel.controller
    for ident in ids_in_view(panel)[:3]:
        c.select_ids([ident])
        c.unassign_selected()
    assert committed(panel) == 3
    assert panel.historyw.count() == 4  # the start row plus three entries
    item = panel.historyw.item(1)
    panel._history_clicked(item)
    assert panel.ts.history.position == 1
    assert (
        panel.historyw.item(3).foreground().color().name()
        != panel.historyw.item(1).foreground().color().name()
    )
    assert panel.historyw.item(1).text().startswith("▸")
    panel._history_clicked(panel.historyw.item(3))
    assert panel.ts.history.position == 3
    # hovering an entry outlines its span
    panel._history_hovered(panel.historyw.item(2))
    assert panel.scene.span_outline is not None


def test_header_shows_unsaved_and_save_writes_the_directory(panel):
    c = panel.controller
    c.select_ids([ids_in_view(panel)[0]])
    c.unassign_selected()
    assert "unsaved" in panel.dirtyw.text()
    assert panel.save()
    assert "saved" == panel.dirtyw.text()
    assert (panel.folder / "ident_v.tracked.npy").exists()
    assert np.array_equal(
        np.load(panel.folder / "ident_v.npy"), panel.ts.ident, equal_nan=True
    )


# ------------------------------------------------------------------ keys


def _key(widget, key, mods=None):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    QTest.keyClick(widget, key, mods or Qt.KeyboardModifier.NoModifier)
    pump(0.02)


def test_key_router_claims_ctrl_z_only_in_edit_mode_over_a_lane(panel, world):
    from PySide6.QtCore import Qt

    from PySide6.QtWidgets import QApplication

    window = world["window"]
    window.activateWindow()
    QApplication.setActiveWindow(window) if hasattr(
        QApplication, "setActiveWindow"
    ) else None
    pump(0.1)
    pan = window.acts.pan_zoom
    view = lane(panel).vb.scene().views()[0]
    view.setFocus()
    c = panel.controller
    c.select_ids([ids_in_view(panel)[0]])
    c.unassign_selected()
    assert committed(panel) == 1
    # pointer over a lane, edit mode on: Ctrl+Z undoes, Pan zoom untouched
    t, f, _ = point_of(panel, ids_in_view(panel)[1])
    hover(panel, t, f)
    checked = pan.isChecked()
    _key(view.viewport(), Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert panel.ts.history.position == 0, "Ctrl+Z undid the edit"
    assert pan.isChecked() == checked, "audian's Pan zoom did not toggle"
    # tool keys
    _key(view.viewport(), Qt.Key.Key_C)
    assert c.tool.key == "C"
    _key(view.viewport(), Qt.Key.Key_Escape)
    assert c.tool.key == "V"
    # edit mode off: Ctrl+Z is audian's again
    panel.set_edit_mode(False)
    _key(view.viewport(), Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert pan.isChecked() != checked
    pan.setChecked(checked)


def test_ctrl_shift_e_toggles_edit_mode(panel, world):
    from PySide6.QtCore import Qt

    view = lane(panel).vb.scene().views()[0]
    view.setFocus()
    mods = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier
    _key(view.viewport(), Qt.Key.Key_E, mods)
    assert not panel.edit_mode
    _key(view.viewport(), Qt.Key.Key_E, mods)
    assert panel.edit_mode


def test_keys_in_a_text_field_are_typed_not_claimed(panel):
    from PySide6.QtCore import Qt

    field = panel.configw
    field.setText("")
    field.setFocus()
    pump(0.05)
    t, f, _ = point_of(panel, ids_in_view(panel)[0])
    hover(panel, t, f)  # even with the pointer over a lane
    _key(field, Qt.Key.Key_V)
    assert field.text() == "v"
    assert panel.controller.tool.key == "V"
    field.setText("")


def test_no_window_actions_are_added(world, tmp_path):
    from PySide6.QtGui import QAction

    window = world["window"]

    def keyed():
        return sorted(
            (a.text(), tuple(s.toString() for s in a.shortcuts()))
            for a in window.findChildren(QAction)
            if a.shortcuts()
        )

    browser = world["browser"]
    before = keyed()
    browser.open_plugin_panel(LABEL)
    pump(0.2)
    p = browser.plugin_panels[LABEL]
    p.set_edit_mode(True)
    assert keyed() == before
    browser.close_plugin_panel(LABEL)
    pump(0.2)


# ------------------------------------------------------- real mouse events


def test_a_real_left_drag_reaches_the_tool_and_middle_drag_pans(panel):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    surface = lane(panel)
    vb = surface.vb
    view = vb.scene().views()[0]
    panel.controller.set_tool("E")
    ident = ids_in_view(panel)[0]
    t, f, _ = point_of(panel, ident, 0.4)

    def screen(t, f):
        x, y = px(surface, t, f)
        scene_pt = vb.mapToScene(x, y)
        p = view.mapFromScene(scene_pt)
        return QPoint(int(p.x()), int(p.y()))

    vp = view.viewport()
    start = screen(t, f)
    QTest.mouseMove(vp, start)
    pump(0.05)
    QTest.mousePress(
        vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start
    )
    for k in range(1, 12):
        QTest.mouseMove(vp, start + QPoint(6 * k, 0))
        pump(0.01)
    QTest.mouseRelease(
        vp,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        start + QPoint(66, 0),
    )
    pump(0.1)
    assert committed(panel) == 1, "the Qt drag path reached the erase tool"
    (x0, x1), (y0, y1) = vb.viewRange()
    regions = []

    def region(*args):
        regions.append(args)

    vb.sigSelectedRegion.connect(region)
    vb.rbScaleBox.hide()
    try:
        QTest.mousePress(
            vp, Qt.MouseButton.MiddleButton, Qt.KeyboardModifier.NoModifier, start
        )
        for k in range(1, 8):
            QTest.mouseMove(vp, start + QPoint(10 * k, 5 * k))
            pump(0.01)
        QTest.mouseRelease(
            vp,
            Qt.MouseButton.MiddleButton,
            Qt.KeyboardModifier.NoModifier,
            start + QPoint(70, 35),
        )
        pump(0.1)
        shown = vb.rbScaleBox.isVisible()
    finally:
        vb.sigSelectedRegion.disconnect(region)
        vb.rbScaleBox.hide()
    assert committed(panel) == 1, "a middle drag is not a tool gesture"
    assert not regions and not shown, "a middle drag pans; it draws no zoom box"
    (u0, u1), (v0, v1) = vb.viewRange()
    # dragged right and down: the view moves back in time and up in frequency
    assert u0 < x0 and u1 < x1
    assert abs((u1 - u0) - (x1 - x0)) < 1e-6 * (x1 - x0), "panning keeps the zoom"
    assert v0 > y0 and v1 > y1


# ------------------------------------------------------------ snippets


class FakeRunnerClient:
    """`RunnerClient`'s signals; `submit` writes a canned run (design 7.4)."""

    def __init__(self, panel):
        from PySide6.QtCore import QObject, Signal

        class Sigs(QObject):
            sigState = Signal(str)
            sigHello = Signal(dict)
            sigProgress = Signal(str, str, int, int, str)
            sigResult = Signal(str, dict)
            sigError = Signal(str, str, str)
            sigLog = Signal(str, str)

        self._s = Sigs()
        for name in (
            "sigState",
            "sigHello",
            "sigProgress",
            "sigResult",
            "sigError",
            "sigLog",
        ):
            setattr(self, name, getattr(self._s, name))
        self.panel = panel
        self.hello = {
            "wavetracker": "fake",
            "python": "3",
            "capabilities": ["detect"],
            "devices": ["cpu"],
        }
        self.state = "idle"
        self.job = None
        self.jobs = []

    def ensure_started(self):
        pass

    def submit(self, op, **params):
        from PySide6.QtCore import QTimer

        jid = f"j{len(self.jobs) + 1}"
        self.jobs.append((op, params))
        self.job = jid
        out = Path(params["output_dir"])
        grid = self.panel._job_grid_for_tests
        k0, k1 = grid
        # two short tracks inside the snippet, in the run's own frames
        m = k1 - k0
        times = (np.arange(m) * STEP + NFFT / 2) / RATE
        frames = np.arange(m)
        fund = np.concatenate([700 + 0 * frames, 720 + 0 * frames]).astype(float)
        idx = np.concatenate([frames, frames])
        ident = np.concatenate([np.zeros(m), np.ones(m)])
        o = np.argsort(idx, kind="stable")
        out.mkdir(parents=True, exist_ok=True)
        np.save(out / "fund_v.npy", fund[o])
        np.save(out / "idx_v.npy", idx[o].astype(np.int64))
        np.save(out / "ident_v.npy", ident[o])
        np.save(out / "sign_v.npy", np.ones((len(o), 2), np.float32))
        np.save(out / "times.npy", times)
        (out / "wavetracker.json").write_text(json.dumps({"rate": RATE}))

        def finish():
            self.job = None
            self.sigResult.emit(jid, {"output_dir": str(out)})

        QTimer.singleShot(10, finish)
        return jid

    def cancel(self):
        self.job = None

    def shutdown(self, timeout_ms=2000):
        pass


def test_snippet_flow_with_a_fake_runner(panel):
    panel.runner = None
    panel.runner_factory = lambda oneshot: FakeRunnerClient(panel)
    v = lane(panel).view()
    grid = panel.ts.grid
    assert grid is not None, "the synthetic results have a regular grid"
    k0, k1 = grid.frame_range(v.x0, v.x1)
    panel._job_grid_for_tests = (k0, k1)
    before = panel.ts.ident.copy()
    panel.track_visible()
    deadline = time.monotonic() + 10
    while panel.scene.snippet is None and time.monotonic() < deadline:
        pump(0.05)
    snippet = panel.scene.snippet
    assert snippet is not None, "the provisional layer arrived"
    assert snippet.k0 == k0
    assert panel.snippetw.isVisible() or not panel.isVisible()
    assert "2 tracks" in panel.snippettextw.text()
    panel.redraw_now()
    assert any(
        c.xData is not None and len(c.xData) for c in panel.overlays[0].snippet_curves
    )
    # discard leaves the model untouched
    panel.discard_snippet()
    assert np.array_equal(panel.ts.ident, before, equal_nan=True)
    assert committed(panel) == 0
    # again, and accept: one history entry
    panel.show_snippet(snippet, grid)
    panel.accept_snippet()
    assert committed(panel) == 1
    assert panel.scene.snippet is None
    panel.ts.check_invariant()


def _pending_snippet(panel):
    """A provisional snippet over the view's frames, not accepted."""
    from audian_plugins.eodsorter.model import Snippet

    v = lane(panel).view()
    grid = panel.ts.grid
    k0, k1 = grid.frame_range(v.x0, v.x1)
    frames = np.arange(k0, k1)
    snippet = Snippet(
        k0=k0,
        k1=k1,
        fund=np.full(len(frames), 940.0),
        idx=frames,
        ident=np.zeros(len(frames)),
        sign=np.ones((len(frames), 2), np.float32),
        cplx=None,
        meta={},
    )
    panel.show_snippet(snippet, grid)
    panel.redraw_now()
    return snippet


def test_a_pending_snippet_says_how_to_edit_it_and_enter_accepts(panel):
    """2026-10-07: tracks of an unaccepted snippet looked editable, and
    hovering or clicking them did nothing at all."""
    from audian_plugins.eodsorter.tools import SNIPPET_PENDING

    snippet = _pending_snippet(panel)
    c = panel.controller
    t = float(panel.ts.times[(snippet.k0 + snippet.k1) // 2])
    hover(panel, t, 940.0)
    assert c.scene.hover is None, "the provisional layer is not the session"
    assert c.label_lines()[-1] == SNIPPET_PENDING
    assert c.hint_text() == SNIPPET_PENDING
    # a gesture there edits nothing and says why
    c.click(lane(panel), px(lane(panel), t, 940.0), None)
    stroke(panel, [(t - 0.5, 939.0), (t + 0.5, 941.0)])
    assert committed(panel) == 0 and len(c.scene.selection) == 0
    assert c.hint_text() == SNIPPET_PENDING
    # Enter accepts, before any issue suggestion
    assert panel.key_applies("accept_issue")
    panel.run_key("accept_issue")
    assert panel.scene.snippet is None and committed(panel) == 1
    hover(panel, t, 940.0)
    assert c.scene.hover is not None, "accepted, its track is editable"
    assert c.hint_text() != SNIPPET_PENDING
    assert "Enter" in panel.acceptw.text()


def test_cleanup_refusal_is_a_sentence_not_an_exception(panel):
    from audian_plugins.eodsorter.panel import cleanup_refusal

    message = (
        "ValueError: cleanup: no identity passed the frequency-density "
        "selection, so there is nothing to clean up. Check --stride/--freq-tol."
    )
    text = cleanup_refusal(message)
    assert text.startswith("Clean up found nothing to keep: no identity passed")
    assert "--stride" not in text
    assert cleanup_refusal("ValueError: something else") is None
    seen = []
    panel.browser.notify = lambda level, msg: seen.append((level, msg))
    try:
        panel._job = {"kind": "cleanup", "id": "j9", "tmp": None}
        panel._job_error("j9", "exception", message)
    finally:
        del panel.browser.notify
    assert seen == [("warning", text)]
    assert panel.controller.hint_text() == text


def test_cleanup_dialog_defaults_are_fitted_to_the_session(panel, monkeypatch):
    from PySide6.QtWidgets import QDialog, QDoubleSpinBox, QSpinBox

    from audian_plugins.eodsorter import model as M

    setup = M.cleanup_setup(panel.ts)
    seen = {}

    def fake_exec(dialog):
        seen["fish"] = dialog.findChildren(QSpinBox)[0].value()
        seen["stride"] = dialog.findChildren(QDoubleSpinBox)[0].value()
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(QDialog, "exec", fake_exec)
    panel.clean_up()
    assert seen["fish"] == setup.n_fish == 5
    assert seen["stride"] == pytest.approx(setup.stride_minutes, abs=0.01)
    assert setup.stride_minutes < 1.0, "20 s of tracks, not a 10-minute stride"


# ---------------------------------------------------------- unsaved (8.4)


def test_closing_with_unsaved_edits_asks_and_saves(world, tmp_path):
    browser = world["browser"]
    folder = tmp_path / "r"
    shutil.copytree(world["pristine"], folder)
    browser.open_plugin_panel(LABEL)
    pump(0.2)
    p = browser.plugin_panels[LABEL]
    p.ask = lambda *a: "Discard"
    if p.ts is not None:
        p.set_session(None)
    p.open_results(folder, ask=False)
    ts = p.ts
    p.controller.commit_plan(lambda: ts.plan_delete_ids([ts.ids()[0]]))
    asked = []

    def ask(title, text, buttons):
        asked.append(buttons)
        return "Save"

    p.ask = ask
    browser.close_plugin_panel(LABEL)
    pump(0.2)
    assert asked and asked[0] == ("Save", "Discard")
    saved = np.load(folder / "ident_v.npy")
    assert np.array_equal(saved, ts.ident, equal_nan=True)


def test_opening_other_results_with_unsaved_edits_can_be_cancelled(
    panel, tmp_path, world
):
    ts = panel.ts
    panel.controller.commit_plan(lambda: ts.plan_delete_ids([ts.ids()[0]]))
    other = tmp_path / "other"
    shutil.copytree(world["pristine"], other)
    panel.ask = lambda title, text, buttons: "Cancel"
    assert not panel.open_results(other)
    assert panel.ts is ts
    panel.ask = lambda title, text, buttons: "Discard"
    assert panel.open_results(other)
    assert panel.ts is not ts


def test_tab_title_marks_unsaved_changes(panel):
    from PySide6.QtWidgets import QTabWidget

    ts = panel.ts
    panel.controller.commit_plan(lambda: ts.plan_delete_ids([ts.ids()[0]]))
    w, title = panel, None
    while w is not None:
        parent = w.parentWidget()
        tabs = parent.parentWidget() if parent is not None else None
        if isinstance(tabs, QTabWidget) and tabs.indexOf(w) >= 0:
            title = tabs.tabText(tabs.indexOf(w))
            break
        w = parent
    assert title == "Tracks ●"


# ------------------------------------------------------------ performance


def test_hover_and_redraw_are_fast_on_100k_detections(panel):
    """×5 of the 6.1 targets, on a tenth of the reference dataset."""
    from audian_plugins.eodsorter import model as M

    ts = panel.ts
    rng = np.random.default_rng(0)
    n_frames = len(ts.times)
    n = 100_000
    idx = np.sort(rng.integers(0, n_frames, n))
    ids = rng.integers(0, 200, n).astype(float)
    fund = 560 + 2.0 * ids + rng.normal(0, 0.2, n)
    # one detection per id and frame
    key = idx * 1000 + ids.astype(int)
    _u, first = np.unique(key, return_index=True)
    idx, ids, fund = idx[first], ids[first], fund[first]
    big = M.TrackSet.from_arrays(
        fund, idx, ids, np.ones((len(idx), 2), np.float32), ts.times
    )
    panel.set_session(big)
    panel.browser.set_times(0.0, DURATION)
    pump(0.2)
    panel.redraw_now()
    o = panel.overlays[0]
    times = []
    for k in range(5):
        panel.browser.set_times(0.5 * k, DURATION - 2)
        for ov in panel.overlays:
            ov.invalidate()
        t0 = time.perf_counter()
        panel.redraw_now()
        times.append(1000 * (time.perf_counter() - t0))
    assert min(times) < 150, f"redraw of all lanes {min(times):.1f} ms"
    surface = lane(panel)
    hov = []
    for k in range(20):
        x = 50 + 30 * k
        panel.controller.hover_at(surface, (x, 100.0))
        t0 = time.perf_counter()
        panel.controller.flush()
        o.update_plot()
        hov.append(1000 * (time.perf_counter() - t0))
    assert np.median(hov) < 20, f"hover {np.median(hov):.1f} ms"


# ------------------------------------------------------------ niceties


def test_issues_g_visits_joins_and_enter_merges(panel):
    panel.browser.set_times(0.0, 3.0)
    pump(0.2)
    for kind, w in panel.issuekindw.items():
        w.setChecked(kind == "join")
    issues = panel.issues()
    assert issues, "the fragmented synthetic fish give join issues"
    panel.goto_issue(+1)
    issue = panel.current_issue
    assert issue is not None and issue.kind == "join"
    assert "possible join" in panel.issuetextw.text()
    assert panel.scene.span_outline is not None
    a, b = issue.ids
    panel.accept_issue()
    assert committed(panel) == 1
    assert len(panel.ts.rows_of(b)) == 0 or len(panel.ts.rows_of(a)) == 0


def test_g_moves_the_window_onto_an_issue_in_time_and_frequency(panel):
    for kind, w in panel.issuekindw.items():
        w.setChecked(kind == "join")
    issues = panel.issues()
    assert issues
    fs = np.array([i.f for i in issues])
    # zoomed in on a small corner with no issue in it: 1 s wide, 40 Hz high,
    # 300 Hz above every issue
    f0 = float(fs.max()) + 300.0
    panel.zoom_to((0.05, 0.05 + 1.0 / 1.2, f0, f0 + 40.0 / 1.2))
    pump(0.2)
    x0, x1, y0, y1 = panel.view_range()
    panel.goto_issue(+1)
    pump(0.2)
    issue = panel.current_issue
    u0, u1, v0, v1 = panel.view_range()
    assert u0 <= issue.t <= u1, "the window moved to the issue in time"
    assert v0 <= issue.f <= v1, "and in frequency"
    assert abs((u1 - u0) - (x1 - x0)) < 1e-3 * (x1 - x0), "the zoom is kept"
    assert abs((v1 - v0) - (y1 - y0)) < 1e-3 * (y1 - y0)
    # an issue already well inside the band does not move the band
    panel.centre_on(issue.t, 0.5 * (v0 + v1))
    pump(0.1)
    assert panel.view_range()[2:] == (v0, v1)


def test_isolate_shows_only_the_selected_tracks(panel):
    a = ids_in_view(panel)[0]
    panel.controller.select_ids([a])
    panel.isolatew.setChecked(True)
    panel.redraw_now()
    g = panel.overlays[0].geometry
    assert g.n_ids == 1
    assert "isolated: 1 track" in panel.hintw.text()
    panel.isolatew.setChecked(False)
    panel.redraw_now()
    assert panel.overlays[0].geometry.n_ids > 1


def test_brush_size_keys_and_limits(panel):
    c = panel.controller
    c.set_brush(14)
    panel.run_key("brush_larger")
    assert c.brush_px > 14 and panel.brushw.value() == c.brush_px
    assert "brush" in panel.hintw.text()
    for _ in range(40):
        panel.run_key("brush_smaller")
    assert c.brush_px == 3
    for _ in range(40):
        panel.run_key("brush_larger")
    assert c.brush_px == 80


def test_zoom_to_selection_moves_the_view(panel):
    ts = panel.ts
    a = ids_in_view(panel)[0]
    panel.controller.select_ids([a])
    panel.zoom_selection()
    pump(0.2)
    rows = ts.rows_of(a)
    t = ts.times[ts.idx[rows]]
    v = lane(panel).view()
    assert v.x0 <= t.min() + 1e-6 and v.x1 >= t.max() - 0.5
    assert v.y0 <= ts.fund[rows].min() and v.y1 >= ts.fund[rows].max()


def test_autosave_writes_beside_the_results(panel):
    ts = panel.ts
    panel.controller.commit_plan(lambda: ts.plan_delete_ids([ts.ids()[0]]))
    assert panel._autosave_timer.isActive()
    panel.autosave()
    from audian_plugins.eodsorter import model as M

    auto = M.read_autosave(panel.folder)
    assert auto is not None and len(auto.ident) == ts.n


def test_context_menu_offers_the_edit_actions(panel):
    a = ids_in_view(panel)[0]
    t, f, _ = point_of(panel, a)
    panel.controller.select_ids([a])
    menu = panel.controller.context_menu(lane(panel), px(lane(panel), t, f), None)
    texts = [act.text() for act in menu.actions() if act.text()]
    assert any(text.startswith("Select track") for text in texts)
    assert any("Unassign selected points" in text for text in texts)
    merge = [act for act in menu.actions() if act.text().startswith("Merge selected")][
        0
    ]
    assert not merge.isEnabled(), "one track selected: nothing to merge"


def test_alt_wheel_resizes_the_brush_and_plain_wheel_passes(panel):
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtWidgets import QGraphicsSceneWheelEvent

    s = lane(panel)
    c = panel.controller
    c.set_brush(14)

    def wheel(mods, delta=120):
        ev = QGraphicsSceneWheelEvent(QGraphicsSceneWheelEvent.Type.GraphicsSceneWheel)
        ev.setModifiers(mods)
        ev.setDelta(delta)
        ev.setPos(QPointF(10, 10))
        ev.setAccepted(False)
        s.wheelEvent(ev)
        return ev.isAccepted()

    assert wheel(Qt.KeyboardModifier.AltModifier)
    assert c.brush_px > 14
    assert not wheel(Qt.KeyboardModifier.NoModifier), "plain wheel reaches the view box"


# ------------------------------------------- regressions from the review


class HoldingRunner(FakeRunnerClient):
    """Accepts jobs and never finishes them."""

    def submit(self, op, **params):
        jid = f"j{len(self.jobs) + 1}"
        self.jobs.append((op, params))
        self.job = jid
        return jid


def test_replace_moves_the_old_results_aside_and_the_session_follows(
    panel, monkeypatch
):
    from audian_plugins.eodsorter import runner as R

    folder = Path(panel.folder)
    monkeypatch.setattr(R, "default_output_dir", lambda first: str(folder))
    fake = HoldingRunner(panel)
    panel.runner = None
    panel.runner_factory = lambda oneshot: fake
    ts = panel.ts
    panel.controller.commit_plan(lambda: ts.plan_delete_ids([ts.ids()[0]]))
    panel.ask = lambda title, text, buttons: "Replace"
    panel.track_recording()
    assert fake.jobs, "the run was submitted"
    assert not folder.exists(), "the old results were renamed, not left in the way"
    moved = Path(panel.folder)
    assert moved.name.startswith(folder.name + ".old-") and moved.exists()
    # unsaved edits of the old results go into the old results
    assert panel.save()
    np.testing.assert_array_equal(np.load(moved / "ident_v.npy"), ts.ident)
    assert not folder.exists()
    panel.cancel_job(quiet=True)


def test_save_as_into_other_results_asks_first(panel, world, tmp_path):
    other = tmp_path / "other"
    shutil.copytree(world["pristine"], other)
    mine = panel.folder
    asked = []
    panel.ask = lambda title, text, buttons: asked.append(buttons) or "Cancel"
    assert not panel.save_into(other)
    assert asked == [("Replace", "Cancel")] and panel.folder == mine
    panel.ask = lambda title, text, buttons: "Replace"
    assert panel.save_into(other)
    assert Path(panel.folder) == other and not panel.ts.is_dirty()


def test_autosave_goes_away_when_edits_are_undone_to_the_saved_state(panel):
    from audian_plugins.eodsorter import model as M

    ts = panel.ts
    panel.controller.commit_plan(lambda: ts.plan_delete_ids([ts.ids()[0]]))
    panel.autosave()
    assert M.read_autosave(panel.folder) is not None
    panel.undo()
    assert not ts.is_dirty()
    panel.autosave()
    assert M.read_autosave(panel.folder) is None


def test_a_stale_autosave_says_so_and_defaults_to_the_newer_file(panel):
    from audian_plugins.eodsorter import model as M

    folder = Path(panel.folder)
    ts = panel.ts
    panel.controller.commit_plan(lambda: ts.plan_delete_ids([ts.ids()[0]]))
    panel.autosave()
    # something else rewrites the identities afterwards
    newer = np.where(np.isfinite(ts.tracked), 7.0, np.nan)
    st = (folder / "ident_v.npy").stat()
    np.save(folder / "ident_v.npy", newer)
    os.utime(folder / "ident_v.npy", (st.st_atime + 5, st.st_mtime + 5))
    panel.set_session(None)
    asked = []

    def ask(title, text, buttons):
        asked.append((text, buttons))
        return buttons[-1]

    panel.ask = ask
    assert panel.open_results(folder, ask=False)
    assert asked and "changed by something else" in asked[0][0]
    assert asked[0][1][-1] == "Keep the newer file"
    np.testing.assert_array_equal(panel.ts.ident, newer)
    assert M.read_autosave(folder) is None


def test_a_session_without_results_directory_is_recovered(panel, monkeypatch, tmp_path):
    from audian_plugins.eodsorter import model as M
    from audian_plugins.eodsorter import panel as P

    monkeypatch.setattr(P, "cache_dir", lambda: tmp_path / "cache")
    grid = panel.ts.grid
    panel.set_session(None)
    ts = M.TrackSet.empty(grid)
    panel.set_session(ts, None, "snippets")
    sn = M.Snippet(
        40, 60, np.full(20, 700.0), np.arange(40, 60), np.zeros(20),
        np.ones((20, 2), np.float32), None, {},
    )  # fmt: skip
    panel.controller.commit_plan(lambda: ts.plan_replace_span(sn))
    panel.autosave()
    rec = panel.recording_paths()[0]
    assert M.read_autosave(P.autosave_cache(rec)) is not None
    panel.set_session(None)
    panel.prefs["results_dirs"] = {}
    panel._loaded_for = None
    panel.ask = lambda title, text, buttons: "Recover"
    panel.load_for_recording()
    assert panel.ts is not None and panel.ts.n == 20 and panel.folder is None
    np.testing.assert_array_equal(panel.ts.fund, ts.fund)
    assert panel.ts.is_dirty()


def test_cleanup_result_keeps_edits_made_while_it_ran_and_drops_for_other_results(
    panel, tmp_path
):
    from audian_plugins.eodsorter import runner as R

    ts = panel.ts
    base = np.array(ts.ident)
    a, b = ts.ids()[:2]
    cleaned = np.where(np.isfinite(base), float(a), np.nan)
    # meanwhile the reader deletes track b
    panel.controller.commit_plan(lambda: ts.plan_delete_ids([b]))
    tmp = R.make_tmpdir()
    np.save(Path(tmp) / "ident_v_cleaned_n1.npy", cleaned)
    job = {"tmp": tmp, "n_fish": 1, "session": ts, "base": base}
    texts = []
    panel.ask = lambda title, text, buttons: texts.append(text) or "Apply"
    panel._cleanup_done(job, {})
    assert "keep your identities" in texts[0]
    rows_b = np.flatnonzero(base == b)
    assert np.isnan(ts.ident[rows_b]).all(), "the deletion survived the cleanup"
    others = np.flatnonzero(np.isfinite(base) & (base != b))
    got = ts.ident[others]
    # merged into one id: the duplicates per frame lose by the local median
    assert np.all((got == a) | np.isnan(got)) and (got == a).sum() > 100
    # a result for a session that is no longer open is dropped
    tmp = R.make_tmpdir()
    np.save(Path(tmp) / "ident_v_cleaned_n1.npy", cleaned)
    before = len(ts.history.entries)
    panel._cleanup_done(
        {"tmp": tmp, "n_fish": 1, "session": object(), "base": base}, {}
    )
    assert len(ts.history.entries) == before and not Path(tmp).exists()


def test_a_snippet_for_other_results_is_dropped(panel):
    from audian_plugins.eodsorter import runner as R

    tmp = R.make_tmpdir()
    job = {"tmp": tmp, "out": tmp, "grid": panel.ts.grid, "k0": 0, "session": object()}
    panel._snippet_done(job, {})
    assert panel.scene.snippet is None and not Path(tmp).exists()


def test_session_channels_for_the_peak_search():
    from audian_plugins.eodsorter import model as M
    from audian_plugins.eodsorter.panel import session_channels

    def ts(meta, c=3):
        return M.TrackSet.from_arrays(
            np.array([500.0]), np.array([0]), np.array([0.0]),
            np.ones((1, c), np.float32), np.array([0.1, 0.2]), meta=meta,
        )  # fmt: skip

    assert session_channels(ts({}), 11) is None
    assert session_channels(ts({"channels": [0, 2, 5]}), 11) == [0, 2, 5]
    excl = {"config": {"spectrogram": {"exclude_channels": [1, 3]}}}
    assert session_channels(ts(excl), 5) == [0, 2, 4]


def test_rebase_ident():
    from audian_plugins.eodsorter.panel import rebase_ident

    base = np.array([0.0, 0.0, 1.0, np.nan])
    result = np.array([5.0, 5.0, 5.0, 5.0])
    current = np.array([0.0, 2.0, 1.0, np.nan, 3.0])
    out, kept = rebase_ident(result, base, current)
    np.testing.assert_array_equal(out, [5.0, 2.0, 5.0, 5.0, 3.0])
    assert kept == 1


def test_a_running_export_thread_outlives_its_panel(app):
    from PySide6.QtCore import QObject, QThread, Signal, Slot

    from audian_plugins.eodsorter import panel as P

    class Slow(QObject):
        done = Signal()

        @Slot()
        def run(self):
            time.sleep(0.3)
            self.done.emit()

    owner = QObject()
    thread = QThread(owner)
    worker = Slow()
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    thread.start()
    pump(0.05)
    P.orphan_thread(thread, worker)
    owner.deleteLater()
    pump(0.1)
    assert P._ORPHANS
    deadline = time.monotonic() + 3
    while P._ORPHANS and time.monotonic() < deadline:
        pump(0.05)
    assert not P._ORPHANS


def test_window_close_stops_the_runner(world, tmp_path):
    browser = world["browser"]
    browser.open_plugin_panel(LABEL)
    pump(0.2)
    p = browser.plugin_panels[LABEL]
    p.ask = lambda *a: "Discard"
    stopped = []
    fake = HoldingRunner(p)
    fake.shutdown = lambda timeout_ms=2000: stopped.append(True)
    p.runner = fake
    p.about_to_flush_labels()  # what audian calls when the window closes
    assert stopped and p._closing
    browser.close_plugin_panel(LABEL)
    pump(0.2)


# --------------------------------------------- regressions from the QA pass


def test_track_colours_avoid_the_hues_a_ridge_is_painted_in(app):
    """The default map runs blue, yellow, white: no track may be yellow."""
    from audian import theme
    from audian_plugins.eodsorter.overlay import (
        _hue_distance,
        bright_hues,
        slot_palette,
    )
    from PySide6.QtGui import QColor

    cmap = theme.spectrogram_colormap(theme.DEFAULT_SPECTROGRAM_MAP)
    avoid = bright_hues(cmap)
    assert avoid, "the default map has chromatic bright colours"
    pal = slot_palette(
        cmap.map(0.0, mode="qcolor"), cmap.map(1.0, mode="qcolor"), avoid
    )
    hues = [QColor(c).getHsv()[0] for c in pal]
    assert len(set(pal)) == len(pal)
    for h in hues:
        assert all(_hue_distance(h, a) >= 25 for a in avoid), (h, avoid)


def test_unchanged_lines_and_dots_do_not_repaint(app):
    from PySide6.QtGui import QPen

    from audian_plugins.eodsorter.overlay import LineItem, PointsItem

    from audian import theme

    line, dots = LineItem(), PointsItem()
    x, y = np.arange(5.0), np.ones(5)
    line.setData(x, y)
    dots.setData(x=x, y=y, size=3, brush=theme.brush("#ff0000"))
    seen = []
    line.update = lambda *a: seen.append("line")
    dots.update = lambda *a: seen.append("dots")
    line.setData(x.copy(), y.copy())
    line.setPen(QPen(line._pen))
    dots.setData(x=x.copy(), y=y.copy(), size=3, brush=theme.brush("#ff0000"))
    assert seen == []
    line.setData(x, y + 1)
    assert seen == ["line"]


def test_dim_spectrogram_draws_a_veil_under_the_tracks(panel):
    o = panel.overlays[0]
    assert o.veil.boundingRect().isEmpty()
    panel.dimspecw.setChecked(True)
    panel.redraw_now()
    assert not o.veil.boundingRect().isEmpty()
    assert o.veil.zValue() < o.curves[0].zValue()
    assert panel.prefs["dim_spec"] is True
    panel.dimspecw.setChecked(False)
    panel.redraw_now()
    assert o.veil.boundingRect().isEmpty()


def test_sections_fold_and_remember_it(panel):
    run = panel.sections["Run settings"]
    edit = panel.sections["Edit"]
    assert edit.is_open()
    run.set_open(False)
    assert not run.group.isVisible() and panel.prefs["folded"]["Run settings"] is True
    run.header.click()
    assert run.is_open() and panel.prefs["folded"]["Run settings"] is False
    # the run buttons and the progress row stay outside the foldable part
    run.set_open(False)
    assert panel.trackvisw.isVisibleTo(panel) and panel.trackrecw.isVisibleTo(panel)


def test_there_is_no_python_to_choose(panel):
    """wavetracker is installed with audian and runs under its interpreter:
    the panel has no field, button or setting for another one."""
    from PySide6.QtWidgets import QPushButton

    from audian_plugins.eodsorter import panel as P

    assert not hasattr(panel, "pythonw") and not hasattr(panel, "checkw")
    assert "python" not in P.DEFAULT_PREFS
    texts = [b.text() for b in panel.findChildren(QPushButton)]
    assert "Choose…" not in texts and "Check" not in texts
    text = panel.interpw.text()
    assert text.startswith("wavetracker ") and "✗" not in text


def test_a_stale_python_setting_is_ignored(panel, monkeypatch):
    from audian_plugins.eodsorter import panel as P

    stored = {"version": P.SETTINGS_VERSION, "python": "/old/venv/bin/python"}
    monkeypatch.setattr(
        "audian.pluginapi.settings", lambda: {P.SETTINGS_KEY: stored}, raising=False
    )
    assert "python" not in P.load_prefs()


def test_a_broken_wavetracker_install_is_one_red_line(panel, monkeypatch):
    from audian_plugins.eodsorter import runner as R

    message = "wavetracker is not installed in this environment: ImportError: x"
    monkeypatch.setattr(R, "wavetracker_status", lambda: (None, message))
    panel.runner = None
    panel.runner_factory = lambda oneshot: (_ for _ in ()).throw(AssertionError)
    panel.sections["Run settings"].set_open(False)
    panel.track_visible()
    assert panel.sections["Run settings"].is_open()
    assert panel.interpw.text() == f"✗ {message}"
    assert "not installed" in panel.hintw.text()


def test_a_runner_that_cannot_start_says_so_in_red(panel):
    fake = HoldingRunner(panel)
    fake.hello = None
    panel.runner = fake
    panel._connect(fake)
    fake.sigError.emit("", "startup", "Traceback...\nModuleNotFoundError: wavetracker")
    assert panel.interpw.text().startswith("✗ startup")
    assert "ModuleNotFoundError" in panel.interpw.text()
    panel.runner = None


def test_hidden_spectrograms_are_pointed_out(panel, world):
    browser = world["browser"]
    try:
        browser.set_panels(traces=1, specs=0)
        pump(0.3)
        panel._check_lanes()
        assert panel.nospecw.isVisibleTo(panel)
        panel.show_spectrograms()
        pump(0.3)
        assert not panel.nospecw.isVisibleTo(panel)
    finally:
        browser.set_panels(traces=0, specs=1)
        pump(0.3)


# ------------------------------------------------- the selection strip


def test_the_selection_strip_follows_the_selection_and_runs_its_actions(panel):
    ts = panel.ts
    c = panel.controller
    strip = panel.stripw
    ids = ids_in_view(panel)
    # two fish side by side (consecutive ids are fragments of one fish)
    a, b = next(
        (i, j)
        for i in ids
        for j in ids
        if i != j and len(np.intersect1d(ts.idx[ts.rows_of(i)], ts.idx[ts.rows_of(j)]))
    )
    assert strip.isHidden(), "no selection, no strip"
    c.select_ids([a])
    assert not strip.isHidden()
    n = len(ts.rows_of(a))
    assert panel.stripsumw.text() == f"1 track · {n:,} points"
    btn = panel.stripbtns
    assert not btn["merge"].isEnabled() and "two or more" in btn["merge"].toolTip()
    assert not btn["swap"].isEnabled() and "exactly two" in btn["swap"].toolTip()
    assert btn["unassign"].isEnabled() and "(Del)" in btn["unassign"].toolTip()
    c.select_ids([a, b])
    assert panel.stripsumw.text().startswith("2 tracks · ")
    assert btn["swap"].isEnabled() and "closest approach" in btn["swap"].toolTip()
    assert f"into {int(a)}" in btn["merge"].toolTip()
    btn["merge"].click()
    assert committed(panel) == 1 and len(ts.rows_of(b)) == 0
    assert panel.stripsumw.text().startswith("1 track · "), "the merged track"
    panel.undo()
    assert len(ts.rows_of(b)) > 0
    c.select_ids([a, b])
    rows_a = ts.rows_of(a).copy()
    btn["swap"].click()
    assert committed(panel) == 1
    t_swap = c.closest_approach()
    swapped = rows_a[ts.times[ts.idx[rows_a]] >= t_swap]
    assert (ts.ident[swapped] == b).all()
    panel.undo()
    c.select_ids([a])
    btn["unassign"].click()
    assert np.isnan(ts.ident[rows_a]).all()
    assert strip.isHidden(), "the selection went with the points"
    panel.undo()
    c.select_ids([a])
    btn["new_id"].click()
    assert committed(panel) == 1 and len(ts.rows_of(a)) == 0
    panel.undo()
    c.select_ids([a])
    btn["clear"].click()
    assert strip.isHidden() and len(panel.scene.selection) == 0


def test_the_select_hint_points_at_the_strip(panel):
    panel.controller.set_tool("V")
    panel.controller.leave(lane(panel))
    panel.controller.changed()
    assert "act on it below or with Del/N/⇧M" in panel.hintw.text()


def test_lane_menu_acts_on_the_hovered_track_when_nothing_is_selected(panel):
    ts = panel.ts
    a = ids_in_view(panel)[0]
    t, f, _ = point_of(panel, a)
    panel.controller.set_selection([])
    menu = panel.controller.context_menu(lane(panel), px(lane(panel), t, f), None)
    acts = {act.text(): act for act in menu.actions() if act.text()}
    assert not any(text.startswith("Merge selected") for text in acts)
    acts[f"Unassign track {int(a)}"].trigger()
    assert committed(panel) == 1 and len(ts.rows_of(a)) == 0
    # with a selection: the selection's actions, with reasons when disabled
    b = ids_in_view(panel)[0]
    panel.controller.select_ids([b])
    tb, fb, _ = point_of(panel, b)
    menu = panel.controller.context_menu(lane(panel), px(lane(panel), tb, fb), None)
    acts = {act.text().split("\t")[0]: act for act in menu.actions() if act.text()}
    assert acts["Selection: " + panel.controller.selection_summary()]
    assert "Clear selection" in acts
    assert not acts["Swap selected after the pointer"].isEnabled()
    assert "exactly two" in acts["Swap selected after the pointer"].toolTip()


def test_right_click_is_the_plugins_only_in_edit_mode(panel):
    surface = lane(panel)
    assert surface.acceptedMouseButtons() & Qt_right()
    panel.set_edit_mode(False)
    assert not surface.acceptedMouseButtons() & Qt_right()
    assert not surface.isVisible()


def Qt_right():  # noqa: N802 - reads as the flag it is
    from PySide6.QtCore import Qt

    return Qt.MouseButton.RightButton


# ------------------------------------------------------- add by ridge


def wait_ridge(p, seconds=10.0):
    end = time.monotonic() + seconds
    while p.ridge_source.busy and time.monotonic() < end:
        pump(0.02)
    pump(0.05)
    assert not p.ridge_source.busy, "the ridge search did not answer"


def test_ridge_add_restores_an_erased_stretch_of_a_track(panel):
    """Erase 2 s of a fish, then brush roughly along the gap from the
    track's end: the ridge puts the points back where the fish is, as one
    edit that extends the same id, and undo removes them."""
    ts = panel.ts
    c = panel.controller
    panel.ridgew.setChecked(True)
    v = lane(panel).view()
    mid = 0.5 * (v.x0 + v.x1)

    def spans(i):
        t = ts.times[ts.idx[ts.rows_of(i)]]
        return t.min() < mid - 1.5 and t.max() > mid + 1.5

    a = [i for i in ids_in_view(panel) if spans(i)][0]
    rows = ts.rows_of(a)
    t = ts.times[ts.idx[rows]]
    gap = rows[(t > mid - 1.0) & (t < mid + 1.0)]
    truth = dict(zip(ts.idx[gap].tolist(), ts.fund[gap].tolist()))
    c.set_selection(gap)
    c.unassign_selected()
    assert committed(panel) == 1
    before = rows[t <= mid - 1.0][-1]
    after = rows[t >= mid + 1.0][0]
    t0, f0 = float(ts.times[ts.idx[before]]), float(ts.fund[before])
    t1, f1 = float(ts.times[ts.idx[after]]), float(ts.fund[after])
    c.set_tool("F")
    assert "ridge" in c.tool.hint(panel.scene, None)
    n0 = ts.n
    # a sloppy stroke: straight from end to start, 1 Hz off
    pts = [
        (t0 + (t1 - t0) * s, f0 + (f1 - f0) * s + 1.0) for s in np.linspace(0, 1, 12)
    ]
    stroke(panel, pts)
    assert panel.scene.marks.add_pending, "the region is shown pending"
    wait_ridge(panel)
    assert committed(panel) == 2, panel.hintw.text()
    new = np.arange(n0, ts.n)
    assert (ts.ident[new] == a).all(), "extends the track the stroke started on"
    k = ts.idx[new]
    hits = [
        abs(ts.fund[r] - truth[int(kk)]) for r, kk in zip(new, k) if int(kk) in truth
    ]
    assert len(hits) >= 0.8 * len(truth)
    assert np.max(hits) < 1.0
    assert np.isfinite(ts.sign[new]).all(), "electrode power from the spectrum"
    info = panel.ridge_source.last_info
    assert info["total"] < 2.0
    panel.undo()
    assert ts.n == n0


def test_ridge_add_without_a_ridge_says_so_and_adds_nothing(panel):
    panel.ridgew.setChecked(True)
    panel.controller.set_tool("F")
    v = lane(panel).view()
    f = 700.0  # between the fish: noise only
    t0 = v.x0 + 1.0
    stroke(panel, [(t0, f), (t0 + 1.0, f)])
    wait_ridge(panel)
    assert committed(panel) == 0
    assert "no ridge found" in panel.hintw.text()
    assert "Ctrl+drag" in panel.hintw.text()


def test_esc_and_a_new_stroke_cancel_a_ridge_search(panel):
    ts = panel.ts
    panel.ridgew.setChecked(True)
    c = panel.controller
    c.set_tool("F")
    a = ids_in_view(panel)[0]
    t, f, _ = point_of(panel, a)
    n0 = ts.n
    stroke(panel, [(t, f + 1), (t + 1.0, f + 1)])
    assert panel.ridge_source.busy
    assert c.escape() == "gesture"
    assert not panel.ridge_source.busy and not panel.scene.marks.add_pending
    pump(1.0)
    assert ts.n == n0 and committed(panel) == 0
    # a second stroke supersedes the first search: one answer, one edit
    stroke(panel, [(t, f + 1), (t + 1.0, f + 1)])
    stroke(panel, [(t + 2.0, f + 1), (t + 3.0, f + 1)])
    wait_ridge(panel)
    assert committed(panel) <= 1


def test_ridge_option_is_remembered(panel):
    panel.ridgew.setChecked(False)
    assert panel.prefs["ridge_add"] is False and not panel.controller.ridge
    panel.ridgew.setChecked(True)
    assert panel.prefs["ridge_add"] is True and panel.controller.ridge


def test_ridge_source_follows_a_chirp_past_a_louder_neighbour(app, tmp_path):
    """The worker on a recording of its own: a chirp rising 600 -> 610 Hz
    and a constant tone at 616 Hz, three times louder; a brush along the
    chirp finds the chirp in every frame, never the tone."""
    soundfile = pytest.importorskip("soundfile")
    from audian_plugins.eodsorter import model as M
    from audian_plugins.eodsorter.ridgeadd import RidgeSource

    rate, dur, nfft = 8000, 8.0, 4096
    tt = np.arange(int(rate * dur)) / rate
    chirp = 600.0 + 10.0 * tt / dur
    sig = 0.05 * np.sin(2 * np.pi * np.cumsum(chirp) / rate)
    tone = 0.15 * np.sin(2 * np.pi * 616.0 * tt)
    noise = 0.005 * np.random.default_rng(3).standard_normal((len(tt), 2))
    audio = np.stack([sig + tone, 0.7 * sig + tone], axis=1) + noise
    wav = tmp_path / "chirp.wav"
    soundfile.write(wav, audio.astype(np.float32), rate)
    grid = M.FrameGrid.for_recording(rate, len(tt), nfft, 0.9)
    ts = M.TrackSet.empty(grid)

    class Host:
        def __init__(self):
            self.ts = ts

        def recording_paths(self):
            return [str(wav)]

        def _n_recording_channels(self):
            return 2

    src = RidgeSource(Host())
    try:
        times = ts.times
        frames = np.flatnonzero((times > 1.0) & (times < 7.0))
        centre = 600.0 + 10.0 * times[frames] / dur
        answers = []
        src.request(
            frames, centre - 4.0, centre + 4.0, lambda *a, **k: answers.append((a, k))
        )
        end = time.monotonic() + 10
        while not answers and time.monotonic() < end:
            pump(0.02)
        (freqs, sign, cplx), kw = answers[0]
        assert not kw.get("error")
        # the chirp ends 7 Hz (3.6 bins) below the louder tone, whose main
        # lobe then swallows it in the last few frames: those stay empty
        kept = np.isfinite(freqs)
        assert kept.mean() > 0.9
        assert np.abs(freqs[kept] - centre[kept]).max() < 0.5
        assert sign.shape == (len(frames), 2)
        assert src.last_info["total"] < 1.0
    finally:
        src.shutdown()
