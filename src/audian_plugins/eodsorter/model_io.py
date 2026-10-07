"""Disk side of the EOD sorter model: wavetracker's directory format.

numpy and the standard library only.  `model.py` re-exports the public
names; nothing here knows about `TrackSet`.

A results directory (wavetracker's `Results.save`) holds ``fund_v.npy``,
``idx_v.npy``, ``sign_v.npy``, ``ident_v.npy``, ``times.npy``, optionally
``cplx_v.npy`` and ``wavetracker.json``.  The sorter adds
``ident_v.tracked.npy`` (the tracker's identities, never overwritten for
existing rows), ``eodsorter.json`` and, between saves,
``.eodsorter-autosave.npz``.

Saving is atomic over several files (design 8.2): every file goes to a
temporary neighbour ``.<name>.tmp<pid>``, then ``eodsorter.commit.json``
lists the pending ``(tmp, final)`` pairs, then each pair is
``os.replace``d, then the commit file is removed.
`finish_interrupted_save` completes step 3 after a crash.
"""

from __future__ import annotations

import io
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

REQUIRED = ("fund_v", "idx_v", "sign_v", "times")
IDENT_FILE = "ident_v.npy"
TRACKED_FILE = "ident_v.tracked.npy"
META_FILE = "wavetracker.json"
SORTER_FILE = "eodsorter.json"
COMMIT_FILE = "eodsorter.commit.json"
AUTOSAVE_FILE = ".eodsorter-autosave.npz"
SORTER_VERSION = 1

_STRAY = re.compile(
    r"^\.((?:fund_v|idx_v|sign_v|cplx_v|times|ident_v[\w.\-]*)\.npy"
    r"|wavetracker\.json|eodsorter\.json|eodsorter\.commit\.json"
    r"|\.eodsorter-autosave\.npz)\.tmp\d+$"
)


class ResultsError(ValueError):
    """A results directory that cannot be loaded; `.args[0]` says why."""


@dataclass(frozen=True)
class Append:
    """A block of rows appended to every per-detection array."""

    fund: np.ndarray
    idx: np.ndarray
    ident: np.ndarray
    tracked: np.ndarray
    sign: np.ndarray
    cplx: np.ndarray | None

    def __len__(self) -> int:
        return len(self.fund)

    @property
    def nbytes(self) -> int:
        total = sum(
            a.nbytes for a in (self.fund, self.idx, self.ident, self.tracked, self.sign)
        )
        return total + (0 if self.cplx is None else self.cplx.nbytes)


@dataclass(frozen=True)
class Autosave:
    """What `read_autosave` found (design 8.3)."""

    ident: np.ndarray  # identities of all n_base + len(append) rows
    append: Append | None  # rows appended since the last save
    n_base: int  # row count of the arrays on disk the autosave was based on
    next_id: int
    labels: dict[int, str]
    notes: dict[int, str]
    history: list[str]
    base_mtime: float  # mtime of ident_v.npy it was based on (0 if none)
    time: float  # wall clock when it was written
    #: (rate, nfft, step, s0, n_frames) of the session's frame grid, to
    #: rebuild a session that has no results directory (None if unknown)
    grid: tuple | None = None
    #: the session's wavetracker metadata, for the same purpose
    meta: dict | None = None
    #: the recording the session belongs to (cache autosaves only)
    recording: str | None = None

    def is_stale(self, folder) -> bool:
        """Whether ``ident_v.npy`` in `folder` changed after this autosave's
        base was loaded -- another tool or another audian rewrote it, and
        recovering would overwrite that (design 8.3)."""
        now = ident_mtime(folder)
        return bool(now and self.base_mtime and abs(now - self.base_mtime) > 1e-3)


# --------------------------------------------------------------------------
# atomic writing


def _fsync_path(path: Path) -> None:
    """Flush one path to the disk, ignoring platforms that cannot."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _tmp_name(path: Path) -> Path:
    return path.with_name(f".{path.name}.tmp{os.getpid()}")


def _write_tmp(path: Path, write: Callable[[Path], None]) -> Path:
    tmp = _tmp_name(path)
    try:
        write(tmp)
        _fsync_path(tmp)
    except BaseException:
        _unlink(tmp)
        raise
    return tmp


def replace_atomically(path: Path, write: Callable[[Path], None]) -> None:
    """Write through `write` to a temporary neighbour, then move it onto `path`
    (a copy of `audian.atomicwrite.replace_atomically`)."""
    path = Path(path)
    tmp = _write_tmp(path, write)
    try:
        os.replace(tmp, path)
    except BaseException:
        _unlink(tmp)
        raise
    _fsync_path(path.parent)


def _unlink(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def npy_writer(array: np.ndarray) -> Callable[[Path], None]:
    def write(tmp: Path) -> None:
        with open(tmp, "wb") as f:  # a file object: np.save would append ".npy"
            np.save(f, array, allow_pickle=False)

    return write


def json_writer(obj) -> Callable[[Path], None]:
    text = json.dumps(obj, indent=2, default=str)

    def write(tmp: Path) -> None:
        tmp.write_text(text)

    return write


def commit_files(folder: Path, items: list[tuple[str, Callable[[Path], None]]]) -> None:
    """Write several files so that they all appear, or (after
    `finish_interrupted_save`) all appear anyway (design 8.2)."""
    folder = Path(folder)
    pending: list[tuple[Path, Path]] = []
    try:
        for name, write in items:
            final = folder / name
            pending.append((_write_tmp(final, write), final))
    except BaseException:
        for tmp, _ in pending:
            _unlink(tmp)
        raise
    pairs = [[tmp.name, final.name] for tmp, final in pending]
    replace_atomically(folder / COMMIT_FILE, json_writer({"pairs": pairs}))
    for tmp, final in pending:
        os.replace(tmp, final)
    _fsync_path(folder)
    _unlink(folder / COMMIT_FILE)
    _fsync_path(folder)


def finish_interrupted_save(folder) -> bool:
    """Complete a save that was interrupted after its commit file was
    written.  Returns True if one was completed.  Stray temporaries of a
    save that crashed earlier are removed."""
    folder = Path(folder)
    if not folder.is_dir():
        return False
    done = False
    commit = folder / COMMIT_FILE
    if commit.exists():
        try:
            pairs = json.loads(commit.read_text())["pairs"]
        except (OSError, ValueError, KeyError, TypeError):
            pairs = []
        for tmp_name, final_name in pairs:
            tmp = folder / Path(tmp_name).name
            final = folder / Path(final_name).name
            if tmp.exists():
                os.replace(tmp, final)
        _fsync_path(folder)
        _unlink(commit)
        done = True
    for entry in folder.iterdir():
        if _STRAY.match(entry.name):
            _unlink(entry)
    return done


# --------------------------------------------------------------------------
# reading


def npy_rows(path: Path) -> int | None:
    """Row count of an ``.npy`` file without reading it, None if missing."""
    path = Path(path)
    if not path.exists():
        return None
    try:
        return int(np.load(path, mmap_mode="r", allow_pickle=False).shape[0])
    except (ValueError, OSError, IndexError):
        return None


def _load(path: Path) -> np.ndarray:
    try:
        return np.load(path, allow_pickle=False)
    except (ValueError, OSError) as exc:
        raise ResultsError(f"cannot read {path.name}: {exc}") from exc


def read_json(path: Path) -> dict | None:
    try:
        obj = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def check_ident(ident: np.ndarray, n: int, name: str) -> np.ndarray:
    ident = np.asarray(ident)
    if ident.ndim != 1 or len(ident) != n:
        raise ResultsError(f"{name} has {ident.shape} entries, expected ({n},)")
    if not np.issubdtype(ident.dtype, np.number) or np.iscomplexobj(ident):
        raise ResultsError(f"{name} is not numeric")
    ident = ident.astype(np.float64)
    ok = np.isnan(ident) | (np.isfinite(ident) & (ident == np.round(ident)))
    if not ok.all():
        bad = ident[~ok][0]
        raise ResultsError(f"{name} holds a non-integer id ({bad})")
    return ident


def read_results(folder) -> dict:
    """Read and check a results directory.

    Returns a dict with ``fund, idx, ident, sign, cplx, times, tracked``
    (``tracked`` None when there is no ``ident_v.tracked.npy``), ``meta``,
    ``sorter`` (eodsorter.json or None), ``dtypes`` (on-disk dtypes),
    ``complaints``.  Raises `ResultsError` for structural problems."""
    folder = Path(folder)
    if not folder.is_dir():
        raise ResultsError(f"{folder} is not a directory")
    missing = [n for n in REQUIRED if not (folder / f"{n}.npy").exists()]
    if missing:
        raise ResultsError(
            f"no wavetracker results in {folder}: missing "
            + ", ".join(f"{n}.npy" for n in missing)
        )
    complaints: list[str] = []
    fund = _load(folder / "fund_v.npy")
    idx = _load(folder / "idx_v.npy")
    sign = _load(folder / "sign_v.npy")
    times = _load(folder / "times.npy")
    dtypes = {"fund_v": fund.dtype, "idx_v": idx.dtype, "sign_v": sign.dtype}
    if fund.ndim != 1:
        raise ResultsError(f"fund_v.npy has shape {fund.shape}, expected (n,)")
    n = len(fund)
    if idx.shape != (n,):
        raise ResultsError(f"idx_v.npy has shape {idx.shape}, fund_v.npy has {n} rows")
    if sign.ndim != 2 or len(sign) != n:
        raise ResultsError(
            f"sign_v.npy has shape {sign.shape}, expected ({n}, electrodes)"
        )
    if times.ndim != 1:
        raise ResultsError(f"times.npy has shape {times.shape}, expected (frames,)")
    if not np.issubdtype(idx.dtype, np.integer):
        if not np.all(np.isfinite(idx)) or np.any(idx != np.round(idx)):
            raise ResultsError("idx_v.npy holds non-integer frame indices")
    idx = idx.astype(np.int64)
    if n and (idx.min() < 0 or idx.max() >= len(times)):
        raise ResultsError(
            f"idx_v.npy points outside times.npy (frames 0..{len(times) - 1})"
        )
    ident_path = folder / IDENT_FILE
    if ident_path.exists():
        ident = check_ident(_load(ident_path), n, IDENT_FILE)
    else:
        ident = np.full(n, np.nan)
    cplx = None
    if (folder / "cplx_v.npy").exists():
        cplx = _load(folder / "cplx_v.npy")
        dtypes["cplx_v"] = cplx.dtype
        if cplx.shape != sign.shape:
            raise ResultsError(
                f"cplx_v.npy has shape {cplx.shape}, sign_v.npy has {sign.shape}"
            )
    tracked = None
    if (folder / TRACKED_FILE).exists():
        raw = np.asarray(_load(folder / TRACKED_FILE), dtype=np.float64)
        if raw.ndim != 1:
            raise ResultsError(f"{TRACKED_FILE} is not one-dimensional")
        tracked = np.full(n, np.nan)
        m = min(n, len(raw))
        tracked[:m] = raw[:m]
        tracked = check_ident(tracked, n, TRACKED_FILE)
    meta = {}
    if (folder / META_FILE).exists():
        got = read_json(folder / META_FILE)
        if got is None:
            complaints.append(f"{META_FILE} could not be read; ignored")
        else:
            meta = got
    sorter = None
    if (folder / SORTER_FILE).exists():
        sorter = read_json(folder / SORTER_FILE)
        if sorter is None:
            complaints.append(f"{SORTER_FILE} could not be read; ignored")
    return dict(
        fund=fund.astype(np.float64),
        idx=idx,
        ident=ident,
        sign=sign.astype(np.float32),
        cplx=None if cplx is None else cplx.astype(np.complex64),
        times=times.astype(np.float64),
        tracked=tracked,
        meta=meta,
        sorter=sorter,
        dtypes=dtypes,
        complaints=complaints,
    )


def ident_variants(folder) -> list[str]:
    """Identity files of a results directory, ``ident_v.npy`` first, e.g.
    ``["ident_v.npy", "ident_v_cleaned_n2.npy"]``."""
    folder = Path(folder)
    if not folder.is_dir():
        return []
    names = sorted(
        p.name
        for p in folder.glob("ident_v*.npy")
        if p.name not in (IDENT_FILE, TRACKED_FILE) and not p.name.startswith(".")
    )
    return ([IDENT_FILE] if (folder / IDENT_FILE).exists() else []) + names


# --------------------------------------------------------------------------
# autosave


def write_autosave_file(folder, payload: dict) -> None:
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)

    def write(tmp: Path) -> None:
        buf = io.BytesIO()
        np.savez(buf, **payload)
        tmp.write_bytes(buf.getvalue())

    replace_atomically(folder / AUTOSAVE_FILE, write)


def read_autosave(folder) -> Autosave | None:
    """The autosave in `folder`, or None if there is none or it is unreadable."""
    path = Path(folder) / AUTOSAVE_FILE
    if not path.exists():
        return None
    try:
        with np.load(path, allow_pickle=False) as z:
            d = {k: z[k] for k in z.files}
        info = json.loads(str(d["info"]))
        n_base = int(info["n_base"])
        ident = np.asarray(d["ident"], dtype=np.float64)
        append = None
        if len(ident) > n_base:
            append = Append(
                fund=np.asarray(d["a_fund"], np.float64),
                idx=np.asarray(d["a_idx"], np.int64),
                ident=ident[n_base:].copy(),
                tracked=np.asarray(d["a_tracked"], np.float64),
                sign=np.asarray(d["a_sign"], np.float32),
                cplx=np.asarray(d["a_cplx"], np.complex64) if "a_cplx" in d else None,
            )
        return Autosave(
            ident=ident,
            append=append,
            n_base=n_base,
            next_id=int(info["next_id"]),
            labels={int(k): str(v) for k, v in info.get("labels", {}).items()},
            notes={int(k): str(v) for k, v in info.get("notes", {}).items()},
            history=[str(h) for h in info.get("history", [])],
            base_mtime=float(info.get("base_mtime", 0.0)),
            time=float(info.get("time", path.stat().st_mtime)),
            grid=tuple(info["grid"]) if info.get("grid") else None,
            meta=info.get("meta") if isinstance(info.get("meta"), dict) else None,
            recording=info.get("recording"),
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None


def discard_autosave(folder) -> None:
    _unlink(Path(folder) / AUTOSAVE_FILE)


def ident_mtime(folder) -> float:
    try:
        return (Path(folder) / IDENT_FILE).stat().st_mtime
    except OSError:
        return 0.0


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")
