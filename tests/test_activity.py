"""Tests for the baseline-referenced activity metrics.

The point of these is not that the numbers are stable, but that the metric
still *separates the two signal classes it exists to separate*.  Each test
therefore builds a signal whose class is known by construction and asserts
the classification, rather than pinning a magic number.
"""

import numpy as np

from audian import activity as ac

RATE = 20000
SECONDS = 15.0
NOISE = 0.01


def _noise(rng, n):
    return rng.normal(0, NOISE, n)


def _pulse_shape(width_s=0.0012):
    w = int(width_s * RATE)
    k = np.arange(-w, w + 1) / RATE
    return w, (
        np.exp(-(((k + 2e-4) / 2.5e-4) ** 2))
        - 0.8 * np.exp(-(((k - 2e-4) / 3.5e-4) ** 2))
    )


def _signals(seed=1):
    """Baseline, a wave-type EOD, sparse eel pulses, and both together."""
    rng = np.random.default_rng(seed)
    n = int(RATE * SECONDS)
    t = np.arange(n) / RATE
    baseline = _noise(rng, n)
    wave = (
        _noise(rng, n)
        + 0.05 * np.sin(2 * np.pi * 400 * t)
        + 0.015 * np.sin(2 * np.pi * 800 * t)
    )
    w, shape = _pulse_shape()
    pulses = _noise(rng, n)
    both = wave.copy()
    for target in (pulses, both):
        for pt in rng.uniform(0.05, SECONDS - 0.05, 45):
            i = int(pt * RATE)
            target[i - w : i + w + 1] += 0.30 * shape
    return baseline, wave, pulses, both


def _recording(seed=1):
    """The four segments concatenated, as one recording.

    Classification is always tested on this rather than on a segment in
    isolation, because :func:`activity.global_baseline` estimates the noise
    floor from the *whole* recording and needs part of it to be quiet.  A
    file that is wall-to-wall wave EOD has no baseline to find -- see
    :func:`test_baseline_needs_some_quiet`, which pins that limitation.
    """
    return np.concatenate(_signals(seed))


def _stats(signal, bins=375):
    column = np.asarray(signal, dtype=float)[:, None]
    return ac.reduce_block(column, len(column) // bins)


def _segment(array, index, nseg=4):
    """Slice the `index`-th of `nseg` equal segments out of a per-bin array."""
    q = len(array) // nseg
    return array[index * q : (index + 1) * q]


BASELINE, SUSTAINED_SEG, TRANSIENT_SEG, BOTH_SEG = 0, 1, 2, 3


def test_baseline_estimate_recovers_the_noise_level():
    baseline, _, _, _ = _signals()
    sigma = ac.global_baseline(_stats(baseline))
    assert abs(sigma[0] - NOISE) / NOISE < 0.15


def test_baseline_is_not_inflated_by_sparse_pulses():
    """A low percentile must exclude the bins that hold a transient."""
    _, _, pulses, _ = _signals()
    sigma = ac.global_baseline(_stats(pulses))
    assert abs(sigma[0] - NOISE) / NOISE < 0.15


def test_wave_raises_rms_and_pulses_do_not():
    stats = _stats(_recording(), 1500)
    rms = ac.rms_excess_db(stats, ac.global_baseline(stats))[:, 0]
    assert np.median(_segment(rms, SUSTAINED_SEG)) > 6.0
    # a sparse transient contributes only A^2/N to a bin of N samples
    assert np.median(_segment(rms, TRANSIENT_SEG)) < 2.0
    assert np.median(_segment(rms, BASELINE)) < 2.0


def test_baseline_needs_some_quiet():
    """Document the estimator's one assumption rather than hide it.

    Given a recording that is *entirely* wave EOD, a low percentile of the
    per-bin RMS finds the EOD, not the noise, and the signal measures as
    baseline.  This is the documented trade for an estimator that can be
    built from composable accumulators.
    """
    _, wave, _, _ = _signals()
    stats = _stats(wave, 1500)
    sigma = ac.global_baseline(stats)
    assert sigma[0] > 3 * NOISE
    rms = ac.rms_excess_db(stats, sigma)[:, 0]
    assert np.median(rms) < 3.0


def test_pulses_raise_peak_far_above_what_noise_can_reach():
    stats = _stats(_recording(), 1500)
    peak = ac.peak_excess_db(stats, ac.global_baseline(stats))[:, 0]
    assert _segment(peak, TRANSIENT_SEG).max() > _segment(peak, BASELINE).max() + 10.0


def test_classification_separates_the_three_cases():
    stats = _stats(_recording(), 1500)
    cls = ac.classify(stats, ac.global_baseline(stats))[:, 0]

    assert (_segment(cls, BASELINE) == ac.QUIET).all()
    assert (_segment(cls, SUSTAINED_SEG) == ac.SUSTAINED).mean() > 0.95

    pulsed = _segment(cls, TRANSIENT_SEG)
    # 45 pulses were injected; bin-edge straddling may split a few
    assert 40 <= int((pulsed == ac.TRANSIENT).sum()) <= 60
    assert (pulsed == ac.SUSTAINED).sum() == 0

    # a segment carrying both must show both, not be forced onto one side
    both = _segment(cls, BOTH_SEG)
    assert (both == ac.SUSTAINED).sum() > 250
    assert (both == ac.TRANSIENT).sum() > 30


def test_classification_is_stable_across_bin_sizes():
    """The verdict must not change just because the user zoomed."""
    recording = _recording()
    for bins in (400, 1500, 6000):
        stats = _stats(recording, bins)
        cls = ac.classify(stats, ac.global_baseline(stats))[:, 0]
        assert (_segment(cls, BASELINE) == ac.QUIET).mean() > 0.95, bins
        assert (_segment(cls, SUSTAINED_SEG) == ac.SUSTAINED).mean() > 0.95, bins
        pulsed = _segment(cls, TRANSIENT_SEG)
        assert (pulsed == ac.TRANSIENT).sum() > 30, bins
        assert (pulsed == ac.SUSTAINED).sum() == 0, bins


def test_combine_is_exact():
    """Pyramid levels must equal direct binning, or zoom would lie."""
    both = np.asarray(_signals()[3], dtype=float)[:, None]
    step = 187
    # the coarse step must be exactly 4x the fine one, or the two are not
    # binning the same samples and the comparison is meaningless
    fine = ac.reduce_block(both, step)
    coarse = ac.reduce_block(both, 4 * step)
    merged = fine.combine(4)
    k = min(merged.nbins, coarse.nbins) - 1
    # the reductions are bit-exact: min/max and the sample counts must match
    # exactly, or a pyramid level is binning different samples.
    assert np.array_equal(merged.n[:k], coarse.n[:k])
    assert np.array_equal(merged.minimum[:k], coarse.minimum[:k])
    assert np.array_equal(merged.maximum[:k], coarse.maximum[:k])
    # the moments are exact only up to summation order: merging four partial
    # sums does not add the same floats in the same sequence as summing 748
    # samples directly.  Observed disagreement is ~1e-16 relative, i.e. the
    # last bit or two of a float64, which no dB readout can resolve.
    assert np.allclose(
        merged.variance()[:k], coarse.variance()[:k], rtol=1e-9, atol=1e-15
    )
    assert np.allclose(merged.peak()[:k], coarse.peak()[:k], rtol=1e-9, atol=1e-15)


def test_variance_is_ac_coupled():
    """A pure DC offset is not activity."""
    n = 40000
    flat = np.full((n, 1), 3.5)
    stats = ac.reduce_block(flat, 800)
    assert np.allclose(stats.variance(), 0.0)
    assert np.allclose(stats.peak(), 0.0)


def test_noise_peak_floor_grows_with_bin_size():
    """More samples per bin means a higher expected noise peak."""
    assert ac.noise_peak_db(800) < ac.noise_peak_db(40000)
    assert ac.noise_peak_db(800) > 0.0


def test_short_final_bin_does_not_inflate_rms():
    """`reduce_block` must count the tail bin's real length."""
    rng = np.random.default_rng(0)
    x = rng.normal(0, NOISE, (1000 + 137, 1))
    stats = ac.reduce_block(x, 1000)
    assert stats.n[0] == 1000
    assert stats.n[-1] == 137
