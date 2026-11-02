"""Boxes on the time-frequency plane: overlap, suppression, scoring.

Numpy only.  A box here is in **seconds and hertz**, never in pixels: `tiling` owns
the pixels, and nothing downstream of a model should be able to tell which tile an
event was found in.
"""

from __future__ import annotations

from typing import Iterable, NamedTuple, Optional, Sequence

import numpy as np


class Box(NamedTuple):
    """One event: a human's, or a model's with a score below 1."""

    t0: float
    t1: float
    f0: float
    f1: float
    #: index into the project's class names
    cls: int = 0
    #: how sure the model is; 1.0 for a human
    score: float = 1.0
    #: the outline as ``[(t, f), ...]``, or None when there is only the box
    polygon: Optional[tuple] = None
    #: which electrode, or None for "not one"
    channel: Optional[int] = None

    @property
    def centre_t(self) -> float:
        return 0.5 * (self.t0 + self.t1)


def as_array(boxes: Sequence[Box]) -> np.ndarray:
    """``(n, 4)`` of ``t0, t1, f0, f1``."""
    if not len(boxes):
        return np.zeros((0, 4))
    return np.array([(b.t0, b.t1, b.f0, b.f1) for b in boxes], dtype=float)


def iou_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Intersection over union of every box in `a` with every box in `b`.

    The plane's two axes are in different units, and that does not matter: IoU is
    a ratio of areas, so it does not change when an axis is rescaled.
    """
    a = a[:, None, :]
    b = b[None, :, :]
    dt = np.minimum(a[..., 1], b[..., 1]) - np.maximum(a[..., 0], b[..., 0])
    df = np.minimum(a[..., 3], b[..., 3]) - np.maximum(a[..., 2], b[..., 2])
    inter = np.clip(dt, 0, None) * np.clip(df, 0, None)
    area_a = (a[..., 1] - a[..., 0]) * (a[..., 3] - a[..., 2])
    area_b = (b[..., 1] - b[..., 0]) * (b[..., 3] - b[..., 2])
    union = area_a + area_b - inter
    return np.divide(inter, union, out=np.zeros_like(inter), where=union > 0)


def nms(boxes: Sequence[Box], iou: float = 0.5, class_aware: bool = True) -> list:
    """The strongest of each group of overlapping boxes, best first.

    The chirp detector this replaces suppressed per batch and never again after
    joining the batches, so an event in the overlap at a batch boundary could be
    counted twice (its benchmark lists this as an open issue).  This one runs once
    over a whole recording.
    """
    if not len(boxes):
        return []
    order = sorted(range(len(boxes)), key=lambda i: -boxes[i].score)
    array = as_array(boxes)
    # Sorted by start time, a box can only collide with its neighbours in that
    # order, which is what keeps this usable on 100 000 boxes.
    by_time = np.argsort(array[:, 0])
    starts = array[by_time, 0]
    longest = float(np.max(array[:, 1] - array[:, 0]))
    alive = np.ones(len(boxes), dtype=bool)
    keep = []
    for i in order:
        if not alive[i]:
            continue
        keep.append(boxes[i])
        alive[i] = False
        lo = np.searchsorted(starts, array[i, 0] - longest, side="left")
        hi = np.searchsorted(starts, array[i, 1], side="right")
        near = by_time[lo:hi]
        near = near[alive[near]]
        if class_aware and len(near):
            near = near[[boxes[j].cls == boxes[i].cls for j in near]]
        if len(near):
            overlap = iou_matrix(array[[i]], array[near])[0]
            alive[near[overlap > iou]] = False
    return keep


class Match(NamedTuple):
    """How a set of found boxes scores against the truth."""

    tp: int
    fp: int
    fn: int

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else 0.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else 0.0

    @property
    def f1(self) -> float:
        denom = 2 * self.tp + self.fp + self.fn
        return 2 * self.tp / denom if denom else 0.0


def match_events(
    found: Iterable[Box],
    truth: Iterable[Box],
    time_tol_s: float = 0.1,
    frequency_overlap: bool = True,
) -> Match:
    """One-to-one matching: nearest centre within `time_tol_s`, best score first.

    The default is the rule the chirp benchmark scored the old detector by --
    centres within 100 ms and any overlap in frequency -- so that numbers from the
    two can be set side by side.  It says "this event was found", and says nothing
    about how tight the box is.
    """
    found = sorted(found, key=lambda b: -b.score)
    truth = sorted(truth, key=lambda b: b.centre_t)
    used = [False] * len(truth)
    centres = np.array([b.centre_t for b in truth])
    tp = 0
    for box in found:
        if not len(truth):
            break
        lo = np.searchsorted(centres, box.centre_t - time_tol_s, side="left")
        hi = np.searchsorted(centres, box.centre_t + time_tol_s, side="right")
        best, distance = None, None
        for j in range(lo, hi):
            if used[j] or truth[j].cls != box.cls:
                continue
            if frequency_overlap and (box.f0 > truth[j].f1 or box.f1 < truth[j].f0):
                continue
            d = abs(centres[j] - box.centre_t)
            if distance is None or d < distance:
                best, distance = j, d
        if best is not None:
            used[best] = True
            tp += 1
    return Match(tp=tp, fp=len(found) - tp, fn=len(truth) - tp)


def auto_cut(scores: Sequence[float], default: float = 0.25) -> float:
    """A score cut read off the scores themselves, where nothing else can say.

    A trained model has a validation set to pick its cut on.  Prompted YOLOE has
    none, and its scores sit nowhere in particular: the best cut on real chirps was
    0.02 for one set of shots and 0.10 for another.  What separates hits from noise
    there is that the scores are two populations on a log scale, so this is Otsu's
    threshold on the logarithm.
    """
    scores = np.asarray(scores, dtype=float)
    scores = scores[scores > 0]
    if len(scores) < 8:
        return default
    values = np.log(scores)
    counts, edges = np.histogram(values, bins=64)
    centres = 0.5 * (edges[:-1] + edges[1:])
    weight = np.cumsum(counts).astype(float)
    total = weight[-1]
    mean = np.cumsum(counts * centres)
    with np.errstate(divide="ignore", invalid="ignore"):
        between = (mean[-1] * weight - total * mean) ** 2 / (weight * (total - weight))
    between = np.nan_to_num(between, nan=0.0, posinf=0.0)
    if between.max() <= 0:
        return default
    return float(np.exp(edges[int(np.argmax(between)) + 1]))


def best_threshold(found: Sequence[Box], truth: Sequence[Box], **kwargs) -> tuple:
    """The score cut with the highest F1, and its `Match`: ``(score, match)``."""
    scores = sorted({round(b.score, 3) for b in found})
    best = (0.0, match_events(found, truth, **kwargs))
    for score in scores:
        result = match_events([b for b in found if b.score >= score], truth, **kwargs)
        if result.f1 > best[1].f1:
            best = (score, result)
    return best
