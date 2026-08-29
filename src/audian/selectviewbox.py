import numpy as np
import pyqtgraph as pg

from PySide6.QtCore import Qt, QRectF, Signal
from PySide6.QtGui import QTransform

from . import theme


class SelectViewBox(pg.ViewBox):
    # channel, view box, selected rectangle (data coordinates):
    sigSelectedRegion = Signal(object, object, object)

    # same, plus the scene position of the event that finished the selection.
    # Wayland forbids global cursor queries, so a context menu raised for a
    # selection has to be placed from this position rather than QCursor.pos().
    sigSelectedRegionAt = Signal(object, object, object, object)

    # x and y of the mouse in view coordinates:
    sigHoverValue = Signal(object, object)

    # the user zoomed this view box by hand: (x zoomed, y zoomed).
    # An automatic fit must not fight a deliberate zoom.
    sigUserZoomed = Signal(bool, bool)

    def __init__(self, channel, *args, **kwargs):
        pg.ViewBox.__init__(self, *args, **kwargs)
        self.setMouseMode(pg.ViewBox.RectMode)
        self.rbScaleBox.setPen(theme.selection_pen())
        self.rbScaleBox.setBrush(theme.selection_brush())
        self.channel = channel
        self.drag_modifiers = Qt.NoModifier
        self.setAcceptHoverEvents(True)

    def publish_region_mode(self) -> None:
        """Let a modified drag override the current region mode, once.

        Shift+drag plays the selection and Alt+drag analyses it, whatever
        the tool bar says, so the two actions stay reachable now that the
        default mode is 'zoom' rather than 'ask'.  The override is written
        onto the browser and consumed by its next `region_menu`, which
        keeps both region signals free of an extra argument.
        """
        browser = getattr(self, "browser", None)
        if browser is None:
            return
        gui = getattr(browser, "gui", None)
        if gui is None or not hasattr(gui, "region_mode_for_modifiers"):
            return
        mode = gui.region_mode_for_modifiers(self.drag_modifiers)
        if mode is not None:
            browser.region_mode_override = mode

    def apply_theme(self) -> None:
        """Re-apply the theme to the rubber-band selection box."""
        self.rbScaleBox.setPen(theme.selection_pen())
        self.rbScaleBox.setBrush(theme.selection_brush())

    def getMenu(self, *args, **kwargs):
        # context menus are disabled throughout audian, see
        # theme.strip_pg_menus():
        return None

    def getContextMenus(self, *args, **kwargs):
        return None

    def keyPressEvent(self, ev):
        ev.ignore()

    def hoverEvent(self, ev):
        if ev.isExit():
            return
        try:
            pos = self.mapToView(ev.pos())
        except Exception:
            return
        if pos is not None:
            self.sigHoverValue.emit(pos.x(), pos.y())

    def wheelEvent(self, ev, axis=None):
        """Plain wheel scrolls, Ctrl+wheel zooms time, Shift+wheel zooms y.

        The channel stack lives in a scroll area, so the plain wheel must not
        be swallowed by the view box.  Zooming needs an explicit modifier.
        """
        mods = ev.modifiers()
        if mods & Qt.ControlModifier:
            zoom_axis = self.XAxis
        elif mods & Qt.ShiftModifier:
            zoom_axis = self.YAxis
        else:
            # let the event propagate to the enclosing scroll area:
            ev.ignore()
            return
        mask = [False, False]
        mask[zoom_axis] = self.state["mouseEnabled"][zoom_axis]
        if not any(mask):
            ev.ignore()
            return
        s = 1.02 ** (ev.delta() * self.state["wheelScaleFactor"])
        scale = [None if m is False else s for m in mask]
        tr = pg.functions.invertQTransform(self.childGroup.transform())
        center = pg.Point(tr.map(ev.pos()))
        self._resetTarget()
        self.scaleBy(scale, center)
        ev.accept()
        self.sigRangeChangedManually.emit(mask)
        self.sigUserZoomed.emit(bool(mask[0]), bool(mask[1]))

    def mouseDragEvent(self, ev, axis=None):
        ## if axis is specified, event will only affect that axis.
        ev.accept()  ## we accept all buttons

        pos = ev.pos()
        lastPos = ev.lastPos()
        dif = pos - lastPos
        dif = dif * -1

        ## Ignore axes if mouse is disabled
        mouseEnabled = np.array(self.state["mouseEnabled"], dtype=np.float64)
        mask = mouseEnabled.copy()
        if axis is not None:
            mask[1 - axis] = 0.0

        ## Scale or translate based on mouse button
        if ev.button() in [Qt.MouseButton.LeftButton, Qt.MouseButton.MiddleButton]:
            if self.state["mouseMode"] == pg.ViewBox.RectMode and axis is None:
                if ev.isStart():
                    # The modifiers that matter are the ones held when the
                    # drag *began*; by the time it finishes the user has
                    # usually let go of them.
                    self.drag_modifiers = ev.modifiers()
                if ev.isFinish():
                    # This is the final move in the drag; change the view scale now
                    rect = QRectF(
                        pg.Point(ev.buttonDownPos(ev.button())), pg.Point(pos)
                    )
                    rect = self.childGroup.mapRectFromParent(
                        rect
                    )  # in data coordinates
                    self.publish_region_mode()
                    self.sigSelectedRegion.emit(self.channel, self, rect)
                    self.sigSelectedRegionAt.emit(
                        self.channel, self, rect, ev.scenePos()
                    )
                    self.drag_modifiers = Qt.NoModifier
                else:
                    ## update shape of scale box
                    self.updateScaleBox(ev.buttonDownPos(), ev.pos())
            else:
                tr = self.childGroup.transform()
                tr = pg.functions.invertQTransform(tr)
                tr = tr.map(dif * mask) - tr.map(pg.Point(0, 0))

                x = tr.x() if mask[0] == 1 else None
                y = tr.y() if mask[1] == 1 else None

                self._resetTarget()
                if x is not None or y is not None:
                    self.translateBy(x=x, y=y)
                self.sigRangeChangedManually.emit(self.state["mouseEnabled"])
                self.sigUserZoomed.emit(x is not None, y is not None)
                if ev.isFinish():
                    self.add_region(self.viewRect())
        elif ev.button() & Qt.MouseButton.RightButton:
            # print "vb.rightDrag"
            if self.state["aspectLocked"] is not False:
                mask[0] = 0

            dif = ev.screenPos() - ev.lastScreenPos()
            dif = np.array([dif.x(), dif.y()])
            dif[0] *= -1
            s = ((mask * 0.02) + 1) ** dif

            tr = self.childGroup.transform()
            tr = pg.functions.invertQTransform(tr)

            x = s[0] if mouseEnabled[0] == 1 else None
            y = s[1] if mouseEnabled[1] == 1 else None

            center = pg.Point(tr.map(ev.buttonDownPos(Qt.MouseButton.RightButton)))
            self._resetTarget()
            self.scaleBy(x=x, y=y, center=center)
            self.sigRangeChangedManually.emit(self.state["mouseEnabled"])
            self.sigUserZoomed.emit(x is not None, y is not None)
            if ev.isFinish():
                self.add_region(self.viewRect())

    def updateScaleBox(self, p1, p2):
        r = QRectF(p1, p2)
        r = self.childGroup.mapRectFromParent(r)
        self.rbScaleBox.setPos(r.topLeft())
        tr = QTransform.fromScale(r.width(), r.height())
        self.rbScaleBox.setTransform(tr)
        self.rbScaleBox.show()

    def hide_region(self):
        self.rbScaleBox.hide()

    def add_region(self, rect):
        self.axHistoryPointer += 1
        self.axHistory = self.axHistory[: self.axHistoryPointer] + [rect]

    def zoom_region(self, rect):
        self.hide_region()
        self.showAxRect(rect)
        self.add_region(rect)
        # a rubber-band zoom is a deliberate zoom of both axes:
        self.sigUserZoomed.emit(True, True)

    def zoom_back(self):
        self.scaleHistory(-1)

    def zoom_forward(self):
        self.scaleHistory(1)

    def zoom_home(self):
        self.scaleHistory(-len(self.axHistory))

    def init_zoom_history(self):
        self.add_region(self.viewRect())
