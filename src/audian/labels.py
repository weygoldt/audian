"""Hand-made labels: the mutable half of what audian draws over a recording.

The immutable half lives in `session.py` and `alignment.py` -- a bundle of
CSVs fitted to the recording by a separate program, which this viewer reads
and never writes.  This module is the other one: marks a reader makes with
the mouse, in a sidecar CSV beside the recording that only audian writes.

The two are deliberately separate types, separate files and separate panels.
An annotation is a claim the log makes about what happened; a label is a
claim the reader makes about what they see.  Merging them would make the
provenance of a row a matter of reading a flag.

Pure data.  Nothing here imports Qt, so the store can be exercised without a
window -- the same split `session.py` (data) and `eventoverlay.py` (Qt)
already have.  `labeloverlay.py` is this module's Qt half.

The CSV
-------

::

    category,kind,channel,t_start_s,t_end_s,f_low_hz,f_high_hz,note

* Times are **seconds from the first frame of the first file**, the timebase
  every other number in this application is in: the bundle's
  ``recording_time_s``, ``save_region``'s ``t0``/``t1``, the plot ranges.
  ``timeaxisitem.starttime_mode`` is a display mode and converts nothing;
  writing a per-file relative time without naming the file would be
  unrecoverable.  The ``_s`` suffix is there so a reader of the file does not
  have to guess.
* ``kind`` is ``point`` or ``span``.  Redundant with ``t_end_s`` being empty,
  and kept because it says what the *category* is rather than what this one
  row happens to look like.
* **Empty means absent.**  A point has no ``t_end_s``; a label drawn on a
  trace has no frequency, because a trace's y axis is amplitude.  Empty
  rather than ``-1``, which is a number some future reader will average, and
  rather than ``0..Nyquist``, which is a *claim* that the signal fills the
  band -- and a false one.
* ``channel`` is the electrode the label was drawn on.  Empty means "not one
  channel", which is what a label drawn on the mean spectrogram writes: that
  panel is an average over the array and is no channel at all.  A reader must
  treat the column as nullable.

Read and written with the standard library's `csv`, not with polars, which is
this repository's reader everywhere else.  Two reasons, both about this file
in particular rather than about polars: the ``note`` column is free text and
needs RFC 4180 quoting on the way out as well as on the way in, and the file
is small and hand-editable, where polars' head-only type inference is a
hazard -- `session._read` pins every column of the bundle CSVs precisely
because inference typed six of them wrongly.  There is nothing here to pin.
"""

from __future__ import annotations

import csv
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable, Iterator, Optional

#: A category placed one sample at a time: a pulse, an onset, a click.
#:
#: Spelled the same as `layers.KIND_POINT`, and equal to it, on purpose: a
#: point is a point whichever half of the application drew it, and two words
#: for one idea would be the beginning of two vocabularies.  They stay
#: separate constants because the two type systems are separate -- an
#: importer that needs both aliases one, which `databrowser` does.
KIND_POINT = "point"
#: A category with an extent: a call, a discharge, a run of noise.
KIND_SPAN = "span"

KINDS = (KIND_POINT, KIND_SPAN)

#: Column order of the sidecar, and the order `Label.row` writes.
COLUMNS = (
    "category",
    "kind",
    "channel",
    "t_start_s",
    "t_end_s",
    "f_low_hz",
    "f_high_hz",
    "note",
)

#: Suffix appended to the recording's stem.  Deliberately not
#: ``<stem>-events.csv`` (audian's ``-a/--events`` flag names the *immutable*
#: bundle) and not ``<id>_<kind>.csv`` or ``*_metadata.toml`` (writing either
#: beside a recording makes `alignment.find_bundle` ambiguous, and it returns
#: None rather than guess).
SIDECAR_SUFFIX = "-labels.csv"


def sidecar_path(recording: Path | str) -> Path:
    """Where the labels of `recording` are kept."""
    recording = Path(recording)
    return recording.with_name(recording.stem + SIDECAR_SUFFIX)


def _number(text: str) -> Optional[float]:
    """A cell as a float, or None when it is empty or not a number.

    Never raises.  The file is hand-editable by design, and one bad cell in
    one row must cost that cell, not the recording's labels.
    """
    text = (text or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _integer(text: str) -> Optional[int]:
    value = _number(text)
    return None if value is None else int(value)


def _cell(value: Optional[float], decimals: int) -> str:
    """A number for the file, or an empty cell when it is absent."""
    if value is None:
        return ""
    return f"{float(value):.{decimals}f}"


@dataclass(frozen=True)
class LabelCategory:
    """One entry of the reader's vocabulary.

    Categories are a *preference*, not part of a recording: the same reader
    labels many recordings with the same words.  They are persisted in
    audian's settings file, and `LabelSet.read` adds any category a loaded
    CSV names that the settings do not know -- so a file from another machine
    loses its colours but never loses a row, and stays self-describing.
    """

    #: What the reader calls it.  The identity of the category: it is what
    #: goes in the CSV, so renaming one orphans the rows already written
    #: under the old name.
    name: str
    #: `KIND_POINT` or `KIND_SPAN`.
    kind: str = KIND_SPAN
    #: Index into `theme.marker_color`, which wraps modulo eight.  An index
    #: rather than a hex value so the colour follows a live theme switch.
    color: int = 0

    def normalized(self) -> "LabelCategory":
        """The same category with its kind forced into `KINDS`."""
        kind = self.kind if self.kind in KINDS else KIND_SPAN
        return replace(self, name=self.name.strip(), kind=kind, color=int(self.color))

    def is_point(self) -> bool:
        return self.kind == KIND_POINT


@dataclass
class Label:
    """One mark a reader made.

    `t1` is None for a point.  `f0` and `f1` are None whenever frequency is
    not meaningful -- on a trace, always, because that y axis is amplitude.
    `channel` is None for a label made on the mean spectrogram.
    """

    category: str
    kind: str = KIND_SPAN
    channel: Optional[int] = None
    t0: float = 0.0
    t1: Optional[float] = None
    f0: Optional[float] = None
    f1: Optional[float] = None
    note: str = ""

    def is_point(self) -> bool:
        return self.kind == KIND_POINT or self.t1 is None

    def t_end(self) -> float:
        """The time this label stops mattering; `t0` again for a point."""
        return self.t0 if self.t1 is None else self.t1

    def has_frequency(self) -> bool:
        return self.f0 is not None and self.f1 is not None

    def overlaps(self, t0: float, t1: float) -> bool:
        """Does this label reach into the window ``[t0, t1]``?

        Inclusive at both ends: a point exactly on the edge of the view is
        drawn, because a mark that vanishes when it reaches the edge reads as
        a mark that was deleted.
        """
        return self.t0 <= t1 and self.t_end() >= t0

    def on_channel(self, channel: int) -> bool:
        """Whether this label belongs on lane `channel`.

        A label with no channel belongs on all of them.  It was made on the
        mean spectrogram, which is an average over the array; drawing it on
        one arbitrary lane would be a claim about an electrode that nobody
        made.
        """
        return self.channel is None or self.channel == channel

    def row(self) -> list[str]:
        """This label as the eight cells of `COLUMNS`.

        Six decimals of seconds is 1 us, well under one frame at any rate
        audian opens; three decimals of Hz is far under one spectrogram bin.
        Both are fixed-point rather than repr() so a diff of two label files
        is readable.
        """
        return [
            self.category,
            self.kind,
            "" if self.channel is None else str(int(self.channel)),
            _cell(self.t0, 6),
            _cell(self.t1, 6),
            _cell(self.f0, 3),
            _cell(self.f1, 3),
            self.note,
        ]

    @staticmethod
    def from_row(row: dict) -> Optional["Label"]:
        """One CSV row as a label, or None when it cannot be placed.

        A row with no category or no start time is not an error and is not
        drawn at t=0: it is a row this viewer cannot place, and the caller
        counts it and says so.  Same rule as `session._read`.
        """
        category = (row.get("category") or "").strip()
        t0 = _number(row.get("t_start_s", ""))
        if not category or t0 is None:
            return None
        t1 = _number(row.get("t_end_s", ""))
        kind = (row.get("kind") or "").strip()
        if kind not in KINDS:
            kind = KIND_POINT if t1 is None else KIND_SPAN
        if kind == KIND_POINT:
            t1 = None
        elif t1 is None:
            # a span row that lost its end is a point in every way that
            # matters to a reader; say so rather than drawing a zero-width box
            kind = KIND_POINT
        elif t1 < t0:
            t0, t1 = t1, t0
        f0 = _number(row.get("f_low_hz", ""))
        f1 = _number(row.get("f_high_hz", ""))
        if f0 is not None and f1 is not None and f1 < f0:
            f0, f1 = f1, f0
        return Label(
            category=category,
            kind=kind,
            channel=_integer(row.get("channel", "")),
            t0=t0,
            t1=t1,
            f0=f0,
            f1=f1,
            note=(row.get("note") or "").strip(),
        )


@dataclass(frozen=True)
class ReadReport:
    """What `LabelSet.read` found, for the caller to report to the reader."""

    #: labels actually placed
    read: int = 0
    #: rows that named no category or no start time
    dropped: int = 0
    #: categories the settings did not know and that were added
    added: tuple[str, ...] = ()
    #: why the file could not be read at all, or "" when it could
    error: str = ""


class LabelSet:
    """The categories and the labels of one open recording.

    `revision` is bumped by every mutation and is what the overlays gate
    their redraw on.  `EventOverlay` has the same discipline and it is not
    decoration: its redraw is skipped whenever the view state and the
    revision are both unchanged, so a mutation that forgets to bump simply
    does not appear.
    """

    def __init__(self, categories: Iterable[LabelCategory] = ()):
        self._categories: list[LabelCategory] = []
        self.labels: list[Label] = []
        #: bumped by every mutation; see the class docstring
        self.revision = 0
        #: True while the store holds labels the sidecar does not
        self.dirty = False
        #: where `save` last wrote, or would write
        self.path: Optional[Path] = None
        #: Why this store must not write, or "".  Set by `read` whenever the
        #: sidecar existed and did not come back whole -- an OSError, a
        #: decoding failure, or rows that named no category or no start time.
        #:
        #: The alternative is a silent hole in someone's data: a file that
        #: read as empty because it could not be parsed looks exactly like a
        #: recording nobody has labelled yet, and the first label added to it
        #: would autosave over whatever was really in there.  An unreadable
        #: sidecar is not an empty one, so the store refuses to overwrite it
        #: and says why.
        self.blocked = ""
        self.set_categories(categories)
        self.revision = 0

    # --- categories -------------------------------------------------------

    @property
    def categories(self) -> tuple[LabelCategory, ...]:
        return tuple(self._categories)

    def __len__(self) -> int:
        return len(self.labels)

    def __iter__(self) -> Iterator[Label]:
        return iter(self.labels)

    def category(self, name: str) -> Optional[LabelCategory]:
        for c in self._categories:
            if c.name == name:
                return c
        return None

    def category_index(self, name: str) -> int:
        """Position of `name` in the vocabulary, or -1."""
        for i, c in enumerate(self._categories):
            if c.name == name:
                return i
        return -1

    def color_of(self, name: str) -> int:
        """Palette index of a category, falling back to 0 for an unknown one."""
        c = self.category(name)
        return 0 if c is None else c.color

    def next_color(self) -> int:
        """The palette index a new category should take.

        The lowest index no category is using, so a vocabulary that has had
        one entry removed reuses its colour instead of drifting up through
        the palette and wrapping onto a colour that is already on screen.
        """
        used = {c.color % 8 for c in self._categories}
        for i in range(8):
            if i not in used:
                return i
        return len(self._categories) % 8

    def set_categories(self, categories: Iterable[LabelCategory]) -> None:
        """Replace the whole vocabulary.

        Labels are left alone.  A category can be renamed out from under its
        rows -- the CSV addresses a category by name -- and the rows are kept
        rather than dropped, because the reader who renames a category has
        not said anything about the marks they made.  `read` puts the missing
        name back the next time the file is opened.
        """
        seen = set()
        kept = []
        for c in categories:
            c = c.normalized()
            if not c.name or c.name in seen:
                continue
            seen.add(c.name)
            kept.append(c)
        self._categories = kept
        self.revision += 1

    def add_category(self, name: str, kind: str = KIND_SPAN, color=None) -> bool:
        """Add one category.  False when the name is empty or already taken."""
        name = (name or "").strip()
        if not name or self.category(name) is not None:
            return False
        if color is None:
            color = self.next_color()
        self._categories.append(
            LabelCategory(name=name, kind=kind, color=int(color)).normalized()
        )
        self.revision += 1
        return True

    def remove_category(self, name: str) -> int:
        """Drop a category and every label under it.  Returns the rows lost.

        Deliberately destructive rather than orphaning: a label whose
        category the vocabulary has forgotten would be drawn in a fallback
        colour under a name nothing explains, and would be written back to
        the CSV forever.  The caller is the one that has to ask first, and
        the count is what it asks about.
        """
        index = self.category_index(name)
        if index < 0:
            return 0
        lost = [i for i, la in enumerate(self.labels) if la.category == name]
        for i in reversed(lost):
            del self.labels[i]
        del self._categories[index]
        self.revision += 1
        if lost:
            self.dirty = True
        return len(lost)

    def count_in(self, name: str) -> int:
        """How many labels carry that category."""
        return sum(1 for la in self.labels if la.category == name)

    # --- labels -----------------------------------------------------------

    def add(self, label: Label) -> Label:
        """Store one label, and mark the set unsaved."""
        self.labels.append(label)
        self.revision += 1
        self.dirty = True
        return label

    def remove(self, index: int) -> Optional[Label]:
        """Drop the label at `index`, and return it.  None when out of range."""
        if not (-len(self.labels) <= index < len(self.labels)):
            return None
        label = self.labels.pop(index)
        self.revision += 1
        self.dirty = True
        return label

    def remove_last(self) -> Optional[Label]:
        """Undo the last `add`.  The whole of this feature's undo."""
        return self.remove(-1) if self.labels else None

    def clear(self) -> None:
        """Forget every label.  The vocabulary is a preference and survives."""
        if self.labels:
            self.labels = []
            self.revision += 1
            self.dirty = True

    def set_note(self, index: int, note: str) -> bool:
        if not (0 <= index < len(self.labels)):
            return False
        self.labels[index].note = note
        self.revision += 1
        self.dirty = True
        return True

    def window(self, t0: float, t1: float, channels=None) -> list:
        """``(index, label)`` for everything reaching into ``[t0, t1]``.

        The index comes with the label because a label has no identity of its
        own -- it is addressed by its position in this list, the way an event
        of the immutable bundle is addressed by ``(layer, series, row)``.

        `channels` is None for every lane, one channel number, or a
        collection of them.  A collection is what the mean spectrogram
        wants: it is one panel standing for a whole selected array, so the
        labels of every channel it averages belong on it.
        """
        if channels is None:
            wanted = None
        elif isinstance(channels, int):
            wanted = {channels}
        else:
            wanted = {int(c) for c in channels}
        out = []
        for i, la in enumerate(self.labels):
            if not la.overlaps(t0, t1):
                continue
            if wanted is not None and la.channel is not None:
                if la.channel not in wanted:
                    continue
            out.append((i, la))
        return out

    # --- the sidecar ------------------------------------------------------

    def read(self, path: Path | str) -> ReadReport:
        """Replace the labels with the ones in `path`.

        A missing file is not an error: it is the state every recording
        starts in, and it reads as an empty set.  A category the vocabulary
        does not know is added with the next free palette colour and reported,
        so a file written on another machine arrives complete.

        A file that did *not* come back whole sets `blocked`, and the store
        then refuses to write over it.  See that attribute for why: the whole
        failure mode is a sidecar that reads as empty for a reason nobody
        sees, and one label added over the top of it.
        """
        path = Path(path)
        self.path = path
        self.labels = []
        self.revision += 1
        self.dirty = False
        self.blocked = ""
        if not path.exists():
            return ReadReport()
        try:
            with open(path, newline="", encoding="utf-8") as fp:
                rows = list(csv.DictReader(fp))
        except (OSError, UnicodeDecodeError, csv.Error) as e:
            self.blocked = f"{path.name} could not be read ({e})"
            return ReadReport(error=str(e))
        dropped = 0
        added = []
        for row in rows:
            label = Label.from_row(row)
            if label is None:
                dropped += 1
                continue
            if self.category(label.category) is None:
                self.add_category(label.category, label.kind)
                added.append(label.category)
            self.labels.append(label)
        if dropped:
            self.blocked = (
                f"{dropped} row(s) of {path.name} name no category or no start time"
            )
        self.revision += 1
        return ReadReport(read=len(self.labels), dropped=dropped, added=tuple(added))

    def write(self, path: Path | str | None = None) -> str:
        """Write the sidecar atomically.  Returns "" or the error to report.

        Hand-made labels are the only user-authored data this application
        holds, and nothing else in the tree writes atomically -- every other
        write is a plain ``open(path, "w")``, so an interrupted one truncates
        the real file.  This one writes a temp file in the same directory,
        fsyncs it and `os.replace`s it into place, which is atomic within a
        filesystem: a reader either sees the whole previous file or the whole
        new one, never a truncated one.

        The temp file is in the same directory and not in the system temp,
        because `os.replace` across filesystems is not atomic and raises.

        Never raises.  The caller reports the returned message to the reader
        rather than logging it -- a settings write that fails quietly costs a
        preference, and this one would cost their work.
        """
        path = Path(path) if path is not None else self.path
        if path is None:
            return "no path for the labels"
        if self.blocked:
            return f"refusing to overwrite labels: {self.blocked}"
        self.path = path
        tmp = path.with_name(path.name + ".tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp, "w", newline="", encoding="utf-8") as fp:
                writer = csv.writer(fp)
                writer.writerow(COLUMNS)
                for label in self.labels:
                    writer.writerow(label.row())
                fp.flush()
                os.fsync(fp.fileno())
            os.replace(tmp, path)
        except OSError as e:
            try:
                tmp.unlink()
            except OSError:
                pass
            return str(e)
        self.dirty = False
        return ""

    def discard(self) -> str:
        """Remove the sidecar, for a set that has been emptied.

        A file left holding a header and no rows says "these labels were
        written and then all deleted", which is true but is not what an
        empty set means to the next reader; and leaving the last row's file
        behind after the reader removed that row would put it back at the
        next open.

        Blocked by the same rule as `write`, and more obviously: deleting a
        file this session could not read is the worst of the two.
        """
        if self.blocked:
            return f"refusing to remove labels: {self.blocked}"
        if self.path is None or not self.path.exists():
            self.dirty = False
            return ""
        try:
            self.path.unlink()
        except OSError as e:
            return str(e)
        self.dirty = False
        return ""

    def save(self, path: Path | str | None = None) -> str:
        """Persist the set: write it, or remove the sidecar when it is empty."""
        if self.labels:
            return self.write(path)
        if path is not None:
            self.path = Path(path)
        return self.discard()


# --- settings ---------------------------------------------------------------


def categories_to_settings(categories: Iterable[LabelCategory]) -> list:
    """The vocabulary as JSON-able values for audian's settings file."""
    return [{"name": c.name, "kind": c.kind, "color": int(c.color)} for c in categories]


def categories_from_settings(values) -> list[LabelCategory]:
    """The vocabulary out of the settings file.  Never raises.

    A malformed entry is skipped rather than defaulted: a category with no
    name is a category the reader cannot pick, and inventing one would put a
    word in the bar that nobody chose.
    """
    out: list[LabelCategory] = []
    if not isinstance(values, (list, tuple)):
        return out
    seen = set()
    for value in values:
        if not isinstance(value, dict):
            continue
        name = str(value.get("name", "")).strip()
        if not name or name in seen:
            continue
        seen.add(name)
        color = value.get("color", len(out))
        try:
            color = int(color)
        except (TypeError, ValueError):
            color = len(out)
        out.append(
            LabelCategory(
                name=name, kind=str(value.get("kind", KIND_SPAN)), color=color
            ).normalized()
        )
    return out


#: The vocabulary a reader who has never opened the editor starts with.
#: One of each kind, so both gestures are reachable on the first recording
#: without opening a dialog, and both obviously renameable.
DEFAULT_CATEGORIES: tuple[LabelCategory, ...] = (
    LabelCategory(name="event", kind=KIND_SPAN, color=0),
    LabelCategory(name="pulse", kind=KIND_POINT, color=1),
)
