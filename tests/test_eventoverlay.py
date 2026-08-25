"""Tests for :mod:`audian.eventoverlay`, the drawing half of annotations.

Runs offscreen::

    QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_eventoverlay.py -q

The point of these tests is the two promises the feature makes: that an
unvalidated alignment can never be shown as if it were fine, and that a
predicted event can never be drawn as if it had been observed.  Both are
properties of the *pens and the geometry*, so they are checked there rather
than in a screenshot.
"""

from __future__ import annotations

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

from audian import eventoverlay, theme  # noqa: E402
from audian.eventoverlay import (  # noqa: E402
    CAP_LIMIT,
    MEASURED_SPAN,
    NAVIGATOR_MEASURED_SPAN,
    NAVIGATOR_PREDICTED_SPAN,
    PREDICTED_SPAN,
    SURFACE_NAVIGATOR,
    SURFACE_ORDER,
    SURFACE_SPECTROGRAM,
    SURFACE_TRACE,
    UNVALIDATED_ALPHA,
    AnnotationLayer,
    EventOverlay,
)

sys.path.insert(0, str(REPO / "tests"))
from test_events import write_alignment  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def alignment(tmp_path):
    rows = [
        "0,0,LOC,,0,1.0,0,1.0,-0.00001,matched",
        "1,0,LOC,,0,2.0,0,2.0,0.00001,matched",
        "2,0,LOC,,0,3.0,0,,,unmatched",
        "3,0,VOLLEY,7,0,4.0,0,4.0,0.00002,matched",
    ]
    return write_alignment(tmp_path / "alignment.csv", rows)


@pytest.fixture
def layer(alignment):
    layer = AnnotationLayer()
    layer.load(alignment, "REC.wav")
    return layer


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


def make_plot(app, xrange=(0.0, 5.0), yrange=(-1.0, 1.0)):
    widget = pg.PlotWidget()
    plot = widget.getPlotItem()
    plot.enableAutoRange(False, False)
    plot.getViewBox().setRange(
        xRange=xrange, yRange=yrange, padding=0, disableAutoRange=True
    )
    widget.resize(1000, 400)
    return widget, plot


# --- toggles ---------------------------------------------------------------


def test_a_class_is_shown_only_when_its_event_and_its_status_are_on(layer):
    assert layer.is_enabled(("LOC", "matched"))
    layer.set_event("LOC", False)
    assert not layer.is_enabled(("LOC", "matched"))
    assert layer.is_enabled(("VOLLEY", "matched"))
    layer.set_event("LOC", True)
    layer.set_status("matched", False)
    assert not layer.is_enabled(("LOC", "matched"))
    assert layer.is_enabled(("LOC", "unmatched"))


def test_the_master_toggle_overrides_every_class(layer):
    layer.set_visible(False)
    assert layer.active_keys() == []
    layer.set_visible(True)
    assert len(layer.active_keys()) == 3


def test_counts_are_reported_per_axis(layer):
    events = dict((e, n) for e, n, _c, _on in layer.event_counts())
    assert events == {"LOC": 3, "VOLLEY": 1}
    statuses = dict((s, n) for s, n, _m, _on in layer.status_counts())
    assert statuses == {"matched": 3, "unmatched": 1}


# --- the shared window cache ------------------------------------------------


def test_the_window_is_computed_once_for_the_whole_stack(layer):
    """32 plots showing the same range must not do the same search 32 times.

    A cache hit hands back the *same* array object, which is also what makes
    the per-plot cost of a redraw independent of the number of channels.
    """
    first = layer.window(("LOC", "matched"), 0.0, 5.0, 1000)[0]
    for _ in range(32):
        assert layer.window(("LOC", "matched"), 0.0, 5.0, 1000)[0] is first
    moved = layer.window(("LOC", "matched"), 0.001, 5.0, 1000)[0]
    assert moved is not first


def test_a_new_view_invalidates_the_cache(layer):
    layer.window(("LOC", "matched"), 0.0, 5.0, 1000)
    xpairs, drawn, total = layer.window(("LOC", "matched"), 1.5, 5.0, 1000)
    assert drawn == 1
    assert xpairs.tolist() == [2.0, 2.0]


def test_window_returns_interleaved_pairs(layer):
    xpairs, drawn, total = layer.window(("LOC", "matched"), 0.0, 5.0, 1000)
    assert drawn == 2
    assert xpairs.tolist() == [1.0, 1.0, 2.0, 2.0]


# --- pens -------------------------------------------------------------------


def test_observed_is_solid_and_predicted_is_dashed(layer):
    matched = layer.line_pen(layer.table[("LOC", "matched")], 1.0)
    predicted = layer.line_pen(layer.table[("LOC", "unmatched")], 1.0)
    assert matched.style() == Qt.SolidLine
    assert predicted.style() == Qt.DashLine


def test_an_unvalidated_fit_breaks_every_line_but_keeps_them_apart(tmp_path):
    """Both promises at once: degraded, and still distinguishable."""
    rows = [
        "0,0,LOC,,0,1.0,0,1.0,0,matched",
        "1,0,LOC,,0,3.0,0,,,unmatched",
    ]
    path = write_alignment(tmp_path / "a.csv", rows, {"validated": "0"})
    layer = AnnotationLayer()
    layer.load(path, "REC.wav")
    assert layer.unvalidated
    matched = layer.line_pen(layer.table[("LOC", "matched")], 1.0)
    predicted = layer.line_pen(layer.table[("LOC", "unmatched")], 1.0)
    assert matched.style() != Qt.SolidLine
    assert predicted.style() != Qt.SolidLine
    assert matched.style() != predicted.style()


def test_an_unvalidated_fit_is_drawn_fainter(alignment, tmp_path):
    ok = AnnotationLayer()
    ok.load(alignment, "REC.wav")
    bad_path = write_alignment(
        tmp_path / "bad.csv",
        ["0,0,LOC,,0,1.0,0,1.0,0,matched"],
        {"validated": "0"},
    )
    bad = AnnotationLayer()
    bad.load(bad_path, "REC.wav")
    a = ok.line_pen(ok.table[("LOC", "matched")], 1.0).color().alphaF()
    b = bad.line_pen(bad.table[("LOC", "matched")], 1.0).color().alphaF()
    assert b == pytest.approx(a * UNVALIDATED_ALPHA, abs=0.01)


# --- the badge --------------------------------------------------------------


def test_a_validated_file_badges_as_validated(layer):
    text, token, tip = layer.badge()
    assert text == "validated"
    assert token == "success"
    assert "scale" in tip


def test_an_unvalidated_file_badges_loudly(tmp_path):
    path = write_alignment(
        tmp_path / "a.csv", ["0,0,LOC,,0,1.0,0,1.0,0,matched"], {"validated": "0"}
    )
    layer = AnnotationLayer()
    layer.load(path, "REC.wav")
    text, token, tip = layer.badge()
    assert text == "UNVALIDATED"
    assert token == "danger"
    assert "validated=0" in tip


def test_a_file_with_no_validated_key_still_badges(tmp_path):
    path = write_alignment(tmp_path / "a.csv", ["0,0,LOC,,0,1.0,0,1.0,0,matched"])
    path.write_text(path.read_text().replace("#validated=1\n", ""))
    layer = AnnotationLayer()
    layer.load(path, "REC.wav")
    assert layer.badge()[0] == "UNVALIDATED"


def test_warnings_badge_without_claiming_failure(tmp_path):
    path = write_alignment(
        tmp_path / "a.csv",
        ["0,0,LOC,,0,1.0,0,1.0,0,matched"],
        {"fit_warnings": "residual drift"},
    )
    layer = AnnotationLayer()
    layer.load(path, "REC.wav")
    text, token, tip = layer.badge()
    assert text == "WARNINGS"
    assert token == "accent"
    assert "residual drift" in tip


def test_a_fit_from_another_recording_is_the_loudest_badge(alignment):
    layer = AnnotationLayer()
    layer.load(alignment, "/data/SOMETHING_ELSE.wav")
    assert layer.recording_mismatch == "REC.wav"
    text, token, _tip = layer.badge()
    assert text == "WRONG RECORDING"
    assert token == "danger"


# --- drawing ----------------------------------------------------------------


def test_one_curve_per_class_and_caps_only_for_predicted(app, layer):
    widget, plot = make_plot(app)
    overlay = EventOverlay(plot, layer)
    overlay.rebuild()
    assert set(overlay.curves) == set(layer.table.keys)
    assert set(overlay.caps) == {("LOC", "unmatched")}


def test_observed_lines_span_the_lane_and_predicted_ones_do_not(app, layer):
    widget, plot = make_plot(app, yrange=(0.0, 1.0))
    overlay = EventOverlay(plot, layer)
    overlay.rebuild()
    overlay.update_plot()

    x, y = overlay.curves[("LOC", "matched")].getData()
    assert x.tolist() == [1.0, 1.0, 2.0, 2.0]
    assert sorted(set(np.round(y, 6))) == list(MEASURED_SPAN)

    x, y = overlay.curves[("LOC", "unmatched")].getData()
    assert x.tolist() == [3.0, 3.0]
    assert sorted(set(np.round(y, 6))) == list(PREDICTED_SPAN)
    # a predicted stub must not reach the lane's floor
    assert y.min() > MEASURED_SPAN[0]


def test_predicted_caps_sit_at_the_top_of_the_stub(app, layer):
    widget, plot = make_plot(app, yrange=(0.0, 1.0))
    overlay = EventOverlay(plot, layer)
    overlay.rebuild()
    overlay.update_plot()
    spots = overlay.caps[("LOC", "unmatched")].getData()
    assert spots[0].tolist() == [3.0]
    assert spots[1].tolist() == pytest.approx([PREDICTED_SPAN[1]])


def test_a_switched_off_class_draws_nothing(app, layer):
    widget, plot = make_plot(app)
    overlay = EventOverlay(plot, layer)
    overlay.rebuild()
    layer.set_event("LOC", False)
    overlay.update_plot()
    assert overlay.curves[("LOC", "matched")].getData()[0].size == 0
    assert overlay.curves[("VOLLEY", "matched")].getData()[0].size > 0


def test_lines_follow_the_y_range(app, layer):
    widget, plot = make_plot(app, yrange=(0.0, 1.0))
    overlay = EventOverlay(plot, layer)
    overlay.rebuild()
    overlay.update_plot()
    plot.getViewBox().setRange(yRange=(-4.0, 6.0), padding=0, disableAutoRange=True)
    overlay.update_plot()
    _x, y = overlay.curves[("LOC", "matched")].getData()
    assert y.min() == pytest.approx(-4.0)
    assert y.max() == pytest.approx(6.0)


def test_events_outside_the_view_are_never_drawn(app, layer):
    widget, plot = make_plot(app, xrange=(0.0, 1.5))
    overlay = EventOverlay(plot, layer)
    overlay.rebuild()
    overlay.update_plot()
    assert overlay.curves[("LOC", "matched")].getData()[0].tolist() == [1.0, 1.0]
    assert overlay.curves[("VOLLEY", "matched")].getData()[0].size == 0


def test_an_unchanged_view_is_not_redrawn(app, layer):
    """A pan reaches every overlay twice; only the first one may cost anything."""
    widget, plot = make_plot(app)
    overlay = EventOverlay(plot, layer)
    overlay.rebuild()
    overlay.update_plot()
    calls = []
    for curve in overlay.curves.values():
        curve.setData = lambda *a, **k: calls.append(a)
    overlay.update_plot()
    overlay.update_plot()
    assert calls == []
    # but a real change still gets through
    layer.set_event("LOC", False)
    overlay.update_plot()
    assert calls


def test_clearing_the_table_removes_every_item(app, layer):
    widget, plot = make_plot(app)
    overlay = EventOverlay(plot, layer)
    overlay.rebuild()
    layer.clear()
    overlay.clear()
    assert overlay.curves == {}
    assert overlay.caps == {}


# --- scale ------------------------------------------------------------------


def test_a_hundred_thousand_events_in_view_draw_a_bounded_number_of_lines(
    app, tmp_path
):
    n = 100_000
    times = np.linspace(0.0, 600.0, n)
    rows = [f"{i},0,LOC,,0,{t:.6f},0,{t:.6f},0,matched" for i, t in enumerate(times)]
    path = write_alignment(tmp_path / "big.csv", rows)
    layer = AnnotationLayer()
    layer.load(path, "REC.wav")

    widget, plot = make_plot(app, xrange=(0.0, 600.0))
    overlay = EventOverlay(plot, layer)
    overlay.rebuild()
    overlay.update_plot()
    drawn = overlay.curves[("LOC", "matched")].getData()[0].size // 2
    pixels = overlay.pixels()
    assert drawn <= pixels + 1
    assert drawn < n / 10


def test_caps_are_dropped_once_they_would_merge_into_a_bar(app, tmp_path):
    n = 5 * CAP_LIMIT
    rows = [f"{i},0,LOC,,0,{i * 0.001:.6f},0,,,unmatched" for i in range(n)]
    path = write_alignment(tmp_path / "big.csv", rows)
    layer = AnnotationLayer()
    layer.load(path, "REC.wav")
    widget, plot = make_plot(app, xrange=(0.0, n * 0.001))
    overlay = EventOverlay(plot, layer)
    overlay.rebuild()
    overlay.update_plot()
    drawn = overlay.curves[("LOC", "unmatched")].getData()[0].size // 2
    caps = overlay.caps[("LOC", "unmatched")].getData()[0]
    assert drawn > CAP_LIMIT
    assert len(caps) == 0


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
        return sum(c.getData()[0].size for c in overlays[surface].curves.values())

    assert all(drawn(s) > 0 for s in SURFACE_ORDER)
    layer.set_surface(SURFACE_NAVIGATOR, False)
    for overlay in overlays.values():
        overlay.update_plot()
    assert drawn(SURFACE_NAVIGATOR) == 0
    assert drawn(SURFACE_TRACE) > 0
    assert drawn(SURFACE_SPECTROGRAM) > 0


def test_the_master_switch_overrides_every_surface(new_plot, layer):
    overlay = EventOverlay(new_plot(), layer, SURFACE_NAVIGATOR)
    overlay.rebuild()
    overlay.update_plot()
    assert layer.surface_enabled(SURFACE_NAVIGATOR)
    layer.set_visible(False)
    assert not layer.surface_enabled(SURFACE_NAVIGATOR)
    overlay.update_plot()
    assert all(c.getData()[0].size == 0 for c in overlay.curves.values())


def test_the_navigator_keeps_observed_and_predicted_in_separate_stripes(app, layer):
    """At sixty pixels a row, length and dashes both vanish; position does not.

    A stretch of session where the fit predicted pulses and nothing was ever
    found has to be visible as a lower stripe with nothing above it, so the
    two bands must not overlap.
    """
    widget, plot = make_plot(app, yrange=(0.0, 1.0))
    overlay = EventOverlay(plot, layer, SURFACE_NAVIGATOR)
    overlay.rebuild()
    overlay.update_plot()

    _x, measured = overlay.curves[("LOC", "matched")].getData()
    _x, predicted = overlay.curves[("LOC", "unmatched")].getData()
    assert sorted(set(np.round(measured, 6))) == list(NAVIGATOR_MEASURED_SPAN)
    assert sorted(set(np.round(predicted, 6))) == list(NAVIGATOR_PREDICTED_SPAN)
    assert predicted.max() < measured.min(), "the two stripes overlap"
    # and the observed one reaches the top edge of the row
    assert measured.max() == pytest.approx(1.0)


def test_the_navigator_draws_no_caps(new_plot, layer):
    """A hollow diamond in an eight pixel band is a smudge, not a symbol."""
    overlay = EventOverlay(new_plot(), layer, SURFACE_NAVIGATOR)
    overlay.rebuild()
    assert overlay.caps == {}
    assert set(overlay.curves) == set(layer.table.keys)


def test_the_lane_surfaces_still_draw_down_the_lane(new_plot, layer):
    for surface in (SURFACE_TRACE, SURFACE_SPECTROGRAM):
        overlay = EventOverlay(new_plot(), layer, surface)
        assert overlay.spans == (MEASURED_SPAN, PREDICTED_SPAN)
        assert overlay.wants_caps


def test_a_spectrogram_mark_is_more_opaque_than_a_trace_mark(new_plot, layer):
    """It competes with an image rather than with an empty ground."""
    trace = EventOverlay(new_plot(), layer, SURFACE_TRACE)
    spec = EventOverlay(new_plot(), layer, SURFACE_SPECTROGRAM)
    assert spec.alpha > trace.alpha


# --- the mouse ---------------------------------------------------------------


def test_overlay_items_never_take_the_mouse(app, layer):
    """A rubber band drag that starts on an event must reach the view box.

    pyqtgraph hands every curve and scatter the full set of accepted mouse
    buttons, so an annotation lying under the pointer is an item the scene
    offers the press to first -- and starting a drag on an event is the most
    likely drag a reader makes.
    """
    widget, plot = make_plot(app)
    overlay = EventOverlay(plot, layer)
    overlay.rebuild()
    for item in list(overlay.curves.values()) + list(overlay.caps.values()):
        assert item.acceptedMouseButtons() == Qt.NoButton
        assert not item.acceptHoverEvents()


# --- actions that must never block ------------------------------------------


def test_toggling_with_nothing_loaded_does_not_open_a_dialog():
    """A key bound to a toggle must not be able to raise a modal file chooser.

    `DataBrowser.toggle_annotations` is on F8.  Falling through to the file
    chooser when no table is loaded made that key open a modal dialog, which
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
    before Qt had processed the new widgets, so the class chips came back
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


# --- theme ------------------------------------------------------------------


def test_pens_are_re_resolved_on_a_theme_switch(app, layer):
    widget, plot = make_plot(app)
    overlay = EventOverlay(plot, layer)
    overlay.rebuild()
    before = overlay.curves[("LOC", "matched")].opts["pen"].color().name()
    theme.set_theme(theme.THEME_LIGHT)
    try:
        overlay.polish()
        after = overlay.curves[("LOC", "matched")].opts["pen"].color().name()
        assert after != before
    finally:
        theme.set_theme(theme.THEME_DARK)
        overlay.polish()


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


def test_the_legend_icon_is_drawn_for_both_kinds():
    solid = eventoverlay.legend_icon("#FF6B6B", True)
    stub = eventoverlay.legend_icon("#FF6B6B", False)
    assert not solid.isNull()
    assert not stub.isNull()
    assert not eventoverlay.swatch_icon("#FF6B6B").isNull()
