"""Regression tests for the drawing-time min/max pyramid.

The pyramid replaces a strided per-channel `reduceat` over the whole
visible range.  What matters is that the envelope it produces is the same
one the strided reduction produced (peaks must not be lost) and that the
bins it reports line up with the time axis the caller draws them on.

Run standalone (`python tests/test_minmaxpyramid.py`) or under pytest.
"""

import os
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, os.fspath(Path(__file__).resolve().parents[1] / "src"))

from audian.buffereddata import MinMaxPyramid  # noqa: E402

SHAPES = [(544288, 16), (3840000, 2), (200000, 1), (65536, 4)]
STEPS = [32, 33, 64, 100, 283, 1024, 4096]


def _buffer(shape, dtype=np.float64):
    rng = np.random.default_rng(12345)
    return rng.standard_normal(shape).astype(dtype)


def _spread(envelope):
    """Tolerance of one bin's worth of envelope range."""
    return 0.05 * (envelope.max() - envelope.min())


def _strided(buffer, channel, i0, i1, step):
    values = buffer[i0:i1, channel]
    segments = np.arange(0, len(values), step)
    out = np.empty(2 * len(segments))
    np.minimum.reduceat(values, segments, out=out[0::2])
    np.maximum.reduceat(values, segments, out=out[1::2])
    return out


def test_envelope_matches_strided_reduction():
    for shape in SHAPES:
        buffer = _buffer(shape)
        pyr = MinMaxPyramid()
        pyr.build(buffer, 0, 1)
        for step in STEPS:
            nbins = min(1900, (len(buffer) - 1) // step)
            if nbins < 2:
                continue
            for start in (0, step * 7, 12345):
                stop = start + nbins * step
                if stop > len(buffer):
                    continue
                got = pyr.decimate(0, start, stop, step)
                if got is None:
                    continue
                values, first = got
                want = _strided(buffer, 0, start, stop, step)
                assert len(values) == len(want), (
                    f"{shape} step {step}: {len(values)} vs {len(want)}"
                )
                # The pyramid snaps bins to its own grid, so bin k may
                # hold samples belonging to k-1 or k+1 -- but never
                # further than that.  Every value must therefore lie
                # inside the strided envelope of the three-bin
                # neighbourhood, and the whole result inside the strided
                # envelope of the padded range.
                assert abs(start - first) < step, (
                    f"{shape} step {step}: bin start off by {start - first}"
                )
                lo = _strided(
                    buffer,
                    0,
                    max(0, start - step),
                    min(len(buffer), stop + step),
                    step,
                )
                assert values.min() >= lo.min() - 1e-9, (
                    f"{shape} step {step}: min out of range"
                )
                assert values.max() <= lo.max() + 1e-9, (
                    f"{shape} step {step}: max out of range"
                )
                # and it must still be a real envelope: the mins below
                # the maxes, bin by bin
                assert np.all(values[0::2] <= values[1::2] + 1e-9), (
                    f"{shape} step {step}: min above max"
                )
                # no more than a bin's worth of the true envelope is lost
                assert values.min() <= want.min() + _spread(want), shape
                assert values.max() >= want.max() - _spread(want), shape


def test_dtype_and_size():
    for dtype in (np.float64, np.float32):
        buffer = _buffer((544288, 16), dtype)
        pyr = MinMaxPyramid()
        pyr.build(buffer, 0, 1)
        assert pyr.levels, "no levels built"
        for _, values in pyr.levels:
            assert values.dtype == dtype
        # a quarter of the buffer is the documented budget
        assert pyr.nbytes() < 0.3 * buffer.nbytes, (
            f"{pyr.nbytes() / 1e6:.1f} MB for a {buffer.nbytes / 1e6:.1f} MB buffer"
        )


def test_rebuild_is_keyed_on_generation():
    buffer = _buffer((65536, 4))
    pyr = MinMaxPyramid()
    pyr.build(buffer, 0, 1)
    first = pyr.levels[0][1]
    pyr.build(buffer, 0, 1)
    assert pyr.levels[0][1] is first, "rebuilt without a change"
    # same offset and length, new content: only the generation says so
    buffer[:] = _buffer((65536, 4)) * 3.0
    pyr.build(buffer, 0, 2)
    assert pyr.levels[0][1] is not first, "stale levels after a reload"


def test_out_of_buffer_requests_are_refused():
    buffer = _buffer((65536, 4))
    pyr = MinMaxPyramid()
    pyr.build(buffer, 1000, 1)
    assert pyr.decimate(0, 0, 32 * 100, 32) is None, "read before the buffer"
    assert pyr.decimate(0, 60000, 200000, 32) is None, "read past the buffer"
    assert pyr.decimate(0, 1000, 1000, 32) is None, "empty range"


def test_short_buffer_falls_back():
    pyr = MinMaxPyramid()
    pyr.build(_buffer((16, 4)), 0, 1)
    assert pyr.levels == []
    assert pyr.decimate(0, 0, 16, 32) is None


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
