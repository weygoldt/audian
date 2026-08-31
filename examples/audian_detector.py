"""The few-shot detector, as a plugin.

Copy this file next to a recording and start audian from that directory --
`Plugins.load_plugins` globs ``audian*.py`` in the working directory and
binds every callable named ``audian_*panel`` -- and a **Detector** tab
appears in the lower half of the side panel.

It lives in ``examples/`` and not in the repository root, and that is not
tidiness.  Discovery is by working directory, and the suite runs from the
root: a copy left there is loaded into *every* browser any test builds,
which is how it was found -- three of `tests/test_parameterbar`'s claims
broke at once, including the one that a browser with no plugins grows no
plugin region, and a test that opened ``data/Gryllus_campestris.wav`` wrote
a category of detections into the recording's tracked sidecar.  The root of
a checkout is a development directory; a reader's data directory is where a
plugin belongs, and copying it there is the same gesture as installing it.

The arithmetic is not here.  `audian.detection` holds it, imports no Qt and
is tested without a window; this file is the half that has a reader in it.
The split is the point of the exercise: a plugin should be able to add a
real feature through `Plugins.add_panel_factory` without the core growing a
special case for it, and the only thing this file needed that audian did
not already offer was nothing.

What the reader does
--------------------

Mark a few examples in any label category, choose that category here, and
the detector learns from them and marks the rest.  Everything above the Run
button works on **the window currently on screen**, so tuning is a loop of
moving a control and looking at what changed rather than of waiting.  Run
then applies the same settings to the whole recording.

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

import os
from pathlib import Path
from typing import Optional

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from audian import detection, theme
from audian.databrowser import ParameterGroup, narrow_combo
from audian.labels import KIND_SPAN, Label

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


class Recording:
    """Read-only access to the whole recording, by time.

    Its own loader rather than the browser's buffer -- see the module
    docstring.  The mean across channels when the examples carry no
    channel, which is what `Label.on_channel` means by one: they were drawn
    on the mean spectrogram, and matching a single lane would be answering
    a question nobody asked.
    """

    def __init__(self, path):
        from thunderlab.dataloader import DataLoader

        self.path = os.fspath(path)
        self.loader = DataLoader(self.path)
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
        if block.ndim > 1:
            block = block[:, channel] if channel is not None else block.mean(axis=1)
        return block, i0 / self.rate

    def close(self) -> None:
        try:
            self.loader.close()
        except Exception:  # noqa: BLE001 - closing must not raise at teardown
            pass


def _sweep(path, templates, settings, channel, token, progress) -> list:
    """Run the detector over a whole recording, block by block.

    Blocks overlap by `detection.margin_s` on each side and only
    detections whose onset falls in the block's own stretch are kept, so an
    event lying across a boundary is found exactly once: the block that
    owns its onset sees all of it, and the neighbour that also sees it
    discards it as belonging to somewhere else.
    """
    recording = Recording(path)
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
        return found
    finally:
        recording.close()


class SweepWorker(QObject):
    """The whole-file run, on a thread of its own."""

    sigProgress = Signal(float)
    sigFinished = Signal(object)
    sigFailed = Signal(str)

    def __init__(self, path, templates, settings, channel, token):
        super().__init__()
        self.path = path
        self.templates = templates
        self.settings = settings
        self.channel = channel
        self.token = token

    def run(self) -> None:
        from audian.tasks.tokens import Cancelled

        try:
            found = _sweep(self.path, self.templates, self.settings,
                           self.channel, self.token, self.sigProgress.emit)
        except Cancelled:
            self.sigFinished.emit(None)
        except Exception as exc:  # noqa: BLE001 - a plugin may not take the app down
            self.sigFailed.emit(str(exc))
        else:
            self.sigFinished.emit(found)


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
        #: what the threshold was last fitted for, so a fresh source is
        #: fitted once and a hand-moved slider is then left alone
        self._calibrated_for = None
        #: `LabelSet.revision` the category list was last built from
        self._categories_at = None
        #: whether a Run finished; an uncommitted preview is taken back off
        #: the recording when the reader looks away
        self._committed = False
        self._thread = None
        self._worker = None
        self._token = None

        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(theme.S6)
        group = ParameterGroup("Detector", self, caption=False, narrow=True)
        self.group = group

        self.sourcew = narrow_combo(QComboBox(self))
        self.sourcew.setToolTip("Label category to learn the examples from")
        self.sourcew.currentIndexChanged.connect(self._invalidate)
        group.add_row("Learn from", "", self.sourcew)

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

        self.statusw = QLabel("", self)
        self.statusw.setWordWrap(True)
        group.add_span_row(self.statusw)

        buttons = QWidget(self)
        row = QHBoxLayout(buttons)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.S4)
        self.calibratew = QPushButton("Fit to examples", buttons)
        self.calibratew.setToolTip(
            "Set the threshold just under the weakest example. The setting "
            "that suits one category rarely suits another."
        )
        self.calibratew.clicked.connect(self._calibrate)
        row.addWidget(self.calibratew, 1)
        self.runw = QPushButton("Run", buttons)
        self.runw.setToolTip("Apply these settings to the whole recording")
        self.runw.clicked.connect(self._run_clicked)
        row.addWidget(self.runw, 1)
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

    def hideEvent(self, event):  # noqa: N802 - Qt's spelling
        """Take an uncommitted preview back off the recording.

        A preview is written into the label set so that it is drawn by the
        same overlay as everything else, and this is the bill for that: the
        rows are really there, and audian saves the label set when anything
        else edits it.  A reader who glanced at this tab and moved on would
        find a category they never asked for in their sidecar.  So a preview
        lives exactly as long as the reader is looking at it; only a Run
        that finished is kept.
        """
        if not self._committed:
            self._clear_found()
        super().hideEvent(event)

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

    def _open(self) -> Optional[Recording]:
        if self.recording is not None:
            return self.recording
        path = getattr(getattr(self.browser, "data", None), "file_path", None)
        if not path:
            return None
        try:
            self.recording = Recording(path)
        except Exception as exc:  # noqa: BLE001 - a bad path is the reader's
            self._say(f"cannot read the recording: {exc}")
            return None
        return self.recording

    def closeEvent(self, event):  # noqa: N802 - Qt's spelling
        if self.recording is not None:
            self.recording.close()
            self.recording = None
        self._stop_sweep()
        super().closeEvent(event)

    # -- the controls ----------------------------------------------------

    def refresh_categories(self) -> None:
        """Offer every category the reader has actually marked something in.

        A category with no spans in it cannot teach the detector anything,
        and offering it would only let the reader select a source that can
        never work.

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
            if not label.is_point() and not label.category.endswith(DETECTED_SUFFIX):
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
        if not counts:
            self._say("draw a few examples in a label category first")

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
        self._schedule()

    def _k_typed(self, spin) -> None:
        if self._writing:
            return
        self._writing = True
        try:
            self.sensitivityw.setValue(
                int(round(detection.sensitivity_from_k(spin.value(), self._domain()))))
        finally:
            self._writing = False
        self._schedule()

    # -- the settings ----------------------------------------------------

    def settings(self) -> detection.Settings:
        return detection.Settings(
            domain=self._domain(),
            representation=self.representationw.currentData() or "pcen",
            combiner=self.combinerw.currentData() or detection.DEFAULT_COMBINER,
            k=float(self.kw.value()),
        ).normalized()

    def examples(self) -> list:
        """The marked spans of the chosen category, as the engine wants them."""
        labels = getattr(self.browser, "labels", None)
        name = self.sourcew.currentData()
        if labels is None or not name:
            return []
        return [detection.Example(label.t0, label.t_end(), label.f0, label.f1)
                for label in labels
                if label.category == name and not label.is_point()]

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
        self._schedule()

    def _schedule(self) -> None:
        if self._ready and not self.isHidden():
            self._debounce.start()

    def _learn(self):
        """Cut the templates, reading the examples out of the file."""
        recording = self._open()
        examples = self.examples()
        if recording is None or not examples:
            return None
        settings = self.settings()
        margin = 4096 / recording.rate + max(e.t1 - e.t0 for e in examples)
        lo = max(min(e.t0 for e in examples) - margin, 0.0)
        hi = min(max(e.t1 for e in examples) + margin, recording.duration)
        block, t0 = recording.samples(lo, hi, self._channel())
        if not block.size:
            return None
        return detection.learn(block, recording.rate, examples, settings, t0)

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
        """
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

        source = (self.sourcew.currentData(), settings.domain,
                  settings.representation, settings.combiner)
        stamp = (*source, round(s0, 3), round(s1, 3))
        if stamp != self._scored_for:
            self.templates = self._learn()
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

        # A fresh source gets its threshold fitted once, without being
        # asked.  The right k for one template set is not the right k for
        # another -- eleven small templates and three large ones want cuts
        # a factor of two apart -- and a panel that opens showing nothing
        # teaches the reader that the detector does not work.
        if source != self._calibrated_for:
            self._calibrated_for = source
            fitted = detection.calibrate_k(self._score, self._times,
                                           self.examples(), self.templates)
            if fitted is not None:
                self._write_k(fitted)
                settings = self.settings()
        found = [c for c in detection.pick(self._score, self._times,
                                           self._level, self.templates, settings)
                 if t0 <= c.t0 < t1]
        self._draw(found)
        cut = detection.threshold_of(self._score, settings.k)
        self._say(f"{len(found)} in view · cut {cut:.3f} of "
                  f"{float(self._score.max()):.3f} · {len(self.templates)} examples")

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

    def _calibrate(self) -> None:
        """Put the cut just under the weakest example, and show where.

        The one setting a reader cannot guess: eleven small templates and
        three large ones want thresholds a factor of two apart, and the
        examples already say which.
        """
        if self._scored_for is None:
            self.preview()
        if self._score is None or self.templates is None:
            return
        k = detection.calibrate_k(self._score, self._times, self.examples(),
                                  self.templates)
        if k is None:
            self._say("nothing to calibrate against in this window")
            return
        self._write_k(k)
        # claim the fit, so the automatic one does not undo a deliberate one
        self._calibrated_for = (self.sourcew.currentData(), self._domain(),
                                self.representationw.currentData() or "pcen",
                                self.combinerw.currentData()
                                or detection.DEFAULT_COMBINER)
        self.preview()

    def _say(self, message: str) -> None:
        self.statusw.setText(message)

    # -- the whole recording ---------------------------------------------

    def _run_clicked(self) -> None:
        if self._thread is not None:
            self._stop_sweep()
            return
        recording = self._open()
        if recording is None:
            return
        if self.templates is None or not self.templates.ok:
            self.templates = self._learn()
        if self.templates is None or not self.templates.ok:
            self._say("no examples to learn from")
            return

        from audian.tasks.tokens import CancelToken

        self._token = CancelToken()
        self._worker = SweepWorker(recording.path, self.templates,
                                   self.settings(), self._channel(), self._token)
        self._thread = QThread(self)
        self._thread.setObjectName("audian-detector")
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.sigProgress.connect(self._progress)
        self._worker.sigFinished.connect(self._finished)
        self._worker.sigFailed.connect(self._failed)
        self.progressw.setValue(0)
        self.progressw.show()
        self.runw.setText("Stop")
        self._say(f"scanning {recording.duration:.0f} s ...")
        self._thread.start()

    def _progress(self, fraction: float) -> None:
        self.progressw.setValue(int(round(100 * fraction)))

    def _stop_sweep(self) -> None:
        if self._token is not None:
            self._token.cancel()

    def _teardown(self) -> None:
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(3000)
        self._thread = None
        self._worker = None
        self._token = None
        self.progressw.hide()
        self.runw.setText("Run")

    def _failed(self, message: str) -> None:
        self._teardown()
        self._say(f"the run failed: {message}")
        notify = getattr(self.browser, "notify", None)
        if notify is not None:
            notify("error", f"detector: {message}")

    def _finished(self, found) -> None:
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
            with path.open("w", encoding="utf-8") as fh:
                fh.write("category,kind,channel,t_start_s,t_end_s,"
                         "f_low_hz,f_high_hz,note\n")
                for candidate in found:
                    low = "" if candidate.f_low_hz is None else f"{candidate.f_low_hz:.3f}"
                    high = "" if candidate.f_high_hz is None else f"{candidate.f_high_hz:.3f}"
                    fh.write(f"{self._category_name()},{KIND_SPAN},{cell},"
                             f"{candidate.t0:.6f},{candidate.t1:.6f},"
                             f"{low},{high},score {candidate.score:.3f}\n")
        except OSError as exc:
            self._say(f"could not write the CSV: {exc}")
            return None
        return path


def audian_detector_panel(browser):
    """Register the Detector tab.

    The name is the whole interface: `Plugins.load_plugins` binds any
    callable in this module named ``audian_*`` that ends in ``panel``.
    """
    return "Detector", DetectorPanel(browser)
