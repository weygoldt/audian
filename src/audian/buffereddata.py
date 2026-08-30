"""Base class for computed data."""

import numpy as np
from audioio import BufferedArray
from math import ceil, floor
from PySide6.QtCore import QObject, Signal

from . import theme


#: Source bytes a chunked kernel works on at a time.
#:
#: Chunking exists for interruptibility -- the cancel token is polled
#: between chunks, so a superseded refilter releases the CPU within one
#: chunk instead of after a whole 27 s buffer.  It is not paid for: the
#: chunk stays in cache, so it is *faster* than the single call.  Measured
#: on the 16 channel, 20 kHz, 27 s buffer (70 MB) against 133 ms for one
#: `sosfilt`: 16 MB +7.4%, 8 MB +5.5%, 4 MB -4.0%, 2 MB -21.9%,
#: 1 MB -24.0%, 0.5 MB -26.6%, 0.25 MB -28.3%.  1 MB is 1.4 ms of work per
#: chunk -- far finer than a frame, and most of the speedup.
CHUNK_BYTES = 1_000_000


def chunk_frames(source, chunk_bytes: int = CHUNK_BYTES) -> int:
    """How many frames of `source` fit in one chunk. At least one."""
    row = source.itemsize
    for n in source.shape[1:]:
        row *= n
    return max(1, chunk_bytes // max(1, row))


class _Notifier(QObject):
    """Signal carrier for `BufferedData`.

    `BufferedData` cannot inherit from `QObject` without dragging the sip
    metaclass into `BufferedArray`'s hierarchy, so the one signal it needs
    lives on a plain helper object instead.  It is also why a trace cannot
    be `moveToThread`'d, and why the compute worker hands *values* back
    rather than being given the trace to own.
    """

    #: emitted with a `tasks.TraceUpdate` once a new buffer is in place
    sigUpdated = Signal(object)


class BufferedData(BufferedArray):
    # Buffers of derived traces are only ever drawn, never written back to
    # file, so single precision halves their footprint for free.
    dtype = np.float32

    def __init__(
        self,
        name,
        source_name,
        tbefore=0,
        tafter=0,
        panel="none",
        panel_type="trace",
        color=None,
        lw_thin=None,
        lw_thick=None,
    ):
        super().__init__(verbose=0)
        self.name = name
        self.source_name = source_name
        self.tbefore = 0
        self.tafter = 0
        self.panel = panel
        self.panel_type = panel_type
        #: per channel, is anything actually drawing this trace right now?
        #: Written by the plot items (see `dataitem.VisibleChannelMirror`),
        #: read here.  A plain array rather than the items themselves, so
        #: that deciding whether a recompute is worth doing never touches a
        #: widget -- and so a worker thread may read it.
        self.visible_channels = np.zeros(0, dtype=bool)
        self.color = theme.trace_color(name) if color is None else color
        self.lw_thin = theme.LW_THIN if lw_thin is None else lw_thin
        self.lw_thick = theme.LW_THICK if lw_thick is None else lw_thick
        self.source = None
        self.source_tbefore = tbefore
        self.source_tafter = tafter
        self.dests = []
        self.need_update = False
        # min/max pyramid over the current buffer, see MinMaxPyramid:
        self.mip_pyramid = None
        # bumped whenever buffer content is (re)loaded, so that a single
        # shared pyramid rebuild can be triggered from any plot item:
        self.buffer_generation = 0
        # how a finished recompute is announced, see apply_update():
        self._notifier = _Notifier()
        self.sigUpdated = self._notifier.sigUpdated

    def expand_times(self, tbefore, tafter):
        self.tbefore += tbefore
        self.tafter += tafter
        return self.source_tbefore + tbefore, self.source_tafter + tafter

    def update_step(self, step=1, more_shape=None):
        tbuffer = self.bufferframes / self.rate
        if step < 1:
            step = 1
        self.rate = self.source.rate / step
        self.frames = (self.source.frames + step - 1) // step
        if more_shape is None:
            self.shape = (self.frames, self.channels)
        else:
            self.shape = (self.frames, self.channels) + more_shape
        self.ndim = len(self.shape)
        self.size = self.frames * self.channels
        if self.source.bufferframes == self.source.frames:
            self.bufferframes = self.frames
        else:
            self.bufferframes = int(tbuffer * self.rate)
        self.offset = (self.source.offset + step - 1) // step
        self.follow = 0

    def open(self, source, step=1, more_shape=None):
        self.source = source
        self.source.dests.append(self)
        self.ampl_min = source.ampl_min
        self.ampl_max = source.ampl_max
        self.unit = source.unit
        self.bufferframes = 0
        self.backframes = 0
        self.channels = self.source.channels
        self.rate = self.source.rate
        self.buffer_changed = np.zeros(self.channels, dtype=bool)
        self.buffer = np.zeros((0, self.channels), dtype=self.dtype)
        self.visible_channels = np.zeros(self.channels, dtype=bool)
        self.buffer_generation = 0
        self.mip_pyramid = MinMaxPyramid() if more_shape is None else None
        self.update_step(step, more_shape)

    def allocate_buffer(self, nframes=None, force=False):
        """Reallocate the buffer, honouring `dtype`.

        `BufferedArray.allocate_buffer()` always allocates float64.
        """
        if self.bufferframes > self.frames:
            self.bufferframes = self.frames
            self.backframes = 0
        if nframes is None:
            nframes = self.bufferframes
        if nframes == 0:
            return
        if (
            force
            or nframes != len(self.buffer)
            or self.shape[1:] != self.buffer.shape[1:]
            or self.buffer.dtype != self.dtype
        ):
            shape = list(self.shape)
            shape[0] = nframes
            self.buffer = np.empty(shape, dtype=self.dtype)

    def align_buffer(self):
        soffset = self.source.offset
        snframes = len(self.source.buffer)
        if soffset > 0:
            n = floor(self.source_tbefore * self.source.rate)
            soffset += n
            snframes -= n
        if self.source.offset + len(self.source.buffer) < self.source.frames:
            n = floor(self.source_tafter * self.source.rate)
            snframes -= n
        offset = ceil(soffset * self.rate / self.source.rate)
        nframes = floor((soffset + snframes) * self.rate / self.source.rate) - offset
        self.move_buffer(offset, nframes)
        self.bufferframes = len(self.buffer)

    def source_window(self, offset, nframes, source_offset, source_frames):
        """Where `[offset, offset+nframes)` comes from in a source buffer.

        Returns `(start, stop, nbefore)` -- the slice to read, and how many
        of its leading frames are filter warm-up that the kernel computes
        and then throws away.

        Split out of `load_buffer` because the compute worker needs the same
        arithmetic against a buffer that is *not* `self.source.buffer`: for
        the second and later traces of a chain the source is the array the
        previous step just produced.
        """
        # transform to rate of source buffer:
        soffset = floor(offset * self.source.rate / self.rate)
        snframes = ceil((offset + nframes) * self.source.rate / self.rate) - soffset
        # These MULTIPLY by the source rate, like align_buffer() does.  They
        # used to divide, which made nbefore 0 for every sane rate, so the
        # filter warm-up region was never prepended and every buffer move
        # produced a fresh filter transient at the seam -- while the extra
        # buffering was still paid for in RAM.
        nbefore = floor(self.source_tbefore * self.source.rate)
        soffset -= nbefore
        snframes += nbefore
        nafter = ceil(self.source_tafter * self.source.rate)
        snframes += nafter
        soffset -= source_offset
        if soffset < 0:
            nbefore += soffset
            snframes += soffset
            soffset = 0
        if soffset + snframes > source_frames:
            snframes = source_frames - soffset
        return soffset, soffset + snframes, nbefore

    def load_buffer(self, offset, nframes, buffer):
        if self.verbose > 0:
            print(
                f"load {self.name} {offset / self.rate:.3f} - "
                f"{(offset + nframes) / self.rate:.3f}"
            )
        self.buffer_generation += 1
        i0, i1, nbefore = self.source_window(
            offset, nframes, self.source.offset, len(self.source.buffer)
        )
        extra = self.process(self.source.buffer[i0:i1], buffer, nbefore)
        if extra:
            self.apply_extra(extra)
        self.after_load()

    def after_load(self) -> None:
        """Hook for what a trace derives from its own settled buffer.

        Runs on the GUI thread on both paths -- after `process()` here, and
        after the swap in `apply_update()`.  A subclass that needs the *new*
        buffer's extent (the spectrogram does) computes it here rather than
        inside `process()`, where `self.buffer` is still the old array the
        screen is being painted from.
        """

    def apply_extra(self, extra: dict) -> None:
        """Adopt the scalars a `process()` derived along with the buffer.

        `process()` returns them rather than assigning them, because it also
        runs on a worker thread, where writing straight into the live trace
        would race whatever the GUI is painting from it.  On the synchronous
        path this is called one statement later and nothing changes.
        """
        for key, value in extra.items():
            setattr(self, key, value)

    def recompute(self):
        if len(self.source.buffer) > 0:
            self.allocate_buffer()
        self.reload_buffer()

    def planned_frames(self) -> int:
        """How long a buffer `recompute()` would produce.

        Exactly what `recompute()` ends up with: `allocate_buffer()` clamps
        `bufferframes` to the trace length and reallocates to it, and
        `reload_buffer()` then fills `len(self.buffer)`.  Splitting the
        answer out lets the compute worker allocate its own output while the
        clamping -- a write to this object -- stays on the GUI thread.
        """
        if len(self.source.buffer) > 0:
            if self.bufferframes > self.frames:
                self.bufferframes = self.frames
                self.backframes = 0
            if self.bufferframes > 0:
                return self.bufferframes
        return len(self.buffer)

    def apply_update(self, update) -> None:
        """Adopt a buffer a worker computed. GUI thread only.

        The swap is one assignment, so a repaint that lands between two
        traces of a chain sees a whole buffer of one and a whole buffer of
        the other -- never a half-filled one, which is what writing into the
        live buffer would have given.
        """
        self.buffer = update.buffer
        self.offset = update.offset
        self.bufferframes = len(update.buffer)
        self.buffer_generation += 1
        self.buffer_changed[:] = True
        if update.extra:
            self.apply_extra(update.extra)
        self.after_load()
        self.sigUpdated.emit(update)

    def is_visible(self):
        return bool(self.visible_channels.any())

    def set_need_update(self):
        self.need_update = bool(self.visible_channels.any())
        for d in self.dests:
            d.set_need_update()
        # end of dependency chain:
        if len(self.dests) == 0:
            # go to sources and propagate needed update:
            trace = self
            while hasattr(trace, "source"):
                s = trace.source
                s.need_update = trace.need_update or s.need_update
                trace = s

    def recompute_all(self):
        if self.need_update:
            self.recompute()
            for d in self.dests:
                d.recompute_all()

    def prepare_update(self) -> bool:
        """Apply changed parameters. True if a recompute is now wanted.

        Split out of `update()` so that the parameter work -- which writes
        to this object and therefore belongs on the GUI thread -- can happen
        without the recompute, which does not.  `update()` is still the
        synchronous whole; `DataBrowser.request_recompute` is the other
        caller.
        """
        return True

    def update(self):
        """Recompute this trace. Subclasses add their own parameters."""
        if self.prepare_update():
            self.recompute_all()


class MinMaxPyramid:
    """Channel-major min/max mip pyramid over a `(frames, channels)` buffer.

    Peak decimation for drawing used to read strided channel columns out of
    the C-ordered buffer -- stride 128 bytes on 16 channels -- and that one
    access pattern accounted for 27 ms of the 35 ms every `set_times` cost
    (strided per-channel reduceat 8.37 ms vs 1.56 ms contiguous).

    Each level holds interleaved min/max pairs, channel-major, at steps
    `base_step`, `2*base_step`, `4*base_step`, ...  Drawing at a given step
    slices the nearest level, so the reduction is contiguous and costs
    O(pixels) rather than O(visible samples).  This is the same trick
    `CompressedData` uses for the navigator, applied to the live buffer.

    The base level is built with `reduceat(..., axis=0)`, which walks the
    C-ordered buffer sequentially -- a full channel-major mirror of the
    buffer would be correct too but costs 119 ms to transpose 70 MB and is
    only ever needed below `base_step`, where the visible range is at most
    `base_step*max_pixel` samples and a strided read is cheap anyway.

    Total memory is about a quarter of the buffer.
    """

    #: step of the finest level; below this the caller reads the buffer
    base_step = 32

    def __init__(self, base_step: int | None = None):
        self.base_step = (
            MinMaxPyramid.base_step if base_step is None else max(2, int(base_step))
        )
        self.levels = []  # [(step, (channels, 2*nbins) array), ...]
        self.offset = -1
        self.nframes = -1
        self.generation = -1
        self.built = False

    def valid_for(self, offset: int, nframes: int, generation: int) -> bool:
        return (
            self.built
            and self.offset == offset
            and self.nframes == nframes
            and self.generation == generation
        )

    def build(self, buffer, offset: int, generation: int) -> None:
        """(Re)build the levels from `buffer`. Cheap to call on every draw."""
        if self.valid_for(offset, len(buffer), generation):
            return
        self.levels = []
        self.offset = offset
        self.nframes = len(buffer)
        self.generation = generation
        self.built = True
        if buffer.ndim != 2 or len(buffer) < 2 * self.base_step:
            return
        step = self.base_step
        level = self._base_level(buffer, step)
        while level is not None:
            self.levels.append((step, level))
            step *= 2
            level = self._coarser_level(level, 2)

    #: at or above this many channels the reshape reduction wins, below it
    #: reduceat does -- measured on a 70 MB buffer at step 32:
    #: 6ch 25.0/9.4 ms, 8ch 25.0/16.0, 12ch 26.0/40.7, 16ch 27.8/79.6
    #: (reshape/reduceat).  Neither is uniformly better.
    reshape_channels = 10

    def _base_level(self, buffer, step: int):
        """Interleaved min/max at `step`, read along the buffer's fast axis.

        Both variants walk the C-ordered buffer sequentially; which one is
        faster depends on how wide a row is, see `reshape_channels`.
        A partial last bin of fewer than `step` frames is dropped -- drawing
        falls back to a direct read there.
        """
        nbins = self.nframes // step
        if nbins < 2:
            return None
        channels = buffer.shape[1]
        out = np.empty((channels, 2 * nbins), dtype=buffer.dtype)
        if channels >= MinMaxPyramid.reshape_channels:
            block = buffer[: nbins * step].reshape(nbins, step, channels)
            out[:, 0::2] = block.min(axis=1).T
            out[:, 1::2] = block.max(axis=1).T
        else:
            edges = np.arange(nbins) * step
            out[:, 0::2] = np.minimum.reduceat(buffer[: nbins * step], edges, axis=0).T
            out[:, 1::2] = np.maximum.reduceat(buffer[: nbins * step], edges, axis=0).T
        return out

    def _coarser_level(self, level, factor: int):
        """Halve a level. min of mins is the min of the pairs, and likewise."""
        nsource = level.shape[1] // 2
        nbins = nsource // factor
        if nbins < 2:
            return None
        edges = np.arange(nbins) * (2 * factor)
        out = np.empty((level.shape[0], 2 * nbins), dtype=level.dtype)
        np.minimum.reduceat(level, edges, axis=1, out=out[:, 0::2])
        np.maximum.reduceat(level, edges, axis=1, out=out[:, 1::2])
        return out

    def nbytes(self) -> int:
        return sum(values.nbytes for _, values in self.levels)

    def level_for(self, step: int):
        """Coarsest `(level_step, array)` that still resolves `step`."""
        best = None
        for level_step, values in self.levels:
            if level_step <= step:
                best = (level_step, values)
            else:
                break
        return best

    def decimate(self, channel: int, start: int, stop: int, step: int):
        """Interleaved min/max of `channel` over `[start, stop)` at `step`.

        `start` and `stop` are absolute frame indices.  Returns
        `(values, first_frame)` -- `first_frame` is where the first bin
        actually begins, which is snapped to the level's own grid and can be
        up to `step` frames before `start`.  Returns `None` when the pyramid
        cannot serve the request and the caller should read the buffer.
        """
        level = self.level_for(step)
        if level is None or stop <= start:
            return None
        i0 = start - self.offset
        i1 = stop - self.offset
        if i0 < 0 or i1 > self.nframes:
            return None
        nbins = (i1 - i0) // step
        if nbins < 1:
            return None
        level_step, level_values = level
        j0 = i0 // level_step
        j1 = min(level_values.shape[1] // 2, (i1 + level_step - 1) // level_step)
        edges = (np.arange(nbins) * (step / level_step)).astype(int) * 2
        if j1 <= j0 or edges[-1] >= 2 * (j1 - j0):
            return None
        values = level_values[channel, 2 * j0 : 2 * j1]
        out = np.empty(2 * nbins, dtype=values.dtype)
        np.minimum.reduceat(values, edges, out=out[0::2])
        np.maximum.reduceat(values, edges, out=out[1::2])
        return out, self.offset + j0 * level_step
