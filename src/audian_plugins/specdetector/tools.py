"""Review on the spectrogram: a click surface and a few keys.

Labelling is audian's own label tool, with SAM as its assistant (`assistant`).  What
is left here is the part of review audian has no gesture for.

`LaneSurface`
    One per lane: an invisible child of the lane's view box that takes a *plain left
    click on a drawn candidate*, in label mode, and nothing else.  It has no drag
    handler at all, so every drag reaches the view box -- in label mode that is
    audian's rubber band, which labels an event the model missed -- and a click
    anywhere else, or with a modifier, goes on as if the surface were not there.

`KeyRouter`
    An application event filter claiming review's keys, only when the panel says it
    can act on them: with this browser's window active, no menu or dialog open, and
    never in a text field.  A window-wide shortcut would take Return away from every
    dialog and spin box in the application.

The gestures are in `panel.SpectrogramDetectorPanel`, which owns the state they change.
"""

from __future__ import annotations

from typing import Callable, Optional

import pyqtgraph as pg
from PySide6.QtCore import QEvent, QObject, QRectF, Qt
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QLineEdit,
    QPlainTextEdit,
    QTextEdit,
)


#: Above the spectrogram and the labels, so that a click on a candidate is offered here
#: first.
SURFACE_Z = 1000

#: The keys, with what they mean, for the tool tip and the manual.
KEY_HELP = (
    ("Click a candidate", "reject it (in label mode)"),
    ("Drag", "label an event the model missed, as always in label mode"),
    ("Enter", "accept the rest of the view and go to the next"),
    ("Shift+Enter", "reject the rest of the view and go to the next"),
    ("Backspace", "take back the last rejection or view"),
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
    """The invisible item on one lane that takes a click on a candidate."""

    def __init__(self, ax, host) -> None:
        super().__init__()
        self.ax = ax
        self.vb = ax.getViewBox()
        self.host = host
        self._rect = QRectF(self.vb.boundingRect())
        self.setParentItem(self.vb)
        self.setZValue(SURFACE_Z)
        self.vb.sigResized.connect(self._resized)
        self.setAcceptHoverEvents(True)
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)

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

    def to_data(self, scene_pos) -> tuple:
        point = self.vb.mapSceneToView(scene_pos)
        return float(point.x()), float(point.y())

    def hoverEvent(self, ev):  # noqa: N802 - pyqtgraph's spelling
        """Claim the click only over a candidate, and never a drag."""
        if ev.isExit():
            return
        t, f = self.to_data(ev.scenePos())
        if self.host.claims_click(self.ax, t, f):
            ev.acceptClicks(Qt.MouseButton.LeftButton)

    def mouseClickEvent(self, ev):  # noqa: N802
        plain = ev.modifiers() == Qt.KeyboardModifier.NoModifier
        if ev.button() != Qt.MouseButton.LeftButton or not plain:
            ev.ignore()
            return
        t, f = self.to_data(ev.scenePos())
        if self.host.candidate_clicked(self.ax, t, f):
            ev.accept()
        else:
            ev.ignore()


#: ``(key, shift)`` -> command the panel runs.
KEY_TABLE = {
    (Qt.Key.Key_Return, False): "enter",
    (Qt.Key.Key_Enter, False): "enter",
    (Qt.Key.Key_Return, True): "shift_enter",
    (Qt.Key.Key_Enter, True): "shift_enter",
    (Qt.Key.Key_Backspace, False): "backspace",
}


class KeyRouter(QObject):
    """Claims review's keys when the panel can act on them; passes the rest along.

    `claims` is asked about each command, so that a key review has nothing to do
    with right now -- Enter with no candidate in view, Backspace with nothing to take
    back -- keeps whatever meaning audian gives it.
    """

    def __init__(self, claims: Callable[[str], bool], run: Callable[[str], None]):
        super().__init__()
        self.claims = claims
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
        name = KEY_TABLE.get((Qt.Key(event.key()), shift))
        if name is None or not self.claims(name):
            return None
        return name

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
