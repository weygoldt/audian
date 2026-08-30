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

Only the selected label is an ROI, and it is an immovable one
------------------------------------------------------------

A reader labelling densely draws a box inside a box, so the gesture has to
survive whatever is already on the lane.  Measured on a four channel stack at
1200x900, one drag per lane with the item under the whole drag, counting the
view box's ``sigSelectedRegion``:

====================================================  ================
item                                                  region signals
====================================================  ================
bare lane                                             1
``QGraphicsRectItem``, no mouse buttons               1
``QGraphicsRectItem``, ``Qt.MouseButton.LeftButton``  1
``pg.ScatterPlotItem``, default buttons               1
``pg.RectROI(movable=True)``                          **0**
``pg.RectROI(movable=False)``                         1
``pg.RectROI(movable=False)`` + grips                 1
====================================================  ================

The movable ROI took the drag and moved *itself*, from (1.0 s, 1000.0 Hz) to
(2.397 s, 2220.3 Hz).  That is the whole reason stored labels are not ROIs:
a lane full of them would cost the ability to start a new box inside an
existing one.

The last two rows are what made editing possible, and they were measured
three times over to be sure of them.  ``movable=False`` clears
`pg.ROI.translatable`, and that flag is what `ROI.hoverEvent` tests before
calling ``ev.acceptDrags(LeftButton)`` and what `MouseDragHandler` tests
before taking a drag: without it the ROI's *body* pre-claims nothing and
ignores the drag, which then reaches the view box exactly as over a bare
lane.  Its **grips still work**, because `Handle.hoverEvent` accepts left
drags unconditionally and is its own item -- measured, a corner grip resized
a box from 2.000 s x 2000.0 Hz to 1.741 s x 1322.0 Hz and a centre grip moved
one from (1.000 s, 1000.0 Hz) to (1.346 s, 1508.5 Hz), each emitting
``sigRegionChangeFinished`` once.

So: every stored label stays a plain item, and the **one** label the reader
has selected grows a `LabelEditor` over the top of it.  A drag that starts
inside the selected box still draws a new box; only the 12 px grips are
control.

Two more measurements the editor leans on:

* ``invertible=False``, which `pg.ROI` defaults to, is what keeps
  ``t1 >= t0`` and ``f1 >= f0``: a corner grip dragged well past the
  opposite corner stopped at 0.599 s x 576.3 Hz, where the same drag with
  ``invertible=True`` gave -0.802 s x -813.6 Hz.
* ``setPos`` / ``setSize`` with ``finish=False`` emit ``sigRegionChanged``
  but **not** ``sigRegionChangeFinished`` -- 0 after two such calls, 1 after
  one ``finish=True``.  That is what lets the editor be re-synced from the
  store on every frame without the sync reading back as an edit.

The stored items are passive (`_passive`) for the reason `eventoverlay`
gives: a mark states where something is and is not a control, so it has no
business claiming hover or clicks.  On these two item types that is hygiene
rather than what keeps the drag alive -- pyqtgraph dispatches drags, clicks
and hovers by ``hasattr(item, 'mouseDragEvent')`` and never consults
``acceptedMouseButtons``, and a `QGraphicsRectItem` has none of those
methods.

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
from PySide6.QtCore import QAbstractTableModel, QModelIndex, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QIconEngine
from PySide6.QtWidgets import (
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
from .eventoverlay import (
    NAV_REGION_Z,
    SURFACE_NAVIGATOR,
    SURFACE_SPECTROGRAM,
    SURFACE_TRACE,
)
from .labels import KIND_SPAN, KINDS, LabelCategory, LabelSet

#: Above `eventoverlay.MARK_Z` (15) and below the crosshair and the playback
#: marker (100).  A label is the thing being authored right now; an
#: annotation that crossed over it would hide the box the reader just drew.
LABEL_Z = 25

#: What a label costs on the navigator, where `LABEL_Z` is not enough.
#:
#: That strip carries the window-selection region, a translucent
#: `pg.LinearRegionItem` at `NAV_REGION_Z` whose brush is (76, 141, 255, 46),
#: so anything below it is painted *through* it -- and precisely inside the
#: stretch of session the reader is working in.  Measured on a four channel
#: recording, the lanes zoomed to the first second so the region covers a
#: quarter of the strip, sampling a box's vertical edge at mid row:
#:
#: ==================  ==================  ==================
#: z of the box        inside the region   outside it
#: ==================  ==================  ==================
#: `LABEL_Z` (25)      (223, 113, 134)     (255, 107, 107)
#: `LABEL_NAV_Z` (65)  (255, 107, 107)     (255, 107, 107)
#: ==================  ==================  ==================
#:
#: (255, 107, 107) is the category's own colour.  The row ground itself goes
#: (13, 18, 25) to (25, 40, 66) under the region, so the wash is real and it
#: is the box that has to clear it.  `eventoverlay.NAV_MARK_Z` buys a fixed
#: label the same thing; this stays above it, which is the order the two
#: kinds of mark have everywhere else.
LABEL_NAV_Z = NAV_REGION_Z + 15

#: The selected label, above every unselected one and still under the cross
#: hair.  `pg.ROI` defaults to 10, which puts it *below* the stored boxes at
#: `LABEL_Z` and below the annotation marks at 15-17: the one box the reader
#: is working on would be the one drawn underneath everything.
LABEL_EDIT_Z = 30

#: Half-side of a grip, in device pixels, which is also its grab radius.
#:
#: Measured by pressing at increasing distances from a grip's centre and
#: asking whether the ROI moved: ``handleSize`` 5, 8 and 10 grabbed out to
#: exactly 5, 8 and 10 px.  So the target is ``2 * GRIP_PX`` across, and 6
#: makes that 12 px -- the same 12 px `POINT_SYMBOL_PX` gives a point label,
#: and small enough to leave the body of a short box draggable for a new one.
#: It has to stay findable on a sixteen channel stack, where a trace lane is
#: 30 px tall, and on a 14 inch panel at 150% scale.
#:
#: A box shorter than ``2 * GRIP_PX`` has its corner grips overlapping the
#: middle one, and there is no attempt to thin them out.  Measured on a
#: 118 px lane at bands of 30, 16, 8 and 4 device pixels, a press on the
#: middle grip moved the box every time and resized it none -- so a thin
#: mark can always be slid, and its edges are reached by zooming the
#: frequency axis, which is what a reader working at that scale is doing.
GRIP_PX = theme.S6

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
    item flipped to ``Qt.MouseButton.LeftButton`` and a scatter with
    pyqtgraph's default buttons both let a drag through unchanged when it was
    measured.  See the module docstring for the one item that did not.
    """
    item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
    item.setAcceptHoverEvents(False)


class LabelEditor(pg.ROI):
    """The grips on the one editable label a reader has selected.

    A `pg.ROI` with ``movable=False``, which is the whole trick: the body
    passes a drag straight through to the view box, so a new box can still be
    started inside this one, while the grips -- separate items, which accept
    left drags whatever the ROI does -- move and resize it.  The measured
    table is in this module's docstring.

    Which grips it has is decided by what the label *is*, not by what the
    surface could show:

    * a **span with a band** (a spectrogram label, on a spectrogram) gets
      four corner grips and a centre grip: time and frequency, and move.
    * a **span with no band** -- every label on a trace, and a
      trace-made label seen on a spectrogram -- gets two grips on the
      vertical edges and a centre grip.  Both are placed at half height, and
      a `pg.ROI` scale handle whose centre shares its y **cannot move in y**:
      measured, dragging one 100 px left and 30 px up changed the width from
      2.000 s to 1.568 s and left the height at 0.2458974853515625, to the
      last bit.  A trace's y axis is amplitude and a box drawn there claims
      no frequency; a grip that could write one would be a claim nobody made.
    * a **point** gets the centre grip alone.  It has no extent to resize,
      and its ROI is a zero-sized one sitting exactly on it, so the grip is
      the only thing of it that is drawn -- the point's own ``+`` or hairline
      is still drawn by the overlay underneath.

    Never `removable`.  `pg.ROI`'s own context menu survives
    `theme.strip_pg_menus` and would offer a "Remove ROI" entry, in a word
    this application does not use for a thing it does not call an ROI.
    Deletion is `DataBrowser.delete_selected_label`.

    ``rotatable`` and ``resizable`` are off for the same reason ``movable``
    is, and they are not decoration.  `pg.MouseDragHandler` reads all three
    and is still reached for a drag on the body once the hover claim is
    gone, so it is the *modifier* that picks a drag mode.  Measured, one
    drag from (1.3 s, 1400 Hz) to (2.7 s, 2600 Hz) inside a box at
    (1.0 s, 1000.0 Hz) sized 2.000 s x 2000.0 Hz:

    ==================  ==============  ==============  ==================
    held down           bare lane       ``movable=      all three off
                                        False`` only
    ==================  ==============  ==============  ==================
    nothing             1               1               1
    Shift (play)        1               **0**           1
    Alt (analyse)       1               **0**           1
    Ctrl                1               1               1
    ==================  ==============  ==============  ==================

    In the two zeros the box took the gesture: Shift scaled it to
    2.862 s x 2861.5 Hz and Alt **rotated** it to -161.5 degrees, which is a
    label geometry that cannot be written down.  Shift+drag and Alt+drag are
    the two modified drags `Audian.region_mode_for_modifiers` binds, so both
    would have been lost over the selected box.  With all three flags off
    the handler reaches its ``else`` and ignores, under every modifier.
    Grips are unaffected: `pg.ROI.movePoint` reads neither flag.
    """

    def __init__(self, label, pos, size, color: int):
        super().__init__(
            pos,
            size,
            movable=False,
            rotatable=False,
            resizable=False,
            removable=False,
            invertible=False,
            pen=theme.pen(theme.marker_color(color), theme.LW_SELECTED),
            handlePen=theme.handle_pen(),
            handleHoverPen=theme.handle_pen(),
        )
        #: the label these grips edit, held by identity
        self.label = label
        self.setZValue(LABEL_EDIT_Z)
        self.handleSize = GRIP_PX
        #: True while `sync` is writing, so the ROI's own change signals can
        #: tell a re-sync from the reader's hand.  Without it every frame
        #: that repositions the grips would read back as an edit and write
        #: the label to disk again.
        self.syncing = False
        #: True between the first move of a drag and its end.  `pg.ROI` has
        #: no flag for this: `ROI.isMoving` is set only for a drag on the
        #: *body*, which this ROI never takes, and a grip drag sets
        #: `Handle.isMoving` on the grip instead.
        self.dragging = False
        #: the size `sync` last wrote, which is what `resized` compares to
        self.synced_size = tuple(self.size())

    def build_grips(self, bounded: bool) -> None:
        """Add the grips this label's kind and surface allow."""
        if self.label.is_point():
            self.addTranslateHandle([0.5, 0.5])
            return
        if bounded:
            for x, y in ((0, 0), (1, 0), (0, 1), (1, 1)):
                self.addScaleHandle([x, y], [1 - x, 1 - y])
        else:
            # centre shares the grip's y, which is what pins the height
            self.addScaleHandle([1, 0.5], [0, 0.5])
            self.addScaleHandle([0, 0.5], [1, 0.5])
        self.addTranslateHandle([0.5, 0.5])

    def sync(self, pos, size) -> None:
        """Put the grips back where the store says, without that being an edit.

        ``finish=False`` is load-bearing: measured, ``setPos`` and ``setSize``
        with it emit ``sigRegionChanged`` but not
        ``sigRegionChangeFinished``, so only the reader's own drag ever
        reaches the write-back.  The `syncing` flag covers the other signal.
        """
        self.syncing = True
        try:
            self.setPos(pg.Point(*pos), finish=False)
            self.setSize(pg.Point(*size), finish=False)
        finally:
            self.syncing = False
        self.synced_size = tuple(self.size())

    def resized(self) -> bool:
        """Whether the last drag changed the box's extent rather than moving it.

        Exact, and it has to be.  `pg.ROI.translate` writes ``state['pos']``
        and never touches ``state['size']``, so after a move the size is the
        one `sync` last set, bit for bit; after a grip resize it is not.
        Comparing the *extent* to the store's instead would go through
        ``pos + size - pos`` and land a few ulps out, and this decides which
        end of a box may be moved when it runs off the lane.
        """
        return tuple(self.size()) != self.synced_size

    def repen(self, color: int) -> None:
        """Take the palette's current colours, if they are not already on.

        Called from `LabelOverlay._sync_editor`, which is to say on every
        pass that redraws the lane, so it covers a live theme switch *and*
        the reader giving the category a different palette index in the
        editor -- two doors onto the same failure, and the second one does
        not go through `polish` at all.  It compares first because the pens
        it would otherwise rebuild are rebuilt on every frame.

        The pooled boxes get this for free -- `LabelOverlay._draw` builds a
        pen for every one of them on every pass -- but the editor is a
        long-lived item whose pens were made once.  Measured across a dark to
        light switch: the pooled box went from ``#ff6b6b`` to ``#c0392b``
        while the editor's outline stayed ``#ff6b6b`` and its grips stayed
        ``#4c8dff``, so the box the reader is working on was the one drawn in
        the wrong theme.

        The grips are separate items and each holds its own copy, so
        ``handlePen`` alone reaches only grips added later.
        """
        outline = theme.pen(theme.marker_color(color), theme.LW_SELECTED)
        grip = theme.handle_pen()
        if (
            outline.color() == self.pen.color()
            and self.handles
            and grip.color() == self.handles[0]["item"].pen.color()
        ):
            return
        self.setPen(outline)
        self.handlePen = grip
        self.handleHoverPen = theme.handle_pen()
        for handle in self.handles:
            item = handle["item"]
            item.pen = self.handlePen
            item.hoverPen = self.handleHoverPen
            item.currentPen = self.handlePen
            item.update()

    def region(self) -> tuple:
        """``(t0, t1, y0, y1)`` of the grips, in data coordinates."""
        pos, size = self.pos(), self.size()
        return (
            float(pos.x()),
            float(pos.x()) + float(size.x()),
            float(pos.y()),
            float(pos.y()) + float(size.y()),
        )


class LabelOverlay:
    """The hand-made labels of one plot.

    One per trace or spectrogram plot, built once when the stack is built and
    costing nothing until a label exists.
    """

    def __init__(
        self,
        plot,
        store: LabelSet,
        surface: str = SURFACE_TRACE,
        on_edit=None,
        on_dropped=None,
    ):
        self.plot = plot
        self.store = store
        self.surface = surface
        #: called ``(overlay, label, t0, t1, f0, f1, resized)`` when a grip
        #: drag ends.
        #: A callback rather than a reference to the browser, for the reason
        #: this module exists: nothing here decides what a label means, and
        #: writing to the store, saving and telling the reader are all
        #: decisions.  `LabelTable` takes its `on_change` the same way.
        self.on_edit = on_edit
        #: called ``(overlay)`` when the grips come off because the label
        #: they were on has left the store, so the owner of the selection
        #: cannot be left holding a row that is gone
        self.on_dropped = on_dropped
        #: the grips on the selected label, when the selected label is on
        #: this plot.  There is at most one in the whole window.
        self.editor: Optional[LabelEditor] = None
        #: whether frequency bounds mean anything on this surface.  A trace's
        #: y axis is amplitude, so a box drawn there is bounded in time only
        #: -- and a label that carries a frequency is still drawn full height
        #: on it rather than at some amplitude it never claimed.
        self.has_frequency = surface == SURFACE_SPECTROGRAM
        #: Whether a label on this plot can be picked up and dragged.
        #:
        #: Not on the navigator.  That strip is a map of the whole session
        #: rather than a surface anything is drawn on: at 3621 s across
        #: 3840 px a one second label is a pixel wide, so a grip there would
        #: be a control aimed at something too small to aim at, and the row
        #: already belongs to the window-selection region.  It shows the
        #: labels; it does not edit them.
        self.editable = surface != SURFACE_NAVIGATOR
        #: z of the marks, which the navigator has to buy separately -- see
        #: `LABEL_NAV_Z`
        self.z = LABEL_NAV_Z if surface == SURFACE_NAVIGATOR else LABEL_Z
        #: master switch, driven from the parameter bar
        self.visible = True
        self.boxes: list[QGraphicsRectItem] = []
        self._boxes_live = 0
        self.points = pg.ScatterPlotItem(
            symbol="+", size=POINT_SYMBOL_PX, pxMode=True, hoverable=False
        )
        self.points.setZValue(self.z)
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
        item.setZValue(self.z)
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
        """Re-read the palette after a live theme switch.

        The editor is re-penned here rather than in `_draw`, which is where
        the pooled boxes get theirs: `update_plot` returns early on a lane
        that is hidden, and nothing repaints that lane when it comes back.
        """
        if self.editor is not None:
            self.editor.repen(self.store.color_of(self.editor.label.category))
        self._drawn = None
        self.update_plot()

    def clear(self) -> None:
        for item in self.boxes:
            item.setVisible(False)
        self._boxes_live = 0
        if not self._points_blank:
            self.points.setData([], [])
            self._points_blank = True

    # --- the selected label -----------------------------------------------

    def editing(self):
        """The label whose grips are on this plot, or None."""
        return None if self.editor is None else self.editor.label

    def pick(self, t: float, y: float, view) -> Optional[object]:
        """The label under ``(t, y)`` on this plot, or None.

        In data coordinates against the store, never against the items: a
        stored label is a plain `QGraphicsRectItem` with no
        ``mouseClickEvent``, so pyqtgraph never offers it a click at all, and
        the query `LabelSet.window` already answers is the same one `_draw`
        asks.  What can be picked is therefore exactly what is on screen --
        including, on the mean spectrogram, the labels of every channel it
        averages.

        **The smallest box wins.**  A reader labelling densely draws a box
        inside a box, and the inner one is the one they are pointing at; the
        outer is still reachable by its own edge.  Smallest is measured as a
        fraction of the *view*, because a box 0.1 s tall in frequency and one
        2 s long are not comparable in their own units.  A point has no
        extent at all and so always wins over whatever it sits in.  Ties go
        to the label added last, which is the one drawn on top.
        """
        tol = GRIP_PX * float(view.viewPixelSize()[0])
        (t0v, t1v), (y0v, y1v) = view.viewRange()
        span = max(t1v - t0v, 1e-12)
        height = max(y1v - y0v, 1e-12)
        ytol = GRIP_PX * float(view.viewPixelSize()[1])
        best = None
        best_area = None
        for _index, label in self.store.window(t - tol, t + tol, self.channels()):
            bounded = self.has_frequency and label.has_frequency()
            if bounded and not (label.f0 - ytol <= y <= label.f1 + ytol):
                continue
            width = 0.0 if label.is_point() else (label.t_end() - label.t0) / span
            band = (label.f1 - label.f0) / height if bounded else 1.0
            area = width * band
            if best_area is None or area <= best_area:
                best, best_area = label, area
        return best

    def start_editing(self, label) -> bool:
        """Put grips on `label`.  False when this plot cannot carry them.

        The answer is the caller's to act on: it holds the selection, and a
        selection whose grips were never built is the one way the two can
        disagree.
        """
        self.stop_editing()
        if not self.editable:
            return False
        bounded = self.has_frequency and label.has_frequency()
        view = self.plot.getViewBox()
        if view is None:
            return False
        (_t0v, _t1v), (y0, y1) = view.viewRange()
        pos, size = self._editor_box(label, bounded, y0, y1)
        editor = LabelEditor(label, pos, size, self.store.color_of(label.category))
        editor.build_grips(bounded)
        self.plot.addItem(editor, ignoreBounds=True)
        editor.sigRegionChanged.connect(self._grips_moved)
        editor.sigRegionChangeFinished.connect(self._grips_released)
        self.editor = editor
        return True

    def stop_editing(self) -> None:
        """Take the grips off, if this plot has them."""
        editor = self.editor
        self.editor = None
        if editor is None:
            return
        editor.sigRegionChanged.disconnect(self._grips_moved)
        editor.sigRegionChangeFinished.disconnect(self._grips_released)
        self.plot.removeItem(editor)

    def _editor_box(self, label, bounded: bool, y0, y1) -> tuple:
        """``(pos, size)`` in data coordinates for `label`'s grips.

        **Exactly the label, never a pixel more.**  `_draw` gives a very
        narrow box a two pixel minimum so it stays visible, and it may,
        because nothing reads a drawn rect back.  Doing the same here would:
        the width the grips have is the width `_grips_released` reports, so a
        box narrower than the minimum would be *widened to it* by a reader
        who only meant to slide it along.  Zoomed out far enough the grips
        do pile up on one another -- and zooming in is what a reader working
        on a mark that small is doing anyway.

        A point gets a zero-sized box sitting exactly on it: there is nothing
        to resize, the centre grip is drawn in device pixels and is therefore
        the only visible part of it, and the point's own ``+`` or hairline is
        still drawn underneath by `_draw`.
        """
        if label.is_point():
            y = 0.5 * (label.f0 + label.f1) if bounded else 0.5 * (y0 + y1)
            return (label.t0, y), (0.0, 0.0)
        width = label.t_end() - label.t0
        if bounded:
            return (label.t0, label.f0), (width, label.f1 - label.f0)
        return (label.t0, y0), (width, y1 - y0)

    def _grips_moved(self, _editor=None) -> None:
        if self.editor is not None and not self.editor.syncing:
            self.editor.dragging = True

    def _grips_released(self, _editor=None) -> None:
        """One grip drag has ended: hand the new geometry to the browser.

        **A lane may only change what it can show.**  Three cases, and the
        third is the one that costs data if it is got wrong:

        * a spectrogram lane, and a label with a band: time and frequency
          both come off the grips.
        * a spectrogram lane, and a label with no band -- one drawn on a
          trace, which is drawn full height here: time comes off the grips
          and the frequency stays absent.  The grips' y is the lane's own
          height; writing it would hand a box drawn over a waveform a band
          nobody claimed, and ``0..Nyquist`` at that.
        * **a trace lane, and a label with a band.**  The box is drawn full
          height there too, so the grips' y is again the lane's -- but this
          label *has* a frequency and it is not the trace's to change.  The
          label's own band is passed straight back through.  Reading the
          lane instead would erase the band of any label a reader happened
          to nudge from the waveform, which is the half of the label that
          says which signal it is.
        """
        editor = self.editor
        if editor is None or editor.syncing:
            return
        editor.dragging = False
        if self.on_edit is None:
            return
        t0, t1, y0, y1 = editor.region()
        label = editor.label
        if self.has_frequency and label.has_frequency():
            if label.is_point():
                # a point's grip sits where `_draw` puts its mark, at the
                # middle of whatever band it carries -- normally none at all,
                # since `store_label` writes f0 == f1, but the sidecar is
                # hand-editable and a band read out of one is not this
                # gesture's to close up
                half = 0.5 * (label.f1 - label.f0)
                f0, f1 = y0 - half, y0 + half
            else:
                f0, f1 = y0, y1
        else:
            f0, f1 = label.f0, label.f1
        self.on_edit(
            self,
            label,
            t0,
            None if label.is_point() else t1,
            f0,
            f1,
            editor.resized(),
        )

    def _sync_editor(self, y0, y1, view) -> None:
        """Put the grips back on the label, after anything moved either.

        Skipped while the reader is dragging one, which is the only time the
        grips are ahead of the store rather than behind it.
        """
        editor = self.editor
        if editor is None or editor.dragging:
            return
        if self.store.index_of(editor.label) < 0:
            # the label went while it was selected, so the item must not
            # outlive its row.  The owner is told rather than left to notice:
            # every caller happens to revalidate its own selection first, and
            # nothing makes them.
            self.stop_editing()
            if self.on_dropped is not None:
                self.on_dropped(self)
            return
        editor.repen(self.store.color_of(editor.label.category))
        bounded = self.has_frequency and editor.label.has_frequency()
        pos, size = self._editor_box(editor.label, bounded, y0, y1)
        editor.sync(pos, size)

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
        # after the boxes, so the grips are put on a label the store has
        # already been read for, and on the y range the lane really has
        self._sync_editor(y0, y1, view)

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

    def paint(self, painter, rect, mode=QIcon.Mode.Normal, state=QIcon.State.Off):
        painter.setBrush(theme.qcolor(theme.MARKER_ICON_BG))
        painter.setPen(theme.pen(theme.MARKER_ICON_RING))
        painter.drawRect(rect.adjusted(0, 0, -1, -1))
        painter.setBrush(QColor(theme.marker_color(self.index)))
        painter.setPen(Qt.PenStyle.NoPen)
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
        editor.setCurrentText(index.model().data(index, Qt.ItemDataRole.EditRole))

    def setModelData(self, editor, model, index):
        model.setData(index, editor.currentText(), Qt.ItemDataRole.EditRole)

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
        editor.setCurrentText(str(index.model().data(index, Qt.ItemDataRole.EditRole)))

    def setModelData(self, editor, model, index):
        model.setData(index, editor.currentText(), Qt.ItemDataRole.EditRole)

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

    def headerData(self, index, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.HEADER[index]
        if orientation == Qt.Orientation.Vertical and role == Qt.ItemDataRole.DisplayRole:
            return f"{index}"
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = self.rows[index.row()]
        column = index.column()
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            if column == 0:
                return row.name
            if column == 1:
                return row.kind
            return str(row.color)
        if role == Qt.ItemDataRole.DecorationRole and column == 2:
            return swatch_icon(row.color)
        if role == Qt.ItemDataRole.ToolTipRole and column == 0:
            count = self.store.count_in(row.name)
            return f"{count} label{'' if count == 1 else 's'} in this recording"
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        return None

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsEditable

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole) -> bool:
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
                QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
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
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(theme.S12, theme.S12, theme.S12, theme.S12)
        outer.setSpacing(theme.S8)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        outer.addLayout(row)
        self.view = QTableView(self)
        self.view.setModel(self.model)
        self.view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
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
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok, self)
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

    def headerData(self, index, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.HEADER[index]
        if orientation == Qt.Orientation.Vertical and role == Qt.ItemDataRole.DisplayRole:
            return f"{index}"
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        label = self.store.labels[index.row()]
        column = index.column()
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
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
        if role == Qt.ItemDataRole.DecorationRole and column == 0:
            return swatch_icon(self.store.color_of(label.category))
        if role == Qt.ItemDataRole.TextAlignmentRole:
            if column in (0, 1, 7):
                return Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        return None

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        flags = Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
        # only the note: everything else is geometry the mouse put there, and
        # a typo in a frequency is a label that moves without being redrawn
        if index.column() == len(self.HEADER) - 1:
            flags |= Qt.ItemFlag.ItemIsEditable
        return flags

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole) -> bool:
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
        if len(rows) > 1:
            # `LabelSet.remove` records an undo for each row it drops, so the
            # slot would hold the last of them: Shift+B would put one row of
            # several back and read as the whole change having been taken
            # back.  A removal of five is not one change to undo.
            self.store.forget_undo()
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
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(theme.S12, theme.S12, theme.S12, theme.S12)
        outer.setSpacing(theme.S8)
        self.view = QTableView(self)
        self.view.setModel(self.model)
        self.view.setFont(theme.font_mono())
        self.view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.view.resizeColumnsToContents()
        self.view.horizontalHeader().setStretchLastSection(True)
        outer.addWidget(self.view)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        remove = box.addButton("&Remove", QDialogButtonBox.ButtonRole.DestructiveRole)
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
    chip.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
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
        self.more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.more.setVisible(False)
        self.menu = QMenu(self)
        self.more.setMenu(self.menu)
        # Ignored: the strip takes the width the column has and never asks
        # for more.  Fixed height, because the number of lines is chosen here
        # and must not follow the number of categories.
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
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
