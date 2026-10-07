"""Add by ridge, the Qt half: read the audio and find the ridge off the GUI
thread (design 5.5).

`RidgeSource` is what the Add tool asks; it owns one worker thread for the
panel's lifetime.  Every request cancels the one before it (a new stroke, or
Esc, supersedes a search still running), and an answer for a superseded
request is dropped.  The audio is read through audian's `open_files`, never
from ``browser.data``, and the opened loader is kept for the next stroke of
the same recording.
"""

from __future__ import annotations

import os
import time
from typing import Optional

import numpy as np
from PySide6.QtCore import QObject, QThread, Signal, Slot

from audian.pluginapi import CancelToken

from . import ridge as RG


#: the loader's buffer, and the largest single read [s]
BUFFER_S = 30.0


def _read(loader, a: int, b: int, token) -> np.ndarray:
    """Samples ``[a, b)`` in buffer-sized pieces (a long stroke)."""
    step = max(1, int(BUFFER_S * float(loader.rate)) // 2)
    parts = []
    for x in range(a, b, step):
        token.check()
        parts.append(np.asarray(loader[x : min(b, x + step)], dtype=np.float32))
    return np.concatenate(parts, axis=0) if parts else np.zeros((0, 1), np.float32)


class RidgeWorker(QObject):
    """Lives on the source's thread; `run` does one search."""

    sigDone = Signal(int, object)  # request id, (freqs, sign, cplx, info) or error str

    def __init__(self) -> None:
        super().__init__()
        self._loader = None
        self._paths = None

    def _open(self, paths):
        key = tuple(paths)
        if self._loader is not None and self._paths == key:
            return self._loader
        self.close()
        from audian.pluginapi import open_files

        arg = paths[0] if len(paths) == 1 else list(paths)
        self._loader = open_files(arg, BUFFER_S, 0.0)
        self._paths = key
        return self._loader

    def close(self) -> None:
        loader, self._loader, self._paths = self._loader, None, None
        if loader is not None:
            try:
                loader.close()
            except Exception:  # noqa: BLE001 - closing is best effort
                pass

    @Slot(int, object)
    def run(self, rid: int, job: dict) -> None:
        token = job["token"]
        try:
            token.check()
            loader = self._open(job["paths"])
            rate = float(loader.rate)
            n_total = len(loader)
            nfft = int(job["nfft"])
            starts = RG.frame_starts(
                job["grid"], job["frames"], job["all_times"], rate, nfft
            )
            frame_dt = job["frame_dt"]
            result = RG.find_ridge(
                lambda a, b: _read(loader, a, b, token),
                n_total,
                rate,
                starts,
                nfft,
                np.asarray(job["all_times"])[job["frames"]],
                job["lo"],
                job["hi"],
                RG.max_slope_from(job["meta"], frame_dt),
                channels=job["channels"],
                check=token.check,
            )
        except Exception as exc:  # noqa: BLE001 - reported to the reader
            if type(exc).__name__ == "Cancelled":
                return
            name = type(exc).__name__
            self.sigDone.emit(rid, f"{name}: {exc}" if str(exc) else name)
            return
        self.sigDone.emit(rid, result)


class RidgeSource(QObject):
    """Add's ridge search (5.5): ``request(frames, lo, hi, done)``."""

    name = "ridge in the brush"
    _sigRun = Signal(int, object)

    def __init__(self, panel, parent=None) -> None:
        super().__init__(parent)
        self.panel = panel
        self._thread: Optional[QThread] = None
        self._worker: Optional[RidgeWorker] = None
        self._rid = 0
        self._token: Optional[CancelToken] = None
        self._done = None
        self._t0 = 0.0
        #: the last answer's timings, for the hint and the tests
        self.last_info: dict = {}

    def _ensure(self) -> None:
        if self._thread is not None:
            return
        self._thread = QThread()
        self._thread.setObjectName("wavetracker-ridge")
        self._worker = RidgeWorker()
        self._worker.moveToThread(self._thread)
        self._sigRun.connect(self._worker.run)
        self._worker.sigDone.connect(self._answer)
        self._thread.start()

    def available(self) -> str:
        """Empty when a search can run, else why not."""
        p = self.panel
        if p.ts is None:
            return "no session"
        if not p.recording_paths():
            return "the recording's files are not known"
        return ""

    def nfft(self) -> int:
        ts = self.panel.ts
        if ts.grid is not None:
            return int(ts.grid.nfft)
        cfg = (ts.meta or {}).get("config") or {}
        spec = cfg.get("spectrogram") if isinstance(cfg, dict) else None
        if isinstance(spec, dict) and spec.get("nfft"):
            return int(spec["nfft"])
        return int(RG.wavetracker_default("spectrogram", "nfft", 2**15))

    def request(self, frames, lo, hi, done) -> None:
        from .panel import session_channels

        self.cancel()
        p = self.panel
        ts = p.ts
        why = self.available()
        if why:
            done(None, None, None, error=why)
            return
        self._ensure()
        times = np.asarray(ts.times, dtype=np.float64)
        frame_dt = float(np.median(np.diff(times))) if len(times) > 1 else 1.0
        self._rid += 1
        self._token = CancelToken()
        self._done = done
        self._t0 = time.perf_counter()
        job = {
            "token": self._token,
            "paths": [os.fspath(x) for x in p.recording_paths()],
            "grid": ts.grid,
            "frames": np.asarray(frames, dtype=np.int64),
            "all_times": times,
            "frame_dt": frame_dt,
            "lo": np.asarray(lo, dtype=np.float64),
            "hi": np.asarray(hi, dtype=np.float64),
            "nfft": self.nfft(),
            "meta": dict(ts.meta or {}),
            "channels": session_channels(ts, p._n_recording_channels()),
        }
        self._sigRun.emit(self._rid, job)

    def _answer(self, rid: int, result) -> None:
        if rid != self._rid or self._done is None:
            return
        done, self._done = self._done, None
        self._token = None
        if isinstance(result, str):
            done(None, None, None, error=result)
            return
        freqs, sign, cplx, info = result
        info = dict(info)
        info["total"] = time.perf_counter() - self._t0
        self.last_info = info
        ts = self.panel.ts
        c = getattr(ts, "n_channels", None) if ts is not None else None
        if sign is not None and c is not None and sign.shape[1] != c:
            sign, cplx = None, None  # the session has other electrodes
        done(freqs, sign, cplx)

    @property
    def busy(self) -> bool:
        return self._done is not None

    def cancel(self) -> None:
        if self._token is not None:
            self._token.cancel()
        self._token = None
        self._done = None

    def shutdown(self) -> None:
        """Stop the thread; one stuck in a read outlives the panel."""
        self.cancel()
        thread, worker = self._thread, self._worker
        self._thread = self._worker = None
        if thread is None:
            return
        try:
            self._sigRun.disconnect()
        except (RuntimeError, TypeError):
            pass
        thread.quit()
        if thread.wait(3000):
            worker.close()
            return
        from .panel import orphan_thread

        orphan_thread(thread, worker)


__all__ = ["RidgeSource", "RidgeWorker"]
