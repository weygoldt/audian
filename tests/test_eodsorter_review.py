"""wavetracker's review flags (review.json) in the EOD sorter's model:
reading them, the issues G visits, their fixes, snippets and saving."""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pytest

from audian_plugins.eodsorter import model_io
from audian_plugins.eodsorter import review as RV
from audian_plugins.eodsorter.model import (
    FrameGrid,
    Snippet,
    TrackSet,
    crop_snippet,
    load_snippet,
)

RATE, NFFT, STEP = 1000.0, 100, 50  # frames every 0.05 s
N_FRAMES = 200


def t_of(k):
    return (np.asarray(k) * STEP + NFFT / 2) / RATE


def meta():
    return {
        "rate": RATE,
        "start": 0.0,
        "frame_step": STEP / RATE,
        "freq_resolution": 1.0,
        "config": {
            "spectrogram": {"nfft": NFFT, "overlap_frac": 0.5},
            "tracking": {"max_dt": 0.5, "freq_tolerance": 2.5},
        },
    }


#: id 0: one fish, joined from fragments at frames 100 and 150; ids 1 and 2:
#: one fish in two tracks (frames 0-99, 110-199); id 3: a short doubtful
#: track; id 5: another fish
TRACKS = {
    0: [(k, 500.0) for k in range(N_FRAMES)],
    1: [(k, 600.0) for k in range(100)],
    2: [(k, 600.5) for k in range(110, N_FRAMES)],
    3: [(k, 800.0) for k in range(20, 41)],
    5: [(k, 900.0) for k in range(N_FRAMES)],
}


def item(kind, confidence, ids, t, f, t0=None, t1=None, k=None, why="why"):
    d = {
        "kind": kind,
        "source": {"join": "linker", "track": "identity"}.get(kind, "linker"),
        "confidence": confidence,
        "t": t,
        "f": f,
        "t0": t - 10.0 if t0 is None else t0,
        "t1": t + 10.0 if t1 is None else t1,
        "ids": [float(i) for i in ids],
        "action": RV.KINDS[kind],
        "impact": 10,
        "why": why,
    }
    if k is not None:
        d["k"] = k
    return d


def items():
    """Least sure first, as wavetracker writes them."""
    return [
        item("track", 0.2, [3], t_of(20), 800.0, t_of(20), t_of(40), why="21 dets"),
        item("join", 0.3, [0], t_of(100), 500.0, k=100, why="linker p = 0.71"),
        item("missed_join", 0.4, [1, 2], t_of(110), 600.5, why="not joined: p 0.4"),
        item("join", 0.6, [0], t_of(150), 500.0, k=150),
        item("track", 0.99, [0], t_of(0), 500.0, t_of(0), t_of(199)),
    ]


def write_folder(folder, tracks=TRACKS, review=None):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    fund, idx, ident = [], [], []
    for i, pts in tracks.items():
        for k, f in pts:
            fund.append(f)
            idx.append(k)
            ident.append(float(i))
    n = len(fund)
    np.save(folder / "fund_v.npy", np.array(fund, float))
    np.save(folder / "idx_v.npy", np.array(idx, np.int64))
    np.save(folder / "ident_v.npy", np.array(ident, float))
    np.save(folder / "sign_v.npy", np.ones((n, 2), np.float32))
    np.save(folder / "times.npy", t_of(np.arange(N_FRAMES)))
    (folder / "wavetracker.json").write_text(json.dumps(meta()))
    if review is not None:
        doc = {"version": 1, "calibration": None, "items": review}
        (folder / RV.REVIEW_FILE).write_text(json.dumps(doc))
    return folder


@pytest.fixture
def session(tmp_path):
    ts, complaints = TrackSet.load(write_folder(tmp_path / "r", review=items()))
    assert complaints == []
    return ts


# --------------------------------------------------------------- reading


def test_read_review_keeps_what_parses_and_says_what_did_not(tmp_path):
    bad = [
        item("join", 0.5, [0], 1.0, 500.0),  # a split without k
        {**item("track", 0.5, [3], 1.0, 800.0), "kind": "harmonic"},
        {**item("track", 1.5, [3], 1.0, 800.0)},  # not a probability
        {**item("missed_join", 0.5, [1], 1.0, 600.0)},  # one id for a pair
        {**item("track", 0.5, [3], 1.0, 800.0), "t": "soon"},
    ]
    folder = write_folder(tmp_path / "r", review=items() + bad)
    got, complaints = RV.read_review(folder)
    assert [it.confidence for it in got] == [0.2, 0.3, 0.4, 0.6, 0.99]
    assert got[1].k == 100 and got[1].ids == (0.0,) and got[1].why == "linker p = 0.71"
    assert got[2].ids == (1.0, 2.0) and got[2].action == "merge"
    assert complaints == [f"5 item(s) of {RV.REVIEW_FILE} could not be read; skipped"]
    # no file: no flags and nothing to say; a newer version: read, and said
    assert RV.read_review(tmp_path) == ((), [])
    doc = json.loads((folder / RV.REVIEW_FILE).read_text())
    doc["version"] = 2
    doc["new_key"] = {"ignored": True}
    (folder / RV.REVIEW_FILE).write_text(json.dumps(doc))
    got, complaints = RV.read_review(folder)
    assert len(got) == 5 and "version 2" in complaints[-1]
    (folder / RV.REVIEW_FILE).write_text("{not json")
    got, complaints = RV.read_review(folder)
    assert got == () and "could not be read" in complaints[0]


# --------------------------------------------------------------- issues


def test_review_issues_are_least_sure_first_below_the_cutoff(session):
    every = session.review_issues(1.0)
    assert [i.item.confidence for i in every] == [0.2, 0.3, 0.4, 0.6, 0.99]
    assert [i.kind for i in every] == ["review"] * 5
    assert [i.item.confidence for i in session.review_issues(0.5)] == [0.2, 0.3, 0.4]
    track, join, missed = session.review_issues(0.5)
    assert track.text.startswith("confidence 0.20 · is 3 a fish? 21 dets")
    assert join.text == (
        "confidence 0.30 · join in 0 at 00:05.0: linker p = 0.71 — Enter splits 0 there"
    )
    assert missed.text == (
        "confidence 0.40 · 1 and 2 not joined: p 0.4 — Enter merges 2 into 1"
    )
    assert (join.t, join.f) == (pytest.approx(t_of(100)), 500.0)


def test_a_split_cuts_at_the_join_and_later_joins_follow_the_new_id(session):
    join = session.review_issues(0.5)[1]
    plan = join.suggestion(session)
    assert plan.label == "Cut 0 at 00:05.0 (after → 6) (review, confidence 0.30)"
    session.apply(plan)
    k = session.idx[session.rows_of(6.0)]
    assert k.min() == 100 and k.max() == N_FRAMES - 1
    assert session.idx[session.rows_of(0.0)].max() == 99
    left = session.review_issues(1.0)
    assert [i.item.confidence for i in left] == [0.2, 0.4, 0.6, 0.99]
    # the join at frame 150 is in id 6 now, and says so
    later = left[2]
    assert later.ids == (6.0,) and "join in 6 at" in later.text
    session.undo()
    assert [i.item.confidence for i in session.review_issues(1.0)] == [
        0.2,
        0.3,
        0.4,
        0.6,
        0.99,
    ]


def test_an_item_whose_fix_is_done_or_whose_ids_are_gone_is_skipped(session):
    # the reader merged the two tracks by hand: the missed join is done
    session.apply(session.plan_merge([1.0, 2.0], into=1.0))
    assert 0.4 not in [i.item.confidence for i in session.review_issues(1.0)]
    session.undo()
    # the reader deleted id 3: its track item names an id that is gone
    session.apply(session.plan_delete_ids([3.0]))
    assert 0.2 not in [i.item.confidence for i in session.review_issues(1.0)]
    session.undo()
    # the reader cut id 0 at the join by hand
    session.apply(session.plan_cut(0.0, t_of(100)))
    assert 0.3 not in [i.item.confidence for i in session.review_issues(1.0)]


def test_a_delete_unassigns_the_items_span_only(tmp_path):
    part = item("track", 0.2, [5], t_of(50), 900.0, t_of(50), t_of(99))
    ts, _ = TrackSet.load(write_folder(tmp_path / "r", review=[part]))
    (issue,) = ts.review_issues(1.0)
    assert issue.text.endswith("— Enter unassigns 5 from 00:02.5 to 00:05.0")
    ts.apply(issue.suggestion(ts))
    k = ts.idx[ts.rows_of(5.0)]
    assert len(k) == N_FRAMES - 50 and not ((k >= 50) & (k <= 99)).any()
    assert ts.review_issues(1.0) == []
    whole = item("track", 0.2, [3], t_of(20), 800.0, t_of(20), t_of(40))
    ts, _ = TrackSet.load(write_folder(tmp_path / "w", review=[whole]))
    (issue,) = ts.review_issues(1.0)
    assert issue.text.endswith("— Enter unassigns 3")
    assert issue.suggestion(ts).label.startswith("Delete id 3 (review")


# --------------------------------------------------------------- snippets


def write_run(folder, tracks, n_frames, review):
    """A snippet run: its own frames from 0, its own ids."""
    folder = write_folder(folder, tracks, review)
    np.save(folder / "times.npy", t_of(np.arange(n_frames)))
    return folder


def test_a_snippets_review_moves_into_session_frames_and_is_cut_back(tmp_path):
    g = FrameGrid(RATE, NFFT, STEP, 0, N_FRAMES)
    run = {
        0: [(k, 700.0) for k in range(60)],  # joined at run frame 5 and 30
        1: [(k, 750.0) for k in range(25, 35)],
        2: [(k, 760.0) for k in range(40, 50)],
        # joined across frames 9-11; the window starts at run frame 10
        3: [(k, 820.0) for k in range(9)] + [(k, 820.0) for k in range(12, 40)],
    }
    review = [
        item("join", 0.1, [0], t_of(5), 700.0, k=5),  # before the window
        item("join", 0.15, [3], t_of(12), 820.0, k=12),  # earlier side cut off
        item("join", 0.2, [0], t_of(30), 700.0, k=30),
        item("missed_join", 0.3, [1, 2], t_of(40), 760.0),
        item("track", 0.4, [0], t_of(0), 700.0, t_of(0), t_of(59)),
    ]
    snippet = load_snippet(write_run(tmp_path / "run", run, 60, review), g, 40)
    offset = 40 * STEP / RATE
    assert [it.k for it in snippet.review] == [45, 52, 70, None, None]
    assert snippet.review[3].t == pytest.approx(t_of(40) + offset)
    assert snippet.times[0] == pytest.approx(t_of(40))
    # Track visible keeps the window: frames 50-89 of the session
    cut = crop_snippet(snippet, 50, 90)
    kept = {it.confidence: it for it in cut.review}
    assert sorted(kept) == [0.2, 0.3, 0.4]
    assert kept[0.4].t0 == pytest.approx(t_of(50))  # the track's part in it
    assert kept[0.4].t1 == pytest.approx(t_of(89))


def test_accept_maps_the_snippets_items_to_session_ids_with_undo(session):
    before = session.review
    k0, k1 = 60, 90
    fund, idx, ident = [], [], []
    for k in range(k0, k1):  # id 0's fish, and a new one at 700 Hz
        fund += [500.0, 700.0]
        idx += [k, k]
        ident += [7.0, 8.0]
    review = (
        RV.parse_item(item("join", 0.05, [7], t_of(75), 500.0, k=75)),
        RV.parse_item(item("track", 0.15, [8], t_of(60), 700.0, t_of(60), t_of(89))),
    )
    snippet = Snippet(
        k0,
        k1,
        np.array(fund),
        np.array(idx, np.int64),
        np.array(ident),
        np.ones((len(fund), 2), np.float32),
        None,
        {},
        review=review,
        times=t_of(np.arange(k0, k1)),
    )
    plan = session.plan_replace_span(snippet)
    session.apply(plan)
    got = {it.confidence: it for it in session.review}
    # 7 continued id 0 at both edges, 8 is new: id 6
    assert got[0.05].ids == (0.0,) and got[0.15].ids == (6.0,)
    # the session's join at frame 100 lies outside the span and stays
    assert 0.3 in got and 0.6 in got
    assert session.review_changed()
    first = session.review_issues(1.0)[0]
    assert first.item.confidence == 0.05 and first.ids == (0.0,)
    session.undo()
    assert session.review == before and not session.review_changed()
    session.redo()
    assert {it.confidence for it in session.review} == set(got)


def test_accept_drops_the_sessions_joins_inside_the_span(session):
    snippet = Snippet(
        90,
        120,
        np.full(30, 500.0),
        np.arange(90, 120),
        np.zeros(30),
        np.ones((30, 2), np.float32),
        None,
        {},
        times=t_of(np.arange(90, 120)),
    )
    session.apply(session.plan_replace_span(snippet))
    confidences = sorted(it.confidence for it in session.review)
    # the join at frame 100 and the missed join at frame 110 were replaced
    assert confidences == [0.2, 0.6, 0.99]


# --------------------------------------------------------------- saving


def accept_with_review(ts):
    review = (RV.parse_item(item("track", 0.07, [7], t_of(60), 700.0)),)
    snippet = Snippet(
        60,
        70,
        np.full(10, 700.0),
        np.arange(60, 70),
        np.full(10, 7.0),
        np.ones((10, 2), np.float32),
        None,
        {},
        review=review,
        times=t_of(np.arange(60, 70)),
    )
    ts.apply(ts.plan_replace_span(snippet))


def test_review_items_round_trip_through_eodsorter_json(session, tmp_path):
    folder = tmp_path / "r"
    session.save(folder)
    sorter = json.loads((folder / model_io.SORTER_FILE).read_text())
    assert "review" not in sorter, "review.json still says it all"
    accept_with_review(session)
    session.save(folder)
    sorter = json.loads((folder / model_io.SORTER_FILE).read_text())
    assert len(sorter["review"]["items"]) == 6
    assert sorter["review"]["base_mtime"] == RV.review_mtime(folder)
    again, complaints = TrackSet.load(folder)
    assert complaints == [] and again.review == session.review
    # wavetracker tracked the recording again: its new review.json wins
    stamp = RV.review_mtime(folder) + 5.0
    os.utime(folder / RV.REVIEW_FILE, (stamp, stamp))
    again, complaints = TrackSet.load(folder)
    assert len(again.review) == 5 and "changed after the last save" in complaints[0]


def test_save_as_writes_the_sessions_items_where_there_is_no_review_json(
    session, tmp_path
):
    other = tmp_path / "other"
    session.save(other)
    assert not (other / RV.REVIEW_FILE).exists()
    again, complaints = TrackSet.load(other)
    assert complaints == [] and again.review == session.review


def test_clean_up_takes_the_items_and_undo_brings_them_back(session):
    before = session.review
    ident = np.array(session.ident)
    session.apply(session.plan_apply_ident(ident + 100, "Clean up", keep_review=False))
    assert session.review == () and session.review_issues(1.0) == []
    session.undo()
    assert session.review == before
    # resolving duplicates keeps the ids, and the items with them
    session.apply(session.plan_apply_ident(ident + 100, "Load another"))
    assert session.review == before


def test_the_autosave_brings_the_items_back(session, tmp_path):
    folder = tmp_path / "r"
    accept_with_review(session)
    changed = session.review
    session.write_autosave(folder)
    auto = model_io.read_autosave(folder)
    assert auto is not None and len(auto.review) == 6
    fresh, _ = TrackSet.load(folder)
    assert fresh.review != changed
    fresh.apply(fresh.plan_recover(auto))
    assert fresh.review == changed
