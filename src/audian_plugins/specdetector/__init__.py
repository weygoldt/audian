"""Finding events on the spectrogram with a detector trained on the recording.

Label a few events with audian's own label tool, where SAM tightens each box and
outlines it; YOLOE proposes more with no training; review the windows the model is
least sure of; train a YOLO on what was reviewed, in about a minute; run it over the
whole recording.  The design and the measurements are in
``docs/specdetector-design.md``.

`tiling`, `dataset`, `review`, `boxes` and `project` are arithmetic and bookkeeping,
importing no Qt.  `models` and `modelrunner` run in a child process and are the only
files that touch Ultralytics.  `client`, `tools` and `panel` are the half with a reader
in it.

This file is the whole interface to audian: `Plugins.bind` registers the callables
below because their names start with ``audian_`` and end in ``panel`` and
``label_assistant``.  Both import lazily, so importing this package costs nothing.
"""

__all__ = ["audian_spectrogram_detector_panel", "audian_sam_label_assistant"]


def audian_spectrogram_detector_panel(browser):
    """Find events on the spectrogram with a detector trained on the recording."""
    from .panel import SpectrogramDetectorPanel

    return "Detector", SpectrogramDetectorPanel(browser)


def audian_sam_label_assistant(browser):
    """SAM for audian's own label tool: tightens a dragged box and outlines it.

    Costs nothing until the first drag with it on: the runner starts then.
    """
    from . import panel
    from .assistant import SamAssistant

    return SamAssistant(browser, lambda: panel.weights_directory(), parent=browser)


audian_spectrogram_detector_panel.menu_path = ("Spectrogram detector",)
audian_spectrogram_detector_panel.menu_tip = (
    "Click a few events on the spectrogram, review what a model proposes, train a "
    "detector in about a minute and run it over the whole recording."
)
