"""Denoising a spectrogram, in layers.

A spectrogram of an electrode array holds three kinds of thing, and only one
of them is noise.  Wave-type fish draw horizontal lines -- a fundamental held
for minutes or hours.  Pulse-type fish draw vertical ones -- a discharge
broadband enough to cross the whole panel in a millisecond.  Mains hum and
cable pickup draw horizontal lines too, which is exactly why they are hard:
on one channel a resting *Sternopygus* at 50 Hz and a mains harmonic at
50 Hz are the same picture, and no filter that reads one channel can tell
them apart without deleting the fish.

Two axes separate them, and the two denoisers here take one each.

`spatial_coherence` uses **space**.  A fish sits somewhere in the water and
its field falls off with distance, so its power differs from electrode to
electrode; pickup arrives on the cable and lands on every electrode at once.
That is the axis a single-channel denoiser cannot see, and it needs no
knowledge of where in the spectrum the interference is.

`mains_comb` uses **where**.  Interference from a mains supply is not
anywhere -- it is at the supply frequency and its multiples, and nowhere
else.  Knowing that lets it be surgical, touching a few narrow bands and
leaving the rest of the panel untouched, which the spatial gate cannot
promise.  It is the one to reach for on a recording with too few electrodes
for a spatial measure to mean anything.

Layers, not alternatives
------------------------

Denoisers stack: every enabled one runs, in the order `DENOISERS` lists
them, each on what the last returned.  That order is a claim and not an
accident -- `mains_comb` estimates a local noise floor from the bins
either side of each harmonic, so it has to read a spectrum that has not
already had holes gated into it.  Put a new denoiser that measures the
floor early, and one that only masks late.

Adding another
--------------

`DENOISERS` is the registry that both the Spectrogram menu and the side
panel build themselves from: append an entry, declare its `Parameter`
rows, and a checkbox with its own controls appears with no further
wiring.  Each denoiser is handed the chunk `BufferedSpectrogram.process()`
just transformed, shaped ``(time, channel, frequency)`` and holding power,
the frequency of each bin, and a dict of its own parameter values; it
returns an array of the same shape.

The one hard requirement is that a denoiser be **pointwise in time**: it
may look across channels and across frequency, but it may not look at
neighbouring columns.  `process()` transforms the buffer in hop-aligned
chunks of `chunk_columns` and relies on the result being bit-identical to
transforming the whole buffer at once (`tests/test_chunked_dsp.py`); a
filter with any time extent would see a different neighbourhood at a chunk
boundary and break that.  Both denoisers here are pointwise, and
`tests/test_denoise.py` asserts it of every entry in the registry -- so the
constraint is enforced on the next one rather than remembered.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

import numpy as np

#: Channels quieter than this fraction of the loudest one, in the same
#: time-frequency bin, are left out of the spread that `spatial_coherence`
#: measures.
#:
#: Not a tuning knob -- a guard against dead electrodes.  Four of the
#: sixteen channels of the flona block are recorded as exactly zero (see
#: `bufferedspectrogram.channel_power`), and a spread measured as
#: max-over-min would read them as infinite contrast and gate nothing at
#: all.  A relative floor drops them without needing to know in advance
#: which electrodes are dead, and 1e-9 -- 90 dB down -- is far below any
#: real far-field level, so no live channel is ever excluded by it.
DEAD_CHANNEL_FLOOR = 1e-9


@dataclass(frozen=True)
class Parameter:
    """One number a denoiser takes, and everything the panel needs to draw it.

    Declared rather than hand-built so that the side panel and the settings
    file are both generated from the registry: a denoiser that grows a
    parameter grows a row, and one that is renamed does not leave a widget
    behind wired to nothing.

    Attributes
    ----------
    key
        Name in the denoiser's own value dict.  Persisted, so it may not
        change once released.
    label
        Row caption in the side panel.
    default, minimum, maximum, step
        The spin box's value, bounds and single step.
    suffix
        Unit shown in the box -- "Hz", "dB", "%", or nothing.
    decimals
        Significant digits the box shows.  `pg.SpinBox` formats with
        ``%g``, so this is significant digits and not places.
    integer
        Whether the value is a count.  Drawn without a slider, since a
        slider over a handful of integers is worse than a box.
    """

    key: str
    label: str
    default: float
    minimum: float
    maximum: float
    step: float = 1.0
    suffix: str = ""
    decimals: int = 4
    integer: bool = False

    def clamp(self, value: float) -> float:
        value = float(np.clip(value, self.minimum, self.maximum))
        return float(round(value)) if self.integer else value


def spatial_coherence(
    block: np.ndarray,
    frequencies: np.ndarray,
    values: Mapping[str, float],
) -> np.ndarray:
    """Suppress bins whose power is the same on every electrode.

    For each time-frequency bin the spread of power across channels is
    measured as ``max/min`` in dB.  A source in the water is peaked on the
    electrodes nearest it and so has a large spread; pickup that arrives
    equally everywhere has none.  Bins below the threshold are attenuated,
    bins above it are kept.

    The gate is soft -- a logistic in dB of spread, `softness` wide --
    rather than a cut.  A hard threshold on a quantity this noisy speckles
    the panel, and speckle in a spectrogram reads as signal.

    Measured on a synthetic four-electrode recording with known contents
    (``wavefish_4ch_hard.wav``): mains hum 1.0x spread at 50, 100 and
    150 Hz; the five wave fish 9.8x, 14.0x, 22.7x, 335x and 509x.  That is
    0 dB against 9.9 dB for the weakest fish, which is where the 6 dB
    default sits.  Real pickup will not be as perfectly common-mode as a
    synthetic one -- electrode impedances, cable lengths and ground loops
    all break it -- so treat the default as a starting point to be moved,
    not a measurement.

    Returned unchanged when there are fewer than two channels: with one
    electrode there is no spread to measure.
    """
    if block.ndim != 3 or block.shape[1] < 2:
        return block

    threshold_db = float(values.get("threshold", 6.0))
    softness_db = float(values.get("softness", 3.0))

    hi = block.max(axis=1)
    # Ignore channels that carry no power at all before taking the minimum,
    # so a dead electrode does not read as infinite contrast.
    floor = hi * DEAD_CHANNEL_FLOOR
    lo = np.where(block > floor[:, None, :], block, np.inf).min(axis=1)
    lo = np.where(np.isfinite(lo), lo, hi)

    with np.errstate(divide="ignore", invalid="ignore"):
        spread_db = 10.0 * np.log10(hi / lo)
    spread_db = np.nan_to_num(spread_db, nan=0.0, posinf=0.0, neginf=0.0)

    if softness_db <= 0:
        mask = (spread_db >= threshold_db).astype(block.dtype)
    else:
        # Clipped before exp() so a bin 700 dB from the threshold -- which a
        # silent channel produces -- overflows to 0 or 1 instead of to inf.
        z = np.clip((spread_db - threshold_db) / softness_db, -60.0, 60.0)
        mask = 1.0 / (1.0 + np.exp(-z))

    return block * mask[:, None, :]


#: How far past a notch, in multiples of its half-width, the band that
#: estimates the floor under it reaches.
#:
#: The reference band has to be clear of the interference and close enough
#: to be the same noise floor.  Three half-widths gives a band twice as
#: wide as the notch on each side, which at the 1 Hz default is 50-47 Hz
#: and 53-56 Hz around a 50 Hz harmonic.
MAINS_REFERENCE_SPAN = 3.0


def mains_comb(
    block: np.ndarray,
    frequencies: np.ndarray,
    values: Mapping[str, float],
) -> np.ndarray:
    """Pull each mains harmonic down to the noise floor beside it.

    Mains interference is a comb: a needle at the supply frequency and at
    every multiple of it, each a bin or two wide, sitting on top of whatever
    else the recording holds.  This finds the floor *beside* each needle --
    the median power of a reference band just outside the notch -- and
    attenuates the notch towards it.

    Attenuating towards the floor rather than notching to zero is the whole
    design, for two reasons.

    A zeroed notch is a black stripe across the panel, and a black stripe
    reads as missing data rather than as removed interference.

    More importantly, **a broadband transient crossing the comb survives**.
    An electric-eel discharge is broadband: at the instant of a pulse the
    50 Hz bin holds pulse energy at much the same level as its neighbours,
    so the floor measured beside it is just as high, the excess is near
    zero, and almost nothing is taken away.  A hard notch would punch a hole
    through every pulse in the recording at every harmonic.  What the comb
    removes is what stands *above* its own surroundings, which is what a
    needle is and a broadband pulse is not.

    What it cannot do is separate a wave fish sitting on a harmonic from the
    harmonic: a *Sternopygus* holding 50.0 Hz is a needle at 50.0 Hz, and
    this will pull it down.  That is not a defect to be tuned away, it is
    the limit of asking one channel about one frequency -- and it is why
    `spatial_coherence` exists and why `width` should be kept narrow.

    Parameters, from `values`
    -------------------------
    frequency
        Supply frequency: 50 Hz across most of the world, 60 in the
        Americas and parts of Asia.  The reason this is a parameter at all.
    harmonics
        How many multiples of it to treat, counting the fundamental.
    width
        Half-width of each notch, in Hz.  It has to cover the interference
        and not just its centre: the floor is measured from a band just
        outside the notch, so a notch narrower than the hum measures the
        floor *on* the hum, finds no excess, and removes nothing at all.
        That is the first knob to widen when hum survives this filter.
    strength
        Percentage of the excess over the floor to remove.  At 100 the
        harmonic is pulled all the way down to its surroundings.
    """
    if block.ndim != 3 or block.shape[2] != len(frequencies):
        return block

    f0 = float(values.get("frequency", 50.0))
    harmonics = int(values.get("harmonics", 8))
    width = float(values.get("width", 1.0))
    # Declared as a percentage because that is what the panel shows.
    strength = float(np.clip(values.get("strength", 100.0), 0.0, 100.0))/100.0
    if f0 <= 0 or harmonics < 1 or width <= 0 or strength <= 0:
        return block

    freqs = np.asarray(frequencies, dtype=float)
    if len(freqs) < 2:
        return block
    nyquist = freqs[-1]

    # Widths are in Hz, but what the notch can actually select is bins --
    # and how wide a bin is moves with the Fourier window the reader is
    # holding.  At nfft 256 and 8 kHz a bin is 31 Hz, so the 1 Hz default
    # would select one bin and leave no room beside it for a reference
    # band, and the filter would silently do nothing at exactly the
    # resolution someone scanning a long recording is using.  So the Hz
    # figures are floored at the bin spacing: `width` is what it says
    # wherever the resolution can express it, and degrades to the finest
    # comb this spectrogram can draw where it cannot.
    spacing = float(freqs[1] - freqs[0])
    half = max(width, spacing)
    outer = max(MAINS_REFERENCE_SPAN*half, half + 2.0*spacing)
    out = None

    for h in range(1, harmonics + 1):
        centre = h * f0
        if centre > nyquist:
            break
        offset = np.abs(freqs - centre)
        notch = offset <= half
        if not notch.any():
            continue
        reference = (offset > half) & (offset <= outer)
        if not reference.any():
            # No room beside this harmonic -- at the very top of the band.
            # Leaving it alone beats guessing a floor.
            continue

        if out is None:
            out = block.copy()
        # (time, channel, 1): one floor per column per channel, so a
        # harmonic that is loud on one electrode and absent on another is
        # treated separately on each -- which is what a real array does.
        floor = np.median(out[:, :, reference], axis=2, keepdims=True)
        excess = np.maximum(out[:, :, notch] - floor, 0.0)
        out[:, :, notch] -= strength * excess

    return block if out is None else out


@dataclass(frozen=True)
class Denoiser:
    """One layer of the Spectrogram menu's denoising list.

    Attributes
    ----------
    key
        Stable identifier.  What the settings file carries, so it may not
        change once released.
    name
        What the menu says.
    apply
        ``(block, frequencies, values) -> block``.
    params
        The numbers it takes, in the order the side panel draws them.
    min_channels
        Below this many channels the entry is shown but disabled.  A
        spatial measure needs at least two electrodes; there is nothing
        useful to say about a mono recording.
    tip
        One line, for the menu's tool tip.
    """

    key: str
    name: str
    apply: Callable[[np.ndarray, np.ndarray, Mapping[str, float]], np.ndarray]
    params: tuple[Parameter, ...] = ()
    min_channels: int = 1
    tip: str = ""

    def defaults(self) -> dict[str, float]:
        return {p.key: p.default for p in self.params}

    def parameter(self, key: str) -> Parameter | None:
        for p in self.params:
            if p.key == key:
                return p
        return None


#: Every denoiser, in the order they are applied to a chunk.
#:
#: `mains` is first because it measures the noise floor beside each
#: harmonic, and wants a spectrum that has not already been gated.
DENOISERS = (
    Denoiser(
        key="mains",
        name="&Mains hum",
        apply=mains_comb,
        params=(
            Parameter("frequency", "Frequency", 50.0, 1.0, 1000.0,
                      step=10.0, suffix="Hz", decimals=5),
            Parameter("harmonics", "Harmonics", 8, 1, 200,
                      step=1, integer=True),
            Parameter("width", "Width", 1.0, 0.05, 50.0,
                      step=0.25, suffix="Hz", decimals=4),
            Parameter("strength", "Strength", 100.0, 0.0, 100.0,
                      step=5.0, suffix="%", decimals=4),
        ),
        tip="Pull the supply frequency and its multiples down to the noise "
        "floor beside them.  50 Hz in Europe, 60 in the Americas.",
    ),
    Denoiser(
        key="spatial",
        name="&Spatial coherence",
        apply=spatial_coherence,
        params=(
            Parameter("threshold", "Threshold", 6.0, 0.0, 60.0,
                      step=1.0, suffix="dB", decimals=4),
            Parameter("softness", "Softness", 3.0, 0.0, 20.0,
                      step=0.5, suffix="dB", decimals=4),
        ),
        min_channels=2,
        tip="Attenuate what arrives equally on every electrode -- mains hum "
        "and cable pickup, which a fish never is.",
    ),
)

#: Registry order, which is also the order a chain applies them in.
KEYS = tuple(d.key for d in DENOISERS)


def denoiser(key: str) -> Denoiser | None:
    """The entry named `key`, or None.

    Returning None rather than raising: `key` can come from a settings file
    written by a version that had a denoiser this one does not, and losing
    the setting is a better answer than refusing to open the recording.
    """
    for d in DENOISERS:
        if d.key == key:
            return d
    return None


def defaults() -> dict[str, dict[str, float]]:
    """Every denoiser's parameters at their default values."""
    return {d.key: d.defaults() for d in DENOISERS}


def ordered(keys) -> tuple[str, ...]:
    """`keys` reduced to known ones, deduplicated, in registry order.

    The chain's order is the registry's and never the order a reader
    happened to tick the boxes in, so that the picture depends on what is
    enabled and not on how it came to be.
    """
    wanted = set(keys)
    return tuple(k for k in KEYS if k in wanted)


def apply_chain(
    block: np.ndarray,
    frequencies: np.ndarray,
    enabled: Sequence[str],
    params: Mapping[str, Mapping[str, float]],
) -> np.ndarray:
    """Run every enabled denoiser over `block`, in registry order.

    `block` is not modified; with nothing enabled it is returned as it came.
    """
    out = block
    for key in ordered(enabled):
        entry = denoiser(key)
        if entry is None:
            continue
        values = dict(entry.defaults())
        values.update(params.get(key, {}))
        out = entry.apply(out, frequencies, values)
    return out
