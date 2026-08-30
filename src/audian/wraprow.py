"""A row of chips that wraps onto another line instead of asking for width.

`pack_row`: the line-breaking arithmetic, shared.
`class WrapRow`: a container that grows downwards as it is narrowed.

The parameter bar does not wrap.  Every chip row in it is a plain
`QHBoxLayout` with a trailing stretch, so a row that outgrows its column
widens the whole application instead -- measured, the two annotation chip
rows want 696 px and 555 px with a session bundle loaded, and the surface
row 326.  In a bar as wide as the window that was affordable.  In a side
panel it is not.

`labeloverlay.CategoryStrip` is the one control in the bar that already
survives an arbitrary width, and it does it by placing its chips *by hand*
rather than through a layout -- a layout would re-impose exactly the
minimum width it exists to avoid.  That packing is what is lifted here, so
that there is one line-breaker in this application and not two.

What is deliberately *not* lifted is the fold.  `CategoryStrip` is two
fixed lines and puts the overflow in a ``+N`` menu, because in a bottom bar
height was the scarce axis and a third line came off every lane in the
stack.  A side panel inverts that: height is what the panel has and width
is what it lacks.  So this row grows downwards and folds nothing -- and it
*must* fold nothing, because the annotation chips are the legend as well as
the switch, and a layer whose chip is in a menu has no colour anybody can
read off.

Measured on PySide6 6.11.2, the six chips of the `Sent` row: one line at
500 px, two at 400, three at 320, four at 220, five at 180, and the height
is exactly ``lines * CHIP_HEIGHT + (lines - 1) * VGAP`` at every one.  A
`QGridLayout` above one of these follows by itself, because the geometry it
hands out comes from `heightForWidth` -- which is exact here, and is the
only number about this widget that is.

`sizeHint` is *not* exact, deliberately: it is one line, the size the row
would prefer, and it stays that whatever width the row currently has.  A
hint that tracked the current width was written first and is a trap -- it
disagrees with itself either side of the first layout pass, and a row that
had never been sized answered with the 142 px of every chip on its own
line.

The consequence is that anything summing *hints* under-reports a wrapped
row, and two callers had to be told: `ParameterGroup.frame_height`, which
is what `equalize` freezes every frame to, and the panel's own scroll area,
which has to ask `layout().heightForWidth()` rather than trust the summed
hints.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QSize
from PySide6.QtWidgets import QSizePolicy, QWidget

from . import theme


def pack_row(
    items,
    budget: int,
    spacing: int,
    rows: int | None = None,
    reserve: int = 0,
):
    """Place ``(key, width)`` pairs over lines `budget` pixels wide.

    Returns ``(placements, leftover)``, a placement being
    ``(key, x, line, width)``.

    `rows` bounds the number of lines.  ``None`` means unbounded, and then
    nothing is ever left over: an item wider than the budget is placed on a
    line of its own and allowed to overhang, because a caller that asked
    for unbounded lines has said it would rather overflow than drop
    something.

    A *bounded* packer stops at the first item that will not go, which is
    what keeps the shown set a **prefix**: `CategoryStrip` shows the first
    nine categories because those are the ones carrying the digit keys, and
    a strip that hid the third to show the fourth would put the chips out
    of step with the keys under them.

    `reserve` is width held back on the last line, so a fold marker can
    never itself be the thing that does not fit.
    """
    placements = []
    line = 0
    x = 0
    for index, (key, width) in enumerate(items):
        while True:
            last = rows is not None and line >= rows - 1
            room = budget - (reserve if last else 0)
            if x + width <= room or (x == 0 and rows is None):
                placements.append((key, x, line, width))
                x += width + spacing
                break
            if x == 0 or last:
                return placements, [k for k, _w in items[index:]]
            line += 1
            x = 0
    return placements, []


class WrapRow(QWidget):
    """A row of chips that takes another line rather than more width.

    Hand-placed, like `CategoryStrip`, and for the same reason: a layout
    would publish a minimum width of its own and the row would once again
    be able to widen the window.  This one asks for nothing -- `Ignored`
    horizontally, a size hint one chip wide -- and answers `heightForWidth`
    with the lines its contents really need at that width.
    """

    #: Gap between chips on a line.
    SPACING = theme.S4
    #: Gap between lines.
    VGAP = theme.S2

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._items: list[QWidget] = []
        self._lines = 1
        # Ignored: the row takes the width its column has and never asks
        # for more.  Preferred vertically, with heightForWidth set, which
        # is how the line count reaches the layout above it.
        policy = QSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    # --- contents ---------------------------------------------------------

    def widgets(self) -> list:
        """The chips on this row, in the order they were added."""
        return list(self._items)

    def add_widget(self, widget: QWidget) -> QWidget:
        """Append one chip, and return it."""
        widget.setParent(self)
        widget.setVisible(True)
        self._items.append(widget)
        self.relayout()
        self.updateGeometry()
        return widget

    def remove_widget(self, widget: QWidget) -> None:
        """Take one chip off the row.  Disposing of it is the caller's."""
        if widget in self._items:
            self._items.remove(widget)
            self.relayout()
            self.updateGeometry()

    # --- geometry ---------------------------------------------------------

    @staticmethod
    def chip_height(widget: QWidget) -> int:
        """The height `widget` will really be given.

        Its size hint clamped into its own minimum and maximum, because a
        chip is `setFixedHeight(theme.CHIP_HEIGHT)` = 22 and *hints* 27:
        laying the row out on the hint leaves five pixels of gap under
        every line and makes `heightForWidth` over-report by five per line.
        """
        height = max(widget.sizeHint().height(), widget.minimumHeight())
        return min(height, widget.maximumHeight())

    def line_height(self) -> int:
        """One line: the tallest chip on it, or an empty chip's worth."""
        heights = [self.chip_height(w) for w in self._items if not w.isHidden()]
        return max(heights) if heights else theme.CHIP_HEIGHT

    def measured(self, width: int):
        """``(placements, lines)`` for the chips at `width` pixels.

        Zero lines when the row is empty, which is not the same as one: the
        `Heard` chip row measures 0x0 with no bundle loaded, and a row that
        claimed a chip's height while holding nothing would be an
        unexplained gap under its caption.
        """
        items = [(w, w.sizeHint().width()) for w in self._items if not w.isHidden()]
        if not items:
            return [], 0
        placements, _leftover = pack_row(items, max(width, 1), self.SPACING)
        return placements, max(line for _k, _x, line, _w in placements) + 1

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        _placements, lines = self.measured(width)
        if lines <= 0:
            return 0
        return lines * self.line_height() + (lines - 1) * self.VGAP

    def sizeHint(self) -> QSize:
        """One line: the height this row would like, not the height it has.

        A size hint is the *preferred* size, and what a row of chips
        prefers is to be on one line.  The height it actually gets comes
        from `heightForWidth`, which the parent `QGridLayout` calls with
        the width it has decided on -- measured, that is what puts the row
        at 22, 46, 70 or 94 px as the panel narrows.

        The width is `S24`, and saying "I would like the 696 px one line
        costs" was tried and does nothing: `qSmartSizeHint` zeroes the
        width of a hint whose horizontal policy is `Ignored`, which this
        one's is, so the number never reaches the layout -- and `Ignored`
        is what keeps the row from ever setting a floor, so it stays.  The
        consequence is that `QGridLayout.totalSizeHint()` runs its own
        height-for-width pass at a preferred width narrow enough to wrap;
        see `ParameterGroup.frame_height`, which is where that is dealt
        with rather than papered over here.
        """
        return QSize(theme.S24, self.line_height() if self._items else 0)

    def minimumSizeHint(self) -> QSize:
        # A chip's worth of nothing, the way `CategoryStrip` does it: this
        # row can never be the term that sets the window's minimum width,
        # however many chips the reader's own data puts in it.
        #
        # The price is that a chip wider than the row overhangs rather than
        # folding.  Measured, that does not happen: the widest chip in the
        # application is `Resting pulses` at 159 px, against the 188 px a
        # group has inside a panel at its 220 px floor.  The alternative --
        # publishing the widest chip as a minimum -- is how the bar's
        # minimum reached 2456 px once, and is not worth re-buying.
        return QSize(theme.S24, self.line_height() if self._items else 0)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.relayout()

    def event(self, event) -> bool:
        """Re-place the chips when one of them changes size on its own.

        A chip is placed at its `sizeHint`, and a chip whose *text* changes
        afterwards has a different one -- the annotation source line is
        whatever the bundle called itself and is set long after the row was
        built.  A layout would hear about that by itself; a row that places
        by hand has to ask.

        `QWidget.updateGeometry` on a child posts a `LayoutRequest` to its
        parent, which is this, so that is the hook.  Without it the source
        line came out as the two characters it had room for when the row
        was empty, and only a later resize -- which is not guaranteed to
        happen -- put it right.
        """
        if event.type() == QEvent.Type.LayoutRequest:
            self.relayout()
        return super().event(event)

    def relayout(self) -> None:
        """Place every chip, and tell the layout above if the count moved."""
        placements, lines = self.measured(self.width())
        height = self.line_height()
        pitch = height + self.VGAP
        for widget, x, line, width in placements:
            widget.setGeometry(x, line * pitch, width, height)
        if lines != self._lines:
            self._lines = lines
            # Only on a change: updateGeometry() is a request to re-lay-out
            # the parent, and one per resize event would be a loop.
            self.updateGeometry()
