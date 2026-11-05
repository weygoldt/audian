"""SAM, as the help audian's own label tool asks for (`audian.labelassist`).

A reader labels in label mode, with the category chips, by dragging a box: audian
stores the label at once, and this answers the drag with a tighter box and the
outline of the event inside it.  There is no second way of labelling, no prompt to
accept and no keys of its own.  The detector reads those labels, outlines included,
like any others.

What it does with SAM
---------------------

Each drag is a label, and each label wants its own answer, so requests wait in order
rather than the newest replacing the rest.  A view the reader rests on in label mode
is encoded ahead of the drag (`prepare`): encoding is the slow half, measured at
38 ms on this desktop's GPU and 564 ms on four CPU threads, against about 50 ms for
the answer itself.

Only a box goes in.  A single click finds the blob under it and not the event -- a
detector trained on click outlines scored 0.569 on chirps against 0.895 from rough
drags (``docs/specdetector-design.md``) -- so the click prompts are gone with the
detector's own label tool.

The runner process is started on the first request, not when audian starts: a
reader who never turns label mode on never pays for it.
"""

from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QObject

from audian.pluginapi import Answer, Drawn, LabelAssistant

from . import models as models_
from .client import RunnerClient, RunnerError

#: The largest view SAM is asked to read, in samples over all channels: 800 MB of
#: float32.  Fully zoomed out on an hour of 16 channels at 20 kHz is 1.15e9 samples,
#: 4.3 GiB before any windows are cut from it, and the job cannot be stopped midway.
MAX_VIEW_SAMPLES = 2e8


class SamAssistant(QObject, LabelAssistant):
    """Tightens a dragged box with SAM and outlines the event in it."""

    name = "SAM"
    tip = (
        "Segment Anything 2.1, run by the Spectrogram detector plugin.  A rough "
        "box is enough: detectors trained on SAM's answers to rough drags did as "
        "well as on careful boxes.  Turn it off to keep exactly the box you drew."
    )

    def __init__(
        self,
        browser,
        weights_dir: Callable[[], Path],
        parent: Optional[QObject] = None,
        client: Optional[RunnerClient] = None,
    ) -> None:
        super().__init__(parent)
        self.browser = browser
        #: asked each time, so that a directory chosen later is the one used
        self.weights_dir = weights_dir
        self.client = client
        #: ``(request, done)`` in the order the reader drew them
        self.waiting: deque = deque()
        #: the `done` of the request in flight; None for an encode or nothing
        self.asked: Optional[Callable] = None
        #: what the runner has encoded, or is encoding: ``(view, channels)``
        self.encoded: Optional[tuple] = None
        self.closed = False

    # -- the protocol --------------------------------------------------

    def unavailable(self) -> str:
        if not (Path(self.weights_dir()) / models_.SAM_WEIGHTS).is_file():
            return (
                f"{models_.SAM_WEIGHTS} is missing: open Plugins > Spectrogram "
                "detector and press Get models"
            )
        return ""

    def prepare(self, view: Drawn) -> None:
        if self.closed or self.unavailable() or self.waiting:
            return
        request = self.request(view)
        if request is None or self._key(request) == self.encoded:
            return
        if self._client().busy():
            return
        self._submit(request, None)

    def refine(self, drawn: Drawn, done) -> None:
        if self.closed:
            return
        request = self.request(drawn)
        if request is None:
            done(None, "this view is too long to read: zoom in to label")
            return
        request["points"] = []
        request["box"] = [float(v) for v in drawn.box]
        self.waiting.append((request, done))
        self._next()

    def close(self) -> None:
        self.closed = True
        self.waiting.clear()
        self.asked = None
        if self.client is not None:
            self.client.shutdown()

    # -- requests ------------------------------------------------------

    def request(self, drawn: Drawn) -> Optional[dict]:
        """What the runner's ``segment`` reads, or None for a view too long to read."""
        t0, t1, f0, f1 = drawn.view
        if t1 <= t0 or f1 <= f0:
            return None
        data = getattr(self.browser, "data", None)
        rate = float(getattr(data, "rate", 0.0) or 0.0)
        channels = int(getattr(data, "channels", 1) or 1)
        if (t1 - t0) * rate * channels > MAX_VIEW_SAMPLES:
            return None
        request = dict(paths=list(drawn.paths), view=dict(t0=t0, t1=t1, f0=f0, f1=f1))
        if isinstance(drawn.channels, int):
            # one lane: the runner reads that channel alone
            request.update(channels=None, reduction=str(drawn.channels))
        else:
            request.update(channels=list(drawn.channels), reduction="mean")
        return request

    @staticmethod
    def _key(request: dict) -> tuple:
        view = request["view"]
        channels = request["channels"]
        return (
            tuple(request["paths"]),
            (view["t0"], view["t1"], view["f0"], view["f1"]),
            None if channels is None else tuple(channels),
            request["reduction"],
        )

    def _client(self) -> RunnerClient:
        if self.client is None:
            self.client = RunnerClient(self.weights_dir(), self)
        if not getattr(self, "_wired", False):
            self.client.sigResult.connect(self._result)
            self.client.sigError.connect(self._error)
            self._wired = True
        return self.client

    def _next(self) -> None:
        if self.closed or not self.waiting or self._client().busy():
            return
        request, done = self.waiting.popleft()
        self._submit(request, done)

    def _submit(self, request: dict, done) -> None:
        try:
            self._client().submit("segment", **request)
        except RunnerError as e:
            if done is not None:
                done(None, f"could not start: {e}")
            return
        self.asked = done
        self.encoded = self._key(request)

    def _result(self, _job: str, message: dict) -> None:
        done, self.asked = self.asked, None
        if done is not None and not self.closed:
            mask = message.get("mask")
            if mask is None:
                done(None, "found nothing in that box")
            else:
                polygon = mask.get("polygon")
                outline = tuple(map(tuple, polygon)) if polygon else None
                done(Answer(tuple(float(v) for v in mask["box"]), outline), "")
        self._next()

    def _error(self, _job: str, kind: str, text: str) -> None:
        done, self.asked = self.asked, None
        # a failed encode leaves nothing encoded
        self.encoded = None
        if done is not None and not self.closed and kind != "cancelled":
            done(None, f"failed: {text}")
        self._next()
