"""Running wavetracker and correcting its tracks on the spectrogram.

The replacement for wavetracker's legacy EOD sorter; the design is
``docs/eodsorter-design.md``.  wavetracker itself runs out of process
(`runner`, `wtrunner`); `model` holds its arrays and every edit; `geometry`,
`overlay`, `tools` and `panel` draw and edit them.

This file is the whole interface to audian: `Plugins.bind` registers the
callable below because its name starts with ``audian_`` and ends in
``panel``.  `panel` is imported lazily, so importing this package costs
nothing until the reader opens the tab.
"""

__all__ = ["audian_wavetracker_panel"]


def audian_wavetracker_panel(browser):
    """Run wavetracker on the recording and correct its tracks."""
    from .panel import WavetrackerPanel

    return "Tracks", WavetrackerPanel(browser)


audian_wavetracker_panel.menu_path = ("Wavetracker",)
audian_wavetracker_panel.menu_tip = (
    "Track wave-type electric fish with wavetracker and correct the tracks "
    "on the spectrogram: merge, cut, erase, assign and add detections."
)
