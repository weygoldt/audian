"""Ground truth: reading it in, and the shape it should have been in.

A reference is a band set the reader did not draw and may not edit -- what
the recording is *known* to contain.  For a synthetic file that is the
generator's own answer; for a real one it is whatever a careful person
agreed on earlier.  The plugin keeps it beside the working bands and draws
it differently, so "what did the tracker miss" and "what did I get wrong"
are questions you answer by looking rather than by exporting two files and
diffing them.

Why this module exists at all
-----------------------------

The truth for the synthetic recordings is stored as **chained bounding
boxes**: ``wavefish_4ch_clean-editable-labels.csv`` holds 323 of them, one
per second, each about three hertz tall, and 120 consecutive boxes are one
Sternopygus.  Every box says the same thing the one before it said, the
category is repeated 120 times, and the *track* -- the thing the file is
actually about -- exists only as the reader's eye joining them up.

That is not a criticism of whoever wrote it: audian's editable label is a
rectangle, and a rectangle chain is the only way to say "a frequency that
moves" in a format that has no such idea.  `bands.Band` is that idea, so
the boxes can be read once and put into a shape that holds a track as a
track.  Nothing here writes the label file; the boxes stay where they are.

Reconstruction, and what it costs
---------------------------------

A chained box contributes its centre.  The band that comes back therefore
has the *time* resolution the boxes had -- one second here, where the
tracker works at 0.2 -- and a frequency good to half a box height.  It is a
coarse copy of a fine thing and is honest about it: a reference is drawn to
be compared against, not measured.

A box that is *alone* contributes both of its ends instead, because it is
not a link in a chain but a span: the noisy recording labels each mains
harmonic as one box across the whole two minutes, and its centre alone
would be a dot at 60 s where a line across the file was meant.

Boxes are grouped by ``(category, note)`` because that is where the two
halves of an identity live: the category is the species and the note is the
individual, so ``Sternopygus`` + ``sternopygus_resident`` is one animal and
one band.  A group is then cut wherever the boxes stop touching -- see
`GAP_FACTOR`, which measures **contiguity rather than regularity**, and is
what keeps three pulses spread evenly across a minute three events instead
of one band a minute long.
"""

from __future__ import annotations

import numpy as np

from .bands import Band, BandSet

#: How far apart two boxes may be, as a multiple of how long a box lasts,
#: and still be the same band.
#:
#: The test is **contiguity, not regularity**: the gap measured is from one
#: box's end to the next one's start, against the typical box duration.  It
#: was the spacing of their starts against the median spacing at first, and
#: that is a different question with a worse answer -- four pulses evenly
#: spread across a minute are evenly spaced, so nothing ever exceeded the
#: median and the run was called one band fifty-six seconds long with four
#: vertices in it.  Boxes that tile a stretch of time are a track; boxes
#: with silence between them are separate things that happen to share a
#: name.
#:
#: Half a box of slack: the chained truth leaves no gap at all, and a single
#: missing box leaves exactly one box's worth, which is a dropout in the
#: labelling rather than the animal leaving.
GAP_FACTOR = 1.5


def _duration(label) -> float:
    if label.t1 is None:
        return 0.0
    return max(0.0, float(label.t1) - float(label.t0))


def _mid_hz(label) -> float:
    return 0.5 * (float(label.f0) + float(label.f1))


def _vertices(run) -> tuple:
    """``(times, freqs)`` for one run of boxes.

    A run of several is a chain, and each box contributes its centre: that
    is where the track was when that box was drawn.

    A run of *one* is not a chain but a span -- "this frequency is present
    from here to here" -- and contributes both of its ends.  The mains
    harmonics of the noisy synthetic recording are labelled exactly that
    way, one box each across the whole two minutes, and taking the centre of
    one would put a single dot at 60 s where a line across the file was
    meant.
    """
    if len(run) == 1:
        label = run[0]
        hz = _mid_hz(label)
        t1 = float(label.t1) if label.t1 is not None else float(label.t0)
        if t1 <= float(label.t0):
            return (np.array([float(label.t0)]), np.array([hz]))
        return (np.array([float(label.t0), t1]), np.array([hz, hz]))
    times = np.array(
        [0.5 * (lab.t0 + (lab.t1 if lab.t1 is not None else lab.t0))
         for lab in run],
        dtype=np.float64,
    )
    freqs = np.array([_mid_hz(lab) for lab in run], dtype=np.float64)
    return times, freqs


def _split_on_gaps(labels) -> list:
    """One run of boxes per stretch they actually tile, in time order."""
    labels = sorted(labels, key=lambda lab: lab.t0)
    if len(labels) < 2:
        return [labels]
    spans = np.array([_duration(lab) for lab in labels], dtype=np.float64)
    usual = float(np.median(spans[spans > 0])) if np.any(spans > 0) else 0.0
    if usual <= 0:
        # every box is an instant; nothing tiles anything, so each stands
        # on its own rather than being joined into a line through them
        return [[label] for label in labels]
    runs, run = [], [labels[0]]
    for previous, label in zip(labels, labels[1:]):
        end = previous.t1 if previous.t1 is not None else previous.t0
        if float(label.t0) - float(end) > GAP_FACTOR * usual:
            runs.append(run)
            run = [label]
        else:
            run.append(label)
    runs.append(run)
    return runs


def bands_from_labels(labels) -> tuple:
    """A `BandSet` of the tracks a chain of boxes was drawing.

    Returns ``(bandset, complaints)``.  `labels` is anything iterable of
    `labels.Label` -- the browser's own `LabelSet` is one, which is how this
    is reached from the panel without reading a file the application has
    already read.

    Labels with no frequency are skipped and counted: a label drawn on a
    trace has an amplitude axis and says nothing about where a band is, and
    silently treating one as a band at 0 Hz would put a line along the
    bottom of every lane.
    """
    complaints: list = []
    groups: dict = {}
    flat = 0
    for label in labels:
        if label.f0 is None or label.f1 is None:
            flat += 1
            continue
        key = (label.category or "", label.note or "")
        groups.setdefault(key, []).append(label)
    if flat:
        complaints.append(
            f"{flat} labels carry no frequency and were skipped; a label "
            "drawn on a trace does not say where a band is"
        )
    if not groups:
        return BandSet(), complaints

    made: list = []
    for (category, note), members in sorted(groups.items()):
        for run in _split_on_gaps(members):
            if not run:
                continue
            times, freqs = _vertices(run)
            channels = {lab.channel for lab in run}
            made.append(
                Band(
                    0,  # renumbered below
                    times,
                    freqs,
                    channel=channels.pop() if len(channels) == 1 else None,
                    category=category,
                    note=note,
                )
            )
    made.sort(key=lambda band: band.t0)

    reference = BandSet()
    reference.add_many(made, f"reference of {len(made)} bands")
    reference.forget_history()
    reference.mark_saved()
    return reference, complaints


def summarise(reference: BandSet) -> str:
    """One line naming what the reference holds, for the panel."""
    if not len(reference):
        return "no reference loaded"
    names = reference.categories()
    what = ", ".join(names[:3]) + ("…" if len(names) > 3 else "")
    n = len(reference)
    return f"{n} band{'' if n == 1 else 's'}" + (f" · {what}" if names else "")
