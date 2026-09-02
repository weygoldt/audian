"""Tests for the mean spectrogram (Shift+F2).

Runs offscreen::

    QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_meanspectrogram.py -q

The bug this feature could most easily ship with is a panel that is visible,
correctly sized, captioned `MEAN 00-15`, and drawing channel 0 -- or drawing
a mean of decibels, which is a different quantity that looks just as much
like a spectrogram.  Neither is visible to `isVisible()` and neither changes
a row height, so every claim here is made about the **image the item holds**
and the **levels the colour bar is set to**, never about whether something
is on screen.

The recording is an array in miniature: a burst that reaches every electrode
plus noise that does not.  A stack of unrelated tones would let a mean-of-
decibels pass, and would say nothing about why the mean is worth drawing.
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

from PySide6.QtCore import QPoint  # noqa: E402
from thunderlab.powerspectrum import decibel  # noqa: E402

from test_panelsplitter import (  # noqa: E402
    FRAMES,
    RATE,
    app,  # noqa: F401  - the session QApplication fixture
    open_stack,
    panel,
    pump,
    settle,
    spec_image,
)

from audian import smoothing  # noqa: E402
from audian.spectrogramplot import (  # noqa: E402
    MAX_CHANNEL_LABEL_CHARS,
    channel_range_label,
)

#: Channels of the synthetic array.  Sixteen is the design centre of this
#: application, and the only count at which the mode it replaces scrolls.
CHANNELS = 16


def array_signal(channels: int, seed: int = 20250916) -> np.ndarray:
    """A burst on every electrode, plus independent noise on each.

    What the animal does reaches the whole array; what the water and the
    electronics do does not.  That difference is the entire reason a mean
    over the channels is worth drawing -- it divides the incoherent half
    down and leaves the coherent half where it was -- so a fixture that does
    not have it cannot test the feature, only its plumbing.
    """
    rng = np.random.default_rng(seed)
    t = np.arange(FRAMES) / RATE
    burst = np.zeros(FRAMES)
    for start in (0.5, 1.7, 2.9):
        i0 = int(start * RATE)
        i1 = i0 + int(0.25 * RATE)
        burst[i0:i1] = 0.3 * np.sin(2 * np.pi * 900 * t[i0:i1])
    signal = np.zeros((FRAMES, channels), dtype=np.float32)
    for c in range(channels):
        # a real grid does not see one amplitude everywhere:
        gain = 0.4 + 0.6 * (c % 4) / 3.0
        signal[:, c] = gain * burst + 0.02 * rng.standard_normal(FRAMES)
    return signal


@pytest.fixture(scope="module")
def stack(app, tmp_path_factory):  # noqa: F811
    """Sixteen electrodes of a coherent burst in incoherent noise."""
    yield from open_stack(
        app,
        tmp_path_factory.mktemp("mean16"),
        CHANNELS,
        array_signal(CHANNELS),
    )


@pytest.fixture
def mean_off(stack):
    """Put the stack back afterwards, so tests do not inherit each other."""
    yield
    for c in list(stack.solo_channels):
        stack.toggle_solo(c)
    for c in list(stack.muted_channels):
        stack.toggle_mute(c)
    if not stack.rail_visible:
        stack.toggle_rail()
    stack.set_mean_spectrogram(False)
    stack.set_panels(traces=True, specs=1)
    settle()
    pump(0.3)


# ---------------------------------------------------------------- the rulers


def spec_plot(browser, channel):
    return panel(browser, "spectrogram").axs[channel]


def spec_item(browser, channel):
    """The `SpecItem` of one lane, the object that holds the pixels."""
    for item in spec_plot(browser, channel).data_items:
        if hasattr(item, "_image_range"):
            return item
    raise AssertionError(f"lane {channel} has no spectrogram item")


def uploaded_rows(item):
    """The (time, channel, freq) slice the item's image was made from."""
    i0, i1, stride = item._image_range
    return item.data.buffer[i0:i1:stride]


def enter_mean(browser):
    """Shift+F2 from the traces-off all-spectrograms mode, and settle."""
    browser.set_panels(traces=False, specs=1)
    settle()
    pump(0.4)
    browser.set_mean_spectrogram(True)
    settle()
    pump(0.6)
    lane = browser.mean_spec_lane()
    assert lane is not None, "mean mode did not pick a lane"
    return lane


def pane_x(browser, widget, item):
    """An item's left and right edge in the stack pane's own pixels."""
    rect = item.mapRectToScene(item.boundingRect())
    origin = widget.mapTo(browser.stack_pane, QPoint(0, 0)).x()
    return origin + rect.left(), origin + rect.right()


# ------------------------------------------------------------- what it draws


def test_the_mean_panel_draws_the_mean_of_the_power(stack, mean_off):
    """The one assertion the whole feature stands on.

    Not "the panel has an image" -- the mode it replaces had sixteen of
    those -- but that the image is `decibel(mean over channels)` of the very
    rows the item uploaded, bin for bin.
    """
    lane = enter_mean(stack)
    item = spec_item(stack, lane)
    assert item.mean_channels == list(range(CHANNELS))
    rows = uploaded_rows(item)
    with np.errstate(all="ignore"):
        want = decibel(rows.mean(axis=1).T)
    image = spec_image(stack, lane)
    assert image is not None and image.size > 0, "the mean panel is empty"
    assert image.shape == want.shape
    assert np.array_equal(image, want, equal_nan=True)


def test_the_mean_of_the_decibels_is_a_different_picture(stack, mean_off):
    """The trap, and the proof that the test above has teeth.

    Averaging decibels is the geometric mean of the powers.  It draws
    something that looks entirely like a spectrogram, so the only way to
    know which one is on screen is to measure -- and on this array it is not
    even close.
    """
    lane = enter_mean(stack)
    item = spec_item(stack, lane)
    rows = uploaded_rows(item)
    with np.errstate(all="ignore"):
        right = decibel(rows.mean(axis=1))
        wrong = np.mean(decibel(rows), axis=1)
    good = np.isfinite(right) & np.isfinite(wrong)
    assert good.any(), "nothing to compare"
    diff = np.abs(right[good] - wrong[good])
    assert np.median(diff) > 1.0, (
        f"the two orders differ by a median of only {np.median(diff):.2f} dB "
        f"on this fixture, so it cannot tell them apart"
    )
    image = spec_image(stack, lane)
    assert not np.array_equal(image, wrong.T, equal_nan=True)


def test_the_mean_panel_is_not_one_of_the_channels(stack, mean_off):
    """A mean that happens to equal a channel would pass every test above."""
    lane = enter_mean(stack)
    item = spec_item(stack, lane)
    rows = uploaded_rows(item)
    image = spec_image(stack, lane)
    for c in range(CHANNELS):
        with np.errstate(all="ignore"):
            one = decibel(rows[:, c, :].T)
        assert not np.array_equal(image, one, equal_nan=True), (
            f"the mean panel is drawing channel {c}"
        )


def test_the_pointer_readout_agrees_with_the_picture(stack, mean_off):
    """The readout under the cursor has to be the pixel it is standing on.

    `get_power` reads the buffer directly rather than the uploaded image, so
    it is a second implementation of the same reduction and the one most
    easily left indexing a channel.
    """
    lane = enter_mean(stack)
    item = spec_item(stack, lane)
    data = item.data
    i0, i1, stride = item._image_range
    # spread over the rows the item actually uploaded, whatever the buffer
    # came out at, rather than over indices that happen to fit one fixture:
    span = i1 - i0
    for frac, freq in ((0.1, 900.0), (0.5, 300.0), (0.9, 1800.0)):
        ti = min(i1 - 1, i0 + int(frac * span))
        # A column is drawn at its window's centre, not its leading edge, so
        # the time that lands on frame `ti` is its left edge plus the shift.
        # Frequency bins are centred too, so the bin under `freq` is the
        # nearest one rather than the one below it.  Both were the other way
        # round, which is what put an nfft-dependent bias into every label
        # drawn on a spectrogram.
        t = (data.offset + ti) / data.rate + item.time_shift()
        fi = int(np.floor(freq / data.fresolution + 0.5))
        with np.errstate(all="ignore"):
            want = decibel(float(np.mean(data.buffer[ti, :, fi])))
            one = decibel(float(data.buffer[ti, 0, fi]))
        got = item.get_power(t, freq)
        assert got == pytest.approx(want, abs=1e-9), (
            f"readout at t={t:.3f} f={freq} says {got:.3f} dB, "
            f"the picture says {want:.3f} dB"
        )
        assert abs(got - one) > 1e-6, "the readout is still channel 0's"


@pytest.fixture
def plain_again(stack):
    """Put the smoothing back, whatever a test did to it.

    The `stack` fixture is module-scoped, so a test that left a filter on
    would hand every test after it a different picture -- and the two that
    measure the colour ramp fit to what is drawn, which is exactly what a
    filter changes.
    """
    yield
    stack.set_spec_smoothing(smoothing.DEFAULT, dispatch=False, save=False)
    settle()
    pump(0.4)


def probe_points(item):
    """A few (buffer index, frequency) pairs inside what is uploaded."""
    i0, i1, _stride = item._image_range
    span = i1 - i0
    for frac, freq in ((0.1, 900.0), (0.5, 300.0), (0.9, 1800.0)):
        yield min(i1 - 1, i0 + int(frac * span)), freq


def test_an_interpolating_smoothing_leaves_the_readout_exact(
    stack, mean_off, plain_again
):
    """Bilinear is a statement about pixels, so the numbers do not move.

    The distinction `smoothing.changes_values` draws, made visible: Qt
    interpolating between bin centres cannot change what the bin says, so
    `SpecItem.get_power` keeps its exact path and returns what it returned
    before smoothing existed.
    """
    lane = enter_mean(stack)
    item = spec_item(stack, lane)
    data = item.data
    before = {
        (ti, freq): item.get_power((data.offset + ti) / data.rate, freq)
        for ti, freq in probe_points(item)
    }
    stack.set_spec_smoothing("bilinear")
    settle()
    pump(0.4)
    assert item.smoothing == "bilinear"
    for (ti, freq), want in before.items():
        got = item.get_power((data.offset + ti) / data.rate, freq)
        assert got == pytest.approx(want, abs=1e-12), (
            f"bilinear moved the readout at f={freq} from {want} to {got}"
        )


def test_a_filtering_smoothing_moves_the_readout_on_to_the_pixel(
    stack, mean_off, plain_again
):
    """With a filter on, the readout is the pixel and not the raw bin.

    The invariant the test above this one states, kept under a filter that
    would otherwise break it: the drawn number is a weighted mean of its
    neighbours, so a readout that still went to the buffer would disagree
    with the pixel the cursor is standing on -- measured elsewhere at a
    median of 3.0 dB and up to 50.7 dB, which is a chirp onset.

    Both halves are asserted.  That the readout equals the image cell is
    the claim; that it has actually *moved* is what stops the test passing
    on a build where the filter silently did nothing.
    """
    lane = enter_mean(stack)
    item = spec_item(stack, lane)
    data = item.data
    # a column is drawn at its window's centre; see the test above
    raw = {
        (ti, freq): item.get_power(
            (data.offset + ti) / data.rate + item.time_shift(), freq
        )
        for ti, freq in probe_points(item)
    }
    stack.set_spec_smoothing("gaussian")
    settle()
    pump(0.4)
    assert item.smoothing == "gaussian"
    i0, _i1, stride = item._image_range
    moved = 0
    for (ti, freq), before in raw.items():
        t = (data.offset + ti) / data.rate + item.time_shift()
        got = item.get_power(t, freq)
        cell = item.image[
            int(np.floor(freq / data.fresolution + 0.5)), (ti - i0) // stride
        ]
        assert got == pytest.approx(float(cell), abs=1e-9), (
            f"readout at f={freq} says {got:.3f} dB and the pixel {cell:.3f} dB"
        )
        if abs(got - before) > 1e-6:
            moved += 1
    assert moved, "the filter changed no readout at all, so it drew nothing"


def test_the_smoothing_reaches_every_lane(stack, mean_off, plain_again):
    """One dropdown, every spectrogram -- and the images are rebuilt.

    `SpecItem.set_smoothing` throws the uploaded crop away, and nothing
    else would: the hysteresis in `update_plot` keys off the time range,
    the stride and the buffer's change flag, none of which knows what
    filter the pixels were computed through.

    The unsmoothed image really does hold ``-inf`` -- the buffer's trailing
    zeros are `decibel(0)` -- so this is also where `smoothing.FLOOR_DB`
    earns itself on real pixels rather than on a constructed array: the
    filtered image has to come back finite everywhere.
    """
    stack.set_panels(traces=False, specs=1)
    settle()
    pump(0.4)
    items = [spec_item(stack, c) for c in stack.visible_channels()]
    assert items, "no lane is showing a spectrogram"
    assert all(item.smoothing == smoothing.DEFAULT for item in items)
    before = [np.array(item.image, copy=True) for item in items]

    stack.set_spec_smoothing("gaussian-strong")
    settle()
    pump(0.6)
    assert stack.spec_smoothing == "gaussian-strong"
    for item, was in zip(items, before):
        assert item.smoothing == "gaussian-strong"
        assert item.image is not None
        assert item.image.shape == was.shape
        assert np.isfinite(item.image).all(), (
            f"lane {item.channel} kept a non-finite bin through the filter"
        )
        live = np.isfinite(was)
        assert not np.allclose(item.image[live], was[live]), (
            f"lane {item.channel} is still showing the unsmoothed pixels"
        )
        assert np.std(item.image[live]) < np.std(was[live])


# ------------------------------------------------------------- the colour ramp


def test_the_mean_panel_fits_a_ramp_to_its_own_span(stack, mean_off):
    """The floor a channel and the mean share; the top they do not.

    Averaging pushes the incoherent noise floor down and leaves the coherent
    burst where it is, so the mean has more contrast than any one electrode
    and needs a longer ramp to show it.  A mean drawn against a channel's
    mapping is still a spectrogram -- just a flatter one, which is why this
    asks for numbers rather than for pixels.
    """
    lane = enter_mean(stack)
    plot = spec_plot(stack, lane)
    item = spec_item(stack, lane)
    rows = uploaded_rows(item)
    fitted = plot._level_range(rows.mean(axis=1))
    per_channel = plot._level_range(rows[:, lane, :])
    assert fitted is not None and per_channel is not None
    assert plot.cbar.levels() == pytest.approx(fitted, abs=0.01), (
        f"the bar reads {plot.cbar.levels()}, the mean's own fit is {fitted}"
    )
    assert plot.cbar.levels() != pytest.approx(per_channel, abs=0.01), (
        "the mean is showing a ramp fitted to a single channel"
    )
    assert plot.fits_levels(), "the mean panel is not allowed to fit its own ramp"


def test_the_mean_panel_owns_the_ramp_wherever_it_lands(stack, mean_off):
    """Solo can take channel 0 off the screen, and the mean goes with it.

    The lane follows the first channel still selected, so the panel holding
    the averaged block is on lane 3 here.  A level-fit rule that only ever
    trusted channel 0 would leave that panel on whatever ramp was last in
    force, with nothing able to replace it.
    """
    enter_mean(stack)
    stack.toggle_solo(3)
    stack.toggle_solo(5)
    settle()
    pump(0.6)
    lane = stack.mean_spec_lane()
    assert lane == 3
    plot = spec_plot(stack, lane)
    item = spec_item(stack, lane)
    rows = uploaded_rows(item)
    fitted = plot._level_range(rows[:, [3, 5], :].mean(axis=1))
    assert fitted is not None
    assert plot.cbar.levels() == pytest.approx(fitted, abs=0.01)


def test_leaving_the_mean_puts_the_channel_back_in_the_lane(stack, mean_off):
    """The lane and the item are the same objects, so both have to be undone.

    The image cache keys off the time range and the buffer's change flag,
    neither of which knows what the pixels were computed from; the level
    mapping is shared across the stack and was last written by the mean.  So
    the round trip is asserted on both.
    """
    lane = enter_mean(stack)
    stack.set_mean_spectrogram(False)
    settle()
    pump(0.6)
    assert stack.mean_spec_lane() is None
    item = spec_item(stack, lane)
    assert item.mean_channels is None
    rows = uploaded_rows(item)
    with np.errstate(all="ignore"):
        want = decibel(rows[:, lane, :].T)
        mean = decibel(rows.mean(axis=1).T)
    image = spec_image(stack, lane)
    assert np.array_equal(image, want, equal_nan=True), (
        "lane 0 is still showing the average"
    )
    assert not np.array_equal(image, mean, equal_nan=True)
    plot = spec_plot(stack, lane)
    fitted = plot._level_range(rows[:, lane, :])
    assert plot.cbar.levels() == pytest.approx(fitted, abs=0.01), (
        f"the bar kept the mean's ramp {plot.cbar.levels()}, "
        f"channel {lane}'s is {fitted}"
    )


# ------------------------------------------------------------- what it says


def test_the_caption_does_not_claim_to_be_a_channel(stack, mean_off):
    lane = enter_mean(stack)
    plot = spec_plot(stack, lane)
    assert plot.channel_label.toPlainText() == "MEAN 00-15"
    stack.set_mean_spectrogram(False)
    settle()
    pump(0.4)
    assert spec_plot(stack, lane).channel_label.toPlainText().startswith("CH 00")


def test_a_narrowed_selection_is_named_in_the_caption(stack, mean_off):
    """Solo and mute pick what the mean averages, so they have to show."""
    enter_mean(stack)
    stack.toggle_solo(3)
    stack.toggle_solo(5)
    settle()
    pump(0.5)
    lane = stack.mean_spec_lane()
    assert stack.mean_channels() == [3, 5]
    assert spec_plot(stack, lane).channel_label.toPlainText() == "MEAN 03,05"
    assert "03, 05" in stack.mean_spectrogram_message()


def test_solo_narrows_what_the_mean_actually_averages(stack, mean_off):
    """Naming a narrower set in the caption is not the same as drawing it."""
    enter_mean(stack)
    stack.toggle_solo(3)
    stack.toggle_solo(5)
    settle()
    pump(0.6)
    lane = stack.mean_spec_lane()
    item = spec_item(stack, lane)
    assert item.mean_channels == [3, 5]
    rows = uploaded_rows(item)
    with np.errstate(all="ignore"):
        want = decibel(rows[:, [3, 5], :].mean(axis=1).T)
        all16 = decibel(rows.mean(axis=1).T)
    image = spec_image(stack, lane)
    assert np.array_equal(image, want, equal_nan=True)
    assert not np.array_equal(image, all16, equal_nan=True), (
        "the caption says two channels and the picture is still all sixteen"
    )


@pytest.mark.parametrize(
    "channels,expected",
    [
        (range(16), "00-15"),
        ([3, 5], "03,05"),
        ([0], "00"),
        ([0, 1, 2, 3, 9], "00-03,09"),
        ([0, 3, 6, 9, 12, 15], "00,03,06,09,12,15"),
        # 23 glyphs of list: folded rather than allowed to run off the lane
        (range(0, 16, 2), "8 ch"),
        ([], "none"),
    ],
)
def test_the_channel_list_stays_on_one_line(channels, expected):
    label = channel_range_label(channels)
    assert label == expected
    assert len(label) <= MAX_CHANNEL_LABEL_CHARS


# ------------------------------------------------------------- the geometry


def test_the_stack_collapses_to_one_lane_with_nothing_left_to_scroll(stack, mean_off):
    """Full screen is half of what was asked for; the other half is that the
    fifteen lanes it replaces actually go away.

    Hiding a figure is what collapses it: `lane_geometry` gives a row it is
    not drawing a minimum height of zero, but the figure keeps the
    `setFixedHeight` of the last pass it *was* drawn in.  Without that the
    mode came out as one 498 px panel over fifteen 120 px ghosts and a
    scrollbar 1830 px long.
    """
    lane = enter_mean(stack)
    assert stack.visible_channels() == [lane]
    assert stack.stack_area.verticalScrollBar().maximum() == 0
    for c in range(CHANNELS):
        assert stack.figs[c].isVisible() == (c == lane), f"lane {c}"
    row = spec_plot(stack, lane).geometry().height()
    viewport = stack.stack_area.viewport().height()
    assert row > 0.75 * viewport, (
        f"the mean panel is {row:.0f} px in a {viewport} px viewport"
    )


def test_the_rail_is_off_screen_while_the_mean_is_showing(stack, mean_off):
    """One rail row labelled `00`, with solo and mute for channel 0 alone,
    pinned beside a panel captioned `MEAN 00-15`, names the wrong thing and
    reaches none of the other fifteen.  The reader's F7 setting is left
    alone, so leaving the mode gives the rail back as they left it.
    """
    assert stack.rail_visible
    lane = enter_mean(stack)
    assert not stack.rail_shown()
    assert stack.rail_visible, "the reader's own setting was overwritten"
    assert not stack.rail_rows[lane].isVisible()
    assert stack.stack_grid.columnMinimumWidth(0) == 0
    stack.set_mean_spectrogram(False)
    settle()
    pump(0.4)
    assert stack.rail_shown()
    assert stack.rail_rows[lane].isVisible()
    assert stack.stack_grid.columnMinimumWidth(0) > 0


def test_no_lane_is_marked_as_current_while_the_mean_is_showing(stack, mean_off):
    """The selection cue has nothing to select between.

    The mean borrows a lane, so with the focus on channel 0 -- the default --
    the panel came up with the raised ground and the bold primary caption
    that mean "this is the one you picked", and with the focus anywhere else
    it did not.  Same mode, same picture, a cue that flickers on a state the
    reader cannot see.  The focus itself is left alone, so leaving the mode
    gives it back.
    """
    stack.rail_clicked(0, False)
    settle()
    lane = enter_mean(stack)
    assert lane == 0, "this test only means something on the borrowed lane"
    assert stack.current_channel == 0
    assert not spec_plot(stack, lane).current
    stack.set_mean_spectrogram(False)
    settle()
    pump(0.4)
    assert stack.current_channel == 0
    assert spec_plot(stack, 0).current, "the cue did not come back"


def test_the_focus_survives_the_round_trip(stack, mean_off):
    """A mode that fits on one screen must not cost the reader their place."""
    stack.set_panels(traces=False, specs=1)
    settle()
    stack.rail_clicked(12, False)
    settle()
    pump(0.3)
    assert stack.current_channel == 12
    stack.set_mean_spectrogram(True)
    settle()
    pump(0.4)
    assert stack.current_channel == 12
    assert stack.visible_channels() == [0]
    stack.set_mean_spectrogram(False)
    settle()
    pump(0.4)
    assert stack.current_channel == 12
    assert spec_plot(stack, 12).current


def test_the_time_axis_lines_up_under_the_mean_panel(stack, mean_off):
    """A full-screen spectrogram's shared axis is measured off the lane
    rather than off the window.

    It also has to survive a gesture that changes the lane's width without
    changing the stack's height: with one lane there is no scroll area
    resize to re-align the axis afterwards, so F5 left the ticks 136 px
    short of the panel they belong to and they stayed that way.

    The sweep starts from the state `DataBrowser.show_cbars` opens in, so
    the first toggle is the *first* time this stack has ever shown a colour
    bar -- which is its own case, and the one
    `DataBrowser.schedule_axis_alignment` repeats a pass for: a
    `pg.ColorBarItem` that has not been laid out publishes no width, and a
    single deferred measurement caught the lane 59.5 px too wide.
    """
    lane = enter_mean(stack)
    for gesture in ("none", "colorbars on", "colorbars off"):
        if gesture != "none":
            stack.toggle_colorbars()
            settle()
            pump(0.4)
        box = pane_x(stack, stack.figs[lane], stack.time_plot(lane).getViewBox())
        axis = pane_x(stack, stack.taxis_fig, stack.taxis)
        assert abs(box[0] - axis[0]) <= 1.0, gesture
        assert abs(box[1] - axis[1]) <= 1.0, (
            f"{gesture}: the axis ends at {axis[1]:.1f} and the panel at {box[1]:.1f}"
        )


# ------------------------------------------------------------- the mode itself


def test_the_shortcut_opens_the_mode_it_lives_in(stack, mean_off):
    """Shift+F2 from the traces-on default turns the traces off itself.

    `toggle_traces` already forces `show_specs = 1` for the same reason: a
    shortcut that silently does nothing outside its own mode is one the
    reader stops trusting.
    """
    stack.set_panels(traces=True, specs=1)
    settle()
    assert stack.show_traces
    stack.set_mean_spectrogram(True)
    settle()
    pump(0.5)
    assert stack.mean_spec
    assert not stack.show_traces
    assert stack.show_specs > 0


def test_the_shortcut_is_a_round_trip_from_wherever_it_started(stack, mean_off):
    """Twice puts the reader back, and does not invent a mode they never
    asked for on the way out.

    ``(True, 3)`` used to be a third state here, back when `show_specs` was
    an F3 size as well as an on/off.  F3 is a toggle now and `set_panels`
    normalises anything truthy to 1, so a 3 is not a state the stack can be
    in.  ``specs=0`` is not a replacement for it: this mode *is* a
    spectrogram mode, and `set_mean_spectrogram` turns the spectrogram on to
    enter it, so a round trip that started with it off does not end with it
    off and should not.  What is left is the two states the traces toggle
    gives, which is what the mode actually reads."""
    for traces, specs in ((True, 1), (False, 1)):
        stack.set_panels(traces=traces, specs=specs)
        settle()
        pump(0.3)
        stack.set_mean_spectrogram(True)
        settle()
        pump(0.4)
        stack.set_mean_spectrogram(False)
        settle()
        pump(0.4)
        assert (stack.show_traces, stack.show_specs) == (traces, specs)
        assert not stack.mean_spec


def test_the_traces_coming_back_ends_the_mean(stack, mean_off):
    """A mean over the array beside one channel's waveform is two pictures
    of two different things in one lane."""
    enter_mean(stack)
    stack.toggle_traces()
    settle()
    pump(0.5)
    assert stack.show_traces
    assert not stack.mean_spec
    assert stack.visible_channels() == list(range(CHANNELS))
    assert spec_item(stack, 0).mean_channels is None


def test_the_spectrograms_going_away_ends_the_mean(stack, mean_off):
    """With no spectrogram there is nothing left to average."""
    enter_mean(stack)
    stack.set_panels(specs=0)
    settle()
    pump(0.5)
    assert not stack.mean_spec
    assert spec_item(stack, 0).mean_channels is None


def test_setting_the_spectrogram_on_again_does_not_end_the_mean(stack, mean_off):
    """Asking for the panel that is already there is not a reason to leave.

    This used to loop over F3 sizes 2, 3 and 4 and was called
    `test_the_spectrogram_size_does_not_end_the_mean`.  F3 is a toggle now
    and `set_panels` normalises anything truthy to 1, so those three ran the
    same assertion three times over a size that no longer exists.  What is
    left of the claim -- and it is still worth holding -- is that a
    `set_panels` which does not turn the spectrogram *off* leaves the mean
    alone.
    """
    lane = enter_mean(stack)
    for specs in (1, 2):
        stack.set_panels(specs=specs)
        settle()
        pump(0.4)
        assert stack.show_specs == 1
        assert stack.mean_spec, f"specs={specs} dropped the mean"
        assert spec_item(stack, lane).mean_channels == list(range(CHANNELS))


def test_the_toolbar_says_which_mode_the_stack_is_in(stack, mean_off):
    """An icon-only toggle has to carry its own state, or it is decoration."""
    window = stack.window()
    act = window.acts.toggle_mean_spec
    assert act.isCheckable()
    assert act.shortcut().toString() == "Shift+F2"
    stack.set_panels(traces=False, specs=1)
    settle()
    window.toggle_mean_spectrogram()
    settle()
    pump(0.5)
    assert stack.mean_spec
    assert act.isChecked()
    window.toggle_mean_spectrogram()
    settle()
    pump(0.5)
    assert not stack.mean_spec
    assert not act.isChecked()


def test_a_column_is_drawn_at_the_centre_of_its_window(stack, mean_off):
    """The bias every spectrogram label carried, pinned so it cannot return.

    `BufferedSpectrogram.process` transforms frame *j* from samples
    ``[j*hop, j*hop + nfft)``, so its window is centred at
    ``(j*hop + nfft/2)/fs``.  The image was drawn from ``j*hop/fs`` and one
    hop wide, putting the cell's own centre ``(nfft-hop)/2`` samples early
    -- 0.31 s at the band plugin's default nfft of 16384 on a 20 kHz
    recording -- and `label_from_region` stored that straight into the CSV
    as plain seconds, with nothing on disk saying which nfft produced it.

    thunderlab's axis is window centres (scipy leaves `boundary` None, so
    the half-window correction it would otherwise apply never happens), so
    the picture was the odd one out and it is the picture that moved.
    """
    lane = enter_mean(stack)
    item = spec_item(stack, lane)
    data = item.data
    i0, i1, _stride = item._image_range
    # in data coordinates: `boundingRect` is the image's own, in pixels, and
    # `setRect` places it through the item's transform
    rect = item.mapRectToParent(item.boundingRect())

    fs = data.source.rate
    expected_shift = (data.nfft - data.hop) / (2.0 * fs)
    assert item.time_shift() == pytest.approx(expected_shift)
    assert expected_shift > 0, "the fixture has to have nfft > hop to mean anything"

    # the first column's cell is centred on its own window's centre
    first = data.offset + i0
    cell_centre = rect.left() + 0.5 / data.rate
    window_centre = (first * data.hop + data.nfft / 2.0) / fs
    assert cell_centre == pytest.approx(window_centre, abs=1e-9)

    # and the frequency axis is centred too: bin 0 straddles zero rather
    # than sitting entirely above it
    assert rect.top() == pytest.approx(-0.5 * data.fresolution, abs=1e-9)
    assert rect.height() == pytest.approx(
        fs / 2 + data.fresolution, abs=1e-9
    )


def test_the_readout_says_nothing_rather_than_wrapping_off_the_left_edge(
    stack, mean_off
):
    """A centred axis reaches before the first frame and below the first bin.

    `get_power` bounded only the upper end, so a negative index read the
    far end of the buffer and returned a confident number for a place the
    picture does not cover.
    """
    lane = enter_mean(stack)
    item = spec_item(stack, lane)
    data = item.data

    ti, fi = item.cell_at(-1.0, -data.fresolution)
    assert ti < 0 and fi < 0, "the fixture no longer exercises the guard"
    assert item.get_power(-1.0, -data.fresolution) is None
    assert item.drawn_power(-1.0, -data.fresolution) is None
