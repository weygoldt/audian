"""Measuring the spectrogram detector on hand-labelled recordings.

Not a test: it trains models, takes minutes on a GPU, and reads recordings that are
not in this repository.  Run it directly, and read `docs/specdetector-design.md` for
what came out::

    .venv/bin/python tests/measure_specdetector.py configs --out <dir>

The ground truth is the chirp benchmark: 335 exhaustively labelled chirps in six
regions of four recordings at 20 kHz, in audian's own label format, none of them
ever seen by the old chirp detector.  Scoring is that benchmark's rule, so the
numbers can be set beside the old detector's: one-to-one, centres within 100 ms,
any overlap in frequency, only boxes centred in a region and overlapping its band.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from dataclasses import replace
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from audian_plugins.specdetector import tiling  # noqa: E402
from audian_plugins.specdetector.boxes import (  # noqa: E402
    Box,
    auto_cut,
    best_threshold,
    iou_matrix,
    match_events,
    nms,
)
from audian_plugins.specdetector.dataset import (  # noqa: E402
    Rect,
    Source,
    build_dataset,
)
from audian_plugins.specdetector.recording import Recording  # noqa: E402

ROOTS = {
    "2019-12-09": "/mnt/data2/2019_tube_competition/2019-12-09-10_00",
    "2020-06-03": "/mnt/data2/2019_tube_competition/2020-06-03-10_10",
    "2022-05-12": "/mnt/data2/2022_tube_competition/raw/2022-05-12-10_00",
    "2022-06-01": "/mnt/data2/2022_tube_competition/raw/2022-06-01-10_00",
}

#: What the frozen old detector (YOLOv8L, 7416 hand boxes plus simulations) reached
#: on these regions, pooled, from `detector_benchmark/results/summary.csv`.
OLD_DETECTOR = {
    "thr 0.30": {"precision": 0.993, "recall": 0.815, "f1": 0.895},
    "thr 0.50": {"precision": 1.000, "recall": 0.764, "f1": 0.866},
}

#: Augmentations that keep a spectrogram a spectrogram.  No flips and no rotation:
#: neither time nor frequency is symmetric.  No hue or saturation, since a tile has
#: none.  The old detector ran `fliplr=0.5` and never tested its effect.
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


def read_labels(name: str) -> tuple:
    """``(paths, regions, chirps)`` of one recording."""
    root = Path(ROOTS[name])
    regions, chirps = [], []
    with open(root / "traces-grid1-editable-labels.csv", newline="") as fp:
        for row in csv.DictReader(fp):
            t0, t1 = float(row["t_start_s"]), float(row["t_end_s"])
            f0, f1 = float(row["f_low_hz"]), float(row["f_high_hz"])
            if row["category"] == "labeled_region":
                regions.append(Rect(t0, t1, f0, f1))
            elif row["category"] == "chirp":
                chirps.append(Box(t0, t1, f0, f1))
    return [os.fspath(root / "traces-grid1.raw")], regions, chirps


def in_rect(box: Box, rect: Rect) -> bool:
    return (
        rect.t0 <= box.centre_t <= rect.t1 and box.f0 <= rect.f1 and box.f1 >= rect.f0
    )


def sweep(model, recording, config, t0, t1, *, conf=0.05, batch=32, imgsz=None) -> list:
    """Detect over ``[t0, t1]``, keeping each box in the tile that owns its centre."""
    grid = tiling.grid_of(config, recording.rate)
    length = grid.frames * grid.hop
    step = max(int(round(grid.step_s * grid.rate)), 1)
    starts = list(range(int(t0 * grid.rate) - length // 2, int(t1 * grid.rate), step))
    starts = [s for s in starts if 0 <= s <= recording.frames - length]
    owners = dict(zip(starts, tiling.owned_spans(starts, grid, recording.frames)))
    found, images, geometries = [], [], []

    def flush():
        results = model.predict(
            images,
            conf=conf,
            iou=0.5,
            imgsz=imgsz or config.image_size,
            verbose=False,
            device=DEVICE,
        )
        for result, (geometry, s0) in zip(results, geometries):
            own0, own1 = owners[s0]
            if result.boxes is None:
                continue
            xyxy = result.boxes.xyxy.cpu().numpy()
            scores = result.boxes.conf.cpu().numpy()
            classes = result.boxes.cls.cpu().numpy().astype(int)
            for (x0, y0, x1, y1), score, cls in zip(xyxy, scores, classes):
                a, b, c, d = geometry.box_from_pixels(x0, y0, x1, y1)
                if own0 <= 0.5 * (a + b) < own1:
                    found.append(Box(a, b, c, d, int(cls), float(score)))
        images.clear()
        geometries.clear()

    for s0 in starts:
        image, geometry = tiling.render_tile(recording, grid, s0, config)
        images.append(image)
        geometries.append((geometry, s0))
        if len(images) >= batch:
            flush()
    if images:
        flush()
    return nms(found, iou=0.5)


def score_on(model, config, targets, **kwargs) -> dict:
    """Pooled and per-recording scores over ``{name: [region indices] or None}``."""
    pooled_found, pooled_truth = [], []
    rows = {}
    start = time.perf_counter()
    swept_s = 0.0
    for name, wanted in targets.items():
        paths, regions, chirps = read_labels(name)
        found_here, truth_here = [], []
        with Recording(paths) as recording:
            for index, region in enumerate(regions):
                if wanted is not None and index not in wanted:
                    continue
                boxes = sweep(model, recording, config, region.t0, region.t1, **kwargs)
                found_here += [b for b in boxes if in_rect(b, region)]
                truth_here += [b for b in chirps if in_rect(b, region)]
                swept_s += region.t1 - region.t0
        threshold, match = best_threshold(found_here, truth_here)
        rows[name] = {
            "chirps": len(truth_here),
            "best_thr": threshold,
            "best_f1": round(match.f1, 3),
            "at_0.25_f1": round(
                match_events([b for b in found_here if b.score >= 0.25], truth_here).f1,
                3,
            ),
        }
        # Offset so that pooled boxes of two recordings can never match each other.
        offset = 1e6 * (len(pooled_truth) + 1)
        pooled_found += [
            b._replace(t0=b.t0 + offset, t1=b.t1 + offset) for b in found_here
        ]
        pooled_truth += [
            b._replace(t0=b.t0 + offset, t1=b.t1 + offset) for b in truth_here
        ]
    threshold, match = best_threshold(pooled_found, pooled_truth)
    at = {}
    for cut in (0.25, 0.5):
        m = match_events([b for b in pooled_found if b.score >= cut], pooled_truth)
        at[cut] = dict(
            precision=round(m.precision, 3),
            recall=round(m.recall, 3),
            f1=round(m.f1, 3),
        )
    return {
        "pooled_best": dict(
            thr=threshold,
            precision=round(match.precision, 3),
            recall=round(match.recall, 3),
            f1=round(match.f1, 3),
        ),
        "at_0.25": at[0.25],
        "at_0.5": at[0.5],
        "per_recording": rows,
        "seconds_per_recording_second": round(
            (time.perf_counter() - start) / max(swept_s, 1e-9), 4
        ),
    }


def train(
    yaml,
    out,
    name,
    *,
    model="yolo26n.pt",
    epochs=60,
    batch=16,
    imgsz=640,
    seed=0,
    **over,
):
    """A model trained on `yaml`, with the spectrogram augmentations."""
    from ultralytics import YOLO

    net = YOLO(os.fspath(WEIGHTS / model))
    args = dict(SPECTROGRAM_AUGMENT) | over
    net.train(
        data=yaml,
        epochs=epochs,
        patience=max(epochs // 3, 10),
        batch=batch,
        imgsz=imgsz,
        project=os.fspath(out),
        name=name,
        exist_ok=True,
        plots=False,
        verbose=False,
        device=DEVICE,
        workers=8,
        seed=seed,
        deterministic=False,
        **args,
    )
    return YOLO(os.fspath(Path(out) / name / "weights" / "best.pt"))


# ---------------------------------------------------------------- experiments

BAND = dict(fmin_hz=500.0, fmax_hz=1500.0)

#: Tile scales and windows on chirps, whose hand boxes are a median 174 ms x 134 Hz.
CONFIGS = {
    # what the old detector ran on: a 16 s tile puts about 7 px on a chirp
    "16s-205ms": dict(window_s=0.205, hop_s=0.0205, tile_s=16.0, overlap_s=2.0),
    "4s-205ms": dict(window_s=0.205, hop_s=0.00625, tile_s=4.0, overlap_s=1.0),
    "4s-100ms": dict(window_s=0.100, hop_s=0.00625, tile_s=4.0, overlap_s=1.0),
    "2s-50ms": dict(window_s=0.050, hop_s=0.003125, tile_s=2.0, overlap_s=0.5),
}


def experiment_configs(out: Path, only=None) -> dict:
    """Which picture of a chirp trains best, from one region's 90 labels."""
    paths, regions, chirps = read_labels("2019-12-09")
    region = regions[1]
    results = {}
    for key, values in CONFIGS.items():
        if only and key not in only:
            continue
        config = tiling.SpecConfig(**values, **BAND, reduction="max").validated()
        source = Source(
            paths,
            boxes=[b for b in chirps if in_rect(b, region)],
            reviewed=[region],
        )
        dense = replace(config, overlap_s=config.tile_s * 0.75)
        directory = out / key
        report = build_dataset(
            [source], dense, ["chirp"], directory / "dataset", Recording
        )
        start = time.perf_counter()
        model = train(report.yaml, directory, "run")
        minutes = (time.perf_counter() - start) / 60
        results[key] = {
            "train_tiles": report.train_tiles,
            "train_events": report.train_events,
            "val_tiles": report.val_tiles,
            "minutes": round(minutes, 2),
            "seen_recording": score_on(model, config, {"2019-12-09": [0, 2]}),
            "other_recordings": score_on(
                model,
                config,
                {"2020-06-03": None, "2022-05-12": None, "2022-06-01": None},
            ),
        }
        print(json.dumps({key: results[key]}, indent=1), flush=True)
    return results


# ---------------------------------------------------------------- few shots


def old_matcher():
    """The template matcher this plugin replaced, for the baseline numbers.

    It left the tree in the commit after 48a8310.  To measure it again, check that
    commit out beside this one and put its ``src`` on PYTHONPATH after this one's.
    """
    try:
        from audian_plugins.eventdetection import engine
    except ImportError:
        raise SystemExit(
            "the template matcher is not in this tree: "
            "`git worktree add ../matcher 48a8310`, then "
            "PYTHONPATH=src:../matcher/src for this baseline"
        ) from None
    return engine


#: The number of shots the DCASE few-shot bioacoustic task hands a system.
SHOTS = 5


def shots_and_targets() -> tuple:
    """The first five chirps of one region, and regions they were not taken from."""
    paths, regions, chirps = read_labels("2019-12-09")
    here = sorted((b for b in chirps if in_rect(b, regions[1])), key=lambda b: b.t0)
    return paths, here[:SHOTS], regions, chirps


def score_found(found, regions, chirps, wanted=(0, 2)) -> dict:
    """Scores of boxes over regions of the seen recording, with the recall at the
    loosest cut: a proposer is judged on what a reviewer can still rescue."""
    kept, truth = [], []
    for index in wanted:
        kept += [b for b in found if in_rect(b, regions[index])]
        truth += [b for b in chirps if in_rect(b, regions[index])]
    threshold, match = best_threshold(kept, truth)
    loosest = match_events(kept, truth)
    return {
        "chirps": len(truth),
        "best_thr": threshold,
        "precision": round(match.precision, 3),
        "recall": round(match.recall, 3),
        "f1": round(match.f1, 3),
        "max_recall": round(loosest.recall, 3),
        "boxes_at_max_recall": len(kept),
    }


def fewshot_ncc(paths, shots, regions, chirps) -> dict:
    """The template matcher this plugin replaces, on the same shots."""
    engine = old_matcher()

    settings = engine.Settings(
        domain=engine.SPECTROGRAM, representation="pcen", k=engine.MIN_K
    ).normalized()
    examples = [engine.Example(b.t0, b.t1, b.f0, b.f1) for b in shots]
    band = (min(b.f0 for b in shots), max(b.f1 for b in shots))
    found = []
    start = time.perf_counter()
    swept = 0.0
    with Recording(paths) as recording:
        a = int((shots[0].t0 - 5.0) * recording.rate)
        b = int((shots[-1].t1 + 5.0) * recording.rate)
        block = recording.read(a, b).astype(np.float64)
        templates = engine.learn(
            block, recording.rate, examples, settings, a / recording.rate
        )
        margin = engine.margin_s(templates)
        for index in (0, 2):
            region = regions[index]
            t = region.t0
            while t < region.t1:
                stop = min(t + 10.0, region.t1)
                a = int(max(t - margin, 0.0) * recording.rate)
                b = int((stop + margin) * recording.rate)
                block = recording.read(a, b).astype(np.float64)
                for hit in engine.detect(
                    block, recording.rate, templates, settings, a / recording.rate
                ):
                    if t <= hit.t0 < stop:
                        found.append(
                            Box(
                                hit.t0,
                                hit.t1,
                                float(
                                    hit.f_low_hz
                                    if hit.f_low_hz is not None
                                    else band[0]
                                ),
                                float(
                                    hit.f_high_hz
                                    if hit.f_high_hz is not None
                                    else band[1]
                                ),
                                0,
                                float(hit.score),
                            )
                        )
                swept += stop - t
                t = stop
    out = score_found(found, regions, chirps)
    out["seconds_per_recording_second"] = round(
        (time.perf_counter() - start) / swept, 4
    )
    return out


def fewshot_yoloe(paths, shots, regions, chirps, config, model_name="yoloe-26s-seg.pt"):
    """YOLOE visual prompts: no training, weights from natural images."""
    from ultralytics import YOLOE

    model = YOLOE(os.fspath(WEIGHTS / model_name))
    with Recording(paths) as recording:
        grid = tiling.grid_of(config, recording.rate)
        # The reference tile that holds the most shots: one image is all it takes.
        best_s0, best_inside = None, []
        for shot in shots:
            s0 = int((shot.centre_t - grid.tile_s / 2) * recording.rate)
            geometry = tiling.geometry_of(grid, s0, config.image_size)
            inside = [b for b in shots if b.t0 >= geometry.t0 and b.t1 <= geometry.t1]
            if len(inside) > len(best_inside):
                best_s0, best_inside = s0, inside
        reference, geometry = tiling.render_tile(recording, grid, best_s0, config)
        boxes = np.array(
            [geometry.box_to_pixels(b.t0, b.t1, b.f0, b.f1) for b in best_inside]
        )
        prompts = {"bboxes": boxes, "cls": np.zeros(len(boxes), dtype=int)}
        model.predict(
            reference,
            refer_image=reference,
            visual_prompts=prompts,
            conf=0.01,
            verbose=False,
            device=DEVICE,
            imgsz=config.image_size,
        )
        start = time.perf_counter()
        found, swept = [], 0.0
        for index in (0, 2):
            region = regions[index]
            found += sweep(
                model, recording, config, region.t0, region.t1, conf=0.01, batch=1
            )
            swept += region.t1 - region.t0
    out = score_found(found, regions, chirps)
    out["shots_used"] = len(best_inside)
    out["seconds_per_recording_second"] = round(
        (time.perf_counter() - start) / swept, 4
    )
    return out


class SamFeatures:
    """SAM's image encoder as a feature extractor, plus its mask decoder."""

    def __init__(self, model_name="mobile_sam.pt"):
        from ultralytics.models.sam import Predictor

        self.predictor = Predictor(
            overrides=dict(
                conf=0.0,
                task="segment",
                mode="predict",
                imgsz=1024,
                model=os.fspath(WEIGHTS / model_name),
                device=f"cuda:{DEVICE}" if DEVICE != "cpu" else "cpu",
                verbose=False,
                save=False,
            )
        )

    def encode(self, image):
        import torch

        self.predictor.reset_image()
        self.predictor.set_image(image)
        features = self.predictor.features
        return torch.nn.functional.normalize(features, dim=1)

    def segment_box(self, box):
        result = self.predictor(bboxes=[list(box)])[0]
        return None if result.masks is None else result.masks.data[0].cpu().numpy()

    def segment_point(self, x, y):
        result = self.predictor(points=[[float(x), float(y)]], labels=[1])[0]
        return None if result.masks is None else result.masks.data[0].cpu().numpy()


def fewshot_persam(paths, shots, regions, chirps, config, peak_floor=0.6) -> dict:
    """PerSAM-style: find what looks like the shots in SAM's own feature space."""
    import torch

    sam = SamFeatures()
    with Recording(paths) as recording:
        grid = tiling.grid_of(config, recording.rate)
        size = config.image_size
        prototypes, widths, heights = [], [], []
        for shot in shots:
            s0 = int((shot.centre_t - grid.tile_s / 2) * recording.rate)
            image, geometry = tiling.render_tile(recording, grid, s0, config)
            features = sam.encode(image)
            pixels = geometry.box_to_pixels(shot.t0, shot.t1, shot.f0, shot.f1)
            mask = sam.segment_box(pixels)
            if mask is None or not mask.any():
                mask = np.zeros((size, size), dtype=bool)
                x0, y0, x1, y1 = (int(v) for v in pixels)
                mask[y0:y1, x0:x1] = True
            cells = features.shape[-1]
            small = torch.nn.functional.interpolate(
                torch.as_tensor(mask, dtype=torch.float32, device=features.device)[
                    None, None
                ],
                size=(cells, cells),
                mode="area",
            )[0, 0]
            weights = small / small.sum().clamp(min=1e-6)
            prototypes.append((features[0] * weights).sum(dim=(1, 2)))
            widths.append(pixels[2] - pixels[0])
            heights.append(pixels[3] - pixels[1])
        prototype = torch.nn.functional.normalize(
            torch.stack(prototypes).mean(0), dim=0
        )
        width, height = float(np.median(widths)), float(np.median(heights))

        start = time.perf_counter()
        found, swept = [], 0.0
        length = grid.frames * grid.hop
        step = max(int(round(grid.step_s * grid.rate)), 1)
        for index in (0, 2):
            region = regions[index]
            swept += region.t1 - region.t0
            starts = range(
                int(region.t0 * grid.rate) - length // 2,
                int(region.t1 * grid.rate),
                step,
            )
            for s0 in starts:
                if not 0 <= s0 <= recording.frames - length:
                    continue
                image, geometry = tiling.render_tile(recording, grid, s0, config)
                features = sam.encode(image)
                similarity = (features[0] * prototype[:, None, None]).sum(0)
                pooled = torch.nn.functional.max_pool2d(
                    similarity[None, None], 3, stride=1, padding=1
                )[0, 0]
                peaks = (
                    torch.nonzero((similarity == pooled) & (similarity > peak_floor))
                    .cpu()
                    .numpy()
                )
                # Strongest first: `nonzero` returns them in raster order, and a
                # cap on that keeps the top rows of the image and nothing else.
                peaks = sorted(
                    peaks, key=lambda p: -float(similarity[int(p[0]), int(p[1])])
                )
                cells = similarity.shape[-1]
                # Each tile answers for its middle: the other half of the overlap on
                # each side.  Good enough for a comparison over one sweep.
                share = max(length - step, 0) / 2 / grid.rate
                own0, own1 = geometry.t0 + share, geometry.t1 - share
                for row, col in peaks[:32]:
                    x, y = (col + 0.5) / cells * size, (row + 0.5) / cells * size
                    mask = sam.segment_point(x, y)
                    bx0, by0, bx1, by1 = (
                        x - width / 2,
                        y - height / 2,
                        x + width / 2,
                        y + height / 2,
                    )
                    if mask is not None and mask.any():
                        ys, xs = np.nonzero(mask)
                        w, h = xs.max() - xs.min() + 1, ys.max() - ys.min() + 1
                        # A mask that swallowed the tile or a speck is SAM
                        # answering another question: keep the shots' own size.
                        if (
                            0.25 * width <= w <= 4 * width
                            and 0.25 * height <= h <= 4 * height
                        ):
                            bx0, by0, bx1, by1 = (
                                xs.min(),
                                ys.min(),
                                xs.max() + 1,
                                ys.max() + 1,
                            )
                    a, b, c, d = geometry.box_from_pixels(bx0, by0, bx1, by1)
                    if own0 <= 0.5 * (a + b) < own1:
                        found.append(Box(a, b, c, d, 0, float(similarity[row, col])))
    found = nms(found, iou=0.5)
    out = score_found(found, regions, chirps)
    out["seconds_per_recording_second"] = round(
        (time.perf_counter() - start) / swept, 4
    )
    return out


def fewshot_finetune(
    paths, shots, regions, chirps, config, out, *, masked=True, epochs=40
):
    """Train on the shots alone, each on a tile where only it is known.

    Unmasked is the trap the masking exists to avoid: the very same tiles, with
    whatever else they hold left on them, and so taught as background.
    """
    source = Source(paths, boxes=list(shots))
    name = "masked" if masked else "unmasked"
    directory = Path(out) / f"finetune-{name}"
    report = build_dataset(
        [source],
        config,
        ["chirp"],
        directory / "dataset",
        Recording,
        jitter_copies=16,
        masking=masked,
    )
    start = time.perf_counter()
    model = train(report.yaml, directory, "run", epochs=epochs, batch=8)
    minutes = (time.perf_counter() - start) / 60
    found = []
    swept_start = time.perf_counter()
    swept = 0.0
    with Recording(paths) as recording:
        for index in (0, 2):
            region = regions[index]
            found += sweep(model, recording, config, region.t0, region.t1, conf=0.01)
            swept += region.t1 - region.t0
    scored = score_found(found, regions, chirps)
    scored["train_tiles"] = report.train_tiles
    scored["train_events"] = report.train_events
    scored["background_tiles"] = report.background_tiles
    scored["minutes"] = round(minutes, 2)
    scored["seconds_per_recording_second"] = round(
        (time.perf_counter() - swept_start) / swept, 4
    )
    return scored


def fewshot_shipping(paths, shots, regions, chirps, config) -> dict:
    """The proposer as it ships: `models.Prompted`, an embedding from every shot."""
    from audian_plugins.specdetector import models

    models.configure(WEIGHTS)
    model = models.Prompted(device=f"cuda:{DEVICE}" if DEVICE != "cpu" else "cpu")
    with Recording(paths) as recording:
        used = model.prompt(recording, config, shots)
        start = time.perf_counter()
        windows = [(regions[i].t0, regions[i].t1) for i in (0, 2)]
        found = models.sweep(model, recording, config, windows, conf=0.01)
        elapsed = time.perf_counter() - start
    out = score_found(found, regions, chirps)
    out["shots_used"] = used
    out["seconds_per_recording_second"] = round(
        elapsed / sum(b - a for a, b in windows), 4
    )
    return out


def experiment_fewshot(out: Path, key: str, only=None) -> dict:
    """What five shots can find, with no other labels, by each proposer."""
    config = tiling.SpecConfig(**CONFIGS[key], **BAND, reduction="max").validated()
    paths, shots, regions, chirps = shots_and_targets()
    print("shots", [round(b.t0, 2) for b in shots], flush=True)
    steps = {
        "ncc": lambda: fewshot_ncc(paths, shots, regions, chirps),
        "persam": lambda: fewshot_persam(paths, shots, regions, chirps, config),
        "yoloe": lambda: fewshot_yoloe(paths, shots, regions, chirps, config),
        "shipping": lambda: fewshot_shipping(paths, shots, regions, chirps, config),
        "finetune-masked": lambda: fewshot_finetune(
            paths, shots, regions, chirps, config, out, masked=True
        ),
        "finetune-unmasked": lambda: fewshot_finetune(
            paths, shots, regions, chirps, config, out, masked=False
        ),
    }
    results = {}
    for name, step in steps.items():
        if only and name not in only:
            continue
        try:
            results[name] = step()
        except Exception as exc:  # noqa: BLE001 - one proposer, not the comparison
            import traceback

            traceback.print_exc()
            results[name] = {"error": f"{type(exc).__name__}: {exc}"}
        print(json.dumps({name: results[name]}), flush=True)
    return results


# ---------------------------------------------------------------- the whole loop

#: A pool to review from and regions nobody ever reviews, per recording.  What a
#: person would do with one recording: label some of it, run the rest.
LOOP_SPLITS = {
    "2019-12-09": dict(pool=[1], test=[0, 2]),
}

#: Review effort per round, in windows.
WINDOWS_PER_ROUND = 5
ROUNDS = 4


def split_region(name: str) -> tuple:
    """``(paths, pool, tests, chirps)``.  A recording with one region is cut in half."""
    paths, regions, chirps = read_labels(name)
    if name in LOOP_SPLITS:
        split = LOOP_SPLITS[name]
        return (
            paths,
            [regions[i] for i in split["pool"]],
            [regions[i] for i in split["test"]],
            chirps,
        )
    region = regions[0]
    middle = 0.5 * (region.t0 + region.t1)
    return (
        paths,
        [region._replace(t1=middle)],
        [region._replace(t0=middle)],
        chirps,
    )


def oracle_review(window, candidates, pool, truth) -> tuple:
    """What a perfect reviewer would say about one window."""
    from audian_plugins.specdetector.boxes import match_events as _match

    labelled = [b for b in truth if window.t0 <= b.t0 and b.t1 <= window.t1]
    rejected = []
    for index in window.candidates:
        box = candidates[index]
        result = _match([box._replace(cls=0)], labelled)
        if result.tp == 0:
            rejected.append(box)
    rect = None
    for region in pool:
        if window.t0 < region.t1 and window.t1 > region.t0:
            rect = Rect(
                max(window.t0, region.t0),
                min(window.t1, region.t1),
                region.f0,
                region.f1,
            )
            break
    return labelled, rejected, rect


def loop_once(
    name,
    strategy,
    out,
    *,
    shot_start=0,
    seed=0,
    rounds=ROUNDS,
    per_round=WINDOWS_PER_ROUND,
):
    """Five shots, then rounds of review and training, scored on what nobody reviewed."""
    from audian_plugins.specdetector import models, review

    device = f"cuda:{DEVICE}" if DEVICE != "cpu" else "cpu"
    models.configure(WEIGHTS)
    paths, pool, tests, chirps = split_region(name)
    pool_truth = sorted(
        (b for b in chirps if any(in_rect(b, r) for r in pool)), key=lambda b: b.t0
    )
    test_truth = [b for b in chirps if any(in_rect(b, r) for r in tests)]
    shots = pool_truth[shot_start : shot_start + SHOTS]
    span = (min(r.t0 for r in pool), max(r.t1 for r in pool))
    pool_windows = [(r.t0, r.t1) for r in pool]
    test_windows = [(r.t0, r.t1) for r in tests]

    with Recording(paths) as recording:
        config = tiling.suggest_config(
            [(b.t0, b.t1, b.f0, b.f1) for b in shots],
            recording.rate,
            channels=recording.channels,
        )
        grid = tiling.grid_of(config, recording.rate)

        def score(model, threshold):
            found = models.sweep(model, recording, config, test_windows, conf=0.01)
            found = [b for b in found if any(in_rect(b, r) for r in tests)]
            best, match = best_threshold(found, test_truth)
            at = match_events([b for b in found if b.score >= threshold], test_truth)
            return dict(
                best_thr=best,
                best_f1=round(match.f1, 3),
                thr=round(threshold, 3),
                precision=round(at.precision, 3),
                recall=round(at.recall, 3),
                f1=round(at.f1, 3),
            )

        history = []
        prompted = models.Prompted(device=device)
        prompted.prompt(recording, config, shots)
        candidates = models.sweep(prompted, recording, config, pool_windows, conf=0.01)
        threshold = auto_cut([b.score for b in candidates])
        history.append(
            dict(
                round=0,
                step="yoloe prompts",
                reviewed_windows=0,
                labels=len(shots),
                **score(prompted, threshold),
            )
        )
        print(json.dumps(dict(name=name, strategy=strategy, **history[-1])), flush=True)

        labelled = list(shots)
        rejected: list = []
        reviewed: list = []
        windows_seen = 0
        for number in range(1, rounds + 1):
            ranked = review.rank_windows(
                candidates,
                grid.tile_s,
                threshold,
                reviewed,
                strategy=strategy,
                span=span,
                seed=seed + number,
            )
            for window in ranked[:per_round]:
                new_labels, new_rejected, rect = oracle_review(
                    window, candidates, pool, pool_truth
                )
                labelled += new_labels
                rejected += new_rejected
                if rect is not None:
                    reviewed.append(rect)
                windows_seen += 1
            unique = list({(b.t0, b.t1): b for b in labelled}.values())
            result = models.train_detector(
                [Source(paths, boxes=unique, reviewed=reviewed, rejected=rejected)],
                config,
                ["chirp"],
                Path(out) / f"{name}-{strategy}-{shot_start}-{seed}" / f"round{number}",
                device=device,
                seed=seed,
            )
            model = models.Trained(result["model"], device)
            threshold = float(result["threshold"])
            history.append(
                dict(
                    round=number,
                    step="trained",
                    reviewed_windows=windows_seen,
                    labels=len(unique),
                    rejected=len(rejected),
                    train_tiles=result["train_tiles"],
                    val_events=result["val_events"],
                    held_out=result["held_out"],
                    seconds=result["seconds"],
                    **score(model, threshold),
                )
            )
            print(
                json.dumps(dict(name=name, strategy=strategy, **history[-1])),
                flush=True,
            )
            candidates = models.sweep(model, recording, config, pool_windows, conf=0.05)
            del model
            release_gpu()
    return dict(
        config=config.as_dict(),
        shots=[round(b.t0, 3) for b in shots],
        pool_chirps=len(pool_truth),
        test_chirps=len(test_truth),
        history=history,
    )


def experiment_loop(out: Path, only=None) -> dict:
    """The learning curve, by ranking, shot set and recording."""
    results = {}
    names = only or ["2019-12-09", "2020-06-03"]
    for name in names:
        for strategy in ("uncertain", "random"):
            for shot_start in (0, 20):
                key = f"{name}/{strategy}/{shot_start}"
                results[key] = loop_once(name, strategy, out, shot_start=shot_start)
                print(json.dumps({key: results[key]["config"]}), flush=True)
    return results


#: What each augmentation is worth, on one region's labels.  ``defaults`` is
#: Ultralytics' own, which is what the old chirp detector ran on.
AUGMENTS = {
    "shipping": {},
    "no-mosaic": dict(mosaic=0.0, close_mosaic=0),
    "time-flip": dict(fliplr=0.5),
    "defaults": None,
}


def experiment_augment(out: Path, only=None) -> dict:
    """Augmentations on one fixed dataset, scored on regions it never saw."""
    from audian_plugins.specdetector import models

    device = f"cuda:{DEVICE}" if DEVICE != "cpu" else "cpu"
    models.configure(WEIGHTS)
    paths, regions, chirps = read_labels("2019-12-09")
    pool, tests = regions[1], [regions[0], regions[2]]
    truth = [b for b in chirps if in_rect(b, pool)]
    test_truth = [b for b in chirps if any(in_rect(b, r) for r in tests)]
    with Recording(paths) as recording:
        config = tiling.suggest_config(
            [(b.t0, b.t1, b.f0, b.f1) for b in truth],
            recording.rate,
            channels=recording.channels,
        )
    results = {}
    for name, over in AUGMENTS.items():
        if only and name not in only:
            continue
        if over is None:
            # Ultralytics' defaults, as `yolo train` would have them.
            from ultralytics.cfg import get_cfg

            defaults = vars(get_cfg())
            augment = {k: defaults[k] for k in models.SPECTROGRAM_AUGMENT}
        else:
            augment = {**models.SPECTROGRAM_AUGMENT, **over}
        scores = []
        for seed in (0, 1, 2):
            result = models.train_detector(
                [Source(paths, boxes=truth, reviewed=[pool])],
                config,
                ["chirp"],
                out / f"{name}-{seed}",
                device=device,
                seed=seed,
                augment=augment,
            )
            model = models.Trained(result["model"], device)
            with Recording(paths) as recording:
                found = models.sweep(
                    model, recording, config, [(r.t0, r.t1) for r in tests], conf=0.01
                )
            found = [b for b in found if any(in_rect(b, r) for r in tests)]
            best, match = best_threshold(found, test_truth)
            at = match_events(
                [b for b in found if b.score >= result["threshold"]], test_truth
            )
            scores.append(
                dict(
                    seed=seed,
                    best_f1=round(match.f1, 3),
                    thr=result["threshold"],
                    precision=round(at.precision, 3),
                    recall=round(at.recall, 3),
                    f1=round(at.f1, 3),
                    seconds=result["seconds"],
                )
            )
            print(json.dumps(dict(name=name, **scores[-1])), flush=True)
            del model
            release_gpu()
        results[name] = scores
    return results


# ---------------------------------------------------------------- SAM on real boxes


def box_iou(a, b) -> float:
    """IoU of two ``(t0, t1, f0, f1)``."""
    dt = min(a[1], b[1]) - max(a[0], b[0])
    df = min(a[3], b[3]) - max(a[2], b[2])
    if dt <= 0 or df <= 0:
        return 0.0
    inter = dt * df
    union = (a[1] - a[0]) * (a[3] - a[2]) + (b[1] - b[0]) * (b[3] - b[2]) - inter
    return inter / union


def cricket_boxes(category: str) -> list:
    """The reader's hand labels of the cricket, from the test fixture."""
    out = []
    with open(REPO / "tests" / "data" / "gryllus-fewshot-labels.csv", newline="") as fp:
        for row in csv.DictReader(fp):
            if row["category"] == category:
                out.append(
                    Box(
                        float(row["t_start_s"]),
                        float(row["t_end_s"]),
                        float(row["f_low_hz"]),
                        float(row["f_high_hz"]),
                    )
                )
    return out


CRICKET = "/home/weygoldt/wrk/tools/claudian/data/Gryllus_campestris.wav"


def sam_targets() -> dict:
    """``{name: (paths, boxes, view_seconds, band)}``: what to prompt SAM on."""
    paths, regions, chirps = read_labels("2019-12-09")
    here = sorted((b for b in chirps if in_rect(b, regions[1])), key=lambda b: b.t0)
    return {
        "chirp": (paths, here[:40], 3.0, (500.0, 1500.0)),
        "cricket pulse": ([CRICKET], cricket_boxes("pulse"), 0.4, (0.0, 24000.0)),
        "cricket syllable": ([CRICKET], cricket_boxes("syllable"), 1.0, (0.0, 24000.0)),
    }


def experiment_sam(out: Path, only=None) -> dict:
    """How near SAM's answer comes to a person's box: a click, and a loose drag."""
    from audian_plugins.specdetector import models

    device = f"cuda:{DEVICE}" if DEVICE != "cpu" else "cpu"
    models.configure(WEIGHTS)
    results = {}
    for weights in only or ["mobile_sam.pt", "sam2.1_t.pt"]:
        segmenter = models.Segmenter(weights, device)
        for name, (paths, boxes, view_s, band) in sam_targets().items():
            clicks, drags, encode, prompt = [], [], [], []
            for box in boxes:
                t0 = max(box.centre_t - view_s / 2, 0.0)
                encode.append(
                    segmenter.set_view(paths, t0, t0 + view_s, band[0], band[1])
                )
                truth = (box.t0, box.t1, box.f0, box.f1)
                start = time.perf_counter()
                answer = segmenter.segment(
                    points=[(box.centre_t, 0.5 * (box.f0 + box.f1), True)]
                )
                prompt.append(1e3 * (time.perf_counter() - start))
                clicks.append(0.0 if answer is None else box_iou(answer["box"], truth))
                # A drag as a person makes it: a quarter too large on every side.
                dt, df = 0.25 * (box.t1 - box.t0), 0.25 * (box.f1 - box.f0)
                answer = segmenter.segment(
                    box=(box.t0 - dt, box.t1 + dt, box.f0 - df, box.f1 + df)
                )
                drags.append(0.0 if answer is None else box_iou(answer["box"], truth))
            row = dict(
                n=len(boxes),
                click_iou_median=round(float(np.median(clicks)), 3),
                click_iou_p25=round(float(np.percentile(clicks, 25)), 3),
                drag_iou_median=round(float(np.median(drags)), 3),
                drag_iou_p25=round(float(np.percentile(drags, 25)), 3),
                encode_ms=round(float(np.median(encode)), 1),
                prompt_ms=round(float(np.median(prompt)), 1),
            )
            results[f"{weights}/{name}"] = row
            print(json.dumps({f"{weights}/{name}": row}), flush=True)
        segmenter.close()
        del segmenter
        release_gpu()
    return results


# ---------------------------------------------------------------- what images to make


def experiment_suggest(out: Path, only=None) -> dict:
    """How large an event should be on a tile, and how much band to keep around it.

    Over two sets of shots on chirps and the hand labels on the cricket, by the two
    proposers: YOLOE as it comes, and a detector trained on the shots alone.
    """
    from audian_plugins.specdetector import models

    device = f"cuda:{DEVICE}" if DEVICE != "cpu" else "cpu"
    models.configure(WEIGHTS)
    paths, _shots, regions, chirps = shots_and_targets()
    here = sorted((b for b in chirps if in_rect(b, regions[1])), key=lambda b: b.t0)
    tests = [(regions[i].t0, regions[i].t1) for i in (0, 2)]
    truth = cricket_truth()
    syllables = cricket_boxes("syllable")
    results = {}
    for pixels, margin in ((27.0, 1.0), (40.0, 1.0), (27.0, 3.0), (40.0, 3.0)):
        tiling.EVENT_PIXELS_T = pixels
        tiling.BAND_MARGIN = margin
        key = f"{pixels:.0f}px-band{margin:.0f}"
        if only and key not in only:
            continue

        def config_of(boxes, recording):
            return tiling.suggest_config(
                [(b.t0, b.t1, b.f0, b.f1) for b in boxes],
                recording.rate,
                channels=recording.channels,
            )

        row: dict = {}
        for start in (0, 20):
            shots = here[start : start + SHOTS]
            with Recording(paths) as recording:
                config = config_of(shots, recording)
                row["chirp_tile_s"] = round(config.tile_s, 2)
                prompted = models.Prompted(device=device)
                prompted.prompt(recording, config, shots)
                found = models.sweep(prompted, recording, config, tests, conf=0.01)
                row[f"chirp{start}_yoloe"] = score_found(found, regions, chirps)["f1"]
                del prompted
                release_gpu()
            result = models.train_detector(
                [Source(paths, boxes=shots)],
                config,
                ["chirp"],
                out / key / f"chirp{start}",
                device=device,
                jitter_copies=16,
            )
            model = models.Trained(result["model"], device)
            with Recording(paths) as recording:
                found = models.sweep(model, recording, config, tests, conf=0.01)
            scored = score_found(found, regions, chirps)
            kept = []
            wanted = []
            for index in (0, 2):
                kept += [
                    b
                    for b in found
                    if in_rect(b, regions[index]) and b.score >= models.TRAINED_CUT
                ]
                wanted += [b for b in chirps if in_rect(b, regions[index])]
            row[f"chirp{start}_trained"] = scored["f1"]
            row[f"chirp{start}_trained_at_cut"] = round(
                match_events(kept, wanted).f1, 3
            )
            del model
            release_gpu()
        with Recording([CRICKET]) as recording:
            config = config_of(syllables, recording)
            row["cricket_tile_s"] = round(config.tile_s, 2)
            prompted = models.Prompted(device=device)
            prompted.prompt(recording, config, syllables)
            found = models.sweep(prompted, recording, config, None, conf=0.01)
            row["cricket_yoloe"] = cricket_scores(found, truth, syllables)["f1"]
            del prompted
            release_gpu()
        result = models.train_detector(
            [Source([CRICKET], boxes=syllables)],
            config,
            ["syllable"],
            out / key / "cricket",
            device=device,
            jitter_copies=16,
        )
        model = models.Trained(result["model"], device)
        with Recording([CRICKET]) as recording:
            found = models.sweep(model, recording, config, None, conf=0.01)
        row["cricket_trained"] = cricket_scores(found, truth, syllables)["f1"]
        row["cricket_trained_at_cut"] = cricket_scores(
            [b for b in found if b.score >= models.TRAINED_CUT], truth, syllables
        )["recall"]
        del model
        release_gpu()
        results[key] = row
        print(json.dumps({key: row}), flush=True)
    return results


# ---------------------------------------------------------------- several resolutions


def resample_edges(power: np.ndarray, edges_in, edges_out) -> np.ndarray:
    """``(frames, bins)`` onto rows with these edges, by the exact overlap with bins."""
    widths = np.diff(edges_in)
    total = np.concatenate(
        [np.zeros((len(power), 1)), np.cumsum(power * widths, axis=1)], axis=1
    )
    at = np.empty((len(power), len(edges_out)))
    position = np.searchsorted(edges_in, edges_out, side="right") - 1
    position = np.clip(position, 0, len(widths) - 1)
    fraction = np.clip((edges_out - edges_in[position]) / widths[position], 0.0, 1.0)
    at[:] = total[:, position] + fraction * power[:, position] * widths[position]
    return np.diff(at, axis=1) / np.diff(edges_out)


def multires_render(reader, grid, s0, config, channels=None):
    """`tiling.render_tile` with a short, the configured and a long window as channels.

    Every channel has the same frame centres, and bins go onto rows by the exact
    overlap of their edges: the pipeline of the published composite, in the shape the
    plugin's tiles have, so that nothing else about the comparison changes.
    """
    size = config.image_size
    planes = []
    edges_out = np.linspace(grid.f_low, grid.f_high, size + 1)
    for factor in (0.25, 1.0, 4.0):
        nfft = max(int(round(grid.nfft * factor)), 8)
        if (nfft - grid.hop) % 2:
            nfft += 1
        df = grid.rate / nfft
        k0 = max(int(np.floor(grid.f_low / df + 0.5)), 0)
        k1 = min(int(np.ceil(grid.f_high / df - 0.5)) + 1, nfft // 2 + 1)
        here = grid._replace(
            nfft=nfft,
            k0=k0,
            k1=k1,
            f_low=(k0 - 0.5) * df,
            f_high=(k1 - 0.5) * df,
        )
        power = tiling.stft_power(tiling.tile_samples(reader, here, s0), here)
        power = tiling.reduce_channels(power, config.reduction, channels)
        edges_in = (np.arange(k0, k1 + 1) - 0.5) * df
        rows = resample_edges(power.astype(np.float64), edges_in, edges_out)
        columns = tiling._resample(rows, size, 0)
        db = 10.0 * np.log10(columns + tiling.EPS)
        plane = tiling._stretch_whitened(db).T[::-1]
        planes.append(np.clip(plane * 255.0 + 0.5, 0, 255).astype(np.uint8))
    return np.dstack(planes), tiling.geometry_of(grid, s0, size)


def experiment_multires(out: Path, only=None) -> dict:
    """Whether several resolutions as channels train a better detector than one.

    The same shots, the same held-out regions, and three seeds each: the differences
    between seeds are what any difference between the two has to be read against.
    """
    from audian_plugins.specdetector import models

    device = f"cuda:{DEVICE}" if DEVICE != "cpu" else "cpu"
    models.configure(WEIGHTS)
    single = tiling.render_tile
    paths, _shots, regions, chirps = shots_and_targets()
    here = sorted((b for b in chirps if in_rect(b, regions[1])), key=lambda b: b.t0)
    tests = [(regions[i].t0, regions[i].t1) for i in (0, 2)]
    truth = cricket_truth()
    syllables = cricket_boxes("syllable")
    results: dict = {}

    def chirp_case(kind, start, seed):
        shots = here[start : start + SHOTS]
        with Recording(paths) as recording:
            config = tiling.suggest_config(
                [(b.t0, b.t1, b.f0, b.f1) for b in shots],
                recording.rate,
                channels=recording.channels,
            )
        result = models.train_detector(
            [Source(paths, boxes=shots)],
            config,
            ["chirp"],
            out / kind / f"chirp{start}-{seed}",
            device=device,
            jitter_copies=16,
            seed=seed,
        )
        model = models.Trained(result["model"], device)
        with Recording(paths) as recording:
            found = models.sweep(model, recording, config, tests, conf=0.01)
        scored = score_found(found, regions, chirps)
        del model
        release_gpu()
        return scored["f1"]

    def cricket_case(kind, seed):
        with Recording([CRICKET]) as recording:
            config = tiling.suggest_config(
                [(b.t0, b.t1, b.f0, b.f1) for b in syllables],
                recording.rate,
                channels=recording.channels,
            )
        result = models.train_detector(
            [Source([CRICKET], boxes=syllables)],
            config,
            ["syllable"],
            out / kind / f"cricket-{seed}",
            device=device,
            jitter_copies=16,
            seed=seed,
        )
        model = models.Trained(result["model"], device)
        with Recording([CRICKET]) as recording:
            found = models.sweep(model, recording, config, None, conf=0.01)
        scored = cricket_scores(found, truth, syllables)
        del model
        release_gpu()
        return scored["f1"]

    for kind, render in (("single", single), ("multires", multires_render)):
        tiling.render_tile = render
        row: dict = {}
        for start in (0, 20):
            row[f"chirp{start}"] = [chirp_case(kind, start, seed) for seed in (0, 1, 2)]
        row["cricket"] = [cricket_case(kind, seed) for seed in (0, 1, 2)]
        results[kind] = row
        print(json.dumps({kind: row}), flush=True)
    tiling.render_tile = single
    return results


def track_rises(
    name: str, min_size_hz: float = 10.0, length_s: float = 4.0, every: bool = False
) -> list:
    """Boxes around the rises the competition analysis found, placed by the tracks.

    Silver truth, and to be read as such: detected from the EOD tracks by that analysis,
    never checked by hand, and only as good as the tracks.  Read through the analysis's
    own arrays, which are in the order of its ``ids.npy``: that is not sorted, and pairing
    rises with sorted track ids read each fish's rises off the other fish's track, which
    made rises of 17 Hz look like 0.8 Hz.

    The analysis's index says a rise happened and not where: on the images the step lay
    up to about 3 s from it, and a detector that put its box on the step was scored as
    wrong.  So every box starts half a second before the track first climbs 40% of the
    way up, is the same length, and runs from the baseline, taken 3 to 10 s before the
    index, to the peak with a little margin.  Ending a box where the track fell by half
    gave lengths from 0.9 to 6 s, set mostly by the noise of the track.

    Only rises the track itself confirms are kept, of at least `min_size_hz`: under about
    8 Hz the boxes were mostly over a flat line.  `every` also returns the ones this
    leaves out, so that a stretch picked to hold no rise can avoid them too.
    """
    root = Path(ROOTS[name])
    analysis = root / "analysis"
    rise_idx = np.load(analysis / "rise_idx.npy", allow_pickle=True)
    rise_size = np.load(analysis / "rise_size.npy", allow_pickle=True)
    fish_freq = np.load(analysis / "fish_freq.npy", allow_pickle=True)
    times = np.load(root / "fill_times.npy")
    boxes = []
    for indices, sizes, freq in zip(rise_idx, rise_size, fish_freq):
        freq = np.asarray(freq, dtype=float)
        indices = np.asarray(indices, dtype=float)
        sizes = np.asarray(sizes, dtype=float)
        keep = ~np.isnan(indices)
        for index, size in zip(indices[keep].astype(int), sizes[keep]):
            if size < min_size_hz or index < 1 or index >= len(times):
                continue
            first = max(index - 12, 0)
            near = freq[first : index + 12]
            early = freq[max(index - 30, 0) : max(index - 9, 0)]
            has_near = np.any(~np.isnan(near))
            has_early = np.any(~np.isnan(early))
            if not has_near and not has_early:
                continue
            base = float(np.nanmedian(early if has_early else near))
            peak = float(np.nanmax(near)) - base if has_near else 0.0
            climbed = np.flatnonzero(near - base >= 0.4 * size)
            shown = has_early and len(climbed) > 0 and peak >= 0.7 * size
            if not shown and not every:
                continue
            onset = first + int(climbed[0]) if len(climbed) else index
            t = float(times[onset])
            boxes.append(
                Box(
                    t - 0.5,
                    t + length_s - 0.5,
                    base - 3.0,
                    base + max(float(size), peak) + 3.0,
                    1,
                )
            )
    return sorted(boxes, key=lambda b: b.t0)


class TrackJudge:
    """Does a fish's own track step up inside a box?  What scores a found rise.

    The rises listed by the analysis cannot say a detection is false: of a detector's 36
    "false" rises the highest-scoring were mostly plain steps that it does not list.  So
    the list is what recall is measured against, and any detection is right if a track
    in its band climbs by `step_hz` over its median of the 4 s before, for at least a
    quarter of the next 3 s: about a second.  A chirp is over within one of the track's
    0.33 s samples, and cannot pass.
    """

    def __init__(self, name: str, step_hz: float = 5.0) -> None:
        import warnings

        root = Path(ROOTS[name])
        self.times = np.load(root / "fill_times.npy")
        self.step_hz = step_hz
        self.fish = []
        self.known: dict = {}
        before_n, after_n = 12, 9
        fish_freq = np.load(root / "analysis" / "fish_freq.npy", allow_pickle=True)
        for freq in fish_freq:
            freq = np.asarray(freq, dtype=float)
            step = np.full(len(freq), np.nan)
            base = np.full(len(freq), np.nan)
            view = np.lib.stride_tricks.sliding_window_view
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", category=RuntimeWarning)
                before = np.nanmedian(view(freq, before_n), axis=1)
                after = np.nanpercentile(view(freq, after_n), 75, axis=1)
            n = len(freq) - before_n - after_n + 1
            base[before_n : before_n + n] = before[:n]
            step[before_n : before_n + n] = after[before_n:][:n] - before[:n]
            self.fish.append((step, base))

    def __call__(self, box: Box) -> bool:
        if box in self.known:
            return self.known[box]
        i0 = np.searchsorted(self.times, box.t0 - 1.0)
        i1 = np.searchsorted(self.times, box.t1)
        shown = False
        for step, base in self.fish:
            with np.errstate(invalid="ignore"):
                here = (
                    (step[i0:i1] >= self.step_hz)
                    & (base[i0:i1] >= box.f0 - 5.0)
                    & (base[i0:i1] <= box.f1 + 5.0)
                )
            shown = shown or bool(here.any())
        self.known[box] = shown
        return shown


def rise_scores(found, truth, judge, tol_s: float = 1.5) -> tuple:
    """``(precision, recall, f1)`` of found rises, by the track and the list.

    Precision is how many the `judge` accepts, a second box within 3 s of an accepted
    one being wrong.  Recall is how many of `truth` have an accepted box, one to one.
    """
    accepted, wrong = [], 0
    for box in sorted(found, key=lambda b: -b.score):
        centre = 0.5 * (box.t0 + box.t1)
        if any(abs(centre - 0.5 * (a.t0 + a.t1)) <= 3.0 for a in accepted):
            wrong += 1
        elif judge(box):
            accepted.append(box)
        else:
            wrong += 1
    precision = len(accepted) / (len(accepted) + wrong) if found else 0.0
    recall = match_events(accepted, truth, time_tol_s=tol_s).recall
    total = precision + recall
    return precision, recall, 2 * precision * recall / total if total else 0.0


def best_rise_scores(found, truth, judge, tol_s: float = 1.5) -> tuple:
    """`rise_scores` at the cut with the highest F1, trying every hundredth."""
    best = rise_scores(found, truth, judge, tol_s)
    for cut in sorted({round(b.score, 2) for b in found}):
        scored = rise_scores([b for b in found if b.score >= cut], truth, judge, tol_s)
        if scored[2] > best[2]:
            best = scored
    return best


def box_tightness(found, truth, tol_s: float) -> tuple:
    """Median centre error in ms and median IoU, over the truth with a box near it."""
    errors, overlaps = [], []
    for wanted in truth:
        centre = 0.5 * (wanted.t0 + wanted.t1)
        near = [
            b
            for b in found
            if abs(0.5 * (b.t0 + b.t1) - centre) <= tol_s
            and b.f0 < wanted.f1
            and wanted.f0 < b.f1
        ]
        if near:
            nearest = min(near, key=lambda b: abs(0.5 * (b.t0 + b.t1) - centre))
            errors.append(abs(0.5 * (nearest.t0 + nearest.t1) - centre) * 1000.0)
            overlaps.append(
                iou_matrix(
                    np.array([[nearest.t0, nearest.t1, nearest.f0, nearest.f1]]),
                    np.array([[wanted.t0, wanted.t1, wanted.f0, wanted.f1]]),
                )[0, 0]
            )
    if not errors:
        return float("nan"), 0.0
    return float(np.median(errors)), float(np.median(overlaps))


def old_detector_chirps(name: str) -> list:
    """The old detector's chirps, at its own cut: silver, with precision near 1.0 and
    recall about 0.76 on the hand-labelled regions."""
    boxes = []
    with open(Path(ROOTS[name]) / "chirpdetector_bboxes.csv", newline="") as fp:
        for row in csv.DictReader(fp):
            boxes.append(
                Box(
                    float(row["t1"]),
                    float(row["t2"]),
                    float(row["f1"]),
                    float(row["f2"]),
                    0,
                )
            )
    return sorted(boxes, key=lambda b: b.t0)


#: The arms of the two-class test: one window each, then all three as channels.  The
#: stack's channels are exactly the three single arms.
TWO_CLASS_ARMS = {
    "50 ms": (0.05, tiling.render_tile),
    "200 ms": (0.2, tiling.render_tile),
    "800 ms": (0.8, tiling.render_tile),
    "stack": (0.2, multires_render),
}


#: Time before this in 2019-12-09 is the test, with all of its hand-labelled chirps.
TWO_CLASS_SPLIT_S = 7000.0


def two_class_sources(rng) -> tuple:
    """What the two-class test trains on: whole stretches, labelled throughout.

    Chirps come in bursts, a median of 4 others within 4 s of each, and rises come with
    chirps.  Labelling a random part of them would teach a network that the rest, sitting
    beside them on the same tile, are nothing.  So what is chosen is time, and everything
    known in that time is labelled.  Stretches are longer than a tile, so tiles sweep them
    and nothing is masked.  The silver chirps still miss about a quarter of the real ones,
    and those stay negatives, the same for every arm.
    """
    name, other = "2019-12-09", "2020-06-03"
    sources, counts = [], dict(rises=0, chirps=0, chirp_stretches=0)
    for recording, chirp_events in ((name, 150), (other, 0)):
        rises = track_rises(recording)
        every = track_rises(recording, min_size_hz=0.0, every=True)
        silver = old_detector_chirps(recording)
        first_s = TWO_CLASS_SPLIT_S if recording == name else 0.0
        rises = [r for r in rises if r.t0 >= first_s]
        stretches = [(r.t0 - 6.0, r.t1 + 6.0) for r in rises]
        seeds = [c for c in silver if c.t0 >= first_s]
        picked = 0
        for index in rng.permutation(len(seeds)):
            if picked >= chirp_events:
                break
            a, z = seeds[index].t0 - 12.0, seeds[index].t0 + 12.0
            if a < first_s or any(a < y and x < z for x, y in stretches):
                continue
            # No rise of any kind, labelled or not, in a stretch picked for chirps.
            if any(a < r.t1 and r.t0 < z for r in every):
                continue
            stretches.append((a, z))
            picked += sum(a <= c.t0 and c.t1 <= z for c in silver)
            counts["chirp_stretches"] += 1
        labels = [
            b
            for b in silver + rises
            if any(a <= b.t0 and b.t1 <= z for a, z in stretches)
        ]
        paths = read_labels(recording)[0]
        # Nobody looked for chirps in the other recording, and faint marks there are
        # unboxed.  Its rises bring their own small rectangles, and the rest is masked.
        reviewed = [Rect(a, z) for a, z in stretches] if chirp_events else []
        sources.append(Source(paths, boxes=labels, reviewed=reviewed))
        counts["rises"] += sum(b.cls == 1 for b in labels)
        counts["chirps"] += sum(b.cls == 0 for b in labels)
    return sources, counts


def experiment_two_classes(out: Path, only=None, seeds=(0, 1, 2)) -> dict:
    """One network, chirps and rises: does several resolutions as channels pay here?

    Chirps are about 174 ms x 134 Hz and rises 4 s x at least 10 Hz, so a window that
    resolves one blurs the other.  Each arm trains on the same stretches, labelled with
    silver chirps and track-derived rises, and is scored on the first 7000 s of 2019-12-09:
    chirps against the hand labels in their regions, and rises against the track's.  A
    rise counts as right if its own track shows one (`TrackJudge`), and every detection is
    kept beside its model so that a change of rule needs no training.
    """
    from audian_plugins.specdetector import models

    name = "2019-12-09"
    split_s = TWO_CLASS_SPLIT_S
    paths, regions, chirps = read_labels(name)
    rises = track_rises(name)
    test_rises = [b for b in rises if b.t1 < split_s]
    judge = TrackJudge(name)
    test_regions = [r for r in regions if r.t1 < split_s]
    gold = [b for b in chirps if any(in_rect(b, r) for r in test_regions)]
    sources, counts = two_class_sources(np.random.default_rng(0))
    # The judge's controls: it should pass the known rises, and hardly a stretch of track
    # picked at random, which is mostly flat.
    rng = np.random.default_rng(1)
    fish_freq = np.load(
        Path(ROOTS[name]) / "analysis" / "fish_freq.npy", allow_pickle=True
    )
    times = np.load(Path(ROOTS[name]) / "fill_times.npy")
    chance = []
    while len(chance) < 2000:
        t = float(rng.uniform(10.0, split_s - 10.0))
        f = float(fish_freq[rng.integers(len(fish_freq))][np.searchsorted(times, t)])
        if not np.isnan(f):
            chance.append(judge(Box(t, t + 4.0, f - 3.0, f + 13.0, 1)))
    print(
        json.dumps(
            counts
            | dict(
                test_rises=len(test_rises),
                test_chirps=len(gold),
                judge_passes_known=round(
                    float(np.mean([judge(b) for b in test_rises])), 3
                ),
                judge_passes_random=round(float(np.mean(chance)), 3),
            )
        ),
        flush=True,
    )
    single_render = TWO_CLASS_ARMS["50 ms"][1]
    results: dict = {}
    for arm, (window_s, render) in TWO_CLASS_ARMS.items():
        if only and arm not in only:
            continue
        tiling.render_tile = render
        # Tiles overlap by the length of a rise, so that each is whole on the tile that
        # owns its centre.
        config = tiling.SpecConfig(
            window_s=window_s,
            hop_s=8.0 / 640,
            tile_s=8.0,
            overlap_s=4.0,
            fmin_hz=650.0,
            fmax_hz=1000.0,
            reduction="max",
        ).validated()
        directory = out / arm.replace(" ", "")
        report = build_dataset(
            sources,
            config,
            ["chirp", "rise"],
            directory / "dataset",
            Recording,
        )
        print(arm, "tiles", report.train_tiles, report.events_per_class, flush=True)
        # Render the test once: the arm's seeds then only differ in the network.
        with Recording(paths) as recording:
            grid = tiling.grid_of(config, recording.rate)
            length = grid.frames * grid.hop
            step = max(int(round(grid.step_s * grid.rate)), 1)
            starts = list(range(0, int(split_s * grid.rate) - length, step))
            owners = tiling.owned_spans(starts, grid, recording.frames)
            rendered = [render(recording, grid, s0, config) for s0 in starts]
        rows = []
        for seed in seeds:
            epochs = max(int(np.ceil(800 * 16 / max(report.train_tiles, 1))), 30)
            model = train(
                report.yaml,
                directory,
                f"seed{seed}",
                epochs=epochs,
                seed=seed,
            )
            found = []
            for begin in range(0, len(rendered), 32):
                batch = rendered[begin : begin + 32]
                results_ = model.predict(
                    [image for image, _ in batch],
                    conf=0.01,
                    iou=0.5,
                    imgsz=640,
                    verbose=False,
                    device=DEVICE,
                )
                for result, (_image, geometry), (own0, own1) in zip(
                    results_, batch, owners[begin : begin + 32]
                ):
                    xyxy = result.boxes.xyxy.cpu().numpy()
                    scores = result.boxes.conf.cpu().numpy()
                    classes = result.boxes.cls.cpu().numpy().astype(int)
                    for (x0, y0, x1, y1), score, cls in zip(xyxy, scores, classes):
                        a, b, c, d = geometry.box_from_pixels(x0, y0, x1, y1)
                        if own0 <= 0.5 * (a + b) < own1:
                            found.append(Box(a, b, c, d, int(cls), float(score)))
            found = nms(found, iou=0.5)
            found_chirps = [
                b
                for b in found
                if b.cls == 0 and any(in_rect(b, r) for r in test_regions)
            ]
            found_rises = [b for b in found if b.cls == 1]
            (directory / f"seed{seed}" / "found.json").write_text(json.dumps(found))
            _, chirp_best = best_threshold(found_chirps, gold)
            cut = models.TRAINED_CUT
            chirp_at = match_events([b for b in found_chirps if b.score >= cut], gold)
            rise_at = rise_scores(
                [b for b in found_rises if b.score >= cut], test_rises, judge
            )
            rise_best = best_rise_scores(found_rises, test_rises, judge)
            row = dict(
                arm=arm,
                seed=seed,
                chirp_best=round(chirp_best.f1, 3),
                chirp_f1=round(chirp_at.f1, 3),
                chirp_p=round(chirp_at.precision, 3),
                chirp_r=round(chirp_at.recall, 3),
                rise_best=round(rise_best[2], 3),
                rise_best_p=round(rise_best[0], 3),
                rise_best_r=round(rise_best[1], 3),
                rise_f1=round(rise_at[2], 3),
                rise_p=round(rise_at[0], 3),
                rise_r=round(rise_at[1], 3),
            )
            rows.append(row)
            print(json.dumps(row), flush=True)
            del model
            release_gpu()
        results[arm] = rows
    tiling.render_tile = single_render
    return results


def experiment_two_class_rules(out: Path, only=None) -> dict:
    """Rescore what `two-classes` left under `out`: does its verdict depend on the rule?

    Needs no card and no training.  Also how exactly the boxes sit at the default cut,
    which an F1 that only asks whether an event was found cannot say.
    """
    from audian_plugins.specdetector import models

    name = "2019-12-09"
    _paths, regions, chirps = read_labels(name)
    test_regions = [r for r in regions if r.t1 < TWO_CLASS_SPLIT_S]
    gold = [b for b in chirps if any(in_rect(b, r) for r in test_regions)]
    rises = [b for b in track_rises(name) if b.t1 < TWO_CLASS_SPLIT_S]
    rules = {
        "1.5 s, 5 Hz": (1.5, 5.0),
        "3 s, 5 Hz": (3.0, 5.0),
        "1.5 s, 3 Hz": (1.5, 3.0),
        "1.5 s, 8 Hz": (1.5, 8.0),
    }
    judges = {step: TrackJudge(name, step_hz=step) for _tol, step in rules.values()}
    results: dict = {}
    for directory in sorted(p for p in out.iterdir() if p.is_dir()):
        if only and directory.name not in only:
            continue
        seeds = sorted(directory.glob("seed*/found.json"))
        if not seeds:
            continue
        rows = []
        for path in seeds:
            found = [Box(*row) for row in json.loads(path.read_text())]
            found_chirps = [
                b
                for b in found
                if b.cls == 0 and any(in_rect(b, r) for r in test_regions)
            ]
            found_rises = [b for b in found if b.cls == 1]
            cut = models.TRAINED_CUT
            error, overlap = box_tightness(
                [b for b in found_chirps if b.score >= cut], gold, 0.1
            )
            row = dict(chirp_centre_ms=round(error, 1), chirp_iou=round(overlap, 3))
            for rule, (tol, step) in rules.items():
                row[rule] = round(
                    best_rise_scores(found_rises, rises, judges[step], tol)[2], 3
                )
            rows.append(row)
        results[directory.name] = rows
        means = {
            key: round(float(np.mean([r[key] for r in rows])), 3) for key in rows[0]
        }
        print(json.dumps({directory.name: means | dict(seeds=len(rows))}), flush=True)
    return results


def experiment_leave_out(out: Path, only=None) -> dict:
    """Train on three of the four recordings, and score on the fourth.

    What a model trained across recordings does on one it has never seen, on the same
    held-out hand labels and by the same rule as the old detector's benchmark.  That
    detector had far more to learn from: 17 recordings, 7416 hand boxes and simulations
    against about 250 boxes from three here.  So this asks how far a little variety goes,
    and is not a contest.
    """
    from audian_plugins.specdetector import models

    device = f"cuda:{DEVICE}" if DEVICE != "cpu" else "cpu"
    models.configure(WEIGHTS)
    data = {name: read_labels(name) for name in ROOTS}
    labelled = {
        name: [b for b in chirps if any(in_rect(b, r) for r in regions)]
        for name, (_paths, regions, chirps) in data.items()
    }
    with Recording(data["2019-12-09"][0]) as recording:
        rate = recording.rate
    everything = [b for boxes in labelled.values() for b in boxes]
    config = tiling.suggest_config(
        [(b.t0, b.t1, b.f0, b.f1) for b in everything], rate, channels=9
    )
    print(config, flush=True)
    results: dict = {}
    pooled_found, pooled_truth = [], []
    for held in ROOTS:
        if only and held not in only:
            continue
        sources = [
            Source(data[name][0], boxes=labelled[name], reviewed=list(data[name][1]))
            for name in ROOTS
            if name != held
        ]
        result = models.train_detector(
            sources, config, ["chirp"], out / held, device=device
        )
        model = models.Trained(result["model"], device)
        paths, regions, chirps = data[held]
        windows = [(r.t0, r.t1) for r in regions]
        with Recording(paths) as recording:
            found = models.sweep(model, recording, config, windows, conf=0.01)
        found = [b for b in found if any(in_rect(b, r) for r in regions)]
        truth = labelled[held]
        best, match = best_threshold(found, truth)
        at = match_events([b for b in found if b.score >= models.TRAINED_CUT], truth)
        results[held] = dict(
            trained_on=sum(len(labelled[n]) for n in ROOTS if n != held),
            chirps=len(truth),
            best_thr=best,
            best_f1=round(match.f1, 3),
            precision=round(at.precision, 3),
            recall=round(at.recall, 3),
            f1=round(at.f1, 3),
            train_tiles=result["train_tiles"],
            seconds=result["seconds"],
        )
        print(json.dumps({held: results[held]}), flush=True)
        offset = 1e6 * (len(pooled_truth) + 1)
        pooled_found += [b._replace(t0=b.t0 + offset, t1=b.t1 + offset) for b in found]
        pooled_truth += [b._replace(t0=b.t0 + offset, t1=b.t1 + offset) for b in truth]
        del model
        release_gpu()
    best, match = best_threshold(pooled_found, pooled_truth)
    at = match_events(
        [b for b in pooled_found if b.score >= models.TRAINED_CUT], pooled_truth
    )
    results["pooled"] = dict(
        best_thr=best,
        best_f1=round(match.f1, 3),
        precision=round(at.precision, 3),
        recall=round(at.recall, 3),
        f1=round(at.f1, 3),
        old_detector=OLD_DETECTOR,
    )
    print(json.dumps({"pooled": results["pooled"]}), flush=True)
    return results


def experiment_cpu(out: Path, only=None) -> dict:
    """Training and a sweep on four CPU threads, which is what a T470s has.

    These are the desktop's cores, so a laptop's will be slower.
    """
    import torch

    from audian_plugins.specdetector import models

    torch.set_num_threads(4)
    models.configure(WEIGHTS)
    syllables = cricket_boxes("syllable")
    with Recording([CRICKET]) as recording:
        config = tiling.suggest_config(
            [(b.t0, b.t1, b.f0, b.f1) for b in syllables],
            recording.rate,
            channels=recording.channels,
        )
    result = models.train_detector(
        [Source([CRICKET], boxes=syllables)],
        config,
        ["syllable"],
        out / "cpu",
        device="cpu",
        jitter_copies=16,
    )
    model = models.Trained(result["model"], "cpu")
    with Recording([CRICKET]) as recording:
        start = time.perf_counter()
        found = models.sweep(model, recording, config, None, batch=4)
        elapsed = time.perf_counter() - start
        duration = recording.duration
    scored = cricket_scores(found, cricket_truth(), syllables)
    report = dict(
        train_seconds=result["seconds"],
        epochs=result["epochs"],
        batch=4,
        tiles=result["train_tiles"],
        sweep_seconds=round(elapsed, 1),
        times_real_time=round(duration / elapsed, 1),
        f1=scored["f1"],
    )
    print(json.dumps(report), flush=True)
    return report


def experiment_session(out: Path, only=None) -> dict:
    """A whole four-file session of about an hour, as a Run reads it."""
    from audian_plugins.specdetector import models

    device = f"cuda:{DEVICE}" if DEVICE != "cpu" else "cpu"
    models.configure(WEIGHTS)
    root = Path("/home/weygoldt/wrk/analyses/fakefish/experiments/exp3")
    paths = sorted(os.fspath(p) for p in root.glob("DR0000_00*.wav"))
    volleys = []
    with open(root / "DR0000_0088-editable-labels.csv", newline="") as fp:
        for row in csv.DictReader(fp):
            if row["category"] == "volley" and row["f_low_hz"]:
                volleys.append(
                    Box(
                        float(row["t_start_s"]),
                        float(row["t_end_s"]),
                        float(row["f_low_hz"]),
                        float(row["f_high_hz"]),
                    )
                )
    with Recording(paths) as recording:
        duration = recording.duration
        config = tiling.suggest_config(
            [(b.t0, b.t1, b.f0, b.f1) for b in volleys],
            recording.rate,
            channels=recording.channels,
        )
        grid = tiling.grid_of(config, recording.rate)
        tiles = len(tiling.tile_starts(duration, grid))
    result = models.train_detector(
        [Source(paths, boxes=volleys)],
        config,
        ["volley"],
        out / "session",
        device=device,
        jitter_copies=16,
    )
    model = models.Trained(result["model"], device)
    start = time.perf_counter()
    with Recording(paths) as recording:
        found = models.sweep(model, recording, config, None)
    elapsed = time.perf_counter() - start
    report = dict(
        files=len(paths),
        channels=recording.channels,
        rate=recording.rate,
        duration_s=round(duration, 1),
        tile_s=round(grid.tile_s, 2),
        tiles=tiles,
        train_seconds=result["seconds"],
        sweep_seconds=round(elapsed, 1),
        times_real_time=round(duration / elapsed, 1),
        found=len(found),
        above_cut=sum(b.score >= models.TRAINED_CUT for b in found),
    )
    print(json.dumps(report), flush=True)
    return report


# ---------------------------------------------------------------- what labels are worth


def labelled_by(segmenter, how, shots, paths, view_s, band) -> list:
    """The shots as a reader using this gesture would have labelled them."""
    if how == "hand":
        return list(shots)
    out = []
    for box in shots:
        t0 = max(box.centre_t - view_s / 2, 0.0)
        segmenter.set_view(paths, t0, t0 + view_s, band[0], band[1])
        if how == "click":
            answer = segmenter.segment(
                points=[(box.centre_t, 0.5 * (box.f0 + box.f1), True)]
            )
        else:
            dt, df = 0.25 * (box.t1 - box.t0), 0.25 * (box.f1 - box.f0)
            answer = segmenter.segment(
                box=(box.t0 - dt, box.t1 + dt, box.f0 - df, box.f1 + df)
            )
        if answer is None:
            continue
        t0_, t1_, f0_, f1_ = answer["box"]
        out.append(Box(t0_, t1_, f0_, f1_))
    return out


def experiment_labels(out: Path, only=None) -> dict:
    """Whether SAM's labels train a detector as good as hand boxes do.

    The question that matters, since SAM's boxes are much tighter than a person's: a
    median IoU of 0.2 from a click says little if the detector it trains finds every
    event.  Also how far the detector's own boxes then are from the hand boxes.
    """
    from audian_plugins.specdetector import models

    device = f"cuda:{DEVICE}" if DEVICE != "cpu" else "cpu"
    models.configure(WEIGHTS)
    results = {}
    segmenter = models.Segmenter(models.SAM_WEIGHTS, device)

    paths, shots, regions, chirps = shots_and_targets()
    with Recording(paths) as recording:
        config = tiling.suggest_config(
            [(b.t0, b.t1, b.f0, b.f1) for b in shots],
            recording.rate,
            channels=recording.channels,
        )
    tests = [(regions[i].t0, regions[i].t1) for i in (0, 2)]

    def chirp_run(how):
        labels = labelled_by(segmenter, how, shots, paths, 3.0, (500.0, 1500.0))
        widths = [b.t1 - b.t0 for b in labels]
        result = models.train_detector(
            [Source(paths, boxes=labels)],
            config,
            ["chirp"],
            out / f"chirp-{how}",
            device=device,
            jitter_copies=16,
        )
        model = models.Trained(result["model"], device)
        with Recording(paths) as recording:
            found = models.sweep(model, recording, config, tests, conf=0.01)
        scored = score_found(found, regions, chirps)
        scored["median_label_ms"] = round(1e3 * float(np.median(widths)), 1)
        kept = [b for b in found if b.score >= scored["best_thr"]]
        scored["median_found_ms"] = round(
            1e3 * float(np.median([b.t1 - b.t0 for b in kept])) if kept else 0.0, 1
        )
        del model
        release_gpu()
        return scored

    truth = cricket_truth()
    syllables = cricket_boxes("syllable")
    with Recording([CRICKET]) as recording:
        cricket_config = tiling.suggest_config(
            [(b.t0, b.t1, b.f0, b.f1) for b in syllables],
            recording.rate,
            channels=recording.channels,
        )

    def cricket_run(how):
        labels = labelled_by(segmenter, how, syllables, [CRICKET], 1.0, (0.0, 24000.0))
        result = models.train_detector(
            [Source([CRICKET], boxes=labels)],
            cricket_config,
            ["syllable"],
            out / f"cricket-{how}",
            device=device,
            jitter_copies=16,
        )
        model = models.Trained(result["model"], device)
        with Recording([CRICKET]) as recording:
            found = models.sweep(model, recording, cricket_config, None, conf=0.01)
        scored = cricket_scores(found, truth, syllables)
        del model
        release_gpu()
        return scored

    for how in only or ("hand", "drag", "click"):
        results[f"chirp/{how}"] = chirp_run(how)
        print(json.dumps({f"chirp/{how}": results[f"chirp/{how}"]}), flush=True)
        results[f"cricket/{how}"] = cricket_run(how)
        print(json.dumps({f"cricket/{how}": results[f"cricket/{how}"]}), flush=True)
    segmenter.close()
    return results


def experiment_sam_cpu(out: Path, only=None) -> dict:
    """SAM on four CPU threads, which is what a T470s has.  An upper bound on nothing:
    this is a desktop's cores, so a laptop's will be slower."""
    import torch

    from audian_plugins.specdetector import models

    torch.set_num_threads(4)
    models.configure(WEIGHTS)
    results = {}
    paths, shots, regions, chirps = shots_and_targets()
    for weights in only or ["mobile_sam.pt", "sam2.1_t.pt"]:
        segmenter = models.Segmenter(weights, "cpu")
        encode, prompt = [], []
        for box in shots:
            t0 = max(box.centre_t - 1.5, 0.0)
            encode.append(segmenter.set_view(paths, t0, t0 + 3.0, 500.0, 1500.0))
            for _ in range(3):
                start = time.perf_counter()
                segmenter.segment(
                    points=[(box.centre_t, 0.5 * (box.f0 + box.f1), True)]
                )
                prompt.append(1e3 * (time.perf_counter() - start))
        results[weights] = dict(
            encode_ms=round(float(np.median(encode)), 1),
            prompt_ms=round(float(np.median(prompt)), 1),
        )
        print(json.dumps({weights: results[weights]}), flush=True)
        segmenter.close()
    return results


# ---------------------------------------------------------------- the old one's best case


def cricket_truth() -> list:
    """Every syllable of the cricket, from its envelope, in a clean recording.

    There is no exhaustive hand label of this recording.  But it is clean enough that
    the envelope finds the syllables outright, and this checks itself against the
    three that were labelled by hand before anything is scored against it.
    """
    import soundfile as sf
    from scipy.signal import butter, sosfiltfilt

    data, rate = sf.read(CRICKET, always_2d=True)
    data = data.mean(axis=1)
    sos = butter(4, [3000.0, 16000.0], btype="band", fs=rate, output="sos")
    envelope = np.abs(sosfiltfilt(sos, data))
    kernel = int(0.004 * rate)
    envelope = np.convolve(envelope, np.ones(kernel) / kernel, mode="same")
    floor = float(np.median(envelope))
    above = envelope > floor + 0.08 * (np.percentile(envelope, 99.9) - floor)
    edges = np.flatnonzero(np.diff(above.astype(int)))
    starts, stops = edges[::2], edges[1::2]
    spans: list = []
    for a, b in zip(starts / rate, stops / rate):
        if spans and a - spans[-1][1] < 0.04:
            spans[-1][1] = b
        else:
            spans.append([a, b])
    spans = [s for s in spans if s[1] - s[0] > 0.04]
    truth = [Box(a, b, 3800.0, 15300.0) for a, b in spans]
    for hand in cricket_boxes("syllable"):
        best = max(
            box_iou((t.t0, t.t1, t.f0, t.f1), (hand.t0, hand.t1, hand.f0, hand.f1))
            for t in truth
        )
        if best < 0.5:
            raise ValueError(
                f"the envelope truth misses a hand label at {hand.t0}: {best}"
            )
    return truth


def cricket_scores(found, truth, shots) -> dict:
    """Scores outside the shots themselves, by the same rule, at a tighter tolerance."""
    used = [b.t0 for b in shots]
    rest = [t for t in truth if not any(abs(t.t0 - u) < 0.05 for u in used)]
    kept = [b for b in found if not any(abs(b.t0 - u) < 0.05 for u in used)]
    threshold, match = best_threshold(kept, rest, time_tol_s=0.05)
    loose = match_events(kept, rest, time_tol_s=0.05)
    return dict(
        truth=len(rest),
        best_thr=threshold,
        precision=round(match.precision, 3),
        recall=round(match.recall, 3),
        f1=round(match.f1, 3),
        max_recall=round(loose.recall, 3),
        boxes=len(kept),
    )


def experiment_cricket(out: Path, only=None) -> dict:
    """The template matcher's best case: a clean recording, three hand labels."""
    from audian_plugins.specdetector import models

    device = f"cuda:{DEVICE}" if DEVICE != "cpu" else "cpu"
    models.configure(WEIGHTS)
    truth = cricket_truth()
    shots = cricket_boxes("syllable")
    print("syllables by envelope:", len(truth), flush=True)
    results = {}
    with Recording([CRICKET]) as recording:
        duration = recording.duration
        config = tiling.suggest_config(
            [(b.t0, b.t1, b.f0, b.f1) for b in shots],
            recording.rate,
            channels=recording.channels,
        )
        print(config, flush=True)

        if not only or "ncc" in only:
            engine = old_matcher()
            settings = engine.Settings(
                domain=engine.SPECTROGRAM, representation="pcen", k=engine.MIN_K
            ).normalized()
            block = recording.read(0, recording.frames).astype(np.float64)
            examples = [engine.Example(b.t0, b.t1, b.f0, b.f1) for b in shots]
            templates = engine.learn(block, recording.rate, examples, settings)
            hits = engine.detect(block, recording.rate, templates, settings)
            found = [Box(h.t0, h.t1, 3800.0, 15300.0, 0, float(h.score)) for h in hits]
            results["ncc"] = cricket_scores(found, truth, shots)
            print(json.dumps({"ncc": results["ncc"]}), flush=True)

        if not only or "yoloe" in only:
            prompted = models.Prompted(device=device)
            prompted.prompt(recording, config, shots)
            found = models.sweep(prompted, recording, config, None, conf=0.01)
            results["yoloe"] = cricket_scores(found, truth, shots)
            results["yoloe"]["auto_cut"] = round(auto_cut([b.score for b in found]), 3)
            print(json.dumps({"yoloe": results["yoloe"]}), flush=True)
            del prompted
            release_gpu()

        if not only or "finetune" in only:
            result = models.train_detector(
                [Source([CRICKET], boxes=list(shots))],
                config,
                ["syllable"],
                out / "finetune",
                device=device,
                jitter_copies=16,
            )
            model = models.Trained(result["model"], device)
            found = models.sweep(model, recording, config, None, conf=0.01)
            scored = cricket_scores(found, truth, shots)
            scored["seconds"] = result["seconds"]
            results["finetune"] = scored
            print(json.dumps({"finetune": scored}), flush=True)
            del model
            release_gpu()
    results["duration_s"] = duration
    return results


def release_gpu() -> None:
    """Hand back what the last model held: the card is shared with other work."""
    import gc

    import torch

    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def main() -> None:
    global DEVICE, WEIGHTS
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "experiment",
        choices=[
            "configs",
            "fewshot",
            "loop",
            "augment",
            "sam",
            "sam-cpu",
            "labels",
            "cricket",
            "suggest",
            "multires",
            "session",
            "cpu",
            "two-classes",
            "two-class-rules",
            "leave-out",
        ],
    )
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--weights", type=Path, required=True)
    parser.add_argument("--device", default="0")
    parser.add_argument("--only", nargs="*")
    parser.add_argument("--config", default="4s-100ms", choices=sorted(CONFIGS))
    parser.add_argument(
        "--seeds", nargs="*", type=int, default=[0, 1, 2], help="for two-classes"
    )
    args = parser.parse_args()
    DEVICE = args.device
    WEIGHTS = args.weights
    args.out.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("YOLO_CONFIG_DIR", os.fspath(args.out))
    os.environ.setdefault("YOLO_AUTOINSTALL", "false")
    if args.experiment == "configs":
        results = experiment_configs(args.out, args.only)
    elif args.experiment == "fewshot":
        results = experiment_fewshot(args.out, args.config, args.only)
    elif args.experiment == "loop":
        results = experiment_loop(args.out, args.only)
    elif args.experiment == "augment":
        results = experiment_augment(args.out, args.only)
    elif args.experiment == "sam":
        results = experiment_sam(args.out, args.only)
    elif args.experiment == "sam-cpu":
        results = experiment_sam_cpu(args.out, args.only)
    elif args.experiment == "labels":
        results = experiment_labels(args.out, args.only)
    elif args.experiment == "suggest":
        results = experiment_suggest(args.out, args.only)
    elif args.experiment == "multires":
        results = experiment_multires(args.out, args.only)
    elif args.experiment == "session":
        results = experiment_session(args.out, args.only)
    elif args.experiment == "leave-out":
        results = experiment_leave_out(args.out, args.only)
    elif args.experiment == "two-classes":
        results = experiment_two_classes(args.out, args.only, args.seeds)
    elif args.experiment == "two-class-rules":
        results = experiment_two_class_rules(args.out, args.only)
    elif args.experiment == "cpu":
        results = experiment_cpu(args.out, args.only)
    elif args.experiment == "cricket":
        results = experiment_cricket(args.out, args.only)
    (args.out / f"{args.experiment}.json").write_text(json.dumps(results, indent=1))
    print("old detector, pooled over all regions:", json.dumps(OLD_DETECTOR))


DEVICE = "0"
WEIGHTS = Path(".")

if __name__ == "__main__":
    main()
