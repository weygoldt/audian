import io
import os
import html
import sys
import glob
import json
import logging
import argparse

import multiprocessing as mp
import pyqtgraph as pg

from pathlib import Path
from PyQt5.QtCore import QPointF, Qt, QTimer, QBuffer, QSize, QRect, QRectF, QEvent
from PyQt5.QtGui import QKeySequence, QIcon, QGuiApplication
from PyQt5.QtGui import QPixmap, QPainter, QPainterPath, QFontMetrics
from PyQt5.QtWidgets import QApplication, QMainWindow, QTabWidget
from PyQt5.QtWidgets import QTabBar, QStylePainter, QStyleOptionTab
from PyQt5.QtWidgets import QStyle, QProxyStyle
from PyQt5.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QLabel
from PyQt5.QtWidgets import QAction, QActionGroup, QPushButton
from PyQt5.QtWidgets import QDialog, QDialogButtonBox, QScrollArea
from PyQt5.QtWidgets import QFileDialog, QMessageBox
from PyQt5.QtWidgets import QStackedWidget, QStatusBar, QToolButton
from PyQt5.QtWidgets import QLineEdit, QGridLayout, QSizePolicy, QFrame
from PyQt5.QtWidgets import QProgressBar, QKeySequenceEdit, QMenu
from PyQt5.QtWidgets import QListWidget, QListWidgetItem, QPlainTextEdit
from PIL import Image
from PIL.PngImagePlugin import PngInfo
from audioio.audioconverter import parse_load_kwargs
from audioio import available_formats, PlayAudio, AudioLoader

from . import theme
from .version import __version__, __year__, audian_dirs
from .databrowser import ANNOTATION_SURFACE_TIPS, DataBrowser
from .eventoverlay import SURFACE_LABELS, SURFACE_ORDER
from .fulltraceplot import OVERVIEW_ACTIVITY, secs_to_str
from .plugins import Plugins
from .panels import Panel


log = logging.getLogger("audian")

# audio file suffixes accepted by drag & drop and by the startup page:
AUDIO_SUFFIXES = (
    ".wav",
    ".wave",
    ".aiff",
    ".aifc",
    ".aif",
    ".flac",
    ".ogg",
    ".oga",
    ".opus",
    ".mp3",
    ".mp4",
    ".m4a",
    ".au",
    ".snd",
    ".raw",
    ".w64",
    ".caf",
    ".mat",
    ".npz",
)


# icon roles: a toolbar glyph is drawn three times, once per QIcon mode, so
# that Qt never has to fake a disabled or hovered variant from a single
# pre-rendered pixmap (which is what made the QStyle standard icons
# invisible at 1.09:1 on bg.surface).
GLYPH_NORMAL = "fg.muted"  # 6.9:1 on bg.surface
GLYPH_ACTIVE = "fg"  # hover / selected
GLYPH_DISABLED = "fg.faint"  # what the palette already gives disabled text
GLYPH_ON = "on.primary"  # on a checked button's primary.dim fill
# NOTE: the design brief asked for 55% alpha on the disabled glyph.  Over
# bg.base that composites to #404855 - 1.96:1, which is the very greyness
# the invisible-icon defect was filed about, and it is dimmer than the
# disabled *label* right next to it in the same button (the palette draws
# that in fg.faint at full alpha).  The glyph matches its own label.
GLYPH_DISABLED_ALPHA = None

# every glyph is designed in a unit box and mapped onto the icon rect, so
# the same outline serves any icon size:
_FILLED_GLYPHS = {
    "play": [[(0.18, 0.06), (0.92, 0.50), (0.18, 0.94)]],
    "pause": [
        [(0.20, 0.08), (0.42, 0.08), (0.42, 0.92), (0.20, 0.92)],
        [(0.58, 0.08), (0.80, 0.08), (0.80, 0.92), (0.58, 0.92)],
    ],
    "seek-forward": [
        [(0.04, 0.10), (0.48, 0.50), (0.04, 0.90)],
        [(0.50, 0.10), (0.94, 0.50), (0.50, 0.90)],
    ],
    "skip-forward": [
        [(0.06, 0.10), (0.62, 0.50), (0.06, 0.90)],
        [(0.70, 0.08), (0.90, 0.08), (0.90, 0.92), (0.70, 0.92)],
    ],
    "forward": [
        [(0.94, 0.50), (0.52, 0.14), (0.52, 0.86)],
        [(0.10, 0.40), (0.56, 0.40), (0.56, 0.60), (0.10, 0.60)],
    ],
}

# the mirrored glyphs share their outline with the forward-facing one:
_MIRRORED_GLYPHS = {
    "seek-backward": "seek-forward",
    "skip-backward": "skip-forward",
    "back": "forward",
}


def _filled_glyph_path(kind: str, size: int) -> QPainterPath | None:
    """The filled outline of `kind`, mapped onto a `size` x `size` icon."""
    mirror = kind in _MIRRORED_GLYPHS
    polygons = _FILLED_GLYPHS.get(_MIRRORED_GLYPHS.get(kind, kind))
    if polygons is None:
        return None
    m = size / 8.0
    e = size - 2 * m
    path = QPainterPath()
    for polygon in polygons:
        for i, (u, v) in enumerate(polygon):
            if mirror:
                u = 1.0 - u
            point = (m + e * u, m + e * v)
            if i == 0:
                path.moveTo(*point)
            else:
                path.lineTo(*point)
        path.closeSubpath()
    return path


def _draw_glyph(painter: QPainter, kind: str, size: int, color: str, alpha) -> None:
    """Paint one glyph in `color` onto an already open painter."""
    painter.setPen(theme.pen(color, theme.LW_THIN, alpha=alpha))
    painter.setBrush(Qt.NoBrush)
    m = 2  # margin
    e = size - 2 * m  # extent
    if kind == "close":
        # Two strokes at a generous inset.  Qt's own SP_TabCloseButton is a
        # heavy bevelled X from the platform style; this is the same mark
        # drawn as the design system draws everything else.
        pen = theme.pen(color, theme.LW_CLOSE, alpha=alpha)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        i = size * 0.30
        painter.drawLine(QPointF(i, i), QPointF(size - i, size - i))
        painter.drawLine(QPointF(size - i, i), QPointF(i, size - i))
        return
    filled = _filled_glyph_path(kind, size)
    if filled is not None:
        painter.fillPath(filled, theme.brush(color, alpha))
        return
    path = QPainterPath()
    if kind == "trace":
        # a small waveform
        path.moveTo(m, size / 2)
        path.lineTo(m + e * 0.2, m + e * 0.15)
        path.lineTo(m + e * 0.4, m + e * 0.85)
        path.lineTo(m + e * 0.6, m + e * 0.3)
        path.lineTo(m + e * 0.8, m + e * 0.7)
        path.lineTo(m + e, size / 2)
        painter.drawPath(path)
    elif kind == "home":
        # "zoom home" is *show the whole recording*, not a house.  Two end
        # stops with a span between them says that; a literal house said
        # nothing about time at all.
        y = size / 2
        painter.drawLine(int(m), int(m + e * 0.15), int(m), int(m + e * 0.85))
        painter.drawLine(int(m + e), int(m + e * 0.15), int(m + e), int(m + e * 0.85))
        painter.drawLine(int(m + e * 0.14), int(y), int(m + e * 0.86), int(y))
        for tip, direction in ((m + e * 0.14, 1), (m + e * 0.86, -1)):
            path.moveTo(tip + direction * e * 0.18, y - e * 0.16)
            path.lineTo(tip, y)
            path.lineTo(tip + direction * e * 0.18, y + e * 0.16)
        painter.drawPath(path)
    elif kind == "spectrogram":
        # a time/frequency field: bands that vary, inside a light frame, so
        # it cannot be confused with the colour bar beside it
        painter.drawRect(QRectF(m, m, e, e))
        for i, frac in enumerate((0.35, 0.75, 0.55)):
            y = m + e * (0.28 + 0.22 * i)
            painter.drawLine(int(m + e * 0.12), int(y), int(m + e * frac), int(y))
    elif kind == "meanspec":
        # x-bar: the spectrogram glyph with the overbar that is the notation
        # for a mean.  A picture of the mechanism -- lanes folding into one --
        # needs three strips, an arrow and a panel inside 12 px of drawable
        # extent; the notation needs one line, and the reader of this
        # application already knows it.  The pair reads as a pair on the tool
        # bar, which they are: F2's mode and the mean of it.
        bar = m + e * 0.06
        painter.drawLine(int(m), int(bar), int(m + e), int(bar))
        field = QRectF(m, m + e * 0.28, e, e * 0.72)
        painter.drawRect(field)
        for i, frac in enumerate((0.35, 0.75, 0.55)):
            y = field.top() + field.height() * (0.24 + 0.26 * i)
            painter.drawLine(
                int(field.left() + e * 0.12),
                int(y),
                int(field.left() + e * frac),
                int(y),
            )
    elif kind == "power":
        # a peaked curve rising from the left
        path.moveTo(m, m + e)
        path.lineTo(m + e * 0.35, m + e)
        path.lineTo(m + e * 0.5, m)
        path.lineTo(m + e * 0.65, m + e)
        path.lineTo(m + e, m + e)
        painter.drawPath(path)
    elif kind == "colorbar":
        # a narrow upright scale with a ramp inside it: upright and solid
        # where the spectrogram glyph is square and open
        bar = QRectF(m + e * 0.32, m, e * 0.36, e)
        painter.drawRect(bar)
        for i in range(4):
            shade = 0.85 - 0.2 * i
            painter.fillRect(
                QRectF(
                    bar.left() + 1,
                    bar.top() + 1 + (bar.height() - 2) * i / 4,
                    bar.width() - 2,
                    (bar.height() - 2) / 4,
                ),
                theme.brush(color, shade * (1.0 if alpha is None else alpha)),
            )
    elif kind == "navigator":
        painter.drawRect(QRectF(m, m + e * 0.25, e, e * 0.5))
        painter.setBrush(theme.brush(color, 0.5 * (1.0 if alpha is None else alpha)))
        painter.drawRect(QRectF(m + e * 0.55, m + e * 0.25, e * 0.3, e * 0.5))
    elif kind == "zoom":
        painter.drawEllipse(QRectF(m, m, e * 0.7, e * 0.7))
        painter.drawLine(int(m + e * 0.62), int(m + e * 0.62), int(m + e), int(m + e))
    elif kind == "analyze":
        # three bars of different height
        for i, h in enumerate((0.4, 0.9, 0.6)):
            x = m + e * (0.1 + 0.32 * i)
            painter.drawLine(int(x), int(m + e), int(x), int(m + e * (1 - h)))
    elif kind == "save":
        painter.drawLine(int(size / 2), int(m), int(size / 2), int(m + e * 0.65))
        path.moveTo(m + e * 0.3, m + e * 0.4)
        path.lineTo(size / 2, m + e * 0.7)
        path.lineTo(m + e * 0.7, m + e * 0.4)
        painter.drawPath(path)
        painter.drawLine(int(m), int(m + e), int(m + e), int(m + e))
    elif kind == "more":
        # Three dots, the universal "there is more behind this".  Filled, so
        # it reads at 16 px where an outline would be three rings.
        for u in (0.15, 0.5, 0.85):
            painter.setBrush(theme.brush(color, alpha))
            painter.setPen(Qt.NoPen)
            r = e * 0.09
            painter.drawEllipse(QPointF(m + e * u, size / 2), r, r)
    elif kind == "play-region":
        # The transport's triangle between the two end stops the "home" glyph
        # already uses for "a bounded stretch of time".  `play_region` shared
        # the plain "play" pixmap with `play_window`, which the words told
        # apart; without the words they were the same mark twice.
        painter.drawLine(int(m), int(m + e * 0.1), int(m), int(m + e * 0.9))
        painter.drawLine(int(m + e), int(m + e * 0.1), int(m + e), int(m + e * 0.9))
        path.moveTo(m + e * 0.28, m + e * 0.18)
        path.lineTo(m + e * 0.82, m + e * 0.5)
        path.lineTo(m + e * 0.28, m + e * 0.82)
        path.closeSubpath()
        painter.fillPath(path, theme.brush(color, alpha))
    elif kind == "label":
        # A box with a corner tag: the mark this mode makes, plus the thing
        # that tells it apart from the zoom rectangle beside it on the bar.
        painter.drawRect(QRectF(m, m + e * 0.25, e * 0.75, e * 0.75))
        painter.fillRect(
            QRectF(m + e * 0.45, m, e * 0.55, e * 0.3), theme.brush(color, alpha)
        )
    elif kind == "ask":
        painter.setFont(theme.font_ui(size - 5, bold=True))
        painter.drawText(QRectF(0, 0, size, size), Qt.AlignCenter, "?")
    elif kind == "channels":
        for i in range(3):
            y = m + e * (0.15 + 0.35 * i)
            painter.drawLine(int(m), int(y), int(m + e), int(y))
    elif kind == "fit":
        painter.drawRect(QRectF(m, m + e * 0.2, e, e * 0.6))
        painter.drawLine(int(m + e * 0.5), int(m), int(m + e * 0.5), int(m + e))


def glyph_pixmap(kind: str, size: int, color: str, alpha=None) -> QPixmap:
    """One glyph rendered into a transparent pixmap."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.Antialiasing, True)
    _draw_glyph(painter, kind, size, color, alpha)
    painter.end()
    return pm


def glyph_icon(kind: str, size: int = 16, color: str = GLYPH_NORMAL) -> QIcon:
    """A monochrome icon drawn as a QPainterPath, in all four QIcon modes.

    No emoji, no external assets, and above all no ``QStyle`` standard
    icon: those are pre-rendered pixmaps in the platform theme's own grey
    and never honour ours.  Every mode is supplied explicitly so that Qt
    picks the right one instead of fading the normal pixmap.
    """
    icon = QIcon()
    for mode, role, alpha in (
        (QIcon.Normal, color, None),
        (QIcon.Active, GLYPH_ACTIVE, None),
        (QIcon.Selected, GLYPH_ACTIVE, None),
        (QIcon.Disabled, GLYPH_DISABLED, GLYPH_DISABLED_ALPHA),
    ):
        icon.addPixmap(glyph_pixmap(kind, size, role, alpha), mode, QIcon.Off)
    # A checked button is filled with primary.dim, which is *dark* in the
    # daylight theme.  Drawing the On state in the same ink as the Off state
    # put a black glyph on navy there -- the icon vanished exactly when the
    # button was active.  On states get the on-primary foreground instead.
    on_pixmap = glyph_pixmap(kind, size, GLYPH_ON, None)
    for mode in (QIcon.Normal, QIcon.Active, QIcon.Selected):
        icon.addPixmap(on_pixmap, mode, QIcon.On)
    icon.addPixmap(
        glyph_pixmap(kind, size, GLYPH_DISABLED, GLYPH_DISABLED_ALPHA),
        QIcon.Disabled,
        QIcon.On,
    )
    return icon


class VerticalTabBar(QTabBar):
    """A narrow spine of upright tabs down the left edge.

    Two axes are being bought here.  Vertical space is what a stacked
    waveform view is short of, so the tab strip comes off the top; and the
    tabs themselves are turned upright so the strip costs about 30 px of
    width rather than the ~180 px a column of horizontal tabs needs for its
    labels.  A sixteen channel stack gets both back.

    The label is rotated to read bottom-to-top, the usual direction for a
    left-hand spine, and each tab carries the same flat close mark the rest
    of the design system uses.
    """

    #: Width of the spine: the label's line height plus breathing room.
    SPINE_PAD = 10

    #: Longest a tab may get before its label is elided.
    MAX_LENGTH = 320

    CLOSE_SIZE = 14

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self._hover_close = -1

    # -- geometry ---------------------------------------------------------

    def _label_font(self, bold: bool = True):
        font = self.font()
        font.setBold(bold)
        return font

    def tabSizeHint(self, index: int) -> QSize:
        """Size an upright tab: narrow across, as long as its label needs.

        Computed rather than taken from ``super()``: Qt reports a vertical
        bar's hint already oriented for the bar, and transposing it again
        collapsed every tab to the minimum.
        """
        metrics = QFontMetrics(self._label_font())
        width = metrics.height() + self.SPINE_PAD
        length = (
            theme.S12
            + metrics.horizontalAdvance(self.tabText(index))
            + theme.S8
            + self.CLOSE_SIZE
            + theme.S8
        )
        return QSize(width, min(length, self.MAX_LENGTH))

    def minimumTabSizeHint(self, index: int) -> QSize:
        metrics = QFontMetrics(self._label_font())
        return QSize(
            metrics.height() + self.SPINE_PAD,
            theme.S12 + self.CLOSE_SIZE + theme.S8,
        )

    def close_rect(self, index: int) -> QRect:
        """Where the close mark sits: the top of the tab.

        The label reads bottom-to-top, so the top of the tab is where the
        text *ends* -- the same place the mark sits on a horizontal tab.
        Computed here rather than handed to ``setTabButton``, which lays a
        button out with a horizontal bar in mind and drops it into the
        middle of the label on a vertical one.
        """
        rect = self.tabRect(index)
        size = self.CLOSE_SIZE
        return QRect(
            rect.left() + (rect.width() - size) // 2,
            rect.top() + theme.S6,
            size,
            size,
        )

    # -- painting ---------------------------------------------------------

    def paintEvent(self, event) -> None:
        painter = QStylePainter(self)
        option = QStyleOptionTab()
        for index in range(self.count()):
            self.initStyleOption(option, index)
            # shape only: letting the style draw the label would rotate it
            # its own way and ignore the space the close mark needs
            painter.drawControl(QStyle.CE_TabBarTabShape, option)
            self._paint_label(painter, index)
            self._paint_close(painter, index)

    def _paint_label(self, painter, index: int) -> None:
        rect = self.tabRect(index)
        current = index == self.currentIndex()
        font = self._label_font(current)
        metrics = QFontMetrics(font)
        # room left once the close mark and its margins are taken off the top
        length = rect.height() - (theme.S6 + self.CLOSE_SIZE + theme.S8) - theme.S8
        if length <= 0:
            return
        label = metrics.elidedText(self.tabText(index), Qt.ElideMiddle, length)
        painter.save()
        # bottom-left of the tab becomes the origin; +x now runs up the
        # screen and +y runs across the spine
        painter.translate(rect.left(), rect.bottom())
        painter.rotate(-90)
        painter.setFont(font)
        painter.setPen(theme.qcolor("fg" if current else "fg.muted"))
        painter.drawText(
            QRect(theme.S8, 0, length, rect.width()),
            Qt.AlignLeft | Qt.AlignVCenter,
            label,
        )
        painter.restore()

    def _paint_close(self, painter, index: int) -> None:
        current = index == self.currentIndex()
        token = (
            "fg"
            if index == self._hover_close
            else ("fg.muted" if current else "fg.faint")
        )
        painter.drawPixmap(
            self.close_rect(index),
            glyph_pixmap("close", self.CLOSE_SIZE, token, None),
        )

    # -- close mark hit testing -------------------------------------------

    def _close_at(self, pos) -> int:
        for index in range(self.count()):
            if self.close_rect(index).contains(pos):
                return index
        return -1

    def mouseMoveEvent(self, event) -> None:
        hovered = self._close_at(event.pos())
        if hovered != self._hover_close:
            self._hover_close = hovered
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        if self._hover_close != -1:
            self._hover_close = -1
            self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            index = self._close_at(event.pos())
            if index >= 0:
                self.tabCloseRequested.emit(index)
                event.accept()
                return
        super().mousePressEvent(event)


class MnemonicStyle(QProxyStyle):
    """Paint the menu bar mnemonic underlines only while Alt is held.

    Qt implements the Alt reveal in the Windows styles only; everywhere
    else ``SH_UnderlineShortcut`` is hard on and every top level menu
    carries a permanent underline.  The ampersands stay in the action
    texts, so Alt+F keeps opening the File menu whether or not the
    compositor lets us see the Alt press - nothing is lost if the reveal
    never fires.
    """

    def __init__(self, parent=None):
        super().__init__()
        if parent is not None:
            # the style must outlive the widget that uses it
            self.setParent(parent)
        self.reveal = False

    def styleHint(self, hint, option=None, widget=None, data=None) -> int:
        if hint == QStyle.SH_UnderlineShortcut:
            return 1 if self.reveal else 0
        return super().styleHint(hint, option, widget, data)


def make_transparent(widget, name: str) -> None:
    """Let the parent background show through a plain container widget.

    The global stylesheet paints every bare ``QWidget`` in bg.base, which
    drops a darker block into the tool bar and the status bar.  An ID
    selector is used so that the rule does not cascade onto the children.
    """
    widget.setObjectName(name)
    widget.setStyleSheet(f"#{name} {{ background: transparent; }}")


class StatusSeparator(QWidget):
    """A hairline between two status bar fields, on the 16px rhythm.

    A ``QFrame`` VLine would be restyled by the global stylesheet and the
    line would land on the widget edge; this paints one hairline in the
    middle of a 16px slot instead, which is the whole rhythm.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(theme.S16)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        make_transparent(self, "audian_status_separator")

    def paintEvent(self, ev) -> None:
        painter = QPainter(self)
        painter.setPen(theme.pen("border", theme.HAIRLINE, cosmetic=False))
        x = self.width() // 2
        painter.drawLine(x, theme.S4, x, self.height() - theme.S4)
        painter.end()


def chip_style(color: str = "fg", border: str = "border") -> str:
    """Stylesheet for a small key chip or status chip."""
    return (
        f"color: {theme.token(color)};"
        f"background: {theme.token('bg.surface')};"
        f"border: {theme.HAIRLINE}px solid {theme.token(border)};"
        f"border-radius: {theme.RADIUS_CONTROL}px;"
        f"padding: {theme.S2}px {theme.S6}px;"
    )


class RecentFiles:
    """The recently opened files, persisted next to the full-trace cache."""

    max_entries = 10
    file_name = "recent.json"

    def __init__(self):
        self.entries = []
        self.load()

    def path(self) -> Path:
        return audian_dirs.user_cache_path / self.file_name

    def load(self) -> None:
        self.entries = []
        try:
            path = self.path()
            if path.exists():
                with open(path) as sf:
                    entries = json.load(sf)
                if isinstance(entries, list):
                    self.entries = [
                        e for e in entries if isinstance(e, dict) and "path" in e
                    ][: self.max_entries]
        except (OSError, ValueError) as e:
            log.debug("could not read recent files: %s", e)

    def save(self) -> None:
        try:
            audian_dirs.user_cache_path.mkdir(parents=True, exist_ok=True)
            with open(self.path(), "w") as df:
                json.dump(self.entries, df, indent=2)
        except OSError as e:
            log.debug("could not write recent files: %s", e)

    def add(self, file_path, channels=None, duration=None, rate=None) -> None:
        path = Path(file_path)
        entry = dict(
            path=os.fspath(path.resolve()),
            name=path.name,
            parent=os.fspath(path.resolve().parent),
            channels=channels,
            duration=duration,
            rate=rate,
        )
        self.entries = [e for e in self.entries if e.get("path") != entry["path"]]
        self.entries.insert(0, entry)
        del self.entries[self.max_entries :]
        self.save()


class ToolStrip(QWidget):
    """The tool bar, able to narrow without the window having to.

    Why
    ---

    Every button on this bar is `QSizePolicy.Minimum`, so the bar's minimum
    width is the plain SUM of them: 1372 px measured on a four channel
    recording, and 1458 once the amplitude and channel buttons are showing
    their longest text.  That is a floor the window cannot go under, and on a
    14 inch laptop at 150% display scaling the whole screen is 1280 logical
    pixels -- so audian could not be made to fit at all.

    What it gives up, and in what order
    -----------------------------------

    Words before controls.  339 px of that 1372 is text wrapped around glyphs
    that are already there, so the first thing to go is the wording on the
    six region-mode buttons and on Fit Y; every control stays on the bar and
    stays one click away, and the tool tips -- written for exactly this --
    carry the name and the shortcut.

    Then the 12 px breathing space either side of the three group rules.

    Only then do controls leave, a whole group at a time, into the overflow
    menu at the end of the bar, and **right to left** so that every button
    still on the bar keeps its x and the reader's aim stays good.  A folded
    control is not gone: it is in a menu that renders the same `QAction`, so
    it keeps its name, its glyph, its checked state, and -- better than the
    bar ever managed -- its shortcut.  The transport never folds, and neither
    does the channel button: the hidden-channel count is the only place in
    the interface that says the other channels are there.

    The floor it publishes
    ----------------------

    `minimumSizeHint` returns the width of the LAST stage, always, whatever
    stage is showing.  A hint that tracked the current stage would raise the
    window's own minimum as the bar relaxed, and Qt would push the window
    back out again the moment it had room.  A constant means the floor is one
    number that never moves, and because that number is the real content
    width of the tightest stage, Qt is never asked to squeeze a button below
    its own minimum -- which is what `QSizePolicy.Ignored` here would have
    done instead: measured, it let Analyze shrink from a 98 px hint to 30 px
    and clipped the glyphs inside the icon-only buttons.

    Still a plain QWidget with a QHBoxLayout, for the reason
    `Audian.setup_toolbar` gives: `QToolBar` recomputes its layout from the
    style on every stylesheet re-apply.  That is also why the overflow
    button is built with the bar rather than created on demand -- the
    build-time loop that caps every item's height runs exactly once.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        #: ``(name, apply, width)`` per stage, widest first, measured at build
        self._stages: list = []
        self._stage = 0
        self._fitting = False
        #: groups that can fold, each ``(name, widgets, actions)``
        self._folding: list = []
        self._folded: list = []
        self.overflow_button = None
        self.overflow_menu = None

    # --- measuring --------------------------------------------------------

    def set_stages(self, stages) -> None:
        """Take the stage table and measure every stage's width, once.

        Measured rather than added up: the numbers that end up in the
        comments have to come from the layout, and a stage's width is not the
        sum of its buttons -- spacing, hidden items and the overflow button
        all move it.
        """
        layout = self.layout()
        if layout is None:
            return
        self._stages = []
        for name, apply in stages:
            apply(True)
            layout.invalidate()
            layout.activate()
            self._stages.append((name, apply, layout.totalMinimumSize().width()))
        # back to the roomiest, and let `fit` choose from the real width
        for _name, apply, _width in self._stages:
            apply(False)
        self._stage = 0
        if self._stages:
            self._stages[0][1](True)
        layout.invalidate()
        layout.activate()
        self.updateGeometry()

    def stage_widths(self) -> dict:
        """``{name: width}``, for the tests and for the commit message."""
        return {name: width for name, _apply, width in self._stages}

    def sizeHint(self):
        layout = self.layout()
        hint = layout.sizeHint() if layout is not None else super().sizeHint()
        return QSize(hint.width(), theme.TOOLBAR_HEIGHT + theme.HAIRLINE)

    def minimumSizeHint(self):
        """The tightest stage's measured width, whatever stage is showing."""
        width = self._stages[-1][2] if self._stages else 0
        return QSize(width, theme.TOOLBAR_HEIGHT + theme.HAIRLINE)

    # --- fitting ----------------------------------------------------------

    @property
    def compact(self) -> bool:
        """True once the bar has given up its words."""
        return self._stage > 0

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.fit()

    def fit(self) -> None:
        """Show the roomiest stage that fits the width the bar has."""
        if self._fitting or not self._stages:
            return
        room = self.width()
        if room <= 0:
            return
        wanted = len(self._stages) - 1
        for index, (_name, _apply, width) in enumerate(self._stages):
            if width <= room:
                wanted = index
                break
        if wanted == self._stage:
            return
        self._fitting = True
        try:
            self._stages[self._stage][1](False)
            self._stages[wanted][1](True)
            self._stage = wanted
            layout = self.layout()
            if layout is not None:
                layout.invalidate()
                layout.activate()
            self.updateGeometry()
        except RuntimeError:
            # a resize can still reach a bar whose buttons Qt has already
            # deleted on the C++ side, on the way out of a window; the same
            # guard `refresh_glyph_icons` keeps for the same reason
            pass
        finally:
            self._fitting = False


class RecentRow(QPushButton):
    """One clickable row of the recent-files column.

    A ``QPushButton`` derives its size hint from its text, not from a child
    layout, so the three stacked labels were drawn on top of each other in
    a 30 px row.  The hint is taken from the layout instead.

    The second line is a fixed-width grid, not one elided string: the
    channel count, the duration and the sample rate each own a right
    aligned mono column, and only the path is elided - on its own
    separators, so that a unit like "kHz" is never cut in half.
    """

    # column widths of the metadata grid, in mono characters:
    CHANNEL_CHARS = 5
    DURATION_CHARS = 10
    RATE_CHARS = 8

    def __init__(self, entry, parent=None):
        super().__init__(parent)
        self.entry = entry
        self.setFlat(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setStyleSheet(
            "QPushButton { border: none; background: transparent;"
            f"border-radius: {theme.RADIUS_CONTROL}px; }}"
            "QPushButton:hover { background: "
            f"{theme.token('bg.surface')}; }}"
        )
        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(theme.S6, theme.S4, theme.S6, theme.S4)
        vbox.setSpacing(theme.S2)
        self.name_label = QLabel(entry.get("name", "?"), self)
        self.name_label.setFont(theme.font_ui())
        self.name_label.setStyleSheet(
            f"color: {theme.token('fg')}; background: transparent;"
        )
        # a 60 character file name must not push the column into the next
        # one; the line is elided to the row width in resizeEvent:
        self.name_label.setMinimumWidth(0)
        self.name_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        vbox.addWidget(self.name_label)

        # NOTE: the design brief asked for fg.faint on the directory; it
        # scores 4.22:1 on bg.base and fails the 4.5:1 bar, so the whole
        # secondary line is fg.muted at the small size instead.
        meta_row = QHBoxLayout()
        meta_row.setContentsMargins(0, 0, 0, 0)
        meta_row.setSpacing(theme.S8)
        mono = theme.font_mono(theme.SIZE_SMALL_PT)
        metrics = theme.mono_metrics(theme.SIZE_SMALL_PT)
        stats = self.stats_text(entry)
        self.stats_label = QLabel(stats, self)
        self.stats_label.setFont(mono)
        self.stats_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.stats_label.setStyleSheet(
            f"color: {theme.token('fg.muted')}; background: transparent;"
        )
        # the grid is fixed: every row lines its numbers up with the next
        # an over-long duration ("10d15h55m0s") widens this one row rather
        # than clipping the sample rate unit off the end of the grid:
        self.stats_label.setFixedWidth(
            max(
                metrics.horizontalAdvance(
                    "0"
                    * (self.CHANNEL_CHARS + self.DURATION_CHARS + self.RATE_CHARS + 2)
                ),
                metrics.horizontalAdvance(stats),
            )
        )
        meta_row.addWidget(self.stats_label, 0)
        self.path_label = QLabel(self)
        self.path_label.setFont(mono)
        self.path_label.setStyleSheet(
            f"color: {theme.token('fg.muted')}; background: transparent;"
        )
        self.path_label.setMinimumWidth(0)
        self.path_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        meta_row.addWidget(self.path_label, 1)
        vbox.addLayout(meta_row)

        self.path_full = entry.get("parent", "") or entry.get("path", "")
        self.path_label.setText(self.path_full)
        self.setToolTip(entry.get("path", ""))
        self.setMinimumHeight(self.layout().sizeHint().height())

    def sizeHint(self):
        return self.layout().sizeHint()

    def minimumSizeHint(self):
        return self.layout().sizeHint()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        metrics = QFontMetrics(self.name_label.font())
        self.name_label.setText(
            metrics.elidedText(
                self.entry.get("name", "?"),
                Qt.ElideRight,
                max(0, self.name_label.width()),
            )
        )
        self.path_label.setText(
            self.elide_path(
                self.path_full,
                QFontMetrics(self.path_label.font()),
                max(0, self.path_label.width()),
            )
        )

    @classmethod
    def stats_text(cls, entry) -> str:
        """The three right aligned metadata columns of one row."""
        channels = entry.get("channels")
        duration = entry.get("duration")
        rate = entry.get("rate")
        return " ".join(
            (
                f"{f'{channels}ch' if channels else '-':>{cls.CHANNEL_CHARS}}",
                f"{secs_to_str(duration, 0) if duration else '-':>{cls.DURATION_CHARS}}",
                f"{f'{rate / 1000:.4g} kHz' if rate else '-':>{cls.RATE_CHARS}}",
            )
        )

    @staticmethod
    def elide_path(path: str, metrics: QFontMetrics, width: int) -> str:
        """Shorten `path` to `width`, cutting only at separators.

        The first and the last component always survive, so the row still
        says which tree and which directory the file lives in.  Qt's own
        ElideMiddle cuts wherever it likes, which is how "20 kHz" became
        "20 kH".
        """
        if width <= 0 or not path:
            return ""
        if metrics.horizontalAdvance(path) <= width:
            return path
        sep = os.sep
        lead = sep if path.startswith(sep) else ""
        parts = [p for p in path.split(sep) if p]
        for drop in range(1, max(1, len(parts) - 1)):
            kept = parts[:1] + ["…"] + parts[1 + drop :]
            text = lead + sep.join(kept)
            if metrics.horizontalAdvance(text) <= width:
                return text
        # even first/…/last does not fit: shorten that, never the grid
        return metrics.elidedText(
            lead + sep.join(parts[:1] + ["…"] + parts[-1:]), Qt.ElideMiddle, width
        )


def settings_path() -> Path:
    """Where the few persistent preferences live.

    Config rather than cache: a wiped cache must cost the user nothing but
    recomputation, and a theme choice is not recomputable.
    """
    return audian_dirs.user_config_path / "settings.json"


def settings() -> dict:
    """Read the preferences file.  Never raises; a broken file reads empty."""
    try:
        path = settings_path()
        if path.exists():
            with open(path) as sf:
                values = json.load(sf)
            if isinstance(values, dict):
                return values
    except (OSError, ValueError) as e:
        log.debug("could not read settings: %s", e)
    return {}


def save_setting(key: str, value) -> None:
    """Update one preference in place.  Never raises."""
    values = settings()
    values[key] = value
    try:
        audian_dirs.user_config_path.mkdir(parents=True, exist_ok=True)
        with open(settings_path(), "w") as df:
            json.dump(values, df, indent=2)
    except OSError as e:
        log.debug("could not write settings: %s", e)


class StartupPage(QWidget):
    """The empty state: no tab, no close button, three centred columns."""

    # the three columns cap out here; the recent column needs room for a
    # path, which is the one field that cannot be abbreviated for free
    max_width = 1100

    def __init__(self, gui):
        super().__init__(gui)
        self.gui = gui
        self.drag_active = False
        self.setAutoFillBackground(True)
        self.setStyleSheet(f"StartupPage {{ background: {theme.token('bg.base')}; }}")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(theme.S24, theme.S24, theme.S24, theme.S24)
        outer.addStretch(1)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        outer.addLayout(row)
        outer.addStretch(1)
        row.addStretch(1)
        content = QWidget(self)
        # the drop tint must wash over the columns, not stop at an opaque
        # block of bg.base:
        make_transparent(content, "audian_startup_content")
        content.setMaximumWidth(self.max_width)
        # Stretch, not AlignCenter: the recent rows elide themselves and so
        # contribute no width of their own, and a hint-sized content widget
        # would collapse the whole block to the width of its longest key
        # chip.  The maximum width still caps it at `max_width`.
        row.addWidget(content, 6)
        row.addStretch(1)

        columns = QHBoxLayout(content)
        columns.setContentsMargins(0, 0, 0, 0)
        columns.setSpacing(theme.S24)
        columns.addLayout(self.build_left(), 4)
        columns.addLayout(self.build_recent(), 6)
        columns.addLayout(self.build_keys(), 3)

    # -- columns ---------------------------------------------------------

    def build_left(self):
        vbox = QVBoxLayout()
        vbox.setSpacing(theme.S8)
        title = QLabel(f"Audian {__version__}", self)
        title.setFont(theme.font_ui(24))
        title.setStyleSheet(f"color: {theme.token('fg')};")
        vbox.addWidget(title)
        subtitle = QLabel(
            "Browse and analyse recordings of animal vocalizations.", self
        )
        subtitle.setFont(theme.font_ui())
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet(f"color: {theme.token('fg.muted')};")
        vbox.addWidget(subtitle)
        vbox.addSpacing(theme.S8)
        button = QPushButton("Open files…\tCtrl+O", self)
        button.setFont(theme.font_ui())
        button.setDefault(True)
        button.setStyleSheet(
            "QPushButton {"
            f"color: {theme.token('bg.base')};"
            f"background: {theme.token('primary')};"
            f"border: {theme.HAIRLINE}px solid {theme.token('primary')};"
            f"border-radius: {theme.RADIUS_CONTROL}px;"
            f"padding: {theme.S6}px {theme.S12}px; }}"
            "QPushButton:hover {"
            f"background: {theme.token('primary.dim')};"
            f"border-color: {theme.token('primary.dim')}; }}"
        )
        button.clicked.connect(self.gui.open_files)
        vbox.addWidget(button, 0, Qt.AlignLeft)
        # the dashed frame around the page is a drop target, so it has to
        # say so - an unlabelled dashed rectangle advertises nothing:
        self.drop_label = QLabel("Drop .wav files here", self)
        self.drop_label.setFont(theme.font_ui(theme.SIZE_SMALL_PT))
        self.drop_label.setStyleSheet(f"color: {theme.token('fg.muted')};")
        vbox.addWidget(self.drop_label, 0, Qt.AlignLeft)
        vbox.addStretch(1)
        return vbox

    def build_recent(self):
        vbox = QVBoxLayout()
        vbox.setSpacing(theme.S4)
        vbox.addWidget(self.section_label("RECENT"))
        self.recent_box = QVBoxLayout()
        self.recent_box.setSpacing(0)
        vbox.addLayout(self.recent_box)
        vbox.addStretch(1)
        self.reload()
        return vbox

    def build_keys(self):
        vbox = QVBoxLayout()
        vbox.setSpacing(theme.S4)
        vbox.addWidget(self.section_label("GET STARTED"))
        keys = [
            ("Ctrl+O", "open files"),
            ("Space", "play the window"),
            ("F2 … F6", "show or hide panels"),
            ("Alt+1 … Alt+0", "toggle a channel"),
            ("Ctrl+Shift+P", "command palette"),
            ("?", "all shortcuts"),
        ]
        grid = QGridLayout()
        grid.setHorizontalSpacing(theme.S8)
        grid.setVerticalSpacing(theme.S6)
        for i, (key, what) in enumerate(keys):
            chip = QLabel(key, self)
            chip.setFont(theme.font_mono(theme.SIZE_SMALL_PT))
            chip.setStyleSheet(chip_style())
            grid.addWidget(chip, i, 0, Qt.AlignLeft | Qt.AlignVCenter)
            desc = QLabel(what, self)
            desc.setFont(theme.font_ui(theme.SIZE_SMALL_PT))
            desc.setStyleSheet(f"color: {theme.token('fg.muted')};")
            grid.addWidget(desc, i, 1, Qt.AlignLeft | Qt.AlignVCenter)
        vbox.addLayout(grid)
        vbox.addStretch(1)
        return vbox

    def section_label(self, text) -> QLabel:
        label = QLabel(text, self)
        font = theme.font_mono(theme.SIZE_SMALL_PT, bold=True)
        label.setFont(font)
        label.setStyleSheet(f"color: {theme.token('fg.muted')};")
        return label

    # -- content ---------------------------------------------------------

    def reload(self) -> None:
        """Rebuild the recent-files column from the persisted list."""
        if not hasattr(self, "recent_box"):
            return
        while self.recent_box.count() > 0:
            item = self.recent_box.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self.gui.recent.load()
        entries = self.gui.recent.entries
        if len(entries) == 0:
            empty = QLabel("No files opened yet.", self)
            empty.setFont(theme.font_ui(theme.SIZE_SMALL_PT))
            empty.setStyleSheet(f"color: {theme.token('fg.muted')};")
            self.recent_box.addWidget(empty)
            return
        for entry in entries[: RecentFiles.max_entries]:
            row = RecentRow(entry, self)
            row.clicked.connect(lambda x=0, e=entry: self.gui.load_files([e["path"]]))
            self.recent_box.addWidget(row)

    DROP_IDLE = "Drop .wav files here"
    DROP_ACTIVE = "Release to open"
    DROP_FILL_ALPHA = 0.12

    def set_drag_active(self, active: bool) -> None:
        active = bool(active)
        if active != self.drag_active:
            self.drag_active = active
            if hasattr(self, "drop_label"):
                color = "primary" if active else "fg.muted"
                self.drop_label.setText(self.DROP_ACTIVE if active else self.DROP_IDLE)
                self.drop_label.setStyleSheet(f"color: {theme.token(color)};")
            self.update()

    def paintEvent(self, ev):
        super().paintEvent(ev)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        color = "primary" if self.drag_active else "border.hi"
        painter.setPen(theme.pen(color, theme.LW_THICK, style=Qt.DashLine))
        if self.drag_active:
            painter.setBrush(theme.brush("primary", self.DROP_FILL_ALPHA))
        else:
            painter.setBrush(Qt.NoBrush)
        m = theme.S12
        painter.drawRoundedRect(
            QRectF(m, m, self.width() - 2 * m, self.height() - 2 * m),
            theme.RADIUS_OVERLAY,
            theme.RADIUS_OVERLAY,
        )
        painter.end()


def fuzzy_score(pattern: str, text: str):
    """Subsequence score of `pattern` in `text`, or None if it does not match.

    Lower is better: consecutive and word-initial matches score best.
    """
    if not pattern:
        return 0
    pattern = pattern.lower()
    low = text.lower()
    score = 0
    pos = -1
    for ch in pattern:
        nxt = low.find(ch, pos + 1)
        if nxt < 0:
            return None
        gap = nxt - pos - 1
        if gap > 0 and not (nxt > 0 and low[nxt - 1] in " \t›-_"):
            score += gap
        pos = nxt
    return score + len(text) // 20


def action_keys(act) -> str:
    return ", ".join(key.toString() for key in act.shortcuts())


class CommandPalette(QDialog):
    """Fuzzy search over every menu action, executed with Enter."""

    def __init__(self, gui):
        super().__init__(gui)
        self.gui = gui
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowModality(Qt.NonModal)
        self.setWindowTitle("Audian commands")
        self.entries = [
            (act, path, f"{act.text().replace('&', '')}  {path}")
            for act, path in gui.all_actions()
            if act.isEnabled()
        ]

        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(theme.S12, theme.S12, theme.S12, theme.S12)
        vbox.setSpacing(theme.S8)
        self.edit = QLineEdit(self)
        self.edit.setFont(theme.font_ui())
        self.edit.setPlaceholderText("Type a command…")
        self.edit.textChanged.connect(self.refilter)
        self.edit.returnPressed.connect(self.run_current)
        self.edit.installEventFilter(self)
        vbox.addWidget(self.edit)
        self.list = QListWidget(self)
        self.list.setFont(theme.font_ui())
        self.list.setAlternatingRowColors(False)
        self.list.itemActivated.connect(lambda item: self.run_current())
        self.list.itemClicked.connect(lambda item: self.run_current())
        vbox.addWidget(self.list, 1)
        self.refilter("")
        self.resize(720, 420)
        self.edit.setFocus()

    def refilter(self, pattern: str) -> None:
        scored = []
        for act, path, hay in self.entries:
            score = fuzzy_score(pattern, hay)
            if score is not None:
                scored.append((score, act, path))
        scored.sort(key=lambda e: (e[0], e[1].text()))
        self.list.clear()
        for score, act, path in scored[:200]:
            keys = action_keys(act)
            text = act.text().replace("&", "")
            label = f"{text}    ·  {path}" if path else text
            if keys:
                label += f"    [{keys}]"
            item = QListWidgetItem(label, self.list)
            item.setData(Qt.UserRole, act)
        if self.list.count() > 0:
            self.list.setCurrentRow(0)

    def eventFilter(self, obj, ev):
        if obj is self.edit and ev.type() == QEvent.KeyPress:
            if ev.key() in (Qt.Key_Down, Qt.Key_Up):
                row = self.list.currentRow()
                row += 1 if ev.key() == Qt.Key_Down else -1
                if 0 <= row < self.list.count():
                    self.list.setCurrentRow(row)
                return True
        return super().eventFilter(obj, ev)

    def run_current(self) -> None:
        item = self.list.currentItem()
        if item is None:
            return
        act = item.data(Qt.UserRole)
        self.close()
        if act is not None:
            act.trigger()


class CheatSheet(QDialog):
    """A translucent overlay grouping the keys by what they are used for."""

    GROUPS = (
        (
            "Navigate",
            (
                "time_home",
                "time_end",
                "time_up",
                "time_down",
                "time_small_up",
                "time_small_down",
                "time_snap",
                "auto_scroll",
                "play_window",
            ),
        ),
        (
            "Zoom",
            (
                "zoom_amplitude_in",
                "zoom_amplitude_out",
                "zoom_frequency_in",
                "zoom_frequency_out",
                "time_zoom_in_centered",
                "time_zoom_out_centered",
                "auto_zoom_amplitude",
                "reset_amplitude",
                "center_amplitude",
                "zoom_back",
                "zoom_forward",
                "zoom_home",
            ),
        ),
        (
            "Filter",
            (
                "highpass_up",
                "highpass_down",
                "lowpass_up",
                "lowpass_down",
                "show_envelope",
                "envelope_up",
                "envelope_down",
            ),
        ),
        (
            "Spectrogram",
            (
                "frequency_resolution_up",
                "frequency_resolution_down",
                "overlap_up",
                "overlap_down",
                "color_map_cycler",
                "power_up",
                "power_down",
                "max_power_up",
                "max_power_down",
                "min_power_up",
                "min_power_down",
            ),
        ),
        (
            "Channels",
            (
                "next_channel",
                "previous_channel",
                "select_next_channel",
                "select_previous_channel",
                "select_all_channels",
                "hide_deselected_channels",
            ),
        ),
        (
            "Fixed labels",
            (
                "toggle_annotations",
                "show_all_annotation_layers",
                "next_annotation",
                "previous_annotation",
                "load_annotations",
            ),
        ),
        (
            "Regions",
            (
                "zoom_region",
                "play_region",
                "analyze_region",
                "save_region",
                "ask_region",
                "label_region",
                "rect_zoom",
                "pan_zoom",
                "cross_hair",
                "analysis_results",
            ),
        ),
        (
            "Editable labels",
            (
                "label_region",
                "toggle_labels",
                "label_editor",
                "label_table",
                "delete_label",
                "undo_label",
            ),
        ),
    )

    def __init__(self, gui):
        super().__init__(gui)
        self.gui = gui
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowModality(Qt.NonModal)
        self.setWindowTitle("Audian keys")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(theme.S12, theme.S12, theme.S12, theme.S12)
        panel = QFrame(self)
        panel.setObjectName("cheatsheet")
        panel.setStyleSheet(
            "#cheatsheet {"
            f"background: {theme.token('bg.raised')};"
            f"border: {theme.HAIRLINE}px solid {theme.token('border')};"
            f"border-radius: {theme.RADIUS_OVERLAY}px; }}"
        )
        outer.addWidget(panel)
        self.setWindowOpacity(0.97)

        grid = QGridLayout(panel)
        grid.setContentsMargins(theme.S16, theme.S16, theme.S16, theme.S16)
        grid.setHorizontalSpacing(theme.S24)
        grid.setVerticalSpacing(theme.S6)
        for col, (title, names) in enumerate(self.GROUPS):
            row = 0
            header = QLabel(title.upper(), panel)
            header.setFont(theme.font_mono(theme.SIZE_SMALL_PT, bold=True))
            header.setStyleSheet(
                f"color: {theme.token('fg.muted')};background: transparent;"
            )
            grid.addWidget(header, row, 2 * col, 1, 2)
            row += 1
            for name in names:
                act = getattr(gui.acts, name, None)
                if act is None or not act.isVisible():
                    continue
                keys = action_keys(act)
                if not keys:
                    continue
                chip = QLabel(keys, panel)
                chip.setFont(theme.font_mono(theme.SIZE_SMALL_PT))
                chip.setStyleSheet(chip_style())
                grid.addWidget(chip, row, 2 * col, Qt.AlignLeft | Qt.AlignVCenter)
                desc = QLabel(act.text().replace("&", ""), panel)
                desc.setFont(theme.font_ui(theme.SIZE_SMALL_PT))
                desc.setStyleSheet(
                    f"color: {theme.token('fg')};background: transparent;"
                )
                grid.addWidget(desc, row, 2 * col + 1, Qt.AlignLeft | Qt.AlignVCenter)
                row += 1
            grid.setRowStretch(row, 1)
        self.adjustSize()

    def keyPressEvent(self, ev):
        if ev.key() in (Qt.Key_Escape, Qt.Key_Question):
            self.close()
            return
        super().keyPressEvent(ev)


class ShortcutsDialog(QDialog):
    """Searchable list of every shortcut, with per-row rebinding."""

    def __init__(self, gui):
        super().__init__(gui)
        self.gui = gui
        self.setAttribute(Qt.WA_DeleteOnClose)
        # browsable, not modal: the user compares it against the running app
        self.setWindowModality(Qt.NonModal)
        self.setWindowTitle("Audian Key Shortcuts")

        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(theme.S12, theme.S12, theme.S12, theme.S12)
        vbox.setSpacing(theme.S8)
        self.search = QLineEdit(self)
        self.search.setPlaceholderText("Search shortcuts…")
        self.search.setFont(theme.font_ui())
        self.search.textChanged.connect(self.refilter)
        vbox.addWidget(self.search)

        scrollarea = QScrollArea(self)
        scrollarea.setWidgetResizable(True)
        vbox.addWidget(scrollarea, 1)
        widget = QWidget()
        grid = QGridLayout(widget)
        grid.setContentsMargins(theme.S8, theme.S8, theme.S8, theme.S8)
        grid.setHorizontalSpacing(theme.S16)
        grid.setVerticalSpacing(theme.S4)
        self.rows = []
        row = 0
        for act, path in gui.all_actions():
            name = QLabel(act.text().replace("&", ""), widget)
            name.setFont(theme.font_ui())
            where = QLabel(path, widget)
            where.setFont(theme.font_ui(theme.SIZE_SMALL_PT))
            where.setStyleSheet(f"color: {theme.token('fg.muted')};")
            edit = QKeySequenceEdit(act.shortcut(), widget)
            edit.setFont(theme.font_mono(theme.SIZE_SMALL_PT))
            edit.editingFinished.connect(lambda a=act, e=edit: self.rebind(a, e))
            grid.addWidget(name, row, 0)
            grid.addWidget(where, row, 1)
            grid.addWidget(edit, row, 2)
            self.rows.append(
                (f"{act.text()} {path}".replace("&", "").lower(), (name, where, edit))
            )
            row += 1
        grid.setRowStretch(row, 1)
        widget.adjustSize()
        scrollarea.setWidget(widget)

        buttons = QDialogButtonBox(QDialogButtonBox.Close, self)
        buttons.rejected.connect(self.reject)
        vbox.addWidget(buttons)
        # size from the realised layout, and no minimum: a minimum size is
        # the one geometry hint a tiling compositor has to fight.
        self.resize(self.sizeHint().expandedTo(QSize(640, 480)))

    def rebind(self, act, edit) -> None:
        seq = edit.keySequence()
        act.setShortcut(seq)
        self.gui.notify(
            "info",
            f"{act.text().replace('&', '')} → "
            f"{seq.toString() if not seq.isEmpty() else 'unbound'}",
        )

    def refilter(self, pattern: str) -> None:
        pattern = pattern.lower().strip()
        for hay, widgets in self.rows:
            visible = pattern in hay if pattern else True
            for widget in widgets:
                widget.setVisible(visible)


class Audian(QMainWindow):
    def __init__(
        self,
        file_paths,
        load_kwargs,
        plugins,
        channels,
        highpass_cutoff,
        lowpass_cutoff,
        unwrap,
        unwrap_clip,
        events_path=None,
    ):
        super().__init__()

        self.plugins = plugins
        if self.plugins is None:
            self.plugins = Plugins()

        class acts:
            pass

        self.acts = acts

        # (widget, glyph kind) pairs, so a theme switch can rebuild icons
        self._glyph_targets = []
        # last (text, active) per status readout, for re-rendering on a switch
        self._readout_state = {}
        # toolbar separators carry a baked colour and must be restyled too
        self._toolbar_separators = []

        self.browsers = []
        self.prev_browser = None  # for load_data()
        self.file_paths = []

        self.channels = channels
        self.highpass_cutoff = highpass_cutoff
        self.lowpass_cutoff = lowpass_cutoff
        # An explicit --events bundle names one recording, so it is handed to
        # the first browser and then dropped.  Every other file opened in the
        # same run looks for a bundle beside itself instead, which is what the
        # [alignment] name check in session.find_bundle() is for.
        self.events_path = events_path

        self.audio = PlayAudio()

        self.link_timezoom = True
        self.link_timescroll = False
        self.link_ranges = {}
        for s in Panel.amplitudes + Panel.frequencies + Panel.powers:
            self.link_ranges[s] = True
        self.link_filter = True
        self.link_envelope = True
        self.link_channels = True
        self.link_panels = True
        self.link_audio = True

        # notifications and diagnostics:
        self.messages = []  # (level, text) as shown by notify()
        self.error_count = 0
        self.toolbar = None
        self.ampl_button = None
        self.statusbar = None
        self.readouts = {}
        self.mnemonic_style = None
        self.readout_box = None
        self.palette_dialog = None
        self.cheatsheet_dialog = None

        # window: size is a hint only, nothing is persisted or restored -
        # on a tiling compositor the window manager owns the geometry.
        rec = None
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            rec = screen.availableGeometry()
        if rec is not None and rec.width() > 0 and rec.height() > 0:
            self.resize(int(0.7 * rec.width()), int(0.7 * rec.height()))
        else:
            self.resize(1280, 800)
        self.setWindowTitle(f"Audian {__version__}")

        self.tabs = QTabWidget(self)
        # Tabs live down the LEFT edge: vertical space is what a waveform
        # stack is short of, and a horizontal strip spends ~30 px of it on
        # what is usually one or two entries.
        self.tabs.setTabBar(VerticalTabBar(self.tabs))
        self.tabs.setTabPosition(QTabWidget.West)
        self.tabs.setDocumentMode(True)
        self.tabs.setMovable(True)
        # a single file needs no tab strip at all - that is the common case,
        # and hiding it gives the whole width back
        self.tabs.setTabBarAutoHide(True)
        self.tabs.setTabsClosable(False)
        # connect the BAR, not the QTabWidget: the widget only forwards its
        # bar's tabCloseRequested while setTabsClosable(True), and the close
        # marks here are painted by VerticalTabBar rather than by Qt.
        self.tabs.tabBar().tabCloseRequested.connect(self.close)
        self.tabs.currentChanged.connect(self.adapt_menu)

        # page 0 is the empty state, page 1 the browser tabs.  The empty
        # state is deliberately NOT a tab: it had a close button that did
        # nothing.
        self.recent = RecentFiles()
        self.startup = StartupPage(self)
        self.stack = QStackedWidget(self)
        self.stack.addWidget(self.startup)
        self.stack.addWidget(self.tabs)
        # the tool bar is a plain band stacked above the canvas
        self.chrome = QWidget(self)
        make_transparent(self.chrome, "audian_chrome")
        self.chrome_box = QVBoxLayout(self.chrome)
        self.chrome_box.setContentsMargins(0, 0, 0, 0)
        self.chrome_box.setSpacing(0)

        # The chrome/canvas seam lives on the CANVAS, not on the tool bar.
        # A border on the bar is laid over by its own buttons -- a 36 px
        # button in a 37 px bar covers all but the gaps -- and padding it
        # clear costs 5 px of the height the tab strip was moved sideways to
        # save.  One pixel on the canvas edge buys the same line for nothing.
        theme.band(self.stack, top=True, ground="bg.base")
        self.chrome_box.addWidget(self.stack, 1)
        self.setCentralWidget(self.chrome)
        self.startup_active = True

        # actions:
        self.toggle_menu = None
        self.show_menu = None
        self.data_menus = []
        self.data_acts = []
        file_menu = self.setup_file_actions(self.menuBar())
        region_menu = self.setup_region_actions(self.menuBar())
        spec_menu = self.setup_spectrogram_actions(self.menuBar())
        view_menu = self.setup_view_actions(self.menuBar())
        help_menu = self.setup_help_actions(self.menuBar())
        self.menus = [file_menu, region_menu, spec_menu, view_menu, help_menu]
        self.setup_mnemonics()

        # chrome that needs the actions:
        self.setup_statusbar()
        self.setup_toolbar()

        # data:
        self.starttime_mode = 0
        self.save_path = [None]
        self.unwrap = unwrap
        self.unwrap_clip = unwrap_clip
        self.load_kwargs = load_kwargs
        self.load_files(file_paths)

        # init widgets to show:
        if len(self.browsers) > 0:
            self.tabs.setCurrentIndex(0)
            self.hide_startup()
        else:
            self.show_startup()

    def __del__(self):
        if self.audio is not None:
            self.audio.close()

    def set_app_theme(self, name: str) -> None:
        """Switch the whole application between the dark and daylight themes.

        ``theme.apply()`` only reaches the Qt chrome -- palette, font and
        stylesheet.  The plots are a pyqtgraph graphics scene whose pens and
        brushes were resolved when each item was built, so without the walk
        below a switch leaves light menus wrapped around dark plots.
        """
        if name not in (theme.THEME_DARK, theme.THEME_LIGHT):
            return
        app = QApplication.instance()
        if app is None:
            return
        theme.apply(app, name)
        self.refresh_glyph_icons()
        self.restyle_chrome()
        for browser in self.browsers:
            if hasattr(browser, "apply_theme"):
                browser.apply_theme()
        # StartupPage bakes token values into per-widget stylesheets across
        # several builders; rebuilding it is exact, where chasing each one
        # would silently miss whichever gets added next.
        if self.startup is not None:
            was_current = self.stack.currentWidget() is self.startup
            fresh = StartupPage(self)
            self.stack.insertWidget(0, fresh)
            self.stack.removeWidget(self.startup)
            self.startup.deleteLater()
            self.startup = fresh
            self.startup.reload()
            if was_current:
                self.stack.setCurrentWidget(self.startup)
        # last: every stylesheet and icon is in place, so recompute the
        # geometry they imply
        self.repolish()
        self.acts.daylight_mode.setChecked(name == theme.THEME_LIGHT)
        save_setting("theme", name)
        self.statusBar().showMessage(
            "Daylight theme - high contrast for outdoor use"
            if name == theme.THEME_LIGHT
            else "Dark theme",
            2500,
        )

    def toggle_daylight(self) -> None:
        """Flip between the dark and daylight themes (Ctrl+Shift+L)."""
        self.set_app_theme(
            theme.THEME_DARK
            if theme.current_theme() == theme.THEME_LIGHT
            else theme.THEME_LIGHT
        )

    def _set_glyph(self, target, kind: str) -> None:
        """Give `target` a themed glyph icon, and remember the pairing.

        A ``QIcon`` bakes its pixmaps when it is built, so icons do not follow
        a live theme switch on their own.  Recording (target, kind) here is
        what lets :meth:`refresh_glyph_icons` rebuild them.
        """
        self._glyph_targets.append((target, kind))
        target.setIcon(glyph_icon(kind))

    def refresh_glyph_icons(self) -> None:
        """Rebuild every glyph icon from the current token table."""
        for target, kind in self._glyph_targets:
            try:
                target.setIcon(glyph_icon(kind))
            except RuntimeError:
                # the underlying C++ object went away with a closed tab
                continue

    def show_startup(self):
        """Show the empty state instead of the browser tabs."""
        self.startup.reload()
        self.stack.setCurrentWidget(self.startup)
        self.startup_active = True
        for menu in self.data_menus:
            menu.setEnabled(False)
        for act in self.data_acts:
            act.setEnabled(False)
        if self.toolbar is not None:
            self.toolbar.setEnabled(False)
        self.set_mode_chip("")
        for field in self.READOUTS:
            self.set_readout(field, None, False)
        # nothing to read out while no file is open:
        self.set_readouts_visible(False)

    def hide_startup(self):
        """Show the browser tabs instead of the empty state."""
        self.stack.setCurrentWidget(self.tabs)
        self.startup_active = False
        for menu in self.data_menus:
            menu.setEnabled(True)
        for act in self.data_acts:
            act.setEnabled(True)
        if self.toolbar is not None:
            self.toolbar.setEnabled(True)
        self.set_readouts_visible(True)

    # ------------------------------------------------------------------
    # status bar: the single feedback channel of the application
    # ------------------------------------------------------------------

    READOUTS = ("t", "dt", "a", "f", "p", "ch")

    # the widest string each readout ever shows - the fields are sized from
    # these once so that nothing ever reflows while the pointer moves:
    # Measured against what DataBrowser.mouse_moved actually emits, not
    # against a prettier hypothetical: the amplitude goes through '%.5g',
    # so '-1.2345e-05' is a string the field really has to hold.  A field
    # that is one glyph too narrow elides in the middle ('A -0.2859...0'),
    # which is worse than useless on a numeric readout.
    READOUT_TEMPLATES = {
        "t": "t=-00:00:12.480",
        "dt": "Δt=-00:12.480 (1.234mHz)",
        "a": "A -1.23e-05…-1.23e-05 a.u.",
        "f": "Δf=-1.234e+05mHz",
        "p": "ΔP=-100.0dB",
        "ch": "ch 15",
    }

    READOUT_PLACEHOLDERS = {
        "t": "t --:--.---",
        "dt": "Δt --",
        "a": "A --",
        "f": "f --",
        "p": "P --",
        "ch": "ch --",
    }

    NOTIFY_COLORS = {
        "info": "fg",
        "success": "success",
        "warning": "accent",
        "error": "danger",
    }

    MODE_NAMES = {
        DataBrowser.MODE_ZOOM: "Zoom",
        DataBrowser.MODE_PLAY: "Play",
        DataBrowser.MODE_ANALYZE: "Analyze",
        DataBrowser.MODE_SAVE: "Save",
        DataBrowser.MODE_ASK: "Ask",
        DataBrowser.MODE_LABEL: "Label",
    }

    def setup_mnemonics(self) -> None:
        """Hide the menu bar underlines until the user holds Alt."""
        bar = self.menuBar()
        self.mnemonic_style = MnemonicStyle(bar)
        bar.setStyle(self.mnemonic_style)
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:
        self.track_mnemonics(event)
        return super().eventFilter(obj, event)

    def track_mnemonics(self, event) -> None:
        """Follow the Alt key so that the menu bar can reveal its mnemonics."""
        style = self.mnemonic_style
        if style is None:
            return
        kind = event.type()
        if kind == QEvent.KeyPress and event.key() == Qt.Key_Alt:
            reveal = True
        elif kind == QEvent.KeyRelease and event.key() == Qt.Key_Alt:
            # keep them while the user is walking an open menu
            reveal = QApplication.activePopupWidget() is not None
        elif kind in (QEvent.MouseButtonPress, QEvent.WindowDeactivate):
            reveal = False
        else:
            return
        if reveal != style.reveal:
            style.reveal = reveal
            self.menuBar().update()

    def setup_statusbar(self) -> None:
        # NOTE: no inline stylesheet here.  The bar used to carry its own
        # QSS, which shadowed the QStatusBar rules of theme.stylesheet()
        # and took the readouts out of the token pipeline entirely.
        bar = QStatusBar(self)
        bar.setSizeGripEnabled(False)
        bar.setFont(theme.font_ui(theme.SIZE_SMALL_PT))
        self.setStatusBar(bar)
        self.statusbar = bar

        # transient message, left aligned, colour carries the level:
        self.message_label = QLabel("", bar)
        self.message_label.setFont(theme.font_ui(theme.SIZE_SMALL_PT))
        self.message_label.setStyleSheet(f"color: {theme.token('fg.muted')};")
        self.message_label.setWordWrap(False)
        # Ignored, and elided by hand, which is what every other elastic
        # label in this application already does (`annotation_hoverw`, the
        # Labels file row, `progress_label`, `RecentRow`'s path).  This one
        # was missed, and it is the worst place to miss it: a QLabel at the
        # default Preferred policy reports its whole text as its minimum
        # width, and it sits in the status bar's stretch slot, so the bar
        # takes that as ITS minimum and the window cannot be narrower than
        # the longest thing audian has said in the last four seconds.
        # Measured: a 137 character "can not read annotations from ..." took
        # the status bar's minimum from 1184 px to 2148 and the window with
        # it.
        self.message_label.setMinimumWidth(0)
        self.message_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        #: the unelided line, for the tool tip and for re-eliding on a resize
        self._message_full = ""
        bar.addWidget(self.message_label, 1)
        self.message_timer = QTimer(self)
        self.message_timer.setSingleShot(True)
        self.message_timer.timeout.connect(self.clear_message)
        # a bar left sitting at 100% is just an unlabelled sliver of
        # chrome; a finished job clears the slot by itself:
        self.progress_timer = QTimer(self)
        self.progress_timer.setSingleShot(True)
        self.progress_timer.timeout.connect(lambda: self.set_progress(None))

        # six fixed width mono readouts on a 16px rhythm, hairline
        # separated.  They live in one container so that the whole row can
        # be hidden while no file is open:
        fm = theme.mono_metrics(theme.SIZE_SMALL_PT)
        self.readout_box = QWidget(bar)
        make_transparent(self.readout_box, "audian_readouts")
        rbox = QHBoxLayout(self.readout_box)
        rbox.setContentsMargins(0, 0, 0, 0)
        rbox.setSpacing(0)
        self._readout_separators = {}
        for i, field in enumerate(self.READOUTS):
            if i > 0:
                rule = StatusSeparator(self.readout_box)
                rbox.addWidget(rule)
                # kept, because hiding a field without its rule leaves a 16 px
                # slot of nothing where the field used to be
                self._readout_separators[field] = rule
            label = QLabel(self.readout_box)
            label.setFont(theme.font_mono(theme.SIZE_SMALL_PT))
            label.setTextFormat(Qt.RichText)
            label.setWordWrap(False)
            label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            # sized from the widest string the field can ever show, so that
            # a moving pointer never reflows the bar:
            label.setFixedWidth(
                fm.horizontalAdvance(self.READOUT_TEMPLATES[field]) + theme.S12
            )
            rbox.addWidget(label)
            self.readouts[field] = label
        bar.addPermanentWidget(self.readout_box)
        for field in self.READOUTS:
            self.set_readout(field, None, False)
        # the cross hair starts off (DataBrowser.__init__), so these start
        # hidden rather than showing three dashes until it is turned on
        self.set_crosshair_readouts_visible(False)

        # persistent error indicator, opens the log:
        self.error_button = QToolButton(bar)
        self.error_button.setFont(theme.font_mono(theme.SIZE_SMALL_PT))
        self.error_button.setCursor(Qt.PointingHandCursor)
        self.error_button.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.error_button.setStyleSheet(
            "QToolButton {"
            f"color: {theme.token('danger')};"
            f"border: {theme.HAIRLINE}px solid {theme.token('danger')};"
            f"border-radius: {theme.RADIUS_CONTROL}px;"
            f"padding: 0px {theme.S6}px; background: transparent; }}"
        )
        self.error_button.clicked.connect(self.show_log)
        self.error_button.setVisible(False)
        bar.addPermanentWidget(self.error_button)

        # progress slot: fixed width so that showing it never reflows, and
        # never an unlabelled bar - the label names the job and carries the
        # percentage, the bar alone would say nothing:
        progress_box = QWidget(bar)
        make_transparent(progress_box, "audian_progress")
        progress_box.setFixedWidth(10 * theme.S16)
        # Hidden until there is a job.  `set_progress(None)` used to hide only
        # the bar inside it and blank the label, leaving the 160 px container
        # standing for the life of the session -- 160 px of the status bar's
        # minimum width, reserved for something that is almost never running.
        self.progress_box = progress_box
        progress_box.setVisible(False)
        pbox = QHBoxLayout(progress_box)
        pbox.setContentsMargins(0, 0, 0, 0)
        pbox.setSpacing(theme.S6)
        self.progress_label = QLabel("", progress_box)
        self.progress_label.setFont(theme.font_ui(theme.SIZE_SMALL_PT))
        self.progress_label.setStyleSheet(f"color: {theme.token('fg.muted')};")
        self.progress_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        pbox.addWidget(self.progress_label, 1)
        self.progress_bar = QProgressBar(progress_box)
        self.progress_bar.setFixedWidth(6 * theme.S12)
        self.progress_bar.setFixedHeight(theme.S8)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setVisible(False)
        self.progress_bar.setAccessibleName("progress")
        pbox.addWidget(self.progress_bar, 0)
        bar.addPermanentWidget(progress_box)

        # mode chip, far right:
        self.mode_chip = QLabel("", bar)
        self.mode_chip.setFont(theme.font_mono(theme.SIZE_SMALL_PT))
        self.mode_chip.setAlignment(Qt.AlignCenter)
        self.mode_chip.setFixedWidth(fm.horizontalAdvance("Analyze") + 4 * theme.S8)
        self.mode_chip.setStyleSheet(chip_style("fg.muted"))
        self.mode_chip.setVisible(False)
        bar.addPermanentWidget(self.mode_chip)

    def set_readouts_visible(self, visible: bool) -> None:
        """Show the numeric readout row only while a file is open."""
        if self.statusbar is None:
            return
        self.readout_box.setVisible(bool(visible))

    #: Readouts that only ever carry a value while the cross hair is on.
    #: Every write to them in `DataBrowser.mouse_moved` is inside
    #: ``if self.cross_hair:``, and the cross hair is off by default and is
    #: not persisted -- so in a default session these three read
    #: ``Δt --  f --  P --`` from the first file to the last.  Measured, they
    #: and their rules are 482 px of a 909 px row: more than half of it,
    #: carrying nothing.
    CROSSHAIR_READOUTS = ("dt", "f", "p")

    def set_crosshair_readouts_visible(self, visible: bool) -> None:
        """Show or hide the three readouts only the cross hair fills.

        The field AND the rule in front of it, or hiding the labels alone
        leaves three empty 16 px slots where they were.  Nothing is lost by
        hiding one: `set_readout` records every value it is given and
        `refresh_readouts` replays it, so a field that comes back is current.
        """
        if self.statusbar is None:
            return
        visible = bool(visible)
        for field in self.CROSSHAIR_READOUTS:
            label = self.readouts.get(field)
            if label is not None:
                label.setVisible(visible)
            rule = self._readout_separators.get(field)
            if rule is not None:
                rule.setVisible(visible)
        layout = self.readout_box.layout()
        if layout is not None:
            # without this the row keeps the width it was measured at and
            # the hiding buys nothing
            layout.invalidate()
            layout.activate()
        self.readout_box.updateGeometry()

    # -- the additive API the browser calls through hasattr guards --------

    @staticmethod
    def split_readout(text: str) -> tuple[str, str]:
        """Split a readout into its key and its value.

        The browser sends whole strings like ``t=00:00:12.480`` or
        ``ch 07``; the key is everything up to and including the first
        ``=`` or the first blank, so that the two halves can be coloured
        apart without changing the caller's API.
        """
        for sep in ("=", " "):
            cut = text.find(sep)
            if cut >= 0:
                return text[: cut + 1], text[cut + 1 :]
        return "", text

    @staticmethod
    def readout_markup(part: str, color: str) -> str:
        """One half of a readout, with its blanks kept at mono width."""
        escaped = html.escape(part).replace(" ", "&nbsp;")
        return f"<span style='color:{theme.token(color)}'>{escaped}</span>"

    def set_readout(self, field: str, text=None, active: bool = True) -> None:
        """Update one status bar readout.

        `field` is one of 't', 'dt', 'a', 'f', 'p', 'ch'.  Passing
        `active=False` dims the value to fg.muted instead of clearing it,
        so the status bar never reflows.

        NOTE: the design brief asked for fg.faint keys.  A key ("Δt", "A",
        "ch") is not decoration, it is the label that says what the number
        means, and fg.faint scores 3.99:1 on bg.surface - exactly the
        failure this readout row was reported for.  The key is fg.muted
        (7.4:1) and the live value is fg (15.4:1) instead, so the hierarchy
        is carried by brightness and nothing in the row is below 4.5:1.
        """
        label = self.readouts.get(field)
        if label is None:
            log.debug("unknown status readout %r", field)
            return
        if text is None:
            text = self.READOUT_PLACEHOLDERS[field]
            active = False
        # the markup carries baked colour strings, so the last state is kept
        # and re-rendered on a theme switch (see refresh_readouts)
        self._readout_state[field] = (text, active)
        key, value = self.split_readout(str(text))
        label.setText(
            self.readout_markup(key, "fg.muted")
            + self.readout_markup(value, "fg" if active else "fg.muted")
        )

    def set_message(self, message: str) -> None:
        """Show `message` in the status bar, elided to the room it has.

        The whole line stays in the tool tip, so nothing said is lost --
        which is the rule the folded label chips follow too.
        """
        self._message_full = str(message)
        self.message_label.setToolTip(self._message_full)
        self.elide_message()

    def elide_message(self) -> None:
        """Re-cut the transient message to the width the slot has now."""
        label = getattr(self, "message_label", None)
        if label is None:
            return
        metrics = QFontMetrics(label.font())
        label.setText(
            metrics.elidedText(self._message_full, Qt.ElideRight, max(label.width(), 1))
        )

    def clear_message(self) -> None:
        """Drop the transient message, tool tip and all.

        The tool tip has to go with the text, or a line the reader can no
        longer see is still reachable by hovering the empty slot.
        """
        self._message_full = ""
        self.message_label.setToolTip("")
        self.message_label.setText("")

    def notify(self, level: str, message: str) -> None:
        """Report something to the user in the status bar.

        info/success/warning are transient (4 s).  error additionally
        raises a persistent indicator that opens the message log.
        """
        level = str(level).lower()
        if level not in self.NOTIFY_COLORS:
            level = "info"
        message = str(message)
        self.messages.append((level, message))
        del self.messages[:-500]
        log.log(
            {
                "info": logging.INFO,
                "success": logging.INFO,
                "warning": logging.WARNING,
                "error": logging.ERROR,
            }[level],
            "%s",
            message,
        )
        if self.statusbar is None:
            return
        color = theme.token(self.NOTIFY_COLORS[level])
        self.message_label.setStyleSheet(f"color: {color};")
        self.set_message(message)
        self.message_timer.start(4000)
        if level == "error":
            self.error_count += 1
            n = self.error_count
            self.error_button.setText(f"{n} error" + ("s" if n > 1 else ""))
            self.error_button.setToolTip("Show the message log")
            self.error_button.setVisible(True)

    def set_mode_chip(self, text: str) -> None:
        """Set the right hand mode chip of the status bar."""
        if self.statusbar is None:
            return
        text = str(text) if text else ""
        self.mode_chip.setText(text)
        # an empty chip is a bordered box that says nothing:
        self.mode_chip.setVisible(bool(text))
        self.mode_chip.setStyleSheet(chip_style("fg", "border.hi"))

    def set_progress(self, fraction=None, text: str = "") -> None:
        """Drive the status bar progress slot.

        `fraction` is 0..1, or None to clear the slot.
        """
        if self.statusbar is None:
            return
        if fraction is None:
            self.progress_timer.stop()
            self.progress_bar.setVisible(False)
            self.progress_label.setText("")
            self.progress_bar.setToolTip("")
            self.progress_box.setVisible(False)
            return
        self.progress_box.setVisible(True)
        try:
            value = int(100 * float(fraction))
        except (TypeError, ValueError):
            return
        value = max(0, min(100, value))
        self.progress_bar.setValue(value)
        self.progress_bar.setVisible(True)
        # the bar alone is an unlabelled sliver; the label names the job
        # and repeats the number:
        label = f"{text} {value:d}%" if text else f"{value:d}%"
        metrics = theme.ui_metrics(theme.SIZE_SMALL_PT)
        self.progress_label.setText(
            metrics.elidedText(label, Qt.ElideRight, self.progress_label.width())
        )
        self.progress_bar.setToolTip(label)
        if value >= 100:
            self.progress_timer.start(4 * theme.MOTION_MS)
        else:
            self.progress_timer.stop()

    def show_log(self) -> None:
        """Show every message notify() has seen."""
        dialog = QDialog(self)
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.setWindowModality(Qt.NonModal)
        dialog.setWindowTitle("Audian messages")
        vbox = QVBoxLayout(dialog)
        view = QPlainTextEdit(dialog)
        view.setReadOnly(True)
        view.setFont(theme.font_mono(theme.SIZE_SMALL_PT))
        view.setPlainText(
            "\n".join(f"{level:8s} {text}" for level, text in self.messages)
        )
        vbox.addWidget(view)
        buttons = QDialogButtonBox(QDialogButtonBox.Close, dialog)
        buttons.rejected.connect(dialog.reject)
        vbox.addWidget(buttons)
        dialog.resize(720, 420)
        dialog.show()
        self.error_count = 0
        self.error_button.setVisible(False)

    # ------------------------------------------------------------------
    # application tool bar
    # ------------------------------------------------------------------

    def toolbar_gap(self) -> None:
        """A 12px gap, a hairline, another 12px gap.

        The two spacers are kept so that a narrow bar can collapse them and
        leave the rules: measured, the six of them are 96 px including the
        layout spacing they take with them, which is one whole group's worth
        of buttons for nothing but air.
        """
        left = QWidget(self.toolbar_content)
        make_transparent(left, "audian_toolbar_gap")
        left.setFixedWidth(theme.S12)
        self._toolbar_gaps.append(left)
        self.toolbar_box.addWidget(left)
        line = QFrame(self.toolbar_content)
        line.setFrameShape(QFrame.VLine)
        line.setFixedWidth(theme.HAIRLINE)
        line.setStyleSheet(f"background: {theme.token('border')};border: none;")
        self._toolbar_separators.append(line)
        self.toolbar_box.addWidget(line)
        right = QWidget(self.toolbar_content)
        make_transparent(right, "audian_toolbar_gap")
        right.setFixedWidth(theme.S12)
        self._toolbar_gaps.append(right)
        self.toolbar_box.addWidget(right)

    def repolish(self) -> None:
        """Recompute every widget's style and size hint after a re-theme.

        Setting a stylesheet repaints, but it does not re-run the style's
        size calculations for widgets that were already laid out: the
        transport buttons came out 37x21 after a switch against 37x32 when
        the theme was set before the window was built.  Unpolishing and
        re-polishing each widget is what makes the two paths agree.
        """
        style = self.style()
        for widget in [self] + self.findChildren(QWidget):
            try:
                style.unpolish(widget)
                style.polish(widget)
                widget.updateGeometry()
            except RuntimeError:
                continue
        for layout_owner in [self] + self.findChildren(QWidget):
            try:
                layout = layout_owner.layout()
            except RuntimeError:
                continue
            if layout is not None:
                layout.invalidate()
                layout.activate()

    def refresh_readouts(self) -> None:
        """Re-render the status readouts after a theme switch.

        They are rich text with the colour written into the markup, so unlike
        a stylesheet they cannot simply be re-applied -- the last value has to
        be pushed through :meth:`set_readout` again.
        """
        for field, (text, active) in list(self._readout_state.items()):
            self.set_readout(field, text, active)

    def restyle_chrome(self) -> None:
        """Re-apply every inline stylesheet the main window owns.

        These are baked from token values when the widget is built, so the
        application stylesheet alone does not move them: without this the
        toolbar stays dark under a light menu bar.
        """
        theme.restyle_tree(self)
        self.refresh_readouts()
        self.tabs.tabBar().update()
        for line in self._toolbar_separators:
            try:
                line.setStyleSheet(f"background: {theme.token('border')};border: none;")
            except RuntimeError:
                continue
        if getattr(self, "mode_chip", None) is not None:
            has_mode = bool(self.mode_chip.text())
            self.mode_chip.setStyleSheet(
                chip_style("fg", "border.hi") if has_mode else chip_style("fg.muted")
            )
        for name, token_name in (
            ("message_label", "fg.muted"),
            ("progress_label", "fg.muted"),
        ):
            widget = getattr(self, name, None)
            if widget is not None:
                widget.setStyleSheet(f"color: {theme.token(token_name)};")

    def toolbar_button(self, act, style=Qt.ToolButtonIconOnly) -> QToolButton:
        button = QToolButton(self.toolbar_content)
        button.setDefaultAction(act)
        button.setToolButtonStyle(style)
        button.setFont(theme.font_ui(theme.SIZE_SMALL_PT))
        button.setAutoRaise(True)
        button.setFixedHeight(theme.TOOLBAR_BUTTON_BOX)
        self.toolbar_box.addWidget(button)
        return button

    def setup_toolbar(self) -> None:
        """Build the tool bar.

        Deliberately a plain widget rather than a ``QToolBar``.  This bar is
        never movable, floatable or dockable, so QToolBar's only contribution
        was its layout -- and that layout is recomputed from the style every
        time the application style sheet is re-applied, with different
        metrics than it used when the window was first built.  Measured
        across a theme switch: items 6 px lower and 2 px shorter, which put
        their bottom borders and rounded corners outside the bar so they were
        clipped.  Pinning heights, setting the bar's contents margins,
        zeroing its padding and skipping its re-polish each failed to hold;
        a widget with an ordinary QHBoxLayout simply does the same thing
        twice.
        """
        tb = ToolStrip(self)
        tb.setObjectName("audian_toolbar")
        theme.band(tb, bottom=True)
        self.toolbar = tb
        self.toolbar_content = tb
        self._toolbar_gaps = []
        self.toolbar_box = QHBoxLayout(tb)
        self.toolbar_box.setContentsMargins(theme.S8, theme.S4, theme.S8, theme.S4)
        self.toolbar_box.setSpacing(theme.S4)
        self.chrome_box.insertWidget(0, tb)

        # transport:
        for act in (
            self.acts.time_home,
            self.acts.time_up,
            self.acts.time_down,
            self.acts.time_end,
            self.acts.play_window,
        ):
            self.toolbar_button(act)
        self.toolbar_gap()

        # region mode, exclusive, legible:
        self._set_glyph(self.acts.zoom_region, "zoom")
        self._set_glyph(self.acts.play_region, "play-region")
        self._set_glyph(self.acts.analyze_region, "analyze")
        self._set_glyph(self.acts.save_region, "save")
        self._set_glyph(self.acts.ask_region, "ask")
        self._set_glyph(self.acts.label_region, "label")
        self.mode_buttons = []
        for act in (
            self.acts.zoom_region,
            self.acts.play_region,
            self.acts.analyze_region,
            self.acts.save_region,
            self.acts.ask_region,
            self.acts.label_region,
        ):
            button = self.toolbar_button(act, Qt.ToolButtonTextBesideIcon)
            self.mode_buttons.append(button)
        self.toolbar_gap()

        # panels:
        self._set_glyph(self.acts.toggle_traces, "trace")
        self._set_glyph(self.acts.toggle_spectrograms, "spectrogram")
        self._set_glyph(self.acts.toggle_mean_spec, "meanspec")
        self._set_glyph(self.acts.toggle_power, "power")
        self._set_glyph(self.acts.toggle_cbars, "colorbar")
        self._set_glyph(self.acts.toggle_fulldata, "navigator")
        self.panel_buttons = []
        for act in (
            self.acts.toggle_traces,
            self.acts.toggle_spectrograms,
            self.acts.toggle_mean_spec,
            self.acts.toggle_power,
            self.acts.toggle_cbars,
            self.acts.toggle_fulldata,
        ):
            act.setCheckable(True)
            self.panel_buttons.append(self.toolbar_button(act))
        self.toolbar_gap()

        # amplitude:
        self._set_glyph(self.acts.auto_zoom_amplitude, "fit")
        self.fit_y_button = self.toolbar_button(
            self.acts.auto_zoom_amplitude, Qt.ToolButtonTextBesideIcon
        )
        self.ampl_button = QToolButton(tb)
        self.ampl_button.setPopupMode(QToolButton.InstantPopup)
        self.ampl_button.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.ampl_button.setFont(theme.font_ui(theme.SIZE_SMALL_PT))
        self.ampl_button.setAutoRaise(True)
        self.ampl_button.setToolTip("Amplitude range policy")
        ampl_menu = QMenu(self.ampl_button)
        # The y-range policy is a property of the browser, not of the
        # application, so the menu drives DataBrowser.set_y_mode directly
        # rather than duplicating a combo box in the bottom bar.
        self.acts.y_modes = []
        group = QActionGroup(self)
        group.setExclusive(True)
        for i, name in enumerate(DataBrowser.y_modes):
            act = QAction(f"Y: {name}", self)
            act.setCheckable(True)
            act.setData(i)
            act.triggered.connect(lambda checked, m=i: self.set_y_mode(m))
            group.addAction(act)
            ampl_menu.addAction(act)
            self.acts.y_modes.append(act)
        self.acts.y_mode_group = group
        ampl_menu.addSeparator()
        ampl_menu.addAction(self.acts.link_amplitude)
        ampl_menu.addAction(self.acts.reset_amplitude)
        ampl_menu.addAction(self.acts.center_amplitude)
        self.ampl_menu = ampl_menu
        self.ampl_button.setMenu(ampl_menu)
        self.update_amplitude_button()
        self.ampl_button.setFixedHeight(theme.TOOLBAR_BUTTON_BOX)
        self.toolbar_box.addWidget(self.ampl_button)

        # right aligned channel selector:
        spacer = QWidget(self.toolbar_content)
        make_transparent(spacer, "audian_toolbar_gap")
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.toolbar_box.addWidget(spacer, 1)
        self.channel_button = QToolButton(self.toolbar_content)
        self._set_glyph(self.channel_button, "channels")
        self.channel_button.setPopupMode(QToolButton.InstantPopup)
        self.channel_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.channel_button.setFont(theme.font_mono(theme.SIZE_SMALL_PT))
        self.channel_button.setAutoRaise(True)
        self.channel_button.setToolTip(
            "Show or hide channels (Alt+0 … Alt+9).\n"
            "Every channel in the file is listed here, including ones hidden "
            "by -c."
        )
        self.channel_button.setText("ch --")
        self.channel_menu = QMenu(self.channel_button)
        self.channel_menu.aboutToShow.connect(self.build_channel_menu)
        self.channel_button.setMenu(self.channel_menu)
        self.channel_button.setFixedHeight(theme.TOOLBAR_BUTTON_BOX)
        self.toolbar_box.addWidget(self.channel_button)

        self.setup_toolbar_stages()

        # The height is fixed only once the bar is populated, otherwise the
        # toolbar layout raises the minimum height from its contents.  The
        # bottom hairline is a QSS border and Qt adds it to the box, so the
        # widget is one hairline taller than the 36px content band.
        layout = tb.layout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.S4)
        for i in range(layout.count()):
            widget = layout.itemAt(i).widget()
            if widget is not None:
                widget.setMaximumHeight(theme.TOOLBAR_HEIGHT - theme.S4)
        tb.setFixedHeight(theme.TOOLBAR_HEIGHT + theme.HAIRLINE)
        self.toolbar.setEnabled(False)

    def setup_toolbar_stages(self) -> None:
        """Build the overflow button and hand `ToolStrip` its stage table.

        Called with the bar populated and before the height loop at the end
        of `setup_toolbar`: that loop caps every item's height exactly once,
        over whatever is in the layout at that instant, so a button created
        later would stand proud of the 37 px band.

        The stages are widest first.  `ToolStrip.set_stages` measures each
        one off the real layout rather than adding button widths up, so the
        numbers quoted anywhere about this bar come from the bar.
        """
        self.overflow_button = QToolButton(self.toolbar_content)
        self._set_glyph(self.overflow_button, "more")
        self.overflow_button.setPopupMode(QToolButton.InstantPopup)
        self.overflow_button.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.overflow_button.setFont(theme.font_ui(theme.SIZE_SMALL_PT))
        self.overflow_button.setAutoRaise(True)
        self.overflow_button.setFixedHeight(theme.TOOLBAR_BUTTON_BOX)
        self.overflow_menu = QMenu(self.overflow_button)
        self.overflow_menu.aboutToShow.connect(self.build_overflow_menu)
        self.overflow_button.setMenu(self.overflow_menu)
        self.overflow_button.setVisible(False)
        # Immediately after the last control and before the expanding
        # spacer: only a SUFFIX of the run is ever folded, so this slot is
        # always right after whatever is still on the bar.
        index = self.toolbar_box.indexOf(self.ampl_button) + 1
        self.toolbar_box.insertWidget(index, self.overflow_button)

        modes = list(self.mode_buttons) + [self.fit_y_button]
        styles = {b: b.toolButtonStyle() for b in modes}

        def unlabel(on: bool) -> None:
            for button in modes:
                button.setToolButtonStyle(
                    Qt.ToolButtonIconOnly if on else styles[button]
                )

        def collapse_gaps(on: bool) -> None:
            for spacer in self._toolbar_gaps:
                spacer.setFixedWidth(0 if on else theme.S12)

        # Right to left, so every button still on the bar keeps its x and the
        # reader's aim keeps working.  Each group owns the rule that precedes
        # it, or folding one would leave a hairline in front of nothing.
        amplitude = ([self.fit_y_button, self.ampl_button], 3)
        panels = (list(self.panel_buttons), 2)
        region = (list(self.mode_buttons), 1)

        def folder(group, rule_index, name):
            widgets, _ = group

            def apply(on: bool) -> None:
                for widget in widgets:
                    widget.setVisible(not on)
                if 0 <= rule_index - 1 < len(self._toolbar_separators):
                    self._toolbar_separators[rule_index - 1].setVisible(not on)
                if on and name not in self._folded_groups:
                    self._folded_groups.append(name)
                elif not on and name in self._folded_groups:
                    self._folded_groups.remove(name)
                self.overflow_button.setVisible(bool(self._folded_groups))
                self.overflow_button.setToolTip(
                    "Moved off the bar: " + ", ".join(self._folded_groups)
                    if self._folded_groups
                    else ""
                )

            return apply

        self._folded_groups = []
        fold_amplitude = folder(amplitude, 3, "amplitude")
        fold_panels = folder(panels, 2, "panels")
        fold_region = folder(region, 1, "region modes")

        def stage(*applies):
            def apply(on: bool) -> None:
                for one in applies:
                    one(on)

            return apply

        self.toolbar.set_stages(
            [
                ("full", lambda on: None),
                ("glyphs", unlabel),
                ("tight", stage(unlabel, collapse_gaps)),
                ("no-amplitude", stage(unlabel, collapse_gaps, fold_amplitude)),
                (
                    "no-panels",
                    stage(unlabel, collapse_gaps, fold_amplitude, fold_panels),
                ),
                (
                    "no-modes",
                    stage(
                        unlabel,
                        collapse_gaps,
                        fold_amplitude,
                        fold_panels,
                        fold_region,
                    ),
                ),
            ]
        )

    def build_overflow_menu(self) -> None:
        """Fill the overflow with whatever is off the bar right now.

        The same `QAction` objects the buttons carry, so a folded control
        keeps its name, its glyph and its check state -- and gains a rendered
        shortcut, which the bar itself never showed.  Built on `aboutToShow`
        rather than on every resize, the way the channel menu is.
        """
        menu = self.overflow_menu
        menu.clear()
        groups = (
            ("region modes", [b.defaultAction() for b in self.mode_buttons]),
            ("panels", [b.defaultAction() for b in self.panel_buttons]),
            ("amplitude", [self.acts.auto_zoom_amplitude]),
        )
        for name, actions in groups:
            if name not in self._folded_groups:
                continue
            if not menu.isEmpty():
                menu.addSeparator()
            for act in actions:
                if act is not None:
                    menu.addAction(act)
            if name == "amplitude" and self.ampl_menu is not None:
                # the submenu's TITLE is the readout, so "Y: per-channel"
                # survives the fold rather than becoming a bare "Y"
                menu.addMenu(self.ampl_menu)

    def set_y_mode(self, mode: int) -> None:
        """Apply an amplitude-range policy to the current browser."""
        browser = self.browser()
        if isinstance(browser, DataBrowser) and hasattr(browser, "set_y_mode"):
            browser.set_y_mode(mode)
        self.update_amplitude_button()

    def update_amplitude_button(self) -> None:
        """Show the current browser's amplitude policy on the tool bar."""
        if self.ampl_button is None:
            return
        browser = self.browser()
        mode = getattr(browser, "y_mode", 0) if browser is not None else 0
        if not 0 <= mode < len(DataBrowser.y_modes):
            mode = 0
        self.ampl_button.setText(f"Y: {DataBrowser.y_modes[mode]}")
        for i, act in enumerate(getattr(self.acts, "y_modes", [])):
            blocked = act.blockSignals(True)
            act.setChecked(i == mode)
            act.blockSignals(blocked)

    def build_channel_menu(self) -> None:
        self.channel_menu.clear()
        for act in self.acts.channels:
            if act.isVisible():
                self.channel_menu.addAction(act)
        self.channel_menu.addSeparator()
        self.channel_menu.addAction(self.acts.select_all_channels)
        self.channel_menu.addAction(self.acts.next_channel)
        self.channel_menu.addAction(self.acts.previous_channel)
        self.channel_menu.addAction(self.acts.hide_deselected_channels)

    def sync_toolbar(self, browser=None) -> None:
        """Retarget the tool bar at `browser` (the current tab)."""
        if self.toolbar is None:
            return
        if browser is None:
            browser = self.browser()
        if not isinstance(browser, DataBrowser) or browser.data is None:
            self.toolbar.setEnabled(False)
            return
        self.toolbar.setEnabled(True)
        states = (
            (self.acts.toggle_traces, bool(browser.show_traces)),
            (self.acts.toggle_spectrograms, bool(browser.show_specs)),
            (self.acts.toggle_mean_spec, bool(browser.mean_spec)),
            (self.acts.toggle_power, bool(browser.show_powers)),
            (self.acts.toggle_cbars, bool(browser.show_cbars)),
            (self.acts.toggle_fulldata, bool(browser.show_fulldata)),
            (
                self.acts.navigator_all_channels,
                getattr(getattr(browser, "datafig", None), "mode", "single") == "all",
            ),
        )
        for act, state in states:
            blocked = act.blockSignals(True)
            act.setChecked(state)
            act.blockSignals(blocked)
        self.update_amplitude_button()
        # Say how many channels are *hidden*, not just which one is current.
        # With `-c 0,8,15` the rail shows three rows and nothing anywhere
        # says the other thirteen exist, so the way to bring one back is
        # unfindable -- even though it is one Alt+N away.
        # show_channels is None until the browser has opened its file, and
        # sync_toolbar runs before that
        shown = len(browser.show_channels or ())
        total = browser.data.channels
        if shown and shown < total:
            self.channel_button.setText(
                f"ch {browser.current_channel:d}  {shown}/{total}"
            )
        else:
            self.channel_button.setText(f"ch {browser.current_channel:d}")
        self.set_readout("ch", f"ch {browser.current_channel:02d}", False)
        self.set_mode_chip(self.MODE_NAMES.get(browser.region_mode, ""))

    # ------------------------------------------------------------------
    # global actions: command palette and cheat sheet
    # ------------------------------------------------------------------

    def setup_global_actions(self) -> None:
        self.acts.command_palette = QAction("&Command palette", self)
        self.acts.command_palette.setShortcut("Ctrl+Shift+P")
        self.acts.command_palette.triggered.connect(self.command_palette)
        self.addAction(self.acts.command_palette)

        self.acts.cheat_sheet = QAction("Cheat &sheet", self)
        self.acts.cheat_sheet.setShortcuts(["?", "Shift+/"])
        self.acts.cheat_sheet.triggered.connect(self.cheat_sheet)
        self.addAction(self.acts.cheat_sheet)

    def all_actions(self):
        """Every menu action with its menu path, walked from self.menus."""
        found = []
        seen = set()

        def walk(menu, path):
            title = menu.title().replace("&", "")
            here = path + [title] if title else path
            for act in menu.actions():
                if act.menu() is not None:
                    walk(act.menu(), here)
                elif not act.isSeparator():
                    text = act.text().replace("&", "")
                    if not text or id(act) in seen:
                        continue
                    seen.add(id(act))
                    found.append((act, " › ".join(here)))

        for menu in self.menus:
            walk(menu, [])
        for act in (self.acts.command_palette, self.acts.cheat_sheet):
            if id(act) not in seen:
                seen.add(id(act))
                found.append((act, "Help"))
        return found

    def close_dialog(self, attr: str) -> None:
        """Close a tracked dialog, tolerating an already deleted one."""
        dialog = getattr(self, attr, None)
        if dialog is None:
            return
        try:
            dialog.close()
        except RuntimeError:
            pass
        setattr(self, attr, None)

    def track_dialog(self, attr: str, dialog) -> None:
        """Keep a dialog reference that clears itself when Qt deletes it."""
        setattr(self, attr, dialog)
        dialog.destroyed.connect(lambda *args: setattr(self, attr, None))

    def command_palette(self) -> None:
        self.close_dialog("palette_dialog")
        dialog = CommandPalette(self)
        self.track_dialog("palette_dialog", dialog)
        dialog.show()

    def cheat_sheet(self) -> None:
        self.close_dialog("cheatsheet_dialog")
        dialog = CheatSheet(self)
        self.track_dialog("cheatsheet_dialog", dialog)
        dialog.show()

    def region_mode_for_modifiers(self, modifiers):
        """Region mode override for a modified drag, or None.

        Shift+drag plays the selection, Alt+drag analyzes it, so that the
        region mode rarely has to be switched at all.  Call this from
        SelectViewBox when a drag starts.
        """
        if modifiers & Qt.ShiftModifier:
            return DataBrowser.MODE_PLAY
        if modifiers & Qt.AltModifier:
            return DataBrowser.MODE_ANALYZE
        return None

    def browser(self):
        return self.tabs.currentWidget()

    def require_browser(self):
        """The current browser, or None (with a notification) if there is none."""
        browser = self.browser()
        if not isinstance(browser, DataBrowser) or browser.data is None:
            self.notify("warning", "no data loaded")
            return None
        return browser

    def save_window(self):
        browser = self.require_browser()
        if browser is not None:
            browser.save_window()

    def show_metadata(self):
        browser = self.require_browser()
        if browser is not None:
            browser.show_metadata()

    def screen_shot(self):
        if self.require_browser() is None:
            return
        app = QApplication.activeWindow()
        screen = QGuiApplication.primaryScreen()
        if app and screen:
            image = screen.grabWindow(app.winId())
            taxis = self.browser().panels["trace"].axs[0].getAxis("bottom")
            file_name, time = taxis.get_file_pos()
            t0s = secs_to_str(time, 3)
            twin = (
                self.browser().plot_ranges["t"].r1[0]
                - self.browser().plot_ranges["t"].r0[0]
            )
            twins = secs_to_str(twin, 3)
            channels = ",".join([f"{c}" for c in self.browser().show_channels])
            file_path = Path(file_name)
            metadata = PngInfo()
            metadata.add_text("ScreenshotFile", file_path.name)
            metadata.add_text("ScreenshotTime", t0s)
            metadata.add_text("ScreenshotWindow", twins)
            metadata.add_text("ScreenshotChannels", channels)
            file_name = "screenshot.png"
            if self.save_path[0] is None:
                file_path = file_path.with_name(file_name)
            else:
                file_path = self.save_path[0] / file_name
            file_path = QFileDialog.getSaveFileName(
                self, "Save screenshot as", os.fspath(file_path), "PNG files (*.png)"
            )[0]
            if file_path:
                try:
                    try:
                        rel_path = Path(file_path).relative_to(Path.cwd(), walk_up=True)
                    except TypeError:
                        rel_path = Path(file_path).relative_to(Path.cwd())
                except ValueError:
                    rel_path = file_path
                try:
                    image_buffer = QBuffer()
                    image_buffer.open(QBuffer.ReadWrite)
                    image.save(image_buffer, "PNG")
                    pil_image = Image.open(io.BytesIO(image_buffer.data()))
                    pil_image.save(file_path, pnginfo=metadata)
                    self.save_path[0] = Path(file_path).parent
                    self.notify("success", f'saved screenshot to "{rel_path}"')
                except PermissionError:
                    self.notify(
                        "error",
                        f'failed to save screenshot to "{rel_path}": permission denied',
                    )

    def dropped_paths(self, ev):
        paths = []
        for url in ev.mimeData().urls():
            local = url.toLocalFile()
            paths.append(Path(local if local else url.path()))
        return paths

    def dragEnterEvent(self, ev):
        # we want audio files or annotated screenshots:
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()
            if self.startup_active:
                self.startup.set_drag_active(True)

    def dragLeaveEvent(self, ev):
        if self.startup_active:
            self.startup.set_drag_active(False)

    def dropEvent(self, ev):
        if self.startup_active:
            self.startup.set_drag_active(False)
        if not ev.mimeData().hasUrls():
            return
        paths = self.dropped_paths(ev)
        audio = [fp for fp in paths if fp.suffix.lower() in AUDIO_SUFFIXES]
        if len(audio) > 0:
            self.load_files([os.fspath(fp) for fp in audio])
            ev.acceptProposedAction()
            return
        pngs = [fp for fp in paths if fp.suffix.lower() == ".png"]
        if len(pngs) == 0:
            self.notify("warning", "dropped file is neither audio nor a screenshot")
            return
        if self.require_browser() is None:
            return
        path = pngs[0]
        screenshot = Image.open(path)
        if "ScreenshotFile" in screenshot.text:
            file_name = screenshot.text["ScreenshotFile"]
            time_str = screenshot.text["ScreenshotTime"]
        else:
            # parse file name of screenshot:
            pcs = path.stem.split("-")
            if len(pcs) < 2:
                return
            file_name = pcs[-2]
            time_str = pcs[-1]
        time = 0.0
        for ts, fac in [["h", 3600], ["m", 60], ["s", 1], ["ms", 0.001]]:
            if ts in time_str:
                i = time_str.find(ts)
                if ts == "m" and i + 1 < len(time_str) and time_str[i + 1] == "s":
                    continue
                time += fac * float(time_str[:i])
                time_str = time_str[i + len(ts) :]
        self.browser().goto_time(file_name, time)
        ev.acceptProposedAction()

    def setup_file_actions(self, menu):
        self.acts.open_files = QAction("&Open", self)
        self.acts.open_files.setShortcuts(QKeySequence.Open)
        self.acts.open_files.triggered.connect(self.open_files)

        self.acts.save_window = QAction("&Save window as", self)
        # Ctrl+S is reserved for saving, never for a panel toggle:
        self.acts.save_window.setShortcuts(["Ctrl+S", "Ctrl+Shift+S"])
        self.acts.save_window.triggered.connect(lambda x: self.save_window())

        self.acts.screen_shot = QAction("Screenshot", self)
        self.acts.screen_shot.setShortcut("Alt+Ctrl+S")
        self.acts.screen_shot.triggered.connect(self.screen_shot)
        self.setAcceptDrops(True)

        self.acts.meta_data = QAction("&Meta data", self)
        self.acts.meta_data.triggered.connect(lambda x: self.show_metadata())

        self.acts.new_tab = QAction("&New tab", self)
        self.acts.new_tab.setShortcut("Ctrl+T")
        self.acts.new_tab.triggered.connect(self.open_files)

        self.acts.close = QAction("&Close tab", self)
        self.acts.close.setShortcut("Ctrl+W")
        self.acts.close.triggered.connect(lambda x: self.close(None))

        self.acts.quit = QAction("&Quit", self)
        self.acts.quit.setShortcuts(QKeySequence.Quit)
        self.acts.quit.triggered.connect(self.quit)

        self.data_acts.extend(
            [
                self.acts.save_window,
                self.acts.screen_shot,
                self.acts.meta_data,
                self.acts.close,
            ]
        )

        file_menu = menu.addMenu("&File")
        file_menu.addAction(self.acts.open_files)
        file_menu.addAction(self.acts.new_tab)
        file_menu.addAction(self.acts.save_window)
        file_menu.addAction(self.acts.screen_shot)
        file_menu.addSeparator()
        file_menu.addAction(self.acts.meta_data)
        file_menu.addSeparator()
        file_menu.addAction(self.acts.close)
        file_menu.addAction(self.acts.quit)
        return file_menu

    def set_rect_mode(self):
        for b in self.browsers:
            b.set_zoom_mode(pg.ViewBox.RectMode)

    def set_pan_mode(self):
        for b in self.browsers:
            b.set_zoom_mode(pg.ViewBox.PanMode)

    def set_region_mode(self, mode):
        for b in self.browsers:
            b.set_region_mode(mode)
        self.set_mode_chip(self.MODE_NAMES.get(mode, ""))

    def current_region_mode(self):
        """The region mode the mode actions currently select."""
        for name, mode in (
            ("zoom_region", DataBrowser.MODE_ZOOM),
            ("play_region", DataBrowser.MODE_PLAY),
            ("analyze_region", DataBrowser.MODE_ANALYZE),
            ("save_region", DataBrowser.MODE_SAVE),
            ("ask_region", DataBrowser.MODE_ASK),
            ("label_region", DataBrowser.MODE_LABEL),
        ):
            if getattr(self.acts, name).isChecked():
                return mode
        return DataBrowser.MODE_ZOOM

    def set_zoom(self):
        self.set_region_mode(DataBrowser.MODE_ZOOM)

    def set_play(self):
        self.set_region_mode(DataBrowser.MODE_PLAY)

    def set_analyze(self):
        self.set_region_mode(DataBrowser.MODE_ANALYZE)

    def set_save(self):
        self.set_region_mode(DataBrowser.MODE_SAVE)

    def set_ask(self):
        self.set_region_mode(DataBrowser.MODE_ASK)

    def set_label(self):
        self.set_region_mode(DataBrowser.MODE_LABEL)

    def set_cross_hair(self, checked):
        for b in self.browsers:
            b.set_cross_hair(checked)
        # The three readouts only the cross hair fills come and go with it.
        # The row visibly grows and shrinks on Ctrl+C, which is the point:
        # it stops reserving half its width for fields that say "--".
        self.set_crosshair_readouts_visible(bool(checked))

    def setup_region_actions(self, menu):
        self.acts.rect_zoom = QAction("&Rectangle zoom", self)
        self.acts.rect_zoom.setCheckable(True)
        self.acts.rect_zoom.setShortcut("Ctrl+R")
        self.acts.rect_zoom.toggled.connect(self.set_rect_mode)

        self.acts.pan_zoom = QAction("&Pan && zoom", self)
        self.acts.pan_zoom.setCheckable(True)
        self.acts.pan_zoom.setShortcut("Ctrl+Z")
        self.acts.pan_zoom.toggled.connect(self.set_pan_mode)

        self.acts.zoom_mode = QActionGroup(self)
        self.acts.zoom_mode.addAction(self.acts.rect_zoom)
        self.acts.zoom_mode.addAction(self.acts.pan_zoom)
        self.acts.rect_zoom.setChecked(True)

        self.acts.zoom_back = QAction("Zoom &back", self)
        self._set_glyph(self.acts.zoom_back, "back")
        self.acts.zoom_back.setToolTip("Zoom back (Backspace)")
        self.acts.zoom_back.setShortcuts(["Backspace", "Alt+Left"])
        self.acts.zoom_back.triggered.connect(lambda x=0: self.browser().zoom_back())

        self.acts.zoom_forward = QAction("Zoom &forward", self)
        self._set_glyph(self.acts.zoom_forward, "forward")
        self.acts.zoom_forward.setToolTip("Zoom forward (Shift+Backspace)")
        self.acts.zoom_forward.setShortcuts(["Shift+Backspace", "Alt+Right"])
        self.acts.zoom_forward.triggered.connect(
            lambda x=0: self.browser().zoom_forward()
        )

        self.acts.zoom_home = QAction("Zoom &home", self)
        self._set_glyph(self.acts.zoom_home, "home")
        self.acts.zoom_home.setToolTip("Zoom home (Alt+Backspace)")
        self.acts.zoom_home.setShortcut("Alt+Backspace")
        self.acts.zoom_home.triggered.connect(lambda x=0: self.browser().zoom_home())

        self.acts.zoom_region = QAction("&Zoom", self)
        self.acts.zoom_region.setCheckable(True)
        self.acts.zoom_region.setShortcut("z")
        self.acts.zoom_region.setToolTip("Drag a region to zoom into it  (z)")
        self.acts.zoom_region.toggled.connect(self.set_zoom)

        self.acts.play_region = QAction("&Play", self)
        self.acts.play_region.setCheckable(True)
        self.acts.play_region.setShortcut("P")
        self.acts.play_region.setToolTip(
            "Drag a region to play it  (P).\n"
            "Shift+drag plays a region whatever this mode is."
        )
        self.acts.play_region.toggled.connect(self.set_play)

        self.acts.analyze_region = QAction("&Analyze", self)
        self.acts.analyze_region.setCheckable(True)
        self.acts.analyze_region.setShortcut("a")
        self.acts.analyze_region.setToolTip(
            "Drag a region to run the analyzers over it  (a).\n"
            "Alt+drag analyses a region whatever this mode is."
        )
        self.acts.analyze_region.toggled.connect(self.set_analyze)

        self.acts.save_region = QAction("&Save", self)
        self.acts.save_region.setCheckable(True)
        self.acts.save_region.setShortcut("s")
        self.acts.save_region.setToolTip(
            "Drag a region to write it out as its own recording  (s)"
        )
        self.acts.save_region.toggled.connect(self.set_save)

        self.acts.ask_region = QAction("Re&quest", self)
        self.acts.ask_region.setCheckable(True)
        self.acts.ask_region.setShortcut("q")
        self.acts.ask_region.setToolTip(
            "Drag a region and choose what to do with it from a menu  (q)"
        )
        self.acts.ask_region.toggled.connect(self.set_ask)

        # 'b' for box.  Every bound sequence in this file and in
        # `databrowser.py` was enumerated: the free single letters are
        # b f i m u w x y -- and of those, m is not really free, because
        # `ChannelRailRow.keyPressEvent` answers m and s itself while a rail
        # row has the focus (mute and solo).  b is free at both levels and
        # says what the gesture draws.
        self.acts.label_region = QAction("La&bel", self)
        self.acts.label_region.setCheckable(True)
        self.acts.label_region.setShortcut("b")
        self.acts.label_region.setToolTip(
            "Drag a box to label it with the current category (b).\n"
            "On a spectrogram the box bounds time AND frequency; on a trace, "
            "time alone."
        )
        self.acts.label_region.toggled.connect(self.set_label)

        self.acts.zoom_rect_mode = QActionGroup(self)
        self.acts.zoom_rect_mode.addAction(self.acts.zoom_region)
        self.acts.zoom_rect_mode.addAction(self.acts.play_region)
        self.acts.zoom_rect_mode.addAction(self.acts.analyze_region)
        self.acts.zoom_rect_mode.addAction(self.acts.save_region)
        self.acts.zoom_rect_mode.addAction(self.acts.ask_region)
        self.acts.zoom_rect_mode.addAction(self.acts.label_region)
        # Zoom is the default: 'ask' popped a four item menu on every single
        # left drag and needed a second click.  'ask' stays as an opt-in.
        self.acts.zoom_region.setChecked(True)

        self.acts.analysis_results = QAction("Analysis results", self)
        # self.acts.analysis_results.setShortcut('Alt+A')
        self.acts.analysis_results.triggered.connect(
            lambda x: self.browser().analysis_results()
        )

        self.acts.play_window = QAction("&Play window", self)
        self._set_glyph(self.acts.play_window, "play")
        self.acts.play_window.setToolTip("Play window (Space)")

        self.acts.audio_source = QAction("Cycle &playback source", self)
        self.acts.audio_source.setShortcut("Shift+P")
        self.acts.audio_source.setToolTip(
            "Step through: the current channel alone, an explicit left/right "
            "channel pair, or every shown channel mixed down to stereo"
        )
        self.acts.audio_source.triggered.connect(self.toggle_audio_source)
        self.data_acts.append(self.acts.audio_source)
        self.acts.play_window.setShortcut(" ")
        self.acts.play_window.triggered.connect(
            lambda x=0: self.browser().play_scroll()
        )

        self.acts.use_heterodyne = QAction("&Use heterodyne frequency", self)
        self.acts.use_heterodyne.setIconText("h")
        self.acts.use_heterodyne.setCheckable(True)
        self.acts.use_heterodyne.setChecked(False)
        self.acts.use_heterodyne.toggled.connect(
            lambda v: self.browser().set_audio(use_heterodyne=bool(v))
        )

        self.acts.cross_hair = QAction("&Cross hair", self)
        self.acts.cross_hair.setCheckable(True)
        self.acts.cross_hair.setChecked(False)
        self.acts.cross_hair.setShortcut("Ctrl+c")
        self.acts.cross_hair.toggled.connect(self.set_cross_hair)

        region_menu = menu.addMenu("&Region")
        region_menu.addAction(self.acts.rect_zoom)
        region_menu.addAction(self.acts.pan_zoom)
        region_menu.addSeparator()
        region_menu.addAction(self.acts.zoom_back)
        region_menu.addAction(self.acts.zoom_forward)
        region_menu.addAction(self.acts.zoom_home)
        region_menu.addSeparator()
        region_menu.addAction(self.acts.zoom_region)
        region_menu.addAction(self.acts.play_region)
        region_menu.addAction(self.acts.analyze_region)
        region_menu.addAction(self.acts.save_region)
        region_menu.addAction(self.acts.ask_region)
        region_menu.addAction(self.acts.label_region)
        region_menu.addSeparator()
        region_menu.addAction(self.acts.analysis_results)
        region_menu.addAction(self.acts.cross_hair)
        region_menu.addSeparator()
        region_menu.addAction(self.acts.play_window)
        region_menu.addAction(self.acts.audio_source)
        region_menu.addAction(self.acts.use_heterodyne)

        self.data_menus.append(region_menu)

        return region_menu

    def toggle_link_timezoom(self):
        self.link_timezoom = not self.link_timezoom

    def toggle_link_timescroll(self):
        self.link_timescroll = not self.link_timescroll

    def toggle_starttime(self):
        self.starttime_mode += 1
        if self.starttime_mode > 2:
            self.starttime_mode = 0
        for b in self.browsers:
            b.set_starttime_mode(self.starttime_mode)

    def apply_time_ranges(self, timefunc, link):
        self.browser().apply_time_ranges(timefunc)
        if link:
            for b in self.browsers:
                if b is not self.browser():
                    b.apply_time_ranges(timefunc)

    def setup_time_actions(self, menu):
        self.acts.link_time_zoom = QAction("Link time &zoom", self)
        self.acts.link_time_zoom.setShortcut("Alt+Z")
        self.acts.link_time_zoom.setCheckable(True)
        self.acts.link_time_zoom.setChecked(self.link_timezoom)
        self.acts.link_time_zoom.toggled.connect(self.toggle_link_timezoom)

        self.acts.toggle_start_time = QAction("Toggle &start time", self)
        self.acts.toggle_start_time.setShortcut("Ctrl+Shift+T")
        self.acts.toggle_start_time.triggered.connect(self.toggle_starttime)

        # +/- and Ctrl +/- belong to the axis under the pointer now, so
        # the un-centered time zoom is menu- and wheel-only:
        self.acts.time_zoom_in = QAction("Zoom &in", self)
        self.acts.time_zoom_in.triggered.connect(
            lambda x: self.apply_time_ranges("zoom_in", self.link_timezoom)
        )

        self.acts.time_zoom_out = QAction("Zoom &out", self)
        self.acts.time_zoom_out.triggered.connect(
            lambda x: self.apply_time_ranges("zoom_out", self.link_timezoom)
        )

        self.acts.time_zoom_in_centered = QAction("Zoom in centered", self)
        self.acts.time_zoom_in_centered.setShortcuts(["Shift+T"])
        self.acts.time_zoom_in_centered.triggered.connect(
            lambda x: self.apply_time_ranges("zoom_in_centered", self.link_timezoom)
        )

        self.acts.time_zoom_out_centered = QAction("Zoom out centered", self)
        self.acts.time_zoom_out_centered.setShortcuts(["T"])
        self.acts.time_zoom_out_centered.triggered.connect(
            lambda x: self.apply_time_ranges("zoom_out_centered", self.link_timezoom)
        )

        self.acts.link_time_scroll = QAction("Link &time scroll", self)
        self.acts.link_time_scroll.setShortcut("Alt+T")
        self.acts.link_time_scroll.setCheckable(True)
        self.acts.link_time_scroll.setChecked(self.link_timescroll)
        self.acts.link_time_scroll.toggled.connect(self.toggle_link_timescroll)

        self.acts.time_down = QAction("Seek &forward", self)
        self._set_glyph(self.acts.time_down, "seek-forward")
        self.acts.time_down.setToolTip("Seek forward (Page down)")
        self.acts.time_down.setShortcuts(QKeySequence.MoveToNextPage)
        self.acts.time_down.triggered.connect(
            lambda x: self.apply_time_ranges("up", self.link_timescroll)
        )

        self.acts.time_up = QAction("Seek &backward", self)
        self._set_glyph(self.acts.time_up, "seek-backward")
        self.acts.time_up.setToolTip("Seek backward (Page up)")
        self.acts.time_up.setShortcuts(QKeySequence.MoveToPreviousPage)
        self.acts.time_up.triggered.connect(
            lambda x: self.apply_time_ranges("down", self.link_timescroll)
        )

        self.acts.time_small_down = QAction("Forward", self)
        self.acts.time_small_down.setShortcuts(QKeySequence.MoveToNextLine)
        self.acts.time_small_down.triggered.connect(
            lambda x: self.apply_time_ranges("small_up", self.link_timescroll)
        )

        self.acts.time_small_up = QAction("Backward", self)
        self.acts.time_small_up.setShortcuts(QKeySequence.MoveToPreviousLine)
        self.acts.time_small_up.triggered.connect(
            lambda x: self.apply_time_ranges("small_down", self.link_timescroll)
        )

        self.acts.time_end = QAction("&End", self)
        self._set_glyph(self.acts.time_end, "skip-forward")
        self.acts.time_end.setToolTip("Skip to end of data (End)")
        self.acts.time_end.setShortcuts(
            [QKeySequence.MoveToEndOfLine, QKeySequence.MoveToEndOfDocument]
        )
        self.acts.time_end.triggered.connect(
            lambda x: self.apply_time_ranges("end", self.link_timescroll)
        )

        self.acts.time_home = QAction("&Home", self)
        self._set_glyph(self.acts.time_home, "skip-backward")
        self.acts.time_home.setToolTip("Skip to beginning of data (Home)")
        self.acts.time_home.setShortcuts(
            [QKeySequence.MoveToStartOfLine, QKeySequence.MoveToStartOfDocument]
        )
        self.acts.time_home.triggered.connect(
            lambda x: self.apply_time_ranges("home", self.link_timescroll)
        )

        self.acts.time_snap = QAction("&Snap", self)
        self.acts.time_snap.setShortcut(".")
        self.acts.time_snap.triggered.connect(
            lambda x: self.apply_time_ranges("snap", self.link_timescroll)
        )

        self.acts.auto_scroll = QAction("&Auto scroll", self)
        self.acts.auto_scroll.setShortcut("!")
        self.acts.auto_scroll.triggered.connect(
            lambda x=0: self.browser().auto_scroll()
        )

        time_menu = menu.addMenu("Time")
        time_menu.addAction(self.acts.link_time_zoom)
        time_menu.addAction(self.acts.toggle_start_time)
        time_menu.addAction(self.acts.time_zoom_in)
        time_menu.addAction(self.acts.time_zoom_out)
        time_menu.addAction(self.acts.time_zoom_in_centered)
        time_menu.addAction(self.acts.time_zoom_out_centered)
        time_menu.addAction(self.acts.link_time_scroll)
        time_menu.addAction(self.acts.time_down)
        time_menu.addAction(self.acts.time_up)
        time_menu.addAction(self.acts.time_small_down)
        time_menu.addAction(self.acts.time_small_up)
        time_menu.addAction(self.acts.time_end)
        time_menu.addAction(self.acts.time_home)
        time_menu.addAction(self.acts.time_snap)
        time_menu.addAction(self.acts.auto_scroll)

        self.data_menus.append(time_menu)

        return time_menu

    def pointer_axes(self, kind):
        """The axes of `kind` the pointer is currently over.

        Falls back to every axis of that kind that is actually in use, so
        the gesture is axis-agnostic even before SelectViewBox reports a
        hovered axis.
        """
        browser = self.browser()
        if not isinstance(browser, DataBrowser) or browser.data is None:
            return ""
        specs = Panel.amplitudes if kind == "amplitude" else Panel.frequencies
        if hasattr(browser, "axis_under_pointer"):
            under = browser.axis_under_pointer(kind)
            if under:
                return under
        return "".join(
            s
            for s in specs
            if s in browser.plot_ranges and browser.plot_ranges[s].is_used()
        )

    def apply_ranges(self, amplitudefunc, axspec):
        if not axspec:
            return
        self.browser().apply_ranges(amplitudefunc, axspec)
        for s in axspec:
            if self.link_ranges[s]:
                for b in self.browsers:
                    if b is not self.browser():
                        b.apply_ranges(amplitudefunc, s)

    def dispatch_ranges(self, axspec, arange):
        for s in range(2):
            if axspec[s] in Panel.times:
                toffs = None
                if self.link_timescroll:
                    toffs = arange[s]
                twin = None
                if self.link_timezoom:
                    twin = arange[s][1] - arange[s][0]
                for b in self.browsers:
                    if b is not self.browser():
                        b.set_times(toffs, twin)
            elif self.link_ranges[axspec[s]]:
                for b in self.browsers:
                    if b is not self.browser():
                        b.set_ranges(axspec[s], *arange[s])

    def toggle_link_amplitude(self):
        for s in Panel.amplitudes:
            self.link_ranges[s] = not self.link_ranges[s]

    def auto_amplitude(self):
        self.browser().auto_ampl()
        for s in Panel.amplitudes:
            if self.link_ranges[s]:
                for b in self.browsers:
                    if b is not self.browser():
                        b.auto_ampl([s])

    def setup_amplitude_actions(self, menu):
        self.acts.link_amplitude = QAction("Link &amplitude", self)
        self.acts.link_amplitude.setShortcut("Alt+A")
        self.acts.link_amplitude.setCheckable(True)
        self.acts.link_amplitude.setChecked(self.link_ranges[Panel.amplitudes[0]])
        self.acts.link_amplitude.toggled.connect(self.toggle_link_amplitude)

        # One axis-agnostic gesture acts on whichever amplitude axis the
        # pointer is over.  The per-axis entries below keep their menu
        # entries (named after their trace, never after the axis letter)
        # but lose the X/x/Y/y/U/u bindings entirely.
        self.acts.zoom_amplitude_in = QAction("Zoom &in", self)
        self.acts.zoom_amplitude_in.setShortcuts(["+", "="])
        self.acts.zoom_amplitude_in.triggered.connect(
            lambda x: self.apply_ranges("zoom_in", self.pointer_axes("amplitude"))
        )

        self.acts.zoom_amplitude_out = QAction("Zoom &out", self)
        self.acts.zoom_amplitude_out.setShortcut("-")
        self.acts.zoom_amplitude_out.triggered.connect(
            lambda x: self.apply_ranges("zoom_out", self.pointer_axes("amplitude"))
        )

        self.acts.zoom_xamplitude_in = QAction("Zoom trace amplitude in", self)
        self.acts.zoom_xamplitude_in.triggered.connect(
            lambda x: self.apply_ranges("zoom_in", Panel.amplitudes[0])
        )

        self.acts.zoom_xamplitude_out = QAction("Zoom trace amplitude out", self)
        self.acts.zoom_xamplitude_out.triggered.connect(
            lambda x: self.apply_ranges("zoom_out", Panel.amplitudes[0])
        )

        self.acts.zoom_yamplitude_in = QAction("Zoom second amplitude in", self)
        self.acts.zoom_yamplitude_in.triggered.connect(
            lambda x: self.apply_ranges("zoom_in", Panel.amplitudes[1])
        )

        self.acts.zoom_yamplitude_out = QAction("Zoom second amplitude out", self)
        self.acts.zoom_yamplitude_out.triggered.connect(
            lambda x: self.apply_ranges("zoom_out", Panel.amplitudes[1])
        )

        self.acts.zoom_uamplitude_in = QAction("Zoom third amplitude in", self)
        self.acts.zoom_uamplitude_in.triggered.connect(
            lambda x: self.apply_ranges("zoom_in", Panel.amplitudes[2])
        )

        self.acts.zoom_uamplitude_out = QAction("Zoom third amplitude out", self)
        self.acts.zoom_uamplitude_out.triggered.connect(
            lambda x: self.apply_ranges("zoom_out", Panel.amplitudes[2])
        )

        self.acts.auto_zoom_amplitude = QAction("&Fit Y", self)
        self.acts.auto_zoom_amplitude.setShortcut("v")
        self.acts.auto_zoom_amplitude.setToolTip(
            "Fit the amplitude axis to what is on screen  (v)"
        )
        self.acts.auto_zoom_amplitude.triggered.connect(self.auto_amplitude)

        self.acts.reset_amplitude = QAction("&Reset", self)
        self.acts.reset_amplitude.setShortcut("Shift+V")
        self.acts.reset_amplitude.triggered.connect(
            lambda x: self.apply_ranges("reset", Panel.amplitudes)
        )

        self.acts.center_amplitude = QAction("&Center", self)
        self.acts.center_amplitude.setShortcut("C")
        self.acts.center_amplitude.triggered.connect(
            lambda x: self.apply_ranges("center", Panel.amplitudes)
        )

        ampl_menu = menu.addMenu("&Amplitude")
        ampl_menu.addAction(self.acts.link_amplitude)
        ampl_menu.addAction(self.acts.zoom_amplitude_in)
        ampl_menu.addAction(self.acts.zoom_amplitude_out)
        ampl_menu.addSeparator()
        ampl_menu.addAction(self.acts.zoom_xamplitude_in)
        ampl_menu.addAction(self.acts.zoom_xamplitude_out)
        ampl_menu.addAction(self.acts.zoom_yamplitude_in)
        ampl_menu.addAction(self.acts.zoom_yamplitude_out)
        ampl_menu.addAction(self.acts.zoom_uamplitude_in)
        ampl_menu.addAction(self.acts.zoom_uamplitude_out)
        ampl_menu.addAction(self.acts.auto_zoom_amplitude)
        ampl_menu.addAction(self.acts.reset_amplitude)
        ampl_menu.addAction(self.acts.center_amplitude)

        self.data_menus.append(ampl_menu)

        return ampl_menu

    def toggle_link_frequency(self):
        for s in Panel.frequencies:
            self.link_ranges[s] = not self.link_ranges[s]

    def setup_frequency_actions(self, menu):
        self.acts.link_frequency = QAction("Link &frequency", self)
        # self.acts.link_frequency.setShortcut('Alt+F')
        self.acts.link_frequency.setCheckable(True)
        self.acts.link_frequency.setChecked(self.link_ranges[Panel.frequencies[0]])
        self.acts.link_frequency.toggled.connect(self.toggle_link_frequency)

        # Ctrl +/- mirrors Ctrl+wheel and acts on the frequency axis the
        # pointer is over; F/f/W/w are gone.
        self.acts.zoom_frequency_in = QAction("Zoom &in", self)
        self.acts.zoom_frequency_in.setShortcuts(["Ctrl++", "Ctrl+="])
        self.acts.zoom_frequency_in.triggered.connect(
            lambda x: self.apply_ranges("zoom_in", self.pointer_axes("frequency"))
        )

        self.acts.zoom_frequency_out = QAction("Zoom &out", self)
        self.acts.zoom_frequency_out.setShortcut("Ctrl+-")
        self.acts.zoom_frequency_out.triggered.connect(
            lambda x: self.apply_ranges("zoom_out", self.pointer_axes("frequency"))
        )

        self.acts.zoom_ffrequency_in = QAction("Zoom spectrogram frequency in", self)
        self.acts.zoom_ffrequency_in.triggered.connect(
            lambda x: self.apply_ranges("zoom_in", Panel.frequencies[0])
        )

        self.acts.zoom_ffrequency_out = QAction("Zoom spectrogram frequency out", self)
        self.acts.zoom_ffrequency_out.triggered.connect(
            lambda x: self.apply_ranges("zoom_out", Panel.frequencies[0])
        )

        self.acts.zoom_wfrequency_in = QAction("Zoom second frequency in", self)
        self.acts.zoom_wfrequency_in.triggered.connect(
            lambda x: self.apply_ranges("zoom_in", Panel.frequencies[1])
        )

        self.acts.zoom_wfrequency_out = QAction("Zoom second frequency out", self)
        self.acts.zoom_wfrequency_out.triggered.connect(
            lambda x: self.apply_ranges("zoom_out", Panel.frequencies[1])
        )

        self.acts.frequency_up = QAction("Move &up", self)
        self.acts.frequency_up.setShortcuts(QKeySequence.MoveToNextChar)
        self.acts.frequency_up.triggered.connect(
            lambda x: self.apply_ranges("up", Panel.frequencies[0])
        )

        self.acts.frequency_down = QAction("Move &down", self)
        self.acts.frequency_down.setShortcuts(QKeySequence.MoveToPreviousChar)
        self.acts.frequency_down.triggered.connect(
            lambda x: self.apply_ranges("down", Panel.frequencies[0])
        )

        self.acts.frequency_home = QAction("&Home", self)
        self.acts.frequency_home.setShortcuts(QKeySequence.MoveToPreviousWord)
        self.acts.frequency_home.triggered.connect(
            lambda x: self.apply_ranges("home", Panel.frequencies[0])
        )

        self.acts.frequency_end = QAction("&End", self)
        self.acts.frequency_end.setShortcuts(QKeySequence.MoveToNextWord)
        self.acts.frequency_end.triggered.connect(
            lambda x: self.apply_ranges("end", Panel.frequencies[0])
        )

        freq_menu = menu.addMenu("Frequenc&y")
        freq_menu.addAction(self.acts.link_frequency)
        freq_menu.addAction(self.acts.zoom_frequency_in)
        freq_menu.addAction(self.acts.zoom_frequency_out)
        freq_menu.addSeparator()
        freq_menu.addAction(self.acts.zoom_ffrequency_in)
        freq_menu.addAction(self.acts.zoom_ffrequency_out)
        freq_menu.addAction(self.acts.zoom_wfrequency_in)
        freq_menu.addAction(self.acts.zoom_wfrequency_out)
        freq_menu.addAction(self.acts.frequency_up)
        freq_menu.addAction(self.acts.frequency_down)
        freq_menu.addAction(self.acts.frequency_home)
        freq_menu.addAction(self.acts.frequency_end)

        self.data_menus.append(freq_menu)

        return freq_menu

    def set_spectrogram(self, spec):
        for b in self.browsers:
            b.set_spectrogram(False, spec)

    def dispatch_resolution(self):
        pass
        """
        TODO: should set nfft and hop for all spectrograms!!!
        if self.link_ranges[Panel.frequencies[0]]:
            for b in self.browsers:
                if not b is self.browser():
                    b.set_resolution(self.browser().data.nfft,
                                     self.browser().data.hop_frac,
                                     False)
        """

    def dispatch_colormap(self):
        cm = self.browser().color_map
        for b in self.browsers:
            if b is not self.browser():
                b.set_color_map(cm, False)

    def toggle_link_power(self):
        for s in Panel.powers:
            self.link_ranges[s] = not self.link_ranges[s]

    def apply_power_ranges(self, amplitudefunc):
        self.apply_ranges(amplitudefunc, self.browser().spectrogram_power)

    def toggle_link_filter(self):
        self.link_filter = not self.link_filter

    def dispatch_filter(self):
        if self.link_filter and "filtered" in self.browser().data:
            highpass_cutoff = self.browser().data["filtered"].highpass_cutoff
            lowpass_cutoff = self.browser().data["filtered"].lowpass_cutoff
            for b in self.browsers:
                if b is not self.browser():
                    bs = b.blockSignals(True)
                    b.update_filter(highpass_cutoff, lowpass_cutoff)
                    b.blockSignals(bs)

    def setup_spectrogram_actions(self, menu):
        self.acts.frequency_resolution_up = QAction("Increase &resolution", self)
        self.acts.frequency_resolution_up.setShortcut("Shift+R")
        self.acts.frequency_resolution_up.triggered.connect(
            lambda x: self.browser().freq_resolution_up()
        )

        self.acts.frequency_resolution_down = QAction("De&crease resolution", self)
        self.acts.frequency_resolution_down.setShortcut("R")
        self.acts.frequency_resolution_down.triggered.connect(
            lambda x: self.browser().freq_resolution_down()
        )

        self.acts.overlap_up = QAction("Increase overlap", self)
        self.acts.overlap_up.setShortcut("Shift+O")
        self.acts.overlap_up.triggered.connect(
            lambda x: self.browser().overlap_frac_up()
        )

        self.acts.overlap_down = QAction("Decrease &overlap", self)
        self.acts.overlap_down.setShortcut("O")
        self.acts.overlap_down.triggered.connect(
            lambda x: self.browser().overlap_frac_down()
        )

        self.acts.color_map_cycler = QAction("&Color map", self)
        self.acts.color_map_cycler.setShortcut("Shift+C")
        self.acts.color_map_cycler.triggered.connect(
            lambda x: self.browser().color_map_cycler()
        )

        self.acts.link_power = QAction("Link &power", self)
        self.acts.link_power.setShortcut("Alt+P")
        self.acts.link_power.setCheckable(True)
        self.acts.link_power.setChecked(self.link_ranges[Panel.powers[0]])
        self.acts.link_power.toggled.connect(self.toggle_link_power)

        self.acts.power_up = QAction("Power &up", self)
        self.acts.power_up.setShortcut("Shift+D")
        self.acts.power_up.triggered.connect(
            lambda x: self.apply_power_ranges("step_up")
        )

        self.acts.power_down = QAction("Power &down", self)
        self.acts.power_down.setShortcut("D")
        self.acts.power_down.triggered.connect(
            lambda x: self.apply_power_ranges("step_down")
        )

        self.acts.max_power_up = QAction("Max up", self)
        self.acts.max_power_up.setShortcut("Shift+K")
        self.acts.max_power_up.triggered.connect(
            lambda x: self.apply_power_ranges("max_up")
        )

        self.acts.max_power_down = QAction("Max down", self)
        self.acts.max_power_down.setShortcut("K")
        self.acts.max_power_down.triggered.connect(
            lambda x: self.apply_power_ranges("max_down")
        )

        self.acts.min_power_up = QAction("Min up", self)
        self.acts.min_power_up.setShortcut("Shift+J")
        self.acts.min_power_up.triggered.connect(
            lambda x: self.apply_power_ranges("min_up")
        )

        self.acts.min_power_down = QAction("Min down", self)
        self.acts.min_power_down.setShortcut("J")
        self.acts.min_power_down.triggered.connect(
            lambda x: self.apply_power_ranges("min_down")
        )

        self.acts.link_filter = QAction("Link &filter", self)
        # self.acts.link_filter.setShortcut('Alt+F')
        self.acts.link_filter.setCheckable(True)
        self.acts.link_filter.setChecked(self.link_filter)
        self.acts.link_filter.toggled.connect(self.toggle_link_filter)

        self.acts.highpass_up = QAction("Increase &highpass cutoff", self)
        self.acts.highpass_up.setShortcut("Shift+H")
        self.acts.highpass_up.triggered.connect(lambda x: self.browser().hpfw.stepUp())

        self.acts.highpass_down = QAction("Decrease highpass cutoff", self)
        self.acts.highpass_down.setShortcut("H")
        self.acts.highpass_down.triggered.connect(
            lambda x: self.browser().hpfw.stepDown()
        )

        self.acts.lowpass_up = QAction("Increase &lowpass cutoff", self)
        self.acts.lowpass_up.setShortcut("Shift+L")
        self.acts.lowpass_up.triggered.connect(lambda x: self.browser().lpfw.stepUp())

        self.acts.lowpass_down = QAction("Decrease lowpass cutoff", self)
        self.acts.lowpass_down.setShortcut("L")
        self.acts.lowpass_down.triggered.connect(
            lambda x: self.browser().lpfw.stepDown()
        )

        spec_menu = menu.addMenu("&Spectrogram")
        self.spectrogram_group = QActionGroup(self)
        self.spectrogram_menu = spec_menu.addMenu("&Active")
        self.data_menus.append(self.spectrogram_menu)
        spec_menu.addAction(self.acts.frequency_resolution_up)
        spec_menu.addAction(self.acts.frequency_resolution_down)
        spec_menu.addAction(self.acts.overlap_up)
        spec_menu.addAction(self.acts.overlap_down)
        spec_menu.addAction(self.acts.color_map_cycler)
        spec_menu.addSeparator()
        spec_menu.addAction(self.acts.link_power)
        spec_menu.addAction(self.acts.power_up)
        spec_menu.addAction(self.acts.power_down)
        spec_menu.addAction(self.acts.max_power_up)
        spec_menu.addAction(self.acts.max_power_down)
        spec_menu.addAction(self.acts.min_power_up)
        spec_menu.addAction(self.acts.min_power_down)
        spec_menu.addSeparator()
        spec_menu.addAction(self.acts.link_filter)
        spec_menu.addAction(self.acts.highpass_up)
        spec_menu.addAction(self.acts.highpass_down)
        spec_menu.addAction(self.acts.lowpass_up)
        spec_menu.addAction(self.acts.lowpass_down)

        self.data_menus.append(spec_menu)

        return spec_menu

    def toggle_link_envelope(self):
        self.link_envelope = not self.link_envelope

    def toggle_show_envelope(self):
        self.browser().update_envelope(
            show_envelope=not self.browser().data.is_visible("envelope")
        )

    def dispatch_envelope(self):
        if self.link_envelope and "envelope" in self.browser().data:
            envelope_cutoff = self.browser().data["envelope"].envelope_cutoff
            show_envelope = self.browser().data.is_visible("envelope")
            for b in self.browsers:
                if b is not self.browser():
                    b.update_envelope(
                        envelope_cutoff=envelope_cutoff,
                        show_envelope=show_envelope,
                        dispatch=False,
                    )

    def setup_envelope_actions(self, menu):
        self.acts.link_envelope = QAction("Link &envelope", self)
        self.acts.link_envelope.setShortcut("Alt+E")
        self.acts.link_envelope.setCheckable(True)
        self.acts.link_envelope.setChecked(self.link_envelope)
        self.acts.link_envelope.toggled.connect(self.toggle_link_envelope)

        self.acts.show_envelope = QAction("&Show envelope", self)
        self.acts.show_envelope.setShortcut("Ctrl+E")
        self.acts.show_envelope.setCheckable(True)
        self.acts.show_envelope.setChecked(True)
        self.acts.show_envelope.toggled.connect(self.toggle_show_envelope)

        self.acts.envelope_up = QAction("Envelope cutoff &up", self)
        self.acts.envelope_up.setShortcut("Shift+E")
        self.acts.envelope_up.triggered.connect(lambda x: self.browser().envfw.stepUp())

        self.acts.envelope_down = QAction("Envelope cutoff &down", self)
        self.acts.envelope_down.setShortcut("E")
        self.acts.envelope_down.triggered.connect(
            lambda x: self.browser().envfw.stepDown()
        )

        envelope_menu = menu.addMenu("&Envelope")
        envelope_menu.addAction(self.acts.link_envelope)
        envelope_menu.addAction(self.acts.show_envelope)
        envelope_menu.addAction(self.acts.envelope_up)
        envelope_menu.addAction(self.acts.envelope_down)

        self.data_menus.append(envelope_menu)

        return envelope_menu

    def toggle_link_channels(self):
        self.link_channels = not self.link_channels

    def toggle_channel(self, channel):
        self.browser().toggle_channel(channel)
        # the tool bar reports how many channels are shown, so it has to be
        # refreshed whenever that changes -- otherwise the count goes stale
        # and says 3/16 while four lanes are on screen
        self.sync_toolbar()
        if self.link_channels and not self.browser().setting:
            for b in self.browsers:
                if b is not self.browser():
                    b.set_channels(
                        self.browser().show_channels,
                        self.browser().selected_channels,
                        self.browser().current_channel,
                    )

    def show_channel(self, channel):
        self.browser().show_channel(channel)
        self.sync_toolbar()
        if self.link_channels and not self.browser().setting:
            for b in self.browsers:
                if b is not self.browser():
                    b.set_channels(
                        self.browser().show_channels,
                        self.browser().selected_channels,
                        self.browser().current_channel,
                    )

    def select_channels(self, selectfunc):
        getattr(self.browser(), selectfunc)()
        if self.link_channels and not self.browser().setting:
            for b in self.browsers:
                if b is not self.browser():
                    b.set_channels(
                        self.browser().show_channels,
                        self.browser().selected_channels,
                        self.browser().current_channel,
                    )
        self.sync_toolbar()

    def hide_deselected_channels(self):
        self.browser().hide_deselected_channels()
        if self.link_channels and not self.browser().setting:
            for b in self.browsers:
                if b is not self.browser():
                    b.set_channels(
                        self.browser().show_channels,
                        self.browser().selected_channels,
                        self.browser().current_channel,
                    )

    def set_channel_action(self, c, n, checked=True, active=True):
        if c >= len(self.acts.channels):
            cact = QAction(f"Channel &{c}", self)
            cact.setIconText(f"{c}")
            cact.setCheckable(True)
            cact.setChecked(checked)
            cact.toggled.connect(lambda x, channel=c: self.toggle_channel(channel))
            if self.toggle_menu:
                self.toggle_menu.addAction(cact)
            self.acts.channels.append(cact)
            sact = QAction(f"Show channel {c}", self)
            sact.triggered.connect(lambda x, channel=c: self.show_channel(channel))
            setattr(self.acts, f"select_channel{c}", sact)
            if self.show_menu:
                self.show_menu.addAction(sact)
            self.acts.show_channels.append(sact)
        else:
            cact = self.acts.channels[c]
            sact = self.acts.show_channels[c]
        if active:
            cact.toggled.disconnect()
            cact.setChecked(checked)
            cact.toggled.connect(lambda x, channel=c: self.toggle_channel(channel))
            cact.setEnabled(c < n)
            cact.setVisible(c < n)
            sact.setEnabled(c < n)
            sact.setVisible(c < n)
            if c < n:
                # channel-count independent: channel 5 is Alt+5 whether the
                # file has 2 or 16 channels.  Above channel 9 there is no
                # binding at all - a two key chord for a channel is worse
                # than no chord.
                if c < 10:
                    cact.setShortcut(f"Alt+{c}")
                    sact.setShortcut(f"Ctrl+{c}")
                else:
                    cact.setShortcut(QKeySequence())
                    sact.setShortcut(QKeySequence())
                keys = ", ".join([key.toString() for key in cact.shortcuts()])
                if keys:
                    cact.setToolTip(f"Toggle channel {c} ({keys})")
                else:
                    cact.setToolTip(f"Toggle channel {c}")

    def setup_channel_actions(self, menu):
        self.acts.link_channels = QAction("Link &channels", self)
        self.acts.link_channels.setShortcut("Alt+C")
        self.acts.link_channels.setCheckable(True)
        self.acts.link_channels.setChecked(self.link_channels)
        self.acts.link_channels.toggled.connect(self.toggle_link_channels)

        self.acts.channels = []
        self.acts.show_channels = []

        self.acts.select_all_channels = QAction("Select &all channels", self)
        self.acts.select_all_channels.setShortcuts(QKeySequence.SelectAll)
        self.acts.select_all_channels.triggered.connect(
            lambda x: self.select_channels("all_channels")
        )

        self.acts.next_channel = QAction("&Next channel", self)
        self.acts.next_channel.setShortcuts(
            [QKeySequence.SelectNextLine, QKeySequence("Alt+PgDown")]
        )
        self.acts.next_channel.triggered.connect(
            lambda x: self.select_channels("next_channel")
        )

        self.acts.previous_channel = QAction("&Previous channel", self)
        self.acts.previous_channel.setShortcuts(
            [QKeySequence.SelectPreviousLine, QKeySequence("Alt+PgUp")]
        )
        self.acts.previous_channel.triggered.connect(
            lambda x: self.select_channels("previous_channel")
        )

        self.acts.select_next_channel = QAction("Select next channel", self)
        self.acts.select_next_channel.setShortcuts(QKeySequence.SelectNextPage)
        self.acts.select_next_channel.triggered.connect(
            lambda x: self.select_channels("select_next_channel")
        )

        self.acts.select_previous_channel = QAction("Select previous channel", self)
        self.acts.select_previous_channel.setShortcuts(QKeySequence.SelectPreviousPage)
        self.acts.select_previous_channel.triggered.connect(
            lambda x: self.select_channels("select_previous_channel")
        )

        self.acts.hide_deselected_channels = QAction("Hide deselected channels", self)
        self.acts.hide_deselected_channels.setShortcuts(QKeySequence.Delete)
        self.acts.hide_deselected_channels.triggered.connect(
            self.hide_deselected_channels
        )

        channel_menu = menu.addMenu("&Channels")
        channel_menu.addAction(self.acts.link_channels)
        channel_menu.addAction(self.acts.select_all_channels)
        channel_menu.addAction(self.acts.next_channel)
        channel_menu.addAction(self.acts.previous_channel)
        channel_menu.addAction(self.acts.select_next_channel)
        channel_menu.addAction(self.acts.select_previous_channel)
        channel_menu.addAction(self.acts.hide_deselected_channels)
        self.toggle_menu = channel_menu.addMenu("&Toggle channels")
        for act in self.acts.channels:
            self.toggle_menu.addAction(act)
        self.show_menu = channel_menu.addMenu("&Show channels")
        for act in self.acts.show_channels:
            self.show_menu.addAction(act)

        self.data_menus.append(channel_menu)
        self.data_menus.append(self.toggle_menu)
        self.data_menus.append(self.show_menu)

        return channel_menu

    def dispatch_trace(self, browser, checked, name):
        for b in self.browsers:
            if b is not browser:
                b.set_trace(checked, name)

    def toggle_link_panels(self):
        self.link_panels = not self.link_panels

    def toggle_traces(self):
        self.browser().toggle_traces()
        if self.link_panels:
            for b in self.browsers:
                if b is not self.browser():
                    b.set_panels(
                        self.browser().show_traces,
                        self.browser().show_specs,
                        self.browser().show_powers,
                        self.browser().show_cbars,
                        self.browser().show_fulldata,
                    )
        self.sync_toolbar()

    def toggle_spectrograms(self):
        self.browser().toggle_spectrograms()
        if self.link_panels:
            for b in self.browsers:
                if b is not self.browser():
                    b.set_panels(
                        self.browser().show_traces,
                        self.browser().show_specs,
                        self.browser().show_powers,
                        self.browser().show_cbars,
                        self.browser().show_fulldata,
                    )
        self.sync_toolbar()

    def toggle_mean_spectrogram(self):
        """Shift+F2: one mean spectrogram over the array, or back to the stack.

        Not propagated by `link_panels`.  That switch links which panels the
        tabs *show*, and the mean is a statement about one recording's own
        channels: two tabs on a 16 channel array and a stereo file do not
        owe each other an average.

        The mode is announced in the status bar because the caption
        abbreviates: it says `MEAN 00-15` on the full array, but a scattered
        selection is folded to a count, and the abbreviated form must never
        be the only place the set is stated.
        """
        browser = self.browser()
        if not isinstance(browser, DataBrowser):
            return
        browser.toggle_mean_spectrogram()
        browser.notify("info", browser.mean_spectrogram_message())
        self.sync_toolbar()

    def reset_panel_split(self):
        """Shift+F3: the trace / spectrogram boundary back to its default.

        Not propagated by `link_panels`, and neither is the drag.  That
        switch links which panels the tabs *show*, which the toolbar mirrors
        so both tabs read the same; the split is a continuous adjustment
        made with the mouse inside one stack, and a stack of two channels
        and a stack of sixteen do not owe each other a ratio.
        """
        browser = self.browser()
        if isinstance(browser, DataBrowser):
            browser.reset_panel_split()

    def toggle_powers(self):
        self.browser().toggle_powers()
        if self.link_panels:
            for b in self.browsers:
                if b is not self.browser():
                    b.set_panels(
                        self.browser().show_traces,
                        self.browser().show_specs,
                        self.browser().show_powers,
                        self.browser().show_cbars,
                        self.browser().show_fulldata,
                    )
        self.sync_toolbar()

    def toggle_colorbars(self):
        self.browser().toggle_colorbars()
        if self.link_panels:
            for b in self.browsers:
                if b is not self.browser():
                    b.set_panels(
                        self.browser().show_traces,
                        self.browser().show_specs,
                        self.browser().show_powers,
                        self.browser().show_cbars,
                        self.browser().show_fulldata,
                    )
        self.sync_toolbar()

    def toggle_fulldata(self):
        self.browser().toggle_fulldata()

    def toggle_navigator_overview(self):
        """Flip the navigator between the waveform envelope and activity."""
        browser = self.browser()
        if not isinstance(browser, DataBrowser):
            return
        if not browser.has_navigator_activity():
            # nothing to switch to: say so instead of leaving a checkable
            # menu item that silently does nothing.
            self.statusBar().showMessage(
                "No activity overview for this recording yet - "
                "the full-trace overview is still being computed.",
                4000,
            )
            self.acts.navigator_activity.setChecked(False)
            return
        browser.toggle_navigator_overview()
        self.acts.navigator_activity.setChecked(
            browser.navigator_overview() == OVERVIEW_ACTIVITY
        )
        if self.link_panels:
            for b in self.browsers:
                if b is not browser and isinstance(b, DataBrowser):
                    if b.has_navigator_activity():
                        b.toggle_navigator_overview()

    def toggle_navigator_mode(self):
        """Flip the navigator between the current channel and all of them."""
        browser = self.browser()
        if isinstance(browser, DataBrowser) and hasattr(
            browser, "toggle_navigator_mode"
        ):
            browser.toggle_navigator_mode()
            self.sync_toolbar(browser)
        if self.link_panels:
            for b in self.browsers:
                if b is not self.browser():
                    b.set_panels(
                        self.browser().show_traces,
                        self.browser().show_specs,
                        self.browser().show_powers,
                        self.browser().show_cbars,
                        self.browser().show_fulldata,
                    )
        self.sync_toolbar()

    # --- annotations ------------------------------------------------------

    def load_annotations(self):
        browser = self.require_browser()
        if browser is not None:
            browser.open_annotations()

    def clear_annotations(self):
        browser = self.require_browser()
        if browser is not None:
            browser.clear_annotations()

    def toggle_annotations(self):
        browser = self.require_browser()
        if browser is not None:
            browser.toggle_annotations()

    def set_annotation_surface(self, surface, on):
        browser = self.require_browser()
        if browser is not None:
            browser.set_annotation_surface(surface, on)

    def set_annotation_layer(self, layer_id, on):
        browser = self.require_browser()
        if browser is not None:
            browser.set_annotation_layer(layer_id, on)

    def show_all_annotation_layers(self):
        browser = self.require_browser()
        if browser is not None:
            browser.show_all_annotation_layers()

    def build_annotation_layer_actions(self, layer) -> None:
        """One checkable entry per layer of the loaded bundle.

        Walked from the bundle, exactly as the chips are, so that the menu
        and the parameter bar carry the same set of toggles by construction
        and a layer can never end up with a chip and no menu entry.  Rebuilt
        only when what the entries SAY changes -- a load, a clear, a tab
        switch onto a different bundle -- so a solo does not rebuild a menu
        ten times.

        What they say is the name and the count, so both are in the key.
        The layer ids alone are the same ten strings in every bundle this
        reader can open, so keying on those left a second session showing the
        first one's counts -- 'Volley trials (11)' over a bundle holding two
        -- while the chips beside them, which are rebuilt outright, showed
        the new ones.
        """
        if getattr(self, "annotation_layer_menu", None) is None:
            # the View menu is not built yet; it will build itself from the
            # browser when it is
            return
        states = layer.layer_states() if layer is not None else []
        entries = tuple((state.id, state.label, state.count) for state in states)
        if entries == self.annotation_layer_entries:
            return
        self.annotation_layer_entries = entries
        # parented to the menu, so clear() destroys them: parented to the
        # window they would pile up on it, one dead set per file opened
        self.annotation_layer_menu.clear()
        self.acts.annotation_layers = {}
        if not states:
            empty = self.annotation_layer_menu.addAction("no annotations loaded")
            empty.setEnabled(False)
            return
        for state in states:
            act = QAction(f"{state.label}  ({state.count})", self.annotation_layer_menu)
            act.setCheckable(True)
            act.setChecked(state.enabled)
            act.setToolTip(state.tip)
            act.toggled.connect(lambda on, i=state.id: self.set_annotation_layer(i, on))
            self.annotation_layer_menu.addAction(act)
            self.acts.annotation_layers[state.id] = act

    def sync_annotation_actions(self, browser) -> None:
        """Point the menu checks at what the browser is actually doing.

        The same switches live on the parameter bar, and a tab switch changes
        which browser they belong to, so the menu is never the source of
        truth -- it is told.
        """
        layer = getattr(browser, "annotations", None)
        if layer is None or browser is not self.browser():
            # a background tab changing its own switches must not move the
            # menu, which speaks for the tab in front
            return
        for surface, act in self.acts.annotation_surfaces.items():
            blocked = act.blockSignals(True)
            act.setChecked(layer.surfaces.get(surface, True))
            act.blockSignals(blocked)
        self.build_annotation_layer_actions(layer)
        for layer_id, act in self.acts.annotation_layers.items():
            blocked = act.blockSignals(True)
            act.setChecked(layer.layers.get(layer_id, False))
            act.blockSignals(blocked)

    def next_annotation(self):
        browser = self.require_browser()
        if browser is not None:
            browser.step_annotation(True)

    def previous_annotation(self):
        browser = self.require_browser()
        if browser is not None:
            browser.step_annotation(False)

    def setup_annotation_actions(self, menu):
        self.acts.load_annotations = QAction("&Load fixed labels…", self)
        self.acts.load_annotations.setShortcut("Ctrl+Shift+A")
        self.acts.load_annotations.setToolTip(
            "Read a session bundle -- a *_metadata.toml and its CSVs -- and "
            "draw its layers over every lane  (Ctrl+Shift+A).\n"
            "These are fixed: audian never writes them."
        )
        self.acts.load_annotations.triggered.connect(self.load_annotations)

        self.acts.toggle_annotations = QAction("&Show fixed labels", self)
        self.acts.toggle_annotations.setShortcut("F8")
        self.acts.toggle_annotations.triggered.connect(self.toggle_annotations)

        self.acts.show_all_annotation_layers = QAction("Show &all layers", self)
        self.acts.show_all_annotation_layers.setShortcut("Shift+F8")
        self.acts.show_all_annotation_layers.setToolTip(
            "Undo a solo: draw every layer of the bundle again"
        )
        self.acts.show_all_annotation_layers.triggered.connect(
            self.show_all_annotation_layers
        )

        # One check per surface, walked from the surface table rather than
        # written out here: a surface that exists as a chip on the parameter
        # bar and not as an entry in the menu is a switch the reader can only
        # find by accident.
        self.acts.annotation_surfaces = {}
        for surface in SURFACE_ORDER:
            act = QAction(f"Show on &{SURFACE_LABELS[surface].lower()}", self)
            act.setCheckable(True)
            act.setChecked(True)
            act.setToolTip(ANNOTATION_SURFACE_TIPS.get(surface, ""))
            act.toggled.connect(
                lambda on, name=surface: self.set_annotation_surface(name, on)
            )
            self.acts.annotation_surfaces[surface] = act

        self.acts.clear_annotations = QAction("&Clear fixed labels", self)
        self.acts.clear_annotations.triggered.connect(self.clear_annotations)

        self.acts.next_annotation = QAction("&Next fixed label", self)
        self.acts.next_annotation.setShortcut("n")
        self.acts.next_annotation.setToolTip(
            "Centre the view on the next fixed label of a layer that is shown"
        )
        self.acts.next_annotation.triggered.connect(self.next_annotation)

        self.acts.previous_annotation = QAction("&Previous fixed label", self)
        self.acts.previous_annotation.setShortcut("Shift+N")
        self.acts.previous_annotation.triggered.connect(self.previous_annotation)

        annotation_menu = menu.addMenu("&Fixed labels")
        annotation_menu.addAction(self.acts.load_annotations)
        annotation_menu.addAction(self.acts.toggle_annotations)
        annotation_menu.addAction(self.acts.clear_annotations)
        annotation_menu.addSeparator()
        # One entry per layer, filled in from whatever bundle is open.  The
        # sub menu is built empty and stays in the same place whether a
        # bundle is loaded or not, so the reader learns where the layers are
        # rather than watching a menu change shape.
        self.annotation_layer_menu = annotation_menu.addMenu("&Layers")
        self.annotation_layer_entries = None
        self.acts.annotation_layers = {}
        self.build_annotation_layer_actions(None)
        annotation_menu.addAction(self.acts.show_all_annotation_layers)
        annotation_menu.addSeparator()
        for act in self.acts.annotation_surfaces.values():
            annotation_menu.addAction(act)
        annotation_menu.addSeparator()
        annotation_menu.addAction(self.acts.next_annotation)
        annotation_menu.addAction(self.acts.previous_annotation)

        self.data_menus.append(annotation_menu)
        return annotation_menu

    def setup_label_actions(self, menu):
        """The menu for the labels the reader makes.

        A menu of its own, next to the fixed labels and not inside it.  The
        two overlays look alike and are not alike -- one is read from a
        bundle the stimulator wrote and cannot be changed, the other is
        written by this application -- and a reader who had to open the
        fixed labels' menu to clear their own work would reasonably wonder
        which of the two it cleared.  Which is why the menus say fixed and
        editable rather than annotations and labels, two words that mean
        the same thing to anyone who has not been told otherwise.
        """
        self.acts.toggle_labels = QAction("Show &editable labels", self)
        self.acts.toggle_labels.setShortcut("F9")
        self.acts.toggle_labels.setToolTip(
            "Take the editable labels off the lanes, or put them back  (F9)"
        )
        self.acts.toggle_labels.triggered.connect(self.toggle_labels)

        self.acts.label_editor = QAction("Label &categories…", self)
        self.acts.label_editor.setShortcut("Ctrl+L")
        self.acts.label_editor.setToolTip(
            "Add, rename and remove label categories (Ctrl+L).  "
            "The first nine get the digit keys."
        )
        self.acts.label_editor.triggered.connect(self.edit_label_categories)

        self.acts.label_table = QAction("Label &list…", self)
        self.acts.label_table.setShortcut("Ctrl+M")
        self.acts.label_table.setToolTip(
            "Every editable label of this recording, and the control that "
            "removes one  (Ctrl+M)"
        )
        self.acts.label_table.triggered.connect(self.show_label_table)

        self.acts.delete_label = QAction("&Delete selected label", self)
        self.acts.delete_label.setShortcut("Ctrl+Delete")
        self.acts.delete_label.setToolTip(
            "Remove the editable label the grips are on  (Ctrl+Delete).  "
            "Ctrl+click a label in label mode (b) to pick it up; then drag a "
            "grip to move or resize it.  Plain Delete hides the deselected "
            "channels and Backspace zooms back, which is why this one takes "
            "Ctrl."
        )
        self.acts.delete_label.triggered.connect(self.delete_selected_label)

        self.acts.undo_label = QAction("&Undo last label change", self)
        self.acts.undo_label.setShortcut("Shift+B")
        self.acts.undo_label.setToolTip(
            "Take back the last change to the editable labels (Shift+B): one "
            "added, one removed, or one moved or resized.  One level, and "
            "only what this session did."
        )
        self.acts.undo_label.triggered.connect(self.undo_last_label_change)

        label_menu = menu.addMenu("&Editable labels")
        label_menu.addAction(self.acts.label_region)
        label_menu.addAction(self.acts.toggle_labels)
        label_menu.addSeparator()
        label_menu.addAction(self.acts.label_editor)
        label_menu.addAction(self.acts.label_table)
        label_menu.addAction(self.acts.delete_label)
        label_menu.addAction(self.acts.undo_label)

        self.data_menus.append(label_menu)
        return label_menu

    # Through `require_browser` and not `self.browser()`, the way every
    # annotation action does: a shortcut fired with no file open would
    # otherwise reach the StartupPage, which has none of these methods.

    def toggle_labels(self):
        browser = self.require_browser()
        if browser is not None:
            browser.toggle_labels()

    def edit_label_categories(self):
        browser = self.require_browser()
        if browser is not None:
            browser.edit_label_categories()

    def show_label_table(self):
        browser = self.require_browser()
        if browser is not None:
            browser.show_label_table()

    def delete_selected_label(self):
        browser = self.require_browser()
        if browser is not None:
            browser.delete_selected_label()

    def undo_last_label_change(self):
        browser = self.require_browser()
        if browser is not None:
            browser.undo_last_label_change()

    def setup_panel_actions(self, menu):
        self.acts.link_panels = QAction("Link &panels", self)
        # self.acts.link_panels.setShortcut('Alt+P')
        self.acts.link_panels.setCheckable(True)
        self.acts.link_panels.setChecked(self.link_panels)
        self.acts.link_panels.toggled.connect(self.toggle_link_panels)

        # panel toggles live on F2-F6: a reflexive Ctrl+S used to reshuffle
        # the whole layout while Ctrl+Shift+S was save-as.
        self.acts.toggle_traces = QAction("Toggle &traces", self)
        self.acts.toggle_traces.setShortcut("F2")
        self.acts.toggle_traces.triggered.connect(self.toggle_traces)

        self.acts.toggle_spectrograms = QAction("Toggle &spectrograms", self)
        self.acts.toggle_spectrograms.setShortcut("F3")
        self.acts.toggle_spectrograms.triggered.connect(self.toggle_spectrograms)

        # The split between a trace and its spectrogram is dragged, and a
        # dragged setting needs a way back.  Shift+F3 sits next to the F3
        # that shows the spectrogram in the first place, and was the only
        # unclaimed modifier on it (Shift+F6 and Alt+F6 are the navigator's,
        # F7 is the rail, F8 the annotations).
        self.acts.reset_panel_split = QAction("Reset spectrogram &split", self)
        self.acts.reset_panel_split.setShortcut("Shift+F3")
        self.acts.reset_panel_split.setToolTip(
            "Put the boundary between the trace and the spectrogram back "
            "where the lane opened it"
        )
        self.acts.reset_panel_split.triggered.connect(self.reset_panel_split)

        # Shift+F2 rather than a key of its own: this mode lives inside the
        # one F2 opens, and it was the only unclaimed modifier on that key
        # (Shift+F3 resets the split, Shift+F6 and Alt+F6 are the
        # navigator's, Shift+F8 the annotations').
        self.acts.toggle_mean_spec = QAction("Toggle &mean spectrogram", self)
        self.acts.toggle_mean_spec.setShortcut("Shift+F2")
        self.acts.toggle_mean_spec.setToolTip(
            "One full-height spectrogram of the mean power over the visible "
            "channels, instead of one per channel"
        )
        self.acts.toggle_mean_spec.triggered.connect(self.toggle_mean_spectrogram)

        self.acts.toggle_power = QAction("Toggle power", self)
        self.acts.toggle_power.setShortcut("F4")
        self.acts.toggle_power.triggered.connect(self.toggle_powers)

        self.acts.toggle_cbars = QAction("Toggle color bars", self)
        self.acts.toggle_cbars.setShortcut("F5")
        self.acts.toggle_cbars.triggered.connect(self.toggle_colorbars)

        self.acts.toggle_fulldata = QAction("Toggle &navigator", self)
        self.acts.toggle_fulldata.setShortcut("F6")
        self.acts.toggle_fulldata.triggered.connect(self.toggle_fulldata)

        self.acts.navigator_all_channels = QAction("Navigator: &all channels", self)
        self.acts.navigator_all_channels.setCheckable(True)
        self.acts.navigator_all_channels.setShortcut("Shift+F6")
        self.acts.navigator_all_channels.setToolTip(
            "Show every channel in the navigator instead of the current one"
        )
        self.acts.navigator_all_channels.triggered.connect(self.toggle_navigator_mode)

        self.acts.navigator_activity = QAction("Navigator: acti&vity", self)
        self.acts.navigator_activity.setCheckable(True)
        self.acts.navigator_activity.setShortcut("Alt+F6")
        self.acts.navigator_activity.setToolTip(
            "Show activity above the noise floor instead of the waveform, "
            "separating sustained calls from transient clicks and pulses"
        )
        self.acts.navigator_activity.triggered.connect(self.toggle_navigator_overview)

        panel_menu = menu.addMenu("&Panels")
        panel_menu.addAction(self.acts.link_panels)
        panel_menu.addAction(self.acts.toggle_traces)
        panel_menu.addAction(self.acts.toggle_spectrograms)
        panel_menu.addAction(self.acts.toggle_mean_spec)
        panel_menu.addAction(self.acts.reset_panel_split)
        panel_menu.addAction(self.acts.toggle_power)
        panel_menu.addAction(self.acts.toggle_cbars)
        panel_menu.addAction(self.acts.toggle_fulldata)
        panel_menu.addAction(self.acts.navigator_all_channels)
        panel_menu.addAction(self.acts.navigator_activity)

        self.data_menus.append(panel_menu)

        return panel_menu

    def sync_audio_source(self, browser) -> None:
        """Name the current source in the menu entry itself.

        It used to be a checkbox, which only works while there are two
        states; with the explicit pair there are three, and a check that
        means "not one of the other two" says nothing.
        """
        if not (
            isinstance(browser, DataBrowser) and hasattr(self.acts, "audio_source")
        ):
            return
        try:
            index = DataBrowser.AUDIO_SOURCES.index(browser.audio_source)
        except ValueError:
            return
        label = DataBrowser.AUDIO_SOURCE_LABELS[index]
        self.acts.audio_source.setText(f"Playback source: {label}")

    def toggle_audio_source(self):
        """Flip playback between the selected channel and the full mix."""
        browser = self.browser()
        if not isinstance(browser, DataBrowser):
            return
        browser.toggle_audio_source()
        self.sync_audio_source(browser)
        if browser.audio_source == DataBrowser.AUDIO_PAIR:
            left, right = browser.audio_channels()
            message = f"Playing channel {left:02d} left, channel {right:02d} right"
        elif browser.audio_source == DataBrowser.AUDIO_SELECTED:
            message = "Playing the selected channel"
        else:
            message = "Playing all shown channels, mixed to stereo"
        self.statusBar().showMessage(message, 2500)

    def dispatch_audio_source(self, source):
        if self.link_audio:
            for b in self.browsers:
                if b is not self.browser() and isinstance(b, DataBrowser):
                    b.set_audio_source(source, False)

    def dispatch_audio_pair(self, left, right):
        if self.link_audio:
            for b in self.browsers:
                if b is not self.browser() and isinstance(b, DataBrowser):
                    b.set_audio_pair(left, right, False)

    def dispatch_audio(self, rate_fac, use_heterodyne, heterodyne_freq):
        if self.link_audio:
            for b in self.browsers:
                if b is not self.browser():
                    b.set_audio(rate_fac, use_heterodyne, heterodyne_freq, False)

    def next_tab(self):
        idx = self.tabs.currentIndex()
        if idx + 1 < self.tabs.count():
            self.tabs.setCurrentIndex(idx + 1)

    def previous_tab(self):
        idx = self.tabs.currentIndex()
        if idx > 0:
            self.tabs.setCurrentIndex(idx - 1)

    def setup_view_actions(self, menu):
        self.acts.toggle_grid = QAction("Toggle &grid", self)
        self.acts.toggle_grid.setShortcut("g")
        self.acts.toggle_grid.triggered.connect(lambda x: self.browser().toggle_grids())

        self.acts.next_file = QAction("Next tab", self)
        self.acts.next_file.setShortcut("Ctrl+PgDown")
        self.acts.next_file.triggered.connect(self.next_tab)

        self.acts.previous_file = QAction("Previous tab", self)
        self.acts.previous_file.setShortcut("Ctrl+PgUp")
        self.acts.previous_file.triggered.connect(self.previous_tab)

        self.acts.daylight_mode = QAction("&Daylight mode", self)
        self.acts.daylight_mode.setCheckable(True)
        self.acts.daylight_mode.setChecked(theme.current_theme() == theme.THEME_LIGHT)
        self.acts.daylight_mode.setShortcut("Ctrl+Shift+L")
        self.acts.daylight_mode.setToolTip(
            "High-contrast light theme for reading the screen in direct sunlight"
        )
        self.acts.daylight_mode.triggered.connect(self.toggle_daylight)

        self.acts.maximize_window = QAction("Toggle &maximize", self)
        self.acts.maximize_window.setShortcut("Ctrl+Shift+M")
        self.acts.maximize_window.triggered.connect(self.toggle_maximize)

        view_menu = menu.addMenu("&View")
        self.setup_time_actions(view_menu)
        self.setup_amplitude_actions(view_menu)
        self.setup_frequency_actions(view_menu)
        self.setup_envelope_actions(view_menu)
        self.setup_channel_actions(view_menu)
        self.setup_panel_actions(view_menu)
        self.setup_annotation_actions(view_menu)
        # after the annotations, because that is the order the two overlays
        # are read in and the order they sit in on the parameter bar
        self.setup_label_actions(view_menu)
        self.traces_menu = view_menu.addMenu("&Traces")
        self.data_menus.append(self.traces_menu)
        view_menu.addAction(self.acts.toggle_grid)
        view_menu.addAction(self.acts.daylight_mode)
        view_menu.addAction(self.acts.maximize_window)
        self.addAction(self.acts.next_file)
        self.addAction(self.acts.previous_file)

        self.data_menus.append(view_menu)

        return view_menu

    def setup_help_actions(self, menu):
        self.setup_global_actions()

        self.acts.key_shortcuts = QAction("&Key shortcuts", self)
        self.acts.key_shortcuts.setShortcut("Ctrl+K")
        self.acts.key_shortcuts.triggered.connect(self.shortcuts)

        self.acts.message_log = QAction("&Message log", self)
        self.acts.message_log.triggered.connect(self.show_log)

        self.acts.about = QAction("&About Audian", self)
        self.acts.about.triggered.connect(self.about)

        help_menu = menu.addMenu("&Help")
        help_menu.addAction(self.acts.command_palette)
        help_menu.addAction(self.acts.cheat_sheet)
        help_menu.addAction(self.acts.key_shortcuts)
        help_menu.addSeparator()
        help_menu.addAction(self.acts.message_log)
        help_menu.addAction(self.acts.about)
        return help_menu

    def adapt_menu(self, index):
        browser = self.tabs.widget(index)
        if isinstance(browser, DataBrowser) and browser.data is not None:
            for c in range(len(self.acts.channels)):
                checked = browser.show_channels is None or c in browser.show_channels
                self.set_channel_action(c, browser.data.channels, checked, True)
            self.traces_menu.clear()
            for act in browser.trace_acts:
                self.traces_menu.addAction(act)
            for act in self.spectrogram_group.actions():
                self.spectrogram_group.removeAction(act)
            self.spectrogram_menu.clear()
            for act in browser.spec_acts:
                self.spectrogram_menu.addAction(act)
                self.spectrogram_group.addAction(act)
            if len(browser.spec_acts) > 0:
                browser.spec_acts[0].setChecked(True)
            self.spectrogram_menu.menuAction().setVisible(len(browser.spec_acts) > 1)
            self.relabel_axis_actions(browser)
            self.sync_annotation_actions(browser)
            browser.update()
        self.sync_toolbar(browser)

    def relabel_axis_actions(self, browser):
        """Name the per-axis zoom entries after their trace, not their axis."""
        amplitudes = {}
        frequencies = {}
        for panel in browser.panels.values():
            if panel.is_yamplitude():
                amplitudes[panel.y()] = panel.name
            elif panel.is_yfrequency():
                frequencies[panel.y()] = panel.name
        for spec, acts in (
            (Panel.amplitudes[0], "x"),
            (Panel.amplitudes[1], "y"),
            (Panel.amplitudes[2], "u"),
        ):
            name = amplitudes.get(spec)
            if not name:
                continue
            getattr(self.acts, f"zoom_{acts}amplitude_in").setText(
                f"Zoom {name} amplitude in"
            )
            getattr(self.acts, f"zoom_{acts}amplitude_out").setText(
                f"Zoom {name} amplitude out"
            )
        for spec, acts in ((Panel.frequencies[0], "f"), (Panel.frequencies[1], "w")):
            name = frequencies.get(spec)
            if not name:
                continue
            getattr(self.acts, f"zoom_{acts}frequency_in").setText(
                f"Zoom {name} frequency in"
            )
            getattr(self.acts, f"zoom_{acts}frequency_out").setText(
                f"Zoom {name} frequency out"
            )

    def set_tab_title(self, browser, fname):
        self.tabs.setTabText(self.tabs.indexOf(browser), fname)

    def open_files(self):
        formats = available_formats()
        for f in ["MP3", "OGG", "WAV"]:
            if f in formats:
                formats.remove(f)
                formats.insert(0, f)
        filters = ["All files (*)"] + [
            f"{f} files (*.{f}, *.{f.lower()})" for f in formats
        ]
        path = Path(".")
        if not self.startup_active:
            path = Path(self.browser().data.file_path).resolve().parent
        file_paths = QFileDialog.getOpenFileNames(
            self, directory=os.fspath(path), filter=";;".join(filters)
        )[0]

        self.load_files(file_paths)

    def load_files(self, file_paths):
        self.file_paths = [Path(fp) for fp in file_paths]
        self.file_paths = [
            fp for fp in self.file_paths if not fp.name.endswith("-fulltrace.wav")
        ]
        if len(self.file_paths) == 0:
            return
        if len(self.browsers) > 0:
            self.prev_browser = self.browser()
        # prepare open all files in a single buffer:
        browser = DataBrowser(
            self.file_paths,
            self.load_kwargs,
            self.plugins,
            self.channels,
            self.audio,
            self.acts,
            self.save_path,
            self.take_events_path(),
        )
        self.tabs.addTab(browser, browser.name())
        self.browsers.append(browser)
        self.tabs.setCurrentWidget(browser)
        self.hide_startup()
        QTimer.singleShot(100, self.load_data)

    def load_data(self):
        for browser in self.browsers:
            if browser.data.data is not None:
                continue
            try:
                browser.open(
                    self,
                    self.unwrap,
                    self.unwrap_clip,
                    self.highpass_cutoff,
                    self.lowpass_cutoff,
                )
            except Exception as e:
                self.notify("error", f"can not open {browser.data.file_path}: {e}")
                log.exception("failed to open %s", browser.data.file_path)
                QMessageBox.critical(
                    self, "Error", f"Can not open file <b>{browser.data.file_path}</b>!"
                )
                self.tabs.removeTab(self.tabs.indexOf(browser))
                self.browsers.remove(browser)
                self.file_paths.remove(browser.data.file_path)
                if self.tabs.count() == 0:
                    self.show_startup()
            if browser.data.data is not None:
                for fn in browser.data.data.file_paths:
                    if fn in self.file_paths:
                        self.file_paths.remove(fn)
            if len(self.file_paths) > 0:
                # still need to load some files:
                nbrowser = DataBrowser(
                    self.file_paths,
                    self.load_kwargs,
                    self.plugins,
                    self.channels,
                    self.audio,
                    self.acts,
                    self.save_path,
                    self.take_events_path(),
                )
                self.tabs.addTab(nbrowser, nbrowser.name())
                self.browsers.append(nbrowser)
            if browser.data.data is None:
                QTimer.singleShot(100, self.load_data)
                break
            self.tabs.setTabText(self.tabs.indexOf(browser), browser.name())
            for b in self.browsers:
                if b.data.data is not None and b.data.channels != browser.data.channels:
                    self.link_channels = False
                    self.acts.link_channels.setChecked(self.link_channels)
            if browser is self.browser():
                self.adapt_menu(self.tabs.currentIndex())
            browser.sigRangesChanged.connect(self.dispatch_ranges)
            browser.sigFilenameChanged.connect(self.set_tab_title)
            browser.sigResolutionChanged.connect(self.dispatch_resolution)
            browser.sigColorMapChanged.connect(self.dispatch_colormap)
            browser.sigFilterChanged.connect(self.dispatch_filter)
            browser.sigEnvelopeChanged.connect(self.dispatch_envelope)
            browser.sigTraceChanged.connect(self.dispatch_trace)
            browser.sigAudioChanged.connect(self.dispatch_audio)
            browser.sigAudioSourceChanged.connect(self.dispatch_audio_source)
            browser.sigAudioPairChanged.connect(self.dispatch_audio_pair)
            browser.set_starttime_mode(self.starttime_mode)
            pb = self.browser() if self.prev_browser is None else self.prev_browser
            if self.link_panels:
                browser.set_panels(
                    pb.show_traces,
                    pb.show_specs,
                    pb.show_powers,
                    pb.show_cbars,
                    pb.show_fulldata,
                )
            else:
                browser.set_panels()
            if self.link_channels:
                browser.set_channels(
                    pb.show_channels, pb.selected_channels, pb.current_channel
                )
            else:
                browser.set_channels()
            browser.set_region_mode(self.current_region_mode())
            self.remember_file(browser)
            self.sync_toolbar()
            self.notify("success", f"opened {browser.name()}")
            # after the open message, so that what the annotations have to
            # say about their alignment is the last thing left standing
            browser.init_annotations()
            QTimer.singleShot(100, self.load_data)
            break

    def take_events_path(self):
        """The --events path, once.  See `Audian.events_path`."""
        path = self.events_path
        self.events_path = None
        return path

    def remember_file(self, browser):
        """Add a freshly opened browser to the recent files list."""
        data = browser.data
        if data is None or data.file_path is None:
            return
        duration = None
        if data.rate:
            duration = data.frames / data.rate
        self.recent.add(data.file_path, data.channels, duration, data.rate)

    def toggle_maximize(self):
        """Ask the window manager to toggle the maximized state.

        Driven from the actual windowState(): on a tiling compositor the
        request may simply be ignored, and no layout decision anywhere may
        depend on the outcome.
        """
        state = self.windowState()
        if state & Qt.WindowMaximized:
            self.setWindowState(state & ~Qt.WindowMaximized)
        else:
            self.setWindowState(state | Qt.WindowMaximized)

    def shortcuts(self):
        # parented, WA_DeleteOnClose and explicitly non-modal: the old
        # dialog leaked a hidden top level window per invocation.
        dialog = ShortcutsDialog(self)
        dialog.show()

    def about(self):
        QMessageBox.about(
            self,
            "About Audian",
            f"""
<b>Audian</b>, version {__version__}<br>(c) {__year__}""",
        )

    def close(self, index=None):
        if self.tabs.count() > 0:
            if index is None:
                index = self.tabs.currentIndex()
            w = self.tabs.widget(index)
            if isinstance(w, DataBrowser):
                # By hand, and in both exit paths: there is no closeEvent
                # anywhere in audian, and `quit` below never goes through
                # QWidget's close machinery at all, so a label saved by a
                # queued zero-timer would go with the event loop.  Note that
                # this method SHADOWS QWidget.close -- `self.close()`
                # elsewhere in this class closes a tab, not the window.
                w.flush_labels()
                if w in self.browsers:
                    self.browsers.remove(w)
                self.tabs.removeTab(index)
                w.close()
                del w
        if self.tabs.count() == 0:
            self.show_startup()

    def quit(self):
        for w in self.browsers:
            w.flush_labels()
            index = self.tabs.indexOf(w)
            self.tabs.removeTab(index)
            w.close()
            del w
        QApplication.quit()


def audian_cli(cargs=[], plugins=None):
    # command line arguments:
    parser = argparse.ArgumentParser(
        description="Browse and analyze recordings of animal vocalizations.",
        epilog=f"version {__version__} by Jan Benda (2015-{__year__})",
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        dest="verbose",
        default=0,
        help="Print debug information (repeat for more)",
    )
    parser.add_argument(
        "-c",
        dest="channels",
        default="",
        type=str,
        metavar="CHANNELS",
        help="Comma separated list of channels to be displayed (first channel is 0).",
    )
    parser.add_argument(
        "-f",
        dest="highpass_cutoff",
        type=float,
        metavar="FREQ",
        default=None,
        help="Cutoff frequency of highpass filter in Hz",
    )
    parser.add_argument(
        "-l",
        dest="lowpass_cutoff",
        type=float,
        metavar="FREQ",
        default=None,
        help="Cutoff frequency of lowpass filter in Hz",
    )
    parser.add_argument(
        "-i",
        dest="load_kwargs",
        default=[],
        action="append",
        metavar="KWARGS",
        help="key-word arguments for the data loader function",
    )
    parser.add_argument(
        "-u",
        dest="unwrap",
        default=0,
        type=float,
        metavar="UNWRAP",
        const=1.5,
        nargs="?",
        help="unwrap clipped data with threshold relative to maximum input range and divide by two using unwrap() from audioio package",
    )
    parser.add_argument(
        "-U",
        dest="unwrap_clip",
        default=0,
        type=float,
        metavar="UNWRAP",
        const=1.5,
        nargs="?",
        help="unwrap clipped data with threshold relative to maximum input range and clip using unwrap() from audioio package",
    )
    parser.add_argument(
        "-a",
        "--events",
        dest="events_path",
        default=None,
        type=str,
        metavar="BUNDLE",
        help="session bundle to draw over the recording: a *_metadata.toml "
        "or the directory holding it and its CSVs; without it a bundle "
        "sitting beside the recording and naming it in [alignment] is picked "
        "up automatically",
    )
    parser.add_argument(
        "--theme",
        dest="theme",
        default=None,
        choices=["dark", "light"],
        help="colour theme; 'light' is the high-contrast daylight theme for "
        "outdoor use (default: whatever was last chosen, else dark)",
    )
    parser.add_argument(
        "files",
        nargs="*",
        default=[],
        type=str,
        help="name of files with the time series data",
    )
    args, qt_args = parser.parse_known_args(cargs)

    # diagnostics go through logging, never through print():
    verbose = args.verbose or 0
    level = logging.WARNING
    if verbose == 1:
        level = logging.INFO
    elif verbose > 1:
        level = logging.DEBUG
    logging.basicConfig(level=level, format="%(levelname)s: %(message)s")

    # selected channels:
    cs = [s.strip() for s in args.channels.split(",")]
    channels = []
    for c in cs:
        if len(c) == 0:
            continue
        css = [s.strip() for s in c.split("-")]
        if len(css) == 2:
            channels.extend(list(range(int(css[0]), int(css[1]) + 1)))
        else:
            channels.append(int(c))

    # unwrap:
    if args.unwrap_clip > 1e-3:
        args.unwrap = args.unwrap_clip
        args.unwrap_clip = True
    else:
        args.unwrap_clip = False

    # kwargs for data loader:
    load_kwargs = parse_load_kwargs(args.load_kwargs)

    # expand wildcard patterns:
    files = []
    if os.name == "nt":
        for fn in args.files:
            files.extend(sorted(glob.glob(fn)))
    else:
        files = args.files

    app = QApplication(sys.argv[:1] + qt_args)
    # the command line wins for this run; otherwise restore the last choice
    theme_name = args.theme or settings().get("theme", theme.THEME_DARK)
    if theme_name not in (theme.THEME_DARK, theme.THEME_LIGHT):
        theme_name = theme.THEME_DARK
    theme.apply(app, theme_name)
    main = Audian(
        files,
        load_kwargs,
        plugins,
        channels,
        args.highpass_cutoff,
        args.lowpass_cutoff,
        args.unwrap,
        args.unwrap_clip,
        args.events_path,
    )
    main.show()
    app.exec_()


def main(cargs):
    mp.set_start_method("forkserver" if os.name == "posix" else "spawn")
    AudioLoader.max_open_files = os.cpu_count() + 2
    AudioLoader.max_open_loaders = 2 * AudioLoader.max_open_files
    plugins = Plugins()
    plugins.load_plugins()
    audian_cli(cargs, plugins)


def run():
    main(sys.argv[1:])
    return 0


if __name__ == "__main__":
    run()
