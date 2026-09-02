"""Tests for the cap on how wide a stretch of a recording may be shown.

Runs without Qt::

    .venv/bin/python -m pytest tests/test_plotranges.py -q

`PlotRange` asks an axis for its limits and tells it what to display, and
nothing here needs a real one, so these run against a stand-in and cost
milliseconds.

The cap exists because zooming out is one keystroke to ask for and a great
deal of work to answer: sixteen lanes each read their whole span out of the
buffer and each recompute a spectrogram over it.  What is asserted is that
the window stops growing, that it stops *somewhere stable* -- a cap that
quietly pans the view every time a key is pressed is its own bug -- and that
nothing else lost anything: a short recording is still shown whole, the
frequency axes are untouched, and every part of a long recording is still
reachable.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from audian.plotranges import MAX_TIME_WINDOW_S, PlotRange  # noqa: E402


HOUR = 3600.0


class FakeAxis:
    """What a `PlotRange` asks of an axis: its limits, and to be told a range."""

    def __init__(self, rmin, rmax, rstep=None):
        self._range = (rmin, rmax, rstep)
        self.xrange = None
        self.limits = {}

    def range(self, axspec):
        return self._range

    def setXRange(self, r0, r1):
        self.xrange = (r0, r1)

    def setLimits(self, **kwargs):
        self.limits.update(kwargs)


def make_range(axspec, rmin, rmax, channels=1):
    """A range over `rmin`..`rmax` with one lane per channel, opened."""
    plot_range = PlotRange(axspec, channels)
    axes = [FakeAxis(rmin, rmax) for _ in range(channels)]
    for channel, ax in enumerate(axes):
        plot_range.add_xaxis(ax, channel)
    plot_range.set_limits()
    return plot_range, axes


def span(plot_range, channel=0):
    return plot_range.r1[channel] - plot_range.r0[channel]


# --- the cap ----------------------------------------------------------------


def test_zooming_out_never_shows_more_than_five_minutes():
    """Twenty presses on an hour-long recording, which would reach it all."""
    times, _ = make_range("t", 0.0, HOUR)
    for _ in range(20):
        times.zoom_out()
        assert span(times) <= MAX_TIME_WINDOW_S + 1e-9


def test_zooming_out_settles_on_the_cap_instead_of_stopping_short():
    """It has to arrive AT five minutes, not at the last doubling below it.

    The window opens at ten seconds and doubles, so the step before the cap
    is 160 s.  A guard that refused the step that would overshoot would leave
    the reader stuck there, looking at half of what they asked for and with
    no way to ask for the rest.
    """
    times, _ = make_range("t", 0.0, HOUR)
    for _ in range(20):
        times.zoom_out()
    assert span(times) == pytest.approx(MAX_TIME_WINDOW_S)


def test_a_zoom_out_that_is_already_at_the_cap_moves_nothing():
    """A key that can do nothing must do nothing, rather than pan.

    The cap keeps the left edge for exactly this reason: an anchor that moved
    would walk the window a little further along the recording on every press
    of a key the reader is holding down because they expect it to zoom.
    """
    times, _ = make_range("t", 0.0, HOUR)
    for _ in range(20):
        times.zoom_out()
    settled = (times.r0[0], times.r1[0])
    for _ in range(5):
        times.zoom_out()
        assert (times.r0[0], times.r1[0]) == settled


def test_a_centred_zoom_out_at_the_cap_keeps_its_centre():
    """The same stability for the centred variant, which anchors differently.

    Clamping it the way `set_ranges` clamps -- by keeping the left edge --
    would move the centre left every time, so it does its own clamping about
    the centre and this is what says so.
    """
    times, _ = make_range("t", 0.0, HOUR)
    times.set_ranges(1000.0, None, 200.0)
    for _ in range(5):
        times.zoom_out_centered()
        assert span(times) == pytest.approx(MAX_TIME_WINDOW_S)
        centre = 0.5 * (times.r0[0] + times.r1[0])
        assert centre == pytest.approx(1100.0)


def test_the_widest_view_of_the_whole_recording_is_the_cap():
    """`reset` is the way out to the limits, and it is capped too."""
    times, _ = make_range("t", 0.0, HOUR)
    times.reset()
    assert span(times) == pytest.approx(MAX_TIME_WINDOW_S)
    assert times.r0[0] == pytest.approx(0.0)


def test_the_view_box_is_told_the_cap_so_the_mouse_cannot_go_wider():
    """The keys are not the only way to zoom.

    pyqtgraph does its own wheel and rubber-band zooming inside the view box
    and never asks this class, so a cap applied only where the keys arrive
    would be one the mouse walks straight past.
    """
    times, axes = make_range("t", 0.0, HOUR)
    assert axes[0].limits["maxXRange"] == pytest.approx(MAX_TIME_WINDOW_S)
    assert axes[0].limits["xMax"] == pytest.approx(HOUR)


# --- what the cap must not touch --------------------------------------------


def test_a_recording_shorter_than_the_cap_is_still_shown_whole():
    """Nothing changes for a two minute recording, which is most of them."""
    short = 120.0
    times, axes = make_range("t", 0.0, short)
    times.reset()
    assert span(times) == pytest.approx(short)
    assert axes[0].limits["maxXRange"] == pytest.approx(short)


def test_a_frequency_range_still_opens_on_the_whole_spectrum():
    """The cap is about time.  Nyquist is not expensive to draw."""
    freqs, axes = make_range("f", 0.0, 24000.0)
    freqs.reset()
    assert span(freqs) == pytest.approx(24000.0)
    assert axes[0].limits["maxXRange"] == pytest.approx(24000.0)


def test_the_end_of_a_long_recording_is_still_reachable():
    """The window is capped; the recording is not.

    `rmax` is untouched, so panning still arrives at the last sample -- which
    is the difference between a cap and a truncation.
    """
    times, _ = make_range("t", 0.0, HOUR)
    for _ in range(20):
        times.zoom_out()
    times.end()
    assert times.r1[0] >= HOUR
    assert span(times) == pytest.approx(MAX_TIME_WINDOW_S)


def test_every_channel_shows_the_same_capped_window():
    """Time ranges are shared across the lanes, and stay so at the cap."""
    times, axes = make_range("t", 0.0, HOUR, channels=4)
    for _ in range(20):
        times.zoom_out()
    for channel in range(4):
        assert span(times, channel) == pytest.approx(MAX_TIME_WINDOW_S)
    assert len({ax.xrange for ax in axes}) == 1
