"""Drawing hand-made labels over one plot, and editing the vocabulary.

The Qt half of `labels.py`, mirroring the `session.py` / `eventoverlay.py`
split: everything here touches Qt and nothing here decides what a label
means.

What a label looks like, and why
--------------------------------

*A label is the only mark in this application that is bounded in y.*  The
immutable annotation overlay is full-lane-height by rule -- ``np.tile((y0,
y1), drawn)`` in `eventoverlay._draw_spans`, stated in that module's
docstring as its one rule -- so a box that stops short of the top of the lane
is, by construction, one a reader drew.  That is the primary cue and it costs
nothing.

The secondary cue is the palette.  Labels take `theme.marker_color`, the
eight categorical marker colours; annotations take `theme.annotation_color`,
a disjoint set of role tokens.  `theme.annotation_color` raises KeyError on
an unknown role by design and the theme audit pins `CATEGORY_ROLES`
literally, so a reader-defined category has no business in that system.

**Outlines, never fills.**  `eventoverlay.SPAN_FILL_ALPHA` is 0.10, measured
for the annotation roles over the grounds a lane is really painted; the
marker palette is a different colour set and no alpha for it over either
ground has been measured.  Over a spectrogram nothing may be filled at all
(`eventoverlay.SURFACE_STYLE` sets ``fill: 0.0`` there, because
`SpecItem`'s colormap spans the whole luminance range).  A y-bounded box
reads from its outline; an unmeasured wash over the data does not.

Nothing here is an ROI
----------------------

A reader labelling densely draws a box inside a box, so the gesture has to
survive whatever is already on the lane.  Measured on a four channel stack at
1200x900, one drag per lane, with the item under the whole drag:

===========================================  ================
item                                         region signals
===========================================  ================
bare lane                                    1
``QGraphicsRectItem``, no mouse buttons      1
``QGraphicsRectItem``, ``Qt.LeftButton``     1
``pg.ScatterPlotItem``, default buttons      1
**movable** ``pg.RectROI``                   **0**
===========================================  ================

The ROI took the drag and moved itself, from (0.8 s, 1000.0 Hz) to
(1.2 s, 1406.8 Hz).  So a per-label ROI -- the obvious way to let a stored
box be dragged into shape -- costs the ability to start a new box inside an
existing one, which is the commoner gesture.  Stored labels are plain items;
editing a label's geometry with the mouse is deliberately not part of this
feature, and labels are removed from the table (`LabelTableModel`) or with
the undo key.

The items are passive anyway (`_passive`), for the reason `eventoverlay`
gives: a mark states where something is and is not a control, so it has no
business claiming hover or clicks.  What the table above says is that on
these two item types it is hygiene rather than what keeps the drag alive.

Self-driven
-----------

Like `EventOverlay`, this connects the view box's own ``sigRangeChanged``
and ``sigResized`` rather than riding `RangePlot.update_plot`, which has a
single ``self.annotations`` slot.  And like `EventOverlay` the redraw is
gated on ``(t0, t1, y0, y1, height, revision)`` -- so **a mutation that does
not bump `LabelSet.revision` will not repaint.**
"""

from __future__ import annotations

from typing import Optional

import pyqtgraph as pg
from PyQt5.QtCore import QAbstractTableModel, QModelIndex, QRectF, QSize, Qt, QVariant
from PyQt5.QtGui import QColor, QIcon, QIconEngine
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGraphicsRectItem,
    QHBoxLayout,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStyledItemDelegate,
    QTableView,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .eventoverlay import SURFACE_SPECTROGRAM, SURFACE_TRACE
from .labels import KIND_SPAN, KINDS, LabelCategory, LabelSet

#: Above `eventoverlay.MARK_Z` (15) and below the crosshair and the playback
#: marker (100).  A label is the thing being authored right now; an
#: annotation that crossed over it would hide the box the reader just drew.
LABEL_Z = 25

#: Half-width of a frequency-less point label, in device pixels.  A point has
#: no extent, so its bar is sized in pixels and converted through the view
#: box: at any zoom it stays a hairline rather than growing into a span.
POINT_HALF_PX = 1.0

#: Symbol size of a point label that does carry a frequency.
POINT_SYMBOL_PX = theme.S12

#: Rect items built up front per overlay.  Grown on demand and never shrunk,
#: the way `eventoverlay`'s label pool is: a stack of sixteen channels has 32
#: overlays, and churning items on every pan is what that pool exists to
#: avoid.  Eight is a working set, not a cap.
INITIAL_POOL = 8


def _passive(item) -> None:
    """Make an item invisible to the mouse.

    A label states where something is; it is not a control, so it takes no
    clicks and no hovers.  `eventoverlay._passive` does the same to its
    curves.

    Not, on these two item types, what keeps the rubber band working: a rect
    item flipped to ``Qt.LeftButton`` and a scatter with pyqtgraph's default
    buttons both let a drag through unchanged when it was measured.  See the
    module docstring for the one item that did not.
    """
    item.setAcceptedMouseButtons(Qt.NoButton)
    item.setAcceptHoverEvents(False)


class LabelOverlay:
    """The hand-made labels of one plot.

    One per trace or spectrogram plot, built once when the stack is built and
    costing nothing until a label exists.
    """

    def __init__(self, plot, store: LabelSet, surface: str = SURFACE_TRACE):
        self.plot = plot
        self.store = store
        self.surface = surface
        #: whether frequency bounds mean anything on this surface.  A trace's
        #: y axis is amplitude, so a box drawn there is bounded in time only
        #: -- and a label that carries a frequency is still drawn full height
        #: on it rather than at some amplitude it never claimed.
        self.has_frequency = surface == SURFACE_SPECTROGRAM
        #: master switch, driven from the parameter bar
        self.visible = True
        self.boxes: list[QGraphicsRectItem] = []
        self._boxes_live = 0
        self.points = pg.ScatterPlotItem(
            symbol="+", size=POINT_SYMBOL_PX, pxMode=True, hoverable=False
        )
        self.points.setZValue(LABEL_Z)
        _passive(self.points)
        # ignoreBounds: `RangePlot.add_item` ends in a bare addItem(), and a
        # rect added that way joins childrenBounds -- measured, it moved a
        # lane's bounds to [[-100.5, 400.5], [-50000.5, 50000.5]] in an
        # earlier pass.  Every item of `eventoverlay` is added this way too.
        self.plot.addItem(self.points, ignoreBounds=True)
        self._points_blank = True
        self._drawn: Optional[tuple] = None
        for _ in range(INITIAL_POOL):
            self._grow()
        view = plot.getViewBox()
        if view is not None:
            view.sigRangeChanged.connect(self._view_changed)
            view.sigResized.connect(self._view_changed)

    # --- items ------------------------------------------------------------

    def _grow(self) -> QGraphicsRectItem:
        item = QGraphicsRectItem()
        item.setZValue(LABEL_Z)
        item.setVisible(False)
        _passive(item)
        self.plot.addItem(item, ignoreBounds=True)
        self.boxes.append(item)
        return item

    def channel(self) -> int:
        return getattr(self.plot, "channel", 0)

    def channels(self):
        """The channels whose labels belong on this plot.

        Its own, normally.  Every channel it averages while it is showing
        the mean spectrogram: that panel stands for the whole selected
        array, so showing one electrode's labels on it -- the lane the mean
        happens to have borrowed -- would say the array had been labelled
        far less than it was.
        """
        mean = getattr(self.plot, "mean_channels", None)
        return list(mean) if mean else self.channel()

    def set_visible(self, on: bool) -> None:
        on = bool(on)
        if on == self.visible:
            return
        self.visible = on
        self._drawn = None
        self.update_plot()

    def invalidate(self) -> None:
        """Force the next `update_plot` to redraw whatever it is handed."""
        self._drawn = None

    def polish(self) -> None:
        """Re-read the palette after a live theme switch."""
        self._drawn = None
        self.update_plot()

    def clear(self) -> None:
        for item in self.boxes:
            item.setVisible(False)
        self._boxes_live = 0
        if not self._points_blank:
            self.points.setData([], [])
            self._points_blank = True

    # --- drawing ----------------------------------------------------------

    def _view_changed(self, *args) -> None:
        self.update_plot()

    def update_plot(self) -> None:
        """Redraw this lane's labels, if anything about them changed.

        A hidden lane still gets its view box's ``sigRangeChanged``, and
        redrawing what nobody can see is the whole cost of hiding a channel
        in a sixteen channel stack.  The last-drawn state is dropped rather
        than kept, because nothing promises a range signal when the lane
        comes back.
        """
        if not self.plot.isVisible():
            self._drawn = None
            return
        view = self.plot.getViewBox()
        if view is None:
            return
        (t0, t1), (y0, y1) = view.viewRange()
        state = (
            t0,
            t1,
            y0,
            y1,
            view.height(),
            self.store.revision,
            self.visible,
            # the mean spectrogram changes which channels' labels belong
            # here without changing anything else in this tuple
            tuple(self.channels()) if isinstance(self.channels(), list) else None,
        )
        if state == self._drawn:
            return
        self._drawn = state
        if not self.visible:
            self.clear()
            return
        self._draw(t0, t1, y0, y1, view)

    def _draw(self, t0, t1, y0, y1, view) -> None:
        channels = self.channels()
        half = POINT_HALF_PX * float(view.viewPixelSize()[0])
        used = 0
        xs: list[float] = []
        ys: list[float] = []
        pens = []
        brushes = []
        for _index, label in self.store.window(t0, t1, channels):
            color = theme.marker_color(self.store.color_of(label.category))
            bounded = self.has_frequency and label.has_frequency()
            if label.is_point():
                if bounded:
                    xs.append(label.t0)
                    ys.append(0.5 * (label.f0 + label.f1))
                    pens.append(theme.pen(color, theme.LW_THIN))
                    brushes.append(theme.brush(color))
                    continue
                left, width = label.t0 - half, 2 * half
            else:
                left, width = label.t0, max(label.t_end() - label.t0, 2 * half)
            if bounded:
                bottom, height = label.f0, label.f1 - label.f0
            else:
                bottom, height = y0, y1 - y0
            if used >= len(self.boxes):
                self._grow()
            item = self.boxes[used]
            item.setRect(QRectF(left, bottom, width, height))
            item.setPen(theme.pen(color, theme.LW_THIN))
            item.setVisible(True)
            used += 1
        for item in self.boxes[used : self._boxes_live]:
            item.setVisible(False)
        self._boxes_live = used
        if xs:
            self.points.setData(xs, ys, pen=pens, brush=brushes)
            self._points_blank = False
        elif not self._points_blank:
            self.points.setData([], [])
            self._points_blank = True


# --- the category editor ----------------------------------------------------


class _SwatchEngine(QIconEngine):
    """A palette swatch, drawn rather than baked from a file.

    A backdrop and a hairline ring under the dot, so it reads on the light
    theme's surfaces as well as the dark one's -- and drawn from the theme's
    own tokens, so a live theme switch only has to rebuild the icon rather
    than find a new asset.
    """

    def __init__(self, index: int):
        super().__init__()
        self.index = int(index)

    def paint(self, painter, rect, mode=QIcon.Normal, state=QIcon.Off):
        painter.setBrush(theme.qcolor(theme.MARKER_ICON_BG))
        painter.setPen(theme.pen(theme.MARKER_ICON_RING))
        painter.drawRect(rect.adjusted(0, 0, -1, -1))
        painter.setBrush(QColor(theme.marker_color(self.index)))
        painter.setPen(Qt.NoPen)
        d = rect.width() // 5
        painter.drawEllipse(rect.adjusted(d, d, -d, -d))


def swatch_icon(index: int) -> QIcon:
    """A `theme.marker_color` swatch for the palette entry `index`."""
    return QIcon(_SwatchEngine(index))


class _KindDelegate(QStyledItemDelegate):
    """point / span as a combo box, because they are the only two."""

    def createEditor(self, parent, option, index):
        editor = QComboBox(parent)
        for kind in KINDS:
            editor.addItem(kind)
        editor.setEditable(False)
        return editor

    def setEditorData(self, editor, index):
        editor.setCurrentText(index.model().data(index, Qt.EditRole))

    def setModelData(self, editor, model, index):
        model.setData(index, editor.currentText(), Qt.EditRole)

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(option.rect)


class _ColorDelegate(QStyledItemDelegate):
    """The eight palette entries, by swatch."""

    def createEditor(self, parent, option, index):
        editor = QComboBox(parent)
        for i in range(8):
            editor.addItem(swatch_icon(i), str(i))
        editor.setEditable(False)
        return editor

    def setEditorData(self, editor, index):
        editor.setCurrentText(str(index.model().data(index, Qt.EditRole)))

    def setModelData(self, editor, model, index):
        model.setData(index, editor.currentText(), Qt.EditRole)

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(option.rect)


class CategoryModel(QAbstractTableModel):
    """The vocabulary, edited on a copy and stored on OK.

    A copy rather than the live list: the categories drive what is on screen
    and what the next drag writes, and a half-typed name that repainted 32
    lanes on every keystroke would be a rename nobody asked for.
    """

    HEADER = ("category", "kind", "color")

    def __init__(self, store: LabelSet, parent=None):
        super().__init__(parent)
        self.store = store
        self.rows: list[LabelCategory] = list(store.categories)

    def rowCount(self, parent=None) -> int:
        return len(self.rows)

    def columnCount(self, parent=None) -> int:
        return len(self.HEADER)

    def headerData(self, index, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self.HEADER[index]
        if orientation == Qt.Vertical and role == Qt.DisplayRole:
            return f"{index}"
        return QVariant()

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return QVariant()
        row = self.rows[index.row()]
        column = index.column()
        if role in (Qt.DisplayRole, Qt.EditRole):
            if column == 0:
                return row.name
            if column == 1:
                return row.kind
            return str(row.color)
        if role == Qt.DecorationRole and column == 2:
            return swatch_icon(row.color)
        if role == Qt.ToolTipRole and column == 0:
            count = self.store.count_in(row.name)
            return f"{count} label{'' if count == 1 else 's'} in this recording"
        if role == Qt.TextAlignmentRole:
            return Qt.AlignLeft | Qt.AlignVCenter
        return QVariant()

    def flags(self, index):
        if not index.isValid():
            return Qt.NoItemFlag
        return Qt.ItemIsSelectable | Qt.ItemIsEnabled | Qt.ItemIsEditable

    def setData(self, index, value, role=Qt.EditRole) -> bool:
        if not index.isValid():
            return False
        row = self.rows[index.row()]
        column = index.column()
        if column == 0:
            name = str(value).strip()
            if not name or any(
                c.name == name for i, c in enumerate(self.rows) if i != index.row()
            ):
                return False
            self.rows[index.row()] = LabelCategory(name, row.kind, row.color)
        elif column == 1:
            kind = str(value)
            if kind not in KINDS:
                return False
            self.rows[index.row()] = LabelCategory(row.name, kind, row.color)
        elif column == 2:
            try:
                color = int(value)
            except (TypeError, ValueError):
                return False
            self.rows[index.row()] = LabelCategory(row.name, row.kind, color % 8)
        else:
            return False
        self.dataChanged.emit(index, index)
        return True

    def add_row(self) -> None:
        """A new category, named and coloured so as not to collide.

        The colour is the lowest palette index nothing in this table is
        using, and only wraps once all eight are taken -- ``len(rows) % 8``
        would hand the ninth category the first one's colour while an index
        freed by a removal sat unused.
        """
        used = {c.name for c in self.rows}
        name = "category"
        suffix = 1
        while name in used:
            suffix += 1
            name = f"category{suffix}"
        taken = {c.color % 8 for c in self.rows}
        color = next((i for i in range(8) if i not in taken), len(self.rows) % 8)
        self.beginInsertRows(QModelIndex(), len(self.rows), len(self.rows))
        self.rows.append(LabelCategory(name, KIND_SPAN, color))
        self.endInsertRows()

    def remove_rows(self, view: QTableView) -> None:
        """Drop the selected categories, asking first when rows would go.

        Removing a category removes its labels -- see
        `LabelSet.remove_category`, which says why orphaning them is worse --
        so the count is what the question is about.  A category with no
        labels goes without a dialog: there is nothing to lose.
        """
        selection = view.selectionModel()
        if selection is None or not selection.hasSelection():
            return
        rows = sorted({i.row() for i in selection.selectedIndexes()}, reverse=True)
        losing = {
            self.rows[r].name: self.store.count_in(self.rows[r].name) for r in rows
        }
        total = sum(losing.values())
        if total:
            listed = ", ".join(f"{name} ({n})" for name, n in losing.items() if n)
            answer = QMessageBox.question(
                view,
                "Remove labelled categories",
                f"Removing {listed} also removes "
                f"{total} label{'' if total == 1 else 's'} of this recording.\n"
                "This cannot be undone.",
                QMessageBox.Cancel | QMessageBox.Yes,
                QMessageBox.Cancel,
            )
            if answer != QMessageBox.Yes:
                return
        for r in rows:
            self.beginRemoveRows(QModelIndex(), r, r)
            del self.rows[r]
            self.endRemoveRows()

    def store_rows(self) -> list[str]:
        """Push the edited vocabulary onto the store.  Returns names dropped."""
        kept = {c.name for c in self.rows}
        gone = [c.name for c in self.store.categories if c.name not in kept]
        for name in gone:
            self.store.remove_category(name)
        self.store.set_categories(self.rows)
        return gone


class CategoryDialog(QDialog):
    """The interactive half of "categories can be added and removed"."""

    def __init__(self, store: LabelSet, parent=None):
        super().__init__(parent)
        self.store = store
        self.model = CategoryModel(store, self)
        self.dropped: list[str] = []
        self.setWindowTitle("Audian label categories")
        self.setWindowModality(Qt.NonModal)
        self.setAttribute(Qt.WA_DeleteOnClose)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(theme.S12, theme.S12, theme.S12, theme.S12)
        outer.setSpacing(theme.S8)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        outer.addLayout(row)
        self.view = QTableView(self)
        self.view.setModel(self.model)
        self.view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.view.setItemDelegateForColumn(1, _KindDelegate(self))
        self.view.setItemDelegateForColumn(2, _ColorDelegate(self))
        self.view.horizontalHeader().setStretchLastSection(True)
        self.view.resizeColumnsToContents()
        row.addWidget(self.view)
        buttons = QVBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        row.addLayout(buttons)
        add = QPushButton("&Add", self)
        add.clicked.connect(self.model.add_row)
        buttons.addWidget(add)
        remove = QPushButton("&Remove", self)
        remove.clicked.connect(lambda: self.model.remove_rows(self.view))
        buttons.addWidget(remove)
        buttons.addStretch(1)
        box = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Ok, self)
        box.rejected.connect(self.reject)
        box.accepted.connect(self.accept)
        outer.addWidget(box)
        # No maximum width: a width cap fights a tiling compositor, and the
        # user of this fork runs one.  The height follows the row count.
        width = 2 * theme.S24 + remove.sizeHint().width()
        width += self.view.verticalHeader().width()
        for c in range(self.model.columnCount()):
            width += self.view.columnWidth(c)
        row_height = self.view.verticalHeader().defaultSectionSize()
        self.resize(
            width,
            (self.model.rowCount() + 3) * row_height
            + box.sizeHint().height()
            + 2 * theme.S12,
        )

    def accept(self) -> None:
        self.dropped = self.model.store_rows()
        super().accept()


# --- the label table --------------------------------------------------------


def _seconds(value) -> str:
    return "" if value is None else f"{float(value):.3f}"


def _hertz(value) -> str:
    return "" if value is None else f"{float(value):.1f}"


class LabelTableModel(QAbstractTableModel):
    """Every label of the open recording, as rows that can be removed.

    Over the live store, not a copy: this is where a label is deleted, and a
    delete that only took effect on OK would be a second place the count in
    the parameter bar could disagree with what is on screen.
    """

    HEADER = ("category", "kind", "ch", "start/s", "end/s", "low/Hz", "high/Hz", "note")

    def __init__(self, store: LabelSet, parent=None):
        super().__init__(parent)
        self.store = store

    def rowCount(self, parent=None) -> int:
        return len(self.store.labels)

    def columnCount(self, parent=None) -> int:
        return len(self.HEADER)

    def headerData(self, index, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self.HEADER[index]
        if orientation == Qt.Vertical and role == Qt.DisplayRole:
            return f"{index}"
        return QVariant()

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return QVariant()
        label = self.store.labels[index.row()]
        column = index.column()
        if role in (Qt.DisplayRole, Qt.EditRole):
            return (
                label.category,
                label.kind,
                "" if label.channel is None else f"{label.channel:02d}",
                _seconds(label.t0),
                _seconds(label.t1),
                _hertz(label.f0),
                _hertz(label.f1),
                label.note,
            )[column]
        if role == Qt.DecorationRole and column == 0:
            return swatch_icon(self.store.color_of(label.category))
        if role == Qt.TextAlignmentRole:
            if column in (0, 1, 7):
                return Qt.AlignLeft | Qt.AlignVCenter
            return Qt.AlignRight | Qt.AlignVCenter
        return QVariant()

    def flags(self, index):
        if not index.isValid():
            return Qt.NoItemFlag
        flags = Qt.ItemIsSelectable | Qt.ItemIsEnabled
        # only the note: everything else is geometry the mouse put there, and
        # a typo in a frequency is a label that moves without being redrawn
        if index.column() == len(self.HEADER) - 1:
            flags |= Qt.ItemIsEditable
        return flags

    def setData(self, index, value, role=Qt.EditRole) -> bool:
        if not index.isValid() or index.column() != len(self.HEADER) - 1:
            return False
        if not self.store.set_note(index.row(), str(value)):
            return False
        self.dataChanged.emit(index, index)
        return True

    def remove_rows(self, view: QTableView) -> int:
        """Delete the selected labels.  Returns how many went."""
        selection = view.selectionModel()
        if selection is None or not selection.hasSelection():
            return 0
        rows = sorted({i.row() for i in selection.selectedIndexes()}, reverse=True)
        for r in rows:
            self.beginRemoveRows(QModelIndex(), r, r)
            self.store.remove(r)
            self.endRemoveRows()
        return len(rows)

    def refresh(self) -> None:
        """Take the store's word for it again, after a change from outside."""
        self.beginResetModel()
        self.endResetModel()


class LabelTable(QDialog):
    """The list of labels, with the one control that removes them."""

    def __init__(self, store: LabelSet, parent=None, on_change=None):
        super().__init__(parent)
        self.store = store
        self.on_change = on_change
        self.model = LabelTableModel(store, self)
        self.setWindowTitle("Audian labels")
        self.setWindowModality(Qt.NonModal)
        self.setAttribute(Qt.WA_DeleteOnClose)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(theme.S12, theme.S12, theme.S12, theme.S12)
        outer.setSpacing(theme.S8)
        self.view = QTableView(self)
        self.view.setModel(self.model)
        self.view.setFont(theme.font_mono())
        self.view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.view.resizeColumnsToContents()
        self.view.horizontalHeader().setStretchLastSection(True)
        outer.addWidget(self.view)
        box = QDialogButtonBox(QDialogButtonBox.Close, self)
        remove = box.addButton("&Remove", QDialogButtonBox.DestructiveRole)
        remove.clicked.connect(self.remove_selected)
        box.rejected.connect(self.reject)
        outer.addWidget(box)
        self.adjustSize()

    def remove_selected(self) -> None:
        if self.model.remove_rows(self.view) and self.on_change is not None:
            self.on_change()


def category_tip(category: LabelCategory, key: str) -> str:
    """What one category chip says when the pointer rests on it."""
    if category.kind == KIND_SPAN:
        how = "drag a box over a lane in label mode (b)"
    else:
        how = "turn the cross hair on (Ctrl+C) and press its key"
    lines = [f"{category.name} -- {category.kind}: {how}."]
    if key:
        lines.append(f"Press {key} to pick it.")
    else:
        lines.append("No key: the digits only reach the first nine categories.")
    return "\n".join(lines)


def category_chip(parent: QWidget, category: LabelCategory, checked: bool, key: str):
    """One category as a checkable chip, coloured like its labels.

    The chip is the legend as well as the picker, the way the annotation
    layer chips are: which colour means which word is read off the bar rather
    than remembered.  The key rides on the chip rather than in the tool tip,
    because a shortcut nobody can see is a shortcut nobody uses.
    """
    chip = QToolButton(parent)
    chip.setText(f"{key} {category.name}" if key else category.name)
    chip.setCheckable(True)
    chip.setChecked(bool(checked))
    chip.setFont(theme.font_mono(theme.SIZE_SMALL_PT))
    chip.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
    chip.setFixedHeight(theme.CHIP_HEIGHT)
    chip.setIconSize(QSize(theme.S12, theme.S12))
    chip.setIcon(swatch_icon(category.color))
    chip.setToolTip(category_tip(category, key))
    return chip


class CategoryStrip(QWidget):
    """The category chips, folding into a ``+N`` menu when the bar is narrow.

    The parameter bar does not wrap, does not scroll and does not elide.  A
    row that asks for more width than its column has widens the whole
    application instead, and *this* row's width is a function of how many
    categories the reader defined -- so it is the one row of the bar that can
    grow without anybody choosing to grow it.

    Measured on a four channel recording, the window asked to be 1200x900,
    back when every group was on screen at once and this one had a fifth of
    the bar: a plain ``QHBoxLayout`` of two chips plus an Edit button took
    the window's minimum from 1372 px to 1572.  Two categories.  The groups
    are behind tabs now and this strip gets 1167 px rather than 198, so
    twelve categories fit -- but the number of them is still the reader's,
    and a strip that grew with it would put the problem straight back.

    So the chips are placed by hand rather than by a layout -- a layout would
    re-impose exactly the minimum this exists to avoid -- and the ones that
    do not fit go into the ``+N`` button's menu with their swatch and their
    key.  Folded, never dropped: the menu is a second place every category
    is, not the only place some of them are.

    Two lines rather than one, because the height is free: the bar is as
    tall as its tallest page whichever page is showing, and that is the
    annotations group's five rows against this group's four.  So the second
    line of chips costs the lanes nothing and doubles what is visible at
    once.

    What is shown is always a *prefix* of the vocabulary, never a
    best-fit selection: the first nine categories are the ones with the
    digit keys, and a strip that hid the third to show the fourth would put
    the visible chips out of step with the keys under them.
    """

    #: Gap between chips, and between the last chip and the ``+N`` button.
    SPACING = theme.S4
    #: Gap between the two lines of chips.
    VGAP = theme.S2
    #: How many lines of chips the strip is tall.
    ROWS = 2

    def __init__(self, parent=None, on_pick=None):
        super().__init__(parent)
        #: called with a category name when a chip or a menu entry is chosen
        self.on_pick = on_pick
        self.chips: dict[str, QToolButton] = {}
        self.keys: dict[str, str] = {}
        self.categories: list[LabelCategory] = []
        self.folded: list[LabelCategory] = []
        self.more = QToolButton(self)
        self.more.setFont(theme.font_mono(theme.SIZE_SMALL_PT))
        self.more.setFixedHeight(theme.CHIP_HEIGHT)
        self.more.setPopupMode(QToolButton.InstantPopup)
        self.more.setVisible(False)
        self.menu = QMenu(self)
        self.more.setMenu(self.menu)
        # Ignored: the strip takes the width the column has and never asks
        # for more.  Fixed height, because the number of lines is chosen here
        # and must not follow the number of categories.
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.setFixedHeight(self.strip_height())

    @classmethod
    def strip_height(cls) -> int:
        return cls.ROWS * theme.CHIP_HEIGHT + (cls.ROWS - 1) * cls.VGAP

    def sizeHint(self) -> QSize:
        return QSize(theme.S24, self.strip_height())

    def minimumSizeHint(self) -> QSize:
        # What the group is allowed to shrink to.  Wide enough for the +N
        # button alone, which is the fully folded state and still names every
        # category through its menu.
        return QSize(self.more.sizeHint().width() or theme.S24, self.strip_height())

    def set_categories(self, categories, current: str, keys) -> None:
        """Rebuild the chips for `categories`, with `current` checked."""
        for chip in self.chips.values():
            chip.setParent(None)
            chip.deleteLater()
        self.chips = {}
        self.categories = list(categories)
        self.keys = dict(keys)
        for category in self.categories:
            chip = category_chip(
                self,
                category,
                category.name == current,
                self.keys.get(category.name, ""),
            )
            chip.clicked.connect(
                lambda _checked=False, name=category.name: self.picked(name)
            )
            chip.setVisible(False)
            self.chips[category.name] = chip
        self.relayout()

    def set_current(self, current: str) -> None:
        for name, chip in self.chips.items():
            on = name == current
            if chip.isChecked() != on:
                blocked = chip.blockSignals(True)
                chip.setChecked(on)
                chip.blockSignals(blocked)

    def picked(self, name: str) -> None:
        if self.on_pick is not None:
            self.on_pick(name)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.relayout()

    def pack(self, widths, budget: int, reserve: int):
        """``(placements, leftover)`` for `widths` over `ROWS` lines.

        `reserve` is room held back on the LAST line for the ``+N`` button,
        so the fold marker can never itself be the thing that does not fit.
        Stops at the first chip that will not go, which is what keeps the
        shown set a prefix -- see the class docstring.
        """
        placements = []
        row = 0
        x = 0
        for index, (category, w) in enumerate(widths):
            while True:
                room = budget - (reserve if row == self.ROWS - 1 else 0)
                if x + w <= room:
                    placements.append((category, x, row, w))
                    x += w + self.SPACING
                    break
                if x == 0 or row >= self.ROWS - 1:
                    return placements, [c for c, _w in widths[index:]]
                row += 1
                x = 0
        return placements, []

    def relayout(self) -> None:
        """Place the chips that fit, and fold the rest into the ``+N`` menu."""
        budget = self.width()
        widths = [
            (c, self.chips[c.name].sizeHint().width())
            for c in self.categories
            if c.name in self.chips
        ]
        placements, leftover = self.pack(widths, budget, 0)
        if leftover:
            # measured with the count it will really carry, because "+9" and
            # "+11" are not the same width
            self.more.setText(f"+{len(leftover)}")
            placements, leftover = self.pack(
                widths, budget, self.more.sizeHint().width() + self.SPACING
            )
        self.folded = list(leftover)
        pitch = theme.CHIP_HEIGHT + self.VGAP
        last_row, last_x = 0, 0
        for category, x, row, w in placements:
            chip = self.chips[category.name]
            chip.setGeometry(x, row * pitch, w, theme.CHIP_HEIGHT)
            chip.setVisible(True)
            last_row, last_x = row, x + w + self.SPACING
        for category in self.folded:
            self.chips[category.name].setVisible(False)
        self.menu.clear()
        if self.folded:
            self.more.setText(f"+{len(self.folded)}")
            self.more.setToolTip(
                "Categories the bar has no room for.  They keep their keys."
            )
            for category in self.folded:
                key = self.keys.get(category.name, "")
                act = self.menu.addAction(
                    swatch_icon(category.color),
                    f"{category.name}  {key}" if key else category.name,
                )
                act.setToolTip(category_tip(category, key))
                act.triggered.connect(
                    lambda _c=False, name=category.name: self.picked(name)
                )
            width = self.more.sizeHint().width()
            self.more.setGeometry(
                max(last_x, budget - width),
                last_row * pitch,
                width,
                theme.CHIP_HEIGHT,
            )
            self.more.setVisible(True)
        else:
            self.more.setVisible(False)
