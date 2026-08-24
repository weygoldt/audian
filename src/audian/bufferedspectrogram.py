"""Spectrogram of source data on the fly."""

import numpy as np

from thunderlab.powerspectrum import decibel, spectrogram

from .buffereddata import BufferedData

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

    def process(self, source, dest, nbefore):
        nsource = (len(dest) - 1) * self.hop + self.nfft
        if nsource > len(source):
            nsource = len(source)
        if nsource >= self.nfft:
            with np.errstate(under="ignore"):
                freq, time, Sxx = spectrogram(
                    source[:nsource],
                    self.source.rate,
                    freq_resolution=None,
                    overlap_frac=None,
                    n_fft=self.nfft,
                    n_overlap=self.nfft - self.hop,
                )
            n = Sxx.shape[1]
            dest[:n] = Sxx.transpose((1, 2, 0))
            dest[n:] = 0
            self.frequencies = freq
        else:
            dest[:] = 0
        # extent of the full buffer:
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
            self.recompute_all()

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
        """
        if len(self.buffer) == 0 or len(self.buffer.shape) < 3:
            return None, None
        i0, i1 = self.visible_slice(t0, t1)
        if i1 <= i0:
            return None, None
        block = self.buffer[i0:i1, channel, :]
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
        if not self.init or len(self.buffer) == 0 or len(self.buffer.shape) < 3:
            return None, None
        nf = self.buffer.shape[2] // 16
        if nf < 1:
            nf = 1
        with np.errstate(all="ignore"):
            db = decibel(self.buffer[:, channel, :])
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
