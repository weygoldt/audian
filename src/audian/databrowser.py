import os

import numpy as np
import pyqtgraph as pg

from contextlib import contextmanager
from pathlib import Path
from copy import deepcopy
from math import fabs, floor, log10
from typing import Optional
from scipy.signal import butter, sosfiltfilt

try:
    from PyQt5.QtCore import Signal
except ImportError:
    from PyQt5.QtCore import pyqtSignal as Signal
from PyQt5.QtCore import Qt, QEvent, QPoint, QSettings, QSize, QTimer
from PyQt5.QtGui import QCursor, QIcon, QKeySequence, QPainter, QPixmap
from PyQt5.QtWidgets import QApplication
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QGridLayout
from PyQt5.QtWidgets import QScrollArea, QSplitter, QFrame, QSlider
from PyQt5.QtWidgets import QLineEdit, QToolButton
from PyQt5.QtWidgets import QSizePolicy, QSpacerItem, QAbstractSpinBox
from PyQt5.QtWidgets import QAction, QMenu, QComboBox
from PyQt5.QtWidgets import QLabel, QTableView
from PyQt5.QtWidgets import QDialog, QDialogButtonBox, QFileDialog
from PyQt5.QtWidgets import QAbstractItemView, QGraphicsRectItem
from audioio import fade
from audioio import update_starttime
from audioio import bext_history_str, add_history
from thunderlab.datawriter import available_formats, write_data

from . import theme
from .data import Data
from .panels import Panel, Panels
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
from .markerdata import colors, color_value
from .markerdata import MarkerLabel, MarkerLabelsModel
from .markerdata import MarkerData, MarkerDataModel
from .analyzer import PlainAnalyzer, style_result_table
from .statisticsanalyzer import StatisticsAnalyzer


pg.setConfigOption("useNumba", True)


def marker_tip(x, y, data):
    s = ""
    if data:
        s += data + "\n"
    s += "time=" + secs_to_str(x)
    return s


def frame_widget(widget: QWidget) -> None:
    """Give a container widget the 1px hairline frame of the design system."""
    widget.setObjectName("audianGroup")
    widget.setStyleSheet(
        "#audianGroup { "
        f"border: {theme.HAIRLINE}px solid {theme.token('border')}; "
        f"border-radius: {theme.RADIUS_CONTROL}px; "
        "}"
    )


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
    label.setStyleSheet(f"color: {theme.token('fg.muted')};")
    return label


class ParameterGroup(QWidget):
    """A labelled and boxed group of related parameter widgets.

    A caption in small caps sits above a framed body into which
    parameter rows are added with `add_row()`.

    Groups are meant to be laid out side by side in equal grid columns and
    then `equalize()`d, so that the bottom bar reads as one band: every
    caption on one baseline, every frame the same height, every right edge
    on the same x.
    """

    def __init__(self, title: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(theme.S2)
        vbox.addWidget(caption_label(title))
        self.body = QWidget(self)
        frame_widget(self.body)
        self.grid = QGridLayout(self.body)
        self.grid.setContentsMargins(theme.S8, theme.S4, theme.S8, theme.S4)
        self.grid.setHorizontalSpacing(theme.S6)
        self.grid.setVerticalSpacing(theme.S2)
        # the fields, not their captions, take the width the column has:
        self.grid.setColumnStretch(1, 1)
        vbox.addWidget(self.body)
        self.rows = 0

    def add_row(self, label: str, shortcut: str, *widgets: QWidget) -> None:
        """Add a labelled row of widgets to the group."""
        self.grid.addWidget(caption_label(label, shortcut), self.rows, 0)
        for i, w in enumerate(widgets):
            self.grid.addWidget(w, self.rows, 1 + i)
        self.rows += 1

    @staticmethod
    def equalize(groups: "list[ParameterGroup]") -> None:
        """Give every group the same frame height.

        A group with two rows and one with three used to produce frames of
        185, 175 and 130 px whose captions sat on three different baselines
        - three separate boxes rather than one bar.  The shorter groups get
        the tallest one's height and keep their rows packed at the top.
        """
        if not groups:
            return
        height = max(g.body.sizeHint().height() for g in groups)
        for group in groups:
            # absorb the added height below the last row, not between rows:
            group.grid.setRowStretch(group.rows, 1)
            group.body.setFixedHeight(height)


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
        for i, label in enumerate(theme.SPECTROGRAM_MAP_LABELS):
            self.addItem(colormap_icon(i), label)
        self.setEditable(False)


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
        # top margin = the figure's own top margin (theme.style_figure uses
        # S4), so the badge sits on the top edge of the plot beside it
        # rather than floating in the middle of the lane:
        vbox.setContentsMargins(0, theme.S4, 0, 0)
        vbox.setSpacing(0)
        # The selection highlight belongs to the *controls*, not to the
        # lane's worth of rail below them: a 290 px raised block with 65 px
        # of controls at the top of it highlights mostly nothing.  The lane
        # it points at is marked in the plot itself, by a raised view box.
        self.card = QWidget(self)
        self.card.setObjectName("railCard")
        self.card.setAttribute(Qt.WA_StyledBackground, True)
        card = QVBoxLayout(self.card)
        card.setContentsMargins(theme.S4, theme.S2, theme.S4, theme.S2)
        card.setSpacing(theme.S2)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(theme.S4)
        self.number = QLabel(f"CH {channel:02d}")
        self.number.setFont(theme.font_mono(theme.SIZE_SMALL_PT, bold=True))
        self.number.setMinimumWidth(
            theme.mono_metrics(theme.SIZE_SMALL_PT).horizontalAdvance("CH 00 ")
        )
        top.addWidget(self.number)
        self.solo_button = self._button("S", "Solo this channel")
        self.solo_button.clicked.connect(lambda: self.browser.toggle_solo(self.channel))
        top.addWidget(self.solo_button)
        self.mute_button = self._button("M", "Hide this channel")
        self.mute_button.clicked.connect(lambda: self.browser.toggle_mute(self.channel))
        top.addWidget(self.mute_button)
        self.level = QLabel()
        self.level.setFont(theme.font_mono(theme.SIZE_SMALL_PT))
        self.level.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.level.setToolTip("Peak level of the visible window, dB full scale")
        # sized from the widest string it ever holds, so the minus sign is
        # never the character that gets elided away:
        self.level.setMinimumWidth(
            theme.mono_metrics(theme.SIZE_SMALL_PT).horizontalAdvance("-100.0 dB")
        )
        top.addWidget(self.level, 1)
        card.addLayout(top)

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
        button.setText(text)
        button.setCheckable(True)
        button.setToolTip(tip)
        button.setFont(theme.font_mono(theme.SIZE_SMALL_PT, bold=True))
        button.setFocusPolicy(Qt.NoFocus)
        # a one glyph toggle does not need the 45 px the generic tool
        # button padding gives it - that width belongs to the level readout
        button.setFixedSize(theme.S24, theme.CONTROL_HEIGHT - theme.S4)
        return button

    def rename(self) -> None:
        self.browser.channel_names[self.channel] = self.name.text()

    def set_peak(self, peak: float, ampl_max: float) -> None:
        """Show the peak level of the visible window in dB full scale."""
        self.peak = peak
        if peak <= 0 or ampl_max <= 0:
            self.level.setText("-inf dB")
            return
        db = 20 * np.log10(min(1.0, peak / ampl_max))
        self.level.setText(f"{db:5.1f} dB")

    def update_state(self) -> None:
        """Repaint solo/mute state and the current-channel emphasis."""
        current = self.channel == self.browser.current_channel
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
    # perceptually uniform maps first, jet last - see theme.py:
    color_maps = theme.SPECTROGRAM_MAPS

    # width of the channel rail and the number of visible channels above
    # which the spectrogram collapses onto the current channel only:
    # Wide enough for the compact single-line row: 'CH 15' + solo + mute +
    # '-10.7 dB' in the mono face, with the S4 margins.  Measured, not
    # guessed - at 160 px the badge and the minus sign were both clipped.
    RAIL_WIDTH = 188
    MAX_SPECTROGRAM_CHANNELS = 4

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

    sigRangesChanged = Signal(object, object)
    sigFilenameChanged = Signal(object, str)
    sigResolutionChanged = Signal()
    sigColorMapChanged = Signal()
    sigFilterChanged = Signal()
    sigEnvelopeChanged = Signal()
    sigTraceChanged = Signal(object, object, object)
    sigAudioChanged = Signal(object, object, object)

    def __init__(
        self,
        file_path,
        load_kwargs,
        plugins,
        channels,
        audio,
        acts,
        save_path,
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
        self.selected_channels = []
        # non-destructive filters over show_channels:
        self.solo_channels = []
        self.muted_channels = []
        self.maximized_channel = None
        self.channel_order = []
        self.channel_names = {}

        # view:
        self.setting = False

        self.trace_fracs = {0: 1, 1: 1, 2: 0.5, 3: 0.25, 4: 0.15}

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
        self.marker_data = MarkerData()
        self.marker_model = MarkerDataModel(self.marker_data)
        self.marker_labels = []
        self.marker_labels.append(MarkerLabel("start", "s", "yellow"))
        self.marker_labels.append(MarkerLabel("end", "e", "blue"))
        self.marker_labels_model = MarkerLabelsModel(
            self.marker_labels, self.acts, self
        )
        self.marker_orig_acts = []

        # plots:
        self.color_map = self.read_color_map_setting()
        self.figs = []  # all GraphicsLayoutWidgets - one for each channel
        self.borders = []
        # the stack's one shared time axis, in a row of its own below the
        # last lane, and the amplitude scale of the selected lane:
        self.taxis = None
        self.taxis_fig = None
        self.taxis_strip = None
        self.taxis_margins = None
        self.y_readout = None
        self.lane_height = theme.CHANNEL_MIN_HEIGHT
        self.stack_pane = None
        self.stack_spacer_row = 0
        self.sig_proxies = []
        # nested lists (channel, panel):
        self.axs = []  # all plots
        self.axgs = []  # plots with grids
        # lists with marker labels and regions:
        self.trace_labels = []  # labels on traces
        self.trace_region_labels = []  # regions with labels on traces
        self.spec_labels = []  # labels on spectrograms
        self.spec_region_labels = []  # regions with labels on spectrograms
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
        if index < 0 or index >= len(theme.SPECTROGRAM_MAPS):
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
        self.marker_data.file_path = self.data.file_path

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

        # load marker data:
        locs, labels = self.data.data.markers()
        self.marker_data.set_markers(locs, labels, self.data.rate)
        if len(labels) > 0:
            lbls = np.unique(labels[:, 0])
            for i, lbl in enumerate(lbls):
                self.marker_labels.append(
                    MarkerLabel(
                        lbl, lbl[0].lower(), list(colors.keys())[i % len(colors)]
                    )
                )

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
        # lists with marker labels and regions:
        self.trace_labels = []  # labels on traces
        self.trace_region_labels = []  # regions with labels on traces
        self.spec_labels = []  # labels on spectrograms
        self.spec_region_labels = []  # regions with labels on spectrograms
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
            theme.style_figure(fig)
            fig.setMinimumHeight(theme.CHANNEL_DENSE_HEIGHT)
            fig.setVisible(c in self.show_channels)

            rail_row = ChannelRailRow(c, self)
            self.rail_rows.append(rail_row)
            self.stack_grid.addWidget(rail_row, c, 0)
            self.stack_grid.addWidget(fig, c, 1)
            self.figs.append(fig)

            # border:
            border = QGraphicsRectItem()
            border.setZValue(-1000)
            border.setPen(theme.border_pen(selected=True))
            fig.scene().addItem(border)
            fig.sigDeviceRangeChanged.connect(self.update_borders)
            self.borders.append(border)

            # setup plot panels:
            row = 0
            for name in reversed(self.panels):
                panel = self.panels[name]
                # spacer:
                if panel.is_spacer():
                    axsp = fig.addLayout(row=row, col=0)
                    axsp.setContentsMargins(0, 0, 0, 0)
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
                    axt.getViewBox().sigRangeChanged.connect(
                        lambda *a, c=c: self.update_y_readout(c)
                    )
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
                    # add marker labels:
                    labels = []
                    for mlabel in self.marker_labels:
                        label = pg.ScatterPlotItem(
                            size=theme.S12,
                            hoverSize=2 * theme.S12,
                            hoverable=True,
                            pen=pg.mkPen(None),
                            brush=theme.brush(color_value(mlabel.color)),
                        )
                        axt.addItem(label)
                        labels.append(label)
                    self.trace_labels.append(labels)
                    self.trace_region_labels.append([])
                # spectrogram:
                elif panel.is_spectrogram():
                    axs = SpectrogramPlot(
                        panel.ax_spec,
                        c,
                        self,
                        xwidth,
                        theme.SPECTROGRAM_MAPS[self.color_map],
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
                    # add marker labels:
                    labels = []
                    for mlabel in self.marker_labels:
                        label = pg.ScatterPlotItem(
                            size=theme.S12,
                            pen=pg.mkPen(None),
                            brush=theme.brush(color_value(mlabel.color)),
                        )
                        axs.addItem(label)
                        labels.append(label)
                    self.spec_labels.append(labels)
                    self.spec_region_labels.append([])
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

        # add marker data to plot:
        labels = [m.label for m in self.marker_labels]
        for t1, ddt, ls, ts in zip(
            self.marker_data.times,
            self.marker_data.delta_times,
            self.marker_data.labels,
            self.marker_data.texts,
        ):
            lidx = labels.index(ls)
            for c, tl in enumerate(self.trace_labels):
                ds = ts if ts else ls
                t0 = t1 - ddt
                idx1 = int(t1 * self.data.rate)
                if ddt > 0:
                    mcolor = color_value(self.marker_labels[lidx].color)
                    region = pg.LinearRegionItem(
                        (t0, t1),
                        orientation="vertical",
                        pen=theme.pen(mcolor),
                        brush=theme.brush(mcolor, 0.35),
                        movable=False,
                        span=(0.02, 0.05),
                    )
                    region.setZValue(-10)
                    self.panels["trace"].add_item(region, c, False)
                    # text = pg.TextItem(ds, color='green', anchor=(0, 0))
                    # text.setPos(t0, 0)
                    # self.panels['trace'].add_item(text, c, False)
                    self.trace_region_labels[c].append(region)
                else:
                    if idx1 >= len(self.data.data):
                        idx1 = len(self.data.data) - 1
                    if idx1 >= 0:
                        tl[lidx].addPoints(
                            (t1,),
                            (self.data.data[idx1, c],),
                            data=(ds,),
                            tip=marker_tip,
                        )
            for c, sl in enumerate(self.spec_labels):
                if ddt > 0:
                    # TODO: self.spec_region_labels
                    sl[lidx].addPoints(
                        (t0, t1), (0.0, 0.0), data=(f"start: {ds}", f"end: {ds}")
                    )
                else:
                    sl[lidx].addPoints((t1,), (0.0,), data=(ds,), tip=marker_tip)

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
        pane.addWidget(self.taxis_strip, 0)
        self.splitter.addWidget(stack_pane)
        self.vbox.addWidget(self.splitter)

        rail_act = QAction("Toggle channel rail", self)
        rail_act.setShortcut("F7")
        rail_act.triggered.connect(self.toggle_rail)
        self.addAction(rail_act)

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
        self.y_readout = QLabel()
        self.y_readout.setFont(theme.font_mono(theme.SIZE_SMALL_PT))
        self.y_readout.setStyleSheet(f"color: {theme.token('fg.muted')};")
        self.y_readout.setAlignment(Qt.AlignRight | Qt.AlignTop)
        self.y_readout.setContentsMargins(theme.S4, theme.S4, theme.S4, 0)
        self.y_readout.setToolTip("Amplitude range shown by every lane")
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
        """Build the bottom bar of labelled parameter groups.

        Replaces the row of single-letter labels ('N:', 'O:', ' L:') by
        three boxed groups with captions that carry their own keyboard
        shortcut, so that shortcuts are visible instead of hidden in
        tool tips.
        """
        self.parambar = QWidget(self)
        grid = QGridLayout(self.parambar)
        grid.setContentsMargins(theme.S8, theme.S6, theme.S8, theme.S6)
        grid.setHorizontalSpacing(theme.S16)
        grid.setVerticalSpacing(0)
        groups = []

        nyquist = self.data.rate / 2

        # filter:
        if "filtered" in self.data:
            filtered = self.data["filtered"]
            group = ParameterGroup("Filter", self.parambar)
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
            group.add_row("High-pass", "H / ⇧H", self.hpsliderw, self.hpfw)

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
            group.add_row("Low-pass", "L / ⇧L", self.lpsliderw, self.lpfw)

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
            group = ParameterGroup("Spectrogram", self.parambar)
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
            group.add_row("Overlap", "O / ⇧O", self.ofracw, self.ofraclabelw)

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
            group = ParameterGroup("Envelope", self.parambar)
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
            group.add_row("Cutoff", "E / ⇧E", self.envsliderw, self.envfw)
            groups.append(group)
        else:
            self.envfw = None
            self.envsliderw = None
            self.acts.link_envelope.setEnabled(False)
            self.acts.show_envelope.setEnabled(False)
            self.acts.envelope_up.setEnabled(False)
            self.acts.envelope_down.setEnabled(False)

        # audio playback:
        group = ParameterGroup("Audio", self.parambar)
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

        # One band, not three boxes: equal columns on a fixed gutter, every
        # caption on one baseline and every frame the same height, so the
        # right edges line up instead of landing wherever the widest field
        # in each group happened to put them.
        for column, group in enumerate(groups):
            group.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
            grid.addWidget(group, 0, column, Qt.AlignTop)
            grid.setColumnStretch(column, 1)
        ParameterGroup.equalize(groups)
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
            theme.style_figure(figure)
        for axs in self.axs:
            for ax in axs:
                if hasattr(ax, "apply_theme"):
                    ax.apply_theme()
                elif hasattr(ax, "polish"):
                    ax.polish()
        for border in self.borders:
            border.setPen(theme.border_pen(selected=True))
        if self.datafig is not None:
            if hasattr(self.datafig, "apply_theme"):
                self.datafig.apply_theme()
            else:
                self.datafig.polish()
        for row in self.rail_rows:
            row.update_state()
        # style_plotitem() has just reset every view box to bg.plot:
        self.update_current_plot()

    # --- channel rail -----------------------------------------------------

    def toggle_rail(self) -> None:
        """Show or hide the channel rail (F7)."""
        self.rail_visible = not self.rail_visible
        for row in self.rail_rows:
            row.setVisible(self.rail_visible and row.channel in self.visible_channels())
        self.stack_grid.setColumnMinimumWidth(
            0, DataBrowser.RAIL_WIDTH if self.rail_visible else 0
        )
        if self.y_readout is not None:
            self.y_readout.setFixedWidth(
                DataBrowser.RAIL_WIDTH if self.rail_visible else 0
            )

    def visible_channels(self) -> list:
        """Channels actually drawn, in display order.

        `show_channels` stays the user's channel selection; solo and mute
        are non-destructive filters on top of it, and maximising a channel
        temporarily hides the others.
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

    def rail_clicked(self, channel: int, extend: bool) -> None:
        """Select a channel from the rail, optionally extending the range."""
        if extend:
            lo = min(channel, self.current_channel)
            hi = max(channel, self.current_channel)
            self.add_to_selected_channels(list(range(lo, hi + 1)))
        else:
            self.selected_channels = [channel]
        self.current_channel = channel
        self.update_borders()
        self.update_rail()
        self.adjust_layout(self.width(), self.height())

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
        """Push solo/mute/order/maximise onto the widgets."""
        visible = self.visible_channels()
        for c in range(len(self.figs)):
            self.figs[c].setVisible(c in visible)
            self.rail_rows[c].setVisible(self.rail_visible and c in visible)
        if self.current_channel not in visible and visible:
            self.current_channel = visible[0]
        self.update_rail()
        self.update_borders()
        self.adjust_layout(self.width(), self.height())

    def update_rail(self) -> None:
        for row in self.rail_rows:
            row.name.setText(self.channel_names.get(row.channel, ""))
            row.update_state()
        self.update_current_plot()

    def update_current_plot(self) -> None:
        """Mark the current channel on its lanes.

        Three cues, none of them colour alone: the view box interior of the
        selected lane is lifted one step off the plot ground
        (`bg.surface` instead of `bg.plot`), its caption goes bold and
        primary, and the channel rail runs a 2 px rule down its row.  In a
        sixteen lane stack the raised ground is the one that works at a
        glance, because it is the whole lane rather than a glyph in it.
        """
        for c, axs in enumerate(self.axs):
            current = c == self.current_channel
            for ax in axs:
                if hasattr(ax, "set_current"):
                    ax.set_current(current)
                view = ax.getViewBox() if hasattr(ax, "getViewBox") else None
                if view is not None:
                    view.setBackgroundColor(
                        theme.qcolor("bg.surface" if current else "bg.plot")
                    )
        self.update_y_readout()
        # the navigator draws one channel in single mode - keep it on the
        # channel the user is actually looking at:
        if self.datafig is not None and hasattr(self.datafig, "set_channel"):
            self.datafig.set_channel(self.current_channel)

    def set_navigator_mode(self, mode: str) -> None:
        """Switch the navigator between one row and the per-channel stack."""
        if self.datafig is None or not hasattr(self.datafig, "set_mode"):
            return
        self.datafig.set_mode(mode)
        self.adjust_layout(self.width(), self.height())

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
        self.set_readout(
            "a",
            f"A {arange.r0[channel]:.3g}…{arange.r1[channel]:.3g}",
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
        self.cross_hair = checked
        if self.cross_hair:
            # disable existing key shortcuts:
            self.marker_orig_acts = []
            for mlabel in self.marker_labels:
                ks = QKeySequence(mlabel.key_shortcut)
                for a in dir(self.acts):
                    act = getattr(self.acts, a)
                    if isinstance(act, QAction) and act.shortcut() == ks:
                        self.marker_orig_acts.append((act.shortcut(), act))
                        act.setShortcut(QKeySequence())
                        break
            # setup marker actions:
            for mlabel in self.marker_labels:
                if mlabel.action is None:
                    mlabel.action = QAction(mlabel.label, self)
                    mlabel.action.triggered.connect(
                        lambda x, label=mlabel.label: self.store_marker(label)
                    )
                    self.addAction(mlabel.action)
                mlabel.action.setShortcut(mlabel.key_shortcut)
                mlabel.action.setEnabled(True)
            self.plot_ranges.clear_marker()
            self.plot_ranges.clear_stored_marker()
        else:
            for field in ("t", "dt", "a", "f", "p"):
                self.set_readout(field, None)
            self.plot_ranges.clear_marker()
            self.plot_ranges.clear_stored_marker()
            self.plot_ranges.update_crosshair()
            # disable marker actions:
            for mlabel in self.marker_labels:
                if mlabel.action is not None:
                    mlabel.action.setEnabled(False)
            # restore key shortcuts:
            for key, act in self.marker_orig_acts:
                act.setShortcuts(key)
            self.marker_orig_acts = []

    def set_marker(self):
        pass
        """
        if not self.marker_ax is None and not self.marker_time is None:
            if not self.marker_ampl is None:
                self.marker_ax.prev_marker.setData((self.marker_time,),
                                                   (self.marker_ampl,))
            if not self.marker_freq is None:
                self.marker_ax.prev_marker.setData((self.marker_time,),
                                                   (self.marker_freq,))
        """

    def store_marker(self, label=""):
        """
        self.marker_model.add_data(self.marker_channel,
                                   self.marker_time, self.marker_ampl,
                                   self.marker_freq,
                                   self.marker_power,self.delta_time,
                                   self.delta_ampl, self.delta_freq,
                                   self.delta_power, label)
        # add new label point to scatter plots:
        labels = [l.label for l in self.marker_labels]
        if len(label) > 0 and label in labels and \
           self.marker_time is not None:
            lidx = labels.index(label)
            for c, tl in enumerate(self.trace_labels):
                if c == self.marker_channel and self.marker_ampl is not None:
                    tl[lidx].addPoints((self.marker_time,),
                                      (self.marker_ampl,),
                                       tip=marker_tip)
                else:
                    tidx = int(self.marker_time*self.data.rate)
                    tl[lidx].addPoints((self.marker_time,),
                                       (self.data.data[tidx, c],),
                                       tip=marker_tip)
            for c, sl in enumerate(self.spec_labels):
                y = 0.0 if self.marker_freq is None else self.marker_freq
                sl[lidx].addPoints((self.marker_time,), (y,))
        """

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
            if not panel.is_used() or not panel.is_visible(channel):
                continue
            ax = panel.axs[channel]
            if not ax.sceneBoundingRect().contains(evt[0]):
                continue
            # remember it for axis_under_pointer(), whether or not the
            # cross hair is on:
            self.hover_panel = panel
            self.hover_channel = channel
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

    def label_editor(self):
        self.marker_labels_model.set(self.marker_labels)
        self.marker_labels_model.edit(self)

    def marker_table(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Audian marker table")
        dialog.setWindowModality(Qt.NonModal)
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        vbox = QVBoxLayout(dialog)
        vbox.setContentsMargins(theme.S12, theme.S12, theme.S12, theme.S12)
        vbox.setSpacing(theme.S8)
        view = QTableView(dialog)
        view.setModel(self.marker_model)
        view.setFont(theme.font_mono())
        view.resizeColumnsToContents()
        view.setSelectionMode(QAbstractItemView.ContiguousSelection)
        vbox.addWidget(view)
        buttons = QDialogButtonBox(
            QDialogButtonBox.Close | QDialogButtonBox.Save | QDialogButtonBox.Reset,
            dialog,
        )
        buttons.rejected.connect(dialog.reject)
        buttons.button(QDialogButtonBox.Reset).clicked.connect(self.marker_model.clear)
        buttons.button(QDialogButtonBox.Save).clicked.connect(
            lambda x: self.marker_model.save(self)
        )
        vbox.addWidget(buttons)
        # no maximum width: a wide marker table must stay readable, and a
        # width cap fights a tiling compositor.
        dialog.adjustSize()
        dialog.show()

    def update_borders(self, rect=None):
        """Frame the current channel only.

        A 4px grey box around all sixteen channels conveys nothing; a
        1px primary frame around the one current channel does. The plot
        additionally bolds its channel label, so the cue is not colour
        alone.
        """
        for c in range(len(self.figs)):
            self.borders[c].setRect(
                0, 0, self.figs[c].size().width(), self.figs[c].size().height()
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
        """
        for name in ("top", "bottom"):
            if name not in plot.axes:
                continue
            axis = plot.getAxis(name)
            axis.setStyle(showValues=False, tickLength=0)
            axis.showLabel(False)
        if "right" in plot.axes:
            plot.getAxis("right").setStyle(showValues=False, tickLength=0)
        if "left" in plot.axes:
            axis = plot.getAxis("left")
            axis.setWidth(left_width)
            # negative: pyqtgraph points ticks into the plot, and that is
            # where a y tick belongs as long as it is labelled.
            axis.setStyle(showValues=values, tickLength=-theme.S4 if values else 0)

    def update_y_readout(self, channel: int = -1) -> None:
        """Write the stack's one amplitude scale into the rail corner.

        Every lane is the same height and, under a shared y range, shows
        the same span, so one scale describes the whole stack.  Sixteen
        copies of it say nothing more - which is what the sixteen ladders
        of *unlabelled* y ticks were, at 56 px of every lane.

        It goes in the corner the rail and the time axis row make between
        them, where the axis label of a plot belongs and where nothing was
        drawn before, rather than over a trace: at 34 px a lane has no
        room to spare for two numbers on top of the data.
        """
        if self.y_readout is None:
            return
        if channel >= 0 and channel != self.current_channel:
            return
        plot = self.trace_plot(self.current_channel)
        if plot is None:
            return
        y0, y1 = plot.getViewBox().viewRange()[1]
        self.y_readout.setText(f"Y {y0:+.3g} \u2026 {y1:+.3g}")

    def trace_plot(self, channel: int) -> Optional[TimePlot]:
        """The channel's first visible trace plot, or None."""
        for panel in self.panels.values():
            if not panel.is_trace() or len(panel.axs) <= channel:
                continue
            if panel.axs[channel].isVisible():
                return panel.axs[channel]
        return None

    def spectrogram_channels(self, channels: list[int]) -> list[int]:
        """Channels that get a spectrogram row.

        Sixteen spectrogram stripes of 32px each are unreadable and the
        rotated frequency label overprints the ticks. Above a handful of
        visible channels the spectrogram collapses onto a single focused
        panel that follows the current channel.
        """
        if self.show_specs <= 0 or not channels:
            return []
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
        lane_h = int(
            max(
                theme.CHANNEL_DENSE_HEIGHT,
                (available - spec_total) // n,
            )
        )
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
        plot = self.trace_plot(channels[0])
        if plot is None:
            return
        view = plot.getViewBox()
        if self.taxis.linkedView() is not view:
            self.taxis.linkToView(view)
        self.taxis.setRange(*view.viewRange()[0])

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
        self.panels.show_spacers(channels[0])
        xheight = self.fontMetrics().ascent()
        lane_h, axis_h, spec_channels, dense = self.lane_geometry(height)
        hidden = self.show_specs > 0 and len(spec_channels) < len(channels)
        if hidden and not self.spec_warned:
            self.notify("warning", "spectrogram hidden - too many channels visible")
        self.spec_warned = hidden
        # what to plot:
        ntraces = 0
        nspacers = 0
        c = channels[0]
        for panel in self.panels.values():
            if panel.is_visible(c) and (
                panel.is_spacer() or panel.has_visible_traces(c)
            ):
                if panel.is_spacer():
                    nspacers += 1
                elif panel.is_trace():
                    ntraces += 1
        nrows = len(channels)
        # the y gutter is a stack-wide decision so that every view box
        # starts at the same x; it is only reclaimed while no spectrogram
        # needs it, because a spectrogram without a frequency scale is
        # worse than a narrow one:
        left_width = 0 if dense else theme.AXIS_LEFT_WIDTH
        trace_frac = self.trace_fracs[self.show_specs]
        for c in range(self.data.channels):
            if c not in channels:
                for panel in self.panels.values():
                    if not panel.is_power():
                        panel.axs[c].setVisible(False)
                continue
            show_spec = c in spec_channels
            nspecs = 1 if show_spec else 0
            # per channel, not once for the first one: with a spectrogram on
            # the focused channel only, sizing every other lane's rows
            # against the focused lane's height tells the trace rows they
            # are 154 px tall when they are 34, and they then keep tick
            # values that do not fit.
            fig_height = max(1.0, float(lane_h))
            if show_spec:
                fig_height += theme.SPECTROGRAM_MIN_HEIGHT
            # What is left for the traces once the spectrogram has taken
            # its fixed share.  The row *minima* have to sum to the figure
            # height or GraphicsLayout overflows instead of shrinking, and
            # the bottom of the last row is silently clipped - which reads
            # as a rectified waveform rather than as a layout bug.
            trace_min = max(
                1,
                int(
                    (fig_height - (theme.SPECTROGRAM_MIN_HEIGHT if show_spec else 0))
                    // max(1, ntraces)
                ),
            )
            spec_height = (
                fig_height / (nspecs + trace_frac * ntraces)
                if (nspecs + trace_frac * ntraces) > 0
                else fig_height
            )
            trace_height = trace_frac * spec_height
            if show_spec and self.show_powers:
                self.figs[c].ci.layout.setColumnFixedWidth(1, 0.1 * width)
            else:
                self.figs[c].ci.layout.setColumnFixedWidth(1, 0)
            for panel in self.panels.values():
                if panel.is_power():
                    continue
                visible = panel.is_spacer() or panel.has_visible_traces(c)
                if panel.is_spectrogram():
                    visible = visible and show_spec
                    panel.axs[c].setVisible(visible)
                elif panel.is_trace():
                    panel.axs[c].setVisible(self.show_traces)
                    visible = visible and self.show_traces
                if not visible:
                    self.figs[c].ci.layout.setRowMinimumHeight(panel.row, 0)
                    self.figs[c].ci.layout.setRowStretchFactor(panel.row, 0)
                    continue
                if panel.is_spacer():
                    row_height = 0
                    stretch = 0
                elif panel.is_spectrogram():
                    row_height = theme.SPECTROGRAM_MIN_HEIGHT
                    stretch = int(100 * spec_height)
                else:
                    row_height = trace_min
                    stretch = int(100 * trace_height)
                if not panel.is_spacer():
                    # the tick values a row can carry follow the height of
                    # that row, not of the lane: a spectrogram is 120 px
                    # tall even in a stack whose traces are 34 px:
                    self.lane_axes(
                        panel.axs[c],
                        row_height >= TICK_VALUES_MIN_HEIGHT,
                        left_width,
                    )
                self.figs[c].ci.layout.setRowMinimumHeight(panel.row, row_height)
                self.figs[c].ci.layout.setRowStretchFactor(panel.row, max(1, stretch))
        self.update_stretches(height)
        self.link_time_axis()
        self.align_time_axis()
        self.update_y_readout()
        # fix full data plot:
        if self.datafig is not None:
            data_height = 5 * xheight // 2 if nrows <= 1 else 3 * xheight // 2
            self.datafig.update_layout(channels, data_height)
            self.datafig.setVisible(self.show_fulldata)
        self.size_splitter()
        # update:
        for c in channels:
            self.figs[c].update()

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
        plot = self.trace_plot(channels[0])
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

    def set_ranges(self, axspec, r0=None, r1=None):
        if self.setting:
            return
        channels = (
            list(range(self.data.channels))
            if self.y_mode == DataBrowser.y_shared
            else self.selected_channels
        )
        with self.updating():
            self.plot_ranges[axspec].set_ranges(
                r0, r1, None, channels, self.isVisible()
            )
        self.report_y_range()

    def apply_ranges(self, amplitudefunc, axspec):
        with self.updating():
            getattr(self.plot_ranges, amplitudefunc)(
                axspec, self.selected_channels, self.isVisible()
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
        if self.color_map < 0 or self.color_map >= len(theme.SPECTROGRAM_MAPS):
            self.color_map = theme.DEFAULT_SPECTROGRAM_MAP
        for panel in self.panels.values():
            if panel.is_spectrogram():
                panel.set_colormap(theme.SPECTROGRAM_MAPS[self.color_map])
        if self.cmapw is not None and self.cmapw.currentIndex() != self.color_map:
            blocked = self.cmapw.blockSignals(True)
            self.cmapw.setCurrentIndex(self.color_map)
            self.cmapw.blockSignals(blocked)
        QSettings("audian", "audian").setValue("spectrogram/colormap", self.color_map)
        if dispatch:
            self.sigColorMapChanged.emit()

    def color_map_cycler(self) -> None:
        self.set_color_map((self.color_map + 1) % len(theme.SPECTROGRAM_MAPS))

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

    def next_channel(self):
        idx = self.show_channels.index(self.current_channel)
        if idx + 1 < len(self.show_channels):
            self.current_channel = self.show_channels[idx + 1]
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
        idx = self.show_channels.index(self.current_channel)
        if idx > 0:
            self.current_channel = self.show_channels[idx - 1]
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
        idx = self.show_channels.index(self.current_channel)
        if idx + 1 < len(self.show_channels):
            self.current_channel = self.show_channels[idx + 1]
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
        idx = self.show_channels.index(self.current_channel)
        if idx > 0:
            self.current_channel = self.show_channels[idx - 1]
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
            visible = self.visible_channels()
            for c in range(self.data.channels):
                self.figs[c].setVisible(c in visible)
                self.rail_rows[c].setVisible(self.rail_visible and c in visible)
                self.acts.channels[c].setChecked(c in self.show_channels)
            self.update_rail()
            self.adjust_layout(self.width(), self.height())
            self.update_borders()

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
            self.show_specs = specs
        if powers is not None:
            self.show_powers = powers
        if cbars is not None:
            self.show_cbars = cbars
        if fulldata is not None:
            self.show_fulldata = fulldata
        for panel in self.panels.values():
            if panel.is_trace():
                panel.set_visible(self.show_traces)
            elif panel.is_spectrogram():
                panel.set_visible(self.show_specs > 0)
                panel.set_cbar_visible(self.show_specs > 0 and self.show_cbars)
            elif panel.is_power():
                panel.set_visible(self.show_specs > 0 and self.show_powers)
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
        self.show_specs += 1
        if self.show_specs > 4:
            self.show_specs = 0
        if self.show_specs == 0:
            self.show_traces = True
        self.set_panels()

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
        self.region_mode = mode

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
        elif mode == DataBrowser.MODE_ASK:
            menu = QMenu(self)
            zoom_act = menu.addAction("&Zoom")
            play_act = menu.addAction("&Play")
            analyze_act = menu.addAction("&Analyze")
            analyze_act.setEnabled(self.acts.analyze_region.isEnabled())
            analyze_act.setVisible(self.acts.analyze_region.isVisible())
            save_act = menu.addAction("&Save as")
            act = menu.exec(self.region_menu_pos(vbox, scene_pos))
            if act is zoom_act:
                vbox.zoom_region(rect)
            elif act is play_act:
                self.play_region(rect.left(), rect.right())
            elif act is analyze_act:
                self.analyze_region(rect.left(), rect.right(), channel)
            elif act is save_act:
                self.save_region(rect.left(), rect.right())
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
        n2 = (len(self.show_channels) + 1) // 2
        playdata = np.zeros((i1 - i0, min(2, len(self.show_channels))))
        playdata[:, 0] = np.mean(data[i0:i1, self.show_channels[:n2]], 1)
        if len(self.show_channels) > 1:
            playdata[:, 1] = np.mean(data[i0:i1, self.show_channels[n2:]], 1)
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
        for c in range(data.channels):
            atime = self.audio_time if c in self.show_channels else -1
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
            locs, labels = self.marker_data.get_markers(self.data.rate)
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
