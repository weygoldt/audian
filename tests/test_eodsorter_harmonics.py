"""Harmonics tracked as fish (design 5.13): numpy only, fast."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from audian_plugins.eodsorter import harmonics as H  # noqa: E402
from audian_plugins.eodsorter import model as M  # noqa: E402

DT = 0.1365  # iriri's frame step [s]
BIN = 0.732  # iriri's frequency resolution [Hz]


def modulated(n, f0, seed=0, noise=0.02):
    """A fish: a slow wander plus fast modulations (chirp-like rises)."""
    rng = np.random.default_rng(seed)
    t = np.arange(n) * DT
    f = f0 + 0.8 * np.sin(2 * np.pi * t / 7.0) + np.cumsum(rng.normal(0, 0.03, n))
    return f + rng.normal(0, noise, n)


def session(tracks, n_frames=None):
    """Arrays for tracks given as ``(id, frames, freqs)``."""
    fund = np.concatenate([f for _, _, f in tracks])
    idx = np.concatenate([k for _, k, _ in tracks]).astype(np.int64)
    ident = np.concatenate([np.full(len(k), float(i)) for i, k, _ in tracks])
    n_frames = n_frames or int(idx.max()) + 1
    times = np.arange(n_frames) * DT
    return fund, idx, ident, times


def found(fund, idx, ident, times, ids=None, **rule):
    return H.find_harmonics(
        fund, idx, ident, times, BIN, ids=ids, rule=H.HarmonicRule(**rule)
    )


# ------------------------------------------------------------- the rule


def test_exact_second_and_third_harmonics_with_noise_are_found_with_their_h():
    n = 400  # 55 s: long enough for the co-modulation test
    k = np.arange(n)
    f1 = modulated(n, 300.0)
    rng = np.random.default_rng(5)
    tracks = [
        (1, k, f1),
        (2, k, 2 * f1 + rng.normal(0, 0.03, n)),
        (3, k, 3 * f1 + rng.normal(0, 0.03, n)),
        (4, k, modulated(n, 455.0, seed=9)),  # an unrelated fish
    ]
    out = {x.harmonic_id: x for x in found(*session(tracks))}
    assert set(out) == {2.0, 3.0}
    assert out[2.0].fundamental_id == 1.0 and out[2.0].h == 2
    assert out[3.0].fundamental_id == 1.0 and out[3.0].h == 3
    for x in out.values():
        assert x.freq_corr is not None and x.freq_corr > 0.9  # long overlap
        assert abs(x.offset_hz) < 0.05
        assert x.n_common == n


def test_a_different_fish_three_hertz_above_twice_f_is_not_a_harmonic():
    n = 400
    k = np.arange(n)
    f1 = modulated(n, 300.0)
    near = 2 * modulated(n, 300.0, seed=3) + 3.0  # its own modulation, +3 Hz
    copy = 2 * f1 + 3.0  # even moving in lockstep, 3 Hz is an offset
    for f2 in (near, copy):
        assert found(*session([(1, k, f1), (2, k, f2)])) == []


def test_lockstep_without_fast_comodulation_fails_a_long_overlap():
    """Interference lines: exact multiples, but flat -- wavetracker does not
    call them harmonics, and neither does a long overlap here."""
    n = 400
    k = np.arange(n)
    rng = np.random.default_rng(1)
    f1 = 400.0 + rng.normal(0, 0.01, n)
    f2 = 1200.0 + rng.normal(0, 0.01, n)
    assert found(*session([(1, k, f1), (2, k, f2)])) == []


def test_a_short_overlap_is_judged_by_the_offset_alone():
    n = 400
    f1 = modulated(n, 300.0)
    k = np.arange(n)
    short = np.arange(100, 122)  # 3 s, like a hand-added stroke
    rng = np.random.default_rng(2)
    f2 = 2 * f1[short] + rng.normal(0, 0.05, len(short))
    out = found(*session([(1, k, f1), (7, short, f2)]))
    assert len(out) == 1
    x = out[0]
    assert (x.harmonic_id, x.fundamental_id, x.h) == (7.0, 1.0, 2)
    assert x.freq_corr is None
    assert x.overlap_s < H.HarmonicRule().min_overlap
    # the offset tolerance scales with the frequency resolution and h
    assert found(*session([(1, k, f1), (7, short, f2 + 0.5)])) == []
    loose = H.find_harmonics(
        *session([(1, k, f1), (7, short, f2 + 0.5)]), 4 * BIN, rule=H.HarmonicRule()
    )
    assert len(loose) == 1


def test_too_few_common_frames_are_not_judged():
    n = 200
    f1 = modulated(n, 300.0)
    k = np.arange(n)
    for m, expect in ((4, 0), (5, 1)):
        short = np.arange(50, 50 + m)
        out = found(*session([(1, k, f1), (7, short, 2 * f1[short])]))
        assert len(out) == expect, m


def test_a_track_that_only_crosses_twice_f_is_not_a_harmonic():
    n = 100
    k = np.arange(n)
    f1 = np.full(n, 300.0)
    f2 = 590.0 + 20.0 * k / n  # passes 600 Hz half way
    assert found(*session([(1, k, f1), (2, k, f2)])) == []


def test_targeted_check_finds_the_relation_both_ways():
    n = 300
    k = np.arange(n)
    f1 = modulated(n, 250.0)
    tracks = [(1, k, f1), (2, k[:60], 3 * f1[:60]), (3, k, modulated(n, 777.0, 4))]
    arrays = session(tracks)
    as_harmonic = found(*arrays, ids=[2])
    as_fundamental = found(*arrays, ids=[1])
    for out in (as_harmonic, as_fundamental):
        assert [(x.harmonic_id, x.fundamental_id, x.h) for x in out] == [(2.0, 1.0, 3)]
    assert found(*arrays, ids=[3]) == []


def test_one_finding_per_harmonic_prefers_a_fundamental_that_is_no_harmonic():
    n = 300
    k = np.arange(n)
    f1 = modulated(n, 200.0)
    tracks = [(1, k, f1), (2, k, 2 * f1), (4, k, 4 * f1)]
    out = {x.harmonic_id: x for x in found(*session(tracks))}
    assert set(out) == {2.0, 4.0}
    assert out[4.0].fundamental_id == 1.0 and out[4.0].h == 4


def test_check_track_and_band_preview_see_a_candidate_not_in_the_session():
    n = 200
    k = np.arange(n)
    f1 = modulated(n, 300.0)
    fund, idx, ident, times = session([(1, k, f1)])
    rows = np.arange(len(fund))
    frames = np.arange(40, 70)
    out = H.check_track(
        frames, 2 * f1[frames], fund, idx, ident, times, BIN, rows, H.HarmonicRule()
    )
    assert len(out) == 1 and np.isnan(out[0].harmonic_id) and out[0].h == 2
    centre = 2 * f1[frames] + 0.8  # a hand-drawn stroke, a bit off
    hits = H.band_harmonics(frames, centre - 2, centre + 2, fund, idx, ident, rows)
    assert hits and hits[0].id == 1.0 and hits[0].h == 2 and hits[0].as_harmonic
    away = H.band_harmonics(frames, centre + 5, centre + 9, fund, idx, ident, rows)
    assert away == []
    # the other way round: the stroke is the fundamental of an existing id
    fund2, idx2, ident2, _ = session([(5, k, 3 * f1)])
    hits = H.band_harmonics(
        frames, f1[frames] - 1, f1[frames] + 1, fund2, idx2, ident2, rows
    )
    assert hits and hits[0].id == 5.0 and hits[0].h == 3 and not hits[0].as_harmonic


def test_highpass_matches_a_centred_running_median_with_gaps():
    rng = np.random.default_rng(3)
    k = np.sort(rng.choice(500, 300, replace=False))
    x = np.cumsum(rng.normal(0, 1, len(k)))
    got = H.highpass(k, x, 41)
    want = np.array(
        [x[i] - np.median(x[np.abs(k - k[i]) <= 20]) for i in range(len(k))]
    )
    assert np.allclose(got, want)


def test_the_mirrored_constants_are_wavetrackers():
    """`HarmonicRule` copies ComodulationConfig rather than importing it
    (pandas, 0.12 s); pin the copy."""
    comod = pytest.importorskip("wavetracker.comodulation")
    cfg = comod.ComodulationConfig()
    rule = H.HarmonicRule()
    assert rule.max_harmonic == cfg.max_harmonic
    assert rule.min_overlap == cfg.min_overlap
    assert rule.timescale == cfg.timescale
    assert rule.min_freq_corr == cfg.min_freq_corr


def test_ordinals():
    assert [H.ordinal(h) for h in (1, 2, 3, 4, 11, 12, 13, 21, 22)] == [
        "1st",
        "2nd",
        "3rd",
        "4th",
        "11th",
        "12th",
        "13th",
        "21st",
        "22nd",
    ]


def synthetic(n_ids=6000, rows=400_000, n_frames=7200, n_harm=30, seed=0):
    rng = np.random.default_rng(seed)
    lens = rng.integers(20, 2 * rows // n_ids - 20, n_ids)
    lens = (lens * rows / lens.sum()).astype(int) + 1
    tracks = []
    for i, n in enumerate(lens):
        k0 = int(rng.integers(0, n_frames - n))
        f = rng.uniform(100, 2000) + np.cumsum(rng.normal(0, 0.05, n))
        tracks.append((i, np.arange(k0, k0 + n), f))
    for j in range(n_harm):
        _, k, f = tracks[j]
        h = 2 + j % 3
        tracks.append((n_ids + j, k.copy(), h * f + rng.normal(0, 0.05, len(f))))
    return session(tracks, n_frames), {float(n_ids + j) for j in range(n_harm)}


def test_the_sweep_is_fast_on_400k_rows_and_6k_ids():
    (fund, idx, ident, times), planted = synthetic()
    t0 = time.perf_counter()
    out = H.find_harmonics(fund, idx, ident, times, BIN)
    took = time.perf_counter() - t0
    assert planted <= {x.harmonic_id for x in out}
    assert took < 2.0, f"sweep took {took:.2f} s (about 0.15 s expected)"


# ------------------------------------------------------------- the model


def trackset(tracks):
    fund, idx, ident, times = session(tracks)
    grid = M.FrameGrid(rate=48000.0, nfft=65536, step=6553, s0=0, n_frames=len(times))
    ts = M.TrackSet.from_arrays(
        fund,
        idx,
        ident,
        sign=np.ones((len(fund), 2), np.float32),
        times=grid.times(),
        grid=grid,
    )
    return ts


def test_the_model_checks_the_ids_an_add_creates_and_makes_an_issue():
    n = 200
    k = np.arange(n)
    f1 = modulated(n, 300.0)
    ts = trackset([(1, k, f1), (2, k, modulated(n, 431.0, 3))])
    assert ts.frequency_bin() == pytest.approx(48000 / 65536)
    frames = np.arange(80, 110)
    plan = ts.plan_add(frames, 2 * f1[frames])
    assert plan.kind == "add"
    new = float(plan.created[0])
    assert ts.grown_ids(plan).tolist() == [new]
    ts.apply(plan)
    out = ts.harmonics(ts.grown_ids(plan))
    assert [(x.harmonic_id, x.fundamental_id, x.h) for x in out] == [(new, 1.0, 2)]
    text = M.harmonic_text(out[0], {new})
    assert f"id {int(new)} looks like the 2nd harmonic of id 1" in text
    assert "Enter: unassign" in text
    assert M.harmonic_mark(out[0]) == "×2 of 1"
    issues = ts.issues(("harmonic",))
    assert len(issues) == 1 and issues[0].kind == "harmonic"
    assert issues[0].ids == (new, 1.0)
    ts.apply(issues[0].suggestion(ts))
    assert len(ts.rows_of(new)) == 0
    assert ts.issues(("harmonic",)) == []


def test_grown_ids_by_plan_kind():
    n = 100
    k = np.arange(n)
    ts = trackset([(1, k[:50], np.full(50, 300.0)), (2, k[50:], np.full(50, 301.0))])
    merge = ts.plan_merge([1, 2], into=1)
    assert ts.grown_ids(merge).tolist() == [1.0]
    rows = ts.rows_of(2)[:10]
    assert ts.grown_ids(ts.plan_assign(rows, 1)).tolist() == [1.0]
    new = ts.plan_new_id(rows)
    assert ts.grown_ids(new).tolist() == [float(new.created[0])]
    # edits that do not give rows to an id at the reader's request
    assert len(ts.grown_ids(ts.plan_cut(1, ts.times[20]))) == 0
    assert len(ts.grown_ids(ts.plan_swap_after(1, 2, ts.times[20]))) == 0
    assert len(ts.grown_ids(ts.plan_delete_ids([1]))) == 0
    assert len(ts.grown_ids(ts.plan_unassign(rows))) == 0


def test_accepting_a_snippet_checks_every_accepted_id():
    n = 200
    k = np.arange(n)
    f1 = modulated(n, 300.0)
    ts = trackset([(1, k, f1)])
    span = np.arange(60, 90)
    snippet = M.Snippet(
        k0=60,
        k1=90,
        fund=np.concatenate([f1[span] + 0.01, 3 * f1[span]]),
        idx=np.concatenate([span, span]),
        ident=np.concatenate([np.full(30, 0.0), np.full(30, 1.0)]),
        sign=np.ones((60, 2), np.float32),
        cplx=None,
    )
    plan = ts.plan_replace_span(snippet, stitch=True)
    assert plan.kind == "replace_span"
    ts.apply(plan)
    grown = ts.grown_ids(plan)
    assert len(grown) == 2
    out = ts.harmonics(grown)
    assert [(x.h, x.fundamental_id in grown, x.harmonic_id in grown) for x in out] == [
        (3, True, True)
    ]


def test_the_sweep_is_cached_per_revision():
    n = 200
    k = np.arange(n)
    f1 = modulated(n, 300.0)
    ts = trackset([(1, k, f1), (2, k[:30], 2 * f1[:30])])
    first = ts.harmonics()
    assert ts.harmonics() == first
    ts.apply(ts.plan_delete_ids([2]))
    assert ts.harmonics() == []
