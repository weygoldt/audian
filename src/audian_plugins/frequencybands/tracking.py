"""Finding bands to curate: fundamentals per frame, linked across frames.

Two ways of answering "what is in this frame", and one way of joining the
answers up.

**Harmonic groups**, and the default, from `thunderfish.harmonics`: the same
finder wavetracker itself uses.  It groups a peak with its own multiples and
reports the *fundamental*, so a fish with four audible harmonics is one band
rather than four, and it identifies the mains and its partials separately so
a 50 Hz hum is not tracked as a fish.  `harmonic_frames`.

**Strongest peaks**, the fallback: every local maximum that stands far enough
above the noise, with no notion of harmonics at all.  It is what runs when
thunderfish is not installed, and it is the honest choice for a signal that
has no harmonic structure to group -- but on the reference recording it
returned 62, 124, 187 and 249 Hz as four separate tracks of one animal, which
is what the reader would then have had to undo by hand.  `peaks_of_block`.

`link` joins either one's output into bands.

Curation needs something to curate.  wavetracker produces bands of its own
and `wavetracker` imports them, but that is a directory a reader either has
or does not, and a plugin that can only open somebody else's output cannot be
tried on an ordinary recording -- which is most of them, and all of the ones
in this repository.

Why the peak finder is still here
---------------------------------

thunderfish is a dependency of this plugin and not of audian, so an
installation without it must still work; `harmonics_available` is what the
panel asks, and the peak finder is what it falls back to.  It is also the
right answer for a signal with no harmonic structure to group.

**Peaks.**  In each spectrogram frame, the local maxima that stand far enough
above that frame's own median power *and* above a floor drawn from the whole
block.  Per-frame because a recording gets louder and quieter -- a gain
change, an animal approaching -- and a single global threshold either loses
the quiet half or fills the loud half with noise; the block floor because a
per-frame threshold alone promotes the loudest noise of a silent frame, which
is measured in `frame_peaks`.

**Links.**  Each fundamental joins the band whose last frequency is nearest,
if that is within `tolerance_hz`, and starts a new band otherwise.  A band
that has gone unseen for longer than `max_gap_s` is closed, so a signal that
stops and restarts becomes two bands rather than one bridged across silence
-- the conservative way round, because merging two bands is one click here
and un-merging a wrong bridge is a split at a place nobody can see.

What it is not
--------------

Not a claim to be right, and it does not need to be: everything here feeds an
interface whose purpose is a person correcting it.

`link` is greedy and nearest-neighbour, so it swaps identity where two bands
cross and it does not weight by amplitude continuity or look ahead.  That is
the single hardest case in tracking and the one this whole plugin exists to
let a reader repair -- on the reference recording two Eigenmannia pass within
1 Hz of each other, and the truth file says outright that they are "separable
only by the per-electrode amplitude signature", which is a tracking problem
and not a curation one.

wavetracker's own tracker does considerably more, and its output should be
preferred when it exists: `wavetracker.import_directory` is how.

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


#: Mains frequency thunderfish is told to discount, in Hz.
#:
#: 50 rather than thunderfish's own default of 60, because this is a
#: European laboratory's tool and the recordings it is aimed at were made
#: beside 50 Hz wiring.  It is a control on the panel, and "off" is one of
#: its entries -- a tank running on batteries has no hum to discount and
#: discounting one throws away a real fish at that frequency.
DEFAULT_MAINS_HZ = 50.0

#: How many harmonics a group must have before it is believed.
#:
#: Three, which is thunderfish's own default, and it was measured rather
#: than inherited.  Two was tried first, on the argument that a group here is
#: only a candidate a reader is about to look at and that a fundamental
#: missed where its third harmonic dipped below the floor is a band broken
#: into pieces somebody must merge.  What two actually does is invent fish in
#: noise: on eight seconds of a single 62 Hz tone in white noise it reported
#: fundamentals at 464, 553, 617 and 755 Hz alongside the real one, groups
#: assembled out of two coincidental peaks.  A fragmented band costs one
#: click to repair and a phantom costs a reader deciding whether it is real.
DEFAULT_MIN_GROUP_SIZE = 3

#: Lowest fundamental to look for.  Above zero because the very bottom of a
#: spectrogram is DC and drift rather than signal.
DEFAULT_MIN_HZ = 20.0


def harmonics_available() -> bool:
    """Whether thunderfish's harmonic group finder can be imported.

    Checked rather than assumed: thunderfish is a dependency of this plugin
    and not of audian, so an audian installed without the extra still opens
    every recording and still curates bands -- it just finds them with
    `peaks_of_block` instead, and the panel says so rather than raising an
    ImportError at the reader.
    """
    try:
        from thunderfish.harmonics import harmonic_groups  # noqa: F401
    except Exception:  # noqa: BLE001 - an optional dependency, absent is fine
        return False
    return True


def harmonic_frames(
    times,
    freqs,
    power,
    mains_hz=DEFAULT_MAINS_HZ,
    min_hz=DEFAULT_MIN_HZ,
    max_hz=2000.0,
    min_group_size=DEFAULT_MIN_GROUP_SIZE,
    max_peaks=DEFAULT_MAX_PEAKS,
    token: CancelToken | None = None,
):
    """One block's *fundamentals*, frame by frame, via thunderfish.

    The same shape as `peaks_of_block` -- ``(time, frequencies)`` per frame,
    ready for `link` -- and a much better answer to the same question.

    `peaks_of_block` finds every strong peak, so one fish with four audible
    harmonics becomes four bands: on the reference recording it returned 62,
    124, 187 and 249 Hz as separate tracks of the same animal, and the
    reader's first job was deleting three of them.  `harmonic_groups` groups
    a peak with its own multiples and reports the *fundamental*, so that fish
    is one band.  It also identifies the mains and its harmonics separately,
    which is what keeps a 50 Hz hum and its 100 and 150 Hz partials from
    being tracked as three fish.

    **`power` is linear**, not decibels: `harmonic_groups` takes its own
    logarithm, and handing it a spectrogram already in dB makes every
    threshold in it meaningless.  This is the one place in the plugin where
    the un-logged spectrogram is wanted, and it is why the panel keeps it.

    ``max_freq`` bounds the *fundamental*, not the spectrum: a harmonic above
    it is still used as evidence for a group whose fundamental is below it,
    which is the whole point of grouping.
    """
    from thunderfish.harmonics import fundamental_freqs, harmonic_groups

    times = np.asarray(times, dtype=np.float64)
    freqs = np.asarray(freqs, dtype=np.float64)
    power = np.asarray(power, dtype=np.float64)
    if power.shape != (times.size, freqs.size):
        raise ValueError(
            f"power is {power.shape} but there are {times.size} times and "
            f"{freqs.size} frequencies"
        )
    kwargs = dict(
        min_freq=float(min_hz),
        max_freq=float(max_hz),
        min_group_size=int(min_group_size),
        max_groups=int(max_peaks),
    )
    if mains_hz and mains_hz > 0:
        kwargs["mains_freq"] = float(mains_hz)
    else:
        # 0 is how thunderfish is told there is no mains to discount
        kwargs["mains_freq"] = 0.0

    frames = []
    for i in range(times.size):
        if token is not None:
            token.check()
        try:
            groups = harmonic_groups(freqs, power[i], **kwargs)[0]
        except Exception:  # noqa: BLE001 - one bad frame, not the whole sweep
            continue
        if not len(groups):
            continue
        found = np.asarray(fundamental_freqs(groups), dtype=np.float64).ravel()
        if not found.size:
            continue
        refined = np.array(
            [
                refine_fundamental(freqs, power[i], group, f0)
                for group, f0 in zip(groups, found)
            ]
        )
        frames.append((float(times[i]), np.sort(refined)))
    return frames


def refine_fundamental(freqs, frame, group, f0: float) -> float:
    """`f0` again, off the frequency grid.

    A fundamental from `harmonic_groups` is built out of peaks that sit on
    spectrogram *bins*, so it lands on a grid of ``df / n`` for whichever
    harmonic ``n`` it was derived from.  On the reference recording that is
    0.24 Hz, against a fish whose frequency drifts over about 2 Hz -- so the
    band drawn from it climbs in visible stair-steps and reads as the animal
    jumping rather than the analysis rounding.

    The fix is the same parabola `refine` fits for the peak finder, applied
    to the group's **strongest** harmonic rather than to the fundamental
    itself: the higher harmonic has the better signal to noise of the two and
    its absolute error is divided by its own number on the way back down, so
    the estimate is finer than the bin spacing rather than tied to it.

    Falls back to `f0` unchanged whenever the arithmetic cannot be trusted --
    a harmonic off the end of the block, a flat top, an unusable order.
    """
    group = np.asarray(group, dtype=np.float64)
    if group.ndim != 2 or group.shape[0] == 0 or f0 <= 0:
        return float(f0)
    strongest = int(np.argmax(group[:, 1])) if group.shape[1] > 1 else 0
    hz = float(group[strongest, 0])
    order = int(round(hz / f0))
    if order < 1 or hz <= 0:
        return float(f0)
    index = int(np.argmin(np.abs(freqs - hz)))
    if index < 1 or index >= freqs.size - 1:
        return float(f0)
    position = refine(frame, index)
    hz_refined = float(np.interp(position, np.arange(freqs.size), freqs))
    if hz_refined <= 0:
        return float(f0)
    estimate = hz_refined / order
    # A refinement that moves the answer by more than half a bin of the
    # fundamental is not a refinement; it means the order was wrong.
    if abs(estimate - f0) > 0.5 * abs(freqs[1] - freqs[0]):
        return float(f0)
    return estimate


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
    frames = list(frames)
    # A band is closed when it has gone *unseen* for longer than max_gap_s,
    # and the ordinary spacing between two frames is not it being unseen.
    # Comparing the bare elapsed time against max_gap_s made the spectrogram's
    # own hop count as a gap, so a max_gap_s below one hop closed every band
    # after a single frame and the tracker returned nothing at all -- with no
    # setting on the panel that explained why.  Measured on a 0.512 s hop, a
    # 0.5 s gap found 28 fundamentals and produced 0 bands.
    #
    # So the hop is measured and added, which also gives max_gap_s = 0 the
    # meaning a reader would expect: join consecutive frames, bridge nothing.
    spacing = 0.0
    if len(frames) > 1:
        steps = np.diff([float(t) for t, _hz in frames])
        steps = steps[steps > 0]
        if steps.size:
            spacing = float(np.median(steps))
    close_after = float(max_gap_s) + spacing

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
            if now - band[2] > close_after:
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
