"""Tests for playback channel selection and amplitude axis labelling.

Both are exercised without a QApplication: the functions under test are
either pure, or read only plain attributes off the browser, so they are
called unbound against a stand-in.
"""

from types import SimpleNamespace

from audian.databrowser import DataBrowser
from audian.timeplot import SI_UNITS, si_prefixable


def _browser(source, current, shown, channels=16):
    """The attributes DataBrowser.audio_channels actually reads."""
    return SimpleNamespace(
        audio_source=source,
        current_channel=current,
        show_channels=list(shown),
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
