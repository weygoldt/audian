"""wavetracker's review flags in the sorter: the places a tracked result is
most likely wrong, with a calibrated confidence and a suggested fix.

numpy and the standard library only.  wavetracker writes ``review.json``
into a results directory (format: wavetracker's ``docs/review-flags.md``);
this module reads it, moves the items of a snippet run into the session's
frames, and maps a snippet's ids onto the session's when it is accepted.
`TrackSet` keeps the session's items and turns the ones that still apply
into issues (`TrackSet.review_issues`).

Each item is a ``join`` (two fragments joined into one identity may be two
fish: split it at frame ``k``), a ``track`` (an identity may not be a fish:
unassign it between ``t0`` and ``t1``) or a ``missed_join`` (two identities
may be one fish: merge ``ids[1]`` into ``ids[0]``).  ``confidence`` is the
probability that the result is right at that place.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np

REVIEW_FILE = "review.json"
#: the version of review.json this reader was written for; a newer one is
#: read as far as its items parse (readers ignore unknown keys)
REVIEW_VERSION = 1
KINDS = {"join": "split", "track": "delete", "missed_join": "merge"}


@dataclass(frozen=True)
class ReviewItem:
    kind: str  # "join", "track" or "missed_join"
    source: str  # "linker", "rise" or "identity"
    confidence: float  # probability that the result is right here
    t: float  # where to look [s, session time]
    f: float  # [Hz]
    t0: float  # time span to show; a track: the part a delete unassigns
    t1: float
    ids: tuple[float, ...]  # one id (join, track) or two (missed_join)
    action: str  # "split", "delete" or "merge"
    impact: int  # detections the fix would move
    why: str
    k: int | None = None  # session frame where a split cuts (frames >= k)


def _number(d: dict, key: str) -> float:
    v = d[key]
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise TypeError(key)
    v = float(v)
    if not math.isfinite(v):
        raise ValueError(key)
    return v


def parse_item(d) -> ReviewItem:
    """One item of review.json; raises KeyError, TypeError or ValueError
    on anything this reader cannot use."""
    if not isinstance(d, dict):
        raise TypeError("item")
    kind = d["kind"]
    if kind not in KINDS:
        raise ValueError(f"kind {kind!r}")
    action = d.get("action", KINDS[kind])
    if action != KINDS[kind]:
        raise ValueError(f"{kind} with action {action!r}")
    ids = d["ids"]
    if not isinstance(ids, list):
        raise TypeError("ids")
    ids = tuple(float(i) for i in ids)
    if len(ids) != (2 if kind == "missed_join" else 1) or not all(
        math.isfinite(i) for i in ids
    ):
        raise ValueError("ids")
    confidence = _number(d, "confidence")
    if not 0.0 <= confidence <= 1.0:
        raise ValueError("confidence")
    k = None
    if action == "split":
        k = d["k"]
        if isinstance(k, bool) or not isinstance(k, int) or k < 0:
            raise ValueError("k")
    t0, t1 = _number(d, "t0"), _number(d, "t1")
    if t1 < t0:
        raise ValueError("t1 before t0")
    return ReviewItem(
        kind=kind,
        source=str(d.get("source", "")),
        confidence=confidence,
        t=_number(d, "t"),
        f=_number(d, "f"),
        t0=t0,
        t1=t1,
        ids=ids,
        action=action,
        impact=int(d.get("impact", 0)),
        why=str(d.get("why", "")),
        k=k,
    )


def parse_items(items) -> tuple[tuple[ReviewItem, ...], int]:
    """The items that parse, and how many did not."""
    out, bad = [], 0
    for d in items if isinstance(items, list) else ():
        try:
            out.append(parse_item(d))
        except (KeyError, TypeError, ValueError):
            bad += 1
    return tuple(out), bad


def read_review(folder) -> tuple[tuple[ReviewItem, ...], list[str]]:
    """The items of ``review.json`` in `folder` and complaints about it.
    A missing file means no flags, and no complaint."""
    path = Path(folder) / REVIEW_FILE
    if not path.exists():
        return (), []
    try:
        doc = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        return (), [
            f"{REVIEW_FILE} could not be read ({exc}); no low-confidence places"
        ]
    if not isinstance(doc, dict):
        return (), [f"{REVIEW_FILE} is not a review file; ignored"]
    items, bad = parse_items(doc.get("items"))
    complaints = []
    if bad:
        complaints.append(f"{bad} item(s) of {REVIEW_FILE} could not be read; skipped")
    version = doc.get("version")
    if isinstance(version, int) and version > REVIEW_VERSION:
        complaints.append(
            f"{REVIEW_FILE} is version {version}, newer than this reader's "
            f"{REVIEW_VERSION}; read as far as it goes"
        )
    return items, complaints


def review_mtime(folder) -> float:
    try:
        return (Path(folder) / REVIEW_FILE).stat().st_mtime
    except OSError:
        return 0.0


def item_json(it: ReviewItem) -> dict:
    """An item as review.json spells it (session ids, frames and time)."""
    d = {
        "kind": it.kind,
        "source": it.source,
        "confidence": it.confidence,
        "t": it.t,
        "f": it.f,
        "t0": it.t0,
        "t1": it.t1,
        "ids": list(it.ids),
        "action": it.action,
        "impact": it.impact,
        "why": it.why,
    }
    if it.k is not None:
        d["k"] = it.k
    return d


def shift(items, dk: int, dt: float) -> tuple[ReviewItem, ...]:
    """Items of a run that started `dk` frames and `dt` seconds into the
    session, in session frames and time."""
    return tuple(
        replace(
            it,
            t=it.t + dt,
            t0=it.t0 + dt,
            t1=it.t1 + dt,
            k=None if it.k is None else it.k + int(dk),
        )
        for it in items
    )


def crop(items, k0: int, k1: int, idx, ident, time_of) -> tuple[ReviewItem, ...]:
    """The items of a snippet cut back to session frames [k0, k1): `idx`
    and `ident` are the cropped snippet's rows, `time_of(k)` the session
    time of frame k.

    A join stays when its id keeps rows on both sides of the cut inside the
    window (a junction whose earlier side lay in the context alone is no
    join of the snippet), a missed join when both ids keep rows and the
    later one starts inside, a track when its id keeps rows, its span
    clipped to them: deleting it then unassigns the window's part only.
    """
    idx = np.asarray(idx, dtype=np.int64)
    ident = np.asarray(ident, dtype=np.float64)
    frames: dict[float, np.ndarray] = {}
    for i in np.unique(ident[np.isfinite(ident)]):
        frames[float(i)] = np.sort(idx[ident == i])
    out = []
    for it in items:
        if it.kind == "join":
            k = frames.get(it.ids[0])
            if k is None or it.k is None or not k0 <= it.k < k1:
                continue
            if not ((k < it.k).any() and (k >= it.k).any()):
                continue
            out.append(it)
        elif it.kind == "missed_join":
            if any(i not in frames for i in it.ids):
                continue
            first = int(frames[it.ids[1]][0])
            if not k0 <= first < k1:
                continue
            out.append(it)
        else:
            k = frames.get(it.ids[0])
            if k is None:
                continue
            a, b = float(time_of(k[0])), float(time_of(k[-1]))
            t0, t1 = max(it.t0, a), min(it.t1, b)
            if t1 < t0:
                continue
            out.append(replace(it, t0=t0, t1=t1, t=max(it.t, t0)))
    return tuple(out)


def relabel(items, mapping: dict) -> tuple[ReviewItem, ...]:
    """Items with their ids renamed by `mapping`; an item with an id that
    has no new name is dropped, and so is a missed join whose two ids now
    have one."""
    out = []
    for it in items:
        try:
            ids = tuple(float(mapping[i]) for i in it.ids)
        except KeyError:
            continue
        if it.kind == "missed_join" and ids[0] == ids[1]:
            continue
        out.append(replace(it, ids=ids))
    return tuple(out)


def outside_span(items, k0: int, k1: int, t0: float, t1: float) -> tuple:
    """The session's own items a snippet over frames [k0, k1) (seconds
    t0-t1) does not replace: joins and missed joins whose place is inside
    go, tracks stay (their ids may live on outside the span)."""
    out = []
    for it in items:
        if it.kind == "join" and it.k is not None and k0 <= it.k < k1:
            continue
        if it.kind == "missed_join" and t0 <= it.t <= t1:
            continue
        out.append(it)
    return tuple(out)
