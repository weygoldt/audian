"""Tests for :mod:`audian.windowing`, the array-in/array-out view slicer.

Runs under pytest, and also standalone::

    .venv/bin/python -m pytest tests/test_windowing.py -q

No I/O and no Qt: every test builds its own arrays.  That is the point of the
module -- the two algorithms that are easy to get subtly wrong, windowing
spans and merging them, are checked against brute-force references on
thousands of random windows here, where a failure names the algorithm instead
of showing up later as an annotation drawn in the wrong place.

The style throughout is property against reference, not example against
constant: the naive version of each function is three lines and obviously
right but O(rows) per redraw, so it makes an excellent oracle for the fast
one.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from audian.windowing import (  # noqa: E402
    EMPTY,
    RUN_NO_TIME,
    RUN_OUT_OF_ORDER,
    RUN_UNCLOSED,
    RUN_UNOPENED,
    SPAN_NO_TIME,
    _pair_general,
    count_columns,
    merge_spans,
    pair_runs,
    step_envelope,
    window_points,
    window_spans,
    window_steps,
)

#: The real file this module was built against: 607.104 s = frames / rate.
RECORDING_S = 607.104


# --------------------------------------------------------------- references


def brute_points(times, t0, t1):
    """Every time in the closed window, the obvious way."""
    return times[(times >= t0) & (times <= t1)]


def brute_spans(starts, ends, t0, t1):
    """The intersection predicate itself, with no sortedness exploited."""
    return np.flatnonzero((starts < t1) & (ends > t0))


def merge_ref(s, e, tol):
    """A Python interval merge: walk the spans, fuse anything within `tol`.

    ``max(e[i], s[i])`` is the reach of one span: an inverted row -- which
    ``session.py`` keeps as written rather than swapping -- covers no time,
    so it reaches its own start and no further, and never drags a group's
    reach back behind the group's start.
    """
    groups = []
    cur_s, cur_e, cur_first = s[0], max(e[0], s[0]), 0
    for i in range(1, len(s)):
        reach = max(e[i], s[i])
        if s[i] - cur_e > tol:
            groups.append((cur_s, cur_e, cur_first))
            cur_s, cur_e, cur_first = s[i], reach, i
        else:
            cur_e = max(cur_e, reach)
    groups.append((cur_s, cur_e, cur_first))
    return [(gs, max(ge, gs + tol), gf) for gs, ge, gf in groups]


def held_value(times, values, t):
    """The value the source says is in force at `t`."""
    return values[int(np.searchsorted(times, t, side="right")) - 1]


def read_back(x, y, t):
    """The value a reader takes off the drawn polyline at `t`."""
    return y[int(np.searchsorted(x, t, side="right")) - 1]


# ----------------------------------------------------------------- fixtures


def disjoint_spans(rng, n=400, t_end=RECORDING_S):
    """Trial-shaped spans: ordered, non-overlapping, short against the file."""
    starts = np.sort(rng.uniform(0.0, t_end, n))
    gaps = np.diff(np.append(starts, t_end))
    ends = starts + np.minimum(rng.uniform(0.05, 3.0, n), gaps * 0.9)
    return starts, ends


def nested_spans(rng, n=400, t_end=RECORDING_S):
    """Spans that nest and overlap: durations up to a third of the file, so a
    span that started 200 s ago is routinely still running."""
    starts = np.sort(rng.uniform(0.0, t_end, n))
    ends = starts + rng.uniform(0.01, t_end / 3.0, n)
    return starts, ends


def inverted_spans(rng, n=400, t_end=RECORDING_S, bad=0.25):
    """Trial-shaped spans with a quarter of the rows WRITTEN BACKWARDS.

    ``session.py`` treats ``end < start`` as a writer defect, warns about it
    and leaves the row as written -- swapping the edges would invent a
    bracket over a stretch in which nothing was running -- so `merge_spans`
    is handed inverted spans on real files.  Neither well-behaved fixture can
    produce one: both build ``end = start + positive width``.  That is why an
    inverted span reached the drawing path as a pair of overlapping bars and
    no property test noticed.
    """
    starts, ends = disjoint_spans(rng, n, t_end)
    flip = rng.random(n) < bad
    # Backwards by up to 30 s, not by a hair: nothing bounds how wrong a
    # writer bug is, and a small inversion never opens the gap that the
    # one-pixel floor then reaches across.
    ends[flip] = starts[flip] - rng.uniform(0.001, 30.0, int(flip.sum()))
    assert np.any(ends < starts)  # the fixture really does invert
    return starts, ends


def random_windows(rng, k, t_end=RECORDING_S):
    """Windows from a 1 ms zoom-in to the whole file, some hanging off both
    edges, because the browser reaches past the ends while panning."""
    t0 = rng.uniform(-30.0, t_end + 30.0, k)
    width = 10.0 ** rng.uniform(-3.0, np.log10(t_end * 1.5), k)
    return t0, t0 + width


# ------------------------------------------------------------ window_points


def test_a_window_holds_exactly_the_times_inside_it():
    rng = np.random.default_rng(11)
    times = np.sort(rng.uniform(0.0, RECORDING_S, 5000))
    for t0, t1 in zip(*random_windows(rng, 2000)):
        got, total = window_points(times, t0, t1)
        want = brute_points(times, t0, t1)
        assert np.array_equal(got, want)
        assert total == want.size


def test_the_count_is_the_true_count_not_the_number_drawn():
    times = np.linspace(0.0, 1.0, 10_000)
    drawn, total = window_points(times, 0.0, 1.0, pixels=100)
    assert total == 10_000
    # one per pixel column, plus the event sitting exactly on the closed right
    # edge, which buckets one past the grid and keeps its own line
    assert drawn.size <= 101


def test_decimation_keeps_the_first_time_in_each_pixel_column():
    times = np.linspace(0.0, 1.0, 1001)
    drawn, _ = window_points(times, 0.0, 1.0, pixels=10)
    columns = (drawn * 10).astype(int)
    assert np.array_equal(np.unique(columns), columns)  # one per column, in order
    assert set(drawn.tolist()) <= set(times.tolist())  # nothing invented
    # the first of each column, not an arbitrary member of it
    assert drawn[0] == 0.0 and drawn[1] == pytest.approx(0.1)


def test_no_pixel_budget_means_no_decimation():
    times = np.linspace(0.0, 1.0, 10_000)
    drawn, total = window_points(times, 0.0, 1.0, pixels=0)
    assert drawn.size == total == 10_000


def test_a_window_past_the_end_of_the_events_holds_nothing():
    times = np.array([1.0, 2.0, 3.0])
    drawn, total = window_points(times, 10.0, 20.0)
    assert drawn.size == 0 and total == 0


# ----------------------------------------------------------- count_columns


def test_column_counts_account_for_every_event_in_the_window():
    rng = np.random.default_rng(12)
    times = np.sort(rng.uniform(0.0, RECORDING_S, 20_000))
    for t0, t1 in zip(*random_windows(rng, 500)):
        cols, counts, total = count_columns(times, t0, t1, 1400)
        assert counts.sum() == total
        assert total == brute_points(times, t0, t1).size


def test_columns_are_ascending_and_inside_the_pixel_grid():
    rng = np.random.default_rng(13)
    times = np.sort(rng.uniform(0.0, RECORDING_S, 20_000))
    for t0, t1 in zip(*random_windows(rng, 500)):
        cols, counts, _ = count_columns(times, t0, t1, 640)
        assert np.all(np.diff(cols) > 0)
        assert cols.size == 0 or (cols[0] >= 0 and cols[-1] < 640)
        assert np.all(counts > 0)


def test_column_counts_equal_a_bucket_by_bucket_reference():
    rng = np.random.default_rng(14)
    times = np.sort(rng.uniform(0.0, 100.0, 3000))
    for t0, t1 in zip(*random_windows(rng, 200, t_end=100.0)):
        pixels = 200
        cols, counts, _ = count_columns(times, t0, t1, pixels)
        ref = Counter(
            min(max(int((t - t0) * pixels / (t1 - t0)), 0), pixels - 1)
            for t in times
            if t0 <= t <= t1
        )
        assert dict(zip(cols.tolist(), counts.tolist())) == dict(ref)


def test_an_event_on_the_right_edge_stays_in_the_last_column():
    times = np.array([0.0, 0.5, 1.0])
    cols, counts, total = count_columns(times, 0.0, 1.0, 10)
    assert total == 3
    assert cols[-1] == 9 and counts.sum() == 3


def test_without_a_pixel_grid_the_total_is_still_reported():
    times = np.array([1.0, 2.0, 3.0])
    cols, counts, total = count_columns(times, 0.0, 5.0, 0)
    assert cols.size == counts.size == 0
    assert total == 3


# ------------------------------------------------------------ window_spans


def test_a_span_intersects_the_view_iff_it_starts_before_the_end_and_ends_after_the_start():
    rng = np.random.default_rng(21)
    starts, ends = disjoint_spans(rng)
    max_end = np.maximum.accumulate(ends)
    for t0, t1 in zip(*random_windows(rng, 6000)):
        s, e, rows, total = window_spans(starts, ends, max_end, t0, t1)
        want = brute_spans(starts, ends, t0, t1)
        assert np.array_equal(rows, want)
        assert np.array_equal(s, starts[want])
        assert np.array_equal(e, ends[want])
        assert total == want.size


def test_nested_spans_are_windowed_exactly_when_the_layer_says_it_is_not_disjoint():
    rng = np.random.default_rng(22)
    starts, ends = nested_spans(rng)
    assert not np.all(starts[1:] >= ends[:-1])  # the fixture really does nest
    max_end = np.maximum.accumulate(ends)
    for t0, t1 in zip(*random_windows(rng, 6000)):
        s, e, rows, total = window_spans(starts, ends, max_end, t0, t1, disjoint=False)
        want = brute_spans(starts, ends, t0, t1)
        assert np.array_equal(rows, want)
        assert np.array_equal(s, starts[want])
        assert total == want.size


def test_a_false_disjoint_promise_shows_up_as_a_span_that_already_ended():
    # The documented failure mode, locked in so nobody "optimises" the flag
    # away: the fast slice is a superset for overlapping spans, and only the
    # mask that disjoint=False buys makes it exact.
    starts = np.array([0.0, 1.0, 2.0])
    ends = np.array([100.0, 1.5, 3.0])
    max_end = np.maximum.accumulate(ends)
    _, e, rows, _ = window_spans(starts, ends, max_end, 50.0, 60.0, disjoint=True)
    assert 1 in rows.tolist() and float(e[rows.tolist().index(1)]) == 1.5
    _, _, honest, _ = window_spans(starts, ends, max_end, 50.0, 60.0, disjoint=False)
    assert honest.tolist() == brute_spans(starts, ends, 50.0, 60.0).tolist() == [0]


def test_a_view_deep_inside_one_long_span_still_finds_it():
    starts = np.array([0.0])
    ends = np.array([600.0])
    s, e, rows, total = window_spans(starts, ends, ends.copy(), 300.0, 301.0)
    assert total == 1 and rows.tolist() == [0]
    assert s[0] == 0.0 and e[0] == 600.0


def test_a_span_that_only_touches_the_edge_is_outside_the_view():
    starts = np.array([0.0, 10.0])
    ends = np.array([5.0, 15.0])
    max_end = np.maximum.accumulate(ends)
    assert window_spans(starts, ends, max_end, 5.0, 10.0)[3] == 0
    assert window_spans(starts, ends, max_end, 4.9, 10.0)[3] == 1
    assert window_spans(starts, ends, max_end, 5.0, 10.1)[3] == 1


def test_an_inverted_span_is_found_at_every_zoom_that_contains_its_bar():
    """It was drawn zoomed out and gone zoomed in, which is the worst of both.

    ``session._build_trials`` keeps a trial whose ``recording_ended_s``
    precedes its ``recording_time_s`` as written -- swapping the two would
    invent a bracket over a stretch in which nothing ran -- and `merge_spans`
    puts its bar at ``[start, start + one pixel]``.  So `max_end` has to carry
    the REACH, ``maximum.accumulate(maximum(ends, starts))``, or the slice
    disagrees with the draw: with start 5.0 and end 2.0 the view ``[0, 8]``
    drew a bar and the view ``[4.5, 5.5]`` -- the one a reader zooms to in
    order to look at it -- came back empty.
    """
    starts = np.array([5.0])
    ends = np.array([2.0])
    reach = np.maximum.accumulate(np.maximum(ends, starts))
    # Not a view starting exactly at 5.0: both edges of `window_spans` are
    # exclusive, and a span reaching exactly t0 is outside the view whether it
    # is inverted, zero-length or ordinary.  That rule is not what broke here.
    for t0, t1 in ((0.0, 8.0), (4.5, 5.5), (4.9, 5.2), (4.999, 6.0)):
        assert window_spans(starts, ends, reach, t0, t1)[3] == 1, (t0, t1)
        bars = merge_spans(
            *window_spans(starts, ends, reach, t0, t1)[:2], (t1 - t0) / 1000.0
        )
        assert bars[3] == 1 and float(bars[0][0]) == 5.0
    for t0, t1 in ((1.0, 3.0), (5.5, 6.0), (0.0, 5.0)):
        assert window_spans(starts, ends, reach, t0, t1)[3] == 0, (t0, t1)


def test_an_inverted_span_does_not_reach_back_over_an_overlapping_neighbour():
    """The reach fix must not turn a nested layer's mask into a superset.

    ``disjoint=False`` filters on the same reach the slice uses, so a span
    that really has finished is still dropped and only the inverted one --
    whose bar is at its own start -- survives.
    """
    starts = np.array([0.0, 1.0, 2.0])
    ends = np.array([100.0, 0.5, 3.0])  # row 1 is inverted, row 0 nests it
    reach = np.maximum.accumulate(np.maximum(ends, starts))
    _, _, rows, _ = window_spans(starts, ends, reach, 50.0, 60.0, disjoint=False)
    assert rows.tolist() == [0]
    _, _, rows, _ = window_spans(starts, ends, reach, 0.9, 1.1, disjoint=False)
    assert rows.tolist() == [0, 1]


def test_an_empty_layer_windows_to_nothing():
    s, e, rows, total = window_spans(EMPTY, EMPTY, EMPTY, 0.0, 10.0)
    assert s.size == e.size == rows.size == 0 and total == 0


def test_a_nan_end_is_refused_instead_of_silently_widening_the_window():
    # np.maximum propagates NaN, so one NaN end makes every later max_end NaN
    # and searchsorted then puts i0 at 0: the window comes back a superset
    # that draws finished spans across the view, which looks like data.
    starts = np.array([1.0, 2.0, 3.0, 4.0])
    ends = np.array([1.5, np.nan, 3.5, 4.5])
    max_end = np.maximum.accumulate(ends)
    with pytest.raises(ValueError, match="NaN"):
        window_spans(starts, ends, max_end, 0.0, 10.0)


def test_a_nan_start_is_refused_wherever_a_sorted_array_can_hold_it():
    # Ascending order is already a precondition and numpy orders NaN last, so
    # a NaN start is the last element -- which is exactly where the O(1)
    # check looks.
    starts = np.array([1.0, 2.0, np.nan])
    ends = np.array([1.5, 2.5, 3.0])
    with pytest.raises(ValueError, match="NaN"):
        window_spans(starts, ends, np.maximum.accumulate(ends), 0.0, 10.0)


def test_a_degenerate_view_does_not_excuse_a_nan_span():
    # The t1 <= t0 early-out must not be a way past the contract: a caller
    # that only ever asks for empty views would never learn its arrays are
    # broken.
    starts = np.array([1.0, 2.0])
    ends = np.array([np.nan, 2.5])
    with pytest.raises(ValueError, match="NaN"):
        window_spans(starts, ends, np.maximum.accumulate(ends), 5.0, 1.0)


def test_the_refusal_names_the_invariant_that_broke():
    # It is raised from a per-redraw path, where the message is all a reader
    # gets.
    assert "NaN" in SPAN_NO_TIME and "max_end" in SPAN_NO_TIME


def test_a_span_still_running_at_infinity_is_windowed_not_refused():
    # +inf is not a missing time, it is "no end yet": it orders correctly, it
    # keeps max_end non-decreasing, and the predicate start < t1 AND end > t0
    # answers for it.  Refusing it would refuse an open-right run.
    starts = np.array([1.0, 2.0, 4.0])
    ends = np.array([1.5, np.inf, 4.5])
    max_end = np.maximum.accumulate(ends)
    _, _, rows, total = window_spans(starts, ends, max_end, 4.2, 4.4)
    assert rows.tolist() == brute_spans(starts, ends, 4.2, 4.4).tolist() == [1, 2]
    assert total == 2


def test_a_reversed_window_holds_nothing():
    starts = np.array([0.0])
    ends = np.array([600.0])
    assert window_spans(starts, ends, ends.copy(), 5.0, 1.0)[3] == 0
    assert window_spans(starts, ends, ends.copy(), 5.0, 5.0)[3] == 0


# ------------------------------------------------------------- merge_spans


@pytest.mark.parametrize("tol", [0.0, 0.05, 0.5, 5.0, 50.0])
def test_merging_matches_a_python_reference_at_every_tolerance(tol):
    rng = np.random.default_rng(31)
    for fixture in (disjoint_spans, nested_spans, inverted_spans):
        s, e = fixture(rng, n=200)
        out_s, out_e, first, total = merge_spans(s, e, tol)
        ref = merge_ref(s, e, tol)
        assert total == s.size
        assert np.allclose(out_s, [g[0] for g in ref])
        assert np.allclose(out_e, [g[1] for g in ref])
        assert first.tolist() == [g[2] for g in ref]


@pytest.mark.parametrize("tol", [0.0, 0.05, 0.5, 5.0, 50.0])
def test_merged_bars_are_sorted_and_never_overlap(tol):
    rng = np.random.default_rng(32)
    for fixture in (disjoint_spans, nested_spans, inverted_spans):
        s, e = fixture(rng, n=300)
        out_s, out_e, _, _ = merge_spans(s, e, tol)
        assert np.all(np.diff(out_s) > 0)
        assert np.all(out_e >= out_s)
        assert np.all(out_s[1:] > out_e[:-1])


@pytest.mark.parametrize("tol", [0.0, 0.05, 0.5, 5.0, 50.0])
def test_every_input_span_is_covered_by_a_merged_bar(tol):
    # The reason merging replaced decimation: a region where a trial was
    # running may never lose its ink, whatever the zoom.
    rng = np.random.default_rng(33)
    for fixture in (nested_spans, inverted_spans):
        s, e = fixture(rng, n=300)
        out_s, out_e, _, _ = merge_spans(s, e, tol)
        owner = np.searchsorted(out_s, s, side="right") - 1
        assert np.all(owner >= 0)
        # An inverted span covers no time, so what has to survive is its
        # start; `reach` is what the bar must cover for either shape.
        reach = np.maximum(e, s)
        assert np.all(out_s[owner] <= s) and np.all(out_e[owner] >= reach)


def test_the_reported_total_is_the_pre_merge_count():
    # The badge says 36 trials even when the view draws 12 bars.
    rng = np.random.default_rng(34)
    s, e = disjoint_spans(rng, n=36, t_end=RECORDING_S)
    for tol in (0.0, 0.5, 5.0, 500.0):
        out_s, _, _, total = merge_spans(s, e, tol)
        assert total == 36
        assert out_s.size <= 36


def test_first_names_the_first_source_span_of_each_group():
    s = np.array([0.0, 1.0, 10.0, 10.5])
    e = np.array([0.5, 2.0, 10.2, 11.0])
    out_s, _, first, _ = merge_spans(s, e, 1.0)
    assert first.tolist() == [0, 2]
    assert np.array_equal(out_s, s[first])


def test_a_zero_length_span_is_widened_to_one_pixel_and_still_cannot_overlap():
    # 200 instants 0.001 s apart against a 0.05 s pixel: every bar is floored
    # to the pixel width, and the floor must not reach into the next bar.
    s = np.arange(200) * 0.001
    e = s.copy()
    out_s, out_e, _, total = merge_spans(s, e, 0.05)
    assert total == 200
    assert np.all(out_e - out_s >= 0.05 - 1e-12)
    assert np.all(out_s[1:] > out_e[:-1])


def test_nested_spans_merge_into_the_span_that_contains_them():
    s = np.array([0.0, 1.0, 2.0])
    e = np.array([100.0, 1.5, 3.0])
    out_s, out_e, first, total = merge_spans(s, e, 0.0)
    assert out_s.tolist() == [0.0] and out_e.tolist() == [100.0]
    assert first.tolist() == [0] and total == 3


def test_a_span_written_backwards_cannot_widen_a_bar_into_the_next_one():
    # The minimal shape of the defect: bar 0 measured its gap from e=0.0, so
    # the two never merged, and the one-device-pixel floor then stretched
    # bar 0 to 11.0 straight through bar 1 starting at 10.5.
    s = np.array([10.0, 10.5])
    e = np.array([0.0, 11.0])  # row 0 is written backwards, as session.py keeps it
    out_s, out_e, _, total = merge_spans(s, e, 1.0)
    assert total == 2
    assert np.all(out_s[1:] > out_e[:-1])
    assert np.all(out_e >= out_s)


def test_a_backwards_span_reaches_its_own_start_and_no_further():
    # It covers no time, so it must not drag the group's reach behind the
    # group's start -- and it must not vanish either: session.py reports the
    # row rather than dropping it, and so does the merge.
    s = np.array([10.0, 40.0])
    e = np.array([0.0, 41.0])
    out_s, out_e, first, total = merge_spans(s, e, 0.0)
    assert total == 2 and first.tolist() == [0, 1]
    assert out_s.tolist() == [10.0, 40.0]
    assert out_e.tolist() == [10.0, 41.0]


def test_merging_nothing_gives_nothing():
    out_s, out_e, first, total = merge_spans(EMPTY, EMPTY, 1.0)
    assert out_s.size == out_e.size == first.size == 0 and total == 0


def test_a_gap_of_exactly_the_tolerance_still_merges():
    # The two bars would share a device pixel column, so they are one bar.
    s = np.array([0.0, 2.0])
    e = np.array([1.0, 3.0])
    assert merge_spans(s, e, 1.0)[0].size == 1
    assert merge_spans(s, e, 0.999)[0].size == 2


# --------------------------------------------------------------- pair_runs

#: The seven shapes from the specification's defect table.  Times are
#: 1, 2, 3, ... so a clamp is visible as a 0 (t_first) or a 100 (t_last).
DEFECT_SHAPES = {
    "clean": ([1, 0, 1, 0], [1, 3], [2, 4], [0, 0], [0, 0], ()),
    "trailing start": (
        [1, 0, 1],
        [1, 3],
        [2, 100],
        [0, 0],
        [0, 1],
        ((2, 3.0, RUN_UNCLOSED),),
    ),
    "leading stop": (
        [0, 1, 0],
        [0, 2],
        [1, 3],
        [1, 0],
        [0, 0],
        ((0, 1.0, RUN_UNOPENED),),
    ),
    "dropped stop": (
        [1, 1, 0],
        [1, 2],
        [2, 3],
        [0, 0],
        [1, 0],
        ((0, 1.0, RUN_UNCLOSED),),
    ),
    "dropped start": (
        [1, 0, 0],
        [1, 2],
        [2, 3],
        [0, 1],
        [0, 0],
        ((2, 3.0, RUN_UNOPENED),),
    ),
    "start alone": ([1], [1], [100], [0], [1], ((0, 1.0, RUN_UNCLOSED),)),
    "stop alone": ([0], [0], [1], [1], [0], ((0, 1.0, RUN_UNOPENED),)),
}


def run_shape(flags):
    """Rows at 1, 2, 3 ... s inside a 0-100 s recording."""
    is_start = np.array(flags, dtype=bool)
    times = np.arange(1, is_start.size + 1, dtype=np.float64)
    return pair_runs(times, is_start, 0.0, 100.0)


@pytest.mark.parametrize("shape", list(DEFECT_SHAPES))
def test_every_defect_shape_pairs_the_way_the_table_says(shape):
    flags, starts, ends, left, right, problems = DEFECT_SHAPES[shape]
    got = run_shape(flags)
    assert got.starts.tolist() == starts
    assert got.ends.tolist() == ends
    assert got.open_left.astype(int).tolist() == left
    assert got.open_right.astype(int).tolist() == right
    assert got.problems == problems


@pytest.mark.parametrize("shape", list(DEFECT_SHAPES))
def test_no_edge_time_is_ever_nan_or_infinite(shape):
    # The single most likely way to ship a broken reader: numpy sorts NaN
    # last, so an open-left span would sit at the END of a "sorted" array and
    # every searchsorted in this module would return the wrong slice in
    # silence.
    got = run_shape(DEFECT_SHAPES[shape][0])
    assert np.isfinite(got.starts).all()
    assert np.isfinite(got.ends).all()


@pytest.mark.parametrize("shape", list(DEFECT_SHAPES))
def test_every_edge_time_is_from_the_input_or_a_named_bound(shape):
    flags = DEFECT_SHAPES[shape][0]
    got = run_shape(flags)
    allowed = set(np.arange(1, len(flags) + 1, dtype=float).tolist()) | {0.0, 100.0}
    assert set(got.starts.tolist()) <= allowed
    assert set(got.ends.tolist()) <= allowed
    assert np.all(got.ends >= got.starts)


def test_a_dropped_stop_leaves_the_earlier_run_open_not_the_later_one():
    # LIFO pairing: the stop closes the LAST start before it, so one lost row
    # costs one wrong edge instead of fusing two runs into one long span.
    got = run_shape([1, 1, 0])
    assert got.open_right.tolist() == [True, False]
    assert got.starts.tolist() == [1.0, 2.0]


def test_a_clean_log_pairs_row_by_row():
    # The shape of the real file: 31 started + 31 stopped, interleaved.
    times = np.sort(np.random.default_rng(41).uniform(0.0, RECORDING_S, 62))
    is_start = np.zeros(62, bool)
    is_start[0::2] = True
    got = pair_runs(times, is_start, 0.0, RECORDING_S)
    assert got.starts.size == 31 and got.problems == ()
    assert not got.open_left.any() and not got.open_right.any()
    assert np.array_equal(got.starts, times[0::2])
    assert np.array_equal(got.ends, times[1::2])


def test_the_general_pairing_agrees_with_the_fast_path_on_a_clean_log():
    # The defect-tolerant path is what the fast path is a shortcut FOR.  If it
    # is only ever run on broken files it is a path nobody has checked.
    rng = np.random.default_rng(42)
    times = np.sort(rng.uniform(0.0, RECORDING_S, 62))
    is_start = np.zeros(62, bool)
    is_start[0::2] = True
    fast = pair_runs(times, is_start, 0.0, RECORDING_S)
    slow = _pair_general(times, is_start, np.arange(62), 0.0, RECORDING_S, [])
    assert np.array_equal(fast.starts, slow.starts)
    assert np.array_equal(fast.ends, slow.ends)
    assert (
        slow.problems == () and not slow.open_left.any() and not slow.open_right.any()
    )


def test_a_random_defect_soup_never_drops_a_row_or_invents_a_time():
    rng = np.random.default_rng(43)
    for _ in range(2000):
        n = int(rng.integers(0, 12))
        is_start = rng.random(n) < 0.5
        times = np.sort(rng.uniform(0.0, 100.0, n))
        got = pair_runs(times, is_start, -1.0, 101.0)
        allowed = set(times.tolist()) | {-1.0, 101.0}
        assert np.isfinite(got.starts).all() and np.isfinite(got.ends).all()
        assert np.all(got.ends >= got.starts)
        assert np.all(np.diff(got.starts) >= 0)
        assert set(got.starts.tolist()) <= allowed
        assert set(got.ends.tolist()) <= allowed
        # every started row opens a span, and every unmatched stop opens one
        unopened = sum(1 for p in got.problems if p[2] == RUN_UNOPENED)
        unclosed = sum(1 for p in got.problems if p[2] == RUN_UNCLOSED)
        assert got.starts.size == int(is_start.sum()) + unopened
        assert unopened == int(got.open_left.sum())
        assert unclosed == int(got.open_right.sum())


def test_pairing_nothing_gives_nothing():
    got = pair_runs(EMPTY, np.zeros(0, bool), 0.0, 100.0)
    assert got.starts.size == got.ends.size == 0
    assert got.open_left.size == got.open_right.size == 0
    assert got.problems == ()


def test_a_row_without_a_usable_time_is_reported_not_silently_skipped():
    times = np.array([1.0, np.nan, 3.0, 4.0])
    is_start = np.array([True, False, True, False])
    got = pair_runs(times, is_start, 0.0, 100.0)
    no_time = [p for p in got.problems if p[2] == RUN_NO_TIME]
    assert len(no_time) == 1
    assert no_time[0][0] == 1 and np.isnan(no_time[0][1])
    # the placeable rows still pair, and the row that could not be placed is
    # reported as the reason the first run never closes
    assert np.isfinite(got.starts).all() and np.isfinite(got.ends).all()
    assert got.starts.tolist() == [1.0, 3.0]
    assert got.ends.tolist() == [3.0, 4.0]
    assert got.open_right.tolist() == [True, False]
    assert any(p[2] == RUN_UNCLOSED for p in got.problems)


def test_rows_out_of_time_order_are_sorted_and_reported():
    times = np.array([3.0, 1.0, 2.0, 4.0])
    is_start = np.array([True, True, False, False])
    got = pair_runs(times, is_start, 0.0, 100.0)
    assert any(p[2] == RUN_OUT_OF_ORDER for p in got.problems)
    assert np.all(np.diff(got.starts) >= 0)
    assert np.all(got.ends >= got.starts)
    assert np.isfinite(got.starts).all() and np.isfinite(got.ends).all()


def test_a_flag_array_of_the_wrong_length_is_refused():
    with pytest.raises(ValueError):
        pair_runs(np.array([1.0, 2.0]), np.array([True]), 0.0, 10.0)


# ------------------------------------------------------------- window_steps


def control_track(rng, n=1373, t_end=RECORDING_S, overrun=0.0):
    """Control-track shaped: sparse change rows over most of the recording,
    with a long stretch at the end where nothing changes.

    `overrun` seconds of change rows are written PAST `t_end`, which is what
    a stimulator that keeps logging after the WAV stops produces.  It
    defaults to 0 so the plain fixture stays the clean case -- but without a
    non-zero setting no test here could reach a row beyond the last frame,
    which is how a staircase that strokes backwards across the view survived
    every property test in this file.
    """
    times = np.sort(rng.uniform(28.9, 500.0 + overrun, n))
    values = rng.choice([0.0, 0.25, 0.5, 1.0], n)
    return times, values, t_end


def test_the_value_in_force_at_the_left_edge_survives_a_long_gap():
    # The control track's last change row is at 500 s and the file runs to
    # 607 s: a 1 s window at 590 s must still draw the held value, not blank.
    times = np.array([28.9, 100.0, 500.0])
    values = np.array([1.0, 0.5, 0.25])
    x, y = window_steps(times, values, 590.0, 591.0, RECORDING_S)
    assert x.size and y[0] == 0.25
    assert x[0] == 590.0 and x[-1] == 591.0


@pytest.mark.parametrize("width", [1.0, 60.0, RECORDING_S])
@pytest.mark.parametrize("overrun", [0.0, 300.0])
def test_the_held_value_reads_back_off_the_polyline_at_every_window_size(
    width, overrun
):
    rng = np.random.default_rng(51)
    times, values, t_end = control_track(rng, overrun=overrun)
    for _ in range(200):
        t0 = float(rng.uniform(times[0] + 0.001, t_end))
        t1 = t0 + width
        x, y = window_steps(times, values, t0, t1, t_end)
        if x.size == 0:
            continue
        for t in rng.uniform(t0, min(t1, t_end, x[-1]), 5):
            assert read_back(x, y, t) == held_value(times, values, t)


def test_the_first_vertex_is_clamped_to_the_view():
    # Without the clamp the first segment stretches back to where the value
    # was set -- up to 500 s outside the view -- and drags the x-range with it.
    times = np.array([28.9, 100.0])
    values = np.array([1.0, 0.5])
    x, _ = window_steps(times, values, 90.0, 95.0, RECORDING_S)
    assert x[0] == 90.0


@pytest.mark.parametrize("overrun", [0.0, 300.0])
def test_the_staircase_only_ever_runs_flat_or_vertical(overrun):
    rng = np.random.default_rng(52)
    times, values, t_end = control_track(rng, n=200, overrun=overrun)
    x, y = window_steps(times, values, 100.0, 900.0, t_end)
    flat = y[1:] == y[:-1]
    vertical = x[1:] == x[:-1]
    assert np.all(flat | vertical)


def test_a_view_before_the_first_change_row_draws_nothing():
    times = np.array([28.9, 100.0])
    values = np.array([1.0, 0.5])
    x, y = window_steps(times, values, 0.0, 10.0, RECORDING_S)
    assert x.size == y.size == 0


def test_the_staircase_stops_at_the_end_of_the_recording():
    # Past the last frame no value is in force, and a line drawn there says
    # one is.
    times = np.array([10.0])
    values = np.array([1.0])
    x, _ = window_steps(times, values, 20.0, 900.0, RECORDING_S)
    assert x[-1] == RECORDING_S


@pytest.mark.parametrize("overrun", [0.0, 300.0])
def test_the_staircase_never_strokes_backwards(overrun):
    # x must be non-decreasing or the line is drawn right-to-left across the
    # view.  A logger that keeps writing after the WAV stops is ordinary in
    # the field, and clamping only the closing vertex left those rows in.
    rng = np.random.default_rng(55)
    times, values, t_end = control_track(rng, n=400, overrun=overrun)
    for t0, t1 in zip(*random_windows(rng, 400)):
        x, _ = window_steps(times, values, t0, t1, t_end)
        assert np.all(np.diff(x) >= 0.0)
        assert x.size == 0 or x[-1] <= min(t1, t_end)


def test_a_change_row_past_the_last_frame_is_outside_the_staircase():
    # 650 s and 700 s are logged, the recording ends at 607.104 s: the
    # staircase closes at the last frame holding the value set at 600 s, and
    # neither later row gets a vertex.
    times = np.array([10.0, 600.0, 650.0, 700.0])
    values = np.array([0.0, 3.0, 4.0, 5.0])
    x, y = window_steps(times, values, 0.0, 800.0, RECORDING_S)
    assert x[-1] == RECORDING_S
    assert np.all(np.diff(x) >= 0.0)
    assert y[-1] == 3.0
    assert 4.0 not in y.tolist() and 5.0 not in y.tolist()


def test_a_view_entirely_past_the_last_frame_draws_nothing():
    # No value is in force there, and a held line would say one is.
    times = np.array([10.0, 650.0])
    values = np.array([1.0, 2.0])
    x, y = window_steps(times, values, 700.0, 800.0, RECORDING_S)
    assert x.size == y.size == 0


def test_a_series_denser_than_the_pixel_grid_falls_back_to_the_envelope():
    rng = np.random.default_rng(53)
    times, values, t_end = control_track(rng)
    x, y = window_steps(times, values, 0.0, t_end, t_end, pixels=100)
    assert x.size <= 2 * 100
    assert set(y.tolist()) <= set(values.tolist())


# ------------------------------------------------------------ step_envelope


def test_the_envelope_reports_both_extremes_of_every_column():
    # A keep-first decimation would draw whichever value happened to be
    # first; a control that swung 0-1 four times inside one pixel would look
    # steady.
    ts = np.array([0.0, 0.1, 0.2, 0.3, 1.5])
    vs = np.array([0.0, 1.0, 0.0, 1.0, 0.5])
    x, y = step_envelope(ts, vs, 0.0, 2.0, 2)
    assert x.tolist() == [0.0, 0.0, 1.5, 1.5]
    assert y[0::2].tolist() == [0.0, 0.5]  # the column minima
    assert y[1::2].tolist() == [1.0, 0.5]  # the column maxima


def test_every_envelope_value_comes_from_the_input():
    rng = np.random.default_rng(54)
    ts = np.sort(rng.uniform(0.0, 100.0, 5000))
    vs = rng.uniform(-3.0, 3.0, 5000)
    x, y = step_envelope(ts, vs, 0.0, 100.0, 300)
    assert np.all(y[0::2] <= y[1::2])
    assert set(y.tolist()) <= set(vs.tolist())
    assert set(x.tolist()) <= set(ts.tolist())


# ---------------------------------------------------------------- the empty


def test_the_shared_empty_result_cannot_be_written_through():
    # It is handed to every caller with nothing in view, so an in-place edit
    # would reach all of them at once.
    with pytest.raises(ValueError):
        EMPTY[...] = 1.0
