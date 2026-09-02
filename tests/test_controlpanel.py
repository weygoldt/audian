"""Tests for the control track panel.

Runs offscreen::

    QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_controlpanel.py -q

The control track is the one part of a session bundle that is *not* an
annotation: it is a value held between change rows, so it is drawn as a step
plot in a panel of its own rather than over a waveform.  What this file pins
down is what that panel promises:

* it is off by default and costs nothing while it is off;
* it holds a value forward across a gap of minutes, so a window far from the
  last change row shows what was actually in force there;
* a gap is a gap and never a zero;
* it shares the lanes' time axis and does not disturb the lane geometry.

Most of it runs against a bare `ControlPanel` with an `AnnotationLayer` and no
browser at all.  The last three build the whole application on a small
synthetic recording, because "still aligned with the lanes" is a claim about
three widgets in one layout and nothing smaller can make it.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QPainterPath  # noqa: E402
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget  # noqa: E402

from audian import theme  # noqa: E402
from audian.controlpanel import ControlPanel  # noqa: E402
from audian.eventoverlay import AnnotationLayer  # noqa: E402
from audian.layers import LAYER_CONTROLS  # noqa: E402

sys.path.insert(0, str(REPO / "tests"))
from test_session import simple, write_bundle  # noqa: E402


RAIL_WIDTH = 48


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class Host(QWidget):
    """A shown parent for the panel.

    The panel reads `isVisible()` to decide whether drawing is worth doing,
    and a widget whose parent was never shown is never visible however many
    times it is switched on.  It is also what keeps the test from creating a
    parentless top level window, which a tiling compositor would place.
    """

    def __init__(self, panel):
        super().__init__()
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.addWidget(panel)
        self.resize(1200, 200)
        self.show()


@pytest.fixture
def make_panel(app):
    """Build panels, and take them down again before the test ends.

    A `Host` left alive to the end of the session is a pyqtgraph scene torn
    down by the interpreter's exit rather than by Qt, and mixing those with
    the whole-application fixture below segfaults the run.  Everything built
    here is closed and deleted while the event loop is still turning.
    """
    hosts = []

    def build(bundle_path):
        layer = AnnotationLayer()
        panel = ControlPanel(layer, RAIL_WIDTH)
        host = Host(panel)
        hosts.append(host)
        if bundle_path is not None:
            layer.load(bundle_path, Path("rec.wav"))
        panel.rebuild()
        panel.refresh()
        app.processEvents()
        return panel

    yield build
    for host in hosts:
        host.close()
        host.deleteLater()
    app.processEvents()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def switch_on(app, panel):
    panel.layer.set_layer(LAYER_CONTROLS, True)
    panel.refresh()
    app.processEvents()


def control_row(t, **values):
    row = {"time_s": t - 28.9, "recording_time_s": t, "volley_amplitude": 1.0}
    row.update(values)
    return row


@pytest.fixture
def panel(make_panel, tmp_path):
    """The small bundle: two control rows, tick_hz and randomness offered."""
    return make_panel(simple(tmp_path / "bundle").ref.metadata_path)


# --- off by default ---------------------------------------------------------


def test_the_panel_is_off_by_default_and_asks_for_no_pixels(panel):
    assert panel.layer.is_enabled(LAYER_CONTROLS) is False
    assert panel.wanted_height() == 0
    assert panel.isVisible() is False


def test_switching_the_control_layer_on_is_what_gives_the_panel_its_height(app, panel):
    switch_on(app, panel)
    assert panel.isVisible() is True
    assert panel.wanted_height() == panel.total
    assert panel.height() == panel.total


def test_switching_it_off_again_returns_every_pixel(app, panel):
    switch_on(app, panel)
    assert panel.wanted_height() > 0
    panel.layer.set_layer(LAYER_CONTROLS, False)
    panel.refresh()
    app.processEvents()
    assert panel.wanted_height() == 0
    assert panel.isVisible() is False
    assert panel.height() == 0


def test_hiding_every_annotation_hides_the_panel_too(app, panel):
    # F8 is a switch over the whole bundle, and the control track is part of
    # the bundle even though it is not drawn over a lane
    switch_on(app, panel)
    panel.layer.set_visible(False)
    panel.refresh()
    assert panel.wanted_height() == 0


def test_a_bundle_with_no_controls_csv_leaves_the_panel_with_no_track(
    make_panel, tmp_path
):
    path = write_bundle(tmp_path / "thin", session_id="THIN", controls=None)
    panel = make_panel(path)
    assert panel.track is None
    assert panel.names == ()
    # not the same state as "switched off": there is nothing to switch on
    panel.layer.set_layer(LAYER_CONTROLS, True)
    assert panel.wanted_height() == 0


# --- the bands --------------------------------------------------------------


def test_every_offered_channel_gets_exactly_one_band(panel):
    assert panel.names == ("tick_hz", "randomness")
    assert set(panel.curves) == set(panel.names)
    assert set(panel.rules) == set(panel.names)
    assert set(panel.labels) == set(panel.names)


def test_the_bands_stack_without_overlapping_and_leave_the_caption_row_free(
    panel,
):
    bands = [panel.band(name) for name in panel.names]
    for bottom, top in bands:
        assert top - bottom == theme.CONTROL_BAND_H
    # first channel on top, and each band's floor is the next one's ceiling
    for (lower_bottom, lower_top), (upper_bottom, _) in zip(bands[1:], bands):
        assert lower_top == upper_bottom
        assert lower_bottom < lower_top
    assert bands[-1][0] == theme.CONTROL_NOTE_H
    assert bands[0][1] == panel.total


def test_the_panel_is_exactly_as_tall_as_its_bands_and_its_caption(panel):
    assert panel.total == (
        len(panel.names) * theme.CONTROL_BAND_H + theme.CONTROL_NOTE_H
    )


def test_a_channel_the_loader_withheld_says_why_instead_of_vanishing(panel):
    # volley_amplitude is 1.0 on every row of the bundle, so it gets no band
    assert "volley_amplitude" not in panel.names
    assert "volley_amplitude" in panel.note.toPlainText()
    assert "volley_amplitude" in panel.toolTip()


def test_a_band_label_carries_the_real_numbers_of_its_range(panel):
    text = panel.labels["tick_hz"].toPlainText()
    low, high = panel.track.ranges["tick_hz"]
    assert "tick_hz" in text
    assert f"{low:g}" in text and f"{high:g}" in text
    assert "Hz" in text


def test_the_scale_label_never_hides_the_value_held_at_the_left_edge(
    app, make_panel, tmp_path
):
    """The label is chrome and the staircase is data, so the data is on top.

    Painted over the curve, the label's opaque fill covered the leftmost
    ~148 px of a 1400 px band -- exactly the held value `window_steps` goes
    out of its way to reconstruct by reaching one row backwards, and at the
    100-160 s window of exp2 the change at 100.1999 s is 7 px into the view
    and was entirely under it.  Measured here at the label's own rect: 91 of
    its 132 columns carried no curve at all with the label on top, none with
    it underneath.  The fill is the plot's own ground, so a step drawn over
    it reads exactly as it does anywhere else in the band.
    """
    QColor = pytest.importorskip("PySide6.QtGui").QColor
    rows = [
        control_row(1.0, tick_hz=5.0, randomness=1.0),
        control_row(30.2, tick_hz=20.0, randomness=0.5),
    ]
    bundle = simple(
        tmp_path / "label",
        controls=rows,
        alignment={"recording_frames": "48000000"},
    )
    panel = make_panel(bundle.ref.metadata_path)
    switch_on(app, panel)
    panel.plot.setXRange(30.0, 90.0, padding=0)
    panel.update_plot()
    app.processEvents()

    label = panel.labels["tick_hz"]
    assert label.zValue() < panel.curves["tick_hz"].zValue()
    image = panel.fig.grab().toImage()
    ink = QColor(theme.annotation_color("control")).rgb()
    rect = label.mapRectToScene(label.boundingRect())
    blank = [
        x
        for x in range(int(rect.left()), int(rect.right()))
        if not any(image.pixel(x, y) == ink for y in range(image.height()))
    ]
    assert blank == []


# --- the step plot ----------------------------------------------------------


def test_the_value_held_at_the_left_edge_of_a_late_window_is_the_last_change(
    app, make_panel, tmp_path
):
    """The whole reason `window_steps` reaches one row backwards.

    A window 500 s after the last change row contains no control row at all.
    Drawn from the rows *inside* it the band would be empty, which reads as
    "the device stopped reporting"; what is true is that the last setting is
    still in force.
    """
    rows = [
        control_row(1.0, tick_hz=5.0, randomness=1.0),
        control_row(4.0, tick_hz=0.5, randomness=0.25),
    ]
    bundle = simple(
        tmp_path / "late",
        controls=rows,
        # 48e6 frames at 48 kHz: a 1000 s recording, so a window 500 s past
        # the last change row is still inside it
        alignment={"recording_frames": "48000000"},
    )
    panel = make_panel(bundle.ref.metadata_path)
    switch_on(app, panel)
    t0, t1 = 504.0, 505.0
    assert panel.track.t_end == pytest.approx(1000.0)
    assert t0 - float(panel.track.times[-1]) == pytest.approx(500.0)
    panel.plot.setXRange(t0, t1, padding=0)
    panel.update_plot()

    for name, held in (("tick_hz", 0.5), ("randomness", 0.25)):
        assert panel.track.value_at(name, t0) == held
        x, y = panel.curves[name].getData()
        assert x[0] == pytest.approx(t0)
        bottom, top = panel.band(name)
        low, high = panel.track.ranges[name]
        floor = bottom + theme.CONTROL_BAND_PAD
        ceiling = top - theme.CONTROL_BAND_PAD
        want = floor + (held - low) * (ceiling - floor) / (high - low)
        assert float(y[0]) == pytest.approx(want)
        # and it is still that value at the right edge: one flat segment
        assert np.allclose(y, want)


def test_the_track_is_a_staircase_and_never_interpolates(app, make_panel, tmp_path):
    rows = [
        control_row(1.0, tick_hz=5.0, randomness=1.0),
        control_row(4.0, tick_hz=20.0, randomness=0.5),
    ]
    panel = make_panel(simple(tmp_path / "step", controls=rows).ref.metadata_path)
    switch_on(app, panel)
    panel.plot.setXRange(0.0, 6.0, padding=0)
    panel.update_plot()
    x, y = panel.curves["tick_hz"].getData()
    # every value is held flat to the next change and the riser is vertical:
    # x repeats at each change, y repeats between them
    assert np.all(np.diff(x) >= 0)
    assert 4.0 in set(x.tolist())
    changes = np.flatnonzero(np.diff(y) != 0)
    for i in changes:
        assert x[i] == x[i + 1], "a value changed without a vertical riser"
    assert len(set(np.round(y, 9).tolist())) == 2


def test_a_value_nobody_measured_is_a_gap_and_never_a_zero(app, make_panel, tmp_path):
    """Null is not 0.0, on the panel as well as in the reader.

    pyqtgraph's default `connect="all"` drops a non-finite point and joins the
    finite ones either side, which would draw a solid line straight across a
    stretch where nothing was in force.  The curves are drawn with
    `connect="finite"` for exactly this.
    """
    rows = [
        control_row(1.0, tick_hz=5.0, randomness=1.0),
        control_row(3.0, tick_hz=None, randomness=0.5),
        control_row(5.0, tick_hz=20.0, randomness=0.25),
    ]
    bundle = simple(tmp_path / "gap", controls=rows)
    panel = make_panel(bundle.ref.metadata_path)
    switch_on(app, panel)

    track = panel.track
    assert np.isnan(track.value_at("tick_hz", 4.0)), "a null read back as a number"
    assert track.value_at("tick_hz", 4.0) != 0.0

    panel.plot.setXRange(0.0, 7.0, padding=0)
    panel.update_plot()
    assert panel.curves["tick_hz"].opts["connect"] == "finite"
    path = panel.curves["tick_hz"].getPath()
    kinds = [path.elementAt(i).type for i in range(path.elementCount())]
    # More than one MoveTo means the path really is broken at the gap.
    #
    # Against the enum member and not against `0`: Qt6's ElementType is a
    # plain Python enum rather than an int-like one, so `kinds.count(0)`
    # matches nothing and the assertion fails on a path that is perfectly
    # correct.  Comparing to the member says what is meant and holds either
    # way.
    moves = kinds.count(QPainterPath.ElementType.MoveToElement)
    assert moves > 1, "the staircase was drawn straight across a null"


def test_a_window_before_the_first_change_row_draws_nothing(app, panel):
    switch_on(app, panel)
    first = float(panel.track.times[0])
    panel.plot.setXRange(0.0, first - 0.01, padding=0)
    panel.update_plot()
    for name in panel.names:
        x, _y = panel.curves[name].getData()
        assert x is None or x.size == 0
    # ... and the floor rules are still there, so "on and empty" does not
    # look like "off"
    assert all(rule.isVisible() for rule in panel.rules.values())


def test_the_staircase_stops_at_the_end_of_the_recording(app, panel):
    switch_on(app, panel)
    end = panel.track.t_end
    panel.plot.setXRange(end - 1.0, end + 5.0, padding=0)
    panel.update_plot()
    for name in panel.names:
        x, _y = panel.curves[name].getData()
        if x is not None and x.size:
            assert x.max() <= end + 1e-9


# --- contract ---------------------------------------------------------------


def test_the_panel_items_never_take_the_mouse(app, panel):
    switch_on(app, panel)
    items = (
        list(panel.curves.values())
        + list(panel.rules.values())
        + list(panel.labels.values())
        + [panel.note]
    )
    assert items
    for item in items:
        assert item.acceptedMouseButtons() == Qt.MouseButton.NoButton
        assert item.acceptHoverEvents() is False


def test_an_unchanged_view_is_not_redrawn(app, panel):
    switch_on(app, panel)
    panel.plot.setXRange(0.0, 6.0, padding=0)
    panel.update_plot()
    calls = []
    for name, curve in panel.curves.items():
        original = curve.setData

        def spy(*a, _n=name, _f=original, **k):
            calls.append(_n)
            return _f(*a, **k)

        curve.setData = spy
    panel.update_plot()
    panel.update_plot()
    assert calls == []


def test_switching_a_layer_bumps_the_revision_and_forces_a_redraw(app, panel):
    switch_on(app, panel)
    panel.plot.setXRange(0.0, 6.0, padding=0)
    panel.update_plot()
    before = panel._drawn
    panel.layer.set_layer("pulses.volley", not panel.layer.layers["pulses.volley"])
    panel.update_plot()
    assert panel._drawn != before


def test_pens_are_re_resolved_on_a_theme_switch(app, panel):
    switch_on(app, panel)
    previous = theme.current_theme()
    before = panel.curves["tick_hz"].opts["pen"].color().name()
    theme.set_theme(theme.THEME_LIGHT)
    try:
        panel.polish()
        after = panel.curves["tick_hz"].opts["pen"].color().name()
        assert after != before
        assert after == theme.annotation_color("control").lower()
        assert panel.note.color.name() == theme.token("fg.faint").lower()
    finally:
        theme.set_theme(previous)
        panel.polish()
    assert panel.curves["tick_hz"].opts["pen"].color().name() == before


def test_a_bundle_fitted_against_another_recording_draws_nothing(make_panel, tmp_path):
    path = write_bundle(
        tmp_path / "wrong",
        session_id="WRONG",
        alignment={"recording_file": '"somebody_else.wav"'},
        controls=[control_row(1.0, tick_hz=5.0, randomness=1.0)],
    )
    panel = make_panel(path)
    panel.layer.set_layer(LAYER_CONTROLS, True)
    panel.refresh()
    assert panel.layer.recording_mismatch is not None
    assert panel.wanted_height() == 0


def test_the_rail_corner_follows_the_channel_rail(panel):
    assert panel.corner.width() == RAIL_WIDTH
    panel.set_rail_width(0)
    assert panel.corner.width() == 0
    panel.set_rail_width(RAIL_WIDTH)
    assert panel.corner.width() == RAIL_WIDTH


# --- in the real stack ------------------------------------------------------


def pump(app, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        time.sleep(0.005)


@pytest.fixture(scope="module")
def browser(app, tmp_path_factory):
    """The whole application on a small synthetic recording.

    The three claims below -- shares the time axis, lines up with the lanes,
    leaves the lane geometry alone -- are claims about widgets in one layout,
    and there is nothing smaller than the real window that can be asked.
    """
    soundfile = pytest.importorskip("soundfile")
    directory = tmp_path_factory.mktemp("stack")
    rate = 8000
    frames = rate * 8
    signal = np.zeros((frames, 2), dtype=np.float32)
    signal[:, 0] = 0.1 * np.sin(np.arange(frames) / 50.0)
    recording = directory / "rec.wav"
    soundfile.write(recording, signal, rate)

    rows = [
        control_row(1.0, tick_hz=5.0, randomness=1.0),
        control_row(4.0, tick_hz=0.5, randomness=0.25),
    ]
    bundle = write_bundle(
        directory / "bundle",
        session_id="STACK",
        alignment={
            "recording_file": '"rec.wav"',
            "recording_rate_hz": str(rate),
            "recording_frames": str(frames),
        },
        controls=rows,
    )

    import audian.audian as audian_app
    from audian.plugins import Plugins

    theme.apply(app)
    plugins = Plugins()
    plugins.load_plugins()
    window = audian_app.Audian(
        [str(recording)], {}, plugins, [], 0, None, False, 0, str(bundle)
    )
    window.resize(1200, 800)
    window.show()
    pump(app, 2.0)
    yield window.browser()
    # Tear the window down while the event loop is still turning.  Left to the
    # interpreter's exit, a pyqtgraph scene of fifty plots is destroyed in an
    # order Qt did not choose and the process dies with SIGSEGV *after* the
    # run has reported success -- which is a green suite that returns 139.
    window.close()
    window.setParent(None)
    window.deleteLater()
    pump(app, 0.3)


def lane_bounds(browser):
    """The first visible lane's view box, in the stack pane's own pixels."""
    channel = browser.visible_channels()[0]
    view = browser.trace_plot(channel).getViewBox()
    rect = view.mapRectToScene(view.boundingRect())
    origin = browser.figs[channel].mapTo(browser.stack_pane, QPoint(0, 0)).x()
    return origin + rect.left(), origin + rect.right()


def panel_bounds(browser):
    panel = browser.control_panel
    view = panel.plot.getViewBox()
    rect = view.mapRectToScene(view.boundingRect())
    origin = panel.fig.mapTo(browser.stack_pane, QPoint(0, 0)).x()
    return origin + rect.left(), origin + rect.right()


def test_the_stack_builds_the_panel_and_leaves_it_off(app, browser):
    assert browser.control_panel is not None
    assert browser.annotations.loaded
    assert browser.control_panel.track is not None
    assert browser.control_panel.wanted_height() == 0
    assert browser.control_panel.isVisible() is False


def test_the_panel_lines_up_with_the_lanes_when_it_is_on(app, browser):
    browser.annotations.set_layer(LAYER_CONTROLS, True)
    pump(app, 0.6)
    try:
        lane_left, lane_right = lane_bounds(browser)
        panel_left, panel_right = panel_bounds(browser)
        assert abs(panel_left - lane_left) <= 1.0
        assert abs(panel_right - lane_right) <= 1.0
    finally:
        browser.annotations.set_layer(LAYER_CONTROLS, False)
        pump(app, 0.4)


def test_the_panel_shares_the_lanes_time_range(app, browser):
    """A time lands on the same screen column in the panel as in a lane.

    Not the same *numbers*: `setXLink` aligns two view boxes by screen pixel,
    so a panel whose view box is a pixel wider than a lane's is handed a
    correspondingly wider range on purpose.  The pixel is the claim, and the
    pixel is what the reader lines up by eye.
    """
    browser.annotations.set_layer(LAYER_CONTROLS, True)
    pump(app, 0.6)
    try:
        browser.set_times(2.0, 1.0)
        pump(app, 0.4)
        lane = browser.trace_plot(browser.visible_channels()[0]).getViewBox()
        own = browser.control_panel.plot.getViewBox()
        (l0, l1) = lane.viewRange()[0]
        (o0, o1) = own.viewRange()[0]
        assert (l0, l1) == pytest.approx((2.0, 3.0), abs=1e-6)
        # one device pixel of the panel, in seconds
        tolerance = (o1 - o0) / max(1.0, own.width())
        assert o0 == pytest.approx(l0, abs=tolerance)
        lane_left = lane.mapViewToScene(QPointF(l0, 0.0)).x()
        lane_origin = (
            browser.figs[browser.visible_channels()[0]]
            .mapTo(browser.stack_pane, QPoint(0, 0))
            .x()
        )
        own_left = own.mapViewToScene(QPointF(l0, 0.0)).x()
        own_origin = browser.control_panel.fig.mapTo(
            browser.stack_pane, QPoint(0, 0)
        ).x()
        assert abs((own_origin + own_left) - (lane_origin + lane_left)) <= 1.0
    finally:
        browser.annotations.set_layer(LAYER_CONTROLS, False)
        pump(app, 0.4)


def axis_bounds(browser):
    axis = browser.taxis
    rect = axis.mapRectToScene(axis.boundingRect())
    origin = browser.taxis_fig.mapTo(browser.stack_pane, QPoint(0, 0)).x()
    return origin + rect.left(), origin + rect.right()


def test_hiding_the_channel_rail_keeps_all_three_columns_lined_up(app, browser):
    """F7 moves the lanes, the time axis and the panel at once.

    `align_time_axis` measures finished geometry, and the toggle used to run
    it before Qt had moved everything: the axis landed 48 px -- one rail --
    out of step with the lanes and stayed there until the next window resize.
    The panel reads the same measurement, so it went wrong the same way.
    """
    browser.annotations.set_layer(LAYER_CONTROLS, True)
    pump(app, 0.6)
    try:
        for _ in range(2):
            browser.toggle_rail()
            pump(app, 0.5)
            lane_left, _ = lane_bounds(browser)
            assert abs(axis_bounds(browser)[0] - lane_left) <= 1.0
            assert abs(panel_bounds(browser)[0] - lane_left) <= 1.0
    finally:
        if not browser.rail_visible:
            browser.toggle_rail()
        browser.annotations.set_layer(LAYER_CONTROLS, False)
        pump(app, 0.5)


def test_toggling_the_panel_leaves_the_lane_geometry_where_it_was(app, browser):
    """Round trip, exactly.

    The panel takes its pixels from the scroll area's viewport, which is what
    `lane_geometry` already measures, so switching it on re-solves the stack
    rather than perturbing it -- and switching it off has to land back on the
    number it started from, or every toggle would walk the lanes a pixel.
    """
    before = browser.lane_geometry(browser.height())
    browser.annotations.set_layer(LAYER_CONTROLS, True)
    pump(app, 0.6)
    during = browser.lane_geometry(browser.height())
    browser.annotations.set_layer(LAYER_CONTROLS, False)
    pump(app, 0.6)
    after = browser.lane_geometry(browser.height())
    assert after == before
    # while it is on, every lane is still exactly one height -- the panel
    # takes from the stack's budget, it does not make the lanes unequal
    lane_h = during[0]
    assert lane_h > 0
    heights = {browser.figs[c].height() for c in browser.visible_channels()}
    assert len(heights) == 1
