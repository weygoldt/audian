"""The Frequency bands tab: where a reader curates what a tracker found.

**Plugins > Frequency bands** opens it.  Closing the tab turns the plugin off,
takes its marks off the lanes and stops its worker, the way the detector's
does.

What the reader does
--------------------

Find the bands, then fix them.  **Find in view** runs the tracker over what is
on screen, which is how the settings are tuned -- it is fast enough to run
again after every change.  **Find in recording** runs the same settings over
the whole file on a thread, with a progress bar and a Cancel that answers.  Or
**Import** reads a wavetracker output directory, which is the case this plugin
was written for: those bands already exist and only need curating.

Then the work itself, which is all mouse:

* **Click** a band to select it.  Selecting is what every button acts on, and
  the selected band is drawn thicker and in the accent colour -- thicker as
  well as coloured, so it is still obvious to a reader who cannot separate
  the accent from the palette.
* **Ctrl+click** adds a band to the selection, which is how two are chosen
  for a merge.
* **Right-click** opens a menu for the band under the cursor: split it there,
  delete it, label it, or merge it with what is already selected.  The menu
  is the discoverable half -- every action it offers is also a button here,
  and every button says in its tooltip what it will do to which band.

Every one of those is undoable, including the destructive ones.  That is the
whole difference in posture from `wavetracker.EODsorter`, where Delete and
Group Delete rewrote the identity array in place and the single Undo held one
step of history that several operations did not push onto at all.

Why there are no keyboard shortcuts
-----------------------------------

Because the work is a pointing task -- which band, at which moment -- and
because audian's window already answers 116 key sequences, checked against
``tests/data/action-inventory.json``.  A plugin that claimed ``X`` for split
would be taking a key the window already uses, and one that invented a safe
corner of the keyboard would be teaching a second vocabulary for a job the
mouse does better.  The buttons carry the discoverability instead.

Threading
---------

The sweep runs on a `QThread` this panel owns, not through
`tasks.TaskManager`: that manager's job signal is connected to every
registered worker, so a second kind of job would also be delivered to
audian's spectrogram worker.  The cancellation vocabulary is still audian's
`CancelToken`, because that part composes.  The recording is opened through
`open_files` rather than read from ``browser.data.buffer``, which is a window
that moves under a background reader.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from audian.pluginapi import (
    Cancelled,
    CancelToken,
    ParameterGroup,
    narrow_combo,
    open_files,
    theme,
)

from . import bands as B
from . import tracking as T
from .overlay import BandOverlay
from .wavetracker import import_directory

#: Seconds of recording per spectrogram chunk in a whole-file sweep.
#:
#: Long enough that a chunk contains both a signal and the silence around it
#: -- the floor `tracking.frame_peaks` needs is drawn per chunk, and a chunk
#: short enough to be entirely inside one call would take its floor from that
#: call and find nothing in it.  Short enough that the spectrogram of one
#: chunk is a few megabytes rather than the hour's hundreds.
CHUNK_S = 60.0

#: How wide the click tolerance is, in pixels.  A band is a 1.6 px line and
#: nobody can hit one; this is the radius within which a click counts as
#: aimed at it.
PICK_RADIUS_PX = 12.0

#: Idle time before an edit is written to the sidecar.
SAVE_DEBOUNCE_MS = 1500

DEFAULT_NFFT = 16384
DEFAULT_MAX_HZ = 2000.0


def open_recording(paths, tbuffer: float):
    """The recording, through audian's opener rather than `DataLoader`.

    A session is often several files joined by their timestamps, and the bare
    loader applies a continuity heuristic that silently drops the tail; see
    `audian.data.open_files`.  One path is passed as a path and several as a
    list, which is the shape the detector plugin passes too.
    """
    paths = list(paths)
    return open_files(paths if len(paths) > 1 else paths[0], tbuffer, 0.0)


class SweepWorker(QObject):
    """The whole-recording tracker, on a thread of its own."""

    sigProgress = Signal(int, str)
    sigDone = Signal(object, str)

    def __init__(self, paths, settings, channel, token) -> None:
        super().__init__()
        self.paths = list(paths)
        self.settings = dict(settings)
        self.channel = int(channel)
        self.token = token

    def run(self) -> None:
        try:
            frames = self._sweep()
        except Cancelled:
            self.sigDone.emit(None, "")
            return
        except Exception as exc:  # noqa: BLE001 - a worker must not take the window with it
            self.sigDone.emit(None, str(exc))
            return
        try:
            found = T.link(
                frames,
                self.settings["tolerance_hz"],
                self.settings["max_gap_s"],
                self.settings["min_duration_s"],
                self.token,
            )
        except Cancelled:
            self.sigDone.emit(None, "")
            return
        self.sigDone.emit(found, "")

    def _sweep(self) -> list:
        from thunderlab.powerspectrum import decibel, spectrogram

        frames: list = []
        with open_recording(self.paths, CHUNK_S) as data:
            rate = float(data.rate)
            total = len(data)
            nfft = int(self.settings["nfft"])
            step = int(CHUNK_S * rate)
            start = 0
            while start < total:
                self.token.check()
                stop = min(total, start + step)
                # A chunk shorter than the window cannot be transformed, and
                # a tail of a few samples is not a band anybody is missing.
                if stop - start < nfft:
                    break
                block = np.asarray(data[start:stop, self.channel], dtype=np.float64)
                freqs, times, spec = spectrogram(
                    block, rate, freq_resolution=rate / nfft, overlap_frac=0.75
                )
                keep = freqs <= float(self.settings["max_hz"])
                power = decibel(spec).T[:, keep]
                frames.extend(
                    T.peaks_of_block(
                        times + start / rate,
                        freqs[keep],
                        power,
                        self.settings["threshold_db"],
                        self.settings["max_peaks"],
                        None,
                        self.token,
                    )
                )
                start = stop
                done = int(100 * start / max(1, total))
                self.sigProgress.emit(
                    done, f"{start / rate:.0f} s of {total / rate:.0f} s"
                )
        return frames


class BandPanel(QWidget):
    """The Frequency bands tab."""

    def __init__(self, browser, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.browser = browser
        self.bands = B.BandSet()
        #: ``category -> palette index``, so a label is the same colour on
        #: every lane and in the table
        self.colors: dict = {}
        self.selection: list = []
        self.overlays: list = []
        self._scenes: list = []
        self._thread = None
        self._worker = None
        self._token = None
        self._loaded_for = None
        self._filling = False

        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(theme.S6)

        self._build_bands_group(box)
        self._build_find_group(box)
        box.addStretch(1)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(SAVE_DEBOUNCE_MS)
        self._save_timer.timeout.connect(self.save_now)

        if hasattr(browser, "sigRangesChanged"):
            browser.sigRangesChanged.connect(self._ranges_changed)

        self._refresh()

    # --- building ---------------------------------------------------------

    def _build_bands_group(self, box) -> None:
        group = ParameterGroup("Bands", self, caption=False, narrow=True)

        self.statusw = QLabel("", self)
        self.statusw.setFont(theme.font_mono(theme.SIZE_SMALL_PT))
        theme.tint(self.statusw, "fg.muted")
        self.statusw.setWordWrap(True)
        group.add_span_row(self.statusw)

        self.tablew = QTableWidget(0, 4, self)
        self.tablew.setHorizontalHeaderLabels(["#", "Label", "Start", "Hz"])
        self.tablew.verticalHeader().setVisible(False)
        self.tablew.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tablew.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.tablew.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tablew.setAlternatingRowColors(True)
        self.tablew.setMinimumHeight(theme.S12 * 10)
        header = self.tablew.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.tablew.setToolTip(
            "Every band in the recording. Click a row to select it on the "
            "spectrogram; double-click to move the view to it."
        )
        self.tablew.itemSelectionChanged.connect(self._table_selection_changed)
        self.tablew.itemDoubleClicked.connect(self._goto_row)
        group.add_span_row(self.tablew)

        self.labelw = narrow_combo(QComboBox(self))
        self.labelw.setEditable(True)
        self.labelw.setToolTip(
            "What the selected bands are. Type a new name or pick one that "
            "is already in use; Apply writes it to every selected band."
        )
        self.applyw = QPushButton("Apply", self)
        self.applyw.setToolTip("Give the selected bands this label")
        self.applyw.clicked.connect(self._apply_label)
        group.add_row("Label", "", ParameterGroup.expanding(self.labelw), self.applyw)

        edits = QWidget(self)
        row = QHBoxLayout(edits)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.S4)
        self.mergew = QPushButton("Merge", edits)
        self.mergew.setToolTip(
            "Join the selected bands into one, keeping every vertex. "
            "Select a second band with Ctrl+click."
        )
        self.mergew.clicked.connect(self._merge)
        row.addWidget(self.mergew, 1)
        self.deletew = QPushButton("Delete", edits)
        self.deletew.setToolTip("Remove the selected bands. Undo puts them back.")
        self.deletew.clicked.connect(self._delete)
        row.addWidget(self.deletew, 1)
        group.add_span_row(edits)

        history = QWidget(self)
        row = QHBoxLayout(history)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.S4)
        self.undow = QPushButton("Undo", history)
        self.undow.clicked.connect(self._undo)
        row.addWidget(self.undow, 1)
        self.redow = QPushButton("Redo", history)
        self.redow.clicked.connect(self._redo)
        row.addWidget(self.redow, 1)
        group.add_span_row(history)

        saves = QWidget(self)
        row = QHBoxLayout(saves)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.S4)
        self.autosavew = QCheckBox("Save automatically", saves)
        self.autosavew.setChecked(True)
        self.autosavew.setToolTip(
            "Write the sidecar a moment after each edit. Off means the only "
            "copy of an edit is in this window until Save is pressed."
        )
        row.addWidget(self.autosavew, 1)
        self.savew = QPushButton("Save", saves)
        self.savew.setToolTip("Write the bands beside the recording now")
        self.savew.clicked.connect(self.save_now)
        row.addWidget(self.savew)
        group.add_span_row(saves)

        box.addWidget(group)

    def _build_find_group(self, box) -> None:
        group = ParameterGroup("Find bands", self, caption=False, narrow=True)

        self.thresholdw = pg.SpinBox(
            self, T.DEFAULT_THRESHOLD_DB, bounds=(1.0, 60.0), suffix=" dB", step=1.0
        )
        self._style(self.thresholdw)
        self.thresholdw.setToolTip(
            "How far a peak must stand above the noise. Higher finds less; "
            "too low fills the silences with fragments."
        )
        group.add_row("Threshold", "", self.thresholdw)

        self.tolerancew = pg.SpinBox(
            self, 6.0, bounds=(0.1, 5000.0), suffix=" Hz", step=1.0
        )
        self._style(self.tolerancew)
        self.tolerancew.setToolTip(
            "How far a band may move between frames. Wider follows a rising "
            "frequency; narrower keeps two close bands apart."
        )
        group.add_row("Tolerance", "", self.tolerancew)

        self.gapw = pg.SpinBox(self, 1.0, bounds=(0.0, 600.0), suffix=" s", step=0.1)
        self._style(self.gapw)
        self.gapw.setToolTip(
            "How long a band may go unseen before it is ended rather than "
            "bridged across the silence."
        )
        group.add_row("Max gap", "", self.gapw)

        self.mindurw = pg.SpinBox(
            self, 1.0, bounds=(0.0, 3600.0), suffix=" s", step=0.1
        )
        self._style(self.mindurw)
        self.mindurw.setToolTip("Drop anything shorter than this")
        group.add_row("Min length", "", self.mindurw)

        self.maxhzw = pg.SpinBox(
            self, DEFAULT_MAX_HZ, bounds=(10.0, 200000.0), suffix=" Hz", step=100.0
        )
        self._style(self.maxhzw)
        self.maxhzw.setToolTip("Ignore everything above this frequency")
        group.add_row("Up to", "", self.maxhzw)

        self.channelw = narrow_combo(QComboBox(self))
        self.channelw.setToolTip(
            "Which electrode to track. A band is found in one channel's "
            "spectrogram and then drawn on every lane, because a band is a "
            "signal in the water rather than a property of the electrode "
            "that heard it best."
        )
        group.add_row("Channel", "", self.channelw)

        self.nfftw = narrow_combo(QComboBox(self))
        for n in (2048, 4096, 8192, 16384, 32768):
            self.nfftw.addItem(str(n), n)
        self.nfftw.setCurrentText(str(DEFAULT_NFFT))
        self.nfftw.setToolTip(
            "Window length of the tracker's own spectrogram. Longer separates "
            "two close frequencies and blurs a fast change; this is not the "
            "spectrogram you are looking at."
        )
        group.add_row("Window", "", self.nfftw)

        buttons = QWidget(self)
        row = QHBoxLayout(buttons)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.S4)
        self.findvieww = QPushButton("Find in view", buttons)
        self.findvieww.setToolTip(
            "Track what is on screen. Fast, and the way to tune the settings "
            "before running the whole recording."
        )
        self.findvieww.clicked.connect(self._find_in_view)
        row.addWidget(self.findvieww, 1)
        self.findallw = QPushButton("Find in recording", buttons)
        self.findallw.setToolTip("Track the whole file with these settings")
        self.findallw.clicked.connect(self._find_in_recording)
        row.addWidget(self.findallw, 1)
        group.add_span_row(buttons)

        self.progressw = QProgressBar(self)
        self.progressw.setRange(0, 100)
        self.progressw.hide()
        group.add_span_row(self.progressw)

        self.cancelw = QPushButton("Cancel", self)
        self.cancelw.clicked.connect(self._cancel)
        self.cancelw.hide()
        group.add_span_row(self.cancelw)

        self.importw = QPushButton("Import wavetracker output…", self)
        self.importw.setToolTip(
            "Read a wavetracker directory of .npy files. They are opened "
            "read-only and never written back; edits go to this recording's "
            "own sidecar."
        )
        self.importw.clicked.connect(self._import)
        group.add_span_row(self.importw)

        box.addWidget(group)

    def _style(self, spin) -> None:
        if hasattr(self.browser, "style_parameter_spinbox"):
            self.browser.style_parameter_spinbox(spin)

    # --- lifecycle --------------------------------------------------------

    def showEvent(self, event):  # noqa: N802 - Qt's spelling
        super().showEvent(event)
        self.attach()
        self.fill_channels()
        self.load_for_recording()

    def fill_channels(self) -> None:
        """List the recording's channels, keeping the reader's choice.

        Filled here rather than in the constructor because a panel is built
        once and a recording is opened many times; a combo listing four
        electrodes while a one-channel file is open is an offer that cannot
        be taken.
        """
        channels = int(getattr(getattr(self.browser, "data", None), "channels", 1) or 1)
        if self.channelw.count() == channels:
            return
        wanted = self.channelw.currentData()
        self.channelw.blockSignals(True)
        self.channelw.clear()
        for c in range(channels):
            self.channelw.addItem(f"{c:02d}", c)
        if wanted is not None and 0 <= int(wanted) < channels:
            self.channelw.setCurrentIndex(int(wanted))
        self.channelw.blockSignals(False)

    def channel(self) -> int:
        chosen = self.channelw.currentData()
        return 0 if chosen is None else int(chosen)

    def closeEvent(self, event):  # noqa: N802 - Qt's spelling
        """Stop the worker and take the marks off the lanes.

        Closing the tab is how a reader turns this plugin off, so what it
        drew has to go with it -- a plugin that leaves its bands on the
        spectrogram has left marks the reader can no longer reach a control
        for.
        """
        self._cancel()
        if self.bands.is_dirty():
            self.save_now()
        self.detach()
        super().closeEvent(event)

    def attach(self) -> None:
        """Put an overlay on every spectrogram lane, once."""
        if self.overlays:
            return
        axes = []
        if hasattr(self.browser, "spectrogram_axes"):
            axes = self.browser.spectrogram_axes()
        for ax in axes:
            self.overlays.append(BandOverlay(ax, self.bands, self.colors))
            scene = ax.scene()
            if scene is not None and scene not in self._scenes:
                # One connection per scene, not per lane: the scene is shared
                # by every lane in the window, so connecting from each
                # overlay would deliver every click to all of them.
                scene.sigMouseClicked.connect(self._scene_clicked)
                self._scenes.append(scene)
        if not axes:
            self.browser.notify(
                "warning",
                "frequency bands: this recording has no spectrogram panel to "
                "draw on; turn one on to see the bands",
            )

    def detach(self) -> None:
        for scene in self._scenes:
            try:
                scene.sigMouseClicked.disconnect(self._scene_clicked)
            except (RuntimeError, TypeError):
                pass
        self._scenes = []
        for overlay in self.overlays:
            overlay.detach()
        self.overlays = []

    def recording_path(self) -> Optional[Path]:
        path = getattr(getattr(self.browser, "data", None), "file_path", None)
        return Path(path) if path else None

    def source_paths(self) -> list:
        """The complete ordered file list behind the browser's timeline.

        Not `Data.file_path`, which is the anchor a sidecar is named after
        and becomes the *first* path once a split recording is open --
        `data.py` reduces a list to its first element on the way in.  A
        sweep taking it would silently track file one of four and report the
        result as the whole recording, which is the same class of failure
        `open_files` exists to prevent.  The live loader keeps the full list;
        the detector plugin reads it the same way.
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

    def load_for_recording(self) -> None:
        """Read the sidecar of whichever recording is open, once per file."""
        path = self.recording_path()
        if path is None or path == self._loaded_for:
            return
        self._loaded_for = path
        loaded, complaints = B.read(path)
        for complaint in complaints:
            self.browser.notify("warning", f"frequency bands: {complaint}")
        if len(loaded):
            self._replace_bands(loaded, f"loaded {len(loaded)} bands")
        else:
            self._refresh()

    # --- the band set -----------------------------------------------------

    def _replace_bands(self, bandset, what: str) -> None:
        self.bands = bandset
        self.selection = []
        self._sync_colors()
        for overlay in self.overlays:
            overlay.set_bands(self.bands)
        self._refresh()
        if what:
            self.browser.notify("info", f"frequency bands: {what}")

    def _sync_colors(self) -> None:
        """Give every label a palette index, keeping the ones already given.

        Stable across a session: a category keeps the colour it was first
        given even when an earlier one is deleted, because a band changing
        colour is read as the band changing, and nothing about it did.
        """
        for name in self.bands.categories():
            if name not in self.colors:
                self.colors[name] = len(self.colors)

    def _changed(self, edit) -> None:
        """After an edit: recolour, redraw, retable, and schedule a save."""
        self._sync_colors()
        self._refresh()
        for overlay in self.overlays:
            overlay.invalidate()
            overlay.update_plot()
        if edit is not None and edit.what:
            self.browser.notify("info", f"frequency bands: {edit.what}")
        if self.autosavew.isChecked():
            self._save_timer.start()

    # --- interface state --------------------------------------------------

    def _refresh(self) -> None:
        n = len(self.bands)
        labelled = sum(1 for b in self.bands if b.category)
        state = "unsaved" if self.bands.is_dirty() else "saved"
        selected = (
            f" · {len(self.selection)} selected" if self.selection else ""
        )
        self.statusw.setText(
            f"{n} band{'' if n == 1 else 's'} · {labelled} labelled{selected} · {state}"
        )
        self.undow.setEnabled(self.bands.can_undo())
        self.undow.setToolTip(
            f"Undo {self.bands.undo_text()}" if self.bands.can_undo() else "Nothing to undo"
        )
        self.redow.setEnabled(self.bands.can_redo())
        self.redow.setToolTip(
            f"Redo {self.bands.redo_text()}" if self.bands.can_redo() else "Nothing to redo"
        )
        self.mergew.setEnabled(len(self.selection) >= 2)
        self.deletew.setEnabled(bool(self.selection))
        self.applyw.setEnabled(bool(self.selection))
        self._fill_label_choices()
        self._fill_table()

    def _fill_label_choices(self) -> None:
        current = self.labelw.currentText()
        names = self.bands.categories()
        if [self.labelw.itemText(i) for i in range(self.labelw.count())] == names:
            return
        self.labelw.blockSignals(True)
        self.labelw.clear()
        self.labelw.addItems(names)
        self.labelw.setEditText(current)
        self.labelw.blockSignals(False)

    def _fill_table(self) -> None:
        self._filling = True
        try:
            rows = list(self.bands)
            self.tablew.setRowCount(len(rows))
            for r, band in enumerate(rows):
                cells = (
                    str(band.bid),
                    band.category,
                    f"{band.t0:.1f}",
                    f"{np.median(band.freqs):.1f}",
                )
                for c, text in enumerate(cells):
                    item = self.tablew.item(r, c)
                    if item is None:
                        item = QTableWidgetItem()
                        item.setFlags(
                            Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                        )
                        self.tablew.setItem(r, c, item)
                    item.setText(text)
                    if c == 1 and band.category:
                        item.setForeground(
                            theme.qcolor(
                                theme.marker_color(self.colors.get(band.category, 0))
                            )
                        )
            self._select_rows()
        finally:
            self._filling = False

    def _select_rows(self) -> None:
        chosen = set(self.selection)
        self.tablew.clearSelection()
        for r, band in enumerate(self.bands):
            if band.bid in chosen:
                self.tablew.selectRow(r)

    def _table_selection_changed(self) -> None:
        if self._filling:
            return
        rows = {i.row() for i in self.tablew.selectedIndexes()}
        ids = self.bands.ids()
        self.set_selection([ids[r] for r in sorted(rows) if 0 <= r < len(ids)])

    def _goto_row(self, item) -> None:
        ids = self.bands.ids()
        row = item.row()
        if not 0 <= row < len(ids):
            return
        band = self.bands.get(ids[row])
        if band is None or not hasattr(self.browser, "set_times"):
            return
        span = max(1.0, band.t1 - band.t0)
        self.browser.set_times(max(0.0, band.t0 - 0.05 * span), span * 1.1)

    def set_selection(self, ids) -> None:
        selection = sorted({int(i) for i in ids if int(i) in self.bands})
        if selection == self.selection:
            return
        self.selection = selection
        for overlay in self.overlays:
            overlay.set_selection(selection)
        self._refresh()

    # --- the mouse --------------------------------------------------------

    def _lane_at(self, scene_pos):
        """The overlay whose lane contains a scene position, and the point."""
        for overlay in self.overlays:
            view = overlay.ax.getViewBox()
            if view is None or not view.sceneBoundingRect().contains(scene_pos):
                continue
            point = view.mapSceneToView(scene_pos)
            return overlay, float(point.x()), float(point.y())
        return None, 0.0, 0.0

    def _tolerances(self, overlay) -> tuple:
        """`PICK_RADIUS_PX` in data units, on this lane, right now."""
        view = overlay.ax.getViewBox()
        (x0, x1), (y0, y1) = overlay.view_range()
        rect = view.boundingRect()
        wide = max(1.0, rect.width())
        high = max(1.0, rect.height())
        return (
            abs(x1 - x0) * PICK_RADIUS_PX / wide,
            abs(y1 - y0) * PICK_RADIUS_PX / high,
        )

    def _scene_clicked(self, event) -> None:
        if not self.overlays:
            return
        overlay, t, f = self._lane_at(event.scenePos())
        if overlay is None:
            return
        tol_t, tol_f = self._tolerances(overlay)
        hit = overlay.band_near(t, f, tol_t, tol_f)
        if event.button() == Qt.MouseButton.RightButton:
            event.accept()
            self._context_menu(event.screenPos(), hit, t)
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if hit is None:
            # A click on empty spectrogram clears the selection, which is
            # the ordinary meaning of clicking away from a thing.  It does
            # not accept the event: a drag that begins there is still the
            # lane's own rubber band.
            if event.modifiers() == Qt.KeyboardModifier.NoModifier:
                self.set_selection([])
            return
        event.accept()
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            chosen = list(self.selection)
            if hit in chosen:
                chosen.remove(hit)
            else:
                chosen.append(hit)
            self.set_selection(chosen)
        else:
            self.set_selection([hit])

    def _context_menu(self, screen_pos, hit, t: float) -> None:
        menu = QMenu(self)
        if hit is None:
            menu.addAction("No band here").setEnabled(False)
        else:
            band = self.bands.get(hit)
            if hit not in self.selection:
                menu.addAction(
                    f"Select band {hit}", lambda: self.set_selection([hit])
                )
            act = menu.addAction(f"Split band {hit} at {t:.3f} s")
            act.triggered.connect(lambda _=False: self._split(hit, t))
            act.setEnabled(band is not None and band.t0 < t < band.t1)
            others = [i for i in self.selection if i != hit]
            if others:
                named = ", ".join(str(i) for i in others)
                menu.addAction(
                    f"Merge band {hit} with {named}",
                    lambda: self._merge_ids([hit, *others]),
                )
            label_menu = menu.addMenu("Label")
            for name in self.bands.categories():
                label_menu.addAction(
                    name, lambda checked=False, n=name: self._label_ids([hit], n)
                )
            if self.bands.categories():
                label_menu.addSeparator()
            label_menu.addAction(
                "Clear label", lambda: self._label_ids([hit], "")
            )
            menu.addSeparator()
            menu.addAction(f"Delete band {hit}", lambda: self._delete_ids([hit]))
        menu.exec(screen_pos.toPoint())

    # --- the edits --------------------------------------------------------

    def _split(self, bid: int, t: float) -> None:
        try:
            edit = self.bands.split(bid, t)
        except (KeyError, ValueError) as exc:
            self.browser.notify("warning", f"frequency bands: {exc}")
            return
        self.set_selection([b.bid for b in edit.added])
        self._changed(edit)

    def _merge(self) -> None:
        self._merge_ids(self.selection)

    def _merge_ids(self, ids) -> None:
        ids = [int(i) for i in ids if int(i) in self.bands]
        if len(ids) < 2:
            self.browser.notify(
                "warning",
                "frequency bands: a merge needs two bands; Ctrl+click a "
                "second one",
            )
            return
        chosen = [self.bands.get(i) for i in ids]
        for note in B.merge_conflicts(chosen):
            self.browser.notify("info", f"frequency bands: {note}")
        edit = self.bands.merge(ids)
        self.set_selection([b.bid for b in edit.added])
        self._changed(edit)

    def _delete(self) -> None:
        self._delete_ids(self.selection)

    def _delete_ids(self, ids) -> None:
        ids = [int(i) for i in ids if int(i) in self.bands]
        if not ids:
            return
        edit = self.bands.delete_many(ids)
        self.set_selection([])
        self._changed(edit)

    def _apply_label(self) -> None:
        self._label_ids(self.selection, self.labelw.currentText())

    def _label_ids(self, ids, name: str) -> None:
        ids = [int(i) for i in ids if int(i) in self.bands]
        if not ids:
            return
        edit = None
        for bid in ids:
            edit = self.bands.set_category(bid, name)
        self._changed(edit)

    def _undo(self) -> None:
        edit = self.bands.undo()
        if edit is None:
            return
        self.set_selection([])
        self._sync_colors()
        self._refresh()
        for overlay in self.overlays:
            overlay.invalidate()
            overlay.update_plot()
        self.browser.notify("info", f"frequency bands: undid {edit.what}")
        if self.autosavew.isChecked():
            self._save_timer.start()

    def _redo(self) -> None:
        edit = self.bands.redo()
        if edit is None:
            return
        self.set_selection([])
        self._sync_colors()
        self._refresh()
        for overlay in self.overlays:
            overlay.invalidate()
            overlay.update_plot()
        self.browser.notify("info", f"frequency bands: redid {edit.what}")
        if self.autosavew.isChecked():
            self._save_timer.start()

    # --- saving -----------------------------------------------------------

    def save_now(self) -> None:
        path = self.recording_path()
        if path is None:
            return
        try:
            csv_file, _ = B.write(self.bands, path)
        except OSError as exc:
            self.browser.notify("error", f"frequency bands: could not save ({exc})")
            return
        self._refresh()
        self.browser.notify("info", f"frequency bands: saved {csv_file.name}")

    # --- finding ----------------------------------------------------------

    def _settings(self) -> dict:
        return {
            "threshold_db": float(self.thresholdw.value()),
            "tolerance_hz": float(self.tolerancew.value()),
            "max_gap_s": float(self.gapw.value()),
            "min_duration_s": float(self.mindurw.value()),
            "max_hz": float(self.maxhzw.value()),
            "max_peaks": T.DEFAULT_MAX_PEAKS,
            "nfft": int(self.nfftw.currentData() or DEFAULT_NFFT),
        }

    def _visible_window(self) -> Optional[tuple]:
        for overlay in self.overlays:
            (x0, x1), _ = overlay.view_range()
            if x1 > x0:
                return (x0, x1)
        return None

    def _find_in_view(self) -> None:
        from thunderlab.powerspectrum import decibel, spectrogram

        window = self._visible_window()
        paths = self.source_paths()
        if window is None or not paths:
            self.browser.notify(
                "warning", "frequency bands: nothing on screen to track"
            )
            return
        settings = self._settings()
        t0, t1 = window
        try:
            with open_recording(paths, max(CHUNK_S, t1 - t0)) as data:
                rate = float(data.rate)
                start = max(0, int(t0 * rate))
                stop = min(len(data), int(t1 * rate))
                if stop - start < settings["nfft"]:
                    self.browser.notify(
                        "warning",
                        "frequency bands: the visible window is shorter than "
                        f"the {settings['nfft']} sample tracking window; zoom "
                        "out or choose a shorter window",
                    )
                    return
                block = np.asarray(
                    data[start:stop, self.channel()], dtype=np.float64
                )
            freqs, times, spec = spectrogram(
                block, rate, freq_resolution=rate / settings["nfft"], overlap_frac=0.75
            )
            keep = freqs <= settings["max_hz"]
            power = decibel(spec).T[:, keep]
            found = T.track(
                times + start / rate,
                freqs[keep],
                power,
                settings["threshold_db"],
                settings["tolerance_hz"],
                settings["max_gap_s"],
                settings["min_duration_s"],
                settings["max_peaks"],
            )
        except Exception as exc:  # noqa: BLE001 - report, do not take the window down
            self.browser.notify("error", f"frequency bands: tracking failed ({exc})")
            return
        self._add_found(found, f"{t0:.1f}-{t1:.1f} s")

    def _add_found(self, found, where: str) -> None:
        if not found:
            self.browser.notify(
                "info",
                f"frequency bands: nothing found in {where}; lower the "
                "threshold or widen the tolerance",
            )
            return
        edit = self.bands.add_many(found, f"add {len(found)} bands from {where}")
        self._changed(edit)

    def _find_in_recording(self) -> None:
        if self._thread is not None:
            return
        paths = self.source_paths()
        if not paths:
            self.browser.notify("warning", "frequency bands: no recording is open")
            return
        self._token = CancelToken()
        self._worker = SweepWorker(
            paths, self._settings(), self.channel(), self._token
        )
        self._thread = QThread(self)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.sigProgress.connect(self._progress)
        self._worker.sigDone.connect(self._sweep_done)
        self.progressw.setValue(0)
        self.progressw.show()
        self.cancelw.show()
        self.findallw.setEnabled(False)
        self.findvieww.setEnabled(False)
        self._thread.start()

    def _progress(self, done: int, text: str) -> None:
        self.progressw.setValue(int(done))
        self.progressw.setFormat(f"{text} (%p%)")

    def _sweep_done(self, found, error: str) -> None:
        self._stop_thread()
        if error:
            self.browser.notify("error", f"frequency bands: {error}")
            return
        if found is None:
            self.browser.notify("info", "frequency bands: tracking cancelled")
            return
        self._add_found(found, "the whole recording")

    def _cancel(self) -> None:
        if self._token is not None:
            self._token.cancel()
        self._stop_thread()

    def _stop_thread(self) -> None:
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(5000)
            self._thread = None
        self._worker = None
        self.progressw.hide()
        self.cancelw.hide()
        self.findallw.setEnabled(True)
        self.findvieww.setEnabled(True)

    # --- importing --------------------------------------------------------

    def _import(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "wavetracker output directory"
        )
        if not folder:
            return
        try:
            found, complaints = import_directory(Path(folder))
        except Exception as exc:  # noqa: BLE001 - somebody else's files
            self.browser.notify("error", f"frequency bands: import failed ({exc})")
            return
        for complaint in complaints:
            self.browser.notify("warning", f"frequency bands: {complaint}")
        if not found:
            self.browser.notify(
                "warning", f"frequency bands: no bands found in {folder}"
            )
            return
        edit = self.bands.add_many(
            found, f"import {len(found)} bands from {Path(folder).name}"
        )
        self._changed(edit)

    # --- view -------------------------------------------------------------

    def _ranges_changed(self, *args) -> None:
        for overlay in self.overlays:
            overlay.update_plot()
