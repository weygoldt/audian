"""Tests for the draggable trace / spectrogram boundary.

Runs offscreen::

    QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_panelsplitter.py -q

The split is a layout, and there is only one question about a layout worth
asking: what height did the rows actually end up with.  So every claim here
is measured off `QGraphicsWidget.geometry()` once the layout has settled,
never off `spec_scales` -- the ratio agreeing with itself would prove
nothing.  `settle` is where the layout is activated, which is also why it is
called by hand after every gesture: a drag invalidates and Qt re-activates
before the next paint, so a test that reads geometry without letting the
event loop turn is reading the frame before the one it means.

Three windows are built, because the three cases differ in kind.  Four
channels at 1200x900 is *dense*: the lane is 34 px, the whole of it goes to
the trace, and the spectrogram's 120 px allowance leaves nothing over -- so
the boundary can be dragged one way only.  Two channels have 130 px of lane
to share, so that is where a drag is measured in both directions.  Sixteen
collapse the spectrogram onto a single focused lane (`spectrogram_channels`)
and are the case whose cost is measured.
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

from PyQt5.QtCore import QEvent, QPoint, QPointF, QSettings, Qt  # noqa: E402
from PyQt5.QtGui import QMouseEvent  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

from audian import theme  # noqa: E402
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
    """Two channels: 130 px of lane over the allowance, room to drag."""
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
    browser.spec_scales.update(browser.default_spec_scales)
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
    120 / 130.
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


def test_each_spectrogram_size_keeps_its_own_split(roomy_browser, roomy_reset):
    """F3 cycles four spectrogram sizes; a drag adjusts the one on screen."""
    c = spec_channel(roomy_browser)
    spec_h, room = roomy_browser.panel_split_heights(c)
    drag(roomy_browser, spec_h + 20, room)
    roomy_browser.finish_panel_split()
    settle()
    dragged = row_height(roomy_browser, "spectrogram", c)
    roomy_browser.set_panels(specs=2)
    settle()
    assert row_height(roomy_browser, "spectrogram", c) != dragged
    roomy_browser.set_panels(specs=1)
    settle()
    assert row_height(roomy_browser, "spectrogram", c) == dragged


def test_a_dense_lane_has_nothing_left_for_a_bigger_spectrogram(browser, split_reset):
    """Which is why every spectrogram size opens the same way there.

    The sizes above the one F3 starts on take their share of the *lane*, and
    a dense lane is 34 px of which the floor is 34.  A stack this tight
    showed the same layout at every size before the boundary could be
    dragged at all -- 121.0, 121.6, 121.8, 121.9 px of spectrogram measured
    at the four sizes -- so collapsing them onto one is what it already did,
    stated instead of stumbled into.
    """
    c = spec_channel(browser)
    heights = []
    for specs in (1, 2, 3, 4):
        browser.set_panels(specs=specs)
        settle()
        heights.append(row_height(browser, "spectrogram", c))
    assert heights == [theme.SPECTROGRAM_MIN_HEIGHT] * 4


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
    assert saved["scales"][str(roomy_browser.show_specs)] == pytest.approx(
        roomy_browser.spec_scales[roomy_browser.show_specs]
    )


def test_a_size_that_was_never_dragged_is_not_written_out(roomy_browser, roomy_reset):
    """Its default follows the lane height, so this window's answer to it is
    not a preference and must not be frozen into the settings file."""
    import audian.audian as audian_app
    from audian.databrowser import DataBrowser

    c = spec_channel(roomy_browser)
    spec_h, room = roomy_browser.panel_split_heights(c)
    drag(roomy_browser, spec_h + 9, room)
    roomy_browser.finish_panel_split()
    saved = audian_app.settings().get(DataBrowser.PANEL_SPLIT_SETTING)
    assert set(saved["scales"]) == {str(roomy_browser.show_specs)}


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
                "scales": {"1": 0.375, "2": "not a number", "9": 2.0, "0": 1.0},
            }
        },
    )
    scales = dict(browser.spec_scales)
    try:
        browser.spec_scales = dict(browser.default_spec_scales)
        browser.restore_panel_split()
        assert browser.spec_scales[1] == pytest.approx(0.375)
        # unreadable entries, and presets this build does not have, are left
        # at their defaults rather than half-applied
        assert browser.spec_scales[2] == browser.default_spec_scales[2]
        assert 9 not in browser.spec_scales
        # F3 size 0 hides the spectrogram, so it has no boundary to drag and
        # no scale to read: a file holding one is an older build's, and it is
        # dropped rather than carried along by every later write
        assert 0 not in browser.spec_scales
    finally:
        browser.spec_scales = scales


@pytest.mark.parametrize("preset", [1, 2, 3, 4])
def test_the_split_a_reader_dragged_means_the_same_on_any_stack(
    browser, roomy_browser, preset
):
    """The measurement the version 2 format exists for.

    `spec_scales` is measured against `theme.SPECTROGRAM_MIN_HEIGHT`, which
    no recording moves, so a reader who halves the spectrogram on one stack
    must find it halved on the other -- at every F3 size, which is why this
    is parametrised.  Version 1 could not do it at any size: it held the
    trace over the spectrogram, and the trace is the lane, 34 px on the
    dense stack against 130 on the roomy one, so the same stored number came
    out 60 px one side and 144 the other, a shrink replayed as a stretch.

    Measuring against `default_spec_height` instead -- the height the size
    opens on, which is the tempting denominator -- fixes only size 1, where
    that default *is* the allowance.  At sizes 2 to 4 it takes a share of
    the lane as well, so it is 185 px on two channels against 120 on
    sixteen, and a boundary dragged to a readable 125 px on the roomy stack
    came back at 81 px on the dense one: the same unreadable stripe this
    format was written to stop, arriving 1.6x instead of 9.7x.
    """
    dense = dict(browser.spec_scales)
    roomy = dict(roomy_browser.spec_scales)
    sizes = (browser.show_specs, roomy_browser.show_specs)
    try:
        for view in (browser, roomy_browser):
            view.set_panels(specs=preset)
            view.spec_scales[preset] = 0.5
            view.adjust_layout(view.width(), view.height())
        settle()
        for view in (browser, roomy_browser):
            c = spec_channel(view)
            assert row_height(view, "spectrogram", c) == pytest.approx(60, abs=1)
    finally:
        browser.spec_scales = dense
        roomy_browser.spec_scales = roomy
        for view, size in zip((browser, roomy_browser), sizes):
            view.set_panels(specs=size)
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
    """
    import audian.audian as audian_app
    from audian.databrowser import DataBrowser

    monkeypatch.setattr(
        audian_app,
        "settings",
        lambda: {DataBrowser.PANEL_SPLIT_SETTING: saved},
    )
    scales = dict(browser.spec_scales)
    try:
        browser.spec_scales = dict(browser.default_spec_scales)
        browser.restore_panel_split()
        assert browser.spec_scales == browser.default_spec_scales
    finally:
        browser.spec_scales = scales


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
    wide_browser.spec_scales.update(wide_browser.default_spec_scales)
    original(wide_browser.width(), wide_browser.height())
    settle()
