"""Finding events on the spectrogram with a detector trained on the recording.

Click a few events and SAM outlines them; YOLOE proposes more with no training; review
the windows the model is least sure of; train a YOLO on what was reviewed, in about a
minute; run it over the whole recording.  The design and the measurements are in
``docs/specdetector-design.md``.

`tiling`, `dataset`, `review`, `boxes` and `project` are arithmetic and bookkeeping,
importing no Qt.  `models` and `modelrunner` run in a child process and are the only
files that touch Ultralytics.  `client`, `tools` and `panel` are the half with a reader
in it.

This file is the whole interface to audian: `Plugins.bind` registers the callable
below because its name starts with ``audian_`` and ends in ``panel``.  `panel` is
imported lazily, so importing this package costs nothing until the tab is opened.
"""

__all__ = ["audian_spectrogram_detector_panel"]


def audian_spectrogram_detector_panel(browser):
    """Find events on the spectrogram with a detector trained on the recording."""
    from .panel import SpectrogramDetectorPanel

    return "Detector", SpectrogramDetectorPanel(browser)


audian_spectrogram_detector_panel.menu_path = ("Spectrogram detector",)
audian_spectrogram_detector_panel.menu_tip = (
    "Click a few events on the spectrogram, review what a model proposes, train a "
    "detector in about a minute and run it over the whole recording."
)
