"""Finding bands to curate: peaks per frame, linked across frames.

Curation needs something to curate.  wavetracker produces bands of its own
and `wavetracker_npy` imports them, but that is a directory a reader either
has or does not, and a plugin that can only open somebody else's output
cannot be tried at all on an ordinary recording -- which is most of them, and
all of the ones in this repository.  So there is a tracker here.

It is deliberately a small one, and says so on screen.  Two stages:

**Peaks.**  In each spectrogram frame, the local maxima that stand far enough
above that frame's own median power.  Per-frame rather than global because a
recording gets louder and quieter -- a gain change, an animal approaching --
and a single global threshold either loses the quiet half or fills the loud
half with noise.

**Links.**  Each peak joins the band whose last frequency is nearest, if that
is within `tolerance_hz`, and starts a new band otherwise.  A band that has
had no peak for longer than `max_gap_s` is closed, so a signal that stops and
restarts becomes two bands rather than one with a bridge across silence -- the
conservative way round, because merging two bands is one keystroke here and
un-merging a wrong bridge is a split at a place nobody can see.

What it is not
--------------

Not a claim to be right.  It is greedy and nearest-neighbour, so it swaps
identity when two bands cross -- the single hardest case, and the one this
whole interface exists to let a reader fix by hand.  It does not follow
harmonics, weight by amplitude continuity, or look ahead.  wavetracker's own
tracker does considerably more and its output should be preferred when it
exists.

Nothing here imports Qt, and the only audian import is the cancellation
vocabulary, so it can be exercised without a window and interrupted with one.
"""

from __future__ import annotations

import numpy as np

from audian.pluginapi import CancelToken

#: How far above a frame's median power a peak must stand to count.
#:
#: The default is deliberately high.  A tracker that returns four hundred
#: fragments has not saved the reader any work -- they must now delete four
#: hundred things -- and one that returns six bands they can see is worth
#: running again with a lower threshold.  Too few is recoverable in one
#: gesture; too many is an afternoon.
DEFAULT_THRESHOLD_DB = 12.0

#: How far a band's frequency may move between one frame and the next.
DEFAULT_TOLERANCE_HZ = 50.0

#: How long a band may go unseen before it is closed rather than bridged.
DEFAULT_MAX_GAP_S = 0.05

#: Bands shorter than this are dropped rather than handed over.
DEFAULT_MIN_DURATION_S = 0.02

#: At most this many peaks are taken from any one frame, strongest first.
#:
#: A bound on the whole run rather than a quality setting: a frame of
#: broadband noise has a local maximum every few bins, and without a cap one
#: such frame contributes hundreds of one-vertex bands that the linker then
#: carries forward.
DEFAULT_MAX_PEAKS = 8


def frame_peaks(
    power_db,
    threshold_db=DEFAULT_THRESHOLD_DB,
    max_peaks=DEFAULT_MAX_PEAKS,
    floor_db=-np.inf,
):
    """Indices of the peaks in one frame, in ascending frequency.

    A bin is a peak when it is greater than both neighbours and stands
    `threshold_db` above the frame's median *and* at or above `floor_db`.
    Strict on both neighbours, so a plateau of equal bins contributes nothing
    rather than contributing all of it -- which matters on quantised or
    heavily smoothed input, where a plateau is common and a peak per bin of
    it is noise.

    Two floors and not one, and the second was put here by measurement.  A
    threshold taken from the frame's own median adapts to a recording that
    gets louder and quieter, which is what it is for -- but between two
    cricket chirps the median *is* the noise, so the same rule promotes the
    loudest noise bins of a silent frame to peaks.  On 18 s of
    ``data/Gryllus_campestris.wav`` that produced 2484 bands, nearly all of
    them a few frames of nothing.  Requiring a peak to clear a floor drawn
    from the whole block as well leaves the quiet frames empty, where they
    belong.
    """
    power_db = np.asarray(power_db, dtype=np.float64)
    if power_db.size < 3:
        return np.zeros(0, dtype=np.int64)
    finite = power_db[np.isfinite(power_db)]
    if finite.size == 0:
        return np.zeros(0, dtype=np.int64)
    floor = max(np.median(finite) + float(threshold_db), float(floor_db))
    inner = power_db[1:-1]
    is_peak = (inner > power_db[:-2]) & (inner > power_db[2:]) & (inner >= floor)
    found = np.flatnonzero(is_peak) + 1
    if found.size > max_peaks:
        found = found[np.argsort(power_db[found])[::-1][:max_peaks]]
    return np.sort(found)


def refine(power_db, index):
    """A peak's position in bins, interpolated to sub-bin precision.

    The parabola through the peak bin and its two neighbours, which is the
    standard estimator for a windowed spectral peak.  Without it a band drawn
    on a spectrogram at nfft 256 is quantised to that grid and steps visibly
    between bins as it drifts, which reads as the *signal* stepping -- a
    picture of the analysis rather than of the animal.

    Falls back to the bare bin when the three points are not a peak (which the
    caller has already excluded) or when the denominator vanishes.
    """
    left, mid, right = power_db[index - 1], power_db[index], power_db[index + 1]
    denom = left - 2.0 * mid + right
    if not np.isfinite(denom) or denom == 0.0:
        return float(index)
    return float(index) + 0.5 * (left - right) / denom


def peaks_of_block(
    times,
    freqs,
    power_db,
    threshold_db=DEFAULT_THRESHOLD_DB,
    max_peaks=DEFAULT_MAX_PEAKS,
    floor_db=None,
    token: CancelToken | None = None,
):
    """One block's peaks, as a list of ``(time, frequencies)`` frames.

    Separated from `link` so that a run over a whole recording can compute
    the spectrogram a chunk at a time and keep only the peaks -- a few
    numbers per frame -- rather than holding every bin of an hour in memory
    at once.  Linking then happens over the accumulated frames, which is why
    a band still runs continuously across a chunk boundary.

    `floor_db` is passed in when a caller has computed it over more than this
    block, which is what a chunked run must do: a floor taken from a chunk
    that happens to be silent is a floor drawn from noise.
    """
    times = np.asarray(times, dtype=np.float64)
    freqs = np.asarray(freqs, dtype=np.float64)
    power_db = np.asarray(power_db, dtype=np.float64)
    if power_db.ndim != 2:
        raise ValueError(f"power must be (n_times, n_freqs), got {power_db.shape}")
    if power_db.shape != (times.size, freqs.size):
        raise ValueError(
            f"power is {power_db.shape} but there are {times.size} times and "
            f"{freqs.size} frequencies"
        )
    if times.size == 0 or freqs.size < 3:
        return []
    if floor_db is None:
        floor_db = block_floor(power_db, threshold_db)
    bins = np.arange(freqs.size)
    frames = []
    for i in range(times.size):
        if token is not None:
            token.check()
        frame = power_db[i]
        found = frame_peaks(frame, threshold_db, max_peaks, floor_db)
        if found.size == 0:
            continue
        positions = np.array([refine(frame, int(p)) for p in found])
        frames.append((float(times[i]), np.interp(positions, bins, freqs)))
    return frames


def block_floor(power_db, threshold_db=DEFAULT_THRESHOLD_DB):
    """The absolute floor a peak must clear, from a whole block's median."""
    power_db = np.asarray(power_db, dtype=np.float64)
    finite = power_db[np.isfinite(power_db)]
    if finite.size == 0:
        return -np.inf
    return float(np.median(finite)) + float(threshold_db)


def link(
    frames,
    tolerance_hz=DEFAULT_TOLERANCE_HZ,
    max_gap_s=DEFAULT_MAX_GAP_S,
    min_duration_s=DEFAULT_MIN_DURATION_S,
    token: CancelToken | None = None,
):
    """Join per-frame peaks into bands.

    `frames` is ``(time, frequencies)`` in ascending time, as
    `peaks_of_block` returns.  The result is ``(times, freqs)`` pairs ordered
    by start time, which is the order a reader reads them in and therefore
    the order the table shows.
    """
    #: open bands, each ``[times, freqs, last_time, last_freq]``
    open_bands: list = []
    closed: list = []

    for now, peak_hz in frames:
        if token is not None:
            token.check()
        # Close what has gone quiet before matching, so a band that timed out
        # cannot capture this frame's peak from a band that is still live.
        still_open = []
        for band in open_bands:
            if now - band[2] > max_gap_s:
                closed.append(band)
            else:
                still_open.append(band)
        open_bands = still_open

        taken = set()
        # Nearest first over all pairs, so the closest match wins the band it
        # is closest to rather than whichever band happened to be earlier in
        # the list -- greedy, but greedy in a defensible order.
        pairs = []
        for bi, band in enumerate(open_bands):
            for pi, hz in enumerate(peak_hz):
                gap = abs(hz - band[3])
                if gap <= tolerance_hz:
                    pairs.append((gap, bi, pi))
        pairs.sort()
        used_bands = set()
        for _, bi, pi in pairs:
            if bi in used_bands or pi in taken:
                continue
            band = open_bands[bi]
            band[0].append(now)
            band[1].append(float(peak_hz[pi]))
            band[2] = now
            band[3] = float(peak_hz[pi])
            used_bands.add(bi)
            taken.add(pi)
        for pi, hz in enumerate(peak_hz):
            if pi in taken:
                continue
            open_bands.append([[now], [float(hz)], now, float(hz)])

    closed.extend(open_bands)
    out = []
    for band in closed:
        t = np.asarray(band[0], dtype=np.float64)
        if t.size == 0 or (t[-1] - t[0]) < min_duration_s:
            continue
        out.append((t, np.asarray(band[1], dtype=np.float64)))
    out.sort(key=lambda pair: pair[0][0])
    return out


def track(
    times,
    freqs,
    power_db,
    threshold_db=DEFAULT_THRESHOLD_DB,
    tolerance_hz=DEFAULT_TOLERANCE_HZ,
    max_gap_s=DEFAULT_MAX_GAP_S,
    min_duration_s=DEFAULT_MIN_DURATION_S,
    max_peaks=DEFAULT_MAX_PEAKS,
    token: CancelToken | None = None,
):
    """Bands in one spectrogram block: `peaks_of_block`, then `link`.

    The whole tracker in one call, for a block that fits in memory -- a
    visible window, or a test.  A run over a long recording goes through the
    two halves separately so that the spectrogram can be computed a chunk at
    a time; see the panel's worker.

    `power_db` is ``(n_times, n_freqs)`` in decibels, which is the shape
    audian's own spectrogram buffer is in once a channel has been taken out
    of it.
    """
    frames = peaks_of_block(
        times, freqs, power_db, threshold_db, max_peaks, None, token
    )
    return link(frames, tolerance_hz, max_gap_s, min_duration_s, token)
