"""Run wavetracker jobs for audian, in a child process of audian's interpreter.

This file is executed, never imported by the plugin::

    <sys.executable> -u wtrunner.py [--oneshot] [--idle SECONDS]

wavetracker is a dependency of audian, so the interpreter running audian has
it; this runs out of process only because wavetracker holds the GIL.

It imports the standard library, numpy and wavetracker only -- not audian,
not Qt -- and runs on Python >= 3.11.  The protocol is described in
docs/eodsorter-design.md, section 4.4: one JSON object per line, requests on
stdin, replies on stdout.  Stdout is the protocol channel and nothing else;
everything else that would be printed (by wavetracker, numba, cleanup) goes
to stderr.

Ops: ``detect``, ``peaks``, ``cleanup``, ``shutdown``.  Every exception in a
job becomes one ``error`` line and the runner keeps serving.  The process
exits 0 after ``shutdown``, at the end of stdin, after ``--idle`` seconds
without a request (default 600), or, with ``--oneshot``, after one job.
"""

from __future__ import annotations

import json
import os
import sys
import threading

PROTOCOL = 1
IDLE_S = 600.0

#: the private handle to the real stdout, set up by `_reserve_stdout`
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


class InputError(ValueError):
    """The input cannot be analysed as asked: reported as ``input``."""


def deep_merge(base: dict, over: dict) -> dict:
    """`over` merged into a copy of `base`, recursively for dicts."""
    out = dict(base)
    for key, value in (over or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _require(req: dict, *names):
    missing = [n for n in names if n not in req]
    if missing:
        raise RequestError(f"{req.get('op')}: missing field(s) {missing}")
    return [req[n] for n in names]


def _as_input(value):
    """A path, or a list of paths; a one-element list is that path."""
    if isinstance(value, (list, tuple)):
        if not value:
            raise RequestError("empty input list")
        return str(value[0]) if len(value) == 1 else [str(v) for v in value]
    if not isinstance(value, str):
        raise RequestError(f"input must be a path or a list of paths: {value!r}")
    return value


def _check_input_exists(inp):
    paths = inp if isinstance(inp, list) else [inp]
    missing = [p for p in paths if not os.path.exists(p)]
    if missing:
        raise InputError(f"recording not found: {', '.join(missing)}")
    if isinstance(inp, list):
        import wavetracker.io as wio

        if not getattr(wio, "MULTI_INPUT", False):
            raise InputError(
                "this wavetracker cannot read a recording split over several "
                "files; update it (list input, docs/eodsorter-design.md 4.6)"
            )


# ---------------------------------------------------------------- hello


def _capabilities() -> list[str]:
    import importlib.util

    caps = ["detect", "peaks"]
    if importlib.util.find_spec("wavetracker.postprocessing.cleanup") is not None:
        caps.append("cleanup")
    import wavetracker.io as wio

    if getattr(wio, "MULTI_INPUT", False):
        caps.append("multi_input")
    return caps


def hello(devices=None) -> dict:
    import platform

    import wavetracker
    from wavetracker.config import Config

    return {
        "type": "hello",
        "protocol": PROTOCOL,
        "python": platform.python_version(),
        "executable": sys.executable,
        "wavetracker": getattr(wavetracker, "__version__", "unknown"),
        "capabilities": _capabilities(),
        "devices": devices or ["auto", "cpu"],
        "default_config": Config().to_dict(),
    }


def probe_devices() -> list[str]:
    import torch

    devices = ["auto", "cpu"]
    if torch.cuda.is_available():
        devices.append("cuda")
        if torch.cuda.device_count() > 1:
            devices += [f"cuda:{i}" for i in range(torch.cuda.device_count())]
    mps = getattr(torch.backends, "mps", None)
    if mps is not None and mps.is_available():
        devices.append("mps")
    return devices


# ---------------------------------------------------------------- ops


class Runner:
    def __init__(self):
        self.devices_probed = False

    # -- detect --------------------------------------------------------
    def op_detect(self, req: dict) -> dict:
        import shutil
        import time

        job = req.get("id")
        (inp, output_dir) = _require(req, "input", "output_dir")
        inp = _as_input(inp)
        final_dir = req.get("final_dir")
        config_path = req.get("config_path")
        overrides = req.get("config") or {}
        start = float(req.get("start") or 0.0)
        duration = req.get("duration")
        duration = None if duration is None else float(duration)
        device = req.get("device") or "auto"
        do_track = bool(req.get("track", True))
        if not isinstance(overrides, dict):
            raise RequestError("config must be an object")

        from wavetracker.config import Config

        try:
            cfg = Config.load(config_path)
            cfg = Config.from_dict(deep_merge(cfg.to_dict(), overrides))
        except FileNotFoundError as e:
            raise RequestError(f"config file not found: {e}") from e
        except (TypeError, ValueError) as e:
            raise RequestError(f"bad config: {e}") from e

        _check_input_exists(inp)
        if final_dir is not None and os.path.exists(final_dir):
            raise InputError(f"output directory already exists: {final_dir}")

        t_all = time.perf_counter()
        send({"type": "progress", "id": job, "stage": "start", "done": 0,
              "total": -1, "text": "loading wavetracker"})  # fmt: skip
        from wavetracker.io import recording_info
        from wavetracker.pipeline import detect, track_results
        from wavetracker.spectrogram import step_size

        if not self.devices_probed:
            self.devices_probed = True
            try:
                send(hello(probe_devices()))
            except Exception:  # never fail a job over the device list
                pass

        try:
            info = recording_info(inp)
        except FileNotFoundError as e:
            raise InputError(f"recording not found: {e}") from e
        except OSError as e:  # audioio found no module that reads the format
            raise InputError(
                f"cannot read the recording: {e}  (installing soundfile in the "
                "wavetracker environment adds formats)"
            ) from e
        sc = cfg.spectrogram
        rate = float(info.rate)
        s0 = min(info.frames, max(0, round(start * rate)))
        s1 = (
            info.frames
            if duration is None
            else min(info.frames, s0 + round(duration * rate))
        )
        step = step_size(sc.nfft, sc.overlap_frac)
        if s1 - s0 < sc.nfft + step:
            raise InputError(
                "Selected data is shorter than two FFT windows "
                f"({(sc.nfft + step) / rate:.2f} s needed, "
                f"{(s1 - s0) / rate:.2f} s given)."
            )

        created = not os.path.exists(output_dir)
        t_det = time.perf_counter()

        def progress(done: int, total: int) -> None:
            elapsed = max(time.perf_counter() - t_det, 1e-9)
            speed = done * step / rate / elapsed
            send({"type": "progress", "id": job, "stage": "detect",
                  "done": int(done), "total": int(total),
                  "text": f"{speed:.0f}x realtime"})  # fmt: skip

        try:
            send({"type": "progress", "id": job, "stage": "detect", "done": 0,
                  "total": -1, "text": "computing spectrogram"})  # fmt: skip
            out = detect(inp, output_dir, cfg, start, duration, device, progress)
            results = out.results
            tracking = 0.0
            if do_track:
                send({"type": "progress", "id": job, "stage": "track", "done": 0,
                      "total": -1, "text": "tracking identities"})  # fmt: skip
                tracking = track_results(results, cfg)
                results.save(output_dir)
            if final_dir is not None:
                send({"type": "progress", "id": job, "stage": "save", "done": 0,
                      "total": -1, "text": "saving"})  # fmt: skip
                os.replace(output_dir, final_dir)
        except BaseException:
            # a failed or interrupted run never leaves a half directory
            if created and final_dir is not None:
                shutil.rmtree(output_dir, ignore_errors=True)
            raise
        timings = dict(vars(out.timings))
        timings["tracking"] = tracking
        timings["total"] = time.perf_counter() - t_all
        return {
            "type": "result",
            "id": job,
            "output_dir": final_dir if final_dir is not None else output_dir,
            "n_detections": int(len(results.fund_v)),
            "n_ids": int(results.n_ids),
            "n_frames": int(len(results.times)),
            "timings": timings,
        }

    # -- peaks ---------------------------------------------------------
    def op_peaks(self, req: dict) -> dict:
        import numpy as np

        job = req.get("id")
        inp, nfft, step, s0, frames, fmin, fmax, out = _require(
            req, "input", "nfft", "step", "s0", "frames", "fmin", "fmax", "out"
        )
        inp = _as_input(inp)
        nfft, step, s0 = int(nfft), int(step), int(s0)
        frames = np.asarray(frames, dtype=np.int64).ravel()
        fmin = np.asarray(fmin, dtype=float).ravel()
        fmax = np.asarray(fmax, dtype=float).ravel()
        if not (len(frames) == len(fmin) == len(fmax)):
            raise RequestError("frames, fmin and fmax differ in length")
        device = req.get("device") or "auto"
        _check_input_exists(inp)

        import torch
        from wavetracker.io import open_recording
        from wavetracker.spectrogram import PowerSpectrogram, frequencies, get_device

        dev = get_device(device)
        with open_recording(inp, buffersize=10.0) as data:
            rate = float(data.rate)
            n_total = len(data)
            channels = req.get("channels")
            if channels is None:
                channels = list(range(data.channels))
            channels = np.asarray(channels, dtype=np.int64)
            if (
                len(channels) == 0
                or channels.min() < 0
                or channels.max() >= data.channels
            ):
                raise InputError(f"channels {channels.tolist()} out of range")
            nc = len(channels)
            freqs = frequencies(nfft, rate)
            df = freqs[1] - freqs[0]
            spec = PowerSpectrogram(nfft, step, rate, dev)
            n = len(frames)
            fund = np.full(n, np.nan)
            sign = np.full((n, nc), np.nan, np.float32)
            cplx = np.full((n, nc), np.nan + 1j * np.nan, np.complex64)
            chunk = 32
            for c0 in range(0, n, chunk):
                sel = np.arange(c0, min(n, c0 + chunk))
                blocks, ok = [], []
                for i in sel:
                    a = s0 + int(frames[i]) * step
                    if a < 0 or a + nfft > n_total:
                        continue
                    blocks.append(np.asarray(data[a : a + nfft])[:, channels].T)
                    ok.append(i)
                if not ok:
                    continue
                x = np.ascontiguousarray(np.stack(blocks), dtype=np.float32)
                x = x.reshape(len(ok) * nc, nfft)
                st = spec.stft(torch.from_numpy(x).to(dev))[..., 0]  # (k*c, freqs)
                pw = PowerSpectrogram.power(st)
                st = st.reshape(len(ok), nc, -1).cpu().numpy()
                pw = pw.reshape(len(ok), nc, -1).cpu().numpy()
                total = pw.sum(1)  # (k, freqs)
                for j, i in enumerate(ok):
                    b0 = int(np.searchsorted(freqs, fmin[i], side="left"))
                    b1 = int(np.searchsorted(freqs, fmax[i], side="right"))
                    if b1 <= b0:
                        # a band narrower than a bin (zoomed far in): the
                        # bin nearest its centre
                        c = 0.5 * (fmin[i] + fmax[i])
                        if not np.isfinite(c):
                            continue
                        b0 = int(
                            np.clip(np.rint((c - freqs[0]) / df), 0, len(freqs) - 1)
                        )
                        b1 = b0 + 1
                    b = b0 + int(np.argmax(total[j, b0:b1]))
                    f = freqs[b]
                    if 0 < b < len(freqs) - 1:
                        y = 10.0 * np.log10(np.maximum(total[j, b - 1 : b + 2], 1e-30))
                        den = y[0] - 2.0 * y[1] + y[2]
                        if den < 0:
                            f += 0.5 * (y[0] - y[2]) / den * df
                    fund[i] = f
                    sign[i] = pw[j, :, b]
                    cplx[i] = st[j, :, b]
        tmp = out + ".tmp.npz"
        np.savez(tmp, frames=frames, fund=fund, sign=sign, cplx=cplx)
        os.replace(tmp, out)
        return {
            "type": "result",
            "id": job,
            "out": out,
            "n": int(n),
            "n_found": int(np.count_nonzero(~np.isnan(fund))),
        }

    # -- cleanup -------------------------------------------------------
    def op_cleanup(self, req: dict) -> dict:
        import numpy as np

        job = req.get("id")
        (folder,) = _require(req, "dir")
        n_fish = req.get("n_fish")
        params = req.get("params") or {}
        allowed = {
            "stride_minutes",
            "overlap_frac",
            "freq_tolerance",
            "time_tolerance_minutes",
            "density_threshold",
            "config_path",
        }
        bad = set(params) - allowed
        if bad:
            raise RequestError(f"unknown cleanup parameter(s): {sorted(bad)}")
        if not os.path.isfile(os.path.join(folder, "fund_v.npy")):
            raise InputError(f"no wavetracker results in {folder}")
        limit = req.get("mem_limit_bytes")
        if limit:
            try:
                import resource

                resource.setrlimit(resource.RLIMIT_AS, (int(limit), int(limit)))
            except (ImportError, ValueError, OSError):
                pass  # not Linux, or not allowed: run without a limit
        send({"type": "progress", "id": job, "stage": "cleanup", "done": 0,
              "total": -1, "text": "cleaning up identities"})  # fmt: skip
        import inspect

        from wavetracker.postprocessing import cleanup

        cleanup.show_results = False
        kwargs = dict(params)
        if "progress" in inspect.signature(cleanup.main).parameters:
            # wavetracker >= f8adc2c reports (step, fraction of the run);
            # older ones leave the bar busy, as before
            last = [-1]

            def progress(stage, fraction):
                permille = int(1000 * fraction)
                if permille != last[0]:
                    last[0] = permille
                    send({"type": "progress", "id": job, "stage": "cleanup",
                          "done": permille, "total": 1000,
                          "text": stage})  # fmt: skip

            kwargs["progress"] = progress
        cleanup.main(folder, n_fish=n_fish, **kwargs)
        plt = sys.modules.get("matplotlib.pyplot")
        if plt is not None:  # cleanup creates figures it never closes
            try:
                plt.close("all")
            except Exception:
                pass
        names = sorted(
            f
            for f in os.listdir(folder)
            if f.startswith("ident_v_cleaned_n") and f.endswith(".npy")
        )
        if n_fish is not None:
            names = [f"ident_v_cleaned_n{int(n_fish)}.npy"]
        if not names or not os.path.isfile(os.path.join(folder, names[0])):
            raise RuntimeError("cleanup wrote no ident_v_cleaned_n*.npy")
        name = names[0]
        suffix = name[len("ident_v") :]
        for base in ("idx_v", "fund_v"):
            cleaned = os.path.join(folder, base + suffix)
            if os.path.exists(cleaned):
                a = np.load(os.path.join(folder, base + ".npy"))
                b = np.load(cleaned)
                if a.shape != b.shape or not np.array_equal(a, b, equal_nan=True):
                    raise RuntimeError(
                        f"cleanup changed {base}; it may only change identities"
                    )
        ident = np.load(os.path.join(folder, name))
        ids = np.unique(ident[~np.isnan(ident)])
        return {
            "type": "result",
            "id": job,
            "ident_path": os.path.join(folder, name),
            "n_ids": int(len(ids)),
            "n_assigned": int(np.count_nonzero(~np.isnan(ident))),
        }

    # -- dispatch ------------------------------------------------------
    def handle(self, req: dict) -> None:
        job = req.get("id")
        op = req.get("op")
        fn = getattr(self, f"op_{op}", None) if isinstance(op, str) else None
        try:
            if fn is None:
                raise RequestError(f"unknown op {op!r}")
            send(fn(req))
        except Exception as e:
            send(error_message(job, e))


def error_message(job, exc: BaseException) -> dict:
    import traceback

    if isinstance(exc, RequestError):
        kind = "bad_request"
    elif isinstance(exc, (InputError, FileNotFoundError)):
        kind = "input"
    elif isinstance(exc, MemoryError) or "OutOfMemory" in type(exc).__name__:
        kind = "oom"
    else:
        kind = "exception"
    message = str(exc) or type(exc).__name__
    if kind == "exception":
        message = f"{type(exc).__name__}: {message}"
    return {
        "type": "error",
        "id": job,
        "kind": kind,
        "message": message,
        "traceback": "".join(traceback.format_exception(exc)),
    }


class _ProtocolLogHandler:
    """Forwards the `wavetracker` logger to the protocol as `log` lines."""

    @staticmethod
    def install() -> None:
        import logging

        class Handler(logging.Handler):
            def emit(self, record):
                try:
                    send(
                        {
                            "type": "log",
                            "level": record.levelname.lower(),
                            "text": record.getMessage(),
                        }
                    )
                except Exception:
                    pass

        log = logging.getLogger("wavetracker")
        log.addHandler(Handler(logging.INFO))
        log.setLevel(logging.INFO)


def _start_reader():
    """Read stdin on a thread so the main loop can time out when idle."""
    import queue

    q: queue.Queue = queue.Queue()

    def reader():
        try:
            for line in sys.stdin:
                q.put(line)
        finally:
            q.put(None)

    threading.Thread(target=reader, daemon=True).start()
    return q


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    _reserve_stdout()
    os.environ.setdefault("MPLBACKEND", "Agg")  # never a window from this process
    oneshot = "--oneshot" in argv
    idle = IDLE_S
    if "--idle" in argv:
        i = argv.index("--idle")
        try:
            idle = float(argv[i + 1])
        except (IndexError, ValueError):
            pass

    # SIGTERM unwinds through the job's cleanup instead of killing outright
    try:
        import signal

        def _terminate(signum, frame):
            raise SystemExit(128 + signum)

        signal.signal(signal.SIGTERM, _terminate)
    except (ImportError, ValueError, AttributeError):
        pass

    try:
        greeting = hello()
    except BaseException as e:  # wavetracker missing or broken
        msg = error_message(None, e)
        msg["kind"] = "startup"
        if isinstance(e, ImportError):
            msg["message"] = (
                "wavetracker is not installed in this environment: "
                f"{sys.executable} cannot import {e.name or 'wavetracker'}: {e}"
            )
        send(msg)
        return 3
    send(greeting)
    _ProtocolLogHandler.install()

    import queue

    runner = Runner()
    lines = _start_reader()
    while True:
        try:
            line = lines.get(timeout=idle)
        except queue.Empty:
            return 0  # idle too long
        if line is None:
            return 0  # end of stdin
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            if not isinstance(req, dict):
                raise ValueError("not a JSON object")
        except ValueError as e:
            send({"type": "error", "id": None, "kind": "bad_request",
                  "message": f"cannot parse request: {e}", "traceback": ""})  # fmt: skip
            continue
        if req.get("op") == "shutdown":
            return 0
        runner.handle(req)
        if oneshot:
            return 0


if __name__ == "__main__":
    sys.exit(main())
