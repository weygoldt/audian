"""Which stretch of the recording a person should look at next.

The point of active learning is that a reviewer's attention is the scarce thing.
Candidates are grouped into windows, because reviewing means looking at everything
in a view and saying the view is done; and windows are ranked so that the ones whose
answer would teach the model most come first.  Pure logic: no Qt, no numpy beyond
sorting, nothing about models.

A window is ranked by how many of its candidates sit near the model's cut, where the
model is least sure.  A window of confident hits teaches little, and neither does one
of obvious noise.  That leaves the failure no ranking of candidates can see: events
the model scored below everything and did not propose at all.  So one window in
`EXPLORE_EVERY` is drawn at random, candidates or not.  Whether either of these earns
its keep is measured in ``docs/specdetector-design.md``.
"""

from __future__ import annotations

import random
from typing import NamedTuple, Optional, Sequence

from .boxes import Box

#: Rankings on offer.  Only ``uncertain`` ships; the others are the baselines it was
#: measured against, and stay callable so that the measurement stays repeatable.
STRATEGIES = ("uncertain", "random", "time")

#: One window in this many is a blind draw, so that the model's own idea of where
#: events are does not get to decide everything that is ever looked at.
EXPLORE_EVERY = 5


class ReviewWindow(NamedTuple):
    """One stretch to look at."""

    t0: float
    t1: float
    #: indices into the candidates
    candidates: tuple
    #: higher is sooner
    priority: float
    #: drawn at random rather than chosen for what it holds
    blind: bool = False


def uncertainty(score: float, threshold: float) -> float:
    """1 at the model's cut, falling to 0 at the far end of the score range."""
    reach = max(threshold, 1.0 - threshold, 1e-6)
    return max(0.0, 1.0 - abs(score - threshold) / reach)


def is_reviewed(box: Box, reviewed: Sequence) -> bool:
    """Whether a box's centre lies in a rectangle somebody has finished with."""
    t = box.centre_t
    f = 0.5 * (box.f0 + box.f1)
    for rect in reviewed:
        if not rect.t0 <= t <= rect.t1 or not rect.reviewed_for(box.cls):
            continue
        if rect.f0 is not None and f < rect.f0:
            continue
        if rect.f1 is not None and f > rect.f1:
            continue
        return True
    return False


def group_windows(
    candidates: Sequence[Box],
    length_s: float,
    reviewed: Sequence = (),
    span: Optional[tuple] = None,
) -> list:
    """Windows of `length_s` covering every candidate not yet reviewed, left to right.

    A window starts a little before its first candidate, so that nothing begins
    right at the edge of the view.
    """
    order = sorted(
        (i for i, b in enumerate(candidates) if not is_reviewed(b, reviewed)),
        key=lambda i: candidates[i].t0,
    )
    margin = 0.1 * length_s
    windows: list = []
    current: list = []
    start = 0.0
    for i in order:
        box = candidates[i]
        if current and box.t1 <= start + length_s:
            current.append(i)
            continue
        if current:
            windows.append((start, start + length_s, tuple(current)))
        start = box.t0 - margin
        if span is not None:
            start = max(start, span[0])
            start = min(start, max(span[1] - length_s, span[0]))
        current = [i]
    if current:
        windows.append((start, start + length_s, tuple(current)))
    return windows


def rank_windows(
    candidates: Sequence[Box],
    length_s: float,
    threshold: float,
    reviewed: Sequence = (),
    *,
    strategy: str = "uncertain",
    span: Optional[tuple] = None,
    seed: int = 0,
    explore_every: int = EXPLORE_EVERY,
) -> list:
    """The windows to review, best first.

    `span` is ``(t0, t1)`` of the recording, and is what the blind draws are taken
    from: a window may hold no candidate at all.
    """
    if strategy not in STRATEGIES:
        raise ValueError(f"unknown strategy: {strategy}")
    rng = random.Random(seed)
    grouped = group_windows(candidates, length_s, reviewed, span)
    ranked = []
    for t0, t1, members in grouped:
        if strategy == "uncertain":
            priority = sum(uncertainty(candidates[i].score, threshold) for i in members)
        elif strategy == "time":
            priority = -t0
        else:
            priority = rng.random()
        ranked.append(ReviewWindow(t0, t1, members, priority))
    ranked.sort(key=lambda w: -w.priority)
    if strategy != "uncertain" or span is None or explore_every <= 0:
        return ranked
    out: list = []
    for index, window in enumerate(ranked):
        if index % explore_every == explore_every - 1:
            out.append(_blind_window(candidates, length_s, reviewed, span, rng))
        out.append(window)
    # However few candidates there are: the fewer the model proposes, the more a blind
    # look is worth, and `pick_window` must have one to take.
    if out and not any(window.blind for window in out):
        out.append(_blind_window(candidates, length_s, reviewed, span, rng))
    return out


def _blind_window(candidates, length_s, reviewed, span, rng) -> ReviewWindow:
    """A random window, avoiding what has been reviewed where that is possible."""
    low, high = span[0], max(span[1] - length_s, span[0])
    t0 = low
    for _ in range(16):
        t0 = rng.uniform(low, high)
        centre = t0 + 0.5 * length_s
        if not any(r.t0 <= centre <= r.t1 for r in reviewed):
            break
    members = tuple(
        i for i, b in enumerate(candidates) if t0 <= b.t0 and b.t1 <= t0 + length_s
    )
    return ReviewWindow(t0, t0 + length_s, members, 0.0, blind=True)


def pick_window(ranked: Sequence, finished: int, explore_every: int = EXPLORE_EVERY):
    """Which of `ranked` to look at after `finished` windows, or None.

    A reader takes one window at a time and the ranking is made afresh for each, so its
    first entry is never the blind one and the rule above would never reach them.
    Every `explore_every`-th window is therefore taken blind on purpose.
    """
    if not ranked:
        return None
    if explore_every > 0 and finished % explore_every == explore_every - 1:
        for window in ranked:
            if window.blind:
                return window
    return next((w for w in ranked if not w.blind), ranked[0])
