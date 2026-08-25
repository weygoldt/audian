"""The drawable model: what a session bundle offers the browser to switch on.

A layer is one nameable thing in a session -- volley trials, the resting-rate
pulse train, the detections the log does not explain -- carrying its rows as
sorted float64 arrays and a colour *role* rather than a colour.  Three shapes
cover everything the device writes: instants (:class:`PointLayer`), intervals
(:class:`SpanLayer`), and values held until the next change row
(:class:`StepTrack`).

The model is deliberately ignorant.  It does not know what colour a role
resolves to, how tall a lane is, or what is in view: :mod:`audian.theme` owns
the first and :mod:`audian.windowing` the third, and keeping both out of here
is what lets every layer be built and asserted against the real exp2 bundle
with no widget on screen.  :mod:`audian.session` builds these out of the CSVs.

**Predicted is not observed.**  Seven of the 2187 exp2 pulses have
``match_status = "unmatched"`` and a null ``detected_time_s``: the fit says
where they are, the recording never confirmed it.  They live in their own
:class:`PointSeries`, not merged into the observed train, because a merge is a
*correctness* bug and not an inefficiency -- 7 rows inside a train of 901 lose
their pixel bucket to an observed neighbour at any zoom-out, and six of the
seven vanish at a 300 px budget.

**Every array is a promise.**  Times are float64, C-contiguous and ascending,
and every ``searchsorted`` in :mod:`audian.windowing` depends on all three.
:attr:`SpanLayer.max_end` and :attr:`SpanLayer.disjoint` are computed once,
here, at load: recomputing them per redraw would defeat the point of having a
windowing primitive at all, and *assuming* `disjoint` would let a windowing
query return a span that had already ended.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import polars as pl


# --- what the bundle is made of ---------------------------------------------

#: Instants: a pulse, a detection, a log line.  Drawn as ticks or as density.
KIND_POINT = "point"

#: Intervals: a trial, a localization run.  Drawn as bars with two edges.
KIND_SPAN = "span"

#: A value held until the next change row.  Drawn as a staircase.
KIND_TRACK = "track"

LAYER_TRIALS_VOLLEY = "trials.volley"
LAYER_TRIALS_BASELINE = "trials.baseline"
LAYER_TRIALS_SILENCE = "trials.silence"

#: ``localization`` and ``baseline`` pulses are ONE visual category.  Both are
#: the animal-facing resting rate -- same amplitude (0.250 exactly on all 908
#: exp2 rows), same rhythm -- and the only difference is whether a trial
#: happened to be open at the time, which the trials track already says.
#: ``pulse_type`` stays in the frame and in :meth:`PointLayer.describe`; the
#: ruling is about visual encoding, not about the data.
LAYER_PULSES_RESTING = "pulses.resting"

#: Volley pulses: the stimulus proper, 3.6x the resting amplitude.
LAYER_PULSES_VOLLEY = "pulses.volley"

LAYER_DET_EXPLAINED = "detections.explained"
LAYER_DET_UNEXPLAINED = "detections.unexplained"
LAYER_RUNS = "localization"
LAYER_SESSION_EVENTS = "session_events"
LAYER_CONTROLS = "controls"

TRACK_TRIALS = "trials"
TRACK_PULSES = "pulses"
TRACK_HEARD = "heard"
TRACK_RUNS = "runs"
TRACK_LOG = "log"
TRACK_CTRL = "ctrl"


# --- the layer model --------------------------------------------------------


class Layer:
    """One thing the reader can switch on: a name, a shape, and a colour role.

    A layer knows what it is and where its rows are in time.  It does not know
    what colour that role resolves to, how tall its track is, or what is in
    view -- :mod:`audian.theme` and :mod:`audian.windowing` own those, and
    keeping them out of here is what lets the reader be tested with no Qt.

    The three name fields are an elision ladder for a chip that has to fit in
    one subline at 1280 px as well as at 2560 px:

    * `label` -- the full name, for menus, tooltips and the readout
      ("Volley trials").
    * `short` -- the chip's word, and the widest chip level is `short` plus
      the count ("Volley 11").
    * `micro` -- three or four characters, the last level before the chip is
      reduced to its glyph ("Vol").

    No level loses information: the count always survives in the tooltip and
    in the readout.
    """

    def __init__(
        self,
        id: str,
        *,
        label: str,
        short: str,
        micro: str,
        kind: str,
        track: str,
        role: str,
        default_on: bool = True,
        tip: str = "",
    ) -> None:
        self.id = id
        self.label = label
        self.short = short
        self.micro = micro
        self.kind = kind
        self.track = track
        #: The name :func:`audian.theme.annotation_color` resolves.  A layer
        #: whose rows carry their own role (explained detections take their
        #: parent pulse's) overrides this per series.
        self.role = role
        self.default_on = default_on
        self.tip = tip

    def __len__(self) -> int:
        raise NotImplementedError

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.id} n={len(self)}>"

    @property
    def t_min(self) -> float | None:
        raise NotImplementedError

    @property
    def t_max(self) -> float | None:
        raise NotImplementedError


@dataclass(frozen=True)
class PointSeries:
    """One evidence class inside a point layer, with its own sorted array.

    The split is structural because it has to survive drawing.  Predicted
    pulses -- 7 of the 908 resting rows on exp2 -- share a layer, a colour and
    a track with the observed ones, and if they shared an ARRAY they would
    also share a pixel bucket: at a 300 px budget six of the seven lose theirs
    to an observed neighbour and simply disappear, with nothing on screen to
    say so.  Separate arrays cannot lose a rare class to a common one.
    """

    #: float64, C-contiguous, ascending.  Every ``searchsorted`` downstream
    #: depends on all three.
    times: np.ndarray
    #: This series' rows, in the same order as `times`, for `describe` and the
    #: hover readout only -- one row of Python on hover, never on the draw path.
    frame: pl.DataFrame
    #: False = the fit says it is here and the recording never confirmed it.
    observed: bool = True
    #: Overrides the layer's role.  Explained detections take the hue of the
    #: pulse that explains them, so this is set per series at load.
    role: str | None = None

    def __len__(self) -> int:
        return int(self.times.size)

    def nearest(self, t: float) -> int | None:
        """Index of the row closest in time to `t`, or None when empty."""
        n = self.times.size
        if n == 0:
            return None
        i = int(np.searchsorted(self.times, t))
        if i <= 0:
            return 0
        if i >= n:
            return n - 1
        return i if (self.times[i] - t) < (t - self.times[i - 1]) else i - 1


class PointLayer(Layer):
    """A layer of instants, split into one to three evidence classes."""

    def __init__(self, id: str, series: Sequence[PointSeries], **kwargs: Any) -> None:
        super().__init__(id, kind=KIND_POINT, **kwargs)
        #: Observed classes first, so a drawing pass that stops early stops on
        #: the rare predicted class rather than on the common observed one.
        self.series: tuple[PointSeries, ...] = tuple(series)
        #: Rows this layer could not tie to the row that should explain them.
        #: Meaningful for `LAYER_DET_EXPLAINED`, where it must be 0: every
        #: explained detection is bit-identical to a matched pulse's
        #: ``detected_time_s`` (max abs difference 0.0 over all 2179 exp2
        #: rows), which is what licenses drawing it in the parent's hue.
        self.unjoined: int = 0

    def __len__(self) -> int:
        return int(sum(s.times.size for s in self.series))

    @property
    def t_min(self) -> float | None:
        times = [s.times[0] for s in self.series if s.times.size]
        return float(min(times)) if times else None

    @property
    def t_max(self) -> float | None:
        times = [s.times[-1] for s in self.series if s.times.size]
        return float(max(times)) if times else None

    def count_between(self, t0: float, t1: float) -> int:
        """True number of rows in ``[t0, t1]``, across every class.

        The count the badge and the readout report.  It is the number in the
        file, never the number of marks that survived the draw.
        """
        total = 0
        for s in self.series:
            i0 = int(np.searchsorted(s.times, t0, side="left"))
            i1 = int(np.searchsorted(s.times, t1, side="right"))
            total += max(0, i1 - i0)
        return total

    def nearest(self, t: float) -> tuple[int, int] | None:
        """``(series index, row)`` closest to `t`, or None when empty."""
        best: tuple[int, int] | None = None
        best_dt = np.inf
        for si, s in enumerate(self.series):
            i = s.nearest(t)
            if i is None:
                continue
            dt = abs(float(s.times[i]) - t)
            if dt < best_dt:
                best, best_dt = (si, i), dt
        return best

    def describe(self, s: int, i: int) -> str:
        """One line about a single row, for the readout or a tool tip."""
        series = self.series[s]
        frame = series.frame
        parts = [self.label]
        row = frame.row(i, named=True) if frame.height > i else {}
        kind = row.get("pulse_type") or row.get("event")
        if kind:
            parts.append(str(kind))
        trial = row.get("trial_number")
        if trial is not None:
            parts.append(f"trial {int(trial)}")
        treatment = row.get("treatment")
        if treatment:
            parts.append(str(treatment))
        parts.append(f"t {float(series.times[i]):.6f} s")
        resid = row.get("residual_s")
        if resid is not None and np.isfinite(resid):
            parts.append(f"resid {1e6 * float(resid):+.0f} µs")
        if not series.observed:
            # The same closing clause the old reader used, and the same
            # promise: nothing in the recording confirms this position.
            parts.append("predicted, not observed")
        return ", ".join(parts)


class SpanLayer(Layer):
    """A layer of intervals: trials, or the runs the localizer was active for."""

    def __init__(
        self,
        id: str,
        starts: np.ndarray,
        ends: np.ndarray,
        frame: pl.DataFrame,
        *,
        open_left: np.ndarray | None = None,
        open_right: np.ndarray | None = None,
        letter: str = "",
        **kwargs: Any,
    ) -> None:
        super().__init__(id, kind=KIND_SPAN, **kwargs)
        #: One character drawn at every drawn span's start edge -- ``V`` /
        #: ``B`` / ``S`` for the three treatments -- and "" for a layer that
        #: refines nothing.  It is the THIRD-TIER refinement: the colour
        #: channel is spent on the top-level kind (a trial happened here), so
        #: which treatment it was is answered by this letter instead.
        #:
        #: Per LAYER, not per row, and that is the whole reason it is viable.
        #: A trial layer is one treatment by construction, so the letter is a
        #: constant the drawing path reads once -- no per-row Python, and a
        #: merged bar that stands for several trials still carries the right
        #: letter because they were all the same treatment.
        self.letter = str(letter)
        self.starts = np.ascontiguousarray(starts, dtype=np.float64)
        self.ends = np.ascontiguousarray(ends, dtype=np.float64)
        #: How far the spans up to each row REACH:
        #: ``np.maximum.accumulate(np.maximum(ends, starts))``, computed ONCE
        #: here.  It is what makes :func:`windowing.window_spans` searchable
        #: whatever the spans do, and recomputing it per redraw would defeat
        #: the point of having a windowing primitive at all.
        #:
        #: The maximum against `starts` is for the INVERTED row -- a trial
        #: whose ``recording_ended_s`` precedes its ``recording_time_s``, which
        #: `session._build_trials` keeps as written and warns about rather than
        #: swapping.  Such a row reaches its own start, because that is where
        #: `windowing.merge_spans` puts its one-pixel bar; without the maximum
        #: `max_end` stopped at the earlier end, and the two disagreed: with
        #: start 5.0 and end 2.0 the bar was drawn at the whole-file view and
        #: vanished at every view that contained it, so the mark existed only
        #: at the zoom where it could not be inspected.
        reach = np.maximum(self.ends, self.starts)
        self.max_end = np.maximum.accumulate(reach) if reach.size else reach
        n = self.starts.size
        self.open_left = (
            np.zeros(n, dtype=bool)
            if open_left is None
            else np.asarray(open_left, bool)
        )
        self.open_right = (
            np.zeros(n, dtype=bool)
            if open_right is None
            else np.asarray(open_right, bool)
        )
        #: Computed, never assumed.  :func:`windowing.window_spans` treats
        #: `disjoint` as a promise about the arrays: pass True for spans that
        #: overlap and it can return a span that had already ended.
        self.disjoint = bool(n < 2 or np.all(self.starts[1:] >= self.ends[:-1]))
        self.frame = frame

    def __len__(self) -> int:
        return int(self.starts.size)

    @property
    def t_min(self) -> float | None:
        return float(self.starts[0]) if self.starts.size else None

    @property
    def t_max(self) -> float | None:
        return float(self.max_end[-1]) if self.max_end.size else None

    @property
    def durations(self) -> np.ndarray:
        return self.ends - self.starts

    def at(self, t: float) -> int | None:
        """Index of the span covering `t`, or None.

        Half-open, ``start <= t < end``: the instant a trial closes belongs to
        no trial, or two adjacent trials would both claim it.  When spans nest,
        the innermost -- the one that started last -- wins.
        """
        if self.starts.size == 0:
            return None
        i0 = int(np.searchsorted(self.max_end, t, side="right"))
        i1 = int(np.searchsorted(self.starts, t, side="right"))
        if i1 <= i0:
            return None
        hit = np.flatnonzero(self.ends[i0:i1] > t)
        return int(i0 + hit[-1]) if hit.size else None

    def name_of(self, i: int) -> str:
        """Which span this is, in as few characters as name it.

        Split out of `describe` for the pointer readout, which assembles
        identity, contents and bounds in that order so that elision eats the
        bounds rather than the counts.  The chip word, not the label: the
        chips beside the readout carry the same word.
        """
        row = self.frame.row(i, named=True) if self.frame.height > i else {}
        number = row.get("trial_number")
        return self.short if number is None else f"{self.short} #{int(number)}"

    def bounds_of(self, i: int) -> str:
        """Where this span runs, and whether either edge is guessed.

        An open edge is never dropped, however short the line has to be: a
        span whose start or end is not in the log is a span whose extent is
        partly invented, and that is exactly what a shorter line is tempted
        to lose.
        """
        start = float(self.starts[i])
        end = float(self.ends[i])
        text = f"{start:.3f}-{end:.3f} s"
        if self.open_left[i]:
            text = "?" + text
        if self.open_right[i]:
            text += "?"
        return text

    def describe(self, i: int, compact: bool = False) -> str:
        """One line about a single span, for the readout or a tool tip.

        `compact` drops what the line beside it already says.  The pointer
        readout appends the span's own contents -- how many marks of each
        switched-on layer fall inside it -- and that clause is the reason the
        readout exists, so it must survive the elision.  Measured in the
        running app: the full form spends 83 characters before the counts
        begin, against 719 px of row, and the counts never appear.  Compact
        uses the layer's chip word rather than its label and leaves out
        `pulses_emitted`, which the contents clause states better and which a
        tool tip still carries in full.

        What compact never drops is an open edge.  A span whose start or end
        is not in the log is a span whose extent is partly guessed, and that
        is exactly the kind of thing a shorter line is tempted to lose.
        """
        row = self.frame.row(i, named=True) if self.frame.height > i else {}
        parts = [self.short if compact else self.label]
        number = row.get("trial_number")
        if number is not None:
            parts.append(f"#{int(number)}")
        start = float(self.starts[i])
        end = float(self.ends[i])
        parts.append(f"{start:.3f}-{end:.3f} s ({end - start:.3f} s)")
        if not compact and "pulses_emitted" in self.frame.columns:
            emitted = row.get("pulses_emitted")
            # Absent and null are different sentences, and on a silence trial
            # the difference is whether the control was checked at all.
            parts.append(
                "pulses emitted not recorded"
                if emitted is None
                else f"{int(emitted)} pulses emitted"
            )
        if self.open_left[i]:
            parts.append("start not in the log")
        if self.open_right[i]:
            parts.append("end not in the log")
        return ", ".join(parts)


class StepTrack(Layer):
    """A layer of held values: what the stimulator was set to, over time.

    Several named channels share one set of change times, because the device
    writes one control row whenever anything changes.  Each channel keeps its
    own frozen range: ``tick_hz`` runs 0.5-20 Hz and ``randomness`` 0.067-1 on
    exp2, and putting them on a shared axis would flatten one of them to a
    line.  The ranges are frozen at LOAD, not per view, so the height of the
    staircase means the same thing at every zoom.
    """

    def __init__(
        self,
        id: str,
        times: np.ndarray,
        channels: Mapping[str, np.ndarray],
        frame: pl.DataFrame,
        *,
        units: Mapping[str, str] | None = None,
        t_end: float = 0.0,
        **kwargs: Any,
    ) -> None:
        super().__init__(id, kind=KIND_TRACK, **kwargs)
        self.times = np.ascontiguousarray(times, dtype=np.float64)
        self.channels: Mapping[str, np.ndarray] = dict(channels)
        self.units: Mapping[str, str] = dict(units or {})
        self.frame = frame
        #: Where the staircase stops.  Past the end of the recording no value
        #: is in force, and a line drawn there says one is.
        self.t_end = (
            float(t_end) if t_end else float(self.times[-1] if self.times.size else 0.0)
        )
        ranges: dict[str, tuple[float, float]] = {}
        for name, values in self.channels.items():
            finite = values[np.isfinite(values)]
            ranges[name] = (
                (float(finite.min()), float(finite.max()))
                if finite.size
                else (0.0, 1.0)
            )
        self.ranges: Mapping[str, tuple[float, float]] = ranges

    def __len__(self) -> int:
        return int(self.times.size)

    @property
    def t_min(self) -> float | None:
        return float(self.times[0]) if self.times.size else None

    @property
    def t_max(self) -> float | None:
        return self.t_end if self.times.size else None

    def value_at(self, name: str, t: float) -> float:
        """The value in force at `t`, or NaN when nothing is in force yet.

        NaN is the honest answer before the first change row and for a channel
        the device had not read at boot (the five ``*_us`` receiver columns are
        null on row 0 of exp2).  It is never 0: a throttle of zero and a
        throttle nobody has measured are different states of the boat.
        """
        values = self.channels.get(name)
        if values is None or self.times.size == 0:
            return float("nan")
        i = int(np.searchsorted(self.times, t, side="right")) - 1
        if i < 0:
            return float("nan")
        return float(values[i])

    def first_valid(self, name: str) -> float | None:
        """The channel's first finite value, or None when it never has one."""
        values = self.channels.get(name)
        if values is None:
            return None
        finite = np.flatnonzero(np.isfinite(values))
        return float(values[finite[0]]) if finite.size else None
