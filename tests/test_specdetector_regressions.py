"""Defects found in `tiling` by an independent check, each pinned on its own.

Kept apart from `test_specdetector_tiling.py` so that a red-check can name them.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from audian_plugins.specdetector import tiling  # noqa: E402

RATE = 96000.0


def config(**over):
    values = dict(window_s=0.005, hop_s=0.001, tile_s=1.0, overlap_s=0.1, image_size=64)
    values.update(over)
    return tiling.SpecConfig(**values).validated()


def test_a_thin_event_keeps_its_energy_when_columns_share_a_pixel():
    """One frame at +40 dB in the eight of a pixel is +31 dB, as power averages.

    It used to read +5 dB, because the decibels were averaged: a brief event in a long
    tile all but vanished, and the docstring promised the opposite.
    """
    power = np.ones((8 * 64, 64), dtype=np.float32)
    power[8 * 10 + 3, :] = 1e4
    image = tiling.to_image(power, 64, "db")
    # db stretches between percentiles; the brightest column must stand out, and by the
    # right amount relative to a pixel holding no event.
    column = image[32, :, 0].astype(float)
    assert int(np.argmax(column)) == 10
    expected_db = 10 * np.log10((7 + 1e4) / 8)
    assert expected_db == pytest.approx(31.0, abs=0.1)
    # With the event at +31 dB and everything else at 0, the stretch tops out at the
    # event: its pixel is white.  Averaged in decibels it would be 5/40 of the way up.
    assert column[10] > 200


def test_resampling_does_not_lose_a_small_value_after_a_huge_one():
    """The running sum was taken in float32: flat 1e-3 after a 1e6 spike read 0."""
    values = np.full((1000, 1), 1e-3, dtype=np.float32)
    values[10] = 1e6
    out = tiling._resample(values, 100, 0)
    assert out[50, 0] == pytest.approx(1e-3, rel=1e-3)


def test_a_band_keeps_both_bins_that_straddle_its_edges():
    """It kept the one at the bottom and dropped the one at the top."""
    grid = tiling.grid_of(
        config(window_s=128 / RATE, hop_s=32 / RATE, fmin_hz=3800.0, fmax_hz=15000.0),
        RATE,
    )
    assert grid.df == pytest.approx(750.0)
    assert grid.f_low <= 3800.0 < grid.f_low + grid.df
    assert grid.f_high >= 15000.0 > grid.f_high - grid.df


def test_a_band_with_no_top_reaches_the_nyquist_bin():
    grid = tiling.grid_of(config(window_s=128 / RATE, hop_s=32 / RATE), RATE)
    assert grid.k1 == grid.nfft // 2 + 1


@pytest.mark.parametrize("nfft,hop", [(128, 31), (135, 30), (128, 32), (512, 240)])
def test_a_window_is_exactly_centred_on_its_column(nfft, hop):
    """Half a sample late whenever `nfft - hop` was odd, in a module that says exact."""
    grid = tiling.grid_of(config(window_s=nfft / RATE, hop_s=hop / RATE), RATE)
    assert (grid.nfft - grid.hop) % 2 == 0


def test_each_instant_has_one_owner_even_at_the_tile_moved_back_to_fit():
    """A 25 s file with 10 s tiles and 2 s overlap had 1 s owned twice."""
    cfg = config(window_s=0.1, hop_s=0.01, tile_s=10.0, overlap_s=2.0)
    grid = tiling.grid_of(cfg, 8000.0)
    starts = tiling.tile_starts(25.0, grid)
    spans = tiling.owned_spans(starts, grid, int(25.0 * 8000))
    assert len(spans) == len(starts)
    assert spans[0][0] == 0.0
    assert spans[-1][1] == pytest.approx(25.0)
    for (_a0, a1), (b0, _b1) in zip(spans, spans[1:]):
        assert b0 == pytest.approx(a1)
    length_s = grid.frames * grid.hop / grid.rate
    for s, (own0, own1) in zip(starts, spans):
        t0 = s / grid.rate
        assert t0 <= own0 < own1 <= t0 + length_s


def test_a_recording_shorter_than_a_tile_still_has_a_floor():
    """The zeros past its end were the median, and 1 s of a 4 s tile came out white."""

    class Short:
        rate = 8000.0
        channels = 1
        frames = 8000

        def read(self, a, b):
            data = np.random.default_rng(3).normal(0, 0.01, self.frames)
            return data[a:b, None].astype(np.float32)

    cfg = tiling.SpecConfig(window_s=0.02, hop_s=0.00625, tile_s=4.0, overlap_s=0.5)
    image, _ = tiling.render_tile(Short(), tiling.grid_of(cfg, 8000.0), 0, cfg)
    recorded = image[:, : image.shape[1] // 4, 0].astype(float)
    assert recorded.mean() < 80
    assert recorded.std() > 0


def test_tiles_that_do_not_touch_each_own_all_they_show():
    """A sweep over a few windows has gaps, and nothing is owned across one."""
    cfg = config(window_s=0.1, hop_s=0.01, tile_s=10.0, overlap_s=2.0)
    grid = tiling.grid_of(cfg, 8000.0)
    frames = grid.frames * grid.hop
    starts = [0, 100 * frames]
    spans = tiling.owned_spans(starts, grid, 1000 * frames)
    length_s = frames / grid.rate
    assert spans[0] == pytest.approx((0.0, length_s))
    assert spans[1] == pytest.approx((100 * length_s, 101 * length_s))
