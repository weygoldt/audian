"""The two denoisers audian ships, as a plugin like any other.

Bundled rather than built in, so that a denoiser written elsewhere is the
equal of these: same registration, same panel rows, same place in the chain.
`audian.denoise` holds the contract they are written against and nothing
else.

They take one axis each, and a recording can want both.

`spatial_coherence` asks **whose**.  A fish sits somewhere in the water and
its field falls off with distance, so its power differs from electrode to
electrode; pickup arrives on the cable and lands on every electrode at once.
That is the axis a single-channel denoiser cannot see, and it needs to know
nothing about where in the spectrum the interference is.

`mains_comb` asks **where**.  Interference from a supply is at the supply
frequency and its multiples and nowhere else, so it can be surgical where
the spatial gate cannot -- and it is the only one of the two with anything
to say about a recording with too few electrodes to measure a spread
across.

`mains_comb` runs first: it measures a noise floor from the bins beside
each harmonic, so it wants a spectrum nothing has gated holes into yet.
"""

from audian.pluginapi import Denoiser, Parameter

from .engine import mains_comb, spatial_coherence

__all__ = ["audian_builtin_denoisers", "mains_comb", "spatial_coherence"]


def audian_builtin_denoisers():
    """The denoisers this plugin contributes, in the order they apply."""
    return [
        Denoiser(
            key="mains",
            name="&Mains hum",
            apply=mains_comb,
            order=10,
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
            tip="Pull the supply frequency and its multiples down to the "
            "noise floor beside them.  50 Hz in Europe, 60 in the Americas.",
        ),
        Denoiser(
            key="spatial",
            name="&Spatial coherence",
            apply=spatial_coherence,
            order=20,
            params=(
                Parameter("threshold", "Threshold", 6.0, 0.0, 60.0,
                          step=1.0, suffix="dB", decimals=4),
                Parameter("softness", "Softness", 3.0, 0.0, 20.0,
                          step=0.5, suffix="dB", decimals=4),
            ),
            min_channels=2,
            tip="Attenuate what arrives equally on every electrode -- mains "
            "hum and cable pickup, which a fish never is.",
        ),
    ]
