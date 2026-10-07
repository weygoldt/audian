"""The EOD sorter's data model: plans, conflict rules, history, files.

Fast and pure: numpy only, no Qt, no `app` fixture (design 9.1).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from audian_plugins.eodsorter import model_io
from audian_plugins.eodsorter.model import (
    EditRejected,
    FrameGrid,
    History,
    ResultsError,
    Snippet,
    TrackSet,
    finish_interrupted_save,
    ident_variants,
    load_snippet,
    local_median_losers,
    read_autosave,
    step_size,
)

NAN = np.nan

# --------------------------------------------------------------------------
# frozen copies of wavetracker (stitching.resolve_duplicates, results.Results.load)


def wt_resolve_duplicates(fund_v, idx_v, ident_v, window: int = 30) -> np.ndarray:
    out = ident_v.copy()
    valid = np.nonzero(~np.isnan(out))[0]
    order = valid[np.lexsort((idx_v[valid], out[valid]))]
    key_id, key_idx = out[order], idx_v[order]
    dup = (np.diff(key_id) == 0) & (np.diff(key_idx) == 0)
    if not dup.any():
        return out
    starts = np.nonzero(dup & ~np.r_[False, dup[:-1]])[0]
    for s0 in starts:
        s1 = s0 + 1
        while s1 < len(dup) and dup[s1]:
            s1 += 1
        group = order[s0 : s1 + 1]
        i, frame = key_id[s0], key_idx[s0]
        m = (key_id == i) & (np.abs(key_idx - frame) <= window) & (key_idx != frame)
        ref = np.median(fund_v[order[m]]) if m.any() else np.median(fund_v[group])
        keep = group[np.argmin(np.abs(fund_v[group] - ref))]
        out[group[group != keep]] = np.nan
    return out


def wt_results_load(folder):
    folder = Path(folder)
    if not (folder / "fund_v.npy").exists():
        raise FileNotFoundError(f"No wavetracker results in {folder}")
    arrays = {
        name: np.load(folder / f"{name}.npy", allow_pickle=False)
        for name in ("fund_v", "idx_v", "sign_v", "times")
    }
    ident = folder / "ident_v.npy"
    arrays["ident_v"] = (
        np.load(ident) if ident.exists() else np.full(len(arrays["fund_v"]), np.nan)
    )
    cplx = folder / "cplx_v.npy"
    arrays["cplx_v"] = np.load(cplx) if cplx.exists() else None
    meta_file = folder / "wavetracker.json"
    arrays["meta"] = json.loads(meta_file.read_text()) if meta_file.exists() else {}
    return arrays


# --------------------------------------------------------------------------
# builders

RATE, NFFT, STEP = 1000.0, 100, 50  # frame_step 0.05 s


def grid_meta(n_frames, s0=0, max_dt=0.5, tol=2.5):
    return {
        "rate": RATE,
        "start": s0 / RATE,
        "frame_step": STEP / RATE,
        "freq_resolution": 1.0,
        "config": {
            "spectrogram": {"nfft": NFFT, "overlap_frac": 0.5},
            "tracking": {"max_dt": max_dt, "freq_tolerance": tol},
        },
    }


def grid_times(n_frames, s0=0):
    return (s0 + np.arange(n_frames) * STEP + NFFT / 2) / RATE


def build(tracks, unassigned=(), n_frames=100, c=2, cplx=True, meta=True):
    """tracks: {id: [(frame, freq), ...]} -> TrackSet; rows in the given order."""
    fund, idx, ident = [], [], []
    for i, pts in tracks.items():
        for k, f in pts:
            fund.append(f)
            idx.append(k)
            ident.append(i)
    for k, f in unassigned:
        fund.append(f)
        idx.append(k)
        ident.append(NAN)
    n = len(fund)
    rng = np.random.default_rng(1)
    sign = rng.random((n, c)).astype(np.float32)
    cp = (sign * np.exp(1j * rng.random((n, c)))).astype(np.complex64) if cplx else None
    return TrackSet.from_arrays(
        np.array(fund, float),
        np.array(idx, np.int64),
        np.array(ident, float),
        sign,
        grid_times(n_frames),
        cplx=cp,
        meta=grid_meta(n_frames) if meta else None,
    )


def small():
    """id 0: frames 0..9 at 100 Hz (rows 0-9); id 1: frames 5..9 at 103 Hz,
    10..14 at 100.1 Hz (rows 10-19); id 2: frames 20..29 at 300 Hz (rows
    20-29); unassigned rows 30 (frame 3, 200 Hz), 31 (frame 25, 301 Hz),
    32 (frame 3, 100.3 Hz)."""
    return build(
        {
            0: [(k, 100.0) for k in range(10)],
            1: [(k, 103.0) for k in range(5, 10)] + [(k, 100.1) for k in range(10, 15)],
            2: [(k, 300.0) for k in range(20, 30)],
        },
        unassigned=[(3, 200.0), (25, 301.0), (3, 100.3)],
    )


def synth(seed, n_ids=40, n_frames=600, c=3, cplx=True, noise=0.15):
    """Random tracks without duplicates per id and frame, plus unassigned rows."""
    rng = np.random.default_rng(seed)
    fund, idx, ident = [], [], []
    for i in range(n_ids):
        a = int(rng.integers(0, n_frames - 20))
        b = int(min(n_frames, a + rng.integers(10, 300)))
        ks = np.arange(a, b)
        ks = ks[rng.random(len(ks)) > 0.3]
        if not len(ks):
            ks = np.array([a])
        base = rng.uniform(300, 900)
        fund.append(base + np.cumsum(rng.normal(0, 0.05, len(ks))))
        idx.append(ks)
        ident.append(np.full(len(ks), float(i)))
    m = int(noise * sum(len(x) for x in idx))
    fund.append(rng.uniform(300, 900, m))
    idx.append(rng.integers(0, n_frames, m))
    ident.append(np.full(m, NAN))
    fund, idx, ident = map(np.concatenate, (fund, idx, ident))
    o = np.argsort(idx, kind="stable")  # wavetracker's row order
    fund, idx, ident = fund[o], idx[o].astype(np.int64), ident[o]
    n = len(fund)
    sign = rng.random((n, c)).astype(np.float32)
    cp = (sign * np.exp(1j * rng.random((n, c)))).astype(np.complex64) if cplx else None
    return TrackSet.from_arrays(
        fund, idx, ident, sign, grid_times(n_frames), cplx=cp, meta=grid_meta(n_frames)
    )


def snapshot(ts):
    return {
        "n": ts.n,
        "fund": ts.fund.tobytes(),
        "idx": ts.idx.tobytes(),
        "ident": ts.ident.tobytes(),
        "tracked": ts.tracked.tobytes(),
        "sign": ts.sign.tobytes(),
        "cplx": None if ts.cplx is None else ts.cplx.tobytes(),
        "labels": dict(ts.labels),
        "notes": dict(ts.notes),
    }


def assert_index(ts):
    """The incremental indexes equal a rebuild from scratch."""
    ident, idx = ts.ident, ts.idx
    ids = np.unique(ident[~np.isnan(ident)])
    np.testing.assert_array_equal(ts.ids(), ids)
    for i in ids:
        r = np.flatnonzero(ident == i)
        r = r[np.lexsort((r, idx[r]))]
        np.testing.assert_array_equal(ts.rows_of(i), r)
    np.testing.assert_array_equal(
        ts.rows_in_frames(0, len(ts.times)), np.argsort(idx, kind="stable")
    )
    st = ts.stats()
    assert len(st) == len(ids)
    for s in st:
        r = ts.rows_of(s["id"])
        f = ts.fund[r]
        assert s["n"] == len(r)
        assert s["k_first"] == idx[r[0]] and s["k_last"] == idx[r[-1]]
        assert s["f_min"] == f.min() and s["f_max"] == f.max()
        assert s["f_median"] == np.median(f)


def rows_new(plan):
    return plan.rows.tolist(), plan.new.tolist()


# --------------------------------------------------------------------------
# frame grid


@pytest.mark.parametrize(
    "nfft, overlap, step",
    [
        (65536, 0.9, 6553),
        (32768, 0.9, 3276),
        (1024, 0.5, 512),
        (4096, 0.75, 1024),
        (100, 0.99, 1),
        (10, 0.95, 1),
        (2048, 0.875, 256),
        (8192, 0.0, 8192),
        (3000, 1 / 3, 2000),
    ],
)
def test_step_equals_wavetrackers_step_size(nfft, overlap, step):
    assert step_size(nfft, overlap) == step
    assert FrameGrid.for_recording(48000, 10**7, nfft, overlap).step == step


def test_frame_and_sample_ranges_round_trip():
    g = FrameGrid(rate=RATE, nfft=NFFT, step=STEP, s0=30, n_frames=200)
    t = g.times()
    assert g.frame_range(t[10], t[20]) == (10, 21)
    assert g.frame_range(t[10] + 1e-9, t[20] - 1e-9) == (11, 20)
    assert g.frame_range(-5, 1e9) == (0, 200)
    assert g.frame_range(t[50], t[40]) == (0, 0)
    for k0, k1 in [(0, 1), (3, 17), (150, 200)]:
        s0, s1 = g.sample_range(k0, k1)
        assert s0 == 30 + k0 * STEP and s1 == 30 + (k1 - 1) * STEP + NFFT
        assert g.frame_range(t[k0], t[k1 - 1]) == (k0, k1)
    g2 = FrameGrid.for_recording(RATE, 1000, NFFT, 0.5)
    assert g2.n_frames == (1000 - NFFT) // STEP + 1
    assert g2.sample_range(0, g2.n_frames)[1] <= 1000


def test_from_results_accepts_wavetracker_grid_and_rejects_irregular_times():
    t = grid_times(100, s0=200)
    g = FrameGrid.from_results(grid_meta(100, s0=200), t)
    assert g == FrameGrid(RATE, NFFT, STEP, 200, 100)
    bad = t.copy()
    bad[40] += 1e-3
    assert FrameGrid.from_results(grid_meta(100, s0=200), bad) is None
    assert FrameGrid.from_results({}, t) is None


# --------------------------------------------------------------------------
# the local-median rule


@pytest.mark.parametrize("seed", range(12))
def test_local_median_equals_wavetrackers_resolve_duplicates(seed):
    rng = np.random.default_rng(seed)
    n = int(rng.integers(5, 400))
    ident = rng.integers(0, 5, n).astype(float)
    ident[rng.random(n) < 0.1] = NAN
    idx = rng.integers(0, int(rng.integers(3, 120)), n)
    fund = np.round(rng.uniform(400, 410, n), int(rng.integers(0, 3)))  # ties too
    expect = wt_resolve_duplicates(fund, idx, ident, window=30)
    got = ident.copy()
    got[local_median_losers(fund, idx, ident)] = NAN
    np.testing.assert_array_equal(got, expect)


# --------------------------------------------------------------------------
# plans on a small hand-built set


def test_merge_keeps_point_closer_to_local_median_for_every_pick_order():
    ts = small()
    p = ts.plan_merge([0, 1], into=0)
    rows, new = rows_new(p)
    assert rows == list(range(10, 20))
    assert np.isnan(new[:5]).all() and new[5:] == [0.0] * 5
    assert p.dropped.tolist() == list(range(10, 15))
    assert "Merge 1 into 0" in p.label and "5 conflicts" in p.label
    p2 = ts.plan_merge([1, 0], into=1)
    assert p2.dropped.tolist() == list(range(10, 15))
    kept = np.setdiff1d(np.arange(20), p2.dropped)
    ts.apply(p2)
    ts.check_invariant()
    assert (ts.ident[kept] == 1).all() and np.isnan(ts.ident[10:15]).all()


def test_assign_displaces_targets_point_and_keeps_the_better_of_two_selected():
    ts = small()
    p = ts.plan_assign([30], target=0)
    assert rows_new(p)[0] == [3, 30]
    assert np.isnan(p.new[0]) and p.new[1] == 0
    assert p.dropped.tolist() == [3]
    p = ts.plan_assign([30, 32], target=0)
    assert rows_new(p)[0] == [3, 32]
    assert np.isnan(p.new[0]) and p.new[1] == 0
    assert p.dropped.tolist() == [3, 30]
    ts.apply(p)
    ts.check_invariant()
    assert np.isnan(ts.ident[30])


def test_assign_of_targets_own_point_with_a_rival_in_its_frame():
    ts = small()
    with pytest.raises(EditRejected, match="already belong"):
        ts.plan_assign([3], target=0)
    with pytest.raises(EditRejected, match="already belong"):
        ts.plan_assign([3, 30], target=0)  # 3 wins; 30 keeps NaN
    # the target's own selected point loses to a better rival: unassigned
    pts = [(k, 100.0) for k in range(10)]
    pts[3] = (3, 104.0)
    ts = build({0: pts}, unassigned=[(3, 100.2)])
    p = ts.plan_assign([3, 10], target=0)
    assert p.rows.tolist() == [3, 10]
    assert np.isnan(p.new[0]) and p.new[1] == 0 and p.dropped.tolist() == [3]
    ts.apply(p)
    ts.check_invariant()


def test_new_id_with_two_selected_points_in_one_frame():
    ts = small()
    assert ts.next_id == 3
    p = ts.plan_new_id([30, 31, 32])
    assert rows_new(p) == ([30, 31], [3.0, 3.0])
    assert p.dropped.tolist() == [32] and p.created == (3,)
    ts.apply(p)
    ts.check_invariant()
    assert np.isnan(ts.ident[32]) and ts.next_id == 4


def test_cut_both_variants_and_empty_sides():
    ts = small()
    t5 = ts.times[5]
    p = ts.plan_cut(0, t5)
    assert rows_new(p) == (list(range(5, 10)), [3.0] * 5)
    p = ts.plan_cut(0, t5, new_part="before")
    assert rows_new(p) == (list(range(0, 5)), [3.0] * 5)
    with pytest.raises(EditRejected, match="empty before"):
        ts.plan_cut(0, ts.times[0])
    with pytest.raises(EditRejected, match="empty after"):
        ts.plan_cut(0, ts.times[9] + 1e-6)


def test_swap_after():
    ts = small()
    p = ts.plan_swap_after(0, 1, ts.times[7])
    rows, new = rows_new(p)
    assert rows == [7, 8, 9] + list(range(12, 20))
    assert new == [1.0] * 3 + [0.0] * 8
    ts.apply(p)
    ts.check_invariant()
    with pytest.raises(EditRejected):
        ts.plan_swap_after(2, 2, 0.0)


def test_add_skips_occupied_frames_and_picks_one_candidate_per_frame():
    ts = small()
    p = ts.plan_add([8, 9, 10, 10], [100, 100, 100.05, 107], target=0)
    a = p.append
    assert a.idx.tolist() == [10] and a.fund.tolist() == [100.05]
    assert a.ident.tolist() == [0.0] and np.isnan(a.tracked).all()
    assert np.isnan(a.sign).all() and np.isnan(a.cplx).all()
    assert a.sign.shape == (1, 2)
    assert "skipped" in p.label
    ch = ts.apply(p)
    assert ch.appended == 1 and ts.n == 34 and ts.ident[33] == 0
    ts.check_invariant()
    with pytest.raises(EditRejected, match="every frame"):
        ts.plan_add([1, 2], [100, 100], target=0)
    p = ts.plan_add([50, 51], [700, 701])
    assert p.created == (3,) and p.append.ident.tolist() == [3.0, 3.0]


def test_unassign_and_delete_ids():
    ts = small()
    assert rows_new(ts.plan_unassign([0, 30]))[0] == [0]
    with pytest.raises(EditRejected):
        ts.plan_unassign([30, 31])
    p = ts.plan_delete_ids([2])
    assert p.rows.tolist() == list(range(20, 30)) and np.isnan(p.new).all()
    p = ts.plan_delete_ids([0])  # id 0
    assert p.rows.tolist() == list(range(10))


def test_rejections():
    ts = small()
    with pytest.raises(EditRejected, match="nothing to merge"):
        ts.plan_merge([0], into=0)
    with pytest.raises(EditRejected, match="not one of"):
        ts.plan_merge([0, 1], into=2)
    with pytest.raises(EditRejected, match="no points"):
        ts.plan_merge([0, 7], into=0)
    for f in (ts.plan_unassign, ts.plan_new_id):
        with pytest.raises(EditRejected, match="nothing selected"):
            f([])
    with pytest.raises(EditRejected, match="nothing selected"):
        ts.plan_assign([], 0)
    p = ts.plan_merge([0, 1], into=0)
    ts.apply(ts.plan_delete_ids([2]))
    with pytest.raises(EditRejected, match="changed since"):
        ts.apply(p)


def test_id_zero_and_all_nan_session():
    ts = build({}, unassigned=[(1, 100.0), (2, 100.5), (3, 101.0)])
    assert ts.next_id == 0 and len(ts.ids()) == 0
    ts.apply(ts.plan_new_id([0, 1]))
    assert ts.ident[:2].tolist() == [0.0, 0.0] and ts.next_id == 1
    ts.apply(ts.plan_assign([2], 0))
    assert ts.rows_of(0).tolist() == [0, 1, 2]
    ts.apply(ts.plan_cut(0, ts.times[2]))
    assert ts.ident.tolist() == [0.0, 1.0, 1.0]
    ts.apply(ts.plan_merge([1, 0], into=0))
    ts.apply(ts.plan_delete_ids([0]))
    assert np.isnan(ts.ident).all()


def test_undone_ids_are_never_handed_out_again():
    ts = small()
    ts.apply(ts.plan_new_id([30]))
    assert ts.next_id == 4
    ts.undo()
    assert ts.next_id == 4
    assert ts.plan_new_id([30]).created == (4,)
    ts.redo()
    assert ts.ident[30] == 3


def test_revert_whole_and_partial():
    ts = small()
    ts.apply(ts.plan_assign([30], 0))  # displaces row 3
    ts.apply(ts.plan_merge([0, 1], into=0))
    p = ts.plan_revert([3])  # back to 0, which now has row 30 at frame 3
    assert rows_new(p)[0] == [3, 30] and p.dropped.tolist() == [30]
    ts.apply(p)
    ts.check_invariant()
    ts.apply(ts.plan_revert())
    np.testing.assert_array_equal(ts.ident, ts.tracked)
    with pytest.raises(EditRejected):
        ts.plan_revert()


def test_labels_and_notes_are_undoable():
    ts = small()
    ts.apply(ts.plan_set_label(0, "female A"))
    ts.apply(ts.plan_set_note(0, "check 12:00"))
    assert ts.labels == {0: "female A"} and ts.notes == {0: "check 12:00"}
    with pytest.raises(EditRejected):
        ts.plan_set_label(0, "female A")
    ch = ts.undo()
    assert ch.ids.tolist() == [0.0] and ts.notes == {}
    ts.undo()
    assert ts.labels == {}
    ts.redo()
    assert ts.labels == {0: "female A"}


def test_apply_ident_resolves_duplicates_and_bumps_next_id():
    ts = small()
    ident = np.array(ts.ident)
    ident[30] = 0  # duplicate of row 3 at frame 3
    ident[31] = 9
    p = ts.plan_apply_ident(ident, "Cleanup")
    assert 30 in p.dropped and p.created == (9,)
    ts.apply(p)
    ts.check_invariant()
    assert ts.next_id == 10 and ts.ident[31] == 9
    with pytest.raises(EditRejected):
        ts.plan_apply_ident(np.full(ts.n, 0.5), "bad")


def test_change_notifications():
    ts = small()
    ch = ts.apply(ts.plan_merge([0, 1], into=0))
    assert ch.ids.tolist() == [0.0, 1.0]
    assert ch.frames == (5, 15) and ch.unassigned and ch.appended == 0
    assert ch.revision == ts.revision == 1
    ch = ts.apply(ts.plan_add([40], [500.0]))
    assert ch.appended == 1 and ch.frames == (40, 41) and not ch.unassigned
    ch = ts.undo()
    assert ch.appended == -1 and ts.n == 33 and ts.revision == 3


def test_public_arrays_are_read_only():
    ts = small()
    for a in (ts.fund, ts.idx, ts.ident, ts.tracked, ts.sign, ts.cplx, ts.times):
        with pytest.raises(ValueError):
            a[0] = 1
    with pytest.raises(ValueError):
        ts.rows_of(0)[0] = 5


def test_loaded_duplicates_are_left_alone_and_reported(tmp_path):
    ts = build({0: [(1, 100.0), (1, 100.2), (2, 100.0)], 1: [(5, 300.0)]})
    ts.check_invariant()  # input duplicates are not violations
    issues = ts.issues(kinds=("crossing",))
    assert any("duplicate" in i.text for i in issues)
    ts.apply(ts.plan_merge([0, 1], into=0))  # resolves them
    assert len(ts.rows_of(0)) == 3
    ts.apply(ts.plan_revert())  # the tracker's own duplicates come back: allowed
    ts.check_invariant()
    assert len(ts.rows_of(0)) == 3


# --------------------------------------------------------------------------
# queries


def test_rows_in_frames_stats_and_issues():
    ts = small()
    assert ts.rows_in_frames(3, 4).tolist() == [3, 30, 32]
    assert ts.rows_in_frames(0, 0).tolist() == []
    assert_index(ts)
    st = ts.stats([1])
    assert st["n"][0] == 10 and st["f_median"][0] == np.median([103] * 5 + [100.1] * 5)
    ts2 = build(
        {
            0: [(k, 500.0) for k in range(0, 20)],
            1: [(k, 500.5) for k in range(25, 50)],  # join 0 -> 1 (gap 0.3 s)
            2: [(k, 800.0) for k in range(0, 50) if not 10 <= k < 30],  # gap 1 s
            3: [(k, 800.5) for k in range(10, 15)],  # short, crosses nothing
            4: [(k, 900.0) for k in range(0, 40)],
            5: [(k, 900.4) for k in range(5, 40)],  # crossing with 4
        }
    )
    iss = ts2.issues(min_points=10, gap_break_s=0.5)
    joins = [i for i in iss if i.kind == "join"]
    assert any(i.ids == (0.0, 1.0) for i in joins)
    j = next(i for i in joins if i.ids == (0.0, 1.0))
    ts2.apply(j.suggestion(ts2))
    assert len(ts2.rows_of(0)) == 45
    assert any(i.kind == "gap" and i.ids == (2.0,) for i in iss)
    short = [i for i in iss if i.kind == "short"]
    assert [i.ids for i in short] == [(3.0,)]
    assert any(i.kind == "crossing" and i.ids == (4.0, 5.0) for i in iss)
    ts_ = [i.t for i in iss]
    assert ts_ == sorted(ts_)


# --------------------------------------------------------------------------
# history


def test_history_depth_and_byte_caps_drop_oldest():
    ts = synth(1)
    ts.history = History(max_depth=5)
    ids = ts.ids()
    for i in ids[:8]:
        ts.apply(ts.plan_delete_ids([i]))
    h = ts.history
    assert len(h.entries) == 5 and h.position == 5 and h.dropped == 3
    assert h.dropped_until is not None
    while ts.undo():
        pass
    assert h.position == 0
    assert np.isnan(
        ts.ident[np.isin(ts.tracked, ids[:3])]
    ).all()  # dropped stay applied
    ts.history = History(max_bytes=1)
    for i in ids[8:11]:
        ts.apply(ts.plan_delete_ids([i]))
    assert len(ts.history.entries) == 1


def test_new_edit_after_undo_drops_redo_branch():
    ts = small()
    ts.apply(ts.plan_delete_ids([2]))
    ts.apply(ts.plan_delete_ids([1]))
    ts.undo()
    assert ts.history.can_redo()
    ts.apply(ts.plan_delete_ids([0]))
    assert not ts.history.can_redo() and len(ts.history.entries) == 2


def test_jump_equals_stepwise_undo_redo():
    ts = synth(2)
    states = [snapshot(ts)]
    rng = np.random.default_rng(0)
    for _ in range(12):
        try:
            ids = ts.ids()
            a, b = rng.choice(ids, 2, replace=False)
            ts.apply(ts.plan_merge([a, b], into=a))
        except EditRejected:
            continue
        states.append(snapshot(ts))
    n = ts.history.position
    for target in rng.integers(0, n + 1, 30):
        ch = ts.jump(int(target))
        assert ts.history.position == target
        assert snapshot(ts) == states[target]
        if ch is not None:
            assert ch.revision == ts.revision
        assert_index(ts)


def test_dirty_state_across_save_undo_redo():
    ts = small()
    assert not ts.is_dirty()
    ts.apply(ts.plan_delete_ids([2]))
    assert ts.is_dirty()
    ts.mark_saved()
    assert not ts.is_dirty()
    ts.undo()
    assert ts.is_dirty()
    ts.redo()
    assert not ts.is_dirty()
    ts.undo()
    ts.apply(ts.plan_delete_ids([1]))  # saved state is gone from the history
    assert ts.is_dirty()
    ts.undo()
    assert ts.is_dirty()


# --------------------------------------------------------------------------
# random edits


def random_plan(ts, rng, n_frames):
    n = ts.n
    ids = ts.ids()
    if len(ids) < 4:
        return ts.plan_revert()
    op = rng.integers(0, 13)
    some_rows = rng.choice(n, int(rng.integers(1, 30)), replace=False)
    pick = lambda k: rng.choice(ids, k, replace=False)  # noqa: E731
    if op == 0:
        return ts.plan_unassign(some_rows)
    if op == 1:
        return ts.plan_delete_ids(pick(1))
    if op == 2:
        return ts.plan_new_id(some_rows)
    if op == 3:
        return ts.plan_assign(some_rows, pick(1)[0])
    if op == 4:
        k = int(rng.integers(2, 4))
        sel = pick(min(k, len(ids)))
        return ts.plan_merge(sel, into=sel[0])
    if op == 5:
        return ts.plan_cut(pick(1)[0], rng.uniform(ts.times[0], ts.times[-1]))
    if op == 6:
        a, b = pick(2)
        return ts.plan_swap_after(a, b, rng.uniform(ts.times[0], ts.times[-1]))
    if op == 7:
        k = rng.integers(0, n_frames - 20)
        m = int(rng.integers(1, 15))
        frames = k + rng.integers(0, 20, m)
        target = pick(1)[0] if rng.random() < 0.5 else None
        return ts.plan_add(frames, rng.uniform(300, 900, m), target=target)
    if op == 8:
        return ts.plan_revert(some_rows)
    if op == 9:
        return ts.plan_set_label(pick(1)[0], f"fish {rng.integers(0, 5)}")
    if op == 10:
        ident = np.array(ts.ident)
        r = rng.choice(n, 50, replace=False)
        ident[r] = rng.choice(np.r_[ids, NAN, ts.next_id + 2], 50)
        return ts.plan_apply_ident(ident, "Cleanup")
    if op == 11:
        k0 = int(rng.integers(0, n_frames - 40))
        k1 = k0 + int(rng.integers(5, 40))
        m = int(rng.integers(0, 40))
        sk = rng.integers(k0, k1, m)
        si = rng.integers(0, 4, m).astype(float)
        # snippet ids are unique per frame, as wavetracker's
        key = np.unique(np.c_[si, sk], axis=0)
        si, sk = key[:, 0], key[:, 1].astype(np.int64)
        m = len(sk)
        snip = Snippet(
            k0,
            k1,
            rng.uniform(300, 900, m),
            sk,
            si,
            rng.random((m, ts.n_channels)).astype(np.float32),
            None,
            {},
        )
        return ts.plan_replace_span(snip, stitch=bool(rng.random() < 0.7))
    return ts.plan_revert()


@pytest.mark.parametrize("seed", [0, 1])
def test_random_edits_keep_invariant_and_undo_exactly(seed):
    rng = np.random.default_rng(100 + seed)
    n_frames = 600
    ts = synth(seed, n_ids=40, n_frames=n_frames)
    ts.history = History(max_depth=10_000)
    assert 4000 < ts.n < 9000
    start = snapshot(ts)
    n_tracked = ts.n
    fixed = {
        k: getattr(ts, k)[:n_tracked].tobytes() for k in ("fund", "idx", "sign", "cplx")
    }
    applied = 0
    for _ in range(300):
        try:
            plan = random_plan(ts, rng, n_frames)
        except EditRejected:
            continue
        ts.apply(plan)
        applied += 1
        ts.check_invariant()
        for k in ("fund", "idx", "ident", "tracked", "sign", "cplx"):
            assert len(getattr(ts, k)) == ts.n
        for k, v in fixed.items():
            assert getattr(ts, k)[:n_tracked].tobytes() == v
    assert applied > 150
    assert_index(ts)
    end = snapshot(ts)
    while ts.undo():
        pass
    assert snapshot(ts) == start
    assert_index(ts)
    while ts.redo():
        pass
    assert snapshot(ts) == end
    assert_index(ts)


# --------------------------------------------------------------------------
# snippets


def write_run(folder, fund, idx, ident, n_frames, c=2, rate=RATE, cplx=True):
    folder.mkdir(parents=True)
    n = len(fund)
    np.save(folder / "fund_v.npy", np.asarray(fund, float))
    np.save(folder / "idx_v.npy", np.asarray(idx, np.int64))
    np.save(folder / "ident_v.npy", np.asarray(ident, float))
    np.save(folder / "sign_v.npy", np.ones((n, c), np.float32))
    if cplx:
        np.save(folder / "cplx_v.npy", np.ones((n, c), np.complex64))
    np.save(folder / "times.npy", (np.arange(n_frames) * STEP + NFFT / 2) / rate)
    (folder / "wavetracker.json").write_text(json.dumps({"rate": rate, "x": 1}))


def stitch_session():
    """A before k0=40 at 500 Hz, B after k1=60 at 700 Hz, C on both sides at
    600 Hz (with rows inside the span), D only after k1 at 800 Hz and also
    before k0 at 850 Hz."""
    return build(
        {
            0: [(k, 500.0) for k in range(30, 40)],  # A
            1: [(k, 700.0) for k in range(60, 70)],  # B
            2: [(k, 600.0) for k in range(30, 70)],  # C, through the span
            3: [(k, 850.0) for k in range(30, 40)]
            + [(k, 800.0) for k in range(60, 70)],
        },
        n_frames=100,
    )


def test_load_snippet_offsets_and_checks_times(tmp_path):
    g = FrameGrid(RATE, NFFT, STEP, 0, 100)
    write_run(tmp_path / "run", [500, 501], [0, 3], [0, 0], n_frames=20)
    s = load_snippet(tmp_path / "run", g, 40)
    assert (s.k0, s.k1) == (40, 60) and s.idx.tolist() == [40, 43]
    assert s.meta["x"] == 1
    write_run(tmp_path / "bad", [500], [0], [0], n_frames=20, rate=999.0)
    with pytest.raises(ResultsError, match="line up"):
        load_snippet(tmp_path / "bad", g, 40)
    with pytest.raises(ResultsError):
        load_snippet(tmp_path / "run", g, 90)


def snip(tracks, k0=40, k1=60, c=2):
    fund, idx, ident = [], [], []
    for i, pts in tracks.items():
        for k, f in pts:
            fund.append(f)
            idx.append(k)
            ident.append(i)
    n = len(fund)
    return Snippet(
        k0,
        k1,
        np.array(fund, float),
        np.array(idx, np.int64),
        np.array(ident, float),
        np.ones((n, c), np.float32),
        None,
        {},
    )


def test_replace_span_unassigns_span_and_appends_with_fresh_ids():
    ts = stitch_session()
    n0 = ts.n
    p = ts.plan_replace_span(snip({7: [(k, 650.0) for k in range(45, 55)]}))
    ch = ts.apply(p)
    assert ch.appended == 10 and ts.n == n0 + 10
    span_old = ts.rows_in_frames(40, 60)
    span_old = span_old[span_old < n0]
    assert np.isnan(ts.ident[span_old]).all()
    assert (ts.ident[n0:] == 4).all() and p.created == (4,)
    assert np.isnan(ts.cplx[n0:]).all()  # session has cplx, snippet not
    ts.check_invariant()
    ts.undo()
    assert ts.n == n0


def test_replace_span_edge_stitching_cases():
    # left only: starts at k0 near A's 500 Hz
    ts = stitch_session()
    ts.apply(ts.plan_replace_span(snip({0: [(k, 500.5) for k in range(40, 50)]})))
    assert (ts.ident[-10:] == 0).all()
    # right only: ends near k1 at B's 700 Hz
    ts = stitch_session()
    ts.apply(ts.plan_replace_span(snip({0: [(k, 700.4) for k in range(50, 60)]})))
    assert (ts.ident[-10:] == 1).all()
    # both edges to the same id (C)
    ts = stitch_session()
    ts.apply(ts.plan_replace_span(snip({0: [(k, 600.2) for k in range(40, 60)]})))
    assert (ts.ident[-20:] == 2).all()
    ts.check_invariant()
    # both edges, different ids: A on the left, B on the right -> B's rows
    # after k1 become A
    ts = stitch_session()
    b_rows = ts.rows_of(1)
    pts = [(k, 500.0 + 10 * (k - 40)) for k in range(40, 60)]  # 500 -> 690 Hz
    pts[-1] = (59, 700.0)
    p = ts.plan_replace_span(snip({0: pts}))
    ts.apply(p)
    assert (ts.ident[-20:] == 0).all() and (ts.ident[b_rows] == 0).all()
    assert len(ts.rows_of(1)) == 0
    ts.check_invariant()
    # ambiguous: left matches D (850 Hz), right matches B, but D has rows
    # after k1 -> right edge left unjoined
    ts = stitch_session()
    pts = [(k, 850.0 - 7.5 * (k - 40)) for k in range(40, 60)]
    pts[-1] = (59, 700.0)
    p = ts.plan_replace_span(snip({0: pts}))
    assert "ambiguous" in p.label
    ts.apply(p)
    assert (ts.ident[-20:] == 3).all() and (ts.ident[b_rows] == 1).all()
    ts.check_invariant()
    # stitch=False: fresh id
    ts = stitch_session()
    p = ts.plan_replace_span(
        snip({0: [(k, 500.5) for k in range(40, 50)]}), stitch=False
    )
    assert p.created == (4,)


def test_snippet_creates_a_session_from_nothing(tmp_path):
    g = FrameGrid(RATE, NFFT, STEP, 0, 100)
    ts = TrackSet.empty(g)
    assert ts.n == 0 and ts.n_channels is None and ts.next_id == 0
    s = snip({3: [(k, 600.0) for k in range(40, 50)]}, c=4)
    s = Snippet(**{**s.__dict__, "meta": grid_meta(20)})
    ts.apply(ts.plan_replace_span(s))
    assert ts.n == 10 and ts.n_channels == 4 and (ts.ident == 0).all()
    ts.save(tmp_path / "new", recording_paths=["/data/rec.wav"])
    meta = json.loads((tmp_path / "new" / "wavetracker.json").read_text())
    assert meta["input"] == "/data/rec.wav" and meta["rate"] == RATE
    back, complaints = TrackSet.load(tmp_path / "new")
    assert back.grid == g and back.n == 10
    ts.undo()
    assert ts.n == 0 and ts.n_channels is None


# --------------------------------------------------------------------------
# saving and loading


def write_results(folder, ts):
    """A wavetracker-like results directory (Results.save layout)."""
    folder.mkdir(parents=True, exist_ok=True)
    np.save(folder / "fund_v.npy", np.asarray(ts.fund))
    np.save(folder / "idx_v.npy", np.asarray(ts.idx))
    np.save(folder / "sign_v.npy", np.asarray(ts.sign))
    np.save(folder / "ident_v.npy", np.asarray(ts.ident))
    np.save(folder / "times.npy", np.asarray(ts.times))
    if ts.cplx is not None:
        np.save(folder / "cplx_v.npy", np.asarray(ts.cplx))
    (folder / "wavetracker.json").write_text(json.dumps(ts.meta))
    return folder


def test_save_load_round_trip_and_results_load(tmp_path):
    d = write_results(tmp_path / "r", synth(3))
    meta_before = (d / "wavetracker.json").read_bytes()
    ts, complaints = TrackSet.load(d)
    assert complaints == [] and ts.grid is not None and not ts.is_dirty()
    tracked0 = np.array(ts.tracked)
    ids = ts.ids()
    ts.apply(ts.plan_merge(ids[:2], into=ids[0]))
    ts.apply(ts.plan_set_label(ids[0], "female A"))
    ts.apply(ts.plan_add([5, 6], [123.0, 123.5]))
    ts.save(d, recording_paths=["/x/rec.wav"])
    assert not ts.is_dirty()
    assert (d / "wavetracker.json").read_bytes() == meta_before
    assert not list(d.glob(".*tmp*")) and not (d / "eodsorter.commit.json").exists()
    back, _ = TrackSet.load(d)
    for k in ("fund", "idx", "ident", "tracked", "sign", "cplx"):
        np.testing.assert_array_equal(getattr(back, k), getattr(ts, k))
    assert back.labels == {int(ids[0]): "female A"}
    assert back.next_id == ts.next_id
    np.testing.assert_array_equal(back.tracked[: len(tracked0)], tracked0)
    r = wt_results_load(d)
    assert len(r["fund_v"]) == len(r["idx_v"]) == len(r["ident_v"]) == len(r["sign_v"])
    assert len(r["cplx_v"]) == ts.n
    np.testing.assert_array_equal(r["ident_v"], ts.ident)
    sorter = json.loads((d / "eodsorter.json").read_text())
    assert sorter["n_tracked_rows"] == len(tracked0) and len(sorter["history"]) == 3


def test_tracked_backup_written_once_and_extended(tmp_path):
    d = write_results(tmp_path / "r", small())
    ts, _ = TrackSet.load(d)
    ts.apply(ts.plan_delete_ids([2]))
    ts.save(d)
    tracked_file = d / "ident_v.tracked.npy"
    first = np.load(tracked_file)
    np.testing.assert_array_equal(first, small().ident)
    ts.apply(ts.plan_merge([0, 1], into=0))
    ts.save(d)
    assert np.load(tracked_file).tobytes() == first.tobytes()
    fund0 = np.load(d / "fund_v.npy")
    ts.apply(ts.plan_add([60, 61], [400, 401]))
    ts.save(d)
    t2 = np.load(tracked_file)
    assert len(t2) == ts.n and t2[: len(first)].tobytes() == first.tobytes()
    assert np.isnan(t2[len(first) :]).all()
    assert np.load(d / "fund_v.npy")[: len(fund0)].tobytes() == fund0.tobytes()
    ts2, _ = TrackSet.load(d)
    assert ts2.n_tracked == len(first)
    # next_id survives a reload, also for ids handed out and undone
    ts2.apply(ts2.plan_new_id([30]))
    ts2.undo()
    ts2.save(d)
    ts3, _ = TrackSet.load(d)
    assert ts3.next_id == ts2.next_id


def test_disk_dtypes_are_kept(tmp_path):
    ts = small()
    d = write_results(tmp_path / "r", ts)
    np.save(d / "fund_v.npy", np.asarray(ts.fund, np.float32))
    np.save(d / "idx_v.npy", np.asarray(ts.idx, np.int32))
    before = np.load(d / "fund_v.npy")
    ts, _ = TrackSet.load(d)
    ts.apply(ts.plan_add([60], [400.25]))
    ts.save(d)
    after = np.load(d / "fund_v.npy")
    assert after.dtype == np.float32 and np.load(d / "idx_v.npy").dtype == np.int32
    assert after[: len(before)].tobytes() == before.tobytes()


def test_interrupted_save_is_completed(tmp_path, monkeypatch):
    d = write_results(tmp_path / "r", small())
    ts, _ = TrackSet.load(d)
    ts.apply(ts.plan_add([60, 61], [400, 401]))
    real = os.replace
    calls = []

    def crash(src, dst):
        calls.append(dst)
        if len(calls) == 3:  # commit file, first pair, then die
            raise KeyboardInterrupt
        return real(src, dst)

    monkeypatch.setattr(model_io.os, "replace", crash)
    with pytest.raises(KeyboardInterrupt):
        ts.save(d)
    monkeypatch.setattr(model_io.os, "replace", real)
    assert (d / "eodsorter.commit.json").exists()
    back, complaints = TrackSet.load(d)  # runs finish_interrupted_save
    assert "completed an interrupted save" in complaints
    assert back.n == ts.n
    np.testing.assert_array_equal(back.ident, ts.ident)
    assert not (d / "eodsorter.commit.json").exists()
    assert not [p for p in d.iterdir() if ".tmp" in p.name]
    assert finish_interrupted_save(d) is False


def test_stray_temporaries_are_removed(tmp_path):
    d = write_results(tmp_path / "r", small())
    (d / ".ident_v.npy.tmp999").write_bytes(b"junk")
    (d / ".unrelated.tmp1").write_bytes(b"keep")
    assert finish_interrupted_save(d) is False
    assert not (d / ".ident_v.npy.tmp999").exists() and (d / ".unrelated.tmp1").exists()


def test_autosave_write_read_recover(tmp_path):
    d = write_results(tmp_path / "r", small())
    ts, _ = TrackSet.load(d)
    ts.apply(ts.plan_merge([0, 1], into=0))
    ts.apply(ts.plan_add([60, 61], [400, 401]))
    ts.apply(ts.plan_set_label(0, "A"))
    ts.write_autosave(d)
    a = read_autosave(d)
    assert a is not None and a.n_base == 33 and len(a.append) == 2
    assert a.labels == {0: "A"} and a.next_id == ts.next_id and len(a.history) == 3
    fresh, _ = TrackSet.load(d)
    before = snapshot(fresh)
    fresh.apply(fresh.plan_recover(a))
    assert snapshot(fresh) == snapshot(ts)
    assert fresh.next_id == ts.next_id and fresh.is_dirty()
    fresh.undo()
    assert snapshot(fresh) == before
    ts.save(d)
    assert read_autosave(d) is None


def test_ident_variants_load_as_first_history_entry(tmp_path):
    ts0 = small()
    d = write_results(tmp_path / "r", ts0)
    variant = np.array(ts0.ident)
    variant[ts0.ident == 1] = 0
    np.save(d / "ident_v_cleaned_n2.npy", variant)
    np.save(d / "ident_v.tracked.npy", np.asarray(ts0.ident))
    assert ident_variants(d) == ["ident_v.npy", "ident_v_cleaned_n2.npy"]
    ts, _ = TrackSet.load(d, ident_file="ident_v_cleaned_n2.npy")
    assert len(ts.history.entries) == 1 and ts.is_dirty()
    ts.check_invariant()
    ts.undo()
    np.testing.assert_array_equal(ts.ident, ts0.ident)


def test_load_rejects_structural_problems(tmp_path):
    good = small()
    with pytest.raises(ResultsError, match="missing"):
        TrackSet.load(tmp_path)
    d = write_results(tmp_path / "a", good)
    np.save(d / "idx_v.npy", np.asarray(good.idx)[:-1])
    with pytest.raises(ResultsError):
        TrackSet.load(d)
    d = write_results(tmp_path / "b", good)
    idx = np.array(good.idx)
    idx[0] = 1000
    np.save(d / "idx_v.npy", idx)
    with pytest.raises(ResultsError, match="outside"):
        TrackSet.load(d)
    d = write_results(tmp_path / "c", good)
    ident = np.array(good.ident)
    ident[0] = 0.5
    np.save(d / "ident_v.npy", ident)
    with pytest.raises(ResultsError, match="non-integer"):
        TrackSet.load(d)
    d = write_results(tmp_path / "d", good)
    np.save(d / "cplx_v.npy", np.asarray(good.cplx)[:-1])
    with pytest.raises(ResultsError):
        TrackSet.load(d)
    d = write_results(tmp_path / "e", good)
    (d / "ident_v.npy").unlink()
    ts, _ = TrackSet.load(d)
    assert np.isnan(ts.ident).all() and ts.next_id == 0


def test_load_complaints(tmp_path):
    ts = small()
    d = write_results(tmp_path / "r", ts)
    meta = dict(ts.meta, files="take.wav")
    (d / "wavetracker.json").write_text(json.dumps(meta))
    _, complaints = TrackSet.load(d, recording_paths=["/x/other.wav"], duration=1.0)
    text = " ".join(complaints)
    assert "take.wav" in text and "beyond" in text
    t = np.array(ts.times)
    t[5] += 0.01
    np.save(d / "times.npy", t)
    back, complaints = TrackSet.load(d)
    assert back.grid is None and any("regular" in c for c in complaints)


def test_saved_dir_loads_with_real_wavetracker(tmp_path):
    results = pytest.importorskip("wavetracker.results")
    d = write_results(tmp_path / "r", synth(4))
    ts, _ = TrackSet.load(d)
    ts.apply(ts.plan_add([5], [123.0]))
    ts.save(d)
    r = results.Results.load(d)
    np.testing.assert_array_equal(r.ident_v, ts.ident)
    assert len(r.cplx_v) == len(r.fund_v) == ts.n


# --------------------------------------------------------------------------
# import graph


@pytest.mark.parametrize("module", ["model", "model_io", "geometry"])
def test_pure_modules_import_no_qt(module):
    src = Path(__file__).resolve().parent.parent / "src"
    if not (src / "audian_plugins" / "eodsorter" / f"{module}.py").exists():
        pytest.skip(f"{module}.py not there yet")
    code = (
        f"import sys; import audian_plugins.eodsorter.{module}; "
        "bad = [m for m in sys.modules if m.startswith(('PySide6', 'pyqtgraph', 'audian.'))"
        " or m == 'audian' or m.startswith('wavetracker')]; "
        "print(bad); sys.exit(1 if bad else 0)"
    )
    env = dict(os.environ, PYTHONPATH=str(src))
    res = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env
    )
    assert res.returncode == 0, res.stdout + res.stderr


# --------------------------------------------------------------------------
# regressions from the correctness review


def test_save_rewrites_rows_when_an_add_is_replaced_by_one_of_equal_length(tmp_path):
    """Review 1: undo an Add and Add as many other points; the row count on
    disk matches, but every appended row differs."""
    d = write_results(tmp_path / "r", small())
    ts, _ = TrackSet.load(d)
    ts.apply(ts.plan_add([50, 51, 52], [600.0, 601.0, 602.0]))
    ts.save(d)
    ts.undo()
    ts.apply(ts.plan_add([70, 71, 72], [900.0, 901.0, 902.0]))
    ts.save(d)
    r = wt_results_load(d)
    np.testing.assert_array_equal(r["fund_v"], ts.fund)
    np.testing.assert_array_equal(r["idx_v"], ts.idx)
    np.testing.assert_array_equal(r["sign_v"], ts.sign)
    np.testing.assert_array_equal(r["ident_v"], ts.ident)
    back, _ = TrackSet.load(d)
    np.testing.assert_array_equal(back.tracked, ts.tracked)
    # and an ordinary save after it still leaves the arrays alone
    before = (d / "fund_v.npy").stat().st_mtime_ns
    ts.apply(ts.plan_delete_ids([2]))
    ts.save(d)
    assert (d / "fund_v.npy").stat().st_mtime_ns == before


def test_save_as_into_other_results_writes_everything(tmp_path):
    """Review 1: Save as into a directory holding results of the same length
    must not keep its data arrays, times or metadata."""
    a = write_results(tmp_path / "a", small())
    other = small()
    b = write_results(tmp_path / "b", other)
    np.save(b / "fund_v.npy", np.asarray(other.fund) + 1000.0)
    np.save(b / "times.npy", np.asarray(other.times) + 99.0)
    (b / "wavetracker.json").write_text(json.dumps({"rate": 1.0}))
    ts, _ = TrackSet.load(a)
    ts.apply(ts.plan_merge([0, 1], into=0))
    ts.save(b)
    r = wt_results_load(b)
    np.testing.assert_array_equal(r["fund_v"], ts.fund)
    np.testing.assert_array_equal(np.load(b / "times.npy"), ts.times)
    assert json.loads((b / "wavetracker.json").read_text())["rate"] == RATE
    np.testing.assert_array_equal(np.load(b / "ident_v.tracked.npy"), ts.tracked)


def test_saved_float64_power_keeps_its_precision(tmp_path):
    """Review 14: rows the tracker found are written back as they were."""
    ts0 = small()
    d = write_results(tmp_path / "r", ts0)
    fine = np.asarray(ts0.sign, np.float64) + 1e-9
    np.save(d / "sign_v.npy", fine)
    ts, _ = TrackSet.load(d)
    ts.apply(ts.plan_add([60, 61], [400.0, 401.0]))
    ts.save(d)
    after = np.load(d / "sign_v.npy")
    assert after.dtype == np.float64 and len(after) == ts.n
    assert after[: len(fine)].tobytes() == fine.tobytes()


def test_autosave_after_undoing_saved_rows_recovers(tmp_path):
    """Review 6: saved appended rows undone, then other edits: recovery
    brings the edits back and the undone rows come back unassigned."""
    d = write_results(tmp_path / "r", small())
    ts, _ = TrackSet.load(d)
    ts.apply(ts.plan_add([50, 51, 52], [600.0, 601.0, 602.0]))
    ts.save(d)
    ts.undo()
    ts.apply(ts.plan_new_id([0, 1, 2, 3]))
    ts.write_autosave(d)
    fresh, _ = TrackSet.load(d)
    fresh.apply(fresh.plan_recover(read_autosave(d)))
    n = ts.n
    np.testing.assert_array_equal(fresh.ident[:n], ts.ident)
    assert np.isnan(fresh.ident[n:]).all() and fresh.n == n + 3


def test_autosave_knows_when_ident_file_changed_after_its_base(tmp_path):
    """Review 4: an autosave older than ident_v.npy is stale."""
    d = write_results(tmp_path / "r", small())
    ts, _ = TrackSet.load(d)
    ts.apply(ts.plan_new_id([0, 1]))
    ts.write_autosave(d)
    assert not read_autosave(d).is_stale(d)
    st = (d / "ident_v.npy").stat()
    np.save(d / "ident_v.npy", np.full(ts.n, 7.0))
    os.utime(d / "ident_v.npy", (st.st_atime + 5, st.st_mtime + 5))
    assert read_autosave(d).is_stale(d)
    # an autosave written after a save is based on the saved file
    ts2, _ = TrackSet.load(d)
    ts2.apply(ts2.plan_new_id([0, 1]))
    ts2.save(d)
    ts2.apply(ts2.plan_new_id([2, 3]))
    ts2.write_autosave(d)
    assert not read_autosave(d).is_stale(d)


def test_autosave_carries_the_grid_to_rebuild_a_folderless_session(tmp_path):
    """Review 5: a session made from snippets only is recoverable."""
    g = FrameGrid(float(RATE), NFFT, STEP, 0, 100)
    ts = TrackSet.empty(g)
    ts.apply(ts.plan_replace_span(snip({0: [(k, 500.0) for k in range(40, 50)]})))
    ts.write_autosave(tmp_path, recording="/x/rec.wav")
    a = read_autosave(tmp_path)
    assert a.recording == "/x/rec.wav" and a.n_base == 0 and len(a.append) == 10
    rate, nfft, step, s0, n_frames = a.grid
    back = TrackSet.empty(FrameGrid(rate, int(nfft), int(step), int(s0), int(n_frames)))
    back.apply(back.plan_recover(a))
    np.testing.assert_array_equal(back.fund, ts.fund)
    np.testing.assert_array_equal(back.ident, ts.ident)


def test_snippet_with_duplicates_per_frame_is_resolved_not_refused():
    """Review 9: keep the detection nearer the local median."""
    ts = small()
    sn = snip({0: [(k, 700.0) for k in range(40, 50)] + [(45, 760.0)]})
    plan = ts.plan_replace_span(sn)
    ts.apply(plan)
    ts.check_invariant()
    new = ts.ident[ts.n - 11 :]
    fund = ts.fund[ts.n - 11 :]
    assert np.isnan(new[fund == 760.0]).all()
    assert "1 duplicate detection unassigned" in ts.history.entries[-1].label


def test_add_skips_frames_without_a_frequency():
    """Review 8: the peak search found nothing in some frames."""
    ts = small()
    plan = ts.plan_add([60, 61, 62], [400.0, np.nan, 402.0])
    ts.apply(plan)
    assert ts.n == 35 and sorted(ts.idx[-2:]) == [60, 62]
    with pytest.raises(EditRejected):
        ts.plan_add([70, 71], [np.nan, np.nan])
