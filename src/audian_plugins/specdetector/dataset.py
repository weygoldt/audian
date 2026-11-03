"""Turning labels and reviewed rectangles into a YOLO dataset.

A detector is taught that whatever is on a training image and is not boxed is
background, so an event nobody got round to labelling is not neutral: it teaches
the model to ignore events.  That is the one rule this module exists to enforce,
and it does so by masking.

**Only what a person has looked at reaches the model.**  What a person has looked
at is a *rectangle in time and frequency*, because that is what a view is, and
because labelling is often done for one band: the hand labels of the chirp
benchmark cover 684-960 Hz of a recording that has fish outside that band too.
Every pixel of a training tile outside all reviewed rectangles is overwritten with
background taken from the same tile (`mask_outside`).

A label outside any reviewed rectangle is not wasted.  It brings a small one of its
own, its box and a margin (`implicit_rect`): the person saw at least that much.
This is what makes a detector trainable from a few shots, and a rejected candidate
goes the same way, as a reviewed rectangle with nothing in it -- the hard negative
that makes the next round better.

Two more rules, from what the chirp detector this replaces got wrong:

* **Validation tiles never share time with training tiles.**  Its five folds were
  drawn per tile, which measured 99 % recall in cross-validation and 76 % on
  recordings it had not seen.  `split_tiles` holds out whole blocks and drops
  any training tile that overlaps one.
* **An event cut off by the edge of a tile is not background.**  It kept only boxes
  wholly inside a window, and so taught the slivers as negatives.  Here a box that
  is mostly on the tile is kept, clipped, and the sliver of one that is not is
  masked (`VISIBLE_FRACTION`).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, NamedTuple, Optional, Sequence

import numpy as np

from . import tiling
from .boxes import Box, as_array, iou_matrix

#: How much of a box must be on a tile for it to count as an event there.
VISIBLE_FRACTION = 0.6

#: Margin around a label that has no reviewed rectangle, as a multiple of its size.
IMPLICIT_MARGIN = 0.5

#: Tiles made per small reviewed rectangle.  The same event at different places on
#: the image: a shift in time is free here, since the tile is rendered from the
#: recording rather than cropped from a fixed picture.
JITTER_COPIES = 8

#: The step of a sweep over a long reviewed rectangle, as a fraction of a tile.
SWEEP_FRACTION = 0.25

#: Every this-many block of tiles is validation, unless the reader asks otherwise.
VALIDATION_EVERY = 5
#: The length of a block of tiles, in tiles.  A block is what is split.
BLOCK_LENGTHS = 4
#: Below this many blocks with an event in them, a held-out split would leave too
#: little to train on and validate nothing: training reports on itself and says so.
#: It is `VALIDATION_EVERY`, and a reader holding out more often lowers both.
MIN_BLOCKS_TO_SPLIT = 5
#: The least that can be split at all: one block to train on, one to hold out.
FEWEST_BLOCKS_TO_SPLIT = 2

#: Two labels of one class this similar are one event drawn on two lanes.
SAME_EVENT_IOU = 0.5


class Rect(NamedTuple):
    """Part of the time-frequency plane somebody has looked at.

    No frequency means all of them.  `classes` is which classes it was reviewed for,
    by index: a class added later has not been looked for here, so the rectangle says
    nothing about it.  None means all of them, as a reviewed region declared in the
    labels themselves does.
    """

    t0: float
    t1: float
    f0: Optional[float] = None
    f1: Optional[float] = None
    classes: Optional[tuple] = None

    def reviewed_for(self, cls: int) -> bool:
        return self.classes is None or cls in self.classes


def rect_from_list(values) -> Rect:
    """A rectangle off the disk or out of a request."""
    values = list(values)
    classes = values[4] if len(values) > 4 else None
    return Rect(*values[:4], None if classes is None else tuple(classes))


def implicit_rect(box: Box, margin: float = IMPLICIT_MARGIN) -> Rect:
    """What a person must have looked at to draw `box`."""
    dt = (box.t1 - box.t0) * margin
    df = (box.f1 - box.f0) * margin
    return Rect(box.t0 - dt, box.t1 + dt, max(box.f0 - df, 0.0), box.f1 + df)


@dataclass
class Source:
    """One recording's contribution to a dataset."""

    paths: Sequence[str]
    boxes: list = field(default_factory=list)
    #: rectangles reviewed exhaustively: everything in them is in `boxes`
    reviewed: list = field(default_factory=list)
    #: rejected candidates, as boxes: reviewed, and known to hold no event
    rejected: list = field(default_factory=list)
    #: rectangles to keep off every image: a label with no band, say, marks an event
    #: that cannot be boxed and must not be taught as background either
    hidden: list = field(default_factory=list)

    def rectangles(self, classes: Optional[int] = None) -> list:
        """Every rectangle looked at: reviewed, plus one per stray box or rejection.

        A rectangle reviewed for only some of the `classes` being trained is not one:
        the others may be in it, unboxed.  What was labelled there brings its own small
        rectangle, as it would anywhere.
        """
        complete = [
            rect
            for rect in self.reviewed
            if classes is None
            or rect.classes is None
            or set(range(classes)) <= set(rect.classes)
        ]
        rects = list(complete)
        for box in list(self.boxes) + list(self.rejected):
            if not any(_inside(box, r) for r in complete):
                rects.append(implicit_rect(box))
        return rects


def _inside(box: Box, rect: Rect) -> bool:
    if box.t0 < rect.t0 or box.t1 > rect.t1:
        return False
    if rect.f0 is not None and box.f0 < rect.f0:
        return False
    if rect.f1 is not None and box.f1 > rect.f1:
        return False
    return True


def merge_lanes(boxes: Sequence[Box]) -> list:
    """Drop duplicates of one event drawn on several lanes.

    Real sessions have them: the twelve ``volley`` labels of one are six events,
    each drawn on both channels.  On a reduced image they are one thing, and two
    identical targets for one object is a confusing lesson.
    """
    kept: list = []
    longest = max((b.t1 - b.t0 for b in boxes), default=0.0)
    for box in sorted(boxes, key=lambda b: (b.t0, b.t1)):
        duplicate = False
        # Sorted by start, so what lies further back than the longest box cannot reach
        # this one.  Its own end says nothing: a long box may be behind a short one.
        for other in reversed(kept):
            if other.t0 + longest < box.t0:
                break
            if other.t1 < box.t0 or other.cls != box.cls:
                continue
            if iou_matrix(as_array([other]), as_array([box]))[0, 0] > SAME_EVENT_IOU:
                duplicate = True
                break
        if not duplicate:
            kept.append(box)
    return kept


class TilePlan(NamedTuple):
    """One image to write."""

    source: int
    s0: int
    split: str
    #: a stable name, so that a rebuild overwrites rather than accumulates
    stem: str


def plan_tiles(
    rects: Sequence[Rect],
    grid: tiling.Grid,
    frames_total: int,
    seed: int = 0,
    jitter_copies: int = JITTER_COPIES,
) -> list:
    """The samples to start tiles at so that every rectangle is covered.

    A rectangle longer than a tile is swept, like a recording.  A shorter one is
    placed `jitter_copies` times at random on the tile, so that one event does not
    always land on the same pixels and teach the model where events are.
    """
    rng = np.random.default_rng(seed)
    length = grid.frames * grid.hop
    # Denser than a detection sweep, which only has to see each event whole once.
    # Here each event should land at several places, and it costs only rendering.
    step = max(int(length * SWEEP_FRACTION), 1)
    last = max(frames_total - length, 0)
    starts = set()
    for rect in rects:
        a = int(round(rect.t0 * grid.rate))
        b = int(round(rect.t1 * grid.rate))
        if b - a >= length:
            for s0 in range(a, b - length, step):
                starts.add(int(np.clip(s0, 0, last)))
            starts.add(int(np.clip(b - length, 0, last)))
        else:
            # The rectangle stays wholly on the tile, wherever it lands.
            low, high = b - length, a
            for _ in range(jitter_copies):
                s0 = int(rng.integers(low, high + 1)) if high > low else low
                starts.add(int(np.clip(s0, 0, last)))
    return sorted(starts)


def split_tiles(
    tiles: Sequence[tuple],
    grids,
    has_event: Sequence[bool],
    every: int = VALIDATION_EVERY,
) -> list:
    """``"train"`` or ``"val"`` per tile of ``(source, s0)``; None drops it.

    `every` is how often a block is held out: every fifth by default, as often as
    every second for a reader with few places labelled, and 0 for never.  Holding
    out every `every`-th block needs that many blocks with an event, and never
    fewer than two.

    What is split is **stretches of time**, not tiles: tiles are gathered into
    blocks at most `BLOCK_LENGTHS` tiles long, every `VALIDATION_EVERY`-th block
    with an event in it is held out, and a training tile that shares any time with
    a held-out one is dropped.  Near-identical pictures on both sides of a split is
    how a validation score comes to say nothing.

    The number of tiles is no measure of what can be held out.  Five shots rendered
    sixteen times each are eighty tiles and five events, and the first version of
    this function, which counted tiles, held out sixteen of them and then dropped
    the other sixty-four for overlapping those.  Below `MIN_BLOCKS_TO_SPLIT` blocks
    with an event there is nothing to spare, so everything trains; `build_dataset`
    reports that, and the panel says that the score is a training score.

    `grids` is one grid, or one per source, since recordings at different sampling
    rates have tiles of different lengths in samples.
    """
    n = len(tiles)
    everything = ["train"] * n
    if not n:
        return everything

    def length_of(source: int) -> int:
        grid = grids if hasattr(grids, "frames") else grids[source]
        return grid.frames * grid.hop

    blocks: list = []
    for i in sorted(range(n), key=lambda i: tiles[i]):
        source, s0 = tiles[i]
        if (
            blocks
            and tiles[blocks[-1][0]][0] == source
            and s0 - tiles[blocks[-1][0]][1] < BLOCK_LENGTHS * length_of(source)
        ):
            blocks[-1].append(i)
        else:
            blocks.append([i])
    eventful = [b for b in blocks if any(has_event[i] for i in b)]
    every = int(every)
    if every <= 0 or len(eventful) < max(every, FEWEST_BLOCKS_TO_SPLIT):
        return everything
    held = [b for index, b in enumerate(eventful) if index % every == every // 2]
    splits: list = list(everything)
    kept = []
    for block in held:
        for i in block:
            splits[i] = "val"
            kept.append(tiles[i])
    for i in range(n):
        if splits[i] == "val":
            continue
        source, s0 = tiles[i]
        length = length_of(source)
        if any(s == source and abs(s0 - h) < length for s, h in kept):
            splits[i] = None
    if not any(has_event[i] for i in range(n) if splits[i] == "train"):
        return everything  # pragma: no cover - needs events only beside held blocks
    return splits


def looked_at_mask(geometry: tiling.TileGeometry, rects: Iterable[Rect]) -> np.ndarray:
    """``(height, width)`` of bool: which pixels of a tile someone has looked at."""
    mask = np.zeros((geometry.height, geometry.width), dtype=bool)
    for rect in rects:
        if rect.t1 < geometry.t0 or rect.t0 > geometry.t1:
            continue
        x0 = int(np.floor(geometry.x_of(rect.t0)))
        x1 = int(np.ceil(geometry.x_of(rect.t1)))
        y0 = 0 if rect.f1 is None else int(np.floor(geometry.y_of(rect.f1)))
        y1 = (
            geometry.height if rect.f0 is None else int(np.ceil(geometry.y_of(rect.f0)))
        )
        mask[max(y0, 0) : max(y1, 0), max(x0, 0) : max(x1, 0)] = True
    return mask


def mask_outside(image: np.ndarray, looked_at: np.ndarray, seed: int) -> np.ndarray:
    """`image` with every pixel nobody looked at replaced by this tile's own floor.

    Drawn at random from the darker half of the tile's own reviewed pixels, so that
    what stands in for "not looked at" has the brightness and the grain of this
    tile's noise.  A flat colour would be a shape the model could learn; so would
    black, which appears nowhere on a real tile.
    """
    if looked_at.all():
        return image
    out = image.copy()
    rng = np.random.default_rng(seed)
    hidden = ~looked_at
    n = int(hidden.sum())
    for plane in range(image.shape[2]):
        values = image[..., plane]
        seen = values[looked_at] if looked_at.any() else values.ravel()
        floor = seen[seen <= np.median(seen)]
        if not len(floor):
            floor = seen
        out[..., plane][hidden] = rng.choice(floor, size=n)
    return out


def tile_labels(
    boxes: Sequence[Box], geometry: tiling.TileGeometry, segment: bool = False
) -> tuple:
    """What a tile's label file holds, and the slivers to hide: ``(lines, slivers)``.

    Each line is YOLO's ``class cx cy w h`` or, with `segment`, ``class x y x y ...``,
    normalised and with y running down the image.  A box is on the tile if at least
    `VISIBLE_FRACTION` of it is.  Otherwise any part of it on the tile comes back as
    a `Rect` to mask: neither an event nor background.
    """
    lines, slivers = [], []
    for box in boxes:
        t0 = max(box.t0, geometry.t0)
        t1 = min(box.t1, geometry.t1)
        f0 = max(box.f0, geometry.f0)
        f1 = min(box.f1, geometry.f1)
        if t1 <= t0 or f1 <= f0:
            continue
        whole = (box.t1 - box.t0) * (box.f1 - box.f0)
        visible = (t1 - t0) * (f1 - f0)
        if whole <= 0 or visible / whole < VISIBLE_FRACTION:
            slivers.append(Rect(t0, t1, f0, f1))
            continue
        if segment:
            lines.append(_polygon_line(box, geometry, (t0, t1, f0, f1)))
        else:
            x0, y0, x1, y1 = geometry.box_to_pixels(t0, t1, f0, f1)
            w, h = geometry.width, geometry.height
            lines.append(
                f"{box.cls} {(x0 + x1) / 2 / w:.6f} {(y0 + y1) / 2 / h:.6f} "
                f"{(x1 - x0) / w:.6f} {(y1 - y0) / h:.6f}"
            )
    return lines, slivers


def _polygon_line(box: Box, geometry: tiling.TileGeometry, clip: tuple) -> str:
    """A segmentation line: the box's own outline, or the box when it has none.

    SAM draws the outline where one is known.  A box that has none, one dragged by
    hand and never prompted, gets its rectangle, which is a coarse mask and still
    a correct one.
    """
    t0, t1, f0, f1 = clip
    polygon = box.polygon
    # Under three points is no outline.  Two would be read by Ultralytics as a box, and
    # one as a corrupt image.
    if polygon is None or len(polygon) < 3:
        polygon = ((t0, f0), (t1, f0), (t1, f1), (t0, f1))
    w, h = geometry.width, geometry.height
    cells = [str(box.cls)]
    for t, f in polygon:
        x = float(np.clip(geometry.x_of(np.clip(t, t0, t1)), 0, w)) / w
        y = float(np.clip(geometry.y_of(np.clip(f, f0, f1)), 0, h)) / h
        cells += [f"{x:.6f}", f"{y:.6f}"]
    return " ".join(cells)


class DatasetReport(NamedTuple):
    """What was written, and what it can claim."""

    yaml: str
    train_tiles: int
    val_tiles: int
    train_events: int
    val_events: int
    background_tiles: int
    #: whether validation is on tiles training never saw
    held_out: bool
    #: how many boxes each class has on the images.  A class can come to none: its
    #: band may be outside the images, or its events too long for a tile.
    events_per_class: tuple = ()


def build_dataset(
    sources: Sequence[Source],
    config: tiling.SpecConfig,
    names: Sequence[str],
    directory,
    open_recording: Callable,
    *,
    segment: bool = False,
    seed: int = 0,
    jitter_copies: int = JITTER_COPIES,
    write_image: Optional[Callable] = None,
    progress: Optional[Callable] = None,
    check: Optional[Callable] = None,
    masking: bool = True,
    hold_out_every: int = VALIDATION_EVERY,
) -> DatasetReport:
    """Write the tiles, the labels and the yaml Ultralytics reads.

    `hold_out_every` is `split_tiles`' `every`: 0 holds nothing out.

    `directory` is rebuilt from nothing: a dataset is a cache of the sources, and
    leftover tiles of a label since deleted would be silently trained on.

    `masking` False leaves unreviewed pixels on the image.  It exists to measure
    what the rule is worth, and nothing in the plugin passes it.
    """
    import shutil

    if write_image is None:
        import cv2

        write_image = lambda path, image: cv2.imwrite(os.fspath(path), image)  # noqa: E731

    root = Path(directory)
    if root.exists():
        shutil.rmtree(root)
    for part in ("images/train", "images/val", "labels/train", "labels/val"):
        (root / part).mkdir(parents=True)

    planned = []
    grids = []
    for index, source in enumerate(sources):
        with open_recording(source.paths) as recording:
            grid = tiling.grid_of(config, recording.rate)
            grids.append(grid)
            rects = source.rectangles(len(names))
            boxes = merge_lanes(source.boxes)
            starts = plan_tiles(
                rects,
                grid,
                recording.frames,
                seed=seed + index,
                jitter_copies=jitter_copies,
            )
            for s0 in starts:
                geometry = tiling.geometry_of(grid, s0, config.image_size)
                on_tile = [
                    b for b in boxes if b.t1 >= geometry.t0 and b.t0 <= geometry.t1
                ]
                lines, _ = tile_labels(on_tile, geometry, segment)
                planned.append((index, s0, bool(lines)))
    tiles = [(index, s0) for index, s0, _ in planned]
    events = [has for _, _, has in planned]
    splits = split_tiles(tiles, grids, events, hold_out_every) if tiles else []
    held_out = "val" in splits
    total = sum(split is not None for split in splits)

    counts = {"train": [0, 0], "val": [0, 0]}
    per_class = [0] * len(names)
    background = 0
    done = 0
    for index, source in enumerate(sources):
        rects = source.rectangles(len(names))
        boxes = merge_lanes(source.boxes)
        wanted = [
            (s0, split)
            for (i, s0), split in zip(tiles, splits)
            if i == index and split is not None
        ]
        if not wanted:
            continue
        with open_recording(source.paths) as recording:
            grid = tiling.grid_of(config, recording.rate)
            for s0, split in wanted:
                if check is not None:
                    check()
                image, geometry = tiling.render_tile(recording, grid, s0, config)
                on_tile = [
                    b for b in boxes if b.t1 >= geometry.t0 and b.t0 <= geometry.t1
                ]
                lines, slivers = tile_labels(on_tile, geometry, segment)
                looked_at = looked_at_mask(geometry, rects)
                hide = list(slivers) + list(source.hidden)
                if hide:
                    looked_at &= ~looked_at_mask(geometry, hide)
                if masking:
                    image = mask_outside(image, looked_at, seed=s0 % (2**32))
                stem = f"s{index:02d}_{s0:012d}"
                write_image(root / "images" / split / f"{stem}.png", image)
                (root / "labels" / split / f"{stem}.txt").write_text(
                    "\n".join(lines) + ("\n" if lines else "")
                )
                counts[split][0] += 1
                counts[split][1] += len(lines)
                for line in lines:
                    per_class[int(line.split(" ", 1)[0])] += 1
                background += not lines
                done += 1
                if progress is not None:
                    progress(done, total)

    val = "images/val" if held_out else "images/train"
    yaml = root / "data.yaml"
    # JSON strings are YAML strings: a directory called ``fish #3`` must not read as a
    # path ending at a comment.
    listed = "\n".join(f"  {i}: {json.dumps(name)}" for i, name in enumerate(names))
    where = json.dumps(os.fspath(root.resolve()))
    yaml.write_text(
        f"path: {where}\ntrain: images/train\nval: {val}\nnames:\n{listed}\n"
    )
    return DatasetReport(
        yaml=os.fspath(yaml),
        train_tiles=counts["train"][0],
        val_tiles=counts["val"][0],
        train_events=counts["train"][1],
        val_events=counts["val"][1],
        background_tiles=background,
        held_out=held_out,
        events_per_class=tuple(per_class),
    )
