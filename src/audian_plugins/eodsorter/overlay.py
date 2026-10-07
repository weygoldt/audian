"""The tracks, drawn on one spectrogram lane.

One `TrackOverlay` per lane, built when the Tracks tab opens and taken down
when it closes.  It draws and does not decide: the model, the selection, the
hover and every preview are in the shared `SceneState`, the arrays come from
the shared `geometry.RenderCache`, and the tools in `tools` fill both.

About thirty items, whatever the number of tracks
-------------------------------------------------

All tracks sharing a colour slot are one NaN-joined `LineItem`
(NaN-joined), so a lane holds ten curves
for the tracks, ten dot layers for their points, one for unassigned
detections, two items for the selection, two for the hovered track, a small
fixed pool for previews and a pool of id labels -- created once, hidden when
empty.  The design asked for one points scatter; it is one `PointsItem`
*per slot*, because a single item would need a colour per point, and the
dots are not `ScatterPlotItem`s at all (see `PointsItem` for why).

Two layers, two keys
--------------------

The **base** layer (tracks, points, unassigned, labels) depends on the model
revision, the view and the display options, and is keyed on exactly those:
a hover never rebuilds it.  The **preview** layer (hover, selection,
previews, stroke captures, flashes) is keyed on `SceneState.revision` and
draws only the few rows those name, undecimated.

Redraws are coalesced: `schedule` arms a zero-timeout timer, so a burst of
range changes, model changes and pointer moves in one event-loop turn is one
redraw.

Colours from the map
--------------------

Every colour is derived from the lane's colour map and re-read on each
redraw (design 5.7): the ten slot hues are 36 degrees apart, bright on a dark
floor and darker on a light one, and steer clear of the map's own bright
hue; the selection is the hue opposed to the map's bright end (magenta on an
achromatic map); halos are drawn in the map's floor colour so a highlighted
track reads on both ends of the ramp.
"""

from __future__ import annotations

import time

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPen

from audian.pluginapi import theme

from . import geometry as G

#: Above the spectrogram and the event marks (15), level with the
#: the old band plugin's layers; the editable labels (25) sit above the tracks.
UNASSIGNED_Z = 19
TRACK_Z = 20
POINT_Z = 20.5
DIM_Z = 19.5
SNIPPET_Z = 21
SELECTED_Z = 22
PREVIEW_Z = 23
LABEL_Z = 23.5
HALO_Z = 24
HOVER_Z = 24.5
RECOLOUR_Z = 25
MARK_Z = 26

#: Under the tracks, over the spectrogram: the "dim spectrogram" veil.
VEIL_Z = 18.5
VEIL_ALPHA = 0.6

#: Line widths, in pixels.  The underlay ("shadow") in the map's floor
#: colour is a pixel wider on each side, so a track reads as a line with a
#: dark edge even where it lies exactly on the bright ridge it marks.
TRACK_WIDTH = 2.0
SHADOW_EXTRA = 2.0
SHADOW_ALPHA = 0.9
HOVER_EXTRA = 2.0
HALO_EXTRA = 2.0
SELECTED_EXTRA = 1.5
SNIPPET_WIDTH = 2.0
DIM_ALPHA = 0.35
UNASSIGNED_ALPHA = 0.45
UNASSIGNED_PX = 3

#: Above this many vertices in a lane antialiasing is turned off (6.2).
ANTIALIAS_LIMIT = 50_000

#: A cut marker spans this many pixels either side of the track.
CUT_MARK_PX = 20.0

#: How long the post-commit flash lasts.
FLASH_MS = 150

#: Pool sizes for the preview recolour layer.
RECOLOUR_POOL = 8

ACHROMATIC_SELECTION = "#FF2BD6"
ACHROMATIC_SATURATION = 60

#: Tool colours (design 5.4).  Over a spectrogram these are always drawn on
#: an underlay in the map's floor colour, which is what keeps them legible.
TOOL_COLOURS = {
    "select": None,  # the selection colour, from the map
    "erase": "#FF5050",
    "cut": "#FFB224",
    "merge": "#5AA0FF",
    "assign": None,  # the selection colour
    "add": "#3FD68A",
}

DROP_COLOUR = "#FF3B3B"


# ------------------------------------------------------------------ colours


def colour_map(ax):
    """The colour map of a lane's spectrogram (audian's default without)."""
    cmap = None
    cbar = getattr(ax, "cbar", None)
    if cbar is not None and hasattr(cbar, "colorMap"):
        try:
            cmap = cbar.colorMap()
        except Exception:  # noqa: BLE001 - a missing map is not a crash
            cmap = None
    if cmap is None:
        cmap = theme.spectrogram_colormap(theme.DEFAULT_SPECTROGRAM_MAP)
    return cmap


def map_ends(ax) -> tuple:
    """The floor and peak colours of the colour map on this lane."""
    cmap = colour_map(ax)
    return cmap.map(0.0, mode="qcolor"), cmap.map(1.0, mode="qcolor")


def bright_hues(cmap, start: float = 0.55, n: int = 19) -> list:
    """The hues of the colours a ridge is painted in: the chromatic part of
    the upper half of the map.  A map whose bright end is white (audian's
    default runs blue, yellow, white) still paints every ridge's flank
    yellow, and a yellow track on it disappears."""
    out = []
    for v in np.linspace(start, 1.0, n):
        hue, sat, val, _a = cmap.map(float(v), mode="qcolor").getHsv()
        if hue >= 0 and sat >= ACHROMATIC_SATURATION and val >= 110:
            out.append(int(hue))
    return out


def is_dark(color: QColor) -> bool:
    return color.lightnessF() < 0.5


def opposed(color: QColor) -> str:
    """The hue opposed to `color` at full chroma, or magenta when it has none."""
    hue, saturation, _value, _alpha = color.getHsv()
    if hue < 0 or saturation < ACHROMATIC_SATURATION:
        return ACHROMATIC_SELECTION
    return QColor.fromHsv((hue + 180) % 360, 255, 255).name()


def _hue_distance(a: int, b: int) -> int:
    d = abs(a - b) % 360
    return min(d, 360 - d)


def slot_palette(floor: QColor, peak: QColor, avoid=()) -> list:
    """Ten slot colours for a lane whose map runs from `floor` to `peak`.

    Hues 36 degrees apart from 15; saturation 200; value 255 on a dark floor
    and 150 on a light one.  A hue within 25 degrees of the map's bright end
    or of any hue in `avoid` (`bright_hues`) is moved to the nearest hue
    clear of them, so no track is drawn in the colour of the ridge it sits
    on.
    """
    value = 255 if is_dark(floor) else 150
    peak_hue, peak_sat, _v, _a = peak.getHsv()
    taboo = [int(h) for h in avoid]
    if peak_hue >= 0 and peak_sat >= ACHROMATIC_SATURATION:
        taboo.append(int(peak_hue))

    def clear(h: int) -> bool:
        return all(_hue_distance(h, t) >= 25 for t in taboo)

    out, used = [], []
    for i in range(G.N_SLOTS):
        hue = (15 + 36 * i) % 360
        if not clear(hue):
            for d in range(5, 181, 5):
                options = [(hue + d) % 360, (hue - d) % 360]
                options = [h for h in options if clear(h)]
                later = [(15 + 36 * j) % 360 for j in range(i + 1, G.N_SLOTS)]
                spaced = [
                    h
                    for h in options
                    if all(_hue_distance(h, u) >= 15 for u in used + later)
                ]
                if spaced:
                    hue = spaced[0]
                    break
            # no clear hue left that is not another slot's: keep it, and let
            # the dark edge (`SHADOW_EXTRA`) carry it
        used.append(hue)
        out.append(QColor.fromHsv(hue, 200, value).name())
    return out


_PALETTES: dict = {}


def _palette_for(cmap, floor: QColor, peak: QColor) -> list:
    """`slot_palette` for a map, cached: it is asked on every redraw."""
    try:
        key = (np.asarray(cmap.pos).tobytes(), np.asarray(cmap.color).tobytes())
    except AttributeError:  # not a pyqtgraph ColorMap
        key = tuple(
            cmap.map(float(v), mode="qcolor").name() for v in np.linspace(0, 1, 12)
        )
    hit = _PALETTES.get(key)
    if hit is None:
        hit = slot_palette(floor, peak, bright_hues(cmap))
        _PALETTES[key] = hit
    return hit


class Colours:
    """Every colour one lane draws in, read from its map."""

    def __init__(self, ax) -> None:
        cmap = colour_map(ax)
        self.floor = cmap.map(0.0, mode="qcolor")
        self.peak = cmap.map(1.0, mode="qcolor")
        self.slots = _palette_for(cmap, self.floor, self.peak)
        self.selection = opposed(self.peak)
        self.contrast = self.floor.name()
        lo, hi = self.floor.lightness(), self.peak.lightness()
        grey = int(0.5 * (lo + hi))
        self.unassigned = QColor(grey, grey, grey).name()
        self.dark = is_dark(self.floor)

    def key(self) -> tuple:
        return (tuple(self.slots), self.selection, self.contrast)

    def slot(self, s: int) -> str:
        return self.slots[int(s) % G.N_SLOTS] if s >= 0 else self.unassigned

    def of_id(self, ident: float) -> str:
        return self.slot(int(G.slot_of([ident])[0]))

    def tool(self, name: str) -> str:
        colour = TOOL_COLOURS.get(name)
        return colour if colour else self.selection

    def resolve(self, colour) -> str:
        """A colour as stored in `Marks` (hex or ``("slot", n)``) as hex."""
        if isinstance(colour, tuple) and colour and colour[0] == "slot":
            return self.slot(int(colour[1]))
        if isinstance(colour, tuple) and colour and colour[0] == "id":
            return self.of_id(float(colour[1]))
        if isinstance(colour, tuple) and colour and colour[0] == "tool":
            return self.tool(str(colour[1]))
        return str(colour) if colour else self.selection


#: Paths built from the arrays the lanes share: every lane showing the same
#: view gets the same geometry arrays from the render cache, and building the
#: `QPainterPath` once instead of once per lane is most of a redraw's cost.
_PATHS: dict = {}
_PATHS_MAX = 64


def _shared_path(x: np.ndarray, y: np.ndarray):
    key = (id(x), id(y))
    hit = _PATHS.get(key)
    if hit is not None and hit[0] is x and hit[1] is y:
        return hit[2]
    path = pg.functions.arrayToQPath(x, y, connect="finite")
    _PATHS[key] = (x, y, path)
    while len(_PATHS) > _PATHS_MAX:
        _PATHS.pop(next(iter(_PATHS)))
    return path


def _offsets(width: float) -> list:
    """1 px passes that together draw a line `width` px wide.

    Vertical offsets, because tracks are nearly horizontal; from three
    pixels up, one pass either side horizontally as well, so a steep step
    is not thinner than the rest of the line.
    """
    n = max(1, int(round(width)))
    out = [(0.0, float(k)) for k in range(-((n - 1) // 2), n // 2 + 1)]
    if n >= 3:
        out += [(-1.0, 0.0), (1.0, 0.0)]
    return out


def _same(old, new) -> bool:
    """Whether an item already shows `new` (both empty counts)."""
    if old is None:
        return False
    if len(old) == 0 and len(new) == 0:
        return True
    return old is new or (
        old.shape == new.shape and np.array_equal(old, new, equal_nan=True)
    )


class LineItem(pg.GraphicsObject):
    """NaN-broken polylines, stroked as 1 px passes.

    Qt's raster engine strokes a 1 px cosmetic line an order of magnitude
    faster than a wider one -- measured on 642,000 vertices: 18 ms at 1 px
    antialiased, 226 ms at 1.6 px aliased, 2 s at 1.6 px antialiased.  So a
    wide line is drawn as the same `QPainterPath` stroked at 1 px several
    times, shifted by whole device pixels, and its underlay ("shadow") the
    same way in the floor colour first.  Visually it is the wide line; in
    cost it is a handful of thin ones.  The `setPen`/`setShadowPen`/`setData`
    signature is the subset of `pg.PlotCurveItem`'s this module uses.
    """

    def __init__(self) -> None:
        super().__init__()
        self._path = None
        self._rect = QRectF()
        self._pen = QPen(QColor("white"))
        self._width = 1.0
        self._shadow = None
        self._lite = False
        self.xData = None
        self.yData = None
        self.opts = {}

    def setPen(self, pen) -> None:  # noqa: N802
        pen = QPen(pen)
        if pen == self._pen:
            return  # an unchanged pen must not repaint the lane
        self._pen = pen
        self._width = float(pen.widthF()) or 1.0
        self.update()

    def setShadowPen(self, pen) -> None:  # noqa: N802
        shadow = None if pen is None else (QPen(pen), float(pen.widthF()))
        if shadow == self._shadow:
            return
        self._shadow = shadow
        self.update()

    def set_lite(self, lite: bool) -> None:
        """One pass, no underlay: for lanes with very many vertices."""
        self._lite = bool(lite)
        self.update()

    def setData(self, x=None, y=None, connect="finite", **_ignored) -> None:  # noqa: N802
        x = np.zeros(0) if x is None else np.asarray(x, dtype=np.float64)
        y = np.zeros(0) if y is None else np.asarray(y, dtype=np.float64)
        if _same(self.xData, x) and _same(self.yData, y):
            # the preview layer re-sets every item on each pointer move;
            # repainting eleven lanes for an unchanged line cost 50 ms
            return
        self.prepareGeometryChange()
        self.xData, self.yData = x, y
        good = np.isfinite(x) & np.isfinite(y)
        if len(x) >= 1 and good.any():
            self._path = _shared_path(x, y)
            x0, x1 = float(x[good].min()), float(x[good].max())
            y0, y1 = float(y[good].min()), float(y[good].max())
            self._rect = QRectF(x0, y0, max(x1 - x0, 1e-9), max(y1 - y0, 1e-9))
        else:
            self._path = None
            self._rect = QRectF()
        self.update()

    def boundingRect(self):  # noqa: N802
        return self._rect

    def dataBounds(self, ax, frac=1.0, orthoRange=None):  # noqa: N802, N803
        return (None, None)

    def _passes(
        self, painter, colour: QColor, width: float, style, full: bool = False
    ) -> None:
        m = painter.transform()
        sx = abs(m.m11()) or 1.0
        sy = abs(m.m22()) or 1.0
        pen = QPen(colour, 1.0)
        pen.setCosmetic(True)
        pen.setStyle(style)
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        offsets = [(0.0, 0.0)] if self._lite and not full else _offsets(width)
        for dx, dy in offsets:
            painter.save()
            painter.translate(dx / sx, dy / sy)
            painter.drawPath(self._path)
            painter.restore()

    def paint(self, painter, option, widget=None) -> None:
        if self._path is None:
            return
        painter.setRenderHint(painter.RenderHint.Antialiasing, not self._lite)
        if self._shadow is not None:
            # kept in lite mode too (aliased, three passes): at overview
            # zoom, where lanes have the most vertices, the edge is what
            # tells a track from the ridge under it
            pen, width = self._shadow
            self._passes(
                painter, pen.color(), min(width, 3.0) if self._lite else width,
                Qt.PenStyle.SolidLine, full=True,
            )  # fmt: skip
        self._passes(painter, self._pen.color(), self._width, self._pen.style())


class VeilItem(pg.GraphicsObject):
    """A translucent wash over the whole lane, under the tracks.

    "Dim spectrogram": the map's floor colour at `VEIL_ALPHA` lowers the
    spectrogram's contrast so the tracks on it stand out, on a dark map and
    a light one alike."""

    def __init__(self) -> None:
        super().__init__()
        self._colour = None
        self._rect = QRectF()

    def set_colour(self, colour) -> None:
        self._colour = None if colour is None else QColor(colour)
        self.viewRangeChanged()

    def viewRangeChanged(self) -> None:  # noqa: N802 - pyqtgraph's hook
        rect = self.viewRect()
        self.prepareGeometryChange()
        self._rect = QRectF(rect) if rect is not None else QRectF()
        self.update()

    def boundingRect(self):  # noqa: N802
        return self._rect if self._colour is not None else QRectF()

    def dataBounds(self, ax, frac=1.0, orthoRange=None):  # noqa: N802, N803
        return (None, None)

    def paint(self, painter, option, widget=None) -> None:
        if self._colour is not None and not self._rect.isEmpty():
            painter.fillRect(self._rect, self._colour)


class PointsItem(pg.GraphicsObject):
    """Round dots from numpy arrays, painted with one `drawPoints` call.

    `pg.ScatterPlotItem` keeps a record per spot and rebuilds them on every
    `setData`; at 100,000 points that was 160 ms of a 180 ms redraw.  Dots of
    one colour and size need none of that: a cosmetic pen of the dot's
    diameter with a round cap turns `QPainter.drawPoints` into a dot plotter,
    and the points go to Qt as one `QPolygonF` filled straight from numpy.
    The `setData` signature is the subset of `ScatterPlotItem`'s used here,
    so the two are interchangeable.  An optional outline (``pen``) is drawn
    as a larger dot underneath.
    """

    def __init__(self) -> None:
        super().__init__()
        self._poly = None
        self._n = 0
        self._rect = QRectF()
        self._size = 3.0
        self._fill = QColor("white")
        self._outline = None
        self.xData = None
        self.yData = None

    def setData(self, x=None, y=None, size=3, pen=None, brush=None, **_ignored):  # noqa: N802
        x = np.zeros(0) if x is None else np.asarray(x, dtype=np.float64)
        y = np.zeros(0) if y is None else np.asarray(y, dtype=np.float64)
        fill = None
        if brush is not None:
            fill = QColor(brush.color()) if hasattr(brush, "color") else QColor(brush)
        outline = None
        if (
            pen is not None
            and hasattr(pen, "color")
            and pen.style() != Qt.PenStyle.NoPen
        ):
            outline = (QColor(pen.color()), float(pen.widthF()))
        if (
            _same(self.xData, x)
            and _same(self.yData, y)
            and float(size) == self._size
            and (fill is None or fill == self._fill)
            and outline == self._outline
        ):
            return
        self.prepareGeometryChange()
        self.xData, self.yData = x, y
        self._n = len(x)
        self._size = float(size)
        if brush is not None:
            self._fill = (
                QColor(brush.color()) if hasattr(brush, "color") else QColor(brush)
            )
        self._outline = None
        if (
            pen is not None
            and hasattr(pen, "color")
            and pen.style() != Qt.PenStyle.NoPen
        ):
            self._outline = (QColor(pen.color()), float(pen.widthF()))
        if self._n:
            poly = pg.functions.create_qpolygonf(self._n)
            arr = pg.functions.ndarray_from_qpolygonf(poly)
            arr[:, 0] = x
            arr[:, 1] = y
            self._poly = poly
            x0, x1 = float(np.nanmin(x)), float(np.nanmax(x))
            y0, y1 = float(np.nanmin(y)), float(np.nanmax(y))
            self._rect = QRectF(x0, y0, max(x1 - x0, 1e-9), max(y1 - y0, 1e-9))
        else:
            self._poly = None
            self._rect = QRectF()
        self.update()

    def __len__(self) -> int:
        return self._n

    def boundingRect(self):  # noqa: N802
        return self._rect

    def dataBounds(self, ax, frac=1.0, orthoRange=None):  # noqa: N802, N803
        return (None, None)

    def paint(self, painter, option, widget=None) -> None:
        if self._poly is None or not self._n:
            return
        painter.setRenderHint(painter.RenderHint.Antialiasing, self._size >= 2.5)
        if self._outline is not None:
            colour, width = self._outline
            pen = QPen(colour, self._size + 2 * width)
            pen.setCosmetic(True)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawPoints(self._poly)
        pen = QPen(self._fill, self._size)
        pen.setCosmetic(True)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawPoints(self._poly)


def _cached(item):
    """Paint `item` from a pixmap until it changes.

    The brush ring and the hover box move over the base layer on every
    pointer move, and each move re-exposed the tracks under them: every
    base item of the lane was stroked again, 40 to 80 ms per move on a
    4 h, 11-lane recording.  A device-coordinate cache repaints them from
    a pixmap instead; it is rebuilt when the item changes or the view
    zooms."""
    item.setCacheMode(pg.QtWidgets.QGraphicsItem.CacheMode.DeviceCoordinateCache)
    return item


def _clear_scatter(item) -> None:
    """Empty a `ScatterPlotItem`, without a repaint when it already is."""
    if len(item.data):
        item.setData(*_empty())


def _passive(item) -> None:
    """Invisible to the mouse: picking is geometric, done by the tools."""
    item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
    item.setAcceptHoverEvents(False)


def _empty():
    return np.zeros(0), np.zeros(0)


def view_of(ax) -> G.View:
    """The `geometry.View` of a lane, from its view box."""
    vb = ax if isinstance(ax, pg.ViewBox) else ax.getViewBox()
    (x0, x1), (y0, y1) = vb.viewRange()
    rect = vb.boundingRect()
    return G.View(
        float(x0),
        float(x1),
        float(y0),
        float(y1),
        max(1.0, float(rect.width())),
        max(1.0, float(rect.height())),
    )


# ------------------------------------------------------------------ overlay


class TrackOverlay:
    """Every track of the session, on one spectrogram lane."""

    def __init__(self, ax, scene: G.SceneState, cache: G.RenderCache) -> None:
        self.ax = ax
        self.scene = scene
        self.cache = cache
        self.colours = Colours(ax)
        self._base_key = None
        self._preview_key = None
        self._items: list = []
        #: timing of the last update, for the benchmark and the tests
        self.last_ms = 0.0
        self.last_base_ms = 0.0
        self.geometry: G.Geometry | None = None

        self.veil = self._add(VeilItem(), VEIL_Z)
        self.curves = [_cached(self._curve(TRACK_Z)) for _ in range(G.N_SLOTS)]
        self.dim_curves: list = []
        self.points = [_cached(self._dots(POINT_Z)) for _ in range(G.N_SLOTS)]
        self.unassigned = _cached(self._dots(UNASSIGNED_Z))
        self.selected = self._curve(SELECTED_Z)
        self.selected_points = self._dots(SELECTED_Z)
        self.halo = self._curve(HALO_Z)
        self.hover = self._curve(HOVER_Z)
        self.outline_halo = self._curve(HALO_Z - 0.2)
        self.outline = self._curve(HALO_Z - 0.1)
        # above the hover, so a hovered track shows what the click will make
        # of it (the cut part in the new id's colour, a merged piece in the
        # anchor's)
        self.recolour = [self._curve(RECOLOUR_Z) for _ in range(RECOLOUR_POOL)]
        self.recolour_points = [self._dots(RECOLOUR_Z) for _ in range(RECOLOUR_POOL)]
        self.stroke_points = self._dots(PREVIEW_Z + 0.1)
        self.rings = self._scatter(MARK_Z)
        self.drops = self._scatter(MARK_Z)
        self.crosses = self._scatter(MARK_Z)
        self.connector = self._curve(MARK_Z - 0.5)
        self.cut_marker = self._curve(MARK_Z)
        self.add_dots = self._scatter(MARK_Z)
        self.span = self._curve(MARK_Z - 1)
        self.flash_curve = self._curve(HOVER_Z + 0.2)
        self.flash_points = self._dots(HOVER_Z + 0.2)
        self.snippet_curves: list = []
        self.texts: list = []

        self._timer = QTimer()
        self._timer.setSingleShot(True)
        self._timer.setInterval(0)
        self._timer.timeout.connect(self.update_plot)
        self._flash_timer = QTimer()
        self._flash_timer.setInterval(16)
        self._flash_timer.timeout.connect(self._flash_step)
        self._flash_t0 = 0.0

        vb = ax.getViewBox()
        if vb is not None:
            vb.sigRangeChanged.connect(self._view_changed)
            vb.sigResized.connect(self._view_changed)

    # --- plumbing ---------------------------------------------------------

    def _add(self, item, z):
        item.setZValue(z)
        _passive(item)
        self.ax.addItem(item, ignoreBounds=True)
        self._items.append(item)
        return item

    def _curve(self, z):
        return self._add(LineItem(), z)

    def _dots(self, z):
        return self._add(PointsItem(), z)

    def _scatter(self, z):
        item = pg.ScatterPlotItem(pxMode=True, hoverable=False)
        return self._add(item, z)

    def _text(self, i: int):
        while len(self.texts) <= i:
            item = pg.TextItem(anchor=(0.0, 1.0))
            item.setFont(theme.font_mono(theme.SIZE_SMALL_PT))
            item.setVisible(False)
            self._add(item, LABEL_Z)
            self.texts.append(item)
        return self.texts[i]

    def _dim_curve(self, s: int):
        while len(self.dim_curves) <= s:
            self.dim_curves.append(_cached(self._curve(DIM_Z)))
        return self.dim_curves[s]

    def _snippet_curve(self, s: int):
        while len(self.snippet_curves) <= s:
            self.snippet_curves.append(_cached(self._curve(SNIPPET_Z)))
        return self.snippet_curves[s]

    def _view_changed(self, *args) -> None:
        self.schedule()

    def schedule(self) -> None:
        """Redraw once, at the end of this event-loop turn."""
        if not self._timer.isActive():
            self._timer.start()

    def invalidate(self, change=None) -> None:
        """Forget what was drawn so the next `update_plot` redraws.

        `change` is noted by the panel in the shared cache already; only the
        keys here are dropped.
        """
        self._base_key = None
        self._preview_key = None

    def item_count(self) -> int:
        return len(self._items)

    def detach(self) -> None:
        """Take every item off the lane and disconnect."""
        self._timer.stop()
        self._flash_timer.stop()
        vb = self.ax.getViewBox()
        if vb is not None:
            for signal in (vb.sigRangeChanged, vb.sigResized):
                try:
                    signal.disconnect(self._view_changed)
                except (RuntimeError, TypeError):
                    pass
        for item in self._items:
            try:
                self.ax.removeItem(item)
            except (RuntimeError, ValueError):
                pass
        self._items.clear()
        self.texts.clear()
        self.dim_curves.clear()
        self.snippet_curves.clear()

    # --- drawing ----------------------------------------------------------

    def view(self) -> G.View:
        return view_of(self.ax)

    def update_plot(self) -> None:
        """Redraw what changed, or return having found nothing to redraw."""
        start = time.perf_counter()
        self.colours = Colours(self.ax)
        view = self.view()
        scene = self.scene
        ts = scene.ts
        snippet = scene.snippet
        base_key = (
            id(ts),
            getattr(ts, "revision", -1),
            view.key(),
            scene.display_key(),
            self.colours.key(),
            id(snippet),
            bool(scene.dim_spec),
        )
        if base_key != self._base_key:
            self._base_key = base_key
            t0 = time.perf_counter()
            self._draw_base(ts, view)
            self._draw_snippet(snippet, view)
            self.last_base_ms = 1000 * (time.perf_counter() - t0)
            self._preview_key = None
        preview_key = (base_key, scene.revision)
        if preview_key != self._preview_key:
            self._preview_key = preview_key
            self._draw_preview(ts, view)
        self.last_ms = 1000 * (time.perf_counter() - start)

    def _draw_base(self, ts, view: G.View) -> None:
        c = self.colours
        if self.scene.dim_spec and (ts is not None or self.scene.snippet is not None):
            self.veil.set_colour(theme.qcolor(c.contrast, alpha=VEIL_ALPHA))
        else:
            self.veil.set_colour(None)
        if ts is None:
            self.geometry = None
            for item in (*self.curves, *self.points, self.unassigned, *self.dim_curves):
                item.setData(*_empty())
            for item in self.texts:
                item.setVisible(False)
            return
        g = self.cache.get(ts, self.scene, view)
        self.geometry = g
        antialias = g.n_vertices < ANTIALIAS_LIMIT
        snippet = self.scene.snippet
        span = None
        if snippet is not None and len(ts.times):
            k0 = max(0, min(len(ts.times) - 1, int(snippet.k0)))
            k1 = max(0, min(len(ts.times) - 1, int(snippet.k1) - 1))
            span = (float(ts.times[k0]), float(ts.times[k1]))
        for s, item in enumerate(self.curves):
            x, y = g.slots.get(s, _empty())
            pen = theme.pen(c.slot(s), width=TRACK_WIDTH)
            item.setPen(pen)
            # a thin underlay in the floor colour keeps a line legible on the
            # ridge it marks; on a lane with very many vertices both it and
            # the second pass go (see `LineItem`)
            item.setShadowPen(
                theme.pen(
                    c.contrast, width=TRACK_WIDTH + SHADOW_EXTRA, alpha=SHADOW_ALPHA
                )
            )
            item.set_lite(not antialias)
            if span is not None and len(x):
                inside = (x >= span[0]) & (x <= span[1])
                y_out = np.where(inside, np.nan, y)
                y_in = np.where(inside, y, np.nan)
                item.setData(x=x, y=y_out, connect="finite")
                dim = self._dim_curve(s)
                dim.setPen(theme.pen(c.slot(s), width=TRACK_WIDTH, alpha=DIM_ALPHA))
                dim.setData(x=x, y=y_in, connect="finite")
            else:
                item.setData(x=x, y=y, connect="finite")
                if s < len(self.dim_curves):
                    self.dim_curves[s].setData(*_empty())
        size = max(0, int(self.scene.point_px))
        for s, item in enumerate(self.points):
            x, y = g.points.get(s, _empty())
            if size == 0 or len(x) == 0:
                item.setData(*_empty())
                continue
            item.setData(
                x=x,
                y=y,
                size=size,
                pen=theme.pen(c.contrast, width=1.0, alpha=SHADOW_ALPHA),
                brush=theme.brush(c.slot(s)),
                symbol="o",
            )
        ux, uy = g.unassigned
        if len(ux):
            self.unassigned.setData(
                x=ux,
                y=uy,
                size=UNASSIGNED_PX,
                pen=None,
                brush=theme.brush(c.unassigned, alpha=UNASSIGNED_ALPHA),
                symbol="o",
            )
        else:
            self.unassigned.setData(*_empty())
        self._draw_labels(ts, g)

    def _draw_labels(self, ts, g: G.Geometry) -> None:
        c = self.colours
        labels = getattr(ts, "labels", {}) or {}
        marks = self.scene.harmonic_marks
        shown = 0
        for ident, t, f in g.labels:
            item = self._text(shown)
            name = labels.get(int(ident)) if isinstance(labels, dict) else None
            mark = marks.get(float(ident))
            item.setText(
                f"{int(ident)}"
                + (f" {name}" if name else "")
                + (f" {mark}" if mark else "")
            )
            item.setColor(theme.qcolor(c.of_id(ident)))
            item.fill = theme.brush(c.contrast, alpha=0.55)
            item.setPos(t, f)
            item.setVisible(True)
            shown += 1
        for item in self.texts[shown:]:
            item.setVisible(False)

    def _draw_snippet(self, snippet, view: G.View) -> None:
        c = self.colours
        if snippet is None or self.scene.ts is None or len(snippet.fund) == 0:
            for item in self.snippet_curves:
                item.setData(*_empty())
            return
        ts = self.scene.ts
        times = ts.times
        ident = np.asarray(snippet.ident, dtype=np.float64)
        good = np.isfinite(ident) & (snippet.idx >= 0) & (snippet.idx < len(times))
        order = np.lexsort((snippet.idx[good], ident[good]))
        ids = ident[good][order]
        k = np.asarray(snippet.idx)[good][order]
        f = np.asarray(snippet.fund)[good][order]
        x = times[k]
        brk = np.zeros(max(0, len(ids) - 1), dtype=bool)
        if len(ids) > 1:
            gap = G.gap_frames_for(times, self.scene.gap_break_s)
            brk = (ids[1:] != ids[:-1]) | (np.diff(k) > gap)
        # the snippet's own colours: its local ids shifted half a palette, so
        # a provisional track does not borrow the colour of the session id it
        # happens to share a number with
        slots = (G.slot_of(ids) + 5) % G.N_SLOTS
        for s in range(G.N_SLOTS):
            m = slots == s
            item = self._snippet_curve(s)
            if not m.any():
                item.setData(*_empty())
                continue
            sel = np.flatnonzero(m)
            sub_brk = np.ones(max(0, len(sel) - 1), dtype=bool)
            if len(sel) > 1:
                adjacent = np.diff(sel) == 1
                sub_brk[adjacent] = brk[sel[:-1][adjacent]]
            xs, ys = G._insert_breaks(x[sel], f[sel], sub_brk)
            pen = theme.pen(c.slot(s), width=SNIPPET_WIDTH)
            pen.setStyle(Qt.PenStyle.CustomDashLine)
            pen.setDashPattern([4.0, 3.0])
            item.setPen(pen)
            item.setShadowPen(
                theme.pen(
                    c.contrast, width=SNIPPET_WIDTH + SHADOW_EXTRA, alpha=SHADOW_ALPHA
                )
            )
            item.setData(x=xs, y=ys, connect="finite")

    # --- the preview layer --------------------------------------------------

    def _track_xy(self, rows):
        scene = self.scene
        gap = G.gap_frames_for(scene.ts.times, scene.gap_break_s)
        return G.track_line(scene.ts, rows, gap)

    def _points_xy(self, rows):
        ts = self.scene.ts
        rows = np.asarray(rows, dtype=np.int64)
        return ts.times[ts.idx[rows]], ts.fund[rows]

    def _draw_preview(self, ts, view: G.View) -> None:
        scene = self.scene
        c = self.colours
        marks = scene.marks
        if ts is None or ts.n == 0:
            for item in (
                self.selected,
                self.selected_points,
                self.halo,
                self.hover,
                self.outline,
                self.outline_halo,
                *self.recolour,
                *self.recolour_points,
                self.stroke_points,
                self.rings,
                self.drops,
                self.crosses,
                self.connector,
                self.cut_marker,
                self.add_dots,
                self.span,
            ):
                item.setData(*_empty())
            return
        size = max(0, int(scene.point_px))

        # selection: undecimated, on top, in the selection colour
        sel = G.rows_in_view(ts, scene.selection, view)
        if len(sel):
            self.selected.setPen(
                theme.pen(c.selection, width=TRACK_WIDTH + SELECTED_EXTRA)
            )
            self.selected.setData(*self._track_xy(sel), connect="finite")
            x, y = self._points_xy(sel)
            self.selected_points.setData(
                x=x, y=y, size=size + 2, pen=None, brush=theme.brush(c.selection)
            )
        else:
            self.selected.setData(*_empty())
            self.selected_points.setData(*_empty())

        # hover: 2 px wider, with a halo in the floor colour
        hover = scene.hover
        if hover is not None and np.isfinite(hover.id):
            rows = G.track_in_view(ts, hover.id, view)
            x, y = self._track_xy(rows)
            width = TRACK_WIDTH + HOVER_EXTRA
            colour = c.of_id(hover.id)
            self.halo.setPen(theme.pen(c.contrast, width=width + 2 * HALO_EXTRA))
            self.halo.setData(x, y, connect="finite")
            self.hover.setPen(theme.pen(colour, width=width))
            self.hover.setData(x, y, connect="finite")
        else:
            self.halo.setData(*_empty())
            self.hover.setData(*_empty())

        # the merge anchor / assign target: outlined
        if marks.outline_id is not None and np.isfinite(marks.outline_id):
            rows = G.track_in_view(ts, marks.outline_id, view)
            x, y = self._track_xy(rows)
            self.outline_halo.setPen(
                theme.pen(c.contrast, width=TRACK_WIDTH + 2 * HOVER_EXTRA + 4)
            )
            self.outline_halo.setData(x, y, connect="finite")
            self.outline.setPen(
                theme.pen(
                    c.of_id(marks.outline_id), width=TRACK_WIDTH + 2 * HOVER_EXTRA
                )
            )
            self.outline.setData(x, y, connect="finite")
        else:
            self.outline.setData(*_empty())
            self.outline_halo.setData(*_empty())

        # recoloured rows (merge, cut, assign previews)
        for i in range(RECOLOUR_POOL):
            curve, pts = self.recolour[i], self.recolour_points[i]
            if i >= len(marks.recolour):
                curve.setData(*_empty())
                pts.setData(*_empty())
                continue
            rows, colour = marks.recolour[i]
            rows = G.rows_in_view(ts, rows, view)
            colour = c.resolve(colour)
            curve.setPen(theme.pen(colour, width=TRACK_WIDTH + HOVER_EXTRA))
            curve.setData(*self._track_xy(rows), connect="finite")
            x, y = self._points_xy(rows)
            pts.setData(x=x, y=y, size=size + 2, pen=None, brush=theme.brush(colour))

        # rows a stroke has captured
        stroke = G.rows_in_view(ts, scene.stroke_rows, view)
        if len(stroke) and scene.stroke_mode in ("select", "assign", "merge"):
            colour = c.resolve(scene.tool_colour) if scene.tool_colour else c.selection
            x, y = self._points_xy(stroke)
            self.stroke_points.setData(
                x=x,
                y=y,
                size=size + 3,
                pen=theme.pen(c.contrast, width=1.0),
                brush=theme.brush(colour),
            )
        else:
            self.stroke_points.setData(*_empty())
        rings = G.rows_in_view(ts, marks.ring_rows, view)
        if len(rings):
            x, y = self._points_xy(rings)
            self.rings.setData(
                x=x,
                y=y,
                size=size + 4,
                pen=theme.pen(c.tool("erase"), width=1.2),
                brush=theme.brush(c.contrast, alpha=0.75),
            )
        else:
            _clear_scatter(self.rings)

        drops = G.rows_in_view(ts, marks.drop_rows, view)
        if len(drops):
            x, y = self._points_xy(drops)
            self.drops.setData(
                x=x,
                y=y,
                size=size + 9,
                symbol="x",
                pen=theme.pen(c.contrast, width=0.8),
                brush=theme.brush(DROP_COLOUR),
            )
        else:
            _clear_scatter(self.drops)

        if marks.crosses:
            xs = [p[0] for p in marks.crosses]
            ys = [p[1] for p in marks.crosses]
            self.crosses.setData(
                x=xs,
                y=ys,
                size=13,
                symbol="x",
                pen=theme.pen(c.contrast, width=1.0),
                brush=theme.brush(c.tool("cut")),
            )
        else:
            _clear_scatter(self.crosses)

        if marks.connector is not None:
            t0, f0, t1, f1 = marks.connector
            pen = theme.pen(c.tool("merge"), width=2.0)
            pen.setStyle(Qt.PenStyle.CustomDashLine)
            pen.setDashPattern([3.0, 3.0])
            self.connector.setPen(pen)
            self.connector.setData([t0, t1], [f0, f1])
        else:
            self.connector.setData(*_empty())

        if marks.cut_marker is not None:
            t, f = marks.cut_marker
            df = view.df(CUT_MARK_PX)
            self.cut_marker.setPen(theme.pen(c.tool("cut"), width=2.0))
            self.cut_marker.setShadowPen(theme.pen(c.contrast, width=4.0))
            self.cut_marker.setData([t, t], [f - df, f + df])
        else:
            self.cut_marker.setData(*_empty())

        if len(marks.add_t):
            colour = c.resolve(marks.add_colour) if marks.add_colour else c.tool("add")
            skip = np.asarray(marks.add_skip, dtype=bool)
            brushes = [
                theme.brush("#9AA0A8") if s else theme.brush(c.contrast, alpha=0.5)
                for s in skip
            ]
            pen = theme.pen(colour, width=2.0 if marks.add_pending else 1.5)
            if marks.add_pending:
                pen.setStyle(Qt.PenStyle.DotLine)
            self.add_dots.setData(
                x=marks.add_t,
                y=marks.add_f,
                size=size + 5,
                pen=pen,
                brush=brushes,
                symbol="o",
            )
        else:
            _clear_scatter(self.add_dots)

        span = marks.span if marks.span is not None else scene.span_outline
        if span is not None:
            t0, t1, f0, f1 = span
            pen = theme.pen(c.selection, width=1.5)
            pen.setStyle(Qt.PenStyle.DashLine)
            self.span.setPen(pen)
            self.span.setData([t0, t1, t1, t0, t0], [f0, f0, f1, f1, f0])
        else:
            self.span.setData(*_empty())

    # --- flash ------------------------------------------------------------

    def flash(self, rows) -> None:
        """Brighten `rows` and fade them out over `FLASH_MS`."""
        ts = self.scene.ts
        if ts is None:
            return
        rows = G.rows_in_view(ts, np.asarray(rows, dtype=np.int64), self.view())
        if len(rows) == 0:
            self.flash_curve.setData(*_empty())
            self.flash_points.setData(*_empty())
            return
        bright = "#FFFFFF" if self.colours.dark else "#000000"
        self.flash_curve.setPen(theme.pen(bright, width=TRACK_WIDTH + 2.5))
        self.flash_curve.setData(*self._track_xy(rows), connect="finite")
        x, y = self._points_xy(rows)
        size = max(3, int(self.scene.point_px) + 3)
        self.flash_points.setData(
            x=x, y=y, size=size, pen=None, brush=theme.brush(bright)
        )
        self.flash_curve.setOpacity(0.9)
        self.flash_points.setOpacity(0.9)
        self._flash_t0 = time.perf_counter()
        self._flash_timer.start()

    def _flash_step(self) -> None:
        elapsed = 1000 * (time.perf_counter() - self._flash_t0)
        if elapsed >= FLASH_MS:
            self._flash_timer.stop()
            self.flash_curve.setData(*_empty())
            self.flash_points.setData(*_empty())
            return
        alpha = 0.9 * (1.0 - elapsed / FLASH_MS)
        self.flash_curve.setOpacity(alpha)
        self.flash_points.setOpacity(alpha)


__all__ = ["Colours", "TrackOverlay", "map_ends", "slot_palette", "view_of"]
