"""Reading a wavetracker output directory, without writing to it.

wavetracker stores a tracked recording as a handful of parallel ``.npy``
arrays in a directory beside it, and `wavetracker.EODsorter` is the program
that curated them.  This reads that directory so its output can be curated
here instead; nothing in this module opens a file for writing.

The arrays and the invariant
----------------------------

Four arrays of equal length, one entry per *detection* -- a peak in one frame
of the spectrogram -- plus one that is a time axis:

``fund_v``
    The detection's fundamental frequency, in Hz.
``idx_v``
    Its index into ``times``.  Detections are not one per frame: a frame with
    three fish in it contributes three entries with the same ``idx_v``.
``ident_v``
    Which identity it was assigned to, as a float, and **NaN when it was
    assigned to none**.  This is why the arrays are float and why every
    comparison against them has to exclude NaN first.
``sign_v``
    Its power at each electrode, ``(n_detections, n_channels)``.  **Not read
    here**, and that is a limitation worth naming rather than hiding: the
    per-electrode amplitude signature is the only thing that separates two
    fish at the same frequency, so it is what a band's channel would have to
    be derived from, and imported bands therefore carry no channel at all.
    Using it is a tracking job rather than a curation one, and doing it
    badly would put a confident number in a column that is allowed to be
    empty.
``times``
    Seconds, one per spectrogram frame, and the only array indexed rather
    than parallel.

So one band is ``times[idx_v[ident_v == i]]`` against ``fund_v[ident_v == i]``,
which is exactly the expression `EODsorter.plot_traces` drew.

Two namings
-----------

``all_fund_v.npy`` and friends for a multi-electrode recording, and bare
``fund_v.npy`` for a single channel.  Both are accepted, the prefixed set
first, which is the order `EODsorter.open` tried them in.

What is checked, and why that is the point
------------------------------------------

`EODsorter.open` loaded five files and checked nothing about them -- not that
they were the same length, not that ``idx_v`` was inside ``times``, not that
they described the same run.  A stale ``all_ident_v.npy`` beside a freshly
recomputed ``all_fund_v.npy`` is the ordinary way that happens, and it
produces a picture that is confidently wrong: identities drawn at other
detections' frequencies, with nothing on screen to say so.  Worse, the
program then saved that picture back over the inputs.

Here every array is checked against the others before a single band is built,
and anything that fails is *reported and skipped* rather than guessed at.
The directory is never written to, so a mistaken import costs an undo.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

#: The prefixed set first: `EODsorter.open` tried ``all_fund_v.npy`` before
#: the bare name, and a directory holding both is a multi-channel run whose
#: bare files are one channel of it.
PREFIXES = ("all_", "")

#: Arrays that must be present and parallel.
DETECTION_ARRAYS = ("fund_v", "idx_v", "ident_v")


def find_arrays(folder: Path) -> tuple:
    """``(prefix, paths)`` for the first complete set in `folder`.

    ``(None, {})`` when neither naming is complete, which is what an
    unrelated directory looks like and is not an error worth raising.
    """
    folder = Path(folder)
    for prefix in PREFIXES:
        names = {name: folder / f"{prefix}{name}.npy" for name in DETECTION_ARRAYS}
        names["times"] = folder / f"{prefix}times.npy"
        if all(path.exists() for path in names.values()):
            return prefix, names
    return None, {}


def import_directory(folder: Path) -> tuple:
    """The bands of a wavetracker directory, and everything wrong with it.

    Returns ``(bands, complaints)`` where `bands` is a list of
    ``(times, freqs)`` pairs ready for `bands.BandSet.add_many`, ordered by
    start time, and `complaints` is prose for the message log.

    Unassigned detections -- ``ident_v`` NaN -- are not imported.  They are
    what the tracker declined to attribute to anything, there are usually far
    more of them than there are real detections, and importing them would
    bury the bands a reader came to curate under a fog of one-vertex
    fragments.  `EODsorter` had a toggle for showing them and this does not
    yet; that is a real capability left out, and the honest place to say so
    is here.
    """
    folder = Path(folder)
    complaints: list = []
    prefix, paths = find_arrays(folder)
    if prefix is None:
        complaints.append(
            f"{folder.name} holds no wavetracker arrays; expected "
            "all_fund_v.npy and its siblings, or the unprefixed names"
        )
        return [], complaints

    try:
        fund_v = np.load(paths["fund_v"])
        idx_v = np.load(paths["idx_v"])
        ident_v = np.load(paths["ident_v"])
        times = np.load(paths["times"])
    except (OSError, ValueError) as exc:
        complaints.append(f"{folder.name} could not be read ({exc})")
        return [], complaints

    fund_v = np.asarray(fund_v, dtype=np.float64).ravel()
    idx_v = np.asarray(idx_v).ravel()
    ident_v = np.asarray(ident_v, dtype=np.float64).ravel()
    times = np.asarray(times, dtype=np.float64).ravel()

    sizes = {"fund_v": fund_v.size, "idx_v": idx_v.size, "ident_v": ident_v.size}
    if len(set(sizes.values())) != 1:
        named = ", ".join(f"{k} {v}" for k, v in sizes.items())
        complaints.append(
            f"{folder.name} is inconsistent: {named}. These arrays are "
            "parallel and must be the same length, so one of them is from a "
            "different run; nothing was imported"
        )
        return [], complaints

    if times.size == 0:
        complaints.append(f"{prefix}times.npy is empty; nothing was imported")
        return [], complaints

    idx_v = idx_v.astype(np.int64, copy=False)
    inside = (idx_v >= 0) & (idx_v < times.size)
    if not np.all(inside):
        complaints.append(
            f"{int((~inside).sum())} of {idx_v.size} detections index outside "
            f"{prefix}times.npy ({times.size} frames) and were skipped; the "
            "arrays are probably from different runs"
        )

    assigned = inside & ~np.isnan(ident_v)
    if not np.any(assigned):
        complaints.append(
            f"{folder.name} has no assigned detections: every ident_v is NaN, "
            "so the recording was detected but never tracked"
        )
        return [], complaints

    unassigned = int((inside & np.isnan(ident_v)).sum())
    if unassigned:
        complaints.append(
            f"{unassigned} unassigned detections were not imported; this "
            "plugin curates tracked bands and has no view for them yet"
        )

    found = []
    for ident in np.unique(ident_v[assigned]):
        chosen = assigned & (ident_v == ident)
        t = times[idx_v[chosen]]
        f = fund_v[chosen]
        order = np.argsort(t, kind="stable")
        found.append((t[order], f[order]))
    found.sort(key=lambda pair: pair[0][0] if pair[0].size else 0.0)
    return found, complaints
