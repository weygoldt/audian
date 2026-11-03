"""Removal of clicks -- impulsive noise -- from raw traces.

A port of `wavetracker.clicks` to numpy, for looking at data rather than
tracking it: electrodes out of the water pick up static, clicks of a few
milliseconds up to four orders of magnitude above the noise, and crackle in
the water smears grain over the whole spectrogram.  Neither is in the signal
anyone is looking for, and both hide it.

Each channel is split by its spectrum:

- Clicks are searched in the top of the spectrum, above `high_band` of the
  sampling rate, where a click reaches and an animal's narrowband signal
  does not.  So even a chirp right at an electrode does not count as one.
  Loud events longer than `max_duration` (a boat, rain) are signals, and
  stay.
- Cutting the clicks out is not enough: a hole in a loud tone is itself a
  click as loud as the tone.  What stands out of the spectral floor --
  tones, their harmonics, hum -- is set aside, the clicks are bridged by
  straight lines in the rest, and the tonal part is added back on top,
  scaled in each hole to what the samples around it hold, so that a tone
  that only starts later is not written into the time before it.

Samples outside a click are returned exactly as they came in, and a channel
without a click is returned untouched.

The traces are cleaned in blocks fixed in absolute time, each computed from
itself and `margin` on both sides, and only its own frames are kept.  So a
sample comes out the same however the buffer around it was loaded: scrolled
to, reloaded whole, or computed on a worker in pieces.  See
`clean_blocks`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import scipy.fft
from scipy.ndimage import maximum_filter1d, rank_filter


@dataclass(frozen=True)
class ClickRemoval:
    """What counts as a click, and how it is repaired.

    The defaults are wavetracker's, where they were measured on field
    recordings of weakly electric fish and on lab recordings with known
    identities (see wavetracker's `ClickConfig`).  Only `threshold` is
    offered in the GUI.
    """

    #: a click reaches this many robust standard deviations of its
    #: channel's high band, which Gaussian noise never does
    threshold: float = 12.0
    #: and lasts for as long as it stays above this many
    extent_threshold: float = 8.0
    #: clicks are searched above this fraction of the sampling rate ...
    high_band: float = 0.2
    #: ... reached over this further fraction, smoothly
    high_band_edge: float = 0.05
    #: events longer than this [s] are signals, not clicks
    max_duration: float = 0.5
    #: also repaired on both sides of a click, its decay [s]
    guard: float = 0.002
    #: time resolution of the search [s]
    resolution: float = 0.001
    #: not searched at the very ends of a block [s]: the join of the
    #: periodic extension shows in the high band there
    edge: float = 0.005
    #: what stands this far above the spectral floor is tonal and never
    #: repaired [dB]
    line_margin: float = 15.0
    #: frequency resolution of the spectral floor [Hz]
    line_width: float = 10.0
    #: range the floor is the `floor_quantile` of [Hz]
    floor_width: float = 1000.0
    #: low, because a loud tone with its harmonics can fill most of it
    floor_quantile: float = 0.2
    #: window over which the tonal part in a hole is scaled [s]
    local_window: float = 0.05
    #: fade that makes a block periodic for the spectral split [s]
    fade: float = 0.5
    #: every n-th sample estimates the noise of the high band
    sample_stride: int = 16
    #: length of the blocks fixed in absolute time [s]
    block: float = 2.0
    #: data on each side of a block it is computed from [s]; at least
    #: `max_duration`, so that a long event touching a block is seen whole
    #: enough to be told from a click
    margin: float = 0.5

    def block_frames(self, rate: float) -> int:
        return max(64, int(round(self.block * rate)))

    def margin_frames(self, rate: float) -> int:
        return max(0, int(round(self.margin * rate)))


def spectral_floor(power: np.ndarray, df: float, cfg: ClickRemoval) -> np.ndarray:
    """Smooth floor under a power spectrum `power` (bins,).

    A low quantile over a wide range, of medians over narrow ranges: a tone
    can fill most of the spectrum around it with its harmonics and its
    modulations, and the floor must stay below that however loud it is.
    """
    n_bins = len(power)
    chunk = min(max(8, round(cfg.line_width / df)), n_bins)
    n_chunks = n_bins // chunk
    medians = np.median(power[: n_chunks * chunk].reshape(n_chunks, chunk), axis=-1)
    width = max(3, round(cfg.floor_width / (chunk * df))) | 1
    rank = max(1, math.ceil(cfg.floor_quantile * width))
    # `nearest` is the replicate padding of the original
    floor = rank_filter(medians, rank - 1, size=width, mode="nearest")
    floor = np.repeat(floor, chunk)
    return np.pad(floor, (0, n_bins - len(floor)), mode="edge")


def periodic(x: np.ndarray, n_fft: int) -> np.ndarray:
    """`x` extended to `n_fft` samples that lead smoothly back to its start.

    A sine that is not periodic in the transform spreads into skirts below
    any margin, which would ring from both ends of the block in the part
    that is repaired.
    """
    n = len(x)
    m = n_fft - n
    if m <= 0:
        return x
    # point reflections continue the value and the slope at both ends
    after = 2 * x[-1] - x[-(m + 1) : -1][::-1]
    before = 2 * x[0] - x[1 : m + 1][::-1]
    k = (np.arange(m) + 0.5) / m
    fade = np.cos(0.5 * np.pi * k) ** 2
    return np.concatenate([x, after * fade + before * (1 - fade)])


def long_runs(hot: np.ndarray, length: int) -> np.ndarray:
    """True within runs of at least `length` consecutive True."""
    n = len(hot)
    if length > n:
        return np.zeros_like(hot)
    total = np.concatenate([[0], np.cumsum(hot, dtype=np.int64)])
    full = (total[length:] - total[:-length]) == length
    starts = np.concatenate([[0], np.cumsum(full, dtype=np.int64)])
    i = np.arange(n)
    lo = np.clip(i - length + 1, 0, None)
    hi = np.clip(i + 1, None, len(full))
    return (starts[hi] - starts[lo]) > 0


def runs_holding(runs: np.ndarray, seeds: np.ndarray) -> np.ndarray:
    """The runs of True in `runs` that hold a True of `seeds`."""
    # every sample of a run has the same count of False before it
    labels = np.cumsum(~runs, dtype=np.int64)
    held = np.bincount(labels, weights=seeds & runs, minlength=len(runs) + 1)
    return runs & (held[labels] > 0)


def find_clicks(high: np.ndarray, rate: float, cfg: ClickRemoval) -> np.ndarray:
    """Mask of the clicks in `high`, one channel's high band (samples,)."""
    n = len(high)
    size = max(1, round(cfg.resolution * rate))
    n_bins = n // size
    if n_bins == 0:
        return np.zeros(n, dtype=bool)
    magnitude = np.abs(high)
    # median over what is not digital silence: half a silent block has
    # noise too
    sample = magnitude[:: cfg.sample_stride]
    sample = sample[sample > 0]
    if len(sample) == 0:
        return np.zeros(n, dtype=bool)
    sigma = np.median(sample) / 0.6745
    # the largest value per bin: fast, and bridges zero crossings
    envelope = magnitude[: n_bins * size].reshape(n_bins, size).max(axis=-1)
    longest = max(1, round(cfg.max_duration / cfg.resolution))
    # hysteresis: a click reaches the threshold, and then lasts for as long
    # as it stays above the lower one.  A long loud event holds no click,
    # and neither does a long event around one.
    trigger = envelope > cfg.threshold * sigma
    trigger &= ~long_runs(trigger, longest)
    hot = runs_holding(envelope > cfg.extent_threshold * sigma, trigger)
    hot &= ~long_runs(hot, longest)
    edge = min(round(cfg.edge / cfg.resolution), n_bins // 2)
    if edge:
        hot[:edge] = False
        hot[-edge:] = False
    guard = round(cfg.guard / cfg.resolution)
    if guard and hot.any():
        hot = maximum_filter1d(hot, 2 * guard + 1, mode="constant", cval=False)
    mask = np.repeat(hot, size)
    return np.pad(mask, (0, n - len(mask)))


def _cumsum0(x: np.ndarray) -> np.ndarray:
    return np.concatenate([[0.0], np.cumsum(x)])


def repair(x: np.ndarray, tonal: np.ndarray, mask: np.ndarray, width: int):
    """`x` with its masked samples bridged under the tonal part `tonal`.

    In a hole, the tonal part is scaled to what the good samples around it
    hold of it -- the least-squares gain over `width` samples, within 0 and
    1: 1 where a tone is there and 0 where it is not yet.  What is left, the
    noise and the click, is replaced by a straight line between the nearest
    good samples on either side, and the scaled tonal part is added back.

    Computed at the masked samples and the ends of their holes only: the
    clicks are a few percent of a block.  Built at full length, the bridge
    and the gain took 1.3 s of the 3.0 s that 20 s of iriri01-grid01-dev03
    (16 channels, 48 kHz, a night of crackle) cost under cProfile;
    this takes 0.4 s, and agrees with that to 2.3e-15 of the largest value.
    """
    n = len(x)
    edges = np.diff(np.concatenate([[0], mask.astype(np.int8), [0]]))
    starts = np.flatnonzero(edges == 1)
    ends = np.flatnonzero(edges == -1)
    # the nearest good samples; none on one side: hold the other
    lo = starts - 1
    hi = ends
    lo = np.where(lo < 0, hi, lo)
    hi = np.where(hi >= n, lo, hi)
    lo = np.clip(lo, 0, n - 1)
    hi = np.clip(hi, 0, n - 1)
    good = ~mask
    num = _cumsum0(x * tonal * good)
    den = _cumsum0(tonal * tonal * good)
    tiny = np.finfo(np.float64).tiny

    def scaled(i):
        a = np.clip(i - width // 2, 0, None)
        b = np.clip(i + width // 2 + 1, None, n)
        gain = (num[b] - num[a]) / np.maximum(den[b] - den[a], tiny)
        return tonal[i] * np.clip(gain, 0.0, 1.0)

    idx = np.flatnonzero(mask)
    run = np.repeat(np.arange(len(starts)), ends - starts)
    left = x[lo] - scaled(lo)
    right = x[hi] - scaled(hi)
    weight = (idx - lo[run]) / np.maximum(hi[run] - lo[run], 1)
    out = x.copy()
    out[idx] = scaled(idx) + left[run] * (1 - weight) + right[run] * weight
    return out


def remove_clicks(x: np.ndarray, rate: float, cfg: ClickRemoval):
    """Repair the clicks in one channel's block `x` (samples,).

    Returns `(out, mask)`: the repaired samples, and which of them were
    repaired.  `out` is `x` itself when there was nothing to repair, and
    equal to `x` wherever `mask` is False.
    """
    n = len(x)
    if n < 64:  # too short to tell anything, and to make periodic
        return x, np.zeros(n, dtype=bool)
    x = np.asarray(x, dtype=np.float64)
    n_fft = scipy.fft.next_fast_len(n + min(round(cfg.fade * rate), n // 2), real=True)
    spectrum = scipy.fft.rfft(periodic(x, n_fft))
    power = spectrum.real**2 + spectrum.imag**2
    df = rate / n_fft
    narrow = power > spectral_floor(power, df, cfg) * 10 ** (cfg.line_margin / 10)
    freqs = np.arange(len(power)) * df
    # a smooth edge: a sharp one would ring around the clicks
    position = np.clip((freqs / rate - cfg.high_band) / cfg.high_band_edge, 0, 1)
    gain = np.sin(0.5 * np.pi * position) ** 2
    high = scipy.fft.irfft(spectrum * np.where(narrow, 0.0, gain), n=n_fft)[:n]
    # round-off of the transform is not noise: digital silence stays silent
    mask = find_clicks(np.where(x == 0, 0.0, high), rate, cfg)
    if not mask.any():
        return x, mask
    tonal = scipy.fft.irfft(np.where(narrow, spectrum, 0.0), n=n_fft)[:n]
    width = max(1, round(cfg.local_window * rate))
    return repair(x, tonal, mask, width), mask


def clean_blocks(x, start, rate, cfg, lo=0, check=None, pool=None, batch=1):
    """Clean `x[lo:]` in blocks fixed in absolute time, one block at a time.

    `x` is `(frames, channels)` and its first frame is frame `start` of the
    recording.  Yields `(i, j, block)`: the frames `x[i:j]`, cleaned, as a
    float64 `(j - i, channels)` array, in order and covering `x[lo:]`.

    Block `k` covers the frames `[k*B, (k+1)*B)` of the recording and is
    computed from `[k*B - M, (k+1)*B + M)`, clipped to what `x` holds.  So
    as long as `x` holds that much around what is wanted -- which the
    filter's buffer margins make sure of -- the result does not depend on
    where `x` starts or ends.

    The channels of a block are independent, so with a `pool` they are
    repaired `batch` at a time side by side: numpy and scipy release the
    GIL for the transforms and the array arithmetic.  `check` is called
    before each batch, so that a cancelled job stops within one of them.
    """
    n = len(x)
    if lo >= n:
        return
    size = cfg.block_frames(rate)
    margin = cfg.margin_frames(rate)
    first = start + lo
    stop = start + n
    channels = x.shape[1]
    k = first // size
    while k * size < stop:
        c0 = max(k * size, first)
        c1 = min((k + 1) * size, stop)
        w0 = max(k * size - margin, start)
        w1 = min((k + 1) * size + margin, stop)
        block = np.array(x[c0 - start : c1 - start], dtype=np.float64)
        # channel-major, so every channel's samples are contiguous
        window = np.ascontiguousarray(x[w0 - start : w1 - start].T, dtype=np.float64)

        def one(c, window=window):
            return remove_clicks(window[c], rate, cfg)

        for b in range(0, channels, batch):
            if check is not None:
                check()
            todo = range(b, min(b + batch, channels))
            if pool is not None and len(todo) > 1:
                results = list(pool.map(one, todo))
            else:
                results = [one(c) for c in todo]
            for c, (out, mask) in zip(todo, results):
                if mask.any():
                    block[:, c] = out[c0 - w0 : c1 - w0]
        yield c0 - start, c1 - start, block
        k += 1
