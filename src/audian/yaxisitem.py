"""Vertical axis with tick spacing tuned for dense multi-channel layouts."""

from math import ceil, floor, log10

import numpy as np
import pyqtgraph as pg
from PyQt5.QtCore import Qt

from . import theme


class YAxisItem(pg.AxisItem):
    """Linear y axis with a capped number of major ticks.

    With 16 channels a trace row is about 60 px high.  More than three tick
    values in that space is noise, so the number of major ticks is capped by
    `max_major_ticks` instead of being driven by the available height alone.

    The axis can also carry an SI unit (`set_si_unit()`) that keeps working
    when the rotated axis label is hidden.  pyqtgraph ties automatic SI
    prefixing to the visibility of that label, but the dense layout replaces
    the label with an in-plot caption -- without this override a frequency axis
    would print 10000 instead of 10 with a 'kHz' caption.
    """

    def __init__(self, *args, max_major_ticks: int = 3, **kwargs):
        # must exist before AxisItem.__init__ runs, it updates the SI prefix
        # and may already ask for tick spacings:
        self._si_unit = ""
        self.si_prefix = ""
        self.max_major_ticks = max(2, int(max_major_ticks))
        #: called with no arguments when the axis is double clicked; see
        #: `set_reset`
        self._on_reset = None
        super().__init__(*args, **kwargs)
        theme.style_axis(self)

    def apply_theme(self) -> None:
        """Re-apply the theme to this axis (idempotent)."""
        theme.style_axis(self)

    def set_reset(self, callback) -> None:
        """What a double click on this axis does, or None for nothing.

        The axis is handed a callback rather than a browser: it is the piece
        that knows a double click happened and nothing else, and which range
        that is and how to put it back are the plot's business.
        """
        self._on_reset = callback
        self.setCursor(Qt.PointingHandCursor if callback else Qt.ArrowCursor)

    def mouseClickEvent(self, ev):
        """A double click puts this axis back to the whole of the data.

        The gesture `PanelSplitter` already has, on the other thing a reader
        drags: *back to the default, the way a `QSplitter` handle behaves.*
        Dragging an axis pans it and there was no way back except a key --
        `Shift+V` for amplitude, and for frequency nothing at all, only home
        and end.

        pyqtgraph turns a double click into an ordinary `MouseClickEvent`
        with `double()` set (`GraphicsScene.mouseDoubleClickEvent`), so this
        is the same entry point a single click arrives at and not
        `QGraphicsItem.mouseDoubleClickEvent`, which never runs here.

        Unhandled events go to `AxisItem.mouseClickEvent`, which forwards to
        the linked view box -- so a plain click still does whatever it did.
        """
        if self._on_reset is not None and ev.double() and ev.button() == Qt.LeftButton:
            self._on_reset()
            ev.accept()
            return
        super().mouseClickEvent(ev)

    def setLogMode(self, *args, **kwargs):
        # no log mode!
        pass

    def set_max_major_ticks(self, n: int) -> None:
        """Set the maximum number of labelled ticks."""
        self.max_major_ticks = max(2, int(n))
        self.picture = None
        self.update()

    def set_si_unit(self, unit: str) -> None:
        """Scale tick values by an SI prefix even without a visible label.

        Parameters
        ----------
        unit: str
            Base unit, e.g. 'Hz'.  Pass an empty string to switch back to
            pyqtgraph's own behaviour.
        """
        self._si_unit = unit
        if unit:
            # without a visible label pyqtgraph only prefixes below 1 and
            # above 1e9 - useless for a frequency axis in Hz:
            self.setSIPrefixEnableRanges(((0.0, float("inf")),))
        self.enableAutoSIPrefix(bool(unit))
        self.updateAutoSIPrefix()

    def si_unit_label(self) -> str:
        """The unit including the current SI prefix, e.g. 'kHz'."""
        if not self._si_unit:
            return ""
        return f"{self.si_prefix}{self._si_unit}"

    def updateAutoSIPrefix(self) -> None:
        if not self._si_unit or not self.autoSIPrefix or self.label.isVisible():
            super().updateAutoSIPrefix()
            self.si_prefix = self.labelUnitPrefix
            return
        # same as AxisItem.updateAutoSIPrefix(), but without requiring a
        # visible label:
        rng = 10 ** np.array(self.range) if self.logMode else self.range
        value = max(abs(rng[0]), abs(rng[1])) * self.scale
        scale = 1.0
        prefix = ""
        for low, high in self.getSIPrefixEnableRanges():
            if low <= value <= high:
                scale, prefix = pg.functions.siScale(value, power=self.unitPower)
                break
        self.autoSIPrefixScale = scale
        self.labelUnitPrefix = prefix
        self.si_prefix = prefix
        self._updateLabel()

    @staticmethod
    def _ticks_in_range(min_val: float, max_val: float, spacing: float) -> int:
        """How many multiples of `spacing` fall inside [min_val, max_val]."""
        if spacing <= 0:
            return 0
        first = ceil(min_val / spacing)
        last = floor(max_val / spacing)
        return max(0, int(last - first) + 1)

    def tickSpacing(self, minVal, maxVal, size):
        diff = abs(maxVal - minVal)
        if diff == 0:
            return []

        # height of a tick label in the face that actually renders there:
        xwidth = theme.mono_metrics(theme.SIZE_SMALL_PT).averageCharWidth()

        # minimum spacing:
        max_ticks = max(2, int(size / (3 * xwidth)))
        max_ticks = min(max_ticks, self.max_major_ticks)
        min_spacing = diff / max_ticks
        p10unit = 10 ** floor(log10(min_spacing))

        # major ticks.  The ladder is coarse, so the first rung at or above
        # `min_spacing` can overshoot badly: a +-0.54 amplitude range in a
        # 50 px row asks for 0.545 and gets 1.0, which puts exactly one
        # labelled tick - the zero line - in view.  A y axis that says only
        # '0' tells the reader nothing about the scale, so step back down
        # one rung whenever the chosen spacing does not leave at least two
        # ticks inside the range.
        factors = [0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0]
        spacing = factors[-1] * p10unit
        previous = None
        for fac in factors:
            candidate = fac * p10unit
            if candidate >= min_spacing:
                spacing = candidate
                if self._ticks_in_range(minVal, maxVal, candidate) < 2:
                    if previous is not None:
                        spacing = previous
                break
            previous = candidate

        # minor ticks:
        factors = [100.0, 10.0, 1.0, 0.1]
        for fac in factors:
            minor_spacing = fac * p10unit
            if minor_spacing < spacing:
                break

        return [(spacing, 0), (minor_spacing, 0)]
