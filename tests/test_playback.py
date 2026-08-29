"""Tests for playback channel selection and amplitude axis labelling.

Both are exercised without a QApplication: the functions under test are
either pure, or read only plain attributes off the browser, so they are
called unbound against a stand-in.
"""

from types import SimpleNamespace

from audian.databrowser import DataBrowser
from audian.timeplot import SI_UNITS, si_prefixable


def _browser(source, current, shown, channels=16, left=0, right=1):
    """The attributes DataBrowser.audio_channels actually reads."""
    return SimpleNamespace(
        audio_source=source,
        current_channel=current,
        show_channels=list(shown),
        audio_left=left,
        audio_right=right,
        data=SimpleNamespace(channels=channels),
    )


def test_selected_source_plays_only_the_current_channel():
    browser = _browser(DataBrowser.AUDIO_SELECTED, 5, range(16))
    assert DataBrowser.audio_channels(browser) == [5]


def test_selected_source_follows_the_selection():
    for channel in (0, 3, 15):
        browser = _browser(DataBrowser.AUDIO_SELECTED, channel, range(16))
        assert DataBrowser.audio_channels(browser) == [channel]


def test_selected_source_never_plays_silence():
    """A hidden current channel falls back rather than playing nothing."""
    browser = _browser(DataBrowser.AUDIO_SELECTED, 9, [1, 2, 3])
    assert DataBrowser.audio_channels(browser) == [1]


def test_shown_source_plays_every_visible_channel():
    browser = _browser(DataBrowser.AUDIO_SHOWN, 5, range(16))
    assert DataBrowser.audio_channels(browser) == list(range(16))


def test_empty_show_channels_falls_back_to_all():
    browser = _browser(DataBrowser.AUDIO_SHOWN, 0, [], channels=4)
    assert DataBrowser.audio_channels(browser) == [0, 1, 2, 3]


def test_si_units_may_be_prefixed():
    for unit in ("V", "A", "Hz", "Pa"):
        assert si_prefixable(unit), unit
    assert "V" in SI_UNITS


def test_non_si_units_are_never_prefixed():
    """The bug this guards: pyqtgraph prefixes any string it is handed.

    thunderlab reports ``a.u.`` for a wav with no unit metadata, and passing
    that to AxisItem.setLabel(units=...) produced "ma.u." with every tick
    value silently multiplied by a thousand -- disagreeing with the stack's
    own amplitude readout directly below it.
    """
    for unit in ("a.u.", "arb", "counts", "dB", "", "a.u"):
        assert not si_prefixable(unit), unit


def test_spectrogram_labels_its_axis_frequency_not_amplitude():
    """A spectrogram's y axis is frequency; its amplitude is the colour bar.

    SpectrogramPlot inherits TimePlot, so the amplitude label leaked onto
    the frequency axis and read "amplitude (a.u.)".
    """
    from audian.spectrogramplot import SpectrogramPlot
    from audian.timeplot import TimePlot

    assert SpectrogramPlot.update_axis_label is not TimePlot.update_axis_label

    calls = []

    class _Axis:
        def setLabel(self, *args, **kwargs):
            calls.append(args)

    plot = SimpleNamespace(
        getAxis=lambda name: _Axis(), data_items=[], _show_tick_values=True
    )
    SpectrogramPlot.update_axis_label(plot)
    assert len(calls) == 1
    text, unit = calls[0][0], calls[0][1]
    assert "frequency" in text and "amplitude" not in text
    assert unit == "Hz"


def test_spectrogram_never_clears_its_axis_label():
    """Clearing it would break the kHz scaling.

    setLabel(None) drops the axis's labelUnits, which is what pyqtgraph's
    auto SI prefixing keys off, and the frequency axis then printed 20000
    where it had printed 20.  In the dense case the label is simply not set.
    """
    from audian.spectrogramplot import SpectrogramPlot

    calls = []

    class _Axis:
        def setLabel(self, *args, **kwargs):
            calls.append(args)

    plot = SimpleNamespace(
        getAxis=lambda name: _Axis(), data_items=[], _show_tick_values=False
    )
    SpectrogramPlot.update_axis_label(plot)
    assert calls == [], calls


def test_pair_source_plays_the_two_chosen_channels_in_order():
    browser = _browser(DataBrowser.AUDIO_PAIR, 0, range(16), left=3, right=9)
    assert DataBrowser.audio_channels(browser) == [3, 9]


def test_pair_ignores_visibility():
    """An explicit pair is a deliberate choice.

    Hiding a lane must not silently change what is in your ears -- which is
    the whole reason the picker exists rather than reusing the shown-channel
    mix.
    """
    browser = _browser(DataBrowser.AUDIO_PAIR, 0, [0, 1], left=3, right=9)
    assert DataBrowser.audio_channels(browser) == [3, 9]


def test_pair_is_clamped_to_the_file():
    """A pair carried over from a wider file must not index past the end."""
    browser = _browser(
        DataBrowser.AUDIO_PAIR, 0, range(2), channels=2, left=9, right=15
    )
    assert DataBrowser.audio_channels(browser) == [1, 1]


def test_pair_may_be_the_same_channel_twice():
    browser = _browser(DataBrowser.AUDIO_PAIR, 0, range(16), left=4, right=4)
    assert DataBrowser.audio_channels(browser) == [4, 4]


def test_source_cycle_visits_every_mode_and_returns():
    """Shift+P steps rather than toggles, so it has to come back round."""
    assert len(DataBrowser.AUDIO_SOURCES) == len(DataBrowser.AUDIO_SOURCE_LABELS)
    assert set(DataBrowser.AUDIO_SOURCES) == {
        DataBrowser.AUDIO_SELECTED,
        DataBrowser.AUDIO_PAIR,
        DataBrowser.AUDIO_SHOWN,
    }
    seen, source = [], DataBrowser.AUDIO_SELECTED
    for _ in range(len(DataBrowser.AUDIO_SOURCES)):
        seen.append(source)
        source = DataBrowser.AUDIO_SOURCES[
            (DataBrowser.AUDIO_SOURCES.index(source) + 1)
            % len(DataBrowser.AUDIO_SOURCES)
        ]
    assert sorted(seen) == sorted(DataBrowser.AUDIO_SOURCES)
    assert source == DataBrowser.AUDIO_SELECTED


class _Click:
    """The three things DataBrowser.mouse_clicked reads off an event."""

    def __init__(self, button, modifiers=0):
        self._button = button
        self._modifiers = modifiers

    def button(self):
        return self._button

    def modifiers(self):
        return self._modifiers

    def scenePos(self):
        return None


def _clickable(current=0, mode=DataBrowser.MODE_ZOOM):
    calls = []
    stub = SimpleNamespace(
        current_channel=current,
        cross_hair=False,
        # read before anything else: a Ctrl+click in label mode reaches for
        # an editable label instead of focusing the lane
        region_mode=mode,
        rail_clicked=lambda ch, extend: calls.append((ch, extend)),
    )
    return stub, calls


def test_clicking_a_lane_focuses_that_channel():
    """The rail card used to be the only way to select a channel."""
    from PySide6.QtCore import Qt

    stub, calls = _clickable(current=0)
    DataBrowser.mouse_clicked(stub, (_Click(Qt.LeftButton),), 5)
    assert calls == [(5, False)]


def test_shift_clicking_a_lane_extends_the_selection():
    from PySide6.QtCore import Qt

    stub, calls = _clickable(current=2)
    DataBrowser.mouse_clicked(stub, (_Click(Qt.LeftButton, Qt.ShiftModifier),), 6)
    assert calls == [(6, True)]


def test_clicking_the_current_lane_does_not_relayout():
    """rail_clicked() relays out the whole stack; a click inside the lane
    that is already current must not pay for that."""
    from PySide6.QtCore import Qt

    stub, calls = _clickable(current=5)
    DataBrowser.mouse_clicked(stub, (_Click(Qt.LeftButton),), 5)
    assert calls == []


def test_right_clicking_a_lane_does_not_change_the_channel():
    from PySide6.QtCore import Qt

    stub, calls = _clickable(current=0)
    DataBrowser.mouse_clicked(stub, (_Click(Qt.RightButton),), 5)
    assert calls == []


def test_ctrl_clicking_a_lane_in_label_mode_does_not_relayout_the_stack():
    """That press is reaching for an editable label, not for a channel.

    `rail_clicked` relays out the whole stack, and the reader is about to
    put a pointer on a 12 px grip: moving the lanes under them between the
    click and the drag would take the grip out from under the hand.  Outside
    label mode Ctrl+click still focuses, because nothing else claims it.
    """
    from PySide6.QtCore import Qt

    stub, calls = _clickable(current=0, mode=DataBrowser.MODE_LABEL)
    stub.mouse_moved = lambda evt, channel: None
    stub.select_label_at = lambda channel, pos: True
    DataBrowser.mouse_clicked(stub, (_Click(Qt.LeftButton, Qt.ControlModifier),), 5)
    assert calls == []

    stub, calls = _clickable(current=0, mode=DataBrowser.MODE_ZOOM)
    DataBrowser.mouse_clicked(stub, (_Click(Qt.LeftButton, Qt.ControlModifier),), 5)
    assert calls == [(5, False)]


def _ranged(y_mode, selected, channels=16):
    return SimpleNamespace(
        y_mode=y_mode,
        selected_channels=list(selected),
        data=SimpleNamespace(channels=channels),
    )


def test_shared_y_applies_amplitude_ops_to_every_channel():
    """Under a shared Y every lane shows the same span by definition.

    Reset (Shift+V), Center and the zoom steps went through apply_ranges(),
    which used the selection unconditionally -- so with a shared Y a drag
    moved all sixteen lanes while Shift+V reset one, because clicking a lane
    narrows the selection to it.
    """
    stub = _ranged(DataBrowser.y_shared, [5])
    assert DataBrowser.range_channels(stub) == list(range(16))


def test_per_channel_y_applies_only_to_the_selection():
    stub = _ranged(DataBrowser.y_per_channel, [5])
    assert DataBrowser.range_channels(stub) == [5]


def test_per_channel_y_honours_a_multi_selection():
    stub = _ranged(DataBrowser.y_per_channel, [2, 3, 4])
    assert DataBrowser.range_channels(stub) == [2, 3, 4]
