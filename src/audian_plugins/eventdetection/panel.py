"""The few-shot detector, as a plugin.

**Plugins > Event detection > Normalised cross-correlation** turns the
detector on and opens its tab in the lower half of the side panel; unticking
it, or closing the tab, turns it off and releases its reader and worker.
Nothing has to be installed or copied: `Plugins.load_bundled` walks
`audian_plugins`, which is what this package is for.

It was a loose file in a working directory before that, which is the oldest
of the three discovery rules and the wrong one to ship on.  Installing a
plugin meant copying it into every directory a reader ever launched from,
and one that was present but not copied looked exactly like a feature that
had not been merged -- the Plugins menu is absent when nothing registers, so
there was nothing on screen to disagree with.

The arithmetic is next door in `engine`, which imports no Qt and is tested
without a window; this file is the half that has a reader in it.  Both halves
are in one package so that the detector can be lifted into a repository of
its own without leaving its brain behind, and everything it needs from audian
comes through `audian.pluginapi` -- the surface that will not move under it
once it lives somewhere else.

What the reader does
--------------------

Mark a few examples in any label category, choose that category here, and
the detector learns from all of them and marks the rest.  **Fit to examples**
uses every label in the recording, runs behind a progress bar, and reports
training recall when it finishes; precision is deliberately absent because
unlabelled events are not known negatives.  The visible window is only the
fast tuning surface: sensitivity, level and adjustable IoU non-maximum
suppression are previewed there.  Run then applies the same settings to the
whole recording.

The result is added as an editable ``<source> (found)`` category and also
written beside the recording as a canonical label CSV, with the NCC score in
the note column.  The examples are never overwritten: their category is the
input, the found category and export are the output.

Two decisions about that loop are worth stating.

**The preview is not a preview.**  Candidates are written into the label
set as a category of their own, so what appears on screen is drawn by the
same overlay, in the same colours, as everything else -- and is the same
code path the Run button commits.  There is no second renderer to disagree
with the first.  The category is rewritten in place on every change and
`forget_undo` is called after, so a slider drag does not fill the undo
history with fifty versions of the same answer.

**Moving the sensitivity slider does not re-run the detector.**  The score
curve is cached and only the threshold is reapplied, which measured 0.12 ms
against 200-940 ms to rescore a window.  Changing what is *matched* -- the
category, the domain, the representation, the combiner -- is what throws
the cache away.

Reading the recording, not the buffer
-------------------------------------

Nothing here touches `browser.data.buffer`, and that is deliberate twice
over.  The buffer is a window that moves: `DataBrowser.set_times` cancels
every task in flight before shifting it *in place*, so a background sweep
holding a slice of it would be reading memory that the reader repositions
by panning.  And the marked examples are routinely outside it -- a reader
labels at 2 s and scrolls to 12 s -- so templates could not be cut from it
at all.  The panel opens its own `thunderlab.DataLoader` instead, and the
sweep opens another on its own thread.

The sweep is on a thread of its own rather than through `tasks.TaskManager`
for one specific reason: `TaskManager.sigJob` is connected to *every*
registered worker's `run` slot, so a second kind of job would be delivered
to the spectrogram's `ComputeWorker` as well as to this one.  Routing jobs
by type is a change to the core, and a plugin asking for one is a plugin
that has not demonstrated anything.  The cancellation vocabulary is still
audian's -- `tasks.tokens.CancelToken` -- because that part composes.
"""

from __future__ import annotations

import csv
import os
from dataclasses import replace
from pathlib import Path
from typing import NamedTuple, Optional

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from audian.pluginapi import (
    KIND_SPAN,
    Cancelled,
    CancelToken,
    Label,
    ParameterGroup,
    narrow_combo,
    open_files,
    theme,
)

from . import engine as detection

#: Appended to the source category to name where detections land.  A
#: separate category and never the source itself: a detector that wrote
#: into the category it learned from would eat the examples it was given,
#: and the reader could never tell their own marks from its guesses.
DETECTED_SUFFIX = " (found)"

#: A control moved is usually a control about to move again.  The same
#: 200 ms the overlap box uses, for the same reason.
PREVIEW_DEBOUNCE_MS = 200

#: Seconds of recording per block of the whole-file sweep.
#:
#: The threshold is measured from each block's own score curve, so this is
#: not only a memory knob: it is the window over which "the noise floor"
#: means anything.  Ten seconds is long enough that the median and MAD are
#: stable for the signals audian is used on, and short enough that a
#: recording whose background changes through it is followed rather than
#: averaged over.
BLOCK_S = 10.0

#: Above this many detections the panel stops drawing a preview and says
#: so.  The navigator already had a legibility problem at a few hundred
#: annotations; a slider dragged to its loose end can produce tens of
#: thousands, and drawing them would punish the reader for exploring.
PREVIEW_LIMIT = 2000

#: The left end of the level control.  It is its explicit "off" position,
#: not a gate at -60 dB: correlation is deliberately independent of level,
#: and a reader should be able to ask it that unqualified question.  Sixty
#: decibels below a block's peak is also below the useful range of the
#: recordings this was measured on, so the first live step (-59 dB) is
#: continuous with off rather than a jump into the middle of the data.
LEVEL_FLOOR_DB = -60

#: A point says where an event is, but normalized cross-correlation also
#: needs to know how much signal belongs to it.  Twenty milliseconds is a
#: useful opening value for the pulse-like events Audian's default ``pulse``
#: category describes; the panel exposes it rather than pretending a point
#: contains an extent it does not.
DEFAULT_POINT_WIDTH_MS = 20.0


class Recording:
    """Read-only access to the whole recording, by time.

    Its own loader rather than the browser's buffer -- see the module
    docstring.  All channels stay present when the examples carry no
    channel, which is what a label drawn on the mean spectrogram means: the
    detector averages their *power* in that domain (and their samples in the
    trace domain), rather than choosing one arbitrary lane or averaging
    waveforms before measuring power.  ``paths`` is the browser's complete
    ordered file list: one logical recording is often several consecutive
    WAVs, and editable-label times are relative to that whole sequence.
    """

    def __init__(self, paths):
        # Use the same multi-file opener as the browser.  A bare DataLoader
        # applies a timestamp-continuity heuristic which is known to drop the
        # final short file of TASCAM sessions; a detector must scan exactly
        # the timeline the GUI displayed.
        if isinstance(paths, (list, tuple, np.ndarray)):
            self.paths = [os.fspath(path) for path in paths]
        else:
            self.paths = [os.fspath(paths)]
        if not self.paths:
            raise ValueError("a recording needs at least one file")
        # `path` remains the first file because output CSVs and editable-label
        # sidecars are anchored there.  `paths` is what readers must reopen.
        self.path = self.paths[0]
        source = self.paths if len(self.paths) > 1 else self.path
        self.loader = open_files(source, BLOCK_S, 0.0)
        self.rate = float(self.loader.rate)
        self.frames = len(self.loader)
        self.channels = int(self.loader.channels)

    @property
    def duration(self) -> float:
        return self.frames / self.rate if self.rate else 0.0

    def samples(self, t0: float, t1: float, channel: Optional[int] = None):
        """``[t0, t1)`` as one signal, and the time its first sample is at."""
        i0 = max(int(np.floor(t0 * self.rate)), 0)
        i1 = min(int(np.ceil(t1 * self.rate)), self.frames)
        if i1 <= i0:
            return np.zeros(0), t0
        block = np.asarray(self.loader[i0:i1], dtype=float)
        if block.ndim > 1 and channel is not None:
            block = block[:, channel]
        return block, i0 / self.rate

    def close(self) -> None:
        try:
            self.loader.close()
        except Exception:  # noqa: BLE001 - closing must not raise at teardown
            pass


def _sweep(paths, templates, settings, channel, token, progress) -> list:
    """Run the detector over a whole recording, block by block.

    Blocks overlap by `detection.margin_s` on each side and only
    detections whose onset falls in the block's own stretch are kept, so an
    event lying across a boundary is found exactly once: the block that
    owns its onset sees all of it, and the neighbour that also sees it
    discards it as belonging to somewhere else.
    """
    recording = Recording(paths)
    try:
        margin = detection.margin_s(templates)
        step = max(BLOCK_S, margin * 4.0)
        duration = recording.duration
        found, start = [], 0.0
        while start < duration:
            token.check()
            stop = min(start + step, duration)
            block, t0 = recording.samples(max(start - margin, 0.0),
                                          min(stop + margin, duration), channel)
            if block.size:
                for candidate in detection.detect(block, recording.rate,
                                                  templates, settings, t0):
                    if start <= candidate.t0 < stop:
                        found.append(candidate)
            start = stop
            if progress is not None:
                progress(min(start / duration, 1.0) if duration else 1.0)
        # `detect` tidies one block at a time.  A requested join gap still
        # has to work across a streaming boundary, so make the collected
        # sequence obey the same post-processing contract once more.
        return detection.tidy(found, templates, settings)
    finally:
        recording.close()


def _fit_windows(duration: float, templates, examples: list) -> list:
    """Bounded score windows covering every example exactly once."""
    radius = max(0.5 * BLOCK_S, detection.margin_s(templates))
    window_s = 2.0 * radius
    windows = []
    for example in sorted(examples, key=lambda item: item.t0):
        start = max(example.t0 - radius, 0.0)
        start = min(start, max(duration - window_s, 0.0))
        stop = min(start + window_s, duration)
        if windows and example.t1 <= windows[-1][1]:
            windows[-1][2].append(example)
        else:
            windows.append([start, stop, [example]])
    return windows


class FitResult(NamedTuple):
    templates: object
    k: Optional[float]
    recalled: int
    total: int


def _fit(paths, templates, examples, settings, channel, token, progress) -> FitResult:
    """Learn globally, fit globally, then measure training recall globally."""
    recording = Recording(paths)
    examples = list(examples)
    try:
        if templates is None:
            done = 0

            def read(t0, t1):
                nonlocal done
                token.check()
                block = recording.samples(t0, t1, channel)
                done += 1
                progress(0.25 * done / max(len(examples), 1))
                return block

            templates = detection.learn_from_reader(
                read, recording.rate, examples, settings,
            )
        else:
            progress(0.25)
        if templates is None or not templates.ok:
            progress(1.0)
            return FitResult(templates, None, 0, len(examples))

        windows = _fit_windows(recording.duration, templates, examples)
        fitted = []
        for index, (start, stop, here) in enumerate(windows):
            token.check()
            block, actual = recording.samples(start, stop, channel)
            if block.size:
                score, times, _level = detection.score_curve(
                    block, recording.rate, templates, settings, actual,
                )
                k = detection.calibrate_k(score, times, here, templates)
                if k is not None:
                    fitted.append(k)
            progress(0.25 + 0.375 * (index + 1) / max(len(windows), 1))
        k = min(fitted) if fitted else None
        if k is None:
            progress(1.0)
            return FitResult(templates, None, 0, len(examples))

        fitted_settings = replace(settings, k=k).normalized()
        recalled = 0
        tolerance = max(templates.duration_s / 3.0, 1.0 / recording.rate)
        for index, (start, stop, here) in enumerate(windows):
            token.check()
            block, actual = recording.samples(start, stop, channel)
            found = detection.detect(
                block, recording.rate, templates, fitted_settings, actual,
            ) if block.size else []
            recalled += sum(
                any(abs(candidate.t0 - example.t0) <= tolerance
                    for candidate in found)
                for example in here
            )
            progress(0.625 + 0.375 * (index + 1) / max(len(windows), 1))
        return FitResult(templates, k, recalled, len(examples))
    finally:
        recording.close()


class SweepWorker(QObject):
    """The whole-file run, on a thread of its own."""

    sigProgress = Signal(float)
    sigFinished = Signal(object)
    sigFailed = Signal(str)

    def __init__(self, paths, templates, settings, channel, token):
        super().__init__()
        self.paths = paths
        self.templates = templates
        self.settings = settings
        self.channel = channel
        self.token = token

    def run(self) -> None:
        try:
            found = _sweep(self.paths, self.templates, self.settings,
                           self.channel, self.token, self.sigProgress.emit)
        except Cancelled:
            self.sigFinished.emit(None)
        except Exception as exc:  # noqa: BLE001 - a plugin may not take the app down
            self.sigFailed.emit(str(exc))
        else:
            self.sigFinished.emit(found)


class FitWorker(QObject):
    """Global learning, threshold fitting and recall, away from the GUI."""

    sigProgress = Signal(float)
    sigFinished = Signal(object)
    sigFailed = Signal(str)

    def __init__(self, paths, templates, examples, settings, channel, token):
        super().__init__()
        self.paths = paths
        self.templates = templates
        self.examples = examples
        self.settings = settings
        self.channel = channel
        self.token = token

    def run(self) -> None:
        try:
            result = _fit(
                self.paths, self.templates, self.examples, self.settings,
                self.channel, self.token, self.sigProgress.emit,
            )
        except Cancelled:
            self.sigFinished.emit(None)
        except Exception as exc:  # noqa: BLE001 - report plugin failures in the GUI
            self.sigFailed.emit(str(exc))
        else:
            self.sigFinished.emit(result)


class DetectorPanel(QWidget):
    """The Detector tab."""

    def __init__(self, browser, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.browser = browser
        self.recording = None
        self.templates = None
        self._score = None
        self._times = None
        self._level = None
        self._scored_for = None
        self._templates_for = None
        #: what the threshold was last fitted for, so a fresh source is
        #: fitted once and a hand-moved slider is then left alone
        self._calibrated_for = None
        self._fitting_for = None
        self._fit_recall = ""
        #: `LabelSet.revision` the category list was last built from
        self._categories_at = None
        #: whether a Run finished; an uncommitted preview is taken back off
        #: the recording when the reader looks away
        self._committed = False
        #: Whether previews may draw.  Cleared by the Clear button and set
        #: again by any control the reader touches: clearing and then
        #: instantly redrawing on the next debounce tick is not clearing.
        self._drawing = True
        self._thread = None
        self._worker = None
        self._token = None
        self._job = None

        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(theme.S6)
        group = ParameterGroup("Detector", self, caption=False, narrow=True)
        self.group = group

        self.sourcew = narrow_combo(QComboBox(self))
        self.sourcew.setToolTip("Label category to learn the examples from")
        self.sourcew.currentIndexChanged.connect(self._source_changed)
        group.add_row("Learn from", "", self.sourcew)

        self.pointwidthw = pg.SpinBox(
            self, DEFAULT_POINT_WIDTH_MS, bounds=(0.01, 10000.0),
            suffix=" ms", step=1.0, decimals=4,
        )
        if hasattr(browser, "style_parameter_spinbox"):
            browser.style_parameter_spinbox(self.pointwidthw)
        self.pointwidthw.setToolTip(
            "Template duration for point labels. The point is treated as "
            "the centre of this interval. Span labels already carry their "
            "own duration."
        )
        self.pointwidthw.sigValueChanged.connect(self._invalidate)
        self._point_width_row = group.add_row(
            "Point width", "", self.pointwidthw,
        )

        self.domainw = narrow_combo(QComboBox(self))
        for key, name in ((detection.SPECTROGRAM, "Spectrogram"),
                          (detection.TRACE, "Trace")):
            self.domainw.addItem(name, key)
        self.domainw.setToolTip("Match the examples as pictures or as waveforms")
        self.domainw.currentIndexChanged.connect(self._domain_changed)
        group.add_row("Match in", "", self.domainw)

        self.representationw = narrow_combo(QComboBox(self))
        for key, name, tip in (
            ("pcen", "PCEN", "Per-channel energy normalisation. Measured best, "
                             "and the only one that holds up at -5 dB."),
            ("db", "Decibel", "What the spectrogram on screen is drawn from."),
            ("whitened", "Whitened", "Decibel with each bin's own median "
                                     "removed. The cheap classic."),
        ):
            self.representationw.addItem(name, key)
            self.representationw.setItemData(
                self.representationw.count() - 1, tip, Qt.ItemDataRole.ToolTipRole)
        self.representationw.currentIndexChanged.connect(self._invalidate)
        # the caption comes back with the field so the whole row can be
        # hidden: hiding only the field leaves its label beside nothing,
        # which reads as a control that failed to load
        self._representation_row = group.add_row("Match on", "",
                                                 self.representationw)

        self.combinerw = narrow_combo(QComboBox(self))
        self.combinerw.setToolTip("How several examples become one score")
        self.combinerw.currentIndexChanged.connect(self._invalidate)
        group.add_row("Combine", "", self.combinerw)

        # Sensitivity and k are one control with two faces, the way the
        # overlap slider and its box are: the slider is the one a hand
        # reaches for and the box is the one that holds the exact value.
        # Neither is the truth on its own -- `_writing` stops the pair
        # echoing each other into a loop.
        self._writing = False
        self.sensitivityw = QSlider(Qt.Orientation.Horizontal, self)
        self.sensitivityw.setRange(0, 100)
        self.sensitivityw.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.sensitivityw.setTickInterval(25)
        self.sensitivityw.setValue(50)
        self.sensitivityw.tooltip = (
            "How far above the score curve's own noise floor a match must "
            "reach. Not an absolute confidence: a fixed one tuned here "
            "returns nothing at all on a noisier stretch."
        )
        self.sensitivityw.setToolTip(self.sensitivityw.tooltip)
        self.sensitivityw.valueChanged.connect(self._sensitivity_moved)
        self.kw = pg.SpinBox(self, detection.DEFAULT_K_SPECTROGRAM,
                             bounds=(detection.MIN_K, detection.MAX_K),
                             suffix=" σ", step=0.1, decimals=3)
        if hasattr(browser, "style_parameter_spinbox"):
            browser.style_parameter_spinbox(self.kw)
        self.kw.tooltip = self.sensitivityw.tooltip
        self.kw.setToolTip(self.kw.tooltip)
        self.kw.sigValueChanged.connect(self._k_typed)
        group.add_row("Sensitivity", "",
                      ParameterGroup.expanding(self.sensitivityw), self.kw)

        # The level gate.  Measured nearly inert on a clean recording, and
        # that is not a fault in it: the correlation is already independent
        # of how loud a stretch is, so on the reference cricket moving this
        # from -60 to -30 dB changed 370 detections to 367 and nothing at
        # all above the default cut.  It earns its keep on a noisy field
        # recording, where a shape can match in something that is not there.
        # Relative to the loudest thing in the block being scored, so it
        # means the same in a quiet file as in a loud one.
        self.levelw = QSlider(Qt.Orientation.Horizontal, self)
        self.levelw.setRange(LEVEL_FLOOR_DB, 0)
        self.levelw.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.levelw.setTickInterval(20)
        self.levelw.setValue(LEVEL_FLOOR_DB)
        self.levelw.tooltip = (
            "Reject matches quieter than this, relative to the loudest part "
            f"of what is being scanned. At {LEVEL_FLOOR_DB} dB it is off."
        )
        self.levelw.setToolTip(self.levelw.tooltip)
        self.levelw.valueChanged.connect(self._level_moved)
        self.leveldbw = pg.SpinBox(self, float(LEVEL_FLOOR_DB),
                                   bounds=(float(LEVEL_FLOOR_DB), 0.0),
                                   suffix=" dB", step=1.0, decimals=4)
        if hasattr(browser, "style_parameter_spinbox"):
            browser.style_parameter_spinbox(self.leveldbw)
        self.leveldbw.tooltip = self.levelw.tooltip
        self.leveldbw.setToolTip(self.leveldbw.tooltip)
        self.leveldbw.sigValueChanged.connect(self._level_typed)
        group.add_row("Min level", "",
                      ParameterGroup.expanding(self.levelw), self.leveldbw)

        self.nmsw = QCheckBox("Suppress overlapping matches", self)
        self.nmsw.setChecked(True)
        self.nmsw.setToolTip(
            "Keep the strongest match when candidate intervals overlap by "
            "more than the allowed intersection-over-union below."
        )
        self.nmsw.toggled.connect(self._nms_toggled)
        group.add_span_row(self.nmsw)

        self.nmsoverlapw = QSlider(Qt.Orientation.Horizontal, self)
        self.nmsoverlapw.setRange(0, 100)
        self.nmsoverlapw.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.nmsoverlapw.setTickInterval(25)
        self.nmsoverlapw.setValue(
            int(round(100 * detection.DEFAULT_NMS_OVERLAP))
        )
        self.nmsoverlapw.tooltip = (
            "Maximum intersection-over-union between surviving matches. "
            "Lower values suppress more nearby detections."
        )
        self.nmsoverlapw.setToolTip(self.nmsoverlapw.tooltip)
        self.nmsoverlapw.valueChanged.connect(self._nms_overlap_moved)
        self.nmsoverlapbox = pg.SpinBox(
            self, 100.0 * detection.DEFAULT_NMS_OVERLAP,
            bounds=(0.0, 100.0), suffix=" %", step=1.0, decimals=3,
        )
        if hasattr(browser, "style_parameter_spinbox"):
            browser.style_parameter_spinbox(self.nmsoverlapbox)
        self.nmsoverlapbox.tooltip = self.nmsoverlapw.tooltip
        self.nmsoverlapbox.setToolTip(self.nmsoverlapbox.tooltip)
        self.nmsoverlapbox.sigValueChanged.connect(self._nms_overlap_typed)
        self._nms_overlap_row = group.add_row(
            "Max overlap", "",
            ParameterGroup.expanding(self.nmsoverlapw), self.nmsoverlapbox,
        )

        # How far from the marked examples a detection's length may stray,
        # and how close two may sit before they are one.  Both are factors
        # and milliseconds rather than absolute times, because both are
        # really statements about the examples: a reader who marks a longer
        # call should not have to retype the bounds that go with it.
        self.tolerancew = pg.SpinBox(self, detection.DURATION_TOLERANCE,
                                     bounds=(1.0, 10.0), suffix="x",
                                     step=0.1, decimals=3)
        if hasattr(browser, "style_parameter_spinbox"):
            browser.style_parameter_spinbox(self.tolerancew)
        self.tolerancew.setToolTip(
            "How many times longer or shorter than the marked examples a "
            "detection may be. Only bites after a merge, since an unmerged "
            "one is exactly one template long."
        )
        self.tolerancew.sigValueChanged.connect(lambda _s: self._postprocess_changed())
        group.add_row("Length", "", self.tolerancew)

        self.gapw = pg.SpinBox(self, 0.0, bounds=(0.0, 5000.0), suffix=" ms",
                               step=5.0, decimals=5)
        if hasattr(browser, "style_parameter_spinbox"):
            browser.style_parameter_spinbox(self.gapw)
        self.gapw.setToolTip(
            "Join detections closer together than this. Off by default: "
            "suppressing the same event found twice is the peak picker's "
            "job, and a gap wide enough to be useful is wider than the "
            "silence inside a pulse train."
        )
        self.gapw.sigValueChanged.connect(lambda _s: self._postprocess_changed())
        group.add_row("Join gap", "", self.gapw)

        self.statusw = QLabel("", self)
        self.statusw.setFont(theme.font_mono(theme.SIZE_SMALL_PT))
        theme.tint(self.statusw, "fg.muted")
        self.statusw.setWordWrap(True)
        group.add_span_row(self.statusw)

        buttons = QWidget(self)
        row = QHBoxLayout(buttons)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.S4)
        self.calibratew = QPushButton("Fit to examples", buttons)
        self.calibratew.setToolTip(
            "Set the threshold just under the weakest labelled example, "
            "using every example in the recording. The visible window is "
            "only the preview used to fine-tune the result."
        )
        self.calibratew.clicked.connect(self._calibrate)
        row.addWidget(self.calibratew, 1)
        self.runw = QPushButton("Run", buttons)
        self.runw.setToolTip(
            "Apply these settings to the whole recording. The preview above "
            "only draws what is in view; this is what fills the rest."
        )
        self.runw.clicked.connect(self._run_clicked)
        row.addWidget(self.runw, 1)
        # Deliberate removal, because nothing else removes them any more.
        # Detections used to be swept away whenever this widget was hidden,
        # which meant shutting the side panel silently emptied the category
        # -- so the reader needs a way to say it on purpose instead.
        self.clearw = QPushButton("Clear", buttons)
        self.clearw.setToolTip("Remove every detection this plugin has added")
        self.clearw.clicked.connect(self._clear_clicked)
        row.addWidget(self.clearw)
        group.add_span_row(buttons)

        self.progressw = QProgressBar(self)
        self.progressw.setRange(0, 100)
        self.progressw.setTextVisible(True)
        self.progressw.hide()
        group.add_span_row(self.progressw)

        box.addWidget(group)
        box.addStretch(1)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(PREVIEW_DEBOUNCE_MS)
        self._debounce.timeout.connect(self.preview)

        # Nothing is detected until the reader looks at this tab.  Building
        # the panel wires controls whose signals all schedule a preview, and
        # a preview writes a category into the label set -- so an untouched
        # plugin would add a hundred and sixty-eight labels to a recording
        # somebody opened to read.  `showEvent` is the honest trigger: it
        # fires when the reader chooses the Detector tab.
        self._ready = False
        self._domain_changed()
        self.refresh_categories()
        self._ready = True
        if hasattr(browser, "sigRangesChanged"):
            browser.sigRangesChanged.connect(lambda *a: self._schedule())

    def showEvent(self, event):  # noqa: N802 - Qt's spelling
        super().showEvent(event)
        self.refresh_categories()
        self._schedule()

    # No `hideEvent`, and its absence is the fix for a real complaint.
    #
    # This used to take an uncommitted preview back off the recording, so
    # that a reader who glanced at the tab did not find a category they
    # never asked for in their sidecar.  What it actually produced was
    # detections that came and went for reasons the reader could not see:
    # measured, shutting the side panel took a drawn preview from fifty
    # marks to nought, and reopening it put them back.  Anything that hides
    # this widget -- another plugin's tab, the panel's own close action --
    # did the same, and the honest reading of that from outside is "the
    # detector only works on what is on screen".
    #
    # Detections are editable labels in a category of their own, which is
    # what they were asked to be: output to keep, correct and save.  They
    # now last until the reader clears them with the button beside Run, or
    # closes the plugin.

    def _clear_clicked(self) -> None:
        """Throw away every detection, and stop drawing new ones for now.

        Stopping matters as much as the removal.  A preview is scheduled on
        a debounce, so clearing alone left the next tick to draw the same
        marks straight back -- the button appeared to do nothing.  Touching
        any control turns drawing back on.
        """
        self._debounce.stop()
        self._drawing = False
        self._committed = False
        self._clear_found()
        self._say("cleared -- move a control to detect again")

    def _clear_found(self) -> None:
        labels = getattr(self.browser, "labels", None)
        if labels is None:
            return
        if labels.remove_category(self._category_name()):
            labels.forget_undo()
            redraw = getattr(self.browser, "redraw_labels", None)
            if redraw is not None:
                redraw()

    # -- the recording ---------------------------------------------------

    def _paths(self) -> list:
        """The complete ordered file list behind the browser's timeline.

        ``Data.file_path`` becomes the first path after opening.  That is the
        right anchor for sidecars, but not a sufficient source for a split
        recording: a label at 1785 s may live in file two while file one ends
        at 932 s.  The live loader retains the complete list.
        """
        data = getattr(self.browser, "data", None)
        loader = getattr(data, "data", None)
        opened = getattr(loader, "file_paths", None)
        if opened is not None:
            paths = [os.fspath(path) for path in opened]
            if paths:
                return paths
        fallback = getattr(data, "file_path", None)
        if fallback is None:
            return []
        if isinstance(fallback, (list, tuple, np.ndarray)):
            return [os.fspath(path) for path in fallback]
        return [os.fspath(fallback)]

    def _open(self) -> Optional[Recording]:
        if self.recording is not None:
            return self.recording
        paths = self._paths()
        if not paths:
            return None
        try:
            self.recording = Recording(paths)
        except Exception as exc:  # noqa: BLE001 - a bad path is the reader's
            self._say(f"cannot read the recording: {exc}")
            self.recording = None
            return None
        self._check_timeline()
        return self.recording

    def _check_timeline(self) -> None:
        """Say so when the detector and the browser disagree about the file.

        The panel opens the recording again rather than reading the display
        buffer, which `set_times` shifts in place under any thread holding a
        slice of it.  The price of that independence is that the two opens
        can disagree -- a globbed session is several files joined by their
        timestamps, and a loader that drops one of them leaves the detector
        scanning a shorter timeline than the reader is looking at, silently
        and with every label past the join landing nowhere.

        This does not repair that.  It refuses to hide it: a mismatch is
        reported in the status line and named in the message log, so a
        recording that scans wrong says which files it opened rather than
        just returning a poor answer.
        """
        recording = self.recording
        data = getattr(self.browser, "data", None)
        loader = getattr(data, "data", None)
        if recording is None or loader is None:
            return
        try:
            shown = len(loader) / float(data.rate)
        except Exception:  # noqa: BLE001 - a browser without a file yet
            return
        if abs(shown - recording.duration) <= 1.0 / max(recording.rate, 1.0):
            return
        message = (f"detector opened {recording.duration:.1f} s in "
                   f"{len(recording.paths)} file(s), but the browser shows "
                   f"{shown:.1f} s")
        self._say(message + " -- results past the join will be wrong")
        notify = getattr(self.browser, "notify", None)
        if notify is not None:
            notify("error", f"{message}: "
                            f"{', '.join(Path(p).name for p in recording.paths)}")

    def closeEvent(self, event):  # noqa: N802 - Qt's spelling
        self._debounce.stop()
        if self.recording is not None:
            self.recording.close()
            self.recording = None
        self._stop_job()
        # A plugin tab is also its on/off switch.  Do not leave a worker
        # behind a tab the reader has closed, or let Qt destroy a running
        # QThread while its worker is still returning from a block.
        self._teardown()
        super().closeEvent(event)

    # -- the controls ----------------------------------------------------

    def refresh_categories(self) -> None:
        """Offer every category the reader has actually marked something in.

        Both span and point labels are examples.  Spans carry their template
        duration; points use the explicit Point width control.  A category
        with nothing in it still cannot teach the detector anything, and
        offering it would only let the reader select a source that can never
        work.

        Called whenever the tab is shown, and on every preview, and not only
        once at build time -- which is what it did, and the panel is built
        during browser setup, *before* the sidecar beside the recording has
        been read.  Filled from an empty label set and never refilled, the
        list came up with nothing in it and no way to get anything into it.
        `LabelSet.revision` makes the repeat cheap: every mutation bumps it,
        so the rebuild happens when the labels changed and not otherwise.
        """
        labels = getattr(self.browser, "labels", None)
        if labels is None:
            return
        revision = getattr(labels, "revision", None)
        if revision is not None and revision == self._categories_at:
            return
        self._categories_at = revision
        counts = {}
        for label in labels:
            if not label.category.endswith(DETECTED_SUFFIX):
                counts[label.category] = counts.get(label.category, 0) + 1
        wanted = self.sourcew.currentData()
        self.sourcew.blockSignals(True)
        self.sourcew.clear()
        for name in sorted(counts):
            self.sourcew.addItem(f"{name}  ({counts[name]})", name)
        if wanted is not None:
            found = self.sourcew.findData(wanted)
            if found >= 0:
                self.sourcew.setCurrentIndex(found)
        self.sourcew.blockSignals(False)
        self._sync_point_width_row()
        if not counts:
            self._say("draw a few examples in a label category first")

    def _source_changed(self, *args) -> None:
        self._sync_point_width_row()
        self._invalidate()

    def _sync_point_width_row(self) -> None:
        """Show the extra duration only when the chosen source needs it."""
        labels = getattr(self.browser, "labels", None)
        name = self.sourcew.currentData()
        has_points = bool(
            labels is not None and name and
            any(label.category == name and label.is_point() for label in labels)
        )
        for widget in self._point_width_row:
            widget.setVisible(has_points)

    def _domain(self) -> str:
        return self.domainw.currentData() or detection.SPECTROGRAM

    def _domain_changed(self, *args) -> None:
        """Re-offer the combiners this domain allows, and recentre the cut.

        Both halves matter.  The trace is not offered mean-template or
        subspace because both measured badly on it, and its `k` is a factor
        of two from the spectrogram's -- carrying one over put the cut above
        the highest score the curve can reach, and found nothing at all.
        """
        domain = self._domain()
        names = {
            detection.MEAN_SCORES: "Mean of scores",
            detection.SUBSPACE: "Subspace",
            detection.MEAN_TEMPLATE: "Mean template",
            detection.MAX_TEMPLATES: "Best of scores",
        }
        wanted = self.combinerw.currentData()
        self.combinerw.blockSignals(True)
        self.combinerw.clear()
        for key in detection.combiners_for(domain):
            self.combinerw.addItem(names.get(key, key), key)
        found = self.combinerw.findData(wanted)
        self.combinerw.setCurrentIndex(max(found, 0))
        self.combinerw.blockSignals(False)

        # the representation is a question about a picture, so it goes away
        # entirely on the trace rather than sitting there greyed out
        spectral = domain == detection.SPECTROGRAM
        for widget in self._representation_row:
            widget.setVisible(spectral)
        self._write_k(detection.default_k(domain))
        self._invalidate()

    def _write_k(self, k: float) -> None:
        """Put `k` in both faces of the one control."""
        k = float(np.clip(k, detection.MIN_K, detection.MAX_K))
        self._writing = True
        try:
            self.kw.setValue(k)
            self.sensitivityw.setValue(
                int(round(detection.sensitivity_from_k(k, self._domain()))))
        finally:
            self._writing = False

    def _sensitivity_moved(self, value: int) -> None:
        if self._writing:
            return
        self._writing = True
        try:
            self.kw.setValue(detection.k_from_sensitivity(value, self._domain()))
        finally:
            self._writing = False
        self._postprocess_changed()

    def _k_typed(self, spin) -> None:
        if self._writing:
            return
        self._writing = True
        try:
            self.sensitivityw.setValue(
                int(round(detection.sensitivity_from_k(spin.value(), self._domain()))))
        finally:
            self._writing = False
        self._postprocess_changed()

    def _write_level(self, level_db: float) -> None:
        """Put the relative dB gate in both faces of its one control."""
        level_db = float(np.clip(level_db, LEVEL_FLOOR_DB, 0.0))
        self._writing = True
        try:
            self.leveldbw.setValue(level_db)
            self.levelw.setValue(int(round(level_db)))
        finally:
            self._writing = False

    def _level_changed(self) -> None:
        """Apply a gate cheaply, scoring once when its levels do not exist.

        Moving a live gate only re-picks the cached score curve.  Turning it
        on after a curve was scored with the gate off is the one transition
        that has to rescore, because that curve carries no parallel level
        array yet.
        """
        if self._level_db() is not None and self._level is None:
            self._scored_for = None
        self._postprocess_changed()

    def _level_moved(self, value: int) -> None:
        if self._writing:
            return
        self._write_level(float(value))
        self._level_changed()

    def _level_typed(self, spin) -> None:
        if self._writing:
            return
        self._write_level(float(spin.value()))
        self._level_changed()

    def _level_db(self) -> Optional[float]:
        """The live level gate, or `None` at the control's off position."""
        value = float(self.leveldbw.value())
        return None if value <= LEVEL_FLOOR_DB else value

    def _nms_toggled(self, enabled: bool) -> None:
        for widget in self._nms_overlap_row:
            widget.setEnabled(enabled)
        self._postprocess_changed()

    def _write_nms_overlap(self, percent: float) -> None:
        percent = float(np.clip(percent, 0.0, 100.0))
        self._writing = True
        try:
            self.nmsoverlapw.setValue(int(round(percent)))
            self.nmsoverlapbox.setValue(percent)
        finally:
            self._writing = False

    def _nms_overlap_moved(self, value: int) -> None:
        if self._writing:
            return
        self._write_nms_overlap(float(value))
        self._postprocess_changed()

    def _nms_overlap_typed(self, spin) -> None:
        if self._writing:
            return
        self._write_nms_overlap(float(spin.value()))
        self._postprocess_changed()

    def _postprocess_changed(self) -> None:
        """A cheap tuning change invalidates recall, but not the score curve."""
        self._fit_recall = ""
        self._resume()

    # -- the settings ----------------------------------------------------

    def settings(self) -> detection.Settings:
        gap_s = float(self.gapw.value()) / 1000.0
        return detection.Settings(
            domain=self._domain(),
            representation=self.representationw.currentData() or "pcen",
            combiner=self.combinerw.currentData() or detection.DEFAULT_COMBINER,
            k=float(self.kw.value()),
            duration_tolerance=float(self.tolerancew.value()),
            merge_gap_s=gap_s or None,
            nms_enabled=self.nmsw.isChecked(),
            nms_overlap=float(self.nmsoverlapbox.value()) / 100.0,
            power_floor_db=self._level_db(),
        ).normalized()

    def examples(self) -> list:
        """The marked spans of the chosen category, as the engine wants them."""
        labels = getattr(self.browser, "labels", None)
        name = self.sourcew.currentData()
        if labels is None or not name:
            return []
        width_s = float(self.pointwidthw.value()) / 1000.0
        examples = []
        for label in labels:
            if label.category != name:
                continue
            if label.is_point():
                # A point identifies the event's centre.  Keep the derived
                # interval symmetric so changing Point width does not move
                # what the reader marked.
                half = 0.5 * width_s
                t0, t1 = label.t0 - half, label.t0 + half
            else:
                t0, t1 = label.t0, label.t_end()
            examples.append(detection.Example(t0, t1, label.f0, label.f1))
        return examples

    def _channel(self) -> Optional[int]:
        """The lane to match on, or `None` for the mean the labels were on."""
        labels = getattr(self.browser, "labels", None)
        name = self.sourcew.currentData()
        if labels is None or not name:
            return None
        channels = {label.channel for label in labels if label.category == name}
        channels.discard(None)
        return channels.pop() if len(channels) == 1 else None

    def _view(self) -> tuple:
        ranges = getattr(self.browser, "plot_ranges", None)
        try:
            trange = ranges["t"]
            return float(trange.r0[0]), float(trange.r1[0])
        except Exception:  # noqa: BLE001 - no view yet is not an error
            return 0.0, 0.0

    # -- the preview -----------------------------------------------------

    def _invalidate(self, *args) -> None:
        """Throw the cached curve away, because what is matched has changed."""
        self._scored_for = None
        self._fit_recall = ""
        self._resume()

    def _schedule(self) -> None:
        """Ask for a preview soon, if one is wanted at all."""
        if self._ready and self._drawing and not self.isHidden():
            self._debounce.start()

    def _resume(self, *args) -> None:
        """A control moved: draw again, even if Clear had stopped it."""
        self._drawing = True
        self._schedule()

    def _learn(self):
        """Cut the templates through one bounded read per example."""
        recording = self._open()
        examples = self.examples()
        if recording is None or not examples:
            return None
        settings = self.settings()
        channel = self._channel()
        return detection.learn_from_reader(
            lambda t0, t1: recording.samples(t0, t1, channel),
            recording.rate,
            examples,
            settings,
        )

    def _fit_key(self, settings) -> tuple:
        """Everything that changes the learned score at an example."""
        return (
            self.sourcew.currentData(), settings.domain,
            settings.representation, settings.combiner,
            tuple(self.examples()), self._channel(),
        )

    def preview(self) -> None:
        """Detect in the window on screen and draw the result as labels.

        Scored over at least `BLOCK_S`, centred on the view, even though
        only what falls inside the view is drawn.  The threshold is measured
        from the curve's own median and spread, so the *length* of the
        scored stretch is part of what it means: on a three second view of a
        singing cricket, where most of the curve is signal rather than
        background, the spread collapses and the default cut came out at
        1.507 -- above the highest value a correlation can take, so the
        panel opened showing nothing at all.  Scoring the span the run will
        use makes what the reader tunes here the thing that happens there.

        Silent while `_drawing` is off.  Clear turns it off, and the guard
        belongs here rather than only in `_schedule` because a fit running
        in the background finishes by drawing too -- a fit started before
        Clear was pressed would otherwise put the marks straight back,
        seconds later, for no reason the reader could see.
        """
        if not self._drawing:
            return
        recording = self._open()
        if recording is None:
            return
        self.refresh_categories()
        settings = self.settings()
        t0, t1 = self._view()
        if t1 <= t0:
            return
        centre, half = 0.5 * (t0 + t1), 0.5 * max(t1 - t0, BLOCK_S)
        s0 = max(centre - half, 0.0)
        s1 = min(centre + half, recording.duration)

        # The boxes themselves are part of the source.  A reader can move or
        # add one without changing the category name; keying only on that
        # name would silently keep scoring against the old examples.
        source = self._fit_key(settings)
        stamp = (*source, round(s0, 3), round(s1, 3))
        if stamp != self._scored_for:
            if self._templates_for != source:
                self.templates = self._learn()
                self._templates_for = source
            if self.templates is None or not self.templates.ok:
                self._say("no examples to learn from")
                return
            margin = detection.margin_s(self.templates)
            block, start = recording.samples(max(s0 - margin, 0.0),
                                             min(s1 + margin, recording.duration),
                                             self._channel())
            if not block.size:
                return
            self._score, self._times, self._level = detection.score_curve(
                block, recording.rate, self.templates, settings, start)
            self._scored_for = stamp

        if self._score is None or self._score.size == 0:
            self._say("the window is shorter than one example")
            return

        found = [c for c in detection.pick(self._score, self._times,
                                           self._level, self.templates, settings)
                 if t0 <= c.t0 < t1]
        self._draw(found)
        cut = detection.threshold_of(self._score, settings.k)
        recall = f" · {self._fit_recall}" if self._fit_recall else ""
        self._say(f"{len(found)} in view · cut {cut:.3f} of "
                  f"{float(self._score.max()):.3f} · "
                  f"{len(self.templates)} examples{recall}")

        # A new source/representation is fitted automatically, as before,
        # but now the global work is visible and cancellable rather than a
        # pause in the GUI thread.  The local preview above appears first.
        if source != self._calibrated_for and self._thread is None:
            self._start_fit(source, self.templates)

    def _category_name(self) -> str:
        return f"{self.sourcew.currentData() or 'events'}{DETECTED_SUFFIX}"

    def _draw(self, found: list) -> None:
        """Replace the found-category with `found`, and redraw.

        Written straight into the label set rather than into an overlay of
        this panel's own, so that what the reader tunes against is drawn by
        the same code that will draw what Run commits.  `forget_undo` after,
        because fifty steps of "the slider moved" is not an edit history.
        """
        labels = getattr(self.browser, "labels", None)
        if labels is None:
            return
        name = self._category_name()
        if len(found) > PREVIEW_LIMIT:
            found = []
            self._say(f"over {PREVIEW_LIMIT} matches -- not drawn. Lower the "
                      f"sensitivity.")
        labels.remove_category(name)
        labels.add_category(name, KIND_SPAN, labels.next_color())
        channel = self._channel()
        for candidate in found:
            labels.add(Label(name, KIND_SPAN, channel, candidate.t0,
                             candidate.t1, candidate.f_low_hz,
                             candidate.f_high_hz,
                             f"score {candidate.score:.3f}"))
        labels.forget_undo()
        for call in ("revalidate_selection", "redraw_labels",
                     "update_label_status"):
            fn = getattr(self.browser, call, None)
            if fn is not None:
                fn()

    def _calibrate(self) -> None:  # noqa: D401 - "Fit to examples"
        """Fit every label in the background, or stop the fit in progress.

        The one setting a reader cannot guess: eleven small templates and
        three large ones want thresholds a factor of two apart, and the
        examples already say which.
        """
        if self._thread is not None:
            if self._job == "fit":
                self._stop_job()
            return
        # pressing a button is asking for an answer, so it also undoes Clear
        self._drawing = True
        examples = self.examples()
        if not examples:
            self._say("no examples to learn from")
            return
        settings = self.settings()
        source = self._fit_key(settings)
        templates = self.templates if self._templates_for == source else None
        self._start_fit(source, templates)

    def _start_fit(self, source, templates=None) -> None:
        if self._thread is not None:
            return
        paths = self._paths()
        examples = self.examples()
        if not paths or not examples:
            self._say("no examples to learn from")
            return

        self._fitting_for = source
        self._token = CancelToken()
        self._worker = FitWorker(
            paths, templates, examples, self.settings(), self._channel(), self._token,
        )
        self._thread = QThread(self)
        self._thread.setObjectName("audian-detector-fit")
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.sigProgress.connect(self._progress)
        self._worker.sigFinished.connect(self._fit_finished)
        self._worker.sigFailed.connect(self._failed)
        self._job = "fit"
        self.progressw.setValue(0)
        self.progressw.show()
        self.calibratew.setText("Stop fitting")
        self.runw.setEnabled(False)
        for widget in (
            self.sourcew, self.pointwidthw, self.domainw, self.representationw,
            self.combinerw, self.sensitivityw, self.kw, self.levelw,
            self.leveldbw, self.nmsw, self.nmsoverlapw, self.nmsoverlapbox,
            self.tolerancew, self.gapw,
        ):
            widget.setEnabled(False)
        self._say(f"fitting {len(examples)} examples from the recording ...")
        self._thread.start()

    def _fit_finished(self, result) -> None:
        source = self._fitting_for
        self._teardown()
        if result is None:
            self._say("fitting stopped")
            return
        # A category or representation can change while cancellation is
        # travelling to the worker.  Never apply a stale fit to the new source.
        if source != self._fit_key(self.settings()):
            self._say("examples changed; discarded the old fit")
            self.preview()
            return
        self.templates = result.templates
        self._templates_for = source
        self._calibrated_for = source
        if result.k is None:
            self._fit_recall = ""
            self._say("nothing to calibrate against in the recording")
            return
        self._write_k(result.k)
        percent = 100.0 * result.recalled / result.total if result.total else 0.0
        self._fit_recall = (
            f"training recall {result.recalled}/{result.total} ({percent:.1f}%)"
        )
        # A worker may have learned templates before there was a preview.
        # Re-score locally if so, then leave recall in the persistent status.
        self._scored_for = None
        if self.isHidden():
            self._say(self._fit_recall)
        else:
            self.preview()

    def _say(self, message: str) -> None:
        self.statusw.setText(message)

    # -- the whole recording ---------------------------------------------

    def _run_clicked(self) -> None:
        if self._thread is not None:
            if self._job == "run":
                self._stop_job()
            return
        self._drawing = True
        recording = self._open()
        if recording is None:
            return
        # Always recut here.  A reader can change the source/domain and hit
        # Run before the debounced preview fires; reusing the last preview's
        # templates would then scan the whole file for the previous choice.
        self.templates = self._learn()
        if self.templates is None or not self.templates.ok:
            self._say("no examples to learn from")
            return

        self._token = CancelToken()
        self._worker = SweepWorker(recording.paths, self.templates,
                                   self.settings(), self._channel(), self._token)
        self._thread = QThread(self)
        self._thread.setObjectName("audian-detector")
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.sigProgress.connect(self._progress)
        self._worker.sigFinished.connect(self._run_finished)
        self._worker.sigFailed.connect(self._failed)
        self._job = "run"
        self.progressw.setValue(0)
        self.progressw.show()
        self.runw.setText("Stop")
        self.calibratew.setEnabled(False)
        self._say(f"scanning {recording.duration:.0f} s ...")
        self._thread.start()

    def _progress(self, fraction: float) -> None:
        self.progressw.setValue(int(round(100 * fraction)))

    def _stop_job(self) -> None:
        if self._token is not None:
            self._token.cancel()

    # Kept as the old private spelling for plugin clients and tests written
    # before fitting became a second background job.
    def _stop_sweep(self) -> None:
        self._stop_job()

    def _teardown(self) -> None:
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(3000)
        self._thread = None
        self._worker = None
        self._token = None
        self._job = None
        self._fitting_for = None
        self.progressw.hide()
        self.runw.setText("Run")
        self.runw.setEnabled(True)
        self.calibratew.setText("Fit to examples")
        self.calibratew.setEnabled(True)
        for widget in (
            self.sourcew, self.pointwidthw, self.domainw, self.representationw,
            self.combinerw, self.sensitivityw, self.kw, self.levelw,
            self.leveldbw, self.nmsw, self.nmsoverlapw, self.nmsoverlapbox,
            self.tolerancew, self.gapw,
        ):
            widget.setEnabled(True)
        self._sync_point_width_row()
        for widget in self._nms_overlap_row:
            widget.setEnabled(self.nmsw.isChecked())

    def _failed(self, message: str) -> None:
        job = self._job or "detector"
        self._teardown()
        self._say(f"the {job} failed: {message}")
        notify = getattr(self.browser, "notify", None)
        if notify is not None:
            notify("error", f"detector: {message}")

    def _run_finished(self, found) -> None:
        self._teardown()
        if found is None:
            self._say("stopped")
            return
        self._committed = True
        self._draw(found)
        path = self._write_csv(found)
        self.refresh_categories()
        where = f" · {path.name}" if path is not None else ""
        self._say(f"{len(found)} found in the recording{where}")
        save = getattr(self.browser, "schedule_label_save", None)
        if save is not None:
            save()

    def _write_csv(self, found: list) -> Optional[Path]:
        """Write the run beside the recording, as its own file.

        Its own file and not the sidecar: the sidecar is the reader's, and a
        run that overwrote it would spend their hand annotation to store a
        guess.  The rows are the schema audian reads, so loading it as
        editable labels is a file-open away, and the detections are already
        in the label set by the time this is written -- the file is for
        keeping and for taking elsewhere, not for getting them on screen.
        """
        recording = self.recording
        if recording is None or not found:
            return None
        source = Path(recording.path)
        name = self._category_name().replace(" ", "-").replace("(", "").replace(")", "")
        path = source.with_name(f"{source.stem}-{name}.csv")
        channel = self._channel()
        cell = "" if channel is None else str(channel)
        try:
            with path.open("w", encoding="utf-8", newline="") as fh:
                writer = csv.writer(fh)
                writer.writerow(("category", "kind", "channel", "t_start_s",
                                 "t_end_s", "f_low_hz", "f_high_hz", "note"))
                for candidate in found:
                    low = "" if candidate.f_low_hz is None else f"{candidate.f_low_hz:.3f}"
                    high = "" if candidate.f_high_hz is None else f"{candidate.f_high_hz:.3f}"
                    writer.writerow((self._category_name(), KIND_SPAN, cell,
                                     f"{candidate.t0:.6f}", f"{candidate.t1:.6f}",
                                     low, high, f"score {candidate.score:.3f}"))
        except OSError as exc:
            self._say(f"could not write the CSV: {exc}")
            return None
        return path
