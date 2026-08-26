"""Tests for the draggable trace / spectrogram boundary.

Runs offscreen::

    QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_panelsplitter.py -q

The split is a layout, and there is only one question about a layout worth
asking: what height did the rows actually end up with.  So every claim here
is measured off `QGraphicsWidget.geometry()` once the layout has settled,
never off `trace_fracs` -- the ratio agreeing with itself would prove
nothing.  `settle` is where the layout is activated, which is also why it is
called by hand after every gesture: a drag invalidates and Qt re-activates
before the next paint, so a test that reads geometry without letting the
event loop turn is reading the frame before the one it means.

Three windows are built, because the three cases differ in kind.  Four
channels at 1200x900 is *dense*: the lane is 34 px, the whole of it goes to
the trace, and the spectrogram's 120 px allowance leaves nothing over -- so
the boundary can be dragged one way only.  Two channels have 126 px of lane
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


def build_window(app, directory, channels):
    """The whole application on a synthetic recording of `channels` channels.

    Both persistence stores are redirected into `directory` first.  This file
    writes a preference -- the split is saved at the end of every gesture --
    and a test that writes the reader's own ``~/.config/audian`` is a test
    that has to be run in a container to be safe.
    """
    soundfile = pytest.importorskip("soundfile")
    import audian.audian as audian_app
    from audian.plugins import Plugins

    rate = 8000
    frames = rate * 4
    signal = np.zeros((frames, channels), dtype=np.float32)
    for c in range(channels):
        signal[:, c] = 0.1 * np.sin(np.arange(frames) / (50.0 + c))
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


def open_stack(app, directory, channels):
    """A browser showing both panels, and the teardown that follows it."""
    import audian.audian as audian_app

    original = audian_app.settings_path
    home = Path(QSettings("audian", "audian").fileName()).parent.parent
    window = build_window(app, directory, channels)
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
    """Two channels: 126 px of lane over the allowance, room to drag."""
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
    browser.trace_fracs.update(browser.default_trace_fracs)
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
    120 / 126.
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


def test_the_focused_lane_keeps_its_spectrogram_when_the_focus_moves(wide_browser):
    """Stepping down the array must not cost the stack its spectrogram.

    Sixteen channels collapse the spectrogram onto the focused lane, so
    every step hides one lane's panel and shows another's.  A panel asked
    whether it has anything to draw with `QGraphicsItem.isVisible` answers
    for its whole ancestry, so a lane hidden once answered "nothing" for
    ever and the stack this application exists for lost its spectrogram at
    the first press of the down arrow.
    """
    view = wide_browser
    before = view.current_channel
    try:
        view.next_channel()
        settle()
        view.adjust_layout(view.width(), view.height())
        settle()
        focus = spec_channel(view)
        assert focus != before
        assert panel(view, "spectrogram").axs[focus].isVisible()
        assert row_height(view, "spectrogram", focus) == theme.SPECTROGRAM_MIN_HEIGHT
        assert splitter(view, focus).isVisible()
        assert not splitter(view, before).isVisible()
    finally:
        while view.current_channel != before:
            view.previous_channel()
        settle()
        view.adjust_layout(view.width(), view.height())
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
    assert saved["fracs"][str(roomy_browser.show_specs)] == pytest.approx(
        roomy_browser.trace_fracs[roomy_browser.show_specs]
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
    assert set(saved["fracs"]) == {str(roomy_browser.show_specs)}


def test_a_saved_split_is_read_back_whatever_the_channel_count(browser, monkeypatch):
    """It is a ratio between two rows, so no bundle can invalidate it."""
    import audian.audian as audian_app
    from audian.databrowser import DataBrowser

    monkeypatch.setattr(
        audian_app,
        "settings",
        lambda: {
            DataBrowser.PANEL_SPLIT_SETTING: {
                "version": DataBrowser.PANEL_SPLIT_SETTING_VERSION,
                "fracs": {"1": 0.375, "2": "not a number", "9": 2.0},
            }
        },
    )
    fracs = dict(browser.trace_fracs)
    try:
        browser.trace_fracs = dict(browser.default_trace_fracs)
        browser.restore_panel_split()
        assert browser.trace_fracs[1] == pytest.approx(0.375)
        # unreadable entries, and presets this build does not have, are left
        # at their defaults rather than half-applied
        assert browser.trace_fracs[2] == browser.default_trace_fracs[2]
        assert 9 not in browser.trace_fracs
    finally:
        browser.trace_fracs = fracs


def test_a_split_saved_by_another_version_is_ignored(browser, monkeypatch):
    import audian.audian as audian_app
    from audian.databrowser import DataBrowser

    monkeypatch.setattr(
        audian_app,
        "settings",
        lambda: {DataBrowser.PANEL_SPLIT_SETTING: {"version": 99, "fracs": {"1": 0.1}}},
    )
    fracs = dict(browser.trace_fracs)
    try:
        browser.trace_fracs = dict(browser.default_trace_fracs)
        browser.restore_panel_split()
        assert browser.trace_fracs == browser.default_trace_fracs
    finally:
        browser.trace_fracs = fracs


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
    wide_browser.trace_fracs.update(wide_browser.default_trace_fracs)
    original(wide_browser.width(), wide_browser.height())
    settle()
