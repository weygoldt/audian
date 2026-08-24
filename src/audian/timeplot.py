"""PlotItem for displaying any data as a function of time."""

import numpy as np
import pyqtgraph as pg

try:
    from PyQt5.QtCore import Signal
except ImportError:
    from PyQt5.QtCore import pyqtSignal as Signal

from . import theme
from .rangeplot import RangePlot
from .timeaxisitem import TimeAxisItem
from .yaxisitem import YAxisItem


# Below this view box height the tick values collide with each other and with
# the in-plot caption, so only the zero line is left.  This is a layout
# threshold, not a design token - theme.CHANNEL_MIN_HEIGHT (80) is the height a
# channel *should* get, this is the height below which numbers stop being
# readable at all.
TICK_VALUES_MIN_HEIGHT = 48


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

        # channel identity: a horizontal caption inside the view box.  A
        # rotated left axis label overprints the tick values as soon as rows
        # get short (16 channels give about 62 px per row).
        self.caption = ylabel
        self.current = False
        self.dense = False
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

    def caption_text(self) -> str:
        text = f"CH {self.channel:02d}"
        if self.caption:
            text += f"   {self.caption}"
        return text

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
        self.channel_label.setText(self.caption_text())
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
        self.sigHoverValue.emit(self.channel, float(x), float(y))

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
        for item in self.data_items:
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
        for item in reversed(self.data_items):
            if item.isVisible():
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
                    return k1 / item.rate, y1, None
                else:
                    return k0 / item.rate, y0, None
        return x, y, None

    def set_starttime(self, mode):
        self.getAxis("bottom").set_starttime_mode(mode)
        self.getAxis("top").set_starttime_mode(mode)
