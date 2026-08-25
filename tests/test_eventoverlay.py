"""Tests for :mod:`audian.eventoverlay`, the drawing half of annotations.

Runs offscreen::

    QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_eventoverlay.py -q

The point of these tests is the promises the drawing makes: that every
annotation is full height and bounded only in x, that a span's interior sits
under the trace while its edges sit over it, that the trace the reader is
working on never falls below its contrast floor inside a span, that a
predicted mark can never be drawn as if it had been observed, and that an
unvalidated fit can never be shown as if it were fine.  All of those are
properties of *pens, brushes, z values and geometry*, so they are checked
there rather than in a screenshot.
"""

from __future__ import annotations

import dataclasses
import os
import sys
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

import pyqtgraph as pg  # noqa: E402
from PyQt5.QtCore import Qt  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

from audian import eventoverlay, session, theme  # noqa: E402
from audian.layers import PointLayer  # noqa: E402
from audian.eventoverlay import (  # noqa: E402
    CAP_LIMIT,
    FILL_Z,
    MARK_Z,
    SPAN_FILL_ALPHA,
    SURFACE_NAVIGATOR,
    SURFACE_ORDER,
    SURFACE_SPECTROGRAM,
    SURFACE_TRACE,
    TRACE_Z,
    AnnotationLayer,
    EventOverlay,
)

sys.path.insert(0, str(REPO / "tests"))
from test_session import pulse, simple, trial, write_bundle  # noqa: E402

VOLLEY = session.LAYER_TRIALS_VOLLEY
BASELINE = session.LAYER_TRIALS_BASELINE
SILENCE = session.LAYER_TRIALS_SILENCE
RESTING = session.LAYER_PULSES_RESTING
PULSE_VOLLEY = session.LAYER_PULSES_VOLLEY
UNEXPLAINED = session.LAYER_DET_UNEXPLAINED
RUNS = session.LAYER_RUNS


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def bundle(tmp_path):
    """The small bundle from `test_session`, plus a predicted pulse."""
    return simple(
        tmp_path,
        pulses=[
            pulse(1.0),
            pulse(2.0, "baseline"),
            pulse(3.0, "volley"),
            pulse(3.5, "volley", detected_time_s=None, match_status="unmatched"),
        ],
    )


@pytest.fixture
def layer(bundle):
    annotations = AnnotationLayer()
    annotations.bundle = bundle
    annotations.layers = {x.id: True for x in bundle}
    return annotations


@pytest.fixture
def new_plot(app):
    """Make plots and keep them alive.

    A `PlotWidget` that nothing holds is collected between statements, and
    the next call into its view box raises "wrapped C/C++ object has been
    deleted" rather than failing the thing under test.
    """
    kept = []

    def make(**kwargs):
        widget, plot = make_plot(app, **kwargs)
        kept.append(widget)
        return plot

    yield make
    kept.clear()


def make_plot(app, xrange=(0.0, 8.0), yrange=(-1.0, 1.0)):
    widget = pg.PlotWidget()
    plot = widget.getPlotItem()
    plot.enableAutoRange(False, False)
    plot.getViewBox().setRange(
        xRange=xrange, yRange=yrange, padding=0, disableAutoRange=True
    )
    widget.resize(1000, 400)
    return widget, plot


def drawn_overlay(app, layer, surface=SURFACE_TRACE, **kwargs):
    widget, plot = make_plot(app, **kwargs)
    overlay = EventOverlay(plot, layer, surface)
    overlay.rebuild()
    overlay.update_plot()
    # the widget has to outlive the call or its view box is collected
    overlay._widget = widget
    return overlay


def spanned(overlay, layer_id):
    """``(fill x, fill y, edge x, edge y)`` of one span layer."""
    fx, fy = overlay.fills[layer_id].getData()
    ex, ey = overlay.edges[layer_id].getData()
    return fx, fy, ex, ey


# --- toggles ----------------------------------------------------------------


def test_every_layer_of_the_bundle_gets_its_own_switch(tmp_path):
    annotations = AnnotationLayer()
    annotations.load(simple(tmp_path).ref.metadata_path)
    assert set(annotations.layers) == {x.id for x in annotations.bundle}
    assert len(annotations.layers) == 10


def test_a_layer_starts_at_the_default_the_reader_states(tmp_path):
    """The reader owns the default; the overlay does not keep a second copy."""
    annotations = AnnotationLayer()
    bundle = annotations.load(simple(tmp_path).ref.metadata_path)
    for one in bundle:
        assert annotations.layers[one.id] == one.default_on


def test_the_localization_runs_start_off(tmp_path):
    """59% coverage: on with everything else they wash the whole overview."""
    annotations = AnnotationLayer()
    annotations.load(simple(tmp_path).ref.metadata_path)
    assert annotations.layers[RUNS] is False


def test_soloing_leaves_one_layer_on_and_costs_one_redraw(layer):
    before = layer.revision
    layer.solo(VOLLEY)
    assert layer.active_ids() == [VOLLEY]
    assert layer.revision == before + 1, "a solo must not redraw once per layer"
    layer.show_all()
    assert len(layer.active_ids()) == 10


def test_the_master_toggle_overrides_every_layer(layer):
    layer.set_visible(False)
    assert layer.active_ids() == []
    layer.set_visible(True)
    assert len(layer.active_ids()) == 10


# --- the shared window cache ------------------------------------------------


def test_the_window_is_computed_once_for_the_whole_stack(layer):
    """32 plots showing the same range must not do the same search 32 times.

    A cache hit hands back the *same* array object, which is also what makes
    the per-plot cost of a redraw independent of the number of channels.
    """
    first = layer.point_window(RESTING, 0, 0.0, 8.0, 1000)[0]
    for _ in range(32):
        assert layer.point_window(RESTING, 0, 0.0, 8.0, 1000)[0] is first
    assert layer.point_window(RESTING, 0, 0.001, 8.0, 1000)[0] is not first


def test_points_and_spans_share_one_cache_per_window(layer):
    """One dict per browser per window, not one per kind."""
    span = layer.span_window(VOLLEY, 0.0, 8.0, 1000)
    point = layer.point_window(RESTING, 0, 0.0, 8.0, 1000)
    assert layer.span_window(VOLLEY, 0.0, 8.0, 1000)[0] is span[0]
    assert layer.point_window(RESTING, 0, 0.0, 8.0, 1000)[0] is point[0]
    layer.span_window(VOLLEY, 1.0, 8.0, 1000)
    # moving the view drops both, not only the kind that asked
    assert layer.point_window(RESTING, 0, 1.0, 8.0, 1000)[0] is not point[0]


def test_a_point_window_returns_interleaved_pairs(layer):
    xpairs, drawn, total = layer.point_window(RESTING, 0, 0.0, 8.0, 1000)
    assert drawn == total == 2
    assert xpairs.tolist() == [1.0, 1.0, 2.0, 2.0]


def test_a_span_window_returns_bin_edges_and_edge_pairs(layer):
    fill_x, edge_x, bars, total = layer.span_window(SILENCE, 0.0, 8.0, 1000)
    assert bars == total == 1
    assert fill_x.tolist() == pytest.approx([5.0, 5.6])
    assert edge_x.tolist() == pytest.approx([5.0, 5.0, 5.6, 5.6])


# --- spans: the windowing cases ---------------------------------------------


def test_a_span_crossing_the_left_edge_only_is_still_drawn(app, layer):
    """The one case searchsorted on `starts` alone gets wrong."""
    overlay = drawn_overlay(app, layer, xrange=(5.3, 8.0))
    fx, _fy, ex, _ey = spanned(overlay, SILENCE)
    assert fx.tolist() == pytest.approx([5.0, 5.6])
    assert ex[0] == pytest.approx(5.0), "the start edge is off screen, not gone"


def test_a_span_crossing_the_right_edge_only_is_still_drawn(app, layer):
    overlay = drawn_overlay(app, layer, xrange=(0.0, 5.3))
    fx, _fy, _ex, _ey = spanned(overlay, SILENCE)
    assert fx.tolist() == pytest.approx([5.0, 5.6])


def test_a_span_that_contains_the_whole_view_covers_it(app, layer):
    overlay = drawn_overlay(app, layer, xrange=(5.2, 5.4))
    fx, fy, _ex, _ey = spanned(overlay, SILENCE)
    assert fx[0] < 5.2 and fx[-1] > 5.4
    # and the interior really is filled across the view, not empty
    assert fy.size == 1


def test_a_zero_length_span_still_draws_one_device_pixel(app, tmp_path):
    """A trial whose end equals its start is a row, not a nothing."""
    bundle = simple(tmp_path, trials=[trial(1, "silence", 4.0, 4.0, 0)])
    annotations = AnnotationLayer()
    annotations.bundle = bundle
    annotations.layers = {x.id: True for x in bundle}
    overlay = drawn_overlay(app, annotations, xrange=(0.0, 8.0))
    fx, _fy, ex, _ey = spanned(overlay, SILENCE)
    assert fx[0] == pytest.approx(4.0)
    assert fx[1] > fx[0], "a zero-length span vanished instead of widening"
    assert ex.size == 4


def test_adjacent_spans_merge_at_the_pixel_floor(app, tmp_path):
    """Two trials a millisecond apart share a pixel column at a 600 s view.

    They merge into one bar rather than one of them being dropped: dropping
    loses a region of time, and at the whole-file view a merge still covers
    everywhere a trial was running.
    """
    bundle = simple(
        tmp_path,
        trials=[
            trial(1, "silence", 100.0, 100.4, 0),
            trial(2, "silence", 100.405, 100.8, 0),
        ],
    )
    annotations = AnnotationLayer()
    annotations.bundle = bundle
    annotations.layers = {x.id: True for x in bundle}
    fill_x, edge_x, bars, total = annotations.span_window(SILENCE, 0.0, 600.0, 1000)
    assert bars == 1, "two spans inside one pixel column must fuse"
    assert total == 2, "the badge must still report the true count"
    assert fill_x.tolist() == pytest.approx([100.0, 100.8])
    assert edge_x.size == 4
    # zoomed in, where the gap is wider than a pixel, they are two bars again
    assert annotations.span_window(SILENCE, 100.0, 101.0, 1000)[2] == 2


def test_the_silence_control_survives_a_whole_file_window(app, tmp_path):
    """The control condition must not disappear from the overview.

    Twelve short silence trials over a ten minute file: a keep-first
    decimation drops most of them, which is exactly the reading that makes an
    experiment unreadable.
    """
    trials = [
        trial(i, "silence", 10.0 + 50.0 * i, 10.5 + 50.0 * i, 0) for i in range(12)
    ]
    bundle = simple(tmp_path, trials=trials)
    annotations = AnnotationLayer()
    annotations.bundle = bundle
    annotations.layers = {x.id: True for x in bundle}
    _fx, _ex, bars, total = annotations.span_window(SILENCE, 0.0, 607.104, 1400)
    assert bars == total == 12


def test_a_span_layer_out_of_view_draws_nothing(app, layer):
    overlay = drawn_overlay(app, layer, xrange=(0.0, 1.0))
    fx, _fy, ex, _ey = spanned(overlay, SILENCE)
    assert fx.size <= 1 and ex.size == 0


# --- spans: the geometry ----------------------------------------------------


def test_a_span_fills_the_full_height_of_the_lane(app, layer):
    overlay = drawn_overlay(app, layer, yrange=(-2.0, 3.0))
    _fx, fy, _ex, ey = spanned(overlay, SILENCE)
    assert fy.max() == pytest.approx(3.0)
    assert overlay.fills[SILENCE].opts["fillLevel"] == pytest.approx(-2.0)
    assert ey.min() == pytest.approx(-2.0)
    assert ey.max() == pytest.approx(3.0)


def test_the_gap_between_two_spans_encloses_no_fill(app, tmp_path):
    """A step curve whose gap bins sit above the fill level paints them too."""
    bundle = simple(
        tmp_path,
        trials=[trial(1, "silence", 1.0, 2.0, 0), trial(2, "silence", 5.0, 6.0, 0)],
    )
    annotations = AnnotationLayer()
    annotations.bundle = bundle
    annotations.layers = {x.id: True for x in bundle}
    overlay = drawn_overlay(app, annotations, yrange=(0.0, 1.0))
    fx, fy, _ex, _ey = spanned(overlay, SILENCE)
    assert fx.tolist() == pytest.approx([1.0, 2.0, 5.0, 6.0])
    assert fy.tolist() == pytest.approx([1.0, 0.0, 1.0])


def test_a_span_fill_is_never_stroked(app, layer):
    """A pen on a step curve draws its baseline across every gap.

    The result is a horizontal rule at the lane's floor that reads as a grid
    line nobody added, and it is there whether or not a span is in view.
    """
    overlay = drawn_overlay(app, layer)
    assert overlay.fills[SILENCE].opts["pen"] is None


def test_the_fill_sits_under_the_trace_and_the_edges_over_it(app, layer):
    """The measurement in SPAN_FILL_ALPHA only holds for a fill under the data."""
    overlay = drawn_overlay(app, layer)
    assert overlay.fills[SILENCE].zValue() < TRACE_Z
    assert overlay.edges[SILENCE].zValue() > TRACE_Z
    assert FILL_Z < TRACE_Z < MARK_Z


def test_a_trace_item_really_sits_at_the_z_the_overlay_assumes(app):
    """TRACE_Z is a claim about pyqtgraph, so it is checked against pyqtgraph."""
    widget, plot = make_plot(app)
    item = pg.PlotDataItem([0.0, 1.0], [0.0, 1.0])
    plot.addItem(item)
    assert item.zValue() == TRACE_Z


def test_one_fill_item_and_one_edge_item_per_span_layer(app, layer):
    """Never one item with `brushes=`: it kills pyqtgraph 0.14's fast path."""
    overlay = drawn_overlay(app, layer)
    assert set(overlay.fills) == {VOLLEY, BASELINE, SILENCE, RUNS}
    assert set(overlay.edges) == set(overlay.fills)
    for item in overlay.fills.values():
        assert "brushes" not in item.opts
        assert item.opts["stepMode"] == "center"
        assert item.opts["skipFiniteCheck"] is True
        assert item.opts["antialias"] is False
        # 'all', pyqtgraph's default: the connect-array form renders the same
        # pixels at 8967 ms against 7.5 ms, and its docstring is off by one
        assert item.opts["connect"] == "all"


# --- points -----------------------------------------------------------------


def test_a_point_spans_the_full_height_of_the_lane(app, layer):
    overlay = drawn_overlay(app, layer, yrange=(0.0, 1.0))
    x, y = overlay.marks[(RESTING, 0)].getData()
    assert x.tolist() == [1.0, 1.0, 2.0, 2.0]
    assert sorted(set(np.round(y, 6))) == [0.0, 1.0]


def test_a_predicted_point_is_full_height_dashed_and_capped(app, layer):
    """Full height, because the rule forbids a per-type y allocation.

    The four differences that remain are the dash, the hollow cap, the
    absence of an answering detection, and the closing clause of `describe`.
    Never the hue: a predicted volley pulse in another colour would read as
    another stimulus.
    """
    overlay = drawn_overlay(app, layer, yrange=(0.0, 1.0))
    observed = overlay.marks[(PULSE_VOLLEY, 0)]
    predicted = overlay.marks[(PULSE_VOLLEY, 1)]
    assert sorted(set(np.round(predicted.getData()[1], 6))) == [0.0, 1.0]
    assert observed.opts["pen"].style() == Qt.SolidLine
    assert predicted.opts["pen"].style() != Qt.SolidLine
    assert predicted.opts["pen"].color().name() == observed.opts["pen"].color().name()
    caps = overlay.caps[(PULSE_VOLLEY, 1)]
    assert caps.getData()[0].tolist() == [3.5]
    assert caps.opts["brush"].style() == Qt.NoBrush, (
        "a filled dot reads as a measurement"
    )


def test_a_cap_is_dropped_far_enough_to_clear_the_top_of_the_view_box(app, layer):
    """A cap centred on `y1` is halved by the view box and reads as a chevron.

    A `pxMode` scatter is centred on its data point, so half of an eight
    pixel diamond sitting exactly on the top edge is outside the box.  The
    glyph is the whole difference between predicted and observed, so half of
    it is not enough.  The inset is a pixel count, so it is checked as one.
    """
    overlay = drawn_overlay(app, layer, yrange=(0.0, 1.0))
    caps = overlay.caps[(PULSE_VOLLEY, 1)]
    y = float(caps.getData()[1][0])
    height = float(overlay.plot.getViewBox().height())
    assert 0.0 < y < 1.0
    drop_px = (1.0 - y) * height
    assert drop_px >= theme.S8 / 2.0, "the upper half of the diamond is clipped"


def test_the_cap_inset_stays_the_same_pixel_count_at_any_zoom(app, layer):
    """The inset clears a symbol measured in pixels, so it cannot be in data
    units: a lane zoomed to a hundredth of its range would drop the cap a
    hundredth as far and clip it again."""
    overlay = drawn_overlay(app, layer, yrange=(0.0, 1.0))
    height = float(overlay.plot.getViewBox().height())
    wide = (1.0 - overlay.cap_y(0.0, 1.0)) * height
    tight = (0.01 - overlay.cap_y(0.0, 0.01)) * height / 0.01
    assert wide == pytest.approx(tight)


def test_only_a_predicted_series_gets_a_cap(app, layer):
    overlay = drawn_overlay(app, layer)
    assert set(overlay.caps) == {(PULSE_VOLLEY, 1)}


def test_caps_are_dropped_once_they_would_merge_into_a_bar(app, tmp_path):
    n = 5 * CAP_LIMIT
    rows = [
        pulse(0.001 * i, "volley", detected_time_s=None, match_status="unmatched")
        for i in range(1, n + 1)
    ]
    bundle = simple(tmp_path, pulses=rows)
    annotations = AnnotationLayer()
    annotations.bundle = bundle
    annotations.layers = {x.id: True for x in bundle}
    overlay = drawn_overlay(app, annotations, xrange=(0.0, n * 0.001))
    # series 0 is always the observed one, empty here; the predicted rows are 1
    drawn = overlay.marks[(PULSE_VOLLEY, 1)].getData()[0].size // 2
    assert drawn > CAP_LIMIT
    assert overlay.caps[(PULSE_VOLLEY, 1)].getData()[0].size == 0


def test_a_hundred_thousand_points_in_view_draw_a_bounded_number_of_lines(
    app, tmp_path
):
    n = 100_000
    times = np.linspace(0.1, 600.0, n)
    bundle = simple(tmp_path, pulses=[pulse(float(t)) for t in times])
    annotations = AnnotationLayer()
    annotations.bundle = bundle
    annotations.layers = {x.id: True for x in bundle}
    overlay = drawn_overlay(app, annotations, xrange=(0.0, 600.0))
    drawn = overlay.marks[(RESTING, 0)].getData()[0].size // 2
    assert drawn <= overlay.pixels() + 1
    assert drawn < n / 10


def test_a_rare_predicted_series_never_loses_its_pixel_to_a_common_one(app, tmp_path):
    """Separate arrays, so the decimation cannot drop one class for another."""
    rows = [pulse(0.1 + 0.001 * i, "volley") for i in range(2000)]
    rows.append(pulse(1.0005, "volley", detected_time_s=None, match_status="unmatched"))
    bundle = simple(tmp_path, pulses=rows)
    annotations = AnnotationLayer()
    annotations.bundle = bundle
    annotations.layers = {x.id: True for x in bundle}
    for pixels in (100, 300, 700, 1800):
        _x, drawn, total = annotations.point_window(PULSE_VOLLEY, 1, 0.0, 8.0, pixels)
        assert (drawn, total) == (1, 1), f"the predicted mark died at {pixels} px"


# --- what the drawing may not do --------------------------------------------


def test_a_switched_off_layer_draws_nothing(app, layer):
    overlay = drawn_overlay(app, layer)
    layer.set_layer(SILENCE, False)
    overlay.update_plot()
    fx, _fy, ex, _ey = spanned(overlay, SILENCE)
    assert fx.size <= 1 and ex.size == 0
    assert overlay.fills[VOLLEY].getData()[0].size > 1


def test_marks_follow_the_y_range(app, layer):
    overlay = drawn_overlay(app, layer, yrange=(0.0, 1.0))
    overlay.plot.getViewBox().setRange(
        yRange=(-4.0, 6.0), padding=0, disableAutoRange=True
    )
    overlay.update_plot()
    _x, y = overlay.marks[(RESTING, 0)].getData()
    assert y.min() == pytest.approx(-4.0)
    assert y.max() == pytest.approx(6.0)
    _fx, fy, _ex, ey = spanned(overlay, SILENCE)
    assert fy.max() == pytest.approx(6.0)
    assert ey.min() == pytest.approx(-4.0)


def test_an_unchanged_view_is_not_redrawn(app, layer):
    """A pan reaches every overlay twice; only the first one may cost anything."""
    overlay = drawn_overlay(app, layer)
    calls = []
    for item in list(overlay.marks.values()) + list(overlay.fills.values()):
        item.setData = lambda *a, **k: calls.append(a)
    overlay.update_plot()
    overlay.update_plot()
    assert calls == []
    layer.set_layer(SILENCE, False)
    overlay.update_plot()
    assert calls


def test_a_hidden_lane_is_never_redrawn(app, layer):
    """Hiding a channel in a sixteen channel stack has to buy its cost back."""
    overlay = drawn_overlay(app, layer)
    overlay.plot.setVisible(False)
    calls = []
    for item in overlay.marks.values():
        item.setData = lambda *a, **k: calls.append(a)
    layer.set_layer(SILENCE, False)
    overlay.update_plot()
    assert calls == []


def test_clearing_the_bundle_removes_every_item(app, layer):
    overlay = drawn_overlay(app, layer)
    layer.clear()
    overlay.clear()
    assert overlay.marks == {} and overlay.caps == {}
    assert overlay.fills == {} and overlay.edges == {}


def test_the_control_track_is_not_drawn_over_a_waveform(app, layer):
    """A held value is a staircase on its own axis, not a mark in a lane."""
    overlay = drawn_overlay(app, layer)
    assert session.LAYER_CONTROLS not in overlay.fills
    assert not any(k[0] == session.LAYER_CONTROLS for k in overlay.marks)
    # and it still has a switch, so a chip can say the layer exists
    assert session.LAYER_CONTROLS in layer.layers


# --- colour ------------------------------------------------------------------


def test_a_role_is_resolved_per_series_never_per_layer(app, tmp_path):
    """An explained detection takes the hue of the pulse that explains it."""
    bundle = simple(tmp_path)
    annotations = AnnotationLayer()
    annotations.bundle = bundle
    explained = bundle[session.LAYER_DET_EXPLAINED]
    roles = {annotations.role(explained, i) for i in range(len(explained.series))}
    assert roles == {"resting"}
    # The layer's role is the fallback and nothing else.  Checked by
    # contradicting it rather than by pinning whatever `session` sets it to --
    # that value answers a different question (which ink the chip is drawn
    # in), and pinning it here made this test fail for a reason that had
    # nothing to do with how a role is resolved.
    stated = dataclasses.replace(explained.series[0], role="silence")
    inherited = dataclasses.replace(explained.series[0], role=None)
    contrarian = PointLayer(
        "test.roles",
        [stated, inherited],
        role="fault",
        label="x",
        short="x",
        micro="x",
        track=explained.track,
    )
    assert annotations.role(contrarian, 0) == "silence"
    assert annotations.role(contrarian, 1) == "fault"


def test_a_span_and_its_pulses_share_one_hue(layer):
    """The whole scheme rests on the load-time partition, so it is stated here."""
    assert layer.color(layer.bundle[VOLLEY]) == layer.color(layer.bundle[PULSE_VOLLEY])
    assert layer.color(layer.bundle[BASELINE]) == layer.color(layer.bundle[RESTING])
    assert layer.color(layer.bundle[SILENCE]) not in {
        layer.color(layer.bundle[VOLLEY]),
        layer.color(layer.bundle[BASELINE]),
    }


def test_pens_are_re_resolved_on_a_theme_switch(app, layer):
    overlay = drawn_overlay(app, layer)
    before = overlay.marks[(RESTING, 0)].opts["pen"].color().name()
    before_fill = overlay.fills[SILENCE].opts["brush"].color().name()
    theme.set_theme(theme.THEME_LIGHT)
    try:
        overlay.polish()
        assert overlay.marks[(RESTING, 0)].opts["pen"].color().name() != before
        assert overlay.fills[SILENCE].opts["brush"].color().name() != before_fill
    finally:
        theme.set_theme(theme.THEME_DARK)
        overlay.polish()


#: The grounds a lane is really painted on.  `databrowser.update_current_plot`
#: sets the focused channel's view box to `bg.lane` and leaves every other
#: lane at `bg.plot`, and the navigator is `bg.plot` too, so a fill has to be
#: audited against both and not just the one the constant was written for.
LANE_GROUNDS = ("bg.plot", "bg.lane")

#: How much contrast the fill is allowed to cost the worst painted trace.
#: Measured at the committed alphas: 0.41 in dark (3.09 -> 2.69 on `bg.plot`,
#: 2.81 -> 2.41 on `bg.lane`) and 0.44 in daylight (4.62 -> 4.18, 3.90 ->
#: 3.55).  Half a point leaves a little room and still fails a doubling.
MAX_FILL_COST = 0.5


def _worst_painted(theme_name, ground, alpha):
    """Worst contrast any painted trace gets on *ground* under the fill."""
    painted = theme.painted_trace_colors(theme_name)
    worst = None
    for role in sorted(eventoverlay.FILL_ROLES):
        tinted = theme.mix_colors(ground, theme.annotation_color(role), alpha).name()
        for color in painted.values():
            ratio = theme.contrast_ratio(color, tinted)
            if worst is None or ratio < worst:
                worst = ratio
    return worst


@pytest.mark.parametrize("theme_name,alpha", sorted(SPAN_FILL_ALPHA.items()))
@pytest.mark.parametrize("ground", LANE_GROUNDS)
def test_a_span_fill_costs_at_most_half_a_ratio_point_on_either_ground(
    app, theme_name, alpha, ground
):
    """What the span fill actually promises, on both grounds a lane is painted.

    Not a floor.  At alpha 0 -- with no annotation on screen at all -- the
    focused lane is already under its floor in both themes, because
    `theme.dim_color` clamps a receded trace against `bg.plot` while the lane
    it lands in is painted `bg.lane`.  That is a pre-existing defect outside
    this module and no fill alpha can undo it, so asserting a floor here
    would be asserting something the fill neither causes nor can deliver.

    What the fill is answerable for is its own cost, and that is what is
    checked: the worst painted trace loses less than half a contrast ratio to
    it, on either ground, in either theme.
    """
    previous = theme.current_theme()
    theme.set_theme(theme_name)
    try:
        bare = _worst_painted(theme_name, ground, 0.0)
        tinted = _worst_painted(theme_name, ground, alpha)
        assert bare - tinted <= MAX_FILL_COST, (
            f"{theme_name} fill at {alpha} on {ground} costs {bare - tinted:.3f}"
        )
    finally:
        theme.set_theme(previous)


@pytest.mark.parametrize("theme_name", sorted(SPAN_FILL_ALPHA))
def test_the_focused_lane_is_under_its_floor_before_any_fill_is_drawn(app, theme_name):
    """The pre-existing defect the fill is NOT allowed to be blamed for.

    `theme.dim_color` clamps a receded trace to the floor against `bg.plot`,
    but `databrowser` paints the focused lane `bg.lane`, which is lighter in
    dark and darker in daylight.  Measured with nothing drawn over it: 2.81
    against a floor of 3.0 in dark, 3.90 against 4.5 in daylight.

    Asserted here so the claim on `SPAN_FILL_ALPHA` cannot rot silently.  When
    this test starts failing the defect has been fixed somewhere in
    `theme`/`databrowser`, and that docstring's table has to be re-measured.
    """
    previous = theme.current_theme()
    theme.set_theme(theme_name)
    try:
        floor = theme.min_graphic_contrast()
        assert _worst_painted(theme_name, "bg.lane", 0.0) < floor
    finally:
        theme.set_theme(previous)


def test_only_span_layers_ever_paint_a_fill(layer):
    """`FILL_ROLES` is the scope of the contrast measurement, so it is pinned
    to the layers that actually paint one.  A new span layer in another role
    would be drawing ink nobody measured."""
    span_roles = {span.role for span in layer.bundle.spans()}
    assert span_roles <= eventoverlay.FILL_ROLES
    assert eventoverlay.FILL_ROLES <= set(theme.ANNOTATION_ROLES)


def test_the_navigator_fills_at_the_alpha_that_was_measured_for_its_ground(
    new_plot, layer
):
    """The navigator's view box is painted `bg.plot`, the very ground
    `SPAN_FILL_ALPHA` was measured against, so it gets the measured alpha and
    not a multiple of it."""
    trace = EventOverlay(new_plot(), layer, SURFACE_TRACE)
    nav = EventOverlay(new_plot(), layer, SURFACE_NAVIGATOR)
    assert nav.fill_scale == trace.fill_scale
    assert layer.fill_alpha(VOLLEY, SURFACE_NAVIGATOR) == pytest.approx(
        layer.fill_alpha(VOLLEY, SURFACE_TRACE)
    )


def test_the_spectrogram_builds_no_fill_item_at_all(new_plot, layer):
    """An opaque image at z=0 swallows a fill at FILL_Z, so there is none.

    Keeping the item would leave a constant, a brush and a `setData` per
    redraw describing a composite that never reaches a pixel.  The edges are
    above the image and still carry the span's extent there.
    """
    spec = EventOverlay(new_plot(), layer, SURFACE_SPECTROGRAM)
    spec.rebuild()
    spec.update_plot()
    assert spec.fill_scale == 0.0
    assert spec.fills == {}
    assert set(spec.edges) == {VOLLEY, BASELINE, SILENCE, RUNS}
    assert spec.edges[SILENCE].getData()[0].size > 0


def test_a_run_is_filled_more_weakly_than_a_trial(layer):
    """A calibration for one layer, not a category encoding: the edges match."""
    assert layer.fill_alpha(RUNS) < layer.fill_alpha(VOLLEY)
    assert layer.edge_pen(layer.bundle[RUNS]).widthF() == pytest.approx(
        layer.edge_pen(layer.bundle[VOLLEY]).widthF()
    )


# --- trust -------------------------------------------------------------------


def test_an_unvalidated_fit_breaks_every_pen_and_hatches_every_fill(app, tmp_path):
    bundle = simple(tmp_path, alignment={"validated": '"true"'})
    annotations = AnnotationLayer()
    annotations.bundle = bundle
    annotations.layers = {x.id: True for x in bundle}
    assert annotations.unvalidated
    overlay = drawn_overlay(app, annotations)
    for item in overlay.marks.values():
        assert item.opts["pen"].style() != Qt.SolidLine
    for item in overlay.edges.values():
        assert item.opts["pen"].style() != Qt.SolidLine
    for item in overlay.fills.values():
        assert item.opts["brush"].style() == Qt.BDiagPattern


def test_a_validated_bundle_badges_as_validated(layer):
    text, token, tip = layer.badge()
    assert text == "validated"
    assert token == "success"
    assert "scale" in tip


def test_an_unvalidated_bundle_badges_loudly(tmp_path):
    annotations = AnnotationLayer()
    annotations.load(simple(tmp_path, alignment={"validated": "1"}).ref.metadata_path)
    text, token, tip = annotations.badge()
    assert text == "UNVALIDATED"
    assert token == "danger"
    assert "validated" in tip


def test_a_bundle_with_no_validated_key_still_badges(tmp_path):
    annotations = AnnotationLayer()
    annotations.load(simple(tmp_path, alignment={"validated": None}).ref.metadata_path)
    assert annotations.badge()[0] == "UNVALIDATED"


def test_warnings_badge_without_claiming_failure(tmp_path):
    annotations = AnnotationLayer()
    annotations.load(
        simple(
            tmp_path, alignment={"fit_warnings": '["residual drift"]'}
        ).ref.metadata_path
    )
    text, token, tip = annotations.badge()
    assert text == "WARNINGS"
    assert token == "accent"
    assert "residual drift" in tip


def test_a_fit_from_another_recording_draws_nothing_at_all(app, tmp_path, monkeypatch):
    """Every mark would land somewhere plausible and wrong."""
    metadata = write_bundle(
        tmp_path / "b",
        alignment={"recording_file": '"SOMETHING_ELSE.wav"'},
        pulses=[pulse(1.0), pulse(2.0)],
        trials=[trial(1, "silence", 1.5, 2.5, 0)],
    )
    # tier 2 opens the file header; the name check alone is what is under test
    monkeypatch.setattr(
        session.SessionMeta,
        "check_recording",
        lambda self, path, info=None: session.RecordingCheck(
            name=False, problems=("fitted against another recording",)
        ),
    )
    annotations = AnnotationLayer()
    annotations.load(metadata, tmp_path / "rec.wav")
    assert annotations.recording_mismatch == "SOMETHING_ELSE.wav"
    assert annotations.badge()[0] == "WRONG RECORDING"
    assert annotations.active_ids() == []
    overlay = drawn_overlay(app, annotations)
    assert all(c.getData()[0].size == 0 for c in overlay.marks.values())
    assert all(c.getData()[0].size == 0 for c in overlay.edges.values())


# --- surfaces ---------------------------------------------------------------


def test_every_surface_starts_on(layer):
    assert [name for name, _label, on in layer.surface_states() if on] == list(
        SURFACE_ORDER
    )


def test_a_surface_can_be_switched_off_on_its_own(new_plot, layer):
    """Wanting marks on the trace is no reason to want them on the strip."""
    overlays = {
        surface: EventOverlay(new_plot(), layer, surface) for surface in SURFACE_ORDER
    }
    for overlay in overlays.values():
        overlay.rebuild()
        overlay.update_plot()

    def drawn(surface):
        return sum(c.getData()[0].size for c in overlays[surface].marks.values())

    assert all(drawn(s) > 0 for s in SURFACE_ORDER)
    layer.set_surface(SURFACE_NAVIGATOR, False)
    for overlay in overlays.values():
        overlay.update_plot()
    assert drawn(SURFACE_NAVIGATOR) == 0
    assert drawn(SURFACE_TRACE) > 0
    assert drawn(SURFACE_SPECTROGRAM) > 0


def test_a_navigator_mark_is_drawn_above_the_selection_region(new_plot, layer):
    """MARK_ALPHA promises full opacity, and on the navigator z buys it.

    The region marking the visible window sits at z=50 with a translucent
    brush, so a mark at the trace surface's z=15 is veiled exactly where the
    reader is looking -- inside the window they are working in.
    """
    nav = EventOverlay(new_plot(), layer, SURFACE_NAVIGATOR)
    nav.rebuild()
    items = list(nav.marks.values()) + list(nav.edges.values())
    assert items
    for item in items:
        assert item.zValue() > eventoverlay.NAV_REGION_Z
    trace = EventOverlay(new_plot(), layer, SURFACE_TRACE)
    trace.rebuild()
    # and the fill stays under the data on every surface that has one
    assert all(f.zValue() < TRACE_Z for f in nav.fills.values())
    assert trace.marks[(RESTING, 0)].zValue() == MARK_Z


def test_the_navigator_draws_no_caps(new_plot, layer):
    """A hollow diamond in an eight pixel band is a smudge, not a symbol."""
    overlay = EventOverlay(new_plot(), layer, SURFACE_NAVIGATOR)
    overlay.rebuild()
    assert overlay.caps == {}
    assert overlay.marks


def test_every_surface_draws_the_full_lane(new_plot, layer):
    """The rule holds on all three: bounded in x, never in y."""
    for surface in SURFACE_ORDER:
        overlay = EventOverlay(new_plot(yrange=(0.0, 1.0)), layer, surface)
        overlay.rebuild()
        overlay.update_plot()
        _x, y = overlay.marks[(RESTING, 0)].getData()
        assert sorted(set(np.round(y, 6))) == [0.0, 1.0]


# --- the mouse ---------------------------------------------------------------


def test_overlay_items_never_take_the_mouse(app, layer):
    """A rubber band drag that starts on an event must reach the view box.

    pyqtgraph hands every curve and scatter the full set of accepted mouse
    buttons, so an annotation lying under the pointer is an item the scene
    offers the press to first -- and starting a drag on an annotation is the
    most likely drag a reader makes.
    """
    overlay = drawn_overlay(app, layer)
    items = (
        list(overlay.marks.values())
        + list(overlay.caps.values())
        + list(overlay.fills.values())
        + list(overlay.edges.values())
    )
    assert items
    for item in items:
        assert item.acceptedMouseButtons() == Qt.NoButton
        assert not item.acceptHoverEvents()


# --- actions that must never block ------------------------------------------


def test_toggling_with_nothing_loaded_does_not_open_a_dialog():
    """A key bound to a toggle must not be able to raise a modal file chooser.

    `DataBrowser.toggle_annotations` is on F8.  Falling through to the file
    chooser when nothing is loaded made that key open a modal dialog, which
    is surprising from the keyboard and a hang for anything driving the
    application without a user in front of it.

    Exercised on the unbound method rather than on a real browser: building
    one needs a file, and what is under test is a two line decision.
    """
    from audian.databrowser import DataBrowser

    said = []

    class Stub:
        toggle_annotations = DataBrowser.toggle_annotations

        def __init__(self):
            self.annotations = AnnotationLayer()

        def notify(self, level, message):
            said.append(message)

        def open_annotations(self):
            pytest.fail("the toggle raised the file chooser")

    Stub().toggle_annotations()
    assert said and "Ctrl+Shift+A" in said[0]


# --- the parameter bar frame -------------------------------------------------


def test_equalize_regrows_a_group_whose_contents_changed(app):
    """The Annotations group gains a row of chips after its bar was built.

    `equalize` froze every frame at the height the bar had then, and measured
    before Qt had processed the new widgets, so the layer chips came back
    clipped to a four pixel sliver.  Both halves are checked here: the frames
    grow, and they grow by what the contents actually need.
    """
    from PyQt5.QtWidgets import QLabel
    from audian.databrowser import ParameterGroup

    groups = [ParameterGroup("A"), ParameterGroup("B")]
    for group in groups:
        group.add_row("row", "", QLabel("x"))
    ParameterGroup.equalize(groups)
    before = groups[0].body.height()

    tall = QLabel("tall")
    tall.setFixedHeight(4 * before)
    groups[1].add_row("tall", "", tall)
    ParameterGroup.equalize(groups)

    needed = groups[1].grid.totalSizeHint().height()
    assert groups[1].body.height() >= needed
    assert groups[0].body.height() == groups[1].body.height()
    assert groups[0].body.height() > before


# --- layout ------------------------------------------------------------------


def test_a_view_box_with_no_layout_yet_does_not_collapse_the_decimation(
    app, layer, monkeypatch
):
    """A two pixel wide view is not a pixel budget, it is a missing layout.

    Cutting the decimation to it would put a whole window of events on two
    lines and leave them there: nothing redraws until the range moves again.
    """
    widget, plot = make_plot(app)
    overlay = EventOverlay(plot, layer)
    monkeypatch.setattr(plot.getViewBox(), "width", lambda: 2)
    assert overlay.pixels() == eventoverlay.DEFAULT_PIXELS
    monkeypatch.setattr(plot.getViewBox(), "width", lambda: 900)
    assert overlay.pixels() >= 900


def test_the_legend_icons_are_drawn_for_every_kind():
    color = theme.annotation_color("volley")
    assert not eventoverlay.legend_icon(color, True).isNull()
    assert not eventoverlay.legend_icon(color, False).isNull()
    assert not eventoverlay.span_icon(color, 0.14).isNull()
    assert not eventoverlay.swatch_icon(color).isNull()


def _painted_rows(pixmap):
    """Rows of the icon's centre column that got any ink."""
    image = pixmap.toImage()
    x = eventoverlay.LEGEND_W // 2
    return [y for y in range(image.height()) if image.pixelColor(x, y).alpha() > 0]


def test_a_predicted_chip_is_as_tall_as_an_observed_one():
    """The chip is the only legend the marks have, so a short predicted line
    here would teach a stub the lane never draws -- and a per-kind y
    allocation is the one thing the drawing rule forbids."""
    color = theme.annotation_color("volley")
    observed = _painted_rows(eventoverlay._legend_pixmap(color, True, False))
    predicted = _painted_rows(eventoverlay._legend_pixmap(color, False, False))
    assert observed == list(range(eventoverlay.LEGEND_H))
    assert predicted[0] == 0, "a predicted mark is dashed and capped, never short"
    # not an exact bottom row: the dashed pen's last gap can land on the last
    # row or two, so what is checked is that the line runs the icon rather
    # than which phase of the dash it ends in
    assert max(predicted) >= 0.7 * (eventoverlay.LEGEND_H - 1)
