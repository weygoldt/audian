"""PlotDataItem for time series trace as a function of time."""

from typing import Any

import numpy as np
import pyqtgraph as pg

from . import theme
from .dataitem import VisibleChannelMirror


#: trace name -> theme role.  Anything else keeps the colour the trace
#: itself carries, so third-party plugin traces still work.
TRACE_ROLES = {
    "data": "raw",
    "raw": "raw",
    "trace": "raw",
    "filtered": "filtered",
    "filter": "filtered",
    "envelope": "envelope",
}


class TraceItem(VisibleChannelMirror, pg.PlotDataItem):
    """One channel of one trace.

    Carries two pieces of *stack* state that decide how loudly it is allowed
    to draw itself, both pushed down from the plot that owns it:

    ``selected``
        This is the channel the user is working on.  It is the only trace in
        the stack painted in a saturated colour (``primary``), which is what
        makes it findable in the data area instead of only in the 200 px rail
        at the far left.
    ``dense``
        More than `theme.DENSE_CHANNELS` channels are on screen.  Everything
        goes hairline and the unselected traces are dimmed harder, so sixteen
        lanes read as texture with one lane standing out of it.
    """

    def __init__(self, data, channel, *args, **kwargs):
        self.data = data
        self.rate = self.data.rate
        self.channel = channel
        self.step = 1
        #: role from the trace's *name*; None for a plugin trace that brings
        #: its own colour.  The role actually painted can differ - see
        #: `effective_role()`.
        self.base_role = TRACE_ROLES.get(str(data.name).lower())
        self.role = self.base_role
        self.color = theme.trace_color(self.role) if self.role else self.data.color
        self.lw_thin = self.data.lw_thin
        self.lw_thick = self.data.lw_thick
        self.selected = False
        self.dense = False
        # last pen state applied, so update_plot() does not re-create and
        # re-assign an identical QPen on every pan of every channel:
        self._pen_key: tuple | None = None
        # Device width of the viewbox, refreshed on resize instead of being
        # queried from the screen on every pan, zoom and buffer refresh.
        self._max_pixel = 0
        self._vb_connected = None

        pg.PlotDataItem.__init__(
            self, *args, connect="all", antialias=False, skipFiniteCheck=True, **kwargs
        )
        # An item that has not been added to a plot yet reports itself
        # visible, which is exactly what `plot_items[channel] = self`
        # used to mean here.  Every later change arrives through
        # `itemChange`, including being parented into a hidden plot.
        self.mirror_visibility()
        self.apply_pen()
        self.setSymbolSize(8)
        self.setSymbolBrush(self.symbol_brush())
        self.setSymbolPen(self.symbol_pen())
        self.setSymbol(None)

    # --- stack state -----------------------------------------------------

    def set_selected(self, selected: bool) -> None:
        """Mark this trace as belonging to the current channel."""
        selected = bool(selected)
        if selected != self.selected:
            self.selected = selected
            self.apply_pen()

    def set_dense(self, dense: bool) -> None:
        """Tell this trace whether it is one of many channels on screen."""
        dense = bool(dense)
        if dense != self.dense:
            self.dense = dense
            self.apply_pen()

    # --- appearance ----------------------------------------------------

    def effective_role(self) -> str | None:
        """Role to paint with, re-resolved from the trace's live filter state.

        A ``filtered`` trace whose filter is a pass-through holds exactly the
        raw samples, so it is painted as raw.  `theme.waveform_role()` is the
        single source of that decision for the whole application.
        """
        if self.base_role is None:
            return None
        return theme.waveform_role(self.data, self.base_role)

    def trace_pen(self, thick: bool = False) -> Any:
        """Pen for this trace, resolved by role, selection and stack density."""
        self.role = self.effective_role()
        if self.role is None:
            return theme.waveform_pen(
                selected=self.selected,
                dense=self.dense,
                thick=thick,
                color=self.color,
            )
        self.color = theme.trace_color(self.role)
        return theme.waveform_pen(
            self.role, selected=self.selected, dense=self.dense, thick=thick
        )

    def apply_pen(self, thick: bool = False) -> None:
        """Set the pen, but only when something about it actually changed.

        `setPen()` invalidates the item and schedules a repaint, so calling it
        unconditionally from `update_plot()` costs one extra full redraw per
        channel per pan.
        """
        key = (
            self.effective_role(),
            self.selected,
            self.dense,
            bool(thick),
            theme.current_theme(),
        )
        if key == self._pen_key:
            return
        self._pen_key = key
        self.setPen(self.trace_pen(thick))

    def symbol_brush(self):
        if self.role is None:
            return theme.brush(self.color)
        return theme.trace_symbol_brush(self.role)

    def symbol_pen(self):
        if self.role is None:
            return theme.pen(self.color)
        return theme.trace_symbol_pen(self.role)

    def polish(self):
        """Re-resolve colours from the theme."""
        self._pen_key = None
        self.apply_pen(self.step == 1)
        self.setSymbolBrush(self.symbol_brush())
        self.setSymbolPen(self.symbol_pen())

    apply_theme = polish

    # --- geometry ------------------------------------------------------

    def max_pixel(self, vb) -> int:
        """Device pixel width of our own viewbox.

        `QApplication.desktop().screenGeometry().width()` used to be queried
        here on every update for every channel.  Besides being deprecated
        and wrong on multi-monitor Wayland, it made a half-width window on a
        3440 px screen draw about three times more points than it has
        pixels (44.4 ms vs 15.4 ms per repaint).
        """
        if vb is not self._vb_connected:
            if self._vb_connected is not None:
                try:
                    self._vb_connected.sigResized.disconnect(self._vb_resized)
                except (TypeError, RuntimeError):
                    pass
            self._vb_connected = vb
            self._max_pixel = 0
            vb.sigResized.connect(self._vb_resized)
        if self._max_pixel <= 0:
            widget = self.getViewWidget()
            dpr = widget.devicePixelRatioF() if widget is not None else 1.0
            self._max_pixel = max(1, int(vb.width() * dpr))
        return self._max_pixel

    def _vb_resized(self, vb=None):
        """Re-decimate when our viewbox changes width.

        The decimation step is derived from the viewbox width, so a
        resize invalidates the drawn data, not just the cached width.
        Without the redraw the item keeps whatever step it computed when
        the viewbox was first laid out - typically a few pixels wide,
        i.e. a step of 120000 and 18 drawn points for a ten second
        window - and never recovers, because nothing else calls
        update_plot() on a resize.
        """
        previous = self._max_pixel
        self._max_pixel = 0
        if previous and self.getViewBox() is not None:
            self.update_plot()

    def buffer_range(self, start: int, stop: int) -> tuple[int, int]:
        """Clamp an index range to the loaded buffer, aligned to `self.step`.

        Drawing must never index the trace itself: `BufferedArray.__getitem__`
        calls `update_buffer()`, so an out-of-buffer index would perform a
        disk read plus, for derived traces, a full re-filter inside the paint
        path -- up to 32 times per frame.
        """
        offset = self.data.offset
        end = offset + len(self.data.buffer)
        step = self.step
        if start < offset:
            # smallest multiple of step that reaches into the buffer
            # (was a Python while loop, run 32 times per redraw):
            start += ((offset - start + step - 1) // step) * step
        if stop > end:
            stop -= ((stop - end + step - 1) // step) * step
        return start, min(stop, end)

    def update_plot(self):
        vb = self.getViewBox()
        if not isinstance(vb, pg.ViewBox):
            return
        # index range and steps that needs to be drawn:
        t0, t1 = vb.viewRange()[0]
        start = max(0, int(t0 * self.rate))
        tstop = int(t1 * self.rate + 1)
        stop = min(len(self.data), tstop)
        max_pixel = self.max_pixel(vb)
        self.step = max(1, (tstop - start) // max_pixel)
        buffer = self.data.buffer
        if self.step > 1:
            # downsample aligned to multiples of step:
            start = (start // self.step) * self.step
            tstop = (stop // self.step + 1) * self.step
            stop = min(len(self.data), tstop)
            start, stop = self.buffer_range(start, stop)
            peaks = self.peaks(start, stop)
            if peaks is None:
                return
            plot_data, first = peaks
            step2 = self.step / 2
            plot_time = (first + np.arange(len(plot_data)) * step2) / self.rate
            self.apply_pen()
            self.setSymbol(None)
            self.setData(plot_time, plot_data)
        else:
            # all data:
            start, stop = self.buffer_range(start, stop)
            if stop <= start:
                return
            i0 = start - self.data.offset
            self.setData(
                np.arange(start, stop) / self.rate,
                buffer[i0 : i0 + stop - start, self.channel],
            )
            self.apply_pen(thick=True)
            if max_pixel / (stop - start) >= 10:
                self.setSymbol("o")
            else:
                self.setSymbol(None)
        self.data.buffer_changed[self.channel] = False

    def peaks(self, start: int, stop: int):
        """Interleaved min/max over `[start, stop)`, decimated by `self.step`.

        Returns `(values, first_frame)`.  Served from the trace's min/max
        pyramid whenever it can resolve `self.step`, so the reduction reads
        contiguous channel-major memory and costs O(pixels) rather than
        O(visible samples).  Below the pyramid's base step the visible range
        is at most `base_step*max_pixel` samples and a strided read out of
        the buffer is cheap enough.
        """
        if stop <= start:
            return None
        pyramid = getattr(self.data, "mip_pyramid", None)
        if pyramid is not None:
            pyramid.build(
                self.data.buffer,
                self.data.offset,
                getattr(self.data, "buffer_generation", 0),
            )
            peaks = pyramid.decimate(self.channel, start, stop, self.step)
            if peaks is not None:
                return peaks
        i0 = start - self.data.offset
        values = self.data.buffer[i0 : i0 + stop - start, self.channel]
        if len(values) < self.step:
            return None
        segments = np.arange(0, len(values), self.step)
        plot_data = np.empty(2 * len(segments), dtype=values.dtype)
        np.minimum.reduceat(values, segments, out=plot_data[0::2])
        np.maximum.reduceat(values, segments, out=plot_data[1::2])
        return plot_data, start

    def get_amplitude(self, x, y, x1=None):
        """Get trace amplitude next to cursor position.

        Reads `data.buffer` directly: going through `self.data[...]` would
        call `update_buffer()` and could move the buffer -- and re-filter it
        -- from a hover handler.  Returns `(x, None)` outside the buffer.
        """
        idx = int(np.round(x * self.rate))
        step = self.step
        if x1 is not None:
            idx1 = int(np.round(x1 * self.rate))
            step = max(1, idx1 - idx)
        if step > 1:
            idx = (idx // step) * step
        i0 = idx - self.data.offset
        n = len(self.data.buffer)
        if i0 < 0 or i0 >= n:
            return idx / self.rate, None
        if step > 1:
            data_block = self.data.buffer[i0 : min(i0 + step, n), self.channel]
            mini = np.argmin(data_block)
            maxi = np.argmax(data_block)
            amin = data_block[mini]
            amax = data_block[maxi]
            if abs(y - amax) < abs(y - amin):
                return (idx + maxi) / self.rate, amax
            else:
                return (idx + mini) / self.rate, amin
        else:
            return idx / self.rate, self.data.buffer[i0, self.channel]


if __name__ == "__main__":
    from numba import njit
    from timeit import Timer

    def setup(step=100):
        x = np.random.randn(1000 * step)
        n = 2 * len(x) // step
        y = np.zeros(n)
        return x, y, step

    def full(x, y, step):
        y[0] = np.min(x)
        y[1] = np.max(x)

    def reduce(x, y, step):
        y[0] = np.minimum.reduce(x)
        y[1] = np.maximum.reduce(x)

    @njit()
    def numba(x, y, step):
        for i in range(0, len(x) // step):
            i0 = i * step
            y[2 * i + 0] = np.min(x[i0 : i0 + step])
            y[2 * i + 1] = np.max(x[i0 : i0 + step])

    def reshape(x, y, step):
        n = len(y)
        y[0:n:2] = np.min(x.reshape(-1, step), 1)
        y[1:n:2] = np.max(x.reshape(-1, step), 1)

    def reduceat(x, y, step):
        n = len(y)
        y[0:n:2] = np.minimum.reduceat(x, np.arange(0, len(x), step))
        y[1:n:2] = np.maximum.reduceat(x, np.arange(0, len(x), step))

    def reduceat_arange(x, y, step):
        n = len(y)
        r = np.arange(0, len(x), step)
        y[0:n:2] = np.minimum.reduceat(x, r)
        y[1:n:2] = np.maximum.reduceat(x, r)

    def reduceat_out(x, y, step):
        n = len(y)
        r = np.arange(0, len(x), step)
        np.minimum.reduceat(x, r, out=y[0:n:2])
        np.maximum.reduceat(x, r, out=y[1:n:2])

    def reduceat_range(x, y, step):
        n = len(y)
        r = range(0, len(x), step)
        y[0:n:2] = np.minimum.reduceat(x, r)
        y[1:n:2] = np.maximum.reduceat(x, r)

    def timeit():
        """Runtime of various ways to compute the miminum and maximum of
        chunks of data as used by audian for downsampling for plotting.

        See also https://stackoverflow.com/questions/61255208/finding-the-maximum-in-a-numpy-array-every-nth-instance

        reduceat_out() is fastest!

        step = 1:
          full                : 0.0055
          reduce              : 0.0028
          numba               : 0.0784
          reshape             : 0.0091
          reduceat            : 0.0193
          reduceat_arange     : 0.0182
          reduceat_out        : 0.0166
          reduceat_range      : 0.1446
        step = 10:
          full                : 0.0094
          reduce              : 0.0062
          numba               : 0.0884
          reshape             : 0.1113
          reduceat            : 0.0667
          reduceat_arange     : 0.0656
          reduceat_out        : 0.0629
          reduceat_range      : 0.2028
        step = 100:
          full                : 0.0543
          reduce              : 0.0479
          numba               : 0.2973
          reshape             : 0.1922
          reduceat            : 0.1289
          reduceat_arange     : 0.1265
          reduceat_out        : 0.1248
          reduceat_range      : 0.2853
        step = 1000:
          full                : 1.3660
          reduce              : 1.3529
          numba               : 2.7436
          reshape             : 1.5176
          reduceat            : 1.4344
          reduceat_arange     : 1.4289
          reduceat_out        : 1.4229
          reduceat_range      : 1.6014
        """
        # init numba:
        x, y, step = setup(10)
        numba(x, y, step)
        # time it:
        repeats = 20
        for step in [1, 10, 100, 1000]:
            print(f"step = {step}:")
            for f in [
                "full",
                "reduce",
                "numba",
                "reshape",
                "reduceat",
                "reduceat_arange",
                "reduceat_out",
                "reduceat_range",
            ]:
                t = Timer(
                    f"{f}(x, y, step)", f"x, y, step = setup({step})", globals=globals()
                )
                times = sorted(t.repeat(repeats, 1000))
                print(f"  {f:<20s}: {np.mean(times[:5]):.4f}")

    def reduceat_output():
        """len of reduceat is same as len of indices."""
        step = 4
        for n in range(1, 15):
            x = np.arange(0, n, 1.0)
            r = np.arange(0, len(x), step)
            y = np.maximum.reduceat(x, r)
            print(
                f"x:{len(x):3d}  s:{step:2d}  r:{len(r):2d}  y:{len(y):2d}  (x+step-1)/s:{(len(x) + step - 1) // step:2d} ",
                x,
                "->",
                y,
            )

    #######################################################################
    reduceat_output()
    print()
    # timeit()
