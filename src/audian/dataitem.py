"""The one thing a plot item still owes the data layer: is it on screen?

`BufferedData` used to hold the live `QGraphicsItem`s themselves, in a
`plot_items` list, and ask each of them `isVisible()` whenever it needed to
know whether recomputing a trace was worth it.  That is the whole of what
coupled the data and DSP layer to the GUI -- and it is not a coupling that
survives moving the recompute off the GUI thread, because `isVisible()` may
only be called from the thread that owns the widget.

So the direction is inverted.  The item pushes its *effective* visibility
into a plain `numpy` bool array on the trace, and the data layer reads only
that.  Effective is the operative word: `QGraphicsItem.isVisible()` is false
whenever any ancestor is hidden, so hiding a whole panel or a whole lane has
to reach the flag too.  It does -- Qt delivers `ItemVisibleHasChanged` to
every descendant of an item whose visibility changed, and on reparenting
into an already-hidden plot -- which is why the mirror is exact rather than
approximate, and why it needs no bookkeeping at the call sites that hide
things.
"""

from PySide6.QtWidgets import QGraphicsItem


class VisibleChannelMirror:
    """Mixin for a plot item that draws one channel of one trace.

    Expects `self.data` (the trace) and `self.channel` to be set before the
    item can become visible.  Mix it in *before* the pyqtgraph base class so
    that `itemChange` is cooperative.
    """

    def mirror_visibility(self) -> None:
        """Write this item's effective visibility into the trace's flags."""
        data = getattr(self, "data", None)
        flags = getattr(data, "visible_channels", None)
        channel = getattr(self, "channel", -1)
        if flags is None or channel < 0 or channel >= len(flags):
            return
        flags[channel] = self.isVisible()

    def itemChange(self, change, value):
        ret = super().itemChange(change, value)
        if change == QGraphicsItem.GraphicsItemChange.ItemVisibleHasChanged:
            self.mirror_visibility()
        return ret
