"""Denoising a spectrogram, chosen from a menu.

A spectrogram of an electrode array holds three kinds of thing, and only one
of them is noise.  Wave-type fish draw horizontal lines -- a fundamental held
for minutes or hours.  Pulse-type fish draw vertical ones -- a discharge
broadband enough to cross the whole panel in a millisecond.  Mains hum and
cable pickup draw horizontal lines too, which is exactly why they are hard: on
one channel a resting *Sternopygus* at 50 Hz and a mains harmonic at 50 Hz
are the same picture, and no filter that reads one channel can tell them
apart without deleting the fish.

What separates them is the array.  A fish sits somewhere in the water and
its field falls off with distance, so its power differs from electrode to
electrode.  Pickup arrives on the cable and lands on every electrode at
once.  That is a difference in *space*, not in time or frequency, and it is
the axis a single-channel denoiser cannot see.

Adding another
--------------

`DENOISERS` is the registry the Spectrogram menu builds itself from: append
an entry and a radio item appears, with no further wiring.  Each one is
handed the chunk `BufferedSpectrogram.process()` just transformed, shaped
``(time, channel, frequency)`` and holding power, and returns an array of
the same shape.

The one hard requirement is that a denoiser be **pointwise in time**: it may
look across channels and across frequency, but it may not look at
neighbouring columns.  `process()` transforms the buffer in hop-aligned
chunks of `chunk_columns` and relies on the result being bit-identical to
transforming the whole buffer at once (`tests/test_chunked_dsp.py`); a
filter with any time extent would see a different neighbourhood at a chunk
boundary and break that.  Spatial coherence is pointwise and so is safe.  A
median-filter harmonic/percussive split is *not*, and would need its own
buffered stage rather than a place in this registry.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

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


def spatial_coherence(
    block: np.ndarray,
    threshold_db: float = 6.0,
    softness_db: float = 3.0,
) -> np.ndarray:
    """Suppress bins whose power is the same on every electrode.

    For each time-frequency bin the spread of power across channels is
    measured as ``max/min`` in dB.  A source in the water is peaked on the
    electrodes nearest it and so has a large spread; pickup that arrives
    equally everywhere has none.  Bins below `threshold_db` of spread are
    attenuated, bins above it are kept.

    The gate is soft -- a logistic in dB of spread, `softness_db` wide --
    rather than a cut.  A hard threshold on a quantity this noisy speckles
    the panel, and speckle in a spectrogram reads as signal.

    Measured on a synthetic four-electrode recording with known contents
    (``wavefish_4ch_hard.wav``): mains hum 1.0x spread at 50, 100 and 150 Hz;
    the five wave fish 9.8x, 14.0x, 22.7x, 335x and 509x.  That is 0 dB
    against 9.9 dB for the weakest fish, which is where the 6 dB default
    sits.  Real pickup will not be as perfectly common-mode as a synthetic
    one -- electrode impedances, cable lengths and ground loops all break
    it -- so treat the default as a starting point to be moved, not a
    measurement.

    Parameters
    ----------
    block: 3-D array of float
        Power, shaped ``(time, channel, frequency)``.
    threshold_db: float
        Spread, in dB, at which the gate is half open.
    softness_db: float
        Width of the logistic, in dB.  Smaller is closer to a hard cut.

    Returns
    -------
    denoised: 3-D array of float
        `block` with common-mode bins attenuated.  `block` itself is left
        alone.  Returned unchanged when there are fewer than two channels,
        since with one electrode there is no spread to measure.
    """
    if block.ndim != 3 or block.shape[1] < 2:
        return block

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


@dataclass(frozen=True)
class Denoiser:
    """One entry of the Spectrogram menu's denoising list.

    Attributes
    ----------
    key
        Stable identifier.  What a settings file or a command line would
        carry, so it may not change once released.
    name
        What the menu says.
    apply
        ``(block, threshold_db, softness_db) -> block``.  `None` for the
        "off" entry, which is a real entry rather than an absence so that
        the menu has something to check when nothing is being done.
    min_channels
        Below this many channels the entry is shown but disabled.  A
        spatial measure needs at least two electrodes and there is nothing
        useful to say about a mono recording.
    tip
        One line, for the menu's status tip.
    """

    key: str
    name: str
    apply: Callable[[np.ndarray, float, float], np.ndarray] | None
    min_channels: int = 1
    tip: str = ""


#: Every denoiser the Spectrogram menu offers, in menu order.  `NONE_KEY`
#: is first and is the default.
DENOISERS = (
    Denoiser(
        key="none",
        name="&None",
        apply=None,
        tip="Show the spectrogram as computed.",
    ),
    Denoiser(
        key="spatial",
        name="&Spatial coherence",
        apply=spatial_coherence,
        min_channels=2,
        tip="Attenuate what arrives equally on every electrode -- mains hum "
        "and cable pickup, which a fish never is.",
    ),
)

NONE_KEY = DENOISERS[0].key

DEFAULT_THRESHOLD_DB = 6.0
DEFAULT_SOFTNESS_DB = 3.0

#: Bounds for the threshold the menu steps through.  Zero is "gate anything
#: with no spread at all"; 40 dB is past the point where all but the most
#: sharply localised source is gone, which is worth being able to see.
MIN_THRESHOLD_DB = 0.0
MAX_THRESHOLD_DB = 40.0
THRESHOLD_STEP_DB = 1.0


def denoiser(key: str) -> Denoiser:
    """The entry named `key`, or the "off" one if there is no such entry.

    Falling back rather than raising: `key` can come from a settings file
    written by a version that had a denoiser this one does not, and losing
    the setting is a better answer than refusing to open the recording.
    """
    for d in DENOISERS:
        if d.key == key:
            return d
    return DENOISERS[0]
