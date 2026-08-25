"""The control track: what the stimulator was set to, over the whole session.

This is **not an annotation and not an overlay**.  An annotation says *an
event happened here*; the control track says *this setting was in force from
here until it changed*.  Nothing about it is bounded in x the way a span is,
it has a y that means a number rather than "the whole lane", and it belongs to
the session rather than to any one electrode.  So it is drawn in a panel of
its own, sharing the stack's time axis, and `EventOverlay` deliberately draws
none of it (see `EventOverlay._wanted`).

Why a panel and not a `Panel`
-----------------------------
`panels.Panel` is the per-channel plot row, and this row is session-global.
Three things make the per-channel machinery the wrong home, none of them
stylistic:

* `PlotRanges.setup` registers only the axis letters in
  ``Panel.times + amplitudes + frequencies + powers``, and `add_plot` raises
  `KeyError` on anything else, so the track would have to borrow a free
  amplitude letter;
* having borrowed one, `PlotRanges.auto_fit` would couple the tick rate to the
  waveform's y policy -- pressing "fit amplitude" would rescale the throttle;
* `DataBrowser.open` builds every panel once per channel, so a sixteen
  channel file would carry sixteen copies of one session-wide fact.

Docking it into `DataBrowser.splitter` was the other candidate and costs a
third size in `size_splitter` plus its own left-margin sync.  Instead the
panel is a strip in `stack_pane`'s column, built with **exactly the widget
structure of the shared time axis** -- a fixed-width corner reserving the
channel rail, then a `GraphicsLayoutWidget` -- so the one left/right margin
`DataBrowser.align_time_axis` already measures off a lane's view box lines
this panel up too, with no second measurement to keep in step.  Nothing in
`lane_geometry` changes: the strip sits outside the scroll area, so switching
it on simply hands `stack_area.viewport()` fewer pixels and the existing
viewport-resize path re-solves the stack.

What it draws
-------------
A **step plot**, never markers and never interpolation: one row of the CSV is
one *change*, and the value stands until the next one.  `windowing.window_steps`
builds the staircase, reaching one row backwards so a value held across a long
gap survives -- on exp2 the control rows span 28.9-590.0 s with a 41.0 s gap
right after the first one, and a series that started at the first row *inside*
the view would draw nothing there and read as "no data" rather than "steady".

Each channel gets its own band with its own frozen range, because `tick_hz`
(0.5-20 Hz) and `randomness` (0.067-1) share no axis with each other or with a
waveform.  The band's floor rule and its scale label carry the real numbers,
so the height of a step is readable without guessing.

**A gap is not a zero.**  `StepTrack` stores null as NaN and `value_at`
returns NaN when nothing is in force, so the staircase has no line there --
which is why every curve is drawn with ``connect="finite"`` and not with
pyqtgraph's default, which joins straight across a NaN.  A throttle of zero
and a throttle nobody has measured are different states of the boat, and the
floor rule under an empty stretch is what says the band is switched on and has
nothing to report.
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pyqtgraph as pg

from PyQt5.QtWidgets import QHBoxLayout, QWidget

from . import theme, windowing
from .eventoverlay import _passive
from .layers import LAYER_CONTROLS, StepTrack

_EMPTY = np.empty(0, dtype=np.float64)

#: Pixel budget assumed when the panel has no width yet, and the floor under
#: which a measured width is not believed.  Same numbers and same reason as
#: `eventoverlay`: a plot that has not been laid out reports a width of 0, and
#: decimating a staircase to zero columns would empty the panel on the first
#: draw and leave it empty until something else moved the view.
DEFAULT_PIXELS = 1200
MIN_PIXELS = 16


class ControlPanel(QWidget):
    """The optional session-global strip that draws a bundle's `StepTrack`.

    Off by default and costing zero pixels while it is off: the layer switch
    it reads (`AnnotationLayer.is_enabled(LAYER_CONTROLS)`) is the same one the
    chip in the parameter bar drives and the same one the settings file
    persists, so the panel adds no toggle of its own and cannot disagree with
    the chip about whether it is showing.

    Without it a bundle's `controls` layer is loaded, counted and toggleable
    but never drawn anywhere -- the overlay refuses it on purpose.
    """

    def __init__(self, layer, rail_width: int, parent=None) -> None:
        super().__init__(parent)
        #: the browser's `eventoverlay.AnnotationLayer`; this panel reads its
        #: bundle, its switches and its `revision`, and never writes to it
        self.layer = layer
        self.track: Optional[StepTrack] = None
        #: channels with a band, top to bottom, in the loader's order
        self.names: tuple[str, ...] = ()
        self.curves: dict[str, pg.PlotCurveItem] = {}
        self.rules: dict[str, pg.InfiniteLine] = {}
        self.labels: dict[str, pg.TextItem] = {}
        self.note: Optional[pg.TextItem] = None
        #: y range of the plot, in view units that are pixels by construction
        self.total = 0
        #: what was last drawn: view range, pixel budget, layer revision
        self._drawn: Optional[tuple] = None
        #: the left/right margin last applied, cached because a
        #: QGraphicsGridLayout cannot be asked what its margins are
        self._margins: Optional[tuple[int, int]] = None
        #: channels whose curve is already empty, so clearing a band that is
        #: already clear costs nothing.  Most of the session is outside any
        #: one view and `setData` is not free even with nothing in it.
        self._blank: set[str] = set()

        box = QHBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(theme.S4)
        # The corner reserves the channel rail's column exactly as the time
        # axis strip's does, which is what makes the axis strip's measured
        # margins apply to this figure unchanged.
        self.corner = QWidget(self)
        self.corner.setFixedWidth(rail_width)
        box.addWidget(self.corner, 0)

        self.fig = pg.GraphicsLayoutWidget()
        theme.style_figure(self.fig)
        # No margins of its own: set_margins() is handed the left and right
        # the lanes turned out to use, and a vertical margin would eat into
        # the band arithmetic, which is in pixels.
        self.fig.ci.layout.setContentsMargins(0, 0, 0, 0)
        self.plot = pg.PlotItem()
        self.plot.showAxes(False)
        # pyqtgraph gives every PlotItem a 1 px inset.  The shared time axis is
        # a bare AxisItem and has none, so the same measured left margin would
        # put this plot one pixel right of the axis and of the lanes -- and it
        # would make the view box 72 px tall inside a 74 px strip, which is
        # what the band arithmetic below is counting in.
        self.plot.layout.setContentsMargins(0, 0, 0, 0)
        self.plot.getViewBox().setDefaultPadding(padding=0)
        self.plot.hideButtons()
        self.plot.setMenuEnabled(False)
        theme.strip_pg_menus(self.plot)
        # strip_pg_menus() leaves pyqtgraph's control widgets alive but
        # parentless, and a parentless QWidget is a top-level window that a
        # tiling compositor will place.  Adopt them.
        for widget in getattr(self.plot, "_audian_ctrl_widgets", []):
            widget.setParent(self)
            widget.setVisible(False)
        self.plot.setMouseEnabled(False, False)
        self.plot.enableAutoRange(False, False)
        self.fig.addItem(self.plot, row=0, col=0)
        box.addWidget(self.fig, 1)

        view = self.plot.getViewBox()
        view.sigRangeChanged.connect(self._view_changed)
        view.sigResized.connect(self._view_changed)

        self.setVisible(False)
        self.setFixedHeight(0)

    # --- wiring the browser drives ---------------------------------------

    def link_view(self, view) -> None:
        """Follow `view`'s time range.

        One `setXLink`, onto whichever lane the shared time axis is reading,
        so the panel is on the same x as the lanes by construction rather than
        by a second copy of the pan path.  The panel's own view box has the
        mouse disabled, so the link only ever carries range *in*.
        """
        own = self.plot.getViewBox()
        if own is None or view is None or own.linkedView(own.XAxis) is view:
            return
        own.setXLink(view)

    def set_margins(self, left: int, right: int) -> None:
        """Take the left and right margin the shared time axis measured."""
        if (left, right) == self._margins:
            return
        self._margins = (left, right)
        self.fig.ci.layout.setContentsMargins(left, 0, right, 0)

    def set_rail_width(self, width: int) -> None:
        """Follow the channel rail when it is shown or hidden (F7)."""
        self.corner.setFixedWidth(width)

    # --- geometry ---------------------------------------------------------

    def wanted_height(self) -> int:
        """Pixels this panel asks for: zero unless it is switched on.

        The height is arithmetic on `theme.CONTROL_BAND_H`, never a measured
        one, so the plot's view box is exactly `self.total` pixels tall and
        one view unit is one pixel -- which is what lets the band boundaries
        below be stated as pixel counts and checked as pixel counts.
        """
        if self.track is None or not self.layer.is_enabled(LAYER_CONTROLS):
            return 0
        return self.total

    def band(self, name: str) -> tuple[float, float]:
        """``(bottom, top)`` of one channel's band, in view units.

        Bands stack upwards from the caption row, first channel at the top,
        which is the order the loader offers them in.
        """
        k = self.names.index(name)
        top = self.total - k * theme.CONTROL_BAND_H
        return top - theme.CONTROL_BAND_H, top

    def _map(self, name: str, values: np.ndarray) -> np.ndarray:
        """Put a channel's values into its own band.

        The range is the one `StepTrack` froze at load, never the range in
        view: a staircase whose height changed meaning as the reader panned
        would be unreadable exactly when it matters.  NaN survives the
        arithmetic as NaN, which is how "nothing in force" reaches the curve.
        """
        low, high = self.track.ranges[name]
        bottom, top = self.band(name)
        floor = bottom + theme.CONTROL_BAND_PAD
        ceiling = top - theme.CONTROL_BAND_PAD
        if high <= low:
            # The loader withholds constant channels, so this is only reached
            # by a bundle written after this panel was built.  Mid-band is the
            # honest place for a value with no range to sit in.
            return np.full(values.size, 0.5 * (floor + ceiling))
        return floor + (values - low) * ((ceiling - floor) / (high - low))

    # --- items ------------------------------------------------------------

    def rebuild(self) -> None:
        """Build one band per offered channel of the loaded bundle.

        Called when the bundle changes, never on a pan.  A bundle with no
        `controls` CSV leaves the panel with no track and no height, which is
        not the same state as a track that is switched off.
        """
        self.clear()
        bundle = self.layer.bundle
        track = bundle.get(LAYER_CONTROLS) if bundle is not None else None
        if not isinstance(track, StepTrack):
            return
        self.track = track
        self.names = tuple(track.channels)
        self.total = len(self.names) * theme.CONTROL_BAND_H + theme.CONTROL_NOTE_H
        self.plot.setYRange(0, self.total, padding=0)

        for name in self.names:
            bottom, _top = self.band(name)
            rule = pg.InfiniteLine(
                angle=0,
                pos=bottom + theme.CONTROL_BAND_PAD,
                movable=False,
                pen=theme.pen("fg.faint", width=theme.LW_HAIRLINE),
            )
            rule.setZValue(-10)
            _passive(rule)
            self.plot.addItem(rule, ignoreBounds=True)
            self.rules[name] = rule

            curve = pg.PlotCurveItem(
                pen=theme.annotation_pen(
                    "control",
                    width=theme.LW_THIN,
                    unvalidated=self.layer.unvalidated,
                ),
                antialias=False,
            )
            # Drawn with connect="finite", which is the whole reason a NaN
            # can be trusted to read as a gap.  Measured on pyqtgraph 0.14:
            # the default connect="all" does NOT break at a NaN, it drops the
            # point and joins the finite values either side -- a solid line
            # straight across a stretch where nothing was in force.  Same
            # cost, 4.97 ms against 4.93 ms per path at 200 000 points.
            curve.setZValue(10)
            _passive(curve)
            self.plot.addItem(curve, ignoreBounds=True)
            self.curves[name] = curve

            # Anchored top-left INSIDE the band, filled with the plot's own
            # ground so the text is readable wherever the staircase runs --
            # and BELOW the curve, which is the whole point.  Painted above
            # it, the fill hid the leftmost 148 px of a 1400 px band: exactly
            # the held value `windowing.window_steps` reaches one row
            # backwards to reconstruct, and at the 100-160 s window of exp2
            # the change at 100.1999 s is 7 px into the view and was entirely
            # under the label.  Below the curve the fill is the same colour
            # the ground would have been, so a step drawn over it reads
            # exactly as it does anywhere else in the band, and the only
            # thing the label now covers is the band's own floor rule.
            label = pg.TextItem(
                self.scale_text(name),
                color=theme.annotation_color("control"),
                anchor=(0, 0),
                fill=theme.brush("bg.plot"),
            )
            label.setFont(theme.font_mono(theme.SIZE_SMALL_PT))
            label.setZValue(5)
            _passive(label)
            self.plot.addItem(label, ignoreBounds=True)
            self.labels[name] = label

        # anchor (0, 1): the caption's BOTTOM-left sits on y=0, so it fills the
        # caption row upwards.  Anchored top-left it hangs below the view box
        # and is clipped away entirely -- the row looks blank and the withheld
        # channel goes unmentioned after all.
        self.note = pg.TextItem(track.tip, color=theme.token("fg.faint"), anchor=(0, 1))
        self.note.setFont(theme.font_mono(theme.SIZE_SMALL_PT))
        self.note.setZValue(20)
        _passive(self.note)
        self.plot.addItem(self.note, ignoreBounds=True)
        self.setToolTip(f"{track.label} -- {track.tip}")

    def scale_text(self, name: str) -> str:
        """One band's label: the channel and the real numbers of its range.

        The panel has no y axis -- two channels with two units cannot share
        one -- so the extremes are printed inside the plot instead.  Without
        them the staircase is a shape with no scale and a step could be any
        size at all.
        """
        low, high = self.track.ranges[name]
        unit = self.track.units.get(name, "")
        unit = f" {unit}" if unit else ""
        return f"{name}  {low:g}-{high:g}{unit}"

    def clear(self) -> None:
        """Drop every item, so a new bundle cannot inherit an old band."""
        for item in (
            list(self.curves.values())
            + list(self.rules.values())
            + list(self.labels.values())
            + ([self.note] if self.note is not None else [])
        ):
            self.plot.removeItem(item)
        self.curves = {}
        self.rules = {}
        self.labels = {}
        self.note = None
        self.track = None
        self.names = ()
        self.total = 0
        self._drawn = None
        self._blank = set()
        self.setToolTip("")

    def polish(self) -> None:
        """Re-resolve every pen and colour after a live theme switch."""
        theme.style_figure(self.fig)
        theme.style_plotitem(self.plot)
        if self.track is None:
            return
        unvalidated = self.layer.unvalidated
        for name in self.names:
            self.rules[name].setPen(theme.pen("fg.faint", width=theme.LW_HAIRLINE))
            self.curves[name].setPen(
                theme.annotation_pen(
                    "control", width=theme.LW_THIN, unvalidated=unvalidated
                )
            )
            self.labels[name].setColor(theme.annotation_color("control"))
            # pg.TextItem has setColor but no setFill, so the brush is
            # assigned and the item repainted by hand
            self.labels[name].fill = theme.brush("bg.plot")
            self.labels[name].update()
        if self.note is not None:
            self.note.setColor(theme.token("fg.faint"))

    # --- drawing ----------------------------------------------------------

    def refresh(self) -> bool:
        """Show or hide the panel from the layer's switches.

        Returns whether the height changed, so the caller can re-solve the
        stack only when it has to.  A hidden `QWidget` is given no space by
        its layout, so "off" really is zero pixels rather than an empty strip.
        """
        height = self.wanted_height()
        if height == self.height() and (height > 0) == self.isVisible():
            return False
        self.setFixedHeight(height)
        self.setVisible(height > 0)
        if height > 0:
            # nothing promises a range signal on the way back from hidden
            self._drawn = None
            self.update_plot()
        return True

    def _view_changed(self, *args) -> None:
        self.update_plot()

    def pixels(self) -> int:
        """Device pixel width of the panel's view box."""
        view = self.plot.getViewBox()
        if view is None:
            return DEFAULT_PIXELS
        widget = self.plot.getViewWidget()
        ratio = widget.devicePixelRatioF() if widget is not None else 1.0
        pixels = int(view.width() * ratio)
        return pixels if pixels >= MIN_PIXELS else DEFAULT_PIXELS

    def update_plot(self) -> None:
        """Redraw the staircases for the current time window.

        The x-link delivers a pan here as well as to every lane, and the
        browser's own redraw path reaches it again, so the early-out is what
        keeps a pan from rebuilding the same three paths twice.
        """
        if self.track is None or not self.isVisible():
            self._drawn = None
            return
        view = self.plot.getViewBox()
        if view is None:
            return
        (t0, t1), _y = view.viewRange()
        pixels = self.pixels()
        state = (t0, t1, pixels, self.layer.revision)
        if state == self._drawn:
            return
        self._drawn = state
        if self.note is not None:
            self.note.setPos(t0, 0.0)
        for name in self.names:
            _bottom, top = self.band(name)
            self.labels[name].setPos(t0, top)
            times, values = windowing.window_steps(
                self.track.times,
                self.track.channels[name],
                t0,
                t1,
                self.track.t_end,
                pixels,
            )
            curve = self.curves[name]
            if times.size == 0:
                if name not in self._blank:
                    curve.setData(_EMPTY, _EMPTY, connect="finite")
                    self._blank.add(name)
                continue
            self._blank.discard(name)
            curve.setData(times, self._map(name, values), connect="finite")
