"""Stub of wavetracker.io."""

import os
from dataclasses import dataclass

MULTI_INPUT = True

RATE = 1000.0
FRAMES = 100_000
CHANNELS = 2


@dataclass(frozen=True)
class RecordingInfo:
    path: str
    rate: float
    channels: int
    frames: int


def recording_info(path):
    paths = path if isinstance(path, (list, tuple)) else [path]
    for p in paths:
        if not os.path.isfile(p):
            raise FileNotFoundError(p)
    return RecordingInfo(str(paths[0]), RATE, CHANNELS, FRAMES * len(paths))
