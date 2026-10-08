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
from scipy.signal import butter, get_window, sosfilt
from thunderlab.powerspectrum import spectrogram

from audian.bufferedfilter import BufferedFilter
from audian.bufferedspectrogram import BufferedSpectrogram, power_spectra
from audian.tasks.tokens import Cancelled, CancelToken

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
    """Blocks of any size, transformed in parallel or not, give one picture.

    Bit-identical to `power_spectra` over the whole buffer in one call --
    the seam test, which is what this file is for: a lost carry-back shifts
    a column per block, so `array_equal` and nothing looser.

    Against thunderlab's `spectrogram` (scipy) the kernel agrees to
    rounding rather than to the bit: it takes each window's mean over a
    contiguous copy instead of through scipy's channel-strided view, which
    sums in another order (and is what made it four times slower).  A shift
    by one column, or a wrong scale, window or detrend, is many orders of
    magnitude outside that tolerance.
    """
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
        scipy_reference = Sxx.transpose((1, 2, 0))[:ncols]
        reference = power_spectra(
            source.buffer, RATE, nfft, hop, get_window("hann", nfft)
        )[:ncols]
        assert reference.shape == scipy_reference.shape
        assert np.allclose(reference, scipy_reference, rtol=1e-9, atol=0), (
            f"nfft={nfft}: the kernel is not scipy's spectrogram"
        )

        for chunk in (17, 128, 4096):
            spec.chunk_columns = chunk
            dest = np.empty((ncols, CHANNELS, nfft // 2 + 1), dtype=spec.dtype)
            extra = spec.process(source.buffer, dest, 0)
            assert np.array_equal(dest, reference), (
                f"nfft={nfft} chunk={chunk} columns differ"
            )
            assert "frequencies" in extra


def test_a_float32_source_is_transformed_in_single_precision(source):
    """What scipy does with the float32 filtered trace, the kernel does too.

    `BufferedData` keeps derived traces in float32, and scipy then computes
    the whole spectrogram in single precision.  The denoiser chain sees the
    block in that dtype, so the kernel must not quietly widen it.
    """
    x = source.buffer.astype(np.float32)
    nfft, hop = 256, 128
    _, _, Sxx = spectrogram(
        x, RATE, freq_resolution=None, overlap_frac=None, n_fft=nfft, n_overlap=hop
    )
    want = Sxx.transpose((1, 2, 0))
    got = power_spectra(x, RATE, nfft, hop, get_window("hann", nfft))
    assert got.dtype == want.dtype == np.float32
    assert np.allclose(got, want, rtol=1e-4, atol=1e-6 * float(want.max()))


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
