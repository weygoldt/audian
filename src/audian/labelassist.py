"""Help with drawing a label: the hook SAM, or anything like it, plugs into.

A reader labels with audian's own tool -- label mode, a category, a drag -- and
nothing about that changes when an assistant is present.  The label is stored the
moment the drag ends, exactly as it would be without one.  An assistant then gets
to answer the drag: a tighter box and the shape inside it, which the browser writes
onto that same label with `LabelSet.refine`.  So:

* the reader never waits for it, and never has to accept anything;
* Shift+B takes back the label, as it would have before;
* a box the reader moves or resizes before the answer arrives keeps their geometry,
  because an answer to a box that is no longer there is not an answer to anything.

What it costs a plugin
----------------------

An ``audian_*label_assistant`` callable, found the way ``audian_*panel`` is, taking
the browser and returning a `LabelAssistant` or None.  The browser then shows a
toggle named after it in the Labels tab, and remembers whether it was on.

`refine` is asynchronous on purpose.  Anything worth having here runs a model, and a
model does not answer inside a mouse release: the answer is handed to `done` when it
comes, on the GUI thread.  `prepare` is a hint that the reader is about to draw in a
view, for an assistant that can do the slow half of its work ahead of the drag --
SAM encodes the image there.

Pure data and a base class.  Nothing here imports Qt, so a plugin's assistant can be
tested without a window, and so can the browser's half with a fake one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional


@dataclass(frozen=True)
class Drawn:
    """Where a reader is drawing, and what they drew.

    `box` is None for `LabelAssistant.prepare`, which is told about a view before
    anything is drawn in it.
    """

    #: every file of the recording, absolute and in order
    paths: tuple
    #: the lane's channel, or a list of them for the mean or the overlay lane
    channels: object
    #: ``(t0, t1, f0, f1)`` the lane shows, inside the recording
    view: tuple
    #: ``(t0, t1, f0, f1)`` the reader drew, or None
    box: Optional[tuple] = None


@dataclass(frozen=True)
class Answer:
    """What an assistant made of a drawn box."""

    #: ``(t0, t1, f0, f1)``
    box: tuple
    #: ``((t, f), ...)``, or None when it found no shape worth keeping
    outline: Optional[tuple] = None


#: What `LabelAssistant.refine` hands its answer to: ``(answer, message)``.  An
#: answer of None with a message is a reason the reader should see; with none, a
#: drag there was nothing to say about.
Done = Callable[[Optional[Answer], str], None]


class LabelAssistant:
    """The base an ``audian_*label_assistant`` returns.  Every method may be kept."""

    #: what the toggle in the Labels tab says
    name = "Assist"
    #: its tool tip
    tip = ""

    def unavailable(self) -> str:
        """Why it cannot help right now, or "" when it can.  Asked before each drag."""
        return ""

    def prepare(self, view: Drawn) -> None:
        """The reader is likely to draw in `view` next."""

    def refine(self, drawn: Drawn, done: Done) -> None:
        """Answer the drag in `drawn`, now or later, by calling `done` once."""
        done(None, "")

    def close(self) -> None:
        """The browser is going.  Stop whatever runs, and call `done` no more."""
