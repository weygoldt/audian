"""Spectrogram denoising: the gate, and the property that lets it be chunked."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from audian import denoise  # noqa: E402


def block(patterns, frames=4, freqs=3):
    """A ``(time, channel, frequency)`` block whose every bin has `patterns`.

    `patterns` is the per-channel power of one bin; it is broadcast over
    time and frequency so a test can talk about one spatial pattern at a
    time.
    """
    p = np.asarray(patterns, dtype=float)
    return np.tile(p[None, :, None], (frames, 1, freqs))


def kept(out, source):
    """Fraction of power the gate let through."""
    return float(out.sum()/source.sum())


class TestSpatialCoherence:
    def test_common_mode_is_suppressed(self):
        """Equal power on every electrode is pickup, not a fish."""
        b = block([1.0, 1.0, 1.0, 1.0])
        out = denoise.spatial_coherence(b, threshold_db=6.0, softness_db=3.0)
        assert kept(out, b) < 0.15

    def test_localised_source_is_kept(self):
        """A source peaked on one electrode survives."""
        b = block([1.0, 0.3, 0.05, 0.002])
        out = denoise.spatial_coherence(b, threshold_db=6.0, softness_db=3.0)
        assert kept(out, b) > 0.95

    def test_the_weakest_synthetic_fish_clears_the_default(self):
        """9.8x spread is the weakest fish in wavefish_4ch_hard.wav.

        The 6 dB default exists because that fish is at 9.9 dB and the hum
        is at 0 dB.  If a change to the gate stops separating those two,
        the default is no longer defensible and this fails.
        """
        fish = block([1.0, 0.31, 0.69, 0.102])       # 9.8x max/min
        hum = block([1.0, 1.0, 1.0, 1.0])
        f = denoise.spatial_coherence(fish, denoise.DEFAULT_THRESHOLD_DB,
                                      denoise.DEFAULT_SOFTNESS_DB)
        h = denoise.spatial_coherence(hum, denoise.DEFAULT_THRESHOLD_DB,
                                      denoise.DEFAULT_SOFTNESS_DB)
        assert kept(f, fish) > 0.7
        assert kept(h, hum) < 0.15

    def test_dead_electrode_does_not_defeat_the_gate(self):
        """A channel recorded as exactly zero must not read as contrast.

        Four of the sixteen channels of the flona block are exactly zero.
        Taken naively, ``max/min`` is then infinite in every bin and the
        gate passes everything -- the denoiser would silently do nothing on
        precisely the recordings it was written for.
        """
        b = block([1.0, 1.0, 1.0, 0.0])
        out = denoise.spatial_coherence(b, threshold_db=6.0, softness_db=3.0)
        assert kept(out, b) < 0.2

    def test_all_channels_dead_is_not_a_crash(self):
        b = block([0.0, 0.0, 0.0, 0.0])
        out = denoise.spatial_coherence(b, threshold_db=6.0, softness_db=3.0)
        assert np.all(np.isfinite(out))

    def test_mono_is_returned_unchanged(self):
        """One electrode has no spread, so there is nothing to measure."""
        b = block([1.0])
        out = denoise.spatial_coherence(b)
        assert out is b

    def test_source_block_is_not_modified(self):
        b = block([1.0, 1.0, 1.0, 1.0])
        before = b.copy()
        denoise.spatial_coherence(b)
        assert np.array_equal(b, before)

    def test_threshold_moves_the_gate(self):
        b = block([1.0, 0.1, 0.1, 0.1])              # 10 dB of spread
        loose = denoise.spatial_coherence(b, threshold_db=0.0, softness_db=3.0)
        tight = denoise.spatial_coherence(b, threshold_db=30.0, softness_db=3.0)
        assert kept(loose, b) > kept(tight, b)

    def test_zero_softness_is_a_hard_cut(self):
        b = block([1.0, 0.1, 0.1, 0.1])              # 10 dB
        below = denoise.spatial_coherence(b, threshold_db=20.0, softness_db=0.0)
        above = denoise.spatial_coherence(b, threshold_db=5.0, softness_db=0.0)
        assert kept(below, b) == pytest.approx(0.0)
        assert kept(above, b) == pytest.approx(1.0)


class TestChunkInvariance:
    """The property that lets `process()` denoise a chunk at a time.

    `BufferedSpectrogram.process()` transforms the buffer in blocks of
    `chunk_columns` and `tests/test_chunked_dsp.py` pins the result as
    bit-identical to transforming it whole.  A denoiser that looked at
    neighbouring columns would break that at every chunk boundary, so the
    registry only admits ones that are pointwise in time -- and this is
    what "pointwise in time" is worth as a test.
    """

    def test_column_blocks_give_the_same_answer(self):
        rng = np.random.default_rng(3)
        b = rng.random((40, 4, 9)) + 1e-3
        whole = denoise.spatial_coherence(b, 6.0, 3.0)
        pieces = np.concatenate(
            [denoise.spatial_coherence(b[i:i + 7], 6.0, 3.0)
             for i in range(0, len(b), 7)]
        )
        assert np.array_equal(whole, pieces)


class TestRegistry:
    def test_none_is_first_and_does_nothing(self):
        assert denoise.DENOISERS[0].key == denoise.NONE_KEY
        assert denoise.DENOISERS[0].apply is None

    def test_keys_are_unique(self):
        keys = [d.key for d in denoise.DENOISERS]
        assert len(keys) == len(set(keys))

    def test_unknown_key_falls_back_rather_than_raises(self):
        """A settings file from a newer version must not stop a recording
        from opening."""
        assert denoise.denoiser("no-such-denoiser").key == denoise.NONE_KEY

    def test_spatial_declares_the_channels_it_needs(self):
        assert denoise.denoiser("spatial").min_channels >= 2

    def test_every_entry_is_pointwise_in_time(self):
        """Every registered denoiser must satisfy the chunking contract."""
        rng = np.random.default_rng(11)
        b = rng.random((24, 4, 5)) + 1e-3
        for entry in denoise.DENOISERS:
            if entry.apply is None:
                continue
            whole = entry.apply(b, 6.0, 3.0)
            halves = np.concatenate(
                [entry.apply(b[:10], 6.0, 3.0), entry.apply(b[10:], 6.0, 3.0)]
            )
            assert np.array_equal(whole, halves), entry.key
