"""Running the models for audian, in a child process of audian's interpreter.

This file is executed, never imported by the panel::

    <sys.executable> -u modelrunner.py --weights DIR [--oneshot] [--idle SECONDS]

Out of process for three reasons.  A training run holds the GIL for minutes.  torch
and OpenCV take seconds and a gigabyte to import, on behalf of a plugin the reader
may not use.  And `opencv-python` carries a Qt of its own, which has no business in
a process that runs PySide6.  So nothing here imports audian or Qt, and one test
checks that it stays that way.

The protocol is wavetracker's runner's: one JSON object per line, requests on stdin,
replies on stdout.  Stdout is the protocol and nothing else; Ultralytics prints a
great deal, so fd 1 is moved aside and everything else goes to stderr.  Every
exception in a job becomes one ``error`` line and the runner keeps serving.

Ops: ``fetch``, ``segment``, ``propose``, ``train``, ``detect``, ``shutdown``.

Cancelling is killing: the client terminates the process, and the next job starts
another.  A ``detect`` writes nothing until its sweep has finished, and then to a
``.partial`` file first, so a kill leaves no result behind that could be mistaken for
a whole one.  It also leaves nothing to resume from.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import select
import sys
import threading
import time
import traceback
from pathlib import Path

PROTOCOL = 1
IDLE_S = 900.0

#: The private handle to the real stdout, set up by `_reserve_stdout`.
_proto = None
_send_lock = threading.Lock()


def _reserve_stdout():
    """Keep fd 1 for the protocol; point everything else at stderr."""
    global _proto
    _proto = os.fdopen(os.dup(1), "w", buffering=1, encoding="utf-8")
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    return _proto


def send(msg: dict) -> None:
    """Write one protocol message."""
    line = json.dumps(msg, default=_json_default) + "\n"
    with _send_lock:
        _proto.write(line)
        _proto.flush()


def _json_default(obj):
    try:
        import numpy as np

        if isinstance(obj, np.generic):
            return obj.item()
        if isinstance(obj, np.ndarray):
            return obj.tolist()
    except ImportError:  # pragma: no cover
        pass
    if isinstance(obj, (set, tuple)):
        return list(obj)
    return str(obj)


class RequestError(ValueError):
    """A malformed request: reported as ``bad_request``."""


def _require(req: dict, *names):
    missing = [n for n in names if n not in req]
    if missing:
        raise RequestError(f"{req.get('op')}: missing field(s) {missing}")
    return [req[n] for n in names]


# ---------------------------------------------------------------- hello


def hello(weights_dir) -> dict:
    """What this interpreter can do.  Cheap: nothing heavy is imported to answer."""
    import importlib.util

    versions = {}
    for name in ("ultralytics", "torch", "cv2"):
        versions[name] = importlib.util.find_spec(name) is not None
    return {
        "type": "hello",
        "protocol": PROTOCOL,
        "python": sys.version.split()[0],
        "packages": versions,
        "weights_dir": os.fspath(weights_dir),
    }


# ---------------------------------------------------------------- state


class Runner:
    """The models kept between jobs, so a second click does not reload SAM."""

    def __init__(self, weights_dir):
        self.weights_dir = Path(weights_dir)
        self.segmenter = None
        self.segmenter_weights = None
        #: set once the GPU has refused SAM, for the life of this process
        self.force_device = None

    def reporter(self, job: str):
        def report(stage, done, total, text=""):
            send(
                {
                    "type": "progress",
                    "id": job,
                    "stage": stage,
                    "done": int(done),
                    "total": int(total),
                    "text": str(text),
                }
            )

        return report

    def segmenter_for(self, weights, device):
        from audian_plugins.specdetector import models

        name = weights or models.SAM_WEIGHTS
        if self.segmenter is None or self.segmenter_weights != (name, device):
            self.segmenter = models.Segmenter(name, device)
            self.segmenter_weights = (name, device)
        return self.segmenter

    # -- ops -----------------------------------------------------------

    def op_fetch(self, req, job):
        from audian_plugins.specdetector import models

        models.configure(self.weights_dir, offline=False)
        names = req.get("names") or list(models.ALL_WEIGHTS)
        try:
            return {"paths": models.fetch(names, self.weights_dir, self.reporter(job))}
        finally:
            # This process goes on to serve other jobs, which stay off the network.
            models.configure(self.weights_dir, offline=True)

    def op_segment(self, req, job):
        try:
            return self._segment(req, req.get("device", "auto"))
        except Exception as exc:  # noqa: BLE001 - only out of memory is handled
            if not _out_of_memory(exc):
                raise
        # The card is full: a model training in the panel's other runner, or anything
        # else on this machine.  Seen for real with 10 MiB of 15.6 GiB free, while
        # another job held the card.  SAM 2.1 tiny on four CPU threads encodes a view in
        # 821 ms and answers a click in 46 ms, so labelling can carry on.
        self.segmenter = None
        self.segmenter_weights = None
        _release_memory()
        self.force_device = "cpu"
        return self._segment(req, "cpu")

    def _segment(self, req, device):
        paths, view = _require(req, "paths", "view")
        segmenter = self.segmenter_for(req.get("weights"), self.force_device or device)
        started = time.perf_counter()
        encode_ms = segmenter.set_view(
            paths,
            float(view["t0"]),
            float(view["t1"]),
            float(view["f0"]),
            float(view["f1"]),
            req.get("channels"),
            req.get("reduction", "mean"),
        )
        points = [(p[0], p[1], bool(p[2])) for p in req.get("points") or []]
        box = req.get("box")
        if not points and box is None:
            # Nothing to prompt with: this request only encodes the view, ahead
            # of the click that is coming.
            return {"encode_ms": round(encode_ms, 1), "mask": None}
        mask = segmenter.segment(points=points or None, box=box)
        return {
            "encode_ms": round(encode_ms, 1),
            "total_ms": round(1e3 * (time.perf_counter() - started), 1),
            "mask": mask,
        }

    def _inference(self, handler, req, job):
        """Run on the GPU, and on the CPU if that is full.  The result says which.

        A sweep on the CPU is slower by a large factor, and a reader should know why
        the bar is crawling, but it finishes.  Training is not retried this way: an
        hour on a CPU is a decision for the reader.
        """
        try:
            return handler(req, job, req.get("device", "auto"))
        except Exception as exc:  # noqa: BLE001 - only out of memory is handled
            if not _out_of_memory(exc):
                raise
        _release_memory()
        return {**handler(req, job, "cpu"), "fell_back_to_cpu": True}

    def op_propose(self, req, job):
        return self._inference(self._propose, req, job)

    def op_detect(self, req, job):
        return self._inference(self._detect, req, job)

    def _propose(self, req, job, device):
        """Candidates over `windows`: from a trained model, or from the examples."""
        from audian_plugins.specdetector import models, tiling
        from audian_plugins.specdetector.boxes import Box
        from audian_plugins.specdetector.recording import Recording

        paths = _require(req, "paths")[0]
        report = self.reporter(job)
        windows = req.get("windows")
        with Recording(paths) as recording:
            if req.get("model"):
                model = models.Trained(req["model"], device)
                config = model.config
                used = 0
                source = "model"
            else:
                config = tiling.SpecConfig.from_dict(_require(req, "config")[0])
                shots = [Box(**s) for s in _require(req, "shots")[0]]
                model = models.Prompted(
                    req.get("weights") or models.PROPOSER_WEIGHTS, device
                )
                used = model.prompt(recording, config, shots, report)
                source = "examples"
            found = models.sweep(
                model,
                recording,
                config,
                windows,
                conf=float(req.get("conf", models.SWEEP_CONFIDENCE)),
                batch=int(req.get("batch", 16)),
                progress=report,
            )
        return {
            "source": source,
            "used": used,
            "boxes": [_box_json(b) for b in found],
        }

    def op_train(self, req, job):
        from audian_plugins.specdetector import dataset, models, tiling
        from audian_plugins.specdetector.boxes import Box

        config, names, directory = _require(req, "config", "names", "directory")
        sources = [_source_of(s, Box, dataset) for s in _require(req, "sources")[0]]

        def epoch_ended(epoch, epochs, values):
            send(
                {
                    "type": "metrics",
                    "id": job,
                    "epoch": int(epoch),
                    "epochs": int(epochs),
                    "values": values,
                }
            )

        return models.train_detector(
            sources,
            tiling.SpecConfig.from_dict(config),
            names,
            directory,
            segment=bool(req.get("segment")),
            epochs=req.get("epochs"),
            batch=req.get("batch"),
            base=req.get("base"),
            patience=req.get("patience"),
            hold_out_every=int(req.get("hold_out_every", dataset.VALIDATION_EVERY)),
            device=req.get("device", "auto"),
            progress=self.reporter(job),
            on_epoch_end=epoch_ended,
        )

    def _detect(self, req, job, device):
        """A whole recording, to a CSV of audian's own label format."""
        from audian_plugins.specdetector import models
        from audian_plugins.specdetector.recording import Recording

        paths, weights, out = _require(req, "paths", "model", "out")
        report = self.reporter(job)
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        partial = out.with_name(out.name + f".partial-{os.getpid()}")
        model = models.Trained(weights, device)
        names = model.names
        started = time.perf_counter()
        with Recording(paths) as recording:
            found = models.sweep(
                model,
                recording,
                model.config,
                req.get("windows"),
                conf=float(req.get("conf", models.SWEEP_CONFIDENCE)),
                batch=int(req.get("batch", 16)),
                progress=report,
            )
            duration = recording.duration
        elapsed = time.perf_counter() - started
        found = sorted(found, key=lambda b: b.t0)
        try:
            write_detections(partial, found, names, req.get("suffix", " (found)"))
            os.replace(partial, out)
        finally:
            if partial.exists():
                partial.unlink()
        return {
            "out": os.fspath(out),
            "count": len(found),
            "seconds": round(elapsed, 2),
            "recording_seconds": round(duration, 2),
            "boxes": [_box_json(b) for b in found]
            if req.get("return_boxes", True)
            else [],
        }


def write_detections(path, boxes, names, suffix: str) -> None:
    """Boxes in the columns of audian's editable-label sidecar.

    Written by hand here rather than through `audian.labels`, which this process may
    not import; ``tests/test_specdetector_runner.py`` reads one back through it.
    """
    with open(path, "w", newline="", encoding="utf-8") as fp:
        writer = csv.writer(fp)
        writer.writerow(
            [
                "category",
                "kind",
                "channel",
                "t_start_s",
                "t_end_s",
                "f_low_hz",
                "f_high_hz",
                "note",
                "outline",
            ]
        )
        for box in boxes:
            writer.writerow(
                [
                    f"{names[box.cls]}{suffix}",
                    "span",
                    "" if box.channel is None else int(box.channel),
                    f"{box.t0:.6f}",
                    f"{box.t1:.6f}",
                    f"{box.f0:.3f}",
                    f"{box.f1:.3f}",
                    f"score={box.score:.4f}",
                    outline_cell(box.polygon),
                ]
            )


def outline_cell(polygon) -> str:
    """An outline as the sidecar writes one: ``t f`` pairs joined by ``;``."""
    if not polygon:
        return ""
    return ";".join(f"{float(t):.6f} {float(f):.3f}" for t, f in polygon)


def _box_json(box) -> dict:
    return {
        "t0": round(float(box.t0), 6),
        "t1": round(float(box.t1), 6),
        "f0": round(float(box.f0), 3),
        "f1": round(float(box.f1), 3),
        "cls": int(box.cls),
        "score": round(float(box.score), 4),
        "polygon": None if box.polygon is None else [list(p) for p in box.polygon],
    }


def _source_of(values: dict, Box, dataset):
    def boxes(key):
        out = []
        for row in values.get(key) or []:
            polygon = row.get("polygon")
            out.append(
                Box(
                    float(row["t0"]),
                    float(row["t1"]),
                    float(row["f0"]),
                    float(row["f1"]),
                    int(row.get("cls", 0)),
                    float(row.get("score", 1.0)),
                    polygon=None if not polygon else tuple(map(tuple, polygon)),
                )
            )
        return out

    return dataset.Source(
        paths=list(values["paths"]),
        boxes=boxes("boxes"),
        reviewed=[dataset.rect_from_list(r) for r in values.get("reviewed") or []],
        rejected=boxes("rejected"),
        hidden=[dataset.rect_from_list(r) for r in values.get("hidden") or []],
    )


# ---------------------------------------------------------------- the loop


def _serve_one(runner: Runner, req: dict) -> None:
    job = req.get("id")
    op = req.get("op")
    handler = getattr(runner, f"op_{op}", None)
    if handler is None:
        send(
            {
                "type": "error",
                "id": job,
                "kind": "bad_request",
                "message": f"unknown op {op!r}",
            }
        )
        return
    try:
        result = handler(req, job)
    except RequestError as exc:
        send({"type": "error", "id": job, "kind": "bad_request", "message": str(exc)})
    except MemoryError as exc:
        send({"type": "error", "id": job, "kind": "oom", "message": str(exc)})
    except Exception as exc:  # noqa: BLE001 - a job's failure is one error line
        name = type(exc).__name__
        kind = "input" if name in {"ValueError", "MissingWeights"} else "exception"
        if _out_of_memory(exc):
            kind = "oom"
        send(
            {
                "type": "error",
                "id": job,
                "kind": kind,
                "message": f"{exc}" or name,
                "traceback": traceback.format_exc(),
            }
        )
    else:
        send({"type": "result", "id": job, **(result or {})})
    finally:
        if op != "segment":
            _release_memory()


def _out_of_memory(exc: BaseException) -> bool:
    """Whether the GPU refused.

    Two shapes, and matching only the first misses real failures: an allocation raises
    `OutOfMemoryError`, but a process that cannot even create its CUDA context on a
    full card gets a plain `RuntimeError` reading "CUDA error: out of memory".
    """
    if type(exc).__name__ == "OutOfMemoryError":
        return True
    return isinstance(exc, RuntimeError) and "out of memory" in str(exc).lower()


def _release_memory() -> None:
    """Give back what a finished job holds.

    torch's allocator keeps what it has reserved until asked, and the card is shared:
    with other work on the same machine, and with SAM in the panel's other runner.  An
    idle runner has no claim on it.  SAM is exempt, because keeping its encoding
    between clicks is the point of it.
    """
    import gc

    gc.collect()
    if "torch" not in sys.modules:
        return
    torch = sys.modules["torch"]
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def _wait_for_request(idle: float) -> bool:
    """Whether a line is waiting on stdin before `idle` seconds pass."""
    try:
        ready, _, _ = select.select([sys.stdin], [], [], idle)
    except (OSError, ValueError):  # not selectable: block on the read instead
        return True
    return bool(ready)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--weights", required=True)
    parser.add_argument("--oneshot", action="store_true")
    parser.add_argument("--idle", type=float, default=IDLE_S)
    args = parser.parse_args(argv)
    _reserve_stdout()
    # This script is run by path, so its own directory is on sys.path and its package
    # is not.  `src/` is two levels up; audian_plugins imports no Qt.  The directory
    # itself comes off, or `import tools` anywhere below finds the plugin's Qt module.
    own = os.fspath(Path(__file__).resolve().parent)
    sys.path[:] = [p for p in sys.path if os.path.abspath(p or ".") != own]
    sys.path.insert(0, os.fspath(Path(__file__).resolve().parents[2]))
    weights_dir = Path(args.weights)
    weights_dir.mkdir(parents=True, exist_ok=True)
    send(hello(weights_dir))
    try:
        from audian_plugins.specdetector import models

        models.configure(weights_dir, offline=True)
    except Exception as exc:  # noqa: BLE001 - a broken install is one error line
        send(
            {
                "type": "error",
                "id": None,
                "kind": "startup",
                "message": f"cannot load ultralytics: {type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(),
            }
        )
        return 1
    runner = Runner(weights_dir)
    while True:
        if not _wait_for_request(args.idle):
            return 0
        line = sys.stdin.readline()
        if not line:
            return 0
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError as exc:
            send(
                {
                    "type": "error",
                    "id": None,
                    "kind": "bad_request",
                    "message": f"not JSON: {exc}",
                }
            )
            continue
        if req.get("op") == "shutdown":
            return 0
        _serve_one(runner, req)
        if args.oneshot:
            return 0


if __name__ == "__main__":
    sys.exit(main())
