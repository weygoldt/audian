#!/usr/bin/env python
"""Write the synthetic recording docs/wavetracker-quickstart.md walks through.

    uv run python scripts/wavetracker_demo.py [DIR]

Writes ``DIR/demo.wav`` (default: ./wavetracker-demo), two minutes on four
electrodes, and ``DIR/demo_truth.npz``, what is really in it.  Six fish, chosen
so that tracking it leaves things to correct: two fish cross, one swims out of
reach for a minute and comes back 5 Hz higher, one rises (which wavetracker's
default pipeline follows), and one has a second harmonic strong enough to
mistake for a fish.  Seeded, so everybody gets the same tracks
and the track numbers in the quickstart are the ones on screen.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import wavetracker.synthetic as synthetic

DURATION = 120.0
#: Hz/s on top of the synthesizer's drift: two fish that meet and cross
SLOPES = {650.0: +0.25, 668.0: -0.25}
#: fish that are gone for a while: (start, end, ramp) [s] of their absence
AWAY = {735.0: (30.0, 90.0, 3.0)}
#: Hz a fish's frequency moves while it is away
SHIFT = {735.0: 5.0}


def presence(times, start, end, ramp):
    """1 while the fish is there, 0 while it is away, cosine ramps between."""
    times = np.asarray(times, dtype=float)
    down = np.clip((times - (start - ramp)) / ramp, 0.0, 1.0)
    up = np.clip((times - end) / ramp, 0.0, 1.0)
    return 0.5 * (1 + np.cos(np.pi * down)) + 0.5 * (1 - np.cos(np.pi * up))


def write_demo(directory) -> Path:
    """Write the recording and its ground truth; the path of the recording."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    original = synthetic.frequency_trace

    def trace(fish, times, rng):
        slope = SLOPES.get(fish.base_freq, 0.0)
        f = original(fish, times, rng) + slope * (times - DURATION / 2)
        if fish.base_freq in SHIFT:
            start, end, _ramp = AWAY[fish.base_freq]
            f = f + SHIFT[fish.base_freq] * np.clip(
                (times - start) / (end - start), 0, 1
            )
        return f

    fish = [
        synthetic.Fish(
            415.0,
            drift=1.0,
            amplitude=1.4,
            position=0.3,
            harmonics=(1.0, 0.8, 0.3, 0.15),  # its second harmonic: ~830 Hz
        ),
        synthetic.Fish(
            560.0,
            drift=1.5,
            amplitude=1.0,
            position=0.7,
            rises=[(25.0, 25.0, 6.0), (70.0, 14.0, 4.0), (98.0, 35.0, 8.0)],
        ),
        synthetic.Fish(650.0, drift=0.6, amplitude=0.9, position=0.2),
        synthetic.Fish(668.0, drift=0.6, amplitude=0.8, position=0.8),
        synthetic.Fish(735.0, drift=2.0, amplitude=0.7, position=0.5),
        synthetic.Fish(905.0, drift=1.5, amplitude=0.6, position=0.6),
    ]
    here = [f for f in fish if f.base_freq not in AWAY]
    away = [f for f in fish if f.base_freq in AWAY]
    synthetic.frequency_trace = trace
    try:
        recording = synthetic.synthesize(
            here,
            DURATION,
            rate=20000.0,
            channels=4,
            noise=0.03,
            mains=0.0,
            rng=np.random.default_rng(7),
        )
        # each fish that leaves alone, faded by its presence, then added
        for j, f in enumerate(away):
            alone = synthetic.synthesize(
                [f],
                DURATION,
                rate=20000.0,
                channels=4,
                noise=0.0,
                mains=0.0,
                rng=np.random.default_rng(8 + j),
            )
            t = np.arange(len(alone.data)) / alone.rate
            recording.data += alone.data * presence(t, *AWAY[f.base_freq])[:, None]
            env = presence(alone.truth_times, *AWAY[f.base_freq])
            recording.truth_freqs = np.vstack(
                [recording.truth_freqs, alone.truth_freqs]
            )
            recording.truth_amps = np.concatenate(
                [recording.truth_amps, alone.truth_amps * env[None, :, None]]
            )
    finally:
        synthetic.frequency_trace = original
    path = directory / "demo.wav"
    synthetic.save_recording(recording, path)
    return path


if __name__ == "__main__":
    path = write_demo(sys.argv[1] if len(sys.argv) > 1 else "wavetracker-demo")
    print(f"wrote {path}\nnow run: uv run audian {path}")
