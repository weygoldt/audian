"""The draggable boundary between a channel's trace and its spectrogram.

`class PanelSplitter`: the grab band that sets how a lane is split.

Why an in-scene item and not a `QSplitter`.  A channel's panels are rows of
one `pyqtgraph.GraphicsLayoutWidget`, laid out by a `QGraphicsGridLayout`
inside a single `QGraphicsScene`; there are no child *widgets* to put a
splitter between.  So the handle is a `QGraphicsWidget` occupying the spacer
row that `panels.Panels.insert_spacers` already puts between every pair of
panels -- a row that is zero pixels tall and was empty until now.

That the row stays zero pixels tall is the point, not an accident.  A
`QSplitter` has to spend real layout height on its handle because widgets
cannot overlap; a `QGraphicsItem` can report a bounding rect taller than the
geometry it was laid out in and take the mouse over its neighbours.  So the
band reaches `theme.PANEL_SPLIT_HANDLE_HEIGHT` px across the boundary while
costing the lane nothing -- which is what lets the default split still hand
the spectrogram its full `theme.SPECTROGRAM_MIN_HEIGHT` allowance.

It reports a drag as a delta in device pixels and lets `DataBrowser` decide
what that means.  The split itself lives on the browser, not here, which is
the whole reason one drag moves every channel: there is only one ratio.
"""

from __future__ import annotations

import pyqtgraph as pg
from PySide6.QtCore import QPointF, QRectF, QSizeF, Qt
from PySide6.QtGui import QPainterPath

from . import theme

#: Above the two plots it straddles, below the 1000 the lane frame uses.
#: The band overlaps the bottom of the spectrogram and the top of the trace,
#: and `QGraphicsScene` hands a press to the topmost item that accepts it --
#: at equal z that is the last one added, which is the trace plot, and the
#: lower half of the band would never see a click.
HANDLE_Z = 500


class PanelSplitter(pg.GraphicsWidget):
    """The grab band on one channel's trace / spectrogram boundary.

    Unlike every other in-scene item in this application, this one accepts
    the mouse.  The rule it breaks -- borders, overlays, labels and markers
    all set `NoButton` so a click reaches the plot beneath -- exists so that
    decoration never swallows a gesture meant for the data.  This is not
    decoration: it is a control, the click *is* meant for it, and the few
    pixels it takes from each neighbour are the pixels a reader aiming at
    the boundary between them is aiming at anyway.

    Its row is always zero pixels tall; `adjust_layout` shows or hides it
    according to whether the lane has two panels to divide, so a lane with
    nothing to split has no handle in it.
    """

    def __init__(self, channel: int, browser):
        super().__init__()
        self.channel = channel
        self.browser = browser
        self.setAcceptHoverEvents(True)
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)
        self.setCursor(Qt.CursorShape.SplitVCursor)
        self.setZValue(HANDLE_Z)
        self._hovered = False
        self._dragging = False
        # re-latched whenever the lane changes under the drag: see
        # `mouseMoveEvent`
        self._press_y = 0.0
        self._press_spec = 0.0
        self._press_room = 0.0
        # where the pointer was at the previous event of this gesture, which
        # is what a re-latch has to measure the current move's travel from
        self._last_y = 0.0
        self._rest_pen = theme.border_pen()
        self._active_pen = theme.handle_pen()

    # --- theme ------------------------------------------------------------

    def polish(self) -> None:
        """Re-resolve the two pens after a live theme switch."""
        self._rest_pen = theme.border_pen()
        self._active_pen = theme.handle_pen()
        self.update()

    # --- layout -----------------------------------------------------------

    def sizeHint(self, which, constraint=QSizeF()):
        """Full width, and no height at all, on every hint.

        A maximum height of zero is what makes the promise in the module
        docstring enforceable rather than a convention: whatever a layout
        pass has left over to hand out, this row cannot take any of it, so
        the lane's whole height stays with the two panels.  The maximum has
        to leave the *width* unbounded, though: answering the same hint for
        every `which` gives the band a maximum width of zero, and a control
        0 px wide is one that paints nothing and can never be clicked.
        """
        if which == Qt.SizeHint.MaximumSize:
            # QWIDGETSIZE_MAX wide, nothing tall
            return QSizeF(16777215.0, 0.0)
        return QSizeF(0.0, 0.0)

    def band_rect(self) -> QRectF:
        """The pixels the band answers for, in item coordinates.

        Centred on the item's own zero-height rect, which the layout has put
        exactly on the boundary, so the band reaches equally into the panel
        above and the panel below.
        """
        reach = float(theme.PANEL_SPLIT_HANDLE_HEIGHT)
        return QRectF(0.0, -0.5 * reach, max(self.geometry().width(), 0.0), reach)

    def boundingRect(self) -> QRectF:
        return self.band_rect()

    def shape(self) -> QPainterPath:
        """Hit test against the whole band, not against a 0 px rect.

        `QGraphicsWidget.shape` is the geometry Qt laid the item out in --
        here a rectangle of zero height, which no click can ever land in.
        """
        path = QPainterPath()
        path.addRect(self.band_rect())
        return path

    def setGeometry(self, rect) -> None:
        """Tell the scene the bounding rect moved with the row.

        Without `prepareGeometryChange` the scene keeps indexing the band at
        the boundary it used to be on, and every drag leaves the pixels that
        take the mouse a little further behind the line that is painted.
        """
        self.prepareGeometryChange()
        super().setGeometry(rect)

    # --- painting ---------------------------------------------------------

    def paint(self, painter, *args) -> None:
        """A hairline at rest, the handle pen while hovered or dragged.

        At rest it reads as what it is anyway -- the separator between two
        panels -- and only claims to be grabbable once the pointer is on it
        and the cursor has already changed shape.
        """
        rect = self.band_rect()
        if rect.width() <= 0:
            return
        active = self._hovered or self._dragging
        painter.setPen(self._active_pen if active else self._rest_pen)
        painter.drawLine(QPointF(rect.left(), 0.0), QPointF(rect.right(), 0.0))

    # --- the gesture ------------------------------------------------------

    def hoverEnterEvent(self, ev) -> None:
        self._hovered = True
        self.update()

    def hoverLeaveEvent(self, ev) -> None:
        self._hovered = False
        self.update()

    def _latch(self, scene_y: float) -> bool:
        """Read the boundary off the real rows and remember where it is."""
        heights = self.browser.panel_split_heights(self.channel)
        if heights is None:
            return False
        self._press_spec, self._press_room = heights
        self._press_y = scene_y
        self._last_y = scene_y
        return True

    def mousePressEvent(self, ev) -> None:
        """Latch where the boundary is now, measured off the real rows."""
        if ev.button() != Qt.MouseButton.LeftButton:
            ev.ignore()
            return
        if not self._latch(ev.scenePos().y()):
            ev.ignore()
            return
        self._dragging = True
        ev.accept()
        self.update()

    def mouseMoveEvent(self, ev) -> None:
        """Turn the pointer's absolute travel into a new spectrogram height.

        Absolute, from the latch, not summed from move to move.  The browser
        clamps the height it is handed, so an incremental drag would throw
        away every pixel a clamp ate and the boundary would come back short
        of where it started -- drift the reader can see and cannot undo.
        With the latched position and the latched height, pushing past a
        clamp and coming back lands on exactly the pixel the drag began on.

        What the latch cannot be is the *press*, for as long as the lane can
        change with the button down.  The travel is mapped onto the pixels
        the two panels share, and F6 hides the navigator -- reachable
        mid-drag -- which hands every lane 56 px more of them: measured, a
        pointer that then moved 20 px moved the boundary 25, because the
        gesture was being scaled by ``room_new / room_old``.  A window
        resize was worse: 30 px of travel moved the boundary 62.

        So a lane change re-latches, from where the pointer was at the
        previous event rather than from where it is now -- the travel since
        then is the reader's and is not the lane's to swallow -- and every
        pixel after that is 1:1 again.

        The scene of a `GraphicsLayoutWidget` is its viewport at 1:1 (its
        scene rect is set to the viewport size), so a scene delta *is* a
        device-pixel delta; nothing here has to map through a view box.
        """
        if not self._dragging:
            ev.ignore()
            return
        scene_y = ev.scenePos().y()
        heights = self.browser.panel_split_heights(self.channel)
        if heights is not None and heights[1] != self._press_room:
            self._latch(self._last_y)
        delta = scene_y - self._press_y
        self._last_y = scene_y
        self.browser.drag_panel_split(self._press_spec + delta, self._press_room)
        ev.accept()

    def mouseReleaseEvent(self, ev) -> None:
        if not self._dragging:
            ev.ignore()
            return
        self._dragging = False
        self.browser.finish_panel_split()
        ev.accept()
        self.update()

    def mouseDoubleClickEvent(self, ev) -> None:
        """Back to the default split, the way a `QSplitter` handle behaves."""
        if ev.button() != Qt.MouseButton.LeftButton:
            ev.ignore()
            return
        self._dragging = False
        self.browser.reset_panel_split()
        ev.accept()
