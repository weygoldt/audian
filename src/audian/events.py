"""Event annotations read from a CSV, backed by polars.

This is the data half of the annotation feature: it knows how to read an
alignment file, what its header promises, and how to hand a viewer the
events that fall inside a time window.  It draws nothing and imports no Qt,
so it can be exercised without a display.

The file format
---------------
An alignment file is a CSV with a ``#key=value`` metadata block on top::

    #fakefish-align
    #recording=DR0000_0087.wav
    #recording_rate_hz=48000
    #recording_channel=0
    #scale=1.00001412677
    #offset_s=28.9354456
    #validated=1
    #seq,tick,event,trial,t_log_s,...      <- commented column line
    seq,tick,event,trial,t_log_s,...       <- the real column line
    9,2543499,LOC,,50.86998,79.806144,...

`polars` reads the rows directly with ``comment_prefix='#'``; the header is
parsed separately by :meth:`AlignmentHeader.parse`, which is the only place
in this module that looks at the file line by line -- and it stops at the
first row.

Of the ten columns only ``t_rec_s`` positions anything: it is seconds from
frame 0 of the recording, present on every row.  Everything else is either
provenance (``tick``, ``t_log_s``, ``offset_s``, ``scale``) or the evidence
behind one row (``t_det_s``, ``resid_s``).

Two things this module refuses to lose
--------------------------------------
1. **Whether the fit was validated.**  Every ``t_rec_s`` is downstream of the
   scale/offset fit in the header.  If that fit is wrong, every annotation is
   in the wrong place *and still looks fine*, so an unvalidated header has to
   survive all the way to the pen that draws the line.  See
   :attr:`AlignmentHeader.trust`.
2. **Whether a row was observed or predicted.**  A ``matched`` row was seen in
   the recording -- ``t_det_s`` holds where.  An ``unmatched`` or ``outside``
   row was not: its ``t_rec_s`` is what the fit *predicts*, which is exactly
   why ``t_det_s`` is empty.  The two are split into different
   :class:`EventClass` objects with different :attr:`EventClass.kind` so they
   cannot be drawn the same way by accident.

Scale
-----
Sessions run to hours and hundreds of thousands of rows, so neither loading
nor drawing may touch a row from Python.  Loading is a lazy polars scan with
a projection push-down, one ``partition_by`` and one ``to_numpy()`` per
column.  Drawing goes through :meth:`EventClass.window`, which is two
``searchsorted`` calls and one vectorised pixel-bucket pass.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, Mapping, Optional, Sequence

import numpy as np
import polars as pl


log = logging.getLogger(__name__)


#: Columns an alignment file is expected to carry, with the dtype each one
#: must have.  Passed to polars as ``schema_overrides``, which -- unlike a
#: full ``schema`` -- tolerates extra or missing columns instead of failing
#: the read.  It exists for ``trial``, ``t_det_s`` and ``resid_s``: those are
#: empty on most rows, so head-only inference types them as ``String`` and
#: every later comparison silently becomes a string comparison.
COLUMN_TYPES: dict[str, type[pl.DataType]] = {
    "seq": pl.Int64,
    "tick": pl.Int64,
    "event": pl.String,
    "trial": pl.Int64,
    "t_log_s": pl.Float64,
    "t_rec_s": pl.Float64,
    "offset_s": pl.Float64,
    "t_det_s": pl.Float64,
    "resid_s": pl.Float64,
    "status": pl.String,
}

#: The only columns a viewer reads.  Everything else is provenance and is
#: left in the file: naming them here lets the lazy scan skip parsing them.
USED_COLUMNS: tuple[str, ...] = (
    "seq",
    "event",
    "trial",
    "t_rec_s",
    "t_det_s",
    "resid_s",
    "status",
)

#: The column that positions an annotation.  Nothing works without it.
TIME_COLUMN = "t_rec_s"

#: Event labels in the order they should be listed and coloured.  Anything
#: else in the file is kept and appended after these, in first-seen order --
#: an unknown label is data, not an error.
EVENT_ORDER: tuple[str, ...] = ("LOC", "BASE", "VOLLEY", "MARKER")

#: Marker palette index per known event label (see `theme.marker_color`).
#:
#: Picked against the *waveform* palette, not just against each other.  An
#: annotation is drawn on top of a trace, so a hue near ``trace.raw`` (cyan),
#: ``trace.filtered`` (amber) or ``trace.envelope`` (pink) disappears into the
#: line it is annotating -- and switching the high-pass on, which repaints
#: every trace amber, must not be able to hide an event class.  That rules out
#: the palette's blue (6), orange (7), pink (5) and teal (3).
#:
#: LOC gets the loudest of what is left: it is the sparse event a reader is
#: usually hunting for.  VOLLEY gets the calmest, because a volley is a burst
#: of a hundred lines and a burst of red reads as an alarm.
EVENT_COLOR_INDEX: dict[str, int] = {
    "LOC": 0,  # red
    "BASE": 1,  # green
    "VOLLEY": 2,  # purple
    "MARKER": 4,  # yellow
}

#: Palette indices left for labels not in `EVENT_COLOR_INDEX`, least likely
#: to collide with a trace colour first.
SPARE_COLOR_INDEX: tuple[int, ...] = (3, 5, 7, 6)

#: Statuses whose ``t_rec_s`` was *observed* in the recording.
MEASURED_STATUSES: frozenset[str] = frozenset({"matched"})

#: Order statuses are listed in; unknown ones are appended.
STATUS_ORDER: tuple[str, ...] = ("matched", "unmatched", "outside")

#: Bucket for a missing event label or status.  Never dropped: a row with
#: no label is still a row, and hiding it would be a silent edit.
UNKNOWN = "?"

#: An event whose time was measured against the recording.
KIND_MEASURED = "measured"

#: An event whose time is what the fit predicts, with nothing in the
#: recording to confirm it.  Never draw one as if it had been seen.
KIND_PREDICTED = "predicted"


# --- header -----------------------------------------------------------------

#: The alignment is validated and carries no warnings.
TRUST_OK = "ok"

#: Validated, but the writer recorded warnings about the fit.
TRUST_WARN = "warn"

#: ``validated=0``, or no ``validated`` key at all.  Positions on screen are
#: not to be believed.
TRUST_UNVALIDATED = "unvalidated"


def _as_float(value: Optional[str]) -> Optional[float]:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _as_int(value: Optional[str]) -> Optional[int]:
    try:
        return int(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


#: Values of ``validated=`` that count as a claim of validation.  Anything
#: else -- ``0``, ``false``, an empty value, a word nobody anticipated -- is
#: *not* such a claim, and the unrecognised case has to fall on the cautious
#: side: an alignment is shown as trustworthy only when the file says so in
#: as many words.
TRUTHY: frozenset[str] = frozenset({"1", "true", "yes", "y", "t", "on"})


def _as_bool(value: Optional[str]) -> Optional[bool]:
    """Tri-state: True, False, or None when the key was absent."""
    if value is None:
        return None
    return value.strip().lower() in TRUTHY


def _as_warnings(value: Optional[str]) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(w.strip() for w in value.split(";") if w.strip())


@dataclass(frozen=True)
class AlignmentHeader:
    """The ``#key=value`` block on top of an alignment file.

    Every field is optional: a file written by another tool may carry none of
    them, and a missing key must read as *unknown* rather than as a default
    that happens to look reassuring.  That is why `validated` is a tri-state
    ``True``/``False``/``None`` and why `trust` treats ``None`` exactly like
    ``False``.
    """

    values: Mapping[str, str] = field(default_factory=dict)

    #: Name of the recording the fit was made against.
    recording: Optional[str] = None
    recording_rate_hz: Optional[float] = None
    #: Channel the fit was made on.  The fit is per channel, so this is read
    #: from the file rather than assumed.
    recording_channel: Optional[int] = None
    recording_sha256: Optional[str] = None

    #: The fit itself.  ``t_rec_s = scale * t_log_s + offset_s``.
    scale: Optional[float] = None
    offset_s: Optional[float] = None
    drift_ppm: Optional[float] = None

    #: ``True`` for ``validated=1``, ``False`` for ``validated=0``, ``None``
    #: when the key is absent.
    validated: Optional[bool] = None
    validation_warnings: tuple[str, ...] = ()
    fit_warnings: tuple[str, ...] = ()

    @classmethod
    def parse(cls, lines: Iterable[str]) -> "AlignmentHeader":
        """Read the leading ``#`` block, stopping at the first data line."""
        values: dict[str, str] = {}
        for line in lines:
            line = line.strip()
            if not line:
                continue
            if not line.startswith("#"):
                break
            body = line[1:].strip()
            if "=" not in body:
                # the marker line ('#fakefish-align') and the commented
                # column line; neither is a key=value pair
                continue
            key, _, value = body.partition("=")
            values[key.strip()] = value.strip()
        validated = values.get("validated")
        return cls(
            values=values,
            recording=values.get("recording") or None,
            recording_rate_hz=_as_float(values.get("recording_rate_hz")),
            recording_channel=_as_int(values.get("recording_channel")),
            recording_sha256=values.get("recording_sha256") or None,
            scale=_as_float(values.get("scale")),
            offset_s=_as_float(values.get("offset_s")),
            drift_ppm=_as_float(values.get("drift_ppm")),
            validated=_as_bool(validated),
            validation_warnings=_as_warnings(values.get("validation_warnings")),
            fit_warnings=_as_warnings(values.get("fit_warnings")),
        )

    @classmethod
    def from_file(cls, path) -> "AlignmentHeader":
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return cls.parse(f)

    @property
    def warnings(self) -> tuple[str, ...]:
        return self.validation_warnings + self.fit_warnings

    @property
    def trust(self) -> str:
        """How far the positions in this file may be believed.

        One of `TRUST_OK`, `TRUST_WARN`, `TRUST_UNVALIDATED`.  A file with no
        ``validated`` key at all is *unvalidated*, not *fine*: nothing in it
        says the fit was ever checked.
        """
        if not self.validated:
            return TRUST_UNVALIDATED
        if self.warnings:
            return TRUST_WARN
        return TRUST_OK

    @property
    def is_validated(self) -> bool:
        return bool(self.validated)

    def fit_summary(self) -> str:
        """One line describing the fit, for a tool tip or a metadata pane."""
        parts = []
        if self.scale is not None:
            parts.append(f"scale {self.scale:.9f}")
        if self.offset_s is not None:
            parts.append(f"offset {self.offset_s:.6f} s")
        if self.drift_ppm is not None:
            parts.append(f"drift {self.drift_ppm:+.3f} ppm")
        if self.recording_channel is not None:
            parts.append(f"fitted on channel {self.recording_channel}")
        return ", ".join(parts)


# --- one drawable class of events -------------------------------------------


class EventClass:
    """All events sharing one ``event`` label and one ``status``.

    This is the unit the UI toggles and the unit the overlay draws, because
    the two things a reader must be able to tell apart -- *what* an event is
    and *whether it was actually seen* -- are exactly ``event`` and
    ``status``.

    `times` is sorted, which is what makes :meth:`window` a pair of binary
    searches instead of a scan.
    """

    __slots__ = (
        "event",
        "status",
        "color_index",
        "times",
        "seq",
        "trial",
        "t_det",
        "resid",
    )

    def __init__(
        self,
        event: str,
        status: str,
        color_index: int,
        times: np.ndarray,
        seq: Optional[np.ndarray] = None,
        trial: Optional[np.ndarray] = None,
        t_det: Optional[np.ndarray] = None,
        resid: Optional[np.ndarray] = None,
    ):
        self.event = event
        self.status = status
        self.color_index = color_index
        self.times = np.ascontiguousarray(times, dtype=np.float64)
        self.seq = seq
        self.trial = trial
        self.t_det = t_det
        self.resid = resid

    def __len__(self) -> int:
        return int(self.times.size)

    def __repr__(self) -> str:
        return f"<EventClass {self.key} n={len(self)}>"

    @property
    def key(self) -> tuple[str, str]:
        return (self.event, self.status)

    @property
    def kind(self) -> str:
        """`KIND_MEASURED` if these events were seen, else `KIND_PREDICTED`."""
        return KIND_MEASURED if self.status in MEASURED_STATUSES else KIND_PREDICTED

    @property
    def measured(self) -> bool:
        return self.kind == KIND_MEASURED

    @property
    def label(self) -> str:
        return f"{self.event} · {self.status}"

    def count_between(self, t0: float, t1: float) -> int:
        """How many events fall in ``[t0, t1]``."""
        i0 = int(np.searchsorted(self.times, t0, side="left"))
        i1 = int(np.searchsorted(self.times, t1, side="right"))
        return i1 - i0

    def window(self, t0: float, t1: float, pixels: int = 0) -> tuple[np.ndarray, int]:
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
        """
        i0 = int(np.searchsorted(self.times, t0, side="left"))
        i1 = int(np.searchsorted(self.times, t1, side="right"))
        total = i1 - i0
        if total <= 0:
            return _EMPTY, 0
        times = self.times[i0:i1]
        span = t1 - t0
        if pixels > 0 and total > pixels and span > 0:
            bucket = ((times - t0) * (pixels / span)).astype(np.int64, copy=False)
            keep = np.empty(bucket.size, dtype=bool)
            keep[0] = True
            np.not_equal(bucket[1:], bucket[:-1], out=keep[1:])
            times = times[keep]
        return times, total

    def nearest(self, t: float) -> Optional[int]:
        """Index of the event closest in time to `t`, or None if empty."""
        n = self.times.size
        if n == 0:
            return None
        i = int(np.searchsorted(self.times, t))
        if i <= 0:
            return 0
        if i >= n:
            return n - 1
        return i if (self.times[i] - t) < (t - self.times[i - 1]) else i - 1

    def describe(self, index: int) -> str:
        """One line about a single event, for a status bar or a tool tip."""
        parts = [f"{self.event} {self.status}"]
        if self.seq is not None and index < self.seq.size:
            seq = self.seq[index]
            if not np.isnan(seq):
                parts.append(f"seq {int(seq)}")
        if self.trial is not None and index < self.trial.size:
            trial = self.trial[index]
            if not np.isnan(trial):
                parts.append(f"trial {int(trial)}")
        parts.append(f"t {self.times[index]:.6f} s")
        if self.measured and self.resid is not None and index < self.resid.size:
            resid = self.resid[index]
            if not np.isnan(resid):
                parts.append(f"resid {1e6 * resid:+.0f} µs")
        if not self.measured:
            parts.append("predicted, not observed")
        return ", ".join(parts)


_EMPTY = np.empty(0, dtype=np.float64)


# --- the table --------------------------------------------------------------


class EventTable:
    """Every event in one alignment file, split into drawable classes."""

    def __init__(
        self,
        classes: Sequence[EventClass],
        header: Optional[AlignmentHeader] = None,
        path=None,
        dropped: int = 0,
    ):
        self.classes: list[EventClass] = list(classes)
        self.header = header or AlignmentHeader()
        self.path = Path(path) if path is not None else None
        #: rows with no usable ``t_rec_s``; nothing can position them
        self.dropped = int(dropped)
        self._by_key = {c.key: c for c in self.classes}

    # -- construction --

    @classmethod
    def from_csv(cls, path, time_column: str = TIME_COLUMN) -> "EventTable":
        """Read an alignment CSV.

        Lazy, and for two reasons that both matter at 500 000 rows:

        * **Projection push-down.**  Only the seven columns a viewer reads are
          selected, so ``tick``, ``t_log_s`` and ``offset_s`` are never
          parsed.  It also means a column this module has never heard of
          cannot break the read -- it is not touched.
        * **A pinned schema instead of a full-file scan.**  `COLUMN_TYPES`
          types every column that is actually read, so type inference has
          nothing left to decide and may look at the head only.  Inferring
          from the whole file instead -- the usual fix for the sparse
          ``trial`` column coming back as ``String`` -- costs 956 ms here
          against 83 ms, all of it spent typing columns that are then thrown
          away.

        Rows with no ``t_rec_s`` are dropped: nothing can position them.  They
        are counted rather than ignored, and `dropped` is reported to the user.
        """
        path = Path(path)
        header = AlignmentHeader.from_file(path)
        scan = pl.scan_csv(
            path,
            comment_prefix="#",
            has_header=True,
            schema_overrides=COLUMN_TYPES,
            null_values=[""],
        )
        available = set(scan.collect_schema().names())
        if time_column not in available:
            raise ValueError(
                f"{path}: no '{time_column}' column -- found {sorted(available)}"
            )
        wanted = [c for c in USED_COLUMNS if c in available]
        if time_column not in wanted:
            wanted.append(time_column)
        # nulls_last puts the unpositionable rows in one block at the end, so
        # they are dropped by a slice rather than by a second pass over the file
        frame = scan.select(wanted).sort(time_column, nulls_last=True).collect()
        dropped = frame[time_column].null_count()
        if dropped:
            frame = frame.head(frame.height - dropped)
        return cls._from_frame(
            frame, header, path, dropped=dropped, time_column=time_column
        )

    @classmethod
    def _from_frame(
        cls,
        frame: pl.DataFrame,
        header: AlignmentHeader,
        path=None,
        dropped: int = 0,
        time_column: str = TIME_COLUMN,
    ) -> "EventTable":
        columns = set(frame.columns)
        # An event with no label, or no status, is still an event: it is
        # given the "?" bucket rather than being dropped, so a malformed row
        # shows up in the UI as a class nobody can name instead of silently
        # not being there.
        group_on = [c for c in ("event", "status") if c in columns]
        for name in group_on:
            frame = frame.with_columns(pl.col(name).fill_null(UNKNOWN))

        if group_on:
            groups = frame.partition_by(group_on, as_dict=True, include_key=True)
        else:
            # a file with neither column at all: one anonymous class, and it
            # is called observed, because nothing says otherwise
            groups = {(UNKNOWN, STATUS_ORDER[0]): frame}

        def column(part: pl.DataFrame, name: str) -> Optional[np.ndarray]:
            """One column as float64, or None when the file does not carry it."""
            if name not in columns:
                return None
            return part[name].cast(pl.Float64, strict=False).to_numpy()

        classes: list[EventClass] = []
        colors = _color_assigner()
        for key, part in groups.items():
            if not isinstance(key, tuple):
                key = (key,)
            fields = dict(zip(group_on, (str(k) for k in key)))
            event = fields.get("event", UNKNOWN)
            status = fields.get("status", STATUS_ORDER[0])
            classes.append(
                EventClass(
                    event=event,
                    status=status,
                    color_index=colors(event),
                    times=part[time_column].to_numpy(),
                    seq=column(part, "seq"),
                    trial=column(part, "trial"),
                    t_det=column(part, "t_det_s"),
                    resid=column(part, "resid_s"),
                )
            )
        classes.sort(key=_class_order)
        return cls(classes, header, path, dropped)

    # -- container --

    def __len__(self) -> int:
        return len(self.classes)

    def __iter__(self) -> Iterator[EventClass]:
        return iter(self.classes)

    def __getitem__(self, key) -> EventClass:
        if isinstance(key, int):
            return self.classes[key]
        return self._by_key[key]

    def __contains__(self, key) -> bool:
        return key in self._by_key

    def get(self, key) -> Optional[EventClass]:
        return self._by_key.get(key)

    @property
    def keys(self) -> list[tuple[str, str]]:
        return [c.key for c in self.classes]

    @property
    def name(self) -> str:
        return self.path.name if self.path is not None else "events"

    # -- summary --

    @property
    def n_events(self) -> int:
        return int(sum(len(c) for c in self.classes))

    @property
    def n_predicted(self) -> int:
        return int(sum(len(c) for c in self.classes if not c.measured))

    @property
    def t_min(self) -> Optional[float]:
        times = [c.times[0] for c in self.classes if len(c)]
        return min(times) if times else None

    @property
    def t_max(self) -> Optional[float]:
        times = [c.times[-1] for c in self.classes if len(c)]
        return max(times) if times else None

    @property
    def trust(self) -> str:
        return self.header.trust

    def summary(self) -> str:
        counts = ", ".join(f"{c.label} {len(c)}" for c in self.classes if len(c))
        return f"{self.n_events} events ({counts})" if counts else "no events"

    # -- queries the viewer needs --

    def nearest(self, t: float, keys: Optional[Iterable] = None):
        """Closest event to `t` across `keys`, as ``(EventClass, index)``.

        Used for the "what is under the pointer" readout and for stepping from
        one annotation to the next, both of which have to stay O(log n).
        """
        selected = (
            self.classes
            if keys is None
            else [self._by_key[k] for k in keys if k in self._by_key]
        )
        best = None
        best_dt = np.inf
        for cls_ in selected:
            i = cls_.nearest(t)
            if i is None:
                continue
            dt = abs(cls_.times[i] - t)
            if dt < best_dt:
                best, best_dt = (cls_, i), dt
        return best

    def step(self, t: float, forward: bool = True, keys: Optional[Iterable] = None):
        """First event strictly after (or before) `t`, as ``(EventClass, index)``."""
        selected = (
            self.classes
            if keys is None
            else [self._by_key[k] for k in keys if k in self._by_key]
        )
        best = None
        best_t = np.inf if forward else -np.inf
        for cls_ in selected:
            times = cls_.times
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
            best, best_t = (cls_, i), times[i]
        return best

    def matches_recording(self, file_path) -> Optional[bool]:
        """Whether this file's header names `file_path` as its recording.

        ``None`` when the header says nothing, which is not the same as a
        mismatch.  Only the file *name* is compared: the fit is stored beside
        the recording and both get copied around together, so the directory
        says nothing, while a different name almost always means a different
        recording -- and then every position on screen is wrong.
        """
        if not self.header.recording:
            return None
        return Path(self.header.recording).name == Path(file_path).name


def _color_assigner():
    """Stable palette index per event label, known labels first."""
    assigned: dict[str, int] = {}
    spare = iter(SPARE_COLOR_INDEX)

    def assign(event: str) -> int:
        if event not in assigned:
            index = EVENT_COLOR_INDEX.get(event)
            if index is None:
                index = next(spare, len(assigned))
            assigned[event] = index
        return assigned[event]

    return assign


def _class_order(cls_: EventClass) -> tuple:
    event = (
        EVENT_ORDER.index(cls_.event) if cls_.event in EVENT_ORDER else len(EVENT_ORDER)
    )
    status = (
        STATUS_ORDER.index(cls_.status)
        if cls_.status in STATUS_ORDER
        else len(STATUS_ORDER)
    )
    return (event, cls_.event, status, cls_.status)


def find_alignment(file_path) -> Optional[Path]:
    """An alignment file sitting beside `file_path` that names it.

    Looks for ``<recording>.alignment.csv``, ``<recording>-alignment.csv`` and
    a plain ``alignment.csv`` in the recording's own directory, and accepts
    one only if its ``#recording=`` header names this recording.  A stray
    ``alignment.csv`` from a neighbouring experiment is exactly the mistake
    that would put every annotation in the wrong place, so the name check is
    not optional.
    """
    path = Path(file_path)
    folder = path.parent
    candidates = [
        folder / f"{path.stem}.alignment.csv",
        folder / f"{path.stem}-alignment.csv",
        folder / "alignment.csv",
    ]
    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            header = AlignmentHeader.from_file(candidate)
        except OSError:
            continue
        if header.recording and Path(header.recording).name == path.name:
            return candidate
    return None
