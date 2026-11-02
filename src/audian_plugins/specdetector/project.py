"""What a detector project remembers between sessions.  Pure data: no Qt, no models.

A reader's labels are not in here.  They live where every other label lives, in the
``<stem>-editable-labels.csv`` beside the recording, so a box drawn for this plugin
is an ordinary label everywhere else in audian, and one drawn long before it existed
is already a training example.  The project holds only what a label cannot say:

* **reviewed rectangles**: which parts of which recording somebody has finished with,
  so that everything unboxed in them may be taught as background;
* **rejections**: what a model proposed and a person said no to, the hard negatives;
* **outlines**: SAM's masks, keyed by the box they belong to;
* **candidates**: the last proposals, so a queue survives closing the window;
* the spectrogram **config** the models were trained on, and which model is active.

The directory defaults to ``<stem>-specdetector`` beside the recording, like
wavetracker's results.  A project can take in further recordings, whose labels stay in
their own sidecars.  Paths are stored relative to the project wherever that can be
done, so that a project and its recordings can move together.

Every file is written atomically.  A half-written project file loses a day's review,
and a crash during a save is exactly when nobody is looking.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional, Sequence

from . import tiling
from .boxes import Box
from .dataset import Rect, rect_from_list

PROJECT_FILE = "project.json"
CANDIDATES_FILE = "candidates.json"
VERSION = 1

#: Decimals a label's geometry is compared at when looking up its outline.  The
#: sidecar keeps six decimals of seconds and three of hertz, so a label read back
#: from the disk still finds what was stored under its box.
KEY_TIME_DECIMALS = 6
KEY_FREQ_DECIMALS = 3


def default_directory(first_path) -> Path:
    """``<dir of first file>/<stem of first file>-specdetector``."""
    path = Path(first_path)
    return path.with_name(f"{path.stem}-specdetector")


def replace_atomically(path: Path, text: str) -> None:
    """`text` at `path`, or the old file untouched."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(
        prefix=path.name, suffix=".tmp", dir=os.fspath(path.parent)
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fp:
            fp.write(text)
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def mask_key(category: str, t0, t1, f0, f1) -> str:
    """What an outline is stored under: the geometry of the box it belongs to.

    Moving a box is a statement that the outline no longer applies.  So an edited box
    simply no longer finds one, and is treated as a box without an outline, rather
    than dragging a shape around that nobody drew.
    """
    return (
        f"{category}|{float(t0):.{KEY_TIME_DECIMALS}f}|{float(t1):.{KEY_TIME_DECIMALS}f}"
        f"|{float(f0):.{KEY_FREQ_DECIMALS}f}|{float(f1):.{KEY_FREQ_DECIMALS}f}"
    )


def box_json(box: Box) -> dict:
    """A box as a plain dict, the shape the runner takes and gives."""
    return {
        "t0": float(box.t0),
        "t1": float(box.t1),
        "f0": float(box.f0),
        "f1": float(box.f1),
        "cls": int(box.cls),
        "score": float(box.score),
        "polygon": None if box.polygon is None else [list(p) for p in box.polygon],
    }


def box_from_json(values: dict) -> Box:
    polygon = values.get("polygon")
    return Box(
        float(values["t0"]),
        float(values["t1"]),
        float(values["f0"]),
        float(values["f1"]),
        int(values.get("cls", 0)),
        float(values.get("score", 1.0)),
        polygon=None if not polygon else tuple(tuple(p) for p in polygon),
    )


@dataclass
class Project:
    """One project directory, in memory.  `save` writes it."""

    directory: Path
    config: Optional[tiling.SpecConfig] = None
    #: class names, in the order of a model's class indices.  Append only: inserting
    #: one would silently renumber every trained model's classes.
    names: list = field(default_factory=list)
    #: each recording is the ordered list of its files, relative to `directory`
    recordings: list = field(default_factory=list)
    #: recording key -> rectangles
    reviewed: dict = field(default_factory=dict)
    #: recording key -> rejected boxes
    rejected: dict = field(default_factory=dict)
    #: `mask_key` -> outline ``[[t, f], ...]``
    masks: dict = field(default_factory=dict)
    #: the active model's weights, relative to `directory`
    model: Optional[str] = None
    #: the display cut, last set by a model or by the reader
    threshold: float = 0.25
    #: whether new models are trained on outlines rather than boxes
    segment: bool = False

    # -- paths ---------------------------------------------------------

    def _relative(self, path) -> str:
        path = os.path.abspath(os.fspath(path))
        try:
            return os.path.relpath(path, os.fspath(self.directory))
        except ValueError:  # another drive
            return path

    def _absolute(self, path: str) -> str:
        if os.path.isabs(path):
            return path
        return os.path.normpath(os.path.join(os.fspath(self.directory), path))

    def recording_key(self, paths: Sequence) -> str:
        """Which recording this is, as stored: its first file, relative."""
        return self._relative(paths[0])

    def add_recording(self, paths: Sequence) -> bool:
        """Remember a recording.  False when it is already known as this.

        A recording is its first file.  The same one opened again with another file
        list replaces the old entry: as two entries it would be two sources of the same
        audio, and a validation tile could then share time with a training tile across
        them, where the split cannot see it.
        """
        relative = [self._relative(p) for p in paths]
        if relative in self.recordings:
            return False
        self.recordings = [r for r in self.recordings if r[0] != relative[0]]
        self.recordings.append(relative)
        return True

    def recording_paths(self) -> list:
        """Every known recording, as absolute file lists."""
        return [[self._absolute(p) for p in files] for files in self.recordings]

    def model_path(self) -> Optional[str]:
        return None if self.model is None else self._absolute(self.model)

    def set_model(self, path) -> None:
        self.model = None if path is None else self._relative(path)

    def models_directory(self) -> Path:
        return Path(self.directory) / "models"

    def detections_path(self, paths: Sequence, stamp: str) -> Path:
        return (
            Path(self.directory) / "detections" / f"{Path(paths[0]).stem}-{stamp}.csv"
        )

    # -- classes -------------------------------------------------------

    def class_index(self, name: str) -> int:
        """The index of a class, adding it at the end if it is new."""
        if name not in self.names:
            self.names.append(name)
        return self.names.index(name)

    # -- review --------------------------------------------------------

    def reviewed_rects(self, paths: Sequence) -> list:
        return list(self.reviewed.get(self.recording_key(paths), []))

    def mark_reviewed(self, paths: Sequence, rect: Rect) -> None:
        self.reviewed.setdefault(self.recording_key(paths), []).append(rect)

    def rejected_boxes(self, paths: Sequence) -> list:
        return list(self.rejected.get(self.recording_key(paths), []))

    def reject(self, paths: Sequence, boxes: Iterable[Box]) -> None:
        self.rejected.setdefault(self.recording_key(paths), []).extend(boxes)

    def forget_review(self, paths: Sequence) -> None:
        """Forget what was reviewed and rejected here.  The labels stay."""
        key = self.recording_key(paths)
        self.reviewed.pop(key, None)
        self.rejected.pop(key, None)

    # -- the disk ------------------------------------------------------

    def as_dict(self) -> dict:
        return {
            "version": VERSION,
            "config": None if self.config is None else self.config.as_dict(),
            "names": list(self.names),
            "recordings": self.recordings,
            "reviewed": {
                key: [list(rect) for rect in rects]
                for key, rects in self.reviewed.items()
            },
            "rejected": {
                key: [box_json(b) for b in boxes]
                for key, boxes in self.rejected.items()
            },
            "masks": self.masks,
            "model": self.model,
            "threshold": float(self.threshold),
            "segment": bool(self.segment),
        }

    def save(self) -> None:
        replace_atomically(
            Path(self.directory) / PROJECT_FILE, json.dumps(self.as_dict(), indent=1)
        )

    def save_candidates(self, paths: Sequence, boxes: Sequence[Box], meta=None) -> None:
        """The proposals for one recording.  Replaced whole by the next proposal."""
        path = self._candidates_path(paths)
        payload = {"meta": meta or {}, "boxes": [box_json(b) for b in boxes]}
        replace_atomically(path, json.dumps(payload))

    def load_candidates(self, paths: Sequence) -> tuple:
        """``(boxes, meta)``; nothing, for a file that is absent or unreadable."""
        path = self._candidates_path(paths)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return [box_from_json(v) for v in payload["boxes"]], payload.get("meta", {})
        except (OSError, ValueError, KeyError, TypeError):
            return [], {}

    def _candidates_path(self, paths: Sequence) -> Path:
        stem = Path(paths[0]).stem
        return Path(self.directory) / f"{stem}-{CANDIDATES_FILE}"

    @staticmethod
    def open(directory) -> "Project":
        """The project in `directory`, or a new empty one.

        A file that exists and cannot be read is an error rather than a fresh start.
        Starting fresh would overwrite a day's review with nothing at the next save.
        """
        directory = Path(directory)
        path = directory / PROJECT_FILE
        project = Project(directory=directory)
        if not path.exists():
            return project
        values = json.loads(path.read_text(encoding="utf-8"))
        if int(values.get("version", 0)) > VERSION:
            raise ValueError(
                f"{path} was written by a newer audian (version {values.get('version')})"
            )
        config = values.get("config")
        project.config = None if config is None else tiling.SpecConfig.from_dict(config)
        project.names = list(values.get("names", []))
        project.recordings = [list(files) for files in values.get("recordings", [])]
        project.reviewed = {
            key: [rect_from_list(rect) for rect in rects]
            for key, rects in values.get("reviewed", {}).items()
        }
        project.rejected = {
            key: [box_from_json(b) for b in boxes]
            for key, boxes in values.get("rejected", {}).items()
        }
        project.masks = dict(values.get("masks", {}))
        project.model = values.get("model")
        project.threshold = float(values.get("threshold", 0.25))
        project.segment = bool(values.get("segment", False))
        return project
