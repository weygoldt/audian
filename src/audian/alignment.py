"""The fit that places a session bundle in a recording, and how far to trust it.

A fakefish session is logged on the device's own clock and has to be placed in
a recording made on someone else's.  ``*_metadata.toml`` carries the fit that
maps one onto the other, the writer's own verdict on that fit, and enough about
the recording to tell whether the file now open is the one it was made against.
This module reads that TOML and nothing else: no CSV, no polars, no numpy, no
Qt.  :mod:`audian.session` reads the rows; this module says whether the
positions they get may be believed.

**The validated gate.**  ``[alignment].validated`` is the only statement in the
bundle that says the positions may be believed.  An absent key, a string
``"true"``, an integer ``1`` -- none of those is that statement, and each reads
as *unvalidated*, not as *fine*.  Getting this wrong paints every mark
somewhere plausible and wrong, which is worse than painting nothing.

**Trust has two axes.**  :class:`Alignment` says whether the marks are in the
right place; :class:`Integrity` says whether the log they came from is whole.
A perfect fit over a log that lost records draws every mark correctly and still
shows a smaller session than the one that happened, and a viewer that reports
only the first cannot tell those apart.

**A bundle names its recording.**  Discovery reads the 2.7 KB TOML and no CSV,
and a bundle is only offered for a recording whose file name it names.  A stray
metadata file from a neighbouring experiment is exactly the mistake that puts
every annotation half a minute out while looking entirely normal.

**A recording may be several files.**  exp3 (PULS0005) is four WAVs treated as
one recording, and the writer says so with PLURAL keys: ``recording_files``,
``recording_sha256`` as an array, ``recording_file_frames``, and
``recording_join_gaps_s``.  Both shapes are parsed and both normalise to a
tuple, so every check downstream is written once and works for one file or for
four.  This is not cosmetic: while only the singular key was read, exp3's
``recording_file`` came back ``None`` and the provenance check -- the one that
exists so a stray bundle cannot put every mark in the wrong place -- did not
fail, it silently had no opinion, which is worse.
"""

from __future__ import annotations

import hashlib
import logging
import tomllib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# --- trust ------------------------------------------------------------------

#: The alignment is validated and carries no warnings.
TRUST_OK = "ok"

#: Validated, but the writer recorded warnings about the fit.
TRUST_WARN = "warn"

#: Not validated: ``validated`` is absent, false, or not a real TOML boolean.
#: Positions on screen are not to be believed.
TRUST_UNVALIDATED = "unvalidated"


# --- which CSVs a bundle carries --------------------------------------------

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

    #: Every file of the recording, in order, as the writer named them.  A
    #: single-file session has exactly one entry -- always a tuple, so the
    #: name check, the digest check and the frame check are each written once.
    recording_files: tuple[str, ...] = ()
    #: One SHA-256 per entry of `recording_files`.  Per file, never one digest
    #: for the whole recording: verifying file 3 of exp3 against file 0's
    #: digest would report a mismatch on a recording that is entirely correct.
    recording_sha256s: tuple[str, ...] = ()
    #: Frames per file.  This is the same assertion ``audian.data.open_files``
    #: makes against the WAV headers, stated by the writer, so the two can be
    #: compared -- see :meth:`check_recording`.
    recording_file_frames: tuple[int, ...] = ()
    #: Seconds the recorder lost at each join, one fewer than there are files.
    #: Carried through as a DECLARED FACT for the badge and nothing else: this
    #: viewer never shifts a mark by a gap.  exp3 declares +32 ms, +32 ms,
    #: -120 ms, and 120 ms is about thirty pulses of a 4 ms volley interval.
    recording_join_gaps_s: tuple[float, ...] = ()
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
    def recording_file(self) -> str | None:
        """The first file of the recording, for a caller that wants one name.

        A split recording has no single file, and the honest answer to "which
        recording is this" is then the first of the four -- which is what the
        mismatch message shows a reader.  Anything that has to be RIGHT about
        a particular file (a digest, a frame count) asks for it by path
        instead: :meth:`sha256_for`, :meth:`frames_for`.
        """
        return self.recording_files[0] if self.recording_files else None

    @property
    def recording_sha256(self) -> str | None:
        """The digest of the first file.  See :meth:`sha256_for` for the rest."""
        return self.recording_sha256s[0] if self.recording_sha256s else None

    @property
    def is_split(self) -> bool:
        """True when the fit was made against several files as one recording."""
        return len(self.recording_files) > 1

    def index_of(self, path) -> int | None:
        """Which file of the recording `path` is, by name, or None.

        By file NAME only, like the rest of the provenance check: the bundle
        and the recording travel together, so the directory says nothing.
        """
        name = Path(path).name
        for i, named in enumerate(self.recording_files):
            if Path(named).name == name:
                return i
        return None

    def sha256_for(self, path) -> str | None:
        """The declared digest of `path`, or None when the bundle has none.

        Falls back to the single digest when the bundle names exactly one file
        and `path` is not it -- a renamed copy is still worth hashing, and the
        name check has already said what it thinks about the name.
        """
        i = self.index_of(path)
        if i is not None and i < len(self.recording_sha256s):
            return self.recording_sha256s[i]
        if len(self.recording_sha256s) == 1 and not self.is_split:
            return self.recording_sha256s[0]
        return None

    def frames_for(self, path) -> int | None:
        """Frames the writer says `path` holds, or None when it does not say."""
        i = self.index_of(path)
        if i is not None and i < len(self.recording_file_frames):
            return self.recording_file_frames[i]
        return None

    @property
    def join_times_s(self) -> tuple[float, ...]:
        """Where the files butt together, in recording seconds.

        The BUNDLE's own arithmetic -- the cumulative file frames over the fit's
        rate -- and it is used to REGION the residual statistics, which are a
        statement about the bundle.  The join markers drawn in the view take
        their position from the loader instead (``loader.start_indices``),
        because a split recording has joins whether or not a bundle is loaded.
        On exp3: 931.968, 1863.936, 2795.904 s.
        """
        rate = self.recording_rate_hz
        if not rate or len(self.recording_file_frames) < 2:
            return ()
        total = 0
        out = []
        for frames in self.recording_file_frames[:-1]:
            total += int(frames)
            out.append(total / float(rate))
        return tuple(out)

    @property
    def file_starts_s(self) -> tuple[float, ...]:
        """Recording second each declared file begins at, or ``()``.

        ``join_times_s`` with a leading zero: the joins are the ends of the
        first n-1 files, which are the starts of the last n-1.  Used to say
        HOW FAR OUT the marks would be when only some of the files are open
        (:meth:`SplitCoverage.message`) -- never to shift one.  On exp3:
        0, 931.968, 1863.936, 2795.904 s.
        """
        joins = self.join_times_s
        return (0.0,) + joins if joins else ()

    def coverage(self, paths: Iterable[Any]) -> "SplitCoverage":
        """Which of the declared files `paths` actually holds.

        Matched by file NAME, like every other provenance check here: the
        bundle and the recording are copied around together, so the directory
        says nothing, and the frame counts cannot answer this question at all
        -- see :class:`SplitCoverage`.
        """
        names = tuple(Path(p).name for p in paths)
        declared = tuple(Path(f).name for f in self.recording_files)
        return SplitCoverage(
            declared=declared,
            opened=tuple(n for n in declared if n in names),
            extra=tuple(n for n in names if n not in declared),
            starts_s=self.file_starts_s,
        )

    def joins(self) -> tuple[tuple[float, float | None], ...]:
        """``(recording second, declared gap in s)`` for each declared join.

        The gap is None when the writer stated joins but no gaps.  For the
        badge tool tip: a join is a fact about the recording, the gap is a
        fact the writer measured, and neither is a correction this viewer
        applies.
        """
        gaps = self.recording_join_gaps_s
        return tuple(
            (t, gaps[i] if i < len(gaps) else None)
            for i, t in enumerate(self.join_times_s)
        )

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


@dataclass(frozen=True)
class SplitCoverage:
    """How much of a recording written as several files is open.

    A recording split across four WAVs is ONE timeline, and every mark in the
    bundle is placed in whole-recording seconds.  Open a proper subset of
    those files and the recording that is on screen starts somewhere else:
    open exp3's third file alone and its content is recording seconds
    1863.936-1868.936, while the marks that land in the view are the ones for
    100-105 s.  Every one of them is 1764 s out of place and every one of them
    looks entirely plausible.

    **No other check can catch this.**  The name check passes -- the open file
    IS one of the four the bundle names.  The frame check passes -- it accepts
    either the whole recording's count or one file's own, deliberately, so
    that a caller may hand it a single WAV header or the loader over all four.
    The declared join gaps produce a soft status line whose subject is gap
    LABELLING, not mark placement.  What is missing is the one thing the
    browser has always known and never asked: how many of the declared files
    are actually open.

    So this is the check, and its answer is a REFUSAL rather than a
    correction.  The offset is computable from `recording_file_frames` --
    `starts_s` is right there -- and re-basing the marks on it is exactly the
    kind of quiet repair that turns into a subtly wrong picture nobody can
    see: it would have to assume the missing files are the declared ones, at
    the declared lengths, joined at the declared gaps, none of which this
    viewer has opened or measured.  A viewer that draws nothing and says why
    is wrong in a way the reader can act on.
    """

    #: File names the bundle declares, in recording order.
    declared: tuple[str, ...] = ()
    #: The declared files that are open, in declared order.
    opened: tuple[str, ...] = ()
    #: Open files the bundle does not name.  Not a refusal on its own -- the
    #: name check owns that -- but it is named in the message, because a
    #: recording padded with a file the fit never saw is not the recording the
    #: fit was made against either.
    extra: tuple[str, ...] = ()
    #: Recording second each DECLARED file starts at, when the bundle says
    #: enough to work it out.  For the message only.
    starts_s: tuple[float, ...] = ()

    @property
    def missing(self) -> tuple[str, ...]:
        return tuple(n for n in self.declared if n not in self.opened)

    @property
    def partial(self) -> bool:
        """True when the loader opened SOME of the declared files, not all.

        Not when none of them is open: that is a bundle belonging to another
        recording, which the name check already refuses with a message about
        the right thing.  Not when there is one declared file: a single-file
        recording is either open or it is not.
        """
        return len(self.declared) > 1 and bool(self.opened) and bool(self.missing)

    @property
    def shift_s(self) -> float | None:
        """Where the first open file sits in the recording, in seconds.

        How far the marks would be out, at least.  ``None`` when the bundle
        does not carry the per-file frame counts to say.
        """
        if not self.opened or len(self.starts_s) != len(self.declared):
            return None
        return self.starts_s[self.declared.index(self.opened[0])]

    def subject(self) -> str:
        """What the fit was made against, as a phrase another sentence can use.

        Written so that it still says the whole truth inside the sentence the
        generic mismatch badge builds around it ("This bundle was fitted
        against ..., not against the open file"): if the specific badge for
        this refusal is ever lost, what is left names all the files and is
        still correct rather than merely alarming.
        """
        return (
            f"all {len(self.declared)} of {', '.join(self.declared)} as one recording"
        )

    def summary(self) -> str:
        """One line for a status bar: what is open, and what to do."""
        return (
            f"the bundle names {len(self.declared)} files as ONE recording and "
            f"{'only ' if len(self.opened) == 1 else ''}"
            f"{', '.join(self.opened)} "
            f"{'is' if len(self.opened) == 1 else 'are'} open; nothing is drawn "
            f"-- open all {len(self.declared)} files together"
        )

    def message(self) -> str:
        """The whole refusal: which files, how wrong it would be, what to do.

        Written out in full because this is the one place the reader meets a
        failure that looks like success everywhere else: the badge would
        otherwise say WARNINGS, the joins would be empty, and the marks would
        be drawn in the wrong minute of the recording.
        """
        lines = [
            f"This bundle was fitted against {len(self.declared)} files as ONE "
            f"recording, and {len(self.opened)} of them "
            f"{'is' if len(self.opened) == 1 else 'are'} open.",
            f"open:    {', '.join(self.opened) or '(none)'}",
            f"missing: {', '.join(self.missing) or '(none)'}",
        ]
        if self.extra:
            lines.append(f"not named by the bundle: {', '.join(self.extra)}")
        shift = self.shift_s
        if shift is None:
            # Not zero.  A bundle that never said how long its files are
            # cannot say where the open one starts, and guessing a number
            # here would be the same invention the refusal exists to avoid.
            lines.append(
                "Every mark is placed in whole-recording seconds, and this "
                "bundle carries no per-file frame counts, so how far out they "
                "would be drawn cannot even be stated."
            )
        elif shift > 0.0:
            lines.append(
                f"Every mark is placed in whole-recording seconds, and "
                f"{self.opened[0]} starts at {shift:.3f} s of the recording, so "
                f"each one would be drawn about {shift:.3f} s from where it "
                f"belongs -- and would look entirely plausible there."
            )
        else:
            lines.append(
                "Every mark is placed in whole-recording seconds, so only the "
                "marks inside this file's own span could land correctly, and "
                "only by accident; everything after it would be drawn over "
                "audio that is not open."
            )
        lines.append("Nothing is drawn, and no mark is re-based to fit.")
        lines.append(
            "To read them, open the whole recording at once:\n  audian "
            + " ".join(self.declared)
        )
        return "\n".join(lines)


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


def _as_tuple(value: Any, cast) -> tuple:
    """A TOML key that may be written as one value or as an array of them.

    A bare scalar is a one-element tuple, never iterated: ``recording_file =
    "DR0000_0087.wav"`` is one file, not fifteen characters.  Entries `cast`
    refuses are dropped, because a partial array is still worth the checks it
    can run.
    """
    if value is None:
        return ()
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        one = cast(value)
        return (one,) if one is not None else ()
    out = [cast(v) for v in value]
    return tuple(v for v in out if v is not None)


def _recording_files(alignment: Mapping[str, Any]) -> tuple[str, ...]:
    """``recording_files`` (split) or ``recording_file`` (single), as a tuple.

    Both keys are the same fact.  Reading only the singular one is what left
    exp3 -- four WAVs written as one recording -- with no name to check and
    therefore no opinion about whether the bundle belonged to what was open.
    """
    files = _as_tuple(alignment.get("recording_files"), _as_str)
    return files or _as_tuple(alignment.get("recording_file"), _as_str)


def _as_warnings(value: Any) -> tuple[str, ...]:
    """A TOML warning list.  A bare string is one warning, not a character list."""
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,) if value.strip() else ()
    if isinstance(value, Sequence):
        return tuple(str(v) for v in value if str(v).strip())
    return (str(value),)


def _split_problems(
    files: tuple[str, ...],
    digests: tuple[str, ...],
    file_frames: tuple[int, ...],
    gaps: tuple[float, ...],
    total_frames: int | None,
) -> list[str]:
    """What the split-recording keys say about each other, when they disagree.

    Four keys describe the same recording from four directions, and the writer
    computes all four.  When they do not agree, the fit was made against
    something other than what the TOML describes -- and every check further
    down is then checking the wrong thing while looking like it passed.  On
    exp2 (one file) and exp3 (four) all four agree and this returns nothing.
    """
    out: list[str] = []
    if files and digests and len(digests) != len(files):
        out.append(
            f"[alignment] names {len(files)} recording file(s) and "
            f"{len(digests)} SHA-256 digest(s)"
        )
    if files and file_frames and len(file_frames) != len(files):
        out.append(
            f"[alignment] names {len(files)} recording file(s) and "
            f"{len(file_frames)} frame count(s)"
        )
    if len(files) > 1 and gaps and len(gaps) != len(files) - 1:
        out.append(
            f"[alignment] names {len(files)} recording file(s), which have "
            f"{len(files) - 1} join(s), and {len(gaps)} join gap(s)"
        )
    if file_frames and total_frames is not None:
        summed = sum(file_frames)
        if summed != total_frames:
            out.append(
                f"[alignment].recording_file_frames sum to {summed}, "
                f"recording_frames says {total_frames}"
            )
    return out


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

        files = _recording_files(alignment)
        digests = _as_tuple(alignment.get("recording_sha256"), _as_str)
        file_frames = _as_tuple(alignment.get("recording_file_frames"), _as_int)
        gaps = _as_tuple(alignment.get("recording_join_gaps_s"), _as_float)
        total_frames = _as_int(alignment.get("recording_frames"))
        problems.extend(
            _split_problems(files, digests, file_frames, gaps, total_frames)
        )

        self.alignment = Alignment(
            recording_files=files,
            recording_sha256s=digests,
            recording_file_frames=file_frames,
            recording_join_gaps_s=gaps,
            recording_rate_hz=_as_float(alignment.get("recording_rate_hz")),
            recording_frames=total_frames,
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
        plausible.  A split recording passes when `path` is ANY of the files
        it names.  **Shape** compares sample rate, frame count and channel
        index against the file header, which catches truncation, resampling,
        and a fit channel the open file does not have.

        The frame count is the one check a split recording changes.  A bundle
        states both the whole recording (``recording_frames``, 173 809 152 on
        exp3) and each file (``recording_file_frames``), and the caller may
        hand over either a single WAV's header or the loader that opened all
        four, so both readings pass and anything else is a bundle that does
        not belong to what is on screen.

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
        if fit.recording_files:
            name = fit.index_of(path) is not None
            if not name:
                named = ", ".join(Path(f).name for f in fit.recording_files)
                problems.append(
                    f"this bundle was fitted against {named}, not {path.name}"
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
        want = [n for n in (fit.recording_frames, fit.frames_for(path)) if n]
        if want:
            actual = int(getattr(info, "frames", -1))
            frames = actual in want
            if not frames:
                problems.append(
                    "the fit was made against "
                    + " or ".join(f"{n} frames" for n in want)
                    + f", {path.name} has {actual}"
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

    ``None`` when the bundle records no digest for this file, which is not a
    failure.  On demand only -- nothing in the load path calls this.

    The digest is looked up BY FILE (:meth:`Alignment.sha256_for`).  A split
    recording carries one digest per file, and hashing file 3 of exp3 against
    file 0's digest would report a mismatch on a recording that is entirely
    correct -- the exact false alarm that gets a check switched off.

    `progress` is called with a fraction in ``[0, 1]`` and may return ``False``
    to abort, in which case the result is ``None``: an interrupted hash is not
    a mismatch and must not be cached as one.
    """
    expected = meta.alignment.sha256_for(recording_path)
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
    #: Every file the fit names, in order.  A tuple even for one file, so
    #: discovery matches a split recording the same way it matches a single
    #: one -- by asking whether the open file is ANY of them.
    recording_files: tuple[str, ...]
    validated: bool | None
    #: Which of `CSV_KINDS` actually exist beside the TOML.  A kind that is
    #: absent here becomes :attr:`SessionBundle.missing`, never an empty layer.
    kinds: frozenset[str] = frozenset()

    @property
    def recording_file(self) -> str | None:
        """The first file the fit names.  See `Alignment.recording_file`."""
        return self.recording_files[0] if self.recording_files else None

    def names(self, path) -> bool:
        """Does this bundle name `path`, by file name?"""
        name = Path(path).name
        return any(Path(f).name == name for f in self.recording_files)

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
        recording_files=meta.alignment.recording_files,
        validated=meta.alignment.validated,
        kinds=kinds,
    )


def find_bundles(recording_path, *, require_match: bool = True) -> list[BundleRef]:
    """Session bundles sitting beside `recording_path`.

    With `require_match` (the default) a bundle is kept only when its
    ``[alignment].recording_file`` -- or any entry of ``recording_files``, for
    a recording written as several WAVs -- names this recording by file name.
    A stray metadata file from a neighbouring experiment is exactly the mistake
    that puts every annotation in the wrong place while looking entirely
    normal, so the name check is not optional.
    """
    path = Path(recording_path)
    directory = path if path.is_dir() else path.parent
    refs = []
    for candidate in sorted(directory.glob("*_metadata.toml")):
        ref = _ref_from_toml(candidate)
        if ref is None:
            continue
        if require_match and not path.is_dir():
            if not ref.recording_files or not ref.names(path):
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
