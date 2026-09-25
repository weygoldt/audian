"""PlotItem for displaying any data as a function of time."""

import numpy as np
import pyqtgraph as pg

from PySide6.QtCore import Signal

from . import theme
from .panels import Panel
from .rangeplot import RangePlot
from .timeaxisitem import TimeAxisItem
from .traceitem import OverlayTraceItem, TraceItem
from .yaxisitem import YAxisItem


# Below this view box height the tick values collide with each other and with
# the in-plot caption, so only the zero line is left.  This is a layout
# threshold, not a design token - theme.CHANNEL_MIN_HEIGHT (80) is the height a
# channel *should* get, this is the height below which numbers stop being
# readable at all.
TICK_VALUES_MIN_HEIGHT = 48

#: Up to this many overlaid channels the caption lists each one in its own
#: colour, which is the legend; beyond it the list would not fit one line of
#: a lane and the caption says how many instead.  Sixteen channels, the
#: design centre, are 8 + 16 * 3 = 56 glyphs.
MAX_LEGEND_CHANNELS = 24


#: Units pyqtgraph may rescale and prefix.  Everything else is shown
#: verbatim: it prefixes whatever string it is given, so a non-SI unit such
#: as ``a.u.`` becomes ``ma.u.`` with the tick values silently multiplied.
SI_UNITS = frozenset(
    {"V", "A", "s", "m", "g", "N", "J", "W", "C", "F", "T", "K", "Hz", "Pa", "Ohm"}
)


def si_prefixable(unit: str) -> bool:
    """Whether `unit` is an SI unit pyqtgraph can safely prefix."""
    return str(unit).strip() in SI_UNITS


class TimePlot(RangePlot):
    # channel, time, value under the mouse pointer:
    sigHoverValue = Signal(int, float, float)

    Y_TOP_PAD = 0.06
    """Extra headroom above the fitted amplitude range, as a fraction of it.

    The in-plot ``CH nn`` caption lives in the top left corner of the view box.
    Without this the topmost tick label lands on the same scan line as the
    caption and the two render as one string (``0.2 _CH 01``).  Overridden to
    zero where the y axis is not an amplitude - a frequency axis has a hard
    Nyquist ceiling and padding above it would just be a lie with a gap in it.
    """

    def __init__(self, aspec, channel, browser, xwidth, ylabel=""):
        self.browser = browser
        left_margin = theme.AXIS_LEFT_WIDTH
        # axis:
        bottom_axis = TimeAxisItem(
            browser.data.data.file_start_times(),
            browser.data.data.file_paths,
            left_margin,
            orientation="bottom",
            showValues=True,
        )
        bottom_axis.set_start_time(browser.data.start_time)
        top_axis = TimeAxisItem(
            browser.data.data.file_start_times(),
            browser.data.data.file_paths,
            left_margin,
            orientation="top",
            showValues=False,
        )
        top_axis.set_start_time(browser.data.start_time)
        left_axis = YAxisItem(orientation="left", showValues=True)
        # all channels must line up exactly, so the left axis is fixed:
        left_axis.setWidth(theme.AXIS_LEFT_WIDTH)
        right_axis = YAxisItem(orientation="right", showValues=False)

        # plot:
        RangePlot.__init__(
            self,
            aspec,
            channel,
            browser,
            axisItems={
                "bottom": bottom_axis,
                "top": top_axis,
                "left": left_axis,
                "right": right_axis,
            },
        )

        # Double clicking a y axis puts it back to the way the lane opened;
        # `reset_y_range` says what that is on each of the two.  The gesture
        # `PanelSplitter` already has on the other thing a reader drags
        # inside a lane.
        for axis in (left_axis, right_axis):
            axis.set_reset(self.reset_y_range)

        # channel identity: a horizontal caption inside the view box.  A
        # rotated left axis label overprints the tick values as soon as rows
        # get short (16 channels give about 62 px per row).
        self.caption = ylabel
        self.current = False
        self.dense = False
        # The overlay panel: every channel in `overlay_channels` drawn into
        # this one plot, each in its own colour.  None when the plot draws
        # only its own channel, which is always, except on the one lane the
        # overlay borrows -- see `DataBrowser.apply_overlay_traces`.
        self.overlay_channels = None
        self.overlay_items = []
        # the channel the pointer was last nearest, drawn emphasised
        self.emphasized_channel = None
        # the channel the last cross-hair snap landed on
        self.marker_channel = channel
        self.channel_label = pg.TextItem(text="", anchor=(0, 0))
        # NOTE: do *not* set QGraphicsItem.ItemIgnoresTransformations here.
        # pg.TextItem already keeps itself unscaled by applying the inverse
        # of its parent's transform in updateTransform(); setting the flag
        # as well applies the correction twice and the text is painted
        # outside the view - present in sceneBoundingRect(), invisible on
        # screen.
        self.channel_label.setZValue(50)
        self.addItem(self.channel_label, ignoreBounds=True)
        self._show_tick_values = True

        # zero line: the only y reference left once tick values are hidden.
        self.zeroline = pg.InfiniteLine(angle=0, movable=False)
        self.zeroline.setPen(theme.zero_pen())
        self.zeroline.setZValue(-10)
        self.zeroline.setValue(0)
        self.addItem(self.zeroline, ignoreBounds=True)

        # audio marker:
        self.vmarker = pg.InfiniteLine(angle=90, movable=False)
        self.vmarker.setPen(theme.cursor_pen())
        self.vmarker.setZValue(100)
        self.vmarker.setValue(-1)
        self.addItem(self.vmarker, ignoreBounds=True)

        view = self.getViewBox()
        view.sigRangeChanged.connect(self._place_caption)
        view.sigResized.connect(self._view_resized)
        view.sigHoverValue.connect(self._hovered)

        self.dense = theme.is_dense(self.visible_channels())
        self._update_caption()

    # --- theme -----------------------------------------------------------

    def polish(self) -> None:
        super().polish()
        self.vmarker.setPen(theme.cursor_pen())
        self.zeroline.setPen(theme.zero_pen())
        self._update_caption()
        self.update_axis_label()
        self._style_traces(retheme=True)
        # the palette is per theme, so the colours are re-read, not re-used
        self._color_overlay()

    # --- channel emphasis -------------------------------------------------

    def visible_channels(self) -> int:
        """How many channels are on screen right now.

        Read off the browser rather than cached, because the user can hide and
        show channels at any time and the stack never rebuilds the plots.
        """
        shown = getattr(self.browser, "show_channels", None)
        if shown:
            return len(shown)
        data = getattr(self.browser, "data", None)
        return int(getattr(data, "channels", 1) or 1)

    def _style_traces(self, retheme: bool = False) -> None:
        """Push selection and stack density into every trace this plot draws.

        `set_selected` and `set_dense` deliberately do nothing when the flag
        has not changed -- they are called on every layout pass.  That makes
        them useless for a *theme* switch, where the flags are identical but
        the colours behind them are not, so `retheme` forces each item to
        re-resolve its pen from the current token table.
        """
        for item in self.data_items:
            if hasattr(item, "set_selected"):
                item.set_selected(self.current)
            if hasattr(item, "set_dense"):
                item.set_dense(self.dense)
            if retheme:
                restyle = getattr(item, "apply_theme", None) or getattr(
                    item, "polish", None
                )
                if callable(restyle):
                    restyle()

    def add_item(self, item, is_data=False):
        super().add_item(item, is_data)
        if is_data:
            self._style_traces()
            self.update_axis_label()

    # --- caption and layout ----------------------------------------------

    def set_current(self, is_current: bool) -> None:
        """Highlight this plot as the current channel.

        The selected channel is the only one drawn in a saturated colour, so
        that in a sixteen lane stack the eye lands on it without hunting.
        Colour alone never carries meaning: the caption also switches to bold,
        and the channel rail marks the same row with a 2 px rule.
        """
        is_current = bool(is_current)
        if is_current == self.current:
            return
        self.current = is_current
        self._update_caption()
        self._style_traces()

    def set_caption(self, caption: str) -> None:
        """Set the text shown after the channel number in the corner caption."""
        self.caption = caption
        self._update_caption()

    # --- the overlay panel -----------------------------------------------

    def own_traces(self) -> list:
        """The items drawing this plot's own channel, one per trace."""
        return [item for item in self.data_items if isinstance(item, TraceItem)]

    def set_overlay_channels(self, channels) -> bool:
        """Draw every channel in `channels` here, or `None` for own channel.

        One `OverlayTraceItem` per other channel per trace this plot draws,
        so a filtered trace and its raw one are overlaid alike and hidden
        alike.  Returns whether anything changed.
        """
        channels = None if channels is None else [int(c) for c in channels]
        if channels == self.overlay_channels:
            return False
        self.overlay_channels = channels
        for item in self.overlay_items:
            self.removeItem(item)
        self.overlay_items = []
        if channels:
            for c in channels:
                if c == self.channel:
                    continue
                for source in self.own_traces():
                    item = OverlayTraceItem(source.data, c)
                    item.source = source
                    self.addItem(item)
                    self.overlay_items.append(item)
        self.emphasized_channel = None
        self.marker_channel = self.channel
        self.sync_overlay_visibility()
        self._color_overlay()
        self._update_caption()
        return True

    def sync_overlay_visibility(self) -> None:
        """Hide an overlaid trace wherever its own lane's is hidden."""
        for item in self.overlay_items:
            item.setVisible(item.source.isVisibleTo(self))

    def _color_overlay(self) -> None:
        """Paint every drawn channel in its own colour, or give the role back."""
        on = bool(self.overlay_channels)
        for item in self.own_traces():
            item.set_channel_color(theme.channel_color(item.channel) if on else None)
            item.set_emphasized(False)
        for item in self.overlay_items:
            item.set_channel_color(theme.channel_color(item.channel))
            item.set_emphasized(False)
        self.emphasized_channel = None

    def drawn_traces(self) -> list:
        """Every visible trace item, own and overlaid."""
        return [
            item for item in self.own_traces() + self.overlay_items if item.isVisible()
        ]

    def nearest_channel(self, x: float, y: float):
        """The overlaid channel whose trace passes closest to `(x, y)`.

        Measured against the value the trace is *drawn* at under the
        pointer -- `TraceItem.get_amplitude` snaps to the min or max of the
        decimation block, which is the vertex on screen -- so the channel
        named is the line the pointer is visibly on.  None off the buffer.
        """
        best, best_distance = None, None
        for item in self.drawn_traces():
            _, value = item.get_amplitude(x, y)
            if value is None:
                continue
            distance = abs(float(value) - y)
            if best_distance is None or distance < best_distance:
                best, best_distance = item.channel, distance
        return best

    def emphasize_channel(self, channel) -> None:
        """Draw `channel`'s traces over the others, and at the selected width."""
        if channel == self.emphasized_channel:
            return
        self.emphasized_channel = channel
        for item in self.own_traces() + self.overlay_items:
            item.set_emphasized(item.channel == channel)

    def update_plot(self):
        """Redraw own and overlaid traces, each decimated as a lane's is.

        No decimation of its own: every overlaid item reads the same min/max
        pyramid a lane's item does, so the work is the same work moved into
        one view box.  Measured offscreen at 1200x900 on a 16 channel,
        8 kHz, 320 s recording at the 300 s window cap, raw and filtered
        traces (32 items either way), median of 15 over three repeats:
        `update_plots` 3.9 ms for the overlay against 3.8-4.3 ms for sixteen
        lanes, and a grab of what is on screen 22.9 ms against 15.7 ms --
        the difference is overdraw, 32 full-height lines stroked into one
        box.  Driver: `tests/measure_overlay.py`.
        """
        super().update_plot()
        for item in self.overlay_items:
            if item.isVisible():
                item.update_plot()

    def caption_text(self) -> str:
        if self.overlay_channels:
            return f"OVERLAY {self.overlay_legend()}"
        text = f"CH {self.channel:02d}"
        if self.caption:
            text += f"   {self.caption}"
        return text

    def overlay_legend(self, html: bool = False) -> str:
        """The overlaid channels in display order, each in its own colour.

        This is the legend: sixteen hues at one lightness are told apart
        side by side far more easily than named from memory, and not at all
        by a reader with a colour-vision deficiency, so the number beside
        each colour is what names the channel.  Plain text for `caption_text`, HTML for the item.
        """
        channels = self.overlay_channels or []
        if len(channels) > MAX_LEGEND_CHANNELS:
            return f"{len(channels)} ch"
        if not html:
            return " ".join(f"{c:02d}" for c in channels)
        return " ".join(
            f'<span style="color:{theme.channel_color(c)}">{c:02d}</span>'
            for c in channels
        )

    def data_unit(self) -> str:
        """Unit of the traces this panel draws, from the recording metadata.

        Empty when nothing is drawn yet or the loader reports no unit.  A
        wav with no unit metadata comes back as ``a.u.`` from thunderlab,
        which is worth showing: "arbitrary units" is a real statement about
        the recording, not a missing value.
        """
        for item in self.data_items:
            unit = getattr(getattr(item, "data", None), "unit", "")
            if unit:
                return str(unit)
        return ""

    def reset_y_range(self) -> None:
        """Put this plot's y axis back to the way the lane opened.

        The meaning `PanelSplitter.mouseDoubleClickEvent` already gives this
        gesture on the other thing a reader drags -- *back to the default,
        the way a QSplitter handle behaves* -- and it is not the same call on
        both axes, because the two do not open the same way.

        A **frequency** axis opens at the band the Spectrogram group's
        *Opens at* field names, so this is `PlotRange.default_view` rather
        than `PlotRange.reset`.  With no band configured the two are the
        same call -- `default_max()` answers `rmax` -- and the gesture still
        measures 0 to 4000 Hz on an 8 kHz recording, which is what it did
        before the field existed.  With a 2 kHz band it measures 0 to 2000,
        and `Ctrl+Shift+V` is the way out to the whole axis.

        An **amplitude** axis opens *fitted to the data*, which is what
        `auto_fit_y` does on load and what `v` does on demand.  `reset` there
        is `Shift+V`, and it goes to the format's full scale -- measured on a
        four channel synthetic recording, a trace sitting in -0.117..0.129
        goes to -1.000..1.000.  That is a defensible thing for a key to do
        and the wrong thing for this gesture: `PlotRanges.auto_fit` records
        that the recordings this application opens can peak at 7% of the file
        format's full scale, so a double click that "reset" the amplitude
        would answer with a flat line.

        Except in **fixed +-1** mode, where the lane opened at +-1 and a
        refit is precisely not that.  Left out, the gesture broke the reader
        out of a mode the tool bar went on claiming: measured, `y_fixed` at
        (-1.0, 1.0), double click, range (-0.116965, 0.128933) with the menu
        still reading "Y: fixed +-1".  `v` has that wart too, but `v` is a
        key called "auto zoom amplitude" and this is advertised as a reset.

        **How many lanes go with it is not the same on the two branches,
        and that follows the application rather than this gesture.**
        `auto_fit_y` fits every visible channel; `apply_ranges` passes
        `range_channels()`, which is the selection when the y mode is
        per-channel.  So a frequency reset in per-channel mode moves the
        selected lanes and not the rest -- measured on two channels with
        only channel 0 selected, both squashed to 1200-2400 Hz: channel 0
        came back to 0-4000 and channel 1 stayed squashed.  That is what
        `Ctrl+Left` and every other range command already do, and a gesture
        that quietly reached further than the keys would be the surprise.

        Routed through the window when there is one, so the explicit
        `link_ranges` fan-out `v` and `Shift+V` use runs for this too.  Note
        that it reaches a linked tab either way -- measured with two tabs and
        a link-off control, a double click on the amplitude axis moved the
        other tab with `link_ranges` on and left it alone with it off, both
        before and after this routing existed, because `sigRangesChanged`
        gets there through `Audian.dispatch_ranges` on its own.  Going
        through the window is for saying so in one place rather than
        depending on which of the two paths fires.
        """
        gui = getattr(self.browser, "gui", None)
        if self.y() in Panel.amplitudes and self.browser.y_mode != self.browser.y_fixed:
            if gui is not None:
                gui.auto_amplitude()
            else:
                self.browser.auto_ampl()
        elif gui is not None:
            gui.apply_ranges("default_view", self.y())
        else:
            self.browser.apply_ranges("default_view", self.y())

    def update_axis_label(self) -> None:
        """Put the amplitude unit on the left axis.

        Only when the axis is actually showing tick values: in a dense stack
        it is collapsed to zero width, and a label there would be painted
        into a column that does not exist.  The stack's shared Y readout
        carries the unit for that case instead.
        """
        axis = self.getAxis("left")
        unit = self.data_unit()
        if not (self._show_tick_values and unit):
            axis.setLabel(None)
            return
        if si_prefixable(unit):
            # a real SI unit: let pyqtgraph rescale the ticks and prefix it
            axis.enableAutoSIPrefix(True)
            axis.setLabel("amplitude", unit, color=theme.token("fg.muted"))
        else:
            # Anything else must be shown verbatim.  pyqtgraph prefixes ANY
            # string it is handed as a unit: "a.u." -- what thunderlab reports
            # for a wav carrying no unit metadata -- came out as "ma.u.", with
            # the ticks rescaled by 1000, disagreeing with the stack's own Y
            # readout directly underneath.
            axis.enableAutoSIPrefix(False)
            axis.setLabel(f"amplitude ({unit})", color=theme.token("fg.muted"))

    def _update_caption(self) -> None:
        color = theme.qcolor("primary" if self.current else "fg.muted")
        self.channel_label.setColor(color)
        self.channel_label.setFont(
            theme.font_mono(theme.SIZE_SMALL_PT, bold=self.current)
        )
        text = self.caption_text()
        if self.overlay_channels and len(self.overlay_channels) <= MAX_LEGEND_CHANNELS:
            # `setHtml` keeps the item's font and default colour, so only the
            # channel numbers need a span
            text = text[: -len(self.overlay_legend())] + self.overlay_legend(True)
            self.channel_label.setHtml(text)
        else:
            self.channel_label.setText(text)
        self._place_caption()

    def _place_caption(self) -> None:
        """Inset the caption from the view box corner.

        S8 from the left, not S4: the left axis right-aligns its tick labels
        hard against the view box edge, so a 4 px inset puts the caption's
        first glyph one pixel from the ``0.2`` tick's dash and the two read as
        a single string.  S4 from the top pairs with `Y_TOP_PAD`, which keeps
        the topmost tick out from under the caption in the first place.
        """
        view = self.getViewBox()
        (x0, x1), (y0, y1) = view.viewRange()
        width = max(view.width(), 1)
        height = max(view.height(), 1)
        dx = (x1 - x0) * theme.S8 / width
        dy = (y1 - y0) * theme.S4 / height
        self.channel_label.setPos(x0 + dx, y1 - dy)

    def _view_resized(self) -> None:
        self._place_caption()
        dense = theme.is_dense(self.visible_channels())
        if dense != self.dense:
            self.dense = dense
            self._style_traces()
        show = self.getViewBox().height() >= TICK_VALUES_MIN_HEIGHT
        if show != self._show_tick_values:
            self._show_tick_values = show
            self.getAxis("left").setStyle(showValues=show)
            self.update_axis_label()
            # the caption states what the axis cannot, so it has to be
            # rebuilt whenever the axis appears or disappears
            self._update_caption()
            # Below the threshold the caption has nowhere to sit except on
            # top of the waveform.  Sixteen channels at 34 px is exactly
            # that case, and the channel rail already names every row, so
            # the in-plot caption is redundant there rather than missing.
            self.channel_label.setVisible(show)

    def _hovered(self, x, y) -> None:
        channel = self.channel
        if self.overlay_items:
            nearest = self.nearest_channel(float(x), float(y))
            if nearest is not None:
                channel = nearest
                self.emphasize_channel(nearest)
        self.sigHoverValue.emit(channel, float(x), float(y))

    # --- ranges -----------------------------------------------------------

    def range(self, axspec):
        if axspec == self.x():
            if len(self.data_items) > 0:
                tmax = self.data_items[0].data.frames / self.data_items[0].data.rate
                return 0, tmax, min(10, tmax)
            else:
                return 0, None, 10
        elif axspec == self.y():
            amin = None
            amax = None
            astep = 1
            for item in self.data_items:
                a0 = item.data.ampl_min
                a1 = item.data.ampl_max
                if amin is None or a0 < amin:
                    amin = a0
                if amax is None or a1 > amax:
                    amax = a1
            if amin is None:
                amin = -1
            if amax is None:
                amax = +1
            return amin, amax, astep

    def amplitudes(self, t0, t1):
        """Data range in `[t0, t1)`, plus `Y_TOP_PAD` headroom at the top.

        This is what `PlotRanges.auto_fit()` fits the y range to, so it is the
        only place that can reserve the strip the in-plot caption sits in.
        """
        amin = None
        amax = None
        # the overlaid channels too: one panel has one y range, and it has
        # to hold every trace drawn in it
        for item in self.data_items + self.overlay_items:
            if not item.isVisible():
                continue
            i0 = int(np.round(t0 * item.rate))
            i1 = int(np.round(t1 * item.rate))
            i0 = max(i0, 0)
            i1 = min(i1, len(item.data))
            if i1 <= i0:
                continue
            a0 = np.min(item.data[i0:i1, item.channel])
            a1 = np.max(item.data[i0:i1, item.channel])
            if amin is None or a0 < amin:
                amin = a0
            if amax is None or a1 > amax:
                amax = a1
        if amin is not None and amax is not None and amax > amin:
            amax += self.Y_TOP_PAD * (amax - amin)
        return amin, amax

    def get_marker_pos(self, x, dx, y, dy):
        """Snap the cross hair to the trace under the pointer.

        On the overlay panel that is the trace whose snapped vertex is
        nearest `y`, and `marker_channel` says which channel it was;
        otherwise it is the topmost visible trace, as it always was.
        """
        best = None
        items = reversed(self.data_items)
        if self.overlay_items:
            items = self.drawn_traces()
        for item in items:
            if not item.isVisible():
                continue
            i0 = max(int(np.round(x * item.rate)), 0)
            i1 = max(int(np.round((x + dx) * item.rate)), i0 + 1)
            if i1 > len(item.data):
                i1 = len(item.data)
            if i1 <= i0:
                i0 = max(0, i1 - 1)
            if i0 >= i1:
                i1 = i0 + 1
            k0 = i0 + np.argmin(item.data[i0:i1, item.channel])
            k1 = i0 + np.argmax(item.data[i0:i1, item.channel])
            y0 = item.data[k0, item.channel]
            y1 = item.data[k1, item.channel]
            yc = (y0 + y1) / 2
            if y >= yc:
                snap = (k1 / item.rate, y1, None)
            else:
                snap = (k0 / item.rate, y0, None)
            if not self.overlay_items:
                self.marker_channel = self.channel
                return snap
            if best is None or abs(snap[1] - y) < abs(best[1][1] - y):
                best = (item.channel, snap)
        if best is not None:
            self.marker_channel = best[0]
            return best[1]
        return x, y, None

    def set_starttime(self, mode):
        self.getAxis("bottom").set_starttime_mode(mode)
        self.getAxis("top").set_starttime_mode(mode)
