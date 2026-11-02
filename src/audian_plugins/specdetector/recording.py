"""Reading a recording in the model runner, without importing audian.

`audian.pluginapi.open_files` is what a plugin should read through, and the
panel does.  The runner cannot: importing anything from ``audian`` runs
``audian/__init__.py``, which imports the whole window and its Qt, into a process
that has just loaded OpenCV with a Qt of its own.  So this opens files the same
way, by the same two steps, and ``tests/test_specdetector_runner.py`` holds the
two to the same frames on a split recording.

The step that matters is the widened continuity tolerance.  A bare
`thunderlab.DataLoader` compares each file's timestamp with the end of the file
before and silently drops everything after the first mismatch, which on a TASCAM
session is always the short final file (`audian.data.open_files` has the
measurement: 825 s of a 60 min session gone without a word).
"""

from __future__ import annotations

import os
from typing import Sequence

import numpy as np

#: Seconds of recording the loader holds.  Short and with no back buffer, because
#: a tile sweep reads forward and a SAM view reads one window.
BUFFER_S = 10.0


def open_loader(paths: Sequence[str], tbuffer: float = BUFFER_S):
    """A `DataLoader` spanning every file in `paths`, or ValueError."""
    from thunderlab.dataloader import DataLoader

    paths = [os.fspath(p) for p in paths]
    if not paths:
        raise ValueError("a recording needs at least one file")
    if len(paths) == 1:
        return DataLoader(paths[0], tbuffer, 0.0, verbose=0)
    loader = DataLoader()
    # A year and not infinity: `timedelta` raises OverflowError on infinity.
    loader._max_time_diff = 365 * 24 * 3600
    try:
        loader.open_multiple(paths, tbuffer, 0.0, verbose=0)
    except Exception:
        loader.close()
        raise
    total = _header_frames(paths)
    if total is not None and loader.frames != total:
        frames = loader.frames
        loader.close()
        raise ValueError(
            f"the loader returned {frames} frames, but the {len(paths)} files "
            f"hold {total}"
        )
    return loader


def _header_frames(paths):
    """How many frames the file headers promise, or None if one cannot say."""
    import soundfile as sf

    total = 0
    for path in paths:
        try:
            total += int(sf.info(path).frames)
        except Exception:  # noqa: BLE001 - "no opinion": the loader reports its own
            return None
    return total


class Recording:
    """Random access to a recording, by sample.

    The interface `tiling.tile_samples` reads through.  Holds no state a caller
    can disturb, and a `read` past the end is short rather than an error.
    """

    def __init__(self, paths, tbuffer: float = BUFFER_S):
        if isinstance(paths, (str, os.PathLike)):
            paths = [paths]
        self.paths = [os.fspath(p) for p in paths]
        self.loader = open_loader(self.paths, tbuffer)
        self.rate = float(self.loader.rate)
        self.frames = int(len(self.loader))
        self.channels = int(self.loader.channels)

    @property
    def duration(self) -> float:
        return self.frames / self.rate if self.rate else 0.0

    def read(self, a: int, b: int) -> np.ndarray:
        """Samples ``[a, b)`` as ``(n, channels)`` float32."""
        a, b = max(int(a), 0), min(int(b), self.frames)
        if b <= a:
            return np.zeros((0, self.channels), dtype=np.float32)
        block = np.asarray(self.loader[a:b], dtype=np.float32)
        return block.reshape(b - a, -1)

    def close(self) -> None:
        try:
            self.loader.close()
        except Exception:  # noqa: BLE001 - closing must not raise at teardown
            pass

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()
