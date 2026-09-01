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

from audian.pluginapi import theme

#: Above the spectrogram image and the event marks, below the editable
#: labels.  A band is context for a label, so a label drawn on top of one
#: stays readable; `labeloverlay.LABEL_Z` is 25 and `eventoverlay.MARK_Z`
#: is 15.
BAND_Z = 20
SELECTED_Z = 22
TEXT_Z = 23

#: Width of an ordinary band and of the selected one, in pixels.
#:
#: The selected band is thicker *as well as* differently coloured, because a
#: reader who cannot separate the highlight colour from the palette can still
#: see which band is selected -- and because on a dense lane the colour of a
#: one-pixel line is the least legible thing on screen.
BAND_WIDTH_PX = 1.6
SELECTED_WIDTH_PX = 3.2

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

#: Colour of a band nobody has labelled yet, which is most of them when a
#: tracker has just run.  Deliberately not a marker colour: unlabelled is a
#: state, not a category, and giving it one of the eight would make the
#: first category a reader creates look like a change of label rather than
#: the addition of one.
UNLABELLED_TOKEN = "fg.muted"


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

        self.curves: dict = {}
        self.selected = pg.PlotCurveItem()
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
            item = pg.PlotCurveItem()
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
        for item in [self.selected, self.dots, *self.curves.values(), *self.texts]:
            try:
                self.ax.removeItem(item)
            except (RuntimeError, ValueError):
                pass
        self.curves.clear()
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
            return UNLABELLED_TOKEN
        return str(self.colors.get(band.category, 0))

    def _pen(self, key: str):
        if key == UNLABELLED_TOKEN:
            return theme.pen(theme.token(UNLABELLED_TOKEN), width=BAND_WIDTH_PX)
        return theme.pen(theme.marker_color(int(key)), width=BAND_WIDTH_PX)

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
        if state == self._drawn:
            return
        self._drawn = state

        if not self.visible:
            for item in self.curves.values():
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
            theme.pen(theme.token("accent"), width=SELECTED_WIDTH_PX)
        )
        self.selected.setData(x=sx, y=sy, connect="finite")

        self.dots.setData(x=dots_x, y=dots_y)

        self._draw_ids(visible, x0, x1)

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
                        theme.token("accent")
                        if selected
                        else (
                            theme.token(UNLABELLED_TOKEN)
                            if not band.category
                            else theme.marker_color(self.colors.get(band.category, 0))
                        )
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
