"""CompressedData

Handle compressed and cached data for FullTracePlot.
"""

import os
import sys
import glob
import json
import argparse
import ctypes as c
import numpy as np

from pathlib import Path
from datetime import datetime
from multiprocessing import Process, Array, Value, set_start_method

from . import activity
from audioio import AudioLoader
from audioio import load_audio, write_audio
from audioio.audioconverter import parse_load_kwargs
from thunderlab.dataloader import DataLoader

from .version import __version__, __year__, audian_dirs


def down_sample_worker(
    proc_idx,
    num_proc,
    nblock,
    step,
    array,
    file_paths,
    tbuffer,
    rate,
    channels,
    unit,
    amax,
    end_indices,
    unwrap_thresh,
    unwrap_clips,
    load_kwargs,
    progress=None,
    stats_array=None,
):
    """Worker for prepare().

    `progress` is a shared counter of finished blocks, read by
    :meth:`CompressedData.progress` to drive the status bar.  It is
    incremented once per block, i.e. about once per thirty seconds of
    audio, so its lock is never contended.
    """
    if end_indices is None:
        data = DataLoader(file_paths, tbuffer, 0, verbose=0, **load_kwargs)
    else:
        data = DataLoader(
            file_paths,
            tbuffer,
            0,
            verbose=0,
            rate=rate,
            channels=channels,
            unit=unit,
            amax=amax,
            end_indices=end_indices,
            **load_kwargs,
        )
    data.set_unwrap(unwrap_thresh, unwrap_clips, False, data.unit)
    datas = np.frombuffer(array.get_obj()).reshape((-1, data.channels))
    stats = None
    if stats_array is not None:
        stats = np.frombuffer(stats_array.get_obj()).reshape((-1, data.channels))
    buffer = np.zeros((nblock, data.channels))
    segments = np.arange(0, len(buffer), step)
    for index in range(proc_idx * nblock, data.frames, num_proc * nblock):
        if data.frames - index < nblock:
            nblock = data.frames - index
            buffer = buffer[:nblock, :]
            segments = np.arange(0, len(buffer), step)
        data.load_buffer(index, nblock, buffer)
        i = 2 * index // step
        with array.get_lock():
            np.minimum.reduceat(
                buffer, segments, out=datas[i + 0 : i + 0 + 2 * len(segments) : 2]
            )
            np.maximum.reduceat(
                buffer, segments, out=datas[i + 1 : i + 1 + 2 * len(segments) : 2]
            )
        if stats is not None:
            # same bin layout and the same index arithmetic as the min/max
            # array above, so the two can never drift apart; see
            # audian.activity for what these two moments are used for.
            with stats_array.get_lock():
                np.add.reduceat(
                    buffer, segments, out=stats[i + 0 : i + 2 * len(segments) : 2]
                )
                np.add.reduceat(
                    np.square(buffer),
                    segments,
                    out=stats[i + 1 : i + 1 + 2 * len(segments) : 2],
                )
        if progress is not None:
            with progress.get_lock():
                progress.value += 1
    return None


class CompressedData:
    fulltraces_file = "fulltraces.json"
    max_files = 1000

    def __init__(self, data):  # , files, load_kwargs, unwrap, unwrap_clip):
        self.data = data
        self.procs = []
        self.shared_array = None
        self.times = None
        self.datas = None
        self.short_data = True
        self._progress = None
        self._total_blocks = 0
        # first and second moments per bin, laid out exactly like `datas`
        # (row 2k = sum, row 2k+1 = sum of squares).  `None` whenever the
        # overview came from a cache written before these existed, in which
        # case the navigator falls back to the plain min/max envelope.
        self.shared_stats = None
        self.stats_datas = None

    @staticmethod
    def stats_path(overview_path):
        """Sidecar holding the per-bin moments beside a cached overview.

        A separate file rather than more channels in the wav: the overview is
        stored as audio purely so `load_audio` can read it back, and widening
        that array would silently invalidate every cache written so far.
        """
        overview_path = Path(overview_path)
        return overview_path.with_name(overview_path.name + ".stats.npy")

    def save_stats(self, overview_path) -> None:
        """Persist the moments next to a just-written overview."""
        if self.stats_datas is None:
            return
        try:
            np.save(self.stats_path(overview_path), np.asarray(self.stats_datas))
        except OSError as e:
            # a missing sidecar only costs the activity overview, so a
            # failure here must never take the min/max cache down with it.
            print(f"could not write activity statistics: {e}", file=sys.stderr)

    def load_stats(self, overview_path, nrows: int) -> bool:
        """Restore the moments for a cached overview.  True on success."""
        path = self.stats_path(overview_path)
        if not path.is_file():
            self.stats_datas = None
            return False
        try:
            stats = np.load(path)
        except (OSError, ValueError):
            self.stats_datas = None
            return False
        if stats.ndim != 2 or len(stats) != nrows:
            # written for a different bin layout: unusable, and silently
            # pairing it with these minima would fabricate a metric.
            self.stats_datas = None
            return False
        self.stats_datas = stats
        return True

    def bin_stats(self, step: int) -> "activity.BinStats | None":
        """Per-bin accumulators for :mod:`audian.activity`, or ``None``.

        Returns ``None`` -- rather than raising or fabricating zeros --
        when no second moment is available, so callers can degrade to the
        min/max envelope instead of showing a metric built on missing data.
        """
        if self.stats_datas is None or self.datas is None:
            return None
        nbins = min(len(self.datas), len(self.stats_datas)) // 2
        if nbins < 2:
            return None
        counts = np.full(nbins, float(step))
        # the final bin is short whenever the recording does not divide
        # evenly by `step`; counting it as full would understate its RMS.
        tail = self.data.frames - (nbins - 1) * step
        if 0 < tail < step:
            counts[-1] = float(tail)
        return activity.BinStats(
            n=counts,
            total=self.stats_datas[0 : 2 * nbins : 2],
            total_sq=self.stats_datas[1 : 2 * nbins : 2],
            minimum=self.datas[0 : 2 * nbins : 2],
            maximum=self.datas[1 : 2 * nbins : 2],
        )

    def __del__(self):
        self.close()

    def close(self):
        for proc in self.procs:
            proc.terminate()
            proc.join()
            proc.close()
        self.procs = []

    # Upper bound on the memory the worker pool may allocate for its block
    # buffers.  Each worker holds one `nblock x channels` float64 buffer plus
    # its own DataLoader; without this cap a 32-core machine allocated ~4.6 GB
    # for an I/O bound job.
    max_pool_bytes = 256 * 1024 * 1024

    # Workers are I/O bound: more than a handful of them only adds RAM and
    # forkserver startup latency.
    max_procs = 8

    # Below this many samples the whole file is decimated inline; spawning a
    # process pool costs more than the work itself.
    inline_samples = 8_000_000

    def compression_layout(self, max_pixel: int) -> tuple[int, int, int]:
        """Decimation step, number of output rows and block size.

        `n` is derived ONCE from `segments` so that the time vector and the
        data array can never disagree in length -- they used to be computed
        by two independent expressions that only agreed by luck (1602 vs
        1601 rows on a 16 channel file, which made `setData` raise inside a
        QTimer slot and left the navigator blank).

        Returns
        -------
        step:
            Number of frames reduced into one min/max pair.
        n:
            Number of rows of both `times` and `datas` (two per segment).
        nblock:
            Number of frames a background worker loads at once, a multiple
            of `step` and small enough to respect `max_pool_bytes`.
        """
        step = max(1, self.data.frames // max_pixel)
        n = 2 * len(np.arange(0, self.data.frames, step))
        nblock = max(step, int(30.0 * self.data.rate // step) * step)
        return step, n, nblock

    def pool_size(self, step: int, nblock: int) -> tuple[int, int]:
        """Number of worker processes and the block size they may use.

        Caps the pool at `max_procs` (the job is I/O bound; `cpu_count() - 1`
        spawned 31 forkserver children here, each with its own block buffer
        and DataLoader) and then shrinks `nblock` until the aggregate block
        memory stays below `max_pool_bytes`.

        `nblock` stays a multiple of `step`: the workers address the shared
        array as `2*index//step`, which only lines up on block boundaries.
        """
        nprocs = max(1, min(CompressedData.max_procs, os.cpu_count() or 1))
        nblocks = max(1, (self.data.frames + nblock - 1) // nblock)
        nprocs = min(nprocs, nblocks)
        budget = CompressedData.max_pool_bytes // (nprocs * self.data.channels * 8)
        nblock = min(nblock, max(step, (int(budget) // step) * step))
        return nprocs, nblock

    def compress_inline(self, step, n):
        """Decimate the whole file in this process, block by block."""
        self.datas = np.zeros((n, self.data.channels))
        self.stats_datas = np.zeros((n, self.data.channels))
        nblock = max(step, int(30.0 * self.data.rate // step) * step)
        buffer = np.zeros((nblock, self.data.channels))
        for index in range(0, self.data.frames, nblock):
            nframes = min(nblock, self.data.frames - index)
            block = buffer[:nframes]
            self.data.load_buffer(index, nframes, block)
            segments = np.arange(0, nframes, step)
            i = 2 * index // step
            np.minimum.reduceat(
                block, segments, out=self.datas[i + 0 : i + 2 * len(segments) : 2]
            )
            np.maximum.reduceat(
                block, segments, out=self.datas[i + 1 : i + 1 + 2 * len(segments) : 2]
            )
            np.add.reduceat(
                block, segments, out=self.stats_datas[i + 0 : i + 2 * len(segments) : 2]
            )
            np.add.reduceat(
                np.square(block),
                segments,
                out=self.stats_datas[i + 1 : i + 1 + 2 * len(segments) : 2],
            )

    def start(self, max_pixel, load_kwargs, do_short=True):
        if self.times is not None and self.datas is not None:
            return
        self.procs = []
        step, n, nblock = self.compression_layout(max_pixel)
        end_indices = None
        if len(self.data.file_paths) > 1:
            end_indices = self.data.end_indices
        self.times = np.arange(n) * (step / 2) / self.data.rate
        if len(self.data.buffer) == self.data.frames:
            # short file, already fully in memory:
            self.short_data = True
            if do_short:
                segments = np.arange(0, self.data.frames, step)
                self.datas = np.zeros((n, self.data.channels))
                np.minimum.reduceat(
                    self.data.buffer,
                    segments,
                    out=self.datas[0 : 2 * len(segments) : 2],
                )
                np.maximum.reduceat(
                    self.data.buffer,
                    segments,
                    out=self.datas[1 : 1 + 2 * len(segments) : 2],
                )
                self.stats_datas = np.zeros((n, self.data.channels))
                np.add.reduceat(
                    self.data.buffer,
                    segments,
                    out=self.stats_datas[0 : 2 * len(segments) : 2],
                )
                np.add.reduceat(
                    np.square(self.data.buffer),
                    segments,
                    out=self.stats_datas[1 : 1 + 2 * len(segments) : 2],
                )
            return
        if self.data.frames * self.data.channels <= CompressedData.inline_samples:
            # small enough that a process pool would cost more than the work:
            self.short_data = True
            if do_short:
                self.compress_inline(step, n)
            return
        # compress in background:
        self.short_data = False
        nprocs, nblock = self.pool_size(step, nblock)
        self._progress = Value(c.c_long, 0)
        self._total_blocks = max(1, (self.data.frames + nblock - 1) // nblock)
        self.shared_array = Array(c.c_double, n * self.data.channels)
        self.datas = np.frombuffer(self.shared_array.get_obj())
        self.datas = self.datas.reshape((n, self.data.channels))
        self.shared_stats = Array(c.c_double, n * self.data.channels)
        self.stats_datas = np.frombuffer(self.shared_stats.get_obj())
        self.stats_datas = self.stats_datas.reshape((n, self.data.channels))
        for i in range(nprocs):
            p = Process(
                target=down_sample_worker,
                args=(
                    i,
                    nprocs,
                    nblock,
                    step,
                    self.shared_array,
                    self.data.file_paths,
                    nblock / self.data.rate + 0.1,
                    self.data.rate,
                    self.data.channels,
                    self.data.unit,
                    self.data.ampl_max,
                    end_indices,
                    self.data.unwrap_thresh,
                    self.data.unwrap_clips,
                    load_kwargs,
                    self._progress,
                    self.shared_stats,
                ),
            )
            self.procs.append(p)
        for p in self.procs:
            p.start()

    def wait(self):
        for p in self.procs:
            p.join()
        for p in self.procs:
            p.close()
        self.procs = []

    def is_busy(self):
        busy = False
        for proc in self.procs:
            if proc.is_alive():
                busy = True
                break
        if not busy:
            for proc in self.procs:
                proc.close()
            self.procs = []
        return busy

    def progress(self) -> float:
        """Fraction of the background compression that is done, 0 to 1.

        Returns 1.0 whenever there is nothing to wait for: a short file
        decimated inline, a cached ``-fulltrace.wav``, or a finished pool.
        """
        if self._progress is None or self._total_blocks <= 0:
            return 1.0
        if not self.procs:
            return 1.0
        done = self._progress.value
        return min(1.0, max(0.0, done / self._total_blocks))

    def get_lock(self):
        lock = self.shared_array.get_lock()
        return lock

    def save_data_local(self):
        if self.short_data:
            return
        ft_path = self.data.filepath.with_name(
            self.data.filepath.stem + "-fulltrace.wav"
        )
        rate = 1 / (self.times[1] - self.times[0])
        rate *= 1e6
        while rate > 2**31:
            rate /= 1e3
        write_audio(ft_path, self.datas, rate, format="WAV", encoding="DOUBLE")
        self.save_stats(ft_path)

    def save_data(self):
        if self.short_data:
            return
        audian_dirs.user_cache_path.mkdir(parents=True, exist_ok=True)
        files = {}
        ft_path = audian_dirs.user_cache_path / CompressedData.fulltraces_file
        if ft_path.exists():
            with ft_path.open() as sf:
                files = json.load(sf)
        # new filename:
        ft_name = f"{1:08X}-fulltrace.wav"
        for k in range(1, CompressedData.max_files + 10):
            ft_name = f"{k:08X}-fulltrace.wav"
            if ft_name not in files.keys():
                break
        # add to dictionary:
        first_file = Path(self.data.file_paths[0]).absolute()
        last_file = Path(self.data.file_paths[-1]).absolute()
        timestamp = datetime.now().isoformat()
        rate = 1 / (self.times[1] - self.times[0])
        ft_props = dict(
            first=os.fspath(first_file),
            last=os.fspath(last_file),
            rate=rate,
            created=timestamp,
            used=timestamp,
        )
        files[ft_name] = ft_props
        # remove old files:
        if len(files) > CompressedData.max_files:
            ft_files = list(files)
            timestamps = [files[ftf]["used"] for ftf in ft_files]
            idx = np.argsort(timestamps)
            for i in idx[: len(ft_files) - CompressedData.max_files]:
                try:
                    (audian_dirs.user_cache_path / ft_files[i]).unlink()
                except Exception as e:
                    print(e)
                files.pop(ft_files[i])
        # save json file:
        with ft_path.open("w") as df:
            json.dump(files, df, indent=4)
        # save file:
        rate *= 1e6
        while rate > 2**31:
            rate /= 1e3
        write_audio(
            audian_dirs.user_cache_path / ft_name,
            self.datas,
            rate,
            format="WAV",
            encoding="DOUBLE",
        )
        self.save_stats(audian_dirs.user_cache_path / ft_name)

    def load_data(self, min_rows: int = 0):
        """Load a cached min/max overview, if one is good enough to use.

        Parameters
        ----------
        min_rows:
            Reject a cached overview with fewer than this many rows (two per
            min/max bin).  A cache written when the navigator was a few
            hundred pixels wide has bins several pixels wide, and drawing it
            on a full-width strip is exactly the under-resolved overview the
            cache was supposed to make cheap.  ``0`` accepts anything.
        """
        self.times = None
        self.datas = None
        # load from folder of data file:
        ft_path = self.data.filepath.with_name(
            self.data.filepath.stem + "-fulltrace.wav"
        )
        if ft_path.exists():
            datas, rate = load_audio(ft_path)
            if len(datas) < min_rows:
                # too coarse for this screen: fall through to the user cache,
                # which may hold a finer one, and recompress if it does not.
                datas = None
            elif not self.load_stats(ft_path, len(datas)):
                # min/max only, i.e. written before the activity overview
                # existed.  Recompressing costs one pass and makes the cache
                # self-healing; keeping it would disable activity forever.
                datas = None
        else:
            datas = None
        if datas is not None:
            self.datas = datas
            rates = np.array([rate / 1e6, rate / 1e3, rate])
            durations = len(self.datas) / rates
            rate = rates[
                np.argmin(np.abs(durations - self.data.frames / self.data.rate))
            ]
            self.times = np.arange(len(self.datas)) / rate
            return
        # load from user cache:
        ft_path = audian_dirs.user_cache_path / CompressedData.fulltraces_file
        if audian_dirs.user_cache_path.exists() and ft_path.exists():
            # load json file:
            files = {}
            with ft_path.open() as sf:
                files = json.load(sf)
            # search for entry with matching source files:
            first_file = Path(self.data.file_paths[0]).absolute()
            last_file = Path(self.data.file_paths[-1]).absolute()
            dirty = False
            # a stale entry must not stop the search: an overview rejected
            # for being too coarse is dropped and the next candidate for the
            # same recording is tried, otherwise the recompressed one written
            # by save_data() could never be found again.
            for ft_file in list(files):
                ft_props = files[ft_file]
                if ft_props["first"] != os.fspath(first_file):
                    continue
                if ft_props["last"] != os.fspath(last_file):
                    continue
                # load full trace data:
                ft_file_path = audian_dirs.user_cache_path / ft_file
                if not ft_file_path.is_file() or ft_file_path.stat().st_size == 0:
                    del files[ft_file]
                    dirty = True
                    continue
                datas, _ = load_audio(ft_file_path)
                if len(datas) < min_rows or not self.load_stats(
                    ft_file_path, len(datas)
                ):
                    # too coarse to draw, or missing the moments the activity
                    # overview needs: forget it rather than keep hitting it.
                    try:
                        ft_file_path.unlink()
                        self.stats_path(ft_file_path).unlink(missing_ok=True)
                    except OSError as e:
                        print(e)
                    del files[ft_file]
                    dirty = True
                    continue
                self.datas = datas
                self.times = np.arange(len(self.datas)) / ft_props["rate"]
                ft_props["used"] = datetime.now().isoformat()
                dirty = True
                break
            if dirty:
                with ft_path.open("w") as df:
                    json.dump(files, df, indent=4)


def main(cargs):
    set_start_method("forkserver" if os.name == "posix" else "spawn")
    AudioLoader.max_open_files = os.cpu_count() + 2
    AudioLoader.max_open_loaders = 2 * AudioLoader.max_open_files
    # command line arguments:
    parser = argparse.ArgumentParser(
        description="Compress timeseries data for audian.",
        epilog=f"version {__version__} by Jan Benda (2026-{__year__})",
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument(
        "-i",
        dest="load_kwargs",
        default=[],
        action="append",
        metavar="KWARGS",
        help="key-word arguments for the data loader function",
    )
    parser.add_argument(
        "-u",
        dest="unwrap",
        default=0,
        type=float,
        metavar="UNWRAP",
        const=1.5,
        nargs="?",
        help="unwrap clipped data with threshold relative to maximum input range and divide by two using unwrap() from audioio package",
    )
    parser.add_argument(
        "-U",
        dest="unwrap_clip",
        default=0,
        type=float,
        metavar="UNWRAP",
        const=1.5,
        nargs="?",
        help="unwrap clipped data with threshold relative to maximum input range and clip using unwrap() from audioio package",
    )
    parser.add_argument(
        "files",
        nargs="+",
        default=[],
        type=str,
        help="name of files with the time series data",
    )
    args = parser.parse_args(cargs)

    # unwrap:
    if args.unwrap_clip > 1e-3:
        args.unwrap = args.unwrap_clip
        args.unwrap_clip = True
    else:
        args.unwrap_clip = False

    # kwargs for data loader:
    load_kwargs = parse_load_kwargs(args.load_kwargs)

    # expand wildcard patterns:
    files = []
    if os.name == "nt":
        for fn in args.files:
            files.extend(sorted(glob.glob(fn)))
    else:
        files = args.files

    # compress:
    data = DataLoader(files, **load_kwargs)
    data.set_unwrap(args.unwrap, args.unwrap_clip, False, data.unit)
    compress = CompressedData(data)
    compress.start(6000, load_kwargs)
    compress.wait()
    compress.save_data_local()


def run():
    main(sys.argv[1:])
    return 0


if __name__ == "__main__":
    run()
