"""Tests for the overlay trace panel (Ctrl+Shift+F2).

Runs offscreen::

    QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_overlaytraces.py -q

The failure this feature could most easily ship with is a panel that is
visible, full height, captioned `OVERLAY 00 01 ...`, and drawing channel 0
sixteen times -- or drawing sixteen channels in one colour, or fitting its
y range to the lane's own channel and clipping the other fifteen.  None of
that changes a row height or an `isVisible()`, so every claim here is made
about **the samples an item holds**, **the colour its pen carries** and
**the range its view box shows**.

Each channel of the fixture sits on its own DC offset, so which channel a
line is drawing can be read straight off the numbers it holds.
"""

from __future__ import annotations

import itertools
import os
import sys
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from test_panelsplitter import (  # noqa: E402
    FRAMES,
    RATE,
    open_stack,
    panel,
    pump,
    settle,
    spec_image,
)

from PySide6.QtCore import QPointF, Qt  # noqa: E402

from audian import theme  # noqa: E402
from audian.traceitem import OverlayTraceItem  # noqa: E402

CHANNELS = 16

#: Channel `c` is centred on `c * OFFSET`, with a ripple far smaller than the
#: spacing and the top channel inside the 16 bit wav's +-1 full scale (a first
#: draft at 0.1 clipped channels 10-15 to exactly 1.0), so no two channels'
#: values overlap anywhere.
OFFSET = 0.06
RIPPLE = 0.005


def offset_signal(channels: int) -> np.ndarray:
    t = np.arange(FRAMES) / RATE
    signal = np.zeros((FRAMES, channels), dtype=np.float32)
    for c in range(channels):
        signal[:, c] = c * OFFSET + RIPPLE * np.sin(2 * np.pi * (200 + 10 * c) * t)
    return signal


@pytest.fixture(scope="module")
def stack(app, tmp_path_factory):  # noqa: F811
    yield from open_stack(
        app,
        tmp_path_factory.mktemp("overlay16"),
        CHANNELS,
        offset_signal(CHANNELS),
    )


@pytest.fixture
def overlay_off(stack):
    """Put the stack back afterwards, so tests do not inherit each other."""
    yield
    for c in list(stack.solo_channels):
        stack.toggle_solo(c)
    for c in list(stack.muted_channels):
        stack.toggle_mute(c)
    stack.set_overlay_traces(False)
    stack.set_mean_spectrogram(False)
    stack.set_panels(traces=True, specs=1)
    for name in ("data",):
        stack.set_trace_visible(name, True)
    settle()
    pump(0.3)


def trace_plot(browser, channel):
    return panel(browser, "trace").axs[channel]


def enter_overlay(browser):
    browser.set_overlay_traces(True)
    settle()
    pump(0.6)
    lane = browser.overlay_lane()
    assert lane is not None, "overlay mode did not pick a lane"
    return lane


def drawn_channels(plot):
    """channel -> the y values its visible item currently holds."""
    out = {}
    for item in plot.own_traces() + plot.overlay_items:
        if not item.isVisible():
            continue
        _, y = item.getData()
        assert y is not None and len(y) > 0, f"channel {item.channel} drew nothing"
        out[item.channel] = np.asarray(y)
    return out


def pen_color(item) -> str:
    return item.opts["pen"].color().name().upper()


# ------------------------------------------------------------- what it draws


def test_the_overlay_draws_every_selected_channel_from_its_own_samples(
    stack, overlay_off
):
    """The assertion the feature stands on: sixteen lines, sixteen channels.

    Each line's values sit within the ripple of its own channel's offset,
    so a panel drawing channel 0 sixteen times fails here, where a count
    of items would pass.
    """
    lane = enter_overlay(stack)
    drawn = drawn_channels(trace_plot(stack, lane))
    assert sorted(drawn) == list(range(CHANNELS))
    for c, y in drawn.items():
        assert np.all(np.abs(y - c * OFFSET) <= RIPPLE * 1.01), (
            f"the line for channel {c} holds {y.min():.3f}..{y.max():.3f}"
        )


def test_every_line_is_drawn_in_the_plain_trace_colour(stack, overlay_off):
    """Nothing picked out: sixteen lines, one muted colour, one width.

    The lane's own channel too -- it is one of the sixteen, not the host --
    and none of them in the selected colour, which is kept for the one the
    reader picks.
    """
    lane = enter_overlay(stack)
    plot = trace_plot(stack, lane)
    raw = [i for i in plot.own_traces() + plot.overlay_items if i.data.name == "data"]
    assert len(raw) == CHANNELS
    assert {pen_color(item) for item in raw} == {pen_color(raw[0])}
    assert {item.opts["pen"].widthF() for item in raw} == {theme.LW_THIN}
    assert pen_color(raw[0]) != theme.qcolor("primary").name().upper()


def test_the_caption_lists_every_channel_as_a_chip(stack, overlay_off):
    lane = enter_overlay(stack)
    plot = trace_plot(stack, lane)
    text = plot.caption_text()
    assert text.split() == ["OVERLAY"] + [f"{c:02d}" for c in range(CHANNELS)]
    assert plot.channel_label.toPlainText() == "OVERLAY"
    assert [chip.toPlainText() for chip in plot.legend_chips] == [
        f"{c:02d}" for c in range(CHANNELS)
    ]
    # one line, left to right, none on top of another
    rects = [chip.sceneBoundingRect() for chip in plot.legend_chips]
    label = plot.channel_label.sceneBoundingRect()
    assert rects[0].left() >= label.right() - 1
    for a, b in zip(rects, rects[1:]):
        assert b.left() >= a.right() - 1
        assert abs(b.top() - a.top()) < 1


def test_the_y_range_holds_every_channel(stack, overlay_off):
    """One panel has one y range, and every line drawn in it has to fit.

    Fitted to the lane's own channel, as the lane was before, the range is
    about +-0.01 around zero and fifteen of sixteen lines are off screen.
    """
    lane = enter_overlay(stack)
    for mode in (stack.y_shared, stack.y_per_channel):
        stack.set_y_mode(mode)
        stack.auto_fit_y(force=True)
        settle()
        pump(0.3)
        y0, y1 = trace_plot(stack, lane).getViewBox().viewRange()[1]
        assert y0 <= -RIPPLE * 0.99, f"mode {mode}: bottom {y0:.3f}"
        assert y1 >= (CHANNELS - 1) * OFFSET + RIPPLE * 0.99, (
            f"mode {mode}: top {y1:.3f}"
        )
    stack.set_y_mode(stack.y_shared)


def test_solo_narrows_what_the_overlay_draws(stack, overlay_off):
    lane = enter_overlay(stack)
    stack.toggle_solo(3)
    stack.toggle_solo(9)
    settle()
    pump(0.5)
    lane = stack.overlay_lane()
    assert lane == 3
    drawn = drawn_channels(trace_plot(stack, lane))
    assert sorted(drawn) == [3, 9]
    assert np.all(np.abs(drawn[9] - 9 * OFFSET) <= RIPPLE * 1.01)
    assert trace_plot(stack, lane).caption_text() == "OVERLAY 03 09"


def test_hiding_a_trace_hides_its_overlaid_copies(stack, overlay_off):
    lane = enter_overlay(stack)
    plot = trace_plot(stack, lane)
    raw = [item for item in plot.overlay_items if item.data.name == "data"]
    filtered = [item for item in plot.overlay_items if item.data.name != "data"]
    assert len(raw) == CHANNELS - 1 and filtered
    stack.set_trace_visible("data", False)
    settle()
    assert not any(item.isVisible() for item in raw)
    # the other trace in the panel is not the one that was hidden
    assert all(item.isVisible() for item in filtered)
    stack.set_trace_visible("data", True)
    settle()
    assert all(item.isVisible() for item in raw)


# ------------------------------------------------------ the collapsed stack


def test_the_stack_collapses_to_one_lane_without_rail_or_selection_cue(
    stack, overlay_off
):
    stack.rail_clicked(0, False)
    settle()
    lane = enter_overlay(stack)
    assert stack.visible_channels() == [lane]
    assert not stack.rail_shown()
    assert stack.rail_visible, "the reader's own setting was overwritten"
    assert not trace_plot(stack, lane).current


def test_leaving_gives_every_lane_its_own_channel_back(stack, overlay_off):
    """No overlay items left anywhere, the host lane draws in its role
    colour again, and every channel's visibility flag reads live.

    The flag check does not go red with `OverlayTraceItem` mirroring put
    back -- see its docstring for why no path reaches a wrong flag -- so it
    is here as the invariant, not as that item's regression test."""
    lane = enter_overlay(stack)
    stack.set_overlay_traces(False)
    settle()
    pump(0.5)
    assert stack.visible_channels() == list(range(CHANNELS))
    for c in range(CHANNELS):
        plot = trace_plot(stack, c)
        assert plot.overlay_items == []
        assert plot.overlay_channels is None
        assert not any(
            isinstance(item, OverlayTraceItem) for item in plot.listDataItems()
        )
        assert plot.legend_chips == []
        for item in plot.own_traces():
            assert not item.emphasized
        assert plot.caption_text().startswith(f"CH {c:02d}")
    data = stack.data["data"]
    assert data.visible_channels.all(), np.flatnonzero(~data.visible_channels)
    assert lane == 0


# ------------------------------------------------------- pointer and labels


def scene_point(plot, x, y):
    return plot.getViewBox().mapViewToScene(QPointF(x, y))


def pens(plot):
    """channel -> (colour, width, z) of its raw trace."""
    return {
        item.channel: (pen_color(item), item.opts["pen"].widthF(), item.zValue())
        for item in plot.own_traces() + plot.overlay_items
        if item.data.name == "data"
    }


def test_the_hover_names_the_channel_without_repainting(stack, overlay_off):
    """The readout follows the pointer; the picture does not flicker."""
    lane = enter_overlay(stack)
    plot = trace_plot(stack, lane)
    before = pens(plot)
    heard = []
    plot.sigHoverValue.connect(lambda c, t, a: heard.append(c))
    for target in (0, 7, 15):
        plot._hovered(1.0, target * OFFSET + RIPPLE / 2)
        assert heard[-1] == target
    assert pens(plot) == before


def test_clicking_a_line_picks_that_channel_out(stack, overlay_off):
    """On top, in the selected colour, at the selected width -- the rest
    untouched -- and it becomes the current channel without narrowing the
    selection the overlay draws."""
    lane = enter_overlay(stack)
    plot = trace_plot(stack, lane)
    before = pens(plot)
    assert stack.overlay_click(scene_point(plot, 1.0, 7 * OFFSET), lane)
    after = pens(plot)
    primary = theme.qcolor("primary").name().upper()
    assert after[7][0] == primary
    assert after[7][1] == theme.LW_SELECTED
    assert after[7][2] > max(z for c, (_, _, z) in after.items() if c != 7)
    assert {c: v for c, v in after.items() if c != 7} == {
        c: v for c, v in before.items() if c != 7
    }
    assert stack.overlay_highlight == 7
    assert stack.current_channel == 7
    assert stack.overlay_channels() == list(range(CHANNELS))
    assert sorted(drawn_channels(plot)) == list(range(CHANNELS))
    chip = plot.legend_chips[7]
    assert chip.textItem.defaultTextColor().name().upper() == primary
    assert chip.textItem.font().bold()
    # the same line again puts it back
    assert stack.overlay_click(scene_point(plot, 1.0, 7 * OFFSET), lane)
    assert stack.overlay_highlight is None
    assert pens(plot) == before


def test_a_click_between_the_lines_picks_nothing(stack, overlay_off):
    lane = enter_overlay(stack)
    plot = trace_plot(stack, lane)
    stack.overlay_click(scene_point(plot, 1.0, 4 * OFFSET), lane)
    assert stack.overlay_highlight == 4
    # halfway between channels 9 and 10: 30 px from either at this height
    assert not stack.overlay_click(scene_point(plot, 1.0, 9.5 * OFFSET), lane)
    assert stack.overlay_highlight == 4


def test_clicking_a_number_in_the_caption_picks_that_channel(stack, overlay_off):
    lane = enter_overlay(stack)
    plot = trace_plot(stack, lane)
    chip = plot.legend_chips[12]
    assert stack.overlay_click(chip.sceneBoundingRect().center(), lane)
    assert stack.overlay_highlight == 12
    assert pens(plot)[12][1] == theme.LW_SELECTED


def test_the_pick_survives_a_solo_and_goes_with_its_channel(stack, overlay_off):
    lane = enter_overlay(stack)
    plot = trace_plot(stack, lane)
    stack.overlay_click(plot.legend_chips[9].sceneBoundingRect().center(), lane)
    # 9 first: soloing 3 alone would take 9 away, and the pick with it
    stack.toggle_solo(9)
    stack.toggle_solo(3)
    settle()
    pump(0.4)
    plot = trace_plot(stack, stack.overlay_lane())
    assert plot.highlighted_channel == 9
    assert pens(plot)[9][1] == theme.LW_SELECTED
    stack.toggle_solo(9)
    settle()
    pump(0.4)
    assert stack.overlay_highlight is None


class _Click:
    def __init__(self, pos):
        self._pos = pos

    def button(self):
        return Qt.MouseButton.LeftButton

    def modifiers(self):
        return Qt.KeyboardModifier.NoModifier

    def scenePos(self):
        return self._pos


def test_a_click_on_the_mean_panel_leaves_the_focus_alone(stack, overlay_off):
    """The mean's lane is borrowed from the first selected channel, and a
    click on a lane focuses that lane's channel -- so with the focus on 12,
    one click on the mean panel moved it to 0, and leaving the mode no
    longer gave the reader's place back."""
    stack.set_panels(traces=False, specs=1)
    settle()
    stack.rail_clicked(12, False)
    stack.set_mean_spectrogram(True)
    settle()
    pump(0.5)
    lane = stack.mean_spec_lane()
    assert lane != 12
    spec = panel(stack, "spectrogram").axs[lane]
    stack.mouse_clicked((_Click(spec.getViewBox().sceneBoundingRect().center()),), lane)
    settle()
    assert stack.current_channel == 12
    assert stack.mean_channels() == list(range(CHANNELS))


def test_a_click_on_the_overlay_picks_rather_than_focusing_the_lane(stack, overlay_off):
    """Through the real click handler, not only `overlay_click`."""
    lane = enter_overlay(stack)
    plot = trace_plot(stack, lane)
    stack.mouse_clicked((_Click(scene_point(plot, 1.0, 7 * OFFSET)),), lane)
    settle()
    assert stack.overlay_highlight == 7
    assert stack.current_channel == 7
    assert stack.overlay_channels() == list(range(CHANNELS))


def test_the_cross_hair_snaps_to_the_nearest_channel(stack, overlay_off):
    lane = enter_overlay(stack)
    plot = trace_plot(stack, lane)
    x, dx = 1.0, 1.0 / RATE
    x_snap, y_snap, _ = plot.get_marker_pos(x, dx, 11 * OFFSET, 0.001)
    assert plot.marker_channel == 11
    assert abs(y_snap - 11 * OFFSET) <= RIPPLE * 1.01


def test_the_overlay_shows_and_makes_channelless_labels(stack, overlay_off):
    """It stands for the array, as the mean does: every overlaid channel's
    labels are drawn on it, and one drawn on it names no electrode."""
    from audian.labels import KIND_SPAN, Label

    from test_labels import live_rects

    lane = enter_overlay(stack)
    plot = trace_plot(stack, lane)
    stack.labels.clear()
    try:
        for c in (0, 5, 11):
            stack.labels.add(Label("event", KIND_SPAN, c, 1.0 + c / 10, 1.05 + c / 10))
        stack.redraw_labels()
        settle()
        overlay = next(o for o in stack.label_overlays if o.plot is plot)
        assert overlay.channels() == list(range(CHANNELS))
        assert len(live_rects(overlay)) == 3

        category = stack.labels.category(stack.current_category)
        assert category is not None
        before = len(stack.labels)
        stack.store_label(category, plot, lane, 2.0, 2.2, None, None)
        assert len(stack.labels) == before + 1
        assert stack.labels.labels[-1].channel is None
    finally:
        stack.labels.clear()
        stack.redraw_labels()


# -------------------------------------------------------- with the mean


def test_overlay_and_mean_share_one_lane(stack, overlay_off):
    """The combination: the overlay as the lane's trace, the mean as its
    spectrogram, describing the same electrodes."""
    lane = enter_overlay(stack)
    stack.set_mean_spectrogram(True)
    settle()
    pump(0.8)
    assert stack.overlay_traces and stack.mean_spec
    assert stack.show_traces and stack.show_specs
    assert stack.overlay_lane() == stack.mean_spec_lane() == lane
    assert stack.visible_channels() == [lane]
    assert sorted(drawn_channels(trace_plot(stack, lane))) == list(range(CHANNELS))
    spec = panel(stack, "spectrogram").axs[lane]
    assert spec.mean_channels == stack.overlay_channels()
    image = spec_image(stack, lane)
    assert image is not None and image.size > 0, "the mean panel is empty"


def test_f2_on_the_pair_keeps_the_mean(stack, overlay_off):
    enter_overlay(stack)
    stack.set_mean_spectrogram(True)
    settle()
    stack.toggle_traces()
    settle()
    pump(0.4)
    assert not stack.overlay_traces
    assert stack.mean_spec
    assert not stack.show_traces


def test_f3_on_the_pair_keeps_the_overlay(stack, overlay_off):
    enter_overlay(stack)
    stack.set_mean_spectrogram(True)
    settle()
    stack.toggle_spectrograms()
    settle()
    pump(0.4)
    assert stack.overlay_traces
    assert not stack.mean_spec
    assert stack.show_specs == 0


def test_the_overlay_does_not_sit_over_one_channels_spectrogram(stack, overlay_off):
    """F3 turning the spectrograms on under an overlay ends the overlay."""
    enter_overlay(stack)
    assert stack.show_specs == 0
    stack.toggle_spectrograms()
    settle()
    pump(0.4)
    assert not stack.overlay_traces
    assert stack.show_traces and stack.show_specs


STARTS = ((True, 0), (False, 1), (True, 1))


@pytest.mark.parametrize("traces,specs", STARTS)
def test_the_shortcut_is_a_round_trip(stack, overlay_off, traces, specs):
    stack.set_panels(traces=traces, specs=specs)
    settle()
    stack.set_overlay_traces(True)
    settle()
    pump(0.3)
    assert stack.overlay_traces and stack.show_traces
    stack.set_overlay_traces(False)
    settle()
    pump(0.3)
    assert (stack.show_traces, stack.show_specs) == (traces, specs)


@pytest.mark.parametrize(
    "first,second", list(itertools.permutations(("overlay", "mean")))
)
def test_either_key_twice_leaves_the_other_mode_as_it_was(
    stack, overlay_off, first, second
):
    """Enter one mode, add the other, take the other away again: back in
    the first mode, with the panels that mode had."""
    setter = {
        "overlay": stack.set_overlay_traces,
        "mean": stack.set_mean_spectrogram,
    }
    setter[first](True)
    settle()
    pump(0.3)
    state = (
        stack.overlay_traces,
        stack.mean_spec,
        stack.show_traces,
        stack.show_specs,
    )
    setter[second](True)
    settle()
    pump(0.3)
    assert stack.overlay_traces and stack.mean_spec
    setter[second](False)
    settle()
    pump(0.3)
    assert (
        stack.overlay_traces,
        stack.mean_spec,
        stack.show_traces,
        stack.show_specs,
    ) == state


def test_the_menu_says_which_mode_the_stack_is_in(stack, overlay_off):
    window = stack.window()
    act = window.acts.toggle_overlay_traces
    assert act.isCheckable()
    assert act.shortcut().toString() == "Ctrl+Shift+F2"
    # on the tool bar beside the mean's button, not only in the menu
    actions = [b.defaultAction() for b in window.panel_buttons]
    assert actions.index(act) == actions.index(window.acts.toggle_mean_spec) + 1
    assert not act.icon().isNull()
    window.toggle_overlay_traces()
    settle()
    pump(0.4)
    assert stack.overlay_traces and act.isChecked()
    window.toggle_overlay_traces()
    settle()
    pump(0.4)
    assert not stack.overlay_traces and not act.isChecked()
