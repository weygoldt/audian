"""The bands, drawn on one spectrogram lane.

One `BandOverlay` per lane, built when the plugin's tab opens and taken down
when it closes.  It draws and does not decide: what a band means, which one is
selected and what an edit does are the panel's, in the same split
`labeloverlay` has with `databrowser` -- and for the same reason, which is
that a renderer that also edits is a renderer nobody can test without a
window.

Nine items, not one per band
----------------------------

Every band sharing a colour is drawn as a *single* `pg.PlotCurveItem`, its
polylines separated by NaN, which pyqtgraph renders as a break rather than a
line to nowhere.  So a lane holds at most nine curve items -- eight marker
colours and the unlabelled one -- whether the recording has six bands or six
hundred, plus one for the selection and one scatter for the single-vertex
bands.

`EODsorter.plot_traces` did the other thing: a matplotlib ``ax.plot`` per
identity, held in a list, and every interaction removed and rebuilt all of
them.  On a grid recording with a few hundred tracked identities that is a
few hundred artists created and destroyed per keypress, and it is most of why
that program felt the way it did.  The count here is bounded by the palette
instead of by the data, and a pan re-uploads two arrays.

Colours taken from the map, not chosen against it
-------------------------------------------------

audian offers eight spectrogram colour maps across two themes and they do not
agree about which end is dark, so any fixed colour is wrong under some of
them.  The first attempt here was `theme.FG_MUTED`, a grey-blue, which
disappeared into every blue map on the list.

So the marks are drawn in the map's *own* two ends, read off the lane:

* The selected band takes a colour **opposed to the peak**: the peak's hue
  turned half way round at full chroma, or magenta when the peak is white or
  black and has no hue to oppose.  A band lies on a ridge and a ridge is the
  top of the ramp, so opposing the top is opposing what is under the line.
* An unlabelled band takes the map's **brightest** colour, lifted to full
  value.
* A labelled band keeps its category's colour, which is the answer to "which
  of these is which" and is what the table beside it shows.

The floor colour was tried for the unlabelled bands and is wrong, which is
worth recording because the argument for it is a good one: a band sits on a
ridge, so paint it in the colour the ridge is furthest from.  What that
argument leaves out is how much of a spectrogram is *not* ridge.  With the
levels set so the noise floor is black -- which is what audian's own
`fit_levels` aims for, and what most of the picture then is -- a floor
coloured band is a black line on a black field everywhere except the few
pixels it is marking.  Measured on the four-lane synthetic recording, three
of six bands were invisible.  Brightness is the property that survives, and
the map's own bright end supplies it without this module knowing which map is
on.

`map_ends` reads the map from the lane's colour bar on every draw and the
result is part of the redraw key, so changing the map repaints the bands in
the new one's colours instead of leaving them in the old one's.

Redrawing only when something changed
-------------------------------------

`update_plot` compares a small tuple -- the band set's revision, the
selection, the view range, the visibility -- against the last one it drew,
and returns without touching the scene when they match.  `sigRangeChanged`
fires for reasons that are not a change to what is on screen (a resize that
keeps the range, a sibling lane's autorange), and a redraw per signal is a
redraw per frame of a drag.

Decimation
----------

A band spanning a long recording has more vertices than the lane has pixels,
and drawing all of them is work whose result is invisible.  Vertices are
strided so that no band contributes more than `PIXEL_DENSITY` points per
pixel of lane width.  Strided rather than averaged: a band is a measurement,
and a mean of two frequencies either side of a step is a frequency the animal
never had.  The selected band is drawn undecimated, because that is the one
the reader is looking at closely.
"""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor

from audian.pluginapi import theme

#: Above the spectrogram image and the event marks, below the editable
#: labels.  A band is context for a label, so a label drawn on top of one
#: stays readable; `labeloverlay.LABEL_Z` is 25 and `eventoverlay.MARK_Z`
#: is 15.
#: The reference sits *under* the working bands, because it is what they
#: are being compared against: where the two agree the reader should see
#: their own band, and where they disagree they should see both.
REFERENCE_Z = 18
BAND_Z = 20
SELECTED_Z = 22
TEXT_Z = 23

#: Width and dash of a reference band.
#:
#: Colour says *what* a band is and the dash says *who claims it*: a
#: reference Sternopygus and a Sternopygus the reader labelled share a hue,
#: one dashed and one solid, so the comparison is one glance rather than two
#: legends.  Distinguishing them by colour instead would have cost the
#: species colour, which is the more useful of the two things to see.
REFERENCE_WIDTH_PX = 1.6
REFERENCE_DASH = (5.0, 4.0)

#: Width of an ordinary band and of the selected one, in pixels.
#:
#: Thick, because these are marks to be seen and aimed at rather than
#: measurements to be read off: a band is a claim about which signal is which,
#: and at one pixel over a busy spectrogram it was a claim nobody could
#: follow across the picture.
#:
#: The selected band is thicker *as well as* differently coloured, so a reader
#: who cannot separate the two hues can still see which band is selected.
BAND_WIDTH_PX = 2.8
SELECTED_WIDTH_PX = 4.6

#: Most vertices to draw per pixel of lane width, per band.
#:
#: Two rather than one: a band that doubles back within a pixel column has
#: two frequencies there, and at one per pixel which of them is drawn depends
#: on where the stride happens to land.
PIXEL_DENSITY = 2.0

#: Band ids are written beside the bands only while at most this many are in
#: view.  Past it the numbers overlap each other and the bands, and a lane of
#: unreadable digits is worse than no numbers -- the table in the panel is
#: where a reader looks one up when the lane is crowded.
MAX_LABELS_DRAWN = 24

#: Key the unlabelled bands share in the per-colour item dictionaries.
#: A string that cannot collide with a palette index.
UNLABELLED_KEY = "unlabelled"

#: Fallback selection colour when the map's bright end has no hue to oppose.
#:
#: Every map audian offers ends near white or near black, and the complement
#: of an achromatic colour is not defined -- ``QColor('#fcf9f3').getHsv()``
#: reports a saturation of 9, which is noise rather than a hue.  Magenta is
#: the choice because it is the one strongly saturated colour that appears
#: nowhere in a perceptually uniform dark-to-light ramp.
ACHROMATIC_SELECTION = "#FF2BD6"

#: Below this saturation a colour is treated as having no hue at all.
ACHROMATIC_SATURATION = 60


def map_ends(ax) -> tuple:
    """The floor and peak colours of the colour map on this lane.

    Read from the lane rather than fixed, so the marks stay legible when the
    reader changes the map -- there are eight of them and they do not agree
    about which end is dark.  `pg.ColorBarItem` is where a `SpectrogramPlot`
    keeps the map it was built with.
    """
    cmap = None
    cbar = getattr(ax, "cbar", None)
    if cbar is not None and hasattr(cbar, "colorMap"):
        try:
            cmap = cbar.colorMap()
        except Exception:  # noqa: BLE001 - a missing map is not a crash
            cmap = None
    if cmap is None:
        cmap = theme.spectrogram_colormap(theme.DEFAULT_SPECTROGRAM_MAP)
    return cmap.map(0.0, mode="qcolor"), cmap.map(1.0, mode="qcolor")


def brightened(color) -> str:
    """`color` at full value, as a hex string.

    The map's bright end is what an unlabelled band is drawn in, and on a
    map whose top is a strong hue rather than white -- inferno ends yellow,
    CET-L18 ends amber -- taking it verbatim would draw the band in exactly
    the colour of the loudest pixels it is sitting on.  Full value pushes it
    clear of them while keeping the hue, so the marks still look like they
    belong to the picture.
    """
    hue, saturation, _value, _alpha = color.getHsv()
    if hue < 0:
        return "#FFFFFF"
    return QColor.fromHsv(hue, min(saturation, 90), 255).name()


def opposed(color) -> str:
    """A colour that stands against `color`, as a hex string.

    Its hue turned half way round the wheel at full chroma.  Used against the
    map's *bright* end, because that is what a band is lying on: a band marks
    a ridge, and a ridge is where the ramp is at its top.
    """
    hue, saturation, _value, _alpha = color.getHsv()
    if hue < 0 or saturation < ACHROMATIC_SATURATION:
        return ACHROMATIC_SELECTION
    return QColor.fromHsv((hue + 180) % 360, 255, 255).name()


def _passive(item) -> None:
    """Make an item invisible to the mouse.

    Everything here is drawn *under* the reader's pointer, not aimed at:
    picking is done by the panel, from the click position against the band
    geometry, so that clicking near a band selects it rather than requiring
    the reader to hit a 1.6 px line.  An item that accepted clicks would eat
    the drags the lane's own view box needs for zooming.
    """
    item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
    item.setAcceptHoverEvents(False)


def stride_for(n: int, width_px: float) -> int:
    """Take every `stride`-th vertex, so `n` of them fit `width_px` pixels."""
    if width_px <= 0 or n <= 0:
        return 1
    allowed = max(2.0, width_px * PIXEL_DENSITY)
    return max(1, int(np.ceil(n / allowed)))


def joined(pieces: list) -> tuple:
    """Several polylines as one ``(x, y)`` pair, separated by NaN.

    The one trick this module depends on: `pg.PlotCurveItem` breaks its line
    at a non-finite point, so unrelated bands can share a single item without
    a stroke running between the end of one and the start of the next.
    """
    if not pieces:
        return (np.zeros(0), np.zeros(0))
    xs, ys = [], []
    for x, y in pieces:
        xs.append(np.asarray(x, dtype=np.float64))
        xs.append(np.array([np.nan]))
        ys.append(np.asarray(y, dtype=np.float64))
        ys.append(np.array([np.nan]))
    # drop the trailing separator, which would otherwise leave the curve's
    # bounding box carrying a NaN and pyqtgraph warning about it
    return (np.concatenate(xs)[:-1], np.concatenate(ys)[:-1])


class BandOverlay:
    """Every band of one `BandSet`, on one spectrogram lane."""

    def __init__(self, ax, bands, colors) -> None:
        self.ax = ax
        #: the `bands.BandSet` being drawn; replaced wholesale by `set_bands`
        self.bands = bands
        #: ``category -> palette index``, owned by the panel so that a colour
        #: is the same on every lane and in the table
        self.colors = colors
        self.visible = True
        self.selection: tuple = ()
        self._drawn = None
        #: the map's two ends and the selection colour taken from them,
        #: refreshed on every draw so a change of map is followed
        self.floor, self.peak = map_ends(ax)
        self.selection_color = opposed(self.peak)
        self.unlabelled_color = brightened(self.peak)

        self.curves: dict = {}
        #: the read-only band set drawn dashed underneath, or None
        self.reference = None
        self.reference_visible = True
        self.ref_curves: dict = {}
        # Antialiased, unlike audian's own `TraceItem`, and the difference is
        # in what is being drawn.  A trace is a million points wide and its
        # pen is off by default because smoothing that many segments costs
        # real time; a band is a few hundred points and is nearly horizontal,
        # which is the worst case for aliasing -- an unsmoothed near-flat line
        # steps a whole pixel wherever it crosses a row boundary, and reads as
        # the frequency jumping rather than the renderer rounding.
        self.selected = pg.PlotCurveItem(antialias=True)
        self.selected.setZValue(SELECTED_Z)
        _passive(self.selected)
        # ignoreBounds throughout: a bare addItem joins the lane's
        # childrenBounds, and a band at 806 Hz on a lane showing 0-400 Hz
        # would drag its auto-range onto empty spectrogram.  `labeloverlay`
        # adds every one of its items this way for the same reason.
        self.ax.addItem(self.selected, ignoreBounds=True)

        self.dots = pg.ScatterPlotItem(symbol="o", size=5, pxMode=True, hoverable=False)
        self.dots.setZValue(BAND_Z)
        _passive(self.dots)
        self.ax.addItem(self.dots, ignoreBounds=True)

        self.texts: list = []

        view = ax.getViewBox()
        if view is not None:
            view.sigRangeChanged.connect(self._view_changed)
            view.sigResized.connect(self._view_changed)

    # --- plumbing ---------------------------------------------------------

    def _view_changed(self, *args) -> None:
        self.update_plot()

    def _curve(self, key: str):
        """The curve item for one colour, made the first time it is needed."""
        item = self.curves.get(key)
        if item is None:
            item = pg.PlotCurveItem(antialias=True)
            item.setZValue(BAND_Z)
            _passive(item)
            self.ax.addItem(item, ignoreBounds=True)
            self.curves[key] = item
        return item


    def _text(self, index: int):
        while len(self.texts) <= index:
            item = pg.TextItem(anchor=(0.0, 1.0))
            item.setZValue(TEXT_Z)
            item.setFont(theme.font_ui())
            _passive(item)
            item.setVisible(False)
            self.ax.addItem(item, ignoreBounds=True)
            self.texts.append(item)
        return self.texts[index]

    def _ref_curve(self, key: str):
        """The dashed curve item for one reference colour."""
        item = self.ref_curves.get(key)
        if item is None:
            item = pg.PlotCurveItem(antialias=True)
            item.setZValue(REFERENCE_Z)
            _passive(item)
            self.ax.addItem(item, ignoreBounds=True)
            self.ref_curves[key] = item
        return item

    def _ref_pen(self, key: str):
        pen = theme.pen(self._color(key), width=REFERENCE_WIDTH_PX)
        pen.setStyle(Qt.PenStyle.CustomDashLine)
        pen.setDashPattern(list(REFERENCE_DASH))
        return pen

    def set_reference(self, reference) -> None:
        """The band set to draw dashed underneath, or None for none."""
        self.reference = reference
        self.invalidate()
        self.update_plot()

    def set_reference_visible(self, on: bool) -> None:
        if bool(on) == self.reference_visible:
            return
        self.reference_visible = bool(on)
        self.invalidate()
        self.update_plot()

    def set_bands(self, bands) -> None:
        self.bands = bands
        self.invalidate()
        self.update_plot()

    def set_selection(self, ids) -> None:
        selection = tuple(sorted(int(i) for i in ids))
        if selection == self.selection:
            return
        self.selection = selection
        self.update_plot()

    def set_visible(self, on: bool) -> None:
        if bool(on) == self.visible:
            return
        self.visible = bool(on)
        self.update_plot()

    def invalidate(self) -> None:
        """Forget what was drawn, so the next `update_plot` really draws."""
        self._drawn = None

    def detach(self) -> None:
        """Take every item off the lane.

        Called when the plugin's tab closes.  A plugin that leaves its items
        behind leaves a recording marked up by something the reader has
        turned off, and the marks cannot then be removed without reopening
        it.
        """
        view = self.ax.getViewBox()
        if view is not None:
            try:
                view.sigRangeChanged.disconnect(self._view_changed)
                view.sigResized.disconnect(self._view_changed)
            except (RuntimeError, TypeError):
                pass
        for item in [self.selected, self.dots, *self.curves.values(),
                     *self.ref_curves.values(), *self.texts]:
            try:
                self.ax.removeItem(item)
            except (RuntimeError, ValueError):
                pass
        self.curves.clear()
        self.ref_curves.clear()
        self.texts.clear()

    # --- drawing ----------------------------------------------------------

    def view_range(self) -> tuple:
        view = self.ax.getViewBox()
        if view is None:
            return ((0.0, 0.0), (0.0, 0.0))
        (x0, x1), (y0, y1) = view.viewRange()
        return ((float(x0), float(x1)), (float(y0), float(y1)))

    def width_px(self) -> float:
        view = self.ax.getViewBox()
        if view is None:
            return 1000.0
        return max(1.0, float(view.boundingRect().width()))

    def _color_key(self, band) -> str:
        if not band.category:
            return UNLABELLED_KEY
        return str(self.colors.get(band.category, 0))

    def _color(self, key: str) -> str:
        """What one colour group is drawn in.

        A band nobody has labelled is drawn in the **floor colour of the
        colour map underneath it**.  That sounds like drawing it invisibly and
        is the opposite: a band lies on a ridge, a ridge is the top of the
        ramp, and the bottom of the ramp is the one colour guaranteed to be
        far from it -- on every map, in both themes, without this module
        knowing which map is on.  It was a fixed grey-blue before, which
        disappeared into every blue map audian offers.

        A band the reader has labelled keeps its category's colour, because
        that colour is the answer to "which of these is which" and is what the
        table beside it shows.
        """
        if key == UNLABELLED_KEY:
            return self.unlabelled_color
        return theme.marker_color(int(key))

    def _pen(self, key: str):
        return theme.pen(self._color(key), width=BAND_WIDTH_PX)

    def update_plot(self) -> None:
        """Redraw the lane, or return having found nothing to redraw."""
        (x0, x1), (y0, y1) = self.view_range()
        state = (
            id(self.bands),
            getattr(self.bands, "revision", 0),
            self.selection,
            self.visible,
            round(x0, 6),
            round(x1, 6),
            round(y0, 6),
            round(y1, 6),
            round(self.width_px()),
        )
        # Re-read the map every pass and put it in the redraw key, so
        # changing the colour map repaints the bands in the new one's colours
        # rather than leaving them in the previous map's.
        self.floor, self.peak = map_ends(self.ax)
        self.selection_color = opposed(self.peak)
        self.unlabelled_color = brightened(self.peak)
        state = (
            *state,
            self.unlabelled_color,
            self.selection_color,
            id(self.reference),
            getattr(self.reference, "revision", -1),
            self.reference_visible,
        )
        if state == self._drawn:
            return
        self._drawn = state

        if not self.visible:
            for item in (*self.curves.values(), *self.ref_curves.values()):
                item.setData(x=[], y=[])
            self.selected.setData(x=[], y=[])
            self.dots.setData(x=[], y=[])
            for item in self.texts:
                item.setVisible(False)
            return

        width = self.width_px()
        by_color: dict = {}
        chosen: list = []
        dots_x, dots_y = [], []
        visible = self.bands.in_window(x0, x1)
        for band in visible:
            if len(band) == 1:
                dots_x.append(band.t0)
                dots_y.append(float(band.freqs[0]))
                continue
            if band.bid in self.selection:
                # undecimated: this is the band being looked at
                chosen.append((band.times, band.freqs))
                continue
            step = stride_for(len(band), width)
            piece = (band.times[::step], band.freqs[::step])
            by_color.setdefault(self._color_key(band), []).append(piece)

        self._draw_reference(x0, x1, width)

        for key, item in self.curves.items():
            if key not in by_color:
                item.setData(x=[], y=[])
        for key, pieces in by_color.items():
            x, y = joined(pieces)
            item = self._curve(key)
            item.setPen(self._pen(key))
            item.setData(x=x, y=y, connect="finite")

        sx, sy = joined(chosen)
        self.selected.setPen(
            theme.pen(self.selection_color, width=SELECTED_WIDTH_PX)
        )
        self.selected.setData(x=sx, y=sy, connect="finite")

        self.dots.setPen(theme.pen(self.unlabelled_color, width=1.0))
        self.dots.setBrush(theme.brush(self.unlabelled_color))
        self.dots.setData(x=dots_x, y=dots_y)

        self._draw_ids(visible, x0, x1)

    def _draw_reference(self, x0: float, x1: float, width: float) -> None:
        """The dashed ground truth under the working bands.

        Grouped by colour and NaN-joined the same way, so a reference of two
        hundred bands is still at most nine more curve items on the lane.
        Always decimated, including anything selected: nothing here can be
        selected, because a reference is not the reader's to edit.
        """
        show = self.reference is not None and self.reference_visible
        by_color: dict = {}
        if show:
            for band in self.reference.in_window(x0, x1):
                if len(band) < 2:
                    continue
                step = stride_for(len(band), width)
                by_color.setdefault(self._color_key(band), []).append(
                    (band.times[::step], band.freqs[::step])
                )
        for key, item in self.ref_curves.items():
            if key not in by_color:
                item.setData(x=[], y=[])
        for key, pieces in by_color.items():
            x, y = joined(pieces)
            item = self._ref_curve(key)
            item.setPen(self._ref_pen(key))
            item.setData(x=x, y=y, connect="finite")

    def _draw_ids(self, visible: list, x0: float, x1: float) -> None:
        """Write each band's id beside it, while there are few enough to read."""
        shown = 0
        if len(visible) <= MAX_LABELS_DRAWN:
            for band in visible:
                # at the band's first vertex inside the view, so a band that
                # started off-screen is still named where it enters
                inside = np.searchsorted(band.times, x0, side="left")
                inside = min(int(inside), len(band) - 1)
                item = self._text(shown)
                selected = band.bid in self.selection
                item.setText(str(band.bid))
                item.setColor(
                    theme.qcolor(
                        self.selection_color
                        if selected
                        else self._color(self._color_key(band))
                    )
                )
                item.setPos(float(band.times[inside]), float(band.freqs[inside]))
                item.setVisible(True)
                shown += 1
        for item in self.texts[shown:]:
            item.setVisible(False)

    # --- picking ----------------------------------------------------------

    def band_near(self, t: float, f: float, tol_t: float, tol_f: float):
        """The band whose nearest vertex to ``(t, f)`` is closest, or None.

        Distance is measured in units of the tolerances -- which the panel
        derives from pixels -- so that "nearest" means nearest *on screen*
        and not nearest in Hz, which on a lane showing 0-1000 Hz over 120 s
        would make every click land on whichever band was lowest.

        Nearest-vertex rather than nearest-segment: it costs one array
        operation per visible band, and the difference only shows on a band
        so sparse that its vertices are further apart than the click
        tolerance -- at which point the vertices are what the reader can see
        anyway.
        """
        best, best_d = None, None
        for band in self.bands.in_window(t - tol_t, t + tol_t):
            dt = (band.times - t) / tol_t
            df = (band.freqs - f) / tol_f
            d2 = dt * dt + df * df
            i = int(np.argmin(d2))
            if d2[i] <= 1.0 and (best_d is None or d2[i] < best_d):
                best, best_d = band.bid, d2[i]
        return best
