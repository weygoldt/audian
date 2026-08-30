"""Chunking a kernel must not change a single sample of what it produces.

The filter and the spectrogram are computed in chunks so that a superseded
recompute can be abandoned within a few milliseconds instead of after a
whole 27 s buffer.  That is only free because both chunkings are exact:

  * `sosfilt` carries its state `zi` across the seams, so a chunk starts
    where the previous one left off rather than from rest;
  * the spectrogram's blocks are hop-aligned and carry back the
    ``nfft - hop`` frames the first window of the block needs.

Both are asserted with `array_equal`, not `allclose`, on purpose.  Losing
the `zi` would grow a filter transient at every seam, and losing the
carry-back would shift one column per block -- both look like *data* rather
than like a bug, so an approximate test would pass through the very defect
it exists to catch.
"""

from __future__ import annotations

import numpy as np
import pytest
from scipy.signal import butter, sosfilt
from thunderlab.powerspectrum import spectrogram

from audian.bufferedfilter import BufferedFilter
from audian.bufferedspectrogram import BufferedSpectrogram
from audian.tasks.tokens import CancelToken, Cancelled

RATE = 20000.0
CHANNELS = 3
NFRAMES = 60000


class FakeSource:
    """The surface of a loader that `BufferedData.open()` actually touches."""

    def __init__(self, nframes=NFRAMES, channels=CHANNELS):
        self.rate = RATE
        self.channels = channels
        self.frames = nframes
        self.offset = 0
        self.bufferframes = nframes
        self.backframes = 0
        self.ampl_min = -1.0
        self.ampl_max = 1.0
        self.unit = "V"
        self.dests = []
        rng = np.random.default_rng(4)
        self.buffer = rng.standard_normal((nframes, channels))


@pytest.fixture
def source():
    return FakeSource()


def test_a_chunked_filter_is_bit_identical_to_one_call(source):
    filt = BufferedFilter()
    filt.open(source)
    filt.highpass_cutoff = 300.0
    filt.lowpass_cutoff = 8000.0
    filt.sos = butter(2, (300.0, 8000.0), "bandpass", fs=RATE, output="sos")
    reference = sosfilt(filt.sos, source.buffer, axis=0).astype(filt.dtype)

    for chunk_bytes in (40_000, 250_000, 1_000_000):
        filt.chunk_bytes = chunk_bytes
        dest = np.empty((NFRAMES, CHANNELS), dtype=filt.dtype)
        filt.process(source.buffer, dest, 0)
        assert np.array_equal(dest, reference), f"seam at {chunk_bytes} bytes"


def test_the_warm_up_region_is_still_dropped(source):
    """`nbefore` frames of filter warm-up are computed and then discarded."""
    filt = BufferedFilter()
    filt.open(source)
    filt.sos = butter(2, 300.0, "highpass", fs=RATE, output="sos")
    reference = sosfilt(filt.sos, source.buffer, axis=0).astype(filt.dtype)

    nbefore = 10000
    dest = np.empty((NFRAMES - nbefore, CHANNELS), dtype=filt.dtype)
    filt.process(source.buffer, dest, nbefore)
    assert np.array_equal(dest, reference[nbefore:])


def test_a_chunked_spectrogram_is_bit_identical_to_one_call(source):
    for nfft, overlap in ((256, 0.5), (512, 0.75)):
        spec = BufferedSpectrogram(nfft=nfft, overlap_frac=overlap)
        spec.open(source)
        hop = spec.hop
        ncols = (NFRAMES - nfft) // hop + 1
        _, _, Sxx = spectrogram(
            source.buffer,
            RATE,
            freq_resolution=None,
            overlap_frac=None,
            n_fft=nfft,
            n_overlap=nfft - hop,
        )
        reference = Sxx.transpose((1, 2, 0))[:ncols]

        for chunk in (17, 128, 4096):
            spec.chunk_columns = chunk
            dest = np.empty((ncols, CHANNELS, nfft // 2 + 1), dtype=spec.dtype)
            extra = spec.process(source.buffer, dest, 0)
            assert np.array_equal(dest, reference), (
                f"nfft={nfft} chunk={chunk} columns differ"
            )
            assert "frequencies" in extra and "spec_rect" in extra


def test_a_cancelled_filter_stops_inside_the_buffer(source):
    """Cancelling is what the chunking is for: it must be seen mid-buffer."""
    filt = BufferedFilter()
    filt.open(source)
    filt.chunk_bytes = 40_000
    filt.sos = butter(2, 300.0, "highpass", fs=RATE, output="sos")

    token = CancelToken()
    seen = []

    def progress(fraction):
        seen.append(fraction)
        if len(seen) == 2:
            token.cancel()

    dest = np.empty((NFRAMES, CHANNELS), dtype=filt.dtype)
    with pytest.raises(Cancelled):
        filt.process(source.buffer, dest, 0, token, progress)
    assert len(seen) == 2, "cancellation was not noticed at the next chunk"
    assert seen[-1] < 0.5, "chunks are too coarse to cancel usefully"


def test_a_cancelled_spectrogram_stops_inside_the_buffer(source):
    spec = BufferedSpectrogram(nfft=256, overlap_frac=0.5)
    spec.open(source)
    spec.chunk_columns = 16
    ncols = (NFRAMES - spec.nfft) // spec.hop + 1

    token = CancelToken()
    seen = []

    def progress(fraction):
        seen.append(fraction)
        token.cancel()

    dest = np.empty((ncols, CHANNELS, spec.nfft // 2 + 1), dtype=spec.dtype)
    with pytest.raises(Cancelled):
        spec.process(source.buffer, dest, 0, token, progress)
    assert seen and seen[0] < 0.1
