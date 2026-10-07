"""Add by ridge (design 5.5): the brush footprint, the spectrogram on the
session's frames, and the Viterbi ridge through the bands.

Pure numpy, no window, so it runs in the fast suite.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from audian_plugins.eodsorter import ridge as RG  # noqa: E402

DF = 0.5  # Hz per bin in the synthetic spectrograms
DT = 0.1  # s per frame


def spectrogram(curves, n_frames=120, f0=500.0, n_bins=200, seed=1, noise=1.0):
    """Gamma(2) noise (a periodogram summed over two electrodes) plus ridges.

    `curves` is ``[(freq per frame, linear power per frame)]``; each ridge
    is a Hann-like main lobe of three bins around its frequency, so the
    parabolic refinement has something realistic to work on."""
    rng = np.random.default_rng(seed)
    freqs = f0 + DF * np.arange(n_bins)
    power = noise * rng.gamma(2.0, 0.5, size=(n_frames, n_bins))
    for f, p in curves:
        x = (freqs[None, :] - np.asarray(f)[:, None]) / DF
        # main lobe of a Hann window: cos^2 in units of bins, 2 bins wide
        lobe = np.where(np.abs(x) < 2, np.cos(np.pi * x / 4) ** 4, 0.0)
        power += np.asarray(p)[:, None] * lobe
    times = DT * np.arange(n_frames)
    return power, freqs, times


def test_brush_band_is_the_footprint_of_a_horizontal_stroke():
    frame_t = np.arange(0.0, 3.0, 0.1)
    lo, hi = RG.brush_bands(frame_t, [1.0, 2.0], [600.0, 600.0], r_t=0.2, r_f=5.0)
    inside = (frame_t >= 1.0) & (frame_t <= 2.0)
    assert np.allclose(lo[inside], 595.0) and np.allclose(hi[inside], 605.0)
    # the brush's round ends: narrower within r_t of the ends, none beyond
    end = np.argmin(np.abs(frame_t - 2.1))
    assert 600 - 5 < lo[end] < 600 and 600 < hi[end] < 605
    assert np.isnan(lo[frame_t < 0.79]).all() and np.isnan(lo[frame_t > 2.21]).all()


def test_brush_band_follows_a_sloped_stroke():
    frame_t = np.linspace(0, 1, 11)
    lo, hi = RG.brush_bands(frame_t, [0.0, 1.0], [500.0, 510.0], r_t=0.01, r_f=1.0)
    centre = 0.5 * (lo + hi)
    assert np.allclose(centre, 500 + 10 * frame_t, atol=0.2)


def test_noise_factor_matches_the_gamma_distribution():
    # one electrode, one bin: the 1 % point of an exponential over its median
    assert RG.noise_factor(1, 1, 0.01) == pytest.approx(np.log(100) / np.log(2), 1e-4)
    # more bins to choose the loudest from, more electrodes to average
    assert RG.noise_factor(1, 8) > RG.noise_factor(1, 1)
    assert RG.noise_factor(2, 8) < RG.noise_factor(1, 8)


def test_the_ridge_follows_the_stroke_fish_not_a_louder_neighbour():
    """Two ridges 3 Hz apart, the neighbour 10x louder and just outside the
    brush; a gap in the fish; the path stays on the fish, inside the band,
    and leaves the gap empty."""
    n = 120
    k = np.arange(n)
    fish = 550.0 + 3.0 * np.sin(2 * np.pi * k / n)  # drifts by 3 Hz
    neighbour = fish + 3.0
    p_fish = np.full(n, 30.0)
    gap = (k >= 50) & (k < 65)
    p_fish[gap] = 0.0
    power, freqs, times = spectrogram([(fish, p_fish), (neighbour, np.full(n, 300.0))])
    lo, hi = fish - 2.0, fish + 2.0  # the stroke, drawn along the fish
    ridge = RG.extract_ridge(
        power, freqs, lo, hi, times, RG.RidgeParams(max_slope=10.0, n_channels=2)
    )
    got = ridge.freqs
    kept = np.isfinite(got)
    assert (got[kept] >= lo[kept]).all() and (got[kept] <= hi[kept]).all()
    assert not kept[gap].any(), "a gap stays a gap"
    on = kept & ~gap
    assert on.sum() >= 0.95 * (~gap).sum()
    assert np.abs(got[on] - fish[on]).max() < 0.3 * DF
    assert not (np.abs(got[kept] - neighbour[kept]) < DF).any()


def test_inside_one_band_the_stroke_decides_between_equal_fish():
    n = 80
    a = np.full(n, 560.0)
    b = np.full(n, 563.0)
    power, freqs, times = spectrogram(
        [(a, np.full(n, 40.0)), (b, np.full(n, 40.0))], n_frames=n
    )
    for centre, want in ((560.0, a), (563.0, b)):
        lo = np.full(n, centre - 4.0)
        hi = np.full(n, centre + 4.0)
        ridge = RG.extract_ridge(
            power, freqs, lo, hi, times, RG.RidgeParams(max_slope=10.0, n_channels=2)
        )
        kept = ridge.kept
        assert kept.sum() > 0.9 * n
        assert np.abs(ridge.freqs[kept] - want[kept]).max() < DF


def test_single_loud_noise_spikes_do_not_pull_the_path():
    n = 100
    rng = np.random.default_rng(5)
    fish = np.full(n, 550.0)
    power, freqs, times = spectrogram([(fish, np.full(n, 20.0))], n_frames=n, seed=3)
    # a loud click somewhere in the band in every fifth frame
    for k in range(0, n, 5):
        b = int(np.searchsorted(freqs, 550.0 + rng.choice([-3.0, 3.0])))
        power[k, b] += 400.0
    lo, hi = fish - 4.0, fish + 4.0
    ridge = RG.extract_ridge(
        power, freqs, lo, hi, times, RG.RidgeParams(max_slope=5.0, n_channels=2)
    )
    kept = ridge.kept
    assert kept.sum() > 0.9 * n
    assert np.abs(ridge.freqs[kept] - 550.0).max() < DF


def test_slope_limit_scales_with_the_frame_step():
    """The same 20 Hz/s chirp is followed at two frame steps when the
    limit allows it, and the jump cost is the same per second."""
    for dt in (0.05, 0.2):
        n = int(6 / dt)
        t = dt * np.arange(n)
        chirp = 520.0 + 20.0 * t / 6  # 20 Hz over 6 s
        power, freqs, _ = spectrogram([(chirp, np.full(n, 30.0))], n_frames=n)
        lo, hi = chirp - 3.0, chirp + 3.0
        ridge = RG.extract_ridge(
            power, freqs, lo, hi, t, RG.RidgeParams(max_slope=10.0, n_channels=2)
        )
        kept = ridge.kept
        assert kept.sum() > 0.9 * n
        assert np.abs(ridge.freqs[kept] - chirp[kept]).max() < DF


def test_power_on_frames_and_parabolic_refinement_on_audio():
    """A tone between two bins: the refined frequency is within a tenth of a
    bin, the spectrum is wavetracker's PSD scaling (a sine of amplitude A has
    A**2/2 total power), and frames outside the audio are NaN."""
    rate, nfft, step = 4000.0, 1024, 256
    df = rate / nfft
    f_true = 600.0 + 0.37 * df
    n = 20 * step + nfft
    t = np.arange(n) / rate
    amp = 0.5
    audio = np.stack(
        [
            amp * np.sin(2 * np.pi * f_true * t),
            0.2 * amp * np.sin(2 * np.pi * f_true * t + 1),
        ],
        axis=1,
    )
    starts = np.arange(-1, 22) * step  # the first and last reach outside
    total, freqs, per, spec = RG.power_on_frames(audio, 0, starts, nfft, rate, 500, 700)
    assert np.isnan(total[0]).all() and np.isnan(total[-1]).all()
    assert np.isfinite(total[1:-1]).all()
    # Parseval on the one-sided PSD: sum * df = A^2/2 per channel
    p0 = per[5, 0].sum() * df
    assert p0 == pytest.approx(amp**2 / 2, rel=0.05)
    assert per.shape == spec.shape and spec.dtype == np.complex64
    times = (starts + nfft / 2) / rate
    lo = np.full(len(starts), 590.0)
    hi = np.full(len(starts), 610.0)
    ridge = RG.extract_ridge(
        total, freqs, lo, hi, times, RG.RidgeParams(max_slope=50.0, n_channels=2)
    )
    got = ridge.freqs[1:-1]
    assert np.isfinite(got).all()
    assert np.abs(got - f_true).max() < 0.1 * df
    assert np.isnan(ridge.freqs[[0, -1]]).all()


def test_find_ridge_reads_only_the_stroke_and_returns_electrode_columns():
    rate, nfft, step = 4000.0, 1024, 256
    n_total = 400 * step
    t = np.arange(n_total) / rate
    f = 600.0 + 2.0 * t  # a slow rise
    sig = 0.1 * np.sin(2 * np.pi * np.cumsum(f) / rate)
    nb = 0.3 * np.sin(2 * np.pi * (f + 6.0) * t)  # a louder neighbour 6 Hz up
    noise = 0.01 * np.random.default_rng(0).standard_normal(n_total)
    audio = np.stack([sig + nb + noise, 0.5 * sig + nb + noise[::-1]], axis=1)
    reads = []

    def read(a, b):
        reads.append((a, b))
        return audio[a:b]

    frames = np.arange(100, 200)
    starts = frames * step
    times = (starts + nfft / 2) / rate
    centre = 600.0 + 2.0 * times
    freqs, sign, cplx, info = RG.find_ridge(
        read, n_total, rate, starts, nfft, times, centre - 3, centre + 3, max_slope=20.0
    )
    assert reads == [(int(starts[0]), int(starts[-1]) + nfft)]
    kept = np.isfinite(freqs)
    assert kept.mean() > 0.9
    assert np.abs(freqs[kept] - centre[kept]).max() < 0.5
    assert sign.shape == (len(frames), 2) and cplx.shape == (len(frames), 2)
    assert (sign[kept, 0] > sign[kept, 1]).all(), "electrode 0 hears the fish louder"
    assert info["kept"] == kept.sum()


def test_max_slope_comes_from_the_session_config():
    meta = {"config": {"tracking": {"freq_tolerance": 1.5}}}
    assert RG.max_slope_from(meta, 0.1) == pytest.approx(15.0)
    meta = {"tracking_config": {"freq_tolerance": 3.0}}
    assert RG.max_slope_from(meta, 0.5) == pytest.approx(6.0)
    # without a config: wavetracker's default (2.5 Hz between linked frames)
    assert RG.max_slope_from({}, 0.25) == pytest.approx(
        RG.wavetracker_default("tracking", "freq_tolerance", 2.5) / 0.25
    )


def test_frame_starts_use_the_grid_or_the_times():
    class Grid:
        s0, step = 100, 50

    assert RG.frame_starts(Grid, [0, 2], None, 1000.0, 64).tolist() == [100, 200]
    times = np.array([0.5, 0.6, 0.7])
    assert RG.frame_starts(None, [1, 2], times, 1000.0, 100).tolist() == [550, 650]
