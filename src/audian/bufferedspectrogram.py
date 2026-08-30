"""Spectrogram of source data on the fly."""

import numpy as np

from thunderlab.powerspectrum import decibel, spectrogram

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

    #: Columns transformed per chunk.  The blocks are hop-aligned and each
    #: carries back the `nfft - hop` frames its first window needs, which is
    #: what makes the result **bit-identical** to transforming the whole
    #: buffer in one call -- see `tests/test_chunked_dsp.py`.
    #:
    #: The point is interruptibility: a superseded spectrogram gives the CPU
    #: back within one chunk instead of after the whole buffer.  It is also
    #: faster, because a block this size stays in cache.  Measured on the
    #: 16 channel, 20 kHz, 27 s buffer (4251 columns, nfft 256), against
    #: 447 ms for the single call: 1024 cols -0.4%, 512 -0.5%, 256 -11.0%,
    #: 128 -19.5%, 64 -33.1%.  128 is the knee -- 10.6 ms of work per chunk,
    #: which is both a fine cancellation granularity and a fifth off.
    chunk_columns = 128

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
        if nsource >= self.nfft:
            with np.errstate(under="ignore"):
                while written < ndest:
                    cancel.check()
                    take = min(self.chunk_columns, ndest - written)
                    lo = written * self.hop
                    hi = min(nsource, lo + (take - 1) * self.hop + self.nfft)
                    if hi - lo < self.nfft:
                        break
                    freq, _, Sxx = spectrogram(
                        source[lo:hi],
                        self.source.rate,
                        freq_resolution=None,
                        overlap_frac=None,
                        n_fft=self.nfft,
                        n_overlap=self.nfft - self.hop,
                    )
                    n = min(Sxx.shape[1], ndest - written)
                    if n < 1:
                        break
                    dest[written : written + n] = Sxx.transpose((1, 2, 0))[:n]
                    written += n
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

    def update(self, nfft=None, overlap_frac=None):
        if self.prepare_update(nfft, overlap_frac):
            self.recompute_all()

    def prepare_update(self, nfft=None, overlap_frac=None) -> bool:
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
        return spec_update

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
