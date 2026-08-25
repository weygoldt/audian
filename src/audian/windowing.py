"""Turning a whole session's annotation arrays into what one view shows.

Every annotation the browser draws is a sorted numpy array that outlives the
view by orders of magnitude: 2187 pulses, 3398 detections, 36 trials and 1373
control rows against a 607.104 s recording that is normally shown 1 s at a
time.  This module is the only place that goes from "the whole array" to "the
arrays for this window", for each of the three shapes annotations come in --
points, spans, and a hold-forward step series -- and it does it with
``searchsorted`` and vectorised numpy so the cost of a redraw is set by what
is *in* the view, not by the size of the file.

It is deliberately array-in, array-out: no polars, no Qt, no import from the
rest of audian.  The two algorithms that are easy to get subtly wrong --
windowing spans, and decimating them -- are then testable against a brute
force reference with no CSV and no widget in the way, which is the whole
reason they live here rather than inside the reader or the plot item.

The three shapes, and why each needs its own function
-----------------------------------------------------
* **Points** (:func:`window_points`, :func:`count_columns`) are instants.
  They are decimated to one per pixel column because what is dropped had no
  pixel of its own -- or, where a draw path wants it, counted per column and
  drawn as density, which drops nothing at all.  Only the first is wired up
  today; :func:`count_columns` says so in its own docstring.
* **Spans** (:func:`window_spans`, :func:`merge_spans`) have extent.  A span
  intersects the view when it *starts before the view ends and ends after the
  view begins*, which is not one ``searchsorted``, and decimating them is an
  interval **merge**, never a keep-first pass: at the full-file view one
  device pixel is 0.43 s against a 0.544 s median trial, so keeping the first
  span per pixel column would erase most of the 12 silence trials and with
  them the control condition.
* **Steps** (:func:`window_steps`, :func:`step_envelope`) are a value held
  until the next change row.  The row that sets the value at the left edge is
  usually *outside* the view -- the exp2 control track spans 28.9-590.0 s in
  1373 change rows -- so the window has to reach one row backwards or the
  track goes blank wherever nothing happened to change.

:func:`pair_runs` is the odd one out: it runs once at load, turning
started/stopped rows into spans, and its contract is that it never drops a
row, never invents a timestamp and never emits a NaN.  A NaN in a span array
would be silent and fatal -- numpy sorts NaN last, so a span whose start is
unknown would sit at the *end* of a "sorted" array and every ``searchsorted``
in this module would quietly return the wrong slice.
"""

from __future__ import annotations

from typing import NamedTuple

import numpy as np

#: The empty result.  Shared rather than freshly allocated because the draw
#: path hits it constantly (most tracks are off-screen most of the time), and
#: read-only so that a caller who scales or offsets it in place is told at
#: once instead of corrupting every other caller's idea of "empty".
EMPTY = np.empty(0, dtype=np.float64)
EMPTY.flags.writeable = False

#: The empty index result, for the same reason.  ``intp`` because these index
#: into the source arrays and are handed straight back to numpy.
EMPTY_ROWS = np.empty(0, dtype=np.intp)
EMPTY_ROWS.flags.writeable = False

#: A run that was started and whose stopped row never arrived.
RUN_UNCLOSED = "run started and never stopped"

#: A stopped row with no started row of its own to close.
RUN_UNOPENED = "a started row is missing"

#: Rows arrived out of time order.  Everything downstream is ``searchsorted``
#: on the result, so the rows are sorted before pairing rather than left to
#: produce a plausible-looking wrong answer.
RUN_OUT_OF_ORDER = "rows are out of time order"

#: A row whose time is NaN or infinite.  It cannot be placed on any axis, so
#: it takes no part in pairing -- but it is reported, never quietly skipped.
RUN_NO_TIME = "row has no usable time"

#: :func:`window_spans` was handed a span array containing NaN.  Named because
#: it is raised from a per-redraw path, where the message is the only thing a
#: reader gets: it has to say which invariant broke, not just that one did.
SPAN_NO_TIME = (
    "span arrays must be free of NaN: a NaN end poisons max_end from that row "
    "on and every window after it silently comes back as a superset"
)


def window_points(
    times: np.ndarray, t0: float, t1: float, pixels: int = 0
) -> tuple[np.ndarray, int]:
    """Times to draw for the view ``[t0, t1]``, and how many are really there.

    Two reductions, in this order:

    * **Window.**  ``searchsorted`` on the sorted times, so the cost of a
      view is set by what is *in* it, not by the size of the file.  This
      is what keeps a hundred thousand events off the draw path.
    * **Pixel buckets.**  Inside the window, events closer together than
      one device pixel would paint the same 1 px column.  The window is
      bucketed by pixel and the first time in each bucket is kept.  It is
      a decimation, but not a visible one: what is dropped had no pixel
      of its own.  Sorted input makes it a single ``diff``, no sort.

    The returned count is the true number in the window, so a caller can
    report the density it is looking at rather than the number of lines
    that survived.

    ``pixels=0`` disables the decimation entirely.  The overlay does NOT call
    it that way -- ``eventoverlay.AnnotationLayer.point_window`` passes a real
    pixel budget (2054 in a 1400 px window) and does decimate -- so what is on
    screen is a mark per column, and the count a reader may believe is the
    returned `total`, never the number of lines drawn.  A layer dense enough
    for that to matter wants :func:`count_columns` instead, which drops
    nothing; nothing draws that way yet.
    """
    i0 = int(np.searchsorted(times, t0, side="left"))
    i1 = int(np.searchsorted(times, t1, side="right"))
    total = i1 - i0
    if total <= 0:
        return EMPTY, 0
    times = times[i0:i1]
    span = t1 - t0
    if pixels > 0 and total > pixels and span > 0:
        bucket = ((times - t0) * (pixels / span)).astype(np.int64, copy=False)
        keep = np.empty(bucket.size, dtype=bool)
        keep[0] = True
        np.not_equal(bucket[1:], bucket[:-1], out=keep[1:])
        times = times[keep]
    return times, total


def count_columns(
    times: np.ndarray, t0: float, t1: float, pixels: int
) -> tuple[np.ndarray, np.ndarray, int]:
    """Per-device-pixel-column event counts: the density reading, unbuilt.

    **Nothing in the draw path calls this yet.**  It is the whole of spec §0.2
    -- "a point layer is never decimated away; ticks when sparse, density when
    dense", which the direction override still marks as applying -- and until
    an overlay uses it, a dense layer is drawn by :func:`window_points`, which
    keeps one mark per pixel column and silently drops the rest.  That is
    latent on exp2, whose largest series is 1279 rows against a 2054 px
    budget, and live on exp3, whose unexplained detections are 7912.  Kept
    rather than deleted for that reason, and said out loud rather than left
    reading like a primitive something already uses.

    Returns ``(columns, counts, total)``: `columns` are the occupied column
    indices (intp, ascending), `counts` the true count in each, `total` the
    number of events in the window.  Nothing is dropped and nothing is
    scaled -- the caller draws a bar of `counts` device pixels, so 0/1/2/3
    events are 0/1/2/3 pixels at every zoom and the reading never changes
    meaning when the view is panned.

    ``np.bincount`` is what makes that affordable: 0.82 ms at 500 000 rows in
    view over 1400 columns, with no Python per row.
    """
    i0 = int(np.searchsorted(times, t0, side="left"))
    i1 = int(np.searchsorted(times, t1, side="right"))
    if i1 <= i0 or pixels <= 0 or t1 <= t0:
        return EMPTY_ROWS, EMPTY_ROWS, max(0, i1 - i0)
    b = ((times[i0:i1] - t0) * (pixels / (t1 - t0))).astype(np.int64, copy=False)
    # An event exactly at t1 lands on column `pixels`, one past the grid; and
    # floating point can push an event a hair outside either edge.  Clipping
    # keeps it in the edge column instead of raising or losing it -- the one
    # place in this module where a mark moves, and it moves by under a pixel.
    np.clip(b, 0, pixels - 1, out=b)
    counts = np.bincount(b, minlength=pixels)
    cols = np.flatnonzero(counts)
    return cols, counts[cols], i1 - i0


def window_spans(
    starts: np.ndarray,
    ends: np.ndarray,
    max_end: np.ndarray,
    t0: float,
    t1: float,
    disjoint: bool = True,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, int]:
    """Spans intersecting ``[t0, t1]``: ``start < t1`` AND ``end > t0``.

    Returns ``(s, e, rows, total)``.  `rows` is the source index of each span,
    so a hover readout can name the trial it found without searching for it
    again.  Both edges are exclusive: a span that ends exactly at `t0`, or
    starts exactly at `t1`, touches the view without covering a pixel of it.

    The two-array form is REQUIRED.  ``searchsorted`` on `starts` alone finds
    every span that *begins* in the view and misses the one that began before
    it and is still running -- which, on a 1 s view of a 607 s file, is the
    only span there is most of the time.

    So the slice is bounded from both sides:

    * ``i1 = searchsorted(starts, t1, "left")`` -- everything from `i1` on
      starts at or after the view's end.  This relies on **`starts` being
      ascending**, which is the load-time invariant for every span layer.
    * ``i0 = searchsorted(max_end, t0, "right")`` where
      ``max_end = np.maximum.accumulate(np.maximum(ends, starts))`` is computed
      once at load (`layers.SpanLayer.max_end`).  `max_end` is non-decreasing
      by construction *whatever the spans do*, so it is always searchable, and
      everything before `i0` reaches no further than `t0`.

      It is the REACH, not the end, and the maximum against `starts` is what
      makes an inverted row (``end < start``, kept as written by
      `session._build_trials`) reachable at all: :func:`merge_spans` places its
      bar at ``[start, start + one pixel]``, so a `max_end` that stopped at the
      earlier `end` sliced it away at every view that contained it while the
      whole-file view still drew it.  With ``start=5.0, end=2.0`` the bar was
      drawn at ``[0, 8]`` and gone at ``[4.5, 5.5]`` -- visible only at the
      zoom where it could not be read.

    When the layer is **disjoint** (``starts[1:] >= ends[:-1]``) `ends` is
    itself non-decreasing, `max_end` equals `ends`, and the slice is exact.
    When spans overlap or nest, `max_end` runs ahead of the individual `ends`
    and the slice is a conservative *superset*: it can include a short span
    buried inside a long one that has already finished.  One boolean mask
    finishes the job, and it is only paid for when it is needed.

    `disjoint` is therefore a promise the caller makes about the arrays, not a
    hint: pass ``True`` for spans that overlap and the result may contain a
    span that ended before the view began -- a trial bracket drawn where no
    trial was running.  Compute it, do not assume it.

    NaN is REFUSED, with a ``ValueError``.  It is not handled, because there
    is nothing to hand back: a span with no time is not "outside the view",
    it is a row the writer never placed, and returning it or dropping it both
    read as an answer.  It is refused rather than merely documented because
    the failure it causes is silent -- a NaN end poisons `max_end` from that
    row on, and the window then comes back a *superset* that draws finished
    spans across the view, which looks exactly like data.

    The check is **O(1), two scalar reads**, not a scan, so it is affordable
    in a per-redraw path.  Measured at 50 000 spans: 1.20 us for the check,
    1.89 us for the two ``searchsorted`` calls, 4.08 us for the whole call --
    and 7.24 us for the ``np.isfinite(starts).all()`` scan it replaces, which
    is both dearer *and* grows with the file, which is the one thing this
    module exists not to do.  It is exact rather than a spot check
    because of the two preconditions above: ``np.maximum`` propagates NaN, so
    one NaN anywhere in `ends` makes ``max_end[-1]`` NaN; and numpy orders NaN
    last, so a NaN in an ascending `starts` is ``starts[-1]``.  A caller who
    breaks *those* preconditions -- an unsorted `starts`, or a `max_end` that
    is not ``np.maximum.accumulate(ends)`` -- is already outside the contract
    and this check cannot save them.
    """
    n = int(starts.size)
    if n == 0:
        return EMPTY, EMPTY, EMPTY_ROWS, 0
    if np.isnan(starts[n - 1]) or np.isnan(max_end[n - 1]):
        raise ValueError(SPAN_NO_TIME)
    # A degenerate or reversed view contains no pixels, so it contains no
    # spans.  The predicate alone would disagree: with t0=5, t1=1 a span
    # covering both still satisfies start < t1 and end > t0.
    if t1 <= t0:
        return EMPTY, EMPTY, EMPTY_ROWS, 0
    i0 = int(np.searchsorted(max_end, t0, side="right"))
    i1 = int(np.searchsorted(starts, t1, side="left"))
    if i1 <= i0:
        return EMPTY, EMPTY, EMPTY_ROWS, 0
    s = starts[i0:i1]
    e = ends[i0:i1]
    rows = np.arange(i0, i1, dtype=np.intp)
    if not disjoint:
        # `np.maximum` for the same reason `max_end` carries it: an inverted
        # span reaches its own start, which is where its bar is drawn.
        keep = np.maximum(e, s) > t0
        s, e, rows = s[keep], e[keep], rows[keep]
    return s, e, rows, int(s.size)


def merge_spans(
    s: np.ndarray, e: np.ndarray, tol: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray, int]:
    """Fuse spans separated by less than `tol` (one device pixel).

    Returns ``(out_s, out_e, first, total)``.  `first` maps each merged bar
    back to the first source span in its group, so a click on a bar still
    lands on a real row; `total` is the TRUE pre-merge count and is what the
    chip badge and the readout must report, never the number of bars drawn.

    This is decimation for spans, and it is a **merge**, not a keep-first
    pass.  Dropping a span loses a region of time: at the 607 s view one
    device pixel is 0.43 s against a 0.544 s median trial, so keeping the
    first span per pixel column drops most of the 12 silence trials and the
    control condition disappears from the overview.  Merging instead turns a
    cluster into one bar that still covers everywhere a trial was running.
    On the real 36 trials: 30 bars at tol 0.5 s, 12 at tol 5 s, no overlaps.

    Vectorised, and it needs no sort: `s` is already ascending, so the group
    boundaries are one ``maximum.accumulate`` and one ``flatnonzero``.  A gap
    of exactly `tol` still merges -- the two bars would share a pixel column.

    An INVERTED span (``end < start``) reaches only as far as its own start.
    ``session.py`` deliberately keeps such a row as written rather than
    swapping its edges -- swapping would invent a bracket over a stretch in
    which nothing was running -- so this function has to be handed them, and
    it must not let one pull a group's reach backwards behind the group's own
    start.  It did: with ``s=[10.0, 10.5]``, ``e=[0.0, 11.0]`` and
    ``tol=1.0`` the gap was measured from ``e[0]=0.0``, so the two never
    merged, and the one-pixel floor then widened bar 0 to ``[10.0, 11.0]``
    straight through bar 1 at ``[10.5, 11.5]``.  Overlapping bars break the
    only thing every caller does with the result -- ``searchsorted`` on
    `out_s` (see ``session._inside``) -- so the reach is ``max(e, s)``.
    """
    n = s.size
    if n == 0:
        return EMPTY, EMPTY, EMPTY_ROWS, 0
    # run_end[i] is how far the spans up to i reach.  Comparing s[i] against
    # run_end[i-1] rather than e[i-1] is what makes nesting safe: a span
    # buried inside a longer one cannot open a gap the longer one already
    # covers.  The maximum against `s` costs one pass and buys the invariant
    # the rest of this function rests on: run_end[i] >= s[i], so a group's
    # reach is never behind its own start.
    run_end = np.maximum.accumulate(np.maximum(e, s))
    boundary = np.flatnonzero(s[1:] - run_end[:-1] > tol) + 1
    first = np.concatenate((np.zeros(1, np.intp), boundary))
    last = np.concatenate((boundary, np.array([n], np.intp))) - 1
    out_s = s[first]
    # run_end[last] is this group's own maximum and never leaks from an
    # earlier group: a boundary at k means s[k] - run_end[k-1] > tol >= 0, so
    # run_end[k-1] < s[k] <= the group's own max end.
    #
    # The floor gives a zero-length span one device pixel so it is visible,
    # and cannot create an overlap.  With run_end >= s elementwise,
    # out_s_prev = s[first_prev] <= run_end[first_prev] <= run_end[k-1], and a
    # boundary at k gives run_end[k-1] < s[k] - tol; so both candidates for
    # out_e_prev -- run_end[k-1] and out_s_prev + tol -- stay strictly below
    # s[k] = out_s_next.  Without the maximum against `s` the first inequality
    # fails for an inverted span and the floor reaches into the next bar.
    out_e = np.maximum(run_end[last], out_s + tol)
    return out_s, out_e, first, n


def window_steps(
    times: np.ndarray,
    values: np.ndarray,
    t0: float,
    t1: float,
    t_end: float,
    pixels: int = 0,
) -> tuple[np.ndarray, np.ndarray]:
    """A hold-forward series as an explicit staircase polyline.

    Returns ``(x, y)`` for a plain line item.  The staircase is built here
    rather than left to pyqtgraph's ``stepMode`` because the caller also
    windows, clamps and decimates it, and those three do not compose with a
    draw-time step mode: the value in force at the left edge has no vertex of
    its own to step from.

    `i0` takes the row IN FORCE at `t0`, not the first row after it.  The row
    that set the current value may be far outside the view -- the exp2
    control track holds a value across gaps of minutes inside its 28.9-590.0 s
    extent -- and without the backward reach the track is blank wherever
    nothing happened to change, which reads as "no data" rather than "steady".
    ``x[0]`` is then clamped to `t0` so that first segment does not stretch
    back to where it was set and drag the view's x-range with it.

    The staircase stops at ``min(t1, t_end)``: past the end of the recording
    there is no value in force, and a line drawn there says there is.  That
    bound is applied to the WINDOW, not just to the closing vertex -- change
    rows at or after `t_end` are outside every staircase this function can
    draw, and a logger that keeps writing after the WAV stops is ordinary in
    the field.  Clamping the closing vertex alone would leave those rows in
    the polyline and send `x` backwards from the last of them to `t_end`,
    stroking the line right-to-left across the view.
    """
    t_stop = min(t1, t_end)
    if t_stop <= t0:
        # The view lies entirely past the last frame, so no value is in force
        # anywhere in it -- the same truth as a view before the first row.
        return EMPTY, EMPTY
    i0 = max(int(np.searchsorted(times, t0, side="right")) - 1, 0)
    i1 = int(np.searchsorted(times, t_stop, side="left"))
    if i1 <= i0:
        # Nothing is in force: the whole view lies before the first change
        # row.  An empty result draws nothing, which is the truth.
        return EMPTY, EMPTY
    ts = times[i0:i1].copy()
    vs = values[i0:i1]
    if ts[0] < t0:
        ts[0] = t0
    if pixels > 0 and ts.size > pixels:
        return step_envelope(ts, vs, t0, t1, pixels)
    # Two vertices per change row: the value is carried flat to the next
    # change and the shared x makes the riser vertical.  The trailing point
    # closes the last step at the view (or recording) end.
    x = np.repeat(np.append(ts, t_stop), 2)[1:-1]
    y = np.repeat(vs, 2)
    return x, y


def step_envelope(
    ts: np.ndarray, vs: np.ndarray, t0: float, t1: float, pixels: int
) -> tuple[np.ndarray, np.ndarray]:
    """Per-column min/max of a step series, for when it is denser than pixels.

    ``reduceat``, never keep-first: a keep-first decimation lies about the
    range inside the bucket, so a control that swung between 0 and 1 four
    times inside one pixel column would be drawn as whichever value happened
    to be first.  Both extremes are real values from the input, which is why
    the envelope can be read as "everything that happened here" rather than
    as a sample of it.
    """
    b = ((ts - t0) * (pixels / (t1 - t0))).astype(np.int64, copy=False)
    first = np.flatnonzero(np.concatenate(([True], b[1:] != b[:-1])))
    lo = np.minimum.reduceat(vs, first)
    hi = np.maximum.reduceat(vs, first)
    x = np.repeat(ts[first], 2)
    y = np.empty(x.size, dtype=np.float64)
    y[0::2] = lo
    y[1::2] = hi
    return x, y


class PairResult(NamedTuple):
    """What :func:`pair_runs` made of a set of started/stopped rows.

    `open_left` / `open_right` mark the spans whose edge was not in the file.
    The *flag* carries that truth, never the number: the times are always
    finite so that every ``searchsorted`` downstream keeps working.
    """

    starts: np.ndarray
    ends: np.ndarray
    open_left: np.ndarray
    open_right: np.ndarray
    problems: tuple[tuple[int, float, str], ...]


def pair_runs(
    times: np.ndarray,
    is_start: np.ndarray,
    t_first: float,
    t_last: float,
) -> PairResult:
    """started/stopped rows -> spans.  Never drops, never invents, never NaN.

    `times` are the rows of one start/stop pair of event kinds in recording
    seconds, ascending; `is_start` says which of the two each row is.  On the
    real exp2 log that is 31 ``localization_started`` and 31
    ``localization_stopped`` rows, perfectly interleaved, which the fast path
    turns into 31 spans totalling 360.015 s with two slices and no problems.

    Everything else is a defect, and a defect is REPORTED, never repaired by
    dropping a row or making one up.  `problems` is a tuple of
    ``(source_row, time, reason)`` using one of the ``RUN_*`` constants.

    A NaN in `starts` or `ends` would destroy every ``searchsorted`` in this
    module -- numpy sorts NaN last, so an open-LEFT span would sit at the END
    of the array and :func:`window_spans` would silently return the wrong
    slice for every view.  An unknown edge is therefore clamped to the
    ADJACENT EVENT TIME when there is a row on that side (a run that was
    never stopped is at least known to have been running until the next thing
    that happened), and otherwise to the recording bound -- `t_first`,
    normally 0.0, and `t_last`, ``frames / rate``, 607.104 s on exp2.  The
    boolean flag, not the number, says the edge is unknown.

    Pairing is LIFO: each stop closes the LAST start before it, and each start
    takes the FIRST stop that names it.  So a dropped stop leaves the
    *earlier* run open rather than swallowing the next one whole -- one lost
    row costs one wrong edge, not a fused pair of runs.
    """
    times = np.asarray(times, dtype=np.float64)
    is_start = np.asarray(is_start, dtype=bool)
    if is_start.shape != times.shape:
        raise ValueError("times and is_start must have the same shape")
    n = times.size
    if n == 0:
        return PairResult(EMPTY, EMPTY, np.zeros(0, bool), np.zeros(0, bool), ())

    problems: list[tuple[int, float, str]] = []

    # A row with no usable time cannot be placed on any axis and would poison
    # the sort below, so it takes no part in pairing.  It is reported, which
    # is the difference between excluding it and losing it.
    finite = np.isfinite(times)
    src = np.arange(n, dtype=np.intp)
    if not finite.all():
        problems += [
            (int(i), float(times[i]), RUN_NO_TIME) for i in np.flatnonzero(~finite)
        ]
        src = src[finite]
        times = times[finite]
        is_start = is_start[finite]
        n = times.size
        if n == 0:
            return PairResult(
                EMPTY, EMPTY, np.zeros(0, bool), np.zeros(0, bool), tuple(problems)
            )

    # Ascending time is a precondition of the pairing AND of every window
    # afterwards.  Sorting is cheaper than the failure mode: an out-of-order
    # pair produces a span with end < start that draws as nothing at all.
    inversions = np.flatnonzero(times[1:] < times[:-1])
    if inversions.size:
        i = int(inversions[0]) + 1
        problems.append((int(src[i]), float(times[i]), RUN_OUT_OF_ORDER))
        order = np.argsort(times, kind="stable")
        src, times, is_start = src[order], times[order], is_start[order]

    # The fast path: perfectly interleaved start, stop, start, stop.  This is
    # what every intact file takes, so it costs two slices and no allocation
    # beyond the copies the caller owns.
    if (
        not problems
        and n % 2 == 0
        and is_start[0::2].all()
        and not is_start[1::2].any()
    ):
        return PairResult(
            times[0::2].copy(),
            times[1::2].copy(),
            np.zeros(n // 2, bool),
            np.zeros(n // 2, bool),
            (),
        )
    return _pair_general(times, is_start, src, t_first, t_last, problems)


def _pair_general(
    times: np.ndarray,
    is_start: np.ndarray,
    src: np.ndarray,
    t_first: float,
    t_last: float,
    problems: list[tuple[int, float, str]],
) -> PairResult:
    """The defect-tolerant pairing, split out so the fast path can be checked.

    Kept separate for one reason beyond readability: it is exercised by the
    tests on *well-formed* input too, where it must agree with the fast path
    row for row.  A pairing that is only ever run on broken files is a pairing
    nobody has checked.
    """
    n = times.size
    si = np.flatnonzero(is_start)
    ei = np.flatnonzero(~is_start)

    # For each stop, the start it closes: the last start before it, or -1 when
    # there is none.  `new` picks that owner's FIRST stop; everything else is
    # a stop with no start of its own.
    first_stop = np.full(si.size, -1, np.intp)
    extra = EMPTY_ROWS
    if ei.size:
        owner = np.searchsorted(si, ei, side="left") - 1
        new = np.empty(ei.size, bool)
        new[0] = True
        np.not_equal(owner[1:], owner[:-1], out=new[1:])
        sel = np.flatnonzero(new & (owner >= 0))
        first_stop[owner[sel]] = ei[sel]
        extra = ei[np.flatnonzero(~new | (owner < 0))]

    # Spans that begin with a started row.
    paired = first_stop >= 0
    a_start = times[si]
    a_end = np.empty(si.size, dtype=np.float64)
    a_end[paired] = times[first_stop[paired]]
    if not paired.all():
        loose = np.flatnonzero(~paired)
        nxt = si[loose] + 1
        # The next row of any kind is the last moment the run is known to have
        # been running; past the last row, the recording end.  The outer
        # maximum only bites if t_last precedes the start row, i.e. the bounds
        # passed in disagree with the rows -- a zero-length span at a real
        # time beats a backwards one.
        adjacent = np.where(nxt < n, times[np.minimum(nxt, n - 1)], t_last)
        a_end[loose] = np.maximum(adjacent, a_start[loose])
        problems += [
            (int(src[si[k]]), float(times[si[k]]), RUN_UNCLOSED) for k in loose
        ]

    # Spans that begin with a stopped row whose start never arrived.
    b_end = times[extra]
    prev = extra - 1
    b_start = np.where(prev >= 0, times[np.maximum(prev, 0)], t_first)
    b_start = np.minimum(b_start, b_end)
    problems += [(int(src[i]), float(times[i]), RUN_UNOPENED) for i in extra]

    starts = np.concatenate((a_start, b_start))
    ends = np.concatenate((a_end, b_end))
    open_left = np.concatenate((np.zeros(si.size, bool), np.ones(extra.size, bool)))
    open_right = np.concatenate((~paired, np.zeros(extra.size, bool)))

    # Stable, so two spans starting at the same instant keep file order.
    order = np.argsort(starts, kind="stable")
    problems.sort(key=lambda p: p[0])
    return PairResult(
        starts[order],
        ends[order],
        open_left[order],
        open_right[order],
        tuple(problems),
    )
