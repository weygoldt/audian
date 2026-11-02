"""The Detector tab: from a few clicks to a whole recording detected.

The loop is the whole interface, and it runs top to bottom::

    Label       click events on the spectrogram; SAM outlines each one
    Find more   candidates, from the examples or from the last trained model
    Review      go through the windows the model is least sure of
    Train       a detector on everything reviewed, in about a minute
    Run         the whole recording, in the background

Everything but the first step can repeat, and labelling goes on while a model trains:
SAM has a runner process of its own for that reason.  The design and the numbers are
in ``docs/specdetector-design.md``.

What goes where
---------------

A reader's labels are ordinary editable labels, in the current category, in the
sidecar beside the recording.  A machine's guesses are labels too, in a category of
their own, ``<class> (found)``, so they are drawn and edited by the code that draws
and edits everything else: there is no second renderer to disagree with the first.
But they are a loan.  `about_to_flush_labels` takes them back off before the sidecar
is written on closing, because that file is hand-authored and nothing in it should be
a guess; the candidates live in the project and come back next time.

A candidate deleted with audian's own label tools counts as rejected, and one moved
with its grips keeps the new geometry when it is accepted.  So the label store stays
the truth about what a candidate looks like now (`_sync_from_labels`).

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
    QInputDialog,
    QLabel,
    QProgressBar,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from audian.pluginapi import (
    KIND_SPAN,
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
from .dataset import Rect
from .tools import KEY_HELP, KeyRouter, LaneSurface, OutlineOverlay, PromptOverlay
from .tools import lane_channels

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

#: How long the view has to rest before SAM encodes it ahead of the next click.
VIEW_REST_MS = 250

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

#: The largest view SAM is asked to read, in samples over all channels: 800 MB of
#: float32.  Fully zoomed out on an hour of 16 channels at 20 kHz is 1.15e9 samples,
#: 4.3 GiB before any windows are cut from it, and the job cannot be stopped midway.
MAX_VIEW_SAMPLES = 2e8

#: How long after a rejection the candidates are written, so that a run of clicks is
#: one write: with 20000 outlined candidates, a write takes more than a second.
PERSIST_MS = 500


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

        #: the prompt being built
        self.prompt_ax = None
        self.points: list = []
        self.prompt_box: Optional[tuple] = None
        self.mask: Optional[dict] = None
        self._generation = 0
        self._segment_generation = -1
        self._segment_again = False

        self.surfaces: list = []
        self.prompts: dict = {}
        self.outlines: list = []
        self.armed = False

        self.router = KeyRouter(self._keys_live, self.run_key)
        self.batch = RunnerClient(self.weights_dir, self)
        self.interactive = RunnerClient(self.weights_dir, self)
        self.batch.sigProgress.connect(self._job_progress)
        self.batch.sigResult.connect(self._job_result)
        self.batch.sigError.connect(self._job_error)
        self.interactive.sigResult.connect(self._segment_result)
        self.interactive.sigError.connect(self._segment_error)
        self._job_kind = ""
        self._job_id = ""
        #: None chooses from the number of labels; a test sets a small number
        self.train_epochs: Optional[int] = None

        self._build()

        self._write_timer = QTimer(self)
        self._write_timer.setSingleShot(True)
        self._write_timer.setInterval(DEBOUNCE_MS)
        self._write_timer.timeout.connect(self._threshold_settled)
        self._view_timer = QTimer(self)
        self._view_timer.setSingleShot(True)
        self._view_timer.setInterval(VIEW_REST_MS)
        self._view_timer.timeout.connect(self._view_rested)
        self._persist_timer = QTimer(self)
        self._persist_timer.setSingleShot(True)
        self._persist_timer.setInterval(PERSIST_MS)
        self._persist_timer.timeout.connect(self._persist_candidates)
        if hasattr(browser, "sigRangesChanged"):
            browser.sigRangesChanged.connect(self._view_changed)

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
            "Turn on Label, and drag a box around a few events to begin.", self
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

        # -- label --
        self.classw = narrow_combo(QComboBox(self))
        self.classw.setToolTip("The class new labels go into, and that Find looks for")
        self.classw.activated.connect(self._class_picked)
        self.addclassw = self._button("+", "Add a new class", self._add_class_clicked)
        group.add_row(
            "Class", "", ParameterGroup.expanding(self.classw), self.addclassw
        )
        keys = "\n".join(f"{key}: {meaning}" for key, meaning in KEY_HELP)
        self.labelw = self._button(
            "Label",
            "Drag a box around an event and SAM outlines it. A rough box is enough: "
            "detectors trained on these did as well as on careful ones. A single "
            "click only finds the blob under it, so use it to refine an outline.\n"
            + keys,
            self._label_toggled,
            checkable=True,
        )
        self.donew = self._button(
            "View done",
            "Everything in view is labelled. Accepts the candidates still in it, "
            "marks the view as reviewed, and goes to the next window.",
            lambda: self.accept_view(),
        )
        self._row(group, self.labelw, self.donew)
        self.outlinew = QCheckBox("Show outlines", self)
        self.outlinew.setChecked(True)
        self.outlinew.setToolTip("Draw SAM's outlines over the boxes they belong to")
        self.outlinew.toggled.connect(self._outlines_toggled)
        group.add_span_row(self.outlinew)

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
        group.add_row("Look in", "", ParameterGroup.expanding(self.scopew))
        group.add_span_row(self.findw)

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
        self.nextw = self._button(
            "Next window",
            "Go to the window whose answer would teach the model most, and turn "
            "on Label.",
            self.next_window,
        )
        self.rejectw = self._button(
            "Reject view",
            "Nothing still proposed in view is an event. Marks the view as "
            "reviewed and goes on. Shift+Enter with Label on.",
            lambda: self.reject_view(),
        )
        self._row(group, self.nextw, self.rejectw)

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

    # ------------------------------------------------------------ small things

    def _say(self, message: str) -> None:
        self.statusw.setText(message)

    def _notify(self, level: str, message: str) -> None:
        notify = getattr(self.browser, "notify", None)
        if notify is not None:
            notify(level, message)

    def _keys_live(self) -> bool:
        return (
            self.armed
            and not self._closed
            and bool(self.browser.isVisible())
            and QApplication.activeWindow() is self.browser.window()
        )

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
        return self.project

    def _save_project(self) -> None:
        if self.project is None:
            return
        try:
            self.project.save()
        except OSError as exc:
            self._notify("error", f"could not save the detector project: {exc}")

    def _missing_weights(self) -> list:
        return [
            name
            for name in models_.ALL_WEIGHTS
            if not (self.weights_dir / name).is_file()
        ]

    def _sync_controls(self) -> None:
        running = self.batch.busy() if hasattr(self, "batch") else False
        has_model = self.project is not None and self.project.model is not None
        self.fetchw.setVisible(bool(self._missing_weights()))
        for widget in (self.findw, self.trainw, self.fetchw):
            widget.setEnabled(not running)
        self.runw.setEnabled(has_model and not running)
        self.exportw.setEnabled(bool(self.candidates))
        self.clearw.setEnabled(bool(self.candidates))
        self.nextw.setEnabled(bool(self.candidates))
        self.rejectw.setEnabled(bool(self.candidates))
        self.progressw.setVisible(running)
        self.stopw.setVisible(running)

    # ------------------------------------------------------------ classes

    def refresh_classes(self) -> None:
        """Offer every span category that is the reader's own."""
        labels = self.browser.labels
        wanted = self.current_class()
        names = [
            c.name
            for c in labels.lasting_categories
            if c.kind == KIND_SPAN
            and not c.name.endswith(FOUND_SUFFIX)
            and c.name != REGION_CATEGORY
        ]
        blocked = self.classw.blockSignals(True)
        self.classw.clear()
        for name in names:
            self.classw.addItem(name, name)
        index = self.classw.findData(wanted)
        if index >= 0:
            self.classw.setCurrentIndex(index)
        self.classw.blockSignals(blocked)

    def current_class(self) -> str:
        """What a new label is.  The browser's current category, when that can be one:
        the digit keys and the chips already choose it, and one choice is enough."""
        current = getattr(self.browser, "current_category", "") or ""
        category = self.browser.labels.category(current) if current else None
        if (
            category is not None
            and category.kind == KIND_SPAN
            and not current.endswith(FOUND_SUFFIX)
            and current != REGION_CATEGORY
        ):
            return current
        return self.classw.currentData() or ""

    def _class_picked(self, *_args) -> None:
        name = self.classw.currentData()
        if name:
            self.browser.current_category = name
            sync = getattr(self.browser, "sync_category_state", None)
            if sync is not None:
                sync()

    def add_class(self, name: str) -> bool:
        name = (name or "").strip()
        if not name or name.endswith(FOUND_SUFFIX):
            return False
        labels = self.browser.labels
        labels.add_category(name, KIND_SPAN)
        self.browser.current_category = name
        sync = getattr(self.browser, "sync_category_state", None)
        if sync is not None:
            sync()
        self.refresh_classes()
        return True

    def _add_class_clicked(self, *_args) -> None:
        name, ok = QInputDialog.getText(self, "New class", "Name of the class:")
        if ok:
            self.add_class(name)

    def _train_on_picked(self, *_args) -> None:
        project = self._ensure_project()
        if project is not None:
            project.segment = bool(self.trainonw.currentData())
            self._save_project()

    # ------------------------------------------------------------ lanes

    def attach(self) -> None:
        """Put a surface and overlays on every lane, once, as lanes come and go."""
        lanes = self._lanes()
        known = {surface.ax for surface in self.surfaces}
        if known == set(lanes):
            return
        self.detach()
        for ax in lanes:
            surface = LaneSurface(ax, self)
            surface.arm(self.armed)
            self.surfaces.append(surface)
            self.prompts[ax] = PromptOverlay(ax)
            outline = OutlineOverlay(ax)
            outline.set_visible(self.outlinew.isChecked())
            self.outlines.append(outline)
        self._update_outlines()

    def detach(self) -> None:
        for surface in self.surfaces:
            surface.detach()
        for overlay in self.prompts.values():
            overlay.detach()
        for overlay in self.outlines:
            overlay.detach()
        self.surfaces = []
        self.prompts = {}
        self.outlines = []

    def _outlines_toggled(self, on: bool) -> None:
        for overlay in self.outlines:
            overlay.set_visible(on)

    def _update_outlines(self) -> None:
        """Every outline that belongs to something still there, on every lane."""
        if not self.outlines:
            return
        polygons = []
        project = self.project
        if project is not None and project.masks:
            for label in self.browser.labels:
                if not label.has_frequency() or label.is_point():
                    continue
                key = project_.mask_key(
                    label.category, label.t0, label.t1, label.f0, label.f1
                )
                polygon = project.masks.get(key)
                if polygon:
                    polygons.append(polygon)
        for uid in self._label_of:
            box = self.candidates.get(uid)
            if box is not None and box.polygon:
                polygons.append(box.polygon)
        for overlay in self.outlines:
            overlay.set_outlines(polygons)

    def showEvent(self, event):  # noqa: N802 - Qt's spelling
        super().showEvent(event)
        if self._closed:
            return
        self.refresh_classes()
        self.attach()
        self._restore()
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
        boxes, meta = project.load_candidates(paths)
        if boxes:
            self._set_candidates(boxes, str(meta.get("source", "")))
            self._write_found()
        self._update_summary()

    # ------------------------------------------------------------ labelling

    def _label_toggled(self, *_args) -> None:
        self.arm(self.labelw.isChecked())

    def arm(self, on: bool) -> None:
        if on and (self.weights_dir / models_.SAM_WEIGHTS).is_file() is False:
            self._say(f"{models_.SAM_WEIGHTS} is missing: press Get models.")
            self.labelw.setChecked(False)
            self.fetchw.setVisible(True)
            on = False
        self.armed = bool(on)
        self.labelw.setChecked(self.armed)
        if self.armed:
            self.attach()
            self.router.install()
            self._ensure_project()
            self._view_timer.start()
        else:
            self.router.uninstall()
            self.clear_prompt()
        for surface in self.surfaces:
            surface.arm(self.armed)

    def _view_of(self, ax) -> tuple:
        """What a lane shows, inside the recording."""
        (t0, t1), (f0, f1) = ax.getViewBox().viewRange()
        duration, nyquist = self._duration(), self._rate() / 2
        t0, t1 = max(t0, 0.0), min(t1, duration)
        f0, f1 = max(f0, 0.0), min(f1, nyquist)
        return float(t0), float(t1), float(f0), float(f1)

    def _channel_request(self, ax) -> dict:
        channels = lane_channels(ax)
        if isinstance(channels, int):
            return {"channels": None, "reduction": str(channels)}
        return {"channels": channels, "reduction": "mean"}

    def lane_clicked(self, ax, t: float, f: float, negative: bool) -> None:
        """A click: reject the candidate under it, or add a prompt."""
        if not negative and not self.points and self.prompt_box is None:
            uid = self._candidate_at(t, f)
            if uid is not None:
                self.reject_candidate(uid)
                return
        if ax is not self.prompt_ax:
            self.clear_prompt()
            self.prompt_ax = ax
        self.points.append((t, f, not negative))
        self._prompt_changed()

    def lane_boxed(self, ax, t0: float, t1: float, f0: float, f1: float) -> None:
        """A drag: box the event, replacing whatever was being built."""
        self.clear_prompt()
        self.prompt_ax = ax
        self.prompt_box = (min(t0, t1), max(t0, t1), min(f0, f1), max(f0, f1))
        self._prompt_changed()

    def _prompt_changed(self) -> None:
        self._generation += 1
        self.mask = None
        self._draw_prompt()
        self._request_segment()

    def _color_of_class(self) -> int:
        return self.browser.labels.color_of(self.current_class())

    def _draw_prompt(self) -> None:
        overlay = self.prompts.get(self.prompt_ax)
        if overlay is None:
            return
        polygon = None if self.mask is None else self.mask["polygon"]
        overlay.show(polygon, self.points, self._color_of_class())

    def clear_prompt(self) -> None:
        self._generation += 1
        self.points = []
        self.prompt_box = None
        self.mask = None
        for overlay in self.prompts.values():
            overlay.clear()
        self.prompt_ax = None

    def _request_segment(self, encode_only: bool = False) -> None:
        """Ask SAM, or note that it must be asked again once it answers.

        One request in flight and the newest prompt waiting.  A reader clicking
        faster than SAM answers wants the answer to their last click, not a queue.
        """
        if self._closed:
            return
        if self.interactive.busy():
            self._segment_again = not encode_only or self._segment_again
            return
        ax = self.prompt_ax
        if ax is None:
            lanes = self._shown_lanes()
            if not lanes:
                return
            ax = lanes[0]
        t0, t1, f0, f1 = self._view_of(ax)
        if t1 <= t0 or f1 <= f0:
            return
        if (t1 - t0) * self._rate() * self._channels() > MAX_VIEW_SAMPLES:
            if not encode_only:
                self.clear_prompt()
                self._say("Zoom in to label: this view is too long for SAM to read.")
            return
        request = dict(
            paths=self._paths(),
            view=dict(t0=t0, t1=t1, f0=f0, f1=f1),
            **self._channel_request(ax),
        )
        if not encode_only:
            request["points"] = [list(p) for p in self.points]
            request["box"] = self.prompt_box
        self._segment_generation = self._generation
        self.interactive.submit("segment", **request)

    def _segment_result(self, _job: str, message: dict) -> None:
        if self._segment_generation == self._generation:
            mask = message.get("mask")
            if mask is not None:
                self.mask = mask
                self._draw_prompt()
                self._say(
                    "Enter accepts. Click to add a part, Ctrl+click to remove one."
                    if self.armed
                    else self.statusw.text()
                )
        if self._segment_again and not self._closed:
            self._segment_again = False
            self._request_segment()

    def _segment_error(self, _job: str, kind: str, text: str) -> None:
        self._segment_again = False
        if kind == "cancelled":
            return
        self._say(f"SAM failed: {text}")
        self._notify("error", f"SAM failed: {text}")
        self.fetchw.setVisible(bool(self._missing_weights()))

    def _view_changed(self, *_args) -> None:
        if self.armed and not self._closed:
            self._view_timer.start()

    def _view_rested(self) -> None:
        """Encode the view ahead of the click, unless there is a click to answer."""
        if self.armed and not self._closed:
            self._request_segment(encode_only=not (self.points or self.prompt_box))

    def accept_mask(self) -> bool:
        """Turn the outline into a label.  False when there is none yet."""
        if self.mask is None:
            return False
        category = self.current_class()
        if not category:
            self._say("Add a class first, with the + button.")
            return False
        project = self._ensure_project()
        if project is None:
            return False
        t0, t1, f0, f1 = self.mask["box"]
        channels = lane_channels(self.prompt_ax) if self.prompt_ax is not None else None
        channel = channels if isinstance(channels, int) else None
        self.browser.labels.add(Label(category, KIND_SPAN, channel, t0, t1, f0, f1, ""))
        project.class_index(category)
        project.masks[project_.mask_key(category, t0, t1, f0, f1)] = self.mask[
            "polygon"
        ]
        self._save_project()
        self.clear_prompt()
        self._labels_changed(save=True)
        self._update_summary()
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
        self._update_outlines()

    def run_key(self, name: str) -> None:
        if name == "enter":
            # One key, and two things it can mean.  It only finishes a view holding
            # candidates to answer: an Enter too many after an outline used to mark the
            # whole view as reviewed, which teaches every unlabelled event in it as
            # background, in silence.  With nothing proposed, that takes **View done**.
            if self.mask is not None:
                self.accept_mask()
            elif self.points or self.prompt_box:
                self._say("Waiting for SAM's outline.")
            elif self._drawn_in_view():
                self.accept_view()
            else:
                self._say(
                    "Nothing is proposed in view. Press View done when everything "
                    "here is labelled."
                )
        elif name == "shift_enter":
            if self._drawn_in_view():
                self.reject_view()
            else:
                self._say("Nothing is proposed in view.")
        elif name == "escape":
            self.clear_prompt()
        elif name == "backspace":
            if self.points:
                self.points.pop()
                if self.points:
                    self._prompt_changed()
                else:
                    self.clear_prompt()
            elif self.prompt_box is not None:
                self.clear_prompt()
            else:
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
        """
        labels = self.browser.labels if labels is None else labels
        project = self.project
        boxes, regions, unusable = [], [], 0
        for label in labels:
            if label.category == REGION_CATEGORY:
                if not label.is_point():
                    regions.append(Rect(label.t0, label.t1, label.f0, label.f1))
                continue
            if label.category.endswith(FOUND_SUFFIX):
                continue
            if label.category not in project.names:
                continue
            if label.is_point() or not label.has_frequency():
                unusable += 1
                continue
            polygon = project.masks.get(
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
        """
        labels = self.browser.labels if labels is None else labels
        half = config.tile_s / 32.0
        spans = []
        for label in labels:
            if label.category not in self.project.names:
                continue
            if label.is_point():
                spans.append(Rect(label.t0 - half, label.t0 + half))
            elif not label.has_frequency():
                spans.append(Rect(label.t0, label.t1))
        return spans

    def _shots_of_class(self) -> list:
        """The boxes Find learns from: every class that has some."""
        project = self._ensure_project()
        if project is None:
            return []
        current = self.current_class()
        if current:
            project.class_index(current)
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
        paths = self._paths()
        project = self.project
        reviewed = self._reviewed_here()
        labelled, _regions, _unusable = self.labelled_boxes()
        edited = {
            uid: self.candidates[uid] for uid in self._edited if uid in self.candidates
        }
        answered = as_array(
            labelled + project.rejected_boxes(paths) + list(edited.values())
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
        self.candidates = dict(edited)
        self._edited = set(edited)
        self._label_of = {}
        self._last_action = None
        for box in kept:
            self.candidates[self._next_id] = box
            self._next_id += 1
        self.candidate_source = source
        return kept

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

    def _candidate_at(self, t: float, f: float) -> Optional[int]:
        """The candidate on screen under a click.  The smallest, as with labels."""
        best, best_area = None, None
        for uid in self._label_of:
            box = self.candidates.get(uid)
            if box is None:
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
            _kind, rect, labels, gone, removed, masks = action
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
            for mask in masks:
                self.project.masks.pop(mask, None)
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
        # An outline waiting is an event the reader has just found.  Marking the view
        # around it without it would teach that very event as background.
        if self.mask is not None and not self.accept_mask():
            return
        if self.points or self.prompt_box is not None:
            self._say("Waiting for SAM's outline.")
            return
        current = self.current_class()
        if current:
            project.class_index(current)
        if not project.names:
            self._say("Add a class first, with the + button.")
            return
        if not self._can_finish_view(rect):
            return
        self._sync_from_labels()
        labels = self.browser.labels
        made, masks, removed = [], [], {}
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
                )
                labels.add(label)
                made.append(label)
                if box.polygon:
                    key = project_.mask_key(name, box.t0, box.t1, box.f0, box.f1)
                    project.masks[key] = [list(p) for p in box.polygon]
                    masks.append(key)
            removed[uid] = self.candidates.pop(uid)
            self._label_of.pop(uid, None)
        # The reader's own act, and too large to take back with audian's one undo: that
        # would have removed one label of many and called it the label just added.
        # Backspace takes back the whole view.
        labels.forget_undo()
        reviewed = self._reviewed_rect(rect)
        project.mark_reviewed(self._paths(), reviewed)
        self._last_action = ("view", reviewed, made, [], removed, masks)
        self._finish_view(f"Accepted {len(made)}.", save=True)

    def reject_view(self) -> None:
        """Nothing still shown in view is an event."""
        project = self._ensure_project()
        rect = self._current_rect()
        if project is None or rect is None:
            return
        if self.mask is not None or self.points or self.prompt_box is not None:
            self._say("Accept or drop the outline first.")
            return
        current = self.current_class()
        if current:
            project.class_index(current)
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
        self._last_action = ("view", reviewed, [], rejected, removed, [])
        self._finish_view(f"Rejected {len(rejected)}.", save=False)

    def _finish_view(self, message: str, save: bool) -> None:
        self.clear_prompt()
        self._save_project()
        self._write_found()
        self._persist_timer.start()
        if save:
            self._labels_changed(save=True)
        self._update_summary()
        self.next_window(prefix=f"{message} Backspace takes it back.")

    def next_window(self, *_args, prefix: str = "") -> None:
        """Go to the window worth looking at next, and turn on the tool."""
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
        if not self.armed:
            self.arm(True)
        # What is on screen, and not what the window holds below the cut: a count the
        # reader cannot find on the lane reads as boxes that failed to draw.
        drawn = sum(ids[i] in self._label_of for i in window.candidates)
        self._say(
            f"{prefix} {len(ranked)} windows to go, {drawn} candidates here. "
            "Click what is wrong, then Enter.".strip()
        )

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
        current = self.current_class()
        if current:
            project.class_index(current)
        boxes, _regions, unusable = self.labelled_boxes()
        if not boxes:
            self._say("Label some events first.")
            return
        config = self._config_for(boxes)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        directory = project.models_directory() / stamp
        project.segment = bool(self.trainonw.currentData())
        self._save_project()
        note = f" {unusable} labels have no band and are left out." if unusable else ""
        self._say(f"Training on {len(boxes)} labels...{note}")
        self._start(
            "train",
            "train",
            config=config.as_dict(),
            names=list(project.names),
            directory=os.fspath(directory),
            sources=self._sources(),
            segment=project.segment,
            epochs=self.train_epochs,
        )

    def _train_done(self, message: dict) -> None:
        project = self.project
        project.set_model(message["model"])
        threshold = float(message.get("threshold", self.threshold))
        project.threshold = threshold
        self.threshold = threshold
        self._write_threshold()
        self._save_project()
        metrics = message.get("metrics", {})
        where = (
            f"on {message.get('val_events', 0)} held-out events"
            if message.get("held_out")
            else "on its own training data: too little labelled to hold any out"
        )
        counts = message.get("events_per_class") or []
        missing = [name for name, count in zip(project.names, counts) if not count]
        warning = (
            f" No event of {', '.join(missing)} fits its images, so it cannot find "
            "them: they are outside the band or too long for the tiles your first "
            "labels set."
            if missing
            else ""
        )
        self._say(
            f"Trained in {message.get('seconds', 0):.0f} s. mAP50 "
            f"{metrics.get('map50', 0):.3f} {where}. Find more shows what it still "
            f"gets wrong.{warning}"
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
                writer = csv.writer(fp)
                writer.writerow(
                    [
                        "category",
                        "kind",
                        "channel",
                        "t_start_s",
                        "t_end_s",
                        "f_low_hz",
                        "f_high_hz",
                        "note",
                    ]
                )
                for box in rows:
                    writer.writerow(
                        [
                            self.project.names[box.cls],
                            KIND_SPAN,
                            "" if box.channel is None else box.channel,
                            f"{box.t0:.6f}",
                            f"{box.t1:.6f}",
                            f"{box.f0:.3f}",
                            f"{box.f1:.3f}",
                            f"score={box.score:.4f}",
                        ]
                    )
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
        self._view_timer.stop()
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
        self.armed = False
        self.router.uninstall()
        for client in (self.batch, self.interactive):
            try:
                client.shutdown(500)
            except Exception:  # noqa: BLE001 - going away regardless
                pass
        self.detach()

    def closeEvent(self, event):  # noqa: N802 - Qt's spelling
        self.about_to_flush_labels()
        super().closeEvent(event)
