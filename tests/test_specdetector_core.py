"""Boxes, datasets, review order and the project file.  No window and no model."""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from audian_plugins.specdetector import boxes as boxes_  # noqa: E402
from audian_plugins.specdetector import dataset, project, review, tiling  # noqa: E402
from audian_plugins.specdetector.boxes import Box  # noqa: E402

RATE = 8000.0


# ------------------------------------------------------------------- boxes


def test_iou_is_what_it_should_be_in_either_unit():
    a = np.array([[0.0, 1.0, 100.0, 200.0]])
    b = np.array([[0.5, 1.5, 100.0, 200.0], [5.0, 6.0, 100.0, 200.0]])
    assert boxes_.iou_matrix(a, b)[0] == pytest.approx([1 / 3, 0.0])
    # hertz to kilohertz: a ratio of areas does not care
    scaled = boxes_.iou_matrix(a * [1, 1, 1e-3, 1e-3], b * [1, 1, 1e-3, 1e-3])
    assert scaled == pytest.approx(boxes_.iou_matrix(a, b))


def test_nms_keeps_the_strongest_of_a_group_and_leaves_the_rest():
    found = [
        Box(0.0, 1.0, 0.0, 10.0, 0, 0.5),
        Box(0.1, 1.1, 0.0, 10.0, 0, 0.9),
        Box(5.0, 6.0, 0.0, 10.0, 0, 0.3),
    ]
    kept = boxes_.nms(found, 0.5)
    assert [b.score for b in kept] == [0.9, 0.3]


def test_nms_does_not_let_one_class_suppress_another():
    found = [Box(0.0, 1.0, 0.0, 10.0, 0, 0.9), Box(0.0, 1.0, 0.0, 10.0, 1, 0.8)]
    assert len(boxes_.nms(found, 0.5)) == 2
    assert len(boxes_.nms(found, 0.5, class_aware=False)) == 1


def test_nms_finds_a_long_box_overlapping_a_later_short_one():
    """Sorted by start time, a long box is far from the short one it swallows."""
    found = [Box(0.0, 10.0, 0.0, 10.0, 0, 0.5), Box(0.0, 10.0, 0.0, 10.0, 0, 0.4)]
    found += [Box(float(t), t + 0.1, 0.0, 10.0, 0, 0.9) for t in range(20, 30)]
    assert len(boxes_.nms(found, 0.5)) == 11


def test_matching_is_one_to_one():
    truth = [Box(1.0, 1.1, 0.0, 10.0)]
    found = [Box(1.0, 1.1, 0.0, 10.0, 0, 0.9), Box(1.01, 1.11, 0.0, 10.0, 0, 0.8)]
    assert boxes_.match_events(found, truth) == (1, 1, 0)


def test_matching_needs_overlap_in_frequency_as_the_benchmark_did():
    truth = [Box(1.0, 1.1, 100.0, 200.0)]
    assert boxes_.match_events([Box(1.0, 1.1, 300.0, 400.0)], truth).tp == 0
    assert boxes_.match_events([Box(1.0, 1.1, 150.0, 400.0)], truth).tp == 1


def test_matching_scores_are_what_they_say():
    match = boxes_.Match(tp=3, fp=1, fn=2)
    assert match.precision == 0.75
    assert match.recall == 0.6
    assert match.f1 == pytest.approx(2 * 3 / (2 * 3 + 1 + 2))
    assert boxes_.Match(0, 0, 0).f1 == 0.0


def test_the_best_threshold_is_the_one_that_separates():
    truth = [Box(float(t), t + 0.1, 0.0, 10.0) for t in range(5)]
    hits = [b._replace(score=0.8) for b in truth]
    noise = [Box(10.0 + t, 10.1 + t, 0.0, 10.0, 0, 0.2) for t in range(20)]
    threshold, match = boxes_.best_threshold(hits + noise, truth)
    assert threshold == pytest.approx(0.8)
    assert match.f1 == 1.0


def test_the_automatic_cut_separates_two_populations_of_scores():
    """On a log scale, because prompted YOLOE's hits were 0.1 and its noise 0.01."""
    rng = np.random.default_rng(1)
    noise = np.exp(rng.normal(np.log(0.01), 0.2, 300))
    hits = np.exp(rng.normal(np.log(0.15), 0.2, 60))
    cut = boxes_.auto_cut(np.concatenate([noise, hits]))
    assert np.percentile(noise, 95) < cut < np.percentile(hits, 5)


def test_the_automatic_cut_admits_it_has_nothing_to_go_on():
    assert boxes_.auto_cut([0.5, 0.6], default=0.25) == 0.25
    assert boxes_.auto_cut([], default=0.25) == 0.25
    assert boxes_.auto_cut([0.3] * 40, default=0.25) == 0.25


# ------------------------------------------------------------------- the dataset


def geometry():
    return tiling.TileGeometry(10.0, 12.0, 100.0, 1100.0, 640, 640)


def test_a_label_brings_its_own_small_rectangle():
    box = Box(1.0, 1.2, 500.0, 700.0)
    rect = dataset.implicit_rect(box)
    assert rect.t0 == pytest.approx(0.9) and rect.t1 == pytest.approx(1.3)
    assert rect.f0 == pytest.approx(400.0) and rect.f1 == pytest.approx(800.0)
    assert dataset.implicit_rect(Box(1.0, 1.2, 10.0, 200.0)).f0 == 0.0


def test_a_rejection_is_a_looked_at_rectangle_with_nothing_in_it():
    source = dataset.Source(["x"], rejected=[Box(1.0, 1.2, 500.0, 700.0)])
    assert len(source.rectangles()) == 1
    assert source.boxes == []


def test_a_label_inside_a_reviewed_rectangle_brings_no_other():
    reviewed = [dataset.Rect(0.0, 10.0, 0.0, 1000.0)]
    inside = dataset.Source(["x"], [Box(1.0, 1.2, 500.0, 700.0)], reviewed)
    outside = dataset.Source(["x"], [Box(1.0, 1.2, 500.0, 1700.0)], reviewed)
    assert len(inside.rectangles()) == 1
    assert len(outside.rectangles()) == 2


def test_only_what_was_looked_at_stays_on_the_image():
    g = geometry()
    looked = dataset.looked_at_mask(g, [dataset.Rect(10.5, 11.0, 300.0, 700.0)])
    assert looked[:, :100].sum() == 0  # before the rectangle in time
    assert looked[:200, 200].sum() == 0  # above it in frequency
    assert looked[300, 200]  # inside: 500 Hz, 10.625 s
    assert not looked[600, 200]  # below it in frequency


def test_a_rectangle_with_no_band_covers_every_frequency():
    looked = dataset.looked_at_mask(geometry(), [dataset.Rect(10.5, 11.0)])
    assert looked[0, 200] and looked[-1, 200]


def test_masking_leaves_seen_pixels_alone_and_replaces_the_rest_with_this_floor():
    rng = np.random.default_rng(3)
    image = rng.integers(0, 40, (64, 64, 3)).astype(np.uint8)
    image[20:30, 20:30] = 250  # an event, seen
    image[50:60, 50:60] = 250  # an event nobody looked at
    looked = np.zeros((64, 64), dtype=bool)
    looked[:40, :40] = True
    out = dataset.mask_outside(image, looked, seed=1)
    assert np.array_equal(out[:40, :40], image[:40, :40])
    # the event nobody looked at is gone, which is the whole point
    assert out[50:60, 50:60].max() < 40
    assert out[~looked].std() > 0  # noise, not a flat shape to learn


def test_nothing_hidden_means_the_same_image():
    image = np.ones((8, 8, 3), dtype=np.uint8)
    assert dataset.mask_outside(image, np.ones((8, 8), dtype=bool), 0) is image


def test_labels_are_normalised_and_y_runs_down_the_image():
    g = geometry()
    lines, slivers = dataset.tile_labels([Box(10.5, 11.0, 600.0, 1100.0, cls=2)], g)
    assert slivers == []
    cls, cx, cy, w, h = lines[0].split()
    assert cls == "2"
    assert float(cx) == pytest.approx(0.375, abs=1e-3)
    assert float(cy) == pytest.approx(0.25, abs=1e-3)  # the top half is the high half
    assert float(w) == pytest.approx(0.25, abs=1e-3)
    assert float(h) == pytest.approx(0.5, abs=1e-3)


def test_an_event_mostly_on_the_tile_is_kept_clipped_and_a_sliver_is_hidden():
    g = geometry()
    mostly, none = dataset.tile_labels([Box(9.9, 10.9, 200.0, 400.0)], g)
    assert len(mostly) == 1 and not none
    assert float(mostly[0].split()[3]) == pytest.approx(0.45, abs=1e-3)
    lines, slivers = dataset.tile_labels([Box(9.0, 10.1, 200.0, 400.0)], g)
    assert lines == []
    assert slivers == [dataset.Rect(10.0, 10.1, 200.0, 400.0)]


def test_a_box_off_the_tile_is_neither():
    assert dataset.tile_labels([Box(1.0, 2.0, 200.0, 400.0)], geometry()) == ([], [])


def test_an_outline_becomes_a_segmentation_line_and_a_box_its_rectangle():
    g = geometry()
    polygon = ((10.5, 200.0), (11.0, 200.0), (10.75, 600.0))
    lines, _ = dataset.tile_labels(
        [Box(10.5, 11.0, 200.0, 600.0, polygon=polygon)], g, segment=True
    )
    cells = lines[0].split()
    assert cells[0] == "0" and len(cells) == 1 + 2 * len(polygon)
    plain, _ = dataset.tile_labels([Box(10.5, 11.0, 200.0, 600.0)], g, segment=True)
    assert len(plain[0].split()) == 1 + 2 * 4


def test_one_event_drawn_on_two_lanes_is_one_target():
    """A real session's twelve volley labels are six events on two channels."""
    twice = [
        Box(1.0, 1.3, 0.0, 5000.0, channel=0),
        Box(1.0, 1.29, 0.0, 4900.0, channel=1),
        Box(1.0, 1.3, 0.0, 5000.0, cls=1, channel=1),
        Box(4.0, 4.3, 0.0, 5000.0, channel=0),
    ]
    assert len(dataset.merge_lanes(twice)) == 3


def grid_for(tile_s=2.0, overlap_s=0.5):
    config = tiling.SpecConfig(
        window_s=0.02, hop_s=0.005, tile_s=tile_s, overlap_s=overlap_s, image_size=64
    )
    return tiling.grid_of(config, RATE)


def test_a_short_rectangle_lands_whole_at_different_places():
    grid = grid_for()
    length = grid.frames * grid.hop
    rect = dataset.Rect(5.0, 5.2)
    starts = dataset.plan_tiles([rect], grid, int(100 * RATE), jitter_copies=16)
    assert len(starts) > 8
    a, b = int(rect.t0 * RATE), int(rect.t1 * RATE)
    assert all(s <= a and b <= s + length for s in starts)
    positions = {(a - s) // grid.hop for s in starts}
    assert len(positions) > 8


def test_a_long_rectangle_is_swept_to_its_end():
    grid = grid_for()
    length = grid.frames * grid.hop
    rect = dataset.Rect(5.0, 25.0)
    starts = dataset.plan_tiles([rect], grid, int(100 * RATE))
    assert starts[0] == int(5.0 * RATE)
    assert starts[-1] + length == int(25.0 * RATE)
    assert np.max(np.diff(starts)) <= int(length * dataset.SWEEP_FRACTION)


def test_no_tile_starts_outside_the_recording():
    grid = grid_for()
    total = int(10 * RATE)
    length = grid.frames * grid.hop
    starts = dataset.plan_tiles(
        [dataset.Rect(0.0, 0.1), dataset.Rect(9.9, 10.0)], grid, total
    )
    assert min(starts) >= 0 and max(starts) + length <= total


def test_copies_of_a_few_events_are_not_mistaken_for_something_to_hold_out():
    """Eighty tiles of five shots used to hold out sixteen and drop all the rest."""
    grid = grid_for()
    tiles = [(0, int(50 * RATE) + i * 200) for i in range(80)]
    splits = dataset.split_tiles(tiles, grid, [True] * 80)
    assert Counter(splits) == {"train": 80}


def test_held_out_tiles_share_no_time_with_training_tiles():
    """What separates the 99 % cross-validation score from the 76 % real one."""
    grid = grid_for()
    length = grid.frames * grid.hop
    step = length // 4
    tiles = [(0, i * step) for i in range(400)]
    splits = dataset.split_tiles(tiles, grid, [True] * 400)
    counts = Counter(splits)
    assert counts["train"] > 200 and counts["val"] > 20
    assert counts[None] > 0  # a training tile touching a held-out one was dropped
    held = [s for (_, s), split in zip(tiles, splits) if split == "val"]
    train = [s for (_, s), split in zip(tiles, splits) if split == "train"]
    for s in train:
        assert all(abs(s - h) >= length for h in held)


def test_the_split_is_per_recording():
    grid = grid_for()
    length = grid.frames * grid.hop
    tiles = [(0, i * 10 * length) for i in range(10)] + [
        (1, i * 10 * length) for i in range(10)
    ]
    splits = dataset.split_tiles(tiles, grid, [True] * 20)
    # the same sample numbers in another recording are not the same time
    assert splits.count(None) == 0
    assert splits.count("val") >= 2


class FakeRecording:
    """The interface a dataset reads: a tone at 1 kHz, in noise."""

    def __init__(self, paths):
        self.paths = paths
        self.rate = RATE
        self.frames = int(100 * RATE)
        self.channels = 1
        rng = np.random.default_rng(4)
        t = np.arange(self.frames) / RATE
        self.signal = rng.normal(0, 0.01, self.frames).astype(np.float32)
        self.signal += (0.3 * np.sin(2 * np.pi * 1000 * t)).astype(np.float32)

    def read(self, a, b):
        return self.signal[a:b, None]

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None


def test_a_dataset_is_what_ultralytics_reads_and_only_what_was_looked_at(tmp_path):
    # In decibels and not whitened, which would remove a steady tone from the image
    # and leave this test nothing to find.
    config = tiling.SpecConfig(
        window_s=0.02,
        hop_s=0.005,
        tile_s=2.0,
        overlap_s=0.5,
        image_size=64,
        fmax_hz=2000.0,
        representation="db",
    )
    written = {}

    def write(path, image):
        written[path.name] = image
        Path(path).write_bytes(b"png")

    source = dataset.Source(
        ["a.wav"],
        boxes=[Box(5.0, 5.2, 800.0, 1200.0)],
        rejected=[Box(20.0, 20.2, 800.0, 1200.0)],
    )
    report = dataset.build_dataset(
        [source],
        config,
        ["chirp"],
        tmp_path / "set",
        FakeRecording,
        write_image=write,
        jitter_copies=4,
    )
    assert report.train_tiles == len(written) > 0
    assert report.train_events >= 1 and report.background_tiles >= 1
    assert report.held_out is False  # one event is nothing to hold out from
    text = Path(report.yaml).read_text()
    assert "chirp" in text and "val: images/train" in text
    labels = sorted((tmp_path / "set" / "labels" / "train").glob("*.txt"))
    assert any(p.read_text().strip() for p in labels)
    assert any(not p.read_text().strip() for p in labels)
    # A tile of the label: the 1 kHz line runs right across the recording, and only
    # the part in the label's rectangle may survive.
    stem = next(p.stem for p in labels if p.read_text().strip())
    image = written[f"{stem}.png"][..., 0].astype(float)
    start = int(stem.split("_")[1]) / RATE
    geometry_ = tiling.TileGeometry(start, start + 2.0, 0.0, 2000.0, 64, 64)
    row = int(geometry_.y_of(1000.0))
    x_label = int(geometry_.x_of(5.1))
    x_far = 2 if geometry_.x_of(5.1) > 32 else 61
    assert image[row, x_label] > 2 * image[row, x_far] + 10


def test_a_dataset_is_rebuilt_from_nothing(tmp_path):
    config = tiling.SpecConfig(
        window_s=0.02, hop_s=0.005, tile_s=2.0, overlap_s=0.5, image_size=64
    )
    directory = tmp_path / "set"
    stale = directory / "images" / "train" / "stale.png"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"old")
    dataset.build_dataset(
        [dataset.Source(["a"], boxes=[Box(5.0, 5.2, 800.0, 1200.0)])],
        config,
        ["x"],
        directory,
        FakeRecording,
        write_image=lambda p, _i: Path(p).write_bytes(b"png"),
    )
    assert not stale.exists()


# ------------------------------------------------------------------- review


def candidate(t, score, f=(100.0, 200.0)):
    return Box(t, t + 0.1, f[0], f[1], 0, score)


def test_uncertainty_peaks_at_the_cut():
    assert review.uncertainty(0.3, 0.3) == 1.0
    assert review.uncertainty(0.9, 0.3) < review.uncertainty(0.4, 0.3)
    assert review.uncertainty(0.0, 0.3) >= 0.0


def test_windows_hold_every_candidate_once():
    found = [candidate(float(t), 0.5) for t in range(0, 30, 3)]
    windows = review.group_windows(found, 10.0)
    members = [i for w in windows for i in w[2]]
    assert sorted(members) == list(range(len(found)))
    for t0, t1, indices in windows:
        for i in indices:
            assert t0 <= found[i].t0 and found[i].t1 <= t1


def test_windows_skip_what_has_been_reviewed():
    found = [candidate(1.0, 0.5), candidate(50.0, 0.5)]
    reviewed = [dataset.Rect(0.0, 10.0, 0.0, 1000.0)]
    windows = review.group_windows(found, 10.0, reviewed)
    assert [w[2] for w in windows] == [(1,)]


def test_a_rectangle_in_another_band_reviews_nothing_here():
    found = [candidate(1.0, 0.5, (100.0, 200.0))]
    assert review.group_windows(found, 10.0, [dataset.Rect(0.0, 10.0, 5000.0, 6000.0)])


def test_the_most_uncertain_window_comes_first():
    found = [candidate(0.0, 0.95), candidate(100.0, 0.31), candidate(200.0, 0.05)]
    ranked = review.rank_windows(found, 10.0, 0.3, explore_every=0)
    assert ranked[0].candidates == (1,)


def test_several_doubtful_candidates_outrank_one():
    found = [candidate(0.0, 0.3), candidate(100.0, 0.3), candidate(102.0, 0.3)]
    ranked = review.rank_windows(found, 10.0, 0.3, explore_every=0)
    assert ranked[0].candidates == (1, 2)


def test_some_windows_are_blind_so_a_missed_event_can_be_met():
    """The failure no ranking of candidates can see: what was never proposed."""
    found = [candidate(float(t) * 20, 0.3) for t in range(20)]
    ranked = review.rank_windows(found, 10.0, 0.3, span=(0.0, 1000.0), explore_every=5)
    blind = [w for w in ranked if w.priority == 0.0]
    assert len(blind) == 4
    assert ranked[4] in blind
    assert all(0.0 <= w.t0 <= 990.0 for w in blind)


def test_a_ranking_can_be_asked_for_again():
    found = [candidate(float(t) * 20, 0.3) for t in range(20)]
    one = review.rank_windows(found, 10.0, 0.3, span=(0.0, 1000.0), seed=3)
    two = review.rank_windows(found, 10.0, 0.3, span=(0.0, 1000.0), seed=3)
    assert one == two


def test_an_unknown_ranking_is_refused():
    with pytest.raises(ValueError):
        review.rank_windows([], 10.0, 0.3, strategy="vibes")


# ------------------------------------------------------------------- what SAM says


def two_blobs():
    mask = np.zeros((100, 100), dtype=bool)
    mask[10:20, 10:20] = True
    mask[10:20, 60:70] = True
    return mask


def test_a_click_keeps_only_the_part_under_it():
    """SAM answered a click on one pip with a mask over its neighbour as well."""
    from audian_plugins.specdetector import models

    kept = models.keep_prompted_parts(two_blobs(), positives=[(15, 15)])
    assert kept[15, 15] and not kept[15, 65]
    assert models.box_of_mask(kept) == (10, 10, 20, 20)


def test_a_click_beside_the_mask_takes_the_nearest_part():
    from audian_plugins.specdetector import models

    kept = models.keep_prompted_parts(two_blobs(), positives=[(75, 15)])
    assert kept[15, 65] and not kept[15, 15]


def test_two_clicks_keep_both_parts():
    from audian_plugins.specdetector import models

    kept = models.keep_prompted_parts(two_blobs(), positives=[(15, 15), (65, 15)])
    assert kept[15, 15] and kept[15, 65]


def test_a_drag_keeps_only_what_is_inside_it():
    from audian_plugins.specdetector import models

    kept = models.keep_prompted_parts(two_blobs(), box=(0, 0, 40, 40))
    assert kept[15, 15] and not kept[15, 65]
    both = models.keep_prompted_parts(two_blobs(), box=(0, 0, 90, 90))
    assert both[15, 15] and both[15, 65]


def test_an_empty_mask_stays_empty():
    from audian_plugins.specdetector import models

    empty = np.zeros((10, 10), dtype=bool)
    assert not models.keep_prompted_parts(empty, positives=[(5, 5)]).any()
    assert models.box_of_mask(empty) is None


def test_an_outline_runs_around_its_mask_and_is_kept_short():
    from audian_plugins.specdetector import models

    mask = np.zeros((200, 200), dtype=np.uint8)
    yy, xx = np.ogrid[:200, :200]
    mask[(yy - 100) ** 2 + (xx - 100) ** 2 < 80**2] = 1
    outline = models.mask_outline(mask, points=16)
    assert 3 <= len(outline) <= 16
    assert outline[:, 0].min() < 25 and outline[:, 0].max() > 175
    assert models.mask_outline(np.zeros((5, 5), dtype=np.uint8)) is None


@pytest.mark.parametrize(
    "tiles,batch,expected",
    [(24, 16, 200), (80, 8, 40), (200, 16, 32), (2000, 16, 30)],
)
def test_training_length_is_set_in_steps(tiles, batch, expected):
    """Fifteen epochs of 24 tiles were 30 steps, and a model that found nothing."""
    from audian_plugins.specdetector import models

    assert models.epochs_for(tiles, batch) == expected


# ------------------------------------------------------------------- what review found


def test_a_rectangle_reviewed_for_some_classes_is_not_reviewed_for_others():
    """A class added later has not been looked for there."""
    box = Box(1.0, 1.2, 500.0, 700.0)
    rect = dataset.Rect(0.0, 10.0, 0.0, 1000.0, classes=(0,))
    source = dataset.Source(["x"], [box], [rect])
    assert source.rectangles(1) == [rect]
    two = source.rectangles(2)
    assert rect not in two
    assert len(two) == 1  # the label keeps the small rectangle of its own
    assert review.is_reviewed(box, [rect])
    assert not review.is_reviewed(box._replace(cls=1), [rect])
    assert source.rectangles(None) == [rect]


def test_a_rectangle_declared_in_the_labels_covers_every_class():
    rect = dataset.Rect(0.0, 10.0, 0.0, 1000.0)
    assert rect.reviewed_for(7)
    source = dataset.Source(["x"], [], [rect])
    assert source.rectangles(5) == [rect]


def test_a_rectangle_survives_a_request_and_the_disk(tmp_path):
    rect = dataset.Rect(1.0, 2.0, 3.0, 4.0, (0, 1))
    assert dataset.rect_from_list(list(rect)) == rect
    assert dataset.rect_from_list([1.0, 2.0, 3.0, 4.0]).classes is None
    p = project.Project(directory=tmp_path)
    p.mark_reviewed([tmp_path / "a.wav"], rect)
    p.save()
    q = project.Project.open(tmp_path)
    assert q.reviewed_rects([tmp_path / "a.wav"]) == [rect]


def test_a_label_with_no_band_is_kept_off_the_image(tmp_path):
    config = tiling.SpecConfig(
        window_s=0.02,
        hop_s=0.005,
        tile_s=2.0,
        overlap_s=0.5,
        image_size=64,
        fmax_hz=2000.0,
        representation="db",
    )
    written = {}
    source = dataset.Source(
        ["a"],
        reviewed=[dataset.Rect(4.0, 6.0)],
        hidden=[dataset.Rect(4.5, 5.5)],
    )
    dataset.build_dataset(
        [source],
        config,
        ["x"],
        tmp_path / "set",
        FakeRecording,
        write_image=lambda path, image: written.update({path.name: image}),
    )
    image = next(iter(written.values()))[..., 0].astype(float)
    start = 4.0
    g = tiling.TileGeometry(start, start + 2.0, 0.0, 2000.0, 64, 64)
    row = int(g.y_of(1000.0))
    assert image[row, int(g.x_of(4.2))] > image[row, int(g.x_of(5.0))] + 50


def test_a_class_that_fits_no_image_shows_in_the_report(tmp_path):
    config = tiling.SpecConfig(
        window_s=0.02,
        hop_s=0.005,
        tile_s=2.0,
        overlap_s=0.5,
        image_size=64,
        fmax_hz=1500.0,
    )
    source = dataset.Source(
        ["a"],
        boxes=[Box(5.0, 5.2, 800.0, 1200.0, 0), Box(7.0, 7.2, 3000.0, 3500.0, 1)],
    )
    report = dataset.build_dataset(
        [source],
        config,
        ["low", "high"],
        tmp_path / "set",
        FakeRecording,
        write_image=lambda p, _i: Path(p).write_bytes(b"png"),
    )
    assert report.events_per_class[0] > 0
    assert report.events_per_class[1] == 0


def test_one_recording_under_a_new_file_list_is_still_one_recording(tmp_path):
    """Two entries of one audio leaked time across the split."""
    p = project.Project(directory=tmp_path)
    a, b = tmp_path / "a.wav", tmp_path / "b.wav"
    p.add_recording([a])
    assert p.add_recording([a, b]) is True
    assert p.recording_paths() == [[str(a), str(b)]]


def test_recordings_at_different_rates_are_split_each_by_its_own_length():
    """Measured with sources[0]'s, 48 of 355 tiles shared time with held-out ones."""
    config = tiling.SpecConfig(window_s=0.02, hop_s=0.005, tile_s=2.0, overlap_s=0.5)
    slow = tiling.grid_of(config, 8000.0)
    fast = tiling.grid_of(config, 32000.0)
    grids = [slow, fast]
    tiles = []
    for source, grid in enumerate(grids):
        length = grid.frames * grid.hop
        tiles += [(source, i * length // 4) for i in range(400)]
    splits = dataset.split_tiles(tiles, grids, [True] * len(tiles))
    for source, grid in enumerate(grids):
        length = grid.frames * grid.hop
        held = [s for (i, s), v in zip(tiles, splits) if i == source and v == "val"]
        train = [s for (i, s), v in zip(tiles, splits) if i == source and v == "train"]
        assert held and train
        for s in train:
            assert all(abs(s - h) >= length for h in held)


def test_several_sources_write_their_tiles_and_report_a_true_total(tmp_path):
    config = tiling.SpecConfig(
        window_s=0.02, hop_s=0.005, tile_s=2.0, overlap_s=0.5, image_size=64
    )
    sources = [
        dataset.Source(["a"], boxes=[Box(5.0, 5.2, 800.0, 1200.0)]),
        dataset.Source(["b"], boxes=[Box(9.0, 9.2, 800.0, 1200.0)]),
    ]
    seen = []
    report = dataset.build_dataset(
        sources,
        config,
        ["x"],
        tmp_path / "set",
        FakeRecording,
        write_image=lambda p, _i: Path(p).write_bytes(b"png"),
        progress=lambda done, total: seen.append((done, total)),
        jitter_copies=4,
    )
    names = [p.name for p in (tmp_path / "set" / "images" / "train").iterdir()]
    assert {n[:3] for n in names} == {"s00", "s01"}
    assert seen[-1][0] == seen[-1][1] == report.train_tiles + report.val_tiles


def test_a_path_with_a_hash_in_it_is_not_cut_at_the_comment(tmp_path):
    yaml = pytest.importorskip("yaml")
    config = tiling.SpecConfig(
        window_s=0.02, hop_s=0.005, tile_s=2.0, overlap_s=0.5, image_size=64
    )
    directory = tmp_path / "fish #3" / "set"
    report = dataset.build_dataset(
        [dataset.Source(["a"], boxes=[Box(5.0, 5.2, 800.0, 1200.0)])],
        config,
        ["a: strange # name"],
        directory,
        FakeRecording,
        write_image=lambda p, _i: Path(p).write_bytes(b"png"),
    )
    parsed = yaml.safe_load(Path(report.yaml).read_text())
    assert parsed["path"] == str(directory.resolve())
    assert parsed["names"][0] == "a: strange # name"


def test_an_outline_of_under_three_points_becomes_the_rectangle():
    """Two points were read as a box and one as a corrupt image."""
    g = geometry()
    thin = Box(10.5, 11.0, 200.0, 600.0, polygon=((10.5, 200.0), (11.0, 600.0)))
    lines, _ = dataset.tile_labels([thin], g, segment=True)
    assert len(lines[0].split()) == 1 + 2 * 4


def test_nested_events_all_match():
    """Centres sorted by start time gave tp=2 fp=1 fn=1 on perfect detections."""
    truth = [
        Box(0.0, 4.0, 0.0, 10.0),  # centre 2.0
        Box(0.5, 0.7, 0.0, 10.0),  # centre 0.6
        Box(1.0, 3.2, 0.0, 10.0),  # centre 2.1
    ]
    found = [b._replace(score=0.9) for b in truth]
    assert boxes_.match_events(found, truth, time_tol_s=0.01) == (3, 0, 0)


def test_a_duplicate_behind_a_short_box_is_still_found():
    long = Box(0.0, 1.0, 0.0, 10.0, channel=0)
    short = Box(0.1, 0.11, 0.0, 10.0)
    copy = Box(0.2, 1.0, 0.0, 10.0, channel=1)
    assert len(dataset.merge_lanes([long, short, copy])) == 2


def test_every_fifth_window_reviewed_is_taken_blind():
    found = [candidate(float(t) * 20, 0.3) for t in range(20)]
    ranked = review.rank_windows(found, 10.0, 0.3, span=(0.0, 1000.0))
    assert not review.pick_window(ranked, 0).blind
    assert not review.pick_window(ranked, 3).blind
    assert review.pick_window(ranked, 4).blind
    assert review.pick_window(ranked, 9).blind
    assert review.pick_window([], 0) is None


class _Boxes:
    def __init__(self, xyxy):
        self.xyxy = _Array(xyxy)
        self.conf = _Array([0.9] * len(xyxy))
        self.cls = _Array([0] * len(xyxy))

    def __len__(self):
        return len(self.xyxy.values)


class _Array:
    def __init__(self, values):
        self.values = np.array(values, dtype=float)

    def cpu(self):
        return self

    def numpy(self):
        return self.values


class _Result:
    def __init__(self, xyxy):
        self.boxes = _Boxes(xyxy)
        self.masks = None


class _Stub:
    """A model answering by tile, from the time an event is at."""

    def __init__(self, answer):
        self.answer = answer
        self.calls = 0

    def predict(self, images, **_kwargs):
        out = [_Result(self.answer(self.calls + i)) for i in range(len(images))]
        self.calls += len(images)
        return out

    def to_project_class(self, cls):
        return int(cls)


class _FlatRecording:
    """What `models.sweep` reads: noise, and nothing to find in it."""

    rate = 8000.0
    channels = 1

    def __init__(self, seconds=4.0):
        self.frames = int(seconds * self.rate)
        self.duration = seconds
        self.samples = np.random.default_rng(1).normal(0, 0.01, self.frames)

    def read(self, a, b):
        return self.samples[a:b, None].astype(np.float32)


def _flat_recording(seconds=4.0):
    return _FlatRecording(seconds)


def test_an_event_each_tile_places_just_past_the_boundary_is_not_lost():
    """Each judges its own box: one placed it late, the next early, nobody owned it."""
    from audian_plugins.specdetector import models

    config = tiling.SpecConfig(
        window_s=0.02,
        hop_s=0.005,
        tile_s=2.0,
        overlap_s=1.0,
        image_size=64,
        fmax_hz=2000.0,
    )
    reader = _flat_recording()
    grid = tiling.grid_of(config, reader.rate)
    starts = tiling.tile_starts(reader.frames / reader.rate, grid)
    spans = tiling.owned_spans(starts, grid, reader.frames)
    boundary = spans[0][1]
    px = 64 / 2.0  # pixels per second on this tile

    def answer(tile):
        if tile == 0:  # first tile: the centre one pixel past its end
            x = (boundary + 1 / px - starts[0] / reader.rate) * px
        elif tile == 1:  # second tile: one pixel before its start
            x = (boundary - 1 / px - starts[1] / reader.rate) * px
        else:
            return np.zeros((0, 4))
        return np.array([[x - 12, 10, x + 12, 20]])

    found = models.sweep(_Stub(answer), reader, config, batch=2)
    assert len(found) == 1


def test_nothing_is_placed_below_zero_hertz_or_past_the_recording():
    """A box from a model wrote ``f_low_hz=-5.000`` into a sidecar."""
    from audian_plugins.specdetector import models

    assert models.clamp_box((-1.0, 5.0, -5.0, 9000.0), (4.0, 4000.0)) == (
        0.0,
        4.0,
        0.0,
        4000.0,
    )
    assert models.clamp_polygon([[-1.0, -5.0], [9.0, 9000.0]], (4.0, 4000.0)) == [
        [0.0, 0.0],
        [4.0, 4000.0],
    ]
    g = tiling.TileGeometry(0.0, 2.0, -50.0, 1000.0, 64, 64)
    boxes = models._boxes_of(_Result(np.array([[0, 0, 64, 64]])), g, int, (1.5, 900.0))
    assert (boxes[0].t0, boxes[0].t1) == (0.0, 1.5)
    assert (boxes[0].f0, boxes[0].f1) == (0.0, 900.0)


# ------------------------------------------------------------------- the project


def test_a_project_survives_the_disk(tmp_path):
    p = project.Project(directory=tmp_path / "proj")
    recording = tmp_path / "rec.wav"
    p.add_recording([recording])
    p.config = tiling.suggest_config([(1.0, 1.1, 100.0, 200.0)], RATE)
    p.names = ["chirp", "rise"]
    p.mark_reviewed([recording], dataset.Rect(1.0, 2.0, 100.0, 200.0))
    p.reject([recording], [Box(1.0, 1.1, 100.0, 200.0, 1, 0.4)])
    p.masks[project.mask_key("chirp", 1.0, 1.1, 100.0, 200.0)] = [
        [1.0, 100.0],
        [1.1, 200.0],
    ]
    p.threshold = 0.42
    p.segment = True
    p.set_model(p.models_directory() / "x" / "best.pt")
    p.save()

    q = project.Project.open(tmp_path / "proj")
    assert q.config == p.config
    assert q.names == ["chirp", "rise"]
    assert q.recording_paths() == [[str(recording)]]
    assert q.reviewed_rects([recording]) == [dataset.Rect(1.0, 2.0, 100.0, 200.0)]
    assert q.rejected_boxes([recording])[0].cls == 1
    assert q.rejected_boxes([recording])[0].score == 0.4
    assert q.threshold == 0.42 and q.segment is True
    assert q.model_path() == str(q.models_directory() / "x" / "best.pt")


def test_a_recording_is_added_once(tmp_path):
    p = project.Project(directory=tmp_path)
    assert p.add_recording([tmp_path / "a.wav"]) is True
    assert p.add_recording([tmp_path / "a.wav"]) is False
    assert p.add_recording([tmp_path / "b.wav", tmp_path / "c.wav"]) is True
    assert len(p.recording_paths()) == 2


def test_paths_are_relative_so_a_project_can_move_with_its_recordings(tmp_path):
    p = project.Project(directory=tmp_path / "rec-specdetector")
    p.add_recording([tmp_path / "rec.wav"])
    p.save()
    assert (
        ".."
        in json.loads((tmp_path / "rec-specdetector" / "project.json").read_text())[
            "recordings"
        ][0][0]
    )


def test_an_unreadable_project_is_an_error_and_not_a_fresh_start(tmp_path):
    (tmp_path / project.PROJECT_FILE).write_text('{"names": [')
    with pytest.raises(ValueError):
        project.Project.open(tmp_path)


def test_a_project_from_the_future_is_refused(tmp_path):
    (tmp_path / project.PROJECT_FILE).write_text(json.dumps({"version": 99}))
    with pytest.raises(ValueError, match="newer"):
        project.Project.open(tmp_path)


def test_a_failed_write_leaves_the_old_file_and_no_debris(tmp_path, monkeypatch):
    path = tmp_path / "file.json"
    project.replace_atomically(path, "old")

    def fail(*_args):
        raise OSError("disk full")

    monkeypatch.setattr(project.os, "replace", fail)
    with pytest.raises(OSError):
        project.replace_atomically(path, "new")
    assert path.read_text() == "old"
    assert [p.name for p in tmp_path.iterdir()] == ["file.json"]


def test_candidates_survive_and_a_missing_file_is_none(tmp_path):
    p = project.Project(directory=tmp_path)
    recording = [tmp_path / "rec.wav"]
    assert p.load_candidates(recording) == ([], {})
    found = [Box(1.0, 1.1, 2.0, 3.0, 0, 0.5, polygon=((1.0, 2.0), (1.1, 3.0)))]
    p.save_candidates(recording, found, {"source": "model"})
    boxes, meta = p.load_candidates(recording)
    assert boxes == found
    assert meta == {"source": "model"}


def test_an_edited_box_does_not_find_the_old_outline():
    key = project.mask_key("x", 1.0, 2.0, 3.0, 4.0)
    assert key == project.mask_key("x", 1.0000001, 2.0, 3.0, 4.0)  # read back from disk
    assert key != project.mask_key("x", 1.01, 2.0, 3.0, 4.0)
    assert key != project.mask_key("y", 1.0, 2.0, 3.0, 4.0)
