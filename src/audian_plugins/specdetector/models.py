"""Everything that touches Ultralytics.  Imported by the runner process only.

`configure` has to run before the first ``import ultralytics``: three of the things
it decides are read once at import time.  Left to itself the package writes its
settings under ``~/.config``, installs missing requirements with pip at run time,
sends usage analytics, and downloads weights and a font whenever it finds them
absent.  In a field station none of those should happen, so:

* **No analytics, no pip.**  ``sync`` off and ``YOLO_AUTOINSTALL`` false.
* **Nothing downloaded unless asked.**  ``YOLO_OFFLINE`` is set for every job but
  an explicit `fetch`, so a missing file is an error naming the file rather than a
  stall on a dead connection.
* **Its files stay in one directory.**  Settings, weights and the font live in the
  directory the panel hands it, which can be copied to a machine with no network.

The choice of models is in ``docs/specdetector-design.md``, with what each was
measured to do.  In short:

* `Segmenter`: SAM 2.1 tiny.  Clicking on the spectrogram.
* `Proposer`: YOLOE visual prompts.  Finding more of what was clicked on, with no
  training, from the embedding of the examples.
* `train_detector`: YOLO26n.  Once a few windows have been reviewed.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence

import numpy as np

from . import dataset as dataset_
from . import tiling
from .boxes import Box, nms
from .recording import Recording

#: What each step runs on, all of it light enough for a laptop.  SAM 2.1 tiny rather
#: than MobileSAM: from a loose drag, its box met a person's at a median IoU of 0.51
#: against 0.37 on chirps and 0.56 against 0.45 on cricket pulses, for an encode of
#: 821 ms against 564 ms on four CPU threads, and 46 ms against 38 ms per click.
SAM_WEIGHTS = "sam2.1_t.pt"
PROPOSER_WEIGHTS = "yoloe-26s-seg.pt"
DETECT_WEIGHTS = "yolo26n.pt"
SEGMENT_WEIGHTS = "yolo26n-seg.pt"
ALL_WEIGHTS = (SAM_WEIGHTS, PROPOSER_WEIGHTS, DETECT_WEIGHTS, SEGMENT_WEIGHTS)

#: The image SAM encodes.  Its encoder's input size; a smaller one is upscaled to
#: this anyway, and a view is rendered for it directly.
SAM_IMAGE_SIZE = 1024

#: Augmentations that keep a spectrogram a spectrogram.  No flips and no rotation,
#: because neither time nor frequency is symmetric, and no hue or saturation, since a
#: tile has neither.  What each is worth is measured in the design document.
SPECTROGRAM_AUGMENT = dict(
    fliplr=0.0,
    flipud=0.0,
    degrees=0.0,
    shear=0.0,
    perspective=0.0,
    hsv_h=0.0,
    hsv_s=0.0,
    hsv_v=0.2,
    translate=0.1,
    scale=0.2,
    mosaic=0.5,
    mixup=0.0,
    erasing=0.0,
    close_mosaic=5,
)

#: Boxes below this are not worth the JSON.  Far under any threshold a reader will
#: use, and above the floor of what the proposer emits on noise.
SWEEP_CONFIDENCE = 0.05

#: Vertices kept of an outline.  Enough to read a shape off, at a size a label file
#: and a redraw can bear.
POLYGON_POINTS = 48

#: Written next to ``best.pt``: everything needed to use a model without its project.
MODEL_META = "model.json"

#: How far past its own span a tile may report a box.  See `sweep`.
OWNERSHIP_PIXELS = 4

#: See `epochs_for`.  The bounds keep a large dataset from a token few epochs, and a
#: tiny one from restarting its data loader thousands of times.
TRAIN_STEPS = 400
MIN_EPOCHS = 30
MAX_EPOCHS = 200

#: Where a trained model's scores are cut until the reader moves the slider.  A constant
#: and not read off the validation set, which cannot say: it is masked, so it hardly
#: produces a false positive, and its F1 curve is flat where it matters.  Measured over
#: 32 models on regions none of them saw, against 0.935 for the best any cut could do
#: with the answers in hand: fixed cuts of 0.15, 0.25, 0.35 and 0.5 gave mean F1 of
#: 0.888, 0.896, 0.892 and 0.887, Otsu on the model's own scores 0.876, and 0.65 gave
#: 0.830.  Those are one plateau.  This end of it, because a false positive is on the
#: screen to be rejected and a miss is not: recall 0.91 here against 0.85 at 0.5.
TRAINED_CUT = 0.25

_configured = False


class MissingWeights(RuntimeError):
    """A weights file is not in the directory and no download was asked for."""


def configure(weights_dir, offline: bool = True) -> None:
    """Set up Ultralytics' environment.  Before its first import, or it is too late.

    The runner's working directory is `weights_dir` as well, because a file named
    without a directory is looked for, and written, in the working directory.
    """
    global _configured
    weights_dir = Path(weights_dir)
    weights_dir.mkdir(parents=True, exist_ok=True)
    os.environ["YOLO_CONFIG_DIR"] = os.fspath(weights_dir)
    os.environ["YOLO_AUTOINSTALL"] = "false"
    if offline:
        os.environ["YOLO_OFFLINE"] = "true"
    else:
        os.environ.pop("YOLO_OFFLINE", None)
    os.chdir(weights_dir)
    if not _configured:
        from ultralytics.utils import SETTINGS

        SETTINGS.update(
            {
                "sync": False,
                "weights_dir": os.fspath(weights_dir),
                "runs_dir": os.fspath(weights_dir / "runs"),
                "datasets_dir": os.fspath(weights_dir / "datasets"),
                "clearml": False,
                "comet": False,
                "dvc": False,
                "mlflow": False,
                "raytune": False,
                "tensorboard": False,
                "wandb": False,
            }
        )
        _configured = True


def device_of(device: Optional[str]) -> str:
    """``"cuda:0"`` or ``"cpu"``: what "auto" means on this machine."""
    if device and device != "auto":
        return str(device)
    import torch

    return "cuda:0" if torch.cuda.is_available() else "cpu"


def weights_path(name: str, weights_dir=None) -> str:
    """The path of a weights file, or `MissingWeights` naming it."""
    directory = Path(weights_dir) if weights_dir is not None else Path.cwd()
    path = directory / name
    if not path.is_file():
        raise MissingWeights(
            f"{name} is not in {directory}: press Get models once while online, or "
            "copy the file there"
        )
    return os.fspath(path)


def fetch(names: Iterable[str], weights_dir, progress: Callable) -> list:
    """Download what is missing, for later offline use.  The only job that goes online."""
    from ultralytics.utils.downloads import attempt_download_asset

    directory = Path(weights_dir)
    names = list(names)
    got = []
    for index, name in enumerate(names):
        progress("download", index, len(names), name)
        path = directory / name
        if not path.is_file():
            attempt_download_asset(os.fspath(path))
        if not path.is_file():
            raise MissingWeights(f"could not download {name}")
        got.append(os.fspath(path))
    progress("download", len(names), len(names), "")
    return got


# ------------------------------------------------------------- outlines


def mask_outline(
    mask: np.ndarray, points: int = POLYGON_POINTS
) -> Optional[np.ndarray]:
    """The outer outline of a mask's largest part, as ``(n, 2)`` pixels ``x, y``.

    None for a mask with nothing in it.
    """
    import cv2

    mask = np.asarray(mask, dtype=np.uint8)
    if not mask.any():
        return None
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    contour = max(contours, key=cv2.contourArea)
    return simplify_outline(contour[:, 0, :].astype(float), points)


def simplify_outline(outline: np.ndarray, points: int = POLYGON_POINTS) -> np.ndarray:
    """At most `points` vertices, by tightening Douglas-Peucker until it fits."""
    import cv2

    if len(outline) <= points:
        return outline
    contour = outline.astype(np.float32).reshape(-1, 1, 2)
    perimeter = float(cv2.arcLength(contour, True))
    epsilon = perimeter * 0.002
    simple = contour
    for _ in range(12):
        simple = cv2.approxPolyDP(contour, epsilon, True)
        if len(simple) <= points:
            break
        epsilon *= 1.5
    return simple[:, 0, :].astype(float)


def outline_to_tf(outline: np.ndarray, geometry: tiling.TileGeometry) -> list:
    """An outline in pixels as ``[[t, f], ...]``, rounded to what a label keeps."""
    return [
        [round(float(geometry.t_of(x)), 6), round(float(geometry.f_of(y)), 3)]
        for x, y in outline
    ]


def within(values, low, high) -> list:
    """`values` held between `low` and `high`.  For a box's edges and an outline's.

    A tile's band is snapped to bin edges and so reaches half a bin below 0 Hz, and a
    tile may run past the end of a recording.  Nothing placed on those is a label: a
    box from a model wrote ``f_low_hz=-5.000`` into a sidecar.
    """
    return [float(np.clip(v, low, high)) for v in values]


def clamp_box(box, limits) -> tuple:
    """``(t0, t1, f0, f1)`` inside ``limits = (duration, nyquist)``."""
    duration, nyquist = limits
    t0, t1 = within(box[:2], 0.0, duration)
    f0, f1 = within(box[2:], 0.0, nyquist)
    return t0, t1, f0, f1


def clamp_polygon(polygon, limits) -> list:
    """An outline ``[[t, f], ...]`` inside ``limits = (duration, nyquist)``."""
    duration, nyquist = limits
    return [
        [float(np.clip(t, 0.0, duration)), float(np.clip(f, 0.0, nyquist))]
        for t, f in polygon
    ]


def keep_prompted_parts(mask: np.ndarray, positives=(), box=None) -> np.ndarray:
    """What of a mask the prompt actually asked for.

    SAM will answer a click on one event with a mask that also covers its neighbour,
    which came back from a click on a 120 ms pip as a box 830 ms long.  It knows nothing
    of what an event is on a spectrogram, so the gesture has to say.  A drag means
    "inside this box".  A click means "the part under the pointer", or the nearest part
    when the pointer is just outside any.
    """
    import cv2

    mask = np.asarray(mask, dtype=bool)
    if box is not None:
        x0, y0, x1, y1 = (int(round(v)) for v in box)
        inside = np.zeros_like(mask)
        inside[max(y0, 0) : max(y1, 0), max(x0, 0) : max(x1, 0)] = True
        mask = mask & inside
    if not len(positives) or not mask.any():
        return mask
    count, parts = cv2.connectedComponents(mask.astype(np.uint8), connectivity=8)
    keep = set()
    height, width = mask.shape
    for x, y in positives:
        col = int(np.clip(x, 0, width - 1))
        row = int(np.clip(y, 0, height - 1))
        if parts[row, col] > 0:
            keep.add(int(parts[row, col]))
            continue
        ys, xs = np.nonzero(parts)
        nearest = np.argmin((xs - col) ** 2 + (ys - row) ** 2)
        keep.add(int(parts[ys[nearest], xs[nearest]]))
    return np.isin(parts, sorted(keep))


def box_of_mask(mask: np.ndarray) -> Optional[tuple]:
    """The tight ``(x0, y0, x1, y1)`` of a mask, with exclusive far edges."""
    ys, xs = np.nonzero(mask)
    if not len(xs):
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


# ------------------------------------------------------------- SAM


class Segmenter:
    """SAM on what is in view.

    The encoder runs once per view and every prompt after that only runs the
    decoder, which is what makes clicking feel like clicking.  One view is kept.
    """

    def __init__(self, weights: str = SAM_WEIGHTS, device: Optional[str] = None):
        from ultralytics.models.sam import Predictor, SAM2Predictor

        # The two generations of model answer to different predictors, and the wrong
        # one fails on the first prompt rather than on loading.
        kind = SAM2Predictor if "sam2" in weights else Predictor
        self.predictor = kind(
            overrides=dict(
                conf=0.0,
                task="segment",
                mode="predict",
                imgsz=SAM_IMAGE_SIZE,
                model=weights_path(weights),
                device=device_of(device),
                verbose=False,
                save=False,
            )
        )
        self.key = None
        self.geometry = None
        self.limits = (float("inf"), float("inf"))
        self.recording = None
        self.paths = None

    def close(self) -> None:
        if self.recording is not None:
            self.recording.close()
            self.recording = None
            self.paths = None

    def _open(self, paths: Sequence[str]) -> Recording:
        if self.recording is None or self.paths != list(paths):
            self.close()
            self.recording = Recording(paths)
            self.paths = list(paths)
        return self.recording

    def set_view(self, paths, t0, t1, f0, f1, channels=None, reduction="mean") -> float:
        """Encode the view, unless it is already encoded.  Returns its cost in ms."""
        key = (tuple(paths), t0, t1, f0, f1, tuple(channels or ()), str(reduction))
        if key == self.key:
            return 0.0
        recording = self._open(paths)
        config, s0 = tiling.view_config(
            t0, t1, f0, f1, recording.rate, SAM_IMAGE_SIZE, reduction
        )
        grid = tiling.grid_of(config, recording.rate)
        image, geometry = tiling.render_tile(recording, grid, s0, config, channels)
        start = time.perf_counter()
        # Forgotten first: if the encode fails, the next click must not find the key of
        # the view it replaced over features that are gone.
        self.key = None
        self.predictor.reset_image()
        self.predictor.set_image(image)
        self.key, self.geometry = key, geometry
        self.limits = (recording.duration, recording.rate / 2)
        return 1e3 * (time.perf_counter() - start)

    def segment(self, points=None, box=None) -> Optional[dict]:
        """A mask for prompts in seconds and hertz.

        `points` is ``[(t, f, positive), ...]`` and `box` is ``(t0, t1, f0, f1)``.
        """
        geometry = self.geometry
        kwargs: dict = {}
        if points:
            kwargs["points"] = [
                [
                    [float(geometry.x_of(t)), float(geometry.y_of(f))]
                    for t, f, _ in points
                ]
            ]
            kwargs["labels"] = [[1 if positive else 0 for _, _, positive in points]]
        if box is not None:
            kwargs["bboxes"] = [list(geometry.box_to_pixels(*box))]
        result = self.predictor(**kwargs)[0]
        if result.masks is None or not len(result.masks.data):
            return None
        mask = result.masks.data[0].cpu().numpy() > 0
        mask = keep_prompted_parts(
            mask,
            positives=[
                (float(geometry.x_of(t)), float(geometry.y_of(f)))
                for t, f, positive in (points or [])
                if positive
            ],
            box=kwargs.get("bboxes", [None])[0],
        )
        tight = box_of_mask(mask)
        if tight is None:
            return None
        outline = mask_outline(mask)
        if outline is None or len(outline) < 3:
            # A mask one pixel thin has no area to run around: its box is its outline.
            x0, y0, x1, y1 = tight
            outline = np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], dtype=float)
        score = float(result.boxes.conf[0]) if result.boxes is not None else 0.0
        return {
            "box": list(clamp_box(geometry.box_from_pixels(*tight), self.limits)),
            "polygon": clamp_polygon(outline_to_tf(outline, geometry), self.limits),
            "score": score,
            "fraction": float(mask.mean()),
        }


# ------------------------------------------------------------- finding more


class Prompted:
    """YOLOE with visual prompts, standing in for a trained model in `sweep`.

    Shots rarely fall on one tile, so each tile holding some gets its own embedding
    and the embeddings are averaged per class.  Prompt classes are the project's own
    indices; a class with no shots is not prompted, and comes back through `names`.
    """

    def __init__(self, weights: str = PROPOSER_WEIGHTS, device: Optional[str] = None):
        from ultralytics import YOLOE

        self.device = device_of(device)
        self.model = YOLOE(weights_path(weights))
        self.classes: list = []

    def prompt(self, recording, config, shots: Sequence[Box], progress=None) -> int:
        """Learn the classes in `shots`.  Returns how many shots were used."""
        import torch
        from ultralytics.models.yolo.yoloe import (
            YOLOEVPDetectPredictor,
            YOLOEVPSegPredictor,
        )

        if not shots:
            raise ValueError("no examples to find more of")
        grid = tiling.grid_of(config, recording.rate)
        size = config.image_size
        self.classes = sorted({b.cls for b in shots})
        task = getattr(self.model.model, "task", "segment")
        kind = YOLOEVPSegPredictor if task == "segment" else YOLOEVPDetectPredictor
        predictor = kind(
            overrides=dict(
                task=task,
                mode="predict",
                save=False,
                batch=1,
                imgsz=size,
                device=self.device,
                conf=SWEEP_CONFIDENCE,
                verbose=False,
            )
        )
        predictor.setup_model(model=self.model.model, verbose=False)
        embeddings: dict = {c: [] for c in self.classes}
        used = 0
        for number, shot in enumerate(shots):
            # One embedding per shot rather than per tile: each is centred, so no
            # shot is lost to a tile edge, and two neighbours do not count as one.
            s0 = int(round((shot.centre_t - grid.tile_s / 2) * recording.rate))
            s0 = int(np.clip(s0, 0, max(recording.frames - grid.frames * grid.hop, 0)))
            image, geometry = tiling.render_tile(recording, grid, s0, config)
            x0, y0, x1, y1 = geometry.box_to_pixels(shot.t0, shot.t1, shot.f0, shot.f1)
            x0, x1 = float(np.clip(x0, 0, size)), float(np.clip(x1, 0, size))
            y0, y1 = float(np.clip(y0, 0, size)), float(np.clip(y1, 0, size))
            if x1 - x0 < 1 or y1 - y0 < 1:
                continue
            predictor.set_prompts(
                {"bboxes": np.array([[x0, y0, x1, y1]]), "cls": np.array([0])}
            )
            predictor.model.names = {0: "object0"}
            vpe = predictor.get_vpe(image)
            embeddings[shot.cls].append(vpe[0, 0].detach())
            used += 1
            if progress is not None:
                progress("examples", number + 1, len(shots), "")
        vectors = []
        for c in self.classes:
            if not embeddings[c]:
                raise ValueError(f"no usable example of class {c}")
            mean = torch.stack(embeddings[c]).mean(dim=0)
            vectors.append(torch.nn.functional.normalize(mean, dim=-1))
        names = [f"object{i}" for i in range(len(self.classes))]
        self.model.set_classes(names, torch.stack(vectors)[None])
        self.model.task = task
        self.model.predictor = None
        return used

    def predict(self, images, **kwargs):
        kwargs["device"] = self.device
        return self.model.predict(images, **kwargs)

    def to_project_class(self, cls: int) -> int:
        return self.classes[int(cls)]


class Trained:
    """A trained YOLO and what it needs, as `Prompted` is a prompted one."""

    def __init__(self, weights: str, device: Optional[str] = None):
        from ultralytics import YOLO

        self.device = device_of(device)
        self.model = YOLO(os.fspath(weights))
        self.meta = read_meta(weights)
        self.names = list(self.meta.get("names", []))
        self.config = tiling.SpecConfig.from_dict(self.meta["config"])

    def predict(self, images, **kwargs):
        kwargs["device"] = self.device
        return self.model.predict(images, **kwargs)

    def to_project_class(self, cls: int) -> int:
        return int(cls)


def read_meta(weights) -> dict:
    """The `MODEL_META` next to a weights file, or ValueError."""
    path = Path(weights).with_name(MODEL_META)
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise ValueError(f"{weights} has no readable {MODEL_META}: {exc}") from exc


# ------------------------------------------------------------- sweeping


def _boxes_of(result, geometry, to_class, limits) -> list:
    """One model result as boxes on the time-frequency plane, inside the recording."""
    boxes = result.boxes
    if boxes is None or not len(boxes):
        return []
    xyxy = boxes.xyxy.cpu().numpy()
    scores = boxes.conf.cpu().numpy()
    classes = boxes.cls.cpu().numpy().astype(int)
    outlines = None
    if result.masks is not None:
        outlines = result.masks.xy
    found = []
    for index, ((x0, y0, x1, y1), score, cls) in enumerate(zip(xyxy, scores, classes)):
        t0, t1, f0, f1 = clamp_box(geometry.box_from_pixels(x0, y0, x1, y1), limits)
        polygon = None
        if outlines is not None and index < len(outlines) and len(outlines[index]) >= 3:
            outline = outline_to_tf(simplify_outline(outlines[index]), geometry)
            polygon = tuple(map(tuple, clamp_polygon(outline, limits)))
        found.append(
            Box(t0, t1, f0, f1, to_class(int(cls)), float(score), polygon=polygon)
        )
    return found


def sweep_starts(recording, grid, windows=None) -> list:
    """The tile starts covering `windows` of ``(t0, t1)``; None is everything.

    The tiles of a window start half a tile before it, so that an event at its edge
    is whole on some tile.  Those tiles' owned spans then decide who reports it.
    """
    length = grid.frames * grid.hop
    if windows is None:
        return tiling.tile_starts(recording.duration, grid)
    all_starts = np.array(tiling.tile_starts(recording.duration, grid))
    wanted = set()
    for t0, t1 in windows:
        a = int(t0 * grid.rate) - length
        b = int(t1 * grid.rate)
        inside = all_starts[(all_starts > a) & (all_starts < b)]
        wanted.update(int(s) for s in inside)
    return sorted(wanted)


def sweep(
    model,
    recording: Recording,
    config: tiling.SpecConfig,
    windows=None,
    *,
    conf: float = SWEEP_CONFIDENCE,
    iou: float = 0.5,
    batch: int = 16,
    progress: Optional[Callable] = None,
    on_batch: Optional[Callable] = None,
) -> list:
    """Detect over `windows`, each box reported once, by the tile that saw it whole.

    Suppression runs once more over everything collected, class by class, because
    two boxes of one event in neighbouring tiles need not have the same centre: the
    old chirp detector left that to chance at every batch boundary.
    """
    grid = tiling.grid_of(config, recording.rate)
    starts = sweep_starts(recording, grid, windows)
    owners = tiling.owned_spans(starts, grid, recording.frames)
    limits = (recording.duration, recording.rate / 2)
    found: list = []
    images: list = []
    held: list = []

    def flush():
        results = model.predict(
            images,
            conf=conf,
            iou=iou,
            imgsz=config.image_size,
            verbose=False,
            save=False,
        )
        fresh = []
        for result, (geometry, (own0, own1)) in zip(results, held):
            # A little past its own span, since each tile judges its own box: if one
            # puts the centre a few pixels late and its neighbour a few early, then
            # neither owns it and the event is lost.  Suppression takes out the double.
            pad = OWNERSHIP_PIXELS * (geometry.t1 - geometry.t0) / geometry.width
            for box in _boxes_of(result, geometry, model.to_project_class, limits):
                if own0 - pad <= box.centre_t < own1 + pad:
                    fresh.append(box)
        found.extend(fresh)
        if on_batch is not None:
            on_batch(fresh)
        images.clear()
        held.clear()

    for number, (s0, owned) in enumerate(zip(starts, owners)):
        image, geometry = tiling.render_tile(recording, grid, s0, config)
        images.append(image)
        held.append((geometry, owned))
        if len(images) >= batch:
            flush()
            if progress is not None:
                progress("sweep", number + 1, len(starts), "")
    if images:
        flush()
    if progress is not None:
        progress("sweep", len(starts), len(starts), "")
    return nms(found, iou=iou)


# ------------------------------------------------------------- training


def epochs_for(tiles: int, batch: int) -> int:
    """How long to train, so that the reader is not asked.

    Set in batches, since an epoch means nothing on its own.  Fifteen epochs of
    24 tiles at batch 16 are 30 batches, and left a model scoring mAP50 0.06 with no
    detection above 0.03; the runs that worked on real chirps had taken 360-400.

    Batches and not weight updates: Ultralytics accumulates gradients to a nominal
    batch of 64 tiles, so at batch 16 the weights change once in four batches.  With
    fewer than 64 training tiles an epoch is less than an update, and a curve repeats
    its held-out numbers between updates -- measured, in pairs, on a run of 20 tiles.
    """
    per_epoch = max(tiles / max(batch, 1), 1.0)
    return int(np.clip(np.ceil(TRAIN_STEPS / per_epoch), MIN_EPOCHS, MAX_EPOCHS))


#: Ultralytics' names for what a training curve shows, and the names used here.  The
#: box loss is the one every task has; the scores are on the held-out tiles, or on
#: the training tiles when nothing was held out.
CURVE_KEYS = {
    "train/box_loss": "train_loss",
    "val/box_loss": "val_loss",
    "metrics/mAP50(B)": "map50",
    "metrics/mAP50-95(B)": "map50_95",
}


def epoch_point(trainer) -> dict:
    """One epoch of a run, as plain numbers: what `CURVE_KEYS` names, and the epoch."""
    values = dict(getattr(trainer, "metrics", None) or {})
    try:
        values.update(trainer.label_loss_items(trainer.tloss, prefix="train"))
    except Exception:  # noqa: BLE001 - a trainer of another shape loses the losses
        pass
    point = {"epoch": int(trainer.epoch) + 1}
    for key, name in CURVE_KEYS.items():
        if key in values:
            try:
                point[name] = round(float(values[key]), 5)
            except (TypeError, ValueError):
                pass
    return point


def best_epoch(curve: Sequence[dict]) -> Optional[int]:
    """The epoch Ultralytics keeps as best: highest mAP50-95, or mAP50 without it.

    That is its fitness for boxes, ``0 * P + 0 * R + 0 * mAP50 + 1 * mAP50-95`` in
    8.4.  A segmenting model adds its masks' mAP50-95, which the curve does not keep,
    so for one of those this is the box half of the choice.
    """
    scored = [p for p in curve if "map50_95" in p or "map50" in p]
    if not scored:
        return None
    return max(scored, key=lambda p: p.get("map50_95", p.get("map50", 0.0)))["epoch"]


def train_detector(
    sources: Sequence[dataset_.Source],
    config: tiling.SpecConfig,
    names: Sequence[str],
    directory,
    *,
    segment: bool = False,
    epochs: Optional[int] = None,
    batch: Optional[int] = None,
    device: Optional[str] = None,
    base: Optional[str] = None,
    seed: int = 0,
    jitter_copies: int = dataset_.JITTER_COPIES,
    progress: Optional[Callable] = None,
    open_recording: Callable = Recording,
    augment: Optional[dict] = None,
    patience: Optional[int] = None,
    hold_out_every: int = dataset_.VALIDATION_EVERY,
    on_epoch_end: Optional[Callable] = None,
) -> dict:
    """Build the dataset, train, and leave a usable model in `directory`.

    The result is ``directory/best.pt`` with a `MODEL_META` beside it: everything
    the model needs to read another recording, so a model can leave its project.

    `epochs`, `batch` and `patience` are chosen here when None.  `base` is a weights
    file: a pretrained one by name, or a model trained before, by path, to go on
    from.  `patience` stops training after that many epochs without a better score
    on the **held-out** tiles, and 0 never stops early.  With nothing held out there
    is nothing honest to watch -- the score would be the training score, which goes
    on rising -- so early stopping is off, and the result says so.

    `on_epoch_end` is handed ``(epoch, epochs, values)`` after every epoch, with the
    losses and scores that also end up in the result's ``curve``.
    """
    from ultralytics import YOLO

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    report = dataset_.build_dataset(
        sources,
        config,
        names,
        directory / "dataset",
        open_recording,
        segment=segment,
        seed=seed,
        jitter_copies=jitter_copies,
        progress=None
        if progress is None
        else lambda done, total: progress("tiles", done, total, ""),
        hold_out_every=hold_out_every,
    )
    if report.train_events == 0:
        raise ValueError("no labelled events to train on")
    device = device_of(device)
    if batch is None:
        batch = 16 if device != "cpu" else 4
    if epochs is None:
        epochs = epochs_for(report.train_tiles, batch)
    epochs, batch = int(epochs), int(batch)
    if patience is None:
        patience = max(epochs // 3, 10)
    stopping = "on" if patience > 0 else "off"
    if not report.held_out and patience > 0:
        patience, stopping = 0, "off: nothing was held out to watch"
    weights = base or (SEGMENT_WEIGHTS if segment else DETECT_WEIGHTS)
    model = YOLO(weights_path(weights))
    started = time.perf_counter()
    curve: list = []

    # Ultralytics calls `on_fit_epoch_end` once more after training, validating
    # best.pt under the number one past the last epoch: measured, 201 points for a
    # run of 200.  Those are the best model's numbers and not an epoch's, and after an
    # early stop the number looks like an ordinary next epoch.  What tells them apart
    # is that only a real epoch has run `on_train_epoch_end` first.
    trained = {"epoch": False}

    def on_train_epoch(_trainer):
        trained["epoch"] = True

    def on_epoch(trainer):
        if not trained["epoch"]:
            return
        trained["epoch"] = False
        point = epoch_point(trainer)
        curve.append(point)
        if on_epoch_end is not None:
            on_epoch_end(point["epoch"], int(trainer.epochs), point)
        if progress is None:
            return
        score = point.get("map50")
        text = f"mAP50 {score:.3f}" if score is not None else ""
        progress("train", point["epoch"], int(trainer.epochs), text)

    model.add_callback("on_train_epoch_end", on_train_epoch)
    model.add_callback("on_fit_epoch_end", on_epoch)
    model.train(
        data=report.yaml,
        epochs=epochs,
        # Ultralytics reads 0 as "never stop"
        patience=patience,
        batch=batch,
        imgsz=config.image_size,
        project=os.fspath(directory),
        name="run",
        exist_ok=True,
        plots=False,
        verbose=False,
        device=device,
        workers=min(os.cpu_count() or 1, 8) if device != "cpu" else 2,
        seed=seed,
        deterministic=False,
        task="segment" if segment else "detect",
        **(SPECTROGRAM_AUGMENT if augment is None else augment),
    )
    trained = directory / "run" / "weights" / "best.pt"
    if not trained.is_file():
        trained = directory / "run" / "weights" / "last.pt"
    best = directory / "best.pt"
    shutil.copy2(trained, best)
    final = YOLO(os.fspath(best))
    metrics = final.val(
        data=report.yaml,
        imgsz=config.image_size,
        batch=batch,
        device=device,
        plots=False,
        verbose=False,
        save_json=False,
        project=os.fspath(directory),
        name="val",
        exist_ok=True,
    )
    box = metrics.box
    summary = {
        "map50": round(float(box.map50), 4),
        "map": round(float(box.map), 4),
        "precision": round(float(box.mp), 4),
        "recall": round(float(box.mr), 4),
    }
    meta = {
        "version": 1,
        "config": config.as_dict(),
        "names": list(names),
        "task": "segment" if segment else "detect",
        "threshold": TRAINED_CUT,
        "metrics": summary,
        #: False when too little was labelled to hold anything out, in which case
        #: `metrics` were measured on what it trained on and flatter it
        "held_out": report.held_out,
        "events_per_class": list(report.events_per_class),
        "train_tiles": report.train_tiles,
        "val_tiles": report.val_tiles,
        "train_events": report.train_events,
        "val_events": report.val_events,
        "background_tiles": report.background_tiles,
        "epochs": epochs,
        "epochs_run": len(curve),
        "best_epoch": best_epoch(curve),
        "batch": batch,
        "patience": patience,
        "early_stopping": stopping,
        "hold_out_every": int(hold_out_every),
        "curve": curve,
        "base": weights,
        "seconds": round(time.perf_counter() - started, 1),
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    (directory / MODEL_META).write_text(json.dumps(meta, indent=1))
    return {"model": os.fspath(best), **meta}
