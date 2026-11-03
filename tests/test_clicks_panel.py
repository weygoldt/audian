"""Click removal through the running application, not just the arrays.

Runs offscreen::

    QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_clicks_panel.py -q

`test_clicks.py` pins what the repair does to samples.  What it cannot see
is the wiring: that the Filter page's switch reaches the filtered trace and,
through it, the spectrogram; that switching it off gives the raw samples
back; that the raw buffer claims its margins only while it is on; and that
the menu, the threshold row, the trace colour and a linked tab follow.

The recording is a tone on every channel with noise under it, and clicks on
two of the four channels: one loud, one weak enough that a high threshold
lets it through.
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

from test_panelsplitter import (  # noqa: E402
    FRAMES,
    RATE,
    open_stack,
    pump,
)

from audian import theme  # noqa: E402
from audian.bufferedfilter import BufferedFilter  # noqa: E402
from audian.clicks import ClickRemoval  # noqa: E402

CHANNELS = 4
#: frames of the loud clicks, on channels 0 and 1
LOUD = (int(0.8 * RATE), int(2.2 * RATE))
#: frame of the weak click, on channel 0
WEAK = int(3.1 * RATE)


def click(at, amplitude, tau=0.0003):
    k = np.arange(FRAMES) - at
    return np.where(k >= 0, amplitude * np.exp(-np.clip(k, 0, None) / (tau * RATE)), 0)


def clean_signal(seed: int = 20261103) -> np.ndarray:
    """The recording without its clicks: what a repair should give back."""
    rng = np.random.default_rng(seed)
    t = np.arange(FRAMES) / RATE
    signal = rng.normal(0.0, 2e-3, (FRAMES, CHANNELS))
    for c in range(CHANNELS):
        signal[:, c] += 0.1 * np.sin(2 * np.pi * (300.0 + 40 * c) * t)
    return signal


def click_signal(seed: int = 20261103) -> np.ndarray:
    """Measured on this recording as written (16 bit): the weak click is
    found at thresholds 12 and 24, and not at 40 or more."""
    signal = clean_signal(seed)
    for at in LOUD:
        signal[:, 0] += click(at, 0.6)
        signal[:, 1] += click(at, -0.5)
    signal[:, 0] += click(WEAK, 0.1)
    return signal


CLEAN = clean_signal()


@pytest.fixture(scope="module")
def view(app, tmp_path_factory):  # noqa: F811
    yield from open_stack(
        app, tmp_path_factory.mktemp("clicks"), CHANNELS, click_signal()
    )


def frames_of(trace, at, before=0.002, after=0.004):
    """Buffer rows of the few milliseconds around absolute frame `at`."""
    lo = at - int(before * RATE) - trace.offset
    hi = at + int(after * RATE) - trace.offset
    assert 0 <= lo < hi <= len(trace.buffer), "the click is not in the buffer"
    return slice(lo, hi)


def filtered(view):
    return view.data["filtered"]


def raw_rows(view):
    """The raw samples at the frames the filtered buffer holds, as it holds them."""
    trace = filtered(view)
    raw = view.data.data
    lo = trace.offset - raw.offset
    return np.asarray(raw.buffer[lo : lo + len(trace.buffer)], dtype=trace.dtype)


def error(view, at, channel):
    """How far the filtered trace is from the recording without clicks,
    around the click at absolute frame `at`."""
    trace = filtered(view)
    rows = frames_of(trace, at)
    truth = CLEAN[trace.offset + rows.start : trace.offset + rows.stop, channel]
    return float(np.abs(trace.buffer[rows, channel] - truth).max())


def high_band_peak(view, channel=0):
    """Loudest column of the top quarter of the spectrogram [dB]."""
    spec = view.data[view.spectrogram]
    buffer = np.asarray(spec.buffer)
    assert buffer.size > 0, "spectrogram buffer is empty"
    top = np.asarray(spec.frequencies) > 0.75 * RATE / 2
    return float(10 * np.log10(buffer[:, channel, top].mean(axis=-1).max()))


def lanes(view):
    """The plot items drawing the filtered trace, one per visible channel."""
    from audian.traceitem import TraceItem

    items = []
    for panel in view.panels.values():
        for ax in panel.axs:
            if not hasattr(ax, "listDataItems"):
                continue  # a splitter between panels, not a plot
            for item in ax.listDataItems():
                if isinstance(item, TraceItem) and item.data is filtered(view):
                    items.append(item)
    return items


def pen_color(item):
    return item.opts["pen"].color().name()


def role_color(item, role):
    """The colour a lane like `item` is painted in when it is `role`."""
    return (
        theme.waveform_pen(role, selected=item.selected, dense=item.dense)
        .color()
        .name()
    )


def switch(view, on):
    view.clicksw.setChecked(on)
    pump(1.5)


class TestFilterPage:
    def test_nothing_is_removed_to_begin_with(self, view):
        assert not filtered(view).remove_clicks
        assert not view.clicksw.isChecked()
        assert np.array_equal(filtered(view).buffer, raw_rows(view))

    def test_the_switch_repairs_the_filtered_trace(self, view):
        """Measured: 0.50 to 0.60 off the clean recording before, 0.007 to
        0.012 after, at both loud clicks on both channels."""
        switch(view, False)
        before = [error(view, at, c) for at in LOUD for c in (0, 1)]
        switch(view, True)
        assert filtered(view).remove_clicks
        after = [error(view, at, c) for at in LOUD for c in (0, 1)]
        assert min(before) > 0.4
        assert max(after) < 0.04
        switch(view, False)

    def test_outside_the_clicks_the_samples_are_the_raw_ones(self, view):
        """No cutoffs: the filtered trace is the raw one except where it was
        repaired, and a channel without clicks is not touched at all.
        Measured: 0.5 % and 0.35 % of the two clicked channels changed."""
        switch(view, True)
        trace = filtered(view)
        raw = raw_rows(view)
        changed = trace.buffer != raw
        assert changed[:, 0].any() and changed[:, 1].any()
        assert not changed[:, 2].any() and not changed[:, 3].any()
        assert changed.mean() < 0.01
        switch(view, False)

    def test_the_spectrogram_follows(self, view):
        """The spectrogram is computed from the filtered trace, so it loses
        the clicks with it -- the reason the switch is on the Filter page
        and not with the spectrogram's own denoisers.  Measured: the loudest
        column of the top quarter from -64.7 dB to -87.1 dB."""
        switch(view, False)
        before = high_band_peak(view)
        switch(view, True)
        after = high_band_peak(view)
        assert before - after > 10.0
        switch(view, False)

    def test_switching_it_off_gives_the_raw_samples_back(self, view):
        switch(view, True)
        assert not np.array_equal(filtered(view).buffer, raw_rows(view))
        switch(view, False)
        assert not filtered(view).remove_clicks
        assert np.array_equal(filtered(view).buffer, raw_rows(view))

    def test_the_threshold_is_offered_only_while_it_is_on(self, view):
        switch(view, False)
        assert view.click_rows and all(w.isHidden() for w in view.click_rows)
        switch(view, True)
        assert not any(w.isHidden() for w in view.click_rows)
        switch(view, False)

    def test_a_higher_threshold_leaves_the_weak_click(self, view):
        """Measured: 0.0067 off the clean recording at 12, 0.100 -- the
        whole click -- at 100."""
        switch(view, True)
        assert error(view, WEAK, 0) < 0.03
        view.update_filter(click_threshold=100.0)
        pump(1.5)
        assert filtered(view).click_threshold == 100.0
        assert view.clickthreshw.value() == 100.0
        assert error(view, WEAK, 0) > 0.08
        view.update_filter(click_threshold=BufferedFilter().click_threshold)
        pump(1.5)
        assert error(view, WEAK, 0) < 0.03
        switch(view, False)

    def test_a_threshold_out_of_bounds_is_clamped(self, view):
        view.update_filter(click_threshold=1e6)
        pump(0.5)
        assert filtered(view).click_threshold == view.CLICK_THRESHOLD_BOUNDS[1]
        view.update_filter(click_threshold=BufferedFilter().click_threshold)
        pump(0.5)


class TestBuffers:
    def test_the_raw_buffer_claims_its_margins_only_while_it_is_on(self, view):
        switch(view, False)
        before = (view.data.tbefore, view.data.tafter)
        switch(view, True)
        # a block and a margin, from the settings rather than from
        # `click_reserve`, which is what is under test
        cfg = ClickRemoval()
        need = cfg.block + cfg.margin
        assert view.data.tbefore - before[0] >= need
        assert view.data.tafter - before[1] >= need
        assert view.data.tbefore - before[0] < need + 0.1
        switch(view, False)
        assert (view.data.tbefore, view.data.tafter) == pytest.approx(before)

    def test_the_trace_is_painted_as_filtered_while_it_is_on(self, view):
        """Painting repaired samples cyan would say they are the raw ones."""
        switch(view, False)
        assert theme.waveform_role(filtered(view)) == "raw"
        assert {pen_color(i) for i in lanes(view)} <= {
            role_color(i, "raw") for i in lanes(view)
        }
        switch(view, True)
        assert theme.waveform_role(filtered(view)) == "filtered"
        items = lanes(view)
        assert items
        for item in items:
            assert pen_color(item) == role_color(item, "filtered")
        # the selected lane is drawn in the primary colour whatever it is,
        # so the difference shows on the others
        others = [item for item in items if not item.selected]
        assert others
        for item in others:
            assert pen_color(item) != role_color(item, "raw")
        switch(view, False)


class TestMenu:
    def test_the_entry_follows_the_switch(self, view):
        act = view.window().acts.remove_clicks
        switch(view, True)
        assert act.isChecked()
        switch(view, False)
        assert not act.isChecked()

    def test_the_entry_drives_the_switch(self, view):
        act = view.window().acts.remove_clicks
        act.setChecked(True)
        pump(1.5)
        assert filtered(view).remove_clicks
        assert view.clicksw.isChecked()
        assert error(view, LOUD[0], 0) < 0.04
        act.setChecked(False)
        pump(1.5)
        assert not filtered(view).remove_clicks
        assert not view.clicksw.isChecked()

    def test_the_entry_has_a_key(self, view):
        act = view.window().acts.remove_clicks
        assert act.shortcut().toString() == "Alt+K"


class TestLinkedTabs:
    def test_a_linked_tab_follows(self, view, tmp_path_factory):
        """Link filter holds every open recording at the same filter, and
        click removal is part of the filter."""
        import soundfile

        window = view.window()
        assert window.link_filter
        path = tmp_path_factory.mktemp("clicks2") / "second.wav"
        soundfile.write(path, click_signal(7), RATE)
        window.load_files([str(path)])
        pump(2.5)
        try:
            other = [b for b in window.browsers if b is not view]
            assert len(other) == 1
            other = other[0]
            window.tabs.setCurrentWidget(view)
            pump(0.5)
            switch(view, True)
            assert other.data["filtered"].remove_clicks
            view.update_filter(click_threshold=30.0)
            pump(1.5)
            assert other.data["filtered"].click_threshold == 30.0
            switch(view, False)
            assert not other.data["filtered"].remove_clicks
            view.update_filter(click_threshold=BufferedFilter().click_threshold)
            pump(1.0)
        finally:
            for b in [b for b in window.browsers if b is not view]:
                window.close_tab(window.tabs.indexOf(b))
            window.tabs.setCurrentWidget(view)
            pump(0.5)


LONG = 60 * RATE


def long_signal(seed: int = 3) -> np.ndarray:
    """A minute of tones and noise, a loud click every 5 s on channel 0."""
    rng = np.random.default_rng(seed)
    t = np.arange(LONG) / RATE
    signal = rng.normal(0.0, 2e-3, (LONG, CHANNELS))
    for c in range(CHANNELS):
        signal[:, c] += 0.1 * np.sin(2 * np.pi * (300.0 + 40 * c) * t)
    k = np.arange(LONG) % (5 * RATE) - int(2.5 * RATE)
    signal[:, 0] += np.where(k >= 0, 0.6 * np.exp(-np.clip(k, 0, None) / 2.4), 0)
    return signal


@pytest.fixture(scope="module")
def tight(app, tmp_path_factory):  # noqa: F811
    """A recording longer than its raw buffer, which is only the view and a
    little -- what sixteen channels at 48 kHz get from the byte budget.
    The budget is read at `open` and restored as soon as the window is up."""
    import contextlib

    from audian.data import Data

    stack = open_stack(
        app, tmp_path_factory.mktemp("clicks-tight"), CHANNELS, long_signal()
    )
    budget = Data.buffer_bytes
    Data.buffer_bytes = 1  # every recording gets `min_buffer_time`
    try:
        browser = next(stack)
    finally:
        Data.buffer_bytes = budget
    yield browser
    with contextlib.suppress(StopIteration):
        next(stack)


class TestTightBuffer:
    def test_switching_off_gives_the_raw_buffer_back(self, tight):
        """`place_buffer` keeps a buffer up to twice the view rather than
        reallocate it.  Without `Data.shrink_buffer` the reserve stayed in
        it after switching off, and every jump went on reading it."""
        view = tight
        # 9 s: with its margins it needs 15 s while clicks are removed, more
        # than the 13.25 s the buffer holds around a 5 s view, so the reserve
        # has to be claimed
        view.set_times(toffset=20.0, twindow=9.0)
        pump(1.0)
        raw = view.data.data
        before = len(raw.buffer)
        assert before < raw.frames, "the recording fits in the buffer"
        switch(view, True)
        assert len(raw.buffer) > before
        assert error_free_of_clicks(view)
        switch(view, False)
        assert len(raw.buffer) <= before
        trace = filtered(view)
        assert trace.offset <= int(20.0 * RATE)
        assert trace.offset + len(trace.buffer) >= int(29.0 * RATE)
        assert np.array_equal(trace.buffer, raw_rows(view))


def error_free_of_clicks(view):
    """Every click in the filtered buffer of `tight` is repaired."""
    trace = filtered(view)
    first = trace.offset
    last = trace.offset + len(trace.buffer)
    clicks = [
        at
        for at in range(int(2.5 * RATE), LONG, 5 * RATE)
        if first + RATE // 100 <= at < last - RATE // 100
    ]
    assert clicks, "no click in the buffer"
    return all(
        np.abs(trace.buffer[at - first : at - first + 8, 0]).max() < 0.15
        for at in clicks
    )
