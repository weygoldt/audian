"""Reading a fakefish session bundle: the CSVs, and the layers they become.

A session is a stimulator log and a recording of what the stimulator did.  The
log is written by the device as a set of CSVs beside a ``*_metadata.toml``, and
the TOML carries the fit that maps the device's own clock onto the recording's
seconds.  This module turns that pair into arrays the browser can draw.

The reader is pure data: ``tomllib``, polars, numpy.  No Qt.  That is what lets the
hard parts -- the trust gate, the null discipline, the treatment partition --
be tested against the real exp2 bundle with no widget in the way.  The reader
is three modules, and the split is by responsibility:

* :mod:`audian.alignment` -- the fit and the provenance.  Reads the TOML, says
  how far the positions may be believed, and finds the bundle that belongs to
  a recording.  Knows nothing about rows.
* :mod:`audian.layers` -- the drawable model.  What a layer is, what shapes
  there are, and what each promises about its arrays.  Knows nothing about
  CSVs.
* this module -- reading and assembling.  Pins the CSV schemas, drops what
  cannot be placed, builds the layers, and cross-checks the bundle's claims
  against each other.

Every public name of the other two is re-exported here, so ``from
audian.session import X`` keeps working for the whole reader.

Two things this module exists to get right
------------------------------------------
**Empty means absent, never zero.**  A null is a value here.  893 pulses have a
null ``treatment`` because they belong to the ambient resting train and to no
trial at all -- the most common value in that column.  1219 exp2 detections
have a null ``source_row`` because no log row explains them, and that is the
most interesting layer in the bundle -- what the log accounts for is only part
of what the microphone heard.  Nothing is ever ``fill_null``ed and
nothing is ever ``drop_null``ed, and every column that can be null is pinned
with ``schema_overrides`` so a sparse column cannot come back as ``String`` and
turn ``records_lost > 0`` into a string comparison that is true for ``'10'``
and false for ``'9'``.

**A row that reaches no layer is still counted.**  Load compares each CSV
against the layers built from it, so a category this reader has no name for is
reported in :attr:`SessionBundle.unlayered` rather than ceasing to exist
somewhere between the file and :meth:`SessionBundle.summary`.

What is deliberately *not* here: colour, geometry, Qt, and any notion of a
window.  A layer knows what it is and what its times are; :mod:`audian.theme`
knows what colour a role is; :mod:`audian.windowing` knows how to cut an array
down to a view.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from . import alignment, windowing
from .alignment import (
    CSV_KINDS,
    KIND_CONTROLS,
    KIND_DETECTIONS,
    KIND_PULSES,
    KIND_SESSION_EVENTS,
    KIND_TRIALS,
    TRUST_OK,
    TRUST_UNVALIDATED,
    TRUST_WARN,
    Alignment,
    BundleRef,
    Integrity,
    RecordingCheck,
    SessionMeta,
    _ref_from_toml,
    find_bundle,
    find_bundles,
    verify_sha256,
)
from .layers import (
    KIND_POINT,
    KIND_SPAN,
    KIND_TRACK,
    LAYER_CONTROLS,
    LAYER_DET_EXPLAINED,
    LAYER_DET_UNEXPLAINED,
    LAYER_PULSES_RESTING,
    LAYER_PULSES_VOLLEY,
    LAYER_RUNS,
    LAYER_SESSION_EVENTS,
    LAYER_TRIALS_BASELINE,
    LAYER_TRIALS_SILENCE,
    LAYER_TRIALS_VOLLEY,
    TRACK_CTRL,
    TRACK_HEARD,
    TRACK_LOG,
    TRACK_PULSES,
    TRACK_RUNS,
    TRACK_TRIALS,
    Layer,
    PointLayer,
    PointSeries,
    SpanLayer,
    StepTrack,
)

log = logging.getLogger(__name__)

#: The whole reader's public surface, gathered here on purpose.  The split into
#: `alignment` and `layers` is about where code lives, not about breaking every
#: caller: `from audian.session import X` answers for any X it answered for
#: before the split, and `__all__` is what says so out loud rather than leaving
#: a re-export looking like an unused import.
__all__ = [
    # audian.alignment
    "TRUST_OK",
    "TRUST_WARN",
    "TRUST_UNVALIDATED",
    "KIND_PULSES",
    "KIND_TRIALS",
    "KIND_SESSION_EVENTS",
    "KIND_DETECTIONS",
    "KIND_CONTROLS",
    "CSV_KINDS",
    "Alignment",
    "Integrity",
    "RecordingCheck",
    "SessionMeta",
    "verify_sha256",
    "BundleRef",
    "find_bundles",
    "find_bundle",
    # audian.layers
    "KIND_POINT",
    "KIND_SPAN",
    "KIND_TRACK",
    "LAYER_TRIALS_VOLLEY",
    "LAYER_TRIALS_BASELINE",
    "LAYER_TRIALS_SILENCE",
    "LAYER_PULSES_RESTING",
    "LAYER_PULSES_VOLLEY",
    "LAYER_DET_EXPLAINED",
    "LAYER_DET_UNEXPLAINED",
    "LAYER_RUNS",
    "LAYER_SESSION_EVENTS",
    "LAYER_CONTROLS",
    "TRACK_TRIALS",
    "TRACK_PULSES",
    "TRACK_HEARD",
    "TRACK_RUNS",
    "TRACK_LOG",
    "TRACK_CTRL",
    "Layer",
    "PointSeries",
    "PointLayer",
    "SpanLayer",
    "StepTrack",
    # this module
    "TIME_COLUMN",
    "PULSE_TYPES",
    "TRIAL_TYPES",
    "EVENT_TYPES",
    "DETECTION_TYPES",
    "CONTROL_TYPES",
    "CSV_TYPES",
    "CONTROL_CHANNELS",
    "RUN_STARTED",
    "RUN_STOPPED",
    "DEFAULT_MATCH_TOLERANCE_S",
    "RESIDUAL_BINS",
    "RESIDUAL_WARN_FACTOR",
    "ResidualRegion",
    "ResidualStats",
    "SessionBundle",
]

#: The digest cache lives in :mod:`audian.alignment`.  This is the same dict
#: object under the name it had before the split, so a caller that clears it
#: still clears the one :func:`verify_sha256` reads.
_SHA_CACHE = alignment._SHA_CACHE


# --- the CSVs ---------------------------------------------------------------

#: The column every row is positioned by, in every CSV.  ``time_s`` -- the
#: stimulator's own clock, offset ~28.9 s and drifting 14 ppm on exp2 -- is
#: NEVER a fallback: a row placed by it would sit half a minute out and look
#: entirely plausible.  A row with no ``recording_time_s`` is dropped and
#: counted into :attr:`SessionBundle.dropped`.
TIME_COLUMN = "recording_time_s"

#: Pulses.  Six of these columns are typed wrongly by head-only inference on
#: the real bundle because the nulls sit on the LEADING rows: the first 893
#: pulses are the ambient resting train, outside any trial, so
#: ``trial_number``, ``treatment``, ``stimulus_item`` and
#: ``pulse_index_in_item`` all start null and come back as ``String``.
PULSE_TYPES: dict[str, Any] = {
    "time_s": pl.Float64,
    "recording_time_s": pl.Float64,
    "pulse_type": pl.String,
    "trial_number": pl.Int64,
    "treatment": pl.String,
    "amplitude": pl.Float64,
    "polarity": pl.Int8,
    "stimulus_item": pl.Int64,
    "pulse_index_in_item": pl.Int64,
    "sample_tick": pl.Int64,
    "source_row": pl.Int64,
    "detected_time_s": pl.Float64,
    "residual_s": pl.Float64,
    "match_status": pl.String,
}

TRIAL_TYPES: dict[str, Any] = {
    "trial_number": pl.Int64,
    "treatment": pl.String,
    "requested": pl.String,
    "was_blinded": pl.Boolean,
    "time_s": pl.Float64,
    "recording_time_s": pl.Float64,
    "ended_s": pl.Float64,
    "recording_ended_s": pl.Float64,
    "duration_s": pl.Float64,
    "stimulus_item": pl.Int64,
    "pulses_emitted": pl.Int64,
    "polarity": pl.Int8,
    "sample_tick": pl.Int64,
    "source_row": pl.Int64,
}

#: Session events.  ``records_lost`` is the trap: all 134 exp2 values are null,
#: so inference calls it ``String``.  Left unpinned, the first file that
#: actually loses records flips the dtype and ``records_lost > 0`` becomes a
#: string comparison -- true for ``'10'``, false for ``'9'`` -- on exactly the
#: column that says the log has holes in it.
EVENT_TYPES: dict[str, Any] = {
    "time_s": pl.Float64,
    "recording_time_s": pl.Float64,
    "event": pl.String,
    "file_index": pl.Int64,
    "clock_unix": pl.Int64,
    "clock_time": pl.String,
    "records_lost": pl.Int64,
    "radio_link_up": pl.Boolean,
    "trial_number": pl.Int64,
    "sample_tick": pl.Int64,
    "source_row": pl.Int64,
}

#: Detections.  ``explained_by_log`` is a real Boolean in the file and stays
#: one; ``source_row`` is null on 1219 of exp2's 3398 rows, which are the
#: detections no log row explains.
DETECTION_TYPES: dict[str, Any] = {
    "recording_time_s": pl.Float64,
    "device_time_s": pl.Float64,
    "amplitude": pl.Float64,
    "explained_by_log": pl.Boolean,
    "source_row": pl.Int64,
}

#: Controls.  The five ``*_us`` receiver columns are null on row 0 only -- at
#: boot the radio has not yet been read -- which is enough to make head-only
#: inference disagree with the rest of the file.
CONTROL_TYPES: dict[str, Any] = {
    "time_s": pl.Float64,
    "recording_time_s": pl.Float64,
    "volley_amplitude": pl.Float64,
    "randomness": pl.Float64,
    "tick_hz": pl.Float64,
    "tick_interval_s": pl.Float64,
    "throttle_pulse_us": pl.Int64,
    "trigger_pulse_us": pl.Int64,
    "randomness_pulse_us": pl.Int64,
    "amplitude_pulse_us": pl.Int64,
    "receiver_zero_us": pl.Int64,
    "sample_tick": pl.Int64,
    "source_row": pl.Int64,
}

CSV_TYPES: dict[str, dict[str, Any]] = {
    KIND_PULSES: PULSE_TYPES,
    KIND_TRIALS: TRIAL_TYPES,
    KIND_SESSION_EVENTS: EVENT_TYPES,
    KIND_DETECTIONS: DETECTION_TYPES,
    KIND_CONTROLS: CONTROL_TYPES,
}

#: Control channels the track may offer, and the unit each is measured in.  A
#: channel is only offered when it actually varies: exp2's ``volley_amplitude``
#: holds a single value (1.0) for all 1373 rows, and a flat line across the
#: whole recording costs a track row and says nothing.
CONTROL_CHANNELS: tuple[tuple[str, str], ...] = (
    ("tick_hz", "Hz"),
    ("randomness", ""),
    ("volley_amplitude", ""),
)

#: The two session-event kinds that bracket a localization run.
RUN_STARTED = "localization_started"
RUN_STOPPED = "localization_stopped"


# --- reading ----------------------------------------------------------------


#: What a matched pulse and its detection may differ by when no bundle says.
#: 0.5 ms is exp2's own ``[alignment].match_tolerance_s``; the real number is
#: read from the TOML whenever it is there, and this exists only so a bundle
#: that omits it still joins rather than silently producing 2179 orphans.
DEFAULT_MATCH_TOLERANCE_S = 5e-4


def _read(path, types: Mapping[str, Any]) -> tuple[pl.DataFrame, int, tuple[str, ...]]:
    """One CSV, pinned, sorted by recording time, with the unplaceable rows cut.

    Returns ``(frame, dropped, warnings)``.  Only the columns in `types` are
    read, and every one of them is pinned: head-only inference types six
    columns wrongly on the real bundle because the nulls sit on the LEADING
    rows, and a ``records_lost`` that comes back ``String`` turns
    ``records_lost > 0`` into a comparison that is true for ``'10'`` and false
    for ``'9'``.

    Rows with no usable `TIME_COLUMN` are counted and removed.  They are not
    an error and they are not zero -- they are rows this viewer cannot place,
    and `dropped` is what the caller reports instead of drawing them at the
    start of the file.
    """
    path = Path(path)
    problems: list[str] = []
    present = pl.scan_csv(path, n_rows=0).collect_schema().names()
    known = [c for c in types if c in present]
    missing = [c for c in types if c not in present]
    if missing:
        problems.append(f"{path.name} has no {', '.join(missing)} column(s)")
    if TIME_COLUMN not in known:
        problems.append(
            f"{path.name} has no {TIME_COLUMN}; nothing in it can be placed"
        )
        return pl.DataFrame(), 0, tuple(problems)

    frame = (
        pl.scan_csv(
            path,
            schema_overrides={c: types[c] for c in known},
            null_values=[""],
        )
        .select(known)
        .sort(TIME_COLUMN, nulls_last=True)
        .collect()
    )
    # is_finite() is null on a null, and a null time is exactly the case being
    # counted here, so the mask has to be filled before it is inverted.
    keep = frame[TIME_COLUMN].is_finite().fill_null(False)
    dropped = int(frame.height - keep.sum())
    if dropped:
        frame = frame.filter(keep)
        problems.append(
            f"{dropped} row(s) of {path.name} have no {TIME_COLUMN} and cannot be placed"
        )
    return frame, dropped, tuple(problems)


def _times(frame: pl.DataFrame, column: str = TIME_COLUMN) -> np.ndarray:
    """A C-contiguous float64 view of a time column, ready for searchsorted."""
    if frame.height == 0 or column not in frame.columns:
        return windowing.EMPTY
    return np.ascontiguousarray(frame[column].to_numpy(), dtype=np.float64)


def _floats(frame: pl.DataFrame, column: str) -> np.ndarray:
    """A numeric column as float64 with NaN for null, one value per row.

    NaN rather than a sentinel because every consumer is numpy: ``sample_tick``
    peaks at 3.0e7, well inside the 2^53 a float64 represents exactly, so
    nothing is lost by the conversion and nothing has to remember which value
    means "no value".

    A column the CSV does not carry reads as NaN on every row, at the frame's
    own height.  A writer that left ``records_lost`` out has said nothing about
    records lost, which is exactly what a null in every row says -- and a
    length-0 array is not a quieter way of saying it, it is a ValueError in
    whichever builder combines the result with a per-row mask.  That is how a
    ``session_events.csv`` with no ``records_lost`` column took the whole
    bundle load down while `_read` was already warning that the column was
    missing.
    """
    if column not in frame.columns:
        return np.full(frame.height, np.nan, dtype=np.float64)
    if frame.height == 0:
        return windowing.EMPTY
    return np.ascontiguousarray(frame[column].cast(pl.Float64).to_numpy(), np.float64)


def _flags(frame: pl.DataFrame, column: str) -> np.ndarray:
    """A nullable Boolean column as a bool array, null reading as False.

    Only ever used where False and null mean the same thing to the caller --
    ``radio_link_up`` null means the row was not a radio row at all, which is
    not a fault, and neither is an explicit True.
    """
    if frame.height == 0 or column not in frame.columns:
        return np.zeros(frame.height, dtype=bool)
    return frame[column].fill_null(False).to_numpy().astype(bool, copy=False)


def _inside(times: np.ndarray, starts: np.ndarray, ends: np.ndarray) -> np.ndarray:
    """Which of `times` fall in ``[start, end)`` of any of the spans.

    The spans are unioned first (``merge_spans`` at zero tolerance), so one
    ``searchsorted`` answers the question whatever the spans do -- overlapping,
    nested or disjoint.  Vectorised because this runs over 2187 pulses against
    36 trials at every load, and because a per-row Python loop over a partition
    check is exactly the kind of load-time cost that gets the check deleted.
    """
    if times.size == 0 or starts.size == 0:
        return np.zeros(times.size, dtype=bool)
    s, e, _, _ = windowing.merge_spans(starts, ends, 0.0)
    i = np.searchsorted(s, times, side="right") - 1
    out = np.zeros(times.size, dtype=bool)
    seen = i >= 0
    out[seen] = times[seen] < e[i[seen]]
    return out


# --- the bundle -------------------------------------------------------------


class SessionBundle:
    """Every layer of one session, loaded, cross-checked, and ready to draw.

    Loading is where the bundle's internal claims are checked against each
    other, because a claim that is only checked at draw time is a claim that
    is checked once per redraw and reported nowhere.  Everything that does not
    add up lands in :attr:`warnings` -- never in a log line nobody reads, and
    never silently repaired.  The reader will happily load a bundle whose
    partition is broken; it will not load one and say nothing about it.
    """

    def __init__(
        self,
        meta: SessionMeta,
        layers: Sequence[Layer],
        *,
        ref: BundleRef | None = None,
        warnings: Sequence[str] = (),
        dropped: Mapping[str, int] | None = None,
        unlayered: Mapping[str, int] | None = None,
        missing: Iterable[str] = (),
        recording_check: RecordingCheck | None = None,
        residuals: "ResidualStats | None" = None,
    ) -> None:
        self.meta = meta
        self.layers: tuple[Layer, ...] = tuple(layers)
        self.ref = ref
        self.warnings: tuple[str, ...] = tuple(warnings)
        self.dropped: Mapping[str, int] = dict(dropped or {})
        #: Rows a CSV holds that no layer carries, by kind.  Different from
        #: `dropped`, which is rows that could not be POSITIONED: these have a
        #: time and simply match no category this viewer knows.  Reported by
        #: `summary` so a number that went down says so on screen.
        self.unlayered: Mapping[str, int] = dict(unlayered or {})
        #: Kinds this bundle does not carry.  A kind in here has NO layer:
        #: "there is no session_events.csv" and "there are no session events"
        #: are different facts and the chip has to be able to say which.
        self.missing: frozenset[str] = frozenset(missing)
        self._check = recording_check or RecordingCheck()
        #: The fit's residual measured per region of the recording.  Never
        #: None, so a caller does not have to ask twice: a bundle with no
        #: pulses carries a `ResidualStats` with no regions.
        self.residuals: ResidualStats = residuals or ResidualStats(
            tolerance_s=meta.alignment.match_tolerance_s or DEFAULT_MATCH_TOLERANCE_S
        )
        self._by_id = {layer.id: layer for layer in self.layers}

    # -- construction --

    @classmethod
    def load(cls, ref_or_path, *, recording=None) -> "SessionBundle":
        """Read a bundle from a `BundleRef`, a metadata TOML, or a directory."""
        ref = _resolve_ref(ref_or_path)
        meta = SessionMeta.from_toml(ref.metadata_path)
        warnings: list[str] = list(meta.warnings)
        # The writer's own warnings about the fit.  They already reach `trust`,
        # which is why exp3's badge says `warn` -- but until they reached this
        # list too, the badge was the only thing that knew, and the status bar
        # said nothing at all about which two segments the fit gave up on.
        warnings.extend(
            f"the writer warned about this fit: {w}" for w in meta.alignment.warnings
        )
        dropped: dict[str, int] = {}
        frames: dict[str, pl.DataFrame] = {}

        for kind in CSV_KINDS:
            path = ref.path(kind)
            if path is None:
                continue
            frame, lost, problems = _read(path, CSV_TYPES[kind])
            frames[kind] = frame
            dropped[kind] = lost
            warnings.extend(problems)

        missing = frozenset(CSV_KINDS) - frozenset(frames)
        expected = meta.expected_rows
        for kind, frame in frames.items():
            want = expected.get(kind)
            if want is not None and want != frame.height + dropped.get(kind, 0):
                warnings.append(
                    f"[counts].rows_{kind} says {want}, "
                    f"{ref.session_id}_{kind}.csv holds "
                    f"{frame.height + dropped.get(kind, 0)}"
                )
        if not meta.integrity.complete:
            # Independent of the fit: the alignment can be perfect and the log
            # can still be missing rows that were never written down.
            warnings.append(
                "the log is incomplete: " + "; ".join(meta.integrity.reasons)
            )

        layers: list[Layer] = []
        unlayered: dict[str, int] = {}
        trials = _build_trials(frames.get(KIND_TRIALS), warnings, unlayered)
        layers.extend(trials)
        pulses = _build_pulses(frames.get(KIND_PULSES), warnings, unlayered)
        layers.extend(pulses)
        layers.extend(
            _build_detections(
                frames.get(KIND_DETECTIONS), frames.get(KIND_PULSES), meta, warnings
            )
        )
        layers.extend(_build_events(frames.get(KIND_SESSION_EVENTS), meta, warnings))
        layers.extend(_build_controls(frames.get(KIND_CONTROLS), meta, warnings))
        _check_partition(trials, pulses, frames.get(KIND_TRIALS), warnings)

        # The backstop.  Each builder above names the rows it knew it could not
        # place; this compares the file against the layers regardless, so a
        # category nobody anticipated is still counted and still reported.
        by_id = {layer.id: layer for layer in layers}
        for kind, ids in _PARTITION_OF.items():
            frame = frames.get(kind)
            if frame is None:
                continue
            carried = sum(len(by_id[i]) for i in ids if i in by_id)
            lost = frame.height - carried
            named = unlayered.get(kind, 0)
            if lost > named:
                warnings.append(
                    f"{lost - named} row(s) of {ref.session_id}_{kind}.csv are in "
                    "no layer for a reason this reader cannot name"
                )
            if lost > 0:
                unlayered[kind] = lost
            else:
                unlayered.pop(kind, None)

        residuals = _build_residuals(frames.get(KIND_PULSES), meta, warnings)

        check = RecordingCheck()
        if recording is not None:
            check = meta.check_recording(recording)
            warnings.extend(check.problems)

        return cls(
            meta,
            layers,
            ref=ref,
            warnings=warnings,
            dropped=dropped,
            unlayered=unlayered,
            missing=missing,
            recording_check=check,
            residuals=residuals,
        )

    # -- the layers --

    def __len__(self) -> int:
        return len(self.layers)

    def __iter__(self):
        return iter(self.layers)

    def __getitem__(self, layer_id: str) -> Layer:
        return self._by_id[layer_id]

    def __contains__(self, layer_id: object) -> bool:
        return layer_id in self._by_id

    def get(self, layer_id: str) -> Layer | None:
        return self._by_id.get(layer_id)

    def points(self) -> list[PointLayer]:
        return [x for x in self.layers if isinstance(x, PointLayer)]

    def spans(self) -> list[SpanLayer]:
        return [x for x in self.layers if isinstance(x, SpanLayer)]

    def tracks(self) -> list[StepTrack]:
        return [x for x in self.layers if isinstance(x, StepTrack)]

    def _selected(self, ids: Iterable[str] | None) -> list[Layer]:
        if ids is None:
            return list(self.layers)
        return [self._by_id[i] for i in ids if i in self._by_id]

    # -- trust --

    @property
    def trust(self) -> str:
        return self.meta.trust

    @property
    def recording_check(self) -> RecordingCheck:
        return self._check

    @property
    def t_min(self) -> float | None:
        times = [x.t_min for x in self.layers if x.t_min is not None]
        return min(times) if times else None

    @property
    def t_max(self) -> float | None:
        times = [x.t_max for x in self.layers if x.t_max is not None]
        return max(times) if times else None

    def summary(self) -> str:
        """One line naming what was loaded, for a status bar.

        Rows that reached no layer are counted here too.  A summary that listed
        only the survivors would report a smaller session than the one on disk
        and look entirely healthy doing it.
        """
        parts = [f"{layer.short} {len(layer)}" for layer in self.layers if len(layer)]
        stranded = sum(self.unlayered.values())
        if stranded:
            parts.append(f"{stranded} row{'' if stranded == 1 else 's'} in no layer")
        if not parts:
            return "no annotations"
        head = f"{self.meta.session_id or 'session'}: " + ", ".join(parts)
        return head if self.trust == TRUST_OK else f"{head} [{self.trust}]"

    # -- queries the viewer needs --

    def nearest(self, t: float, ids: Iterable[str] | None = None):
        """Closest mark to `t`, as ``(layer, series, row)``.

        `series` is the index into :attr:`PointLayer.series`; for a span layer
        or a track it is always 0 and `row` indexes the spans or the change
        rows.  One shape for every layer kind, so the hover readout and the
        step key do not each need a type switch.
        """
        best = None
        best_dt = np.inf
        for layer in self._selected(ids):
            for si, times in enumerate(_series_times(layer)):
                if times.size == 0:
                    continue
                i = int(np.searchsorted(times, t))
                for j in (i - 1, i):
                    if 0 <= j < times.size:
                        dt = abs(float(times[j]) - t)
                        if dt < best_dt:
                            best, best_dt = (layer, si, j), dt
        return best

    def step(self, t: float, forward: bool = True, ids: Iterable[str] | None = None):
        """First mark strictly after (or before) `t`, as ``(layer, series, row)``."""
        best = None
        best_t = np.inf if forward else -np.inf
        for layer in self._selected(ids):
            for si, times in enumerate(_series_times(layer)):
                if times.size == 0:
                    continue
                if forward:
                    i = int(np.searchsorted(times, t, side="right"))
                    if i >= times.size or times[i] >= best_t:
                        continue
                else:
                    i = int(np.searchsorted(times, t, side="left")) - 1
                    if i < 0 or times[i] <= best_t:
                        continue
                best, best_t = (layer, si, i), float(times[i])
        return best

    def spans_at(
        self, t: float, ids: Iterable[str] | None = None
    ) -> list[tuple[SpanLayer, int]]:
        """Every span covering `t`, as ``(layer, index)``.

        A list rather than one answer because a trial and a localization run
        overlap by design: the localizer keeps running while a trial plays.
        """
        out = []
        for layer in self._selected(ids):
            if isinstance(layer, SpanLayer):
                i = layer.at(t)
                if i is not None:
                    out.append((layer, i))
        return out

    def pulses_in(
        self,
        span_layer: SpanLayer,
        index: int,
        point_ids: Iterable[str] | None = None,
    ) -> dict[str, tuple[int, int, int]]:
        """Which point rows fall inside one span, as half-open slices.

        Keyed ``"<layer id>#<series index>"``, valued ``(series, i0, i1)``, one
        entry per series that has rows in the span.  The key carries the series
        because a layer's evidence classes must not be merged even here: a
        readout that said "2 pulses" over a span holding one observed and one
        predicted pulse would be reporting a measurement that was never made.

        Membership is ``start <= t < end``, the same half-open interval
        `SpanLayer.at`, `spans_at` and the load-time partition check use.  It
        has to be the same one: 19 exp2 marks land bit-exactly on a trial end,
        and a closed interval here made this method report a volley pulse
        inside the silence control while `spans_at` said the mark belonged to
        no trial and the partition check -- the one thing meant to catch
        exactly that -- stayed silent.
        """
        t0 = float(span_layer.starts[index])
        t1 = float(span_layer.ends[index])
        out: dict[str, tuple[int, int, int]] = {}
        layers = (
            self.points()
            if point_ids is None
            else [x for x in self._selected(point_ids) if isinstance(x, PointLayer)]
        )
        for layer in layers:
            for si, series in enumerate(layer.series):
                i0 = int(np.searchsorted(series.times, t0, side="left"))
                i1 = int(np.searchsorted(series.times, t1, side="left"))
                if i1 > i0:
                    out[f"{layer.id}#{si}"] = (si, i0, i1)
        return out


def _series_times(layer: Layer) -> tuple[np.ndarray, ...]:
    """Every sorted time array a layer holds, in series order."""
    if isinstance(layer, PointLayer):
        return tuple(s.times for s in layer.series)
    if isinstance(layer, SpanLayer):
        return (layer.starts,)
    if isinstance(layer, StepTrack):
        return (layer.times,)
    return ()


def _resolve_ref(ref_or_path) -> BundleRef:
    """A `BundleRef` from a ref, a ``*_metadata.toml``, or a directory."""
    if isinstance(ref_or_path, BundleRef):
        return ref_or_path
    path = Path(ref_or_path)
    if path.is_dir():
        candidates = sorted(path.glob("*_metadata.toml"))
        if len(candidates) != 1:
            raise FileNotFoundError(
                f"{path} holds {len(candidates)} session metadata files; "
                "name the one that is meant"
            )
        path = candidates[0]
    ref = _ref_from_toml(path)
    if ref is None:
        raise OSError(f"{path} is not a readable session metadata file")
    return ref


# --- layer construction, one polars pass each -------------------------------


#: Which layers a CSV's rows are divided into.  Every row that survives `_read`
#: has to land in exactly one of them, and `SessionBundle.load` compares the two
#: counts at every load: a row whose category this viewer has no name for is a
#: row that would otherwise cease to exist somewhere between the file and
#: `SessionBundle.summary`, with nothing on screen to say a number went down.
#: `LAYER_RUNS` is deliberately absent -- a localization run is derived from a
#: PAIR of session_events rows, so it does not carry one.
_PARTITION_OF: Mapping[str, tuple[str, ...]] = {
    KIND_TRIALS: (LAYER_TRIALS_VOLLEY, LAYER_TRIALS_BASELINE, LAYER_TRIALS_SILENCE),
    KIND_PULSES: (LAYER_PULSES_RESTING, LAYER_PULSES_VOLLEY),
    KIND_DETECTIONS: (LAYER_DET_EXPLAINED, LAYER_DET_UNEXPLAINED),
    KIND_SESSION_EVENTS: (LAYER_SESSION_EVENTS,),
    KIND_CONTROLS: (LAYER_CONTROLS,),
}


def _unlayered(counts: dict[str, int], kind: str, n: int) -> None:
    """Record `n` rows of `kind` that a builder recognised but could not place."""
    if n:
        counts[kind] = counts.get(kind, 0) + int(n)


#: The three treatments, and the layer each becomes.  Silence is a real
#: treatment with a real duration -- 12 trials on exp2, every one with
#: ``pulses_emitted = 0`` -- and it is loaded, drawn and counted exactly like
#: the other two.  A control condition that quietly failed to load would be
#: invisible in precisely the way that makes an experiment unreadable.
_TRIAL_LAYERS: tuple[tuple[str, str, str, str, str, str], ...] = (
    (LAYER_TRIALS_VOLLEY, "volley", "Volley trials", "Volley", "Vol", "volley"),
    (
        LAYER_TRIALS_BASELINE,
        "baseline",
        "Baseline trials",
        "Baseline",
        "Base",
        "resting",
    ),
    (LAYER_TRIALS_SILENCE, "silence", "Silence trials", "Silence", "Sil", "silence"),
)

_TRIAL_TIPS = {
    LAYER_TRIALS_VOLLEY: "trials in which a volley of stimulus pulses was played",
    LAYER_TRIALS_BASELINE: "trials in which the resting rate was left running",
    LAYER_TRIALS_SILENCE: "the control: trials in which nothing was played at all",
}


def _build_trials(
    frame: pl.DataFrame | None, warnings: list[str], unlayered: dict[str, int]
) -> list[SpanLayer]:
    """Three span layers, one per treatment.

    Rows whose ``treatment`` matches none of the three are counted into
    `unlayered` and named in `warnings`.  They belong to no layer, which is a
    fact about this viewer's vocabulary and not a fact about the session.
    """
    if frame is None:
        return []
    starts_all = _times(frame)
    ends_all = _times(frame, "recording_ended_s")
    if ends_all.size != starts_all.size:
        ends_all = starts_all.copy()
    open_right = ~np.isfinite(ends_all)
    if open_right.any():
        warnings.append(
            f"{int(open_right.sum())} trial(s) have no recording_ended_s; "
            "drawn as an instant and flagged open"
        )
        ends_all = np.where(open_right, starts_all, ends_all)
    inverted = ends_all < starts_all
    if inverted.any():
        # A writer bug.  Swapping the two silently would produce a plausible
        # bracket over a stretch of time in which nothing was running.
        warnings.append(
            f"{int(inverted.sum())} trial(s) end before they start; "
            "left as written, not swapped"
        )

    known = frame["treatment"] if "treatment" in frame.columns else None
    layers = []
    for layer_id, treatment, label, short, micro, role in _TRIAL_LAYERS:
        mask = (
            (known == treatment).fill_null(False).to_numpy()
            if known is not None
            else np.zeros(frame.height, dtype=bool)
        )
        layers.append(
            SpanLayer(
                layer_id,
                starts_all[mask],
                ends_all[mask],
                frame.filter(mask),
                open_right=open_right[mask],
                label=label,
                short=short,
                micro=micro,
                track=TRACK_TRIALS,
                role=role,
                default_on=True,
                tip=_TRIAL_TIPS[layer_id],
            )
        )
    if known is None:
        _unlayered(unlayered, KIND_TRIALS, frame.height)
        return layers
    strays = known.is_not_null() & ~known.is_in([t for _, t, *_ in _TRIAL_LAYERS])
    n = int(strays.sum())
    if n:
        names = ", ".join(sorted(set(known.filter(strays).to_list())))
        warnings.append(
            f"{n} trial(s) have an unknown treatment ({names}); "
            "in no layer and not drawn"
        )
        _unlayered(unlayered, KIND_TRIALS, n)
    blank = int(known.is_null().sum())
    if blank:
        # A blank treatment cell is not a treatment named "" and it is not
        # silence: it is a trial whose condition the writer did not record.
        # Saying nothing is how it stopped existing between the CSV and
        # summary(), which is the one thing this reader must never do.
        warnings.append(
            f"{blank} trial(s) have no treatment at all; in no layer and not drawn"
        )
        _unlayered(unlayered, KIND_TRIALS, blank)
    return layers


#: ``localization`` and ``baseline`` pulses are the same visual category and a
#: different ``pulse_type``.  The type stays in the frame and in `describe`.
_RESTING_TYPES = ("localization", "baseline")


def _build_pulses(
    frame: pl.DataFrame | None, warnings: list[str], unlayered: dict[str, int]
) -> list[PointLayer]:
    """Two point layers -- the resting train and the volleys -- each split by evidence.

    Rows whose ``pulse_type`` is neither a resting type nor ``volley`` are
    counted into `unlayered` and named in `warnings`: a pulse this viewer has
    no category for is still a pulse the stimulator fired.
    """
    if frame is None:
        return []
    seen = (
        frame["detected_time_s"].is_not_null()
        if "detected_time_s" in frame.columns
        else None
    )
    if "match_status" in frame.columns:
        observed = (frame["match_status"] == "matched").fill_null(False)
        if seen is not None:
            disagree = int((observed != seen).sum())
            if disagree:
                # Derived from one, asserted against the other.  Neither column
                # is trusted alone: match_status is a word and detected_time_s
                # is a number, and a bundle where they disagree is a bundle
                # whose writer changed one of them.
                warnings.append(
                    f"{disagree} pulse(s) disagree between match_status and "
                    "detected_time_s about whether they were observed"
                )
    elif seen is not None:
        # No match_status, but detected_time_s answers the same question and is
        # the harder evidence of the two.  Calling every pulse observed here
        # would draw a position nothing in the recording confirms with a solid
        # pen and drop describe()'s "predicted, not observed" clause -- the
        # unsafe side of a distinction spec 7.2 requires to survive.
        observed = seen
    else:
        # Neither column exists, so nothing in the bundle says what was heard.
        # All-observed is a guess, and it is named as one rather than drawn as
        # a measurement.
        observed = pl.Series([True] * frame.height, dtype=pl.Boolean)
        warnings.append(
            "the pulses CSV carries neither match_status nor detected_time_s; "
            "every pulse is read as observed on no evidence"
        )
    obs = observed.to_numpy().astype(bool, copy=False)

    kinds = frame["pulse_type"] if "pulse_type" in frame.columns else None
    groups = (
        (
            LAYER_PULSES_RESTING,
            "Resting-rate pulses",
            "Resting",
            "Rest",
            "resting",
            (
                kinds.is_in(_RESTING_TYPES).fill_null(False).to_numpy()
                if kinds is not None
                else np.ones(frame.height, dtype=bool)
            ),
            "the ambient train the animal hears between trials, "
            "localization and baseline pulses alike",
        ),
        (
            LAYER_PULSES_VOLLEY,
            "Volley pulses",
            "Volley",
            "Vol",
            "volley",
            (
                (kinds == "volley").fill_null(False).to_numpy()
                if kinds is not None
                else np.zeros(frame.height, dtype=bool)
            ),
            "the stimulus proper: 3.6x the resting amplitude, in bursts",
        ),
    )
    times_all = _times(frame)
    layers = []
    for layer_id, label, short, micro, role, mask, tip in groups:
        series = [
            PointSeries(
                times=np.ascontiguousarray(times_all[mask & obs]),
                frame=frame.filter(mask & obs),
                observed=True,
            )
        ]
        predicted = mask & ~obs
        if predicted.any():
            series.append(
                PointSeries(
                    times=np.ascontiguousarray(times_all[predicted]),
                    frame=frame.filter(predicted),
                    observed=False,
                )
            )
        layers.append(
            PointLayer(
                layer_id,
                series,
                label=label,
                short=short,
                micro=micro,
                track=TRACK_PULSES,
                role=role,
                default_on=True,
                tip=tip,
            )
        )
    if kinds is not None:
        strays = kinds.is_not_null() & ~kinds.is_in([*_RESTING_TYPES, "volley"])
        n = int(strays.sum())
        if n:
            names = ", ".join(sorted(set(kinds.filter(strays).to_list())))
            warnings.append(
                f"{n} pulse(s) have an unknown pulse_type ({names}); "
                "in no layer and not drawn"
            )
            _unlayered(unlayered, KIND_PULSES, n)
        blank = int(kinds.is_null().sum())
        if blank:
            # Unlike a null treatment on a pulse -- which is the ambient train
            # and the most common value in that column -- a null pulse_type
            # says nothing about what was fired, so there is no category to put
            # it in and no honest way to draw it.  It is counted, not dropped.
            warnings.append(
                f"{blank} pulse(s) have no pulse_type at all; in no layer and not drawn"
            )
            _unlayered(unlayered, KIND_PULSES, blank)
    return layers


def _build_detections(
    frame: pl.DataFrame | None,
    pulses: pl.DataFrame | None,
    meta: SessionMeta,
    warnings: list[str],
) -> list[PointLayer]:
    """What the microphone heard, split by whether the log explains it."""
    if frame is None:
        return []
    explained = (
        frame["explained_by_log"].fill_null(False).to_numpy().astype(bool, copy=False)
        if "explained_by_log" in frame.columns
        else np.zeros(frame.height, dtype=bool)
    )
    if "source_row" in frame.columns:
        # Two independent statements of the same fact.  A row that claims to be
        # explained and names no log row is not explained by anything.
        orphan = frame["source_row"].is_null().to_numpy().astype(bool, copy=False)
        disagree = int((orphan != ~explained).sum())
        if disagree:
            warnings.append(
                f"{disagree} detection(s) disagree between explained_by_log and "
                "source_row about whether the log explains them"
            )
    times = _times(frame)

    unexplained = PointLayer(
        LAYER_DET_UNEXPLAINED,
        [
            PointSeries(
                times=np.ascontiguousarray(times[~explained]),
                frame=frame.filter(~explained),
                observed=True,
            )
        ],
        label="Unexplained detections",
        short="Unexplained",
        # "Novel" and "the animal" were both an INTERPRETATION.  What the
        # bundle states is `explained_by_log = false` and nothing more; a fish,
        # a boat knock and a second stimulator are all consistent with it, and
        # deciding which is the reader's job, not this layer's label.
        micro="Unex",
        track=TRACK_HEARD,
        role="detection.novel",
        default_on=True,
        tip="pulses in the recording that no log row accounts for",
    )

    det_t = np.ascontiguousarray(times[explained])
    det_frame = frame.filter(explained)
    is_volley, unmatched = _parent_pulse_is_volley(det_t, pulses, meta)
    series = []
    for role, mask in (
        ("volley", is_volley & ~unmatched),
        ("resting", ~is_volley & ~unmatched),
    ):
        if mask.any():
            series.append(
                PointSeries(
                    times=np.ascontiguousarray(det_t[mask]),
                    frame=det_frame.filter(mask),
                    observed=True,
                    role=role,
                )
            )
    if unmatched.any() or not series:
        # Stated on the series rather than inherited from the layer.  The
        # layer's own role is the CHIP's colour now (see below), and a
        # detection with no pulse behind it must keep drawing in the ink that
        # means "the log does not account for this" whatever the chip shows.
        series.append(
            PointSeries(
                times=np.ascontiguousarray(det_t[unmatched]),
                frame=det_frame.filter(unmatched),
                observed=True,
                role="detection.novel",
            )
        )
    if unmatched.any():
        # Coloured ink, like the unexplained ones, and named here: a detection
        # the log claims to explain with no pulse behind it is a hole in the
        # very identity that lets the HEARD row borrow the pulse's hue.
        warnings.append(
            f"{int(unmatched.sum())} explained detection(s) have no matched pulse "
            "within the fit's match tolerance"
        )
    layer = PointLayer(
        LAYER_DET_EXPLAINED,
        series,
        label="Explained detections",
        short="Explained",
        micro="Exp",
        track=TRACK_HEARD,
        # A colour this layer ACTUALLY DRAWS.  Every series here takes its hue
        # from the pulse that explains it -- volley red, resting teal -- so a
        # layer role of `detection.novel` painted the chip in an ink no mark
        # of this layer ever uses, and painted it pixel-identical to the
        # Unexplained chip beside it.  The chips are the only legend there is;
        # two identical chips for two layers is worse than no chip at all.
        # First series wins because the series are built in a fixed order and
        # a chip has one swatch: whichever hue leads, it is a hue on screen.
        role=next((s.role for s in series if s.role), "detection.novel"),
        default_on=True,
        tip="pulses in the recording that a logged pulse accounts for",
    )
    layer.unjoined = int(unmatched.sum())
    return [unexplained, layer]


def _parent_pulse_is_volley(
    det_t: np.ndarray, pulses: pl.DataFrame | None, meta: SessionMeta
) -> tuple[np.ndarray, np.ndarray]:
    """Tie each explained detection to the pulse that explains it.

    Returns ``(is_volley, unmatched)``.  The join is a nearest-neighbour
    search on the matched pulses' ``detected_time_s`` inside the fit's own
    ``match_tolerance_s``, and on the real bundle it is exact: the 2179
    explained ``recording_time_s`` are bit-identical to the 2179 matched
    ``detected_time_s``, maximum absolute difference 0.0.  That identity is
    what licenses drawing an explained detection in its parent pulse's hue
    instead of giving it one of its own, and it is why the visible x-offset
    between a PULSES tick and its HEARD stub is the literal fit residual.

    The parent's *type* comes back as a boolean rather than as a string array:
    a numpy array of Python strings on the draw path is per-row Python by
    another name, so every string comparison in this module happens once, in
    polars, at load.
    """
    empty = np.zeros(det_t.size, dtype=bool)
    if det_t.size == 0 or pulses is None or pulses.height == 0:
        return empty, np.ones(det_t.size, dtype=bool)
    if "detected_time_s" not in pulses.columns or "pulse_type" not in pulses.columns:
        return empty, np.ones(det_t.size, dtype=bool)
    matched = pulses.filter(pl.col("detected_time_s").is_not_null()).sort(
        "detected_time_s"
    )
    if matched.height == 0:
        return empty, np.ones(det_t.size, dtype=bool)
    parent_t = np.ascontiguousarray(matched["detected_time_s"].to_numpy(), np.float64)
    parent_volley = (matched["pulse_type"] == "volley").fill_null(False).to_numpy()

    i = np.searchsorted(parent_t, det_t)
    hi = np.clip(i, 0, parent_t.size - 1)
    lo = np.clip(i - 1, 0, parent_t.size - 1)
    pick = np.where(
        np.abs(parent_t[hi] - det_t) <= np.abs(parent_t[lo] - det_t), hi, lo
    )
    tol = meta.alignment.match_tolerance_s
    if tol is None:
        tol = DEFAULT_MATCH_TOLERANCE_S
    unmatched = np.abs(parent_t[pick] - det_t) > tol
    return parent_volley[pick] & ~unmatched, unmatched


def _build_events(
    frame: pl.DataFrame | None, meta: SessionMeta, warnings: list[str]
) -> list[Layer]:
    """The localization runs, and the log line every one of them came from."""
    if frame is None:
        return []
    layers: list[Layer] = []
    events = frame["event"] if "event" in frame.columns else None
    times = _times(frame)

    if events is not None:
        edge = events.is_in([RUN_STARTED, RUN_STOPPED]).fill_null(False).to_numpy()
        edge_times = np.ascontiguousarray(times[edge])
        is_start = (events == RUN_STARTED).fill_null(False).to_numpy()[edge]
        t_last = meta.alignment.duration_s
        if t_last is None:
            t_last = float(times[-1]) if times.size else 0.0
        runs = windowing.pair_runs(edge_times, is_start, 0.0, t_last)
        source = np.flatnonzero(edge)
        for row, when, reason in runs.problems:
            original = int(source[row]) if row < source.size else int(row)
            warnings.append(
                f"localization run at {when:.3f} s (session_events row "
                f"{original}): {reason}"
            )
        run_frame = pl.DataFrame(
            {
                "recording_time_s": runs.starts,
                "recording_ended_s": runs.ends,
                "duration_s": runs.ends - runs.starts,
                "open_left": runs.open_left,
                "open_right": runs.open_right,
            }
        )
        layers.append(
            SpanLayer(
                LAYER_RUNS,
                runs.starts,
                runs.ends,
                run_frame,
                open_left=runs.open_left,
                open_right=runs.open_right,
                label="Localization runs",
                short="Runs",
                micro="Run",
                track=TRACK_RUNS,
                role="run",
                # OFF by default, on the user's ruling: the 31 exp2 runs are up
                # to 58 s each and cover 59% of the session, so switched on
                # with everything else they wash the whole overview over and
                # the layer the reader actually came for is the one that goes.
                default_on=False,
                tip="the stretches in which the localizer was driving the resting rate",
            )
        )

    lost = _floats(frame, "records_lost")
    fault = np.isfinite(lost) & (lost > 0)
    if "radio_link_up" in frame.columns:
        # Null means the row was not a radio row, which is not a fault; only an
        # explicit False is one.
        down = (frame["radio_link_up"] == False).fill_null(False).to_numpy()  # noqa: E712
        fault = fault | down.astype(bool, copy=False)
    series = [
        PointSeries(
            times=np.ascontiguousarray(times[~fault]),
            frame=frame.filter(~fault),
            observed=True,
        )
    ]
    if fault.any():
        series.append(
            PointSeries(
                times=np.ascontiguousarray(times[fault]),
                frame=frame.filter(fault),
                observed=True,
                role="fault",
            )
        )
    layers.append(
        PointLayer(
            LAYER_SESSION_EVENTS,
            series,
            label="Session events",
            short="Log",
            micro="Log",
            track=TRACK_LOG,
            role="session",
            default_on=False,
            tip="boots, clock anchors, radio link changes and run edges",
        )
    )
    return layers


def _build_controls(
    frame: pl.DataFrame | None, meta: SessionMeta, warnings: list[str]
) -> list[StepTrack]:
    """The stimulator's settings, as a hold-forward staircase."""
    if frame is None:
        return []
    times = _times(frame)
    channels: dict[str, np.ndarray] = {}
    units: dict[str, str] = {}
    flat: list[str] = []
    for name, unit in CONTROL_CHANNELS:
        if name not in frame.columns:
            continue
        values = _floats(frame, name)
        finite = values[np.isfinite(values)]
        if finite.size and float(finite.min()) == float(finite.max()):
            # One value for the whole session.  A flat line costs a track row
            # and says nothing the tooltip cannot say in words.
            flat.append(f"{name} held {finite[0]:g} throughout")
            continue
        if finite.size == 0:
            continue
        channels[name] = values
        units[name] = unit
    tip = "what the stimulator was set to, held between change rows"
    if flat:
        tip = f"{tip}; not offered: {', '.join(flat)}"
    t_end = meta.alignment.duration_s or (float(times[-1]) if times.size else 0.0)
    return [
        StepTrack(
            LAYER_CONTROLS,
            times,
            channels,
            frame,
            units=units,
            t_end=t_end,
            label="Control track",
            short="Controls",
            micro="Ctrl",
            track=TRACK_CTRL,
            role="control",
            default_on=False,
            tip=tip,
        )
    ]


# --- residuals, per region --------------------------------------------------

#: How far a region's median residual may sit outside the fit's own
#: ``match_tolerance_s`` before it is worth saying so out loud.  Ten, not two,
#: and the unit that decides it is the volley inter-pulse interval, ~4 ms: at
#: exp3's 0.5 ms tolerance a 2x gate fires at 1 ms, where every mark is still
#: on the pulse it names and merely early within it, while 10x is 5 ms, past a
#: whole interval, which is where a mark sits on the WRONG pulse and still
#: looks perfectly plausible.  Measured against the exp3 fit of 2026-08-25
#: 12:12, whose per-file medians were +0.016, +0.564, -1.421 and -38.155 ms:
#: 10x named the fourth file (76x) and left the third (2.8x) alone.  The fit
#: rewritten at 18:33 corrects for the joins and now sits within 15 us
#: everywhere, so nothing warns -- the gate is for the next fit that does not.
RESIDUAL_WARN_FACTOR = 10.0

#: Regions to cut the session into when the bundle declares no joins.  Eight
#: because the point is to catch drift the global figure hides, and a global
#: median IS one bin: exp2 at 607 s gives 75.9 s regions, comfortably more
#: than one trial and comfortably fewer than a panel of numbers.
RESIDUAL_BINS = 8


@dataclass(frozen=True)
class ResidualRegion:
    """What the fit's residual does over one stretch of the recording.

    The residual is ``detected_time_s - recording_time_s``: where the recording
    heard a pulse, minus where the fit says it should be.  Both arrays are
    already in memory, so this is one subtraction, and it is the only thing in
    this reader that says whether the marks in front of you are where they
    belong -- not whether the fit was good ON AVERAGE.
    """

    #: Half-open, ``t0 <= t < t1``, in recording seconds.
    t0: float
    t1: float
    #: What the region is: ``"file 2 of 4"`` or ``"region 2 of 8"``.
    label: str
    #: Pulses whose position is stated here, and how many of those the
    #: recording actually confirmed.  Both, because a region can have a lovely
    #: median over the 259 of its 874 pulses that matched.
    total: int
    matched: int
    #: NaN when nothing here matched -- which is an answer, not a gap.
    median_s: float
    q25_s: float
    q75_s: float

    @property
    def iqr_s(self) -> float:
        return self.q75_s - self.q25_s

    @property
    def match_fraction(self) -> float:
        return self.matched / self.total if self.total else 0.0

    def summary(self) -> str:
        """One line for a tool tip: what the marks here are worth."""
        if not self.matched:
            return (
                f"{self.label} ({self.t0:.0f}-{self.t1:.0f} s): "
                f"none of its {self.total} pulses matched the recording"
            )
        return (
            f"{self.label} ({self.t0:.0f}-{self.t1:.0f} s): "
            f"residual {1e3 * self.median_s:+.3f} ms, "
            f"IQR {1e3 * self.iqr_s:.3f} ms, "
            f"{self.matched} of {self.total} pulses matched"
        )


class ResidualStats:
    """The fit's residual measured per region, and what that is worth knowing.

    A single number for a whole session is not a promise about the region on
    screen, and exp3 is the case that shows it twice over.  Its header reports
    ``residual_median_s = 9.5e-07`` and ``match_fraction = 0.881``, both true
    and both dominated by the two files that hold 3203 of the 4652 matched
    pulses.  Per file, the fourth confirms **259 of its 874 pulses** -- under a
    third, against a session-wide 88% -- so most of what is drawn over the last
    quarter of that recording is a predicted position and nothing in the header
    says so.  An earlier fit of the same session was worse in the other
    direction: per-file medians of +0.016, +0.564, -1.421 and -38.155 ms, the
    last with an IQR reaching -134 ms, under the same one-microsecond header.

    Regions are the recording's own files when the bundle declares them, and
    `RESIDUAL_BINS` equal bins otherwise.  Computed once, at load, vectorised;
    there is deliberately no residual plot, no per-pulse overlay and no
    correction anywhere -- the answer this exists to give is "can I trust what
    I am looking at right now", and that is a number, not a panel.
    """

    def __init__(
        self,
        regions: Sequence[ResidualRegion] = (),
        *,
        tolerance_s: float = DEFAULT_MATCH_TOLERANCE_S,
        split: bool = False,
    ) -> None:
        self.regions: tuple[ResidualRegion, ...] = tuple(regions)
        #: The fit's own ``match_tolerance_s`` -- what IT called a match.  The
        #: warning threshold is a multiple of this rather than an absolute,
        #: because a fit that matched to 0.5 ms and one that matched to 50 ms
        #: are making different promises.
        self.tolerance_s = float(tolerance_s)
        #: True when the regions are the recording's files rather than bins.
        self.split = bool(split)
        self._starts = np.array([r.t0 for r in self.regions], dtype=np.float64)

    def __len__(self) -> int:
        return len(self.regions)

    def __iter__(self):
        return iter(self.regions)

    def at(self, t: float) -> ResidualRegion | None:
        """The region containing recording second `t`, or None.

        The lookup the pointer readout and the badge tool tip need: the
        residual where the reader is looking, not the residual on average.
        """
        if not self.regions:
            return None
        i = int(np.searchsorted(self._starts, t, side="right")) - 1
        if i < 0 or t >= self.regions[i].t1:
            return None
        return self.regions[i]

    @property
    def worst(self) -> ResidualRegion | None:
        """The region whose median sits furthest from zero, matched ones only."""
        seen = [r for r in self.regions if r.matched]
        return max(seen, key=lambda r: abs(r.median_s)) if seen else None

    @property
    def warnings(self) -> tuple[str, ...]:
        """Regions whose residual is far outside the fit's own tolerance.

        Folded into :attr:`SessionBundle.warnings` at load, so the reason a
        badge says `warn` is readable instead of being a colour.
        """
        limit = RESIDUAL_WARN_FACTOR * self.tolerance_s
        out = []
        for region in self.regions:
            if not region.matched or not np.isfinite(region.median_s):
                continue
            if abs(region.median_s) <= limit:
                continue
            out.append(
                f"{region.label} ({region.t0:.0f}-{region.t1:.0f} s) sits "
                f"{1e3 * region.median_s:+.3f} ms from the fit, "
                f"{abs(region.median_s) / self.tolerance_s:.0f}x its "
                f"{1e3 * self.tolerance_s:.3f} ms match tolerance "
                f"({region.matched} of {region.total} pulses matched)"
            )
        return tuple(out)


def _region_edges(meta: SessionMeta, times: np.ndarray) -> tuple[np.ndarray, bool]:
    """Where to cut the session for residual statistics, and whether by file.

    The files when the bundle declares them, because a join is where the
    recorder lost time and therefore exactly where the fit is most likely to
    stop holding: exp3's three joins are +32, +32 and -120 ms, and its residual
    steps at every one of them.  Equal bins otherwise, which is the same
    measurement with an arbitrary grid instead of a meaningful one.
    """
    joins = meta.alignment.join_times_s
    end = meta.alignment.duration_s
    if end is None:
        end = float(times[-1]) if times.size else 0.0
    if joins:
        return np.array((0.0, *joins, max(end, joins[-1])), dtype=np.float64), True
    start = 0.0
    if end <= start:
        end = float(times[-1]) if times.size else 1.0
    return np.linspace(start, max(end, start + 1e-9), RESIDUAL_BINS + 1), False


def _build_residuals(
    frame: pl.DataFrame | None, meta: SessionMeta, warnings: list[str]
) -> ResidualStats:
    """Residual median and spread per region, in one pass over two columns.

    One subtraction and one ``searchsorted`` over arrays the bundle already
    holds, then ``np.percentile`` per region -- at most `RESIDUAL_BINS` numpy
    calls, no Python per row.  Measured on exp3's 5281 pulses over four
    regions: 0.39 ms, once, at load.
    """
    tolerance = meta.alignment.match_tolerance_s or DEFAULT_MATCH_TOLERANCE_S
    if frame is None or frame.height == 0:
        return ResidualStats(tolerance_s=tolerance)
    if (
        "recording_time_s" not in frame.columns
        or "detected_time_s" not in frame.columns
    ):
        return ResidualStats(tolerance_s=tolerance)

    rec = _floats(frame, "recording_time_s")
    det = _floats(frame, "detected_time_s")
    placed = np.isfinite(rec)
    # A pulse the fit could not place has no region and no residual.  It is
    # already counted as a predicted mark by its layer; counting it here as an
    # unmatched pulse of some region would blame a region for it.
    rec, det = rec[placed], det[placed]
    edges, split = _region_edges(meta, np.sort(rec))
    if edges.size < 2:
        return ResidualStats(tolerance_s=tolerance)

    residual = det - rec
    matched = np.isfinite(residual)
    # Vectorised region assignment: one searchsorted for the whole file, then
    # one boolean pass per region.  `clip` keeps a pulse past the fit's own
    # idea of the recording in the last region rather than dropping it.
    where = np.clip(np.searchsorted(edges[1:-1], rec, side="right"), 0, edges.size - 2)
    n = edges.size - 1
    regions = []
    for i in range(n):
        here = where == i
        total = int(here.sum())
        hit = here & matched
        count = int(hit.sum())
        if count:
            q25, median, q75 = (
                float(x) for x in np.percentile(residual[hit], (25.0, 50.0, 75.0))
            )
        else:
            q25 = median = q75 = float("nan")
        regions.append(
            ResidualRegion(
                t0=float(edges[i]),
                t1=float(edges[i + 1]),
                label=f"{'file' if split else 'region'} {i + 1} of {n}",
                total=total,
                matched=count,
                median_s=median,
                q25_s=q25,
                q75_s=q75,
            )
        )
    stats = ResidualStats(regions, tolerance_s=tolerance, split=split)
    warnings.extend(stats.warnings)
    return stats


def _check_partition(
    trials: Sequence[SpanLayer],
    pulses: Sequence[PointLayer],
    trial_frame: pl.DataFrame | None,
    warnings: list[str],
) -> None:
    """The claim that lets one hue serve a treatment's span and its pulses.

    A volley pulse is inside a volley trial, a baseline pulse is inside a
    baseline trial, a localization pulse is inside no trial at all, and a
    silence trial contains no pulse of any kind.  Measured on exp2: 0 of 893
    localization pulses in any span, 15 of 15 baseline pulses in a baseline
    span, 1279 of 1279 volley pulses in a volley span, 0 pulses in any of the
    12 silence spans, and ``pulses_emitted == 0`` on every one of those 12.

    The whole hue-sharing scheme starts lying the moment that stops holding --
    a red bracket over a train of teal pulses says a volley played and it did
    not -- and a comment cannot notice.  So it is checked here, vectorised,
    at every load, and a violation is named in :attr:`SessionBundle.warnings`.
    """
    by_id = {layer.id: layer for layer in trials}
    volley = by_id.get(LAYER_TRIALS_VOLLEY)
    baseline = by_id.get(LAYER_TRIALS_BASELINE)
    silence = by_id.get(LAYER_TRIALS_SILENCE)
    if volley is None or baseline is None or silence is None or not pulses:
        return

    all_starts = np.concatenate([layer.starts for layer in trials])
    all_ends = np.concatenate([layer.ends for layer in trials])
    order = np.argsort(all_starts, kind="stable")
    all_starts, all_ends = all_starts[order], all_ends[order]

    frames = {layer.id: layer for layer in pulses}
    resting = frames.get(LAYER_PULSES_RESTING)
    volley_pulses = frames.get(LAYER_PULSES_VOLLEY)

    if resting is not None:
        for series in resting.series:
            kinds = (
                series.frame["pulse_type"]
                if "pulse_type" in series.frame.columns
                else None
            )
            if kinds is None:
                continue
            loc = (kinds == "localization").fill_null(False).to_numpy()
            stray = int(_inside(series.times[loc], all_starts, all_ends).sum())
            if stray:
                warnings.append(
                    f"{stray} localization pulse(s) fall inside a trial; "
                    "the resting train is supposed to be the between-trials rhythm"
                )
            base = (kinds == "baseline").fill_null(False).to_numpy()
            outside = int(
                (~_inside(series.times[base], baseline.starts, baseline.ends)).sum()
            )
            if outside:
                warnings.append(
                    f"{outside} baseline pulse(s) fall outside every baseline trial"
                )
    if volley_pulses is not None:
        for series in volley_pulses.series:
            outside = int((~_inside(series.times, volley.starts, volley.ends)).sum())
            if outside:
                warnings.append(
                    f"{outside} volley pulse(s) fall outside every volley trial"
                )

    every_pulse = np.concatenate(
        [s.times for layer in pulses for s in layer.series] or [windowing.EMPTY]
    )
    every_pulse.sort()
    intruders = int(_inside(every_pulse, silence.starts, silence.ends).sum())
    if intruders:
        warnings.append(
            f"{intruders} pulse(s) fall inside a silence trial; "
            "the control condition is not silent"
        )
    if trial_frame is not None and "pulses_emitted" in silence.frame.columns:
        emitted = silence.frame["pulses_emitted"]
        loud = int((emitted != 0).fill_null(False).sum())
        if loud:
            warnings.append(f"{loud} silence trial(s) report a non-zero pulses_emitted")
        # A null pulses_emitted is not a zero.  Filling it certified the control
        # condition on a measurement that was never written down, which is the
        # one claim in this bundle nothing else can re-derive: no pulse layer
        # can prove a trial emitted nothing, it can only fail to find one.
        unknown = int(emitted.is_null().sum())
        if unknown:
            warnings.append(
                f"{unknown} silence trial(s) do not report pulses_emitted at all; "
                "the control condition is unverified, not verified as silent"
            )
