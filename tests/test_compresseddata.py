"""Regression tests for CompressedData length derivation.

`times` and `datas` used to be sized by two independent expressions that
agreed only for some values of `max_pixel`.  On a 16 channel file at
`max_pixel = 3440` they came out 1602 vs 1601, `PlotCurveItem.setData()`
raised inside a QTimer slot and the navigator stayed blank -- so whether
the overview rendered depended on the width of the user's monitor.

Run standalone (`python tests/test_compresseddata.py`) or under pytest.
"""

import os
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, os.fspath(Path(__file__).resolve().parents[1] / "src"))

from thunderlab.dataloader import DataLoader  # noqa: E402

from audian.compresseddata import CompressedData  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
# Monitor widths that matter here: laptop, 1080p, 1440p and the user's
# ultrawide.  The 1601/1602 mismatch only showed up on some of them.
MAX_PIXELS = (800, 1920, 2560, 3440)
FILES = [
    REPO / ".devdata" / "eelgrid_16ch.wav",
    REPO / "data" / "Gryllus_campestris.wav",
]


def _layouts(file_path):
    """(max_pixel, step, n) for every tested monitor width."""
    with DataLoader(os.fspath(file_path), 60.0, 0.0, verbose=0) as data:
        data.set_unwrap(0, False, False, data.unit)
        compress = CompressedData(data)
        for max_pixel in MAX_PIXELS:
            step, n, nblock = compress.compression_layout(max_pixel)
            yield max_pixel, step, n, nblock, data.frames, data.channels


def test_layout_lengths_agree():
    """times and datas are both sized from the same `n`."""
    for file_path in FILES:
        if not file_path.exists():
            continue
        for max_pixel, step, n, _, frames, _ in _layouts(file_path):
            segments = np.arange(0, frames, step)
            times = np.arange(n) * (step / 2)
            datas = np.zeros((n, 1))
            assert len(times) == len(datas), (
                f"{file_path.name} at {max_pixel}px: "
                f"{len(times)} times vs {len(datas)} rows"
            )
            assert n == 2 * len(segments), (
                f"{file_path.name} at {max_pixel}px: "
                f"n={n} but 2*segments={2 * len(segments)}"
            )


def test_start_produces_matching_arrays():
    """The arrays CompressedData actually hands to setData match."""
    for file_path in FILES:
        if not file_path.exists():
            continue
        for max_pixel in MAX_PIXELS:
            with DataLoader(os.fspath(file_path), 60.0, 0.0, verbose=0) as data:
                data.set_unwrap(0, False, False, data.unit)
                compress = CompressedData(data)
                compress.start(max_pixel, {})
                compress.wait()
                assert compress.times is not None, f"{file_path.name}: no times"
                assert compress.datas is not None, f"{file_path.name}: no datas"
                assert len(compress.times) == len(compress.datas), (
                    f"{file_path.name} at {max_pixel}px: "
                    f"{len(compress.times)} times vs {len(compress.datas)} rows"
                )
                assert compress.datas.shape[1] == data.channels


def test_pool_is_bounded():
    """The worker pool is capped in both process count and memory."""
    file_path = REPO / ".devdata" / "eelgrid_16ch.wav"
    if not file_path.exists():
        return
    with DataLoader(os.fspath(file_path), 60.0, 0.0, verbose=0) as data:
        compress = CompressedData(data)
        step, _, nblock = compress.compression_layout(1920)
        nprocs, nblock = compress.pool_size(step, nblock)
        assert nprocs <= CompressedData.max_procs
        assert nblock % step == 0, "nblock must stay a multiple of step"
        total = nprocs * nblock * data.channels * 8
        assert total <= CompressedData.max_pool_bytes, (
            f"{total / 1e6:.0f} MB of block buffers"
        )


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                failed += 1
                print(f"FAIL {name}: {e}")
    sys.exit(1 if failed else 0)
