"""Labelling on the spectrogram with the mouse: surface, prompt, outlines, keys.

`LaneSurface`
    One per lane: an invisible child of the lane's view box that takes the *left*
    button while the tool is on.  Right drags still reach the view box, and the
    wheel still zooms, so the view can be moved without leaving the tool.

`PromptOverlay`
    What SAM is being asked and what it said, in the lane's own data coordinates:
    the clicks, and the outline it answered with.  Outlines only and never fills:
    `SpecItem`'s colormap spans the whole luminance range, and `labeloverlay` has
    the reasoning for why nothing over a spectrogram may be filled.

`OutlineOverlay`
    The stored outlines of labels and candidates in view, one path per lane.

`KeyRouter`
    An application event filter claiming this tool's few keys, only while the tool
    is on and this browser's window is the active one, with no menu or dialog open,
    and never in a text field.  A window-wide shortcut would take Return away from
    every dialog and spin box in the application.

The gestures are in `panel.Panel`, which owns the state they change.
"""

from __future__ import annotations

from typing import Callable, Optional

import pyqtgraph as pg
from PySide6.QtCore import QEvent, QObject, QRectF, Qt
from PySide6.QtGui import QPainterPath
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QGraphicsPathItem,
    QGraphicsRectItem,
    QLineEdit,
    QPlainTextEdit,
    QTextEdit,
)

from audian.pluginapi import theme

#: Above the spectrogram, the labels and the view box's own rubber band.
SURFACE_Z = 1000
OVERLAY_Z = 900

#: The keys, with what they mean, for the tooltip and the manual.
KEY_HELP = (
    ("Drag", "box the event"),
    ("Click", "add this part to the outline"),
    ("Ctrl+click", "not this part"),
    ("Click a candidate", "reject it"),
    ("Enter", "accept the outline, or the rest of the view and go to the next"),
    ("Shift+Enter", "reject the rest of the view and go to the next"),
    ("Backspace", "undo the last click or rejection"),
    ("Esc", "drop the outline"),
)


def lane_channels(ax):
    """Which channels a lane shows: an int, or a list when it is the mean panel."""
    mean = getattr(ax, "mean_channels", None)
    if mean:
        return [int(c) for c in mean]
    overlay = getattr(ax, "overlay_channels", None)
    if overlay:
        return [int(c) for c in overlay]
    return int(getattr(ax, "channel", 0))


class LaneSurface(pg.GraphicsObject):
    """The invisible item on one lane that takes the tool's mouse."""

    def __init__(self, ax, host) -> None:
        super().__init__()
        self.ax = ax
        self.vb = ax.getViewBox()
        self.host = host
        self.armed = False
        self._rect = QRectF(self.vb.boundingRect())
        self.setParentItem(self.vb)
        self.setZValue(SURFACE_Z)
        self.vb.sigResized.connect(self._resized)
        self.band = QGraphicsRectItem(self)
        self.band.setVisible(False)
        self.band.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.arm(False)

    def boundingRect(self):  # noqa: N802 - Qt's spelling
        return self._rect

    def paint(self, painter, option, widget=None) -> None:
        pass

    def _resized(self, *_args) -> None:
        self.prepareGeometryChange()
        self._rect = QRectF(self.vb.boundingRect())

    def detach(self) -> None:
        try:
            self.vb.sigResized.disconnect(self._resized)
        except (RuntimeError, TypeError):
            pass
        scene = self.scene()
        if scene is not None:
            scene.removeItem(self)
        self.setParentItem(None)

    def arm(self, on: bool) -> None:
        self.armed = bool(on)
        self.setVisible(self.armed)
        self.setAcceptHoverEvents(self.armed)
        self.setAcceptedMouseButtons(
            Qt.MouseButton.LeftButton if self.armed else Qt.MouseButton.NoButton
        )
        self.setCursor(Qt.CursorShape.CrossCursor)
        if not self.armed:
            self.band.setVisible(False)

    def to_data(self, scene_pos) -> tuple:
        point = self.vb.mapSceneToView(scene_pos)
        return float(point.x()), float(point.y())

    def hoverEvent(self, ev):  # noqa: N802 - pyqtgraph's spelling
        if not self.armed or ev.isExit():
            return
        ev.acceptDrags(Qt.MouseButton.LeftButton)
        ev.acceptClicks(Qt.MouseButton.LeftButton)

    def mouseClickEvent(self, ev):  # noqa: N802
        if not self.armed or ev.button() != Qt.MouseButton.LeftButton:
            ev.ignore()
            return
        ev.accept()
        t, f = self.to_data(ev.scenePos())
        negative = bool(ev.modifiers() & Qt.KeyboardModifier.ControlModifier)
        self.host.lane_clicked(self.ax, t, f, negative)

    def mouseDragEvent(self, ev):  # noqa: N802
        if not self.armed or ev.button() != Qt.MouseButton.LeftButton:
            ev.ignore()
            return
        ev.accept()
        start = ev.buttonDownPos()
        now = ev.pos()
        if ev.isFinish():
            self.band.setVisible(False)
            t0, f0 = self.to_data(ev.buttonDownScenePos())
            t1, f1 = self.to_data(ev.scenePos())
            self.host.lane_boxed(self.ax, t0, t1, f0, f1)
            return
        self.band.setPen(theme.pen(theme.marker_color(0), theme.LW_SELECTED))
        self.band.setRect(QRectF(start, now).normalized())
        self.band.setVisible(True)


def _path_of(polygons) -> QPainterPath:
    path = QPainterPath()
    for polygon in polygons:
        if len(polygon) < 2:
            continue
        path.moveTo(float(polygon[0][0]), float(polygon[0][1]))
        for t, f in polygon[1:]:
            path.lineTo(float(t), float(f))
        path.closeSubpath()
    return path


def _passive(item) -> None:
    """A mark states where something is; it claims no mouse."""
    item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
    item.setAcceptHoverEvents(False)


class PromptOverlay:
    """The clicks and the answer, on one lane."""

    def __init__(self, ax) -> None:
        self.ax = ax
        self.outline = QGraphicsPathItem()
        self.outline.setZValue(OVERLAY_Z)
        _passive(self.outline)
        self.points = pg.ScatterPlotItem(pxMode=True, size=9)
        self.points.setZValue(OVERLAY_Z + 1)
        _passive(self.points)
        ax.addItem(self.outline, ignoreBounds=True)
        ax.addItem(self.points, ignoreBounds=True)
        self.clear()

    def show(self, polygon, points, color: int) -> None:
        pen = theme.pen(theme.marker_color(color), theme.LW_SELECTED)
        pen.setCosmetic(True)
        self.outline.setPen(pen)
        self.outline.setPath(_path_of([polygon] if polygon else []))
        self.outline.setVisible(bool(polygon))
        spots = [
            dict(
                pos=(t, f),
                symbol="+" if positive else "x",
                pen=pg.mkPen(theme.marker_color(color), width=2),
                brush=None,
            )
            for t, f, positive in points
        ]
        self.points.setData(spots)
        self.points.setVisible(bool(points))

    def clear(self) -> None:
        self.outline.setVisible(False)
        self.outline.setPath(QPainterPath())
        self.points.setData([])
        self.points.setVisible(False)

    def detach(self) -> None:
        for item in (self.outline, self.points):
            try:
                self.ax.removeItem(item)
            except (RuntimeError, ValueError):
                pass


class OutlineOverlay:
    """Stored outlines on one lane, drawn as one path and only what is in view.

    One path rather than an item per outline, and re-made only when the view or the
    outlines change: an item per outline is what a recording with ten thousand
    detections cannot afford to pan.
    """

    def __init__(self, ax) -> None:
        self.ax = ax
        self.vb = ax.getViewBox()
        self.item = QGraphicsPathItem()
        self.item.setZValue(OVERLAY_Z - 1)
        _passive(self.item)
        ax.addItem(self.item, ignoreBounds=True)
        self.polygons: list = []
        self.spans: list = []
        self.visible = True
        self.vb.sigRangeChanged.connect(self.redraw)

    def set_outlines(self, polygons) -> None:
        self.polygons = [p for p in polygons if len(p) >= 3]
        self.spans = [
            (min(t for t, _ in p), max(t for t, _ in p)) for p in self.polygons
        ]
        self.redraw()

    def set_visible(self, on: bool) -> None:
        self.visible = bool(on)
        self.redraw()

    def redraw(self, *_args) -> None:
        if not self.visible or not self.polygons:
            self.item.setVisible(False)
            return
        (t0, t1), _ = self.vb.viewRange()
        shown = [
            p for p, (a, b) in zip(self.polygons, self.spans) if a <= t1 and b >= t0
        ]
        pen = theme.pen(theme.marker_color(1), theme.LW_THIN)
        pen.setCosmetic(True)
        self.item.setPen(pen)
        self.item.setPath(_path_of(shown))
        self.item.setVisible(bool(shown))

    def detach(self) -> None:
        try:
            self.vb.sigRangeChanged.disconnect(self.redraw)
        except (RuntimeError, TypeError):
            pass
        try:
            self.ax.removeItem(self.item)
        except (RuntimeError, ValueError):
            pass


#: ``(key, shift)`` -> command the panel runs.
KEY_TABLE = {
    (Qt.Key.Key_Return, False): "enter",
    (Qt.Key.Key_Enter, False): "enter",
    (Qt.Key.Key_Return, True): "shift_enter",
    (Qt.Key.Key_Enter, True): "shift_enter",
    (Qt.Key.Key_Backspace, False): "backspace",
    (Qt.Key.Key_Escape, False): "escape",
}


class KeyRouter(QObject):
    """Claims the tool's keys while it is on; passes everything else along."""

    def __init__(self, live: Callable[[], bool], run: Callable[[str], None]):
        super().__init__()
        self.live = live
        self.run = run
        self._installed = False

    def install(self) -> None:
        app = QApplication.instance()
        if app is not None and not self._installed:
            app.installEventFilter(self)
            self._installed = True

    def uninstall(self) -> None:
        app = QApplication.instance()
        if app is not None and self._installed:
            app.removeEventFilter(self)
        self._installed = False

    def command_for(self, event) -> Optional[str]:
        if not self.live():
            return None
        # Enter belongs to a dialog's button, a menu's item and a combo box's list.
        # Taken here, it fired nothing there and finished a view behind them.
        if QApplication.activePopupWidget() or QApplication.activeModalWidget():
            return None
        modifiers = event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier
        shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
        if modifiers & ~Qt.KeyboardModifier.ShiftModifier:
            return None
        focus = QApplication.focusWidget()
        if isinstance(
            focus, (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox, QComboBox)
        ):
            return None
        return KEY_TABLE.get((Qt.Key(event.key()), shift))

    def eventFilter(self, obj, event):  # noqa: N802 - Qt's spelling
        kind = event.type()
        if kind == QEvent.Type.ShortcutOverride:
            if self.command_for(event) is not None:
                event.accept()
                return True
            return False
        if kind == QEvent.Type.KeyPress:
            name = self.command_for(event)
            if name is None:
                return False
            if not event.isAutoRepeat():
                self.run(name)
            return True
        return False
