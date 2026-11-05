"""The mapping between a recording and the images a model sees.

Everything a model reports comes back through this mapping, so these tests put
a click and a tone at known places and look for them in the pixels, rather than
checking that the arithmetic agrees with itself.  Runs without a window.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from audian_plugins.specdetector import tiling  # noqa: E402

RATE = 48000.0


class Reader:
    """The interface `tiling.tile_samples` reads, over an array."""

    def __init__(self, samples, rate=RATE):
        samples = np.asarray(samples, dtype=np.float32)
        self.samples = samples[:, None] if samples.ndim == 1 else samples
        self.rate = float(rate)
        self.frames = len(self.samples)
        self.channels = self.samples.shape[1]

    def read(self, a, b):
        return self.samples[a:b]


def config(**over):
    values = dict(
        window_s=0.005,
        hop_s=0.001,
        tile_s=0.64,
        overlap_s=0.04,
        image_size=640,
        representation="db",
        reduction="0",
    )
    values.update(over)
    return tiling.SpecConfig(**values).validated()


def noise(seconds, rate=RATE, level=1e-4, seed=1):
    return np.random.default_rng(seed).normal(0, level, int(seconds * rate))


def brightest_column(image):
    return int(np.argmax(image[..., 0].astype(float).sum(axis=0)))


def brightest_row(image):
    return int(np.argmax(image[..., 0].astype(float).sum(axis=1)))


@pytest.mark.parametrize("seconds", [0.1, 0.25003, 0.5])
def test_a_click_lands_on_the_column_of_its_time(seconds):
    """No half-window slip: this is the bias every detection would inherit."""
    samples = noise(1.0)
    samples[int(seconds * RATE)] = 1.0
    cfg = config()
    reader = Reader(samples)
    grid = tiling.grid_of(cfg, RATE)
    image, geometry = tiling.render_tile(reader, grid, 0, cfg)
    found = geometry.t_of(brightest_column(image) + 0.5)
    # one column is a millisecond, and a click on a hanning window is symmetric
    assert abs(found - seconds) <= 0.001


def test_a_click_is_placed_the_same_from_a_tile_that_starts_elsewhere():
    samples = noise(2.0)
    samples[int(1.2 * RATE)] = 1.0
    cfg = config()
    reader = Reader(samples)
    grid = tiling.grid_of(cfg, RATE)
    for start_s in (0.7, 0.9, 1.1):
        image, geometry = tiling.render_tile(reader, grid, int(start_s * RATE), cfg)
        found = geometry.t_of(brightest_column(image) + 0.5)
        assert abs(found - 1.2) <= 0.001, start_s


@pytest.mark.parametrize("hertz", [1000.0, 7321.0, 20000.0])
def test_a_tone_lands_on_the_row_of_its_frequency(hertz):
    t = np.arange(int(1.0 * RATE)) / RATE
    samples = noise(1.0) + 0.1 * np.sin(2 * np.pi * hertz * t)
    cfg = config(window_s=0.02)
    grid = tiling.grid_of(cfg, RATE)
    image, geometry = tiling.render_tile(Reader(samples), grid, 0, cfg)
    found = geometry.f_of(brightest_row(image) + 0.5)
    assert abs(found - hertz) <= grid.df


def test_high_frequencies_are_at_the_top_of_the_image():
    t = np.arange(int(1.0 * RATE)) / RATE
    samples = noise(1.0) + 0.1 * np.sin(2 * np.pi * 20000.0 * t)
    cfg = config(window_s=0.02)
    image, _ = tiling.render_tile(Reader(samples), tiling.grid_of(cfg, RATE), 0, cfg)
    assert brightest_row(image) < image.shape[0] // 4


def test_a_band_is_snapped_to_bin_edges_and_reports_what_it_kept():
    cfg = config(window_s=0.02, fmin_hz=1000.0, fmax_hz=5000.0)
    grid = tiling.grid_of(cfg, RATE)
    assert grid.f_low == pytest.approx((grid.k0 - 0.5) * grid.df)
    assert grid.f_high == pytest.approx((grid.k1 - 0.5) * grid.df)
    assert abs(grid.f_low - 1000.0) <= grid.df
    assert abs(grid.f_high - 5000.0) <= grid.df


def test_a_tone_stays_at_its_frequency_inside_a_narrow_band():
    t = np.arange(int(1.0 * RATE)) / RATE
    samples = noise(1.0) + 0.1 * np.sin(2 * np.pi * 3000.0 * t)
    cfg = config(window_s=0.02, fmin_hz=1000.0, fmax_hz=5000.0)
    grid = tiling.grid_of(cfg, RATE)
    image, geometry = tiling.render_tile(Reader(samples), grid, 0, cfg)
    found = geometry.f_of(brightest_row(image) + 0.5)
    assert abs(found - 3000.0) <= grid.df


def test_boxes_survive_the_round_trip_through_pixels():
    geometry = tiling.TileGeometry(2.0, 3.0, 100.0, 5100.0, 640, 640)
    box = (2.25, 2.5, 1000.0, 3000.0)
    pixels = geometry.box_to_pixels(*box)
    assert pixels[0] < pixels[2] and pixels[1] < pixels[3]
    assert geometry.box_from_pixels(*pixels) == pytest.approx(box)


def test_every_image_is_square_and_uint8_at_any_resolution():
    samples = noise(1.0)
    for window_s, hop_s in ((0.002, 0.0002), (0.05, 0.01)):
        cfg = config(window_s=window_s, hop_s=hop_s, image_size=320)
        image, _ = tiling.render_tile(
            Reader(samples), tiling.grid_of(cfg, RATE), 0, cfg
        )
        assert image.shape == (320, 320, 3)
        assert image.dtype == np.uint8
        assert image.std() > 0  # holds a picture, not a constant


def test_resampling_keeps_the_mean_whichever_way_the_sizes_differ():
    values = np.random.default_rng(2).random((101, 7))
    for n in (13, 101, 640):
        out = tiling._resample(values, n, 0)
        assert out.shape == (n, 7)
        assert out.mean(axis=0) == pytest.approx(values.mean(axis=0))


def test_digital_silence_reaches_no_image_as_a_nan():
    """Four of sixteen electrodes in a real session are recorded as exact zeros."""
    samples = np.zeros((48000, 4), dtype=np.float32)
    samples[:, 0] = noise(1.0)
    for representation in tiling.REPRESENTATIONS:
        for reduction in ("max", "mean", "0", "2"):
            cfg = config(representation=representation, reduction=reduction)
            image, _ = tiling.render_tile(
                Reader(samples), tiling.grid_of(cfg, RATE), 0, cfg
            )
            assert image.dtype == np.uint8


def test_max_keeps_a_signal_on_one_electrode_that_the_mean_lowers():
    """The reason `max` is the default on an array."""
    block = np.ones((10, 16, 5))
    block[:, 3, :] = 100.0
    assert tiling.reduce_channels(block, "max")[0, 0] == 100.0
    assert tiling.reduce_channels(block, "mean")[0, 0] == pytest.approx(115.0 / 16)
    assert tiling.reduce_channels(block, "3")[0, 0] == 100.0


def test_asking_for_a_channel_the_recording_lacks_says_so():
    with pytest.raises(ValueError, match="channel 5"):
        tiling.reduce_channels(np.ones((10, 2, 5)), "5")


def test_a_tile_past_the_end_of_the_recording_is_zero_filled_not_short():
    cfg = config()
    grid = tiling.grid_of(cfg, RATE)
    reader = Reader(noise(0.2))
    block = tiling.tile_samples(reader, grid, 0)
    assert len(block) == (grid.frames - 1) * grid.hop + grid.nfft
    assert np.all(block[-1000:] == 0.0)


def test_tiles_cover_a_recording_and_the_last_one_ends_with_it():
    cfg = config()
    grid = tiling.grid_of(cfg, RATE)
    starts = tiling.tile_starts(10.0, grid)
    length = grid.frames * grid.hop
    assert starts[0] == 0
    assert starts[-1] + length == int(10.0 * RATE)
    assert all(
        b - a <= int(round(grid.step_s * RATE)) for a, b in zip(starts, starts[1:])
    )


def test_a_recording_shorter_than_a_tile_is_one_tile():
    grid = tiling.grid_of(config(), RATE)
    assert tiling.tile_starts(0.1, grid) == [0]


def test_each_instant_is_owned_by_exactly_one_tile():
    """What keeps an event on a tile boundary from being reported twice."""
    cfg = config()
    grid = tiling.grid_of(cfg, RATE)
    total = int(10.0 * RATE)
    starts = tiling.tile_starts(10.0, grid)
    spans = tiling.owned_spans(starts, grid, total)
    assert spans[0][0] == 0.0
    assert spans[-1][1] == pytest.approx(10.0)
    for t in np.arange(0.0, 10.0, 0.0137):
        assert sum(a <= t < b for a, b in spans) == 1, t


def test_an_event_is_always_whole_in_the_tile_that_owns_its_centre():
    cfg = tiling.suggest_config([(1.0, 1.1, 1000.0, 2000.0)], RATE)
    grid = tiling.grid_of(cfg, RATE)
    total = int(120.0 * RATE)
    starts = tiling.tile_starts(120.0, grid)
    spans = tiling.owned_spans(starts, grid, total)
    half = 0.05
    for centre in np.arange(half, 120.0 - half, 0.0311):
        index = next(i for i, (a, b) in enumerate(spans) if a <= centre < b)
        geometry = tiling.geometry_of(grid, starts[index], 640)
        assert geometry.t0 <= centre - half and centre + half <= geometry.t1


def test_a_config_is_in_seconds_so_a_model_sees_the_same_picture_at_any_rate():
    cfg = config(window_s=0.02, hop_s=0.002, fmin_hz=1000.0, fmax_hz=5000.0)
    low = tiling.grid_of(cfg, 48000.0)
    high = tiling.grid_of(cfg, 96000.0)
    assert low.tile_s == pytest.approx(high.tile_s, rel=0.01)
    assert low.f_low == pytest.approx(high.f_low, abs=max(low.df, high.df))
    assert low.nfft / low.rate == pytest.approx(high.nfft / high.rate, rel=0.05)


def test_a_config_survives_the_disk_and_ignores_what_it_does_not_know():
    cfg = config(fmax_hz=5000.0)
    values = cfg.as_dict() | {"from_the_future": 1}
    assert tiling.SpecConfig.from_dict(values) == cfg


@pytest.mark.parametrize(
    "over",
    [
        {"hop_s": 0.0},
        {"tile_s": 0.0},
        {"overlap_s": 1.0},
        {"fmin_hz": 5000.0, "fmax_hz": 1000.0},
        {"representation": "rainbow"},
        {"reduction": "loudest"},
        {"image_size": 4},
    ],
)
def test_a_config_that_cannot_work_is_refused(over):
    with pytest.raises(ValueError):
        config(**over)


def test_a_suggestion_fits_the_examples_it_was_made_from():
    boxes = [(1.0, 1.1, 4000.0, 15000.0), (2.0, 2.09, 3800.0, 15300.0)]
    cfg = tiling.suggest_config(boxes, 96000.0, channels=2)
    grid = tiling.grid_of(cfg, 96000.0)
    assert grid.f_low < 3800.0 and grid.f_high > 15300.0
    assert grid.tile_s >= 4 * 0.1
    assert cfg.overlap_s >= 2 * 0.1
    assert cfg.reduction == "max"
    pixels = 0.095 / grid.tile_s * cfg.image_size
    assert pixels >= 24  # a stride-8 head has several cells on the event


def test_a_suggestion_without_frequencies_keeps_the_whole_band():
    cfg = tiling.suggest_config([(1.0, 1.1, None, None)], RATE)
    assert cfg.fmin_hz == 0.0 and cfg.fmax_hz is None
    assert cfg.reduction == "0"


def test_a_suggestion_needs_something_to_suggest_from():
    with pytest.raises(ValueError):
        tiling.suggest_config([], RATE)
    with pytest.raises(ValueError):
        tiling.suggest_config([(1.0, 1.0, None, None)], RATE)


def test_the_view_config_renders_exactly_the_rectangle_in_view():
    config_, s0 = tiling.view_config(2.0, 4.0, 1000.0, 9000.0, RATE, 1024)
    grid = tiling.grid_of(config_, RATE)
    geometry = tiling.geometry_of(grid, s0, 1024)
    assert geometry.t0 == pytest.approx(2.0)
    assert geometry.t1 == pytest.approx(4.0, abs=grid.hop / RATE)
    assert geometry.f0 == pytest.approx(1000.0, abs=grid.df)
    assert geometry.f1 == pytest.approx(9000.0, abs=grid.df)
