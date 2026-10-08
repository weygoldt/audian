"""Spectrogram of source data on the fly."""

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

import numpy as np
from scipy import fft as sp_fft
from scipy.signal import get_window

from thunderlab.powerspectrum import decibel

from . import denoise
from .buffereddata import BufferedData
from .tasks.tokens import NEVER


def channel_power(block, channel):
    """One channel of a (time, channel, freq) block, or the mean of several.

    `channel` is an index or a sequence of them; a sequence averages the
    *power* over those channels, then leaves the single `decibel()` to the
    caller.  That order is the whole of what a mean spectrogram is, and
    getting it wrong is not a near miss.  Measured on the flona block
    (4283x16x129): four of those sixteen electrodes -- 08 to 11 -- are
    recorded as exactly zero, `decibel(0)` is -inf, and a mean of decibels
    is therefore -inf at all 552507 bins.  The naive way draws an empty
    panel.  Even over the twelve live channels alone it is off by a median
    of 2.30 dB and by as much as 40.93 dB; the mean of the power is finite
    everywhere.

    A sequence that *is* every channel in order is reduced straight off a
    view of the buffer.  The general path has to gather the wanted channels
    into a new array first, and that gather is most of what it costs: on the
    same block, all sixteen reduce in 3.58 ms while eight of sixteen take
    17.69 ms.  The fast path is taken on the exact list rather than on its
    length, so a sequence that repeats a channel still gets the mean it
    asked for.
    """
    if np.ndim(channel) == 0:
        return block[:, int(channel), :]
    channels = [int(c) for c in channel]
    if channels == list(range(block.shape[1])):
        return block.mean(axis=1)
    return block[:, channels, :].mean(axis=1)


def fast_decibel(power, min_power=1e-20):
    """`thunderlab.powerspectrum.decibel` for arrays, bit for bit, in place.

    ``decibel`` copies its input, gathers the bins above `min_power` into a
    second array, takes their log and scatters it back: four passes and two
    fancy-index operations.  This takes one contiguous copy (C order, which
    `pg.ImageItem` wants anyway) and works on it in place.  Every element
    goes through the same ``10 * log10(p)`` on a contiguous array, so the
    result is identical -- including NaN staying NaN and anything at or below
    `min_power` (zero, negative) becoming ``-inf``.  Measured on the image
    `SpecItem` uploads for a 60 s, 48 kHz view at 4K: 7.1 ms -> 2.0 ms.
    """
    out = np.array(power, dtype=np.result_type(power, np.float32), order="C")
    low = out <= min_power
    with np.errstate(all="ignore"):
        np.log10(out, out=out)
    out *= 10.0
    out[low] = -np.inf
    return out


class _Pool:
    """A few threads for transforming spectrogram blocks side by side.

    numpy's ufuncs and scipy's FFT release the GIL, so independent column
    blocks really do run in parallel.  Shared by every spectrogram; created
    on first use.  Only the transform runs here: the denoiser chain, which
    is plugin code, stays on the calling thread.
    """

    lock = Lock()
    executor = None
    #: at four the curve flattens (60 s, 48 kHz, 2 channels: one thread
    #: 42 ms, two 27 ms, four 18 ms, eight 17 ms)
    threads = max(1, min(4, (os.cpu_count() or 1)))

    @classmethod
    def get(cls):
        with cls.lock:
            if cls.executor is None and cls.threads > 1:
                cls.executor = ThreadPoolExecutor(
                    cls.threads, thread_name_prefix="audian-spec"
                )
            return cls.executor


def power_spectra(source, rate, nfft, hop, window):
    """One-sided power spectral density of every full window in `source`.

    `source` is ``(frames, channels)``; the result is
    ``(columns, channels, nfft//2 + 1)`` in the dtype scipy would give --
    float32 for a float32 source, float64 otherwise.

    The same arithmetic as `scipy.signal.spectrogram` with ``detrend=
    'constant'``, ``scaling='density'``, ``mode='psd'`` (what
    `thunderlab.powerspectrum.spectrogram` asks for): subtract each window's
    mean, multiply by the window, real FFT, ``|X|^2`` times
    ``1/(rate*sum(w^2))``, doubled except at DC and Nyquist.  scipy reads
    the windows through a view whose window axis is strided by the channel
    count, and numpy's mean over that axis costs more than the FFT itself:
    on 60 s of 48 kHz stereo 178 ms for the transform against 42 ms here,
    where the samples are made channel-major first so every window is
    contiguous.  The sums run in a different order, so the result agrees
    with scipy to rounding (relative 1e-11 in float64), not to the bit;
    a column depends only on its own samples, so blocks of any size
    still give bit-identical columns -- see `tests/test_chunked_dsp.py`.
    """
    source = np.asarray(source)
    dtype = np.float32 if source.dtype == np.float32 else np.float64
    ncols = (len(source) - nfft) // hop + 1
    channels = source.shape[1]
    nbins = nfft // 2 + 1
    if ncols < 1:
        return np.zeros((0, channels, nbins), dtype=dtype)
    win = window.astype(dtype)
    scale = dtype(1.0 / (rate * float(np.sum(window * window))))
    used = source[: (ncols - 1) * hop + nfft]
    samples = np.ascontiguousarray(used.T, dtype=dtype)  # (channels, frames)
    step = samples.strides[1]
    frames = np.lib.stride_tricks.as_strided(
        samples,
        shape=(channels, ncols, nfft),
        strides=(samples.strides[0], hop * step, step),
        writeable=False,
    )
    seg = frames - frames.mean(axis=-1, keepdims=True, dtype=dtype)
    seg *= win
    spec = sp_fft.rfft(seg, axis=-1)
    power = np.empty(spec.shape, dtype=dtype)
    np.multiply(spec.real, spec.real, out=power)
    power += spec.imag * spec.imag
    power *= scale
    if nfft % 2:
        power[..., 1:] *= 2
    else:
        power[..., 1:-1] *= 2
    return power.transpose(1, 0, 2)


NOISE_FLOOR_MARGIN_DB = 3.0
"""Headroom above the *broadband* median power that the colour ramp starts at.

The historical estimate took the 95th percentile of the top 1/16 of the
frequency axis -- an assumed-empty band.  Measured on
``data/Gryllus_campestris.wav`` that band sits 10 dB below the real broadband
floor, so half the panel landed above 13% of the colour ramp and read as a
saturated wash.  Clamping the lower limit to the in-view median plus this
margin makes the floor track the data that is actually on screen.
"""


class BufferedSpectrogram(BufferedData):
    # Power spans a large dynamic range and is fed to decibel(), percentile()
    # and max(); unlike the drawn-only trace buffers this one stays float64.
    dtype = np.float64

    # Only nfft frames of look-ahead are actually needed; this used to be
    # 10 s, which inflated the raw buffer for nothing.
    lookahead_time = 0.5

    def __init__(
        self,
        name="spectrogram",
        source="filtered",
        panel="spectrogram",
        nfft=256,
        overlap_frac=0.5,
    ):
        super().__init__(
            name,
            source,
            tafter=BufferedSpectrogram.lookahead_time,
            panel=panel,
            panel_type="spectrogram",
        )
        self.nfft = nfft
        self.hop = 0
        self.overlap_frac = overlap_frac
        #: Which registered denoisers run on each chunk, and what
        #: each of them is set to.  Keys and plain numbers rather than
        #: objects, so both are things a settings file can carry.  Every
        #: denoiser keeps its parameters whether or not it is enabled, so
        #: that turning one off and on again does not reset it.
        self.denoisers = ()
        self.denoise_params = denoise.defaults()
        self.set_hop()
        self.frequencies = np.zeros(0)
        self.fresolution = 1
        self.tresolution = 1
        self.spec_rect = []
        self.use_spec = True
        self.init = True

    def open(self, source):
        self.hop = int(self.nfft * (1 - self.overlap_frac))
        self.fresolution = source.rate / self.nfft
        self.frequencies = np.arange(
            0, source.rate / 2 + self.fresolution / 2, self.fresolution
        )
        self.tresolution = self.hop / source.rate
        self.spec_rect = []
        self.use_spec = True
        super().open(source, self.hop, more_shape=(self.nfft // 2 + 1,))
        self.unit = f"{self.unit}^2/Hz"
        self.ampl_min = 0
        self.ampl_max = self.source.rate / 2

    #: Columns transformed per chunk, or None to size chunks by
    #: `chunk_samples`.  The blocks are hop-aligned and each carries back
    #: the `nfft - hop` frames its first window needs, which is what makes
    #: the result **bit-identical** to transforming the whole buffer in one
    #: call -- see `tests/test_chunked_dsp.py`.
    #:
    #: The point is interruptibility: a superseded spectrogram gives the CPU
    #: back within one chunk instead of after the whole buffer.  It is also
    #: faster, because a block this size stays in cache, and the blocks are
    #: what `_Pool` hands out to its threads.
    chunk_columns = None

    #: Samples (columns x channels x nfft) per chunk when `chunk_columns`
    #: is None.  With `power_spectra` and four threads the knee sits at
    #: about a quarter million, whatever the channel count -- measured at
    #: nfft 256, median of 7: 76 s of 48 kHz stereo 29.7 / 17.1 / 12.4 ms
    #: at 128 / 256 / 512 columns; 26 s of 16 channels at 20 kHz 33.9 /
    #: 19.5 / 15.6 / 38.9 ms at 16 / 32 / 64 / 128; 40 s of 8 channels 27.0
    #: / 14.8 / 12.0 / 30.0 ms at 32 / 64 / 128 / 256.  A chunk is then
    #: 3-10 ms of work for one thread.
    chunk_samples = 1 << 18

    def chunk_size(self, channels: int) -> int:
        """Columns per chunk for a source of `channels` channels."""
        if self.chunk_columns is not None:
            return max(1, int(self.chunk_columns))
        per_column = max(1, channels * self.nfft)
        return max(8, min(1024, self.chunk_samples // per_column))

    def process(self, source, dest, nbefore, cancel=NEVER, progress=None):
        """Transform `source` into `dest`, in interruptible column blocks.

        Returns the scalars it derived rather than assigning them, because
        this also runs on a worker thread and `frequencies` and `spec_rect`
        are read by the paint path; `BufferedData.apply_extra` adopts them
        on the GUI thread as part of the swap.
        """
        ndest = len(dest)
        nsource = (ndest - 1) * self.hop + self.nfft
        if nsource > len(source):
            nsource = len(source)
        extra = {}
        written = 0
        # Read once, not per chunk: a denoiser swapped mid-buffer would
        # leave the two halves treated differently, which is the same
        # hazard `DataBrowser.request_recompute` joins the worker to avoid.
        enabled = denoise.ordered(self.denoisers)
        params = {k: dict(v) for k, v in self.denoise_params.items()}
        if nsource >= self.nfft:
            rate = self.source.rate
            nfft, hop = self.nfft, self.hop
            freq = sp_fft.rfftfreq(nfft, 1 / rate)
            window = get_window("hann", nfft)
            chunks = []
            start = 0
            columns = self.chunk_size(source.shape[1] if source.ndim > 1 else 1)
            while start < ndest:
                take = min(columns, ndest - start)
                lo = start * hop
                hi = min(nsource, lo + (take - 1) * hop + nfft)
                if hi - lo < nfft:
                    break
                chunks.append((lo, hi, start, take))
                start += take
            pool = _Pool.get() if len(chunks) > 1 else None
            batch = _Pool.threads if pool is not None else 1

            def transform(chunk):
                lo, hi, at, take = chunk
                block = power_spectra(source[lo:hi], rate, nfft, hop, window)
                n = min(len(block), take)
                if enabled:
                    return block[:n]
                # no denoiser: the block goes straight to its own rows of
                # `dest`, from the thread that made it
                dest[at : at + n] = block[:n]
                return n

            for b in range(0, len(chunks), batch):
                cancel.check()
                todo = chunks[b : b + batch]
                if pool is not None and len(todo) > 1:
                    results = list(pool.map(transform, todo))
                else:
                    results = [transform(c) for c in todo]
                for (_lo, _hi, at, _take), result in zip(todo, results):
                    cancel.check()
                    if enabled:
                        # plugin code, so on this thread and in order
                        with np.errstate(under="ignore"):
                            block = denoise.apply_chain(result, freq, enabled, params)
                        n = len(block)
                        dest[at : at + n] = block
                    else:
                        n = result
                    if n < 1:
                        break
                    written = at + n
                    extra["frequencies"] = freq
                    if progress is not None:
                        progress(written / ndest)
        dest[written:] = 0
        return extra

    def after_load(self) -> None:
        """Extent of the buffer that is now in place.

        Read from `self.buffer` rather than from the array `process()` just
        filled, because on a partial reload those are not the same length --
        `move_buffer` hands `process()` only the slice it recycled.  On the
        threaded path this runs after the swap, so `self.buffer` is again
        the right array to measure.
        """
        self.spec_rect = [
            self.offset / self.rate,
            0,
            len(self.buffer) / self.rate,
            self.source.rate / 2 + self.fresolution,
        ]

    def set_hop(self):
        hop = int(np.round((1 - self.overlap_frac) * self.nfft))
        if hop < 1:
            hop = 1
        if hop > self.nfft:
            hop = self.nfft
        if self.hop != hop:
            self.hop = hop
            self.overlap_frac = 1 - self.hop / self.nfft
            return True
        else:
            return False

    def update(self, nfft=None, overlap_frac=None, denoisers=None, denoise_params=None):
        if self.prepare_update(nfft, overlap_frac, denoisers, denoise_params):
            self.recompute_all()

    def prepare_update(
        self,
        nfft=None,
        overlap_frac=None,
        denoisers=None,
        denoise_params=None,
    ) -> bool:
        spec_update = False
        if nfft is not None:
            if nfft < 8:
                nfft = 8
            max_nfft = min(len(self.source) // 2, 2**30)
            if nfft > max_nfft:
                nfft = max_nfft
            if self.nfft != nfft:
                self.nfft = nfft
                spec_update = True
        if overlap_frac is not None:
            if overlap_frac < 0.0:
                overlap_frac = 0.0
            elif overlap_frac > 0.99999:
                overlap_frac = 0.99999
            self.overlap_frac = overlap_frac
        if self.set_hop():
            spec_update = True
        if spec_update:
            self.tresolution = self.hop / self.source.rate
            self.fresolution = self.source.rate / self.nfft
            self.update_step(self.hop, more_shape=(self.nfft // 2 + 1,))

        # A denoiser change leaves the buffer's shape alone -- same nfft,
        # same hop -- so it must not go through `update_step()`, but the
        # contents are stale and have to be transformed again.  Hence a
        # second flag rather than folding it into `spec_update`.
        chain_changed = False
        if denoisers is not None:
            wanted = denoise.ordered(denoisers)
            if wanted != denoise.ordered(self.denoisers):
                self.denoisers = wanted
                chain_changed = True

        # A parameter of a denoiser that is switched off changes what
        # enabling it will do, not the picture in front of the reader -- so
        # it is stored either way and only counts as a change when its own
        # denoiser is running.  Clamped here rather than at the widget, so
        # a value from a settings file is bounded too.
        touched_running = False
        if denoise_params:
            running = set(denoise.ordered(self.denoisers))
            for key, values in denoise_params.items():
                entry = denoise.denoiser(key)
                if entry is None:
                    continue
                current = self.denoise_params.setdefault(key, entry.defaults())
                for pkey, value in values.items():
                    param = entry.parameter(pkey)
                    if param is None or value is None:
                        continue
                    clamped = param.clamp(value)
                    if clamped != current.get(pkey):
                        current[pkey] = clamped
                        if key in running:
                            touched_running = True

        # Enabling or disabling always needs the buffer redone -- including
        # disabling, which is the recompute that undoes the denoising.
        return spec_update or chain_changed or touched_running

    #: Columns per cached block of `column_sums`.
    sum_block = 64

    def load_buffer(self, offset, nframes, buffer):
        super().load_buffer(offset, nframes, buffer)
        if nframes >= len(self.buffer):
            # a whole reload: a new hop may have renumbered the columns
            self._forget_sums()
        else:
            self._forget_sums(offset, offset + nframes)

    def apply_update(self, update) -> None:
        self._forget_sums()
        super().apply_update(update)

    def _forget_sums(self, start=None, stop=None) -> None:
        """Drop cached block sums over absolute columns `[start, stop)`."""
        cache = getattr(self, "_sums", None)
        if not cache:
            return
        if start is None:
            cache.clear()
            return
        b = self.sum_block
        for k in range(start // b, (stop + b - 1) // b):
            cache.pop(k, None)

    def column_sums(self, i0: int, i1: int):
        """Power summed over buffer rows `[i0, i1)`, per channel and bin.

        What the power curve beside each lane is made of -- the visible
        block's mean over time -- without reading the visible block on every
        pan step and for every lane.  Reading it was 3.3 ms per lane per
        step on 60 s of stereo, and on sixteen channels each lane read the
        whole interleaved buffer for its one channel: 16 x 26 MB per step.

        Sums are cached per block of `sum_block` columns on absolute column
        indices, so a pan only adds up its two ragged ends and the blocks it
        has not seen; `load_buffer` and `apply_update` drop the blocks whose
        columns they rewrite.  Shifting the buffer in place does not change
        a column, so it keeps them.  The sum runs in another order than
        ``block.mean(axis=0)`` did, so the curve agrees with it to rounding.
        """
        buf = self.buffer
        n = len(buf)
        i0 = max(0, min(n, i0))
        i1 = max(i0, min(n, i1))
        shape = buf.shape[1:]
        cache = getattr(self, "_sums", None)
        if cache is None or getattr(self, "_sums_shape", None) != shape:
            cache = self._sums = {}
            self._sums_shape = shape
        b = self.sum_block
        a0 = self.offset + i0
        a1 = self.offset + i1
        k0 = (a0 + b - 1) // b
        k1 = a1 // b
        total = np.zeros(shape)
        if k1 <= k0:
            if i1 > i0:
                np.add.reduce(buf[i0:i1], axis=0, out=total)
            return total
        if k0 * b > a0:
            total += np.add.reduce(buf[i0 : k0 * b - self.offset], axis=0)
        if a1 > k1 * b:
            total += np.add.reduce(buf[k1 * b - self.offset : i1], axis=0)
        blocks = []
        for k in range(k0, k1):
            s = cache.get(k)
            if s is None:
                j = k * b - self.offset
                s = cache[k] = np.add.reduce(buf[j : j + b], axis=0, dtype=np.float64)
            blocks.append(s)
        total += np.add.reduce(blocks, axis=0)
        # forget blocks the buffer no longer holds, so memory stays bounded
        if len(cache) > 2 * (n // b + 2):
            lo = self.offset // b
            hi = (self.offset + n) // b
            for k in [k for k in cache if k < lo or k > hi]:
                del cache[k]
        return total

    def visible_slice(self, t0: float, t1: float) -> tuple[int, int]:
        """Index range of `[t0, t1]` within the current buffer.

        Clamped to the buffer, so the result is always safe to slice with
        and never triggers a reload.
        """
        n = len(self.buffer)
        if n == 0:
            return 0, 0
        i0 = int(np.floor(t0 * self.rate)) - self.offset
        i1 = int(np.ceil(t1 * self.rate)) + 1 - self.offset
        i0 = max(0, min(n, i0))
        i1 = max(i0, min(n, i1))
        if i1 <= i0:
            return 0, n
        return i0, i1

    def estimate_noiselevels_visible(self, channel, t0, t1):
        """Noise levels from the visible part of the buffer only.

        `estimate_noiselevels()` deliberately keeps its `self.init` guard:
        without it a 424 ms decibel pass over the whole 16 channel buffer
        would run on every scroll.  This variant looks at the cropped
        visible slice instead, so it is cheap enough to call whenever the
        buffer actually moved -- but only then, not on every repaint.

        `channel` may be a sequence, in which case the estimate is made of
        the mean power over those channels -- see `channel_power`.
        """
        if len(self.buffer) == 0 or len(self.buffer.shape) < 3:
            return None, None
        i0, i1 = self.visible_slice(t0, t1)
        if i1 <= i0:
            return None, None
        block = channel_power(self.buffer[i0:i1], channel)
        nf = max(1, block.shape[1] // 16)
        with np.errstate(all="ignore"):
            db = decibel(block)
            zmin = np.percentile(db[:, -nf:], 95)
            zmax = np.max(db)
            zmin = max(zmin, np.median(db) + NOISE_FLOOR_MARGIN_DB)
        if not np.isfinite(zmin) or not np.isfinite(zmax):
            return None, None
        zmax = zmin + 0.95 * (zmax - zmin)
        if zmax - zmin < 20:
            zmax = zmin + 20
        if zmax - zmin > 80:
            zmin = zmax - 80
        return zmin, zmax

    def estimate_noiselevels(self, channel):
        """Colour ramp the whole buffer suggests, once, at start-up.

        `channel` may be a sequence, which asks for the mean power over
        those channels rather than one of them.  The two are not
        interchangeable: measured on the flona block, channel 0 gives
        -72.2 .. -47.1 dB and the mean of all sixteen gives
        -74.5 .. -9.6 dB.  The floor moves 2.3 dB, so the heuristic keeps
        landing where it was tuned to land -- but the top moves 37.5 dB, so
        a mean panel handed a per-channel ramp is a spectrogram with most of
        its contrast thrown away.
        """
        if not self.init or len(self.buffer) == 0 or len(self.buffer.shape) < 3:
            return None, None
        nf = self.buffer.shape[2] // 16
        if nf < 1:
            nf = 1
        with np.errstate(all="ignore"):
            db = decibel(channel_power(self.buffer, channel))
            zmin = np.percentile(db[:, -nf:], 95)
            zmax = np.max(db)
            zmin = max(zmin, np.median(db) + NOISE_FLOOR_MARGIN_DB)
        if not np.isfinite(zmin) or not np.isfinite(zmax):
            return None, None
        self.init = False
        zmax = zmin + 0.95 * (zmax - zmin)
        if zmax - zmin < 20:
            zmax = zmin + 20
        if zmax - zmin > 80:
            zmin = zmax - 80
        return zmin, zmax
