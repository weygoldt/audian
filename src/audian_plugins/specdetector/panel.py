"""The Detector tab: from a few labels to a whole recording detected.

The loop is the whole interface, and it runs top to bottom::

    Label       audian's own label mode (b): pick a category, drag a box.  With SAM
                on in the Labels tab, it tightens the box and outlines the event
    Find more   candidates, from the examples or from the last trained model
    Review      go through the windows the model is least sure of
    Train       a detector on everything reviewed, in about a minute
    Run         the whole recording, in the background

Everything but the first step can repeat, and labelling goes on while a model trains:
SAM has a runner process of its own for that reason (`assistant`).  The design and the
numbers are in ``docs/specdetector-design.md``.

What goes where
---------------

This tab has no way of labelling of its own.  A reader's labels are ordinary editable
labels, drawn with audian's label tool, in the sidecar beside the recording, outlines
included; every span category with boxes on the spectrogram is a class, unless the
reader leaves it out under **Classes**.  A machine's guesses are labels too, in a category of
their own, ``<class> (found)``, so they are drawn and edited by the code that draws
and edits everything else: there is no second renderer to disagree with the first.
But they are a loan.  `about_to_flush_labels` takes them back off before the sidecar
is written on closing, because that file is hand-authored and nothing in it should be
a guess; the candidates live in the project and come back next time.

A candidate deleted with audian's own label tools counts as rejected, and one moved
with its grips keeps the new geometry when it is accepted.  So the label store stays
the truth about what a candidate looks like now (`_sync_from_labels`).

Review shares the lanes with the label tool rather than taking them over.  In label
mode a click on a candidate rejects it, and every other click and every drag goes on
to audian, so an event the model missed is labelled the ordinary way.  Enter and
Shift+Enter finish a view only while candidates are drawn in it; otherwise they are
not this tab's keys.

Nothing here touches ``browser.data.buffer``, which is a window that moves.  The
runner opens the recording itself, from the browser's full file list.
"""

from __future__ import annotations

import csv
import os
import time
from pathlib import Path
from typing import Optional

import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMenu,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from audian.pluginapi import (
    KIND_SPAN,
    LABEL_COLUMNS,
    Label,
    LabelSet,
    ParameterGroup,
    data_path,
    narrow_combo,
    save_setting,
    settings,
    sidecar_path,
    theme,
)

from . import models as models_
from . import project as project_
from . import review, tiling
from .boxes import Box, auto_cut, iou_matrix, as_array
from .client import RunnerClient, RunnerError
from .curve import TrainingCurve
from .dataset import BLOCK_LENGTHS, Rect
from .tools import KEY_HELP, KeyRouter, LaneSurface, lane_channels

#: Appended to a class to name where a machine's guesses go.  Never the class itself:
#: a reader must always be able to tell their own marks from a model's.
FOUND_SUFFIX = " (found)"

#: Labels in this category are rectangles somebody labelled exhaustively.  The hand
#: labels of the chirp benchmark are written this way, so such a recording is ready
#: to train on as it is.
REGION_CATEGORY = "labeled_region"

#: At most this many candidates are written into the label store and drawn.  The full
#: list is kept, and a Run's CSV holds all of it.  Set by what panning can bear, since
#: the store is searched in full on each redraw.  Measured by
#: `tests/measure_specdetector_labels.py`, 1200 x 900, a 5 s view of 60 s: 5000 labels
#: rewrite in 62 ms and redraw in 54 ms; 20000 take 422 ms and 182 ms.
FOUND_LIMIT = 5000

#: How long a moved control is left alone before the labels are rewritten.
DEBOUNCE_MS = 150

#: The slider is logarithmic, because scores that matter range from 0.01 from a
#: prompted YOLOE to 0.9 from a trained model.
THRESHOLD_FLOOR = 0.01

#: This plugin's key in audian's preferences: which project each recording has joined.
SETTINGS_KEY = "specdetector"

#: A candidate this similar to a label, or to a rejection, was already answered.
ANSWERED_IOU = 0.5

#: Where a Find covers less than everything, it looks in this many windows spread evenly
#: over the recording.  Enough to review for a long while, on a recording too long to
#: sweep between clicks.  Each window can take in the tiles either side of it, so this
#: reads about three times as many tiles.
SAMPLE_WINDOWS = 100

#: How long after a rejection the candidates are written, so that a run of clicks is
#: one write: with 20000 outlined candidates, a write takes more than a second.
PERSIST_MS = 500

#: The YOLO26 sizes a detector can start from, each in a box and an outline kind.
#: Only the smallest ships with Get models' first download; the others are fetched
#: when chosen (measured sizes of the box kind: n 5.5 MB; the rest are larger).
MODEL_SIZES = (
    ("n", "fastest"),
    ("s", "small"),
    ("m", "medium"),
    ("l", "large"),
    ("x", "largest"),
)

#: How often a labelled stretch of time is held out, and what the setting says.
HOLD_OUT_CHOICES = (
    (5, "1 stretch in 5"),
    (3, "1 in 3"),
    (2, "1 in 2"),
    (0, "nothing"),
)


class ElidedToolButton(QToolButton):
    """A tool button whose text is cut to its width, with all of it in the tip.

    For the Classes button, whose text is the list of classes and can be as long as
    the reader's vocabulary.  Measured, "every category with boxes" alone asked for
    192 px and took the tab to 208, past the 204 a 220 px panel has inside its margins;
    the panel scrolls sideways then rather than widening the window, so nothing else
    would have said so.
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._full = ""
        self._tip = ""
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(theme.S24 * 2)

    def setFullText(self, text: str) -> None:  # noqa: N802 - Qt's style
        self._full = text
        self._elide()

    def fullText(self) -> str:  # noqa: N802 - Qt's style
        return self._full

    def setToolTip(self, tip: str) -> None:  # noqa: N802 - Qt's spelling
        self._tip = tip
        self._elide()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt's spelling
        super().resizeEvent(event)
        self._elide()

    def _elide(self) -> None:
        room = max(self.width() - 2 * theme.S8 - theme.S12, theme.S12)
        shown = self.fontMetrics().elidedText(
            self._full, Qt.TextElideMode.ElideRight, room
        )
        super().setText(shown)
        tip = self._tip
        if shown != self._full:
            tip = f"{self._full}\n\n{tip}" if tip else self._full
        super().setToolTip(tip)


def weights_directory() -> Path:
    """Where the model files live.  Not the cache: a field laptop with no network
    that had its cache cleared would have no way to get them back."""
    return data_path() / "specdetector-weights"


def _value_of_slider(value: int) -> float:
    return float(THRESHOLD_FLOOR * (1.0 / THRESHOLD_FLOOR) ** (value / 1000.0))


def _slider_of_value(value: float) -> int:
    value = float(np.clip(value, THRESHOLD_FLOOR, 1.0))
    return int(
        round(1000.0 * np.log(value / THRESHOLD_FLOOR) / np.log(1.0 / THRESHOLD_FLOOR))
    )


class SpectrogramDetectorPanel(QWidget):
    """The Detector tab."""

    def __init__(self, browser, parent: Optional[QWidget] = None, *, weights_dir=None):
        super().__init__(parent)
        self.browser = browser
        self.weights_dir = Path(weights_dir or weights_directory())
        self.project: Optional[project_.Project] = None
        self._closed = False

        #: candidate id -> Box.  Ids outlive a rewrite of the label store.
        self.candidates: dict = {}
        self._next_id = 0
        #: candidate id -> the Label standing for it right now
        self._label_of: dict = {}
        #: what a proposal said it is, for the message and for the next ranking
        self.candidate_source = ""
        self.threshold = 0.25
        #: candidates whose box the reader moved, which a new proposal does not replace
        self._edited: set = set()
        #: what Backspace takes back: a rejection, or a whole finished view
        self._last_action: Optional[tuple] = None

        #: one per lane, taking a click on a candidate and nothing else
        self.surfaces: list = []

        self.router = KeyRouter(self._claims_key, self.run_key)
        self.batch = RunnerClient(self.weights_dir, self)
        self.batch.sigProgress.connect(self._job_progress)
        self.batch.sigResult.connect(self._job_result)
        self.batch.sigError.connect(self._job_error)
        self.batch.sigMetrics.connect(self._epoch_ended)
        self._job_kind = ""
        self._job_id = ""
        #: the seconds a Preview was asked about
        self._preview_window = (0.0, 0.0)
        #: None chooses from the number of labels; a test sets a small number
        self.train_epochs: Optional[int] = None

        self._build()

        self._write_timer = QTimer(self)
        self._write_timer.setSingleShot(True)
        self._write_timer.setInterval(DEBOUNCE_MS)
        self._write_timer.timeout.connect(self._threshold_settled)
        self._persist_timer = QTimer(self)
        self._persist_timer.setSingleShot(True)
        self._persist_timer.setInterval(PERSIST_MS)
        self._persist_timer.timeout.connect(self._persist_candidates)

    # ------------------------------------------------------------ the interface

    def _button(self, text: str, tip: str, slot, parent=None, checkable=False):
        button = QPushButton(text, parent or self)
        button.setToolTip(tip)
        button.setCheckable(checkable)
        button.clicked.connect(slot)
        return button

    def _row(self, group, *widgets) -> QWidget:
        holder = QWidget(self)
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.S4)
        for widget in widgets:
            row.addWidget(widget, 1)
        group.add_span_row(holder)
        return holder

    def _build(self) -> None:
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(theme.S6)
        group = ParameterGroup("Detector", self, caption=False, narrow=True)
        self.group = group

        self.statusw = QLabel(
            "In label mode (b), drag a box around a few events, in the category to "
            "learn. With SAM on in the Labels tab, it outlines each one.",
            self,
        )
        self.statusw.setFont(theme.font_mono(theme.SIZE_SMALL_PT))
        theme.tint(self.statusw, "fg.muted")
        self.statusw.setWordWrap(True)
        group.add_span_row(self.statusw)
        self.summaryw = QLabel("", self)
        self.summaryw.setFont(theme.font_mono(theme.SIZE_SMALL_PT))
        theme.tint(self.summaryw, "fg.muted")
        self.summaryw.setWordWrap(True)
        group.add_span_row(self.summaryw)

        self.fetchw = self._button(
            "Get models",
            "Download the model files once, while online. About 120 MB. After "
            "that everything here works with no network.",
            self.fetch_models,
        )
        group.add_span_row(self.fetchw)

        # -- classes: audian's own label categories --
        self.classesw = ElidedToolButton(self)
        self.classesw.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.classesw.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.classesw.setToolTip(
            "What the detector learns: every label category with boxes on the "
            "spectrogram. Untick one to leave it out; its labels are then kept off "
            "the training images rather than taught as background. New categories "
            "come from the Labels tab (Ctrl+L)."
        )
        self.classes_menu = QMenu(self.classesw)
        self.classes_menu.aboutToShow.connect(self._fill_classes_menu)
        self.classesw.setMenu(self.classes_menu)
        group.add_row("Classes", "", ParameterGroup.expanding(self.classesw))

        # -- find more --
        self.scopew = narrow_combo(QComboBox(self))
        for key, name, tip in (
            ("view", "This view", "Only what is on screen: takes a moment."),
            (
                "sample",
                "A sample",
                "Tiles spread evenly over the recording: enough to review.",
            ),
            ("all", "Whole recording", "Every tile."),
        ):
            self.scopew.addItem(name, key)
            self.scopew.setItemData(
                self.scopew.count() - 1, tip, Qt.ItemDataRole.ToolTipRole
            )
        self.scopew.setToolTip("Where to look")
        self.findw = self._button(
            "Find more",
            "Propose more events: from the last trained model if there is one, "
            "otherwise from your labels, with no training.",
            self.find_more,
        )
        self.previeww = self._button(
            "Preview",
            "Run the trained model on what is on screen, and nothing else: a quick "
            "look at how it does here. Its answers replace the candidates in view, "
            "and can be reviewed as any others. Needs a trained model.",
            self.preview_view,
        )
        group.add_row("Look in", "", ParameterGroup.expanding(self.scopew))
        self._row(group, self.findw, self.previeww)

        self.threshw = QSlider(Qt.Orientation.Horizontal, self)
        self.threshw.setRange(0, 1000)
        self.threshw.setValue(_slider_of_value(self.threshold))
        self.threshw.setToolTip(
            "Only candidates scoring above this are shown. Moving it runs nothing: "
            "the model already answered."
        )
        self.threshw.valueChanged.connect(self._threshold_moved)
        self.threshlabel = QLabel(f"{self.threshold:.2f}", self)
        self.threshlabel.setFont(theme.font_mono(theme.SIZE_SMALL_PT))
        group.add_row(
            "Cut", "", ParameterGroup.expanding(self.threshw), self.threshlabel
        )

        # -- review --
        keys = "\n".join(f"{key}: {meaning}" for key, meaning in KEY_HELP)
        self.nextw = self._button(
            "Next window",
            "Go to the window whose answer would teach the model most, in label "
            "mode.\n" + keys,
            self.next_window,
        )
        self.donew = self._button(
            "View done",
            "Everything in view is labelled. Accepts the candidates still in it, "
            "marks the view as reviewed, and goes to the next window. Enter, while "
            "candidates are drawn in view.",
            lambda: self.accept_view(),
        )
        self._row(group, self.nextw, self.donew)
        self.rejectw = self._button(
            "Reject view",
            "Nothing still proposed in view is an event. Marks the view as "
            "reviewed and goes on. Shift+Enter, while candidates are drawn in view.",
            lambda: self.reject_view(),
        )
        group.add_span_row(self.rejectw)

        # -- train --
        self.trainonw = narrow_combo(QComboBox(self))
        self.trainonw.addItem("Boxes", False)
        self.trainonw.addItem("Outlines", True)
        self.trainonw.setToolTip(
            "What the detector learns to draw. Outlines take a little longer to "
            "train, and a box with no outline is used as its rectangle."
        )
        self.trainonw.activated.connect(self._train_on_picked)
        group.add_row("Learn", "", ParameterGroup.expanding(self.trainonw))
        self._build_training_settings(group)
        self.trainw = self._button(
            "Train",
            "Train a detector on your labels. Only what you have looked at reaches "
            "it: everything else on an image is masked, so an event you have not "
            "labelled is never taught as background.",
            self.train,
        )
        self.runw = self._button(
            "Run",
            "Detect over the whole recording, with the last trained model.",
            self.run_detection,
        )
        self._row(group, self.trainw, self.runw)
        self.curvew = TrainingCurve(self)
        self.curvew.setVisible(False)
        group.add_span_row(self.curvew)
        self.exportw = self._button(
            "Export CSV",
            "Write what is above the cut beside the recording, as a label CSV.",
            self.export_csv,
        )
        self.clearw = self._button(
            "Clear",
            "Forget the current candidates. Your labels and the review stay.",
            self.clear_candidates,
        )
        self._row(group, self.exportw, self.clearw)
        self.joinw = self._button(
            "Add to a project",
            "Make this recording part of another recording's project, so that Train "
            "learns from both. A detector generalises from the recordings it has seen.",
            self._join_clicked,
        )
        group.add_span_row(self.joinw)

        self.progressw = QProgressBar(self)
        self.progressw.setRange(0, 100)
        self.progressw.setTextVisible(True)
        self.progressw.hide()
        group.add_span_row(self.progressw)
        self.stopw = self._button("Stop", "Stop what is running", self.stop_job)
        self.stopw.hide()
        group.add_span_row(self.stopw)

        box.addWidget(group)
        box.addStretch(1)
        self._sync_controls()

    # ------------------------------------------------------------ training settings

    def _build_training_settings(self, group) -> None:
        """How a model is trained, folded away under one button until asked for."""
        self.settingsw = QToolButton(self)
        self.settingsw.setText("Training settings")
        self.settingsw.setCheckable(True)
        self.settingsw.setArrowType(Qt.ArrowType.RightArrow)
        self.settingsw.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.settingsw.setToolTip(
            "The model, how long it trains, when it stops, and what is held out to "
            "judge it by."
        )
        self.settingsw.toggled.connect(self._settings_toggled)
        group.add_span_row(self.settingsw)

        # Rows of the tab's own group, shown and hidden together.  A group nested
        # inside it cost a second frame and its margins: measured, the tab went to
        # 210 px with them open, past the 204 the panel has.
        self.settings_rows = []
        box = self
        self.modelw = narrow_combo(QComboBox(box))
        for size, name in MODEL_SIZES:
            self.modelw.addItem(f"YOLO26{size} ({name})", size)
        self.modelw.setToolTip(
            "Larger models learn more and train slower. Any but the smallest is "
            "downloaded the first time, with Get models."
        )
        self.settings_rows += group.add_row(
            "Model", "", ParameterGroup.expanding(self.modelw)
        )
        self.startw = narrow_combo(QComboBox(box))
        self.startw.addItem("Pretrained", "pretrained")
        self.startw.addItem("Last model", "last")
        self.startw.setToolTip(
            "Pretrained starts from YOLO's own weights. Last model goes on from the "
            "model this project trained last, to fine-tune it on what was labelled "
            "since; it keeps that model's size and kind."
        )
        self.settings_rows += group.add_row(
            "Start", "", ParameterGroup.expanding(self.startw)
        )
        self.epochsw = QSpinBox(box)
        self.epochsw.setRange(0, 2000)
        self.epochsw.setSpecialValueText("Auto")
        self.epochsw.setToolTip(
            "How many passes over the training tiles. Auto trains for a fixed "
            f"number of batches ({models_.TRAIN_STEPS}), whatever the number "
            "of labels."
        )
        self.settings_rows += group.add_row(
            "Epochs", "", ParameterGroup.expanding(self.epochsw)
        )
        self.stopearlyw = QCheckBox("Stop early", box)
        self.patiencew = QSpinBox(box)
        self.patiencew.setRange(0, 1000)
        self.patiencew.setSpecialValueText("Auto")
        self.patiencew.setToolTip(
            "Stop after this many epochs without a better score on the held-out "
            "tiles. Auto is a third of the epochs, and at least 10. Needs something "
            "held out: with nothing to watch, training runs to the end."
        )
        self.stopearlyw.setToolTip(self.patiencew.toolTip())
        self.settings_rows += group.add_row(
            "Patience", "", self.stopearlyw, ParameterGroup.expanding(self.patiencew)
        )
        self.batchw = QSpinBox(box)
        self.batchw.setRange(0, 256)
        self.batchw.setSpecialValueText("Auto")
        self.batchw.setToolTip(
            "Tiles per batch. Auto is 16 on a GPU and 4 on the CPU; a "
            "larger model may need less to fit on the card."
        )
        self.settings_rows += group.add_row(
            "Batch", "", ParameterGroup.expanding(self.batchw)
        )
        self.holdw = narrow_combo(QComboBox(box))
        for every, name in HOLD_OUT_CHOICES:
            self.holdw.addItem(name, every)
        self.holdw.setToolTip(
            "Labelled stretches of time set aside to judge the model by, and for "
            "early stopping to watch. A stretch is everything within four tiles' "
            "length of other labels, so labels in one view are one stretch: label "
            "in several places to have something to hold out."
        )
        self.settings_rows += group.add_row(
            "Hold out", "", ParameterGroup.expanding(self.holdw)
        )
        for widget in (self.modelw, self.startw, self.holdw):
            widget.activated.connect(self._settings_changed)
        for widget in (self.epochsw, self.patiencew, self.batchw):
            widget.valueChanged.connect(self._settings_changed)
        self.stopearlyw.toggled.connect(self._settings_changed)
        for widget in self.settings_rows:
            widget.setVisible(False)

    def _settings_toggled(self, on: bool) -> None:
        self.settingsw.setArrowType(
            Qt.ArrowType.DownArrow if on else Qt.ArrowType.RightArrow
        )
        for widget in self.settings_rows:
            widget.setVisible(on)

    def training_settings(self) -> dict:
        """What the settings say, in the project's terms."""
        return {
            "size": self.modelw.currentData() or "n",
            "start": self.startw.currentData() or "pretrained",
            "epochs": int(self.epochsw.value()),
            "stop_early": bool(self.stopearlyw.isChecked()),
            "patience": int(self.patiencew.value()),
            "batch": int(self.batchw.value()),
            "hold_out_every": int(self.holdw.currentData() or 0),
        }

    def _show_training_settings(self, settings: dict) -> None:
        widgets = (
            self.modelw,
            self.startw,
            self.epochsw,
            self.stopearlyw,
            self.patiencew,
            self.batchw,
            self.holdw,
        )
        blocked = [w.blockSignals(True) for w in widgets]
        try:
            for combo, key in (
                (self.modelw, "size"),
                (self.startw, "start"),
                (self.holdw, "hold_out_every"),
            ):
                index = combo.findData(settings[key])
                combo.setCurrentIndex(max(index, 0))
            self.epochsw.setValue(settings["epochs"])
            self.stopearlyw.setChecked(settings["stop_early"])
            self.patiencew.setValue(settings["patience"])
            self.patiencew.setEnabled(settings["stop_early"])
            self.batchw.setValue(settings["batch"])
        finally:
            for widget, was in zip(widgets, blocked):
                widget.blockSignals(was)

    def _settings_changed(self, *_args) -> None:
        self.patiencew.setEnabled(self.stopearlyw.isChecked())
        project = self._ensure_project()
        if project is None:
            return
        project.training = self.training_settings()
        self._save_project()
        self._sync_controls()

    def _base_weights(self, settings: dict) -> str:
        """The pretrained weights a new model starts from, by file name."""
        return f"yolo26{settings['size']}{'-seg' if self._segment() else ''}.pt"

    def _segment(self) -> bool:
        return bool(self.trainonw.currentData())

    # ------------------------------------------------------------ small things

    def _say(self, message: str) -> None:
        self.statusw.setText(message)

    def _notify(self, level: str, message: str) -> None:
        notify = getattr(self.browser, "notify", None)
        if notify is not None:
            notify(level, message)

    def _label_mode(self) -> bool:
        mode = getattr(type(self.browser), "MODE_LABEL", None)
        return mode is not None and getattr(self.browser, "region_mode", None) == mode

    def _reviewing(self) -> bool:
        """Whether review has the lanes' clicks and keys: in label mode, in this
        browser's active window, with this tab still open."""
        return (
            bool(self.surfaces)
            and not self._closed
            and self._label_mode()
            and bool(self.browser.isVisible())
            and QApplication.activeWindow() is self.browser.window()
        )

    def _claims_key(self, name: str) -> bool:
        """Only the keys review can act on now; everything else stays audian's.

        Backspace zooms back everywhere else in audian, so it is taken only while
        there is a review step to take back.
        """
        if not self._reviewing():
            return False
        if name == "backspace":
            return self._last_action is not None
        return self._drawn_in_view()

    def _paths(self) -> list:
        """The complete ordered file list behind the browser's timeline.

        Absolute, since the runner works from its weights directory: ``audian rec.wav``
        gave it a name it could not find, and every job failed.
        """
        data = getattr(self.browser, "data", None)
        loader = getattr(data, "data", None)
        opened = getattr(loader, "file_paths", None)
        if opened is not None:
            paths = [os.path.abspath(os.fspath(path)) for path in opened]
            if paths:
                return paths
        fallback = getattr(data, "file_path", None)
        if fallback is None:
            return []
        if isinstance(fallback, (list, tuple, np.ndarray)):
            return [os.path.abspath(os.fspath(path)) for path in fallback]
        return [os.path.abspath(os.fspath(fallback))]

    def _rate(self) -> float:
        return float(self.browser.data.rate)

    def _duration(self) -> float:
        return len(self.browser.data.data) / self._rate()

    def _channels(self) -> int:
        return int(self.browser.data.channels)

    def _lanes(self) -> list:
        return list(self.browser.spectrogram_axes())

    def _shown_lanes(self) -> list:
        """The lanes on screen.  A hidden channel's lane still exists, and still reports
        itself visible (a plot item does not learn that its widget was hidden)."""
        lanes = self._lanes()
        visible = getattr(self.browser, "visible_channels", None)
        if visible is None:
            return lanes
        wanted = set(visible())
        return [ax for ax in lanes if getattr(ax, "channel", None) in wanted] or lanes

    def _hidden_channels(self) -> list:
        """Channels the images are made from, and that nobody can see right now.

        A training image is the reduction of the channels its config names, whichever
        lanes were on screen.  So an event on a hidden channel in a view marked as done
        reached the model unmasked and unboxed: brightness 255 over a floor of 36, with
        no label on it, which is the one thing this plugin is built never to do.
        """
        config = self.project.config if self.project is not None else None
        reduction = None if config is None else tiling.parse_reduction(config.reduction)
        if isinstance(reduction, int):
            needed = {reduction}
        else:
            needed = set(range(self._channels()))
        shown: set = set()
        for ax in self._shown_lanes():
            channels = lane_channels(ax)
            shown |= {channels} if isinstance(channels, int) else set(channels)
        return sorted(needed - shown)

    def _project_directory(self, first_path) -> Path:
        """Where this recording's project is: one it joined, or its own beside it."""
        stored = settings().get(SETTINGS_KEY)
        joined = stored.get("projects", {}) if isinstance(stored, dict) else {}
        directory = (
            joined.get(os.fspath(first_path)) if isinstance(joined, dict) else None
        )
        if directory and (Path(directory) / project_.PROJECT_FILE).exists():
            return Path(directory)
        return project_.default_directory(first_path)

    def join_project(self, directory) -> bool:
        """Make this recording one of another project's, and train with it.

        A detector generalises from what it has seen, and one recording is not much:
        models trained on 90 labels from one scored F1 0.70 on others, 0.73 on 131 to 314
        labels from three, and the old chirp detector, trained on 17 recordings, 0.895.
        So a project can hold several.  The labels stay in each recording's own sidecar,
        and the review is kept per recording; what they share is the image settings and
        the models.
        """
        paths = self._paths()
        if not paths:
            return False
        directory = Path(directory)
        try:
            project = project_.Project.open(directory)
        except (OSError, ValueError) as exc:
            self._say(f"cannot read {directory}: {exc}")
            return False
        self._clear_found()
        self.candidates = {}
        self._edited = set()
        self._last_action = None
        self.project = project
        project.add_recording(paths)
        self.threshold = project.threshold
        self._write_threshold()
        self._save_project()
        stored = settings().get(SETTINGS_KEY)
        stored = dict(stored) if isinstance(stored, dict) else {}
        joined = stored.get("projects")
        joined = dict(joined) if isinstance(joined, dict) else {}
        joined[paths[0]] = os.fspath(directory.resolve())
        stored["projects"] = joined
        save_setting(SETTINGS_KEY, stored)
        boxes, meta = project.load_candidates(paths)
        if boxes:
            self._set_candidates(boxes, str(meta.get("source", "")))
            self._write_found()
        self._update_summary()
        self._sync_controls()
        self._say(
            f"This recording is now one of {len(project.recordings)} in this project. "
            "Train learns from all of them."
        )
        return True

    def _join_clicked(self, *_args) -> None:
        directory = QFileDialog.getExistingDirectory(
            self,
            "Choose the project of another recording",
            os.path.dirname(self._paths()[0]),
        )
        if directory:
            self.join_project(directory)

    def _ensure_project(self) -> Optional[project_.Project]:
        """The project of this recording, created on first need."""
        if self.project is not None:
            return self.project
        paths = self._paths()
        if not paths:
            self._say("Open a recording first.")
            return None
        directory = self._project_directory(paths[0])
        try:
            self.project = project_.Project.open(directory)
        except (OSError, ValueError) as exc:
            # A project that cannot be read is not an empty one: starting fresh would
            # overwrite somebody's review at the next save.
            self._say(f"cannot read {directory}: {exc}")
            self._notify("error", f"cannot read the detector project: {exc}")
            return None
        self.project.add_recording(paths)
        self.threshold = self.project.threshold
        self._write_threshold()
        self._show_training_settings(self.project.training_settings())
        self._show_model_curve()
        return self.project

    def _save_project(self) -> None:
        if self.project is None:
            return
        try:
            self.project.save()
        except OSError as exc:
            self._notify("error", f"could not save the detector project: {exc}")

    def _missing_weights(self) -> list:
        """What Get models would fetch: the usual files and the size chosen to train."""
        wanted = list(models_.ALL_WEIGHTS)
        if hasattr(self, "modelw"):
            settings = self.training_settings()
            if settings["start"] == "pretrained":
                base = self._base_weights(settings)
                if base not in wanted:
                    wanted.append(base)
        return [name for name in wanted if not (self.weights_dir / name).is_file()]

    def _sync_controls(self) -> None:
        running = self.batch.busy() if hasattr(self, "batch") else False
        has_model = self.project is not None and self.project.model is not None
        self.fetchw.setVisible(bool(self._missing_weights()))
        for widget in (self.findw, self.trainw, self.fetchw):
            widget.setEnabled(not running)
        self.runw.setEnabled(has_model and not running)
        self.previeww.setEnabled(has_model and not running)
        self.exportw.setEnabled(bool(self.candidates))
        self.clearw.setEnabled(bool(self.candidates))
        self.nextw.setEnabled(bool(self.candidates))
        self.rejectw.setEnabled(bool(self.candidates))
        self.progressw.setVisible(running)
        self.stopw.setVisible(running)

    # ------------------------------------------------------------ classes

    @staticmethod
    def learnable(labels: LabelSet, name: str) -> bool:
        """Whether category `name` can be a class at all: a span of the reader's own.

        Not a point, which has no box; not a machine's guesses; not the rectangles a
        reader marks as labelled throughout, which say where and not what.
        """
        category = labels.category(name)
        return (
            category is not None
            and category.kind == KIND_SPAN
            and not name.endswith(FOUND_SUFFIX)
            and name != REGION_CATEGORY
        )

    def _left_out(self) -> set:
        return set(self.project.left_out) if self.project is not None else set()

    def _label_sets(self) -> list:
        """The label store of every recording of the project, this one's live."""
        project = self.project
        here = project.recording_key(self._paths())
        sets = [self.browser.labels]
        for paths in project.recording_paths():
            if project.recording_key(paths) == here:
                continue
            labels = LabelSet()
            labels.read(sidecar_path(paths[0]))
            sets.append(labels)
        return sets

    def sync_classes(self, label_sets=None) -> list:
        """Make every category with boxes a class, and return the classes learnt now.

        Classes are appended in the order they are met, never reordered: a trained
        model's class indices are positions in that list.  A category left out keeps
        its place, and only stops being taught.
        """
        project = self._ensure_project()
        if project is None:
            return []
        left_out = self._left_out()
        for labels in label_sets if label_sets is not None else self._label_sets():
            for label in labels:
                if (
                    label.has_frequency()
                    and not label.is_point()
                    and label.category not in left_out
                    and self.learnable(labels, label.category)
                ):
                    project.class_index(label.category)
        # The button says what is learnt, and this is where that changes.
        self.refresh_classes()
        return [name for name in project.names if name not in left_out]

    def current_class(self) -> str:
        """The browser's current category, when it is a class Find can look for."""
        current = getattr(self.browser, "current_category", "") or ""
        if (
            current
            and self.learnable(self.browser.labels, current)
            and current not in self._left_out()
        ):
            return current
        return ""

    def refresh_classes(self) -> None:
        """Name what is learnt on the Classes button."""
        project = self.project
        if project is None:
            self.classesw.setFullText("every category with boxes")
            return
        learnt = [name for name in project.names if name not in self._left_out()]
        text = ", ".join(learnt) if learnt else "none yet: label some events"
        if project.left_out:
            text += f" (not {', '.join(project.left_out)})"
        self.classesw.setFullText(text)

    def _fill_classes_menu(self) -> None:
        """One tick per category that could be a class."""
        self.classes_menu.clear()
        labels = self.browser.labels
        left_out = self._left_out()
        names = [
            c.name for c in labels.lasting_categories if self.learnable(labels, c.name)
        ]
        if self.project is not None:
            names += [n for n in self.project.names if n not in names]
        for name in names:
            action = self.classes_menu.addAction(name)
            action.setCheckable(True)
            action.setChecked(name not in left_out)
            action.toggled.connect(
                lambda on, name=name: self.set_class_learnt(name, on)
            )

    def set_class_learnt(self, name: str, on: bool) -> None:
        """Teach category `name`, or leave it out."""
        project = self._ensure_project()
        if project is None:
            return
        left_out = [n for n in project.left_out if n != name]
        if not on:
            left_out.append(name)
        project.left_out = left_out
        self._save_project()
        self.refresh_classes()
        self._update_summary()

    def _train_on_picked(self, *_args) -> None:
        project = self._ensure_project()
        if project is not None:
            project.segment = bool(self.trainonw.currentData())
            self._save_project()

    # ------------------------------------------------------------ lanes

    def attach(self) -> None:
        """Put a review surface on every lane, once, as lanes come and go."""
        lanes = self._lanes()
        known = {surface.ax for surface in self.surfaces}
        if known == set(lanes) and (self.surfaces or not lanes):
            return
        self.detach()
        for ax in lanes:
            self.surfaces.append(LaneSurface(ax, self))
        if self.surfaces:
            self.router.install()

    def detach(self) -> None:
        for surface in self.surfaces:
            surface.detach()
        self.surfaces = []
        self.router.uninstall()

    def showEvent(self, event):  # noqa: N802 - Qt's spelling
        super().showEvent(event)
        if self._closed:
            return
        self.attach()
        self._restore()
        self.refresh_classes()
        self._sync_controls()

    def _restore(self) -> None:
        """Pick up a project and its queue left from another session."""
        if self.project is not None:
            return
        paths = self._paths()
        if not paths:
            return
        directory = self._project_directory(paths[0])
        if not (directory / project_.PROJECT_FILE).exists():
            return
        project = self._ensure_project()
        if project is None:
            return
        self.trainonw.setCurrentIndex(1 if project.segment else 0)
        self._adopt_masks()
        boxes, meta = project.load_candidates(paths)
        if boxes:
            self._set_candidates(boxes, str(meta.get("source", "")))
            self._write_found()
        self._update_summary()

    def _adopt_masks(self) -> None:
        """Move outlines kept in the project onto the labels they belong to.

        Before labels could carry an outline, this tab kept SAM's in the project,
        keyed by the box.  The labels of this recording take theirs back, so that
        audian draws them and the sidecar keeps them; a recording not open keeps its
        own in the project until it is, and `labelled_boxes` still finds them there.
        """
        project = self.project
        if not project.masks:
            return
        labels = self.browser.labels
        adopted = []
        for label in list(labels):
            if label.outline or not label.has_frequency() or label.is_point():
                continue
            key = project_.mask_key(
                label.category, label.t0, label.t1, label.f0, label.f1
            )
            polygon = project.masks.get(key)
            if polygon and labels.refine(
                label, label.t0, label.t1, label.f0, label.f1, polygon
            ):
                adopted.append(key)
        if adopted:
            for key in adopted:
                del project.masks[key]
            self._save_project()
            self._labels_changed(save=True)

    # ------------------------------------------------------------ review clicks

    def _view_of(self, ax) -> tuple:
        """What a lane shows, inside the recording."""
        (t0, t1), (f0, f1) = ax.getViewBox().viewRange()
        duration, nyquist = self._duration(), self._rate() / 2
        t0, t1 = max(t0, 0.0), min(t1, duration)
        f0, f1 = max(f0, 0.0), min(f1, nyquist)
        return float(t0), float(t1), float(f0), float(f1)

    def claims_click(self, ax, t: float, f: float) -> bool:
        """Whether a plain click here is review's: one on a drawn candidate, in label
        mode.  Every other click, and every drag, goes on to audian."""
        return self._reviewing() and self._candidate_at(t, f, ax) is not None

    def candidate_clicked(self, ax, t: float, f: float) -> bool:
        """Reject the candidate under a click.  False when there is none."""
        if not self._reviewing():
            return False
        uid = self._candidate_at(t, f, ax)
        if uid is None:
            return False
        self.reject_candidate(uid)
        return True

    def _labels_changed(self, save: bool = False) -> None:
        # The label table addresses rows by position, so one left open over a rewritten
        # store removes whatever is now at that position.
        for call in (
            "revalidate_selection",
            "redraw_labels",
            "update_label_status",
            "refresh_label_table",
        ):
            fn = getattr(self.browser, call, None)
            if fn is not None:
                fn()
        if save:
            schedule = getattr(self.browser, "schedule_label_save", None)
            if schedule is not None:
                schedule()

    def run_key(self, name: str) -> None:
        """A key `_claims_key` gave to review."""
        if name == "enter":
            self.accept_view()
        elif name == "shift_enter":
            self.reject_view()
        elif name == "backspace":
            self.undo_last()

    def _drawn_in_view(self) -> bool:
        rect = self._current_rect()
        if rect is None:
            return False
        return any(
            uid in self.candidates and self._in_rect(self.candidates[uid], rect)
            for uid in self._label_of
        )

    # ------------------------------------------------------------ what is labelled

    def labelled_boxes(self, labels=None) -> tuple:
        """The reader's boxes of the project's classes: ``(boxes, regions, unusable)``.

        A label with no band, a point, is not a box and cannot be trained on as one.
        They are counted, for the reader to be told, and never given an invented band.
        A category left out is not a class here, and its labels are hidden instead
        (`_unusable_spans`).

        The outline is the label's own.  One kept in the project from before labels
        could carry it is still found there, for a recording not yet opened since.
        """
        labels = self.browser.labels if labels is None else labels
        project = self.project
        left_out = self._left_out()
        boxes, regions, unusable = [], [], 0
        for label in labels:
            if label.category == REGION_CATEGORY:
                if not label.is_point():
                    regions.append(Rect(label.t0, label.t1, label.f0, label.f1))
                continue
            if label.category.endswith(FOUND_SUFFIX):
                continue
            if label.category not in project.names or label.category in left_out:
                continue
            if label.is_point() or not label.has_frequency():
                unusable += 1
                continue
            polygon = label.outline or project.masks.get(
                project_.mask_key(
                    label.category, label.t0, label.t1, label.f0, label.f1
                )
            )
            boxes.append(
                Box(
                    label.t0,
                    label.t1,
                    label.f0,
                    label.f1,
                    project.names.index(label.category),
                    1.0,
                    polygon=None if not polygon else tuple(map(tuple, polygon)),
                    channel=label.channel,
                )
            )
        return boxes, regions, unusable

    def _unusable_spans(self, config, labels=None) -> list:
        """Where a label says there is an event that cannot be trained on, as rectangles.

        It marks an event all the same.  Left on the image it is an unboxed event, which
        is to say a lesson that events are background, so it is kept off the images.
        It has no band, so that means every frequency for as long as it lasts, and a
        point is given a little time either side.

        A label of a category the reader left out is the same lesson: they did not say
        it is not an event, only that this detector is not to learn it.  It is hidden
        by its own box.
        """
        labels = self.browser.labels if labels is None else labels
        half = config.tile_s / 32.0
        left_out = self._left_out()
        spans = []
        for label in labels:
            if label.category in left_out and self.learnable(labels, label.category):
                if label.has_frequency() and not label.is_point():
                    spans.append(Rect(label.t0, label.t1, label.f0, label.f1))
                    continue
            elif label.category not in self.project.names:
                continue
            if label.is_point():
                spans.append(Rect(label.t0 - half, label.t0 + half))
            elif not label.has_frequency():
                spans.append(Rect(label.t0, label.t1))
        return spans

    def _shots_of_class(self) -> list:
        """The boxes Find learns from: every class that has some, in this recording."""
        project = self._ensure_project()
        if project is None:
            return []
        self.sync_classes([self.browser.labels])
        return self.labelled_boxes()[0]

    def _config_for(self, boxes) -> Optional[tiling.SpecConfig]:
        """The project's image settings, suggested from `boxes` the first time."""
        project = self.project
        if project.config is None:
            if not boxes:
                return None
            project.config = tiling.suggest_config(
                [(b.t0, b.t1, b.f0, b.f1) for b in boxes],
                self._rate(),
                channels=self._channels(),
            )
            self._save_project()
        return project.config

    # ------------------------------------------------------------ jobs

    def _start(self, kind: str, op: str, **request) -> bool:
        """Hand a job to the batch runner."""
        try:
            self._job_id = self.batch.submit(op, **request)
        except RunnerError as exc:
            self._say(str(exc))
            return False
        self._job_kind = kind
        self.progressw.setValue(0)
        self.progressw.setFormat("starting")
        self._sync_controls()
        return True

    def stop_job(self, *_args) -> None:
        # In this order: the cancel can report back before it returns, and that answer
        # must not be overwritten by the announcement of it.
        self._say("Stopping...")
        self.batch.cancel()

    def _job_progress(self, _job, stage, done, total, text) -> None:
        if total > 0:
            self.progressw.setRange(0, 100)
            self.progressw.setValue(int(round(100.0 * done / total)))
        else:
            self.progressw.setRange(0, 0)
        words = {
            "train": f"epoch {done}/{total}",
            "tiles": f"tiles {done}/{total}",
            "sweep": f"tiles {done}/{total}",
            "examples": f"examples {done}/{total}",
            "download": text or "downloading",
        }.get(stage, stage)
        self.progressw.setFormat(
            f"{words} {text}".strip() if stage == "train" else words
        )

    def _job_result(self, _job: str, message: dict) -> None:
        kind, self._job_kind = self._job_kind, ""
        handler = {
            "fetch": self._fetch_done,
            "find": self._find_done,
            "train": self._train_done,
            "run": self._run_done,
            "preview": self._preview_done,
        }.get(kind)
        if handler is not None:
            handler(message)
        self._sync_controls()

    def _job_error(self, _job: str, kind: str, text: str) -> None:
        self._job_kind = ""
        if kind == "cancelled":
            self._say("Stopped.")
        else:
            self._say(f"Failed: {text}")
            self._notify("error", f"detector: {text}")
        self._sync_controls()

    # -- get models --

    def fetch_models(self, *_args) -> None:
        self._say("Downloading the models...")
        self._start("fetch", "fetch", names=self._missing_weights())

    def _fetch_done(self, _message: dict) -> None:
        self._say("Models are here. This machine no longer needs a network for this.")

    # -- find more --

    def _windows_for(self, scope: str, config) -> Optional[list]:
        if scope == "all":
            return None
        if scope == "view":
            lanes = self._shown_lanes()
            if not lanes:
                return None
            t0, t1, _f0, _f1 = self._view_of(lanes[0])
            return [[t0, t1]]
        grid = tiling.grid_of(config, self._rate())
        starts = tiling.tile_starts(self._duration(), grid)
        if len(starts) <= SAMPLE_WINDOWS:
            return None
        chosen = np.linspace(0, len(starts) - 1, SAMPLE_WINDOWS).astype(int)
        return [
            [starts[i] / grid.rate, (starts[i] + grid.frames * grid.hop) / grid.rate]
            for i in chosen
        ]

    def find_more(self, *_args) -> None:
        project = self._ensure_project()
        if project is None:
            return
        shots = self._shots_of_class()
        model = project.model_path()
        scope = self.scopew.currentData()
        if model:
            config = self._model_config() or project.config
            windows = self._windows_for(scope, config)
            self._say("Looking with the trained model...")
            self._start(
                "find", "propose", paths=self._paths(), model=model, windows=windows
            )
            return
        if not shots:
            self._say("Label an example first: Find more learns from your labels.")
            return
        config = self._config_for(shots)
        windows = self._windows_for(scope, config)
        self._say(f"Looking for more like your {len(shots)} labels...")
        self._start(
            "find",
            "propose",
            paths=self._paths(),
            config=config.as_dict(),
            # the classes prompted must be classes of the project
            shots=[dict(t0=b.t0, t1=b.t1, f0=b.f0, f1=b.f1, cls=b.cls) for b in shots],
            windows=windows,
            conf=THRESHOLD_FLOOR,
        )

    def _model_config(self) -> Optional[tiling.SpecConfig]:
        path = self.project.model_path() if self.project is not None else None
        if not path:
            return None
        try:
            return tiling.SpecConfig.from_dict(models_.read_meta(path)["config"])
        except (ValueError, KeyError, TypeError):
            return None

    def _find_done(self, message: dict) -> None:
        boxes = [project_.box_from_json(v) for v in message.get("boxes", [])]
        source = str(message.get("source", ""))
        kept = self._set_candidates(boxes, source)
        if source == "examples":
            self.threshold = auto_cut([b.score for b in kept], self.threshold)
        else:
            self.threshold = self._model_threshold() or self.threshold
        self._write_threshold()
        self._write_found()
        self._persist_candidates()
        self.project.threshold = self.threshold
        self._save_project()
        shown = len(self._label_of)
        cpu = (
            " (GPU was full: ran on the CPU.)"
            if message.get("fell_back_to_cpu")
            else ""
        )
        self._say(
            f"{shown} candidates above the cut, of {len(self.candidates)}. "
            f"Next window goes to the one worth looking at first.{cpu}"
        )

    def _model_threshold(self) -> Optional[float]:
        path = self.project.model_path() if self.project is not None else None
        if not path:
            return None
        try:
            return float(models_.read_meta(path)["threshold"])
        except (ValueError, KeyError, TypeError):
            return None

    def _set_candidates(self, boxes, source: str) -> list:
        """Replace the queue, without what has been answered already.

        A candidate the reader moved stays as they left it, and one they deleted counts
        as rejected, so the store has to be read before its candidates are forgotten.
        """
        self._sync_from_labels()
        edited = {
            uid: self.candidates[uid] for uid in self._edited if uid in self.candidates
        }
        kept = self._unanswered(boxes, edited)
        self.candidates = dict(edited)
        self._edited = set(edited)
        self._label_of = {}
        self._last_action = None
        for box in kept:
            self.candidates[self._next_id] = box
            self._next_id += 1
        self.candidate_source = source
        return kept

    def _unanswered(self, boxes, edited: dict) -> list:
        """`boxes` without what the reader has already answered.

        Inside a reviewed view, the same as a label, a rejection, or a candidate the
        reader moved: each of those is an answer, and proposing it again asks the
        same question twice.
        """
        reviewed = self._reviewed_here()
        labelled, _regions, _unusable = self.labelled_boxes()
        answered = as_array(
            labelled
            + self.project.rejected_boxes(self._paths())
            + list(edited.values())
        )
        kept = []
        for box in boxes:
            if review.is_reviewed(box, reviewed):
                continue
            if len(answered):
                overlap = iou_matrix(as_array([box]), answered)[0]
                if overlap.max() > ANSWERED_IOU:
                    continue
            kept.append(box)
        return kept

    # -- preview --

    def preview_view(self, *_args) -> None:
        """Run the trained model on what is on screen, and on nothing else."""
        project = self._ensure_project()
        if project is None or not project.model_path():
            self._say("Train a model first: Preview shows what it finds on screen.")
            return
        lanes = self._shown_lanes()
        if not lanes:
            return
        t0, t1, _f0, _f1 = self._view_of(lanes[0])
        if t1 <= t0:
            return
        self._preview_window = (t0, t1)
        self._say("Looking on screen with the trained model...")
        self._start(
            "preview",
            "propose",
            paths=self._paths(),
            model=project.model_path(),
            windows=[[t0, t1]],
        )

    def _preview_done(self, message: dict) -> None:
        """The model's answers replace the candidates on screen; the rest stay."""
        t0, t1 = self._preview_window
        found = [project_.box_from_json(v) for v in message.get("boxes", [])]
        inside = [b for b in found if t0 <= b.centre_t <= t1]
        self._sync_from_labels()
        edited = {
            uid: box for uid, box in self.candidates.items() if uid in self._edited
        }
        kept = {
            uid: box
            for uid, box in self.candidates.items()
            if uid in edited or not (t0 <= box.centre_t <= t1)
        }
        new = self._unanswered(inside, edited)
        self.candidates = kept
        self._last_action = None
        for box in new:
            self.candidates[self._next_id] = box
            self._next_id += 1
        if self.candidate_source in ("", "examples"):
            self.candidate_source = "model"
        self._write_found()
        self._persist_candidates()
        above = sum(1 for b in new if b.score >= self.threshold)
        self._say(
            f"Preview: {above} events above the cut on screen, of {len(new)} found. "
            "Move Cut to see more or fewer; click what is wrong and Enter, as in review."
        )

    def _reviewed_here(self) -> list:
        paths = self._paths()
        rects = self.project.reviewed_rects(paths) if self.project is not None else []
        return rects + self.labelled_boxes()[1] if self.project is not None else rects

    def _persist_candidates(self) -> None:
        if self.project is None:
            return
        try:
            self.project.save_candidates(
                self._paths(),
                list(self.candidates.values()),
                {"source": self.candidate_source},
            )
        except OSError as exc:
            self._notify("error", f"could not save the candidates: {exc}")

    # -- the cut --

    def _write_threshold(self) -> None:
        blocked = self.threshw.blockSignals(True)
        self.threshw.setValue(_slider_of_value(self.threshold))
        self.threshw.blockSignals(blocked)
        self.threshlabel.setText(f"{self.threshold:.3f}")

    def _threshold_moved(self, value: int) -> None:
        self.threshold = _value_of_slider(value)
        self.threshlabel.setText(f"{self.threshold:.3f}")
        self._write_timer.start()

    def _threshold_settled(self) -> None:
        if self.project is not None:
            self.project.threshold = self.threshold
            self._save_project()
        self._write_found()
        self._say(f"{len(self._label_of)} candidates above the cut.")

    # -- the found category --

    def _found_name(self, cls: int) -> str:
        return f"{self.project.names[cls]}{FOUND_SUFFIX}"

    def _sync_from_labels(self) -> None:
        """Believe the label store about the candidates standing in it.

        A candidate deleted with the ordinary label tools was rejected.  One moved
        with its grips is where the reader put it.  This has to run before the store
        is rewritten, or both would be undone without a word.
        """
        if not self._label_of or self.project is None:
            return
        present = {id(label) for label in self.browser.labels}
        categories = {c.name for c in self.browser.labels.categories}
        gone = []
        for uid, label in list(self._label_of.items()):
            box = self.candidates.get(uid)
            if box is None:
                continue
            if id(label) not in present:
                # The whole category going is a reader clearing it, not an opinion
                # about any one candidate.
                if label.category in categories:
                    gone.append(uid)
                continue
            if label.category != self._found_name(box.cls):
                # Moved to another category by hand: it is the reader's label now.
                del self.candidates[uid]
                self._label_of.pop(uid, None)
                continue
            geometry = (label.t0, label.t1, label.f0, label.f1)
            if label.has_frequency() and geometry != (box.t0, box.t1, box.f0, box.f1):
                self.candidates[uid] = box._replace(
                    t0=label.t0, t1=label.t1, f0=label.f0, f1=label.f1, polygon=None
                )
                self._edited.add(uid)
        if gone:
            self.project.reject(self._paths(), [self.candidates[uid] for uid in gone])
            for uid in gone:
                del self.candidates[uid]
                self._label_of.pop(uid, None)
            self._save_project()

    def _write_found(self) -> None:
        """Put the candidates above the cut into the label store, and redraw."""
        if self.project is None or self._closed:
            return
        self._sync_from_labels()
        labels = self.browser.labels
        shown = sorted(
            (
                (uid, box)
                for uid, box in self.candidates.items()
                if box.score >= self.threshold
            ),
            key=lambda item: -item[1].score,
        )
        hidden = max(len(shown) - FOUND_LIMIT, 0)
        shown = shown[:FOUND_LIMIT]
        self._label_of = {}
        with labels.undo_undisturbed():
            for name in self.project.names:
                labels.remove_category(f"{name}{FOUND_SUFFIX}")
            for cls in sorted({box.cls for _uid, box in shown}):
                # Before it has a row: from here on no save can write one.
                labels.mark_transient(self._found_name(cls))
                labels.add_category(
                    self._found_name(cls), KIND_SPAN, labels.next_color()
                )
            for uid, box in shown:
                label = Label(
                    self._found_name(box.cls),
                    KIND_SPAN,
                    box.channel,
                    box.t0,
                    box.t1,
                    box.f0,
                    box.f1,
                    f"score={box.score:.4f}",
                    box.polygon,
                )
                labels.add(label)
                self._label_of[uid] = label
        self._labels_changed(save=False)
        self._sync_controls()
        if hidden:
            self._notify(
                "warning",
                f"{hidden} candidates are above the cut but not drawn: raise the "
                "cut, or read the full list from the CSV",
            )

    def _clear_found(self) -> None:
        labels = self.browser.labels
        self._sync_from_labels()
        self._label_of = {}
        if self.project is None:
            return
        removed = 0
        with labels.undo_undisturbed():
            for name in self.project.names:
                removed += labels.remove_category(f"{name}{FOUND_SUFFIX}")
        if removed:
            self._labels_changed(save=False)

    def clear_candidates(self, *_args) -> None:
        self._clear_found()
        self.candidates = {}
        self._persist_candidates()
        self._sync_controls()
        self._say("Candidates cleared. Your labels and the review are unchanged.")

    # -- review --

    def _candidate_at(self, t: float, f: float, ax=None) -> Optional[int]:
        """The candidate drawn under a click on lane `ax`.  The smallest, as with labels.

        Only one drawn on that lane: a candidate of channel 3 is not on channel 5's
        lane, and a click there is about something else.
        """
        channels = None if ax is None else lane_channels(ax)
        shown = (
            None
            if channels is None
            else {channels}
            if isinstance(channels, int)
            else set(channels)
        )
        best, best_area = None, None
        for uid in self._label_of:
            box = self.candidates.get(uid)
            if box is None:
                continue
            if (
                shown is not None
                and box.channel is not None
                and box.channel not in shown
            ):
                continue
            if box.t0 <= t <= box.t1 and box.f0 <= f <= box.f1:
                area = (box.t1 - box.t0) * (box.f1 - box.f0)
                if best_area is None or area < best_area:
                    best, best_area = uid, area
        return best

    def reject_candidate(self, uid: int) -> None:
        box = self.candidates.pop(uid, None)
        if box is None:
            return
        self.project.reject(self._paths(), [box])
        self._last_action = ("reject", uid, box)
        self._save_project()
        self._write_found()
        self._persist_timer.start()
        self._update_summary()

    def undo_last(self) -> None:
        """Take back the last rejection, or the last view finished.  One level, as
        audian's own undo is."""
        action, self._last_action = self._last_action, None
        if action is None or self.project is None:
            return
        self._sync_from_labels()
        paths = self._paths()
        key = self.project.recording_key(paths)
        rejected = self.project.rejected.get(key, [])
        if action[0] == "reject":
            _kind, uid, box = action
            if box in rejected:
                rejected.remove(box)
            self.candidates[uid] = box
            self._say("Took back the rejection.")
        else:
            _kind, rect, labels, gone, removed = action
            reviewed = self.project.reviewed.get(key, [])
            if rect in reviewed:
                reviewed.remove(rect)
            store = self.browser.labels
            for label in labels:
                index = store.index_of(label)
                if index >= 0:
                    store.remove(index)
            store.forget_undo()
            for box in gone:
                if box in rejected:
                    rejected.remove(box)
            self.candidates.update(removed)
            self._labels_changed(save=True)
            self._say("Took back the last view.")
        self._save_project()
        self._write_found()
        self._persist_timer.start()
        self._update_summary()

    def _current_rect(self) -> Optional[Rect]:
        """What is on screen, in the band every shown lane has in view."""
        lanes = self._shown_lanes()
        if not lanes:
            return None
        t0, t1, f0, f1 = self._view_of(lanes[0])
        for ax in lanes[1:]:
            _t0, _t1, low, high = self._view_of(ax)
            f0, f1 = max(f0, low), min(f1, high)
        return Rect(t0, t1, f0, f1)

    def _reviewed_rect(self, rect: Rect) -> Rect:
        """`rect`, for the classes the reader knows about now and no others."""
        return rect._replace(classes=tuple(range(len(self.project.names))))

    def _can_finish_view(self, rect: Rect) -> bool:
        """Whether this view may be called done, or say why it may not."""
        hidden = self._hidden_channels()
        if hidden:
            names = ", ".join(str(c) for c in hidden[:6])
            self._say(
                f"Channel {names} is hidden, and the model sees it. Show every "
                "channel, or the mean spectrogram (Shift+F2), before finishing a view."
            )
            return False
        undrawn = sum(
            1
            for uid, box in self.candidates.items()
            if self._in_rect(box, rect)
            and box.score >= self.threshold
            and uid not in self._label_of
        )
        if undrawn:
            self._say(
                f"{undrawn} candidates here are above the cut but not drawn. Raise the "
                "Cut, or zoom in, before finishing this view."
            )
            return False
        return True

    def _in_rect(self, box: Box, rect: Rect) -> bool:
        t = box.centre_t
        f = 0.5 * (box.f0 + box.f1)
        return rect.t0 <= t <= rect.t1 and rect.f0 <= f <= rect.f1

    def accept_view(self) -> None:
        """Everything in view is answered: what is shown is right, the rest is not."""
        project = self._ensure_project()
        rect = self._current_rect()
        if project is None or rect is None:
            return
        # The view is reviewed for the classes known now, which must include a
        # category the reader started labelling in it a moment ago.
        if not self.sync_classes([self.browser.labels]):
            self._say("Label some events first, in label mode (b).")
            return
        if not self._can_finish_view(rect):
            return
        self._sync_from_labels()
        labels = self.browser.labels
        made, removed = [], {}
        for uid, box in list(self.candidates.items()):
            if not self._in_rect(box, rect):
                continue
            if uid in self._label_of:
                name = project.names[box.cls]
                # The reader may have removed the class while this was on screen.
                labels.add_category(name, KIND_SPAN)
                label = Label(
                    name,
                    KIND_SPAN,
                    box.channel,
                    box.t0,
                    box.t1,
                    box.f0,
                    box.f1,
                    f"accepted score={box.score:.4f}",
                    box.polygon,
                )
                labels.add(label)
                made.append(label)
            removed[uid] = self.candidates.pop(uid)
            self._label_of.pop(uid, None)
        # The reader's own act, and too large to take back with audian's one undo: that
        # would have removed one label of many and called it the label just added.
        # Backspace takes back the whole view.
        labels.forget_undo()
        reviewed = self._reviewed_rect(rect)
        project.mark_reviewed(self._paths(), reviewed)
        self._last_action = ("view", reviewed, made, [], removed)
        self._finish_view(f"Accepted {len(made)}.", save=True)

    def reject_view(self) -> None:
        """Nothing still shown in view is an event."""
        project = self._ensure_project()
        rect = self._current_rect()
        if project is None or rect is None:
            return
        self.sync_classes([self.browser.labels])
        if not self._can_finish_view(rect):
            return
        self._sync_from_labels()
        rejected, removed = [], {}
        for uid, box in list(self.candidates.items()):
            if not self._in_rect(box, rect):
                continue
            if uid in self._label_of:
                rejected.append(box)
            removed[uid] = self.candidates.pop(uid)
            self._label_of.pop(uid, None)
        project.reject(self._paths(), rejected)
        reviewed = self._reviewed_rect(rect)
        project.mark_reviewed(self._paths(), reviewed)
        self._last_action = ("view", reviewed, [], rejected, removed)
        self._finish_view(f"Rejected {len(rejected)}.", save=False)

    def _finish_view(self, message: str, save: bool) -> None:
        self._save_project()
        self._write_found()
        self._persist_timer.start()
        if save:
            self._labels_changed(save=True)
        self._update_summary()
        self.next_window(prefix=f"{message} Backspace takes it back.")

    def next_window(self, *_args, prefix: str = "") -> None:
        """Go to the window worth looking at next, in label mode."""
        project = self._ensure_project()
        if project is None:
            return
        if project.config is None or not self.candidates:
            self._say(f"{prefix} Nothing left to review. Train, or Find more.".strip())
            return
        config = self._model_config() or project.config
        grid = tiling.grid_of(config, self._rate())
        ids = list(self.candidates)
        ranked = review.rank_windows(
            [self.candidates[uid] for uid in ids],
            grid.tile_s,
            self.threshold,
            self._reviewed_here(),
            span=(0.0, self._duration()),
            seed=len(project.reviewed_rects(self._paths())),
        )
        finished = len(project.reviewed_rects(self._paths()))
        window = review.pick_window(ranked, finished) if ids else None
        if window is None:
            self._say(f"{prefix} Nothing left to review. Train, or Find more.".strip())
            return
        self.goto(window.t0, window.t1, grid.f_low, grid.f_high)
        self.enter_label_mode()
        # What is on screen, and not what the window holds below the cut: a count the
        # reader cannot find on the lane reads as boxes that failed to draw.
        drawn = sum(ids[i] in self._label_of for i in window.candidates)
        self._say(
            f"{prefix} {len(ranked)} windows to go, {drawn} candidates here. "
            "Click what is wrong, then Enter.".strip()
        )

    def enter_label_mode(self) -> None:
        """Label mode, where a click rejects and a drag labels what was missed.

        Through the window's own Label action when there is one, so that the tool
        bar, the mode chip and every other tab follow, as when the reader presses b.
        """
        if self._label_mode():
            return
        acts = getattr(self.browser.window(), "acts", None)
        action = getattr(acts, "label_region", None)
        if action is not None:
            action.trigger()
        if not self._label_mode():
            set_mode = getattr(self.browser, "set_region_mode", None)
            if set_mode is not None:
                set_mode(type(self.browser).MODE_LABEL)

    def goto(self, t0: float, t1: float, f0: float, f1: float) -> None:
        """Show this rectangle."""
        browser = self.browser
        try:
            browser.set_times(max(0.0, t0), t1 - t0)
            browser.set_ranges("f", f0, f1)
        except Exception:  # noqa: BLE001 - internal API; fall back per lane
            pass
        for ax in self._lanes():
            vb = ax.getViewBox()
            vb.setXRange(t0, t1, padding=0)
            vb.setYRange(f0, f1, padding=0)

    def _update_summary(self) -> None:
        if self.project is not None:
            self.summaryw.setText(self.summary())
        self.refresh_classes()

    def summary(self) -> str:
        project = self.project
        paths = self._paths()
        boxes, regions, unusable = self.labelled_boxes()
        text = (
            f"{len(boxes)} labels, {len(project.reviewed_rects(paths)) + len(regions)} "
            f"views reviewed, {len(project.rejected_boxes(paths))} rejected"
        )
        if unusable:
            text += f"; {unusable} labels have no band and are left out"
        return text

    # -- train --

    def _sources(self) -> list:
        """What every recording of the project contributes."""
        project = self.project
        here = project.recording_key(self._paths())
        sources = []
        for paths in project.recording_paths():
            key = project.recording_key(paths)
            if key == here:
                labels = self.browser.labels
            else:
                labels = LabelSet()
                labels.read(sidecar_path(paths[0]))
            boxes, regions, _ = self.labelled_boxes(labels)
            if not boxes and not project.rejected_boxes(paths):
                continue
            sources.append(
                dict(
                    paths=paths,
                    boxes=[project_.box_json(b) for b in boxes],
                    reviewed=[list(r) for r in project.reviewed_rects(paths) + regions],
                    rejected=[
                        project_.box_json(b) for b in project.rejected_boxes(paths)
                    ],
                    hidden=[
                        list(r) for r in self._unusable_spans(project.config, labels)
                    ],
                )
            )
        return sources

    def train(self, *_args) -> None:
        project = self._ensure_project()
        if project is None:
            return
        # Every recording's categories, before any box is given a class index.
        self.sync_classes()
        self.refresh_classes()
        boxes, _regions, unusable = self.labelled_boxes()
        if not boxes:
            self._say("Label some events first, in label mode (b).")
            return
        settings = self.training_settings()
        base = self._start_weights(settings)
        if base is None:
            return
        config = self._config_for(boxes)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        directory = project.models_directory() / stamp
        project.segment = self._segment()
        project.training = settings
        self._save_project()
        note = f" {unusable} labels have no band and are left out." if unusable else ""
        self._say(f"Training on {len(boxes)} labels...{note}")
        self.curvew.clear("training...")
        self.curvew.setVisible(True)
        self._start(
            "train",
            "train",
            config=config.as_dict(),
            names=list(project.names),
            directory=os.fspath(directory),
            sources=self._sources(),
            segment=project.segment,
            base=base,
            epochs=self.train_epochs or settings["epochs"] or None,
            batch=settings["batch"] or None,
            # None lets the runner choose, 0 never stops early
            patience=(settings["patience"] or None) if settings["stop_early"] else 0,
            hold_out_every=settings["hold_out_every"],
        )

    def _start_weights(self, settings: dict) -> Optional[str]:
        """The weights a new model starts from, or None after saying why there are none.

        A pretrained file goes by name, since the runner finds it in its weights
        directory; the project's last model goes by path.  Going on from a model that
        draws something else than Learn asks for would train one kind on top of the
        other, so that is refused rather than guessed at.
        """
        if settings["start"] == "last":
            base = self.project.model_path() if self.project is not None else None
            if not base:
                self._say("There is no model to go on from yet: start from Pretrained.")
                return None
            try:
                task = models_.read_meta(base).get("task")
            except ValueError:
                task = None
            wanted = "segment" if self._segment() else "detect"
            if task != wanted:
                drawn = "outlines" if task == "segment" else "boxes"
                self._say(
                    f"The last model draws {drawn}: set Learn to match it, or start "
                    "from Pretrained."
                )
                return None
            return base
        base = self._base_weights(settings)
        if not (self.weights_dir / base).is_file():
            self.fetchw.setVisible(True)
            self._say(f"{base} is not here yet: press Get models once while online.")
            return None
        return base

    def _epoch_ended(self, _job: str, point: dict) -> None:
        if self._job_kind == "train":
            self.curvew.add(point)
            self.curvew.setVisible(True)

    def _curve_note(self, meta: dict) -> str:
        if meta.get("held_out"):
            return f"held out: {meta.get('val_events', 0)} events"
        return "nothing held out: these are scores on the training tiles"

    def _show_model_curve(self) -> None:
        """The curve of the model in use, kept beside it."""
        path = self.project.model_path() if self.project is not None else None
        if not path:
            return
        try:
            meta = models_.read_meta(path)
        except ValueError:
            return
        if meta.get("curve"):
            self.curvew.set_points(
                meta["curve"], meta.get("best_epoch"), self._curve_note(meta)
            )
            self.curvew.setVisible(True)

    def _how_long(self, message: dict) -> str:
        """How the training ended, and what to do when early stopping could not act."""
        run = int(message.get("epochs_run") or 0)
        planned = int(message.get("epochs") or 0)
        text = f"{run} of {planned} epochs" if run else f"{planned} epochs"
        if run and run < planned:
            text += ", stopped early"
        best = message.get("best_epoch")
        if best:
            text += f", best at {best}"
        stopping = str(message.get("early_stopping") or "")
        if stopping.startswith("off: nothing"):
            every = int(message.get("hold_out_every") or 0)
            config = self.project.config if self.project is not None else None
            gap = BLOCK_LENGTHS * config.tile_s if config is not None else 0.0
            if every <= 0:
                text += ". Hold out is set to nothing, so early stopping had no data"
            else:
                text += (
                    ". Early stopping was off, with nothing held out: label in at "
                    f"least {max(every, 2)} places more than {gap:.0f} s apart, or "
                    "hold out more often"
                )
        return text

    def _train_done(self, message: dict) -> None:
        project = self.project
        project.set_model(message["model"])
        threshold = float(message.get("threshold", self.threshold))
        project.threshold = threshold
        self.threshold = threshold
        self._write_threshold()
        self._save_project()
        self.curvew.set_points(
            message.get("curve") or self.curvew.points,
            message.get("best_epoch"),
            self._curve_note(message),
        )
        metrics = message.get("metrics", {})
        where = (
            f"on {message.get('val_events', 0)} held-out events"
            if message.get("held_out")
            else "on its own training data, which flatters it"
        )
        counts = message.get("events_per_class") or []
        missing = [
            name
            for name, count in zip(project.names, counts)
            if not count and name not in self._left_out()
        ]
        warning = (
            f" No event of {', '.join(missing)} fits its images, so it cannot find "
            "them: they are outside the band or too long for the tiles your first "
            "labels set."
            if missing
            else ""
        )
        self._say(
            f"Trained in {message.get('seconds', 0):.0f} s, {self._how_long(message)}. "
            f"mAP50 {metrics.get('map50', 0):.3f} {where}. Preview shows how it does "
            f"on screen.{warning}"
        )

    # -- run --

    def run_detection(self, *_args) -> None:
        project = self._ensure_project()
        if project is None or not project.model_path():
            self._say("Train a model first.")
            return
        stamp = time.strftime("%Y%m%d-%H%M%S")
        out = project.detections_path(self._paths(), stamp)
        self._say("Detecting over the whole recording...")
        self._start(
            "run",
            "detect",
            paths=self._paths(),
            model=project.model_path(),
            out=os.fspath(out),
        )

    def _run_done(self, message: dict) -> None:
        boxes = [project_.box_from_json(v) for v in message.get("boxes", [])]
        self._set_candidates(boxes, "run")
        self.threshold = self._model_threshold() or self.threshold
        self._write_threshold()
        self._write_found()
        self._persist_candidates()
        self.project.threshold = self.threshold
        self._save_project()
        seconds = float(message.get("seconds", 0.0))
        length = float(message.get("recording_seconds", 0.0))
        speed = f", {length / seconds:.0f}x real time" if seconds > 0 else ""
        if message.get("fell_back_to_cpu"):
            speed += " (GPU was full: ran on the CPU)"
        self._say(
            f"{len(self._label_of)} events above the cut, of {message.get('count', 0)} "
            f"found{speed}. All scores are in {Path(message.get('out', '')).name}."
        )
        self._notify("info", f"detections written to {message.get('out', '')}")

    def export_csv(self, *_args) -> Optional[Path]:
        """What is above the cut, in the columns of a label file, beside the recording."""
        paths = self._paths()
        if not paths or self.project is None:
            return None
        self._sync_from_labels()
        path = Path(paths[0])
        out = path.with_name(f"{path.stem}-detections.csv")
        rows = sorted(
            (b for b in self.candidates.values() if b.score >= self.threshold),
            key=lambda b: b.t0,
        )
        try:
            with open(out, "w", newline="", encoding="utf-8") as fp:
                # The sidecar's own columns and cells, outline included, so the file
                # opens as labels anywhere audian reads them.
                writer = csv.writer(fp)
                writer.writerow(LABEL_COLUMNS)
                for box in rows:
                    label = Label(
                        self.project.names[box.cls],
                        KIND_SPAN,
                        box.channel,
                        box.t0,
                        box.t1,
                        box.f0,
                        box.f1,
                        f"score={box.score:.4f}",
                        box.polygon,
                    )
                    writer.writerow(label.row())
        except OSError as exc:
            self._notify("error", f"could not write {out}: {exc}")
            return None
        self._say(f"Wrote {len(rows)} events to {out.name}.")
        return out

    # ------------------------------------------------------------ going away

    def about_to_flush_labels(self) -> None:
        """audian is closing this recording: take the guesses back, stop the runners.

        Its only notice: Qt delivers no close event to a child when the window goes.
        The guesses come off because the sidecar being written is hand-authored; they
        are in the project and return next time.  The runners stop here because a
        process left to its `QProcess` destructor at exit has crashed this application.
        """
        if self._closed:
            return
        self._closed = True
        self._write_timer.stop()
        self._persist_timer.stop()
        try:
            self._clear_found()
            self._persist_candidates()
            # Without the project file, `_restore` finds nothing to pick up next time.
            if self.candidates:
                self._save_project()
        finally:
            self._shutdown()

    def _shutdown(self) -> None:
        try:
            self.batch.shutdown(500)
        except Exception:  # noqa: BLE001 - going away regardless
            pass
        self.detach()

    def closeEvent(self, event):  # noqa: N802 - Qt's spelling
        self.about_to_flush_labels()
        super().closeEvent(event)
