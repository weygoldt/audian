"""Tracked frequency bands: the store, the edits, and the two files they live in.

A **band** is one signal followed through a spectrogram: a polyline of
``(time, frequency)`` vertices carrying an identity.  A fish's electric organ
discharge over an hour, a cricket's carrier across a chirp, a harmonic that
comes and goes -- one band each.  This module is the whole of what a band is
and what may be done to one.  Nothing here imports Qt, so the arithmetic can
be exercised without a window; `overlay` and `panel` are the halves that have
a reader in them.

Why this is not a `Label`
-------------------------

audian already has an editable label, and a band is deliberately not one.  A
`labels.Label` is a *rectangle*: ``t0..t1`` by ``f0..f1``, four numbers.  That
is the right shape for "a call happened here", and the wrong shape for a
frequency that moves -- a band that drifts from 700 Hz to 900 Hz over ten
minutes has a bounding box covering 200 Hz of spectrogram it never occupied,
and two bands crossing each other have identical boxes.  Boxes cannot express
identity through a crossing, which is the one question this whole interface
exists to answer.

So bands are their own store, with their own file, drawn by their own
overlay.  They sit *beside* the labels rather than inside them, and a reader
who wants a box still has boxes.

Why two files
-------------

`save` writes two, and neither is redundant:

``<stem>-frequency-bands.csv``
    One row per band -- identity, category, extent, vertex count, note.
    Small, diffable, hand-editable, and the file a reader actually reads.
    This is the layer that carries the reader's *claims*.

``<stem>-frequency-bands.npz``
    The geometry: every vertex of every band, in CSR-style flat arrays.
    Machine-written and machine-read.

One file was tried both ways on paper and neither works.  A CSV with one row
per *vertex* is exact but enormous and unreadable: the reference cricket
recording -- 78 s at nfft 256, hop 128, so 344 frames a second -- gives a band
spanning the file 27000 vertices, and five such bands make a four megabyte
"hand-editable" file in which the category is repeated 135000 times and can
therefore disagree with itself.  A CSV of bounding boxes alone is readable and
*lossy*, which is the one thing a curation tool may not be: the vertices are
the work.

So: the exact thing in a binary file, and the readable thing in a text file,
with the text file naming which band each row is about.  `read` takes geometry
from the ``.npz`` and category and note from the ``.csv``, joined on the band
id, and reports rather than guesses when they disagree.

What is *not* written
---------------------

wavetracker's own ``.npy`` files.  `wavetracker.EODsorter` loaded
``all_fund_v.npy``, ``all_ident_v.npy`` and their siblings from a directory
and saved the edited identities straight back over them, with no backup, no
temporary file and no version -- so an interrupted save left a recording's
tracking permanently truncated, and a bad merge was unrecoverable the moment
it was written.  Here the ``.npy`` directory is an *import source* and is
opened read-only.  Everything this plugin writes goes beside the recording,
under the recording's own stem, which is where every other audian sidecar
already lives.

Both files are written to a temporary name in the same directory and then
`os.replace`-d into place, which is atomic on POSIX and on Windows.  A save
interrupted at any point leaves either the old file or the new one, never a
half of either.
"""

from __future__ import annotations

import csv
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Iterable, Iterator, Optional

import numpy as np

#: Appended to the recording's stem for the readable half.
#:
#: Deliberately not ``-labels.csv`` and not ``-editable-labels.csv``: that
#: suffix is `labels.SIDECAR_SUFFIX` and naming a second file the same thing
#: with different columns is how a reader loses one of them.
CSV_SUFFIX = "-frequency-bands.csv"

#: Appended to the recording's stem for the geometry.
NPZ_SUFFIX = "-frequency-bands.npz"

#: Column order of the readable half, and the order `Band.row` writes.
COLUMNS = (
    "band",
    "category",
    "channel",
    "t_start_s",
    "t_end_s",
    "f_min_hz",
    "f_max_hz",
    "f_mean_hz",
    "n_points",
    "note",
)

#: Written into the ``.npz`` so a file from a later plugin is refused rather
#: than misread.  Bumped only when the array layout changes.
FORMAT_VERSION = 1


def csv_path(recording: Path | str) -> Path:
    """Where the readable half of `recording`'s bands is kept."""
    recording = Path(recording)
    return recording.with_name(recording.stem + CSV_SUFFIX)


def npz_path(recording: Path | str) -> Path:
    """Where the geometry of `recording`'s bands is kept."""
    recording = Path(recording)
    return recording.with_name(recording.stem + NPZ_SUFFIX)


def _number(text: str) -> Optional[float]:
    """A cell as a float, or None when it is empty or not a number.

    Never raises.  The CSV is hand-editable by design, and one bad cell must
    cost that cell rather than the recording's bands.
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
class Band:
    """One signal followed through the spectrogram.

    `times` and `freqs` are parallel arrays of equal length, ascending in
    time.  A band with fewer than one vertex cannot be drawn and is not
    allowed into a `BandSet`; a band with exactly one is a legitimate single
    detection, and is drawn as a dot.

    Frozen, and the arrays are never mutated in place.  Every edit produces
    new bands, which is what makes `BandSet.undo` able to put the old ones
    back by holding a reference rather than a copy.
    """

    #: Identity.  Unique within a `BandSet`, stable across a save and load,
    #: and what the CSV row is joined on.
    bid: int
    times: np.ndarray
    freqs: np.ndarray
    #: The electrode this band was tracked on.  None means "not one channel"
    #: -- what a band tracked on the mean spectrogram writes, matching the
    #: nullable ``channel`` of `labels.Label`.
    channel: Optional[int] = None
    #: The reader's claim about what this band is.  Empty until they make
    #: one, which is the state most bands are in when they arrive.
    category: str = ""
    note: str = ""

    def __post_init__(self) -> None:
        # Normalising in the constructor rather than trusting callers: bands
        # arrive from a tracker, from an importer and from a file, and a
        # dtype or a shape that is wrong in one of those three would
        # otherwise be found by the renderer, at which point the traceback
        # names pyqtgraph and not the importer that produced it.
        times = np.asarray(self.times, dtype=np.float64).ravel()
        freqs = np.asarray(self.freqs, dtype=np.float64).ravel()
        if times.size != freqs.size:
            raise ValueError(
                f"band {self.bid}: {times.size} times and {freqs.size} "
                "frequencies; a vertex needs both"
            )
        if times.size == 0:
            raise ValueError(f"band {self.bid}: a band needs at least one vertex")
        if times.size > 1 and np.any(np.diff(times) < 0):
            order = np.argsort(times, kind="stable")
            times, freqs = times[order], freqs[order]
        object.__setattr__(self, "times", times)
        object.__setattr__(self, "freqs", freqs)

    def __len__(self) -> int:
        return int(self.times.size)

    @property
    def t0(self) -> float:
        return float(self.times[0])

    @property
    def t1(self) -> float:
        return float(self.times[-1])

    @property
    def f_min(self) -> float:
        return float(np.min(self.freqs))

    @property
    def f_max(self) -> float:
        return float(np.max(self.freqs))

    @property
    def f_mean(self) -> float:
        return float(np.mean(self.freqs))

    def overlaps(self, t0: float, t1: float) -> bool:
        """Does this band reach into the window ``[t0, t1]``?

        Closed on both ends: a band that starts exactly at `t1` is touching
        the window and is drawn, because a mark that vanishes at the edge of
        the view looks like a mark that was deleted.
        """
        return self.t1 >= t0 and self.t0 <= t1

    def row(self) -> list:
        """This band as one CSV row, in `COLUMNS` order."""
        return [
            str(self.bid),
            self.category,
            "" if self.channel is None else str(int(self.channel)),
            _cell(self.t0, 6),
            _cell(self.t1, 6),
            _cell(self.f_min, 3),
            _cell(self.f_max, 3),
            _cell(self.f_mean, 3),
            str(len(self)),
            self.note,
        ]


@dataclass(frozen=True)
class Edit:
    """One undoable change, as the bands it removed and the ones it added.

    Every edit in this module is expressible that way -- a split removes one
    band and adds two, a merge removes several and adds one, a delete removes
    one and adds none, and a re-category removes one and adds one that differs
    only in its `Band.category`.  So there is one undo mechanism rather than
    five, and adding a new kind of edit does not mean writing its inverse by
    hand and getting it subtly wrong.

    It holds the `Band` objects themselves, not copies of their arrays.  Bands
    are frozen and never mutated in place, so the band an edit is holding *is*
    the band as it was, and undo is a re-insertion rather than a restore from
    a snapshot: undoing a merge of two hour-long bands costs a dictionary
    write, not 27000 floats.
    """

    #: What the reader sees, in the imperative and lower case, so that it
    #: reads as "Undo split band 4 at 12.500 s".
    what: str
    removed: tuple = ()
    added: tuple = ()


#: How many edits deep the history goes.
#:
#: Bounded because an edit holds its bands alive, and an unbounded history in
#: a session that re-tracks a long recording several times would keep every
#: version of every band for as long as the window is open.  Two hundred is
#: well past the point where a reader would rather reload the file than keep
#: pressing Ctrl+Z.
HISTORY_DEPTH = 200


class BandSet:
    """The bands of one recording, and the history of what was done to them.

    Every mutator returns the `Edit` it performed and pushes it on the undo
    stack, so a caller that wants to tell the reader what happened has the
    description without composing one, and a caller that wants to offer Undo
    has the step without looking it up.
    """

    def __init__(self, bands: Iterable[Band] = ()) -> None:
        self._bands: dict[int, Band] = {}
        #: Monotonic, and never reset by a deletion.
        #:
        #: `EODsorter.new_assign` took ``max(ident_v) + 1``, which re-issues
        #: the id of a deleted identity to the next new one.  A note, an
        #: export, or a colleague's message saying "band 7 is the female"
        #: then refers to a different animal than it did an hour before, and
        #: nothing on screen says so.  Here an id is spent once.
        self._next_bid = 1
        self._undo: list[Edit] = []
        self._redo: list[Edit] = []
        #: Bumped on every change, stamped by `mark_saved`, so "are there
        #: unsaved edits" is a comparison rather than a flag that somebody
        #: has to remember to set on every path.
        self.revision = 0
        self._saved_revision = 0
        for band in bands:
            self._insert(band)
        self.revision = 0
        self._saved_revision = 0

    # --- reading ----------------------------------------------------------

    def __len__(self) -> int:
        return len(self._bands)

    def __iter__(self) -> Iterator[Band]:
        """Bands in ascending id, which is the order they were created in."""
        for bid in sorted(self._bands):
            yield self._bands[bid]

    def __contains__(self, bid: object) -> bool:
        return bid in self._bands

    def get(self, bid: int) -> Optional[Band]:
        return self._bands.get(int(bid))

    def ids(self) -> list:
        return sorted(self._bands)

    def categories(self) -> list:
        """Every label in use, sorted; the unlabelled ones are not one."""
        return sorted({b.category for b in self._bands.values() if b.category})

    def in_window(self, t0: float, t1: float) -> list:
        """Every band reaching into ``[t0, t1]``, in ascending id."""
        return [b for b in self if b.overlaps(t0, t1)]

    def time_span(self) -> Optional[tuple]:
        """``(first, last)`` over every band, or None when there are none."""
        if not self._bands:
            return None
        return (
            min(b.t0 for b in self._bands.values()),
            max(b.t1 for b in self._bands.values()),
        )

    def is_dirty(self) -> bool:
        return self.revision != self._saved_revision

    def mark_saved(self) -> None:
        self._saved_revision = self.revision

    # --- the one primitive ------------------------------------------------

    def _insert(self, band: Band) -> Band:
        if band.bid in self._bands:
            raise ValueError(f"band {band.bid} is already in this set")
        self._bands[band.bid] = band
        self._next_bid = max(self._next_bid, int(band.bid) + 1)
        self.revision += 1
        return band

    def _drop(self, bid: int) -> Band:
        band = self._bands.pop(int(bid))
        self.revision += 1
        return band

    def _apply(self, what: str, removed: Iterable, added: Iterable) -> Edit:
        """Perform one change and record it -- the only place that does both.

        Removals happen first, so that an edit which replaces a band with
        another of the same id (every re-category, every note) does not
        collide with itself in `_insert`.
        """
        removed = tuple(removed)
        added = tuple(added)
        for band in removed:
            self._drop(band.bid)
        for band in added:
            self._insert(band)
        edit = Edit(what, removed, added)
        self._undo.append(edit)
        del self._undo[:-HISTORY_DEPTH]
        # A new edit is a new branch: what was undone is no longer reachable,
        # and keeping it would let Redo re-add a band that a later split has
        # since replaced, leaving two bands claiming the same vertices.
        self._redo.clear()
        return edit

    def new_id(self) -> int:
        return self._next_bid

    def add(self, times, freqs, channel=None, category="", note="") -> Edit:
        """One band, as a tracker or the reader just made it."""
        band = Band(self._next_bid, times, freqs, channel, category, note)
        return self._apply(f"add band {band.bid}", (), (band,))

    def add_many(self, bands: Iterable, what: str = "") -> Edit:
        """Several bands as *one* undoable step.

        A tracker run produces forty bands and is one thing the reader did;
        forty undo entries to get back to where they started is not a history,
        it is a punishment.  Items are `Band` instances or ``(times, freqs)``
        pairs, and any id they carry is ignored: ids are this set's to issue.
        """
        made = []
        bid = self._next_bid
        for item in bands:
            if isinstance(item, Band):
                made.append(replace(item, bid=bid))
            else:
                times, freqs = item
                made.append(Band(bid, times, freqs))
            bid += 1
        if not made:
            return Edit(what or "add no bands")
        return self._apply(what or f"add {len(made)} bands", (), tuple(made))

    def delete(self, bid: int) -> Edit:
        band = self._bands[int(bid)]
        return self._apply(f"delete band {band.bid}", (band,), ())

    def delete_many(self, bids: Iterable) -> Edit:
        bands = tuple(self._bands[int(b)] for b in bids)
        if not bands:
            return Edit("delete no bands")
        what = (
            f"delete band {bands[0].bid}"
            if len(bands) == 1
            else f"delete {len(bands)} bands"
        )
        return self._apply(what, bands, ())

    def set_category(self, bid: int, category: str) -> Edit:
        band = self._bands[int(bid)]
        category = (category or "").strip()
        what = (
            f"label band {band.bid} {category!r}"
            if category
            else f"clear the label of band {band.bid}"
        )
        return self._apply(what, (band,), (replace(band, category=category),))

    def set_category_many(self, bids: Iterable, category: str) -> Edit:
        """Label several bands as *one* undoable step.

        Labelling a selection of twelve is one thing the reader did, and
        twelve presses of Undo to take it back is not a history.  Every other
        multi-band edit here is already one step -- `delete_many`, `merge`,
        `add_many` -- and this was the one that was not.
        """
        bids = [int(b) for b in bids if int(b) in self._bands]
        if not bids:
            return Edit("label no bands")
        if len(bids) == 1:
            return self.set_category(bids[0], category)
        category = (category or "").strip()
        removed = tuple(self._bands[b] for b in bids)
        added = tuple(replace(band, category=category) for band in removed)
        what = (
            f"label {len(bids)} bands {category!r}"
            if category
            else f"clear the labels of {len(bids)} bands"
        )
        return self._apply(what, removed, added)

    def set_note(self, bid: int, note: str) -> Edit:
        band = self._bands[int(bid)]
        return self._apply(
            f"note band {band.bid}", (band,), (replace(band, note=note),)
        )

    def split(self, bid: int, t: float) -> Edit:
        """Cut one band in two at `t`, the later part starting at or after it.

        The vertex *at* `t` begins the second band, so a cut on a vertex keeps
        that vertex and a cut between two keeps both: no vertex is ever
        consumed by the cut itself.  A cut outside the band would leave one
        side empty, and raises rather than making an empty band.

        `EODsorter.cut` did this by rewriting ``ident_v`` in place for every
        detection at or before the cut, with no way back.  Here the original
        band is held by the returned `Edit`, so Ctrl+Z puts it back whole.
        """
        band = self._bands[int(bid)]
        cut = int(np.searchsorted(band.times, float(t), side="left"))
        if cut <= 0 or cut >= len(band):
            raise ValueError(
                f"band {band.bid} spans {band.t0:.3f}-{band.t1:.3f} s; a cut "
                f"at {float(t):.3f} s would leave one side of it empty"
            )
        first = replace(
            band, bid=self._next_bid, times=band.times[:cut], freqs=band.freqs[:cut]
        )
        second = replace(
            band,
            bid=self._next_bid + 1,
            times=band.times[cut:],
            freqs=band.freqs[cut:],
        )
        return self._apply(
            f"split band {band.bid} at {float(t):.3f} s", (band,), (first, second)
        )

    def merge(self, bids: Iterable) -> Edit:
        """Join bands into one, keeping every vertex.

        The vertices of all of them, ordered by time.  **Nothing is dropped**,
        and that is the whole difference from what this replaces:
        `EODsorter.connect` found the detections the two traces held at the
        same time steps and set their identity to NaN -- silently discarding
        real detections so that the result would be a function of time.  A
        merge undertaken to repair a tracking error therefore destroyed data
        exactly when the two traces overlapped, which is exactly when a reader
        reaches for it.  Here an overlap survives as two vertices at the same
        time, and `merge_conflicts` says so before the reader commits.
        """
        bids = [int(b) for b in bids]
        if len(bids) < 2:
            raise ValueError("a merge needs at least two bands")
        bands = sorted((self._bands[b] for b in bids), key=lambda b: b.t0)
        times = np.concatenate([b.times for b in bands])
        freqs = np.concatenate([b.freqs for b in bands])
        order = np.argsort(times, kind="stable")
        notes = [b.note for b in bands if b.note]
        channels = {b.channel for b in bands}
        merged = Band(
            self._next_bid,
            times[order],
            freqs[order],
            channel=channels.pop() if len(channels) == 1 else None,
            category=next((b.category for b in bands if b.category), ""),
            note=" / ".join(notes),
        )
        named = ", ".join(str(b.bid) for b in bands)
        return self._apply(f"merge bands {named}", tuple(bands), (merged,))

    # --- history ----------------------------------------------------------

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo_text(self) -> str:
        return self._undo[-1].what if self._undo else ""

    def redo_text(self) -> str:
        return self._redo[-1].what if self._redo else ""

    def forget_history(self) -> None:
        """Drop the history without touching the bands.

        For the moment a whole set is replaced -- a file loaded, a tracker
        re-run over the recording -- when the steps before it describe bands
        that no longer exist.
        """
        self._undo.clear()
        self._redo.clear()

    def undo(self) -> Optional[Edit]:
        if not self._undo:
            return None
        edit = self._undo.pop()
        for band in edit.added:
            self._drop(band.bid)
        for band in edit.removed:
            self._insert(band)
        self._redo.append(edit)
        return edit

    def redo(self) -> Optional[Edit]:
        if not self._redo:
            return None
        edit = self._redo.pop()
        for band in edit.removed:
            self._drop(band.bid)
        for band in edit.added:
            self._insert(band)
        self._undo.append(edit)
        return edit


def merge_conflicts(bands: list) -> list:
    """What a merge of `bands` would otherwise decide on the reader's behalf.

    Returned rather than resolved, so the interface can say "these three carry
    two different labels" *before* the merge, instead of the reader finding out
    afterwards that one of them won.  Empty when the merge is unambiguous,
    which is the common case and the one that must not ask a question.
    """
    notes = []
    labelled = {b.category for b in bands if b.category}
    if len(labelled) > 1:
        named = ", ".join(sorted(repr(c) for c in labelled))
        notes.append(f"different labels ({named}); the earliest in time wins")
    if len({b.channel for b in bands}) > 1:
        notes.append("different channels; the merged band claims none")
    ordered = sorted(bands, key=lambda b: b.t0)
    overlap = sum(
        max(0.0, first.t1 - second.t0) for first, second in zip(ordered, ordered[1:])
    )
    if overlap > 0:
        notes.append(
            f"they overlap by {overlap:.3f} s; every vertex is kept, so the "
            "merged band has two frequencies at those times"
        )
    return notes


# --- the files ------------------------------------------------------------


def _replace_atomically(path: Path, write: Callable) -> None:
    """Write `path` via a temporary neighbour, then rename it into place.

    `os.replace` is atomic on POSIX and on Windows, so a save interrupted at
    any point -- a full disk, a killed process, a laptop lid -- leaves either
    the whole old file or the whole new one.

    This is the single most important difference from what this replaces.
    `EODsorter.save` wrote ``np.save`` straight over ``all_ident_v.npy``, the
    only copy of a recording's tracking, with no temporary file and no backup:
    an interruption during that write left the array truncated, and there was
    nothing to go back to.

    The temporary lives in the same directory as its target, because
    `os.replace` across a filesystem boundary is not atomic and raises on
    some platforms -- and a recording on a mounted drive with ``/tmp`` on the
    root disk is the ordinary case here, not an exotic one.
    """
    tmp = path.with_name(f".{path.name}.tmp{os.getpid()}")
    try:
        write(tmp)
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def write(bandset: BandSet, recording: Path | str) -> tuple:
    """Write both files beside `recording`; return the paths written.

    An empty set still writes both files.  The alternative -- deleting them,
    or leaving the previous ones -- means a reader who deliberately removed
    every band reopens the recording to find them all back, which is
    indistinguishable from the save having failed.
    """
    bands = list(bandset)
    csv_file = csv_path(recording)
    npz_file = npz_path(recording)

    def _write_csv(path: Path) -> None:
        with open(path, "w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(COLUMNS)
            for band in bands:
                writer.writerow(band.row())

    def _write_npz(path: Path) -> None:
        offsets = np.zeros(len(bands) + 1, dtype=np.int64)
        if bands:
            offsets[1:] = np.cumsum([len(b) for b in bands])
        with open(path, "wb") as stream:
            np.savez_compressed(
                stream,
                version=np.array(FORMAT_VERSION, dtype=np.int64),
                ids=np.array([b.bid for b in bands], dtype=np.int64),
                offsets=offsets,
                times=(
                    np.concatenate([b.times for b in bands])
                    if bands
                    else np.zeros(0, dtype=np.float64)
                ),
                freqs=(
                    np.concatenate([b.freqs for b in bands])
                    if bands
                    else np.zeros(0, dtype=np.float64)
                ),
                # NaN for "no channel": an integer array cannot hold the
                # absence that `Band.channel` is allowed to be, and a
                # sentinel like -1 is a number somebody downstream averages.
                channels=np.array(
                    [np.nan if b.channel is None else float(b.channel) for b in bands],
                    dtype=np.float64,
                ),
            )

    _replace_atomically(npz_file, _write_npz)
    _replace_atomically(csv_file, _write_csv)
    bandset.mark_saved()
    return (csv_file, npz_file)


def _read_geometry(path: Path, complaints: list) -> list:
    """The bands of a ``.npz``, or an empty list with a complaint saying why."""
    try:
        with np.load(path) as data:
            version = int(data["version"])
            if version > FORMAT_VERSION:
                complaints.append(
                    f"{path.name} was written by a newer version of this "
                    f"plugin (format {version}, this one reads {FORMAT_VERSION}); "
                    "it was not read, and saving would overwrite it"
                )
                return []
            ids = np.asarray(data["ids"], dtype=np.int64)
            offsets = np.asarray(data["offsets"], dtype=np.int64)
            times = np.asarray(data["times"], dtype=np.float64)
            freqs = np.asarray(data["freqs"], dtype=np.float64)
            channels = np.asarray(data["channels"], dtype=np.float64)
    except (OSError, KeyError, ValueError) as exc:
        complaints.append(f"{path.name} could not be read ({exc}); no bands loaded")
        return []
    if offsets.size != ids.size + 1 or channels.size != ids.size:
        complaints.append(
            f"{path.name} is inconsistent ({ids.size} ids, "
            f"{offsets.size} offsets, {channels.size} channels); no bands loaded"
        )
        return []
    bands = []
    for i, bid in enumerate(ids):
        start, stop = int(offsets[i]), int(offsets[i + 1])
        if not 0 <= start <= stop <= times.size or stop <= start:
            complaints.append(f"band {int(bid)} has an unusable extent; skipped")
            continue
        channel = channels[i]
        bands.append(
            Band(
                int(bid),
                times[start:stop],
                freqs[start:stop],
                None if np.isnan(channel) else int(channel),
            )
        )
    return bands


def read(recording: Path | str) -> tuple:
    """The bands beside `recording`, and everything wrong with them.

    Returns ``(BandSet, complaints)``.  It never raises for a damaged file:
    a recording whose sidecar is broken must still open, with the damage
    reported, because the alternative is an application that cannot be
    started to fix the file that stops it starting.

    Geometry comes from the ``.npz`` and the reader's claims from the
    ``.csv``, joined on the band id.  When the two disagree -- a row for a
    band the geometry does not have, or a band the CSV never mentions --
    both are kept and both are reported.  `EODsorter` loaded five ``.npy``
    files with no check that they described the same recording or even had
    compatible lengths, so a stale ``all_ident_v.npy`` beside a fresh
    ``all_fund_v.npy`` produced a confidently wrong picture and no warning.
    """
    complaints: list = []
    npz_file = npz_path(recording)
    csv_file = csv_path(recording)
    if not npz_file.exists():
        if csv_file.exists():
            complaints.append(
                f"{csv_file.name} is here but {npz_file.name} is not, so the "
                "bands have their labels and no shape; nothing was loaded"
            )
        return BandSet(), complaints

    bands = _read_geometry(npz_file, complaints)
    claims: dict = {}
    if csv_file.exists():
        try:
            with open(csv_file, newline="", encoding="utf-8") as stream:
                for row in csv.DictReader(stream):
                    bid = _integer(row.get("band", ""))
                    if bid is None:
                        continue
                    claims[bid] = (
                        (row.get("category") or "").strip(),
                        (row.get("note") or "").strip(),
                    )
        except (OSError, csv.Error) as exc:
            complaints.append(
                f"{csv_file.name} could not be read ({exc}); the bands were "
                "loaded without their labels"
            )
    known = {b.bid for b in bands}
    orphans = sorted(set(claims) - known)
    if orphans:
        named = ", ".join(str(o) for o in orphans[:5])
        more = "" if len(orphans) <= 5 else f" and {len(orphans) - 5} more"
        complaints.append(
            f"{csv_file.name} labels band {named}{more}, which the geometry "
            "does not contain; those rows were ignored"
        )
    bands = [
        replace(b, category=claims.get(b.bid, ("", ""))[0], note=claims.get(b.bid, ("", ""))[1])
        for b in bands
    ]
    return BandSet(bands), complaints
