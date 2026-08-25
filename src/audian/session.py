"""Reading a fakefish session bundle: the log, and how far it may be believed.

A session is a stimulator log and a recording of what the stimulator did.  The
log is written by the device as a set of CSVs beside a ``*_metadata.toml``, and
the TOML carries the fit that maps the device's own clock onto the recording's
seconds.  This module turns that pair into arrays the browser can draw, and it
is the only place in audian that knows the bundle's shape.

It is pure data: ``tomllib``, polars, numpy, and :mod:`audian.windowing`.  No
Qt.  That is what lets the hard parts -- the trust gate, the null discipline,
the treatment partition -- be tested against the real exp2 bundle with no
widget in the way.

Three things this reader exists to get right
--------------------------------------------
**The validated gate.**  ``[alignment].validated`` is the only statement in the
bundle that says the positions may be believed.  An absent key, a string
``"true"``, an integer ``1`` -- none of those is that statement, and each reads
as *unvalidated*, not as *fine*.  Getting this wrong paints every mark
somewhere plausible and wrong, which is worse than painting nothing.

**Predicted is not observed.**  Seven of the 2187 exp2 pulses have
``match_status = "unmatched"`` and a null ``detected_time_s``: the fit says
where they are, the recording never confirmed it.  They live in their own
:class:`PointSeries`, not merged into the observed train, because a merge is a
*correctness* bug and not an inefficiency -- 7 rows inside a train of 901 lose
their pixel bucket to an observed neighbour at any zoom-out, and six of the
seven vanish at a 300 px budget.

**Empty means absent, never zero.**  A null is a value here.  893 pulses have a
null ``treatment`` because they belong to the ambient resting train and to no
trial at all -- the most common value in that column.  842 detections have a
null ``source_row`` because no log row explains them, which is the eel and the
most interesting layer in the bundle.  Nothing is ever ``fill_null``ed and
nothing is ever ``drop_null``ed, and every column that can be null is pinned
with ``schema_overrides`` so a sparse column cannot come back as ``String`` and
turn ``records_lost > 0`` into a string comparison that is true for ``'10'``
and false for ``'9'``.

What is deliberately *not* here: colour, geometry, Qt, and any notion of a
window.  A layer knows what it is and what its times are; :mod:`audian.theme`
knows what colour a role is; :mod:`audian.windowing` knows how to cut an array
down to a view.
"""

from __future__ import annotations

import hashlib
import logging
import tomllib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from . import windowing

log = logging.getLogger(__name__)


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


# --- trust ------------------------------------------------------------------

#: The alignment is validated and carries no warnings.
TRUST_OK = "ok"

#: Validated, but the writer recorded warnings about the fit.
TRUST_WARN = "warn"

#: Not validated: ``validated`` is absent, false, or not a real TOML boolean.
#: Positions on screen are not to be believed.
TRUST_UNVALIDATED = "unvalidated"


# --- the CSVs ---------------------------------------------------------------

KIND_PULSES = "pulses"
KIND_TRIALS = "trials"
KIND_SESSION_EVENTS = "session_events"
KIND_DETECTIONS = "detections"
KIND_CONTROLS = "controls"

#: The five CSVs a bundle may carry, in the order they are loaded.  A bundle
#: that is missing one is not broken -- it is a bundle without that kind, and
#: :attr:`SessionBundle.missing` says so rather than inventing an empty layer.
CSV_KINDS: tuple[str, ...] = (
    KIND_PULSES,
    KIND_TRIALS,
    KIND_SESSION_EVENTS,
    KIND_DETECTIONS,
    KIND_CONTROLS,
)

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
#: one; ``source_row`` is null on the leading 842 rows, which are the
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


# --- the fit ----------------------------------------------------------------


@dataclass(frozen=True)
class Alignment:
    """``[alignment]``: the fit from device seconds to recording seconds.

    Every field is optional, because a bundle written by another tool may
    carry none of them and a missing key has to read as *unknown* rather than
    as a default that happens to look reassuring.  That is why
    :attr:`validated` is a tri-state and why :attr:`trust` treats everything
    that is not the literal boolean ``True`` as unvalidated.
    """

    recording_file: str | None = None
    recording_sha256: str | None = None
    recording_rate_hz: float | None = None
    recording_frames: int | None = None
    #: The fit is per channel.  Reading this off rather than assuming 0 is the
    #: difference between a ground-truth check passing and passing by luck on
    #: a stereo file.
    recording_channel: int | None = None

    scale: float | None = None
    offset_s: float | None = None
    drift_ppm: float | None = None
    method: str | None = None
    model: str | None = None

    match_tolerance_s: float | None = None
    match_fraction: float | None = None
    residual_median_s: float | None = None
    residual_mad_s: float | None = None
    residual_p95_abs_s: float | None = None

    #: ``True`` only when ``tomllib`` returned a real boolean ``True``.  A
    #: string ``"true"`` and an integer ``1`` are NOT claims of validation:
    #: they are a writer that did not follow the format, and the unrecognised
    #: case has to fall on the cautious side.
    validated: bool | None = None
    validation_warnings: tuple[str, ...] = ()
    fit_warnings: tuple[str, ...] = ()

    @property
    def warnings(self) -> tuple[str, ...]:
        return tuple(self.validation_warnings) + tuple(self.fit_warnings)

    @property
    def trust(self) -> str:
        """How far the positions in this bundle may be believed.

        One of `TRUST_OK`, `TRUST_WARN`, `TRUST_UNVALIDATED`.  The identity
        check is deliberate: ``validated == 1`` is true for ``True`` and for
        ``1``, and a bundle whose writer put an integer there has not told us
        the fit was checked.
        """
        if self.validated is not True:
            return TRUST_UNVALIDATED
        if self.warnings:
            return TRUST_WARN
        return TRUST_OK

    @property
    def is_validated(self) -> bool:
        return self.validated is True

    @property
    def duration_s(self) -> float | None:
        """Length of the recording the fit was made against, in seconds.

        ``recording_frames / recording_rate_hz`` -- 29140992 / 48000 =
        607.104 s on exp2.  This is the right-hand bound for a run whose
        stopped row never arrived, so it has to come from the fit's own idea
        of the recording rather than from whatever file happens to be open.
        """
        if not self.recording_frames or not self.recording_rate_hz:
            return None
        return float(self.recording_frames) / float(self.recording_rate_hz)

    def fit_summary(self) -> str:
        """One line describing the fit, for a tool tip or a metadata pane."""
        parts = []
        if self.scale is not None:
            parts.append(f"scale {self.scale:.9f}")
        if self.offset_s is not None:
            parts.append(f"offset {self.offset_s:.6f} s")
        if self.drift_ppm is not None:
            parts.append(f"drift {self.drift_ppm:+.3f} ppm")
        if self.residual_median_s is not None:
            parts.append(f"residual {1e6 * self.residual_median_s:+.0f} µs")
        if self.match_fraction is not None:
            parts.append(f"matched {100.0 * self.match_fraction:.1f}%")
        if self.recording_channel is not None:
            parts.append(f"fitted on channel {self.recording_channel}")
        return ", ".join(parts)


@dataclass(frozen=True)
class Integrity:
    """``[integrity]``: whether the log itself has holes.

    A second, independent trust axis.  The fit can be perfect and the log can
    still be missing records -- the device drops them when the ring buffer
    overruns -- and then the marks that ARE drawn are in the right place while
    the ones that are not drawn were never written down.  A viewer that only
    reports :class:`Alignment` cannot tell those two apart.
    """

    records_lost: int | None = None
    drop_events: int | None = None
    sequence_breaks: int | None = None
    truncated_by_power_loss: bool | None = None
    interrupted_file_indices: tuple[int, ...] = ()

    @property
    def complete(self) -> bool:
        return not self.reasons

    @property
    def reasons(self) -> tuple[str, ...]:
        out = []
        if self.records_lost:
            out.append(f"{self.records_lost} log records lost")
        if self.drop_events:
            out.append(f"{self.drop_events} drop events")
        if self.sequence_breaks:
            out.append(f"{self.sequence_breaks} sequence breaks")
        if self.truncated_by_power_loss:
            out.append("the log was truncated by a power loss")
        if self.interrupted_file_indices:
            names = ", ".join(str(i) for i in self.interrupted_file_indices)
            out.append(f"interrupted log files: {names}")
        return tuple(out)


@dataclass(frozen=True)
class RecordingCheck:
    """Whether the open recording is the one the fit was made against.

    Each field is tri-state and ``None`` means *the check did not run*, which
    is not the same as passing.  The distinction matters because tier 3 (the
    SHA-256) is deliberately never run on open, so a bundle whose content has
    never been verified must not report itself as verified.
    """

    name: bool | None = None
    rate: bool | None = None
    frames: bool | None = None
    channel: bool | None = None
    sha256: bool | None = None
    problems: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        """True when nothing that ran failed.  Checks that did not run pass."""
        return not any(
            v is False
            for v in (self.name, self.rate, self.frames, self.channel, self.sha256)
        )


def _as_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _as_float(value: Any) -> float | None:
    # bool is an int in Python; a boolean in a numeric slot is a writer bug and
    # silently becoming 1.0 would hide it.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def _as_warnings(value: Any) -> tuple[str, ...]:
    """A TOML warning list.  A bare string is one warning, not a character list."""
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,) if value.strip() else ()
    if isinstance(value, Sequence):
        return tuple(str(v) for v in value if str(v).strip())
    return (str(value),)


class SessionMeta:
    """The ``*_metadata.toml`` beside the CSVs: who wrote them and how well.

    Holds the raw table too, because the device writes sections this reader
    has no opinion about (``[stimulus_library]``, ``[trial_design]``) and a
    metadata pane should be able to show them without a second parse.
    """

    def __init__(
        self,
        raw: Mapping[str, Any],
        *,
        path: Path | None = None,
        warnings: Sequence[str] = (),
    ) -> None:
        self.raw: Mapping[str, Any] = raw
        self.path = path
        session = raw.get("session") or {}
        clock = raw.get("clock") or {}
        alignment = raw.get("alignment") or {}
        integrity = raw.get("integrity") or {}

        self.session_id: str | None = _as_str(session.get("session_id"))
        self.device: str | None = _as_str(session.get("device"))
        self.firmware_build: str | None = _as_str(session.get("firmware_build"))
        self.recorded_at: str | None = _as_str(session.get("recorded_at"))
        #: The DEVICE clock, not the recording's.  50 kHz on exp2 against a
        #: 48 kHz WAV -- confusing the two is a 4% drift on every mark.
        self.sample_rate_hz: int | None = _as_int(clock.get("sample_rate_hz"))

        problems: list[str] = list(warnings)
        validated = alignment.get("validated")
        if "validated" not in alignment:
            # Absent reads as unvalidated, like a string or an integer does --
            # but silently, a bundle whose writer forgot the key was
            # indistinguishable in `warnings` from one that genuinely never had
            # a fit to validate, and those need different answers from a human.
            problems.append(
                "[alignment].validated is absent; reading the fit as unvalidated"
            )
        elif validated is not None and not isinstance(validated, bool):
            # Not a refusal to load -- a refusal to BELIEVE.  The bundle still
            # draws, dashed, under the unvalidated badge.
            problems.append(
                f"[alignment].validated is {validated!r}, not a boolean; "
                "reading the fit as unvalidated"
            )
            validated = None

        self.alignment = Alignment(
            recording_file=_as_str(alignment.get("recording_file")),
            recording_sha256=_as_str(alignment.get("recording_sha256")),
            recording_rate_hz=_as_float(alignment.get("recording_rate_hz")),
            recording_frames=_as_int(alignment.get("recording_frames")),
            recording_channel=_as_int(alignment.get("recording_channel")),
            scale=_as_float(alignment.get("scale")),
            offset_s=_as_float(alignment.get("offset_s")),
            drift_ppm=_as_float(alignment.get("drift_ppm")),
            method=_as_str(alignment.get("method")),
            model=_as_str(alignment.get("model")),
            match_tolerance_s=_as_float(alignment.get("match_tolerance_s")),
            match_fraction=_as_float(alignment.get("match_fraction")),
            residual_median_s=_as_float(alignment.get("residual_median_s")),
            residual_mad_s=_as_float(alignment.get("residual_mad_s")),
            residual_p95_abs_s=_as_float(alignment.get("residual_p95_abs_s")),
            validated=validated,
            validation_warnings=_as_warnings(alignment.get("validation_warnings")),
            fit_warnings=_as_warnings(alignment.get("fit_warnings")),
        )
        self.integrity = Integrity(
            records_lost=_as_int(integrity.get("records_lost")),
            drop_events=_as_int(integrity.get("drop_events")),
            sequence_breaks=_as_int(integrity.get("sequence_breaks")),
            truncated_by_power_loss=(
                integrity.get("truncated_by_power_loss")
                if isinstance(integrity.get("truncated_by_power_loss"), bool)
                else None
            ),
            interrupted_file_indices=tuple(
                int(i)
                for i in (integrity.get("interrupted_file_indices") or ())
                if isinstance(i, int) and not isinstance(i, bool)
            ),
        )
        counts = raw.get("counts") or {}
        self.counts: Mapping[str, int] = {
            k: int(v)
            for k, v in counts.items()
            if isinstance(v, int) and not isinstance(v, bool)
        }
        #: Everything wrong with the TOML itself, folded into
        #: :attr:`SessionBundle.warnings` at load.  Never silent.
        self.warnings: tuple[str, ...] = tuple(problems)

    @classmethod
    def from_toml(cls, path) -> "SessionMeta":
        path = Path(path)
        with open(path, "rb") as f:
            raw = tomllib.load(f)
        return cls(raw, path=path)

    @property
    def trust(self) -> str:
        return self.alignment.trust

    @property
    def expected_rows(self) -> Mapping[str, int]:
        """``[counts].rows_<kind>`` -- what the writer says each CSV holds."""
        return {
            kind: self.counts[f"rows_{kind}"]
            for kind in CSV_KINDS
            if f"rows_{kind}" in self.counts
        }

    def check_recording(self, path, *, info: Any = None) -> RecordingCheck:
        """Is `path` the recording this fit was made against?

        Two tiers, both free.  **Name** compares file names only: the bundle
        and the recording are copied around together, so the directory says
        nothing, while a different name almost always means a different
        recording -- and then every mark is in the wrong place and still looks
        plausible.  **Shape** compares sample rate, frame count and channel
        index against the file header, which catches truncation, resampling,
        and a fit channel the open file does not have.

        Tier 3, the SHA-256 over 175 MB, is :func:`verify_sha256` and is never
        called from here: a second of stall on every file open is how a check
        gets switched off by the first person who meets it in the field.

        `info` takes an already-read header (anything with ``samplerate``,
        ``frames`` and ``channels``) so a caller that has just opened the file
        does not open it twice.
        """
        path = Path(path)
        fit = self.alignment
        problems: list[str] = []

        name: bool | None = None
        if fit.recording_file:
            name = Path(fit.recording_file).name == path.name
            if not name:
                problems.append(
                    f"this bundle was fitted against {Path(fit.recording_file).name}, "
                    f"not {path.name}"
                )

        if info is None:
            try:
                import soundfile

                info = soundfile.info(str(path))
            except Exception as err:  # noqa: BLE001 - any reader failure is the same
                problems.append(f"could not read the header of {path.name}: {err}")
                return RecordingCheck(name=name, problems=tuple(problems))

        rate: bool | None = None
        frames: bool | None = None
        channel: bool | None = None
        if fit.recording_rate_hz is not None:
            rate = float(getattr(info, "samplerate", 0)) == fit.recording_rate_hz
            if not rate:
                problems.append(
                    f"the fit was made at {fit.recording_rate_hz:.0f} Hz, "
                    f"{path.name} is {getattr(info, 'samplerate', 0)} Hz"
                )
        if fit.recording_frames is not None:
            frames = int(getattr(info, "frames", -1)) == fit.recording_frames
            if not frames:
                problems.append(
                    f"the fit was made against {fit.recording_frames} frames, "
                    f"{path.name} has {getattr(info, 'frames', -1)}"
                )
        if fit.recording_channel is not None:
            channel = 0 <= fit.recording_channel < int(getattr(info, "channels", 0))
            if not channel:
                problems.append(
                    f"the fit was made on channel {fit.recording_channel}, "
                    f"{path.name} has {getattr(info, 'channels', 0)} channels"
                )
        return RecordingCheck(
            name=name,
            rate=rate,
            frames=frames,
            channel=channel,
            problems=tuple(problems),
        )


#: Digest results, keyed by ``(path, size, mtime_ns)``.  Hashing 175 MB takes
#: about a second, and a menu item the user may hit twice should answer the
#: second time at once -- while any edit to the file changes size or mtime and
#: throws the answer away.
_SHA_CACHE: dict[tuple[str, int, int], bool] = {}


def verify_sha256(
    meta: SessionMeta,
    recording_path,
    *,
    chunk: int = 1 << 22,
    progress=None,
) -> bool | None:
    """Tier 3: does the recording's content match ``[alignment].recording_sha256``?

    ``None`` when the bundle records no digest, which is not a failure.  On
    demand only -- nothing in the load path calls this.

    `progress` is called with a fraction in ``[0, 1]`` and may return ``False``
    to abort, in which case the result is ``None``: an interrupted hash is not
    a mismatch and must not be cached as one.
    """
    expected = meta.alignment.recording_sha256
    if not expected:
        return None
    path = Path(recording_path)
    try:
        stat = path.stat()
    except OSError:
        return None
    key = (str(path.resolve()), stat.st_size, stat.st_mtime_ns)
    if key in _SHA_CACHE:
        return _SHA_CACHE[key]

    digest = hashlib.sha256()
    done = 0
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            digest.update(block)
            done += len(block)
            if progress is not None and stat.st_size:
                if progress(done / stat.st_size) is False:
                    return None
    result = digest.hexdigest().lower() == expected.strip().lower()
    _SHA_CACHE[key] = result
    return result


# --- discovery --------------------------------------------------------------


@dataclass(frozen=True)
class BundleRef:
    """A bundle found on disk, before any CSV has been read.

    Discovery parses the 2.7 KB TOML and nothing else, so opening a directory
    of recordings costs a few kilobytes rather than every CSV in it.
    """

    session_id: str
    metadata_path: Path
    directory: Path
    recording_file: str | None
    validated: bool | None
    #: Which of `CSV_KINDS` actually exist beside the TOML.  A kind that is
    #: absent here becomes :attr:`SessionBundle.missing`, never an empty layer.
    kinds: frozenset[str] = frozenset()

    def path(self, kind: str) -> Path | None:
        """``<dir>/<session_id>_<kind>.csv``, or None when it is not there."""
        if kind not in self.kinds:
            return None
        return self.directory / f"{self.session_id}_{kind}.csv"


def _ref_from_toml(metadata_path: Path) -> BundleRef | None:
    try:
        meta = SessionMeta.from_toml(metadata_path)
    except (OSError, tomllib.TOMLDecodeError) as err:
        log.warning(
            "%s is not a readable session metadata file: %s", metadata_path, err
        )
        return None
    session_id = meta.session_id or metadata_path.name[: -len("_metadata.toml")]
    directory = metadata_path.parent
    kinds = frozenset(
        kind for kind in CSV_KINDS if (directory / f"{session_id}_{kind}.csv").is_file()
    )
    return BundleRef(
        session_id=session_id,
        metadata_path=metadata_path,
        directory=directory,
        recording_file=meta.alignment.recording_file,
        validated=meta.alignment.validated,
        kinds=kinds,
    )


def find_bundles(recording_path, *, require_match: bool = True) -> list[BundleRef]:
    """Session bundles sitting beside `recording_path`.

    With `require_match` (the default) a bundle is kept only when its
    ``[alignment].recording_file`` names this recording by file name.  A stray
    metadata file from a neighbouring experiment is exactly the mistake that
    puts every annotation in the wrong place while looking entirely normal,
    so the name check is not optional.
    """
    path = Path(recording_path)
    directory = path if path.is_dir() else path.parent
    refs = []
    for candidate in sorted(directory.glob("*_metadata.toml")):
        ref = _ref_from_toml(candidate)
        if ref is None:
            continue
        if require_match and not path.is_dir():
            if not ref.recording_file:
                continue
            if Path(ref.recording_file).name != path.name:
                continue
        refs.append(ref)
    return refs


def find_bundle(recording_path) -> BundleRef | None:
    """The one bundle that names `recording_path`, or None.

    ``None`` when several ``*_metadata.toml`` name the same recording, and the
    ambiguity is logged.  Never silently pick one: two sessions logged against
    one recording mispositions everything, and the wrong choice looks exactly
    like the right one.
    """
    refs = find_bundles(recording_path)
    if not refs:
        return None
    if len(refs) > 1:
        log.warning(
            "%d session bundles name %s (%s); refusing to guess which one is meant",
            len(refs),
            Path(recording_path).name,
            ", ".join(r.metadata_path.name for r in refs),
        )
        return None
    return refs[0]


# --- the layer model --------------------------------------------------------


class Layer:
    """One thing the ribbon can switch on: a name, a shape, and a colour role.

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
        #: ``detected_time_s`` (max abs difference 0.0 over all 2180 exp2
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
        **kwargs: Any,
    ) -> None:
        super().__init__(id, kind=KIND_SPAN, **kwargs)
        self.starts = np.ascontiguousarray(starts, dtype=np.float64)
        self.ends = np.ascontiguousarray(ends, dtype=np.float64)
        #: ``np.maximum.accumulate(ends)``, computed ONCE here.  It is what
        #: makes :func:`windowing.window_spans` searchable whatever the spans
        #: do, and recomputing it per redraw would defeat the point of having
        #: a windowing primitive at all.
        self.max_end = np.maximum.accumulate(self.ends) if self.ends.size else self.ends
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

    def describe(self, i: int) -> str:
        """One line about a single span, for the readout or a tool tip."""
        row = self.frame.row(i, named=True) if self.frame.height > i else {}
        parts = [self.label]
        number = row.get("trial_number")
        if number is not None:
            parts.append(f"#{int(number)}")
        start = float(self.starts[i])
        end = float(self.ends[i])
        parts.append(f"{start:.3f}-{end:.3f} s ({end - start:.3f} s)")
        if "pulses_emitted" in self.frame.columns:
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


# --- reading ----------------------------------------------------------------


#: What a matched pulse and its detection may differ by when no bundle says.
#: 0.5 ms is exp2's own ``[alignment].match_tolerance_s``; the real number is
#: read from the TOML whenever it is there, and this exists only so a bundle
#: that omits it still joins rather than silently producing 2180 orphans.
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
        self._by_id = {layer.id: layer for layer in self.layers}

    # -- construction --

    @classmethod
    def load(cls, ref_or_path, *, recording=None) -> "SessionBundle":
        """Read a bundle from a `BundleRef`, a metadata TOML, or a directory."""
        ref = _resolve_ref(ref_or_path)
        meta = SessionMeta.from_toml(ref.metadata_path)
        warnings: list[str] = list(meta.warnings)
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
                f"{blank} pulse(s) have no pulse_type at all; "
                "in no layer and not drawn"
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
        micro="Novel",
        track=TRACK_HEARD,
        role="detection.novel",
        default_on=True,
        tip="pulses in the recording that no log row accounts for -- the animal",
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
        series.append(
            PointSeries(
                times=np.ascontiguousarray(det_t[unmatched]),
                frame=det_frame.filter(unmatched),
                observed=True,
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
        role="detection.novel",
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
    ``match_tolerance_s``, and on the real bundle it is exact: the 2180
    explained ``recording_time_s`` are bit-identical to the 2180 matched
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
                default_on=True,
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
