"""Class managing all raw data, spectrograms, filtered and derived data
and the time window shown.

"""

import logging
import os

import numpy as np

from audioio import get_datetime
from thunderlab.dataloader import DataLoader

from . import theme
from .buffereddata import MinMaxPyramid
from .bufferedspectrogram import BufferedSpectrogram

log = logging.getLogger(__name__)


#: How far apart two consecutive files' timestamps may be before the join is
#: reported as a possible discontinuity.  One second is what the loader itself
#: uses; it is a *reporting* threshold here, never a reason to drop audio.
MAX_JOIN_GAP_S = 1.0


def file_frames(paths) -> list:
    """Frame count of every file, read from its header.

    The authority on how long a split recording is.  Header reads only, so it
    costs milliseconds on a gigabyte -- which is what makes it affordable to
    check the loader against it every single time a session is opened.
    """
    import soundfile as sf

    frames = []
    for path in paths:
        try:
            frames.append(int(sf.info(os.fspath(path)).frames))
        except Exception:
            # a file soundfile cannot read is a real failure, but it is the
            # loader's failure to report -- here it only means "no opinion"
            frames.append(None)
    return frames


def open_files(paths, tbuffer, tback, verbose=0, **kwargs):
    """Open one or more files as a single recording, without losing the tail.

    `thunderlab.DataLoader` decides whether consecutive files belong to one
    recording by comparing each file's metadata timestamp against the end of
    the previous one, and on a mismatch it ``break``s -- dropping that file
    **and every file after it**.  The check is not gated on ``mode``, so
    ``mode='relaxed'`` does not disable it, and the documented escape hatch
    ``AudioLoader.set_time_delta()`` is declared without ``self`` and raises
    ``TypeError`` on any call.

    That heuristic assumes the timestamp is when a file was *opened*.  A TASCAM
    writes bext ``OriginationTime`` as the moment the file was *closed*, so the
    expected and the actual value are both shifted by one file's duration and
    the two errors cancel -- exactly as long as every file is the same length.
    They stop cancelling at the last file of a session, which is the only short
    one.  So it always drops precisely the tail, and only the tail.

    Measured on a four-file 60.35 min TASCAM session: 173,809,152 frames on
    disk, 134,203,392 returned, the final 825.12 s silently absent, no error at
    ``verbose=1`` beyond one line on stdout that no GUI user ever sees.

    Audian is a viewer: a wall-clock gap between two files is a fact to report,
    never a reason to hide a quarter of the recording.  So the continuity
    tolerance is widened to accept anything, every file the caller named is
    concatenated, and the joins are reported separately by `join_gaps()`.

    Returns
    -------
    loader: DataLoader
        Open, positioned at the start, spanning every file.

    Raises
    ------
    ValueError
        If the loader still returns fewer frames than the files hold.  A viewer
        that quietly shows 46 minutes of a 60 minute session looks completely
        normal, so this is never a warning.
    """
    single = not isinstance(paths, (list, tuple, np.ndarray))
    if single:
        return DataLoader(paths, tbuffer, tback, verbose=verbose, **kwargs)

    paths = list(paths)
    if len(paths) == 1:
        return DataLoader(paths[0], tbuffer, tback, verbose=verbose, **kwargs)

    # Construct empty, widen the tolerance, then open: `_max_time_diff` is set
    # by __init__, so it cannot be raised on a loader that was handed its files
    # up front -- the check has already run and truncated the list by then.
    loader = DataLoader()
    # A year, not inf: the value is handed to timedelta(seconds=...), which
    # raises OverflowError on a float infinity.
    loader._max_time_diff = 365 * 24 * 3600
    try:
        loader.open_multiple(paths, tbuffer, tback, verbose=verbose, **kwargs)
    except Exception:
        loader.close()
        raise

    expected = file_frames(paths)
    if all(f is not None for f in expected):
        total = sum(expected)
        if loader.frames != total:
            opened = {os.fspath(p) for p in loader.file_paths}
            missing = [os.fspath(p) for p in paths if os.fspath(p) not in opened]
            loader.close()
            raise ValueError(
                f"loader returned {loader.frames} frames, but the "
                f"{len(paths)} files hold {total} "
                f"({(total - loader.frames) / max(loader.rate, 1):.2f} s missing)"
                + (f"; not opened: {', '.join(missing)}" if missing else "")
            )
    return loader


def join_gaps(loader):
    """Report joins whose timestamps do not look continuous.

    This is the information `thunderlab.DataLoader` throws a quarter of a
    session away to act on.  Surfacing it instead is the whole trade: the user
    is told the third join looks wrong and can judge it, rather than being
    shown 46 of 60 minutes with no indication that anything is missing.

    Yields ``(index, path, expected_seconds, actual_seconds)`` per suspect
    join, where the seconds are measured from the first file's timestamp.
    """
    paths = list(getattr(loader, "file_paths", []))
    if len(paths) < 2:
        return
    starts = loader.data_files if hasattr(loader, "data_files") else []
    times = []
    for path, opened in zip(paths, list(starts) + [None] * len(paths)):
        stamp = None
        try:
            source = opened if opened is not None else path
            stamp = get_datetime(
                source.metadata() if hasattr(source, "metadata") else {}
            )
        except Exception:
            stamp = None
        times.append(stamp)
    if times[0] is None:
        return
    ends = np.asarray(loader.end_indices, dtype=float) / max(loader.rate, 1)
    for i in range(1, len(paths)):
        if times[i] is None:
            continue
        actual = (times[i] - times[0]).total_seconds()
        # What the timestamp would read if this file followed the last one with
        # no gap.  Which of open-time or close-time the recorder writes is not
        # knowable from the file, so both readings are accepted -- and both are
        # measured against file 0's own timestamp, whichever that is.  Reading
        # the close-time case against absolute file ends instead makes every
        # TASCAM session report a spurious gap at its final short file, which
        # is a warning that trains the user to ignore warnings.
        as_open = ends[i - 1]
        as_close = ends[i] - ends[0]
        if min(abs(actual - as_open), abs(actual - as_close)) > MAX_JOIN_GAP_S:
            yield i, os.fspath(paths[i]), as_open, actual


def count_buffer_loads(loader) -> None:
    """Give a thunderlab DataLoader a buffer generation counter.

    The min/max pyramid over the raw buffer has to be rebuilt whenever that
    buffer is refilled.  `move_buffer()` only raises the per-channel
    `buffer_changed` flags, and every plot item clears its own flag
    independently, so they cannot drive a single shared rebuild.
    """
    loader.buffer_generation = 0
    inner = loader.load_buffer

    def load_buffer(offset, nframes, buffer):
        loader.buffer_generation += 1
        return inner(offset, nframes, buffer)

    loader.load_buffer = load_buffer


class Data(object):
    """All raw data, spectrograms, filtered and derived data of one file."""

    # Byte budget for the raw buffer of a single trace.  60 s of 16 channels
    # at 20 kHz is 153.6 MB of raw data, the same again filtered and another
    # 155 MB of spectrogram -- for a view that shows 10 s.  A working set that
    # size is far outside L3, which is also what makes the strided per-channel
    # reduceat in TraceItem so expensive.  `buffer_time` is scaled down toward
    # this budget as channels x rate rises.
    #
    # This is a budget, not a hard cap: audioio's `_buffer_position()` expands
    # the buffer by up to half again when the requested range sits near the
    # start or the end of the file, so the real peak is ~1.5x.  Measured on
    # the 16 channel file: 69.7 MB nominal, 93.7 MB at the worst scroll
    # position -- against 153.6 MB flat before.
    buffer_bytes = 64 * 1024 * 1024
    min_buffer_time = 10.0
    max_buffer_time = 60.0

    def __init__(self, file_path, **kwargs):
        self.buffer_time = Data.max_buffer_time
        self.back_time = Data.max_buffer_time / 3
        self.follow_time = 0
        self.file_path = file_path
        self.load_kwargs = kwargs
        self.data = None
        self.rate = None
        self.channels = 0
        self.frames = 0
        self.start_time = None
        self.meta_data = {}
        self.tbefore = 0
        self.tafter = 0
        self.traces = []
        self.sources = []
        #: what open() noticed but did not act on, for the browser to surface
        self.load_warnings = []

    def add_trace(self, trace):
        self.traces.append(trace)

    def remove_trace(self, name):
        t = self[name]
        if t is not None:
            i = self.traces.index(t)
            del self.traces[i]

    def clear_traces(self):
        self.traces = []

    # Kept for the same reason CompressedData.__del__ is: no Qt, no shiboken
    # hazard, and it is the last backstop that releases the file handle for a
    # Data dropped without going through DataBrowser.shutdown.
    def __del__(self):
        self.close()

    def __len__(self):
        return len(self.traces)

    def __getitem__(self, key):
        for trace in self.traces:
            if trace.name.lower() == key.lower():
                return trace
        return None

    def __contains__(self, key):
        for trace in self.traces:
            if trace.name.lower() == key.lower():
                return True
        return False

    def keys(self):
        return [trace.name for trace in self.traces]

    def get_trace_names(self, class_name):
        traces = []
        for trace in self.traces:
            if isinstance(trace, class_name):
                traces.append(trace.name)
        return traces

    def is_visible(self, name):
        """Is any channel of trace `name` currently drawn?

        Answered from the trace's own `visible_channels` flags, which the
        plot items keep up to date -- the data layer never holds a widget.
        Making a trace visible is therefore the *browser's* job, not this
        object's; see `DataBrowser.set_trace_visible`.
        """
        if name not in self:
            return False
        # `self["data"]` is the raw DataLoader, which carries the flags but
        # not BufferedData's accessors, so read the array directly.
        flags = getattr(self[name], "visible_channels", None)
        return flags is not None and bool(flags.any())

    def get_region(self, t0, t1, channel):
        traces = {}
        for t in self.traces:
            i0 = int(t0 * t.rate)
            if i0 < 0:
                i0 = 0
            i1 = int(t1 * t.rate) + 1
            if i1 > len(t):
                i1 = len(t)
            time = np.arange(i0, i1) / t.rate
            data = t[i0:i1, channel]
            if isinstance(t, BufferedSpectrogram):
                freqs = t.frequencies
                traces[t.name] = (time, freqs, data)
            else:
                traces[t.name] = (time, data)
        return traces

    def setup_traces(self):
        """order trace sequence."""
        traces = []
        self.sources = []
        i = -1
        while i < len(traces):
            sname = traces[i].name if i >= 0 else "data"
            dtraces = []
            for k in range(len(self.traces)):
                if self.traces[k] is not None and self.traces[k].source_name is sname:
                    dtraces.append(self.traces[k])
                    self.traces[k] = None
            for t in reversed(dtraces):
                traces.insert(i + 1, t)
                self.sources.insert(i + 1, i)
            i += 1
        if len(traces) < len(self.traces):
            for trace in self.traces:
                if trace is not None:
                    print(
                        f'! ERROR: source "{trace.source_name}" for trace "{trace.name}" not found!'
                    )
            print("! the following sources are available:")
            print("  data")
            for source in traces:
                print(f"  {source.name}")
        self.traces = traces

    def scale_buffer_time(self, rate: float, channels: int) -> None:
        """Shrink `buffer_time` so one raw buffer fits the byte budget.

        The raw buffer is float64, so `buffer_time*rate*channels*8` bytes.
        Small files keep the full `max_buffer_time`; a 16 channel 20 kHz
        recording drops from 60 s (153.6 MB) to a bit over 25 s.
        """
        if rate <= 0 or channels <= 0:
            return
        budget = Data.buffer_bytes / (rate * channels * 8)
        self.buffer_time = min(Data.max_buffer_time, max(Data.min_buffer_time, budget))
        self.back_time = self.buffer_time / 3

    def open(self, unwrap, unwrap_clip):
        if self.data is not None:
            self.data.close()
        # expand buffer times:
        self.tbefore = 0
        self.tafter = 0
        tbefore = [0] * len(self.traces)
        tafter = [0] * len(self.traces)
        for k in reversed(range(len(self.traces))):
            tb, ta = self.traces[k].expand_times(tbefore[k], tafter[k])
            i = self.sources[k]
            if i < 0:
                self.tbefore = max(self.tbefore, tb)
                self.tafter = max(self.tafter, ta)
            else:
                tbefore[i] = max(tbefore[i], tb)
                tafter[i] = max(tafter[i], ta)
        # raw data:
        tbuffer = self.buffer_time + self.tbefore + self.tafter
        tback = self.back_time + self.tbefore
        verbose = isinstance(self.file_path, (list, tuple, np.ndarray))
        self.load_warnings = []
        try:
            self.data = open_files(
                self.file_path, tbuffer, tback, verbose=verbose, **self.load_kwargs
            )
        except Exception as e:
            self.data = None
            if isinstance(self.file_path, (list, tuple, np.ndarray)):
                self.file_path = self.file_path[0]
            raise e
        for index, path, expected, actual in join_gaps(self.data):
            # reported, never acted on: see open_files()
            self.load_warnings.append(
                f"{os.path.basename(path)} starts {actual - expected:+.3f} s "
                f"from where the previous file ends -- the recording may not be "
                f"continuous across this join"
            )
            log.warning("%s", self.load_warnings[-1])
        # Rate and channel count are only known now, so size the buffer here.
        # Nothing is allocated until the first update_buffer(), so rewriting
        # bufferframes/backframes at this point is safe.
        self.scale_buffer_time(self.data.rate, self.data.channels)
        self.data.bufferframes = int(
            (self.buffer_time + self.tbefore + self.tafter) * self.data.rate
        )
        self.data.backframes = int((self.back_time + self.tbefore) * self.data.rate)
        self.data.set_unwrap(unwrap, unwrap_clip, False, self.data.unit)
        self.data.follow = int(self.follow_time * self.data.rate)
        self.data.name = "data"
        self.data.panel = "trace"
        self.data.panel_type = "trace"
        self.data.visible_channels = np.zeros(self.data.channels, dtype=bool)
        self.data.color = theme.trace_color("raw")
        # lw_thin MUST stay <= 1.0: Qt's raster engine has a fast path for
        # 1 pixel lines, width 1.1 falls back to QStroker.  Measured on the
        # 16 channel file: 28.3 ms vs 4.4 ms per repaint (and 908 ms vs
        # 5.4 ms with antialiasing on).  This is the single largest
        # wall-clock item in the app; do not "restore" 1.1 for looks.
        self.data.lw_thin = theme.LW_THIN
        self.data.lw_thick = theme.LW_THICK
        self.data.dests = []
        self.data.need_update = False
        count_buffer_loads(self.data)
        self.data.mip_pyramid = MinMaxPyramid()
        self.traces.insert(0, self.data)
        self.sources = [None] + [i + 1 for i in self.sources]
        self.file_path = self.data.filepath
        self.rate = self.data.rate
        self.channels = self.data.channels
        self.frames = self.data.frames
        # metadata:
        self.meta_data = dict(Format=self.data.format_dict())
        self.meta_data.update(self.data.metadata())
        self.start_time = get_datetime(self.meta_data)
        # derived data:
        for trace, source in zip(self.traces[1:], self.sources[1:]):
            trace.open(self.traces[source])
        self.set_need_update()

    def close(self):
        if self.data is not None:
            self.data.close()
            self.data = None

    def set_need_update(self):
        if self.data is None:
            return
        self.data.need_update = bool(self.data.visible_channels.any())
        for d in self.data.dests:
            d.set_need_update()

    def update_times(self, t0, t1):
        """Move every buffer to cover `[t0, t1]`.

        Everything this touches is shifted **in place**:
        `BufferedArray._recycle_buffer` moves the surviving part of the
        loader's own array down over itself, and `align_buffer` does the
        same for each derived trace.  A compute worker reading those arrays
        would see a torn buffer, so the caller must have joined the workers
        first -- see `DataBrowser.set_times`.
        """
        if self.data.need_update:
            self.data.update_time(t0 - self.tbefore, t1 + self.tafter)
        for trace in self.traces[1:]:
            if trace.need_update:
                trace.align_buffer()
        i0 = int(t0 * self.data.rate)
        if i0 >= self.data.frames:
            i0 = self.data.frames - 1
        fp, _ = self.data.get_file_index(i0)
        return self.data.basename(fp)
