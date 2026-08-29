import logging
import os

import numpy as np
import pyqtgraph as pg

from contextlib import contextmanager
from pathlib import Path
from copy import deepcopy
from math import fabs, floor, isfinite, log10
from typing import NamedTuple, Optional
from scipy.signal import butter, sosfiltfilt

try:
    from PyQt5.QtCore import Signal
except ImportError:
    from PyQt5.QtCore import pyqtSignal as Signal
from PyQt5.QtCore import Qt, QEvent, QPoint, QRectF, QSettings, QSize, QTimer
from PyQt5.QtGui import QCursor, QIcon, QPainter, QPixmap
from PyQt5.QtWidgets import QApplication
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QGridLayout
from PyQt5.QtWidgets import QLayout
from PyQt5.QtWidgets import QScrollArea, QSplitter, QFrame, QSlider
from PyQt5.QtWidgets import QLineEdit, QToolButton
from PyQt5.QtWidgets import QSizePolicy, QSpacerItem, QAbstractSpinBox
from PyQt5.QtWidgets import QButtonGroup, QStackedLayout
from PyQt5.QtWidgets import QAction, QMenu, QComboBox
from PyQt5.QtWidgets import QLabel
from PyQt5.QtWidgets import QDialog, QDialogButtonBox, QFileDialog
from PyQt5.QtWidgets import QGraphicsRectItem
from audioio import fade
from audioio import update_starttime
from audioio import bext_history_str, add_history
from thunderlab.datawriter import available_formats, write_data

from . import theme
from .data import Data
from .panels import Panel, Panels
from .panelsplitter import PanelSplitter
from .plotranges import PlotRanges
from .bufferedspectrogram import BufferedSpectrogram
from .fulltraceplot import (
    MODE_ALL,
    MODE_SINGLE,
    OVERVIEW_ACTIVITY,
    OVERVIEW_WAVEFORM,
    FullTracePlot,
    secs_to_str,
)
from .selectviewbox import SelectViewBox
from .timeaxisitem import TimeAxisItem
from .timeplot import TICK_VALUES_MIN_HEIGHT, TimePlot
from .spectrogramplot import SpectrogramPlot
from .labels import (
    DEFAULT_CATEGORIES,
    Label,
    LabelSet,
    categories_from_settings,
    categories_to_settings,
    sidecar_path,
)

# `layers.KIND_POINT` below is the same string for the same idea in the
# immutable half; aliased rather than renamed so neither module has to know
# about the other.
from .labels import KIND_POINT as LABEL_POINT
from .labeloverlay import (
    CategoryDialog,
    CategoryStrip,
    LabelOverlay,
    LabelTable,
    category_tip,
)
from .eventoverlay import (
    LEGEND_H,
    LEGEND_W,
    SURFACE_NAVIGATOR,
    SURFACE_SPECTROGRAM,
    SURFACE_TRACE,
    AnnotationLayer,
    EventOverlay,
    _passive,
    describe_mark,
    legend_icon,
    mark_time,
    span_icon,
    swatch_icon,
)
from .controlpanel import ControlPanel
from .alignment import SplitCoverage
from .layers import (
    KIND_POINT,
    KIND_SPAN,
    LAYER_DET_EXPLAINED,
    LAYER_DET_UNEXPLAINED,
    TRACK_PULSES,
    TRACK_TRIALS,
)
from .analyzer import PlainAnalyzer, style_result_table
from .statisticsanalyzer import StatisticsAnalyzer


log = logging.getLogger(__name__)


def set_axis_style(axis: pg.AxisItem, **style) -> bool:
    """`AxisItem.setStyle`, but only when it would change something.

    Returns whether it wrote.  `setStyle` has no idea what it already holds:
    it drops the cached picture, re-measures the axis and schedules a
    repaint for every call, so re-stating the chrome an axis already has
    costs a full axis repaint each -- 0.28 ms measured, times every axis of
    every lane, on every layout pass.
    """
    if all(axis.style.get(key) == value for key, value in style.items()):
        return False
    axis.setStyle(**style)
    return True


#: What each annotation surface chip promises, in the reader's terms rather
#: than in the code's.
ANNOTATION_SURFACE_TIPS = {
    SURFACE_TRACE: "Draw the annotations over the waveform lanes",
    SURFACE_SPECTROGRAM: "Draw the annotations over the spectrogram lanes",
    SURFACE_NAVIGATOR: (
        "Draw the annotations over the navigator strip, full height, so the "
        "whole session shows where the events are"
    ),
}

#: The two chip rows of the Annotations group, as ``(caption, tip, tracks)``.
#: Which row a layer lands on is read off its own ``track``, so a bundle
#: carrying a layer this file has never heard of still gets a chip: the last
#: row's empty set is the catch-all.
#:
#: Two rows and not one, because the ten chips of a session bundle measure
#: 1133 px, which did not fit the 678 px field a group had when every group
#: was on screen at once.  Behind tabs the page is the whole bar -- measured
#: 1183 px on a 1200 px window -- so they would now fit on one row, and the
#: rows stay two anyway: they are captioned SENT and HEARD, which is the
#: division the reader thinks in, not a way of coping with a narrow column.
#: Two and not three because this group is the tallest in the bar and the
#: bar is as tall as its tallest page, so a third row would take 24 px out
#: of every lane in the stack.
ANNOTATION_CHIP_ROWS = (
    (
        "Sent",
        "What the stimulator was commanded to do: the trials of the protocol "
        "and the pulses it emitted.",
        frozenset((TRACK_TRIALS, TRACK_PULSES)),
    ),
    (
        "Heard",
        "What the recording turned out to contain, and what the device logged "
        "about itself: detections, localization runs, session events, the "
        "control track.",
        frozenset(),
    ),
)


class RecordingInfo(NamedTuple):
    """What the loader opened, in the shape a `soundfile.info` header has.

    `SessionMeta.check_recording` accepts an already-read header so a caller
    that has the file open does not open it twice.  A split recording has no
    single header -- four files are one recording here -- so the browser
    hands it the loader's own totals instead, which is the only frame count
    that describes what is on screen.
    """

    samplerate: float
    frames: int
    channels: int


def gap_text(delta: float) -> str:
    """A signed time difference, in the unit that carries meaning at its size.

    Milliseconds are what matters when the pointer is on an event or a
    recorder loses time at a join; fourteen thousand of them are not a
    reading, they are a statement that the thing is nowhere near.
    """
    if abs(delta) < 1.0:
        return f"{1e3 * delta:+.1f} ms"
    return f"{delta:+.2f} s"


def annotation_chip_row(track: str) -> int:
    """Which chip row a layer of `track` belongs on.

    The last row takes everything unclaimed, so a track added to the reader
    later gets a chip in the bar rather than no chip at all.
    """
    for index, (_caption, _tip, tracks) in enumerate(ANNOTATION_CHIP_ROWS):
        if track in tracks:
            return index
    return len(ANNOTATION_CHIP_ROWS) - 1


pg.setConfigOption("useNumba", True)


def frame_widget(widget: QWidget) -> None:
    """Give a container widget the 1px hairline frame of the design system."""
    theme.frame(widget)


def caption_label(text: str, shortcut: str = "") -> QLabel:
    """Small caps caption above a parameter field.

    Parameters
    ----------
    text: str
        Name of the parameter.
    shortcut: str
        Keyboard shortcut, appended to the caption so that it is visible
        instead of hiding in a tool tip.
    """
    if shortcut:
        text = f"{text}  {shortcut}"
    label = QLabel(text.upper())
    label.setFont(theme.font_ui(theme.SIZE_SMALL_PT))
    theme.tint(label, "fg.muted")
    return label


class ParameterGroup(QWidget):
    """A labelled and boxed group of related parameter widgets.

    A framed body into which parameter rows are added with `add_row()`,
    under a small-caps caption -- except in the parameter bar, where the
    group's tab button carries the name and `caption=False` drops it.

    One group is on screen at a time, in the bar's `QStackedLayout`, and
    `equalize()` gives every group the same frame height so that changing
    tab cannot change the height of the bar and resize every lane in the
    channel stack.
    """

    #: Grid column that soaks up the width the fields do not want.
    #:
    #: A group used to get 1/N of the bar and every field was starved, so
    #: the stretch sat on column 1 and the fields took what was going.  A
    #: group now gets the WHOLE bar: measured on a 1449 px window, that put
    #: the Audio group's Source and Speed combo boxes at 1327 px each and
    #: the spectrogram's overlap slider at 1182.  A 1182 px slider is a
    #: genuine gain in resolution; a 1327 px combo box reading "1" is a
    #: defect.  So the stretch moves out here, past any column `add_row`
    #: will ever fill, and the widgets that actually want the width ask for
    #: it themselves with `expanding()`.
    SPACER_COLUMN = 8

    def __init__(
        self, title: str, parent: Optional[QWidget] = None, caption: bool = True
    ):
        super().__init__(parent)
        #: the group's name.  Read by the bar's tab strip, by the settings
        #: key that remembers which tab was open, and by the tests -- which
        #: used to recover it from the caption widget that `caption=False`
        #: now removes.
        self.title = title
        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(theme.S2)
        if caption:
            vbox.addWidget(caption_label(title))
        self.body = QWidget(self)
        frame_widget(self.body)
        self.grid = QGridLayout(self.body)
        self.grid.setContentsMargins(theme.S8, theme.S4, theme.S8, theme.S4)
        self.grid.setHorizontalSpacing(theme.S6)
        self.grid.setVerticalSpacing(theme.S2)
        self.grid.setColumnStretch(ParameterGroup.SPACER_COLUMN, 1)
        vbox.addWidget(self.body)
        self.rows = 0
        #: ``(row name, shortcut)`` for every row that prints one.  The bar
        #: shows one group at a time now, so a reader looking for a key can
        #: no longer read every group's off the screen at once; the tab's
        #: tool tip carries its own group's, and the cheat sheet and the
        #: command palette carry all of them.
        self.shortcuts: list = []

    @staticmethod
    def expanding(widget: QWidget) -> QWidget:
        """Mark one field as wanting the width the group has, and return it.

        For the fields where width IS resolution -- the filter and envelope
        cutoff sliders, the overlap slider, the chip strips -- and for
        nothing else.  `add_row` reads the policy back and hands that column
        the stretch instead of the spacer; see `SPACER_COLUMN`.
        """
        policy = widget.sizePolicy()
        policy.setHorizontalPolicy(QSizePolicy.Expanding)
        widget.setSizePolicy(policy)
        return widget

    #: Horizontal policies that mean "give me the leftover".  `Ignored` is
    #: in here on purpose: it does NOT carry Qt's ExpandFlag, but the two
    #: labels that use it in this bar -- the annotation pointer readout and
    #: the Labels file row -- elide themselves to whatever width they are
    #: given and are useless at their size hint, which is zero.
    WIDE_POLICIES = (
        QSizePolicy.Expanding,
        QSizePolicy.MinimumExpanding,
        QSizePolicy.Ignored,
    )

    @staticmethod
    def wants_width(widget: QWidget) -> bool:
        return widget.sizePolicy().horizontalPolicy() in ParameterGroup.WIDE_POLICIES

    def claim_stretch(self, column: int, widget: QWidget) -> None:
        """Give `column` the leftover width if its widget asked for it.

        The spacer keeps the leftover only while no field wants it.  Both
        with stretch would split it, and a slider that gets half of what is
        going is a slider whose resolution depends on how many combo boxes
        happen to share its group.
        """
        if not self.wants_width(widget):
            return
        self.grid.setColumnStretch(column, 1)
        self.grid.setColumnStretch(ParameterGroup.SPACER_COLUMN, 0)

    def add_row(self, label: str, shortcut: str, *widgets: QWidget) -> list:
        """Add a labelled row of widgets, and return everything it placed.

        The caption comes back with the fields so a caller can hide a whole
        row: hiding only the field leaves its label beside nothing, which
        reads as a control that failed to load.
        """
        caption = caption_label(label, shortcut)
        if shortcut:
            self.shortcuts.append((label, shortcut))
        self.grid.addWidget(caption, self.rows, 0)
        for i, w in enumerate(widgets):
            self.grid.addWidget(w, self.rows, 1 + i)
            self.claim_stretch(1 + i, w)
        self.rows += 1
        return [caption, *widgets]

    def add_span_row(self, widget: QWidget, columns: int = 2) -> QWidget:
        """Add a captionless row whose widget takes the group's whole width.

        For a field that is a strip of controls naming themselves, and whose
        caption would only repeat the group title.  What it buys is the
        caption column: measured on a 1200 px window, the chip strip gets
        1167 px of the Labels page's 1183 rather than stopping at the field
        column.
        """
        self.grid.addWidget(widget, self.rows, 0, 1, columns)
        # unconditionally: a row spans because its widget wants the whole
        # width, which is the only reason to give up the caption column
        self.grid.setColumnStretch(columns - 1, 1)
        self.grid.setColumnStretch(ParameterGroup.SPACER_COLUMN, 0)
        self.rows += 1
        return widget

    #: Qt's "no maximum", for releasing a previous setFixedHeight.
    UNBOUNDED = 16777215

    @staticmethod
    def equalize(groups: "list[ParameterGroup]") -> None:
        """Give every group the same frame height.

        A group with two rows and one with three used to produce frames of
        185, 175 and 130 px whose captions sat on three different baselines
        - three separate boxes rather than one bar.  The shorter groups get
        the tallest one's height and keep their rows packed at the top.

        Callable again after a group's contents change, which the annotation
        chips do: they are rebuilt whenever a file is loaded.  That needs two
        things this used to skip.  The previous `setFixedHeight` has to be
        released first, or a group can only ever grow to whatever the bar was
        when it was first built; and the layout has to be activated before it
        is measured, because widgets added a moment ago are not in its size
        hint yet -- which is how the class chips ended up clipped to a four
        pixel sliver.
        """
        if not groups:
            return
        for group in groups:
            group.body.setMinimumHeight(0)
            group.body.setMaximumHeight(ParameterGroup.UNBOUNDED)
            # Every nested layout, not just the group's own: a row whose
            # field is a container -- the chip strip is one -- reports the
            # height it had before its contents changed until that inner
            # layout is activated, and the group is then sized for a row
            # that is no longer there.
            for layout in group.body.findChildren(QLayout):
                layout.invalidate()
                layout.activate()
            group.grid.invalidate()
            group.grid.activate()
        height = max(g.grid.totalSizeHint().height() for g in groups)
        for group in groups:
            # absorb the added height below the last row, not between rows:
            group.grid.setRowStretch(group.rows, 1)
            group.body.setFixedHeight(height)


class ParameterTabs(QWidget):
    """The parameter bar's groups, one on screen at a time behind a tab.

    Why
    ---

    The groups used to sit side by side in equal grid columns, which made the
    bar's minimum width the SUM of theirs.  Measured on a four channel
    recording in a window asked to be 1200x900: 1445 px empty, and **2456 px
    once a session bundle is loaded** and the annotations group grows its ten
    layer chips -- at which point `window.resize(1200, 900)` returns a
    2456 px window.  That is the second step of this fork's own workflow, and
    it did not fit any laptop.  A `QStackedLayout` hints the MAX over its
    pages rather than the sum, which is the whole of the fix.

    Not a `QTabWidget`
    ------------------

    `theme`'s stylesheet styles `QTabBar` with unconditional *type* selectors
    written for the file spine down the left edge of the window -- zero
    padding, no right border, and a selected mark that is a rule down the
    LEFT side.  A horizontal strip at the bottom of the window would inherit
    all of it, and the only way out would be re-scoping the file tabs' own
    styling.  Checkable `QToolButton`s in an exclusive `QButtonGroup` inherit
    none of it and are the same idiom as the tool bar's region-mode segment,
    which the reader already knows.

    Height
    ------

    Two things keep a tab change from resizing every lane in the stack.
    `QStackedLayout` reports the max over ALL pages, hidden ones included, so
    the bar's height does not depend on which tab is current; and this widget
    is `QSizePolicy.Fixed` vertically, because `QStackedLayout` inherits
    `QLayout.expandingDirections()` -- which is both directions -- and
    `QWidgetItem` hands that to its widget.  Measured without the policy: the
    bar took 402 px against a 154 px size hint and the channel stack's scroll
    viewport fell to 247 px over 616 px of content.
    """

    #: Emitted with the group title when the reader picks a tab.
    sigTabChanged = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.groups: list[ParameterGroup] = []
        self.buttons: dict[str, QToolButton] = {}
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(theme.S2)

        self.strip = QWidget(self)
        row = QHBoxLayout(self.strip)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.S2)
        row.addStretch(1)
        self.strip.setFixedHeight(theme.CHIP_HEIGHT)
        outer.addWidget(self.strip)

        host = QWidget(self)
        self.stack = QStackedLayout(host)
        self.stack.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(host)
        # see the class docstring: without this the stack's inherited
        # expandingDirections() grows the bar into the channel stack
        host.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

        self.group_buttons = QButtonGroup(self)
        self.group_buttons.setExclusive(True)
        self.stack.currentChanged.connect(self._changed)

    def add(self, group: ParameterGroup) -> QToolButton:
        """Give `group` a page and a tab, and return the tab button."""
        button = QToolButton(self.strip)
        button.setObjectName("paramTab")
        button.setText(group.title.upper())
        button.setCheckable(True)
        button.setFont(theme.font_ui(theme.SIZE_SMALL_PT))
        button.setFixedHeight(theme.CHIP_HEIGHT)
        # NoFocus, the way the channel rail's toggles are: a focused
        # checkable QToolButton eats Space, which is play-window, and the
        # arrow keys, which nudge the view -- and a tab is clicked often.
        button.setFocusPolicy(Qt.NoFocus)
        button.setToolTip(self.group_tip(group))
        index = len(self.groups)
        button.clicked.connect(lambda _c=False, i=index: self.show_index(i))
        self.group_buttons.addButton(button, index)
        # ahead of the trailing stretch, so the tabs stay left-aligned
        layout = self.strip.layout()
        layout.insertWidget(layout.count() - 1, button)
        self.stack.addWidget(group)
        self.groups.append(group)
        self.buttons[group.title] = button
        if index == 0:
            button.setChecked(True)
        return button

    @staticmethod
    def group_tip(group: ParameterGroup) -> str:
        """What one tab says: its name, and the keys its rows print."""
        lines = [f"{group.title} parameters"]
        for name, shortcut in group.shortcuts:
            lines.append(f"  {name}  {shortcut}")
        return "\n".join(lines)

    def _changed(self, index: int) -> None:
        if 0 <= index < len(self.groups):
            button = self.group_buttons.button(index)
            if button is not None and not button.isChecked():
                button.setChecked(True)
            self.sigTabChanged.emit(self.groups[index].title)

    def show_index(self, index: int) -> None:
        if 0 <= index < len(self.groups):
            self.stack.setCurrentIndex(index)

    def show_group(self, title: str) -> bool:
        """Raise the tab called `title`.  False when there is no such tab."""
        for index, group in enumerate(self.groups):
            if group.title == title:
                self.stack.setCurrentIndex(index)
                return True
        return False

    def current_title(self) -> str:
        index = self.stack.currentIndex()
        if 0 <= index < len(self.groups):
            return self.groups[index].title
        return ""

    def set_alert(self, title: str, on: bool, tip: str = "") -> None:
        """Mark a tab whose page is saying something that must not be missed.

        The Labels page can read READ-ONLY or SAVE FAILED, and the
        annotations badge can say the bundle names another recording;  behind
        an unraised tab those are the two states where a hidden page costs
        data rather than convenience.

        The mark is a "!" appended to the tab's name, and the reason it is a
        glyph rather than a colour is the reason the spectrogram's colour map
        is CET-CBL2: a reader who cannot separate the hues would be told
        nothing.  It is not tinted at all -- a per-widget ``color:`` would
        also have to fight the checked state's own colour, for a cue that
        would then be the weaker half of this one.

        The button is left at the width it was given when the bar was built,
        so a state arriving never reflows the strip and slides a tab out
        from under the pointer.
        """
        button = self.buttons.get(title)
        if button is None:
            return
        on = bool(on)
        if button.property("alert") == on and button.property("alertTip") == tip:
            return
        button.setProperty("alert", on)
        button.setProperty("alertTip", tip)
        button.setText(f"{title.upper()} !" if on else title.upper())
        base = self.group_tip(self.groups[list(self.buttons).index(title)])
        button.setToolTip(f"{tip}\n\n{base}" if on and tip else base)

    def polish(self) -> None:
        """Re-apply the theme's metrics to the strip after a live switch."""
        for button in self.buttons.values():
            button.setFont(theme.font_ui(theme.SIZE_SMALL_PT))
            button.setFixedHeight(theme.CHIP_HEIGHT)
        self.strip.setFixedHeight(theme.CHIP_HEIGHT)


class LogSlider(QSlider):
    """Horizontal slider with a logarithmic mapping onto a frequency range.

    The slider resolution is fixed; `value_hz()` and `set_hz()` convert
    between slider steps and Hz. A zero lower bound is supported: the
    first step maps to 0 Hz exactly.
    """

    STEPS = 1000

    def __init__(self, fmin: float, fmax: float, parent: Optional[QWidget] = None):
        super().__init__(Qt.Horizontal, parent)
        self.fmin = float(fmin)
        self.fmax = float(fmax)
        self.setRange(0, self.STEPS)
        self.setSingleStep(1)
        self.setPageStep(self.STEPS // 20)
        self.setMinimumWidth(theme.AXIS_LEFT_WIDTH * 2)

    def _lo(self) -> float:
        # a decade below Nyquist/1000 is a sane floor for a log axis:
        return max(self.fmin, self.fmax / 1e4)

    def value_hz(self) -> float:
        if self.value() == 0 and self.fmin <= 0:
            return 0.0
        lo = self._lo()
        frac = self.value() / self.STEPS
        return float(lo * (self.fmax / lo) ** frac)

    def set_hz(self, freq: float) -> None:
        lo = self._lo()
        if freq <= lo:
            value = 0
        else:
            frac = np.log(freq / lo) / np.log(self.fmax / lo)
            value = int(round(self.STEPS * min(1.0, max(0.0, frac))))
        blocked = self.blockSignals(True)
        self.setValue(value)
        self.blockSignals(blocked)


def colormap_icon(index: int, width: int = 64, height: int = 12) -> QIcon:
    """Render a spectrogram colormap into a gradient swatch icon."""
    cmap = theme.spectrogram_colormap(index)
    pixmap = QPixmap(width, height)
    pixmap.fill(theme.qcolor("bg.raised"))
    painter = QPainter(pixmap)
    colors = cmap.map(np.linspace(0.0, 1.0, width), mode="byte")
    for x in range(width):
        r, g, b = (int(v) for v in colors[x][:3])
        painter.setPen(theme.qcolor(f"#{r:02x}{g:02x}{b:02x}"))
        painter.drawLine(x, 0, x, height)
    painter.setPen(theme.pen("border"))
    painter.setBrush(Qt.NoBrush)
    painter.drawRect(0, 0, width - 1, height - 1)
    painter.end()
    return QIcon(pixmap)


class ColorMapCombo(QComboBox):
    """Combo box showing the actual gradient of each spectrogram colormap."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setIconSize(QSize(64, 12))
        self.setFont(theme.font_ui(theme.SIZE_SMALL_PT))
        self.populate()
        self.setEditable(False)

    def populate(self) -> None:
        """(Re)fill the combo from the active theme's colormap list.

        The two themes offer different maps -- and different numbers of them
        -- so a theme switch has to rebuild the items, not just repaint the
        swatches, or the labels would name maps that are no longer on offer.
        """
        blocked = self.blockSignals(True)
        keep = self.currentIndex()
        self.clear()
        self.setFont(theme.font_ui(theme.SIZE_SMALL_PT))
        for i, label in enumerate(theme.spectrogram_map_labels()):
            self.addItem(colormap_icon(i), label)
        if 0 <= keep < self.count():
            self.setCurrentIndex(keep)
        self.blockSignals(blocked)

    # kept as the name the theme switch calls
    refresh_swatches = populate


class LevelMeter(QWidget):
    """A slim peak-level bar for one channel.

    Replaces a right-aligned ``-100.0 dB`` label.  That label was the widest
    thing in the rail -- it alone set the rail's width, because it has to be
    sized for the longest string it can ever hold -- and sixteen numbers
    stacked down the side are read one at a time anyway.  A bar is read as a
    column: which electrodes are hot is visible without reading anything.

    The number is not lost; it moves to the tooltip and, for the current
    channel, to the status bar.
    """

    HEIGHT = 3
    #: dB full scale at the bottom of the bar.
    FLOOR_DB = -60.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self.db = None
        self.selected = False
        self.setFixedHeight(self.HEIGHT)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMinimumWidth(theme.S16)

    def set_level(self, db, selected: bool = False) -> None:
        self.db = db
        self.selected = bool(selected)
        self.setToolTip(
            "Peak level of the visible window, dB full scale"
            if db is None
            else f"{db:.1f} dB peak in the visible window"
        )
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.setPen(Qt.NoPen)
        painter.fillRect(self.rect(), theme.brush("border"))
        if self.db is None:
            painter.end()
            return
        frac = max(0.0, min(1.0, 1.0 - self.db / self.FLOOR_DB))
        width = int(round(self.width() * frac))
        if width > 0:
            painter.fillRect(
                0,
                0,
                width,
                self.height(),
                theme.brush("primary" if self.selected else "fg.muted"),
            )
        painter.end()


class ChannelRailRow(QWidget):
    """One row of the channel rail: number, name, solo, mute and level.

    There is exactly one rail design, whatever the channel count: a single
    line - badge, solo, mute, peak level - pinned to the *top* edge of the
    lane it controls, plus the electrode name field which folds out inline
    on the selected channel when its lane is tall enough to hold a second
    line.

    Two designs used to exist, a two line card for a handful of channels
    and a compact line for many, and the card was vertically centred: on a
    stereo file it spent 290 px of rail on 65 px of controls and ran a
    full height selection rule down the side of empty space.  Growing the
    file from two channels to sixteen should not change what the controls
    look like or where they sit.
    """

    def __init__(self, channel: int, browser: "DataBrowser"):
        super().__init__(browser)
        self.channel = channel
        self.browser = browser
        self.drag_origin = None
        self.expanded = False
        self.setFocusPolicy(Qt.StrongFocus)
        # a plain QWidget ignores a background set through the style sheet
        # unless it is told to paint one - without this the current channel
        # gets no highlight at all and the cue is bold text alone:
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedWidth(DataBrowser.RAIL_WIDTH)
        self.setToolTip("S solo, M mute, double-click maximise, drag reorder")

        vbox = QVBoxLayout(self)
        # A hair of top margin so the badge sits on the top edge of the plot
        # beside it rather than floating in the middle of the lane.  The
        # figure has none of its own to match any more: a lane's vertical
        # pixels are `lane_geometry`'s exact budget, not the figure's to pad
        # (`theme.style_channel_figure`).
        vbox.setContentsMargins(0, theme.S2, 0, 0)
        vbox.setSpacing(0)
        # The selection highlight belongs to the *controls*, not to the
        # lane's worth of rail below them: a 290 px raised block with 65 px
        # of controls at the top of it highlights mostly nothing.  The lane
        # it points at is marked in the plot itself, by a raised view box.
        self.card = QWidget(self)
        self.card.setObjectName("railCard")
        self.card.setAttribute(Qt.WA_StyledBackground, True)
        card = QVBoxLayout(self.card)
        card.setContentsMargins(theme.S4, 0, theme.S4, 0)
        card.setSpacing(0)

        # Number on its own line, the two toggles side by side beneath it.
        # A single row of number + solo + mute is as wide as all three
        # together; stacking them makes the rail as wide as the widest one,
        # which is what a column of sixteen of them should cost.  Fully
        # vertical -- number, then solo, then mute -- does not fit: a lane is
        # 38 px at sixteen channels and three stacked items need more.
        top = QVBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(theme.S2)
        # just the number: "CH" repeated down sixteen rows is sixteen copies
        # of a word the plot caption beside it already says
        self.number = QLabel(f"{channel:02d}")
        self.number.setFont(theme.font_mono(theme.SIZE_SMALL_PT, bold=True))
        self.number.setMinimumWidth(
            theme.mono_metrics(theme.SIZE_SMALL_PT).horizontalAdvance("00")
        )
        self.number.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
        # A stacked row has to fit inside a dense lane.  Whatever this row
        # asks for, the grid gives it, so an unconstrained stack simply made
        # every lane taller: at 54 px the sixteen channel stack no longer fit
        # the window and five of them went below the scroll.
        self.number.setFixedHeight(theme.RAIL_NUMBER_HEIGHT)
        top.addWidget(self.number)
        toggles = QHBoxLayout()
        toggles.setContentsMargins(0, 0, 0, 0)
        toggles.setSpacing(theme.S2)
        self.solo_button = self._button("S", "Solo this channel")
        self.solo_button.clicked.connect(lambda: self.browser.toggle_solo(self.channel))
        toggles.addWidget(self.solo_button)
        self.mute_button = self._button("M", "Hide this channel")
        self.mute_button.clicked.connect(lambda: self.browser.toggle_mute(self.channel))
        toggles.addWidget(self.mute_button)
        top.addLayout(toggles)
        card.addLayout(top)
        self.level = LevelMeter(self.card)
        card.addWidget(self.level)

        # The name starts empty: pre-filling it with the channel index only
        # repeats the badge next to it and hides the fact that the field is
        # editable.  The placeholder says what it is for instead.
        self.name = QLineEdit(self)
        self.name.setPlaceholderText("electrode")
        self.name.setFont(theme.font_ui(theme.SIZE_SMALL_PT))
        self.name.setFrame(False)
        self.name.setToolTip("Electrode label")
        self.name.editingFinished.connect(self.rename)
        self.name.setVisible(False)
        card.addWidget(self.name)
        vbox.addWidget(self.card)
        vbox.addStretch(1)

        self.peak = 0.0
        self.update_state()

    def set_expanded(self, expanded: bool) -> None:
        """Fold the electrode name field in or out.

        Only the selected channel expands, and only when its lane is tall
        enough for a second line - at `theme.CHANNEL_DENSE_HEIGHT` there is
        room for the badge line and nothing else.
        """
        expanded = bool(expanded)
        if expanded == self.expanded:
            return
        self.expanded = expanded
        self.name.setVisible(expanded)
        self.setToolTip(
            "S solo, M mute, double-click maximise, drag reorder"
            + ("" if expanded else "  (select this channel to rename it)")
        )

    def _button(self, text: str, tip: str) -> QToolButton:
        button = QToolButton(self)
        button.setObjectName("railToggle")
        button.setText(text)
        button.setCheckable(True)
        button.setToolTip(tip)
        button.setFont(theme.font_mono(theme.SIZE_SMALL_PT, bold=True))
        button.setFocusPolicy(Qt.NoFocus)
        # a one glyph toggle does not need the 45 px the generic tool button
        # padding gives it, and in a stacked rail two of them side by side
        # are what sets the column's width
        button.setFixedSize(theme.S16 + theme.S2, theme.RAIL_TOGGLE_HEIGHT)
        return button

    def rename(self) -> None:
        self.browser.channel_names[self.channel] = self.name.text()

    def set_peak(self, peak: float, ampl_max: float) -> None:
        """Show the peak level of the visible window in dB full scale."""
        self.peak = peak
        selected = self.channel == self.browser.current_channel
        if peak <= 0 or ampl_max <= 0:
            self.level.set_level(None, selected)
            return
        db = 20 * np.log10(min(1.0, peak / ampl_max))
        self.level.set_level(db, selected)

    def update_state(self) -> None:
        """Repaint solo/mute state and the current-channel emphasis."""
        current = self.channel == self.browser.current_channel
        self.level.set_level(self.level.db, current)
        self.solo_button.setChecked(self.channel in self.browser.solo_channels)
        self.mute_button.setChecked(self.channel in self.browser.muted_channels)
        self.number.setFont(theme.font_mono(theme.SIZE_SMALL_PT, bold=current))
        # The current channel is marked by a 2 px primary rule down the
        # left edge plus a raised surface and brighter, bolder text - not
        # by flooding the row with saturated blue, which reads as an error
        # state and drowns the data next to it.
        if current:
            self.setStyleSheet(
                f"#railCard {{"
                f" background: {theme.token('bg.raised')};"
                f" border-left: {theme.FOCUS_WIDTH}px solid {theme.token('primary')};"
                f" }}"
                f" QLabel {{ color: {theme.token('fg')}; background: transparent; }}"
            )
        else:
            self.setStyleSheet(
                f"#railCard {{"
                f" background: transparent;"
                f" border-left: {theme.FOCUS_WIDTH}px solid transparent;"
                f" }}"
                f" QLabel {{"
                f" color: {theme.token('fg.muted')}; background: transparent; }}"
            )

    def mouseDoubleClickEvent(self, event) -> None:
        self.browser.toggle_maximize(self.channel)

    def mousePressEvent(self, event) -> None:
        self.drag_origin = event.pos()
        modifiers = QApplication.keyboardModifiers()
        self.browser.rail_clicked(self.channel, bool(modifiers & Qt.ShiftModifier))

    def mouseMoveEvent(self, event) -> None:
        if self.drag_origin is None:
            return
        dy = event.pos().y() - self.drag_origin.y()
        if abs(dy) < self.height() // 2:
            return
        self.browser.move_channel(self.channel, 1 if dy > 0 else -1)
        self.drag_origin = None

    def mouseReleaseEvent(self, event) -> None:
        self.drag_origin = None

    def event(self, event) -> bool:
        # claim S and M before the application-wide shortcuts see them:
        if (
            event.type() == QEvent.ShortcutOverride
            and event.key() in (Qt.Key_S, Qt.Key_M)
            and event.modifiers() == Qt.NoModifier
        ):
            event.accept()
            return True
        return super().event(event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_S:
            self.browser.toggle_solo(self.channel)
        elif event.key() == Qt.Key_M:
            self.browser.toggle_mute(self.channel)
        else:
            super().keyPressEvent(event)


class SharedTimeAxis(TimeAxisItem):
    """The one time axis of the channel stack, in a row of its own.

    Every lane hides its own time axis and this item, in a dedicated row
    below the last lane, carries the tick values for all of them.  That is
    what makes the lanes *exactly* the same height: the bottom lane no
    longer has to be taller than its neighbours by however much axis
    chrome pyqtgraph happens to need, a difference that used to be
    measured after the fact and only converged over several layout passes.

    The audian time axis puts its label in the left gutter, which the
    dense stack reclaims; here the label goes back under the ticks where
    pyqtgraph puts it, because this row is not squeezed for height.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.mode_source = None

    def resizeEvent(self, ev=None) -> None:
        pg.AxisItem.resizeEvent(self, ev)

    def sync_starttime_mode(self) -> None:
        """Adopt the start-time mode of the lanes.

        The application sets that mode straight on the per-plot axes
        (`PlotRanges.set_starttime`), which this item is not one of, so it
        reads it back from the lane axis it was given instead of waiting
        for a call that never comes.
        """
        if self.mode_source is None:
            return
        mode = self.mode_source()
        if mode is not None and mode != self._starttime_mode:
            self.set_starttime_mode(mode)

    def tickStrings(self, values, scale, spacing):
        self.sync_starttime_mode()
        return super().tickStrings(values, scale, spacing)


class DataBrowser(QWidget):
    # Width of the channel rail.  Wide enough for the number, solo and mute,
    # and nothing else: the rail used to be 188 px because it carried a
    # right-aligned '-100.0 dB' label, which has to be sized for the longest
    # string it can ever hold and so set the width of the whole column on its
    # own.  The level is a slim bar now (LevelMeter) with the number in the
    # tooltip, which reads better across sixteen channels anyway and costs a
    # third of the space.  Then the row itself was stacked -- number over
    # solo+mute -- so the column is as wide as its widest line rather than as
    # wide as all three side by side, and the corner readout that used to set
    # the floor moved into the status bar, which was already printing the
    # same range.
    RAIL_WIDTH = 48
    MAX_SPECTROGRAM_CHANNELS = 4

    #: Key the annotation switches are saved under.  One key holding one
    #: entry per layer, not one key per layer: `save_setting` rewrites the
    #: whole settings file, so thirteen keys would be thirteen rewrites.
    ANNOTATION_SETTING = "annotations"
    #: Bumped when the shape of that value changes, so an older audian's
    #: settings can be recognised rather than half-read.
    ANNOTATION_SETTING_VERSION = 1

    #: Key the hand-made label *categories* are saved under.  The labels
    #: themselves are not a preference and never go here: they belong to one
    #: recording and live in a CSV beside it (`labels.sidecar_path`).  The
    #: vocabulary is the other way round -- the same reader labels many
    #: recordings with the same words.
    LABEL_SETTING = "labels"
    #: Bumped when the shape of that value changes.
    LABEL_SETTING_VERSION = 1

    #: Key the parameter bar's open tab is saved under, by group NAME.  Not
    #: by index: the Filter and Envelope groups only exist when the data has
    #: those traces, so the same index is a different tab on a different
    #: recording.  Not per recording either -- a reader walking the file
    #: spine of one session is doing one job, and a tab that flipped as they
    #: stepped would be a control moving under them.
    #: The two tabs that hold marks over the signal, and the whole of the
    #: distinction between them.
    #:
    #: They used to be "Annotations" and "Labels", which are the same word
    #: twice: both mean "something drawn over the recording", and a reader
    #: had to already know which was which.  The axis that actually
    #: separates them is whether audian may change them -- one set is read
    #: from a bundle the stimulator wrote and describes what *caused* the
    #: signal, the other is drawn afterwards by whoever is looking -- so
    #: that is what the names say.
    FIXED_TAB = "Fixed labels"
    EDITABLE_TAB = "Editable labels"

    PARAM_TAB_SETTING = "parameter-tab"
    #: Bumped when the shape of that value changes, or -- as in version 2 --
    #: when the tab NAMES change, since the value is one of them and a
    #: saved "Labels" no longer resolves to anything.
    PARAM_TAB_SETTING_VERSION = 2

    #: Key the trace / spectrogram split is saved under.  One key holding
    #: one number, for the same reason as above.
    #:
    #: The number is `spec_scale`: the spectrogram row over
    #: `theme.SPECTROGRAM_MIN_HEIGHT`, the allowance every lane grows by to
    #: make room for one.  It is the only height in this layout that does
    #: not move, and measuring the split against it is what makes a saved
    #: split mean the same thing on the next recording.
    #:
    #: Version 1 stored the trace over the spectrogram, which is
    #: dimensionless and looked portable and is not: the trace *is* the
    #: lane, and the lane at 1200x900 is 34 px on four channels and sixteen,
    #: 130 px on two, and 329 px in a window tall enough (1200x1298).  So
    #: holding that ratio made the spectrogram track the lane instead of
    #: itself.  Measured on the file found in the field, 2.7437722419928825
    #: -- a 281 px spectrogram dragged on a 1052 px lane -- replayed on a
    #: sixteen channel stack as a 41 px spectrogram, a height
    #: `spectrogramplot.can_render` refuses to draw at all when it is asked
    #: about a lane.  Three presses of F3 before the reader saw anything.
    PANEL_SPLIT_SETTING = "panel-split"
    #: Bumped when the shape of that value changes.  2: the number is the
    #: spectrogram over its default, not the trace over the spectrogram.
    #: 3: one number, not a number per F3 size -- F3 has one size now, so a
    #: version 2 file holds up to four splits with no way to say which of
    #: them the reader would have wanted kept.  It is dropped with a logged
    #: warning rather than guessed at, the rule this setting already had.
    PANEL_SPLIT_SETTING_VERSION = 3

    # y-range policies of the trace panels:
    y_shared = 0
    y_per_channel = 1
    y_fixed = 2
    y_modes = ["shared", "per-channel", "fixed ±1"]

    # region-selection modes (named MODE_* so the methods of the same
    # name further down the class body do not shadow them - F811):
    MODE_ZOOM = 0
    MODE_PLAY = 1
    MODE_ANALYZE = 2
    MODE_SAVE = 3
    MODE_ASK = 4
    #: label the selection with the current category, and keep labelling.
    #: The mode a reader working through a recording stays in; the one-off
    #: is the "Label as" submenu of MODE_ASK.
    MODE_LABEL = 5

    #: Play only the current channel, in mono.
    AUDIO_SELECTED = "selected"
    #: Play every shown channel, mixed down to stereo: the first half of
    #: them averaged into the left ear, the second half into the right.
    AUDIO_SHOWN = "shown"
    #: Play one explicitly chosen channel in each ear.  Unlike AUDIO_SHOWN
    #: this averages nothing and does not care which channels are visible:
    #: the pair is a deliberate choice, so hiding a channel must not
    #: silently change what is being heard.
    AUDIO_PAIR = "pair"

    sigRangesChanged = Signal(object, object)
    sigFilenameChanged = Signal(object, str)
    sigResolutionChanged = Signal()
    sigColorMapChanged = Signal()
    sigFilterChanged = Signal()
    sigEnvelopeChanged = Signal()
    sigTraceChanged = Signal(object, object, object)
    sigAudioChanged = Signal(object, object, object)
    sigAudioSourceChanged = Signal(object)
    sigAudioPairChanged = Signal(object, object)

    def __init__(
        self,
        file_path,
        load_kwargs,
        plugins,
        channels,
        audio,
        acts,
        save_path,
        events_path=None,
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)

        # actions of main window:
        self.acts = acts
        self.save_path = save_path

        # data:
        self.schannels = channels
        self.data = Data(file_path, **load_kwargs)
        self.plot_ranges = PlotRanges()
        self.trace_acts = []
        self.spec_acts = []

        # panels:
        self.panels = Panels()
        self.panels.add_trace()
        self.panels.add_spectrogram()

        # plugins:
        self.plugins = plugins
        self.analysis_table = None
        self.analyzers = []
        self.plugins.setup_traces(self)
        self.data.setup_traces()

        # channel selection:
        self.show_channels = None
        self.current_channel = 0
        # What playback sends to the speakers.  "selected" is the default:
        # averaging eight electrodes of an array into one ear is not a
        # signal anybody wants to listen to, and the point of selecting a
        # channel is usually to hear that channel.
        self.audio_source = DataBrowser.AUDIO_SELECTED
        self.audio_left = 0
        self.audio_right = min(1, max(0, self.data.channels - 1))
        self.selected_channels = []
        # non-destructive filters over show_channels:
        self.solo_channels = []
        self.muted_channels = []
        self.maximized_channel = None
        self.channel_order = []
        self.channel_names = {}

        # view:
        self.setting = False

        # How a lane showing both is split: the spectrogram's height as a
        # multiple of the height it opens at.  `None` is not "no split" but
        # "the split it opens on", which is a function of the lane height
        # rather than a constant -- see `default_spec_height`.  One number
        # on the browser, not one per channel, which is why dragging the
        # boundary in any lane moves all of them.
        #
        # One number and not four.  It was keyed by `show_specs` back when
        # F3 stepped through four sizes and each kept its own split; F3 is a
        # toggle now, so three of those four keys were unreachable and the
        # fourth was the split.
        self.spec_scale = None
        self.restore_panel_split()
        # the y gutter every lane reserves, a stack-wide decision made by
        # `adjust_layout` and re-used by the drag:
        self.lane_left_width = theme.AXIS_LEFT_WIDTH

        self.region_mode = DataBrowser.MODE_ASK
        # one-shot override set by a modified rubber-band drag:
        self.region_mode_override = None

        specs = self.data.get_trace_names(BufferedSpectrogram)
        self.spectrogram = specs[0] if len(specs) > 0 else ""
        self.spectrogram_power = ""

        self.grids = 0
        self.show_traces = True
        self.show_specs = 0
        self.show_powers = False
        self.show_cbars = True
        self.show_fulldata = True
        # One full-height spectrogram of the mean power over the array,
        # instead of one stripe per electrode.  Lives inside the traces-off
        # all-spectrograms mode and `set_panels` holds it to that.
        self.mean_spec = False
        # whether Shift+F2 was the thing that turned the traces off, so that
        # pressing it twice puts the reader back exactly where they were
        self.traces_before_mean = False

        # auto scroll:
        self.scroll_step = 0.0
        self.scroll_timer = QTimer(self)
        self.scroll_timer.timeout.connect(self.scroll_further)

        # audio:
        self.audio = audio
        self.audio_timer = QTimer(self)
        self.audio_timer.timeout.connect(self.mark_audio)
        self.audio_time = 0.0
        self.audio_use_heterodyne = False
        self.audio_heterodyne_freq = 40000.0
        self.audio_rate_fac = 1.0
        self.audio_tmax = 0.0
        self.audio_markers = []  # vertical lines showing position while playing

        # window:
        self.vbox = QVBoxLayout(self)
        self.vbox.setContentsMargins(0, 0, 0, 0)
        self.vbox.setSpacing(0)
        self.setEnabled(False)
        self.gui = None
        self.toolbar = None
        self.parambar = None
        # panel and channel the pointer is currently over, for the
        # axis-agnostic zoom gestures (see axis_under_pointer):
        self.hover_panel = None
        self.hover_channel = 0
        self.audiofacw = None
        self.audiosrcw = None
        self.audioleftw = None
        self.audiorightw = None
        self.audiopairw = None
        self.nfftw = None
        self.ofracw = None
        self.ofraclabelw = None
        self.cmapw = None
        self.ymodew = None
        self.hpfw = None
        self.lpfw = None
        self.envfw = None
        self.hpsliderw = None
        self.lpsliderw = None
        self.envsliderw = None
        self.linkbandw = None
        self.link_band = False
        # channel stack (scroll area, grid of rail rows and figures):
        self.splitter = None
        self.stack_area = None
        self.stack_grid = None
        self.stack_pane = None
        self.rail_rows = []
        self.rail_visible = True
        self.scrollable_stack = False

        # debounced recomputations:
        self.resize_timer = QTimer(self)
        self.resize_timer.setSingleShot(True)
        self.resize_timer.timeout.connect(self.apply_resize)
        self.filter_timer = QTimer(self)
        self.filter_timer.setSingleShot(True)
        self.filter_timer.timeout.connect(self.apply_filter)
        self.pending_highpass = None
        self.pending_lowpass = None
        self.envelope_timer = QTimer(self)
        self.envelope_timer.setSingleShot(True)
        self.envelope_timer.timeout.connect(self.apply_envelope)
        self.pending_envelope = None
        self.overview_timer = QTimer(self)
        self.overview_timer.timeout.connect(self.report_overview_progress)

        # y-range policy:
        self.y_mode = DataBrowser.y_shared
        self.y_locked = False
        self.spec_warned = False

        # cross hair:
        self.cross_hair = False

        # labels: the marks this reader makes, in a sidecar CSV beside the
        # recording.  Deliberately a separate store, file and parameter-bar
        # group from the annotations below: one is a claim the log makes,
        # the other a claim the reader makes, and the provenance of a mark
        # must not be a flag on a shared row.  The vocabulary is a
        # preference and is restored before any file is open, so the bar can
        # show it whether or not this recording has any labels yet.
        self.labels = LabelSet(self.restore_label_categories())
        #: the category the next drag writes; "" while the vocabulary is empty
        self.current_category = (
            self.labels.categories[0].name if self.labels.categories else ""
        )
        self.label_overlays = []
        #: The one label the reader has picked out to edit, held by identity
        #: rather than by row: a row number goes stale the moment any earlier
        #: label is removed, and the selection has to survive that.
        self.selected_label = None
        #: The overlay carrying that label's grips.  One overlay, not one per
        #: lane, even for a label that is drawn on every lane -- the reader
        #: pointed at one of them, and grips on the other fifteen would be
        #: fifteen places to drag the same box from.
        self.selected_overlay = None
        #: QActions bound to the digit keys, one per category, rebuilt
        #: whenever the vocabulary changes
        self.category_acts = []
        self.label_group = None
        self.label_chipbox = None
        self.label_chips = {}
        self.label_showw = None
        self.label_statusw = None
        self.label_undow = None
        #: a sidecar write is queued for the end of this turn of the loop
        self.label_save_pending = False
        #: the last message `save_labels` produced, so the bar can say
        #: "save failed" rather than silently going back to "saved"
        self.label_error = ""
        self.label_dialog = None
        self.label_table_dialog = None

        # annotations: events read from a CSV and drawn over every lane.
        # The layer is created up front and stays empty until a table is
        # loaded, so nothing downstream has to test for its existence.
        self.annotations = AnnotationLayer(self)
        self.annotations.sigTableChanged.connect(self.rebuild_annotations)
        self.annotations.sigVisibilityChanged.connect(self.redraw_annotations)
        self.annotations.sigVisibilityChanged.connect(self.schedule_annotation_save)
        #: path given on the command line; None means "look for one"
        self.events_path = events_path
        self.annotation_overlays = []
        self.annotation_group = None
        self.annotation_sourcew = None
        self.annotation_badgew = None
        #: `SplitCoverage` of the loaded bundle against the files the loader
        #: actually opened, or None while nothing is loaded.  It is what the
        #: badge says when only part of a split recording is open
        #: (`check_recording_coverage`).
        self.annotation_coverage = None
        #: one container per row of ANNOTATION_CHIP_ROWS, each an HBox the
        #: chips are inserted into ahead of a trailing stretch
        self.annotation_rowboxes = []
        self.annotation_chips = []
        #: the way back from a solo, beside the first chip
        self.annotation_allw = None
        #: a settings write is queued for the end of this turn of the loop
        self.annotation_save_pending = False
        #: layer id -> its chip, so a solo driven from a key or the menu can
        #: put the bar back in step without rebuilding it
        self.annotation_layer_chips = {}
        #: the parameter bar's groups, kept so the bar can be re-equalised
        #: when the annotation chips change the height of their group
        self.param_groups = []
        #: the tab strip over those groups; None until `setup_parameter_bar`
        self.param_tabs = None
        #: the tab name last written to the settings file, so a restore and
        #: a rebuild do not rewrite it
        self._param_tab_saved = ""
        self.annotation_showw = None
        self.annotation_surfacew = {}
        self.annotation_hoverw = None
        #: the layer set that was showing before the current solo, so the
        #: second click on a soloed chip gives it back.  None means "no solo
        #: is in progress", which is the state every other way of changing
        #: the switches puts this back into.
        self.annotation_layers_before_solo = None
        #: one vertical rule per join of a split recording, per lane and per
        #: navigator row.  Positions come from the LOADER, never from a
        #: bundle -- see `attach_join_markers`.
        self.join_markers = []
        #: ``(channel, join index, InfLineLabel)`` for the trace-lane rules;
        #: only the current channel's labels are shown, so three joins do not
        #: print forty-eight times in a sixteen lane stack
        self.join_labels = []
        #: the control track's optional panel; it is not an overlay, so it is
        #: not in `annotation_overlays` and never gets an EventOverlay
        self.control_panel = None

        # plots:
        self.color_map = self.read_color_map_setting()
        self.figs = []  # all GraphicsLayoutWidgets - one for each channel
        self.borders = []
        self.splitters = []  # every trace/spectrogram grab band
        # the stack's one shared time axis, in a row of its own below the
        # last lane, and the amplitude scale of the selected lane:
        self.taxis = None
        self.taxis_fig = None
        self.taxis_strip = None
        self.taxis_margins = None
        self.y_readout = None
        self.lane_height = theme.CHANNEL_MIN_HEIGHT
        #: One deferred scroll per turn of the event loop, however many
        #: times the lanes were resized in it - see `show_focused_lane`.
        self.scroll_focus_pending = False
        self.stack_pane = None
        self.stack_spacer_row = 0
        self.sig_proxies = []
        # nested lists (channel, panel):
        self.axs = []  # all plots
        self.axgs = []  # plots with grids
        # full traces:
        self.datafig = None
        # colors and fonts are owned by theme.apply()

    def __del__(self):
        self.close()

    @staticmethod
    def read_color_map_setting() -> int:
        """Spectrogram colormap index as stored by a previous session."""
        settings = QSettings("audian", "audian")
        try:
            index = int(
                settings.value("spectrogram/colormap", theme.DEFAULT_SPECTROGRAM_MAP)
            )
        except (TypeError, ValueError):
            index = theme.DEFAULT_SPECTROGRAM_MAP
        if index < 0 or index >= len(theme.spectrogram_maps()):
            index = theme.DEFAULT_SPECTROGRAM_MAP
        return index

    @contextmanager
    def updating(self):
        """Guard a block that programmatically changes plot ranges.

        `set_times()`, `set_ranges()` and `update_ranges()` bail out while
        `self.setting` is set, so that a range we set ourselves does not
        bounce back through `sigRangeChanged`. The flag *must* be cleared
        again on every path, including early returns and exceptions -
        leaking it silently freezes scrolling and zooming for the rest of
        the session.
        """
        self.setting = True
        try:
            yield
        finally:
            self.setting = False

    def set_readout(self, field: str, text=None, active: bool = True) -> None:
        """Hand a crosshair readout to the main window, if it has a place.

        The readouts used to be QActions on the bottom tool bar that were
        shown and hidden while the mouse moved, reflowing the whole bar
        under the cursor.  `active=False` greys the value instead of
        clearing it, which is how ambient values (the current y range) sit
        next to live ones (the pointer position) without the bar reflowing.
        """
        window = self.window()
        if window is not None and hasattr(window, "set_readout"):
            window.set_readout(field, text, active)

    def notify(self, level: str, message: str) -> None:
        """Report to the main window if it knows how, else to stderr."""
        window = self.window()
        if window is not None and hasattr(window, "notify"):
            window.notify(level, message)
        else:
            print(f"{level}: {message}")

    def name(self):
        if self.data.data is not None:
            return self.data.data.basename()
        else:
            if isinstance(self.data.file_path, (list, tuple, np.ndarray)):
                return Path(self.data.file_path[0]).stem
            else:
                return Path(self.data.file_path).stem

    def get_trace(self, name):
        return self.data[name]

    def add_trace(self, trace):
        self.data.add_trace(trace)

    def remove_trace(self, name):
        self.data.remove_trace(name)

    def clear_traces(self):
        self.data.clear_traces()

    def get_analyzer(self, name):
        for a in self.analyzers:
            if name.lower() == a.name.lower():
                return a
        return None

    def add_analyzer(self, analyzer):
        self.analyzers.append(analyzer)

    def remove_analyzer(self, name):
        for k, a in enumerate(self.analyzers):
            if name.lower() == a.name.lower():
                del self.analyzers[k]

    def clear_analyzer(self):
        self.analyzers = []

    def add_to_panel_trace(self, trace_name, channel, plot_item):
        panel_name = self.data[trace_name].panel
        self.panels[panel_name].add_item(plot_item, channel, False)

    def toggle_trace(self, checked, name):
        self.data.set_visible(name, checked)
        self.adjust_layout(self.width(), self.height())
        self.sigTraceChanged.emit(self, checked, name)

    def set_trace(self, checked, name):
        self.data.set_visible(name, checked)
        for act in self.trace_acts:
            if act.text() == name:
                act.blockSignals(True)
                act.setChecked(checked)
                act.blockSignals(False)

    def open(self, gui, unwrap, unwrap_clip, highpass_cutoff, lowpass_cutoff):
        # Keep the main window: the status bar readouts, notifications and
        # the progress slot all live on it, and a browser is not always the
        # child of the window it belongs to while tabs are being moved.
        self.gui = gui
        # load data:
        self.data.open(unwrap, unwrap_clip)
        if self.data.data is None:
            return
        # What the loader noticed about the joins between files but did not act
        # on.  It reaches the status bar rather than stdout, because the whole
        # failure mode here is information nobody sees: a split recording that
        # is quietly missing its last file looks completely normal.
        for message in getattr(self.data, "load_warnings", []):
            self.notify("warning", message)

        # add traces to menu:
        self.trace_acts = []
        for t in self.data.traces:
            act = QAction(t.name, self)
            act.setCheckable(True)
            act.setChecked(True)
            act.toggled.connect(lambda x, name=t.name: self.toggle_trace(x, name))
            self.trace_acts.append(act)
        # add spectrogram selection to menu:
        self.spec_acts = []
        for spec in self.data.get_trace_names(BufferedSpectrogram):
            act = QAction(spec, self)
            act.setCheckable(True)
            act.setChecked(False)
            act.toggled.connect(lambda x, name=spec: self.set_spectrogram(x, name))
            self.spec_acts.append(act)

        # ranges:
        self.plot_ranges.setup(self.data.channels)

        # requested filtering:
        if "filtered" in self.data:
            filtered = self.data["filtered"]
            filter_changed = False
            if highpass_cutoff is not None:
                filtered.highpass_cutoff = highpass_cutoff
                filter_changed = True
            if lowpass_cutoff is not None:
                filtered.lowpass_cutoff = lowpass_cutoff
                filter_changed = True
            if filter_changed:
                filtered.update()

        # setup channel selection:
        if self.show_channels is None:
            if len(self.schannels) == 0:
                self.show_channels = list(range(self.data.channels))
            else:
                self.show_channels = [
                    c for c in self.schannels if c < self.data.channels
                ]
        else:
            self.show_channels = [
                c for c in self.show_channels if c < self.data.channels
            ]
        if len(self.show_channels) == 0:
            self.show_channels = [0]

        self.current_channel = self.show_channels[0]
        self.selected_channels = list(range(self.data.channels))
        self.channel_order = list(range(self.data.channels))
        for c in range(self.data.channels):
            # empty by default - the rail badge already shows the index:
            self.channel_names.setdefault(c, "")

        # make panels:
        self.panels.fill(self.data)
        self.panels.insert_spacers()

        # setup plots:
        self.figs = []  # all GraphicsLayoutWidgets - one for each channel
        self.borders = []
        self.sig_proxies = []
        # nested lists (channel, panel):
        self.axs = []  # all plots
        self.axgs = []  # plots with grids
        self.audio_markers = []  # vertical line showing position while playing
        # font size:
        xwidth = self.fontMetrics().averageCharWidth()
        self.setup_stack()
        for c in range(self.data.channels):
            self.axs.append([])
            self.axgs.append([])
            self.audio_markers.append([])

            # one figure per channel:
            fig = pg.GraphicsLayoutWidget()
            theme.style_channel_figure(fig)
            fig.setMinimumHeight(theme.CHANNEL_DENSE_HEIGHT)
            fig.setVisible(c in self.show_channels)

            rail_row = ChannelRailRow(c, self)
            self.rail_rows.append(rail_row)
            self.stack_grid.addWidget(rail_row, c, 0)
            self.stack_grid.addWidget(fig, c, 1)
            self.figs.append(fig)

            # border:
            border = QGraphicsRectItem()
            # ON TOP, not behind.  At z=-1000 the plots painted over it and
            # only the parts they did not cover showed through -- the outer
            # margins -- so the frame read as an open bracket: top and sides
            # but no bottom edge.  It carries no brush, so on top it costs
            # exactly the 1 px outline it is meant to be.
            border.setZValue(1000)
            border.setPen(theme.border_pen(selected=True))
            # and it must never swallow a click meant for the plot beneath
            border.setAcceptedMouseButtons(Qt.NoButton)
            border.setAcceptHoverEvents(False)
            fig.scene().addItem(border)
            fig.sigDeviceRangeChanged.connect(self.update_borders)
            self.borders.append(border)

            # setup plot panels:
            row = 0
            for name in reversed(self.panels):
                panel = self.panels[name]
                # spacer:
                if panel.is_spacer():
                    # The spacer row was an empty GraphicsLayout of zero
                    # height.  It is the boundary between two panels, so it
                    # is where the control that moves that boundary belongs;
                    # `adjust_layout` gives the row a height only while there
                    # are two panels to divide, so an unused splitter costs
                    # one hidden, unpainted item and no layout at all.
                    axsp = PanelSplitter(c, self)
                    self.splitters.append(axsp)
                    fig.addItem(axsp, row=row, col=0)
                    panel.add_ax(row, axsp)
                # trace plot:
                elif panel.is_trace():
                    ylabel = panel.name if panel.name != "trace" else ""
                    axt = TimePlot(panel.ax_spec, c, self, xwidth, ylabel)
                    self.audio_markers[-1].append(axt.vmarker)
                    fig.addItem(axt, row=row, col=0)
                    # polish only once the item is in a scene:
                    axt.polish()
                    if hasattr(axt, "sigHoverValue"):
                        axt.sigHoverValue.connect(self.show_hover_value)
                    # the view box resizes when the y gutter is reclaimed or
                    # given back, which is exactly when the shared time axis
                    # below the stack has to be re-aligned - and it is the
                    # only signal that fires *after* that relayout:
                    axt.getViewBox().sigResized.connect(
                        lambda *a: self.align_time_axis()
                    )
                    self.axgs[-1].append(axt)
                    self.axs[-1].append(axt)
                    panel.add_ax(row, axt)
                    panel.add_traces(c, self.data)
                    self.plot_ranges.add_plot(axt)
                # spectrogram:
                elif panel.is_spectrogram():
                    axs = SpectrogramPlot(
                        panel.ax_spec,
                        c,
                        self,
                        xwidth,
                        theme.spectrogram_maps()[self.color_map],
                        self.show_cbars,
                        self.show_powers,
                    )
                    self.audio_markers[-1].append(axs.vmarker)
                    panel.add_ax(row, axs, axs.cbar)
                    panel.add_traces(c, self.data)
                    self.panels.add_power_ax(panel.name, row, axs.powerax)
                    self.plot_ranges.add_plot(axs)
                    self.plot_ranges.add_plot(axs.powerax)
                    fig.addItem(axs, row=row, col=0)
                    fig.addItem(axs.powerax, row=row, col=1)
                    fig.addItem(axs.cbar, row=row, col=2)
                    # polish only once the item is in a scene:
                    axs.polish()
                    self.axgs[-1].append(axs)
                    self.axs[-1].append(axs)
                # power:
                elif panel.is_power():
                    # was already set up with spectrogram
                    continue

                row += 1

            proxy = pg.SignalProxy(
                fig.scene().sigMouseMoved,
                rateLimit=60,
                slot=lambda x, c=c: self.mouse_moved(x, c),
            )
            self.sig_proxies.append(proxy)
            proxy = pg.SignalProxy(
                fig.scene().sigMouseClicked,
                rateLimit=60,
                slot=lambda x, c=c: self.mouse_clicked(x, c),
            )
            self.sig_proxies.append(proxy)

        with self.updating():
            self.plot_ranges.set_limits()
            self.plot_ranges.set_ranges()
            self.disable_unused_range_actions()
        self.auto_fit_y()
        self.data.set_need_update()
        self.set_times()

        # Transport, zoom, amplitude policy and channel selection all live
        # on the application tool bar (audian.Audian.setup_toolbar).  This
        # browser contributes only the parameter bar, so there is exactly
        # one control for every setting instead of two.
        #
        # The per-channel toggle actions still have to be built here - the
        # application tool bar's channel menu and the Alt+N shortcuts read
        # them - they are just no longer shown a second time.
        for c in range(max(self.data.channels, len(self.acts.channels))):
            gui.set_channel_action(
                c, self.data.channels, c in self.show_channels, gui.browser() is self
            )

        if self.spectrogram:
            self.spectrogram_power = self.panels[self.data[self.spectrogram].panel].z()
        self.setup_parameter_bar()
        self.vbox.addWidget(self.parambar)

        # full data (navigator), docked below the channel stack:
        self.datafig = FullTracePlot(
            self.data, self.panels["trace"].axs, theme.AXIS_LEFT_WIDTH
        )
        self.datafig.polish()
        self.splitter.addWidget(self.datafig)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 0)
        self.splitter.setCollapsible(0, False)

        # after the navigator exists: its rows are one of the three surfaces
        # an annotation is drawn on, so there is one attach pass, not two
        self.attach_annotation_overlays()
        # the labels: overlays first, then the sidecar, so the read that
        # bumps the revision has something to repaint
        self.attach_label_overlays()
        self.load_labels()
        self.update_label_status()
        # the joins are the loader's own knowledge and need no bundle, so
        # they are drawn as soon as there are plots to draw them on
        self.attach_join_markers()

        if hasattr(self.datafig, "sigHoverTime"):
            self.datafig.sigHoverTime.connect(self.show_navigator_time)
            # the status bar now carries the readout, so the in-scene
            # overlay would only duplicate it:
            if getattr(self.datafig, "time_info", None) is not None:
                self.datafig.time_info.setVisible(False)
                self.datafig.overlay_enabled = False

        # pyqtgraph leaves one unreachable, parentless control form per
        # PlotItem behind; sweep them onto the hidden holder now that every
        # plot exists.  One scan, not one per plot.
        theme.collect_orphan_widgets()

        self.setEnabled(True)
        self.adjust_layout(self.width(), self.height())

        # setup analyzers:
        PlainAnalyzer(self)
        StatisticsAnalyzer(self)
        self.plugins.setup_analyzer(self)
        if len(self.analyzers) == 0:
            self.acts.analyze_region.setEnabled(False)
            self.acts.analyze_region.setVisible(False)

        # update visibility of traces:
        for name in self.data.keys():
            for act in self.trace_acts:
                if act.text() == name:
                    act.blockSignals(True)
                    act.setChecked(self.data.is_visible(name))
                    act.blockSignals(False)

        # fulltrace data:
        self.datafig.prepare()
        self.overview_timer.start(250)

    def disable_unused_range_actions(self) -> None:
        """Hide zoom actions for axes that no panel actually uses."""
        if not self.plot_ranges[Panel.amplitudes[0]].is_used():
            self.acts.zoom_xamplitude_in.setEnabled(False)
            self.acts.zoom_xamplitude_out.setEnabled(False)
            self.acts.zoom_xamplitude_in.setVisible(False)
            self.acts.zoom_xamplitude_out.setVisible(False)
        if not self.plot_ranges[Panel.amplitudes[1]].is_used():
            self.acts.zoom_yamplitude_in.setEnabled(False)
            self.acts.zoom_yamplitude_out.setEnabled(False)
            self.acts.zoom_yamplitude_in.setVisible(False)
            self.acts.zoom_yamplitude_out.setVisible(False)
        if not self.plot_ranges[Panel.amplitudes[2]].is_used():
            self.acts.zoom_uamplitude_in.setEnabled(False)
            self.acts.zoom_uamplitude_out.setEnabled(False)
            self.acts.zoom_uamplitude_in.setVisible(False)
            self.acts.zoom_uamplitude_out.setVisible(False)
        if not self.plot_ranges[Panel.frequencies[0]].is_used():
            self.acts.zoom_ffrequency_in.setEnabled(False)
            self.acts.zoom_ffrequency_out.setEnabled(False)
            self.acts.zoom_ffrequency_in.setVisible(False)
            self.acts.zoom_ffrequency_out.setVisible(False)
        if not self.plot_ranges[Panel.frequencies[1]].is_used():
            self.acts.zoom_wfrequency_in.setEnabled(False)
            self.acts.zoom_wfrequency_out.setEnabled(False)
            self.acts.zoom_wfrequency_in.setVisible(False)
            self.acts.zoom_wfrequency_out.setVisible(False)

    def setup_stack(self) -> None:
        """Build the channel stack, its rail and its one shared time axis.

        Rail rows and channel figures share one grid, so that each rail
        row stays aligned with the channel it controls no matter how the
        stack is scrolled or how the row heights are distributed.  The
        time axis sits *below* that grid, outside the scroll area, so that
        it stays on screen when the lanes do not all fit - an axis that
        scrolls away is an axis the reader cannot use.
        """
        self.splitter = QSplitter(Qt.Vertical, self)
        self.splitter.setChildrenCollapsible(False)
        stack_pane = QWidget(self)
        pane = QVBoxLayout(stack_pane)
        pane.setContentsMargins(0, 0, 0, 0)
        pane.setSpacing(0)
        stack_widget = QWidget()
        self.stack_grid = QGridLayout(stack_widget)
        self.stack_grid.setContentsMargins(0, 0, 0, 0)
        self.stack_grid.setHorizontalSpacing(theme.S4)
        self.stack_grid.setVerticalSpacing(0)
        self.stack_grid.setColumnStretch(0, 0)
        self.stack_grid.setColumnStretch(1, 1)
        self.stack_grid.setColumnMinimumWidth(0, DataBrowser.RAIL_WIDTH)
        # a scroll area only works once plain wheel events scroll instead
        # of zooming the view boxes:
        self.scrollable_stack = "wheelEvent" in SelectViewBox.__dict__
        if self.scrollable_stack:
            self.stack_area = QScrollArea(stack_pane)
            self.stack_area.setWidgetResizable(True)
            self.stack_area.setFrameShape(QFrame.NoFrame)
            self.stack_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self.stack_area.setWidget(stack_widget)
            # The rows divide up the *viewport*, and the viewport is still
            # zero-sized while the browser is being built.  Without this
            # the row stretches are computed once from a meaningless
            # height and never revisited, because a QScrollArea's viewport
            # resizing does not resize the browser.
            self.stack_area.viewport().installEventFilter(self)
            pane.addWidget(self.stack_area, 1)
        else:
            self.stack_area = None
            pane.addWidget(stack_widget, 1)
        # one spacer row under the last lane absorbs the pixels the lanes
        # rounded off, so the layout never distributes them itself:
        self.stack_spacer_row = self.data.channels
        self.stack_grid.addItem(
            QSpacerItem(0, 0, QSizePolicy.Minimum, QSizePolicy.Expanding),
            self.stack_spacer_row,
            0,
            1,
            2,
        )
        self.stack_grid.setRowStretch(self.stack_spacer_row, 1)
        self.stack_pane = stack_pane
        self.setup_time_axis()
        # Between the lanes and the shared time axis, and *outside* the scroll
        # area: the control track is one row for the whole session, so it must
        # not scroll away with the channels, and the axis has to stay directly
        # under whatever is bottom-most.
        self.control_panel = ControlPanel(
            self.annotations, DataBrowser.RAIL_WIDTH, stack_pane
        )
        pane.addWidget(self.control_panel, 0)
        pane.addWidget(self.taxis_strip, 0)
        self.splitter.addWidget(stack_pane)
        self.vbox.addWidget(self.splitter)

        rail_act = QAction("Toggle channel rail", self)
        rail_act.setShortcut("F7")
        rail_act.triggered.connect(self.toggle_rail)
        self.addAction(rail_act)

        # Escape drops the label selection.  Bound here rather than in
        # `audian.setup_label_actions` because it belongs in no menu: it is
        # the way out of a state, not a command, and a menu entry reading
        # "Deselect" would be a line in the Editable labels menu that is
        # greyed out almost all of the time.  Nothing else in the
        # application binds Escape -- the cheat sheet handles its own, in a
        # dialog, which keeps it.
        deselect_act = QAction("Deselect the editable label", self)
        deselect_act.setShortcut("Escape")
        deselect_act.setShortcutContext(Qt.WindowShortcut)
        deselect_act.triggered.connect(self.deselect_label)
        self.addAction(deselect_act)

    def setup_time_axis(self) -> None:
        """Build the stack's one time axis, in a row of its own.

        Every lane hides its own time axis, so every lane can be exactly
        the same height.  The bottom lane used to carry the axis for all of
        them and therefore had to be taller than its neighbours by however
        much chrome pyqtgraph needed - on the sixteen channel file that was
        a 63 px step where every other step was 31 to 38 px, and the extra
        height went to the axis rather than to the trace, so the loudest
        channel in the file drew smaller than a quieter one under a shared
        y range.  A wrong reading, not a wrong pixel.
        """
        self.taxis_fig = pg.GraphicsLayoutWidget()
        theme.style_figure(self.taxis_fig)
        # no margins of its own: align_time_axis() sets the left and right
        # ones to whatever the lanes above turn out to use, and a top
        # margin would only push the ticks off the lane they belong to.
        self.taxis_fig.ci.layout.setContentsMargins(0, 0, 0, 0)
        self.taxis = SharedTimeAxis(
            self.data.data.file_start_times(),
            self.data.data.file_paths,
            0,
            orientation="bottom",
            showValues=True,
        )
        self.taxis.set_start_time(self.data.start_time)
        self.taxis.mode_source = self.lane_starttime_mode
        self.taxis_fig.addItem(self.taxis, row=0, col=0)
        self.taxis_fig.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

        # the corner between the rail and the time axis: the one place in
        # the stack that is not a lane, an axis or a control, which is
        # exactly what the shared amplitude scale needs.
        # A spacer, not a readout.  The amplitude range used to be printed
        # here because a dense stack hides the per-lane axis -- but the
        # status bar's own 'A' field already reports the same range
        # persistently, so this was a second copy that also set the floor on
        # how narrow the rail could get.  The corner still has to reserve the
        # rail's width, or the time axis stops lining up with the plots.
        self.y_readout = QWidget()
        self.y_readout.setFixedWidth(DataBrowser.RAIL_WIDTH)

        self.taxis_strip = QWidget(self.stack_pane)
        strip = QHBoxLayout(self.taxis_strip)
        strip.setContentsMargins(0, 0, 0, 0)
        strip.setSpacing(theme.S4)
        strip.addWidget(self.y_readout, 0)
        strip.addWidget(self.taxis_fig, 1)

    def set_starttime_mode(self, mode: int) -> None:
        """Push a start-time mode onto every time axis in this browser.

        The lanes get it through `PlotRanges.set_starttime()` as before; the
        shared axis below the stack is not one of the lane axes, so it is set
        here explicitly rather than left to read the mode back off a lane at
        paint time.
        """
        self.plot_ranges[Panel.times[0]].set_starttime(mode)
        taxis = getattr(self, "taxis", None)
        if taxis is not None:
            taxis.set_starttime_mode(mode)
            taxis.picture = None
            taxis.update()

    def lane_starttime_mode(self) -> Optional[int]:
        """The start-time mode the lanes are currently drawing with.

        `PlotRanges.set_starttime()` pushes the mode onto the per-lane time
        axes, which the shared axis is not one of, so it reads the mode off
        a lane instead of waiting for a call that is never made.
        """
        for c in self.visible_channels():
            plot = self.trace_plot(c)
            if plot is None or "bottom" not in plot.axes:
                continue
            return getattr(plot.getAxis("bottom"), "_starttime_mode", None)
        return None

    def setup_parameter_bar(self) -> None:
        """Build the bottom bar: one tab per group, one group on screen.

        Replaces the row of single-letter labels ('N:', 'O:', ' L:') by
        boxed groups of named rows, each row carrying its own keyboard
        shortcut so that shortcuts are visible instead of hidden in tool
        tips.  Behind tabs the reader sees one group's shortcuts at a time;
        every group's are in its tab's tool tip, and all of them are in the
        cheat sheet and the command palette.

        The groups are stacked rather than laid side by side because side by
        side made the bar's minimum width the SUM of theirs -- 1445 px on a
        four channel recording, and 2456 px once a session bundle loaded ten
        layer chips into the annotations group, which is more than a laptop
        panel has.  See `ParameterTabs`.
        """
        self.parambar = QWidget(self)
        # A chrome band, not canvas: it gets the chrome ground and a rule
        # along its leading edge, so the boundary between the controls and
        # the data above them is stated rather than implied by a gap.
        self.parambar.setObjectName("audianParamBar")
        theme.band(self.parambar, top=True)
        # Fixed, explicitly, all the way down.  `ParameterTabs` pins its own
        # stack host for the reason its docstring gives; pinning the band
        # around it as well means a future row added to this bar cannot make
        # the whole thing elastic without somebody noticing.
        self.parambar.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        grid = QGridLayout(self.parambar)
        grid.setContentsMargins(theme.S8, theme.S8, theme.S8, theme.S6)
        grid.setHorizontalSpacing(theme.S16)
        grid.setVerticalSpacing(0)
        self.param_tabs = ParameterTabs(self.parambar)
        self.param_tabs.sigTabChanged.connect(self.parameter_tab_changed)
        groups = []

        nyquist = self.data.rate / 2

        # filter:
        if "filtered" in self.data:
            filtered = self.data["filtered"]
            group = ParameterGroup("Filter", self.parambar, caption=False)
            min_step = 10 ** floor(log10(0.01 * nyquist))
            self.hpfw = pg.SpinBox(
                self,
                filtered.highpass_cutoff,
                bounds=(0, nyquist),
                suffix="Hz",
                siPrefix=True,
                step=0.5,
                dec=True,
                decimals=3,
                minStep=min_step,
            )
            self.style_parameter_spinbox(self.hpfw)
            self.hpfw.sigValueChanged.connect(
                lambda s: self.update_filter(highpass_cutoff=s.value())
            )
            self.hpsliderw = LogSlider(0, nyquist, self.parambar)
            self.hpsliderw.set_hz(filtered.highpass_cutoff)
            self.hpsliderw.valueChanged.connect(
                lambda v: self.update_filter(highpass_cutoff=self.hpsliderw.value_hz())
            )
            group.add_row(
                "High-pass",
                "H / ⇧H",
                ParameterGroup.expanding(self.hpsliderw),
                self.hpfw,
            )

            self.lpfw = pg.SpinBox(
                self,
                filtered.lowpass_cutoff,
                bounds=(0.01 * nyquist, nyquist),
                suffix="Hz",
                siPrefix=True,
                step=0.5,
                dec=True,
                decimals=3,
                minStep=min_step,
            )
            self.style_parameter_spinbox(self.lpfw)
            self.lpfw.sigValueChanged.connect(
                lambda s: self.update_filter(lowpass_cutoff=s.value())
            )
            self.lpsliderw = LogSlider(0.01 * nyquist, nyquist, self.parambar)
            self.lpsliderw.set_hz(filtered.lowpass_cutoff)
            self.lpsliderw.valueChanged.connect(
                lambda v: self.update_filter(lowpass_cutoff=self.lpsliderw.value_hz())
            )
            group.add_row(
                "Low-pass",
                "L / ⇧L",
                ParameterGroup.expanding(self.lpsliderw),
                self.lpfw,
            )

            self.linkbandw = QToolButton(self.parambar)
            self.linkbandw.setText("Linked band")
            self.linkbandw.setCheckable(True)
            self.linkbandw.setFont(theme.font_ui(theme.SIZE_SMALL_PT))
            self.linkbandw.setToolTip(
                "Move both cutoffs together, keeping the band width"
            )
            self.linkbandw.toggled.connect(self.set_link_band)
            group.add_row("Band", "", self.linkbandw)
            groups.append(group)
        else:
            self.hpfw = None
            self.lpfw = None
            self.hpsliderw = None
            self.lpsliderw = None
            self.linkbandw = None
            self.acts.link_filter.setEnabled(False)
            self.acts.highpass_up.setEnabled(False)
            self.acts.highpass_down.setEnabled(False)
            self.acts.lowpass_up.setEnabled(False)
            self.acts.lowpass_down.setEnabled(False)

        # spectrogram:
        if "spectrogram" in self.data:
            spectrogram = self.data["spectrogram"]
            group = ParameterGroup("Spectrogram", self.parambar, caption=False)
            self.nfftw = QComboBox(self)
            self.nfftw.tooltip = "Number of samples of a Fourier window"
            self.nfftw.setToolTip(self.nfftw.tooltip)
            self.nfftw.setFont(theme.font_mono(theme.SIZE_SMALL_PT))
            for i in range(3, 20):
                nfft = 2**i
                self.nfftw.addItem(self.nfft_label(nfft), nfft)
            self.nfftw.setEditable(False)
            self.set_nfft_widget(spectrogram.nfft)
            self.nfftw.currentIndexChanged.connect(
                lambda i: self.set_resolution(nfft=self.nfftw.itemData(i))
            )
            group.add_row("Window", "R / ⇧R", self.nfftw)

            self.ofracw = QSlider(Qt.Horizontal, self.parambar)
            self.ofracw.tooltip = "Overlap of Fourier windows"
            self.ofracw.setToolTip(self.ofracw.tooltip)
            self.ofracw.setRange(0, 99)
            self.ofracw.setTickPosition(QSlider.TicksBelow)
            self.ofracw.setTickInterval(25)
            self.ofracw.setValue(int(round(100 * spectrogram.overlap_frac)))
            self.ofracw.valueChanged.connect(
                lambda v: self.set_resolution(overlap_frac=0.01 * v)
            )
            self.ofraclabelw = QLabel()
            self.ofraclabelw.setFont(theme.font_mono(theme.SIZE_SMALL_PT))
            group.add_row(
                "Overlap",
                "O / ⇧O",
                ParameterGroup.expanding(self.ofracw),
                self.ofraclabelw,
            )

            self.cmapw = ColorMapCombo(self.parambar)
            self.cmapw.setCurrentIndex(self.color_map)
            self.cmapw.currentIndexChanged.connect(lambda i: self.set_color_map(i))
            group.add_row("Colormap", "⇧C", self.cmapw)
            groups.append(group)
        else:
            self.nfftw = None
            self.ofracw = None
            self.ofraclabelw = None
            self.cmapw = None

        # envelope:
        if "envelope" in self.data:
            envelope = self.data["envelope"]
            group = ParameterGroup("Envelope", self.parambar, caption=False)
            self.envfw = pg.SpinBox(
                self,
                envelope.envelope_cutoff,
                bounds=(0, 0.5 * nyquist),
                suffix="Hz",
                siPrefix=True,
                step=0.5,
                dec=True,
                decimals=3,
                minStep=10 ** floor(log10(0.00001 * nyquist)),
            )
            self.style_parameter_spinbox(self.envfw)
            self.envfw.sigValueChanged.connect(
                lambda s: self.update_envelope(envelope_cutoff=s.value())
            )
            self.envsliderw = LogSlider(0, 0.5 * nyquist, self.parambar)
            self.envsliderw.set_hz(envelope.envelope_cutoff)
            self.envsliderw.valueChanged.connect(
                lambda v: self.update_envelope(
                    envelope_cutoff=self.envsliderw.value_hz()
                )
            )
            group.add_row(
                "Cutoff",
                "E / ⇧E",
                ParameterGroup.expanding(self.envsliderw),
                self.envfw,
            )
            groups.append(group)
        else:
            self.envfw = None
            self.envsliderw = None
            self.acts.link_envelope.setEnabled(False)
            self.acts.show_envelope.setEnabled(False)
            self.acts.envelope_up.setEnabled(False)
            self.acts.envelope_down.setEnabled(False)

        # audio playback:
        group = ParameterGroup("Audio", self.parambar, caption=False)
        self.audiosrcw = QComboBox(self.parambar)
        self.audiosrcw.setToolTip(
            "What playback sends to the speakers: the current channel on its "
            "own, or every shown channel mixed down to stereo"
        )
        self.audiosrcw.addItems(list(DataBrowser.AUDIO_SOURCE_LABELS))
        self.audiosrcw.setEditable(False)
        self.audiosrcw.setFont(theme.font_ui(theme.SIZE_SMALL_PT))
        self.audiosrcw.setCurrentIndex(
            DataBrowser.AUDIO_SOURCES.index(self.audio_source)
        )
        self.audiosrcw.currentIndexChanged.connect(
            lambda i: self.set_audio_source(DataBrowser.AUDIO_SOURCES[i])
        )
        group.add_row("Source", "⇧P", self.audiosrcw)

        # the explicit stereo pair, revealed only when it is the source
        # a plain QWidget paints no background unless it is told to, which
        # is what we want here: the row sits on the parameter band's ground
        self.audiopairw = QWidget(self.parambar)
        pair = QHBoxLayout(self.audiopairw)
        pair.setContentsMargins(0, 0, 0, 0)
        pair.setSpacing(theme.S6)
        channels = [f"ch {c:02d}" for c in range(self.data.channels)]
        for side, tip in (
            ("left", "Channel sent to the LEFT ear"),
            ("right", "Channel sent to the RIGHT ear"),
        ):
            label = QLabel("L" if side == "left" else "R", self.audiopairw)
            label.setFont(theme.font_mono(theme.SIZE_SMALL_PT, bold=True))
            theme.tint(label, "fg.muted")
            combo = QComboBox(self.audiopairw)
            combo.addItems(channels)
            combo.setEditable(False)
            combo.setFont(theme.font_mono(theme.SIZE_SMALL_PT))
            combo.setToolTip(tip)
            combo.setCurrentIndex(
                self.audio_left if side == "left" else self.audio_right
            )
            combo.currentIndexChanged.connect(
                (lambda i: self.set_audio_pair(left=i))
                if side == "left"
                else (lambda i: self.set_audio_pair(right=i))
            )
            pair.addWidget(label)
            pair.addWidget(combo, 1)
            if side == "left":
                self.audioleftw = combo
            else:
                self.audiorightw = combo
        self.audiopairrow = group.add_row("Pair", "", self.audiopairw)
        self.set_pair_row_visible(self.audio_source == DataBrowser.AUDIO_PAIR)
        self.audiofacw = QComboBox(self.parambar)
        self.audiofacw.setToolTip("Audio time expansion factor")
        self.audiofacw.addItems(
            ["0.1", "0.2", "0.5", "1", "2", "5", "10", "20", "50", "100"]
        )
        self.audiofacw.setEditable(False)
        self.audiofacw.setFont(theme.font_mono(theme.SIZE_SMALL_PT))
        self.audiofacw.setCurrentText(f"{self.audio_rate_fac:g}")
        self.audiofacw.currentTextChanged.connect(
            lambda s: self.set_audio(rate_fac=float(s))
        )
        group.add_row("Speed", "", self.audiofacw)
        self.audiohetfw = pg.SpinBox(
            self.parambar,
            self.audio_heterodyne_freq,
            bounds=(10000, 100000),
            suffix="Hz",
            siPrefix=True,
            step=0.1,
            dec=True,
            decimals=3,
            minStep=5000,
        )
        self.audiohetfw.setToolTip("Audio heterodyne frequency")
        self.style_parameter_spinbox(self.audiohetfw)
        self.audiohetfw.sigValueChanged.connect(
            lambda s: self.set_audio(heterodyne_freq=s.value())
        )
        if self.data.rate > 50000:
            self.hetbuttonw = QToolButton(self.parambar)
            self.hetbuttonw.setDefaultAction(self.acts.use_heterodyne)
            self.hetbuttonw.setToolButtonStyle(Qt.ToolButtonTextOnly)
            group.add_row("Heterodyne", "", self.audiohetfw, self.hetbuttonw)
        else:
            self.audiohetfw.setVisible(False)
        groups.append(group)

        # annotations, then the labels: read-only first, writable second, in
        # the reading order of the bar
        groups.append(self.setup_annotation_group())
        groups.append(self.setup_label_group())

        # One band, one group at a time.  `equalize` is still what packs a
        # short group's rows at the top of a frame the stack stretches to the
        # full height -- but it is no longer what keeps the bar's height
        # constant across a tab change.  QStackedLayout is: it reports the
        # max over ALL its pages, hidden ones included.
        for group in groups:
            group.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
            self.param_tabs.add(group)
        grid.addWidget(self.param_tabs, 0, 0)
        grid.setColumnStretch(0, 1)
        self.param_groups = groups
        ParameterGroup.equalize(groups)
        self.restore_parameter_tab()
        if "spectrogram" in self.data:
            self.set_resolution(dispatch=False)

    def style_parameter_spinbox(self, spin: pg.SpinBox) -> None:
        """Theme a parameter-bar spin box and drop its stepper stubs.

        The style sheet gives the up/down buttons a frame but no arrow
        glyphs, so every numeric field ended in a pair of empty boxes that
        look like a control and do nothing legible.  The value is set by
        typing, by dragging, by the slider beside it and by a labelled
        keyboard shortcut; the arrow keys still step it.  Nothing is lost
        by removing a button with no glyph in it.
        """
        theme.style_spinbox(spin)
        spin.setButtonSymbols(QAbstractSpinBox.NoButtons)

    def nfft_label(self, nfft: int) -> str:
        """Label a Fourier window by its length *and* its duration."""
        duration = nfft / self.data.rate
        if duration >= 1:
            return f"{nfft} ({duration:.3g} s)"
        return f"{nfft} ({1000 * duration:.3g} ms)"

    def set_nfft_widget(self, nfft: int) -> None:
        index = self.nfftw.findData(nfft)
        if index < 0:
            return
        blocked = self.nfftw.blockSignals(True)
        self.nfftw.setCurrentIndex(index)
        self.nfftw.blockSignals(blocked)

    def set_link_band(self, checked: bool) -> None:
        self.link_band = checked

    def apply_theme(self) -> None:
        """Re-apply the theme to every plot item of this browser.

        Single entry point for live restyling: theme.set_theme() changes
        the token table, this walks the object graph and repaints.
        """
        for figure in self.figs:
            theme.style_channel_figure(figure)
        # the shared time axis lives in a figure of its own, outside the
        # scroll area, and so is not in self.figs
        if self.taxis_fig is not None:
            theme.style_figure(self.taxis_fig)
        if getattr(self, "taxis", None) is not None and hasattr(
            self.taxis, "apply_theme"
        ):
            self.taxis.apply_theme()
            self.align_time_axis()
        for axs in self.axs:
            for ax in axs:
                if hasattr(ax, "apply_theme"):
                    ax.apply_theme()
                elif hasattr(ax, "polish"):
                    ax.polish()
        for border in self.borders:
            border.setPen(theme.border_pen(selected=True))
        for splitter in self.splitters:
            splitter.polish()
        if self.datafig is not None:
            if hasattr(self.datafig, "apply_theme"):
                self.datafig.apply_theme()
            else:
                self.datafig.polish()
        theme.restyle_tree(self)
        for row in self.rail_rows:
            row.update_state()
        # the colormap is cached per theme and oriented to the page -- the
        # noise floor is the dark end under the dark theme and the light end
        # under the daylight one -- so it has to be re-pushed, or the
        # spectrogram stays a dark slab in a white window.
        if self.cmapw is not None:
            self.cmapw.populate()
        self.set_color_map(self.color_map, dispatch=False)
        # annotation pens carry a resolved colour, and the chip icons are
        # baked pixmaps, so both have to be drawn again rather than restyled
        for overlay in self.annotation_overlays:
            overlay.polish()
        self.polish_join_markers()
        if self.control_panel is not None:
            self.control_panel.polish()
        self.build_annotation_chips()
        self.update_annotation_badge()
        self.redraw_annotations()
        # the same for the labels: their pens carry a resolved
        # `theme.marker_color` and the chip swatches are baked pixmaps
        for overlay in self.label_overlays:
            overlay.polish()
        if self.param_tabs is not None:
            self.param_tabs.polish()
        self.build_category_chips()
        self.update_label_status()
        # style_plotitem() has just reset every view box to bg.plot:
        self.update_current_plot()

    # --- channel rail -----------------------------------------------------

    def rail_shown(self) -> bool:
        """Is the channel rail on screen right now?

        `rail_visible` is the reader's F7 setting and is never touched by
        anything else, so that leaving a mode gives the rail back exactly as
        they left it.  The mean spectrogram is the one mode that overrides
        it: the rail is a column of per-channel controls beside per-channel
        lanes, and mean mode has one lane that is no channel at all.  A rail
        row reading `00` with its own solo and mute buttons, pinned next to
        a panel captioned `MEAN 00-15`, names the wrong thing twice over.
        """
        return self.rail_visible and not self.mean_spec

    def apply_rail_width(self) -> None:
        """Give the rail column, the Y readout and the control panel one width.

        The three columns that have to line up -- the lanes, the time axis
        and the control panel -- are told to change width here, and
        `align_time_axis` measures the finished geometry.  Run now it reads
        one of them before Qt has moved it and lands 48 px out; the axis
        then stays wrong until something else resizes the window.  One turn
        of the event loop later every width is real.
        """
        width = DataBrowser.RAIL_WIDTH if self.rail_shown() else 0
        if self.stack_grid is None or self.stack_grid.columnMinimumWidth(0) == width:
            # nothing to move, and nothing to re-align: this runs from
            # `set_panels`, which every panel toggle goes through
            return
        self.stack_grid.setColumnMinimumWidth(0, width)
        if self.y_readout is not None:
            self.y_readout.setFixedWidth(width)
        if self.control_panel is not None:
            self.control_panel.set_rail_width(width)
        self.schedule_axis_alignment()

    def toggle_rail(self) -> None:
        """Show or hide the channel rail (F7).

        The setting is flipped even while the mean spectrogram is holding
        the rail off screen -- it is the reader's preference for when they
        come back, not a description of what is drawn -- but a key that
        changes nothing visible has to say so, or the next F7 appears to be
        the one that did nothing.
        """
        self.rail_visible = not self.rail_visible
        for row in self.rail_rows:
            row.setVisible(self.rail_shown() and row.channel in self.visible_channels())
        self.apply_rail_width()
        if self.mean_spec:
            state = "shown" if self.rail_visible else "hidden"
            self.notify(
                "info",
                f"channel rail {state} -- it stays off screen while the mean "
                f"spectrogram does (Shift+F2)",
            )

    # --- which channels are on screen -------------------------------------

    def selected_channels_in_order(self) -> list:
        """Channels the reader has asked to see, in display order.

        `show_channels` stays the user's channel selection; solo and mute
        are non-destructive filters on top of it, and maximising a channel
        temporarily hides the others.

        This is the set *before* the mean spectrogram collapses it onto one
        lane, which is why it is separate from `visible_channels`: the mean
        needs to know which electrodes it is averaging, and the layout needs
        to know that only one lane is drawn.  Solo and mute are therefore
        the mean's channel selector as well as the stack's -- an ordinary
        thing to want on an electrode grid, and cheap to allow because the
        mean's level does not jump when the count changes the way a sum's
        would.
        """
        if self.show_channels is None:
            return []
        order = [c for c in self.channel_order if c in self.show_channels]
        # channels added without a place in the order go last:
        order += [c for c in self.show_channels if c not in order]
        if self.maximized_channel is not None and self.maximized_channel in order:
            return [self.maximized_channel]
        solo = [c for c in order if c in self.solo_channels]
        if solo:
            return solo
        visible = [c for c in order if c not in self.muted_channels]
        return visible if visible else order

    def visible_channels(self) -> list:
        """Lanes actually drawn, in display order.

        One lane while the mean spectrogram is showing, and that lane is the
        first selected channel's.  Deliberately an existing lane rather than
        a seventeenth pseudo-channel: the stack, the rail, the borders,
        selection and playback are all indexed over `range(data.channels)`,
        and a mean that is not a channel has no business in any of them.
        """
        channels = self.selected_channels_in_order()
        if self.mean_spec and channels:
            return channels[:1]
        return channels

    def mean_channels(self) -> list:
        """Channels the mean spectrogram averages, or [] when it is off."""
        return self.selected_channels_in_order() if self.mean_spec else []

    def mean_spec_lane(self):
        """The lane the mean is drawn on, or None when the mean is off."""
        channels = self.visible_channels()
        return channels[0] if (self.mean_spec and channels) else None

    def focus_channel(self, channel: int) -> None:
        """Point the focus at `channel`, and let the layout follow it.

        `spectrogram_channels` picks its one lane off `current_channel`, so
        on a stack that has collapsed the spectrogram onto the focused lane
        the focus and the layout are the same fact.  Four gestures moved the
        first without the second - the arrow keys and their shifted
        selecting twins, all four of which stopped at `update_borders` - and
        the spectrogram stayed behind on the lane the reader had just left:
        sixteen channels, one press of the down arrow, and the stack this
        application exists for had a spectrogram nowhere at all.

        The relayout is spent only when the set of lanes that get a
        spectrogram actually changes, which on a stack that draws every
        spectrogram is never.  That is not premature: a full `adjust_layout`
        on sixteen lanes is 7.4 ms of Python measured offscreen at 1200x900,
        which is nothing once per keystroke and more than a 60 Hz frame per
        mouse move - the reason `mouse_clicked` guards its call to
        `rail_clicked` at all.  `update_stretches` still runs either way,
        because the rail row of the focused channel expands and that is a
        lane height like any other.
        """
        channels = self.visible_channels()
        before = self.spectrogram_channels(channels)
        self.current_channel = channel
        if self.spectrogram_channels(channels) != before:
            self.adjust_layout(self.width(), self.height())
        else:
            self.update_stretches(self.height())

    def show_focused_lane(self) -> None:
        """Scroll the focused lane into view, on a stack that scrolls.

        A no-op whenever the whole stack fits, which is most of the time.
        It matters where the lanes outgrow the viewport: sixteen
        spectrograms with the traces off are 1952 px in a 500 px viewport,
        so pressing F2 while the focus is on channel 12 left the reader
        looking at channels 0 to 3 and the lane they had selected off the
        bottom of the screen.  The stack already marks the focused lane
        three ways -- a frame, a bold caption, a rule down its rail row -- and
        all three are useless where they cannot be seen.

        Scrolls the least it can, so a lane already on screen does not move
        the view at all.

        It measures a geometry the caller has only just asked for, and the
        two lines that deal with that do different jobs.  Measured on F2
        with the focus on channel 12, sixteen channels at 1200x900, all four
        ways round:

            deferred + activated   lane 1464..1586, view 1086..1586   on screen
            inline   + activated   lane 1464..1586, view 1086..1586   on screen
            deferred, not activated                 view  132..632    off screen
            inline,   not activated                 view   74..574    off screen

        So *activating* is what makes it right: without it the lane position
        read here is the one the stack had before the panels changed, and
        the bar is left wherever it already was.  Deferring is what makes it
        cheap.  Run inline, the activation is unavoidable because the layout
        is still mid-flight every time, and one press of the down arrow goes
        from 13.7 ms to 63.7 ms - held down, the difference between stepping
        through the array and watching it crawl.  A turn of the event loop
        later Qt has usually done the work anyway and the guard skips it;
        only a gesture that changes the stack's total height, which F2 does
        and a focus move does not, still has to pay.

        The height is `setFixedHeight`'s minimum rather than `geometry()`,
        which is a frame behind even so, and the arithmetic is here rather
        than in `QScrollArea.ensureWidgetVisible` because that one reads the
        same stale geometry and scrolls the lane's top edge to the bottom of
        the viewport, leaving all 122 px of it below the fold.
        """
        self.scroll_focus_pending = False
        if self.stack_area is None or self.stack_grid is None:
            return
        c = self.current_channel
        if c < 0 or c >= len(self.figs) or not self.figs[c].isVisible():
            return
        stack = self.stack_area.widget()
        wanted = stack.sizeHint().height()
        if wanted > 0 and stack.height() != wanted:
            self.stack_grid.activate()
            stack.adjustSize()
        fig = self.figs[c]
        top = fig.mapTo(self.stack_area.widget(), QPoint(0, 0)).y()
        lane_h = max(fig.minimumHeight(), fig.height())
        bar = self.stack_area.verticalScrollBar()
        viewport = self.stack_area.viewport().height()
        if top < bar.value():
            bar.setValue(top)
        elif top + lane_h > bar.value() + viewport:
            bar.setValue(min(bar.maximum(), top + lane_h - viewport))

    def rail_clicked(self, channel: int, extend: bool) -> None:
        """Select a channel from the rail, optionally extending the range."""
        if extend:
            lo = min(channel, self.current_channel)
            hi = max(channel, self.current_channel)
            self.add_to_selected_channels(list(range(lo, hi + 1)))
        else:
            self.selected_channels = [channel]
        self.focus_channel(channel)
        self.update_borders()
        self.update_rail()

    def toggle_solo(self, channel: int) -> None:
        if channel in self.solo_channels:
            self.solo_channels.remove(channel)
        else:
            self.solo_channels.append(channel)
        self.apply_channel_visibility()

    def toggle_mute(self, channel: int) -> None:
        if channel in self.muted_channels:
            self.muted_channels.remove(channel)
        else:
            self.muted_channels.append(channel)
        self.apply_channel_visibility()

    def toggle_maximize(self, channel: int) -> None:
        """Blow one channel up to the full stack height, or restore."""
        if self.maximized_channel == channel:
            self.maximized_channel = None
        else:
            self.maximized_channel = channel
            self.current_channel = channel
        self.apply_channel_visibility()

    def move_channel(self, channel: int, delta: int) -> None:
        """Move a channel up or down in the display order."""
        if channel not in self.channel_order:
            return
        index = self.channel_order.index(channel)
        target = index + delta
        if target < 0 or target >= len(self.channel_order):
            return
        self.channel_order[index], self.channel_order[target] = (
            self.channel_order[target],
            self.channel_order[index],
        )
        self.apply_channel_visibility()

    def apply_channel_visibility(self) -> None:
        """Push solo/mute/order/maximise onto the widgets.

        Solo and mute pick the channels the mean averages as well as the
        lanes the stack draws, so the mean is re-aimed here and redrawn
        after the layout: `adjust_layout` only draws the panels *it*
        revealed, and a mean panel that was already on screen is not one of
        them -- it is the same panel with a different set of electrodes
        behind it.
        """
        self.apply_mean_spectrogram()
        visible = self.apply_lane_visibility()
        if self.current_channel not in visible and visible:
            self.current_channel = visible[0]
        self.update_rail()
        self.update_borders()
        self.adjust_layout(self.width(), self.height())
        if self.mean_spec:
            self.panels.update_plots()

    def apply_lane_visibility(self) -> list:
        """Show exactly the lanes `visible_channels()` names, and the rail
        rows beside them.  Returns those channels.

        Hiding the widget is what actually collapses a lane: `lane_geometry`
        gives a row it is not drawing a minimum height of zero, but the
        figure keeps the `setFixedHeight` of the last pass it *was* drawn
        in, and a fixed height a hidden row still honours.  Measured at
        1200x900 on the sixteen channel array: entering mean mode without
        this left one 498 px panel over fifteen 120 px ghosts and a
        scrollbar 1830 px long, on a mode whose whole point is that it fits
        on one screen.
        """
        visible = self.visible_channels()
        for c in range(len(self.figs)):
            self.figs[c].setVisible(c in visible)
            self.rail_rows[c].setVisible(self.rail_shown() and c in visible)
        # This is the one call every lane-level visibility change goes
        # through, so it is where a selection whose lane has just gone is
        # noticed -- hiding a channel, or the mean spectrogram collapsing
        # sixteen of them onto one.
        self.revalidate_selection()
        return visible

    def update_rail(self) -> None:
        for row in self.rail_rows:
            row.name.setText(self.channel_names.get(row.channel, ""))
            row.update_state()
        self.update_current_plot()

    def update_current_plot(self) -> None:
        """Mark the current channel on its lanes.

        Three cues, none of them colour alone: the view box interior of the
        selected lane is lifted one step off the plot ground
        (`bg.lane` instead of `bg.plot`), its caption goes bold and
        primary, and the channel rail runs a 2 px rule down its row.  In a
        sixteen lane stack the raised ground is the one that works at a
        glance, because it is the whole lane rather than a glyph in it.

        No lane is marked while the mean spectrogram is showing.  There is
        one lane and it is no channel, so "this is the one you selected" has
        nothing to distinguish it from -- and marking it by whether the
        focus happens to sit on the channel whose lane the mean borrowed
        would put a raised ground and a bold caption on the same panel for a
        reason the reader cannot see.  The focus itself is left where it is,
        so leaving the mode gives it back.
        """
        marked = -1 if self.mean_spec else self.current_channel
        for c, axs in enumerate(self.axs):
            current = c == marked
            for ax in axs:
                if hasattr(ax, "set_current"):
                    ax.set_current(current)
                view = ax.getViewBox() if hasattr(ax, "getViewBox") else None
                if view is not None:
                    view.setBackgroundColor(
                        theme.qcolor("bg.lane" if current else "bg.plot")
                    )
        # the navigator draws one channel in single mode - keep it on the
        # channel the user is actually looking at:
        if self.datafig is not None and hasattr(self.datafig, "set_channel"):
            self.datafig.set_channel(self.current_channel)
        # the join labels follow the current lane for the same reason the
        # frame and the bold caption do
        self.update_join_markers()

    def set_navigator_mode(self, mode: str) -> None:
        """Switch the navigator between one row and the per-channel stack."""
        if self.datafig is None or not hasattr(self.datafig, "set_mode"):
            return
        self.datafig.set_mode(mode)
        self.adjust_layout(self.width(), self.height())
        # rows that were hidden a moment ago now have to carry their marks
        self.redraw_annotations()

    def toggle_navigator_mode(self) -> None:
        """Flip the navigator between 'single' and 'all'."""
        if self.datafig is None or not hasattr(self.datafig, "set_mode"):
            return
        current = getattr(self.datafig, "mode", MODE_SINGLE)
        self.set_navigator_mode(MODE_SINGLE if current == MODE_ALL else MODE_ALL)

    def navigator_overview(self) -> str:
        """Which overview the navigator is currently drawing."""
        if self.datafig is None:
            return OVERVIEW_WAVEFORM
        return getattr(self.datafig, "overview", OVERVIEW_WAVEFORM)

    def has_navigator_activity(self) -> bool:
        """Whether an activity overview can be built for this recording."""
        if self.datafig is None or not hasattr(self.datafig, "has_activity"):
            return False
        return bool(self.datafig.has_activity())

    def toggle_navigator_overview(self) -> None:
        """Flip the navigator between the waveform envelope and activity.

        A min/max envelope cannot tell a sustained signal from a transient
        -- one eel pulse or bat click saturates a bin exactly as a chirp of
        the same peak amplitude does.  The activity overview plots sustained
        energy and transient crest separately, both against one global noise
        floor; see :mod:`audian.activity`.
        """
        if self.datafig is None or not hasattr(self.datafig, "set_overview"):
            return
        current = self.navigator_overview()
        target = (
            OVERVIEW_WAVEFORM if current == OVERVIEW_ACTIVITY else OVERVIEW_ACTIVITY
        )
        self.datafig.set_overview(target)
        # the activity overview has a different y range, so the band moves
        self.redraw_annotations()

    def update_levels(self) -> None:
        """Update the peak level of every rail row for the visible window."""
        if not self.rail_rows or self.data is None:
            return
        try:
            trace = self.data["data"]
            trange = self.plot_ranges[Panel.times[0]]
            i0 = max(0, int(trange.r0[0] * trace.rate))
            i1 = min(len(trace), int(trange.r1[0] * trace.rate))
            if i1 <= i0:
                return
            step = max(1, (i1 - i0) // 2000)
            block = np.abs(trace[i0:i1:step, :])
            peaks = np.max(block, axis=0)
            ampl_max = trace.ampl_max
        except (KeyError, IndexError, ValueError, AttributeError):
            return
        for row in self.rail_rows:
            if row.channel < len(peaks):
                row.set_peak(float(peaks[row.channel]), ampl_max)

    # --- y ranges ---------------------------------------------------------

    def set_y_mode(self, mode: int) -> None:
        """Select shared, per-channel or fixed amplitude ranges."""
        self.y_mode = mode
        self.y_locked = mode == DataBrowser.y_fixed
        if mode == DataBrowser.y_fixed:
            for axspec in Panel.amplitudes:
                arange = self.plot_ranges[axspec]
                if arange.is_used():
                    self.set_ranges(axspec, -1.0, 1.0)
        else:
            self.auto_fit_y(force=True)
        self.report_y_range()

    def auto_fit_y(self, force: bool = False) -> None:
        """Fit the amplitude ranges to the data of the visible time window.

        Shared across channels is the default: for an electrode array,
        comparable amplitudes across electrodes are the measurement.
        """
        if self.y_locked and not force:
            return
        trange = self.plot_ranges[Panel.times[0]]
        t0 = trange.r0[0]
        t1 = trange.r1[0]
        channels = (
            self.visible_channels()
            if self.y_mode == DataBrowser.y_per_channel
            else list(range(self.data.channels))
        )
        with self.updating():
            if hasattr(self.plot_ranges, "auto_fit"):
                self.plot_ranges.auto_fit(t0, t1, channels=channels, headroom=0.08)
            else:
                self.plot_ranges.auto(
                    Panel.amplitudes, t0, t1, channels, self.isVisible()
                )
        self.report_y_range()

    def report_y_range(self) -> None:
        """Show the current amplitude range in the status bar."""
        arange = None
        for axspec in Panel.amplitudes:
            candidate = self.plot_ranges.get(axspec)
            if candidate is not None and candidate.is_used():
                arange = candidate
                break
        if arange is None:
            return
        if self.cross_hair:
            # the crosshair owns the amplitude readout while it is on
            return
        channel = min(self.current_channel, len(arange.r0) - 1)
        # Ambient information: the pointer readout owns this field whenever
        # it has something to say, so the y range is shown greyed out and
        # at three significant digits, which is what the field is sized for.
        plot = self.trace_plot(channel)
        unit = (
            plot.data_unit() if plot is not None and hasattr(plot, "data_unit") else ""
        )
        suffix = f" {unit}" if unit else ""
        self.set_readout(
            "a",
            f"A {arange.r0[channel]:.3g}…{arange.r1[channel]:.3g}{suffix}",
            active=False,
        )

    # --- navigator progress -----------------------------------------------

    def report_overview_progress(self) -> None:
        """Report the progress of the compressed full-trace overview."""
        window = self.window()
        compressed = getattr(self.datafig, "compressed_data", None)
        if compressed is None:
            self.overview_timer.stop()
            return
        busy = compressed.is_busy()
        if window is not None and hasattr(window, "set_progress"):
            if busy:
                fraction = (
                    compressed.progress() if hasattr(compressed, "progress") else 0.0
                )
                window.set_progress(fraction, "Building overview…")
            else:
                window.set_progress(None)
        if busy:
            return
        self.overview_timer.stop()
        datas = getattr(compressed, "datas", None)
        times = getattr(compressed, "times", None)
        if datas is None or times is None or len(times) != len(datas):
            self.notify("error", "overview unavailable")

    def close(self):
        if self.datafig is not None:
            self.datafig.close()
        if self.data is not None:
            self.data.close()

    def show_metadata(self):

        def format_dict(md, level):
            mdtable = ""
            for k in md:
                pads = ""
                if level > 0:
                    pads = f' style="padding-left: {level * 30:d}px;"'
                if isinstance(md[k], dict):
                    # new section:
                    if level == 0:
                        mdtable += f'<tr><td colspan=2><font size="+1"><b>{k}:</b></font></td></tr>'
                    else:
                        mdtable += f"<tr><td colspan=2{pads}><b>{k}:</b></td></tr>"
                    mdtable += format_dict(md[k], level + 1)
                    if level == 0:
                        mdtable += "<tr><td colspan=2></td></tr>"
                else:
                    # key-value pair:
                    value = md[k]
                    if isinstance(value, (list, tuple)):
                        value = ", ".join([f"{v}" for v in value])
                    else:
                        value = f"{value}"
                    value = value.replace("\r\n", "\n")
                    value = value.replace("\r", "\n")
                    value = value.replace("\n", "<br>")
                    mdtable += f"<tr><td{pads}><b>{k}</b></td><td>{value}</td></tr>"
            return mdtable

        w = self.fontMetrics().averageCharWidth()
        mdtable = f"<style>td {{padding: 0 {w}px 0 0; }}</style><table>"
        mdtable += format_dict(self.data.meta_data, 0)
        mdtable += "</table>"
        dialog = QDialog(self)
        dialog.setWindowTitle("Meta data")
        # browsable, so explicitly non-modal, and destroyed when closed
        # instead of being kept alive forever by its C++ parent:
        dialog.setWindowModality(Qt.NonModal)
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        vbox = QVBoxLayout(dialog)
        vbox.setContentsMargins(theme.S12, theme.S12, theme.S12, theme.S12)
        vbox.setSpacing(theme.S8)
        label = QLabel(mdtable)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        scrollarea = QScrollArea(dialog)
        scrollarea.setWidget(label)
        vbox.addWidget(scrollarea)
        buttons = QDialogButtonBox(QDialogButtonBox.Close, dialog)
        buttons.rejected.connect(dialog.reject)
        vbox.addWidget(buttons)
        dialog.show()

    def set_cross_hair(self, checked):
        """Turn the readout cross hair on or off.

        It is also the gesture that places a *point* label: with the cross
        hair on, a point category's digit key puts one exactly where the
        pointer is (`category_key`).  Nothing is bound or unbound here --
        the digit keys are live either way and say what they need -- so this
        is only the readout.
        """
        self.cross_hair = checked
        self.plot_ranges.clear_marker()
        self.plot_ranges.clear_stored_marker()
        if not self.cross_hair:
            for field in ("t", "dt", "a", "f", "p"):
                self.set_readout(field, None)
            self.plot_ranges.update_crosshair()

    def show_hover_value(self, channel: int, time: float, value: float) -> None:
        """Report a trace hover to the status bar.

        Replaces the 32 parentless QLabel popups TimePlot.show_times used
        to map and unmap on every mouse move.
        """
        if self.cross_hair:
            # the crosshair readout is richer and owns the fields
            return
        self.set_readout("t", f"t={secs_to_str(time)}")
        if value is None:
            self.set_readout("a", None)
        else:
            self.set_readout("a", f"A={value:.5g}")
        self.set_readout("ch", f"ch {channel:02d}", False)

    def show_navigator_time(self, channel: int, time: float) -> None:
        """Report a navigator hover to the status bar."""
        self.set_readout("t", f"t={secs_to_str(time)}")
        self.set_readout("ch", f"ch {channel:02d}", False)

    def axis_under_pointer(self, kind: str) -> str:
        """The axspec characters of `kind` in the panel under the pointer.

        `kind` is 'amplitude' or 'frequency'.  Returns '' when the pointer
        is not over a plot, which makes `Audian.pointer_axes` fall back to
        every axis of that kind - so +/- always does something, it just
        does it to one panel when the pointer names one.
        """
        panel = self.hover_panel
        if panel is None:
            return ""
        wanted = Panel.amplitudes if kind == "amplitude" else Panel.frequencies
        return "".join(
            a
            for a in panel.ax_spec
            if a in wanted and a in self.plot_ranges and self.plot_ranges[a].is_used()
        )

    def mouse_moved(self, evt, channel):
        if self.cross_hair:
            self.plot_ranges.clear_marker()

        # find axes and position:
        self.hover_panel = None
        for panel in self.panels.values():
            # A spacer is a `PanelSplitter`, a bare pg.GraphicsWidget with no
            # view box, and its ax_spec is the word "spacer" -- so `x()` is
            # 's' and `y()` is 'p', which makes `is_ypower()` spuriously true.
            # With the cross hair on, the pointer in the grab band raised
            # `AttributeError: 'GraphicsLayoutWidget' object has no attribute
            # 'mapSceneToView'` below, because `getViewBox()` on a widget that
            # has none returns the graphics view.  Measured on a four channel
            # stack: the top 3 px of the 7 px band, on every move.
            if panel.is_spacer():
                continue
            if not panel.is_used() or not panel.is_visible(channel):
                continue
            ax = panel.axs[channel]
            if not ax.sceneBoundingRect().contains(evt[0]):
                continue
            # remember it for axis_under_pointer(), whether or not the
            # cross hair is on:
            self.hover_panel = panel
            self.hover_channel = channel
            if self.annotations.loaded and panel.is_time():
                pointer = ax.getViewBox().mapSceneToView(evt[0])
                self.show_annotation_under(pointer.x())
            if self.cross_hair:
                pixel_pos = evt[0]
                pos = ax.getViewBox().mapSceneToView(pixel_pos)
                pixel_pos.setX(pixel_pos.x() + 1)
                pixel_pos.setY(pixel_pos.y() + 1)
                npos = ax.getViewBox().mapSceneToView(pixel_pos)
                x0 = pos.x()
                x1 = npos.x()
                y0 = pos.y()
                y1 = npos.y()
                x, y, z = ax.get_marker_pos(x0, abs(x1 - x0), y0, abs(y1 - y0))
                self.plot_ranges[panel.x()].set_marker(channel, ax, x)
                self.plot_ranges[panel.y()].set_marker(channel, ax, y)
                if z is not None:
                    self.plot_ranges[panel.z()].set_marker(channel, ax, z)
                """
                if not self.marker_time is None:
                    self.marker_time, self.marker_ampl = \
                        panel.get_amplitude(channel, self.marker_time,
                                            pos.y(), npos.x())
                """
            break

        # set cross-hair positions:
        if self.cross_hair:
            self.plot_ranges.update_crosshair()

            # report the time, and the interval between two markers.
            # The reciprocal in Hz next to a delta time is what makes a
            # pulse train readable - keep it verbatim.
            time, delta_time = self.plot_ranges.marker_delta_time()
            dt_text = None
            if delta_time is not None:
                sign = "-" if delta_time < 0 else ""
                dt_text = f"\u0394{time}={sign}{secs_to_str(fabs(delta_time))}"
                if fabs(delta_time) > 1e-6:
                    if 1 / fabs(delta_time) > 1000:
                        dt_text += f" ({0.001 / fabs(delta_time):.4g}kHz)"
                    elif 1 / fabs(delta_time) < 1:
                        dt_text += f" ({1000 / fabs(delta_time):.4g}mHz)"
                    else:
                        dt_text += f" ({1 / fabs(delta_time):.4g}Hz)"
            time, pos = self.plot_ranges.marker_time()
            t_text = None
            if pos is not None:
                sign = "-" if pos < 0 else ""
                t_text = f"t={sign}{secs_to_str(fabs(pos))}"
            self.set_readout("t", t_text)
            self.set_readout("dt", dt_text)
            # report amplitude:
            a_text = None
            ampl, delta_ampl = self.plot_ranges.marker_delta_amplitude()
            if delta_ampl is not None:
                a_text = f"\u0394{ampl}={delta_ampl:6.3f}"
            else:
                ampl, pos = self.plot_ranges.marker_amplitude()
                if pos is not None:
                    a_text = f"{ampl}={pos:.5g}"
            self.set_readout("a", a_text)
            # report frequency:
            f_text = None
            freq, delta_freq = self.plot_ranges.marker_delta_frequency()
            if delta_freq is not None:
                if abs(delta_freq) > 1000:
                    f_text = f"\u0394{freq}={delta_freq / 1000:.4g}kHz"
                elif abs(delta_freq) < 1:
                    f_text = f"\u0394{freq}={delta_freq * 1000:.4g}mHz"
                else:
                    f_text = f"\u0394{freq}={delta_freq:.4g}Hz"
            else:
                freq, pos = self.plot_ranges.marker_frequency()
                if pos is not None:
                    if pos > 1000:
                        f_text = f"{freq}={pos / 1000:.4g}kHz"
                    elif pos < 1:
                        f_text = f"{freq}={pos * 1000:.4g}mHz"
                    else:
                        f_text = f"{freq}={pos:.4g}Hz"
            self.set_readout("f", f_text)
            # report power:
            p_text = None
            pwr, delta_power = self.plot_ranges.marker_delta_power()
            if delta_power is not None:
                p_text = f"\u0394{pwr}={delta_power:6.1f}dB"
            else:
                pwr, pos = self.plot_ranges.marker_power()
                if pos is not None:
                    p_text = f"{pwr}={pos:6.1f}dB"
            self.set_readout("p", p_text)

    def mouse_clicked(self, evt, channel):
        # Ctrl+click picks the editable label under the pointer, and picks
        # nothing else: it is the one modifier free over a lane -- Shift+drag
        # plays, Alt+drag analyses and Alt is the menu mnemonic besides, and
        # Meta belongs to the tiling compositor this fork's user runs.
        # Ctrl+wheel already zooms time, which is a different gesture.
        #
        # Only in label mode.  That mode already decides who owns the pixels
        # of a lane -- it disarms the spectrogram's filter handles for
        # exactly this reason -- and a grip appearing under a reader who is
        # filtering or zooming would be a control they did not ask for.
        if (
            self.region_mode == DataBrowser.MODE_LABEL
            and (evt[0].button() & Qt.LeftButton) > 0
            and (evt[0].modifiers() & Qt.ControlModifier)
        ):
            # `mouse_moved` runs through a 60 Hz SignalProxy, so the pointer
            # may not have reached this lane in `hover_panel` yet
            self.mouse_moved((evt[0].scenePos(),), channel)
            self.select_label_at(channel, evt[0].scenePos())
            return

        # Clicking a lane focuses it, wherever in the lane you click.  The
        # rail card was the only way to do this, which meant reaching for a
        # 48 px column to select the plot you were already looking at.  Every
        # panel of a channel lives in that channel's own figure, so the
        # spectrogram selects it as readily as the trace.
        if (evt[0].button() & Qt.LeftButton) > 0:
            extend = bool(evt[0].modifiers() & Qt.ShiftModifier)
            # guarded: rail_clicked() relays out the stack, which is not
            # something to do on every click inside the current channel
            if extend or channel != self.current_channel:
                self.rail_clicked(channel, extend)

        if not self.cross_hair:
            return

        # update position:
        self.mouse_moved((evt[0].scenePos(),), channel)

        # clear marker:
        if (evt[0].button() & Qt.RightButton) > 0:
            self.plot_ranges.clear_stored_marker()

        # store marker position:
        if (evt[0].button() & Qt.LeftButton) > 0:  # and \
            # (evt[0].modifiers() & Qt.ControlModifier) == Qt.ControlModifier:
            self.plot_ranges.store_marker()

    # --- labels ----------------------------------------------------------
    #
    # The mutable half.  Everything here writes; nothing in the annotation
    # section below it does.  The two are kept apart on purpose -- see
    # `labels.py`.

    def label_settings(self) -> dict:
        """The saved label preferences, or {} when they are from another build.

        Whole-value-or-nothing, the rule `annotation_settings` states: a value
        written under another shape holds keys this build would map onto the
        wrong things, and a reader who finds their vocabulary half restored
        has no way to tell that from a bug.
        """
        from .audian import settings

        saved = settings().get(DataBrowser.LABEL_SETTING)
        if not isinstance(saved, dict):
            return {}
        version = saved.get("version")
        if version != DataBrowser.LABEL_SETTING_VERSION:
            log.warning(
                "ignoring %s settings written in version %r; this audian "
                "writes version %d",
                DataBrowser.LABEL_SETTING,
                version,
                DataBrowser.LABEL_SETTING_VERSION,
            )
            return {}
        return saved

    def restore_label_categories(self) -> tuple:
        """The vocabulary this reader last used, or the defaults.

        Called from `__init__`, before any file is open: the categories are a
        preference and belong to the reader rather than to the recording, so
        the parameter bar can show them with nothing loaded.
        """
        saved = categories_from_settings(self.label_settings().get("categories"))
        return tuple(saved) if saved else DEFAULT_CATEGORIES

    def save_label_settings(self) -> None:
        """Write the vocabulary to the settings file.

        The categories only.  Which category is current is not saved: it is
        where the reader's hand is right now, not a preference, and one
        restored from a previous session would make the first drag of the
        next one write a word nobody chose.
        """
        from .audian import save_setting

        save_setting(
            DataBrowser.LABEL_SETTING,
            {
                "version": DataBrowser.LABEL_SETTING_VERSION,
                "categories": categories_to_settings(self.labels.categories),
            },
        )

    # -- the sidecar --

    def labels_path(self) -> Path:
        """Where this recording's hand-made labels are kept.

        Off `recording_path`, which is file 0 of a split recording -- so a
        reader who later opens file 2 on its own will not find them.  The
        bundle convention solves that by listing every file in
        ``[alignment].recording_files``; a header in a plain CSV would cost
        the one property this file has, which is that anything can read it.
        Named after the recording as it was opened, and left there.
        """
        return sidecar_path(self.recording_path())

    def load_labels(self) -> None:
        """Read the sidecar of the recording just opened.

        A missing file is the state every recording starts in and is silent.
        Anything else the reader is told about: a category the settings did
        not know has just been added to their vocabulary, and rows that named
        no category or no start time were dropped rather than drawn at t=0.
        """
        path = self.labels_path()
        # every label object in the store is replaced, so a selection held by
        # identity now points at a row this recording does not have
        self.deselect_label()
        report = self.labels.read(path)
        self.sync_category_state()
        if report.error:
            self.notify("error", f'could not read "{path.name}": {report.error}')
            return
        if report.dropped:
            # and the store will now refuse to write over that file --
            # `LabelSet.blocked` says why.  Reported as an error rather than
            # a warning because labelling this recording is now read-only
            # until the reader deals with the file.
            self.notify(
                "error",
                f"{report.dropped} label row(s) of "
                f'"{path.name}" name no category or no start time; '
                "labels for this recording will not be saved until it is "
                "fixed or moved away",
            )
        if report.added:
            self.notify(
                "info",
                f"added {', '.join(report.added)} to the label categories, "
                f'from "{path.name}"',
            )
            self.save_label_settings()
        if report.read:
            self.notify("info", f'{report.read} labels from "{path.name}"')

    def schedule_label_save(self) -> None:
        """Queue one sidecar write for the end of this turn of the loop.

        Debounced the way `schedule_annotation_save` is, and for a stronger
        reason: removing a category removes every label under it, so one
        click can be fifty mutations, and each one would otherwise rewrite
        the file.
        """
        if self.label_save_pending:
            return
        self.label_save_pending = True
        QTimer.singleShot(0, self.save_labels)

    def save_labels(self) -> None:
        """Write the sidecar now, and say so in the parameter bar.

        Hand-made labels are the only user-authored data this application
        holds, so a failed write reaches the reader rather than a debug log:
        `save_setting` swallowing an OSError costs a preference, this one
        would cost their work.  `LabelSet.save` writes atomically and removes
        the sidecar when the last label goes.
        """
        self.label_save_pending = False
        if self.data is None or self.data.file_path is None:
            return
        error = self.labels.save(self.labels_path())
        if error and error != self.label_error:
            self.notify("error", f"could not save labels: {error}")
        self.label_error = error
        self.update_label_status()

    def flush_labels(self) -> None:
        """Write any pending labels before this browser goes away.

        There is no `closeEvent` anywhere in audian and `Audian.quit` never
        goes through Qt's close machinery at all, so both exit paths call
        this by hand.  A queued zero-timer save would otherwise be dropped
        with the event loop, and the last label of a session is exactly the
        one a reader has not written down anywhere else.
        """
        if self.labels.dirty or self.label_save_pending:
            self.save_labels()

    # -- the overlays --

    def attach_label_overlays(self) -> None:
        """Give every trace, spectrogram and navigator plot a label overlay.

        The navigator included, the same way `attach_annotation_overlays`
        includes it.  It was left out when this feature was built, on the
        argument that the strip shows the whole session and a one second
        label in an hour of it is a third of a pixel -- but that is an
        argument about *where* a mark is, not about whether it should be
        there, and it applies just as hard to the fixed labels, which have
        always been drawn on it.  Both kinds of mark answer the same question
        on that strip: where in the session is there anything to look at.
        Reading it off one kind and not the other made the overview a partial
        map.

        The navigator rows are read-only (`LabelOverlay.editable`): a label
        is picked up and dragged on a lane, where there are pixels to aim at.
        """
        self.label_overlays = []
        for panel in self.panels.values():
            if panel.is_spacer() or panel.is_power():
                continue
            if panel.is_trace():
                surface = SURFACE_TRACE
            elif panel.is_spectrogram():
                surface = SURFACE_SPECTROGRAM
            else:
                continue
            for ax in panel.axs:
                self.label_overlays.append(
                    LabelOverlay(
                        ax,
                        self.labels,
                        surface,
                        on_edit=self.label_edited,
                        on_dropped=self.label_editor_dropped,
                    )
                )
        # one row per channel, the whole session in each.  No `on_edit`: a
        # navigator overlay refuses to build grips anyway, and handing it a
        # write-back it can never call would say otherwise.
        for ax in getattr(self.datafig, "axs", []):
            self.label_overlays.append(LabelOverlay(ax, self.labels, SURFACE_NAVIGATOR))

    def redraw_labels(self) -> None:
        """Repaint every lane's labels, whatever the view state is.

        The overlays gate their redraw on `LabelSet.revision` *and* the view
        range, so a lane that did not move redraws only because this drops
        its last-drawn state first.
        """
        for overlay in self.label_overlays:
            overlay.invalidate()
            overlay.update_plot()

    def set_labels_visible(self, on: bool) -> None:
        """Take the labels off the lanes for a moment, or put them back."""
        if not on:
            # a selection nobody can see is a selection the next Ctrl+Delete
            # would act on by surprise
            self.deselect_label()
        for overlay in self.label_overlays:
            overlay.set_visible(on)
        if self.label_showw is not None and self.label_showw.isChecked() != bool(on):
            blocked = self.label_showw.blockSignals(True)
            self.label_showw.setChecked(bool(on))
            self.label_showw.blockSignals(blocked)

    def labels_visible(self) -> bool:
        return bool(self.label_overlays and self.label_overlays[0].visible)

    def toggle_labels(self) -> None:
        self.set_labels_visible(not self.labels_visible())

    # -- the vocabulary --

    def sync_category_state(self) -> None:
        """Put the current category, the digit keys and the chips back in step.

        Called after anything changes the vocabulary: the editor, a CSV that
        named a category the settings did not know, or a category being
        removed out from under the one that was current.
        """
        names = [c.name for c in self.labels.categories]
        if self.current_category not in names:
            self.current_category = names[0] if names else ""
        self.rebuild_category_actions()
        self.build_category_chips()
        self.redraw_labels()

    def rebuild_category_actions(self) -> None:
        """Bind the digit keys to the first nine categories.

        Plain digits, which nothing else in audian uses: channels are
        ``Alt+<n>`` and selection is ``Ctrl+<n>``, so 1-9 were free at every
        level.  Nine and not ten, because a vocabulary is read as a list
        starting at one and a category on ``0`` would be the tenth entry
        under the first key.

        Rebuilt rather than re-labelled: a category can be renamed, removed
        or reordered in the editor, and an action left bound to the old name
        would write a word the vocabulary no longer has.
        """
        for act in self.category_acts:
            self.removeAction(act)
            act.setParent(None)
            act.deleteLater()
        self.category_acts = []
        for i, category in enumerate(self.labels.categories[:9]):
            act = QAction(category.name, self)
            act.setShortcut(str(i + 1))
            act.setShortcutContext(Qt.WindowShortcut)
            act.triggered.connect(
                lambda _checked=False, name=category.name: self.category_key(name)
            )
            self.addAction(act)
            self.category_acts.append(act)

    @staticmethod
    def category_key_for(index: int) -> str:
        """The digit that picks category `index`, or "" past the ninth."""
        return str(index + 1) if index < 9 else ""

    def set_current_category(self, name: str) -> None:
        """Choose what the next drag writes."""
        if self.labels.category(name) is None:
            return
        self.current_category = name
        self.update_category_chips()

    def category_key(self, name: str) -> None:
        """What a category's digit key does.

        It picks the category.  And, because a point has no extent to drag,
        it *places* one when the current category is a point category and the
        cross hair is on -- which is the gesture requirement 3 asks for and
        the only one that snaps to a single sample.  With the cross hair off
        it only picks, and says how to place one, so the key never silently
        does nothing.
        """
        category = self.labels.category(name)
        if category is None:
            return
        self.set_current_category(name)
        if not category.is_point():
            return
        if not self.cross_hair:
            self.notify(
                "info",
                f"{name} is a point category -- turn the cross hair on "
                f"(Ctrl+C) and press {name}'s key to place one",
            )
            return
        self.add_point_label()

    def edit_label_categories(self) -> None:
        """Open the category editor (Ctrl+L).

        One dialog at a time, and parented: an unparented top level window is
        tiled by the compositor this fork's user runs.
        """
        if self.label_dialog is not None:
            self.label_dialog.raise_()
            return
        dialog = CategoryDialog(self.labels, self)
        self.label_dialog = dialog

        def finished(result=0):
            self.label_dialog = None
            if result != QDialog.Accepted:
                return
            # a dropped category takes its labels with it, the grips included
            self.revalidate_selection()
            self.sync_category_state()
            self.save_label_settings()
            if dialog.dropped:
                self.notify(
                    "info", f"removed label categories: {', '.join(dialog.dropped)}"
                )
            self.schedule_label_save()
            self.update_label_status()

        dialog.finished.connect(finished)
        dialog.show()

    def show_label_table(self) -> None:
        """Open the list of labels (Ctrl+M), where a label is removed."""
        if self.label_table_dialog is not None:
            self.label_table_dialog.raise_()
            return

        def changed():
            # the list can remove the very label the grips are on
            self.revalidate_selection()
            self.redraw_labels()
            self.schedule_label_save()
            self.update_label_status()

        dialog = LabelTable(self.labels, self, on_change=changed)
        self.label_table_dialog = dialog
        dialog.finished.connect(lambda _r=0: setattr(self, "label_table_dialog", None))
        dialog.show()

    # -- editing one label --
    #
    # Creating a label is a drag; editing one is a *selection* followed by a
    # drag of its grips.  The two gestures share the lane and must not share
    # a press, because the box a reader draws next is very often inside the
    # box they drew last -- which is why nothing stored is an ROI and why the
    # one that is has its body disarmed.  See `labeloverlay.LabelEditor`.

    def overlay_for_axis(self, ax):
        """The label overlay of one plot, or None."""
        for overlay in self.label_overlays:
            if overlay.plot is ax:
                return overlay
        return None

    def select_label_at(self, channel, scene_pos) -> bool:
        """Pick the editable label under `scene_pos`.  False when there is none.

        The hit test runs on the overlay the pointer is over, in data
        coordinates, so what can be picked is exactly what that lane draws --
        on the mean spectrogram, the labels of every channel it averages, and
        on any lane a label that names no channel at all.
        """
        panel = self.hover_panel
        if panel is None or not panel.is_time():
            return False
        if channel >= len(panel.axs):
            return False
        ax = panel.axs[channel]
        view = ax.getViewBox()
        if view is None:
            return False
        pos = view.mapSceneToView(scene_pos)
        return self.select_label_in(ax, float(pos.x()), float(pos.y()))

    def select_label_in(self, ax, t: float, y: float) -> bool:
        """Pick the label at data ``(t, y)`` on one plot, or drop the selection."""
        overlay = self.overlay_for_axis(ax)
        view = ax.getViewBox()
        if overlay is None or view is None:
            return False
        label = overlay.pick(t, y, view)
        if label is None:
            self.deselect_label()
            return False
        self.select_label(overlay, label)
        return True

    def select_label_from_region(self, channel, vbox, rect) -> bool:
        """A Ctrl+drag reaches for a label; it never writes one.

        Ctrl+click is the pick gesture, and whether a press becomes a click
        or a drag is not something a reader decides: `QGraphicsScene` calls
        it a drag after five device pixels of travel, or after any travel at
        all once half a second has passed.  So a Ctrl+click that wobbles, or
        that is simply held while the reader looks, arrives here as a region
        -- and in label mode a region writes a label.  The reader would get a
        hairline mark in their sidecar for having a slow hand.

        Reaching for a label is what they meant either way, so that is what
        this does, at the middle of whatever was dragged over.
        """
        panel = self.panels.get_panel(vbox)
        if panel is None or not panel.is_time() or channel >= len(panel.axs):
            return False
        centre = rect.center()
        return self.select_label_in(
            panel.axs[channel], float(centre.x()), float(centre.y())
        )

    def select_label(self, overlay, label) -> None:
        """Put the grips on one label, taking them off whatever had them."""
        if self.selected_overlay is not None and self.selected_overlay is not overlay:
            self.selected_overlay.stop_editing()
        # after the grips are up, never before: a lane with no view box
        # cannot carry them, and a selection whose grips were never built is
        # the one way the two can disagree
        if not overlay.start_editing(label):
            self.selected_overlay = None
            self.selected_label = None
            return
        self.selected_overlay = overlay
        self.selected_label = label
        self.update_label_status()
        self.notify(
            "info",
            f"{self.describe_label(label)} -- drag a grip to move or resize it, "
            "Ctrl+Delete removes it",
        )

    def label_editor_dropped(self, overlay) -> None:
        """An overlay took its grips off by itself.  Follow it."""
        if self.selected_overlay is overlay:
            self.selected_overlay = None
            self.selected_label = None
            self.update_label_status()

    def revalidate_selection(self) -> None:
        """Drop the selection when nobody can act on it any more.

        Two ways that happens.  The label may have **left the store** --
        removing a category removes every label under it, and the label list
        removes whatever rows were picked, and neither of them knows what
        the grips are on; the selection is held by identity, so a scan is
        the only way to find out.

        Or the **lane may have gone**: hiding a channel, turning the
        spectrograms off, or the mean spectrogram taking over all leave the
        grips on a plot nobody can see.  That is the same argument F9 and
        leaving label mode already make -- a selection nobody can see is the
        one the next Ctrl+Delete acts on, and the File row would go on
        naming it.
        """
        if self.selected_label is None:
            return
        if self.labels.index_of(self.selected_label) < 0:
            self.deselect_label()
            return
        overlay = self.selected_overlay
        plot = getattr(overlay, "plot", None)
        if plot is None or not plot.isVisible():
            # a whole panel switched off: `Panel.set_visible` reaches the
            # plot items themselves, so this one does answer
            self.deselect_label()
            return
        if overlay.channel() not in self.visible_channels():
            # A hidden *channel* does not.  `apply_lane_visibility` hides the
            # channel's `GraphicsLayoutWidget`, and a `QGraphicsItem` inside
            # a hidden widget still reports ``isVisible()`` True -- that flag
            # is about the item in its scene, not about whether anything is
            # showing the scene.  Ask the browser which lanes are on screen
            # instead, which is the same question `apply_lane_visibility`
            # just answered.
            self.deselect_label()

    def deselect_label(self) -> None:
        """Take the grips off, if any are on."""
        if self.selected_overlay is not None:
            self.selected_overlay.stop_editing()
        had = self.selected_label is not None
        self.selected_overlay = None
        self.selected_label = None
        if had:
            self.update_label_status()

    def axis_bounds(self, ax, which: str):
        """``(min, max)`` of one of a plot's axes, or None when it has none.

        The recording's own extent, not the view's: `PlotRange.rmin` and
        `rmax` are what `set_limits` hands the view boxes.
        """
        spec = ax.x() if which == "x" else ax.y()
        r = self.plot_ranges.get(spec)
        if r is None or r.rmin is None or r.rmax is None:
            return None
        if not (isfinite(r.rmin) and isfinite(r.rmax)):
            return None
        return float(r.rmin), float(r.rmax)

    @staticmethod
    def fit_into(low_bound, high_bound, v0, v1, resized: bool):
        """Put ``[v0, v1]`` back inside the bounds.  How depends on the drag.

        **A move slides; a resize clamps.**  The two need opposite answers
        and the difference is not cosmetic.

        A move that runs off the lane is put back by shifting the whole box,
        because its extent is the thing the reader already had right;
        clamping its leading edge alone would squash it against the
        boundary.

        A resize may only move the edge that was dragged.  Sliding one
        shifts the edge nobody touched: measured, a band of 983-3017 Hz on a
        0-4000 Hz lane, pulled 100 px past Nyquist by its top corner, came
        back as ``0.000,4000.000`` -- the low edge dragged down to zero and
        the row claiming the signal fills the band, which is exactly the
        claim `labels.py` says the format exists to refuse.  Squashing
        against the edge is what a resize against the edge means.
        """
        if v1 is None:
            return min(max(v0, low_bound), high_bound), None
        if not resized:
            if v0 < low_bound:
                v1 += low_bound - v0
                v0 = low_bound
            if v1 > high_bound:
                v0 -= v1 - high_bound
                v1 = high_bound
        return max(v0, low_bound), min(v1, high_bound)

    def label_edited(self, overlay, label, t0, t1, f0, f1, resized) -> None:
        """One grip drag has ended: write the new geometry and save it.

        The same sequence `store_label` runs after an add, for the same
        reason -- miss the revision bump inside `LabelSet.set_geometry` and
        the lanes keep drawing the old box, miss the save and the reader's
        correction is not on disk, miss the table refresh and an open label
        list disagrees with the lane it came from.

        The geometry is put back inside the recording first.  A grip is
        dragged by the pointer and the pointer does not stop at the edge of
        the lane: measured, one drag 600 px to the left of a 925 px lane
        wrote ``t_start_s`` of **-1.595676** into the sidecar -- a label
        before the first frame of a file whose every time is "seconds from
        the first frame".  The rubber band that *makes* a label cannot go
        there, because a drag is a rectangle between two points in the lane;
        a grip can.
        """
        index = self.labels.index_of(label)
        if index < 0:
            self.deselect_label()
            return
        bounds = self.axis_bounds(overlay.plot, "x")
        if bounds is not None:
            t0, t1 = DataBrowser.fit_into(bounds[0], bounds[1], t0, t1, resized)
        if f0 is not None and f1 is not None and overlay.has_frequency:
            bounds = self.axis_bounds(overlay.plot, "y")
            if bounds is not None:
                f0, f1 = DataBrowser.fit_into(bounds[0], bounds[1], f0, f1, resized)
        if not self.labels.set_geometry(index, t0, t1, f0, f1):
            # The drag normalised to the geometry already stored, so nothing
            # repaints -- and the grips are still where the pointer left
            # them.  On a trace that is every vertical nudge, because the y
            # of a full-height box is never read back.  Put them on the
            # label again, or they sit a lane's height away from it until
            # something else happens to redraw.
            overlay.invalidate()
            overlay.update_plot()
            return
        self.redraw_labels()
        self.refresh_label_table()
        self.schedule_label_save()
        self.update_label_status()
        what = "resized" if resized else "moved"
        self.notify("info", f"{what} {self.describe_label(label)}")

    def delete_selected_label(self) -> None:
        """Remove the selected label (Ctrl+Delete).

        `Delete` on its own hides the deselected channels and `Backspace`
        zooms back, so neither was available; `Ctrl+Delete` is bound to
        nothing else anywhere in the application.
        """
        label = self.selected_label
        if label is None:
            self.notify("warning", "no label selected -- Ctrl+click one first")
            return
        index = self.labels.index_of(label)
        self.deselect_label()
        if index < 0:
            return
        self.labels.remove(index)
        self.redraw_labels()
        self.refresh_label_table()
        self.schedule_label_save()
        self.update_label_status()
        self.notify("info", f"removed {self.describe_label(label)}")

    # -- making a label --

    def label_from_region(self, channel, vbox, rect) -> bool:
        """Turn one rubber-band drag into a label.  False when it cannot.

        `rect` is already in data coordinates: seconds on x, and on a
        spectrogram **Hz on y, with `top()` the LOW frequency** -- the view
        box y-flips, so the mapped rect's top edge is its numerically smaller
        one.  Measured on a 0-4000 Hz lane at 1200x900: a drag asking for
        1000 -> 2600 Hz arrived as ``top=983.1 bottom=2610.2``, and dragging
        the same box in the opposite direction gave the identical rect.  Get
        this the wrong way round and every label's band is inverted with
        nothing on screen to say so.

        On a trace there is no frequency to store.  That y axis is amplitude,
        and writing ``0..Nyquist`` instead would be a claim that the signal
        fills the band.
        """
        panel = self.panels.get_panel(vbox)
        if panel is None or not panel.is_time():
            return False
        category = self.labels.category(self.current_category)
        if category is None:
            self.notify("warning", "no label category yet -- add one with Ctrl+L")
            return False
        t0, t1 = float(rect.left()), float(rect.right())
        if t1 < t0:
            t0, t1 = t1, t0
        f0 = f1 = None
        if panel.is_spectrogram():
            f0, f1 = float(rect.top()), float(rect.bottom())
            if f1 < f0:
                f0, f1 = f1, f0
        ax = panel.axs[channel] if channel < len(panel.axs) else None
        self.store_label(category, ax, channel, t0, t1, f0, f1)
        return True

    def store_label(self, category, ax, channel, t0, t1, f0, f1) -> None:
        """Add one label of `category`, and start the save that follows it.

        A label made on the mean spectrogram carries **no channel**: that
        panel is an average over the whole selected array and is no electrode
        at all, so naming one would be a claim nobody made.  Every other
        label carries the lane it was drawn on, which is what makes "there
        was a discharge at 12.3 s, 400-900 Hz" into a statement about an
        electrode.

        A point category takes the low corner of whatever was dragged -- the
        earliest time and, on a spectrogram, the lowest frequency.  A point
        has no extent, so there is nothing else the drag could mean, and
        refusing the gesture would leave the mode doing nothing.
        """
        mean = getattr(ax, "mean_channels", None) if ax is not None else None
        stored_channel = None if mean else int(channel)
        if category.is_point():
            label = Label(
                category=category.name,
                kind=LABEL_POINT,
                channel=stored_channel,
                t0=t0,
                t1=None,
                f0=f0,
                f1=f0,
            )
        else:
            label = Label(
                category=category.name,
                kind=category.kind,
                channel=stored_channel,
                t0=t0,
                t1=t1,
                f0=f0,
                f1=f1,
            )
        self.labels.add(label)
        self.redraw_labels()
        self.refresh_label_table()
        self.schedule_label_save()
        self.update_label_status()
        self.notify("info", self.describe_label(label))

    def add_point_label(self) -> bool:
        """Place a point label where the cross hair is.  False when it cannot.

        The position comes from `plot_ranges`, which is where the cross hair
        keeps it, so a point on a trace lands on the extreme sample of the
        pointer's own pixel column (`TimePlot.get_marker_pos`) -- literally a
        single sample -- and one on a spectrogram lands on the exact (t, f)
        under the pointer.
        """
        category = self.labels.category(self.current_category)
        if category is None:
            return False
        panel = self.hover_panel
        if panel is None or not panel.is_time():
            self.notify("warning", "point the cross hair at a lane first")
            return False
        _name, t = self.plot_ranges.marker_time()
        if t is None:
            return False
        f = None
        if panel.is_spectrogram():
            _name, f = self.plot_ranges.marker_frequency()
        channel = self.hover_channel
        ax = panel.axs[channel] if channel < len(panel.axs) else None
        self.store_label(category, ax, channel, float(t), None, f, f)
        return True

    def undo_last_label_change(self) -> None:
        """Take back the last change to the labels (Shift+B).

        One level, and it now covers a **geometry edit** as well as an add
        and a delete.  It had to: the sidecar is written a turn of the event
        loop after the gesture, so a box dragged into the wrong shape is on
        disk before the reader has finished watching it happen, and without
        this the shape they drew would be gone for good.  There is still no
        undo stack anywhere in audian and a real one is a different feature.

        It used to pop the last row whatever had happened, which meant that
        on a recording just opened, this key deleted a label the reader had
        never touched.  A change nobody made is not a change to take back.
        """
        before = self.selected_label
        kind = self.labels.undo()
        if kind is None:
            self.notify("warning", "nothing to undo")
            return
        if before is not None and self.labels.index_of(before) < 0:
            self.deselect_label()
        self.redraw_labels()
        self.refresh_label_table()
        self.schedule_label_save()
        self.update_label_status()
        self.notify(
            "info",
            {
                "add": "removed the label just added",
                "remove": "put the removed label back",
                "geometry": "put the label back where it was",
            }[kind],
        )

    def describe_label(self, label) -> str:
        """One line naming a label, for the status bar."""
        where = "mean" if label.channel is None else f"ch {label.channel:02d}"
        when = secs_to_str(label.t0)
        if not label.is_point():
            when += f"-{secs_to_str(label.t_end())}"
        band = ""
        if label.has_frequency() and label.f1 > label.f0:
            band = f", {label.f0:.0f}-{label.f1:.0f} Hz"
        elif label.has_frequency():
            band = f", {label.f0:.0f} Hz"
        return f"{label.category} on {where} at {when}{band}"

    def refresh_label_table(self) -> None:
        """Keep an open label table in step with a change made on a lane."""
        if self.label_table_dialog is not None:
            self.label_table_dialog.model.refresh()

    # -- the parameter bar group --

    def setup_label_group(self) -> "ParameterGroup":
        """Build the Editable labels group of the parameter bar.

        Three rows, against the annotation group's five.  The bar is as
        tall as its tallest page whichever page is showing, so this group
        costs the lanes no height at all; a row of this bar is about 24 px
        off every lane in a sixteen channel stack, which is why the row
        count is still a design constraint rather than an afterthought.

        Width is no longer the hard half.  It was, when the groups sat
        side by side and this one had a fifth of the bar: a plain
        `QHBoxLayout` of two chips and a button took the window's minimum
        from 1372 px to 1572.  Behind tabs the page gets the whole bar --
        measured 1183 px on a 1200 px window -- and twelve categories fit
        over the strip's two lines with nothing folded.  `CategoryStrip`
        stays because the number of categories is still the reader's to
        choose and a strip that could outgrow any width is still the one row
        of this bar that could.

        Always built, hidden until a file is open, for the same reason the
        annotation group is: a control that appears and disappears is one
        nobody learns to look at.
        """
        group = ParameterGroup(DataBrowser.EDITABLE_TAB, self.parambar, caption=False)

        # Captionless and spanning: the chips name themselves and carry
        # their own keys, so "CATEGORY 1-9" beside them would be the group
        # title said twice at the cost of 80 px of chips per line.
        self.label_chipbox = CategoryStrip(self.parambar, on_pick=self.chip_clicked)
        self.label_chipbox.setToolTip(
            "The label categories.  Click one, or press its digit, to make it "
            "the one the next drag writes."
        )
        group.add_span_row(self.label_chipbox)

        wherebox = QWidget(self.parambar)
        where = QHBoxLayout(wherebox)
        where.setContentsMargins(0, 0, 0, 0)
        where.setSpacing(theme.S4)
        self.label_showw = QToolButton(wherebox)
        self.label_showw.setText("Show")
        self.label_showw.setCheckable(True)
        self.label_showw.setChecked(True)
        self.label_showw.setFont(theme.font_ui(theme.SIZE_SMALL_PT))
        self.label_showw.setFixedHeight(theme.CHIP_HEIGHT)
        self.label_showw.setToolTip(
            "Show the editable labels over the lanes  (F9).\n"
            "In label mode (b), Ctrl+click one to pick it up: it grows grips "
            "to drag it into shape,\nand Ctrl+Delete removes it.  Escape puts "
            "it down again, and Shift+B takes back the last change."
        )
        self.label_showw.toggled.connect(self.set_labels_visible)
        where.addWidget(self.label_showw)
        editw = QToolButton(wherebox)
        editw.setText("Edit…")
        editw.setFont(theme.font_ui(theme.SIZE_SMALL_PT))
        editw.setFixedHeight(theme.CHIP_HEIGHT)
        editw.setToolTip("Add, rename and remove label categories  (Ctrl+L)")
        editw.clicked.connect(self.edit_label_categories)
        where.addWidget(editw)
        tablew = QToolButton(wherebox)
        tablew.setText("List…")
        tablew.setFont(theme.font_ui(theme.SIZE_SMALL_PT))
        tablew.setFixedHeight(theme.CHIP_HEIGHT)
        tablew.setToolTip(
            "Every editable label of this recording, and the control that "
            "removes one  (Ctrl+M).  Undo the last one with Shift+B."
        )
        tablew.clicked.connect(self.show_label_table)
        where.addWidget(tablew)
        where.addStretch(1)
        group.add_row("Show", "F9", wherebox)

        self.label_statusw = QLabel("", self.parambar)
        self.label_statusw.setFont(theme.font_mono(theme.SIZE_SMALL_PT))
        self.label_statusw.setWordWrap(False)
        # Ignored, not Preferred: this line changes on every label added, and
        # a label that asks for the width of its longest string would relayout
        # the whole bar under the pointer -- the failure `annotation_hoverw`
        # was rebuilt to stop.  The full text stays in the tool tip.
        self.label_statusw.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        theme.tint(self.label_statusw, "fg.muted")
        group.add_row("File", "", self.label_statusw)

        self.label_group = group
        # No setVisible here: the stack owns which page is on screen.  The
        # Labels tab is available from the moment the bar exists -- the
        # vocabulary is a preference and does not need a file -- and the
        # group's own file row states the empty case.
        return group

    def build_category_chips(self) -> None:
        """Rebuild the category chips.

        The chips are the legend as well as the picker: which colour means
        which word is read off the bar rather than remembered, exactly as the
        annotation layer chips work.  Built from `LabelSet.categories` and
        never from a list in this file, so the chips, the digit keys and the
        saved vocabulary are the same set by construction.
        """
        if self.label_chipbox is None:
            return
        keys = {
            c.name: self.category_key_for(i)
            for i, c in enumerate(self.labels.categories)
        }
        self.label_chipbox.set_categories(
            self.labels.categories, self.current_category, keys
        )
        self.label_chips = self.label_chipbox.chips
        # The group grew or shrank by a chip, and `equalize` froze every frame
        # height when the bar was built.  Deferred by one turn of the loop:
        # widgets added a moment ago are not in their parent's size hint yet,
        # and measuring before Qt has processed the invalidation clips the
        # last row.
        QTimer.singleShot(0, self.equalize_parameter_bar)

    def chip_clicked(self, name: str) -> None:
        """A chip picks its category, and a point chip also places one.

        The same rule the digit key follows, so the two ways of choosing a
        category cannot mean different things.
        """
        self.category_key(name)

    def update_category_chips(self) -> None:
        """Check exactly the chip of the current category."""
        if self.label_chipbox is not None:
            self.label_chipbox.set_current(self.current_category)

    def label_status_text(self) -> str:
        """What the File row says.

        Empty states are separate sentences on purpose.  "no labels yet"
        with the gesture beside it is the difference between a reader who
        starts and one who looks for a menu; the alternative -- a bare
        ``0`` -- says nothing about how to make it a 1.
        """
        count = len(self.labels)
        if self.labels.blocked:
            # ahead of everything else: a reader labelling into a store that
            # cannot save is doing work that will be lost, and the count
            # would read as "saved" the moment they stopped
            return f"READ-ONLY -- {self.labels.blocked}"
        if self.label_error:
            return f"{count} labels -- SAVE FAILED: {self.label_error}"
        if not count:
            if not self.labels.categories:
                return "no categories yet -- add one with Edit…"
            return "no labels yet -- press b, then drag over a lane"
        name = self.labels.path.name if self.labels.path is not None else "?"
        state = "unsaved" if (self.labels.dirty or self.label_save_pending) else "saved"
        text = f"{count} label{'' if count == 1 else 's'} · {name} · {state}"
        # The selection has to be readable somewhere that stays put.  The
        # status bar says it once, at the moment of picking, and is then
        # taken by the next pointer readout; this row is the only place that
        # still answers "which one am I about to Ctrl+Delete?" a minute later.
        if self.selected_label is not None:
            text += f" · editing {self.describe_label(self.selected_label)}"
        return text

    def update_label_status(self) -> None:
        """Redraw the File row, elided to the width the row already has."""
        label = self.label_statusw
        if label is None:
            return
        text = self.label_status_text()
        label.setToolTip(text)
        metrics = theme.mono_metrics(theme.SIZE_SMALL_PT)
        label.setText(metrics.elidedText(text, Qt.ElideRight, max(label.width(), 1)))
        if self.label_undow is not None:
            self.label_undow.setEnabled(self.labels.can_undo())
        self.update_label_alert()

    # --- annotations -----------------------------------------------------

    def attach_annotation_overlays(self) -> None:
        """Give every trace, spectrogram and navigator plot an overlay.

        Done once, when the plots are built.  The overlays start empty and
        cost nothing until a table is loaded; creating them up front means a
        file opened later never has to walk the plot tree again.

        Which surface a plot is decides how the marks are drawn, so it is
        passed rather than inferred: a line down a lane is right over a
        waveform and wrong over a strip showing ten minutes at once.
        """
        self.annotation_overlays = []
        for panel in self.panels.values():
            if panel.is_spacer() or panel.is_power():
                continue
            if panel.is_trace():
                surface = SURFACE_TRACE
            elif panel.is_spectrogram():
                surface = SURFACE_SPECTROGRAM
            else:
                continue
            for ax in panel.axs:
                overlay = EventOverlay(ax, self.annotations, surface)
                ax.annotations = overlay
                self.annotation_overlays.append(overlay)
        # the navigator: one row per channel, the whole session in each
        for ax in getattr(self.datafig, "axs", []):
            overlay = EventOverlay(ax, self.annotations, SURFACE_NAVIGATOR)
            ax.annotations = overlay
            self.annotation_overlays.append(overlay)

    # --- where the files of one recording butt together ------------------

    def recording_joins(self) -> list[float]:
        """Seconds at which the loader started reading a new file.

        Read off the LOADER and never off a bundle.  A split recording has
        joins whether or not any annotation was ever fitted to it, and a
        viewer that took their positions from an alignment file would be
        drawing a claim about the files out of a claim about the log --
        which is exactly the confusion a join marker exists to prevent.
        `audian.data.open_files` has already asserted the loader's frame
        total against the file headers, so these indices are what is on
        screen.
        """
        loader = getattr(self.data, "data", None)
        starts = getattr(loader, "start_indices", None)
        rate = float(getattr(loader, "rate", 0.0) or 0.0)
        if starts is None or rate <= 0.0 or len(starts) < 2:
            return []
        # index 0 is the start of the recording, which is not a join
        return [float(i) / rate for i in list(starts)[1:]]

    def declared_join_gaps(self) -> list:
        """What the loaded bundle says the recorder lost at each join.

        A declared gap only ever *annotates* a marker the loader positioned;
        it never places one, and it is never corrected for.  Nothing is
        labelled when the bundle names a different number of joins than the
        loader opened files: two sources that disagree about how many joins
        the recording has cannot be matched up join by join, and a gap
        printed against the wrong join is worse than no gap at all.
        """
        bundle = self.annotations.bundle
        if bundle is None:
            return []
        gaps = list(getattr(bundle.meta.alignment, "recording_join_gaps_s", ()) or ())
        if not gaps:
            return []
        return gaps if len(gaps) == len(self.recording_joins()) else []

    def attach_join_markers(self) -> None:
        """Draw one quiet rule per join, on every lane and navigator row.

        Chrome, not data: a join is a fact about the FILES, so it reads like
        the zero line -- same ink, same hairline -- and it sits below every
        annotation, so a mark is never obscured by one.  It is drawn on the
        navigator too, where the whole session is in view and a join is most
        worth knowing about.

        Not on the spectrogram: `SpecItem` is an opaque image at z=0, so a
        rule below the annotations would not be composited there at all, and
        one above them would read as an event rather than as chrome.
        """
        self.join_markers = []
        self.join_labels = []
        joins = self.recording_joins()
        if not joins:
            return
        lanes = []
        for panel in self.panels.values():
            if panel.is_trace():
                lanes.extend((c, ax) for c, ax in enumerate(panel.axs))
        for channel, ax in lanes:
            for which, time in enumerate(joins):
                line = self.join_marker(ax, time, label=True)
                label = getattr(line, "label", None)
                if label is not None:
                    self.join_labels.append((channel, which, label))
        for ax in getattr(self.datafig, "axs", []):
            for time in joins:
                self.join_marker(ax, time, label=False)
        self.update_join_markers()

    def join_marker(self, ax, time: float, label: bool):
        """One full-height rule at `time`, below everything else in `ax`."""
        line = pg.InfiniteLine(
            angle=90,
            pos=time,
            movable=False,
            pen=theme.join_pen(),
            label="" if label else None,
            labelOpts={
                # Bottom-left anchored just above the floor of the lane: the
                # top-left corner already carries the channel caption, and a
                # top-anchored label at this position hangs BELOW the view
                # box, which clips its children -- measured, the label's rect
                # sat 25 px outside a 260 px lane and never appeared.
                "position": 0.03,
                "color": theme.token("fg.faint"),
                "movable": False,
                "anchors": [(0.0, 1.0), (0.0, 1.0)],
            },
        )
        # below FILL_Z (-20), the lowest an annotation is ever drawn at
        line.setZValue(-30)
        _passive(line)
        # pyqtgraph only gives an InfiniteLine a `label` when it was built
        # with one, so it is asked for rather than assumed
        text = getattr(line, "label", None)
        if text is not None:
            text.setFont(theme.font_mono(theme.SIZE_SMALL_PT))
            _passive(text)
        ax.addItem(line, ignoreBounds=True)
        self.join_markers.append(line)
        return line

    def update_join_markers(self) -> None:
        """Say what a bundle declares was lost at each join, on one lane.

        The gap is printed on the current channel's lane only.  Three joins
        in a sixteen lane stack are forty-eight labels, all saying the same
        thing about the recording -- so the label follows the lane the reader
        is reading, the way the lane's own frame and bold caption already do.
        """
        if not self.join_labels:
            return
        gaps = self.declared_join_gaps()
        for channel, which, label in self.join_labels:
            text = gap_text(gaps[which]) if which < len(gaps) else ""
            # shown BEFORE the text is set: pyqtgraph's InfLineLabel drops a
            # setFormat on a hidden label without a word (`valueChanged`
            # returns early when the item is not visible), so setting the
            # text first and showing afterwards puts an empty label on screen
            label.setVisible(bool(text) and channel == self.current_channel)
            label.setFormat(text)

    def polish_join_markers(self) -> None:
        """Re-resolve the rules' pen and ink after a live theme switch."""
        for line in self.join_markers:
            line.setPen(theme.join_pen())
            text = getattr(line, "label", None)
            if text is not None:
                text.setColor(theme.token("fg.faint"))

    def set_annotation_surface(self, surface: str, on: bool) -> None:
        """Show or hide the overlay on one surface."""
        self.annotations.set_surface(surface, on)

    def recording_path(self) -> Path:
        """The recording a session bundle has to name.

        `Data.file_path` is whatever the browser was handed, which is a list
        while several files are being opened into one buffer and a single
        path afterwards.  A bundle belongs to one recording, so the first
        file is the one to check the name against -- and the frame count is
        checked against the whole of what the loader opened, which is what
        `recording_info` is for.
        """
        path = self.data.file_path
        if isinstance(path, (list, tuple, np.ndarray)):
            path = path[0] if len(path) else ""
        return Path(path)

    def init_annotations(self) -> None:
        """Load the annotations this browser was opened with, if any.

        An explicit ``--events`` path is loaded and any failure reported.  With
        no path, a session bundle sitting beside the recording is picked up
        automatically -- but only if its ``[alignment].recording_file`` names
        *this* recording, and never silently: opening one is always announced.
        """
        if self.events_path is not None:
            self.load_annotations(self.events_path)
            return
        found = self.annotations.discover(self.recording_path())
        if found is not None:
            self.load_annotations(found, discovered=True)

    def recording_info(self) -> Optional[RecordingInfo]:
        """The header facts of the whole recording, as the loader has them.

        `SessionMeta.check_recording` reads ONE file's header when it is not
        handed an `info`, and a split recording is not one file.  On exp3 --
        four WAVs opened as one recording -- that made the frame check
        compare the bundle's 173,809,152 frames against DR0000_0088.wav's
        44,734,464 and report a perfect match as the wrong bundle, which is
        the crying-wolf failure in the one check meant to catch a genuinely
        wrong bundle.  `data.open_files` has already asserted the loader's
        total against every file header, so these three numbers are the
        recording rather than a quarter of it.
        """
        loader = getattr(self.data, "data", None)
        if loader is None:
            return None
        return RecordingInfo(
            samplerate=float(getattr(loader, "rate", 0.0) or 0.0),
            frames=int(getattr(loader, "frames", 0) or 0),
            channels=int(getattr(loader, "channels", 0) or 0),
        )

    def open_file_names(self) -> list[str]:
        """The file names the loader actually opened, in the order it has them.

        Off the LOADER, not off `Data.file_path`: the latter is what this
        browser was asked to open, and a file that was asked for and not
        opened is precisely the case a provenance check must not read past
        (`audian.data.open_files` drops one and raises about it).

        An empty list means the loader could not say -- and unknown is not
        wrong: `check_recording_coverage` makes no claim from it.
        """
        loader = getattr(self.data, "data", None)
        return [Path(p).name for p in getattr(loader, "file_paths", ()) or ()]

    def check_recording_coverage(self, bundle) -> Optional[SplitCoverage]:
        """Refuse to draw a bundle when only part of its recording is open.

        The failure this exists for passed every other guard.  Opening exp3's
        DR0000_0090.wav alone -- file 3 of the 4 the bundle names -- gave
        ``RecordingCheck(name=True, rate=True, frames=True, channel=True,
        problems=())``, a badge reading WARNINGS about something else, and a
        lane full of marks at 100-105 s over audio whose real content is
        recording seconds 1863.936-1868.936.  Measured on that file: 260 mark
        segments drawn on the trace lane and 4982 on the navigator, every one
        of them 1863.936 s from where it belongs and every one of them looking
        exactly like a mark in the right place.  With this check: 0 and 0.

        The name check cannot catch it: the open file IS one of the four.  The
        frame check cannot catch it: it accepts either the whole recording's
        frame count or one file's own, deliberately, so a caller may hand it a
        single WAV header or the loader over all four.  What was missing is
        the one fact this browser has always had and never asked -- how many
        of the declared files it opened.  Matched by NAME, never by frame
        count, for the same reason as every other provenance check here.

        The refusal is `AnnotationLayer.recording_mismatch`, because that is
        the viewer's one gate on drawing at all, and this is the same class of
        wrong: every position on screen would come from a fit against
        something other than what is open.  The badge then says which files
        are open, which are missing and what to do about it
        (`update_annotation_badge`).  Nothing is re-based to fit -- see
        `SplitCoverage`.
        """
        self.annotation_coverage = None
        names = self.open_file_names()
        if bundle is None or not names:
            return None
        coverage = bundle.meta.alignment.coverage(names)
        if not coverage.partial:
            return None
        self.annotation_coverage = coverage
        self.annotations.recording_mismatch = coverage.subject()
        # The bundle was loaded before this could run, so a redraw and a badge
        # have already gone out against the state that let it draw.  One more
        # pass puts every lane and the badge on the refusal.
        self.annotations.invalidate()
        self.rebuild_annotations()
        return coverage

    def bundle_problems(self, bundle) -> list[str]:
        """Everything the bundle has to say, with its own frame check redone.

        The provenance check the bundle ran at load asked one file's header
        how long the recording is; this browser knows what the loader
        actually opened, so the check is run again against that and its
        answer replaces the one made without it.  Nothing else in
        `bundle.warnings` is touched.
        """
        info = self.recording_info()
        stale = frozenset(bundle.recording_check.problems)
        problems = [w for w in bundle.warnings if w not in stale]
        if info is None:
            problems.extend(bundle.recording_check.problems)
            return problems
        check = bundle.meta.check_recording(self.recording_path(), info=info)
        problems.extend(check.problems)
        gaps = list(getattr(bundle.meta.alignment, "recording_join_gaps_s", ()) or ())
        joins = len(self.recording_joins())
        if gaps and len(gaps) != joins:
            # Not fatal and not corrected for: it only means the declared
            # gaps cannot be matched to the joins the loader reported, so
            # none of them is printed against a join.
            problems.append(
                f"the bundle declares {len(list(gaps))} join gap(s), the "
                f"loader opened {joins + 1} file(s) -- no gap is labelled"
            )
        return problems

    def residual_tip(self, bundle) -> str:
        """The fit's residual per region, as the reader measured it.

        This viewer never computes a residual: ``detected_time_s -
        recording_time_s`` is the reader's arithmetic and the tolerance it is
        judged against is the fit's own, so this only prints what
        `SessionBundle.residuals` found.

        Per region and not the one number in the header, because the header's
        number is not a promise about what is on screen: exp3 states a median
        residual of about a microsecond for the whole session, while only 259
        of the 874 pulses in its last file matched the recording at all.  The
        regions the reader cut are the recording's own files when there are
        joins, so this table and the join rules in the lanes are talking about
        the same stretches.

        The regions that are far outside the tolerance are already in
        `bundle.warnings` and reach the status bar through `bundle_problems`;
        this is the same measurement where a reader looks for it once the
        warning has scrolled away.
        """
        stats = getattr(bundle, "residuals", None)
        if stats is None or not len(stats):
            return ""
        head = (
            f"Fit residual by {'file' if stats.split else 'region'} "
            f"(match tolerance {1e3 * stats.tolerance_s:.3f} ms):"
        )
        return head + "\n" + "\n".join(f"• {r.summary()}" for r in stats.regions)

    def load_annotations(self, path, discovered: bool = False) -> bool:
        """Read a session bundle and draw it over every lane."""
        try:
            bundle = self.annotations.load(path, self.recording_path())
        except Exception as e:
            self.notify("error", f"can not read annotations from {path}: {e}")
            log.exception("failed to read annotations from %s", path)
            return False
        found = " found beside the recording" if discovered else ""
        self.notify("success", f"{Path(path).name}{found}: {bundle.summary()}")
        lost = sum(bundle.dropped.values())
        if lost:
            self.notify(
                "warning",
                f"{lost} annotation rows have no recording_time_s and were dropped",
            )
        # An unvalidated fit, the wrong recording, or only part of the
        # recording is not a detail for a tool tip: each invalidates every
        # position on screen, so it goes through the same channel as an error
        # would.
        coverage = self.check_recording_coverage(bundle)
        if coverage is not None:
            self.notify("error", f"{Path(path).name}: {coverage.summary()}")
        elif self.annotations.recording_mismatch:
            self.notify(
                "error",
                f"{Path(path).name} was fitted against "
                f"{self.annotations.recording_mismatch}, not against "
                f"{self.recording_path().name} -- nothing is drawn",
            )
        elif self.annotations.unvalidated:
            self.notify(
                "warning",
                f"{Path(path).name} carries an UNVALIDATED alignment: "
                f"annotation times are unverified predictions",
            )
        for warning in self.bundle_problems(bundle):
            self.notify("warning", f"{Path(path).name}: {warning}")
        # a bundle can declare what the recorder lost at each join, and the
        # rules the loader placed are where that is said
        self.update_join_markers()
        # after the notifications, so what is said about the bundle is said
        # about the bundle and not about the reader's last session
        self.restore_annotation_layers()
        return True

    def open_annotations(self) -> None:
        """Ask for a session bundle and load it."""
        file_path = QFileDialog.getOpenFileName(
            self,
            "Load annotations",
            os.fspath(self.recording_path().parent),
            "Session metadata (*_metadata.toml);;All files (*)",
        )[0]
        if file_path:
            self.load_annotations(file_path)

    def clear_annotations(self) -> None:
        if not self.annotations.loaded:
            return
        self.annotations.clear()
        self.annotation_coverage = None
        # the joins stay -- they are the loader's -- but a gap the bundle
        # declared goes with the bundle
        self.update_join_markers()
        self.notify("info", "annotations cleared")

    def toggle_annotations(self) -> None:
        """Show or hide the overlay.

        With nothing loaded this says so and stops.  It used to fall through
        to the file chooser, which meant a key bound to a *toggle* could open
        a modal dialog -- surprising from the keyboard, and a hang for
        anything driving the application without a user in front of it.
        Loading has its own action.
        """
        if not self.annotations.loaded:
            self.notify("info", "no annotations loaded -- Ctrl+Shift+A opens a file")
            return
        self.annotations.toggle()

    # -- which layers are drawn --

    def annotation_chip_clicked(self, layer_id: str) -> None:
        """Solo the clicked layer, or extend the set under a modifier.

        The gesture the channel rail beside the lanes already teaches: a
        plain click leaves one thing showing, a held modifier adds to or
        removes from what is showing, and one control puts everything back.
        Ctrl and shift both extend -- the rail extends its selection with
        shift, list widgets everywhere extend with ctrl, and a reader who
        reaches for either is asking for the same thing.
        """
        modifiers = QApplication.keyboardModifiers()
        extend = bool(modifiers & (Qt.ControlModifier | Qt.ShiftModifier))
        self.solo_annotation_layer(layer_id, extend)

    def apply_annotation_layers(self, wanted) -> bool:
        """Put the layer switches into `wanted`, in one redraw and one write.

        `AnnotationLayer.set_layer` signals per call and a restored set moves
        up to ten switches, so the signals are held and the redraw and the
        settings write are made once by hand -- what `solo()` already does
        for a solo, for a set that is not a solo.

        Only layers this bundle carries are touched: a switch for a layer it
        does not have would put a name in the bar that the recording knows
        nothing about.  Returns whether anything moved.
        """
        layer = self.annotations
        before = dict(layer.layers)
        blocked = layer.blockSignals(True)
        try:
            for layer_id in before:
                on = wanted.get(layer_id)
                if isinstance(on, bool):
                    layer.set_layer(layer_id, on)
        finally:
            layer.blockSignals(blocked)
        if layer.layers == before:
            return False
        self.redraw_annotations()
        self.schedule_annotation_save()
        return True

    def solo_annotation_layer(self, layer_id: str, extend: bool = False) -> None:
        """Show `layer_id` alone, or toggle it beside the others.

        Clicking the only layer that is on puts back the set that was showing
        before the solo -- not every layer there is.  This is the round trip
        the channel rail's solo button makes: `solo_channels` is an overlay
        over the mute state and dropping a channel from it restores that
        state exactly.  Switching all ten layers on instead would switch on
        the three the bundle deliberately defaults off -- the localization
        runs alone cover 59% of the exp2 session -- and `schedule_annotation_save`
        would then persist that set, so a gesture meant to be free would
        destroy the reader's working set for good.  `Shift+F8` is the way to
        all-on, and it says so on the All button.
        """
        if not self.annotations.loaded:
            self.notify("info", "no annotations loaded -- Ctrl+Shift+A opens a file")
            return
        showing = [i for i, on in self.annotations.layers.items() if on]
        if extend:
            self.annotations.toggle_layer(layer_id)
        elif showing == [layer_id]:
            restore = self.annotation_layers_before_solo
            self.annotation_layers_before_solo = None
            if restore is None:
                # soloed before this browser was watching -- a saved set
                # restored into a solo, say.  All-on is then the only set
                # that is certainly not the one on screen.
                self.annotations.show_all()
            else:
                self.apply_annotation_layers(restore)
        else:
            if len(showing) != 1:
                # remember the working set, not the solo it is replacing:
                # soloing one layer after another still comes back to what
                # the reader had before the first of them
                self.annotation_layers_before_solo = dict(self.annotations.layers)
            self.annotations.solo(layer_id)
        # A gesture that asks for the state it is already in changes nothing
        # and emits nothing, and the chip has flipped its own check by then.
        self.update_annotation_chips()

    def set_annotation_layer(self, layer_id: str, on: bool) -> None:
        """Switch one layer on or off, as the menu entry does.

        A hand-built set is the reader's own working set from here on, so the
        set a solo would have restored is forgotten: coming back to a set
        that was replaced two gestures ago would be a surprise.
        """
        if not self.annotations.loaded:
            self.notify("info", "no annotations loaded -- Ctrl+Shift+A opens a file")
            return
        self.annotation_layers_before_solo = None
        self.annotations.set_layer(layer_id, bool(on))

    def show_all_annotation_layers(self) -> None:
        """Undo a solo: every layer of the bundle draws again."""
        if not self.annotations.loaded:
            self.notify("info", "no annotations loaded -- Ctrl+Shift+A opens a file")
            return
        self.annotation_layers_before_solo = None
        self.annotations.show_all()
        self.update_annotation_chips()

    # -- what survives a restart --

    def annotation_settings(self) -> dict:
        """The saved annotation preferences, or an empty mapping.

        A value whose ``version`` is not the one this build writes is dropped
        whole rather than half-read, which is the entire reason the version
        is written.  `ANNOTATION_SETTING_VERSION` is bumped exactly when the
        *shape* of the value changes, so a value carrying another number
        holds keys this build would map onto the wrong switches -- and a
        reader who opens a bundle and finds three arbitrary layers off has no
        way of telling that from a bug.  Defaults are a state audian can
        explain; a half-restored set is not.

        Imported here and not at the top of the file: `audian.py` imports
        this module, so a module level import would be a cycle.
        """
        from .audian import settings

        saved = settings().get(DataBrowser.ANNOTATION_SETTING)
        if not isinstance(saved, dict):
            return {}
        version = saved.get("version")
        if version != DataBrowser.ANNOTATION_SETTING_VERSION:
            log.warning(
                "ignoring %s settings written in version %r; this audian "
                "writes version %d",
                DataBrowser.ANNOTATION_SETTING,
                version,
                DataBrowser.ANNOTATION_SETTING_VERSION,
            )
            return {}
        return saved

    def restore_annotation_surfaces(self) -> None:
        """Put back which surfaces the reader last drew annotations on."""
        saved = self.annotation_settings().get("surfaces")
        if not isinstance(saved, dict):
            return
        for surface, _label, _enabled in self.annotations.surface_states():
            on = saved.get(surface)
            if isinstance(on, bool):
                self.annotations.set_surface(surface, on)

    def restore_annotation_layers(self) -> None:
        """Put back the layer switches this reader last left on.

        Only for layers the bundle in front of us actually carries: a saved
        switch for a layer this session does not have would put a name in the
        bar that the recording knows nothing about.  A layer the settings
        have never seen keeps its own `default_on`, which is how a layer
        added in a later version arrives visible instead of silently off.
        """
        if not self.annotations.loaded:
            return
        self.annotation_layers_before_solo = None
        wanted = self.annotation_settings().get("layers")
        if not isinstance(wanted, dict):
            return
        # One redraw for ten layers: `set_layer` signals per call, and a load
        # that puts back nine switches would otherwise redraw all 32 lanes
        # nine times before the file is even on screen.
        blocked = self.annotations.blockSignals(True)
        try:
            for state in self.annotations.layer_states():
                on = wanted.get(state.id)
                if isinstance(on, bool):
                    self.annotations.set_layer(state.id, on)
        finally:
            self.annotations.blockSignals(blocked)
        self.redraw_annotations()

    def schedule_annotation_save(self) -> None:
        """Queue one settings write for the end of this turn of the loop.

        `save_setting` reads, updates and rewrites the whole settings file,
        and one click on a chip moves up to ten switches, so writing per
        switch would rewrite that file ten times for one gesture.
        """
        if self.annotation_save_pending or not self.annotations.loaded:
            return
        self.annotation_save_pending = True
        QTimer.singleShot(0, self.save_annotation_settings)

    def save_annotation_settings(self) -> None:
        """Write the layer and surface switches to the settings file.

        Every layer of the bundle gets an entry, so a layer switched off is
        remembered as off rather than coming back on at the next start.

        The F8 master is deliberately not saved.  It is a glance -- take the
        marks off the trace for a moment and put them back -- and one left
        off would come back as an audian that draws no annotations at all and
        says nothing about why.  Which layers to read is a working set the
        reader chose; whether to look at any of them right now is not.
        """
        self.annotation_save_pending = False
        if not self.annotations.loaded:
            return
        from .audian import save_setting

        save_setting(
            DataBrowser.ANNOTATION_SETTING,
            {
                "version": DataBrowser.ANNOTATION_SETTING_VERSION,
                "layers": {
                    state.id: bool(state.enabled)
                    for state in self.annotations.layer_states()
                },
                "surfaces": {
                    surface: bool(enabled)
                    for surface, _label, enabled in self.annotations.surface_states()
                },
            },
        )

    def rebuild_annotations(self) -> None:
        """React to a new (or cleared) table: rebuild items, chips and badge."""
        # the remembered set belongs to the bundle it was taken from
        self.annotation_layers_before_solo = None
        for overlay in self.annotation_overlays:
            if self.annotations.loaded:
                overlay.rebuild()
            else:
                overlay.clear()
        if self.control_panel is not None:
            self.control_panel.rebuild()
        self.build_annotation_chips()
        self.update_annotation_badge()
        self.redraw_annotations()

    def redraw_annotations(self) -> None:
        if self.control_panel is not None and self.control_panel.refresh():
            # the strip took or gave back pixels, and the lanes divide up
            # what is left of the pane
            self.adjust_layout(self.width(), self.height())
        for overlay in self.annotation_overlays:
            overlay.update_plot()
        self.update_annotation_chips()

    def annotation_keys(self) -> list:
        return self.annotations.active_ids()

    def step_annotation(self, forward: bool = True) -> None:
        """Centre the view on the next annotation in time.

        This is how the annotations get checked against the recording: step to
        an observed pulse, zoom in, and see whether the line sits on it.  Only
        the layers that are switched on are stepped through, so the step
        follows what is on screen.
        """
        if not self.annotations.loaded:
            self.notify("info", "no annotations loaded -- Ctrl+Shift+A opens a file")
            return
        if not self.annotation_keys():
            self.notify("warning", "no annotation layer is switched on")
            return
        trange = self.plot_ranges[Panel.times[0]]
        centre = 0.5 * (trange.r0[0] + trange.r1[0])
        found = self.annotations.step(centre, forward)
        if found is None:
            self.notify("info", "no further annotation in that direction")
            return
        layer, series, index = found
        time = mark_time(layer, series, index)
        window = trange.r1[0] - trange.r0[0]
        self.set_times(time - 0.5 * window, window)
        self.notify("info", describe_mark(layer, series, index))

    def annotation_under(self, time: float) -> str:
        """Describe what the pointer is on, for the readout.

        A span the pointer is *inside* is found by asking which spans cover
        the time (`SessionBundle.spans_at`), never by measuring to the
        nearest mark.  `nearest()` measures a span from its start, so at the
        midpoint of a 58 s localization run it reported the run the pointer
        was standing in as 29 s away -- which reads as "there is nothing
        here", in the feature's only textual answer.

        Both halves are reported when both exist, because they answer
        different questions: the span says where the pointer *is*, the
        nearest instant says what it is next to.  The spans that already
        answered are left out of the second question so the same layer
        cannot be reported twice, once rightly and once as far away.

        Every covering span carries its OWN counts, right after its own
        description (`marks_in`).  Spans nest -- a trial runs inside a
        localization run -- and one count list at the end of the line would
        have no stated subject: 312 detections inside a 1 s trial and inside
        the 58 s run around it are different measurements, and a reader who
        cannot tell which one is shown has been handed the wrong one half the
        time.
        """
        bundle = self.annotations.bundle
        if bundle is None or not self.annotations.drawable:
            return ""
        ids = self.annotation_keys()
        if not ids:
            return ""
        parts = []
        covering = bundle.spans_at(time, ids)
        for layer, index in covering:
            # Identity, then contents, then bounds -- in that order, because
            # the field is 627 px (about 78 characters) at a 1920 px window
            # and the line runs past it.  Whatever is last is what elision
            # eats, and the bounds are the part the reader needs least: the
            # span is drawn on screen with both its edges, so its extent is
            # already visible, while the counts are the reason the readout was
            # asked for.  Ordered the other way round -- bounds, then
            # contents -- the counts were cut off mid-number at every window
            # width this application is given.
            parts.append(
                f"{layer.name_of(index)}  {self.marks_in(layer, index)}"
                f"  {layer.bounds_of(index)}  inside"
            )
        rest = [i for i in ids if i not in {x.id for x, _i in covering}]
        found = bundle.nearest(time, rest) if rest else None
        if found is not None:
            layer, series, index = found
            delta = mark_time(layer, series, index) - time
            parts.append(
                f"{describe_mark(layer, series, index)}  (Δ {gap_text(delta)})"
            )
        return "   ·   ".join(parts)

    def marks_in(self, layer, index: int) -> str:
        """What the switched-on point layers hold inside one span.

        The stage-2 question of the field workflow -- *how many unexplained
        detections fell inside THIS trial, against a silence one* -- answered
        where the reader is already looking rather than in a panel they have
        to go and find.  On exp2 it reads out the asymmetry directly: the
        baseline trials carry 8.3 unexplained detections per second against
        1.27/s outside any trial, and 76 of them sit in one span.

        The counting is `SessionBundle.pulses_in` and nothing else.  It is two
        `searchsorted` calls per series against arrays that are already
        sorted; walking rows in Python here would put a per-row loop on the
        mouse-move path, where it runs on every pixel of every pan.  Measured
        on exp3 with all ten layers switched on -- 7863 unexplained
        detections, 4423 volley pulses, a 58 s run to stand inside -- the
        whole of `annotation_under` costs 0.042 ms a call.

        **Only the layers that are switched on are counted**, because the
        readout is a statement about what is on screen.  A count for a hidden
        layer is a number about something the reader cannot see, cannot step
        through and cannot check against the waveform -- and the layer
        toggles are how the reader narrows the question in the first place,
        so a total that ignored them would answer a question nobody asked.
        Solo a layer and this line counts that layer alone.

        Observed and predicted rows are never added together, for the same
        reason `pulses_in` keys its result by series: a bundle's predicted
        pulses are positions nothing in the recording confirms, and a total
        that mixed them would report a measurement that was never made.

        The layers are named by their own `short` word -- the same word on
        their chip -- and counted in the bundle's order, which is the order
        the chips are in: Sent before Heard.  No word here is this viewer's:
        `explained_by_log` is a fact the writer recorded, and what an
        unexplained detection *is* stays the reader's to decide.
        """
        bundle = self.annotations.bundle
        if bundle is None:
            return ""
        ids = self.annotation_keys()
        totals: dict[tuple[str, bool], int] = {}
        for key, (series, i0, i1) in bundle.pulses_in(layer, index, ids).items():
            point = bundle.get(key.split("#")[0])
            if point is None:
                continue
            observed = bool(point.series[series].observed)
            totals[(point.id, observed)] = totals.get((point.id, observed), 0) + (
                i1 - i0
            )
        # Grouped by what the count MEANS, not by which layer produced it.
        #
        # Listing the layers one after another put "Resting 1, Explained 1" in
        # a single comma list, which reads as one axis and is two: a pulse is
        # something the stimulator SENT, a detection is something the
        # recording HEARD, and "explained" is a property only the second kind
        # can have.  A reader who saw a resting pulse with no explained
        # detection beside it concluded, reasonably, that the viewer was
        # contradicting itself.  It was not -- those pulses were emitted and
        # never heard back, which is exactly why their detected_time_s is
        # empty -- but nothing on the line said so.
        #
        # `sent`, `not heard`, `heard` and `unexplained` say it.  The grouping
        # is read off Layer.track, the same axis the chip rows are captioned
        # by, so it is the data model's own vocabulary rather than this
        # method's opinion.
        sent = notheard = 0
        for point in bundle:
            if point.track != TRACK_PULSES:
                continue
            sent += totals.get((point.id, True), 0)
            notheard += totals.get((point.id, False), 0)
        heard = totals.get((LAYER_DET_EXPLAINED, True), 0)
        unexplained = totals.get((LAYER_DET_UNEXPLAINED, True), 0)

        parts = []
        if sent or notheard:
            # the parenthetical is the whole answer to "why is sent > heard"
            gap = f" ({notheard} not heard)" if notheard else ""
            parts.append(f"sent {sent + notheard}{gap}")
        if heard or self.annotations.is_enabled(LAYER_DET_EXPLAINED):
            parts.append(f"heard {heard}")
        if unexplained or self.annotations.is_enabled(LAYER_DET_UNEXPLAINED):
            parts.append(f"unexplained {unexplained}")
        # anything that is neither sent nor heard -- session events, and any
        # layer a future bundle adds -- keeps its own name rather than being
        # forced into an axis it is not on
        for point in bundle:
            if point.track in (TRACK_PULSES,) or point.id in (
                LAYER_DET_EXPLAINED,
                LAYER_DET_UNEXPLAINED,
            ):
                continue
            for observed in (True, False):
                count = totals.get((point.id, observed))
                if count:
                    name = point.short if observed else f"{point.short} pred"
                    parts.append(f"{name} {count}")
        if parts:
            return ", ".join(parts)
        # Three different sentences, and a reader who cannot tell them apart
        # cannot tell an empty trial from a switched-off layer.
        if not any(getattr(bundle.get(i), "kind", "") == KIND_POINT for i in ids):
            return "nothing -- no point layer is switched on"
        return "no mark of the layers on screen"

    def show_annotation_under(self, time: float) -> None:
        """Name the annotation nearest the pointer in the parameter bar.

        Elided to the width the row already has, never wider.  This readout
        changes on every mouse move, and a label that asks for the width of
        its longest string would relayout the whole parameter bar under the
        pointer -- the same failure the status bar readouts were rebuilt to
        stop.  The full line stays available as the tool tip.
        """
        label = self.annotation_hoverw
        if label is None:
            return
        text = self.annotation_under(time)
        label.setToolTip(text)
        metrics = theme.mono_metrics(theme.SIZE_SMALL_PT)
        label.setText(metrics.elidedText(text, Qt.ElideRight, max(label.width(), 1)))

    # -- the parameter bar group --

    def setup_annotation_group(self) -> "ParameterGroup":
        """Build the Fixed labels group of the parameter bar.

        Always built, even with nothing loaded: the group is the only place
        that states where the annotations came from and whether their
        alignment was ever validated, and a control that appears and
        disappears is one nobody learns to look at.  It is hidden while no
        table is loaded and shown the moment one is.
        """
        group = ParameterGroup(DataBrowser.FIXED_TAB, self.parambar, caption=False)

        self.annotation_sourcew = QLabel("—", self.parambar)
        self.annotation_sourcew.setFont(theme.font_mono(theme.SIZE_SMALL_PT))
        theme.tint(self.annotation_sourcew, "fg")
        self.annotation_badgew = QLabel("", self.parambar)
        self.annotation_badgew.setFont(theme.font_mono(theme.SIZE_SMALL_PT, bold=True))
        self.annotation_badgew.setAlignment(Qt.AlignCenter)
        loadw = QToolButton(self.parambar)
        loadw.setText("Load…")
        loadw.setFont(theme.font_ui(theme.SIZE_SMALL_PT))
        loadw.setToolTip(
            "Read a session bundle -- a *_metadata.toml and its CSVs  "
            "(Ctrl+Shift+A).\nThese labels are fixed: audian never writes them."
        )
        loadw.setFixedHeight(theme.CHIP_HEIGHT)
        loadw.clicked.connect(self.open_annotations)
        # One cell, not three.  The pointer readout further down this group
        # asks for the leftover width, which puts the stretch on column 1 --
        # so a badge in column 2 and a button in column 3 ended up against
        # the far edge of a page that is now the whole bar rather than a
        # fifth of it.  Packed into one container with a trailing stretch
        # they stay beside the name they belong to at any width.
        sourcebox = QWidget(self.parambar)
        sourcerow = QHBoxLayout(sourcebox)
        sourcerow.setContentsMargins(0, 0, 0, 0)
        sourcerow.setSpacing(theme.S6)
        sourcerow.addWidget(self.annotation_sourcew)
        sourcerow.addWidget(self.annotation_badgew)
        sourcerow.addWidget(loadw)
        sourcerow.addStretch(1)
        group.add_row("Source", "", sourcebox)

        # Where the marks are drawn: the master switch and one chip per
        # surface.  Separate from the layer chips below on purpose -- "which
        # events" and "which panels" are different questions, and putting
        # them in one strip would make the answer to either hard to read off.
        self.restore_annotation_surfaces()
        wherebox = QWidget(self.parambar)
        where = QHBoxLayout(wherebox)
        where.setContentsMargins(0, 0, 0, 0)
        where.setSpacing(theme.S4)
        self.annotation_showw = QToolButton(wherebox)
        self.annotation_showw.setText("Show")
        self.annotation_showw.setCheckable(True)
        self.annotation_showw.setChecked(True)
        self.annotation_showw.setFont(theme.font_ui(theme.SIZE_SMALL_PT))
        self.annotation_showw.setToolTip(
            "Show the fixed labels at all  (F8).\n"
            "The chips beside it choose which panels they reach."
        )
        self.annotation_showw.toggled.connect(self.annotations.set_visible)
        self.annotation_showw.setFixedHeight(theme.CHIP_HEIGHT)
        where.addWidget(self.annotation_showw)
        self.annotation_surfacew = {}
        for surface, label, enabled in self.annotations.surface_states():
            chip = QToolButton(wherebox)
            chip.setText(label)
            chip.setCheckable(True)
            chip.setChecked(enabled)
            chip.setFont(theme.font_ui(theme.SIZE_SMALL_PT))
            chip.setFixedHeight(theme.CHIP_HEIGHT)
            chip.setToolTip(ANNOTATION_SURFACE_TIPS.get(surface, ""))
            chip.toggled.connect(
                lambda on, name=surface: self.set_annotation_surface(name, on)
            )
            where.addWidget(chip)
            self.annotation_surfacew[surface] = chip
        # left packed: without this the HBox shares the leftover between the
        # chips, and on a full-width page they drift apart across the window
        where.addStretch(1)

        group.add_row("Show", "F8", wherebox)

        # The pointer readout gets a row of its own, spanning the group.
        #
        # It rode at the end of the Show row to save a row of the parameter
        # bar, which is 24 px off every lane in the stack -- a real cost, and
        # the right trade while the readout was one clause.  It is not one
        # clause any more: since the trial summary landed the line runs to
        # ~227 characters, and on the Show row it was elided long before the
        # counts began.  The counts are the whole reason the readout was
        # asked for -- they answer "how many unexplained detections fell
        # inside THIS trial", the question the second stage of the field
        # workflow is made of.  On its own row it now gets 1097 px of a
        # 1200 px window, measured, which is most of the line.
        self.annotation_hoverw = QLabel("", self.parambar)
        self.annotation_hoverw.setFont(theme.font_mono(theme.SIZE_SMALL_PT))
        self.annotation_hoverw.setWordWrap(False)
        # Ignored, not Preferred: the label takes the width the row has and
        # never asks for more, so what the pointer is near cannot change the
        # geometry of the bar (see show_annotation_under).
        self.annotation_hoverw.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        theme.tint(self.annotation_hoverw, "fg.muted")
        group.add_row("Pointer", "", self.annotation_hoverw)

        # One chip per layer, in the two captioned rows of
        # ANNOTATION_CHIP_ROWS.  The chips are the legend as well as the
        # toggle, so they are never elided away to glyphs; what gives instead
        # is the count, which lives in the tool tip and on the menu entry.
        self.annotation_rowboxes = []
        for index, (caption, tip, _tracks) in enumerate(ANNOTATION_CHIP_ROWS):
            box = QWidget(self.parambar)
            strip = QHBoxLayout(box)
            strip.setContentsMargins(0, 0, 0, 0)
            strip.setSpacing(theme.S4)
            if index == 0:
                # The way back from a solo sits ahead of the first chip, at
                # the corner the eye reaches first, because a solo is one
                # click away from every chip and this is the one control that
                # undoes all of them.
                self.annotation_allw = QToolButton(box)
                self.annotation_allw.setText("All")
                self.annotation_allw.setFont(theme.font_ui(theme.SIZE_SMALL_PT))
                self.annotation_allw.setFixedHeight(theme.CHIP_HEIGHT)
                self.annotation_allw.clicked.connect(self.show_all_annotation_layers)
                strip.addWidget(self.annotation_allw)
            strip.addStretch(1)
            placed = group.add_row(caption, "", box)
            placed[0].setToolTip(tip)
            self.annotation_rowboxes.append(box)

        self.annotation_group = group
        # No setVisible, and no disabled tab either.  An unloaded
        # Annotations page is not blank: it holds the source line, the Show
        # chips and the Load... button, which is the only route into the
        # feature for a reader who does not know Ctrl+Shift+A.  Disabling
        # the tab would hide the one control that fixes the state it is
        # complaining about.
        return group

    def build_annotation_chips(self) -> None:
        """Rebuild the per-layer toggle chips.

        The chips double as the legend: each one is drawn with the pen or the
        brush the overlay itself uses, so a layer's colour and whether it is a
        span or a train of instants can be read off the bar rather than
        remembered.  Toggling is the primary interaction here -- the reader
        looks at one or two layers at a time -- so every layer gets its own
        chip and nothing is folded into a facet.

        Built from the bundle's own layers, never from a list in this file:
        the chips, the menu entries and the saved switches are then the same
        set by construction and cannot drift apart.
        """
        if not self.annotation_rowboxes:
            return
        for chip in self.annotation_chips:
            chip.parent().layout().removeWidget(chip)
            chip.setParent(None)
            chip.deleteLater()
        self.annotation_chips = []
        self.annotation_layer_chips = {}
        if not self.annotations.loaded:
            self.update_annotation_alert()
            return

        unvalidated = self.annotations.unvalidated
        bundle = self.annotations.bundle
        for state in self.annotations.layer_states():
            box = self.annotation_rowboxes[annotation_chip_row(bundle[state.id].track)]
            chip = self.annotation_chip(box, state.short, state.enabled)
            if state.kind == KIND_SPAN:
                chip.setIcon(
                    span_icon(
                        state.color,
                        self.annotations.fill_alpha(state.id, SURFACE_TRACE),
                        unvalidated,
                    )
                )
            elif state.kind == KIND_POINT:
                chip.setIcon(legend_icon(state.color, True, unvalidated))
            else:
                chip.setIcon(swatch_icon(state.color))
            chip.setToolTip(self.annotation_chip_tip(state))
            # clicked, not toggled: the click is a solo (or an extend under a
            # modifier), so the check state is pushed back from the layer
            # afterwards rather than being what the click means.
            chip.clicked.connect(
                lambda _checked, i=state.id: self.annotation_chip_clicked(i)
            )
            self.annotation_layer_chips[state.id] = chip
            self.annotation_chips.append(chip)
            layout = box.layout()
            layout.insertWidget(layout.count() - 1, chip)

        # A bundle just loaded is the one gesture where raising the tab is
        # right: the reader asked for that data, and the source line, the
        # badge and the layer chips they asked for are all on this page.
        # Not on `open()`, which runs for every file and would override the
        # tab the reader chose.
        self.raise_parameter_tab(DataBrowser.FIXED_TAB)
        self.update_annotation_alert()
        # The group grew or shrank by a row of chips, and equalize() froze
        # every frame height when the bar was built.  Deferred by one turn
        # of the event loop rather than run here: widgets added a moment ago
        # do not reach their parent's size hint until Qt has processed the
        # layout invalidation, and measuring before that leaves the group a
        # row-spacing short and clips the last row of chips.
        QTimer.singleShot(0, self.equalize_parameter_bar)

    # --- the parameter bar's tabs ----------------------------------------

    def parameter_tab_settings(self) -> dict:
        """The saved tab choice, or {} when it is from another build."""
        from .audian import settings

        saved = settings().get(DataBrowser.PARAM_TAB_SETTING)
        if not isinstance(saved, dict):
            return {}
        if saved.get("version") != DataBrowser.PARAM_TAB_SETTING_VERSION:
            log.warning(
                "ignoring %s settings written in version %r; this audian "
                "writes version %d",
                DataBrowser.PARAM_TAB_SETTING,
                saved.get("version"),
                DataBrowser.PARAM_TAB_SETTING_VERSION,
            )
            return {}
        return saved

    def restore_parameter_tab(self) -> None:
        """Open the bar on the tab this reader last chose.

        By NAME and not by index: the Envelope group only exists when the
        data has an envelope, and the Filter group only when it has a
        filtered trace, so the same index is a different tab on a different
        recording.  An unknown name falls back to the first tab rather than
        to nothing.
        """
        if self.param_tabs is None:
            return
        wanted = self.parameter_tab_settings().get("tab")
        if isinstance(wanted, str) and self.param_tabs.show_group(wanted):
            return
        self.param_tabs.show_index(0)

    def parameter_tab_changed(self, title: str) -> None:
        """Everything that has to happen when a different group is raised.

        A `QStackedLayout` gives geometry to the current page only, so a
        page that has never been raised has a stale width -- and three
        things in this bar size themselves off their own `width()`: the
        Labels file row and the annotation pointer readout elide to it, and
        the category chip strip folds to it.  Measured on a page that had
        never been current: 100 px against the 1162 it gets once raised, so
        without this the file row is elided to a sliver and every category
        folds into the `+N` menu.

        `apply_resize` already makes the same calls after a window resize,
        for the same reason.
        """
        self.save_parameter_tab(title)
        self.update_label_status()
        if self.label_chipbox is not None:
            self.label_chipbox.relayout()
        self.reelide_annotation_hover()

    def save_parameter_tab(self, title: str) -> None:
        """Remember the tab, unless nothing has actually changed.

        Written on a reader's pick, never on restore and never at build:
        `save_setting` reads, updates and rewrites the whole settings file,
        and a browser that wrote its own default at construction would
        overwrite the choice made in the window beside it.
        """
        if not title or title == self._param_tab_saved:
            return
        self._param_tab_saved = title
        from .audian import save_setting

        save_setting(
            DataBrowser.PARAM_TAB_SETTING,
            {
                "version": DataBrowser.PARAM_TAB_SETTING_VERSION,
                "tab": title,
            },
        )

    def raise_parameter_tab(self, title: str) -> None:
        """Bring one group to the front, if the bar has been built."""
        if self.param_tabs is not None:
            self.param_tabs.show_group(title)

    def reelide_annotation_hover(self) -> None:
        """Re-elide the pointer readout to the width it has now.

        Off its own tool tip, which is where `show_annotation_under` keeps
        the full line.  The readout is rewritten on every mouse move, so it
        heals itself the moment the pointer moves -- this is only for the
        text already on screen when the tab is raised.
        """
        label = self.annotation_hoverw
        if label is None:
            return
        metrics = theme.mono_metrics(theme.SIZE_SMALL_PT)
        label.setText(
            metrics.elidedText(label.toolTip(), Qt.ElideRight, max(label.width(), 1))
        )

    def update_label_alert(self) -> None:
        """Mark the Labels tab when its file row is saying something bad.

        READ-ONLY and SAVE FAILED are the two states in this bar where a
        page nobody is looking at costs the reader their work rather than a
        glance, so they get a mark on the tab as well as the row.
        """
        if self.param_tabs is None:
            return
        bad = bool(self.labels.blocked or self.label_error)
        self.param_tabs.set_alert(
            DataBrowser.EDITABLE_TAB, bad, self.label_status_text() if bad else ""
        )

    def update_annotation_alert(self) -> None:
        """Mark the Annotations tab when the badge is not plain agreement.

        A bundle fitted to another recording draws nothing at all, so the
        badge is the only place on screen that says so.
        """
        if self.param_tabs is None:
            return
        bad, tip = False, ""
        if self.annotations.loaded:
            # (text, token, tooltip); the token is the colour the badge is
            # painted in, and it is `danger` for WRONG RECORDING and for an
            # UNVALIDATED fit, `accent` for a fit with warnings recorded
            # against it, and `success` when it is simply validated.
            _text, token, tip = self.annotations.badge()
            bad = token in ("danger", "accent")
        self.param_tabs.set_alert(DataBrowser.FIXED_TAB, bad, tip if bad else "")

    def equalize_parameter_bar(self) -> None:
        """Re-level the parameter bar's frames after a group changed size."""
        if self.param_groups:
            ParameterGroup.equalize(self.param_groups)

    def annotation_chip_tip(self, state) -> str:
        """What one layer chip says when the pointer rests on it.

        The count lives here rather than on the chip.  Ten chips carrying
        their counts measured 1514 px against the 678 px a group had when
        every group was on screen at once.  The page is the whole bar now
        (1183 px measured), so it is closer than it was -- and the count
        stays in the tip because it is the one part of a chip that can move
        without losing the legend: a layer with no rows still reads
        `0 in session`, which is the difference between a layer that is
        empty and one that is off.
        """
        count = f"{state.count} in session"
        drawn = (
            ""
            if state.kind in (KIND_POINT, KIND_SPAN)
            else "\nDrawn by the control panel, not over the lanes."
        )
        return (
            f"{state.label} -- {count}\n{state.tip}{drawn}\n"
            f"Click to show this layer alone; ctrl- or shift-click to switch "
            f"just this one on or off."
        )

    def annotation_chip(self, parent: QWidget, text: str, checked: bool) -> QToolButton:
        chip = QToolButton(parent)
        chip.setText(text)
        chip.setCheckable(True)
        chip.setChecked(bool(checked))
        chip.setFont(theme.font_mono(theme.SIZE_SMALL_PT))
        chip.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        chip.setFixedHeight(theme.CHIP_HEIGHT)
        # exactly the pixmap size: a legend icon scaled by even one pixel
        # loses the hairline that says whether the line is solid or broken
        chip.setIconSize(QSize(LEGEND_W, LEGEND_H))
        return chip

    def update_annotation_chips(self) -> None:
        """Keep the Show button and the surface chips in step with the layer.

        They can be driven from the menu and from a key as well as from the
        bar, and a checkbox that disagrees with what is on screen is worse
        than no checkbox.
        """
        if self.annotation_showw is None:
            return
        blocked = self.annotation_showw.blockSignals(True)
        self.annotation_showw.setChecked(self.annotations.visible)
        self.annotation_showw.blockSignals(blocked)
        # A solo switches nine layers at once from a key or the menu, so the
        # chips have to be told: a checked chip over a layer that is not on
        # screen is worse than no chip.
        for layer_id, chip in self.annotation_layer_chips.items():
            blocked = chip.blockSignals(True)
            chip.setChecked(self.annotations.layers.get(layer_id, False))
            chip.blockSignals(blocked)
        for surface, chip in self.annotation_surfacew.items():
            # the surface chips stay usable while the master is off: they say
            # where the overlay *would* go, and dimming them would hide that
            blocked = chip.blockSignals(True)
            chip.setChecked(self.annotations.surfaces.get(surface, True))
            chip.blockSignals(blocked)
        if self.annotation_allw is not None:
            # Enabled either way, and it says how many layers are hidden: a
            # disabled button gets no mouse events and so no tool tip, which
            # is exactly the state a reader would want the sentence for.
            hidden = sum(1 for on in self.annotations.layers.values() if not on)
            self.annotation_allw.setToolTip(
                f"Switch all {hidden} hidden layers back on  (Shift+F8)"
                if hidden
                else "Every layer of this bundle is switched on  (Shift+F8)"
            )
        window = self.window()
        if window is not None and hasattr(window, "sync_annotation_actions"):
            window.sync_annotation_actions(self)

    def update_annotation_badge(self) -> None:
        """Restate where the annotations came from and how far to trust them."""
        if self.annotation_sourcew is None:
            return
        bundle = self.annotations.bundle
        if bundle is None:
            self.annotation_sourcew.setText("—")
            self.annotation_sourcew.setToolTip("")
            self.annotation_badgew.setText("")
            self.annotation_badgew.setVisible(False)
            if self.annotation_hoverw is not None:
                self.annotation_hoverw.setText("")
            return
        metrics = theme.mono_metrics(theme.SIZE_SMALL_PT)
        session_id = bundle.meta.session_id or "session"
        name = metrics.elidedText(session_id, Qt.ElideMiddle, 20 * theme.S8)
        # The channel goes in the label, not only in the tool tip: the fit is
        # per channel, and which one it was made against is the difference
        # between an annotation that was checked against what is on screen
        # and one that was checked against the lane below it.
        channel = bundle.meta.alignment.recording_channel
        fitted = f"  fit ch {channel:02d}" if channel is not None else "  fit ch ??"
        self.annotation_sourcew.setText(name + fitted)
        source = bundle.ref.metadata_path if bundle.ref is not None else session_id
        self.annotation_sourcew.setToolTip(
            f"{source}\n{bundle.summary()}\n"
            + (
                f"The alignment was fitted against channel {channel} of the "
                "recording.\nIt is drawn over every channel, because the "
                "channels share one clock,\nbut only that one was checked."
                if channel is not None
                else "The header does not say which channel the fit was made against."
            )
        )
        text, token, tip = self.annotations.badge()
        if self.annotation_coverage is not None:
            # Not "WRONG RECORDING": it is the right recording, and saying so
            # wrongly is how a reader learns to click past the badge.  What is
            # wrong is that only part of it is open, and the count is the
            # whole diagnosis -- 1 OF 4 is a state a reader can act on.
            coverage = self.annotation_coverage
            text = f"{len(coverage.opened)} OF {len(coverage.declared)} FILES"
            token = "danger"
            tip = coverage.message()
        # The reader's own per-region residuals ride on the badge, because
        # "how far can I trust what is on screen" is the question the badge
        # exists to answer and a global median does not answer it.
        residuals = self.residual_tip(bundle)
        if residuals:
            tip += "\n" + residuals
        self.annotation_badgew.setText(text)
        self.annotation_badgew.setToolTip(tip)
        self.annotation_badgew.setVisible(bool(text))
        self.annotation_badgew.setStyleSheet(
            f"color: {theme.token(token)};"
            f"background: {theme.token('bg.surface')};"
            f"border: {theme.HAIRLINE}px solid {theme.token(token)};"
            f"border-radius: {theme.RADIUS_CONTROL}px;"
            f"padding: {theme.S2}px {theme.S6}px;"
        )

    def update_borders(self, rect=None):
        """Frame the current channel only.

        A 4px grey box around all sixteen channels conveys nothing; a
        1px primary frame around the one current channel does. The plot
        additionally bolds its channel label, so the cue is not colour
        alone.
        """
        for c in range(len(self.figs)):
            # Inset by half the pen width.  A rect at (0, 0, w, h) puts its
            # edges exactly on the figure's boundary, and a centred pen then
            # has half its width outside: the bottom and right strokes were
            # clipped away entirely and the frame read as an open bracket.
            inset = float(theme.HAIRLINE)
            self.borders[c].setRect(
                inset,
                inset,
                max(0.0, self.figs[c].size().width() - 2 * theme.HAIRLINE),
                max(0.0, self.figs[c].size().height() - 2 * theme.HAIRLINE),
            )
            self.borders[c].setVisible(c == self.current_channel)
        # a figure's device range only changes when its geometry does, which
        # is exactly when the shared axis below it needs re-aligning:
        self.align_time_axis()
        self.update_current_plot()

    def showEvent(self, event):
        if self.data is None:
            return
        with self.updating():
            self.plot_ranges.set_ranges()
            self.data.set_need_update()
            self.panels.update_plots()
            self.plot_ranges.set_powers()

    def eventFilter(self, obj, event):
        if (
            self.stack_area is not None
            and obj is self.stack_area.viewport()
            and event.type() == QEvent.Resize
            and self.show_channels
        ):
            self.update_stretches(event.size().height())
            self.resize_timer.start(100)
        return super().eventFilter(obj, event)

    def resizeEvent(self, event):
        """Only the cheap part runs per event; the layout is debounced.

        Hyprland delivers an uncoalesced stream of resize events while a
        window edge is dragged, and `adjust_layout()` repaints every
        channel scene.
        """
        if self.show_channels is None or len(self.show_channels) == 0:
            return
        self.update_stretches(event.size().height())
        self.resize_timer.start(100)

    def apply_resize(self) -> None:
        # One pass is enough: the lane height is solved in integers from
        # the viewport and the axis row's own height, not measured off the
        # previous layout, so there is nothing left to converge.
        self.adjust_layout(self.width(), self.height())
        self.data.set_need_update()
        # The Labels file row elides itself to the width the row has, and
        # is otherwise redrawn only when a label changes -- so without this
        # a widened window keeps the text cut to the old column, and a
        # narrowed one keeps a line that no longer fits.
        self.update_label_status()

    def lane_axes(self, plot: pg.PlotItem, values: bool, left_width: int) -> None:
        """Strip the axis chrome of one lane that the stack now shares.

        Three things go, and none of them carried information.

        The x tick *dashes*: `maxTickLength` is negative in pyqtgraph, so
        every lane painted a row of inward ticks across its own data area -
        thirty rows of dotted texture in a 500 px stack, competing with the
        traces.  The time values come from the one shared axis below the
        stack now, so the per-lane x axes keep neither values nor ticks
        (their height collapses to zero, which is what makes every lane
        exactly as tall as every other one).

        The unlabelled y ladder: once a row is shorter than
        `TICK_VALUES_MIN_HEIGHT` pyqtgraph stops drawing the tick *values*
        but keeps drawing the tick marks, so each of sixteen lanes carried
        56 px of ticks with no numbers beside them.  Ticks without values
        are dropped with the values.

        The reserved y gutter: `left_width` is a stack-wide decision, so
        that every lane's view box starts at the same x and the traces stay
        comparable across channels.

        Every write is compared first.  `AxisItem.setStyle` drops the
        cached picture, re-measures the axis and schedules a repaint
        whatever it was handed, so a layout pass that re-states the chrome
        every lane already has repaints every axis in the stack for nothing
        - and this now runs on every step of a drag as well as on every
        resize.
        """
        for name in ("top", "bottom"):
            if name not in plot.axes:
                continue
            axis = plot.getAxis(name)
            set_axis_style(axis, showValues=False, tickLength=0)
            if axis.label.isVisible():
                axis.showLabel(False)
        if "right" in plot.axes:
            set_axis_style(plot.getAxis("right"), showValues=False, tickLength=0)
        if "left" in plot.axes:
            axis = plot.getAxis("left")
            if axis.width() != left_width:
                axis.setWidth(left_width)
            # negative: pyqtgraph points ticks into the plot, and that is
            # where a y tick belongs as long as it is labelled.
            set_axis_style(
                axis, showValues=values, tickLength=-theme.S4 if values else 0
            )

    @staticmethod
    def row_shows_tick_values(row_height: float) -> bool:
        """Does a row this tall leave its view box the height for values?

        The threshold is the view box's, and a view box is
        `theme.PLOT_FRAME_HEIGHT` px shorter than its row.
        `TimePlot._view_resized` applies it there and hides the caption that
        stands in for the values when it fails, so the browser has to ask
        the same question of the same number.  Asked of the row instead, the
        two disagreed over a 2 px band: a row of exactly 48 px drew
        amplitude tick values while the plot that owns them, seeing 46, had
        already hidden its caption - and that band is exactly where an
        over-dragged boundary parks.
        """
        return row_height - theme.PLOT_FRAME_HEIGHT >= TICK_VALUES_MIN_HEIGHT

    def trace_plot(self, channel: int) -> Optional[TimePlot]:
        """The channel's first visible trace plot, or None."""
        for panel in self.panels.values():
            if not panel.is_trace() or len(panel.axs) <= channel:
                continue
            if panel.axs[channel].isVisible():
                return panel.axs[channel]
        return None

    def time_plot(self, channel: int) -> Optional[TimePlot]:
        """Any of the channel's visible plots that carries the shared x.

        The trace first, because it is the one the stack is usually read
        off; the spectrogram when the traces are off, because then it is the
        only plot in the lane and every lane shares one time range anyway.

        `trace_plot` is not that: it answers None once F2 has hidden every
        trace, and the shared time axis, which asked it, went on mapping its
        ticks through the *hidden* trace's view box.  Measured on two
        channels with the traces off: the hidden trace's view box is 1037 px
        wide while the spectrogram the ticks are drawn under is 981, so the
        axis was scaled to a width nothing on screen had.
        """
        for prefer_trace in (True, False):
            for panel in self.panels.values():
                if panel.is_power() or panel.is_spacer():
                    continue
                if panel.is_trace() != prefer_trace or len(panel.axs) <= channel:
                    continue
                if panel.axs[channel].isVisible():
                    return panel.axs[channel]
        return None

    def spectrogram_plots(self):
        """Every spectrogram plot of the stack, one per channel.

        The panels themselves, not the lanes they happen to be in: mean mode
        has to reach the hidden ones too, to take the average back off them.
        """
        for panel in self.panels.values():
            if panel.is_spectrogram() and not panel.is_power():
                for ax in panel.axs:
                    yield ax

    def spectrogram_channels(self, channels: list[int]) -> list[int]:
        """Channels that get a spectrogram row.

        Sixteen spectrogram stripes of 32px each are unreadable and the
        rotated frequency label overprints the ticks. Above a handful of
        visible channels the spectrogram collapses onto a single focused
        panel that follows the current channel.

        That collapse is a *fallback*, and it only makes sense while there
        is a trace to fall back on.  With the traces off the spectrogram is
        the only thing a lane has left to draw, so collapsing left fifteen
        of sixteen lanes drawing nothing at all -- the reader pressed F2 to
        say "spectrograms only" and got one spectrogram and fifteen strips
        of background.  So every visible channel keeps its own, and the
        stack scrolls rather than going blank: measured at 1200x900,
        sixteen readable spectrograms are 1952 px of stack in a 500 px
        viewport, four lanes on screen at a time.  Scrolling is not a cost
        this introduces - the stack already scrolls 116 px at four channels
        and 424 px at six with the spectrograms on.
        """
        if self.show_specs <= 0 or not channels:
            return []
        if not self.show_traces:
            return list(channels)
        row_height = self.height() / max(1, len(channels))
        if hasattr(SpectrogramPlot, "can_render"):
            fits = SpectrogramPlot.can_render(row_height)
        else:
            fits = (
                len(channels) <= DataBrowser.MAX_SPECTROGRAM_CHANNELS
                and row_height >= theme.SPECTROGRAM_MIN_HEIGHT
            )
        if fits:
            return list(channels)
        focus = self.current_channel
        if focus not in channels:
            focus = channels[0]
        return [focus]

    def lane_geometry(self, height: int) -> tuple[int, int, list[int], bool]:
        """Solve the stack layout exactly, in integers.

        Returns ``(lane_h, axis_h, spec_channels, dense)``.

        `lane_h` is ``floor((available - spectrograms) / n)`` and every
        lane gets exactly that, no more and no less.  The remainder - at
        most ``n - 1`` px - goes into one spacer row at the bottom of the
        stack.  Nothing here is measured off a previous layout pass, so it
        does not converge, it just holds.

        The lanes shrink towards `theme.CHANNEL_DENSE_HEIGHT` before the
        stack starts to scroll, because sixteen electrodes are meant to be
        compared side by side rather than half a screen apart.
        """
        channels = self.visible_channels()
        axis_h = self.time_axis_height()
        # the viewport already excludes the axis strip, which lives below
        # the scroll area; the fallback height does not:
        available = height - axis_h
        if self.stack_area is not None:
            viewport = self.stack_area.viewport().height()
            if viewport > 0:
                available = viewport
        spec_channels = self.spectrogram_channels(channels)
        spec_total = theme.SPECTROGRAM_MIN_HEIGHT * len(spec_channels)
        n = max(1, len(channels))
        # `theme.CHANNEL_DENSE_HEIGHT` is the shortest row this application
        # still calls a readable *trace*; with the traces off there is no
        # trace in the lane to keep readable, and the floor is only what
        # `pyqtgraph.PlotItem` spends on its own margins.  Measured: a
        # 122 px row draws a 120 px spectrogram, a 121 px row draws 119.
        # Holding the trace's floor with no trace in the lane cost four
        # channels a scrollbar for 29 px of spectrogram nobody asked for.
        floor = (
            theme.CHANNEL_DENSE_HEIGHT if self.show_traces else theme.PLOT_FRAME_HEIGHT
        )
        lane_h = int(max(floor, (available - spec_total) // n))
        if self.maximized_channel is not None:
            lane_h = max(lane_h, theme.CHANNEL_MIN_HEIGHT)
        # a lane this short cannot carry tick values - see TimePlot, which
        # drops them at the same threshold - so it carries none of the
        # chrome that goes with them either:
        dense = lane_h < TICK_VALUES_MIN_HEIGHT and not spec_channels
        return lane_h, axis_h, spec_channels, dense

    def update_stretches(self, height: int) -> None:
        """Give every lane the same, exact, integer height.

        Cheap enough to run on every resize event: it only touches grid row
        heights, not the plot scenes.

        Every visible row is *fixed* to `lane_geometry()`'s lane height and
        every stretch factor is zero, so the layout has nothing left to
        hand out on its own.  Leftover pixels distributed by stretch are
        what made the lane pitch wobble between 31 and 38 px down a stack
        of sixteen, and a reader who cannot count rows cannot find a
        channel.  The remainder lives in one spacer row at the bottom.
        """
        if self.stack_grid is None:
            return
        channels = self.visible_channels()
        if not channels:
            return
        if self.stack_area is not None and self.stack_area.viewport().height() <= 1:
            # not laid out yet - the event filter will call us again with a
            # real height rather than freeze a nonsense ratio now
            return
        lane_h, axis_h, spec_channels, dense = self.lane_geometry(height)
        self.lane_height = lane_h
        for c in range(self.data.channels):
            if c not in channels:
                self.stack_grid.setRowStretch(c, 0)
                self.stack_grid.setRowMinimumHeight(c, 0)
                continue
            row_h = lane_h
            if c in spec_channels:
                row_h += theme.SPECTROGRAM_MIN_HEIGHT
            self.stack_grid.setRowStretch(c, 0)
            self.stack_grid.setRowMinimumHeight(c, row_h)
            self.figs[c].setFixedHeight(row_h)
            self.rail_rows[c].set_expanded(
                c == self.current_channel and row_h >= theme.CHANNEL_MIN_HEIGHT
            )
        if self.taxis_strip is not None:
            self.taxis_fig.setFixedHeight(axis_h)
            self.taxis_strip.setFixedHeight(axis_h)
        # one place for the remainder, and one only:
        self.stack_grid.setRowStretch(self.stack_spacer_row, 1)
        # Every lane height in this application is set right here, so this is
        # the one place a lane can be pushed off the bottom of the viewport,
        # and therefore the one place worth asking whether the focused one
        # was - see `show_focused_lane`.  Hanging it off the two gestures
        # that seemed to need it instead left four other routes to a stack
        # taller than its viewport without it: solo, mute, maximise and
        # move_channel through `apply_channel_visibility`, show_channel and
        # hide_deselected_channels through `set_channels`, the wrapping
        # branch of the arrow keys, and dragging the window shorter.  On
        # sixteen channels with the traces *on*, one solo and un-solo of the
        # focused lane was enough to leave the stack's only spectrogram off
        # the bottom of the screen: the headline bug through a side door.
        if not self.scroll_focus_pending:
            self.scroll_focus_pending = True
            QTimer.singleShot(0, self.show_focused_lane)

    def time_axis_height(self) -> int:
        """Height of the stack's one shared time axis row.

        The axis knows its own height: a pyqtgraph `AxisItem` pins its
        minimum and maximum height to its tick text, its label and its tick
        length whenever any of those change.  Nothing in it depends on the
        lane heights, so this is a plain number that holds from the first
        layout pass - unlike the old measurement, which compared the bottom
        figure's chrome against another channel's and therefore needed the
        very layout it was being used to compute.
        """
        floor_h = (
            theme.mono_metrics(theme.SIZE_SMALL_PT).height()
            + theme.ui_metrics(theme.SIZE_SMALL_PT).height()
            + theme.S8
        )
        if self.taxis is None:
            return int(floor_h)
        return int(max(floor_h, round(self.taxis.minimumHeight())))

    def link_time_axis(self) -> None:
        """Point the shared time axis at a lane's view box.

        All lanes share one x range, so any visible one will do; it has to
        be a *visible* one, because a hidden view box has no width and the
        axis maps its ticks through the view it is linked to.
        """
        if self.taxis is None:
            return
        channels = self.visible_channels()
        if not channels:
            return
        plot = self.time_plot(channels[0])
        if plot is None:
            return
        view = plot.getViewBox()
        if self.taxis.linkedView() is not view:
            self.taxis.linkToView(view)
        self.taxis.setRange(*view.viewRange()[0])
        # the control panel shares this one x, for the same reason and off the
        # same lane: one link, so it can never be a frame behind the axis
        if self.control_panel is not None:
            self.control_panel.link_view(view)

    # --- the trace / spectrogram split ------------------------------------

    def visible_trace_panels(self, channel: int) -> int:
        """How many trace panels a lane is currently drawing."""
        n = 0
        for panel in self.panels.values():
            if (
                panel.is_trace()
                and panel.is_visible(channel)
                and panel.has_visible_traces(channel)
            ):
                n += 1
        return n

    def split_spacers(self, channel: int) -> set[str]:
        """Names of the spacer rows that carry a grab band in this lane.

        A spacer earns one only where it has a visible spectrogram on one
        side and a visible trace on the other -- the boundary the split
        actually moves.  Everything else is a `SplitVCursor` over a boundary
        that does not exist: the trailing spacer under the last panel, the
        spacer between two trace panels (which no ratio here divides), and
        the focused lane of a sixteen channel stack in the pass before its
        spectrogram has anything to draw, where the band sat in 52 px of
        dead lane and swallowed presses meant for the trace.

        Per channel, and it replaces `Panels.show_spacers`, which answered
        for the whole stack from what the first visible channel happened to
        be showing.  On a sixteen channel stack exactly one lane has a
        boundary and the other fifteen do not, so there is no stack-wide
        answer to give.
        """
        panels = [p for p in self.panels.values() if not p.is_power()]
        bands = set()
        for i, panel in enumerate(panels):
            if not panel.is_spacer() or i == 0 or i + 1 >= len(panels):
                continue
            sides = (panels[i - 1], panels[i + 1])
            if not all(p.axs[channel].isVisible() for p in sides):
                continue
            if any(p.is_spectrogram() for p in sides) and any(
                p.is_trace() for p in sides
            ):
                bands.add(panel.name)
        return bands

    def lane_content_height(self, channel: int, fig_height: float) -> int:
        """The pixels one channel figure's rows actually have to share.

        The figure height less the layout's own margins - which
        `theme.style_channel_figure` leaves at zero top and bottom, because
        a lane's height is `lane_geometry`'s to spend and not the figure's -
        less what the rows this lane is *not* drawing still cost it.  A
        hidden `PlotItem` keeps `theme.PLOT_FRAME_HEIGHT` px of its row: a
        `QGraphicsGridLayout` lays hidden items out like any other, which is
        why a sixteen channel stack's plain trace lanes overflowed their
        34 px figure by exactly the 2 px their absent spectrogram was
        holding.

        Rows that sum to more than this do not shrink, they overflow:
        `QGraphicsView` clamps the central item up to the layout's minimum
        and the bottom of the last row falls off the screen, which reads as
        a rectified waveform rather than as a layout bug.
        """
        layout = self.figs[channel].ci.layout
        _, top, _, bottom = layout.getContentsMargins()
        content = fig_height - top - bottom
        for panel in self.panels.values():
            if panel.is_power() or panel.is_spacer():
                continue
            if not panel.axs[channel].isVisible():
                content -= theme.PLOT_FRAME_HEIGHT
        return int(max(1, round(content)))

    def default_spec_height(self, content: int) -> float:
        """The spectrogram's share of a lane that has not been dragged.

        The allowance, and nothing else.  `lane_geometry` grows a lane by
        `theme.SPECTROGRAM_MIN_HEIGHT` px exactly so that a spectrogram can
        be drawn there, so that is what the spectrogram row opens with and
        the traces keep the lane the stack was solved for::

            lane = content - SPECTROGRAM_MIN_HEIGHT
            spec = content - lane

        So a four channel stack in a 1200x900 window opens 120 / 34 - what
        it looked like before the boundary could be dragged at all.  The
        spectrogram opens on its allowance and never on a share of the lane,
        so the panel this number sizes is never opened shorter than the
        height `spectrogramplot.can_render` demands before it will draw.

        This used to take a share of the lane as well, one per F3 size, so
        that F3 could be pressed again for a taller spectrogram, and it had a
        floor under what the traces kept for the sizes that took most of the
        lane.  F3 is a toggle now and the boundary is dragged, so there is
        one default, the share is 1, and the floor cannot bind: it was
        ``min(traces * PANEL_SPLIT_MIN_HEIGHT, lane)`` against a share of the
        whole lane, so ``min(max(lane, floor), lane)`` was ``lane``
        identically.  Enumerated over content 1 to 1000 and one to sixteen
        trace rows, it changed the answer in no case.  Gone rather than kept
        as a no-op, which is why this no longer needs to know how many trace
        rows there are.

        `panel_split_limits` still applies a floor, and that one does bind:
        it is what stops the boundary being *dragged* into a row too short
        to draw.
        """
        return content - max(1.0, float(content - theme.SPECTROGRAM_MIN_HEIGHT))

    def panel_split_limits(self, content: int, traces: int) -> tuple[float, float]:
        """How far the boundary may be dragged, as spectrogram heights.

        `theme.PANEL_SPLIT_MIN_HEIGHT` for every row, *widened to include
        the default split* - which in a dense lane sits exactly on the floor
        and with two trace panels below it (34 px of lane shared by two
        rows is 17 px each).  A clamp that excludes the position a lane
        opens in is a clamp that jerks the boundary away from the pointer on
        the first pixel of travel and can never be dragged back.
        """
        floor = float(theme.PANEL_SPLIT_MIN_HEIGHT)
        default = self.default_spec_height(content)
        lo = max(1.0, min(floor, default))
        hi = min(float(content - traces), max(content - traces * floor, default))
        return (lo, hi) if lo <= hi else (hi, hi)

    def panel_split_rows(
        self, channel: int, fig_height: float, show_spec: bool, ntraces: int
    ) -> tuple[int, int]:
        """Row heights for one channel figure: (spectrogram, one trace).

        Integers, and they sum to exactly the height the rows have to share
        (`lane_content_height`).  The grab band is not among them: it sits
        in a row of its own that is always 0 px tall and reaches across the
        boundary instead - see `panelsplitter`.

        The algebra of a *dragged* split.  `spec_scale` says the
        spectrogram is `scale` allowances tall, and the trace rows share
        whatever the lane has left::

            spec = scale * theme.SPECTROGRAM_MIN_HEIGHT
            trace = (content - spec) / n

        `None` is a split nobody has dragged, and it opens on
        `default_spec_height` instead - the allowance the lane grew by.

        The denominator is that bare allowance, which is now the same
        number the lane opens on but was not when F3 had four sizes: the
        default then took a share of the lane too, so at size 2 it was
        120 px of lane content on a sixteen channel stack and 185 on a two
        channel one, and a split dragged on the roomy stack replayed at
        81 px on the dense one, which `spectrogramplot.can_render` will not
        draw.  Keeping the allowance as the denominator keeps that answer
        right if a lane-dependent default ever comes back.  Weaker than
        version 1's
        1.6 against 9.7, and the same mechanism.  The allowance is the one
        height here that no recording moves.

        What a reader gives up for that: once a size has been dragged it
        stops growing with the lane, because they said where they wanted the
        boundary and that is an answer in spectrograms, not in lanes.
        Shift+F3 hands the size back to the default that does grow.

        The traces are then *rounded* to whole pixels and the spectrogram
        takes the remainder, so the rows sum exactly and the boundary lands
        on a pixel the reader can point at.  Rounded and not floored: a drag
        hands back the scale it just read off these same rows, and the
        arithmetic returns it only to within a float's last bit -
        89.99999999999999 floors to 89, and a boundary dragged 20 px down
        moved 21.
        """
        content = self.lane_content_height(channel, fig_height)
        # `ntraces` is what THIS lane is drawing, not what the F2 toggle
        # says the stack should be: `lane_fallback` can put a trace back in
        # a lane with the traces off, and reading the toggle then handed the
        # rescued row a budget of zero.  It kept whatever Qt's leftover
        # distribution gave it - 15.5 px of a 29 px lane on sixteen
        # channels, 198 of 248 on two - so the backstop saved the lane from
        # being empty and left most of it background anyway.
        traces = max(0, int(ntraces))
        if not show_spec:
            return 0, max(1, content // max(1, traces)) if traces > 0 else 0
        if traces <= 0:
            return max(1, content), 0
        scale = self.spec_scale
        if scale is None:
            spec = self.default_spec_height(content)
        else:
            spec = float(scale) * theme.SPECTROGRAM_MIN_HEIGHT
        lo, hi = self.panel_split_limits(content, traces)
        spec = min(max(spec, lo), hi)
        trace = min(
            max(1, int(round((content - spec) / traces))), (content - 1) // traces
        )
        spec = max(1, int(content - traces * trace))
        return spec, trace

    def fit_figure_layout(self, channel: int) -> None:
        """Re-apply one channel figure's central geometry to its view range.

        `pyqtgraph.GraphicsView` sizes its central item only when the widget
        itself resizes, and `QGraphicsWidget.setGeometry` clamps up to the
        layout's minimum at that moment.  Row minima that *shrink* later --
        which is what every split does -- therefore leave the item at the
        taller geometry it was clamped to before: measured at 298 px inside
        a 296 px viewport on the sixteen channel recording.  Those 2 px are
        then handed out by stretch, which puts the boundary on a fraction of
        a pixel and clips the bottom of the last row.

        Self-correcting if `range` is a resize behind: the resize that is
        coming runs `setRange`, which does this same thing with the newer
        number.
        """
        fig = self.figs[channel]
        rect = QRectF(fig.range)
        if rect.isValid() and fig.ci.geometry() != rect:
            fig.ci.setGeometry(rect)

    def panel_split_heights(self, channel: int) -> Optional[tuple[float, float]]:
        """Where the boundary is now: (spectrogram height, px it shares).

        Measured off the rows as they stand on screen rather than recomputed
        from `spec_scale`, so a drag starts from the boundary the reader is
        pointing at instead of jumping to wherever the arithmetic would have
        put it.  `None` when this lane has no boundary to move.
        """
        spec_h = 0.0
        room = 0.0
        for panel in self.panels.values():
            if panel.is_power() or panel.is_spacer():
                continue
            ax = panel.axs[channel]
            if not ax.isVisible():
                continue
            height = float(ax.geometry().height())
            if panel.is_spectrogram():
                spec_h += height
                room += height
            elif panel.is_trace():
                room += height
        if spec_h <= 0.0 or room <= spec_h:
            return None
        return spec_h, room

    def drag_panel_split(self, spec_h: float, room: float) -> None:
        """Put the boundary at `spec_h` px down the `room` px it shares.

        Both numbers come from `panel_split_heights` at the latch, and
        `spec_h` carries the pointer's whole travel since - so the clamps
        below bite on the *position*, not on an increment, and a drag that
        pushes past one and comes back lands on the pixel it started from.
        `room` is re-latched whenever the lane changes under the gesture, so
        the ratio this stores is always read against the pixels the panels
        share right now; mapping travel onto a stale `room` scaled the whole
        drag by ``room_new / room_old``.

        One scale for the whole browser, so every channel showing both
        panels moves together; that sync is not code here, it is the absence
        of a per-channel copy.

        What is stored is the spectrogram over its allowance - see
        `PANEL_SPLIT_SETTING`.  The allowance is a constant, so unlike
        version 1's ratio this number carries nothing else with it: not the
        lane it was measured on, and not the number of trace rows that were
        sharing it.  Version 1 carried both without saying so, and a
        boundary saved with a second trace panel showing and replayed with
        one halved the total trace - 68 px to 34 on a 250 px lane, measured.
        """
        channels = self.visible_channels()
        if not channels:
            return
        traces = self.visible_trace_panels(channels[0]) if self.show_traces else 0
        if traces <= 0 or room <= 0:
            return
        content = int(round(room))
        lo, hi = self.panel_split_limits(content, traces)
        spec_h = min(max(spec_h, lo), hi)
        self.spec_scale = spec_h / theme.SPECTROGRAM_MIN_HEIGHT
        self.apply_panel_split()

    def apply_panel_split(self) -> None:
        """Push the current split into the rows, and touch nothing else.

        A full `adjust_layout` on a sixteen channel stack is 5.2 ms of
        Python - a third of a 60 Hz frame before anything has been drawn.
        This is 0.03 - 0.06 ms of it, which is why the drag runs this and
        the release runs that.

        The frame is another matter, and it is most of the drag.  Measured
        offscreen at 1200x900, per delivered move, against a control of
        plain hover moves over the same pixels (0.5 - 0.7 ms): 6.9 ms on a
        sixteen channel stack, which gives its spectrogram one focused lane
        and repaints 14 axes a move; 22.6 ms on four lanes and 44 axes;
        24.3 ms on two and 37.  That is a repaint of every axis of every
        lane whose rows moved, and two thirds of those paints regenerate
        the picture rather than replay a cached one.  None of it is this
        function: stub `lane_axes` out altogether and the same drag still
        costs 23.0, 24.1 and 6.9 ms with the same paint counts.  It is the
        relayout, which is the work that moves the boundary, and the only
        way to spend less of it is to move fewer pixels.

        Deliberately no `QTimer` in front of this.  `resize_timer` and
        `filter_timer` coalesce because a resize or a refilter can afford to
        arrive a beat late; a boundary under the reader's finger cannot, and
        Qt already coalesces the relayout itself - `setRowMinimumHeight`
        invalidates, and the layout is activated once before the next paint
        however many mouse moves landed in between.

        What it does not skip is the chrome.  A drag *can* move a row across
        `TICK_VALUES_MIN_HEIGHT`, because the split opens with the trace on
        the lane height and a dense lane is 34 px, well under it - so
        `lane_axes` runs again for every row whose answer changed.  It is
        asked about the height the view box will get, not the row's, which
        is the number `TimePlot._view_resized` tests: deciding on the row
        left a lane drawing amplitude tick values that the plot itself
        believed it had none of, and it hid the caption that says so.

        That it *compares* before writing is what keeps the chrome from
        doubling the drag.  `AxisItem.setStyle` drops the cached picture,
        re-measures the axis - which resizes it and invalidates the layout
        a second time - and schedules a repaint, however little it was
        handed.  Re-stating the chrome every move instead of comparing it
        costs, measured: 22.6 -> 45.3 ms per move on four lanes (44 -> 83
        axis paints), 24.3 -> 42.0 on two (37 -> 65) and 6.9 -> 10.7 on
        sixteen (14 -> 18).  It does not make the drag cheap; it stops the
        decision this function has to make from being paid for twice.

        The lane pitch belongs to `update_stretches`, which the split never
        touches, and only lanes that show a spectrogram are touched at all:
        a lane without one gives its whole height to the trace whatever the
        ratio says.
        """
        if self.show_channels is None:
            return
        channels = self.visible_channels()
        if not channels:
            return
        ntraces = self.visible_trace_panels(channels[0])
        for c in self.spectrogram_channels(channels):
            layout = self.figs[c].ci.layout
            spec_h, trace_h = self.panel_split_rows(
                c, float(self.figs[c].height()), True, ntraces
            )
            for panel in self.panels.values():
                if panel.is_power() or not panel.axs[c].isVisible():
                    continue
                if panel.is_spacer():
                    continue
                if panel.is_spectrogram():
                    row_height = spec_h
                elif panel.is_trace():
                    row_height = trace_h
                else:
                    continue
                self.lane_axes(
                    panel.axs[c],
                    self.row_shows_tick_values(row_height),
                    self.lane_left_width,
                )
                layout.setRowMinimumHeight(panel.row, row_height)
                layout.setRowStretchFactor(panel.row, max(1, int(100 * row_height)))
            self.fit_figure_layout(c)

    def finish_panel_split(self) -> None:
        """End of the gesture: one full layout pass, one settings write."""
        self.adjust_layout(self.width(), self.height())
        self.save_panel_split()

    def reset_panel_split(self) -> None:
        """Back to the split the lane opens on (Shift+F3).

        Back to `None`, not back to a number: the default follows the lane
        height, so a reset window and a reset stack of sixteen open on
        different pixels and on the same rule.
        """
        if self.show_specs <= 0 or not self.show_traces:
            self.notify("info", "no trace/spectrogram split to reset")
            return
        self.spec_scale = None
        self.adjust_layout(self.width(), self.height())
        self.save_panel_split()

    def restore_panel_split(self) -> None:
        """Put back the split this reader last dragged.

        Read once, in `__init__`, before any figure exists: the value is the
        spectrogram over the height a lane opens it at, and knows
        nothing about the recording, so a bundle with two channels and one
        with sixteen read the same entry and both open on the split the
        reader chose.  A version 1 file is *not* read: it holds the other
        ratio, which does not survive the change of lane height, and there
        is no way to tell a value the reader chose from one an earlier build
        wrote for them.  The warning below says so.

        Imported here and not at the top of the file: `audian.py` imports
        this module, so a module level import would be a cycle.
        """
        from .audian import settings

        saved = settings().get(DataBrowser.PANEL_SPLIT_SETTING)
        if not isinstance(saved, dict):
            return
        version = saved.get("version")
        if version != DataBrowser.PANEL_SPLIT_SETTING_VERSION:
            log.warning(
                "ignoring %s settings written in version %r; this audian "
                "writes version %d",
                DataBrowser.PANEL_SPLIT_SETTING,
                version,
                DataBrowser.PANEL_SPLIT_SETTING_VERSION,
            )
            return
        try:
            scale = float(saved.get("scale"))
        except (TypeError, ValueError):
            return
        if not np.isfinite(scale):
            return
        # Which scales are reachable depends on the lane height, and the
        # pixel floors settle that at layout time.  All a stored value has to
        # be is positive and finite, so that a file edited by hand cannot put
        # a zero or a negative height into a row.
        self.spec_scale = min(max(scale, 0.01), 100.0)

    def save_panel_split(self) -> None:
        """Write the split.  Once per gesture, never per mouse move.

        `save_setting` reads, updates and rewrites the whole settings file,
        and one drag is a hundred mouse moves.

        A split still on its default is written as **null**, not as the
        number that default happened to come to in this window: the default
        follows the lane height, and freezing this window's answer into the
        settings file would open the next stack on a split nobody chose.

        Version 2 made the same point by *omitting* a key nothing had
        dragged, and had an anecdote for it -- a real file was found holding
        an entry for F3 size 0, which had no boundary at all, so no gesture
        could have written it and nothing ever read it, and every drag
        rewrote it.  With one split there is one key and it is always
        written, so the null is what carries that rule now.
        """
        from .audian import save_setting

        save_setting(
            DataBrowser.PANEL_SPLIT_SETTING,
            {
                "version": DataBrowser.PANEL_SPLIT_SETTING_VERSION,
                "scale": None if self.spec_scale is None else float(self.spec_scale),
            },
        )

    def lane_fallback(self, channel: int, rows: dict) -> dict:
        """Put one row back into a lane that ended up with none.

        A lane with nothing in it is a strip of background the reader
        cannot tell from a dead channel, and every way of producing one is a
        bug in whichever rule decided it: the collapse blanked fifteen of
        sixteen lanes as soon as the traces went off, and
        `set_panels(traces=False, specs=0)` would blank every lane in the
        stack - no key reaches that pair, but nothing in `set_panels`
        forbids it either.  Those decisions belong upstream, in
        `spectrogram_channels` and `lane_geometry`, which own the row
        budget; this is the assertion that they cannot be quietly violated,
        and it is what the "no visible lane is empty" tests hold onto.

        The trace goes back first.  It is the panel that needs no allowance
        of its own, so it is the one a lane already has the height for -
        forcing a spectrogram into a lane budgeted at
        `theme.CHANNEL_DENSE_HEIGHT` squeezes it into 32 px, which is the
        stripe `spectrogramplot.can_render` exists to refuse.
        """
        for prefer_trace in (True, False):
            for panel in self.panels.values():
                if panel.name not in rows:
                    continue
                if panel.is_trace() != prefer_trace:
                    continue
                if panel.has_visible_traces(channel):
                    return {**rows, panel.name: True}
        return rows

    def adjust_layout(self, width: int, height: int) -> None:
        """Lay out the channel stack and the panels within each channel.

        Every lane is the same fixed height (`update_stretches`), the time
        axis has a row of its own below the last one, and the panels inside
        a lane split its height by stretch.
        """
        if self.show_channels is None:
            return
        channels = self.visible_channels()
        if not channels:
            return
        xheight = self.fontMetrics().ascent()
        lane_h, axis_h, spec_channels, dense = self.lane_geometry(height)
        hidden = self.show_specs > 0 and len(spec_channels) < len(channels)
        if hidden and not self.spec_warned:
            self.notify("warning", "spectrogram hidden - too many channels visible")
        self.spec_warned = hidden
        # what to plot:
        nrows = len(channels)
        # the y gutter is a stack-wide decision so that every view box
        # starts at the same x; it is only reclaimed while no spectrogram
        # needs it, because a spectrogram without a frequency scale is
        # worse than a narrow one:
        left_width = 0 if dense else theme.AXIS_LEFT_WIDTH
        self.lane_left_width = left_width
        # Panels this pass turns on, flushed once the rows are their final
        # size - see the loop at the bottom.
        revealed = []
        for c in range(self.data.channels):
            if c not in channels:
                for panel in self.panels.values():
                    if not panel.is_power():
                        panel.axs[c].setVisible(False)
                continue
            show_spec = c in spec_channels
            # Visibility first, heights second.  A row's height depends on
            # which rows this lane is drawing -- a hidden panel still holds
            # `theme.PLOT_FRAME_HEIGHT` px of the lane -- and the grab band
            # depends on there being two panels for it to divide.  Deciding
            # the band from `show_spec` instead put a live SplitVCursor over
            # a boundary that did not exist: on the focused lane of a
            # sixteen channel stack, `show_spec` is true a layout pass
            # before the spectrogram itself has anything to draw.
            spec_visible = False
            rows = {}
            for panel in self.panels.values():
                if panel.is_power() or panel.is_spacer():
                    continue
                visible = panel.has_visible_traces(c)
                if panel.is_spectrogram():
                    visible = visible and show_spec
                elif panel.is_trace():
                    visible = visible and self.show_traces
                rows[panel.name] = visible
            if not any(rows.values()):
                rows = self.lane_fallback(c, rows)
            for panel in self.panels.values():
                if panel.name not in rows:
                    continue
                if panel.is_spectrogram():
                    spec_visible = spec_visible or rows[panel.name]
                ax = panel.axs[c]
                if rows[panel.name] and not ax.isVisible():
                    revealed.append(ax)
                ax.setVisible(rows[panel.name])
            bands = self.split_spacers(c)
            for panel in self.panels.values():
                if panel.is_spacer():
                    panel.axs[c].setVisible(panel.name in bands)
            # per channel, not once for the first one: with a spectrogram on
            # the focused channel only, sizing every other lane's rows
            # against the focused lane's height tells the trace rows they
            # are 154 px tall when they are 34, and they then keep tick
            # values that do not fit.
            fig_height = max(1.0, float(lane_h))
            if show_spec:
                fig_height += theme.SPECTROGRAM_MIN_HEIGHT
            lane_traces = sum(
                1 for p in self.panels.values() if p.is_trace() and rows.get(p.name)
            )
            spec_h, trace_h = self.panel_split_rows(
                c, fig_height, spec_visible, lane_traces
            )
            if show_spec and self.show_powers:
                self.figs[c].ci.layout.setColumnFixedWidth(1, 0.1 * width)
            else:
                self.figs[c].ci.layout.setColumnFixedWidth(1, 0)
            for panel in self.panels.values():
                if panel.is_power():
                    continue
                if panel.is_spacer():
                    # the band reaches across the boundary from a row of no
                    # height at all, so it never takes a pixel from either
                    # panel - see `panelsplitter`:
                    self.figs[c].ci.layout.setRowMinimumHeight(panel.row, 0)
                    self.figs[c].ci.layout.setRowStretchFactor(panel.row, 0)
                    continue
                if not panel.axs[c].isVisible():
                    self.figs[c].ci.layout.setRowMinimumHeight(panel.row, 0)
                    self.figs[c].ci.layout.setRowStretchFactor(panel.row, 0)
                    continue
                row_height = spec_h if panel.is_spectrogram() else trace_h
                # the tick values a row can carry follow the height of that
                # row, not of the lane: a lane of 34 px plus the
                # spectrogram's 120 px allowance splits into two rows of its
                # own, and each answers for itself:
                self.lane_axes(
                    panel.axs[c],
                    self.row_shows_tick_values(row_height),
                    left_width,
                )
                self.figs[c].ci.layout.setRowMinimumHeight(panel.row, row_height)
                self.figs[c].ci.layout.setRowStretchFactor(
                    panel.row, max(1, int(100 * row_height))
                )
        self.update_stretches(height)
        self.link_time_axis()
        self.align_time_axis()
        self.schedule_axis_alignment()
        # fix full data plot:
        if self.datafig is not None:
            data_height = 5 * xheight // 2 if nrows <= 1 else 3 * xheight // 2
            self.datafig.update_layout(channels, data_height)
            self.datafig.setVisible(self.show_fulldata)
        self.size_splitter()
        # update:
        for c in channels:
            self.fit_figure_layout(c)
            self.figs[c].update()
        # A panel that was hidden a moment ago has never been asked to draw,
        # and showing it does not ask.  `Panel.update_plots` skips hidden
        # panels -- rightly, uploading a spectrogram nobody can see is what
        # `specitem` exists to avoid -- and the only caller that follows a
        # layout with one is `set_panels`.  So every other way of revealing a
        # panel gave the reader an empty one: on sixteen channels, F3 drew a
        # spectrogram on the focused lane and stepping to the next lane made
        # room for one and left it blank.  It stayed blank until something
        # else moved the range, which is why pressing F2 twice appeared to
        # cure it for good - that pass has every lane visible at once, so
        # every `SpecItem` uploads and none is ever empty again.
        #
        # Run last, so the item reads the width its row ended up with rather
        # than the one it had before the split moved; and cheap, because
        # `SpecItem.update_plot` returns immediately when what is on screen
        # is already uploaded, which is what makes the redundant calls a
        # hidden ancestor can produce here harmless.
        for ax in revealed:
            ax.update_plot()

    def schedule_axis_alignment(self) -> None:
        """Line the axis up again once Qt has finished moving the lanes.

        `align_time_axis` measures a lane's view box, and a lane's view box
        is a `pyqtgraph.GraphicsView` central item, which is only re-fitted
        when the widget itself resizes -- `fit_figure_layout` says as much
        and leans on "the resize that is coming".

        On a stack of sixteen a resize always is coming, because the scroll
        area's content changed height; the mean spectrogram is the one mode
        where it is not, and there the inline measurement was simply left
        standing.  Measured at 1200x900 on the mean panel: F5 widened the
        lane's view box from 1320 px to 1456 and the axis stayed at 1320,
        so the ticks under a full-screen spectrogram were 136 px short of
        it and stayed that way until the window was resized.

        One turn of the event loop is enough, and the call is idempotent:
        `align_time_axis` only writes the margins when they changed.
        """
        QTimer.singleShot(0, self.align_time_axis)

    def align_time_axis(self) -> None:
        """Line the shared time axis up with the lanes above it.

        The axis lives in its own widget, so it has to be told where the
        lane view boxes start and end.  Measuring the reference lane is
        safe here in a way the old axis-height measurement was not: the
        lanes do not depend on the axis row's *width*, so this reads a
        finished number instead of feeding into the number it is reading.
        """
        if self.taxis_fig is None:
            return
        channels = self.visible_channels()
        if not channels:
            return
        plot = self.time_plot(channels[0])
        if plot is None:
            return
        view = plot.getViewBox()
        rect = view.mapRectToScene(view.boundingRect())
        fig = self.figs[channels[0]]
        # in the pane's own pixels, so that the rail column, the scroll
        # area's frame and its scroll bar are all accounted for by
        # measurement instead of by arithmetic that has to be kept in step:
        origin = fig.mapTo(self.stack_pane, QPoint(0, 0)).x()
        axis_origin = self.taxis_fig.mapTo(self.stack_pane, QPoint(0, 0)).x()
        left = max(0, int(round(origin + rect.left() - axis_origin)))
        right = max(
            0,
            int(round(axis_origin + self.taxis_fig.width() - origin - rect.right())),
        )
        layout = self.taxis_fig.ci.layout
        if (left, right) != self.taxis_margins:
            self.taxis_margins = (left, right)
            layout.setContentsMargins(left, 0, right, 0)
        # One measurement, two consumers.  The control panel is built with the
        # axis strip's widget structure precisely so that the margins measured
        # off a lane apply to it unchanged; giving it its own measurement
        # would be a second thing to keep in step with the lanes.
        if self.control_panel is not None:
            self.control_panel.set_margins(left, right)

    def size_splitter(self) -> None:
        """Give the navigator its natural height and the stack the rest.

        The navigator is `setMaximumHeight`-capped (56 px in single mode,
        one row per channel in 'all' mode).  A QSplitter does not
        redistribute when a child's maximum changes after the fact, so it
        happily hands the navigator 450 px of which it paints 104 - the
        rest is dead space between the plots and the parameter bar.
        Recomputing the sizes here is cheap and idempotent.
        """
        if self.splitter is None or self.datafig is None:
            return
        total = self.splitter.height()
        if total <= 0:
            return
        handle = self.splitter.handleWidth()
        if not self.datafig.isVisible() or not self.show_fulldata:
            nav = 0
        else:
            # the navigator wants its maximum (rows + time axis + margins),
            # but never more than a third of the stack - on a 16 channel
            # file in 'all' mode that maximum is 432 px:
            nav = min(self.datafig.maximumHeight(), max(1, int(0.34 * total)))
            nav = max(nav, self.datafig.minimumHeight())
        stack = max(theme.CHANNEL_MIN_HEIGHT, total - nav - handle)
        if self.splitter.sizes() != [stack, nav]:
            self.splitter.setSizes([stack, nav])

    def update_ranges(self, viewbox, arange):
        """React to a view range change, on the axis that actually moved.

        `sigRangeChanged` always delivers both the x and the y range.
        Re-deriving the time window from an unchanged x range decimates
        and re-uploads every trace of every channel - 109 ms for a
        y-only zoom on a 16 channel file - while nothing about the
        decimation depends on y.
        """
        if self.setting:
            return
        panel = self.panels.get_panel(viewbox)
        if not panel:
            return
        axspec = panel.ax_spec
        moved = False
        for s in range(2):
            r0, r1 = arange[s]
            stored = self.plot_ranges[axspec[s]]
            channel = getattr(viewbox, "channel", 0)
            if channel >= len(stored.r0):
                channel = 0
            if self.same_range(stored.r0[channel], stored.r1[channel], r0, r1):
                continue
            moved = True
            if axspec[s] in Panel.times:
                self.set_times(r0, r1 - r0)
            else:
                # the user set this range by hand - stop refitting it:
                self.y_locked = True
                self.set_ranges(axspec[s], r0, r1)
        if moved:
            self.sigRangesChanged.emit(axspec, arange)

    @staticmethod
    def same_range(r0: float, r1: float, n0: float, n1: float) -> bool:
        """True if a new range is the stored one up to rounding."""
        span = max(abs(r1 - r0), abs(n1 - n0))
        if span <= 0:
            return r0 == n0 and r1 == n1
        eps = 1e-6 * span
        return abs(n0 - r0) < eps and abs(n1 - r1) < eps

    def goto_time(self, file_name, time):
        file_times = self.data.data.file_start_times()
        file_paths = self.data.data.file_paths
        if "." in file_name:
            for ft, fp in zip(file_times, file_paths):
                if Path(fp).name == file_name:
                    t0 = ft + time
                    self.plot_ranges["t"].goto(t0)
                    return
        else:
            for ft, fp in zip(file_times, file_paths):
                if Path(fp).stem.replace("-", "") == file_name:
                    t0 = ft + time
                    self.plot_ranges["t"].goto(t0)
                    return

    def set_times(self, toffset=None, twindow=None):
        if self.setting:
            return
        with self.updating():
            trange = self.plot_ranges[Panel.times[0]]
            trange.set_ranges(toffset, None, twindow, None, True)
            fn = self.data.update_times(trange.r0[0], trange.r1[0])
            self.sigFilenameChanged.emit(self, fn)
            self.panels.update_plots()
            self.plot_ranges.set_powers()
        self.update_levels()
        if not self.y_locked:
            self.auto_fit_y()

    def apply_time_ranges(self, timefunc):
        with self.updating():
            getattr(self.plot_ranges, timefunc)(Panel.times[0], None, self.isVisible())
            trange = self.plot_ranges[Panel.times[0]]
            fn = self.data.update_times(trange.r0[0], trange.r1[0])
            self.sigFilenameChanged.emit(self, fn)
            # TODO: set time range here!
            self.panels.update_plots()
            self.plot_ranges.set_powers()
        self.update_levels()

    def range_channels(self) -> list:
        """Channels an amplitude operation applies to.

        Under a shared Y every lane shows the same span *by definition*, so
        an operation on one lane is an operation on all of them.  Only when
        the lanes are scaled independently does the selection decide.

        This used to be worked out in set_ranges() alone: apply_ranges(),
        which is what Reset (Shift+V), Center and the zoom steps go through,
        used the selection unconditionally.  So under a shared Y -- the
        default -- dragging a range moved every lane while Shift+V reset one,
        and clicking a lane silently narrowed the selection to it.
        """
        if self.y_mode == DataBrowser.y_shared:
            return list(range(self.data.channels))
        return self.selected_channels

    def set_ranges(self, axspec, r0=None, r1=None):
        if self.setting:
            return
        channels = self.range_channels()
        with self.updating():
            self.plot_ranges[axspec].set_ranges(
                r0, r1, None, channels, self.isVisible()
            )
        self.report_y_range()

    def apply_ranges(self, amplitudefunc, axspec):
        with self.updating():
            getattr(self.plot_ranges, amplitudefunc)(
                axspec, self.range_channels(), self.isVisible()
            )
        self.report_y_range()

    def auto_ampl(self, axspec=Panel.amplitudes):
        """Refit the amplitude ranges to the data now (v)."""
        self.y_locked = self.y_mode == DataBrowser.y_fixed
        self.auto_fit_y(force=True)

    def set_spectrogram(self, checked, spec):
        if checked:
            self.spectrogram = spec
            if self.spectrogram:
                self.spectrogram_power = self.panels[
                    self.data[self.spectrogram].panel
                ].z()
            self.set_resolution()

    def set_resolution(
        self, nfft=None, overlap_frac=None, dispatch: bool = True
    ) -> None:
        """Set the Fourier window and overlap of the spectrogram.

        The membership check happens *before* the guard flag is taken:
        an early return that leaves `self.setting` set freezes every
        later scroll and zoom for the rest of the session.
        """
        if self.setting:
            return
        if not self.spectrogram or self.spectrogram not in self.data:
            return
        spectrogram = self.data[self.spectrogram]
        with self.updating():
            spectrogram.update(nfft, overlap_frac)
            self.panels.update_plots()
            self.plot_ranges.set_powers()
            if self.nfftw is not None:
                self.set_nfft_widget(spectrogram.nfft)
                T = spectrogram.nfft / self.data.rate
                if 1 / T >= 1000:
                    deltaf_label = f"\u0394f={0.001 / T:.2f}kHz"
                elif 1 / T >= 1:
                    deltaf_label = f"\u0394f={1 / T:.3g}Hz"
                else:
                    deltaf_label = f"\u0394f={1000 / T:.3g}mHz"
                self.nfftw.setToolTip(f"{self.nfftw.tooltip}, {deltaf_label}")
            if self.ofracw is not None:
                blocked = self.ofracw.blockSignals(True)
                self.ofracw.setValue(int(round(100 * spectrogram.overlap_frac)))
                self.ofracw.blockSignals(blocked)
                dt = spectrogram.hop / self.data.rate
                if dt >= 1:
                    deltat_label = f"\u0394t={dt:.3g}s"
                else:
                    deltat_label = f"\u0394t={1000 * dt:.3g}ms"
                self.ofraclabelw.setText(
                    f"{100 * spectrogram.overlap_frac:5.1f}% {deltat_label}"
                )
                self.ofracw.setToolTip(
                    f"{self.ofracw.tooltip}, hop={spectrogram.hop}, {deltat_label}"
                )
        if dispatch:
            self.sigResolutionChanged.emit()

    def freq_resolution_down(self):
        if self.spectrogram in self.data:
            self.set_resolution(nfft=self.data[self.spectrogram].nfft // 2)

    def freq_resolution_up(self):
        if self.spectrogram in self.data:
            self.set_resolution(nfft=2 * self.data[self.spectrogram].nfft)

    def overlap_frac_up(self):
        if self.spectrogram in self.data:
            hop_frac = 1 - self.data[self.spectrogram].overlap_frac
            self.set_resolution(overlap_frac=1 - hop_frac / 2)

    def overlap_frac_down(self):
        if self.spectrogram in self.data:
            hop_frac = 1 - self.data[self.spectrogram].overlap_frac
            self.set_resolution(overlap_frac=1 - hop_frac * 2)

    def set_color_map(self, color_map=None, dispatch: bool = True) -> None:
        """Apply a perceptually uniform spectrogram colormap and remember it."""
        if color_map is not None:
            self.color_map = int(color_map)
        if self.color_map < 0 or self.color_map >= len(theme.spectrogram_maps()):
            self.color_map = theme.DEFAULT_SPECTROGRAM_MAP
        for panel in self.panels.values():
            if panel.is_spectrogram():
                panel.set_colormap(theme.spectrogram_maps()[self.color_map])
        if self.cmapw is not None and self.cmapw.currentIndex() != self.color_map:
            blocked = self.cmapw.blockSignals(True)
            self.cmapw.setCurrentIndex(self.color_map)
            self.cmapw.blockSignals(blocked)
        QSettings("audian", "audian").setValue("spectrogram/colormap", self.color_map)
        if dispatch:
            self.sigColorMapChanged.emit()

    def color_map_cycler(self) -> None:
        self.set_color_map((self.color_map + 1) % len(theme.spectrogram_maps()))

    def update_filter(self, highpass_cutoff=None, lowpass_cutoff=None):
        """Called when filter cutoffs were changed by key shortcuts or handles
        in spectrum plots and when dispatching.

        The recompute is debounced: key auto-repeat on H / L emits one
        `sigValueChanged` per event, and refiltering plus respectrogramming
        16 channels costs about 1.5 s. A burst of repeats must cost one
        recompute, not one per key event.
        """
        if self.setting:
            return
        if "filtered" not in self.data:
            return
        if highpass_cutoff is not None:
            self.pending_highpass = highpass_cutoff
        if lowpass_cutoff is not None:
            self.pending_lowpass = lowpass_cutoff
        filtered = self.data["filtered"]
        if (
            self.link_band
            and self.pending_highpass is not None
            and lowpass_cutoff is None
        ):
            band = filtered.lowpass_cutoff - filtered.highpass_cutoff
            self.pending_lowpass = min(self.data.rate / 2, self.pending_highpass + band)
        elif (
            self.link_band
            and self.pending_lowpass is not None
            and highpass_cutoff is None
        ):
            band = filtered.lowpass_cutoff - filtered.highpass_cutoff
            self.pending_highpass = max(0.0, self.pending_lowpass - band)
        self.filter_timer.start(200)

    def apply_filter(self) -> None:
        """Recompute the filtered trace from the stashed cutoffs."""
        if "filtered" not in self.data:
            return
        filtered = self.data["filtered"]
        if self.pending_highpass is not None:
            filtered.highpass_cutoff = self.pending_highpass
        if self.pending_lowpass is not None:
            filtered.lowpass_cutoff = self.pending_lowpass
        self.pending_highpass = None
        self.pending_lowpass = None
        with self.updating():
            for ax in self.panels["spectrogram"].axs:
                ax.set_filter_handles(filtered.highpass_cutoff, filtered.lowpass_cutoff)
            self.set_filter_widgets(filtered.highpass_cutoff, filtered.lowpass_cutoff)
            if hasattr(filtered, "request_update"):
                filtered.request_update(0)
            else:
                filtered.update()
            self.panels.update_plots()
            self.plot_ranges.set_powers()
        # the navigator resolves raw-vs-filtered from the live cutoffs, so it
        # has to be told the moment they change or the strip stays cyan for a
        # whole interaction while the stack above has already gone amber:
        if hasattr(self.datafig, "refresh_colors"):
            self.datafig.refresh_colors()
        self.sigFilterChanged.emit()  # dispatch

    def set_filter_widgets(self, highpass: float, lowpass: float) -> None:
        for widget, value in ((self.hpfw, highpass), (self.lpfw, lowpass)):
            if widget is None:
                continue
            blocked = widget.blockSignals(True)
            widget.setValue(value)
            widget.blockSignals(blocked)
        if self.hpsliderw is not None:
            self.hpsliderw.set_hz(highpass)
        if self.lpsliderw is not None:
            self.lpsliderw.set_hz(lowpass)

    def update_envelope(
        self, envelope_cutoff=None, show_envelope=None, dispatch: bool = True
    ) -> None:
        """Called when envelope cutoff was changed by key shortcuts or widget.

        The membership check happens *before* the guard flag is taken.
        The default plugin set installs 'filtered' and 'spectrogram' but
        no 'envelope', so the very first Ctrl+E used to leak
        `self.setting == True` and silently freeze all further scrolling
        and zooming.
        """
        if self.setting:
            return
        if "envelope" not in self.data:
            return
        if envelope_cutoff is not None:
            self.pending_envelope = envelope_cutoff
            self.envelope_timer.start(200)
        if show_envelope is not None:
            with self.updating():
                for name in self.data.keys():
                    if name.startswith("env"):
                        self.set_trace(show_envelope, name)
            self.adjust_layout(self.width(), self.height())
        if dispatch:
            self.sigEnvelopeChanged.emit()

    def apply_envelope(self, dispatch: bool = True) -> None:
        """Recompute the envelope from the stashed cutoff."""
        if "envelope" not in self.data or self.pending_envelope is None:
            return
        envelope = self.data["envelope"]
        with self.updating():
            envelope.envelope_cutoff = self.pending_envelope
            self.pending_envelope = None
            if hasattr(envelope, "request_update"):
                envelope.request_update(0)
            else:
                envelope.update()
            self.data.set_need_update()
            self.panels.update_plots()
            if self.envfw is not None:
                blocked = self.envfw.blockSignals(True)
                self.envfw.setValue(envelope.envelope_cutoff)
                self.envfw.blockSignals(blocked)
            if self.envsliderw is not None:
                self.envsliderw.set_hz(envelope.envelope_cutoff)
        if dispatch:
            self.sigEnvelopeChanged.emit()

    def add_to_show_channels(self, channels):
        if isinstance(channels, int):
            channels = [channels]
        for channel in channels:
            if channel not in self.show_channels:
                self.show_channels.append(channel)
        self.show_channels.sort()

    def add_to_selected_channels(self, channels):
        if isinstance(channels, int):
            channels = [channels]
        for channel in channels:
            if channel not in self.selected_channels:
                self.selected_channels.append(channel)
        self.selected_channels.sort()

    def all_channels(self):
        if self.selected_channels == self.show_channels:
            self.selected_channels = list(range(self.data.channels))
        else:
            self.selected_channels = list(self.show_channels)
        self.update_borders()

    def stepped_channel(self, forward: bool) -> Optional[int]:
        """The next channel down (or up) the stack that is actually drawn.

        `show_channels` is the window of channels the stack is scrolled to;
        `visible_channels` is what survives solo, mute and maximise on top of
        that, and only those have a lane on screen.  The arrow keys walked
        the first and could land on the second's leftovers: with channel 5
        muted, stepping down from 4 put the focus on a lane that is not
        there.  Harmless while nothing read the focus back - the frame just
        went somewhere invisible - but `spectrogram_channels` falls back to
        `channels[0]` when the focused channel is not among them, so on a
        collapsed sixteen channel stack one press of the down arrow moved
        the spectrogram from lane 4 to lane 0.  Returns `None` at the end of
        the window, which is where the caller scrolls it.
        """
        drawn = self.visible_channels()
        idx = self.show_channels.index(self.current_channel)
        window = (
            self.show_channels[idx + 1 :] if forward else self.show_channels[:idx][::-1]
        )
        return next((c for c in window if c in drawn), None)

    def next_channel(self):
        step = self.stepped_channel(True)
        if step is not None:
            self.focus_channel(step)
            self.selected_channels = [self.current_channel]
            self.update_borders()
        else:
            if self.show_channels[-1] < self.data.channels - 1:
                n = len(self.show_channels)
                if n > 1:
                    n -= 1
                if self.show_channels[-1] + n >= self.data.channels:
                    n = self.data.channels - 1 - self.show_channels[-1]
                self.add_to_show_channels(
                    list(
                        range(
                            self.show_channels[-1] + 1, self.show_channels[-1] + 1 + n
                        )
                    )
                )
                del self.show_channels[:n]
                self.current_channel += 1
            self.selected_channels = [self.current_channel]
            self.set_channels()

    def previous_channel(self):
        step = self.stepped_channel(False)
        if step is not None:
            self.focus_channel(step)
            self.selected_channels = [self.current_channel]
            self.update_borders()
        else:
            if self.show_channels[0] > 0:
                n = len(self.show_channels)
                if n > 1:
                    n -= 1
                if self.show_channels[0] < n:
                    n = self.show_channels[0]
                self.add_to_show_channels(
                    list(range(self.show_channels[0] - n, self.show_channels[0]))
                )
                del self.show_channels[-n:]
                self.current_channel -= 1
            self.selected_channels = [self.current_channel]
            self.set_channels()

    def select_next_channel(self):
        show_selected_channels = [
            c
            for c in range(self.data.channels)
            if c in self.show_channels and c in self.selected_channels
        ]
        if len(show_selected_channels) > 0:
            self.current_channel = show_selected_channels[-1]
        step = self.stepped_channel(True)
        if step is not None:
            self.focus_channel(step)
            self.add_to_selected_channels(self.current_channel)
            self.update_borders()
        else:
            if self.show_channels[-1] < self.data.channels - 1:
                n = len(self.show_channels)
                if self.show_channels[-1] + n >= self.data.channels:
                    n = self.data.channels - 1 - self.show_channels[-1]
                self.add_to_show_channels(
                    list(
                        range(
                            self.show_channels[-1] + 1, self.show_channels[-1] + 1 + n
                        )
                    )
                )
                del self.show_channels[:n]
            if self.current_channel < self.data.channels - 1:
                self.current_channel += 1
                self.add_to_selected_channels(self.current_channel)
            self.set_channels()

    def select_previous_channel(self):
        show_selected_channels = [
            c
            for c in range(self.data.channels)
            if c in self.show_channels and c in self.selected_channels
        ]
        if len(show_selected_channels) > 0:
            self.current_channel = show_selected_channels[0]
        step = self.stepped_channel(False)
        if step is not None:
            self.focus_channel(step)
            self.add_to_selected_channels(self.current_channel)
            self.update_borders()
        else:
            if self.show_channels[0] > 0:
                n = len(self.show_channels)
                if self.show_channels[0] < n:
                    n = self.show_channels[0]
                self.add_to_show_channels(
                    list(range(self.show_channels[0] - n, self.show_channels[0]))
                )
                del self.show_channels[-n:]
            if self.current_channel > 0:
                self.current_channel -= 1
                self.add_to_selected_channels(self.current_channel)
            self.set_channels()

    def set_channels(
        self, show_channels=None, selected_channels=None, current_channel=None
    ):
        if self.setting:
            return
        with self.updating():
            if show_channels is not None:
                if self.data is None:
                    self.schannels = show_channels
                    return
                self.show_channels = [
                    c for c in show_channels if c < self.data.channels
                ]
            if selected_channels is not None:
                self.selected_channels = [
                    c for c in selected_channels if c < self.data.channels
                ]
            if current_channel is not None:
                self.current_channel = current_channel
            # current channel must be in shown and selected channels:
            show_selected_channels = [
                c
                for c in range(self.data.channels)
                if c in self.show_channels and c in self.selected_channels
            ]
            if self.current_channel not in show_selected_channels:
                for c in show_selected_channels:
                    if c >= self.current_channel:
                        self.current_channel = c
                        break
                if self.current_channel not in show_selected_channels:
                    self.current_channel = show_selected_channels[-1]
            # `show_channels` narrows what the mean averages, the same way
            # solo and mute do - see `apply_channel_visibility`:
            self.apply_mean_spectrogram()
            self.apply_lane_visibility()
            for c in range(self.data.channels):
                self.acts.channels[c].setChecked(c in self.show_channels)
            self.update_rail()
            self.adjust_layout(self.width(), self.height())
            self.update_borders()
            if self.mean_spec:
                self.panels.update_plots()

    def toggle_channel(self, channel):
        if self.setting:
            return
        if channel < 0 or channel >= self.data.channels:
            return
        if self.acts.channels[channel].isChecked():
            self.add_to_show_channels(channel)
            self.add_to_selected_channels(channel)
            self.set_channels()
        else:
            if channel in self.show_channels:
                self.show_channels.remove(channel)
                if len(self.show_channels) == 0:
                    c = channel + 1
                    if c >= self.data.channels:
                        c = 0
                    self.show_channels = [c]
                    self.add_to_selected_channels(c)
                if channel in self.selected_channels:
                    self.selected_channels.remove(channel)
                    if len(self.selected_channels) == 0:
                        for c in self.show_channels:
                            if c < channel:
                                self.current_channel = c
                            else:
                                break
                        self.selected_channels = [self.current_channel]
                # if len(self.show_channels) == 1:
                #    self.acts.channels[self.show_channels[0]].setCheckable(False)
                self.set_channels()
        self.setFocus()

    def show_channel(self, channel):
        if channel < 0 or channel >= self.data.channels:
            return
        if self.current_channel == channel and self.show_channels == [channel]:
            self.set_channels(list(range(self.data.channels)))
        else:
            self.current_channel = channel
            self.add_to_selected_channels(channel)
            self.set_channels([channel])

    def hide_deselected_channels(self):
        show_channels = [c for c in self.show_channels if c in self.selected_channels]
        if len(show_channels) == 0:
            show_channels = [self.show_channels[0]]
        self.set_channels(show_channels)

    def set_panels(
        self, traces=None, specs=None, powers=None, cbars=None, fulldata=None
    ):
        if traces is not None:
            self.show_traces = traces
        if specs is not None:
            # 0 or 1, whatever was passed.  `show_specs` used to be an F3
            # size as well as an on/off, and the four sizes are gone -- so a
            # 2 arriving from a caller written against the old meaning, or
            # from a settings file, is "on" and is stored as "on" rather than
            # left in the field for the next reader to wonder about.
            self.show_specs = 1 if specs else 0
        if powers is not None:
            self.show_powers = powers
        if cbars is not None:
            self.show_cbars = cbars
        if fulldata is not None:
            self.show_fulldata = fulldata
        # The mean lives inside the traces-off all-spectrograms mode and
        # cannot outlive it.  A mean over the array beside one channel's
        # waveform is two different pictures of two different things stacked
        # in one lane, and with the spectrograms off there is nothing left
        # to average at all -- so F2 and F3 drop it rather than leaving a
        # toggle checked over a stack that stopped obeying it.
        if self.mean_spec and (self.show_traces or self.show_specs <= 0):
            self.mean_spec = False
            self.traces_before_mean = False
        self.apply_mean_spectrogram()
        # `set_panels` is the one panel-level call that can change how many
        # *lanes* there are, because the mean collapses sixteen onto one.
        # Every other caller reaches the stack through
        # `apply_channel_visibility`:
        self.apply_lane_visibility()
        for panel in self.panels.values():
            if panel.is_trace():
                panel.set_visible(self.show_traces)
            elif panel.is_spectrogram():
                panel.set_visible(self.show_specs > 0)
                panel.set_cbar_visible(self.show_specs > 0 and self.show_cbars)
            elif panel.is_power():
                panel.set_visible(self.show_specs > 0 and self.show_powers)
        # whole panels go here rather than whole lanes, which is the other
        # way the plot carrying the grips can stop being on screen
        self.revalidate_selection()
        if self.datafig is not None:
            self.datafig.setVisible(self.show_fulldata)
        self.adjust_layout(self.width(), self.height())
        self.data.set_need_update()
        trange = self.plot_ranges[Panel.times[0]]
        fn = self.data.update_times(trange.r0[0], trange.r1[0])
        self.sigFilenameChanged.emit(self, fn)
        self.panels.update_plots()
        self.plot_ranges.set_powers()

    def toggle_traces(self):
        self.show_traces = not self.show_traces
        if not self.show_traces:
            self.show_specs = 1
        self.set_panels()

    def toggle_spectrograms(self):
        """F3 shows the spectrograms or hides them.  That is all it does now.

        It used to step through five states -- off, then four sizes that
        handed the spectrogram a bigger share of every lane -- because when
        it was written that was the only way to make the spectrogram taller.
        The boundary between a trace and its spectrogram has been draggable
        since, continuously and in any lane, and Shift+F3 puts it back; four
        preset sizes reachable only by pressing F3 up to five times are a
        second, coarser answer to a question that already has a better one.

        The cost was not only the extra presses.  `show_specs` was doing two
        jobs at once -- whether there is a spectrogram, and how tall it is --
        so every reader had to know which one it meant, the dragged split was
        stored four times over under a key nobody could see, and turning the
        spectrogram off and on again could open it at a different height
        than it had a moment before.

        Turning it off still puts the traces back, which is not politeness:
        `set_panels` will happily hide both, and a stack with neither is a
        window of empty lanes with no key that obviously fills them again.
        `toggle_traces` holds the same line from its own side.
        """
        off = self.show_specs > 0
        self.set_panels(traces=True if off else None, specs=0 if off else 1)

    def apply_mean_spectrogram(self) -> bool:
        """Push the current mean state onto the spectrogram panels.

        Every panel is told, not only the one on screen: a panel that keeps
        an average it is no longer allowed to draw would put it back the
        next time solo or mute made it visible again.

        Returns True when a panel actually changed, which is the moment the
        selection cue has to be redrawn: the mean borrows a lane and is no
        channel, so which lane counts as the current one changes with it --
        see `update_current_plot`.  It runs on every `set_panels`, so it
        does that work only when there is work.
        """
        lane = self.mean_spec_lane()
        channels = self.mean_channels()
        changed = False
        for ax in self.spectrogram_plots():
            if ax.set_mean_channels(channels if ax.channel == lane else None):
                changed = True
        self.apply_rail_width()
        if changed:
            self.update_current_plot()
        return changed

    def set_mean_spectrogram(self, on: bool) -> None:
        """Show one full-height mean spectrogram, or go back to the stack.

        Turning it on turns the traces off, the way `toggle_traces` turns
        the spectrograms on: this mode only means anything with the lanes
        given over to spectrograms, and a shortcut that silently does
        nothing outside its mode is a shortcut the reader stops trusting.
        Turning it off puts the traces back only if this is what took them
        away, so that pressing the key twice is a round trip from wherever
        the reader started.
        """
        on = bool(on)
        if on == self.mean_spec:
            return
        if on:
            self.traces_before_mean = self.show_traces
            self.mean_spec = True
            self.show_traces = False
            if self.show_specs <= 0:
                self.show_specs = 1
        else:
            self.mean_spec = False
            if self.traces_before_mean:
                self.show_traces = True
            self.traces_before_mean = False
        self.set_panels()

    def toggle_mean_spectrogram(self) -> None:
        self.set_mean_spectrogram(not self.mean_spec)

    def mean_spectrogram_message(self) -> str:
        """What the status bar says about the mode that was just entered."""
        if not self.mean_spec:
            return "mean spectrogram off"
        channels = self.mean_channels()
        listed = ", ".join(f"{c:02d}" for c in sorted(channels))
        return f"mean spectrogram over {len(channels)} channels: {listed}"

    def toggle_colorbars(self):
        self.show_cbars = not self.show_cbars
        self.set_panels()

    def toggle_powers(self):
        self.show_powers = not self.show_powers
        self.set_panels()

    def toggle_fulldata(self):
        self.show_fulldata = not self.show_fulldata
        self.set_panels()

    def toggle_grids(self):
        self.grids -= 1
        if self.grids < 0:
            self.grids = 3
        self.panels.show_grid(self.grids)

    def set_zoom_mode(self, mode):
        for axs in self.axs:
            for ax in axs:
                ax.getViewBox().setMouseMode(mode)

    def zoom_back(self):
        for axs in self.axs:
            for ax in axs:
                ax.getViewBox().zoom_back()

    def zoom_forward(self):
        for axs in self.axs:
            for ax in axs:
                ax.getViewBox().zoom_forward()

    def zoom_home(self):
        for axs in self.axs:
            for ax in axs:
                ax.getViewBox().zoom_home()

    def set_region_mode(self, mode):
        """Choose what a rubber-band drag does.

        Label mode takes the mouse away from the spectrogram's filter
        handles for as long as it lasts.  Both want the same pixels -- see
        `SpectrogramPlot.set_handles_movable` -- and on this fork's data the
        band worth labelling is the bottom of the lane, which is exactly
        where the highpass cutoff sits.
        """
        self.region_mode = mode
        movable = mode != DataBrowser.MODE_LABEL
        if movable:
            # Leaving label mode gives the lane back, grips included: they
            # are 12 px targets sitting where a filter cutoff is about to be
            # draggable again, and a reader who has stopped labelling has
            # stopped meaning them.
            self.deselect_label()
        for ax in self.spectrogram_plots():
            ax.set_handles_movable(movable)
        if mode == DataBrowser.MODE_LABEL:
            # The other gesture worth raising a tab for.  Pressing b, or
            # clicking Label on the tool bar, is an unambiguous "I am about
            # to write labels", and which category the next drag writes is
            # on that page.  Not raised again on the way out: leaving the
            # mode says nothing about what the reader wants to look at.
            self.raise_parameter_tab(DataBrowser.EDITABLE_TAB)

    def region_menu_at(self, channel, vbox, rect, scene_pos):
        """Act on a selected region, popping up the menu at the drag.

        `QCursor.pos()` is not answerable under Wayland: QtWayland
        returns a stale cached position or (0, 0), which puts the menu in
        a screen corner. Map the scene position of the drag into the view
        that produced it instead.
        """
        self.region_menu(channel, vbox, rect, scene_pos)

    def region_menu(self, channel, vbox, rect, scene_pos=None):
        panel = self.panels.get_panel(vbox)
        # A modified drag (Shift = play, Alt = analyse) overrides the tool
        # bar mode for exactly one region; SelectViewBox sets it just
        # before it emits.
        mode = self.region_mode
        if self.region_mode_override is not None:
            mode = self.region_mode_override
            self.region_mode_override = None
        if mode == DataBrowser.MODE_ZOOM or not panel.is_time():
            vbox.zoom_region(rect)
        elif mode == DataBrowser.MODE_PLAY:
            self.play_region(rect.left(), rect.right())
        elif mode == DataBrowser.MODE_ANALYZE:
            self.analyze_region(rect.left(), rect.right(), channel)
        elif mode == DataBrowser.MODE_SAVE:
            self.save_region(rect.left(), rect.right())
        elif mode == DataBrowser.MODE_LABEL:
            # `drag_modifiers` is latched when the drag starts and cleared
            # only after both region signals have gone out, so it still says
            # what was held down when the press happened
            if vbox.drag_modifiers & Qt.ControlModifier:
                self.select_label_from_region(channel, vbox, rect)
            else:
                self.label_from_region(channel, vbox, rect)
        elif mode == DataBrowser.MODE_ASK:
            menu = QMenu(self)
            zoom_act = menu.addAction("&Zoom")
            play_act = menu.addAction("&Play")
            analyze_act = menu.addAction("&Analyze")
            analyze_act.setEnabled(self.acts.analyze_region.isEnabled())
            analyze_act.setVisible(self.acts.analyze_region.isVisible())
            save_act = menu.addAction("&Save as")
            # One entry per category, so a single label can be made without
            # leaving whatever mode the reader is in.  The submenu is built
            # from `LabelSet.categories` and dispatched through a dict rather
            # than the chain of `act is` tests the four fixed entries use:
            # the categories are not a fixed set, and a chain would have to be
            # edited in two places every time one is added.
            label_acts = {}
            if self.labels.categories:
                submenu = menu.addMenu("&Label as")
                for i, category in enumerate(self.labels.categories):
                    key = self.category_key_for(i)
                    act = submenu.addAction(
                        f"{category.name}  {key}" if key else category.name
                    )
                    act.setToolTip(category_tip(category, key))
                    label_acts[act] = category.name
            act = menu.exec(self.region_menu_pos(vbox, scene_pos))
            if act is zoom_act:
                vbox.zoom_region(rect)
            elif act is play_act:
                self.play_region(rect.left(), rect.right())
            elif act is analyze_act:
                self.analyze_region(rect.left(), rect.right(), channel)
            elif act is save_act:
                self.save_region(rect.left(), rect.right())
            elif act in label_acts:
                self.set_current_category(label_acts[act])
                self.label_from_region(channel, vbox, rect)
        vbox.hide_region()

    def region_menu_pos(self, vbox, scene_pos):
        """Global position for the region menu, without asking the cursor."""
        if scene_pos is not None:
            scene = vbox.scene()
            views = scene.views() if scene is not None else []
            if views:
                view = views[0]
                return view.mapToGlobal(view.mapFromScene(scene_pos))
        return QCursor.pos()

    def play_scroll(self):
        if self.scroll_timer.isActive():
            self.scroll_timer.stop()
            self.scroll_step /= 2
        elif self.audio_timer.isActive():
            self.audio.stop()
            self.audio_timer.stop()
            for amarkers in self.audio_markers:
                for vmarker in amarkers:
                    vmarker.setValue(-1)
        else:
            self.play_window()

    def auto_scroll(self):
        if self.scroll_step == 0:
            self.scroll_step = 0.005
        elif self.scroll_step > 1.0:
            if self.scroll_timer.isActive():
                self.scroll_timer.stop()
            self.scroll_step = 0
            return
        else:
            self.scroll_step *= 2
        if not self.scroll_timer.isActive():
            self.scroll_timer.start(50)

    def scroll_further(self):
        trange = self.plot_ranges[Panel.times[0]]
        if trange.at_end():
            self.scroll_timer.stop()
            self.scroll_step /= 2
        else:
            twin = trange.r1[0] - trange.r0[0]
            self.set_times(trange.r0[0] + twin * self.scroll_step, twin)

    def set_audio(
        self, rate_fac=None, use_heterodyne=None, heterodyne_freq=None, dispatch=True
    ):
        if rate_fac is not None:
            self.audio_rate_fac = rate_fac
            if not dispatch:
                self.audiofacw.setCurrentText(f"{self.audio_rate_fac:g}")
        if use_heterodyne is not None:
            self.audio_use_heterodyne = use_heterodyne
        if heterodyne_freq is not None:
            self.audio_heterodyne_freq = float(heterodyne_freq)
            if not dispatch:
                self.audiohetfw.setValue(self.audio_heterodyne_freq)
        if dispatch:
            self.sigAudioChanged.emit(
                self.audio_rate_fac,
                self.audio_use_heterodyne,
                self.audio_heterodyne_freq,
            )

    def audio_channels(self) -> list:
        """Channels playback will send to the speakers, in output order.

        Falls back to the shown channels when the *current* channel is
        hidden, so pressing play never produces silence.  An explicitly
        chosen pair is never overridden that way: the point of choosing it
        is that hiding a lane does not change what you are listening to.
        """
        last = max(0, self.data.channels - 1)
        if self.audio_source == DataBrowser.AUDIO_PAIR:
            return [min(self.audio_left, last), min(self.audio_right, last)]
        shown = list(self.show_channels) or list(range(self.data.channels))
        if self.audio_source == DataBrowser.AUDIO_SELECTED:
            if self.current_channel in shown:
                return [self.current_channel]
            return [shown[0]]
        return shown

    def set_pair_row_visible(self, visible: bool) -> None:
        """Show or hide the L/R row *and its caption*.

        Hiding only the field left the word "Pair" floating beside nothing,
        which reads as a control that failed to load.

        Re-levels the bar afterwards, which it never used to.  `equalize`
        freezes every frame at the tallest group's height and
        `totalSizeHint` EXCLUDES a hidden widget, so a row revealed later is
        a row the frozen frame was not measured for -- it fitted only
        because the Audio group happened to have slack under the taller
        Annotations group, which is a coincidence of which group is tallest
        and not something to rely on.
        """
        if self.audiopairw is None:
            return
        self.audiopairw.setVisible(visible)
        for widget in getattr(self, "audiopairrow", None) or []:
            widget.setVisible(visible)
        QTimer.singleShot(0, self.equalize_parameter_bar)

    def set_audio_pair(self, left=None, right=None, dispatch: bool = True) -> None:
        """Choose the channel in each ear for `AUDIO_PAIR` playback."""
        last = max(0, self.data.channels - 1)
        if left is not None:
            self.audio_left = max(0, min(int(left), last))
        if right is not None:
            self.audio_right = max(0, min(int(right), last))
        for widget, value in (
            (self.audioleftw, self.audio_left),
            (self.audiorightw, self.audio_right),
        ):
            if widget is not None and widget.currentIndex() != value:
                blocked = widget.blockSignals(True)
                widget.setCurrentIndex(value)
                widget.blockSignals(blocked)
        if dispatch:
            self.sigAudioPairChanged.emit(self.audio_left, self.audio_right)

    def set_audio_source(self, source: str, dispatch: bool = True) -> None:
        """Choose between hearing the selected channel and hearing the mix."""
        if source not in (
            DataBrowser.AUDIO_SELECTED,
            DataBrowser.AUDIO_SHOWN,
            DataBrowser.AUDIO_PAIR,
        ):
            return
        self.audio_source = source
        if self.audiosrcw is not None:
            index = DataBrowser.AUDIO_SOURCES.index(source)
            if self.audiosrcw.currentIndex() != index:
                blocked = self.audiosrcw.blockSignals(True)
                self.audiosrcw.setCurrentIndex(index)
                self.audiosrcw.blockSignals(blocked)
        # progressive disclosure: the two channel pickers only exist as a
        # question once the pair mode is what is being asked about
        self.set_pair_row_visible(source == DataBrowser.AUDIO_PAIR)
        if dispatch:
            self.sigAudioSourceChanged.emit(source)

    #: Order Shift+P steps through.
    AUDIO_SOURCES = (AUDIO_SELECTED, AUDIO_PAIR, AUDIO_SHOWN)

    #: Human labels, index-aligned with AUDIO_SOURCES.
    AUDIO_SOURCE_LABELS = (
        "selected channel",
        "channel pair (L/R)",
        "all shown (stereo mix)",
    )

    def toggle_audio_source(self) -> None:
        """Step through the playback sources."""
        order = DataBrowser.AUDIO_SOURCES
        try:
            index = order.index(self.audio_source)
        except ValueError:
            index = 0
        self.set_audio_source(order[(index + 1) % len(order)])

    def play_region(self, t0, t1):
        data = self.data["filtered"] if "filtered" in self.data else self.data["data"]
        rate = data.rate
        i0 = int(np.round(t0 * rate))
        i1 = int(np.round(t1 * rate))
        if i0 < 0:
            i0 = 0
            t0 = 0.0
        if i1 > len(data):
            i1 = len(data)
            t1 = i1 / rate
        played = self.audio_channels()
        if self.audio_source == DataBrowser.AUDIO_SELECTED:
            playdata = np.asarray(data[i0:i1, played[0]], dtype=float).reshape(-1, 1)
        elif self.audio_source == DataBrowser.AUDIO_PAIR:
            # straight through: one channel per ear, nothing averaged
            playdata = np.zeros((i1 - i0, 2))
            playdata[:, 0] = data[i0:i1, played[0]]
            playdata[:, 1] = data[i0:i1, played[1]]
        else:
            n2 = (len(played) + 1) // 2
            playdata = np.zeros((i1 - i0, min(2, len(played))))
            playdata[:, 0] = np.mean(data[i0:i1, played[:n2]], 1)
            if len(played) > 1:
                playdata[:, 1] = np.mean(data[i0:i1, played[n2:]], 1)
        if self.audio_use_heterodyne:
            # multiply with heterodyne frequency:
            heterodyne = np.sin(
                2 * np.pi * self.audio_heterodyne_freq * np.arange(len(playdata)) / rate
            )
            playdata = (playdata.T * heterodyne).T
            # low-pass filter and downsample:
            fcutoff = 20000.0
            sos = butter(2, 20000, "low", output="sos", fs=rate)
            nstep = int(np.round(rate / (2 * fcutoff)))
            if nstep < 1:
                nstep = 1
            playdata = sosfiltfilt(sos, playdata, 0)[::nstep]
            rate /= nstep
        fade(playdata, rate / self.audio_rate_fac, 0.1)
        self.audio.play(playdata, rate / self.audio_rate_fac, blocking=False)
        self.audio_time = t0
        self.audio_tmax = t1
        self.audio_timer.start(50)
        # the cursor runs only on the channels actually being heard
        for c in range(data.channels):
            atime = self.audio_time if c in played else -1
            for vmarker in self.audio_markers[c]:
                vmarker.setValue(atime)

    def play_window(self):
        trange = self.plot_ranges[Panel.times[0]]
        self.play_region(trange.r0[0], trange.r1[0])

    def mark_audio(self):
        self.audio_time += 0.05 / self.audio_rate_fac
        for amarkers in self.audio_markers:
            for vmarker in amarkers:
                if vmarker.value() >= 0:
                    vmarker.setValue(self.audio_time)
        if self.audio_time > self.audio_tmax:
            self.audio_timer.stop()
            for amarkers in self.audio_markers:
                for vmarker in amarkers:
                    vmarker.setValue(-1)

    def analyze_region(self, t0, t1, channel):
        QApplication.setOverrideCursor(Qt.WaitCursor)
        if t0 < 0:
            t0 = 0
        if t1 > self.data.data.frames / self.data.data.rate:
            t1 = self.data.data.frames / self.data.data.rate
        traces = self.data.get_region(t0, t1, channel)
        for a in self.analyzers:
            a.analyze(t0, t1, channel, traces)
        QApplication.restoreOverrideCursor()
        if self.analysis_table is None:
            self.analysis_results()
        else:
            table = self.get_analysis_table()
            if len(table) > 0:
                self.analysis_table.setData(table)

    def get_analysis_table(self):
        table = []
        r = 0
        while True:
            row = {}
            for a in self.analyzers:
                if r < a.data.rows():
                    for c in range(a.data.columns()):
                        us = f"/{a.data.unit(c)}" if a.data.unit(c) else ""
                        header = a.data.label(c) + us
                        row.update({header: a.data[r, c]})
            if len(row) == 0:
                break
            table.append(row)
            r += 1
        return table

    def analysis_results(self):
        if self.analysis_table is not None:
            return
        if len(self.analyzers) == 0:
            return
        table = self.get_analysis_table()
        if len(table) == 0:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Audian analysis table")
        dialog.setWindowModality(Qt.NonModal)
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        vbox = QVBoxLayout(dialog)
        vbox.setContentsMargins(theme.S12, theme.S12, theme.S12, theme.S12)
        vbox.setSpacing(theme.S8)
        self.analysis_table = pg.TableWidget()
        style_result_table(self.analysis_table)
        self.analysis_table.setData(table)
        c = 0
        for a in self.analyzers:
            for i in range(a.data.columns()):
                self.analysis_table.setFormat(a.data.format(i), c)
                c += 1
        vbox.addWidget(self.analysis_table)
        buttons = QDialogButtonBox(
            QDialogButtonBox.Close | QDialogButtonBox.Save | QDialogButtonBox.Reset,
            dialog,
        )
        buttons.rejected.connect(dialog.reject)
        buttons.button(QDialogButtonBox.Reset).clicked.connect(self.clear_analysis)
        buttons.button(QDialogButtonBox.Save).clicked.connect(self.save_analysis)
        vbox.addWidget(buttons)
        dialog.finished.connect(lambda _: setattr(self, "analysis_table", None))
        dialog.adjustSize()
        dialog.show()

    def clear_analysis(self):
        if self.analysis_table is not None:
            self.analysis_table.clear()
        for a in self.analyzers:
            a.clear()

    def save_analysis(self):
        if len(self.analyzers) == 0 or self.analyzers[0].data.columns() == 0:
            return
        file_path = Path(self.data.file_path)
        file_name = file_path.stem + "-analysis.csv"
        if self.save_path[0] is None:
            file_path = file_path.with_name(file_name)
        else:
            file_path = self.save_path[0] / file_name
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save analysis as",
            os.fspath(file_path),
            "comma-separated values (*.csv)",
        )
        if not file_path:
            return
        table = self.analyzers[0].data
        for a in self.analyzers[1:]:
            for c in range(a.data.columns()):
                table.append(
                    a.data.label(c),
                    a.data.unit(c),
                    a.data.format(c),
                    value=a.data.data[c],
                )
        table.write(
            file_path,
            table_format="csv",
            delimiter=";",
            unit_style="header",
            column_numbers=None,
            sections=0,
        )
        self.save_path[0] = Path(file_path).parent

    def save_region(self, t0, t1):
        i0 = int(np.round(t0 * self.data.rate))
        i1 = int(np.round(t1 * self.data.rate))
        if i0 < 0:
            i0 = 0
            t0 = 0.0
        if i1 > len(self.data.data):
            i1 = len(self.data.data)
            t1 = i1 / self.data.rate
        name = Path(self.data.file_path).stem
        # if self.channel > 0:
        #    filename = f'{name}-{channel:d}-{t0:.4g}s-{t1s:.4g}s.wav'
        t0s = secs_to_str(t0)
        t1s = secs_to_str(t1)
        file_name = f"{name}-{t0s}-{t1s}.wav"
        formats = available_formats()
        for f in ["MP3", "OGG", "WAV"]:
            if f in formats:
                formats.remove(f)
                formats.insert(0, f)
        filters = ["All files (*)"] + [
            f"{f} files (*.{f}, *.{f.lower()})" for f in formats
        ]
        file_path = Path(self.data.file_path)
        if self.save_path[0] is None:
            file_path = file_path.with_name(file_name)
        else:
            file_path = self.save_path[0] / file_name
        file_path = QFileDialog.getSaveFileName(
            self, "Save region as", os.fspath(file_path), ";;".join(filters)
        )[0]
        if file_path:
            md = deepcopy(self.data.data.metadata())
            update_starttime(md, t0, self.data.rate)
            hkey = "CodingHistory"
            if "BEXT" in md:
                hkey = "BEXT." + hkey
            bext_code = bext_history_str(
                self.data.data.encoding, self.data.rate, self.data.channels
            )
            add_history(
                md,
                bext_code + f",T=cut out {t0s}-{t1s}: {Path(file_path).name}",
                hkey,
                bext_code + f",T={self.data.file_path}",
            )
            # Read off the FILE, not off a store.  These are the markers
            # the recording came with; audian neither writes them nor lets
            # them be edited, and routing them through a seconds-based copy
            # only cost a rounding trip in each direction.
            locs, labels = self.data.data.markers()
            sel = (locs[:, 0] + locs[:, 1] >= i0) & (locs[:, 0] <= i1)
            locs = locs[sel]
            labels = labels[sel]
            try:
                try:
                    rel_path = Path(file_path).relative_to(Path.cwd(), walk_up=True)
                except TypeError:
                    rel_path = Path(file_path).relative_to(Path.cwd())
            except ValueError:
                rel_path = file_path
            rel_path = os.fspath(rel_path)
            try:
                write_data(
                    file_path,
                    self.data.data[i0:i1, self.selected_channels],
                    self.data.rate,
                    self.data.data.ampl_max,
                    self.data.data.unit,
                    md,
                    locs,
                    labels,
                    encoding=self.data.data.encoding,
                )
                self.save_path[0] = Path(file_path).parent
                self.notify("info", f'saved region to "{rel_path}"')
            except PermissionError:
                self.notify(
                    "error", f'failed to save region to "{rel_path}": permission denied'
                )

    def save_window(self):
        trange = self.plot_ranges[Panel.times[0]]
        self.save_region(trange.r0[0], trange.r1[0])
