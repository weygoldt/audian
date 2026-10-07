"""The Tracks tab: run wavetracker, see its tracks, correct them.

**Plugins > Wavetracker** opens it (design sections 4, 5 and 8).  Top to
bottom:

1. **Session**: the results directory, the unsaved marker, Save and a menu
   (Open results…, Save as…, Revert to tracker output…, Show in file
   manager); Track visible / Track recording, then Clean up… (the step after
   tracking), with a progress bar and Cancel while a job runs.
2. **Run settings** (folded after the first run): the wavetracker status
   line, the fish frequency range, device, config file and an Advanced
   disclosure.
3. **Snippet** (only while a provisional snippet is shown): Accept, Discard.
4. **Edit**: the Edit tracks toggle, the tool row, the hint line, the brush
   and display options, the Keys list.
5. **Tracks**: a sortable table that follows the selection.
6. **Issues**: what `G` visits.
7. **History**: every edit, undo/redo, click to jump.

The panel is the single place `TrackSet.apply` is called (`apply_plan`):
every tool, menu, key and button hands it a plan.  After each change it
notes the change in the render cache, redraws the overlays, refreshes the
hover and the tables, flashes what changed and arms the autosave.

Deviations from the design, both forced by the host: the hint line sits
under the tool row rather than pinned at the bottom (audian's plugin scroll
area gives a panel a fixed height, so nothing can stay pinned), and the
hover label box on the lane repeats it next to the pointer; results are
loaded on the GUI thread (a 1M-row directory loads in well under the 1.5 s
budget, so a worker was not worth its cancellation paths).
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import numpy as np
from PySide6.QtCore import (
    QAbstractTableModel,
    QEvent,
    QModelIndex,
    QSortFilterProxyModel,
    Qt,
    QThread,
    QTimer,
    Signal,
)
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QTabWidget,
    QTableView,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from audian.pluginapi import CancelToken, ParameterGroup, narrow_combo, theme

from . import geometry as G
from . import harmonics as HM
from . import model as M
from . import runner as R
from .overlay import TrackOverlay, view_of
from .ridgeadd import RidgeSource
from .tools import (
    BRUSH_MAX,
    BRUSH_MIN,
    KEY_HELP,
    CentreSource,
    FineSpecSource,
    KeyRouter,
    ToolController,
    ToolSurface,
)

#: The key of this plugin's preferences in audian's settings store (4.1).
SETTINGS_KEY = "eodsorter"
SETTINGS_VERSION = 1
# There is no "python" key any more: wavetracker is installed with audian
# and runs under its interpreter.  `load_prefs` keeps only the keys listed
# here, so a stale "python" from an older version is dropped on the next save.
DEFAULT_PREFS = {
    "version": SETTINGS_VERSION,
    "device": "auto",
    "brush_px": 14,
    "sticky_brush": True,
    "ridge_add": True,
    "show_unassigned": True,
    "point_px": 3,
    "gap_break_s": 0.5,
    "show_ids": True,
    "dim_spec": False,
    "fish_range": [],
    "folded": {},
    "results_dirs": {},
}

AUTOSAVE_MS = 2000
TABLE_VIEW_MS = 200
LANE_POLL_MS = 1000

TOOLS = (
    (
        "V",
        "⌖",
        "Select",
        "Select tracks (click) or points (brush). Shift adds, Ctrl removes.",
    ),
    (
        "E",
        "⌫",
        "Erase",
        "Unassign the point under the pointer, or every point the brush paints.",
    ),
    (
        "C",
        "✂",
        "Cut",
        "Cut a track where you click; drag a line to cut every track it crosses.",
    ),
    (
        "M",
        "⋈",
        "Merge",
        "Click the track to merge into, then each piece to join to it.",
    ),
    (
        "A",
        "⇥",
        "Assign",
        "Assign the selected points to the track you click, or brush points onto a target.",
    ),
    ("F", "✚", "Add", "Paint along a missed track to add detections there."),
)

ISSUE_KINDS = (
    ("join", "possible joins"),
    ("gap", "gaps"),
    ("short", "short tracks"),
    ("crossing", "crossings"),
    ("harmonic", "harmonics"),
)

#: a harmonic check of the ids an edit gave rows to (5.13) runs in the GUI
#: thread when it covers at most this many rows (about 10 ms: iriri, 150,000
#: rows in 18 ms), else in a worker thread
HARMONIC_SYNC_ROWS = 60_000
#: how the warning names the edit Ctrl+Z undoes, by plan kind
GROW_VERB = {
    "add": "add",
    "new_id": "new id",
    "assign": "assign",
    "merge": "merge",
    "replace_span": "accept",
}


# ---------------------------------------------------------------- settings


def load_prefs() -> dict:
    """The plugin's preferences, with defaults for what is missing."""
    prefs = dict(DEFAULT_PREFS)
    try:
        from audian.pluginapi import settings

        stored = settings().get(SETTINGS_KEY)
    except ImportError:  # an audian without the re-export (design 4.1)
        from PySide6.QtCore import QSettings

        import json

        raw = QSettings("audian", SETTINGS_KEY).value("prefs", "")
        try:
            stored = json.loads(raw) if raw else None
        except ValueError:
            stored = None
    if isinstance(stored, dict) and stored.get("version") == SETTINGS_VERSION:
        for key, value in stored.items():
            if key in prefs and isinstance(value, type(prefs[key])):
                prefs[key] = value
            elif (
                key in prefs
                and isinstance(prefs[key], float)
                and isinstance(value, int)
            ):
                prefs[key] = float(value)
    return prefs


def save_prefs(prefs: dict) -> None:
    try:
        from audian.pluginapi import save_setting

        save_setting(SETTINGS_KEY, dict(prefs))
    except ImportError:
        import json

        from PySide6.QtCore import QSettings

        QSettings("audian", SETTINGS_KEY).setValue("prefs", json.dumps(prefs))


def cache_dir() -> Path:
    """Where the autosave of a session with no results directory goes."""
    try:
        from audian.pluginapi import cache_path

        base = cache_path()
    except ImportError:  # an audian without it
        try:
            from platformdirs import user_cache_path

            base = user_cache_path("audian")
        except ImportError:  # pragma: no cover
            base = Path.home() / ".cache" / "audian"
    return base / "eodsorter"


def session_channels(ts, n_channels: Optional[int]) -> Optional[list]:
    """The recording channels the session's electrode columns are, for the
    runner's ``peaks`` op: the results' ``channels``, else every channel but
    the configured ``exclude_channels``; None (all) when neither is known.
    Without this an Add on results with excluded electrodes returned a power
    column per recording channel and every stroke was rejected."""
    meta = ts.meta or {}
    chans = meta.get("channels")
    if isinstance(chans, (list, tuple)) and chans:
        try:
            out = [int(c) for c in chans]
        except (TypeError, ValueError):
            out = None
        if out is not None and (ts.n_channels is None or len(out) == ts.n_channels):
            return out
    cfg = meta.get("config") or {}
    spec = cfg.get("spectrogram") if isinstance(cfg, dict) else None
    excl = (spec or {}).get("exclude_channels") or []
    if excl and n_channels:
        try:
            excl = {int(c) for c in excl}
        except (TypeError, ValueError):
            return None
        return [c for c in range(int(n_channels)) if c not in excl]
    return None


_ORPHANS: list = []


def orphan_thread(thread, worker=None) -> None:
    """Keep a still running `QThread` (and its worker) alive past its
    parent, and let it go once it has finished."""
    from PySide6.QtCore import QCoreApplication

    thread.setParent(None)
    app = QCoreApplication.instance()
    if app is not None:
        thread.setParent(app)
    entry = (thread, worker)
    _ORPHANS.append(entry)

    def gone():
        if entry in _ORPHANS:
            _ORPHANS.remove(entry)
        thread.deleteLater()

    thread.finished.connect(gone)
    thread.quit()


def cleanup_refusal(message) -> Optional[str]:
    """wavetracker cleanup's own refusal (a ValueError naming ``cleanup:``)
    as a sentence for the reader, or None for any other error."""
    lines = [x.strip() for x in str(message).strip().splitlines() if x.strip()]
    for line in reversed(lines):
        head, _, rest = line.partition(": ")
        if head == "ValueError" and rest.startswith("cleanup:"):
            rest = rest[len("cleanup:") :].strip()
            rest = rest.replace("--stride/--freq-tol", "stride / freq tolerance")
            return f"Clean up found nothing to keep: {rest}"
    return None


def rebase_ident(result, base, current):
    """An asynchronous whole-session result (cleanup) onto edits made while
    it ran: `result` was computed from `base`; rows whose identity changed
    since (`current` differs from `base`) keep the current one, and rows
    appended since are left as they are.  Returns (identities for all of
    `current`, number of rows that kept an edit)."""
    result = np.asarray(result, dtype=np.float64)
    base = np.asarray(base, dtype=np.float64)
    current = np.asarray(current, dtype=np.float64)
    out = current.copy()
    m = len(base)
    same = (current[:m] == base) | (np.isnan(current[:m]) & np.isnan(base))
    out[:m] = np.where(same, result, current[:m])
    return out, int((~same).sum())


def autosave_cache(recording: str) -> Path:
    """Where the autosave of a session without results directory goes: one
    directory per recording under `cache_dir`."""
    import hashlib

    key = hashlib.sha1(os.fspath(recording).encode("utf-8", "replace")).hexdigest()
    return cache_dir() / key[:16]


def elide_middle(text: str, widget: QWidget, width: int) -> str:
    metrics = QFontMetrics(widget.font())
    return metrics.elidedText(text, Qt.TextElideMode.ElideMiddle, max(40, width))


# ---------------------------------------------------------------- tables


class Section(QWidget):
    """A `ParameterGroup` under a header that folds it away.

    The panel lives in the side bar's scroll area, and unfolded it is three
    screens long; the parts used while editing stay open and the rest can be
    folded, which is remembered (``folded`` in the preferences).
    """

    sigFolded = Signal(str, bool)

    def __init__(self, title: str, parent=None, folded: bool = False) -> None:
        super().__init__(parent)
        self.title = title
        self._open = not folded
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(theme.S2)
        self.header = QToolButton(self)
        self.header.setText(title.upper())
        self.header.setAutoRaise(True)
        self.header.setFont(theme.font_ui(theme.SIZE_SMALL_PT))
        theme.tint(self.header, "fg.muted")
        self.header.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.header.setToolTip(f"Show or hide {title.lower()}")
        # a disclosure, not a button: no frame, the caption's own colour
        self.header.setStyleSheet(
            "QToolButton { border: none; background: transparent; padding: 1px 0px; }"
        )
        self.header.clicked.connect(lambda: self.set_open(not self._open))
        line = QHBoxLayout()
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(theme.S4)
        line.addWidget(self.header, 0)
        line.addStretch(1)
        #: widgets shown at the right of the header (a summary)
        self.extra = line
        box.addLayout(line)
        self.group = ParameterGroup(title, self, caption=False, narrow=True)
        box.addWidget(self.group)
        self._show()

    def _show(self) -> None:
        self.group.setVisible(self._open)
        self.header.setArrowType(
            Qt.ArrowType.DownArrow if self._open else Qt.ArrowType.RightArrow
        )

    def is_open(self) -> bool:
        return self._open

    def set_open(self, open_: bool) -> None:
        if bool(open_) == self._open:
            return
        self._open = bool(open_)
        self._show()
        self.sigFolded.emit(self.title, not self._open)

    # the ParameterGroup API, so the builders read as before
    def add_row(self, *args):
        return self.group.add_row(*args)

    def add_span_row(self, *args):
        return self.group.add_span_row(*args)


class TrackTable(QAbstractTableModel):
    """Every track: id, label, start, end, duration, median Hz, points."""

    COLUMNS = ("id", "label", "start", "end", "dur", "Hz", "pts")
    sigRename = Signal(float, str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.st = (
            np.zeros(0, dtype=M.STATS_DTYPE) if hasattr(M, "STATS_DTYPE") else None
        )
        self.labels: dict = {}
        self.colours = None

    def set_stats(self, st, labels, colours) -> None:
        self.beginResetModel()
        self.st = st
        self.labels = dict(labels or {})
        self.colours = colours
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):  # noqa: N802
        return 0 if parent.isValid() or self.st is None else len(self.st)

    def columnCount(self, parent=QModelIndex()):  # noqa: N802
        return len(self.COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if (
            orientation == Qt.Orientation.Horizontal
            and role == Qt.ItemDataRole.DisplayRole
        ):
            return self.COLUMNS[section]
        return None

    def id_at(self, row: int) -> float:
        return float(self.st["id"][row])

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        r, c = index.row(), index.column()
        s = self.st[r]
        ident = float(s["id"])
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            if c == 0:
                return str(int(ident))
            if c == 1:
                return self.labels.get(int(ident), "")
            if c == 2:
                return M.fmt_time(float(s["t_first"]))
            if c == 3:
                return M.fmt_time(float(s["t_last"]))
            if c == 4:
                return f"{float(s['t_last'] - s['t_first']):.1f}"
            if c == 5:
                return f"{float(s['f_median']):.1f}"
            if c == 6:
                return f"{int(s['n']):,}"
        if role == Qt.ItemDataRole.UserRole:
            return (
                ident,
                self.labels.get(int(ident), ""),
                float(s["t_first"]),
                float(s["t_last"]),
                float(s["t_last"] - s["t_first"]),
                float(s["f_median"]),
                int(s["n"]),
            )[c]
        if role == Qt.ItemDataRole.ToolTipRole and c == 5:
            return f"{float(s['f_min']):.1f}–{float(s['f_max']):.1f} Hz"
        if (
            role == Qt.ItemDataRole.ForegroundRole
            and c == 0
            and self.colours is not None
        ):
            return theme.qcolor(self.colours.of_id(ident))
        if role == Qt.ItemDataRole.TextAlignmentRole and c != 1:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return None

    def flags(self, index):
        base = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if index.column() == 1:
            base |= Qt.ItemFlag.ItemIsEditable
        return base

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):  # noqa: N802
        if role != Qt.ItemDataRole.EditRole or index.column() != 1:
            return False
        self.sigRename.emit(self.id_at(index.row()), str(value))
        return True


class SortProxy(QSortFilterProxyModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setSortRole(Qt.ItemDataRole.UserRole)
        self.window: Optional[tuple] = None

    def filterAcceptsRow(self, row, parent):  # noqa: N802
        if self.window is None:
            return True
        st = self.sourceModel().st
        t0, t1 = self.window
        return bool(st["t_last"][row] >= t0 and st["t_first"][row] <= t1)


# ---------------------------------------------------------------- panel


class WavetrackerPanel(QWidget):
    """The Tracks tab."""

    sigModelChanged = Signal(object)  # Change
    _sigHarmonics = Signal(object)  # (job, findings) from the worker

    def __init__(self, browser, parent=None) -> None:
        super().__init__(parent)
        self.browser = browser
        self.prefs = load_prefs()
        self.scene = G.SceneState(
            show_unassigned=bool(self.prefs["show_unassigned"]),
            point_px=int(self.prefs["point_px"]),
            gap_break_s=float(self.prefs["gap_break_s"]),
            show_ids=bool(self.prefs["show_ids"]),
            dim_spec=bool(self.prefs["dim_spec"]),
        )
        self.cache = G.RenderCache()
        self.controller = ToolController(self.scene, host=self, parent=self)
        self.controller.brush_px = int(self.prefs["brush_px"])
        self.controller.sticky = bool(self.prefs["sticky_brush"])
        self.controller.ridge = bool(self.prefs["ridge_add"])
        self.ridge_source = RidgeSource(self, parent=self)
        self.controller.ridge_source = self.ridge_source
        self.overlays: list = []
        self.surfaces: list = []
        self._attached_axes: list = []
        self.edit_mode = False
        self.folder: Optional[Path] = None
        self.fine_source = None
        self.runner = None
        self.cleaner = None
        #: how runner clients are made; tests replace it with a fake
        self.runner_factory = lambda oneshot: R.RunnerClient(
            oneshot=oneshot, parent=self
        )
        self._job: Optional[dict] = None
        self._export_thread = None
        self._exporter = None
        self._token = None
        self._issues: list = []
        self._issues_key = None
        self.current_issue = None
        #: harmonic findings that involve an edited id, by harmonic id
        #: (5.13): marked on the lanes while they stand
        self.harmonic_findings: dict = {}
        #: the issue the last harmonic warning made current (Enter accepts
        #: it without moving on)
        self._warned_issue = None
        self._goto_span = None
        self._history_len = -1
        self._loaded_for = None
        self._closing = False
        self._title_dirty = None
        self._job_started = 0.0

        self._build()
        self.controller.sigCommit.connect(self.apply_plan)
        self._sigHarmonics.connect(self._harmonics_answer)
        self.controller.sigHint.connect(self._set_hint)
        self.controller.sigChanged.connect(self._scene_changed)
        self.controller.sigSelection.connect(self._selection_changed)
        self.controller.sigTool.connect(self._tool_changed)

        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.setInterval(AUTOSAVE_MS)
        self._autosave_timer.timeout.connect(self.autosave)
        self._prefs_timer = QTimer(self)
        self._prefs_timer.setSingleShot(True)
        self._prefs_timer.setInterval(500)
        self._prefs_timer.timeout.connect(lambda: save_prefs(self.prefs))
        self._table_timer = QTimer(self)
        self._table_timer.setSingleShot(True)
        self._table_timer.setInterval(TABLE_VIEW_MS)
        self._table_timer.timeout.connect(self._filter_table)
        self._lane_timer = QTimer(self)
        self._lane_timer.setInterval(LANE_POLL_MS)
        self._lane_timer.timeout.connect(self._check_lanes)

        self.router = KeyRouter(self)
        self._refresh_all()

    # ================================================================ build

    def _build(self) -> None:
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(theme.S6)
        self.sections: dict = {}
        # most used first: what is open and whether it is saved, the run
        # buttons and their progress, a pending snippet, the tools and the
        # history; the run settings, the table and the issues below, foldable
        self._build_session(box)
        self._build_snippet(box)
        self._build_edit(box)
        self._build_history(box)
        self._build_tracks(box)
        self._build_issues(box)
        self._build_run(box)
        box.addStretch(1)

    def _section(self, box, title: str, folded: bool = False) -> Section:
        stored = self.prefs.get("folded", {})
        if isinstance(stored, dict) and title in stored:
            folded = bool(stored[title])
        section = Section(title, self, folded)
        section.sigFolded.connect(self._folded)
        self.sections[title] = section
        box.addWidget(section)
        return section

    def _folded(self, title: str, folded: bool) -> None:
        stored = dict(self.prefs.get("folded", {}))
        stored[title] = bool(folded)
        self.prefs["folded"] = stored
        self._prefs_timer.start()

    @staticmethod
    def _row(*widgets, stretch=None) -> QWidget:
        row = QWidget()
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(theme.S4)
        for i, w in enumerate(widgets):
            line.addWidget(w, 1 if stretch is None or i in stretch else 0)
        return row

    def _muted(self, text: str = "", wrap: bool = True) -> QLabel:
        label = QLabel(text, self)
        label.setFont(theme.font_mono(theme.SIZE_SMALL_PT))
        theme.tint(label, "fg.muted")
        label.setWordWrap(wrap)
        return label

    def _build_session(self, box) -> None:
        group = ParameterGroup("Session", self, caption=False, narrow=True)
        self.pathw = QLabel("No tracking results", self)
        self.pathw.setFont(theme.font_ui(bold=True))
        self.pathw.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.pathw.setMinimumWidth(40)
        self.pathw.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        group.add_span_row(self.pathw)
        self.dirtyw = QLabel("", self)
        theme.tint(self.dirtyw, "accent")
        self.statusw = self._muted()
        group.add_span_row(self.statusw)

        self.foundw = QWidget(self)
        found = QHBoxLayout(self.foundw)
        found.setContentsMargins(0, 0, 0, 0)
        self.foundtextw = self._muted("Found tracking results")
        found.addWidget(self.foundtextw, 1)
        self.foundbtnw = QPushButton("Open", self.foundw)
        self.foundbtnw.clicked.connect(self._open_found)
        found.addWidget(self.foundbtnw)
        self.foundw.hide()
        group.add_span_row(self.foundw)

        # the tracks are drawn on spectrograms: say so when none is shown
        self.nospecw = QWidget(self)
        nospec = QHBoxLayout(self.nospecw)
        nospec.setContentsMargins(0, 0, 0, 0)
        self.nospectextw = self._muted("Tracks are drawn on spectrograms")
        theme.tint(self.nospectextw, "accent")
        nospec.addWidget(self.nospectextw, 1)
        showspec = QPushButton("Show", self.nospecw)
        showspec.setToolTip("Show the spectrograms (and hide the traces)")
        showspec.clicked.connect(self.show_spectrograms)
        nospec.addWidget(showspec)
        self.nospecw.hide()
        group.add_span_row(self.nospecw)

        self.savew = QPushButton("Save", self)
        self.savew.setToolTip(
            "Write the corrected identities into the results directory "
            "(Ctrl+S in edit mode). The tracker's own identities are kept in "
            "ident_v.tracked.npy and never overwritten."
        )
        self.savew.clicked.connect(self.save)
        self.morew = QToolButton(self)
        self.morew.setText("⋯")
        self.morew.setToolTip("More: open, save as, revert, show in file manager")
        self.morew.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.morew)
        menu.addAction("Open results…", self.open_results_dialog)
        self.saveasact = menu.addAction("Save as…", self.save_as)
        menu.addSeparator()
        self.revertact = menu.addAction("Revert to tracker output…", self.revert)
        self.resolveact = menu.addAction("Resolve duplicates", self.resolve_duplicates)
        menu.addSeparator()
        self.showfmact = menu.addAction(
            "Show in file manager", self.show_in_file_manager
        )
        menu.addAction("Close session", self.close_session)
        self.morew.setMenu(menu)
        group.add_span_row(self._row(self.dirtyw, self.savew, self.morew, stretch=(0,)))

        self.trackvisw = QPushButton("Track visible", self)
        self.trackvisw.setToolTip(
            "Run wavetracker on what is on screen and show the result as a "
            "provisional layer to accept or discard"
        )
        self.trackvisw.clicked.connect(self.track_visible)
        self.trackrecw = QPushButton("Track recording", self)
        self.trackrecw.setToolTip("Run wavetracker on the whole recording")
        self.trackrecw.clicked.connect(self.track_recording)
        group.add_span_row(self._row(self.trackvisw, self.trackrecw))
        # the step after tracking, so it sits right under it
        self.cleanupw = QPushButton("Clean up…", self)
        self.cleanupw.setToolTip(
            "wavetracker's cleanup, usually the first thing after tracking: "
            "join the tracks of a number of persistent fish and drop the rest "
            "(defaults fitted to the tracks' span)"
        )
        self.cleanupw.clicked.connect(self.clean_up)
        group.add_span_row(self.cleanupw)

        self.progressw = QProgressBar(self)
        self.progressw.setRange(0, 100)
        self.progressw.setTextVisible(True)
        self.cancelw = QPushButton("Cancel", self)
        self.cancelw.clicked.connect(self.cancel_job)
        self.jobroww = self._row(self.progressw, self.cancelw, stretch=(0,))
        self.jobroww.hide()
        group.add_span_row(self.jobroww)
        box.addWidget(group)

    def _build_run(self, box) -> None:
        group = self._section(box, "Run settings")
        self.runsummaryw = self._muted("", wrap=False)
        self.runsummaryw.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        group.extra.addWidget(self.runsummaryw, 0)
        self.interpw = QLabel("", self)
        self.interpw.setWordWrap(True)
        self.interpw.setFont(theme.font_ui(theme.SIZE_SMALL_PT))
        self.interpw.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        group.add_span_row(self.interpw)

        self.fminw = QDoubleSpinBox(self)
        self.fmaxw = QDoubleSpinBox(self)
        stored = self.prefs.get("fish_range") or []
        lo, hi = stored if len(stored) == 2 else (80.0, 2400.0)
        for w, v in ((self.fminw, float(lo)), (self.fmaxw, float(hi))):
            w.setRange(0.0, 100000.0)
            w.setDecimals(0)
            w.setSuffix(" Hz")
            w.setValue(v)
            w.valueChanged.connect(self._range_changed)
        self.fminw.setToolTip("Lowest fish frequency to detect")
        self.fmaxw.setToolTip("Highest fish frequency to detect")
        group.add_row("Fish range", "", self.fminw, self.fmaxw)
        self.rangewarnw = self._muted("set a narrow band for your species")
        theme.tint(self.rangewarnw, "accent")
        group.add_span_row(self.rangewarnw)

        self.devicew = narrow_combo(QComboBox(self))
        for d in ("auto", "cpu", "cuda"):
            self.devicew.addItem(d, d)
        self.devicew.setCurrentIndex(
            max(0, self.devicew.findData(self.prefs["device"]))
        )
        self.devicew.currentIndexChanged.connect(self._device_changed)
        group.add_row("Device", "", self.devicew)

        self.configw = QLineEdit(self)
        self.configw.setPlaceholderText("wavetracker defaults")
        self.configw.setToolTip("An optional wavetracker YAML config file")
        cbrowse = QToolButton(self)
        cbrowse.setText("…")
        cbrowse.clicked.connect(self._browse_config)
        group.add_row("Config", "", ParameterGroup.expanding(self.configw), cbrowse)

        self.advbtnw = QToolButton(self)
        self.advbtnw.setText("Advanced")
        self.advbtnw.setCheckable(True)
        self.advbtnw.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.advbtnw.setArrowType(Qt.ArrowType.RightArrow)
        self.advbtnw.toggled.connect(self._toggle_advanced)
        group.add_span_row(self.advbtnw)
        self.advw = QWidget(self)
        form = QFormLayout(self.advw)
        form.setContentsMargins(theme.S4, 0, 0, 0)
        form.setSpacing(theme.S2)
        self.nfftw = narrow_combo(QComboBox(self.advw))
        for n in (4096, 8192, 16384, 32768, 65536):
            self.nfftw.addItem(str(n), n)
        self.nfftw.setCurrentIndex(self.nfftw.findData(32768))
        form.addRow("nfft", self.nfftw)
        self.overlapw = QDoubleSpinBox(self.advw)
        self.overlapw.setRange(0.0, 0.99)
        self.overlapw.setSingleStep(0.05)
        self.overlapw.setValue(0.9)
        form.addRow("overlap", self.overlapw)
        self.threshmodew = narrow_combo(QComboBox(self.advw))
        self.threshmodew.addItem("from loaded results", "results")
        self.threshmodew.addItem("estimate from snippet", "estimate")
        self.threshmodew.addItem("fixed", "fixed")
        self.threshmodew.currentIndexChanged.connect(self._sync_thresholds)
        form.addRow("thresholds", self.threshmodew)
        self.lothw = QDoubleSpinBox(self.advw)
        self.hithw = QDoubleSpinBox(self.advw)
        for w, v in ((self.lothw, 10.0), (self.hithw, 20.0)):
            w.setRange(0.0, 200.0)
            w.setSuffix(" dB")
            w.setValue(v)
        form.addRow("low / high", self._row(self.lothw, self.hithw))
        self.blockw = QDoubleSpinBox(self.advw)
        self.blockw.setRange(1.0, 600.0)
        self.blockw.setValue(20.0)
        self.blockw.setSuffix(" s")
        form.addRow("block", self.blockw)
        self.ftolw = QDoubleSpinBox(self.advw)
        self.ftolw.setRange(0.01, 100.0)
        self.ftolw.setValue(2.5)
        self.ftolw.setSuffix(" Hz")
        form.addRow("freq. tolerance", self.ftolw)
        self.maxdtw = QDoubleSpinBox(self.advw)
        self.maxdtw.setRange(0.1, 600.0)
        self.maxdtw.setValue(10.0)
        self.maxdtw.setSuffix(" s")
        form.addRow("max dt", self.maxdtw)
        self.stitchw = QCheckBox("stitching", self.advw)
        self.stitchw.setChecked(True)
        form.addRow("", self.stitchw)
        self.advw.hide()
        group.add_span_row(self.advw)

        self._sync_thresholds()
        self._range_changed()

    def _build_snippet(self, box) -> None:
        self.snippetw = ParameterGroup("Snippet", self, caption=True, narrow=True)
        self.snippettextw = QLabel("", self)
        self.snippettextw.setWordWrap(True)
        self.snippetw.add_span_row(self.snippettextw)
        self.joinw = QCheckBox("Join to tracks at the edges", self)
        self.joinw.setChecked(True)
        self.snippetw.add_span_row(self.joinw)
        self.acceptw = QPushButton("Accept  (Enter)", self)
        self.acceptw.setToolTip(
            "Replace the session's tracks in this span with the snippet's; "
            "until then its tracks are only shown, not editable"
        )
        # the one thing to do next while a snippet is pending: say so
        self.acceptw.setStyleSheet(
            "QPushButton { background: %s; color: %s; font-weight: bold; }"
            % (theme.token("primary"), theme.token("on.primary"))
        )
        self.acceptw.clicked.connect(self.accept_snippet)
        self.discardw = QPushButton("Discard", self)
        self.discardw.clicked.connect(self.discard_snippet)
        self.snippetw.add_span_row(self._row(self.acceptw, self.discardw))
        self.snippetw.hide()
        box.addWidget(self.snippetw)

    def _build_edit(self, box) -> None:
        group = self._section(box, "Edit")
        self.editw = QPushButton("Edit tracks", self)
        self.editw.setCheckable(True)
        self.editw.setMinimumHeight(30)
        font = QFont(self.editw.font())
        font.setBold(True)
        self.editw.setFont(font)
        self.editw.setToolTip(
            "Edit mode (Ctrl+Shift+E): the left mouse button on the "
            "spectrogram belongs to the active tool. Middle-drag grabs and "
            "moves the spectrogram; Ctrl+wheel and Shift+wheel zoom time and "
            "frequency; right-click opens the actions menu."
        )
        self.editw.toggled.connect(self.set_edit_mode)
        group.add_span_row(self.editw)

        tools = QWidget(self)
        row = QHBoxLayout(tools)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)
        self.toolgroup = QButtonGroup(self)
        self.toolgroup.setExclusive(True)
        self.toolbtns = {}
        for key, icon, name, tip in TOOLS:
            b = QToolButton(tools)
            b.setText(f"{icon} {key}")
            b.setCheckable(True)
            b.setToolTip(f"{name} ({key}): {tip}")
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            b.setMinimumWidth(20)
            b.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            b.clicked.connect(lambda _c=False, k=key: self.controller.set_tool(k))
            self.toolgroup.addButton(b)
            row.addWidget(b, 1)
            self.toolbtns[key] = b
        self.toolbtns["V"].setChecked(True)
        group.add_span_row(tools)

        self.hintw = QLabel("", self)
        self.hintw.setWordWrap(True)
        self.hintw.setMinimumHeight(QFontMetrics(self.hintw.font()).height() * 2 + 4)
        self.hintw.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.hintw.setFont(theme.font_ui(theme.SIZE_SMALL_PT))
        theme.tint(self.hintw, "fg")
        group.add_span_row(self.hintw)
        self._build_selection_strip(group)

        self.undow = QPushButton("↶ Undo", self)
        self.undow.clicked.connect(self.undo)
        self.redow = QPushButton("Redo ↷", self)
        self.redow.clicked.connect(self.redo)
        group.add_span_row(self._row(self.undow, self.redow))

        self.brushw = QSlider(Qt.Orientation.Horizontal, self)
        self.brushw.setRange(BRUSH_MIN, BRUSH_MAX)
        self.brushw.setValue(self.controller.brush_px)
        self.brushw.setToolTip("Brush radius in screen pixels ([ and ], or Alt+wheel)")
        self.brushvalw = self._muted(f"{self.controller.brush_px} px", wrap=False)
        self.brushw.valueChanged.connect(self._brush_slider)
        group.add_row(
            "Brush", "", ParameterGroup.expanding(self.brushw), self.brushvalw
        )
        self.stickyw = QCheckBox("Sticky brush", self)
        self.stickyw.setChecked(self.controller.sticky)
        self.stickyw.setToolTip(
            "A stroke that starts on a track only touches that track (hold Alt to paint freely)"
        )
        self.stickyw.toggled.connect(self._sticky_changed)
        self.dimspecw = QCheckBox("Dim spectrogram", self)
        self.dimspecw.setChecked(self.scene.dim_spec)
        self.dimspecw.setToolTip(
            "Darken (or, on a light colour map, lighten) the spectrogram under "
            "the tracks so they stand out at every zoom"
        )
        self.dimspecw.toggled.connect(self._dim_spec_toggled)
        group.add_span_row(self._row(self.stickyw, self.dimspecw))
        self.ridgew = QCheckBox("Track ridge in brush", self)
        self.ridgew.setChecked(self.controller.ridge)
        self.ridgew.setToolTip(
            "Add (F): the stroke marks a region and the strongest continuous "
            "ridge of the raw spectrogram inside it becomes the detections; "
            "frames without a ridge above the noise stay empty. Off: one "
            "point per frame from 'Add from'. Ctrl+drag paints literally "
            "for one stroke."
        )
        self.ridgew.toggled.connect(self._ridge_changed)
        group.add_span_row(self.ridgew)

        self.optbtnw = QToolButton(self)
        self.optbtnw.setText("Display and keys")
        self.optbtnw.setCheckable(True)
        self.optbtnw.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.optbtnw.setArrowType(Qt.ArrowType.RightArrow)
        group.add_span_row(self.optbtnw)
        self.optw = QWidget(self)
        opt = QVBoxLayout(self.optw)
        opt.setContentsMargins(0, 0, 0, 0)
        opt.setSpacing(theme.S2)

        self.unassignedw = QCheckBox("Unassigned (U)", self)
        self.unassignedw.setChecked(self.scene.show_unassigned)
        self.unassignedw.toggled.connect(self._unassigned_toggled)
        self.isolatew = QCheckBox("Only selected (I)", self)
        self.isolatew.toggled.connect(self._isolate_toggled)
        opt.addWidget(self._row(self.unassignedw, self.isolatew))
        self.showidsw = QCheckBox("Ids on tracks", self)
        self.showidsw.setChecked(self.scene.show_ids)
        self.showidsw.toggled.connect(self._show_ids_toggled)
        opt.addWidget(self.showidsw)

        self.pointw = QSpinBox(self)
        self.pointw.setRange(0, 8)
        self.pointw.setValue(self.scene.point_px)
        self.pointw.setSuffix(" px")
        self.pointw.setToolTip("Point size; 0 draws lines only")
        self.pointw.valueChanged.connect(self._point_changed)
        self.gapw = QDoubleSpinBox(self)
        self.gapw.setRange(0.0, 600.0)
        self.gapw.setSingleStep(0.1)
        self.gapw.setValue(self.scene.gap_break_s)
        self.gapw.setSuffix(" s")
        self.gapw.setToolTip("Break a track's line where detections are further apart")
        self.gapw.valueChanged.connect(self._gap_changed)
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(theme.S2)
        form.addRow("Points", self.pointw)
        form.addRow("Gap break", self.gapw)

        self.addsrcw = narrow_combo(QComboBox(self))
        self.addsrcw.addItem("Automatic", "auto")
        self.addsrcw.addItem("Trace by hand", "hand")
        self.addsrcw.setToolTip(
            "Where Add gets its frequencies: the fine spectrogram, else the "
            "runner's peak search, else the stroke centre; or always the "
            "stroke centre"
        )
        self.addsrcw.currentIndexChanged.connect(self._update_add_source)
        self.addsrctextw = self._muted("", wrap=False)
        form.addRow("Add from", self._row(self.addsrcw, self.addsrctextw))
        opt.addLayout(form)

        lines = [
            f"<tr><td><b>{k}</b></td><td>{what}</td><td style='color:gray'>{shadow}</td></tr>"
            for k, what, shadow in KEY_HELP
        ]
        self.keysw = QLabel(
            "<table cellspacing=2>"
            + "".join(lines)
            + "</table><p style='color:gray'>Keys act in edit mode while the "
            "pointer is over a spectrogram or the focus is in this panel; "
            "the third column is audian's binding they shadow there.</p>",
            self,
        )
        self.keysw.setFont(theme.font_ui(theme.SIZE_SMALL_PT))
        self.keysw.setWordWrap(True)
        opt.addWidget(self.keysw)
        self.optw.hide()
        self.optbtnw.toggled.connect(self._toggle_options)
        group.add_span_row(self.optw)
        # the old name, for the tests and the Keys disclosure
        self.keysbtnw = self.optbtnw

    def _build_selection_strip(self, group) -> None:
        """What can be done with the selection, shown while there is one
        (5.8): the same actions as the keys and the lane's right-click."""
        self.stripw = QWidget(self)
        theme.frame(self.stripw)
        lay = QVBoxLayout(self.stripw)
        lay.setContentsMargins(theme.S4, theme.S2, theme.S4, theme.S4)
        lay.setSpacing(theme.S2)
        self.stripsumw = QLabel("", self.stripw)
        self.stripsumw.setFont(theme.font_ui(theme.SIZE_SMALL_PT, bold=True))
        theme.tint(self.stripsumw, "fg")
        lay.addWidget(self.stripsumw)
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(2)
        grid.setVerticalSpacing(2)
        self.stripbtns = {}
        names = (
            "unassign",
            "unassign_tracks",
            "new_id",
            "merge",
            "swap",
            "zoom",
            "clear",
        )
        keys = {
            "unassign": "Del",
            "unassign_tracks": "⇧Del",
            "new_id": "N",
            "merge": "⇧M",
            "swap": "⇧X",
            "zoom": "⇧Z",
            "clear": "Esc",
        }
        labels = {
            "unassign": "Unassign",
            "unassign_tracks": "Unassign tracks",
            "new_id": "New id",
            "merge": "Merge",
            "swap": "Swap",
            "zoom": "Zoom",
            "clear": "Clear",
        }
        font = theme.font_ui(theme.SIZE_SMALL_PT)
        for i, name in enumerate(names):
            b = QToolButton(self.stripw)
            b.setText(f"{labels[name]}  {keys[name]}")
            b.setFont(font)
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            b.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            b.setMinimumWidth(20)
            b.clicked.connect(lambda _c=False, n=name: self._strip_action(n))
            # the long one gets a row of its own; the rest go two by two
            if name == "unassign_tracks":
                grid.addWidget(b, 0, 1)
            else:
                pos = {
                    "unassign": (0, 0),
                    "new_id": (1, 0),
                    "merge": (1, 1),
                    "swap": (2, 0),
                    "zoom": (2, 1),
                    "clear": (3, 0),
                }[name]
                grid.addWidget(b, *pos)
            self.stripbtns[name] = b
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        lay.addLayout(grid)
        self.stripw.hide()
        group.add_span_row(self.stripw)

    def _strip_action(self, name: str) -> None:
        for n, _label, _key, run, why, _tip in self.controller.selection_actions(
            for_strip=True
        ):
            if n == name:
                if why:
                    self.controller.reject(why)
                else:
                    run()
                break
        self._refresh_strip()

    def _refresh_strip(self) -> None:
        c = self.controller
        if not len(self.scene.selection) or self.ts is None:
            self.stripw.hide()
            return
        self.stripsumw.setText(c.selection_summary())
        for name, _label, _key, _run, why, tip in c.selection_actions(for_strip=True):
            b = self.stripbtns[name]
            b.setEnabled(not why)
            b.setToolTip(why if why else tip)
        self.stripw.show()

    def _build_tracks(self, box) -> None:
        group = self._section(box, "Tracks")
        self.trackmodel = TrackTable(self)
        self.trackmodel.sigRename.connect(self._rename_from_table)
        self.proxy = SortProxy(self)
        self.proxy.setSourceModel(self.trackmodel)
        self.tablew = QTableView(self)
        self.tablew.setModel(self.proxy)
        self.tablew.setSortingEnabled(True)
        self.tablew.sortByColumn(2, Qt.SortOrder.AscendingOrder)
        self.tablew.verticalHeader().setVisible(False)
        self.tablew.verticalHeader().setDefaultSectionSize(
            QFontMetrics(self.tablew.font()).height() + 4
        )
        self.tablew.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tablew.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tablew.setEditTriggers(
            QAbstractItemView.EditTrigger.EditKeyPressed
            | QAbstractItemView.EditTrigger.SelectedClicked
        )
        self.tablew.setAlternatingRowColors(True)
        self.tablew.setMinimumHeight(theme.S12 * 10)
        self.tablew.setFont(theme.font_mono(theme.SIZE_SMALL_PT))
        header = self.tablew.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(True)
        header.setMinimumSectionSize(24)
        self.tablew.setToolTip(
            "Click selects the track; Ctrl/Shift extend. Double-click zooms "
            "to it. F2 renames."
        )
        self.tablew.selectionModel().selectionChanged.connect(self._table_selected)
        self.tablew.doubleClicked.connect(self._table_double)
        self.tablew.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tablew.customContextMenuRequested.connect(self._table_menu)
        group.add_span_row(self.tablew)
        self.inviewbw = QCheckBox("Only tracks in view", self)
        self.inviewbw.setChecked(True)
        self.inviewbw.toggled.connect(lambda _on: self._filter_table())
        self.selinfow = self._muted("")
        group.add_span_row(self.inviewbw)
        group.add_span_row(self.selinfow)
        self._filling_table = False

    def _build_issues(self, box) -> None:
        group = self._section(box, "Issues", folded=True)
        kinds = QWidget(self)
        grid = QGridLayout(kinds)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(theme.S6)
        grid.setVerticalSpacing(0)
        self.issuekindw = {}
        for i, (kind, text) in enumerate(ISSUE_KINDS):
            w = QCheckBox(text, kinds)
            w.setToolTip(f"G visits {text}")
            w.setChecked(kind != "crossing")
            w.toggled.connect(self._issues_changed)
            grid.addWidget(w, i // 2, i % 2)
            self.issuekindw[kind] = w
        group.add_span_row(kinds)
        self.minptsw = QSpinBox(self)
        self.minptsw.setRange(1, 100000)
        self.minptsw.setValue(10)
        self.minptsw.valueChanged.connect(self._issues_changed)
        self.previssuew = QToolButton(self)
        self.previssuew.setText("◀ Shift+G")
        self.previssuew.clicked.connect(lambda: self.goto_issue(-1))
        self.nextissuew = QToolButton(self)
        self.nextissuew.setText("G ▶")
        self.nextissuew.clicked.connect(lambda: self.goto_issue(+1))
        self.minptsw.setToolTip("A track with fewer points than this is a short track")
        group.add_row("Short below", "", self.minptsw)
        group.add_span_row(self._row(self.previssuew, self.nextissuew))
        self.issuetextw = self._muted("")
        group.add_span_row(self.issuetextw)

    def _build_history(self, box) -> None:
        group = self._section(box, "History")
        self.historyw = QListWidget(self)
        self.historyw.setFont(theme.font_mono(theme.SIZE_SMALL_PT))
        rows = QFontMetrics(self.historyw.font()).height() + 2
        self.historyw.setMinimumHeight(rows * 5)
        self.historyw.setMaximumHeight(rows * 9)
        self.historyw.setMouseTracking(True)
        self.historyw.itemClicked.connect(self._history_clicked)
        self.historyw.itemEntered.connect(self._history_hovered)
        self.historyw.viewport().installEventFilter(self)
        self.historyw.setToolTip(
            "Click an entry to go back (or forward) to just after it"
        )
        group.add_span_row(self.historyw)

    # ============================================================ lifecycle

    def showEvent(self, event):  # noqa: N802 - Qt's spelling
        super().showEvent(event)
        self.attach()
        self._lane_timer.start()
        self._interp_status()
        self.load_for_recording()

    def closeEvent(self, event):  # noqa: N802 - Qt's spelling
        """Unsaved edits are saved or discarded (8.4), then everything stops."""
        if not self._closing:
            self._closing = True
            self._ask_save_or_discard("Close the Tracks tab")
            self._shutdown()
        super().closeEvent(event)

    def about_to_flush_labels(self) -> None:
        """audian calls this on quit and on closing the file (8.4).

        It is the panel's only notice that the window is going: Qt does not
        deliver `closeEvent` to a child when the window closes.  So besides
        the unsaved-changes question it stops the runner, the export thread
        and the timers here -- a runner process left to the `QProcess`
        destructor at interpreter exit crashed the application.
        """
        if not self._closing:
            self._closing = True
            self._ask_save_or_discard("Close")
            self._shutdown()

    def _shutdown(self) -> None:
        self._autosave_timer.stop()
        self._prefs_timer.stop()
        self._lane_timer.stop()
        self._table_timer.stop()
        save_prefs(self.prefs)
        self.cancel_job(quiet=True)
        self.ridge_source.shutdown()
        for client in (self.runner, self.cleaner):
            if client is not None:
                try:
                    client.shutdown(500)
                except Exception:  # noqa: BLE001 - shutting down regardless
                    pass
        self.router.uninstall()
        self.detach()

    def router_live(self) -> bool:
        """Whether the key router may act: this browser is the visible tab."""
        return bool(self.browser.isVisible()) and not self._closing

    def attach(self) -> None:
        """An overlay and a tool surface on every spectrogram lane."""
        axes = (
            list(self.browser.spectrogram_axes())
            if hasattr(self.browser, "spectrogram_axes")
            else []
        )
        if axes == self._attached_axes and self.overlays:
            return
        self.detach()
        for ax in axes:
            overlay = TrackOverlay(ax, self.scene, self.cache)
            self.overlays.append(overlay)
            surface = ToolSurface(ax, self.controller)
            surface.arm(self.edit_mode)
            self.surfaces.append(surface)
        self._attached_axes = axes
        if self.overlays:
            vb = self.overlays[0].ax.getViewBox()
            vb.sigRangeChanged.connect(self._view_changed)
        if not axes:
            self.browser.notify(
                "warning",
                "wavetracker: this recording has no spectrogram to draw on; "
                "turn one on to see the tracks",
            )
        self.schedule()

    def detach(self) -> None:
        if self.overlays:
            try:
                self.overlays[0].ax.getViewBox().sigRangeChanged.disconnect(
                    self._view_changed
                )
            except (RuntimeError, TypeError):
                pass
        for overlay in self.overlays:
            overlay.detach()
        for surface in self.surfaces:
            surface.detach()
        self.overlays = []
        self.surfaces = []
        self._attached_axes = []
        self.controller.hover_lane = None

    def _check_lanes(self) -> None:
        try:
            axes = list(self.browser.spectrogram_axes())
        except Exception:  # noqa: BLE001
            return
        if axes != self._attached_axes:
            self.attach()
        self._update_nospec(axes)

    def _update_nospec(self, axes=None) -> None:
        if axes is None:
            try:
                axes = list(self.browser.spectrogram_axes())
            except Exception:  # noqa: BLE001
                axes = []
        shown = any(ax.isVisible() for ax in axes)
        self.nospecw.setVisible(
            not shown and (self.ts is not None or self.scene.snippet is not None)
        )

    def show_spectrograms(self) -> None:
        """The spectrogram lanes on, which is where the tracks are drawn."""
        set_panels = getattr(self.browser, "set_panels", None)
        if set_panels is not None:
            try:
                set_panels(specs=1)
            except Exception as exc:  # noqa: BLE001 - an internal API moved
                self.browser.notify("warning", f"wavetracker: {exc}")
        self._check_lanes()

    def changeEvent(self, event):  # noqa: N802
        if event.type() in (QEvent.Type.PaletteChange, QEvent.Type.StyleChange):
            for overlay in self.overlays:
                overlay.invalidate()
            self.schedule()
            self._refresh_table()
        super().changeEvent(event)

    # ============================================================ drawing

    def schedule(self) -> None:
        for overlay in self.overlays:
            overlay.schedule()

    def redraw_now(self) -> None:
        for overlay in self.overlays:
            overlay.update_plot()

    def _scene_changed(self) -> None:
        self.schedule()

    def _set_hint(self, text: str) -> None:
        self.hintw.setText(text)

    def _view_changed(self, *args) -> None:
        if self.inviewbw.isChecked():
            self._table_timer.start()

    def view_range(self) -> Optional[tuple]:
        for overlay in self.overlays:
            v = view_of(overlay.ax)
            if v.x1 > v.x0:
                return v.x0, v.x1, v.y0, v.y1
        return None

    def flash(self, rows) -> None:
        for overlay in self.overlays:
            overlay.flash(rows)

    # ============================================================ the model

    @property
    def ts(self):
        return self.scene.ts

    def set_session(self, ts, folder=None, what: str = "") -> None:
        """Replace the session (results opened, a run finished, closed)."""
        self.scene.ts = ts
        self.scene.snippet = None
        self.scene.set_selection([])
        self.scene.hover = None
        self.scene.marks = G.Marks()
        self.scene.preview = None
        self.cache.clear()
        self.folder = Path(folder) if folder is not None else None
        self.current_issue = None
        self._issues_key = None
        self.harmonic_findings = {}
        self._warned_issue = None
        self.scene.harmonic_marks = {}
        self._history_len = -1
        for tool in self.controller.tools.values():
            tool.deactivate()
        self._load_fine_spec()
        self._update_add_source()
        self._sync_session_fields()
        for overlay in self.overlays:
            overlay.invalidate()
        self.scene.touch_display()
        self._refresh_all()
        self.schedule()
        if what:
            self.browser.notify("info", f"wavetracker: {what}")

    def apply_plan(self, plan) -> Optional[object]:
        """The single place `TrackSet.apply` is called."""
        ts = self.ts
        if ts is None:
            return None
        try:
            change = ts.apply(plan)
        except M.EditRejected as exc:
            self.controller.reject(str(exc.args[0]))
            return None
        except Exception as exc:  # noqa: BLE001 - the model stays at its last revision
            self.browser.notify("error", f"wavetracker: the edit failed ({exc})")
            return None
        self._after_change(change, flash=True)
        self.controller.say(plan.label)
        self._check_harmonics(plan, change)
        return change

    # ---- harmonics (5.13)

    def _check_harmonics(self, plan, change) -> None:
        """Whether an id the reader just gave rows to is a harmonic of
        another id, or another id a harmonic of it."""
        ts = self.ts
        ids = ts.grown_ids(plan)
        if not len(ids):
            return
        sub = ts.harmonic_subset(ids)
        if sub is None:
            return
        job = (id(ts), change.revision, ids, plan.kind)
        args = (*sub, np.asarray(ts.times), ts.frequency_bin())
        if len(sub[0]) <= HARMONIC_SYNC_ROWS:
            self._harmonics_found(job, HM.find_harmonics(*args, ids=ids))
            return
        import threading

        def run() -> None:
            try:
                found = HM.find_harmonics(*args, ids=ids)
            except Exception:  # noqa: BLE001 - a check must not take the window
                return
            if not self._closing:
                try:
                    self._sigHarmonics.emit((job, found))
                except RuntimeError:  # the panel is gone
                    pass

        threading.Thread(target=run, name="wavetracker-harmonics", daemon=True).start()

    def _harmonics_answer(self, payload) -> None:
        self._harmonics_found(*payload)

    def _harmonics_found(self, job, found) -> None:
        ts_id, revision, ids, kind = job
        ts = self.ts
        if ts is None or id(ts) != ts_id or ts.revision != revision:
            return  # another edit came first; its own check speaks
        edited = {float(i) for i in ids}
        found = [
            x for x in found if x.harmonic_id in edited or x.fundamental_id in edited
        ]
        if not found:
            return
        for x in found:
            self.harmonic_findings[float(x.harmonic_id)] = x
        self._mark_harmonics()
        first = found[0]
        issue = M.harmonic_issue(first, edited)
        verb = GROW_VERB.get(kind, "edit")
        hid = M._fid(first.harmonic_id)
        if len(found) == 1:
            text = f"{issue.text} · Ctrl+Z undoes the {verb}"
            short = (
                f"id {hid} looks like the {first.ordinal()} harmonic of id "
                f"{M._fid(first.fundamental_id)}"
            )
        else:
            names = ", ".join(
                f"{M._fid(x.harmonic_id)} ({M.harmonic_mark(x)})" for x in found[:4]
            )
            names += ", …" if len(found) > 4 else ""
            text = (
                f"{len(found)} ids look like harmonics: {names} — Enter: unassign "
                f"id {hid} · Ctrl+Z undoes the {verb}"
            )
            short = f"{len(found)} ids look like harmonics: {names}"
        self.current_issue = issue
        self._warned_issue = issue
        self.issuetextw.setText(text)
        self.controller.say(text)
        self.browser.notify("warning", f"wavetracker: {short}")

    def _mark_harmonics(self) -> None:
        marks = {
            float(h): M.harmonic_mark(x) for h, x in self.harmonic_findings.items()
        }
        if marks != self.scene.harmonic_marks:
            self.scene.harmonic_marks = marks
            self.scene.touch_display()
            self.schedule()

    def _revalidate_harmonics(self) -> None:
        """After any change: keep the findings that still stand (an undo of
        the add, or unassigning the harmonic, ends one)."""
        ts = self.ts
        old = self.harmonic_findings
        if ts is None or not old:
            return
        still = {}
        for x in ts.harmonics(list(old)):
            h = float(x.harmonic_id)
            if h in old and float(x.fundamental_id) == float(old[h].fundamental_id):
                still[h] = x
        self.harmonic_findings = still
        self._mark_harmonics()
        issue = self.current_issue
        if (
            issue is not None
            and issue.kind == "harmonic"
            and issue is self._warned_issue
            and float(issue.ids[0]) not in still
        ):
            self.current_issue = None
            self._warned_issue = None
            self.issuetextw.setText("")

    def _after_change(self, change, flash: bool = False) -> None:
        if change is None:
            return
        ts = self.ts
        self.cache.note_change(change)
        sel = self.scene.selection
        if len(sel) and (sel >= ts.n).any():
            self.scene.set_selection(sel[sel < ts.n])
        for overlay in self.overlays:
            overlay.invalidate(change)
        self.controller.refresh()
        self._issues_key = None
        self._revalidate_harmonics()
        self._refresh_all()
        self.schedule()
        if flash:
            rows = np.asarray(change.rows, dtype=np.int64)
            if change.appended > 0:
                rows = np.concatenate(
                    (rows, np.arange(ts.n - change.appended, ts.n, dtype=np.int64))
                )
            rows = rows[rows < ts.n]
            QTimer.singleShot(0, lambda r=rows: self.flash(r))
        self._autosave_timer.start()
        self.sigModelChanged.emit(change)

    def undo(self) -> None:
        self._step(self.ts.undo if self.ts is not None else None, "undid")

    def redo(self) -> None:
        self._step(self.ts.redo if self.ts is not None else None, "redid")

    def _step(self, fn, verb: str) -> None:
        if fn is None:
            return
        tool = self.controller.tool
        tool.cancel()
        ts = self.ts
        h = ts.history
        index = h.position - 1 if verb == "undid" else h.position
        label = h.entries[index].label if 0 <= index < len(h.entries) else ""
        span = h.entries[index].span if 0 <= index < len(h.entries) else None
        change = fn()
        if change is None:
            self.controller.say(f"nothing to {verb[:-1]}o")
            return
        self._after_change(change, flash=True)
        self._say_where(f"{verb} {label}", span)

    def _say_where(self, text: str, span) -> None:
        view = self.view_range()
        if span is not None and view is not None and np.all(np.isfinite(span)):
            t0, t1 = span[0], span[1]
            if t1 < view[0] or t0 > view[1]:
                self._goto_span = span
                self.controller.say(f"{text} at {M.fmt_time(t0)} — Shift+Z to go there")
                return
        self._goto_span = None
        self.controller.say(text)

    def jump(self, position: int) -> None:
        ts = self.ts
        if ts is None:
            return
        self.controller.tool.cancel()
        change = ts.jump(position)
        if change is not None:
            self._after_change(change, flash=True)

    # ============================================================ refresh

    def _refresh_all(self) -> None:
        self._update_nospec()
        self._refresh_strip()
        self._refresh_header()
        self._refresh_table()
        self._refresh_history()
        self._refresh_buttons()
        self.controller.changed()

    def _refresh_header(self) -> None:
        ts = self.ts
        if ts is None:
            self.pathw.setText("No tracking results")
            self.statusw.setText("Open results or run wavetracker")
            self.dirtyw.setText("")
            self._set_title(False)
            return
        name = str(self.folder) if self.folder is not None else "Unsaved session"
        self.pathw.setText(elide_middle(name, self.pathw, self.pathw.width() or 260))
        self.pathw.setToolTip(name)
        n_ids = len(ts.ids())
        n_un = int(np.isnan(ts.ident).sum()) if ts.n else 0
        h = ts.history
        edits = (
            abs(h.position - h.saved_position)
            if h.saved_position is not None
            else h.position
        )
        self.statusw.setText(
            f"{n_ids:,} tracks · {ts.n - n_un:,} points · {n_un:,} unassigned"
            + (
                f" · {edits} edit{'s' if edits != 1 else ''} since save"
                if edits
                else ""
            )
        )
        dirty = ts.is_dirty()
        self.dirtyw.setText("● unsaved changes" if dirty else "saved")
        self._set_title(dirty)

    def _set_title(self, dirty: bool) -> None:
        """ "Tracks ●" on the tab while there are unsaved changes."""
        if dirty == self._title_dirty:
            return
        w = self
        while w is not None and w.parentWidget() is not None:
            parent = w.parentWidget()
            tabs = parent.parentWidget() if parent is not None else None
            if isinstance(tabs, QTabWidget):
                i = tabs.indexOf(w)
                if i >= 0:
                    tabs.setTabText(i, "Tracks ●" if dirty else "Tracks")
                    self._title_dirty = dirty
                    return
            w = parent

    def _refresh_buttons(self) -> None:
        ts = self.ts
        has = ts is not None
        self.savew.setEnabled(has and ts.is_dirty())
        self.saveasact.setEnabled(has)
        self.revertact.setEnabled(has and ts.n > 0)
        self.showfmact.setEnabled(self.folder is not None)
        self.undow.setEnabled(has and ts.history.can_undo())
        self.redow.setEnabled(has and ts.history.can_redo())
        if has and ts.history.can_undo():
            self.undow.setToolTip(
                f"Undo {ts.history.entries[ts.history.position - 1].label} (Ctrl+Z in edit mode)"
            )
        if has and ts.history.can_redo():
            self.redow.setToolTip(
                f"Redo {ts.history.entries[ts.history.position].label} (Ctrl+Shift+Z)"
            )
        busy = self._job is not None
        self.trackrecw.setEnabled(not busy)
        self.trackvisw.setEnabled(not busy)
        self.cleanupw.setEnabled(not busy and has and ts.n > 0 and ts.grid is not None)
        self.jobroww.setVisible(busy)
        snippet = self.scene.snippet
        self.snippetw.setVisible(snippet is not None)
        locked = has and ts.grid is not None
        for w in (self.nfftw, self.overlapw):
            w.setEnabled(not locked)
            w.setToolTip(
                "locked: snippets must use the loaded session's frame grid"
                if locked
                else ""
            )

    def _refresh_table(self) -> None:
        ts = self.ts
        self._filling_table = True
        try:
            if ts is None:
                self.trackmodel.set_stats(np.zeros(0, dtype=M.STATS_DTYPE), {}, None)
            else:
                colours = self.overlays[0].colours if self.overlays else None
                self.trackmodel.set_stats(ts.stats(), ts.labels, colours)
            self._filter_table()
        finally:
            self._filling_table = False
        self._sync_table_selection()

    def _filter_table(self) -> None:
        if self.inviewbw.isChecked():
            view = self.view_range()
            self.proxy.window = None if view is None else (view[0], view[1])
        else:
            self.proxy.window = None
        if hasattr(self.proxy, "beginFilterChange"):
            self.proxy.beginFilterChange()
            self.proxy.endFilterChange()
        else:
            self.proxy.invalidateFilter()

    def _sync_table_selection(self) -> None:
        ids = set(self.scene.selected_ids().tolist())
        sm = self.tablew.selectionModel()
        self._filling_table = True
        try:
            sm.clearSelection()
            first = None
            from PySide6.QtCore import QItemSelection, QItemSelectionModel

            selection = QItemSelection()
            st = self.trackmodel.st
            if ids and st is not None:
                for r in np.flatnonzero(np.isin(st["id"], list(ids))):
                    src = self.trackmodel.index(int(r), 0)
                    idx = self.proxy.mapFromSource(src)
                    if idx.isValid():
                        selection.select(idx, idx)
                        if first is None:
                            first = idx
            sm.select(
                selection,
                QItemSelectionModel.SelectionFlag.Select
                | QItemSelectionModel.SelectionFlag.Rows,
            )
            if first is not None:
                self.tablew.scrollTo(first)
        finally:
            self._filling_table = False
        n = len(self.scene.selection)
        if n:
            self.selinfow.setText(
                f"{n:,} points in {len(ids)} track{'s' if len(ids) != 1 else ''} selected"
            )
        else:
            self.selinfow.setText("")

    def _refresh_history(self) -> None:
        ts = self.ts
        self.historyw.blockSignals(True)
        self.historyw.clear()
        if ts is None:
            self.historyw.blockSignals(False)
            return
        h = ts.history
        start = "— start of session —"
        if h.dropped:
            start = f"— history starts at {time.strftime('%H:%M', time.localtime(h.dropped_until or 0))} —"
        item = QListWidgetItem(start)
        item.setData(Qt.ItemDataRole.UserRole, 0)
        self.historyw.addItem(item)
        faint = theme.qcolor(theme.token("fg.faint"))
        for i, cmd in enumerate(h.entries, start=1):
            stamp = time.strftime("%H:%M:%S", time.localtime(cmd.time))
            saved = " ✓" if h.saved_position == i else ""
            item = QListWidgetItem(f"{stamp}  {cmd.label}{saved}")
            item.setData(Qt.ItemDataRole.UserRole, i)
            item.setToolTip(cmd.label)
            if i > h.position:
                item.setForeground(faint)
            self.historyw.addItem(item)
        current = self.historyw.item(h.position)
        if current is not None:
            font = QFont(current.font())
            font.setBold(True)
            current.setFont(font)
            current.setText("▸ " + current.text())
            self.historyw.setCurrentItem(current)
            self.historyw.scrollToItem(current)
        self.historyw.blockSignals(False)

    # ============================================================ selection

    def _selection_changed(self, rows) -> None:
        self._sync_table_selection()
        self._refresh_strip()
        if self.scene.isolate:
            self.scene.touch_display()
            self.schedule()

    def _table_selected(self, *args) -> None:
        if self._filling_table:
            return
        rows = {
            self.proxy.mapToSource(i).row()
            for i in self.tablew.selectionModel().selectedRows()
        }
        ids = [self.trackmodel.id_at(r) for r in sorted(rows)]
        self.controller.select_ids(ids)

    def _table_double(self, index) -> None:
        row = self.proxy.mapToSource(index).row()
        if index.column() == 1:
            return
        self.controller.zoom_to_ids([self.trackmodel.id_at(row)])

    def _table_menu(self, pos) -> None:
        index = self.tablew.indexAt(pos)
        if not index.isValid():
            return
        ident = self.trackmodel.id_at(self.proxy.mapToSource(index).row())
        menu = QMenu(self)
        menu.addAction("Zoom to track", lambda: self.controller.zoom_to_ids([ident]))
        menu.addAction("Rename track…", lambda: self.rename_track(ident))
        menu.addSeparator()
        c = self.controller
        menu.addAction("Unassign selected tracks", c.delete_selected_tracks)
        menu.addAction("Merge selected tracks", c.merge_selected)
        menu.addAction("Swap selected after cursor", c.swap_after_cursor)
        menu.exec(self.tablew.viewport().mapToGlobal(pos))

    def rename_track(self, ident: float) -> None:
        ts = self.ts
        if ts is None:
            return
        old = ts.labels.get(int(ident), "")
        text, ok = QInputDialog.getText(
            self, "Rename track", f"Name for track {int(ident)}:", text=old
        )
        if ok:
            self._rename_from_table(ident, text)

    def _rename_from_table(self, ident: float, text: str) -> None:
        ts = self.ts
        if ts is None:
            return
        self.controller.commit_plan(lambda: ts.plan_set_label(ident, text.strip()))

    # ============================================================ edit mode

    def set_edit_mode(self, on: bool) -> None:
        on = bool(on)
        if self.editw.isChecked() != on:
            self.editw.setChecked(on)
            return
        self.edit_mode = on
        if not on:
            for tool in self.controller.tools.values():
                tool.cancel()
            self.scene.hover = None
            self.scene.marks = G.Marks()
            self.scene.preview = None
            self.scene.touch()
        for surface in self.surfaces:
            surface.arm(on)
        for b in self.toolbtns.values():
            b.setEnabled(on)
        self.editw.setText("Editing tracks" if on else "Edit tracks")
        self.controller.changed()
        if not on:
            self.hintw.setText(
                "Edit tracks (Ctrl+Shift+E) to correct them with the mouse"
            )
        self.schedule()

    def _tool_changed(self, key: str) -> None:
        b = self.toolbtns.get(key)
        if b is not None and not b.isChecked():
            b.setChecked(True)

    def _brush_slider(self, value: int) -> None:
        if value != self.controller.brush_px:
            self.controller.set_brush(value)

    def brush_changed(self, r: int) -> None:
        self.brushw.blockSignals(True)
        self.brushw.setValue(r)
        self.brushw.blockSignals(False)
        self.brushvalw.setText(f"{r} px")
        self.prefs["brush_px"] = int(r)
        self._prefs_timer.start()

    def _ridge_changed(self, on: bool) -> None:
        self.controller.ridge = bool(on)
        self.prefs["ridge_add"] = bool(on)
        self._prefs_timer.start()
        self.controller.changed()

    def _sticky_changed(self, on: bool) -> None:
        self.controller.sticky = bool(on)
        self.prefs["sticky_brush"] = bool(on)
        self._prefs_timer.start()

    def _unassigned_toggled(self, on: bool) -> None:
        self.scene.show_unassigned = bool(on)
        self.scene.unassigned_forced = None
        self.prefs["show_unassigned"] = bool(on)
        self._prefs_timer.start()
        self.scene.touch_display()
        self.schedule()

    def toggle_unassigned(self) -> None:
        g = self.overlays[0].geometry if self.overlays else None
        if g is not None and g.unassigned_hidden:
            self.scene.unassigned_forced = True
            self.scene.touch_display()
            self.schedule()
            self.controller.say("showing unassigned points (above the automatic limit)")
            return
        self.unassignedw.setChecked(not self.unassignedw.isChecked())
        self.controller.say(
            "unassigned points "
            + ("shown" if self.unassignedw.isChecked() else "hidden")
        )

    def _isolate_toggled(self, on: bool) -> None:
        self.scene.isolate = bool(on)
        self.scene.touch_display()
        self.schedule()
        if on:
            n = len(self.scene.selected_ids())
            self.controller.say(f"isolated: {n} track{'s' if n != 1 else ''}")

    def _show_ids_toggled(self, on: bool) -> None:
        self.scene.show_ids = bool(on)
        self.prefs["show_ids"] = bool(on)
        self._prefs_timer.start()
        self.scene.touch_display()
        self.schedule()

    def _point_changed(self, value: int) -> None:
        self.scene.point_px = int(value)
        self.prefs["point_px"] = int(value)
        self._prefs_timer.start()
        self.scene.touch_display()
        self.schedule()

    def _gap_changed(self, value: float) -> None:
        self.scene.gap_break_s = float(value)
        self.prefs["gap_break_s"] = float(value)
        self._prefs_timer.start()
        self.scene.touch_display()
        self.schedule()

    def _toggle_options(self, on: bool) -> None:
        self.optw.setVisible(on)
        self.optbtnw.setArrowType(
            Qt.ArrowType.DownArrow if on else Qt.ArrowType.RightArrow
        )

    _toggle_keys = _toggle_options

    def _dim_spec_toggled(self, on: bool) -> None:
        self.scene.dim_spec = bool(on)
        self.prefs["dim_spec"] = bool(on)
        self._prefs_timer.start()
        self.scene.touch_display()
        self.schedule()

    def _toggle_advanced(self, on: bool) -> None:
        self.advw.setVisible(on)
        self.advbtnw.setArrowType(
            Qt.ArrowType.DownArrow if on else Qt.ArrowType.RightArrow
        )

    # ============================================================ keys

    def key_applies(self, name: str) -> bool:
        if name == "accept_issue":
            # Enter accepts a pending snippet first, else the issue's
            # suggestion
            return self.scene.snippet is not None or self.current_issue is not None
        return True

    def run_key(self, name: str) -> None:
        c = self.controller
        try:
            if name.startswith("tool_"):
                c.set_tool(name[-1])
                return
            table = {
                "toggle_edit": lambda: self.set_edit_mode(not self.edit_mode),
                "undo": self.undo,
                "redo": self.redo,
                "save": self.save,
                "unassign_selected": c.unassign_selected,
                "delete_selected_tracks": c.delete_selected_tracks,
                "new_id": c.new_id_from_selection,
                "merge_selected": c.merge_selected,
                "swap_after": c.swap_after_cursor,
                "toggle_unassigned": self.toggle_unassigned,
                "toggle_isolate": lambda: self.isolatew.setChecked(
                    not self.isolatew.isChecked()
                ),
                "zoom_selection": self.zoom_selection,
                "next_issue": lambda: self.goto_issue(+1),
                "previous_issue": lambda: self.goto_issue(-1),
                "accept_issue": (
                    self.accept_snippet
                    if self.scene.snippet is not None
                    else self.accept_issue
                ),
                "brush_smaller": lambda: c.brush_scale(0.8),
                "brush_larger": lambda: c.brush_scale(1.25),
                "cycle": c.cycle_hover,
                "escape": c.escape,
            }
            fn = table.get(name)
            if fn is not None:
                fn()
        except Exception as exc:  # noqa: BLE001 - a key must not take the window
            self.browser.notify("error", f"wavetracker: {name} failed ({exc})")

    def zoom_selection(self) -> None:
        c = self.controller
        if (
            not len(self.scene.selection)
            and self.scene.hover is None
            and self._goto_span
        ):
            self.zoom_to(self._goto_span)
            self._goto_span = None
            return
        c.zoom_selection()

    def notify(self, level: str, message: str) -> None:
        self.browser.notify(level, f"wavetracker: {message}")

    def zoom_to(self, span) -> None:
        """Show ``(t0, t1, f0, f1)`` plus a 10 % margin (5.12)."""
        t0, t1, f0, f1 = (float(x) for x in span)
        dt = max(t1 - t0, 0.5)
        df = max(f1 - f0, 5.0)
        t0, dt = t0 - 0.1 * dt, 1.2 * dt
        f0, f1 = f0 - 0.1 * df, f1 + 0.1 * df
        b = self.browser
        try:
            b.set_times(max(0.0, t0), dt)
        except Exception:  # noqa: BLE001 - internal API, fall back per lane
            for overlay in self.overlays:
                overlay.ax.getViewBox().setXRange(t0, t0 + dt, padding=0)
        try:
            b.set_ranges("f", f0, f1)
        except Exception:  # noqa: BLE001
            pass
        for overlay in self.overlays:
            vb = overlay.ax.getViewBox()
            (y0, y1) = vb.viewRange()[1]
            if abs(y0 - f0) > 1e-6 or abs(y1 - f1) > 1e-6:
                vb.setYRange(f0, f1, padding=0)

    def centre_on(self, t: float, f: float, view=None) -> None:
        """Move the visible window onto ``(t, f)`` without changing the zoom.

        Time is always centred.  Frequency moves only when `f` is not well
        inside the visible band (the middle 80 %), so stepping through issues
        of one fish does not jitter the view up and down.
        """
        view = view if view is not None else self.view_range()
        if view is None:
            return
        x0, x1, y0, y1 = view
        width = x1 - x0
        t0 = max(0.0, t - 0.5 * width)
        b = self.browser
        try:
            b.set_times(t0, width)
        except Exception:  # noqa: BLE001 - internal API, fall back per lane
            for overlay in self.overlays:
                overlay.ax.getViewBox().setXRange(t0, t0 + width, padding=0)
        height = y1 - y0
        if not (np.isfinite(f) and height > 0):
            return
        if y0 + 0.1 * height <= f <= y1 - 0.1 * height:
            return
        f0 = max(0.0, f - 0.5 * height)
        f1 = f0 + height
        try:
            b.set_ranges("f", f0, f1)
        except Exception:  # noqa: BLE001
            pass
        for overlay in self.overlays:
            vb = overlay.ax.getViewBox()
            (v0, v1) = vb.viewRange()[1]
            if abs(v0 - f0) > 1e-6 or abs(v1 - f1) > 1e-6:
                vb.setYRange(f0, f1, padding=0)

    # ============================================================ issues

    def _issues_changed(self, *args) -> None:
        self._issues_key = None
        self.current_issue = None
        self.issuetextw.setText("")

    def issues(self) -> list:
        ts = self.ts
        if ts is None:
            return []
        kinds = tuple(k for k, w in self.issuekindw.items() if w.isChecked())
        key = (id(ts), ts.revision, kinds, self.minptsw.value(), self.scene.gap_break_s)
        if key != self._issues_key:
            self._issues = (
                ts.issues(
                    kinds,
                    gap_break_s=self.scene.gap_break_s,
                    min_points=self.minptsw.value(),
                )
                if kinds
                else []
            )
            self._issues_key = key
        return self._issues

    def goto_issue(self, step: int) -> None:
        issues = self.issues()
        if not issues:
            self.controller.say("no issues of the chosen kinds")
            self.current_issue = None
            self.issuetextw.setText("no issues")
            return
        view = self.view_range()
        centre = 0.5 * (view[0] + view[1]) if view is not None else 0.0
        times = np.array([i.t for i in issues])
        cur = self.current_issue
        if step > 0:
            later = np.flatnonzero(times > centre + 1e-6)
            if cur is not None and cur in issues:
                at = issues.index(cur)
                k = at + 1 if at + 1 < len(issues) else 0
            else:
                k = int(later[0]) if len(later) else 0
        else:
            earlier = np.flatnonzero(times < centre - 1e-6)
            if cur is not None and cur in issues:
                at = issues.index(cur)
                k = at - 1 if at > 0 else len(issues) - 1
            else:
                k = int(earlier[-1]) if len(earlier) else len(issues) - 1
        issue = issues[k]
        self.current_issue = issue
        if view is not None:
            self.centre_on(issue.t, issue.f, view)
        df = 10.0
        ts = self.ts
        dt = 2 * float(np.median(np.diff(ts.times[:100]))) if len(ts.times) > 1 else 1.0
        self.scene.span_outline = (
            issue.t - dt,
            issue.t + dt,
            issue.f - df,
            issue.f + df,
        )
        self.scene.touch()
        self.schedule()
        text = f"{k + 1}/{len(issues)} · {issue.text}"
        self.issuetextw.setText(text)
        self.controller.say(issue.text)

    def accept_issue(self) -> None:
        issue = self.current_issue
        ts = self.ts
        if issue is None or ts is None:
            return
        if issue.kind == "gap":
            self.controller.set_tool("F")
            self.controller.say("Add: paint along the gap to fill it")
            return
        if issue.suggestion is None:
            self.controller.say("no suggestion for this issue: zoom in and decide")
            return
        warned = issue is self._warned_issue
        if self.controller.commit_plan(lambda: issue.suggestion(ts)):
            self.current_issue = None
            self.scene.span_outline = None
            if warned:
                # the warning came after an edit, not from G: stay here
                self._warned_issue = None
                self.issuetextw.setText("")
                self.controller.say(
                    f"unassigned id {M._fid(issue.ids[0])} · Ctrl+Z restores it"
                )
                return
            self.goto_issue(+1)

    # ============================================================ history

    def _history_clicked(self, item) -> None:
        self.jump(int(item.data(Qt.ItemDataRole.UserRole)))

    def _history_hovered(self, item) -> None:
        ts = self.ts
        i = int(item.data(Qt.ItemDataRole.UserRole))
        span = None
        if ts is not None and 1 <= i <= len(ts.history.entries):
            span = ts.history.entries[i - 1].span
            if not np.all(np.isfinite(span)):
                span = None
        if span != self.scene.span_outline:
            self.scene.span_outline = span
            self.scene.touch()
            self.schedule()

    def eventFilter(self, obj, ev):  # noqa: N802
        if ev.type() == QEvent.Type.Leave and obj is self.historyw.viewport():
            if self.scene.span_outline is not None and self.current_issue is None:
                self.scene.span_outline = None
                self.scene.touch()
                self.schedule()
        return super().eventFilter(obj, ev)

    # ============================================================ files

    def recording_paths(self) -> list:
        data = getattr(self.browser, "data", None)
        loader = getattr(data, "data", None)
        opened = getattr(loader, "file_paths", None)
        if opened:
            return [os.fspath(p) for p in opened]
        path = getattr(data, "file_path", None)
        if path is None:
            return []
        if isinstance(path, (list, tuple)):
            return [os.fspath(p) for p in path]
        return [os.fspath(path)]

    def _n_recording_channels(self) -> Optional[int]:
        try:
            return int(self.browser.data.channels)
        except (AttributeError, TypeError, ValueError):
            return None

    def _duration(self) -> Optional[float]:
        data = getattr(self.browser, "data", None)
        try:
            return float(data.frames) / float(data.rate)
        except (AttributeError, TypeError, ZeroDivisionError):
            return None

    def load_for_recording(self) -> None:
        """Reopen the results last used for this recording, or offer them."""
        paths = self.recording_paths()
        if not paths or paths[0] == self._loaded_for:
            return
        self._loaded_for = paths[0]
        if self.ts is not None:
            return
        remembered = self.prefs.get("results_dirs", {}).get(paths[0])
        if remembered and (Path(remembered) / "fund_v.npy").exists():
            self.open_results(remembered)
            return
        found = Path(R.default_output_dir(paths[0]))
        if (found / "fund_v.npy").exists():
            self._found = found
            self.foundtextw.setText(f"Found tracking results — {found.name}")
            self.foundw.show()
        self._offer_cache_recovery(paths[0])

    def _offer_cache_recovery(self, recording: str) -> bool:
        """Offer the autosave of a session that never had a results
        directory (one built from accepted snippets), kept per recording."""
        folder = autosave_cache(recording)
        try:
            auto = M.read_autosave(folder)
        except Exception:  # noqa: BLE001 - a broken autosave is no reason to fail
            auto = None
        if auto is None or self.ts is not None:
            return False
        if auto.grid is None or auto.n_base != 0 or auto.append is None:
            M.discard_autosave(folder)
            return False
        when = time.strftime("%H:%M", time.localtime(auto.time))
        answer = self.ask(
            "Recover unsaved tracks",
            f"Recover the unsaved tracks of this recording from {when} "
            f"({len(auto.append):,} points)? They were never saved to a "
            "results directory.",
            ("Recover", "Discard"),
        )
        if answer != "Recover":
            M.discard_autosave(folder)
            return False
        rate, nfft, step, s0, n_frames = auto.grid
        grid = M.FrameGrid(float(rate), int(nfft), int(step), int(s0), int(n_frames))
        ts = M.TrackSet.empty(grid, auto.meta or {})
        self.set_session(ts, None, "recovered unsaved tracks")
        return self.controller.commit_plan(lambda: ts.plan_recover(auto))

    def _open_found(self) -> None:
        self.foundw.hide()
        self.open_results(self._found)

    def open_results_dialog(self) -> None:
        if not self._ask_unsaved("Open other results"):
            return
        start = (
            str(self.folder.parent)
            if self.folder
            else str(
                Path(self.recording_paths()[0]).parent if self.recording_paths() else ""
            )
        )
        folder = QFileDialog.getExistingDirectory(self, "wavetracker results", start)
        if not folder:
            return
        variants = M.ident_variants(folder)
        ident_file = "ident_v.npy"
        if len(variants) > 1:
            choice, ok = QInputDialog.getItem(
                self, "Identities", "Which identities to open:", variants, 0, False
            )
            if not ok:
                return
            ident_file = choice
        self.open_results(folder, ident_file, ask=False)

    def open_results(
        self, folder, ident_file: str = "ident_v.npy", ask: bool = True
    ) -> bool:
        if ask and not self._ask_unsaved("Open other results"):
            return False
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            ts, complaints = M.TrackSet.load(
                folder,
                ident_file,
                recording_paths=self.recording_paths() or None,
                duration=self._duration(),
            )
        except (M.ResultsError, OSError, ValueError) as exc:
            QApplication.restoreOverrideCursor()
            self.browser.notify("error", f"wavetracker: cannot open {folder}: {exc}")
            return False
        QApplication.restoreOverrideCursor()
        for complaint in complaints:
            self.browser.notify("warning", f"wavetracker: {complaint}")
        self.foundw.hide()
        self.set_session(
            ts,
            folder,
            f"opened {Path(folder).name}: {len(ts.ids()):,} tracks, {ts.n:,} points",
        )
        self._remember(folder)
        self._offer_recovery(folder)
        return True

    def _remember(self, folder) -> None:
        paths = self.recording_paths()
        if not paths:
            return
        dirs = dict(self.prefs.get("results_dirs", {}))
        dirs[paths[0]] = str(folder)
        self.prefs["results_dirs"] = dirs
        self._prefs_timer.start()

    def _offer_recovery(self, folder) -> None:
        try:
            auto = M.read_autosave(folder)
        except Exception:  # noqa: BLE001 - a broken autosave is no reason to fail
            auto = None
        ts = self.ts
        if auto is None or ts is None:
            return
        same = (
            len(auto.ident) == ts.n
            and np.array_equal(auto.ident, ts.ident, equal_nan=True)
            and auto.append is None
        )
        if same:
            M.discard_autosave(folder)
            return
        when = time.strftime("%H:%M", time.localtime(auto.time))
        text = f"Recover unsaved edits from {when}?"
        buttons = ("Recover", "Discard")
        if auto.is_stale(folder):
            # ident_v.npy was rewritten after the autosave's base was loaded
            # (cleanup, another audian, the legacy sorter): recovering would
            # throw those identities away (8.3)
            text += (
                "\n\nident_v.npy was changed by something else after these "
                "edits were made. Recovering replaces those newer identities "
                "with the autosaved ones."
            )
            buttons = ("Recover anyway", "Keep the newer file")
        answer = self.ask("Recover unsaved edits", text, buttons)
        if answer in ("Recover", "Recover anyway"):
            self.controller.commit_plan(lambda: ts.plan_recover(auto))
        else:
            M.discard_autosave(folder)

    def save(self) -> bool:
        ts = self.ts
        if ts is None:
            return False
        if self.folder is None:
            return self.save_as()
        try:
            ts.save(self.folder, recording_paths=self.recording_paths() or None)
        except OSError as exc:
            self.browser.notify("error", f"wavetracker: could not save ({exc})")
            return False
        self._autosave_timer.stop()
        self._remember(self.folder)
        self._refresh_all()
        self.browser.notify("success", f"wavetracker: saved {self.folder.name}")
        return True

    def save_as(self) -> bool:
        ts = self.ts
        if ts is None:
            return False
        start = str(self.folder.parent) if self.folder else ""
        folder = QFileDialog.getExistingDirectory(self, "Save tracks into", start)
        if not folder:
            return False
        return self.save_into(folder)

    def save_into(self, folder) -> bool:
        """Save the session into `folder` and keep it there from now on.

        A directory holding other results is replaced wholesale (every
        array, times and metadata), after asking."""
        folder = Path(folder)
        same = self.folder is not None and os.path.abspath(
            self.folder
        ) == os.path.abspath(folder)
        if not same and (folder / "fund_v.npy").exists():
            answer = self.ask(
                "Results exist",
                f"{folder} already holds tracking results. Replace them with "
                "these tracks?",
                ("Replace", "Cancel"),
            )
            if answer != "Replace":
                return False
        previous = self.folder
        self.folder = folder
        if not self.save():
            self.folder = previous
            return False
        if previous is None:
            paths = self.recording_paths()
            M.discard_autosave(autosave_cache(paths[0] if paths else ""))
        return True

    def _autosave_dir(self) -> Path:
        """The results directory, or for a session that has none (built from
        snippets) a cache directory of its own recording, so that two
        recordings never share one autosave."""
        if self.folder is not None:
            return Path(self.folder)
        paths = self.recording_paths()
        return autosave_cache(paths[0] if paths else "")

    def autosave(self) -> None:
        ts = self.ts
        if ts is None:
            return
        folder = self._autosave_dir()
        if not ts.is_dirty():
            # undone back to the saved state: an older autosave would offer
            # to bring back edits the reader took back on purpose
            self._discard_autosave()
            return
        paths = self.recording_paths()
        try:
            folder.mkdir(parents=True, exist_ok=True)
            ts.write_autosave(folder, recording=paths[0] if paths else None)
        except OSError as exc:
            self.browser.notify("warning", f"wavetracker: autosave failed ({exc})")

    def revert(self) -> None:
        ts = self.ts
        if ts is None:
            return
        answer = self.ask(
            "Revert to tracker output",
            "Give every detection back the identity wavetracker gave it? "
            "This is one undoable edit; nothing is written until you save.",
            ("Revert", "Cancel"),
        )
        if answer == "Revert":
            self.controller.commit_plan(lambda: ts.plan_revert())

    def resolve_duplicates(self) -> None:
        ts = self.ts
        if ts is None:
            return
        self.controller.commit_plan(
            lambda: ts.plan_apply_ident(np.array(ts.ident), "Resolve duplicates")
        )

    def show_in_file_manager(self) -> None:
        if self.folder is None:
            return
        opener = {"darwin": "open", "win32": "explorer"}.get(sys.platform, "xdg-open")
        try:
            subprocess.Popen([opener, str(self.folder)])
        except OSError as exc:
            self.browser.notify("warning", f"wavetracker: {exc}")

    def close_session(self) -> None:
        if not self._ask_unsaved("Close the session"):
            return
        self.set_session(None)

    # ---------------------------------------------- unsaved changes (8.4)

    def ask(self, title: str, text: str, buttons) -> str:
        """A question with named buttons; returns the chosen name.

        Every dialog of the panel goes through here, so tests patch one
        method rather than `QMessageBox`.
        """
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setText(text)
        made = {}
        for name in buttons:
            role = QMessageBox.ButtonRole.AcceptRole
            if name in ("Cancel", "Keep current"):
                role = QMessageBox.ButtonRole.RejectRole
            elif name in ("Discard", "Discard edits and load"):
                role = QMessageBox.ButtonRole.DestructiveRole
            made[box.addButton(name, role)] = name
        box.exec()
        return made.get(box.clickedButton(), buttons[-1])

    def _ask_unsaved(self, what: str) -> bool:
        """Save / Discard / Cancel before replacing a dirty session."""
        ts = self.ts
        if ts is None or not ts.is_dirty():
            return True
        answer = self.ask(
            what, "The tracks have unsaved changes.", ("Save", "Discard", "Cancel")
        )
        if answer == "Save":
            return self.save()
        if answer == "Discard":
            self._discard_autosave()
            return True
        return False

    def _ask_save_or_discard(self, what: str) -> None:
        """Save / Discard only: audian does not let a panel veto a close."""
        ts = self.ts
        if ts is None or not ts.is_dirty():
            return
        try:
            answer = self.ask(
                what, "The tracks have unsaved changes.", ("Save", "Discard")
            )
        except Exception:  # noqa: BLE001 - no dialog: keep the autosave
            self.autosave()
            return
        if answer == "Save":
            if not self.save():
                self.autosave()
        else:
            self._discard_autosave()

    def _discard_autosave(self) -> None:
        folder = self._autosave_dir()
        try:
            M.discard_autosave(folder)
        except OSError:
            pass

    # ============================================================ runner

    def _interp_status(self, error: str = "") -> None:
        """The wavetracker line: its version, devices once the runner said
        hello, or why it cannot run (red)."""
        runner = self.runner
        version, broken = R.wavetracker_status()
        ok = broken is None
        if error:
            text = f"✗ {error}"
            token = "danger"
            ok = False
        elif broken:
            text = f"✗ {broken}"
            token = "danger"
        elif runner is not None and runner.hello:
            h = runner.hello
            devices = [d for d in h.get("devices", []) if d not in ("auto",)]
            text = (
                f"✓ wavetracker {h.get('wavetracker', version)} · Python "
                f"{h.get('python', '?')} · {', '.join(devices) or 'cpu'}"
            )
            token = "success"
        else:
            text = f"wavetracker {version} · Python {platform.python_version()}"
            if runner is not None and runner.state == "starting":
                text += " · starting…"
            token = "fg.muted"
        self.interpw.setText(text)
        theme.tint(self.interpw, token)
        self._refresh_run_summary(ok)

    def _refresh_run_summary(self, ok: Optional[bool] = None) -> None:
        """What the folded Run settings header still shows."""
        if ok is None:
            ok = R.wavetracker_status()[1] is None
        lo, hi = self.fminw.value(), self.fmaxw.value()
        mark = "" if ok else "✗ "
        self.runsummaryw.setText(f"{mark}{lo:.0f}–{hi:.0f} Hz")
        self.runsummaryw.setToolTip(self.interpw.text())

    def _browse_config(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "wavetracker config", "", "YAML (*.yaml *.yml);;All files (*)"
        )
        if path:
            self.configw.setText(path)

    def _device_changed(self) -> None:
        self.prefs["device"] = str(self.devicew.currentData())
        self._prefs_timer.start()

    def _range_changed(self) -> None:
        lo, hi = self.fminw.value(), self.fmaxw.value()
        if [lo, hi] != self.prefs.get("fish_range"):
            self.prefs["fish_range"] = [lo, hi]
            if hasattr(self, "_prefs_timer"):
                self._prefs_timer.start()
        if hasattr(self, "runsummaryw") and hasattr(self, "interpw"):
            self._refresh_run_summary()
        broad = abs(lo - 80.0) < 1e-6 and abs(hi - 2400.0) < 1e-6
        self.rangewarnw.setVisible(broad)
        style = "QDoubleSpinBox { color: %s; }" % theme.token("accent") if broad else ""
        self.fminw.setStyleSheet(style)
        self.fmaxw.setStyleSheet(style)

    def _sync_thresholds(self) -> None:
        fixed = self.threshmodew.currentData() == "fixed"
        self.lothw.setEnabled(fixed)
        self.hithw.setEnabled(fixed)

    def _sync_session_fields(self) -> None:
        """Prefill the run fields from the loaded results' config."""
        ts = self.ts
        meta = getattr(ts, "meta", None) or {}
        cfg = meta.get("config") or {}
        hg = cfg.get("harmonic_groups") or {}
        if "min_freq" in hg and "max_freq" in hg:
            self.fminw.setValue(float(hg["min_freq"]))
            self.fmaxw.setValue(float(hg["max_freq"]))
        sp = cfg.get("spectrogram") or {}
        if "nfft" in sp:
            i = self.nfftw.findData(int(sp["nfft"]))
            if i < 0:
                self.nfftw.addItem(str(int(sp["nfft"])), int(sp["nfft"]))
                i = self.nfftw.count() - 1
            self.nfftw.setCurrentIndex(i)
        if "overlap_frac" in sp:
            self.overlapw.setValue(float(sp["overlap_frac"]))
        tr = cfg.get("tracking") or {}
        if "freq_tolerance" in tr:
            self.ftolw.setValue(float(tr["freq_tolerance"]))
        if "max_dt" in tr:
            self.maxdtw.setValue(float(tr["max_dt"]))
        if meta.get("low_threshold") is not None:
            self.threshmodew.setCurrentIndex(0)
        self._range_changed()

    def _config_overrides(self, snippet: bool = False) -> dict:
        cfg = {
            "harmonic_groups": {
                "min_freq": float(self.fminw.value()),
                "max_freq": float(self.fmaxw.value()),
            },
            "spectrogram": {
                "nfft": int(self.nfftw.currentData()),
                "overlap_frac": float(self.overlapw.value()),
                "block_duration": float(self.blockw.value()),
            },
            "tracking": {
                "freq_tolerance": float(self.ftolw.value()),
                "max_dt": float(self.maxdtw.value()),
            },
            "stitching": {"enabled": bool(self.stitchw.isChecked())},
        }
        ts = self.ts
        if ts is not None and ts.grid is not None:
            cfg["spectrogram"]["nfft"] = int(ts.grid.nfft)
            sp = (ts.meta.get("config") or {}).get("spectrogram") or {}
            if "overlap_frac" in sp:
                cfg["spectrogram"]["overlap_frac"] = float(sp["overlap_frac"])
            if "exclude_channels" in sp:
                cfg["spectrogram"]["exclude_channels"] = list(sp["exclude_channels"])
        if snippet:
            mode = self.threshmodew.currentData()
            meta = ts.meta if ts is not None else {}
            if mode == "results" and meta.get("low_threshold") is not None:
                cfg["harmonic_groups"]["low_threshold"] = float(meta["low_threshold"])
                cfg["harmonic_groups"]["high_threshold"] = float(meta["high_threshold"])
            elif mode == "fixed":
                cfg["harmonic_groups"]["low_threshold"] = float(self.lothw.value())
                cfg["harmonic_groups"]["high_threshold"] = float(self.hithw.value())
        return cfg

    def _client(self, oneshot: bool = False):
        if oneshot:
            if self.cleaner is None:
                self.cleaner = self.runner_factory(True)
                self._connect(self.cleaner)
            return self.cleaner
        if self.runner is None:
            self.runner = self.runner_factory(False)
            self._connect(self.runner)
        return self.runner

    def _connect(self, client) -> None:
        client.sigHello.connect(lambda _h: self._interp_status())
        client.sigError.connect(self._runner_failed)
        client.sigProgress.connect(self._job_progress)
        client.sigResult.connect(self._job_result)
        client.sigError.connect(self._job_error)
        client.sigState.connect(lambda _s: self._update_add_source())

    def _runner_failed(self, job, kind, message) -> None:
        """A runner that could not start says why, in the wavetracker line
        (no job is waiting for it, so nothing else would)."""
        if kind in ("startup", "protocol") or (
            self.runner is not None and not self.runner.hello and kind != "cancelled"
        ):
            first = str(message).strip().splitlines()
            self._interp_status(f"{kind}: {first[-1] if first else 'failed'}")
            self.sections["Run settings"].set_open(True)

    def _submit(self, kind: str, job: dict, oneshot: bool = False, **extra) -> bool:
        client = self._client(oneshot)
        try:
            jid = client.submit(**job)
        except R.RunnerError as exc:
            self.browser.notify("error", f"wavetracker: {exc}")
            return False
        self._job = {"kind": kind, "id": jid, "client": client, **extra}
        self._job_started = time.monotonic()
        if "Run settings" not in self.prefs.get("folded", {}):
            # set up once, then out of the way (the reader can unfold it)
            self.sections["Run settings"].set_open(False)
        self.progressw.setRange(0, 0)
        self.progressw.setFormat("starting wavetracker…")
        self._refresh_buttons()
        return True

    def _job_progress(self, job, stage, done, total, text) -> None:
        if self._job is None or self._job.get("id") != job:
            return
        if total < 0:
            self.progressw.setRange(0, 0)
            self.progressw.setFormat(text or stage)
            return
        self.progressw.setRange(0, 1000)
        frac = done / max(total, 1)
        self.progressw.setValue(int(1000 * frac))
        eta = ""
        elapsed = time.monotonic() - self._job_started
        if frac > 0.02:
            remaining = elapsed * (1 - frac) / frac
            eta = f"{int(remaining // 60)}:{int(remaining % 60):02d} left"
        # the panel is narrow: percentage first, then what is running (the
        # step, else the stage), then the time left; whatever does not fit
        # is elided from the step, and the tooltip has it all
        what = text or stage
        full = " · ".join(x for x in (f"{100 * frac:.0f}%", what, eta) if x)
        self.progressw.setToolTip(f"{stage}: {full}")
        fixed = " · ".join(x for x in (f"{100 * frac:.0f}%", "", eta) if x)
        metrics = self.progressw.fontMetrics()
        room = self.progressw.width() - metrics.horizontalAdvance(fixed + " · ") - 12
        if what and room > metrics.horizontalAdvance("…") * 3:
            what = metrics.elidedText(what, Qt.TextElideMode.ElideRight, room)
            line = " · ".join(x for x in (f"{100 * frac:.0f}%", what, eta) if x)
        else:
            line = " · ".join(x for x in (f"{100 * frac:.0f}%", eta) if x)
        self.progressw.setFormat(line)  # only %p, %v, %m are placeholders

    def _job_error(self, job, kind, message) -> None:
        current = self._job
        if current is None or current.get("id") != job:
            return
        self._job = None
        self._refresh_buttons()
        if current["kind"] in ("snippet", "cleanup"):
            self._remove_tmp(current.get("tmp"))
        if kind == "cancelled":
            self.browser.notify("info", "wavetracker: cancelled")
            return
        if current["kind"] == "cleanup" and kind == "exception":
            # cleanup refusing the data (its ValueError) is an answer, not a
            # crash: say it in words, on the hint line too
            text = cleanup_refusal(message)
            if text is not None:
                self.controller.say(text)
                self.browser.notify("warning", text)
                return
        self.browser.notify("error", f"wavetracker ({kind}): {message}")

    def cancel_job(self, quiet: bool = False) -> None:
        if self._token is not None:
            self._token.cancel()
        self._stop_export()
        job = self._job
        if job is not None and job.get("client") is not None:
            job["client"].cancel()
        if job is not None and job["kind"] == "snippet":
            self._remove_tmp(job.get("tmp"))
        self._job = None
        if not quiet:
            self._refresh_buttons()

    def _job_result(self, job, result) -> None:
        current = self._job
        if current is None or current.get("id") != job:
            return
        self._job = None
        self._refresh_buttons()
        kind = current["kind"]
        try:
            if kind == "whole":
                self._whole_done(result)
            elif kind == "snippet":
                self._snippet_done(current, result)
            elif kind == "cleanup":
                self._cleanup_done(current, result)
        except Exception as exc:  # noqa: BLE001 - report, keep the session
            self.browser.notify("error", f"wavetracker: {exc}")

    # ---- whole recording (4.2)

    def _ready_to_run(self) -> bool:
        """Whether wavetracker can run here; if not (a broken install), open
        the Run settings at the line that says why."""
        broken = R.wavetracker_status()[1]
        if broken is None:
            return True
        self.sections["Run settings"].set_open(True)
        self._interp_status()
        self.controller.reject(broken)
        return False

    def track_recording(self) -> None:
        if not self._ready_to_run():
            return
        paths = self.recording_paths()
        if not paths:
            self.browser.notify("warning", "wavetracker: no recording is open")
            return
        runner = self.runner
        if len(paths) > 1 and runner is not None and runner.hello is not None:
            if "multi_input" not in runner.hello.get("capabilities", []):
                self.controller.say(
                    "needs a wavetracker with list input (see docs/eodsorter-design.md 4.6)"
                )
                return
        out = R.default_output_dir(paths[0])
        if Path(out).exists() and (Path(out) / "fund_v.npy").exists():
            answer = self.ask(
                "Results exist",
                f"{out} already holds tracking results.",
                ("Open existing", "Replace", "Choose another", "Cancel"),
            )
            if answer == "Open existing":
                self.open_results(out)
                return
            if answer == "Choose another":
                chosen = QFileDialog.getExistingDirectory(
                    self, "Output directory", str(Path(out).parent)
                )
                if not chosen:
                    return
                out = str(Path(chosen) / Path(out).name)
            elif answer == "Replace":
                if not self._move_results_aside(out):
                    return
            else:
                return
        if Path(out).exists():
            # an empty directory, or one without results: the runner wants
            # the name free, and an empty one can simply go
            try:
                Path(out).rmdir()
            except OSError:
                if not self._move_results_aside(out):
                    return
        job = R.detect_job(
            paths,
            R.partial_dir(out),
            out,
            self._config_overrides(),
            self.configw.text().strip() or None,
            0.0,
            None,
            str(self.devicew.currentData()),
        )
        self._submit("whole", job, replace=Path(out).exists(), out=out)

    def _move_results_aside(self, out) -> bool:
        """Rename `out` to ``<out>.old-<stamp>`` (4.2, never deleted).

        When it is the session's own directory, the session follows it: its
        unsaved edits belong to the old results, and saving them later must
        not write into the new run that will appear under the old name."""
        try:
            moved = R.move_aside(out)
        except OSError as exc:
            self.browser.notify(
                "error", f"wavetracker: cannot move {out} aside ({exc})"
            )
            return False
        if self.folder is not None and os.path.abspath(self.folder) == os.path.abspath(
            out
        ):
            self.folder = Path(moved)
            if self.ts is not None:
                self.ts.moved_to(self.folder)
            self._remember(self.folder)
            self._refresh_header()
        self.browser.notify(
            "info", f"wavetracker: old results kept as {Path(moved).name}"
        )
        return True

    def _whole_done(self, result) -> None:
        out = result.get("output_dir")
        ts = self.ts
        if ts is not None and ts.is_dirty():
            answer = self.ask(
                "Tracking finished",
                "The new results are ready, but the tracks have unsaved changes.",
                ("Save edits, then load", "Discard edits and load", "Keep current"),
            )
            if answer == "Keep current":
                self.browser.notify("info", f"wavetracker: new results are in {out}")
                return
            if answer == "Save edits, then load" and not self.save():
                return
            if answer == "Discard edits and load":
                self._discard_autosave()
        self.open_results(out, ask=False)

    # ---- snippet (4.3)

    def _snippet_grid(self):
        ts = self.ts
        if ts is not None:
            return ts.grid
        data = getattr(self.browser, "data", None)
        try:
            rate = float(data.rate)
            frames = int(data.frames)
        except (AttributeError, TypeError):
            return None
        return M.FrameGrid.for_recording(
            rate, frames, int(self.nfftw.currentData()), float(self.overlapw.value())
        )

    def track_visible(self) -> None:
        if not self._ready_to_run():
            return
        paths = self.recording_paths()
        view = self.view_range()
        grid = self._snippet_grid()
        if not paths or view is None:
            self.controller.say("nothing on screen to track")
            return
        if grid is None:
            self.controller.reject(
                "these results have no regular frame grid; snippet runs are disabled"
            )
            return
        k0, k1 = grid.frame_range(view[0], view[1])
        if k1 - k0 < 2:
            need = (grid.nfft + grid.step) / grid.rate
            self.controller.reject(
                f"zoom out: wavetracker needs at least two FFT windows, {need:.1f} s here"
            )
            return
        span = (k1 - k0) * grid.step / grid.rate
        if span > R.MAX_SNIPPET_S:
            self.controller.reject("use Track recording for spans this long")
            return
        if k1 - k0 < 30:
            self.controller.say(
                "comb removal needs 30 frames; this snippet runs without it"
            )
        tmp = R.make_tmpdir()
        wav = str(Path(tmp) / "snippet.wav")
        s0, s1 = grid.sample_range(k0, k1)
        self._token = CancelToken()
        self._exporter = R.SnippetExporter(paths, (s0, s1), wav, self._token)
        self._export_thread = QThread(self)
        self._exporter.moveToThread(self._export_thread)
        self._export_thread.started.connect(self._exporter.run)
        self._exporter.sigDone.connect(self._snippet_exported)
        self._job = {
            "kind": "export",
            "id": None,
            "client": None,
            "tmp": tmp,
            "grid": grid,
            "k0": k0,
            "k1": k1,
            "session": self.ts,
        }
        self._job_started = time.monotonic()
        self.progressw.setRange(0, 0)
        self.progressw.setFormat("writing the snippet…")
        self._refresh_buttons()
        self._export_thread.start()

    def _stop_export(self) -> None:
        thread, exporter = self._export_thread, self._exporter
        self._export_thread = None
        self._exporter = None
        if thread is None:
            return
        thread.quit()
        if not thread.wait(5000):
            # still inside a read (slow disk): a QThread destroyed while
            # running aborts the application, so let it outlive the panel
            # and delete itself when it is done
            orphan_thread(thread, exporter)

    def _snippet_exported(self, wav: str, error: str) -> None:
        job = self._job
        self._stop_export()
        if job is None or job.get("kind") != "export":
            return
        if error:
            self._job = None
            self._remove_tmp(job["tmp"])
            self._refresh_buttons()
            if error != "cancelled":
                self.browser.notify(
                    "error", f"wavetracker: snippet export failed ({error})"
                )
            return
        self._job = None
        out = str(Path(job["tmp"]) / "run")
        detect = R.detect_job(
            wav,
            out,
            None,
            self._config_overrides(snippet=True),
            self.configw.text().strip() or None,
            0.0,
            None,
            str(self.devicew.currentData()),
        )
        if not self._submit(
            "snippet",
            detect,
            tmp=job["tmp"],
            grid=job["grid"],
            k0=job["k0"],
            k1=job["k1"],
            out=out,
            session=job.get("session"),
        ):
            self._remove_tmp(job["tmp"])

    def _snippet_done(self, job, result) -> None:
        out = result.get("output_dir") or job["out"]
        if self.ts is not job.get("session", self.ts):
            self._remove_tmp(job["tmp"])
            self.browser.notify(
                "warning",
                "wavetracker: other results were opened while the snippet ran; "
                "its result was dropped",
            )
            return
        try:
            snippet = M.load_snippet(out, job["grid"], job["k0"])
        finally:
            self._remove_tmp(job["tmp"])
        self.show_snippet(snippet, job["grid"])

    def show_snippet(self, snippet, grid=None) -> None:
        """Show a snippet run as the provisional layer (4.3, step 8)."""
        self._snippet_grid_used = grid
        self.scene.snippet = snippet
        self.scene.touch_display()
        n_ids = len(np.unique(snippet.ident[np.isfinite(snippet.ident)]))
        times = self.ts.times if self.ts is not None else grid.times()
        t0 = float(times[min(snippet.k0, len(times) - 1)])
        t1 = float(times[min(snippet.k1 - 1, len(times) - 1)])
        self.snippettextw.setText(
            f"Snippet {M.fmt_time(t0)}–{M.fmt_time(t1)} · {n_ids} tracks · "
            f"{len(snippet.fund):,} points — not editable until accepted: "
            "Accept (Enter) or Discard"
        )
        if self.ts is None and grid is not None:
            # the snippet creates the session when accepted; draw it on an
            # empty one meanwhile
            self.scene.ts = M.TrackSet.empty(grid)
        self._refresh_buttons()
        for overlay in self.overlays:
            overlay.invalidate()
        self.schedule()

    def accept_snippet(self) -> None:
        snippet = self.scene.snippet
        ts = self.ts
        if snippet is None or ts is None:
            return
        stitch = self.joinw.isChecked()
        if self.controller.commit_plan(
            lambda: ts.plan_replace_span(snippet, stitch=stitch)
        ):
            self.discard_snippet()

    def discard_snippet(self) -> None:
        self.scene.snippet = None
        self.scene.touch_display()
        for overlay in self.overlays:
            overlay.invalidate()
        self._refresh_buttons()
        self.schedule()

    @staticmethod
    def _remove_tmp(tmp) -> None:
        if not tmp:
            return
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)

    # ---- cleanup (4.5)

    def clean_up(self) -> None:
        ts = self.ts
        if ts is None or ts.n == 0:
            return
        setup = M.cleanup_setup(ts)
        if setup.reason is not None:
            self.controller.reject(f"Clean up: {setup.reason}")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Clean up")
        form = QFormLayout(dialog)
        note = QLabel(
            "wavetracker's cleanup joins the tracks of a fixed number of "
            "persistent fish, window by window, and drops the rest. The "
            f"tracks here span {M.fmt_time(setup.span_s)}; the defaults are "
            "fitted to that, and fish defaults to how many ids are tracked "
            "at once. On dense field data it may find nothing to keep: then "
            "correct the tracklets by hand."
        )
        note.setWordWrap(True)
        form.addRow(note)
        nfish = QSpinBox(dialog)
        nfish.setRange(1, 1000)
        nfish.setValue(max(1, setup.n_fish))
        nfish.setToolTip("How many fish to keep (default: ids tracked at once)")
        form.addRow("fish", nfish)
        fields = {}
        for key, value, step in (
            ("stride_minutes", setup.stride_minutes, 1.0),
            ("overlap_frac", M.CLEANUP_OVERLAP, 0.05),
            ("freq_tolerance", 2.5, 0.5),
            ("time_tolerance_minutes", setup.time_tolerance_minutes, 1.0),
            ("density_threshold", 0.1, 0.05),
        ):
            w = QDoubleSpinBox(dialog)
            w.setRange(0.0, 10000.0)
            w.setDecimals(2)
            w.setSingleStep(step)
            w.setValue(value)
            form.addRow(key.replace("_", " "), w)
            fields[key] = w
        mem = QDoubleSpinBox(dialog)
        mem.setRange(0.5, 4096.0)
        mem.setSuffix(" GB")
        limit = R.default_mem_limit()
        mem.setValue((limit or 8 * 2**30) / 2**30)
        form.addRow("memory limit", mem)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        tmp = R.make_tmpdir()
        # cut to the frames with detections: see M.CleanupSetup
        for name, array in setup.arrays(ts).items():
            np.save(Path(tmp) / f"{name}.npy", array)
        job = R.cleanup_job(
            tmp,
            nfish.value(),
            {k: float(w.value()) for k, w in fields.items()},
            int(mem.value() * 2**30),
        )
        self._submit(
            "cleanup",
            job,
            oneshot=True,
            tmp=tmp,
            n_fish=nfish.value(),
            revision=ts.revision,
            session=ts,
            base=np.array(ts.ident),
        )

    def _cleanup_done(self, job, result) -> None:
        ts = self.ts
        path = Path(job["tmp"]) / f"ident_v_cleaned_n{job['n_fish']}.npy"
        try:
            ident = np.load(path, allow_pickle=False)
        finally:
            self._remove_tmp(job["tmp"])
        if ts is None or ts is not job.get("session", ts):
            self.browser.notify(
                "warning",
                "wavetracker: other results were opened while cleanup ran; "
                "its result was dropped",
            )
            return
        base = job.get("base")
        if base is None:
            base = np.asarray(ts.ident)
        if len(ident) != len(base) or len(base) > ts.n:
            self.browser.notify(
                "error", "wavetracker: cleanup returned the wrong number of rows"
            )
            return
        ident, kept_edits = rebase_ident(ident, base, np.asarray(ts.ident))
        old = np.asarray(ts.ident)
        changed = ~((old == ident) | (np.isnan(old) & np.isnan(ident)))
        kept = len(np.unique(ident[np.isfinite(ident)]))
        n_un = int((np.isnan(ident) & ~np.isnan(old)).sum())
        text = (
            f"kept {kept} ids, {int(changed.sum()):,} points reassigned, "
            f"{n_un:,} unassigned"
        )
        if kept_edits:
            text += (
                f"; {kept_edits:,} points you edited while it ran keep your identities"
            )
        if self.ask("Cleanup finished", text, ("Apply", "Cancel")) == "Apply":
            n = job["n_fish"]
            self.controller.commit_plan(
                lambda: ts.plan_apply_ident(ident, f"Clean up ({n} fish)")
            )

    # ---- Add's frequency source (5.5)

    def _load_fine_spec(self) -> None:
        self.fine_source = None
        folder = self.folder
        ts = self.ts
        if folder is None or ts is None:
            return
        spec = folder / "fine_spec.npy"
        if not spec.exists():
            return
        try:
            self.fine_source = FineSpecSource(
                np.load(spec, mmap_mode="r"),
                np.load(folder / "fine_freqs.npy"),
                np.load(folder / "fine_times.npy"),
                ts.times,
            )
        except (OSError, ValueError) as exc:
            self.browser.notify(
                "warning", f"wavetracker: cannot read fine_spec.npy ({exc})"
            )

    def _update_add_source(self, *args) -> None:
        c = self.controller
        if self.addsrcw.currentData() == "hand":
            c.add_source = CentreSource()
        elif self.fine_source is not None:
            c.add_source = self.fine_source
        elif (
            self.runner is not None
            and self.runner.hello is not None
            and self.ts is not None
            and self.ts.grid is not None
        ):
            c.add_source = RunnerPeaks(self)
        else:
            c.add_source = CentreSource()
        self.addsrctextw.setText(c.add_source.name)


class RunnerPeaks:
    """Add's frequencies from the runner's ``peaks`` op (5.5, case 2)."""

    name = "peak search"
    immediate = False

    def __init__(self, panel: WavetrackerPanel) -> None:
        self.panel = panel
        self._job = None

    def preview(self, frames, centre, r_hz):
        return np.asarray(centre, dtype=np.float64)

    def estimate(self, frames, centre, r_hz):
        return np.asarray(centre, dtype=np.float64), None, None

    def request(self, frames, centre, r_hz, done) -> None:
        panel = self.panel
        client = panel.runner
        ts = panel.ts
        if client is None or client.job is not None or ts is None or ts.grid is None:
            done(*self.estimate(frames, centre, r_hz))
            return
        tmp = R.make_tmpdir()
        out = str(Path(tmp) / "peaks.npz")
        centre = np.asarray(centre, dtype=np.float64)
        job = R.peaks_job(
            panel.recording_paths(),
            ts.grid,
            session_channels(ts, panel._n_recording_channels()),
            frames,
            centre - r_hz,
            centre + r_hz,
            out,
            str(panel.devicew.currentData()),
        )
        try:
            jid = client.submit(**job)
        except R.RunnerError:
            done(*self.estimate(frames, centre, r_hz))
            return
        self._job = jid

        def result(job_id, msg):
            if job_id != jid:
                return
            disconnect()
            try:
                with np.load(out) as data:
                    fund = np.asarray(data["fund"], dtype=np.float64)
                    sign = np.asarray(data["sign"]) if "sign" in data else None
                    cplx = np.asarray(data["cplx"]) if "cplx" in data else None
                done(fund, sign, cplx)
            except (OSError, KeyError, ValueError) as exc:
                done(None, None, None, error=str(exc))
            finally:
                WavetrackerPanel._remove_tmp(tmp)

        def error(job_id, kind, message):
            if job_id != jid:
                return
            disconnect()
            WavetrackerPanel._remove_tmp(tmp)
            done(None, None, None, error=message)

        def disconnect():
            for sig, fn in ((client.sigResult, result), (client.sigError, error)):
                try:
                    sig.disconnect(fn)
                except (RuntimeError, TypeError):
                    pass

        client.sigResult.connect(result)
        client.sigError.connect(error)

    def cancel(self) -> None:
        client = self.panel.runner
        if client is not None and self._job is not None and client.job == self._job:
            client.cancel()
        self._job = None


__all__ = ["WavetrackerPanel", "load_prefs", "save_prefs"]
