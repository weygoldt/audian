"""FullTracePlot

The navigator strip below the data plots: a compressed min/max overview of the
whole recording with a draggable region that marks the visible time window.

Two rendering modes:

- ``'single'`` (the default): one row of :data:`theme.NAVIGATOR_HEIGHT` pixels
  showing the currently selected channel.  This is what a 16-channel electrode
  recording wants -- sixteen stacked overviews are noise, not signal.
- ``'all'``: the historical per-channel stack, behind an explicit toggle.

Everything the mouse draws on screen lives *inside* the graphics scene.  No
child widget of this class is ever a window: on Wayland a child QLabel of a
parentless widget is promoted to a real xdg_toplevel and every mouse move maps
and unmaps a compositor surface.
"""

import sys
from math import floor
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QPainter, QPainterPath
from PySide6.QtWidgets import QSizePolicy

from . import activity, theme
from .compresseddata import CompressedData
from .timeaxisitem import TimeAxisItem


def secs_to_str(time, msec_level=10, precision=10):
    days = time // (24 * 3600)
    time -= (24 * 3600) * days
    hours = time // 3600
    time -= 3600 * hours
    mins = time // 60
    time -= 60 * mins
    secs = int(floor(time))
    time -= secs
    msecs = 1000 * time
    if msecs >= 100:
        msec_str = f"{msecs:03.0f}ms"
    elif msecs >= 10:
        msec_str = f"{msecs:04.1f}ms"
    elif msecs >= 1:
        msec_str = f"{msecs:4.2f}ms"
    else:
        msec_str = f"{msecs:5.3f}ms"
    ts = []
    if days > 0:
        ts = [f"{days:.0f}d", f"{hours:.0f}h", f"{mins:.0f}m", f"{secs:.0f}s"]
        if msec_level >= 4:
            ts.append(msec_str)
    elif hours > 0:
        ts = [f"{hours:.0f}h", f"{mins:.0f}m", f"{secs:.0f}s"]
        if msec_level >= 3:
            ts.append(msec_str)
    elif mins > 0:
        ts = [f"{mins:.0f}m", f"{secs:.0f}s"]
        if msec_level >= 2:
            ts.append(msec_str)
    elif secs > 0:
        ts = [f"{secs:.0f}s"]
        if msec_level >= 1:
            ts.append(msec_str)
    elif msecs >= 1:
        ts = [msec_str]
    else:
        ts = [f"{1000 * msecs:.0f}µs"]
    if precision < 1:
        precision = 1
    return "".join(ts[:precision])


MODE_SINGLE = "single"
"""Navigator shows a single channel in one NAVIGATOR_HEIGHT row."""

MODE_ALL = "all"
"""Navigator shows every visible channel stacked, one row each."""

OVERVIEW_WAVEFORM = "waveform"
"""Navigator content: the min/max waveform envelope of the recording."""

OVERVIEW_ACTIVITY = "activity"
"""Navigator content: baseline-referenced activity, see :mod:`audian.activity`.

Separates sustained signals (calls, chirps, wave-type EODs) from transients
(eel pulses, bat clicks), which a min/max envelope cannot do because a single
transient saturates a bin exactly as a continuous signal of the same peak
amplitude does.
"""


HANDLE_WIDTH = theme.HANDLE_WIDTH
"""Width of a region grab handle, in device pixels.  Aliases the theme token."""

HANDLE_HEIGHT_FRACTION = theme.HANDLE_HEIGHT_FRACTION
"""Fraction of the strip height a grab handle spans, centred on the row."""


NAV_REGION_Z = 50
"""z of the window-selection region, the one thing on this strip that is a
control rather than a picture.

`eventoverlay.NAV_REGION_Z` mirrors this number rather than importing it,
because importing `fulltraceplot` pulls the whole browser in; a test asserts
the two agree.
"""

NAV_TRACE_Z = 70
"""z of the overview itself, and it sits ABOVE the annotation marks.

Reported by the reader: in a densely annotated stretch the overview is
completely invisible, covered by the vertical annotation lines.  It is a
z-order and nothing else.  `eventoverlay.NAV_MARK_Z` is 60 and
`labeloverlay.LABEL_NAV_Z` is 65, `SURFACE_NAVIGATOR` draws every annotation
at full lane height, and the overview used to be at 10 -- so a dense stretch
is a picket fence with a waveform somewhere behind it.

**The waveform rises; the marks do not fall.**  That is the whole decision,
and the obvious alternative undoes a measured one.  The marks are at 60 and
65 to clear the translucent region below them, and `labeloverlay.LABEL_NAV_Z`
tabulates what it costs when they do not: a box edge samples (223, 113, 134)
under the region against (255, 107, 107) above it, and it is wrong precisely
inside the stretch of session the reader is working in.  Marks put under the
trace are marks under the region again.

Measured on data/Gryllus_campestris.wav at 1600x1000, 17.951 s carrying 118
pulses, 35 trials and 44 label spans -- the picket fence the reader
described -- with the window on 4..10 s, so the selection region covers
x 361..734 of a 1232x96 strip.  Counting pixels that are exactly the
overview's own pen colour, (87, 144, 174):

==================  =========  ==================  ===========
z of the overview   in total   inside the region   outside it
==================  =========  ==================  ===========
10 (was)            2600       0                   2600
`NAV_TRACE_Z` (70)  5363       1866                3497
==================  =========  ==================  ===========

Twice the overview on screen, and the middle column is the consequence that
had to be looked at rather than assumed.  At 10 the region's translucent
brush was painted OVER the waveform, so not one pixel inside the window the
reader is working in was the waveform's own colour: it sampled
(85, 143, 189) there instead, which is exactly (87, 144, 174) under
`theme.region_brush`.  Above the region the wash tints the ground and no
longer the trace -- which is what a minimap usually does, and which is a
visible change nobody asked for.

The region stays legible, because what marks it is the ground and not the
trace: measured on this strip, the row ground goes (13, 18, 25) outside the
region to (25, 40, 66) inside it, the same pair `labeloverlay.LABEL_NAV_Z`
records.  What is given up is a little of its two edge lines, which the
waveform now crosses -- 120 pixels of (76, 141, 255) before against 54
after, in the same four columns, so both edges are still drawn and neither
has moved.

The region keeps the mouse.  `EnvelopeItem` and `ActivityItem` are plain
`pg.GraphicsObject`s with no `ItemIsMovable` and no `mousePressEvent`, so
`QGraphicsItem`'s default ignores a press and the scene hands it on down --
unlike the cutoff `pg.InfiniteLine`s `set_handles_movable` had to switch off.
Driven rather than reasoned: a press on the region above the raised trace
still moves it.
"""

NAV_ACTIVITY_Z = NAV_TRACE_Z + 1
"""z of the activity overview, which is the other half of `NAV_TRACE_Z`.

The two are never visible at once -- `set_overview` switches between them --
so this only has to keep the order they had at 10 and 11.
"""

NAV_ZERO_Z = NAV_TRACE_Z + 10
"""z of the navigator's zero line, above the overview it is a reference for.

It rises with the trace and not with the marks.  A zero line left at 20 would
be a reference chopped into pieces by exactly the annotations the trace has
just cleared, and it would be missing under the part of the strip the reader
is looking at.
"""


class EnvelopeItem(pg.GraphicsObject):
    """Filled min/max envelope of a compressed waveform.

    The navigator used to push the interleaved ``[min0, max0, min1, max1,
    ...]`` array straight into a :class:`pyqtgraph.PlotDataItem` with the
    two samples of a bin half a bin apart on the time axis.  A polyline
    through those points draws a diagonal from every maximum to the *next*
    minimum, so a 17.95 s overview came out as a regular sawtooth of roughly
    constant amplitude -- a waveform that is not in the file.

    This item draws the same data as the shape it actually is: a closed
    polygon whose upper edge is the per-bin maximum and whose lower edge is
    the per-bin minimum, with both edges sampled at the bin centre.  There is
    no interpolation artefact left to alias, and with one bin per pixel
    column the strip is also cheaper to raster than the polyline was.
    """

    def __init__(self, role: str = "raw") -> None:
        super().__init__()
        self._role = role
        self._selected = False
        self._path = QPainterPath()
        self._rect = QRectF()
        self._pen = theme.waveform_pen(role)
        self._brush = theme.waveform_fill_brush(role)

    # -- appearance -------------------------------------------------------

    def set_role(self, role: str, selected: bool = False) -> None:
        """Adopt a trace role (and the selected-channel highlight)."""
        if role == self._role and selected == self._selected:
            return
        self._role = role
        self._selected = selected
        self.apply_theme()

    def apply_theme(self) -> None:
        """Re-resolve pen and brush from the theme.  Idempotent.

        Both come from the same ``waveform_*`` helpers the data plots use, so
        the strip cannot end up a different colour from the traces it
        navigates.
        """
        self._pen = theme.waveform_pen(self._role, selected=self._selected)
        self._brush = theme.waveform_fill_brush(self._role, selected=self._selected)
        self.update()

    # -- data -------------------------------------------------------------

    def set_envelope(
        self, times: np.ndarray, lows: np.ndarray, highs: np.ndarray
    ) -> None:
        """Replace the envelope.  ``times`` are bin centres, one per bin."""
        n = min(len(times), len(lows), len(highs))
        self.prepareGeometryChange()
        if n < 2:
            self._path = QPainterPath()
            self._rect = QRectF()
            self.informViewBoundsChanged()
            self.update()
            return
        times = np.asarray(times[:n], dtype=float)
        lows = np.asarray(lows[:n], dtype=float)
        highs = np.asarray(highs[:n], dtype=float)
        # upper edge left to right, lower edge right to left, then closed:
        xs = np.concatenate((times, times[::-1]))
        ys = np.concatenate((highs, lows[::-1]))
        path = pg.functions.arrayToQPath(xs, ys, connect="all")
        path.closeSubpath()
        self._path = path
        x0 = float(times[0])
        y0 = float(np.min(lows))
        self._rect = QRectF(x0, y0, float(times[-1]) - x0, float(np.max(highs)) - y0)
        self.informViewBoundsChanged()
        self.update()

    # -- QGraphicsItem ----------------------------------------------------

    def boundingRect(self) -> QRectF:
        return QRectF(self._rect)

    def paint(self, p: QPainter, *args) -> None:
        if self._path.isEmpty():
            return
        p.save()
        try:
            # the envelope is a dense stack of near-vertical edges;
            # antialiasing only smears them into a haze.
            p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            p.setPen(self._pen)
            p.setBrush(self._brush)
            p.drawPath(self._path)
        finally:
            p.restore()


class ActivityItem(pg.GraphicsObject):
    """Baseline-referenced activity overview of a whole recording.

    The min/max envelope drawn by :class:`EnvelopeItem` is faithful, but it
    cannot separate the two kinds of event this tool is used to find: a
    single transient -- an eel pulse, a bat click -- saturates a bin's
    maximum exactly as a sustained signal of the same peak amplitude does.
    This item plots the two components of :mod:`audian.activity` instead,
    both in dB above one global noise floor:

    * a filled band from 0 dB to the bin's **RMS excess** -- sustained
      energy, which a cricket chirp, a bird phrase or a wave-type EOD
      produces and a sparse transient does not;
    * a spike from the band up to the bin's **peak excess**, drawn only
      where the bin is classified transient -- the crest that a delta-like
      eel pulse or a bat click produces and a continuous signal does not.

    Reading it is therefore direct rather than inferential: a raised band is
    sustained activity, tall thin spikes over a flat band are transients,
    and both together are both.  Because the reference is global, a quiet
    stretch stays visibly quiet instead of being renormalised into looking
    as busy as everything else.
    """

    def __init__(self) -> None:
        super().__init__()
        self._band = QPainterPath()
        self._spikes = QPainterPath()
        self._rect = QRectF()
        self.apply_theme()

    def apply_theme(self) -> None:
        """Re-resolve pens and brushes from the theme.  Idempotent."""
        # ACCENT is reserved for the playback cursor, so the two activity
        # components borrow the two trace roles instead: continuous energy
        # reads in the raw-trace hue, transients in the filtered one.
        self._band_pen = theme.pen(theme.token("trace.raw"), width=1.0)
        self._band_brush = theme.brush(theme.token("trace.raw"), alpha=0.35)
        self._spike_pen = theme.pen(theme.token("trace.filtered"), width=1.0)
        self.update()

    def set_activity(
        self,
        times: np.ndarray,
        rms_db: np.ndarray,
        peak_db: np.ndarray,
        transient: np.ndarray,
    ) -> None:
        """Replace the overview.  All arrays are one entry per bin."""
        n = min(len(times), len(rms_db), len(peak_db), len(transient))
        self.prepareGeometryChange()
        if n < 2:
            self._band = QPainterPath()
            self._spikes = QPainterPath()
            self._rect = QRectF()
            self.informViewBoundsChanged()
            self.update()
            return
        t = np.asarray(times[:n], dtype=float)
        rms = np.asarray(rms_db[:n], dtype=float)
        peak = np.asarray(peak_db[:n], dtype=float)
        hot = np.asarray(transient[:n], dtype=bool)

        # band: 0 dB baseline out and the RMS excess back, closed.
        xs = np.concatenate((t, t[::-1]))
        ys = np.concatenate((rms, np.zeros(n)))
        band = pg.functions.arrayToQPath(xs, ys, connect="all")
        band.closeSubpath()
        self._band = band

        # spikes: one disconnected vertical segment per transient bin,
        # from the top of the band to the peak, so the drawn length is
        # literally the crest -- the quantity that does the discriminating.
        if hot.any():
            hx = np.repeat(t[hot], 2)
            hy = np.empty(len(hx))
            hy[0::2] = rms[hot]
            hy[1::2] = peak[hot]
            connect = np.tile(np.array([1, 0], dtype=np.int32), int(hot.sum()))
            self._spikes = pg.functions.arrayToQPath(hx, hy, connect=connect)
        else:
            self._spikes = QPainterPath()

        top = float(max(rms.max(), peak[hot].max() if hot.any() else 0.0))
        self._rect = QRectF(float(t[0]), 0.0, float(t[-1]) - float(t[0]), top)
        self.informViewBoundsChanged()
        self.update()

    def boundingRect(self) -> QRectF:
        return QRectF(self._rect)

    def paint(self, p: QPainter, *args) -> None:
        if self._band.isEmpty():
            return
        p.save()
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            p.setPen(self._band_pen)
            p.setBrush(self._band_brush)
            p.drawPath(self._band)
            if not self._spikes.isEmpty():
                p.setPen(self._spike_pen)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawPath(self._spikes)
        finally:
            p.restore()


class NavigatorRegion(pg.LinearRegionItem):
    """The current-position region, with edges you can find and grab.

    ``LinearRegionItem`` gives a fill and two hairlines.  Against the
    navigator background that fill measures 1.27:1, so the block reads only
    because it is large: its *edges* -- the part that answers "where am I in
    the file" precisely -- disappear, and there is nothing that looks
    grabbable.  This subclass keeps the 1 px primary edges and paints a
    short, :data:`HANDLE_WIDTH` px wide primary handle centred on each of
    them.
    """

    def _handle_half_width(self) -> float:
        """Half a handle, in view units."""
        try:
            pixel_width = self.pixelWidth()
        except Exception:
            pixel_width = 0.0
        if not pixel_width or not np.isfinite(pixel_width):
            return 0.0
        return 0.5 * HANDLE_WIDTH * float(pixel_width)

    def boundingRect(self) -> QRectF:
        rect = super().boundingRect()
        pad = self._handle_half_width()
        if pad > 0:
            rect.adjust(-pad, 0, pad, 0)
        return rect

    def paint(self, p: QPainter, *args) -> None:
        super().paint(p, *args)
        pad = self._handle_half_width()
        if pad <= 0:
            return
        rect = super().boundingRect()
        height = rect.height()
        if height <= 0:
            return
        handle_height = HANDLE_HEIGHT_FRACTION * height
        top = rect.center().y() - 0.5 * handle_height
        p.save()
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            p.setPen(theme.no_pen())
            p.setBrush(theme.brush("primary"))
            for x in (rect.left(), rect.right()):
                p.drawRect(QRectF(x - pad, top, 2 * pad, handle_height))
        finally:
            p.restore()


class FullTracePlot(pg.GraphicsLayoutWidget):
    sigHoverTime = Signal(int, float)
    """Emitted while hovering the navigator: (channel, time in seconds).

    Additive API for the status-bar readout.  Nothing in audian connects it
    yet; the in-scene overlay stays until it does.
    """

    _timer = None
    compressed_data = None

    # pyqtgraph's GraphicsView calls resizeEvent() from inside its own
    # __init__, i.e. before FullTracePlot.__init__ has assigned anything, so
    # every attribute the resize path touches needs a class-level default.
    axs: list = []
    axtraces: list = []
    left_margin = theme.AXIS_LEFT_WIDTH
    _syncing_margin = True
    _align_timer = None
    _align_target = None
    _align_steps = 0

    RETRY_MIN_MS = 250
    """First backoff step while the background compression is still running."""

    RETRY_MAX_MS = 2000
    """Backoff ceiling.  There is no polling once the data are complete."""

    def __init__(self, data, axtraces, left_margin, *args, **kwargs):
        pg.GraphicsLayoutWidget.__init__(self, *args, **kwargs)

        self.data = data
        self.tmax = self.data.data.frames / self.data.rate
        self.axtraces = axtraces
        # `left_margin` is the caller's guess.  The navigator spans the whole
        # window while the data plots sit to the right of the channel rail, so
        # the only way the two can share a column grid is to measure the real
        # offset once both are laid out -- see _sync_left_margin().
        self.left_margin = left_margin
        self.no_signal = False

        self.mode = MODE_SINGLE
        self.overview = OVERVIEW_WAVEFORM
        self._activity = None
        self.channel = 0
        self.show_channels = list(range(self.data.channels))
        self.data_height = theme.NAVIGATOR_HEIGHT
        self._natural_height = theme.NAVIGATOR_HEIGHT

        # plotting state:
        self._plotting_started = False
        self._plot_failed = False
        self._retry_ms = self.RETRY_MIN_MS
        self._times = None
        self._datas = None
        self._drawn = set()

        theme.style_figure(self)

        # for each channel prepare a plot panel:
        self.axs = []
        self.lines = []
        self.act_items = []
        self.regions = []
        self.zero_lines = []
        self.region_proxies = []
        for c in range(self.data.channels):
            axt = self._make_channel_plot(c)
            self.addItem(axt, row=c, col=0)
            self.axs.append(axt)

        # hover readout, in the scene -- never a window:
        self.time_info = theme.overlay_textitem(anchor=(0, 1))
        self.scene().addItem(self.time_info)
        # The in-scene overlay stays on until something connects
        # sigHoverTime, so the information is never lost if the wiring
        # slips.  DataBrowser.open turns it off once the status bar has
        # the readout, rather than showing the same numbers twice.
        self.overlay_enabled = True

        self.compressed_data = CompressedData(self.data.data)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.plot_data)

        # Column alignment is driven by the data plots, not by us: their view
        # boxes move when the window resizes, when the channel rail is
        # toggled and when the stack's scroll bar appears.  A single sync
        # from our own resizeEvent runs before any of that has settled.
        self._align_timer = QTimer(self)
        self._align_timer.setSingleShot(True)
        self._align_timer.timeout.connect(self._sync_left_margin)
        for axtrace in self.axtraces:
            if axtrace is not None:
                axtrace.getViewBox().sigResized.connect(self._schedule_sync)

        self._apply_layout()
        # everything the resize path reads now exists:
        self._syncing_margin = False

    def _make_channel_plot(self, channel: int) -> pg.PlotItem:
        """Build one navigator row: plot panel, region, trace and zero line."""
        axt = pg.PlotItem(axisItems={"bottom": self._make_time_axis()})
        # Which electrode this row is, spelled the way `TimePlot` spells it.
        # `axs[c]` is always channel c -- the modes change which rows are
        # *shown*, never what a row is -- and an overlay hung on one of these
        # has no other way to ask: a `pg.PlotItem` carries no channel, so
        # `LabelOverlay.channel` would fall back to 0 and every navigator row
        # would draw the first electrode's labels.
        axt.channel = channel
        axt.showAxes(True, False)
        axt.getAxis("left").setWidth(self.left_margin)
        axt.getViewBox().setDefaultPadding(padding=0)
        axt.hideButtons()
        axt.setMenuEnabled(False)
        theme.strip_pg_menus(axt)
        # strip_pg_menus() keeps the pyqtgraph control widgets alive but
        # parentless, which makes each one a top-level QWidget.  Adopt them:
        # a hidden child never becomes a compositor surface.
        for widget in getattr(axt, "_audian_ctrl_widgets", []):
            widget.setParent(self)
            widget.setVisible(False)
        axt.setMouseEnabled(False, False)
        axt.enableAutoRange(False, False)
        axt.setLimits(xMin=0, xMax=self.tmax, minXRange=self.tmax, maxXRange=self.tmax)
        axt.setXRange(0, self.tmax)

        # add region marker:
        region = NavigatorRegion(
            pen=theme.region_pen(),
            brush=theme.region_brush(),
            hoverPen=theme.region_hover_pen(),
            hoverBrush=theme.region_hover_brush(),
            movable=True,
            swapMode="block",
        )
        region.setZValue(NAV_REGION_Z)
        region.setBounds((0, self.tmax))
        region.setRegion((self.axtraces[channel].viewRange()[0]))
        # a region drag emits at mouse rate (60-120 Hz) and one step costs
        # ~111 ms on 16 channels, so coalesce it; the final position comes
        # through undelayed:
        self.region_proxies.append(
            pg.SignalProxy(
                region.sigRegionChanged, rateLimit=30, slot=self._region_dragged
            )
        )
        region.sigRegionChangeFinished.connect(self.update_time_range)
        self.axtraces[channel].sigXRangeChanged.connect(self.update_region)
        axt.addItem(region)
        self.regions.append(region)

        # add data:
        line = EnvelopeItem(self.trace_role())
        line.setZValue(NAV_TRACE_Z)
        axt.addItem(line)
        self.lines.append(line)

        # the activity overview shares the panel and the region; only one of
        # the two is ever visible, so switching costs a repaint, not a rebuild.
        act = ActivityItem()
        act.setZValue(NAV_ACTIVITY_Z)
        act.setVisible(False)
        axt.addItem(act)
        self.act_items.append(act)

        # add zero line:
        zero_line = axt.addLine(y=0, movable=False, pen=theme.zero_pen())
        zero_line.setZValue(NAV_ZERO_Z)
        self.zero_lines.append(zero_line)

        theme.style_plotitem(axt)
        return axt

    def _make_time_axis(self):
        """A real time axis for the bottom of the navigator.

        Only the bottom-most visible row shows its values; the duration of the
        recording used to be painted over the data as a QGraphicsSimpleTextItem
        and is now simply the last tick.
        """
        try:
            axis = TimeAxisItem(
                self.data.data.file_start_times(),
                self.data.data.file_paths,
                # the axis label is placed relative to the *y axis* width, so
                # it has to be the same number the data plots use or the two
                # "REC (s)" captions sit at different offsets.
                theme.AXIS_LEFT_WIDTH,
                orientation="bottom",
                showValues=False,
            )
            start_time = getattr(self.data, "start_time", None)
            if start_time is not None:
                axis.set_start_time(start_time)
        except Exception:
            axis = pg.AxisItem(orientation="bottom", showValues=False)
        theme.style_axis(axis)
        axis.setHeight(0)
        return axis

    def shutdown(self):
        """Stop the navigator's timers and let the worker pool go.

        Named `shutdown` for the same reason `DataBrowser.shutdown` is: this
        is a `QWidget`, and shadowing `close` turns every `widget.close()` in
        the tree into something the caller did not ask for.
        """
        try:
            for timer in (self._timer, self._align_timer):
                if timer is not None:
                    timer.stop()
        except RuntimeError:
            # the C++ side is already gone (interpreter shutdown)
            pass
        if self.compressed_data is not None:
            self.compressed_data.close()

    # -- theming ----------------------------------------------------------

    def trace_role(self) -> str:
        """The trace role the *data plots* are drawing, so colours agree.

        The navigator used to hard-code ``'raw'`` while the plots above it
        drew the same unfiltered signal as ``'filtered'``: one recording, two
        colours, in the same window.  :func:`theme.waveform_role` is the one
        function that decides this for the whole application -- a filtered
        trace whose filter is a pass-through reports ``'raw'`` -- so the
        answer is taken from the trace items that are actually on screen and
        falls back to asking the theme about the raw file data.
        """
        role = self._main_plot_role()
        if role is not None:
            return role
        return theme.waveform_role(self.data.data, "raw")

    def _main_plot_role(self) -> str | None:
        """Role of the topmost visible trace item in the data plots."""
        role = None
        for axt in self.axtraces:
            for item in getattr(axt, "items", None) or []:
                if not hasattr(item, "effective_role") or not item.isVisible():
                    continue
                candidate = item.effective_role()
                if isinstance(candidate, str) and candidate:
                    # traces are stacked source-first, so the last visible one
                    # is the derived trace the eye reads as "the waveform".
                    role = candidate
            if role is not None:
                return role
        return None

    def apply_theme(self) -> None:
        """Re-apply every colour from the theme.  Idempotent, no data work."""
        theme.style_figure(self)
        for axt in self.axs:
            theme.style_plotitem(axt)
        self.refresh_colors()
        # the activity overview keeps its own pens and brushes
        for act in self.act_items:
            act.apply_theme()
        for zero_line in self.zero_lines:
            zero_line.setPen(theme.zero_pen())
        for region in self.regions:
            region.setBrush(theme.region_brush())
            region.setHoverBrush(theme.region_hover_brush())
            for line in region.lines:
                line.setPen(theme.region_pen())
                line.setHoverPen(theme.region_hover_pen())
            region.update()

    def refresh_colors(self) -> None:
        """Re-resolve the trace colour.  Cheap; no repaint unless it changed.

        Call after anything that can flip :func:`theme.waveform_role` --
        engaging or clearing a filter cutoff -- so the strip follows the data
        plots from amber back to cyan and the two never disagree.
        """
        for c in range(len(self.lines)):
            self._style_line(c)

    def _style_line(self, channel: int) -> None:
        """Give one envelope the role colour, and the selected-row highlight.

        The *role* always comes from :meth:`trace_role`, so the strip and the
        data plots can never paint the same signal two colours.  The
        selected-channel highlight is deliberately not carried over into
        single mode: ``primary`` is also the colour of the position region
        that lives on this strip, and a lone primary waveform underneath a
        primary region leaves the region -- the one thing the navigator
        exists to show -- with nothing to stand out from.  In single mode
        there is no stack to be selected *among*, and the row is labelled
        ``CH nn`` regardless.
        """
        selected = self.mode == MODE_ALL and channel == self.current_channel()
        self.lines[channel].set_role(self.trace_role(), selected=selected)

    def polish(self) -> None:
        """Thin shim kept for databrowser: theme first, then start plotting."""
        self.apply_theme()
        self.start_plotting()
        self._schedule_sync()

    # -- data -------------------------------------------------------------

    def start_plotting(self) -> None:
        """Kick off drawing of the compressed data.  Safe to call twice."""
        if self._plotting_started:
            return
        self._plotting_started = True
        self._retry_ms = self.RETRY_MIN_MS
        self.plot_data()

    def prepare(self) -> None:
        max_pixel = self._max_pixel()
        # a cache written at a coarser resolution would put the sawtooth
        # straight back, so it is only good enough if it has the bins:
        self.compressed_data.load_data(min_rows=2 * max_pixel)
        self.compressed_data.start(max_pixel, self.data.load_kwargs)
        # cached data are ready right here -- do not wait for a timer:
        self._retry_ms = self.RETRY_MIN_MS
        self.plot_data()

    def _max_pixel(self) -> int:
        """Number of min/max bins to compress the recording into.

        One bin per *device* pixel of the widest the strip can plausibly get
        -- the screen -- not per pixel of whatever width the widget happens
        to have while the browser is still being built.  ``prepare()`` runs
        during ``DataBrowser.open()``, where the navigator was a few hundred
        pixels wide, so every bin ended up spanning three or four pixels and
        the overview came out visibly under-resolved.  Compressing to the
        screen width instead costs a few thousand rows once and makes every
        later resize free.
        """
        ratio = float(self.devicePixelRatioF() or 1.0)
        width = int(self.width() * ratio)
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            width = max(width, int(screen.geometry().width() * ratio))
        return max(1, width)

    def _schedule_retry(self) -> None:
        if self._plot_failed:
            return
        self._timer.start(self._retry_ms)
        self._retry_ms = min(2 * self._retry_ms, self.RETRY_MAX_MS)

    def plot_data(self) -> None:
        """Draw whatever compressed data are available right now.

        Never raises into the Qt event loop: a broken channel degrades the
        navigator, it does not take the window down.
        """
        try:
            self._plot_data()
        except Exception as e:
            self._timer.stop()
            if not self._plot_failed:
                self._plot_failed = True
                print(
                    f"audian: navigator cannot plot the compressed data: {e!r}",
                    file=sys.stderr,
                )

    def _plot_data(self) -> None:
        cdata = self.compressed_data
        if cdata.times is None or cdata.datas is None:
            # prepare() has not run yet, or the cache is still loading:
            self._schedule_retry()
            return
        if not cdata.is_busy():
            self._timer.stop()
            self._store(*self._clamp(cdata.times, cdata.datas))
            self._draw()
            cdata.save_data()
            return
        # background workers are still writing into shared memory: copy the
        # slice under the lock, release, and only then paint.
        lock = cdata.get_lock()
        if lock.acquire(block=False):
            try:
                times, datas = self._clamp(cdata.times, cdata.datas)
                times = np.array(times)
                datas = np.array(datas)
            finally:
                lock.release()
            self._store(times, datas)
            self._draw()
        self._schedule_retry()

    @staticmethod
    def _clamp(times, datas):
        """Belt-and-braces guard against a times/datas length mismatch.

        The root cause is CompressedData's length derivation; this clamp stays
        regardless, because a plugin or a future worker path can reintroduce
        the mismatch and a raise here blanks the whole navigator.
        """
        datas = np.asarray(datas)
        if datas.ndim == 1:
            datas = datas.reshape((-1, 1))
        n = min(len(times), len(datas))
        return times[:n], datas[:n]

    def _store(self, times, datas) -> None:
        self._times = times
        self._datas = datas
        self._drawn = set()
        self._activity = None
        self._compute_activity()
        self._update_ranges()

    def _compute_activity(self) -> None:
        """Derive the activity overview from the compressed accumulators.

        Leaves :attr:`_activity` as ``None`` -- and the navigator on the
        waveform envelope -- whenever the second moment is unavailable,
        which happens for overviews restored from a cache written before
        these statistics existed.  Showing a metric built on absent data
        would be worse than showing the envelope.
        """
        self._activity = None
        cdata = self.compressed_data
        if cdata is None or self._times is None or len(self._times) < 4:
            return
        rate = float(self.data.rate)
        step = int(round((float(self._times[2]) - float(self._times[0])) * rate))
        if step < 1:
            return
        try:
            stats = cdata.bin_stats(step)
            if stats is None:
                return
            sigma = activity.global_baseline(stats)
            rms = activity.rms_excess_db(stats, sigma)
            peak = activity.peak_excess_db(stats, sigma)
            transient = activity.classify(stats, sigma) == activity.TRANSIENT
        except (ValueError, FloatingPointError, ZeroDivisionError):
            return
        centres = np.asarray(self._times)[1::2][: len(rms)]
        self._activity = (
            centres,
            rms[: len(centres)],
            peak[: len(centres)],
            transient[: len(centres)],
        )

    def has_activity(self) -> bool:
        """Whether an activity overview could be built for this recording."""
        return self._activity is not None

    def set_overview(self, overview: str) -> None:
        """Switch the strip between the waveform envelope and activity."""
        if overview not in (OVERVIEW_WAVEFORM, OVERVIEW_ACTIVITY):
            return
        if overview == OVERVIEW_ACTIVITY and self._activity is None:
            self._compute_activity()
            if self._activity is None:
                return
        if overview == self.overview:
            return
        self.overview = overview
        show_activity = overview == OVERVIEW_ACTIVITY
        for line, act in zip(self.lines, self.act_items):
            line.setVisible(not show_activity)
            act.setVisible(show_activity)
        for zero in self.zero_lines:
            zero.setVisible(not show_activity)
        self._drawn = set()
        self._update_ranges()
        self._draw()

    def _update_ranges(self) -> None:
        if self._datas is None or len(self._datas) == 0:
            return
        if self.overview == OVERVIEW_ACTIVITY and self._activity is not None:
            self._update_activity_ranges()
            return
        for c in range(min(self._datas.shape[1], len(self.axs))):
            column = self._datas[:, c]
            y = float(max(abs(np.min(column)), abs(np.max(column))))
            if not np.isfinite(y) or y <= 0:
                y = 1.0
            self.axs[c].setYRange(-y, y)
            self.axs[c].setLimits(yMin=-y, yMax=y, minYRange=2 * y, maxYRange=2 * y)

    def _update_activity_ranges(self) -> None:
        """Give every channel the SAME dB range, deliberately.

        The whole point of referencing one global baseline is that bins stay
        comparable; per-channel autoscaling would undo that across channels
        exactly as a per-bin baseline undoes it across time.
        """
        _, rms, peak, _ = self._activity
        top = float(np.nanmax(peak)) if peak.size else 1.0
        top = max(top, float(np.nanmax(rms)) if rms.size else 1.0, 6.0)
        top *= 1.05
        for c in range(min(rms.shape[1], len(self.axs))):
            self.axs[c].setYRange(0.0, top)
            self.axs[c].setLimits(yMin=0.0, yMax=top, minYRange=top, maxYRange=top)

    def _draw(self) -> None:
        """Push data into the lines of the channels that are actually shown."""
        if self._times is None or self._datas is None:
            return
        nchannels = self._datas.shape[1]
        # the compressed array is interleaved [min0, max0, min1, max1, ...];
        # both envelopes are sampled at the centre of their own bin, which is
        # exactly where the maxima already sit.
        centres = np.asarray(self._times)[1::2]
        for c in self.visible_channels():
            if c in self._drawn or c >= nchannels or c >= len(self.lines):
                continue
            if self.overview == OVERVIEW_ACTIVITY and self._activity is not None:
                t, rms, peak, transient = self._activity
                if c < rms.shape[1]:
                    self.act_items[c].set_activity(
                        t, rms[:, c], peak[:, c], transient[:, c]
                    )
                    self._drawn.add(c)
                continue
            column = self._datas[:, c]
            self.lines[c].set_envelope(centres, column[0::2], column[1::2])
            self._drawn.add(c)

    # -- layout -----------------------------------------------------------

    def current_channel(self) -> int:
        """The channel drawn in 'single' mode, forced into the shown set."""
        if self.channel in self.show_channels:
            return self.channel
        if len(self.show_channels) > 0:
            return self.show_channels[0]
        return 0

    def visible_channels(self) -> list:
        if len(self.show_channels) == 0:
            return []
        if self.mode == MODE_SINGLE:
            return [self.current_channel()]
        return list(self.show_channels)

    def set_mode(self, mode: str) -> None:
        """Switch between the single-row navigator and the channel stack."""
        if mode not in (MODE_SINGLE, MODE_ALL) or mode == self.mode:
            return
        self.mode = mode
        self._apply_layout()
        self._draw()

    def set_channel(self, channel: int) -> None:
        """Follow the browser's channel selection in 'single' mode."""
        if channel is None:
            return
        channel = int(channel)
        if channel < 0 or channel >= len(self.axs) or channel == self.channel:
            return
        self.channel = channel
        # 'all' mode needs it too: the selected row changes colour.
        self._apply_layout()
        self._draw()

    def update_layout(self, channels, data_height) -> None:
        self.show_channels = list(channels)
        if data_height:
            self.data_height = int(data_height)
        self._apply_layout()
        self._draw()

    def _axis_height(self) -> int:
        """Room for one row of tick labels plus the axis label."""
        metrics = theme.mono_metrics(theme.SIZE_SMALL_PT)
        return 2 * metrics.height() + theme.S4

    def _channel_cue(self, channel: int, show: bool) -> None:
        """Give a row its identity: a channel label and, for the selected
        channel, the primary trace colour.  Colour alone never carries
        meaning, so the label is always there too."""
        axis = self.axs[channel].getAxis("left")
        # a single horizontal tick in the left margin, aligned with the zero
        # line -- a rotated axis label is illegible in a 24 px row.  The label
        # is spelled out the same way as the data plots above ("CH 00"); the
        # abbreviated "C0" was not shorter for any reason, it was just cut.
        axis.setStyle(showValues=show)
        axis.setTicks([[(0, f"CH {channel:02d}")], []] if show else [[], []])
        theme.style_axis(axis)
        self._style_line(channel)

    def _apply_layout(self) -> None:
        visible = self.visible_channels()
        axis_height = self._axis_height()
        if self.mode == MODE_SINGLE:
            row_height = theme.NAVIGATOR_HEIGHT
        else:
            row_height = max(theme.S16, self.data_height)
        last = visible[-1] if len(visible) > 0 else -1
        for c, axt in enumerate(self.axs):
            show = c in visible
            axt.setVisible(show)
            axis = axt.getAxis("bottom")
            self._channel_cue(c, show)
            bottom = show and c == last
            axis.setStyle(showValues=bottom)
            # the label is drawn into the left margin, so it survives a zero
            # height and has to be switched off explicitly:
            axis.showLabel(bottom)
            axis.setHeight(None if bottom else 0)
            if bottom:
                self.ci.layout.setRowFixedHeight(c, row_height + axis_height)
            else:
                self.ci.layout.setRowFixedHeight(c, row_height if show else 0)
        _, top, _, bottom = self.ci.layout.getContentsMargins()
        total = int(row_height * len(visible) + axis_height + top + bottom)
        # never setFixedHeight(): that pins the minimum too and squeezes the
        # data plots to nothing as soon as there are many channels.
        # one row plus the time axis is the floor: below that the bottom
        # axis is clipped away and the navigator loses its time scale,
        # which is most of what it is for.
        floor_height = int(theme.NAVIGATOR_HEIGHT + axis_height + top + bottom)
        self.setMinimumHeight(min(floor_height, max(theme.NAVIGATOR_HEIGHT, total)))
        self.setMaximumHeight(max(floor_height, total))
        # QSizePolicy.Policy.Maximum reads sizeHint() as the upper bound, and
        # GraphicsLayoutWidget's own hint knows nothing about our rows.  Left
        # at the default the splitter caps the navigator at 56 px and the
        # bottom time axis is clipped away entirely.
        self._natural_height = max(floor_height, total)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self.updateGeometry()

    def sizeHint(self):
        hint = super().sizeHint()
        hint.setHeight(self._natural_height)
        return hint

    def minimumSizeHint(self):
        hint = super().minimumSizeHint()
        hint.setHeight(self.minimumHeight())
        return hint

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._schedule_sync()

    def showEvent(self, ev):
        super().showEvent(ev)
        self._schedule_sync()

    def sync_layout(self) -> None:
        """Public hook: re-align the strip with the data plots above it.

        Call after anything that moves the data plots sideways -- toggling
        the channel rail, showing or hiding a scroll bar.
        """
        self._schedule_sync()

    def _schedule_sync(self, *args) -> None:
        """Coalesce alignment work: many geometry signals, one measurement."""
        timer = getattr(self, "_align_timer", None)
        if timer is None:
            return
        try:
            timer.start(0)
        except RuntimeError:
            # the C++ side is gone (teardown)
            pass

    # -- column grid ------------------------------------------------------

    @staticmethod
    def _viewbox_left(plot_item) -> float | None:
        """Global x of a plot item's view box left edge, in device pixels."""
        vbox = plot_item.getViewBox() if plot_item is not None else None
        scene = vbox.scene() if vbox is not None else None
        views = scene.views() if scene is not None else []
        if not views:
            return None
        left = vbox.sceneBoundingRect().left()
        view = views[0]
        return float(view.mapToGlobal(view.mapFromScene(QPointF(left, 0.0))).x())

    def _sync_left_margin(self) -> None:
        """Line the strip's data area up with the data plots above it.

        The navigator spans the whole window; the channel figures sit to the
        right of the channel rail.  A left axis of a fixed width can only
        agree with them by accident, and it did not: the strip's data started
        at 76 px against the plots' 255 px, so the overview was not in the
        same column grid as the data it navigates.  Measuring the difference
        needs no knowledge of the rail, the grid spacing or the scroll bar,
        and it keeps working when the rail is toggled away.
        """
        if self._syncing_margin or not self.axs or not self.axtraces:
            return
        self._syncing_margin = True
        try:
            self._do_sync_left_margin()
        except Exception as e:
            # a layout callback must never take the window down; an
            # unaligned navigator is a blemish, a traceback out of
            # resizeEvent is a dead application.
            print(f"audian: navigator cannot align its left margin: {e!r}")
        finally:
            self._syncing_margin = False

    MAX_ALIGN_STEPS = 4
    """Corrections allowed per target position.

    The correction is a delta, and the graphics layout does not always
    publish the new geometry before the next resize signal arrives, so a
    step can be measured against a stale position.  In practice it settles
    in one or two; this is only here so that it can never grind.
    """

    def _do_sync_left_margin(self) -> None:
        # sceneBoundingRect() is only current once the grid layout has run,
        # and a resize is exactly when it has not:
        self.ci.layout.activate()
        target = None
        for axtrace in self.axtraces:
            if axtrace is not None and axtrace.isVisible():
                target = self._viewbox_left(axtrace)
                if target is not None:
                    break
        if target is None:
            return
        if self._align_target is None or abs(target - self._align_target) >= 1.0:
            self._align_target = target
            self._align_steps = 0
        elif self._align_steps >= self.MAX_ALIGN_STEPS:
            return
        mine = None
        for axt in self.axs:
            if axt.isVisible():
                mine = self._viewbox_left(axt)
                if mine is not None:
                    break
        if mine is None:
            return
        delta = target - mine
        if abs(delta) < 1.0:
            return
        width = max(0, int(round(self.left_margin + delta)))
        if width == self.left_margin:
            return
        self._align_steps += 1
        self.left_margin = width
        for axt in self.axs:
            axt.getAxis("left").setWidth(width)
        self.ci.layout.activate()
        self._schedule_sync()

    # -- region sync ------------------------------------------------------

    def _region_dragged(self, args) -> None:
        """Rate-limited slot behind pg.SignalProxy."""
        self.update_time_range(args[0])

    def update_time_range(self, region) -> None:
        if self.no_signal:
            return
        self.no_signal = True
        try:
            xmin, xmax = region.getRegion()
            if self.mode == MODE_SINGLE:
                # only one region is on screen, and time is shared: move every
                # trace plot with it.
                for ax in self.axtraces:
                    ax.setXRange(xmin, xmax)
            else:
                for ax, reg in zip(self.axtraces, self.regions):
                    if reg is region:
                        ax.setXRange(xmin, xmax)
                        break
        finally:
            self.no_signal = False

    def update_region(self, vbox, x_range) -> None:
        if self.no_signal:
            return
        self.no_signal = True
        try:
            for ax, region in zip(self.axtraces, self.regions):
                if ax.getViewBox() is vbox:
                    region.setRegion(x_range)
                    break
        finally:
            self.no_signal = False

    # -- mouse ------------------------------------------------------------

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            spos = self.mapToScene(ev.pos())
            for ax, region in zip(self.axs, self.regions):
                if not ax.isVisible():
                    continue
                vb = ax.getViewBox()
                pos = vb.mapSceneToView(spos)
                [xmin, xmax], [ymin, ymax] = ax.viewRange()
                if xmin <= pos.x() <= xmax and ymin <= pos.y() <= ymax:
                    dx = (xmax - xmin) / max(1, self.width())
                    x = pos.x()
                    rxmin, rxmax = region.getRegion()
                    if x < rxmin - 2 * dx or x > rxmax + 2 * dx:
                        rdx = rxmax - rxmin
                        rx0 = max(0, x - rdx / 2)
                        rx1 = rx0 + rdx
                        if rx1 > self.tmax:
                            rx0 = max(0, rx1 - rdx)
                        region.setRegion((rx0, rx1))
                        ev.accept()
                        return
                    break
        ev.ignore()
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):
        spos = self.mapToScene(ev.pos())
        for c, ax in enumerate(self.axs):
            if not ax.isVisible():
                continue
            vb = ax.getViewBox()
            pos = vb.mapSceneToView(spos)
            [xmin, xmax], [ymin, ymax] = ax.viewRange()
            if xmin <= pos.x() <= xmax and ymin <= pos.y() <= ymax:
                self.sigHoverTime.emit(c, float(pos.x()))
                if self.overlay_enabled:
                    self.time_info.setHtml(self._hover_html(c, pos.x()))
                    self._place_time_info(spos)
                    self.time_info.setVisible(True)
                break
        else:
            self.time_info.setVisible(False)
        super().mouseMoveEvent(ev)

    def _hover_html(self, channel: int, time: float) -> str:
        ts = '<style type="text/css"> td { padding: 0 4px; } </style>'
        ts += (
            '<table><tr><td colspan="2">channel</td>'
            f"<td><b>{channel}</b></td><td></td></tr>"
        )
        taxis = self.axtraces[channel].getAxis("bottom")
        rows = 0
        if hasattr(taxis, "makeStrings"):
            for sm in range(3):
                label, units, vals, fname = taxis.makeStrings(
                    [time], 1, 1, sm, True, min_spacing=0.01
                )
                if len(vals) == 0:
                    continue
                if sm > 0 and label == "REC":
                    continue
                fname = Path(fname).name if label == "File" else ""
                ts += (
                    f"<tr><td>{label}</td><td>({units})</td>"
                    f'<td align="right"><b>{vals[0]}</b></td>'
                    f"<td>{fname}</td></tr>"
                )
                rows += 1
        if rows == 0:
            ts += (
                '<tr><td>REC</td><td>(s)</td><td align="right">'
                f"<b>{secs_to_str(time)}</b></td><td></td></tr>"
            )
        ts += "</table>"
        return ts

    def _place_time_info(self, spos) -> None:
        """Position the readout in scene pixels, clamped to the scene rect.

        Scene coordinates on a GraphicsLayoutWidget are 1:1 with viewport
        pixels, so there is no coordinate maths and nothing global involved.
        """
        rect = self.sceneRect()
        box = self.time_info.boundingRect()
        x = spos.x() + theme.S8
        y = spos.y() - theme.S4
        if x + box.width() > rect.right():
            x = rect.right() - box.width()
        if x < rect.left():
            x = rect.left()
        if y - box.height() < rect.top():
            y = spos.y() + box.height() + theme.S8
        if y > rect.bottom():
            y = rect.bottom()
        self.time_info.setPos(x, y)

    def leaveEvent(self, ev):
        self.time_info.setVisible(False)
        super().leaveEvent(ev)
