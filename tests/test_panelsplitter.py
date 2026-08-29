"""Tests for the draggable trace / spectrogram boundary.

Runs offscreen::

    QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_panelsplitter.py -q

The split is a layout, and there is only one question about a layout worth
asking: what height did the rows actually end up with.  So every claim here
is measured off `QGraphicsWidget.geometry()` once the layout has settled,
never off `spec_scale` -- the ratio agreeing with itself would prove
nothing.  `settle` is where the layout is activated, which is also why it is
called by hand after every gesture: a drag invalidates and Qt re-activates
before the next paint, so a test that reads geometry without letting the
event loop turn is reading the frame before the one it means.

Three windows are built, because the three cases differ in kind.  Four
channels at 1200x900 is *dense*: the lane is 34 px, the whole of it goes to
the trace, and the spectrogram's 120 px allowance leaves nothing over -- so
the boundary can be dragged one way only.  Two channels have 127 px of lane
to share, so that is where a drag is measured in both directions.  Sixteen
collapse the spectrogram onto a single focused lane (`spectrogram_channels`)
and are the case whose cost is measured.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from PyQt5.QtCore import QEvent, QPoint, QPointF, QSettings, Qt  # noqa: E402
from PyQt5.QtGui import QMouseEvent  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

from audian import theme  # noqa: E402
from audian.databrowser import DataBrowser  # noqa: E402
from audian.panels import Panel  # noqa: E402
from audian.timeplot import TICK_VALUES_MIN_HEIGHT  # noqa: E402

#: The window every measurement quoted in this file was made at.
WINDOW = (1200, 900)


# --------------------------------------------------------------- the windows


@pytest.fixture(scope="session")
def app():
    instance = QApplication.instance()
    if instance is None:
        instance = QApplication([])
    return instance


def pump(seconds):
    end = time.monotonic() + seconds
    app = QApplication.instance()
    while time.monotonic() < end:
        app.processEvents()
        app.sendPostedEvents(None, QEvent.DeferredDelete)
        time.sleep(0.005)


def settle():
    """Let Qt activate the layouts a gesture invalidated."""
    app = QApplication.instance()
    for _ in range(3):
        app.processEvents()


RATE = 8000
FRAMES = RATE * 4


def tones(channels):
    """One steady tone per channel, each at its own frequency.

    Enough to give every lane something to draw, which is all a layout test
    needs.  Anything that cares what the *channels* have in common -- the
    mean spectrogram does -- passes its own signal instead.
    """
    signal = np.zeros((FRAMES, channels), dtype=np.float32)
    for c in range(channels):
        signal[:, c] = 0.1 * np.sin(np.arange(FRAMES) / (50.0 + c))
    return signal


def build_window(app, directory, channels, signal=None):
    """The whole application on a synthetic recording of `channels` channels.

    `signal` is a (frames, channels) float array; `tones` is the default.

    Both persistence stores are redirected into `directory` first.  This file
    writes a preference -- the split is saved at the end of every gesture --
    and a test that writes the reader's own ``~/.config/audian`` is a test
    that has to be run in a container to be safe.
    """
    soundfile = pytest.importorskip("soundfile")
    import audian.audian as audian_app
    from audian.plugins import Plugins

    rate = RATE
    if signal is None:
        signal = tones(channels)
    recording = directory / "rec.wav"
    soundfile.write(recording, signal, rate)

    audian_app.settings_path = lambda: directory / "settings.json"
    for fmt in (QSettings.NativeFormat, QSettings.IniFormat):
        for scope in (QSettings.UserScope, QSettings.SystemScope):
            QSettings.setPath(fmt, scope, os.fspath(directory))

    theme.apply(app)
    plugins = Plugins()
    plugins.load_plugins()
    window = audian_app.Audian(
        [str(recording)], {}, plugins, [], 0, None, False, 0, None
    )
    window.resize(*WINDOW)
    window.show()
    pump(2.0)
    return window


def open_stack(app, directory, channels, signal=None):
    """A browser showing both panels, and the teardown that follows it."""
    import audian.audian as audian_app

    original = audian_app.settings_path
    home = Path(QSettings("audian", "audian").fileName()).parent.parent
    window = build_window(app, directory, channels, signal)
    view = window.browser()
    view.set_panels(specs=1)
    pump(1.0)
    yield view
    # Torn down while the event loop is still turning: left to the
    # interpreter's exit, a pyqtgraph scene is destroyed in an order Qt did
    # not choose and the process dies with SIGSEGV *after* the run has
    # reported success.
    window.close()
    window.setParent(None)
    window.deleteLater()
    pump(0.3)
    audian_app.settings_path = original
    for fmt in (QSettings.NativeFormat, QSettings.IniFormat):
        for scope in (QSettings.UserScope, QSettings.SystemScope):
            QSettings.setPath(fmt, scope, os.fspath(home))


@pytest.fixture(scope="module")
def browser(app, tmp_path_factory):
    """Four channels: a dense stack, 154 px of lane, nothing to spare."""
    yield from open_stack(app, tmp_path_factory.mktemp("split4"), 4)


@pytest.fixture(scope="module")
def roomy_browser(app, tmp_path_factory):
    """Two channels: 127 px of lane over the allowance, room to drag."""
    yield from open_stack(app, tmp_path_factory.mktemp("split2"), 2)


@pytest.fixture(scope="module")
def wide_browser(app, tmp_path_factory):
    """Sixteen channels: the eel array, and the case whose cost is measured."""
    yield from open_stack(app, tmp_path_factory.mktemp("split16"), 16)


# ---------------------------------------------------------------- the rulers


def panel(browser, kind):
    for p in browser.panels.values():
        if kind == "spacer" and p.is_spacer():
            return p
        if kind == "spectrogram" and p.is_spectrogram() and not p.is_power():
            return p
        if kind == "trace" and p.is_trace():
            return p
    raise AssertionError(f"no {kind} panel")


def row_height(browser, kind, channel):
    """The height one panel's row actually got, in device pixels."""
    return float(panel(browser, kind).axs[channel].geometry().height())


def viewbox_height(browser, kind, channel):
    return float(panel(browser, kind).axs[channel].getViewBox().geometry().height())


def boundary(browser, channel):
    """Scene y of the grab band's row: the boundary the reader drags."""
    return float(panel(browser, "spacer").axs[channel].geometry().top())


def splitter(browser, channel):
    return panel(browser, "spacer").axs[channel]


def spec_channel(browser):
    return browser.spectrogram_channels(browser.visible_channels())[0]


def figure_height(browser, channel):
    return float(browser.figs[channel].height())


def room_now(browser, channel):
    """The pixels the two panels of one lane share right now."""
    return browser.panel_split_heights(channel)[1]


def drag(browser, spec_h, room):
    """One mouse move: a new position, then the frame that draws it."""
    browser.drag_panel_split(spec_h, room)
    settle()


def send(app, browser, channel, kind, y, button, buttons):
    """One real mouse event at scene y, routed the way a pointer is.

    The *global* position is not decoration: `QGraphicsScene` finds the item
    under the mouse by mapping the event's screen position back through the
    viewport, so an event carrying only a local position looks up an
    entirely different pixel and is delivered to nothing at all -- a drag
    built that way reports "the boundary moved 0.0 px" and raises nothing.
    """
    viewport = browser.figs[channel].viewport()
    pos = QPoint(200, int(round(y)))
    app.sendEvent(
        viewport,
        QMouseEvent(
            kind,
            QPointF(pos),
            QPointF(viewport.mapToGlobal(pos)),
            button,
            buttons,
            Qt.NoModifier,
        ),
    )
    settle()


def reset_split(browser):
    browser.spec_scale = None
    browser.set_panels(specs=1, traces=True)
    browser.adjust_layout(browser.width(), browser.height())
    settle()


@pytest.fixture
def split_reset(browser):
    """Put the split back afterwards, so tests do not inherit each other."""
    yield
    reset_split(browser)


@pytest.fixture
def roomy_reset(roomy_browser):
    yield
    reset_split(roomy_browser)


# ------------------------------------------------------- the default layout


@pytest.mark.parametrize("stack", ["browser", "roomy_browser", "wide_browser"])
def test_a_lane_opens_with_the_spectrogram_at_the_height_it_grew_the_lane_by(
    stack, request
):
    """The layout the application opens on, which is not a drag's to change.

    `lane_geometry` grows a lane by `theme.SPECTROGRAM_MIN_HEIGHT` px when
    it is going to draw a spectrogram there, so that is what the spectrogram
    row opens with and the traces keep the lane the stack was solved for.
    Splitting the whole figure by a ratio instead opened a four channel
    stack at 69 px of spectrogram -- 51 px under the height
    `spectrogramplot.can_render` refuses to draw one in, so the panel the
    lane grew to make room for opened unreadable by the browser's own rule,
    before the reader had touched anything.

    Measured at 1200x900: four channels and sixteen open 120 / 34, two open
    120 / 127.
    """
    from audian.spectrogramplot import SpectrogramPlot

    view = request.getfixturevalue(stack)
    c = spec_channel(view)
    spec = row_height(view, "spectrogram", c)
    trace = row_height(view, "trace", c)
    assert spec == theme.SPECTROGRAM_MIN_HEIGHT
    assert trace == figure_height(view, c) - theme.SPECTROGRAM_MIN_HEIGHT
    assert SpectrogramPlot.can_render(spec)


def test_the_grab_band_takes_no_height_from_either_panel(browser):
    """The row is 0 px; the band reaches across the boundary instead.

    A `QSplitter` has to spend layout height on its handle because widgets
    cannot overlap.  Seven pixels of a 154 px lane is what pushed the
    spectrogram off its allowance, and an in-scene item does not have to
    spend them: it reports a bounding rect taller than the row it was laid
    out in.
    """
    c = spec_channel(browser)
    band = splitter(browser, c)
    assert band.isVisible()
    assert row_height(browser, "spacer", c) == 0
    reach = band.boundingRect()
    assert reach.height() == theme.PANEL_SPLIT_HANDLE_HEIGHT
    # centred on the boundary: as far into the panel above as below
    assert reach.top() == -reach.bottom()
    assert row_height(browser, "spectrogram", c) + row_height(
        browser, "trace", c
    ) == figure_height(browser, c)


@pytest.mark.parametrize("stack", ["browser", "roomy_browser", "wide_browser"])
def test_the_rows_of_every_lane_add_up_to_that_lane_exactly(stack, request):
    """Rows summing past the figure clip the last one, silently.

    `QGraphicsView` clamps the central item up to the layout's minimum, so
    the overflow is not shared out or scrolled to, it simply falls off the
    bottom of the viewport.  Both ways of overflowing are covered here: the
    figure's own vertical margins (8 px, which is why a channel lane has
    none) and the `theme.PLOT_FRAME_HEIGHT` px a *hidden* panel still holds,
    which is what a sixteen channel stack's plain trace lanes overflowed by.
    """
    view = request.getfixturevalue(stack)
    for c in view.visible_channels():
        rows = 0.0
        for p in view.panels.values():
            if p.is_power() or not p.axs[c].isVisible():
                continue
            rows += float(p.axs[c].geometry().height())
        hidden = sum(
            theme.PLOT_FRAME_HEIGHT
            for p in view.panels.values()
            if not p.is_power() and not p.is_spacer() and not p.axs[c].isVisible()
        )
        assert rows + hidden == pytest.approx(figure_height(view, c), abs=0.01)
        assert view.figs[c].ci.geometry().height() == pytest.approx(
            figure_height(view, c), abs=0.01
        )


def test_a_view_box_is_shorter_than_its_row_by_the_plot_frame(roomy_browser):
    """What `theme.PLOT_FRAME_HEIGHT` is, pinned to the thing it describes.

    Every chrome decision in the browser is taken on a row height and acted
    on by a plot that measures its view box, so the constant that converts
    between them cannot be allowed to drift silently.
    """
    c = spec_channel(roomy_browser)
    for kind in ("spectrogram", "trace"):
        assert (
            row_height(roomy_browser, kind, c) - viewbox_height(roomy_browser, kind, c)
            == theme.PLOT_FRAME_HEIGHT
        )


# ------------------------------------------------------------------ the drag


def test_the_band_is_the_one_in_scene_item_that_takes_the_mouse(browser):
    """Every other item sets NoButton so a click reaches the plot beneath."""
    c = spec_channel(browser)
    band = splitter(browser, c)
    assert band.acceptedMouseButtons() & Qt.LeftButton
    assert band.acceptHoverEvents()
    assert band.cursor().shape() == Qt.SplitVCursor
    assert browser.borders[c].acceptedMouseButtons() == Qt.NoButton
    # and it has to be above the two plots it overlaps, or the half of it
    # that lies over the trace would never see a press
    assert band.zValue() > panel(browser, "trace").axs[c].zValue()
    assert band.zValue() > panel(browser, "spectrogram").axs[c].zValue()


def test_dragging_the_band_moves_the_boundary_by_exactly_that_many_px(
    roomy_browser, roomy_reset
):
    c = spec_channel(roomy_browser)
    before = boundary(roomy_browser, c)
    spec_h, room = roomy_browser.panel_split_heights(c)
    for step in (7, 15, -11, -19, 22):
        drag(roomy_browser, spec_h + step, room)
        assert boundary(roomy_browser, c) == pytest.approx(before + step, abs=0.01)


def test_a_real_press_drag_release_through_the_scene_moves_the_boundary(
    app, roomy_browser, roomy_reset
):
    """Not the browser's API: a mouse event handed to the view.

    `QGraphicsSceneMouseEvent` cannot be constructed from Python, so the only
    way to prove the band is reachable by an actual pointer is to hand a
    `QMouseEvent` to the viewport and let `QGraphicsView` route it to
    whatever item is under that pixel.  The scene of a
    `GraphicsLayoutWidget` is its viewport at 1:1, so scene y and widget y
    are the same number -- and the pixel pressed here is the boundary
    itself, which is the row the band is laid out in and has no height at
    all.  Only its bounding rect makes that pixel hit anything.
    """
    c = spec_channel(roomy_browser)
    before = boundary(roomy_browser, c)
    send(
        app,
        roomy_browser,
        c,
        QEvent.MouseButtonPress,
        before,
        Qt.LeftButton,
        Qt.LeftButton,
    )
    send(
        app, roomy_browser, c, QEvent.MouseMove, before + 12, Qt.NoButton, Qt.LeftButton
    )
    send(
        app,
        roomy_browser,
        c,
        QEvent.MouseButtonRelease,
        before + 12,
        Qt.LeftButton,
        Qt.NoButton,
    )
    assert boundary(roomy_browser, c) == pytest.approx(before + 12, abs=0.01)


def test_a_lane_that_changes_under_the_drag_does_not_rescale_it(
    app, roomy_browser, roomy_reset
):
    """F6 is reachable with the button down, and it resizes every lane.

    The travel is mapped onto the pixels the two panels share, so a gesture
    latched at the press is scaled by ``room_new / room_old`` from the
    moment that number changes: measured, hiding the navigator mid-drag
    turned 20 px of further pointer travel into 25 px of boundary, and a
    window resize turned 30 px into 62.  Nothing raised; the boundary simply
    ran away from the pointer.
    """
    c = spec_channel(roomy_browser)
    start = boundary(roomy_browser, c)
    room_before = room_now(roomy_browser, c)
    send(
        app,
        roomy_browser,
        c,
        QEvent.MouseButtonPress,
        start,
        Qt.LeftButton,
        Qt.LeftButton,
    )
    send(
        app, roomy_browser, c, QEvent.MouseMove, start + 20, Qt.NoButton, Qt.LeftButton
    )
    assert boundary(roomy_browser, c) == pytest.approx(start + 20, abs=0.01)

    roomy_browser.toggle_fulldata()  # F6, with the button still down
    settle()
    moved = boundary(roomy_browser, c)
    assert room_now(roomy_browser, c) != room_before

    # 40 px further down the *pointer's* travel, which is 20 px on from
    # where it was when the lane changed.  To the pixel and not to the
    # fraction: a resize hands its leftover out by stretch until the next
    # pass rounds the rows back to whole pixels, so the boundary the lane
    # change left behind is on a fraction and the split rounds off it.  The
    # bug this pins moved 25 px, not 20.
    send(
        app, roomy_browser, c, QEvent.MouseMove, start + 40, Qt.NoButton, Qt.LeftButton
    )
    assert boundary(roomy_browser, c) == pytest.approx(moved + 20, abs=1.0)
    send(
        app,
        roomy_browser,
        c,
        QEvent.MouseButtonRelease,
        start + 40,
        Qt.LeftButton,
        Qt.NoButton,
    )
    roomy_browser.toggle_fulldata()
    settle()


def test_double_clicking_the_band_resets_the_split(app, roomy_browser, roomy_reset):
    """A `QSplitter` handle does this, so this one does too."""
    c = spec_channel(roomy_browser)
    before = boundary(roomy_browser, c)
    spec_h, room = roomy_browser.panel_split_heights(c)
    drag(roomy_browser, spec_h + 20, room)
    assert boundary(roomy_browser, c) != pytest.approx(before, abs=0.01)
    send(
        app,
        roomy_browser,
        c,
        QEvent.MouseButtonDblClick,
        boundary(roomy_browser, c),
        Qt.LeftButton,
        Qt.LeftButton,
    )
    assert boundary(roomy_browser, c) == pytest.approx(before, abs=0.01)


def test_one_drag_moves_every_channel_that_shows_both_panels(browser, split_reset):
    """The sync the whole feature is for: one ratio, not one per channel."""
    channels = browser.visible_channels()
    assert len(channels) == 4
    spec_h, room = browser.panel_split_heights(channels[0])
    drag(browser, spec_h - 17, room)
    heights = {c: row_height(browser, "spectrogram", c) for c in channels}
    assert len(set(heights.values())) == 1
    assert heights[channels[0]] == pytest.approx(spec_h - 17, abs=0.01)


# ---------------------------------------------------------------- the clamps


def test_neither_row_can_be_dragged_under_its_floor(roomy_browser, roomy_reset):
    c = spec_channel(roomy_browser)
    floor = theme.PANEL_SPLIT_MIN_HEIGHT
    spec_h, room = roomy_browser.panel_split_heights(c)
    drag(roomy_browser, spec_h + 10_000, room)
    assert row_height(roomy_browser, "spectrogram", c) == room - floor
    assert row_height(roomy_browser, "trace", c) == floor
    drag(roomy_browser, spec_h - 10_000, room)
    assert row_height(roomy_browser, "spectrogram", c) == floor
    assert row_height(roomy_browser, "trace", c) == room - floor


def test_a_drag_past_a_clamp_and_back_lands_where_it_started(
    roomy_browser, roomy_reset
):
    """Why the drag carries an absolute position and not an increment.

    Summed increments would throw away every pixel a clamp ate, and the
    boundary would come back short of where the gesture began -- drift the
    reader can see and cannot undo.
    """
    c = spec_channel(roomy_browser)
    before = boundary(roomy_browser, c)
    spec_h, room = roomy_browser.panel_split_heights(c)
    for step in (5, 400, 900, 300, 0):
        drag(roomy_browser, spec_h + step, room)
    assert boundary(roomy_browser, c) == pytest.approx(before, abs=0.01)
    for step in (-5, -400, -900, -300, 0):
        drag(roomy_browser, spec_h + step, room)
    assert boundary(roomy_browser, c) == pytest.approx(before, abs=0.01)


def test_the_clamp_always_includes_the_split_the_lane_opens_on(browser, split_reset):
    """The measurement behind `theme.PANEL_SPLIT_MIN_HEIGHT`.

    Four channels in a 1200x900 window is the tightest stack that still
    shows a spectrogram in every lane: the figure is 154 px, the
    spectrogram's allowance is 120 of them and the trace opens on the
    remaining 34 -- which *is* the floor.  So this lane can only be dragged
    one way, and the position it opens in has to be one of the positions the
    clamp allows: with a floor of 48 the first pixel of travel would have
    snapped the boundary 14 px away from the pointer and never let it back.
    """
    c = spec_channel(browser)
    assert figure_height(browser, c) == 154
    assert row_height(browser, "trace", c) == theme.PANEL_SPLIT_MIN_HEIGHT
    _, room = browser.panel_split_heights(c)
    lo, hi = browser.panel_split_limits(int(room), 1)
    assert lo == theme.PANEL_SPLIT_MIN_HEIGHT
    assert hi == row_height(browser, "spectrogram", c)
    assert room - 2 * theme.PANEL_SPLIT_MIN_HEIGHT == 86


def test_a_row_at_the_clamp_agrees_with_itself_about_its_chrome(
    roomy_browser, roomy_reset
):
    """The state an over-drag parks in, which used to be wrong and sticky.

    The floor is on the row and `TimePlot` tests the view box, which is
    `theme.PLOT_FRAME_HEIGHT` px shorter, so a row parked at exactly 48 drew
    amplitude tick values while the plot that owns them had hidden the
    caption that stands in for them -- `lane_axes`' own rule, "ticks without
    values are dropped with the values", inverted.  It happened on both
    sides, at exactly the clamp, which is where any over-drag parks, and
    only growing the row back repaired it.

    So both sides are asked the same question about the same number, and the
    answer is re-taken whenever the split moves.
    """
    c = spec_channel(roomy_browser)
    spec_h, room = roomy_browser.panel_split_heights(c)

    def agrees():
        for kind in ("spectrogram", "trace"):
            plot = panel(roomy_browser, kind).axs[c]
            values = plot.getAxis("left").style["showValues"]
            fits = viewbox_height(roomy_browser, kind, c) >= TICK_VALUES_MIN_HEIGHT
            assert values == fits, f"{kind} row draws values it has no room for"
            assert (plot.getAxis("left").style["tickLength"] != 0) == fits
            if hasattr(plot, "channel_label"):
                assert plot.channel_label.isVisible() == fits

    for target in (spec_h + 10_000, spec_h - 10_000):
        drag(roomy_browser, target, room)
        agrees()
        # and it stays repaired through a layout pass, not just for a frame
        roomy_browser.adjust_layout(roomy_browser.width(), roomy_browser.height())
        settle()
        agrees()
    drag(roomy_browser, spec_h, room)
    agrees()


def test_a_drag_that_crosses_the_tick_value_threshold_takes_the_chrome_with_it(
    roomy_browser, roomy_reset
):
    """The claim `apply_panel_split` used to make, tested instead of asserted.

    The split opens with the trace on the lane height, and a dense lane is
    34 px -- well under `TICK_VALUES_MIN_HEIGHT` -- so a drag most certainly
    can move a row across that threshold and the chrome has to follow it
    without a full layout pass.
    """
    c = spec_channel(roomy_browser)
    spec_h, room = roomy_browser.panel_split_heights(c)
    tall = room - theme.PANEL_SPLIT_MIN_HEIGHT
    drag(roomy_browser, tall, room)
    trace = panel(roomy_browser, "trace").axs[c]
    assert viewbox_height(roomy_browser, "trace", c) < TICK_VALUES_MIN_HEIGHT
    assert not trace.getAxis("left").style["showValues"]
    drag(roomy_browser, spec_h, room)
    assert viewbox_height(roomy_browser, "trace", c) >= TICK_VALUES_MIN_HEIGHT
    assert trace.getAxis("left").style["showValues"]


# ------------------------------------------------------------- the lane pitch


def test_a_drag_changes_no_lane_pitch_and_no_figure_height(browser, split_reset):
    """The split moves a boundary inside a fixed lane, never the lane."""
    channels = browser.visible_channels()
    c = channels[0]

    def tops():
        return [
            browser.figs[ch].mapTo(browser.stack_pane, QPoint(0, 0)).y()
            for ch in channels
        ]

    pitch = browser.lane_height
    heights = [browser.figs[ch].height() for ch in channels]
    before = tops()
    spec_h, room = browser.panel_split_heights(c)
    drag(browser, spec_h - 20, room)
    browser.finish_panel_split()
    settle()
    assert browser.lane_height == pitch
    assert [browser.figs[ch].height() for ch in channels] == heights
    assert tops() == before


# ------------------------------------------------------------ when it exists


def test_the_band_is_gone_when_the_spectrogram_is_hidden(browser, split_reset):
    c = spec_channel(browser)
    browser.set_panels(specs=0)
    settle()
    assert not splitter(browser, c).isVisible()
    assert browser.panel_split_heights(c) is None


def test_the_band_is_gone_when_the_trace_is_hidden(browser, split_reset):
    c = spec_channel(browser)
    browser.set_panels(specs=1, traces=False)
    settle()
    assert not splitter(browser, c).isVisible()
    assert browser.panel_split_heights(c) is None


def test_a_lane_with_no_spectrogram_of_its_own_has_no_band(wide_browser):
    """Sixteen channels collapse the spectrogram onto the focused lane."""
    channels = wide_browser.visible_channels()
    shown = wide_browser.spectrogram_channels(channels)
    assert len(shown) == 1
    for c in channels:
        assert splitter(wide_browser, c).isVisible() == (c in shown)


def test_a_lane_whose_spectrogram_has_nothing_to_draw_has_no_band(wide_browser):
    """`show_spec` is what the lane grew for, not what it ended up drawing.

    On the focused lane of a sixteen channel stack the two part company: the
    lane is 154 px because a spectrogram is meant to go there, while the
    panel itself has nothing in it yet.  Deciding the band from the
    intention left a live `SplitVCursor` in 52 px of dead lane, over a
    boundary that was not there, swallowing presses meant for the trace.
    """
    view = wide_browser
    c = spec_channel(view)
    spec = panel(view, "spectrogram")
    items = list(spec.axs[c].data_items)
    assert items and splitter(view, c).isVisible()
    try:
        for item in items:
            item.setVisible(False)
        view.adjust_layout(view.width(), view.height())
        settle()
        assert c in view.spectrogram_channels(view.visible_channels())
        assert not spec.axs[c].isVisible()
        assert not splitter(view, c).isVisible()
        assert view.panel_split_heights(c) is None
    finally:
        for item in items:
            item.setVisible(True)
        view.adjust_layout(view.width(), view.height())
        settle()
    assert splitter(view, c).isVisible()


@pytest.mark.parametrize(
    "gesture",
    [
        "next_channel",
        "previous_channel",
        "select_next_channel",
        "select_previous_channel",
        "rail_clicked",
    ],
)
def test_the_spectrogram_follows_the_focus_by_every_gesture_that_moves_it(
    wide_browser, gesture
):
    """Stepping down the array must not cost the stack its spectrogram.

    Sixteen channels collapse the spectrogram onto the focused lane, so
    every step hides one lane's panel and shows another's.  Two ways that
    went wrong, and the second is why this is parametrised.

    A panel asked whether it has anything to draw with
    `QGraphicsItem.isVisible` answers for its whole ancestry, so a lane
    hidden once answered "nothing" for ever and the stack lost its
    spectrogram at the first press of the down arrow.

    Then, with that fixed, four of the five gestures below still left the
    spectrogram on the lane the reader had just left: they moved
    `current_channel` and stopped at `update_borders`, and only
    `rail_clicked` relaid the stack out.  The version of this test that
    called `adjust_layout` by hand after the gesture passed throughout -
    it was doing for the browser the thing the browser was failing to do.
    So nothing here calls it.
    """
    view = wide_browser
    restore = view.current_channel
    # somewhere in the middle of the array, so "previous" has somewhere to go
    view.rail_clicked(4, False)
    settle()
    before = view.current_channel
    try:
        if gesture == "rail_clicked":
            view.rail_clicked(before + 1, False)
        else:
            getattr(view, gesture)()
        settle()
        focus = view.current_channel
        assert focus != before
        assert spec_channel(view) == focus
        assert panel(view, "spectrogram").axs[focus].isVisible()
        assert row_height(view, "spectrogram", focus) == theme.SPECTROGRAM_MIN_HEIGHT
        assert splitter(view, focus).isVisible()
        assert not splitter(view, before).isVisible()
    finally:
        view.rail_clicked(restore, False)
        settle()


# ------------------------------------------------------------- the keyboard


def test_shift_f3_puts_the_split_back(roomy_browser, roomy_reset):
    c = spec_channel(roomy_browser)
    before = boundary(roomy_browser, c)
    spec_h, room = roomy_browser.panel_split_heights(c)
    drag(roomy_browser, spec_h + 20, room)
    roomy_browser.finish_panel_split()
    settle()
    assert boundary(roomy_browser, c) != pytest.approx(before, abs=0.01)
    roomy_browser.reset_panel_split()
    settle()
    assert boundary(roomy_browser, c) == pytest.approx(before, abs=0.01)


def test_the_reset_is_bound_to_a_key_nothing_else_claims(browser):
    window = browser.window()
    reset = window.acts.reset_panel_split
    assert reset.shortcut().toString() == "Shift+F3"
    claimed = [
        act
        for act in vars(window.acts).values()
        if hasattr(act, "shortcuts")
        and any(s.toString() == "Shift+F3" for s in act.shortcuts())
    ]
    assert claimed == [reset]


def test_f3_turns_the_spectrogram_on_and_off_and_nothing_else(
    roomy_browser, roomy_reset
):
    """It used to step through off and four sizes, so it took five presses
    to get back to where it started and the fifth was the only way.

    The boundary is dragged now, which is a better answer to the question
    those four sizes were answering, so F3 answers only the other one.
    """
    window = roomy_browser.window()
    assert roomy_browser.show_specs > 0
    window.toggle_spectrograms()
    settle()
    assert roomy_browser.show_specs == 0
    window.toggle_spectrograms()
    settle()
    assert roomy_browser.show_specs == 1
    # two presses, not five
    window.toggle_spectrograms()
    window.toggle_spectrograms()
    settle()
    assert roomy_browser.show_specs == 1


def test_f3_never_leaves_a_lane_with_neither_panel(roomy_browser, roomy_reset):
    """`set_panels` will hide both, and a stack with neither is a window of
    empty lanes with no key that obviously fills them again."""
    window = roomy_browser.window()
    roomy_browser.set_panels(traces=False, specs=1)
    settle()
    assert not roomy_browser.show_traces
    window.toggle_spectrograms()
    settle()
    assert roomy_browser.show_specs == 0
    assert roomy_browser.show_traces
    roomy_browser.set_panels(traces=True, specs=1)
    settle()


def test_the_split_survives_the_spectrogram_being_toggled_off_and_on(
    roomy_browser, roomy_reset
):
    """The dragged split was stored per F3 size, so a round trip through the
    sizes could bring the spectrogram back at a height nobody chose.  There
    is one split now and it is the one the reader dragged."""
    window = roomy_browser.window()
    c = spec_channel(roomy_browser)
    spec_h, room = roomy_browser.panel_split_heights(c)
    drag(roomy_browser, spec_h + 20, room)
    roomy_browser.finish_panel_split()
    settle()
    dragged = row_height(roomy_browser, "spectrogram", c)
    assert dragged != pytest.approx(spec_h, abs=0.01)
    window.toggle_spectrograms()
    settle()
    window.toggle_spectrograms()
    settle()
    assert row_height(roomy_browser, "spectrogram", c) == pytest.approx(
        dragged, abs=0.01
    )


# ------------------------------------------------------------- the settings


def test_the_split_is_written_once_the_gesture_ends(roomy_browser, roomy_reset):
    import audian.audian as audian_app
    from audian.databrowser import DataBrowser

    c = spec_channel(roomy_browser)
    spec_h, room = roomy_browser.panel_split_heights(c)
    drag(roomy_browser, spec_h + 13, room)
    roomy_browser.finish_panel_split()
    saved = audian_app.settings().get(DataBrowser.PANEL_SPLIT_SETTING)
    assert saved["version"] == DataBrowser.PANEL_SPLIT_SETTING_VERSION
    assert saved["scale"] == pytest.approx(roomy_browser.spec_scale)


def test_a_split_nobody_dragged_is_written_as_null(roomy_browser, roomy_reset):
    """Its default follows the lane height, so this window's answer to it is
    not a preference and must not be frozen into the settings file."""
    import audian.audian as audian_app
    from audian.databrowser import DataBrowser

    roomy_browser.spec_scale = None
    roomy_browser.save_panel_split()
    saved = audian_app.settings().get(DataBrowser.PANEL_SPLIT_SETTING)
    assert saved["scale"] is None


def test_a_saved_split_is_read_back_whatever_the_channel_count(browser, monkeypatch):
    """It measures the spectrogram against its own default, so it travels."""
    import audian.audian as audian_app
    from audian.databrowser import DataBrowser

    monkeypatch.setattr(
        audian_app,
        "settings",
        lambda: {
            DataBrowser.PANEL_SPLIT_SETTING: {
                "version": DataBrowser.PANEL_SPLIT_SETTING_VERSION,
                "scale": 0.375,
            }
        },
    )
    scale = browser.spec_scale
    try:
        browser.spec_scale = None
        browser.restore_panel_split()
        assert browser.spec_scale == pytest.approx(0.375)
    finally:
        browser.spec_scale = scale


@pytest.mark.parametrize("stored", ["not a number", None, float("inf")])
def test_a_split_that_is_not_a_number_leaves_the_default_alone(
    browser, monkeypatch, stored
):
    """Half-applying an unreadable value is worse than ignoring it."""
    import audian.audian as audian_app
    from audian.databrowser import DataBrowser

    monkeypatch.setattr(
        audian_app,
        "settings",
        lambda: {
            DataBrowser.PANEL_SPLIT_SETTING: {
                "version": DataBrowser.PANEL_SPLIT_SETTING_VERSION,
                "scale": stored,
            }
        },
    )
    scale = browser.spec_scale
    try:
        browser.spec_scale = None
        browser.restore_panel_split()
        assert browser.spec_scale is None
    finally:
        browser.spec_scale = scale


def test_the_split_a_reader_dragged_means_the_same_on_any_stack(browser, roomy_browser):
    """The measurement the version 2 format exists for.

    `spec_scale` is measured against `theme.SPECTROGRAM_MIN_HEIGHT`, which no
    recording moves, so a reader who halves the spectrogram on one stack must
    find it halved on the other.  Version 1 could not: it held the trace over
    the spectrogram, and the trace is the lane, 34 px on the dense stack
    against 130 on the roomy one, so the same stored number came out 60 px
    one side and 144 the other, a shrink replayed as a stretch.

    Measuring against `default_spec_height` instead -- the height a lane
    opens on, which is the tempting denominator -- is the same thing now that
    the default *is* the allowance, and was not when F3 had four sizes: at
    sizes 2 to 4 the default took a share of the lane as well, so it was
    185 px on two channels against 120 on sixteen.
    """
    dense = browser.spec_scale
    roomy = roomy_browser.spec_scale
    try:
        for view in (browser, roomy_browser):
            view.spec_scale = 0.5
            view.adjust_layout(view.width(), view.height())
        settle()
        for view in (browser, roomy_browser):
            c = spec_channel(view)
            assert row_height(view, "spectrogram", c) == pytest.approx(60, abs=1)
    finally:
        browser.spec_scale = dense
        roomy_browser.spec_scale = roomy
        settle()


@pytest.mark.parametrize(
    "saved",
    [
        pytest.param({"version": 99, "fracs": {"1": 0.1}}, id="a later version"),
        pytest.param(
            {
                "version": 1,
                "fracs": {
                    "0": 1.0,
                    "1": 2.7437722419928825,
                    "2": 1.3717948717948718,
                    "3": 0.25,
                    "4": 0.15,
                },
            },
            id="the version 1 file this format replaced",
        ),
    ],
)
def test_a_split_saved_by_another_version_is_ignored(browser, monkeypatch, saved):
    """The version 1 case is the one a reader actually hit.

    Those five numbers are a real ``~/.config/audian/settings.json``.  Read
    as version 2 scales they would open F3 size 1 at 2.74 times its default;
    read as the version 1 fracs they are, they open it at 41 px on a sixteen
    channel stack.  Neither is what the reader chose, and nothing in the file
    distinguishes a value they dragged from one an earlier build wrote for
    them - the entry for size 0 is the tell, since size 0 hides the
    spectrogram and has no boundary to drag.  So the whole entry goes.

    Version 2 is now in the same position: it holds up to four splits, one
    per F3 size, and F3 has one size.  Nothing in the file says which of them
    the reader would have wanted kept, so it goes the same way.
    """
    import audian.audian as audian_app
    from audian.databrowser import DataBrowser

    monkeypatch.setattr(
        audian_app,
        "settings",
        lambda: {DataBrowser.PANEL_SPLIT_SETTING: saved},
    )
    scale = browser.spec_scale
    try:
        browser.spec_scale = None
        browser.restore_panel_split()
        assert browser.spec_scale is None
    finally:
        browser.spec_scale = scale


# ------------------------------------------------------- the empty lane


@pytest.mark.parametrize("stack", ["browser", "roomy_browser", "wide_browser"])
@pytest.mark.parametrize("traces", [True, False])
@pytest.mark.parametrize("specs", [0, 1, 2, 3, 4])
def test_no_visible_lane_is_ever_empty(stack, traces, specs, request):
    """The one thing the reader actually saw, held as an invariant.

    A lane with no row in it is a strip of background nothing tells apart
    from a dead channel, and there turned out to be two ways to get one.
    F3 then F2 on sixteen channels collapsed the spectrogram onto the
    focused lane and hid the traces everywhere, which left fifteen of
    sixteen lanes drawing nothing -- reachable with two keystrokes, and
    swept across 600 states -- channel count x window size x F3 size x
    `show_traces` x `show_powers` x saved split -- where it emptied 1316
    lanes in 140 of them.  `set_panels(traces=False, specs=0)` empties the
    whole stack; no key reaches that pair, but nothing in `set_panels`
    forbids it, so it is swept here too.

    Swept rather than spot-checked because both bugs were combinations, not
    single settings.  `show_powers` is the one axis dropped from the sweep
    and it was dropped on measurement: over 1860 pairs of lanes it changed
    no lane's visible panels and no row height, the power panel living in
    the figure's second column and taking no row of its own.
    """
    view = request.getfixturevalue(stack)
    before = (view.show_traces, view.show_specs)
    try:
        view.set_panels(traces=traces, specs=specs)
        settle()
        for c in view.visible_channels():
            drawn = [
                (p.name, float(p.axs[c].geometry().height()))
                for p in view.panels.values()
                if not p.is_power()
                and not p.is_spacer()
                and p.axs[c].isVisible()
                and p.axs[c].geometry().height() > 0
            ]
            assert drawn, (
                f"lane {c} of {len(view.visible_channels())} draws nothing "
                f"with traces={traces} specs={specs}"
            )
            # and the rows it draws fill it.  A backstop that puts a panel
            # back without a height leaves the lane looking exactly as
            # broken: the rescued row took 15.5 px of a 29 px lane, because
            # `panel_split_rows` was budgeting from the F2 toggle instead of
            # from what the lane had ended up drawing.
            content = view.lane_content_height(c, float(view.figs[c].height()))
            assert sum(h for _, h in drawn) == pytest.approx(content, abs=1.0), (
                f"lane {c} rows {drawn} do not fill its {content} px "
                f"with traces={traces} specs={specs}"
            )
    finally:
        view.set_panels(traces=before[0], specs=before[1])
        settle()


def test_the_traces_off_stack_gives_every_lane_a_readable_spectrogram(wide_browser):
    """F2 means "spectrograms only", so all sixteen get one - and it scrolls.

    The collapse onto the focused lane is a fallback for a lane that still
    has a trace to show; with the traces off there is nothing to fall back
    on.  So `spectrogram_channels` hands every visible channel a spectrogram
    and `lane_geometry` drops the lane's floor from
    `theme.CHANNEL_DENSE_HEIGHT` -- which is the height a *trace* needs to
    stay readable, and there is no trace here -- to the two px a
    `pyqtgraph.PlotItem` spends on its own margins.

    The cost is a stack taller than its viewport, and it is deliberate:
    sixteen readable spectrograms do not fit in a 1200x900 window and the
    reader asked for sixteen readable spectrograms.
    """
    view = wide_browser
    before = (view.show_traces, view.show_specs)
    try:
        view.set_panels(traces=False, specs=1)
        settle()
        channels = view.visible_channels()
        assert view.spectrogram_channels(channels) == channels
        for c in channels:
            assert not panel(view, "trace").axs[c].isVisible()
            assert row_height(view, "spectrogram", c) == theme.SPECTROGRAM_MIN_HEIGHT, (
                f"lane {c}"
            )
        # every lane is its spectrogram plus the plot's own two px of margin
        assert view.lane_geometry(view.height())[0] == theme.PLOT_FRAME_HEIGHT
        assert figure_height(view, channels[0]) == (
            theme.SPECTROGRAM_MIN_HEIGHT + theme.PLOT_FRAME_HEIGHT
        )
    finally:
        view.set_panels(traces=before[0], specs=before[1])
        settle()


def lane_on_screen(browser, channel):
    """Is this lane wholly inside the scroll area's viewport?"""
    area = browser.stack_area
    fig = browser.figs[channel]
    top = fig.mapTo(area.widget(), QPoint(0, 0)).y()
    value = area.verticalScrollBar().value()
    return (
        value <= top
        and top + max(fig.minimumHeight(), fig.height())
        <= value + area.viewport().height()
    )


@pytest.mark.parametrize("focus", [0, 3, 8, 12, 15])
def test_the_focused_lane_stays_on_screen_when_the_stack_outgrows_the_view(
    wide_browser, focus
):
    """Sixteen spectrograms are four viewports; the selection has to come too.

    The stack marks the focused lane three ways -- a frame around it, a bold
    caption, a rule down its rail row -- and all three are useless where they
    cannot be seen.  F2 with the focus on channel 12 left the reader looking
    at channels 0 to 3.
    """
    view = wide_browser
    before = (view.show_traces, view.show_specs, view.current_channel)
    try:
        view.rail_clicked(focus, False)
        settle()
        view.set_panels(traces=False, specs=1)
        settle()
        pump(0.2)
        assert lane_on_screen(view, focus)
    finally:
        view.set_panels(traces=before[0], specs=before[1])
        view.rail_clicked(before[2], False)
        settle()


def spec_image(browser, channel):
    """The pixels the spectrogram of this lane actually has, or None."""
    ax = panel(browser, "spectrogram").axs[channel]
    for item in ax.data_items:
        image = getattr(item, "image", None)
        if image is not None:
            return image
    return None


def forget_spectrogram(browser, channel):
    """Put a lane's spectrogram back to never having been drawn.

    The stack fixtures are module scoped, so by the time a test runs some
    earlier one may have had every lane visible at once and uploaded all
    sixteen images.  That is exactly the state that hides the bug below, so
    the precondition has to be established rather than assumed.
    """
    ax = panel(browser, "spectrogram").axs[channel]
    for item in ax.data_items:
        if hasattr(item, "_image_range"):
            item.clear()
            item._image_range = None


@pytest.mark.parametrize("gesture", ["next_channel", "rail_clicked"])
def test_the_lane_the_spectrogram_moves_to_actually_draws_one(wide_browser, gesture):
    """Making room for a panel is not the same as drawing in it.

    `Panel.update_plots` skips hidden panels, rightly -- uploading a
    spectrogram nobody can see is what `specitem` exists to avoid -- and the
    only caller that follows a layout pass with one is `set_panels`.  So
    every other way of revealing a panel handed the reader an empty one: on
    sixteen channels, F3 drew a spectrogram on the focused lane and one
    press of the down arrow made room on the next lane and left it blank.

    It stayed blank until something else moved the range, which is why
    pressing F2 twice appeared to cure it for good: that pass has every lane
    visible at once, so every `SpecItem` uploads and none is ever empty
    again.  A test that asks `isVisible()` sees none of this -- the panel is
    visible, the row is the right height, and there is nothing in it -- so
    this one asks for the image.
    """
    view = wide_browser
    restore = view.current_channel
    try:
        view.rail_clicked(4, False)
        settle()
        pump(0.4)
        assert spec_channel(view) == 4
        forget_spectrogram(view, 5)
        assert spec_image(view, 5) is None
        if gesture == "rail_clicked":
            view.rail_clicked(5, False)
        else:
            view.next_channel()
        settle()
        pump(0.6)
        assert view.current_channel == 5
        assert spec_channel(view) == 5
        image = spec_image(view, 5)
        assert image is not None and image.size > 0, (
            f"lane 5 has a {row_height(view, 'spectrogram', 5)} px spectrogram "
            f"row with nothing in it"
        )
    finally:
        view.rail_clicked(restore, False)
        settle()


def test_an_arrow_key_steps_over_a_channel_that_is_not_drawn(wide_browser):
    """The focus has to land somewhere the reader can see.

    `show_channels` is the window the stack is scrolled to; `mute` and its
    friends take channels out of that afterwards.  Walking the first could
    put the focus on a lane that is not on screen, which was harmless only
    while nothing read the focus back: `spectrogram_channels` falls back to
    `channels[0]` when the focused channel is not among them, so once
    `focus_channel` started relaying the stack out, one press of the down
    arrow onto muted channel 5 moved the spectrogram from lane 4 to lane 0
    -- four lanes the wrong way, and the frame onto a lane with no figure.
    """
    view = wide_browser
    restore = view.current_channel
    try:
        view.toggle_mute(5)
        settle()
        view.rail_clicked(4, False)
        settle()
        assert spec_channel(view) == 4
        view.next_channel()
        settle()
        assert view.current_channel == 6
        assert view.current_channel in view.visible_channels()
        assert spec_channel(view) == 6
        view.previous_channel()
        settle()
        assert view.current_channel == 4
        assert spec_channel(view) == 4
    finally:
        if 5 in view.muted_channels:
            view.toggle_mute(5)
        settle()
        view.rail_clicked(restore, False)
        settle()


@pytest.mark.parametrize(
    "gesture",
    [
        "solo",
        "mute",
        "maximize",
        "show_channel",
        "hide_deselected_channels",
        "select_previous_channel",
        "resize",
    ],
)
def test_every_gesture_that_re_flows_the_stack_keeps_the_focus_on_screen(
    wide_browser, gesture
):
    """Not just the two gestures the scroll was first hung off.

    Sixteen lanes are taller than the viewport even with the traces on --
    679 px against 500 -- so any gesture that puts every channel back while
    the focus is near the bottom can leave it off screen.  With the
    spectrogram collapsed onto that lane, this *is* the reported bug through
    a side door: one solo and un-solo of channel 15 left the stack's only
    spectrogram below the fold, no F2 involved.  So the scroll hangs off
    `update_stretches`, where every lane height in this application is set,
    rather than off the gestures that were noticed first.
    """
    view = wide_browser
    before = (view.show_traces, view.show_specs, view.current_channel)
    size = view.window().size()
    try:
        view.rail_clicked(15, False)
        settle()
        pump(0.2)
        assert lane_on_screen(view, 15), "the setup itself did not scroll"
        if gesture == "solo":
            view.toggle_solo(15)
            settle()
            view.toggle_solo(15)
        elif gesture == "mute":
            view.toggle_mute(0)
            settle()
            view.toggle_mute(0)
        elif gesture == "maximize":
            view.toggle_maximize(15)
            settle()
            view.toggle_maximize(15)
        elif gesture == "show_channel":
            view.show_channel(15)
            settle()
            view.show_channel(15)
        elif gesture == "hide_deselected_channels":
            view.hide_deselected_channels()
            settle()
            view.set_channels(list(range(16)))
        elif gesture == "select_previous_channel":
            for _ in range(6):
                view.select_next_channel()
                settle()
            view.select_previous_channel()
        elif gesture == "resize":
            view.window().resize(1200, 400)
        settle()
        pump(0.3)
        assert lane_on_screen(view, view.current_channel), (
            f"{gesture} left lane {view.current_channel} off screen"
        )
    finally:
        view.window().resize(size)
        view.set_channels(list(range(16)))
        view.set_panels(traces=before[0], specs=before[1])
        view.rail_clicked(before[2], False)
        settle()
        pump(0.3)


def test_the_time_axis_never_maps_its_ticks_through_a_hidden_plot(wide_browser):
    """With the traces off, the axis has to follow the spectrogram.

    `link_time_axis` reached for `trace_plot`, which is the lane's first
    visible *trace* and is None once F2 has hidden every one of them; it
    returned early and left the axis linked to the hidden trace's view box.
    Measured on two channels: that box is 1037 px wide while the
    spectrogram the ticks are drawn under is 981.
    """
    view = wide_browser
    before = (view.show_traces, view.show_specs)
    try:
        view.set_panels(traces=False, specs=1)
        settle()
        linked = view.taxis.linkedView()
        assert linked is not None
        owners = [
            p
            for p in view.panels.values()
            if not p.is_power()
            and not p.is_spacer()
            and any(ax.getViewBox() is linked for ax in p.axs)
        ]
        assert owners and owners[0].is_spectrogram()
        assert all(
            ax.isVisible() for p in owners for ax in p.axs if ax.getViewBox() is linked
        )
    finally:
        view.set_panels(traces=before[0], specs=before[1])
        settle()


# ----------------------------------------------------------------- the cost


def test_a_drag_step_costs_a_fraction_of_the_python_a_full_layout_does(wide_browser):
    """What one mouse move costs *in Python*, which is not what it costs.

    Nothing is processed between the steps below, so Qt never paints: this
    measures `apply_panel_split` and nothing else.  That is the number worth
    fencing, because it is the one this file's code decides -- a full
    `adjust_layout` on this stack is 5.2 ms of Python, a third of a 60 Hz
    frame before anything has been drawn, and a drag runs two row heights
    and a chrome comparison instead.

    The frame is the larger half and it is not here: with one
    `processEvents` a move, the same drag is 6.9 ms on this stack, 22.6 on
    four lanes and 24.3 on two, nearly all of it the relayout's repaint.
    That cost is the boundary moving, so there is nothing to fence it
    against; see `apply_panel_split`.

    The bound is loose because it is a fence against a slow machine, not a
    benchmark; what the test really pins is the line below it, which is the
    thing that would regress.
    """
    c = spec_channel(wide_browser)
    spec_h, room = wide_browser.panel_split_heights(c)
    steps = [spec_h - (i % 21) for i in range(200)]
    start = time.perf_counter()
    for target in steps:
        wide_browser.drag_panel_split(target, room)
    per_step_ms = 1000.0 * (time.perf_counter() - start) / len(steps)
    assert per_step_ms < 1.0, f"{per_step_ms:.3f} ms per drag step"


def test_a_focus_move_relayouts_only_where_the_spectrogram_has_to_move(
    roomy_browser, wide_browser
):
    """The arrow key must not buy a relayout it has no use for.

    Two channels draw a spectrogram each, so stepping between them changes
    nothing about which lanes have one and `focus_channel` has nothing to
    lay out: measured 1.4 ms a press, against 13.5 ms on sixteen where the
    spectrogram really does move lanes and the layout really is the work.
    Both are inside a 60 Hz frame; the one that would regress is the first,
    if `focus_channel` ever started relayouting unconditionally.

    Counted rather than timed, because a wall clock on a loaded machine
    fences nothing: what is asserted is the number of `adjust_layout` calls
    one press makes.
    """
    for view, expected in ((roomy_browser, 0), (wide_browser, 1)):
        before = view.current_channel
        calls = []
        original = view.adjust_layout
        view.adjust_layout = lambda w, h, _c=calls, _o=original: (
            _c.append(1),
            _o(w, h),
        )[1]
        try:
            view.next_channel()
            settle()
        finally:
            del view.adjust_layout
        assert len(calls) == expected, (
            f"{len(calls)} relayouts for one arrow key on "
            f"{len(view.visible_channels())} channels"
        )
        view.rail_clicked(before, False)
        settle()


def test_a_drag_step_writes_no_axis_style_it_did_not_have_to(
    roomy_browser, roomy_reset, monkeypatch
):
    """Why the drag is 24 ms a move and not 42.

    `AxisItem.setStyle` has no idea what it already holds: it drops the
    cached picture, re-measures the axis -- which resizes it, invalidating
    the layout a second time -- and schedules a repaint, whatever it was
    handed.  The chrome decision runs on every step of the drag now, so
    re-stating it rather than comparing it costs a repaint of every axis in
    every lane, twice over: measured on this stack at 42.0 ms per move
    against 24.3, and 65 axis paints per move against 37.
    """
    import pyqtgraph as pg

    calls = []
    original = pg.AxisItem.setStyle
    monkeypatch.setattr(
        pg.AxisItem,
        "setStyle",
        lambda self, **k: (calls.append(k), original(self, **k))[1],
    )
    c = spec_channel(roomy_browser)
    spec_h, room = roomy_browser.panel_split_heights(c)
    for step in range(1, 21):
        drag(roomy_browser, spec_h - step, room)
    assert calls == []


def test_a_drag_never_runs_the_full_layout_and_the_release_runs_it_once(
    wide_browser, monkeypatch
):
    """The claim the timing above is a proxy for, pinned directly."""
    calls = []
    original = wide_browser.adjust_layout
    monkeypatch.setattr(
        wide_browser,
        "adjust_layout",
        lambda *a, **k: (calls.append(1), original(*a, **k))[1],
    )
    c = spec_channel(wide_browser)
    spec_h, room = wide_browser.panel_split_heights(c)
    for step in range(-20, 1):
        wide_browser.drag_panel_split(spec_h + step, room)
    assert calls == []
    wide_browser.finish_panel_split()
    assert len(calls) == 1
    settle()
    wide_browser.spec_scale = None
    original(wide_browser.width(), wide_browser.height())
    settle()


# ------------------------------------------------- double click to reset
#
# The gesture `PanelSplitter` already has on the other thing a reader drags
# inside a lane: back to the way the lane opened.


def axis_centre(ax, side="left"):
    """A point inside one axis of `ax`, in scene coordinates."""
    axis = ax.getAxis(side)
    return axis.mapRectToScene(axis.boundingRect()).center()


def send_at(browser, channel, kind, x, y, button, buttons):
    """One real mouse event at a scene point.

    `send` above pins x at 200, which is right for the grab band -- it
    reaches across the lane -- and wrong for an axis, which is a column
    56 px wide at the left edge.
    """
    viewport = browser.figs[channel].viewport()
    pos = QPoint(int(round(x)), int(round(y)))
    QApplication.instance().sendEvent(
        viewport,
        QMouseEvent(
            kind,
            QPointF(pos),
            QPointF(viewport.mapToGlobal(pos)),
            button,
            buttons,
            Qt.NoModifier,
        ),
    )
    settle()


def click_axis(browser, channel, ax, double, side="left"):
    """Click, or double click, the axis of one lane."""
    at = axis_centre(ax, side)
    x, y = at.x(), at.y()
    send_at(browser, channel, QEvent.MouseMove, x, y, Qt.NoButton, Qt.NoButton)
    send_at(
        browser, channel, QEvent.MouseButtonPress, x, y, Qt.LeftButton, Qt.LeftButton
    )
    send_at(
        browser, channel, QEvent.MouseButtonRelease, x, y, Qt.LeftButton, Qt.NoButton
    )
    if double:
        # pyqtgraph turns this into an ordinary MouseClickEvent with
        # `double()` set -- `QGraphicsItem.mouseDoubleClickEvent` is never
        # reached, so sending the Qt double-click event is what exercises it
        send_at(
            browser,
            channel,
            QEvent.MouseButtonDblClick,
            x,
            y,
            Qt.LeftButton,
            Qt.LeftButton,
        )
        send_at(
            browser,
            channel,
            QEvent.MouseButtonRelease,
            x,
            y,
            Qt.LeftButton,
            Qt.NoButton,
        )
    pump(0.2)


@pytest.mark.parametrize("kind", ["trace", "spectrogram"])
def test_double_clicking_a_y_axis_puts_it_back_where_the_lane_opened(browser, kind):
    """One gesture, and it is not the same call on the two axes.

    A frequency axis opens at its full range; an amplitude axis opens
    *fitted to the data*.  `reset` on the amplitude is what Shift+V does and
    goes to the format's full scale -- measured, a trace sitting in
    -0.117..0.129 goes to -1.000..1.000 -- which for recordings that peak at
    a few percent of full scale is a flat line, not a reset.
    """
    c = spec_channel(browser) if kind == "spectrogram" else 0
    ax = panel(browser, kind).axs[c]
    view = ax.getViewBox()
    _t, (y0, y1) = view.viewRange()
    view.setYRange(y0 + 0.3 * (y1 - y0), y0 + 0.6 * (y1 - y0), padding=0)
    settle()
    _t, (z0, z1) = view.viewRange()
    assert (z0, z1) != pytest.approx((y0, y1))
    click_axis(browser, c, ax, double=True)
    _t, (w0, w1) = view.viewRange()
    assert (w0, w1) == pytest.approx((y0, y1), abs=1e-6)


def test_a_single_click_on_a_y_axis_is_still_forwarded_to_the_view_box(browser):
    """The axis still hands an ordinary click on to its view box.

    Asserting only that the range did not move proves nothing: that is just
    as true when the click is swallowed, and swallowing it is exactly what a
    ``return`` in place of the ``super()`` call would do.  So this counts the
    forwarding, and checks the range as well.
    """
    ax = panel(browser, "trace").axs[1]
    view = ax.getViewBox()
    seen = []
    original = view.mouseClickEvent
    view.mouseClickEvent = lambda ev: seen.append(ev.double()) or original(ev)
    _t, (y0, y1) = view.viewRange()
    try:
        view.setYRange(y0 + 0.3 * (y1 - y0), y0 + 0.6 * (y1 - y0), padding=0)
        settle()
        _t, (z0, z1) = view.viewRange()
        click_axis(browser, 1, ax, double=False)
        assert seen == [False], "the axis swallowed a plain click"
        _t, (w0, w1) = view.viewRange()
        assert (w0, w1) == pytest.approx((z0, z1))
    finally:
        view.mouseClickEvent = original
        view.setYRange(y0, y1, padding=0)
        settle()


@pytest.mark.parametrize("button", ["right", "middle"])
def test_only_the_left_button_resets_an_axis(browser, button):
    """Nothing held the button guard: taking it out left every test green."""
    ax = panel(browser, "trace").axs[1]
    view = ax.getViewBox()
    which = Qt.RightButton if button == "right" else Qt.MiddleButton
    _t, (y0, y1) = view.viewRange()
    try:
        view.setYRange(y0 + 0.3 * (y1 - y0), y0 + 0.6 * (y1 - y0), padding=0)
        settle()
        _t, (z0, z1) = view.viewRange()
        at = axis_centre(ax)
        x, y = at.x(), at.y()
        send_at(browser, 1, QEvent.MouseMove, x, y, Qt.NoButton, Qt.NoButton)
        send_at(browser, 1, QEvent.MouseButtonPress, x, y, which, which)
        send_at(browser, 1, QEvent.MouseButtonRelease, x, y, which, Qt.NoButton)
        send_at(browser, 1, QEvent.MouseButtonDblClick, x, y, which, which)
        send_at(browser, 1, QEvent.MouseButtonRelease, x, y, which, Qt.NoButton)
        pump(0.2)
        _t, (w0, w1) = view.viewRange()
        assert (w0, w1) == pytest.approx((z0, z1))
    finally:
        view.setYRange(y0, y1, padding=0)
        settle()


def test_a_double_click_beside_the_axis_does_not_reset(browser):
    """`pg.GraphicsScene` is built with ``clickRadius=2``, so an event lands
    on an item from up to two pixels away.

    Measured before the axis checked where the press actually was, on a lane
    whose view box spans x 61 to 996.5: a double click at x 62, 63, 994 and
    995 reset the range -- over the waveform, and on the right hand side
    where no axis is drawn at all.
    """
    ax = panel(browser, "trace").axs[1]
    view = ax.getViewBox()
    left = ax.getAxis("left")
    edge = left.mapRectToScene(left.boundingRect()).right()
    at = axis_centre(ax)
    _t, (y0, y1) = view.viewRange()
    try:
        for dx in (1, 2, 3):
            view.setYRange(y0 + 0.3 * (y1 - y0), y0 + 0.6 * (y1 - y0), padding=0)
            settle()
            _t, (z0, z1) = view.viewRange()
            x = edge + dx
            send_at(browser, 1, QEvent.MouseMove, x, at.y(), Qt.NoButton, Qt.NoButton)
            send_at(
                browser,
                1,
                QEvent.MouseButtonPress,
                x,
                at.y(),
                Qt.LeftButton,
                Qt.LeftButton,
            )
            send_at(
                browser,
                1,
                QEvent.MouseButtonRelease,
                x,
                at.y(),
                Qt.LeftButton,
                Qt.NoButton,
            )
            send_at(
                browser,
                1,
                QEvent.MouseButtonDblClick,
                x,
                at.y(),
                Qt.LeftButton,
                Qt.LeftButton,
            )
            send_at(
                browser,
                1,
                QEvent.MouseButtonRelease,
                x,
                at.y(),
                Qt.LeftButton,
                Qt.NoButton,
            )
            pump(0.2)
            _t, (w0, w1) = view.viewRange()
            assert (w0, w1) == pytest.approx((z0, z1)), (
                f"{dx} px past the axis reset it"
            )
    finally:
        view.setYRange(y0, y1, padding=0)
        settle()


def test_a_collapsed_axis_carries_no_gesture(wide_browser):
    """The dense stack -- sixteen channels with the spectrograms off -- takes
    the y gutter to nothing, and both axes measure 0.0 px wide.

    Wiring the gesture onto an axis nobody can see would leave it reachable
    only through the two pixel fringe over the data, with nothing on screen
    to say it was there.
    """
    specs = wide_browser.show_specs
    try:
        wide_browser.set_panels(traces=True, specs=0)
        settle()
        pump(0.3)
        c = wide_browser.visible_channels()[0]
        ax = panel(wide_browser, "trace").axs[c]
        left = ax.getAxis("left")
        assert left.boundingRect().width() == 0.0
        view = ax.getViewBox()
        _t, (y0, y1) = view.viewRange()
        view.setYRange(y0 + 0.3 * (y1 - y0), y0 + 0.6 * (y1 - y0), padding=0)
        settle()
        _t, (z0, z1) = view.viewRange()
        click_axis(wide_browser, c, ax, double=True)
        _t, (w0, w1) = view.viewRange()
        assert (w0, w1) == pytest.approx((z0, z1))
    finally:
        wide_browser.set_panels(traces=True, specs=specs)
        # and put the range back: the squash above is this test's, and the
        # stack fixture is module scoped
        wide_browser.auto_ampl()
        settle()


def test_the_gesture_leaves_fixed_amplitude_mode_alone(browser):
    """In fixed +-1 the lane opened at +-1, so a refit is exactly not "back
    to the way the lane opened" -- and it broke the reader out of a mode the
    tool bar went on claiming.  Measured before this: y_fixed at (-1.0, 1.0),
    double click, (-0.116965, 0.128933), menu still reading "Y: fixed +-1".
    """
    mode = browser.y_mode
    picked = list(browser.selected_channels)
    current = browser.current_channel
    try:
        # Focus the lane this test reads, and do it first.  A click on an
        # axis is forwarded to the view box and `mouse_clicked` focuses the
        # lane it lands in, so the double click below narrows the selection
        # to channel 0 -- and `set_ranges` only touches `range_channels()`.
        # A test that set the mode while a different lane was selected would
        # find channel 0 still fitted and blame the gesture.
        browser.rail_clicked(0, False)
        browser.set_y_mode(DataBrowser.y_fixed)
        settle()
        ax = panel(browser, "trace").axs[0]
        view = ax.getViewBox()
        _t, (y0, y1) = view.viewRange()
        assert (y0, y1) == pytest.approx((-1.0, 1.0))
        view.setYRange(-0.4, 0.2, padding=0)
        settle()
        click_axis(browser, 0, ax, double=True)
        _t, (w0, w1) = view.viewRange()
        assert (w0, w1) == pytest.approx((-1.0, 1.0))
        assert browser.y_mode == DataBrowser.y_fixed
    finally:
        browser.set_y_mode(mode)
        browser.set_channels(selected_channels=picked, current_channel=current)
        settle()


def test_a_frequency_reset_follows_the_y_mode_like_every_other_range_command(
    roomy_browser,
):
    """`apply_ranges` passes `range_channels()`, which is the *selection* in
    per-channel y mode -- so a frequency reset moves the selected lanes and
    leaves the rest, exactly as `Ctrl+Left` does.

    Measured on two channels with only channel 0 selected, both squashed to
    1200-2400 Hz: channel 0 came back to 0-4000 and channel 1 stayed
    squashed.  A gesture that quietly reached further than the keys would be
    the surprise, so this pins the behaviour rather than wishing it away.
    """
    mode = roomy_browser.y_mode
    picked = list(roomy_browser.selected_channels)
    current = roomy_browser.current_channel
    try:
        roomy_browser.set_y_mode(DataBrowser.y_per_channel)
        roomy_browser.rail_clicked(0, False)
        settle()
        shown = roomy_browser.visible_channels()
        assert len(shown) > 1
        axs = [panel(roomy_browser, "spectrogram").axs[c] for c in shown]
        full = [ax.getViewBox().viewRange()[1] for ax in axs]
        for ax in axs:
            ax.getViewBox().setYRange(1200.0, 2400.0, padding=0)
        settle()
        click_axis(roomy_browser, shown[0], axs[0], double=True)
        after = [ax.getViewBox().viewRange()[1] for ax in axs]
        assert after[0] == pytest.approx(full[0], abs=1e-6)
        assert after[1] == pytest.approx((1200.0, 2400.0), abs=1e-6)
    finally:
        roomy_browser.set_y_mode(mode)
        roomy_browser.set_channels(selected_channels=picked, current_channel=current)
        for ax in panel(roomy_browser, "spectrogram").axs:
            ax.getViewBox().setYRange(0.0, 4000.0, padding=0)
        settle()


def test_an_amplitude_reset_reaches_every_lane(wide_browser):
    """`auto_fit_y` fits every visible channel whatever the selection is.

    An amplitude is comparable across electrodes or it is not a measurement,
    so refitting one lane of sixteen would leave a stack that cannot be read
    across."""
    shown = wide_browser.visible_channels()
    assert len(shown) > 1
    axs = [panel(wide_browser, "trace").axs[c] for c in shown]
    before = [ax.getViewBox().viewRange()[1] for ax in axs]
    for ax in axs:
        vb = ax.getViewBox()
        _t, (y0, y1) = vb.viewRange()
        vb.setYRange(y0 + 0.3 * (y1 - y0), y0 + 0.6 * (y1 - y0), padding=0)
    settle()
    click_axis(wide_browser, shown[0], axs[0], double=True)
    after = [ax.getViewBox().viewRange()[1] for ax in axs]
    for (b0, b1), (a0, a1) in zip(before, after):
        assert (a0, a1) == pytest.approx((b0, b1), abs=1e-6)


def test_only_the_y_axes_are_wired_and_only_a_drawn_one_answers(browser):
    """Both y axes are wired; only the one with width answers.

    The right axis is wired for the same reason the left is -- it is a
    `YAxisItem` and could be given width -- but measured it is 0.0 px wide on
    every lane, so the geometry test in `mouseClickEvent` is what decides,
    not the wiring.  Saying "the right axis is still an axis to click" would
    be a claim about a column that is not there.
    """
    for kind in ("trace", "spectrogram"):
        c = spec_channel(browser) if kind == "spectrogram" else 0
        ax = panel(browser, kind).axs[c]
        for side in ("left", "right"):
            assert ax.getAxis(side)._on_reset is not None, (kind, side)
        assert ax.getAxis("left").boundingRect().width() > 0
        assert ax.getAxis("right").boundingRect().width() == 0.0
    # and the time axis is not a y axis: nothing was wired to it
    ax = panel(browser, "trace").axs[0]
    for side in ("bottom", "top"):
        assert not hasattr(ax.getAxis(side), "_on_reset"), side


def test_an_old_spectrogram_size_arrives_as_simply_on(browser, split_reset):
    """`show_specs` was an F3 size as well as an on/off, and the sizes are
    gone.  A 2 from a caller written against the old meaning, or from a
    settings file, is "on" -- and is stored as "on", rather than left in the
    field for the next reader to wonder about."""
    browser.set_panels(specs=3)
    settle()
    assert browser.show_specs == 1
    c = spec_channel(browser)
    assert row_height(browser, "spectrogram", c) == pytest.approx(
        theme.SPECTROGRAM_MIN_HEIGHT, abs=1
    )
    browser.set_panels(specs=0)
    settle()
    assert browser.show_specs == 0


# ----------------------------------------------- the keys the axes now share
#
# The reported defect: "v/ctrl+v only resets the trace y axis and double
# click only resets the spec y axis".  Measured, the halves were these -- the
# frequency axis had the gesture and no key at all (`setup_frequency_actions`
# defined link, two zooms, up, down, home and end, and neither a fit nor a
# reset), while the amplitude axis had `v` and `Shift+V` as well as the
# gesture.  So each axis had half the vocabulary, and `Ctrl+V` was bound to
# nothing anywhere in the tree.


@pytest.mark.parametrize("kind", ["trace", "spectrogram"])
def test_the_double_click_and_the_lane_s_own_key_agree(browser, kind):
    """The user's sentence as an assertion: on both views, both actions do
    the same thing.

    Squash the lane, double click its axis, record; squash it identically,
    press the lane's bare key, record; the two must be the same range.  It
    was already true on the trace by coincidence -- the gesture calls
    `gui.auto_amplitude()`, which is exactly what `v` is connected to -- and
    is now true on the spectrogram by construction, because both go through
    `apply_ranges("default_view", ...)`.

    Red before this commit on the spectrogram half with ``AttributeError:
    'Actions' object has no attribute 'default_view_frequency'``.
    """
    gui = browser.window()
    c = spec_channel(browser) if kind == "spectrogram" else 0
    ax = panel(browser, kind).axs[c]
    view = ax.getViewBox()
    _t, (y0, y1) = view.viewRange()
    squashed = (y0 + 0.3 * (y1 - y0), y0 + 0.6 * (y1 - y0))

    view.setYRange(*squashed, padding=0)
    settle()
    click_axis(browser, c, ax, double=True)
    _t, by_mouse = view.viewRange()

    view.setYRange(*squashed, padding=0)
    settle()
    act = (
        gui.acts.default_view_frequency
        if kind == "spectrogram"
        else gui.acts.auto_zoom_amplitude
    )
    act.trigger()
    settle()
    _t, by_key = view.viewRange()

    assert by_mouse == pytest.approx(by_key, abs=1e-6)


def test_the_frequency_axis_has_a_key_back_to_its_band(browser):
    """Ctrl+V, which was bound to nothing at all before this commit.

    With no band configured `default_view` and `reset` are the same call, so
    this returns the whole axis -- measured 0 to 4000 Hz on the 8 kHz
    fixture.  What it is *for* is the case where a band is set; see
    tests/test_specband.py.
    """
    gui = browser.window()
    assert gui.acts.default_view_frequency.shortcut().toString() == "Ctrl+V"
    c = spec_channel(browser)
    ax = panel(browser, "spectrogram").axs[c]
    view = ax.getViewBox()
    view.setYRange(1200, 2400, padding=0)
    settle()
    gui.acts.default_view_frequency.trigger()
    settle()
    _t, (w0, w1) = view.viewRange()
    assert (w0, w1) == pytest.approx((0.0, 4000.0))


def test_the_frequency_axis_has_a_key_all_the_way_out(browser):
    """Ctrl+Shift+V, the escape hatch.

    A reader who sets a 2 kHz band has to be able to get back to the whole
    axis, and that route must not be folklore -- it is in the Frequency
    menu, on the cheat sheet, and in the Opens-at field's own tool tip.
    """
    gui = browser.window()
    assert gui.acts.reset_frequency.shortcut().toString() == "Ctrl+Shift+V"
    c = spec_channel(browser)
    ax = panel(browser, "spectrogram").axs[c]
    view = ax.getViewBox()
    view.setYRange(1200, 2400, padding=0)
    settle()
    gui.acts.reset_frequency.trigger()
    settle()
    _t, (w0, w1) = view.viewRange()
    assert (w0, w1) == pytest.approx((0.0, 4000.0))


def test_the_way_out_is_discoverable(browser):
    """A key that works and that nothing tells the reader about is folklore.

    Both new actions have to reach the command palette (which walks the
    menus) and the cheat sheet (which reads `CheatSheet.GROUPS` by name).
    """
    from audian.audian import CheatSheet

    gui = browser.window()
    named = {name for _title, names in CheatSheet.GROUPS for name in names}
    assert "default_view_frequency" in named
    assert "reset_frequency" in named

    actions = [act for act, _path in gui.all_actions()]
    assert gui.acts.default_view_frequency in actions
    assert gui.acts.reset_frequency in actions


def test_the_two_fit_actions_are_not_the_same_command(browser):
    """"Fit Y" names the amplitude axis -- the tool bar button and the
    "Y: fixed +-1" readout both use Y that way -- so the frequency entry is
    "Fit" and not "Fit Y".

    `all_actions` dedupes by `id(act)` and the palette renders text plus
    menu path, so two identically worded rows under View > Amplitude and
    View > Frequency would read as a bug.
    """
    gui = browser.window()
    assert gui.acts.auto_zoom_amplitude.text() == "&Fit Y"
    assert gui.acts.default_view_frequency.text() == "&Fit"
    assert gui.acts.reset_frequency.text() == "&Reset"

    entries = gui.all_actions()
    rendered = {f"{act.text().replace('&', '')}  {path}" for act, path in entries}
    assert len(rendered) == len(entries)


def test_v_leaves_frequency_alone_and_ctrl_v_leaves_amplitude_alone(browser):
    """The keys stay per axis kind.

    `v` takes `Panel.amplitudes` and Ctrl+V takes `Panel.frequencies`; a
    later change making either axis-agnostic across kinds would be a
    surprise, because the reader presses one to fix one lane.
    """
    gui = browser.window()
    c = spec_channel(browser)
    fax = panel(browser, "spectrogram").axs[c]
    aax = panel(browser, "trace").axs[0]
    fview, aview = fax.getViewBox(), aax.getViewBox()

    fview.setYRange(1200, 2400, padding=0)
    settle()
    _t, before = fview.viewRange()
    gui.acts.auto_zoom_amplitude.trigger()
    settle()
    _t, after = fview.viewRange()
    assert after == pytest.approx(before), "v moved a frequency axis"

    _t, before_a = aview.viewRange()
    gui.acts.default_view_frequency.trigger()
    settle()
    _t, after_a = aview.viewRange()
    assert after_a == pytest.approx(before_a), "Ctrl+V moved an amplitude axis"


def test_the_trace_double_click_still_fits_rather_than_resets(browser):
    """A pin on 10b8832, which measured this and chose it deliberately.

    `reset` on an amplitude goes to the format's full scale -- (-1.0, 1.0)
    -- and on a recording peaking at a few percent of it that is a flat
    line, not a reset.  The fitted answer is (-0.116965, 0.128933).  This
    goes red the moment somebody folds the amplitude branch of
    `reset_y_range` onto `default_view` for symmetry's sake.
    """
    ax = panel(browser, "trace").axs[0]
    view = ax.getViewBox()
    view.setYRange(-0.16, 0.08, padding=0)
    settle()
    click_axis(browser, 0, ax, double=True)
    _t, (w0, w1) = view.viewRange()
    assert (w0, w1) != pytest.approx((-1.0, 1.0))
    assert (w0, w1) == pytest.approx((-0.116965, 0.128933), abs=1e-4)


# --------------------------------------------- the band a spectrogram opens at
#
# These live here, and not in a module of their own, because a module of
# their own needs its own browser fixtures and **two more windows in the
# process is enough to take the whole suite down**.  Measured, three ways:
# pristine master runs 747 passed with no crash; this branch with the band
# tests in a separate file segfaulted in two runs out of four, always inside
# `theme.collect_orphan_widgets` at `widget.parentWidget()`, once while
# building `test_parameterbar`'s fixture and once while building the band
# module's own; and this branch with that file removed runs 755 passed
# clean.  The three modules in isolation
# (`test_panelsplitter test_parameterbar test_specband`) also pass -- 165 of
# them -- so it is the whole suite's accumulated widget state and not any
# one module.
#
# The latent defect is `collect_orphan_widgets` walking a snapshot of
# `QApplication.topLevelWidgets()` and reparenting inside the same loop, so
# a `setParent` can destroy a widget a later iteration then dereferences.
# It is pre-existing and untouched by this commit; it is in todo.md with
# these numbers.  Reusing the `browser` fixture that is already here costs
# no window at all, which is the cheap way not to trip it.


@pytest.fixture
def band_reset(browser):
    """Put the band back: `browser` is module scoped and widely shared."""
    yield
    browser.set_spectrogram_band(None, 0.5 * browser.data.rate)
    settle()


def band_settings_path():
    """The redirected settings file of whichever browser is current."""
    import audian.audian as audian_app

    return Path(audian_app.settings_path())


def stored_band():
    path = band_settings_path()
    if not path.exists():
        return None
    with open(path) as sf:
        return json.load(sf).get(DataBrowser.SPEC_BAND_SETTING)


def write_raw_band(value):
    path = band_settings_path()
    existing = {}
    if path.exists():
        with open(path) as sf:
            existing = json.load(sf)
    existing[DataBrowser.SPEC_BAND_SETTING] = value
    with open(path, "w") as sf:
        json.dump(existing, sf)


def clear_raw_band():
    path = band_settings_path()
    if not path.exists():
        return
    with open(path) as sf:
        existing = json.load(sf)
    existing.pop(DataBrowser.SPEC_BAND_SETTING, None)
    with open(path, "w") as sf:
        json.dump(existing, sf)


def freq_view(browser):
    c = spec_channel(browser)
    return panel(browser, "spectrogram").axs[c].getViewBox().viewRange()[1]


# ---- a default, and not a limit -------------------------------------------
#
# The whole design in one idea.  The tempting implementation puts the
# reader's 2 kHz into `PlotRange.rmax`, and it looks right -- the lane opens
# at 0-2 kHz.  It is wrong in a way nothing on screen says: `rmax` is what
# `set_ranges` clips to and what `set_limits` hands `setLimits`, so Nyquist
# stops existing.


def test_a_band_changes_where_the_lane_opens_and_not_where_it_can_go(
    browser, band_reset
):
    """Red without the feature, and red *again* on the `rmax` implementation.

    An `rmax` band gives ``yLimits == [0, 2000]`` and clips the hand-set
    range to it, so the second and third assertions are the ones that tell a
    default view apart from a ceiling.
    """
    browser.set_spectrogram_band(None, 2000.0)
    settle()
    assert freq_view(browser) == pytest.approx((0.0, 2000.0))

    c = spec_channel(browser)
    vb = panel(browser, "spectrogram").axs[c].getViewBox()
    assert vb.state["limits"]["yLimits"] == [0, 4000.0]

    vb.setYRange(3000, 4000, padding=0)
    settle()
    assert vb.viewRange()[1] == pytest.approx((3000.0, 4000.0))


def test_end_still_reaches_nyquist_with_a_band_in_force(browser, band_reset):
    """Under an `rmax` band `end` would stop at 2 kHz -- and stopping there
    is indistinguishable, on screen, from a recording with no energy above
    it."""
    browser.set_spectrogram_band(None, 2000.0)
    settle()
    browser.apply_ranges("end", "f")
    settle()
    assert freq_view(browser) == pytest.approx((2000.0, 4000.0))


def test_the_deepest_zoom_does_not_move_when_a_band_is_set(browser, band_reset):
    """`min_dr = (rmax - rmin) / 2**16`, so an `rmax` band would halve it.

    Nothing else in the suite can see that: it changes how far a reader can
    zoom in, four figures down, and no view range ever reports it.
    """
    frange = browser.plot_ranges["f"]
    before = frange.min_dr
    assert before == pytest.approx(0.06103515625)
    browser.set_spectrogram_band(None, 2000.0)
    settle()
    assert frange.min_dr == pytest.approx(before)
    assert frange.rmax == pytest.approx(4000.0)


def test_four_zoom_outs_from_the_band_reach_nyquist(browser, band_reset):
    """A route out that does not go through the new key, so that rebinding
    Ctrl+Shift+V later cannot strand a reader inside a band."""
    browser.set_spectrogram_band(None, 2000.0)
    settle()
    for _ in range(4):
        browser.apply_ranges("zoom_out", "f")
        settle()
    assert freq_view(browser) == pytest.approx((0.0, 4000.0))


# ---- what a lane opens at, with no window involved ------------------------


def test_set_limits_opens_a_range_at_its_band_and_clips_it_to_its_limit():
    """The core of the feature, on a bare `PlotRange`.

    This is where "opens at" actually lives, and asserting it here rather
    than through a second browser is what keeps this file to the windows it
    already had.  `set_limits` is the same call `DataBrowser.open` makes.
    """
    from audian.plotranges import PlotRange

    class FakeAxis:
        def range(self, axspec):
            return 0, 4000.0, None

        def setLimits(self, **kwargs):
            self.limits = kwargs

        def setYRange(self, r0, r1):
            self.yrange = (r0, r1)

    r = PlotRange("f", 1)
    r.add_yaxis(FakeAxis(), 0)

    r.set_limits()
    assert (r.r0[0], r.r1[0]) == pytest.approx((0.0, 4000.0))

    r.set_default_max(2000.0)
    r.set_limits()
    assert (r.r0[0], r.r1[0]) == pytest.approx((0.0, 2000.0))
    assert r.rmax == pytest.approx(4000.0), "the band must not become the limit"

    # and `reset` is still the whole axis, which is the way back out
    r.reset(do_set=False)
    assert (r.r0[0], r.r1[0]) == pytest.approx((0.0, 4000.0))
    r.default_view(do_set=False)
    assert (r.r0[0], r.r1[0]) == pytest.approx((0.0, 2000.0))


def test_a_band_larger_than_nyquist_is_clamped_not_obeyed():
    """A preference outlives the recording it was written beside."""
    from audian.plotranges import PlotRange

    class FakeAxis:
        def range(self, axspec):
            return 0, 4000.0, None

        def setLimits(self, **kwargs):
            pass

        def setYRange(self, r0, r1):
            pass

    r = PlotRange("f", 1)
    r.add_yaxis(FakeAxis(), 0)
    r.set_default_max(96000.0)
    assert r.default_max() == pytest.approx(4000.0)


# ---- the setting ----------------------------------------------------------


def test_the_band_is_a_default_and_ships_off(browser):
    """Inert until a reader asks for it, so every existing range test stays
    green for a reason rather than by luck."""
    assert browser.plot_ranges["f"].rdefault is None
    assert browser.plot_ranges["f"].default_max() == pytest.approx(4000.0)


def test_a_band_that_does_not_fit_the_recording_is_clamped(browser):
    """Clamped twice on purpose -- in `spectrogram_band` and again in
    `PlotRange.default_max` -- so the answer stays right if either moves."""
    try:
        write_raw_band(
            {"version": DataBrowser.SPEC_BAND_SETTING_VERSION, "max_hz": 96000.0}
        )
        assert browser.spectrogram_band() == (None, pytest.approx(4000.0))
    finally:
        clear_raw_band()


@pytest.mark.parametrize(
    "value",
    [
        {"version": 0, "max_hz": 2000.0},
        {"version": 3, "max_hz": 2000.0},
        {"max_hz": 2000.0},
        "2000",
        {"version": 2, "max_hz": "2 kHz"},
        {"version": 2, "max_hz": float("nan")},
        {"version": 2, "max_hz": 0},
        {"version": 2, "max_hz": -1},
        {"version": 2, "max_hz": None},
    ],
    ids=[
        "version-zero",
        "newer-version",
        "no-version",
        "not-a-dict",
        "unparseable",
        "nan",
        "zero",
        "negative",
        "explicit-null",
    ],
)
def test_a_band_that_cannot_be_believed_is_ignored(browser, value):
    """A settings file is a file a reader may edit by hand, so a wrong shape
    is dropped rather than guessed at -- `restore_panel_split`'s ladder."""
    try:
        write_raw_band(value)
        assert browser.spectrogram_band() == (None, None)
    finally:
        clear_raw_band()


def test_a_band_at_nyquist_is_written_as_null(browser, band_reset):
    """An 8 kHz recording writing 4000 would cap every 96 kHz recording
    opened afterwards, from a preference nobody typed."""
    try:
        browser.set_spectrogram_band(None, 2000.0)
        settle()
        assert stored_band() == {"version": 2, "min_hz": None, "max_hz": 2000.0}

        browser.set_spectrogram_band(None, 4000.0)
        settle()
        assert stored_band() == {"version": 2, "min_hz": None, "max_hz": None}
    finally:
        clear_raw_band()


def test_applying_a_band_without_saving_moves_the_lane_and_writes_nothing(
    browser, band_reset
):
    """`save=False` is what the window passes to every tab but the one the
    reader typed in.

    The clamp is per recording, so letting each tab write would make the
    stored value depend on the order of `Audian.browsers`: 4000 typed into
    an 8 kHz tab is "show everything" and stores null, while a 96 kHz tab
    beside it reads the same 4000 as a real band and stores it.
    """
    try:
        clear_raw_band()
        browser.set_spectrogram_band(None, 2000.0, save=False)
        settle()
        assert freq_view(browser) == pytest.approx((0.0, 2000.0))
        assert stored_band() is None

        # the guard is not poisoned: a real gesture afterwards still writes
        browser.set_spectrogram_band(None, 1000.0)
        settle()
        assert stored_band() == {"version": 2, "min_hz": None, "max_hz": 1000.0}
    finally:
        clear_raw_band()


def test_changing_the_band_leaves_the_amplitude_fit_alone(browser, band_reset):
    """Red for the naive fix as well as for no fix at all.

    Re-applying the band with `plot_ranges.set_limits()` would re-run the
    `# ranges:` block for *every* letter and put the amplitude back to
    (-1.0, 1.0), destroying the fit `auto_fit_y` made at open.
    """
    trace = panel(browser, "trace").axs[0].getViewBox()
    browser.apply_ranges("default_view", "f")
    settle()
    before = trace.viewRange()[1]

    browser.set_spectrogram_band(None, 2000.0)
    settle()

    assert freq_view(browser) == pytest.approx((0.0, 2000.0))
    assert trace.viewRange()[1] == pytest.approx(before)
    assert trace.viewRange()[1] != pytest.approx((-1.0, 1.0))


def test_the_band_reaches_both_frequency_letters(browser, band_reset):
    """`Panels.add_spectrogram` hands a second spectrogram y='w'.

    Asserted on `rdefault` and not on a view range: 'w' is `is_used() ==
    False` on the default stack, so every mutating `PlotRange` method
    early-returns and no range would move.
    """
    browser.set_spectrogram_band(None, 2000.0)
    settle()
    assert browser.plot_ranges["f"].rdefault == pytest.approx(2000.0)
    assert browser.plot_ranges["w"].rdefault == pytest.approx(2000.0)


# ---- the field ------------------------------------------------------------


def test_the_field_shows_what_this_recording_actually_opens_at(browser, band_reset):
    """`default_max()` and not the raw preference, so the number in the box
    is the number on the axis."""
    browser.set_spectrogram_band(None, 2000.0)
    settle()
    browser.set_band_widget(None, 2000.0)
    assert browser.fmaxw.value() == pytest.approx(2000.0)


def test_the_field_names_the_way_back_out(browser):
    """A reader who has just typed 2 kHz is told, at the control they typed
    it into, that the rest of the axis is one key away."""
    assert "Ctrl+Shift+V" in browser.fmaxw.toolTip()
    assert "4 kHz" in browser.fmaxw.toolTip()


def test_the_opens_at_row_costs_the_bar_no_width(browser):
    """`todo.md` asked for the group's `minimumSizeHint` before and after,
    because `21169f6` records that this bar is the binding constraint on a
    14" laptop -- and the Spectrogram group is already the widest page.

    Measured, before and after this commit: the group is 535 px both times,
    the bar 551 (= 535 + 2 * S8) and the window floor 734.  The row is paid
    for in height instead: the bar goes 154 -> 168 and every group's frame
    116 -> 130, because this group was tied-tallest at three rows.
    """
    groups = {g.title: g.minimumSizeHint().width() for g in browser.param_groups}
    assert groups["Spectrogram"] == 535
    assert browser.parambar.minimumSizeHint().width() == 535 + 2 * theme.S8
    assert browser.window().minimumSizeHint().width() == 734


# ------------------------------------------- a lane the reader zoomed by hand
#
# Reported from the application after the rest of this was written and
# green: "double clicking the trace y axis still does not reset anything".
#
# It was true, and every test here missed it, because they all squash a lane
# with `setYRange` -- which is not a user zoom and leaves `user_locked`
# clear.  A reader squashes it by dragging, `SelectViewBox` emits
# `sigUserZoomed`, `PlotRange._user_zoomed` sets the lock, and
# `auto_fit_y(force=True)` then died at
# `if respect_lock and self.user_locked: return` without touching anything.
#
# So the lock is set explicitly below, which is the state a drag leaves
# behind, and these are the only tests in this file that exercise the
# gesture the way the application is actually used.


@pytest.fixture
def unlock_amplitudes(browser):
    """Clear any lock a test set, and refit, before the next one runs."""
    yield
    for axspec in Panel.amplitudes:
        arange = browser.plot_ranges.get(axspec)
        if arange is not None:
            arange.set_user_locked(False)
    browser.auto_ampl()
    settle()


def hand_zoom(browser, ax, r0, r1):
    """Squash a lane the way a drag does: the range AND the lock.

    `setYRange` alone is what every other test in this file uses and is
    precisely what hid this defect -- it moves the range without ever
    setting `user_locked`, so a fit that respects the lock looks like a fit
    that works.
    """
    ax.getViewBox().setYRange(r0, r1, padding=0)
    browser.plot_ranges[ax.y()].set_user_locked(True)
    settle()


@pytest.mark.parametrize("how", ["double click", "v"])
def test_a_hand_zoomed_trace_still_answers_the_gesture(
    browser, unlock_amplitudes, how
):
    """The reported defect, both ways into it.

    Measured before the fix: fitted at open to (-0.116965, 0.128933), hand
    zoomed to (-0.16, 0.08), double clicked, and still (-0.16, 0.08) -- the
    gesture did nothing at all.  `v` was identical, because both go through
    `auto_amplitude` -> `auto_ampl` -> `auto_fit_y(force=True)`.
    """
    ax = panel(browser, "trace").axs[0]
    view = ax.getViewBox()
    _t, (y0, y1) = view.viewRange()

    hand_zoom(browser, ax, -0.16, 0.08)
    assert view.viewRange()[1] == pytest.approx((-0.16, 0.08))
    assert browser.plot_ranges["x"].user_locked

    if how == "v":
        browser.window().acts.auto_zoom_amplitude.trigger()
        settle()
    else:
        click_axis(browser, 0, ax, double=True)

    assert view.viewRange()[1] == pytest.approx((y0, y1), abs=1e-6)


def test_the_gesture_releases_the_lock_it_overrode(browser, unlock_amplitudes):
    """Going back to the automatic view means the automatic view keeps
    following the data afterwards.

    `PlotRange.auto` clears `user_locked` whenever it is called with
    `respect_lock=False`, so the one-line fix releases the lock as well as
    overriding it -- and a lane left locked-but-fitted would silently stop
    tracking on the next time scroll.
    """
    ax = panel(browser, "trace").axs[0]
    hand_zoom(browser, ax, -0.16, 0.08)
    assert browser.plot_ranges["x"].user_locked

    click_axis(browser, 0, ax, double=True)
    assert not browser.plot_ranges["x"].user_locked


def test_a_time_scroll_still_leaves_a_hand_zoom_alone(browser, unlock_amplitudes):
    """The rule the fix must not break.

    `set_times` calls `auto_fit_y()` unforced, and an automatic fit must
    never fight a zoom the reader chose.  Only the gestures that ask for a
    fit by name override the lock.
    """
    ax = panel(browser, "trace").axs[0]
    view = ax.getViewBox()
    hand_zoom(browser, ax, -0.16, 0.08)

    browser.auto_fit_y()  # what a scroll does: unforced
    settle()

    assert view.viewRange()[1] == pytest.approx((-0.16, 0.08))
    assert browser.plot_ranges["x"].user_locked


# ------------------------------------------------- the floor of the band
#
# The band has two ends now.  A floor is the same kind of thing as the
# ceiling and obeys the same rule: it moves where the lane OPENS and never
# where it can go, so 500-2000 Hz still pans and zooms down to 0 and up to
# Nyquist, and Ctrl+Shift+V still shows the whole axis.


def test_a_floor_moves_where_the_lane_opens_and_not_where_it_can_go(
    browser, band_reset
):
    """The `rmax` mistake, in its mirror image: a floor written into `rmin`
    would take 0 Hz away instead of merely starting above it."""
    browser.set_spectrogram_band(500.0, 2000.0)
    settle()
    assert freq_view(browser) == pytest.approx((500.0, 2000.0))

    c = spec_channel(browser)
    vb = panel(browser, "spectrogram").axs[c].getViewBox()
    assert vb.state["limits"]["yLimits"] == [0, 4000.0]

    vb.setYRange(0, 300, padding=0)
    settle()
    assert vb.viewRange()[1] == pytest.approx((0.0, 300.0))


def test_the_gestures_go_back_to_both_ends(browser, band_reset):
    """Ctrl+V returns to the band; Ctrl+Shift+V is still the whole axis."""
    browser.set_spectrogram_band(500.0, 2000.0)
    settle()
    c = spec_channel(browser)
    vb = panel(browser, "spectrogram").axs[c].getViewBox()

    vb.setYRange(1200, 1500, padding=0)
    settle()
    browser.apply_ranges("default_view", "f")
    settle()
    assert vb.viewRange()[1] == pytest.approx((500.0, 2000.0))

    browser.apply_ranges("reset", "f")
    settle()
    assert vb.viewRange()[1] == pytest.approx((0.0, 4000.0))


def test_a_floor_at_or_above_the_ceiling_loses(browser, band_reset):
    """It would open the lane on nothing at all, so it is dropped and the
    range opens at its limit -- clamped twice, in `set_spectrogram_band` and
    again in `PlotRange.default_min`."""
    browser.set_spectrogram_band(3000.0, 2000.0)
    settle()
    assert freq_view(browser) == pytest.approx((0.0, 2000.0))
    assert browser.plot_ranges["f"].rdefault_min is None


def test_a_floor_is_stored_and_read_back(browser, band_reset):
    """Both ends round-trip through the settings file."""
    try:
        browser.set_spectrogram_band(500.0, 2000.0)
        settle()
        assert stored_band() == {
            "version": 2,
            "min_hz": 500.0,
            "max_hz": 2000.0,
        }
        assert browser.spectrogram_band() == (
            pytest.approx(500.0),
            pytest.approx(2000.0),
        )
    finally:
        clear_raw_band()


def test_a_floor_at_zero_is_written_as_null(browser, band_reset):
    """The floor's limit is 0 Hz, and an end sitting at its limit is stored
    as null rather than as this recording's number -- the rule the ceiling
    already had."""
    try:
        browser.set_spectrogram_band(0.0, 2000.0)
        settle()
        assert stored_band() == {"version": 2, "min_hz": None, "max_hz": 2000.0}
    finally:
        clear_raw_band()


def test_a_version_1_band_is_migrated_rather_than_dropped(browser):
    """Version 1 was max only, and its number still means what it meant.

    The opposite of what `PANEL_SPLIT_SETTING_VERSION` 3 does with a
    version 2 split -- and for a reason about the values rather than the
    habit: a version 2 split held up to four numbers with nothing to say
    which the reader wanted, so there was nothing to carry forward.  A
    version 1 band holds exactly one number.  Dropping it would throw away
    a preference the reader typed, to no purpose.
    """
    try:
        write_raw_band({"version": 1, "max_hz": 2000.0})
        assert browser.spectrogram_band() == (None, pytest.approx(2000.0))
    finally:
        clear_raw_band()


def test_set_limits_opens_a_range_at_both_ends_of_its_band():
    """The core of the feature on a bare `PlotRange`, floor included."""
    from audian.plotranges import PlotRange

    class FakeAxis:
        def range(self, axspec):
            return 0, 4000.0, None

        def setLimits(self, **kwargs):
            pass

        def setYRange(self, r0, r1):
            pass

    r = PlotRange("f", 1)
    r.add_yaxis(FakeAxis(), 0)

    r.set_default_min(500.0)
    r.set_default_max(2000.0)
    r.set_limits()
    assert (r.r0[0], r.r1[0]) == pytest.approx((500.0, 2000.0))
    assert r.rmin == pytest.approx(0.0), "the floor must not become the limit"
    assert r.rmax == pytest.approx(4000.0)

    r.reset(do_set=False)
    assert (r.r0[0], r.r1[0]) == pytest.approx((0.0, 4000.0))
    r.default_view(do_set=False)
    assert (r.r0[0], r.r1[0]) == pytest.approx((500.0, 2000.0))


def test_the_two_band_fields_show_both_ends(browser, band_reset):
    """The row reads left to right the way the band is written."""
    browser.set_spectrogram_band(500.0, 2000.0)
    settle()
    browser.set_band_widget(500.0, 2000.0)
    assert browser.fminw.value() == pytest.approx(500.0)
    assert browser.fmaxw.value() == pytest.approx(2000.0)
