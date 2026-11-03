"""Filter data on the fly."""

from math import floor

import numpy as np

from scipy.signal import butter, sosfilt

from . import theme
from .buffereddata import CHUNK_BYTES, BufferedData, TransformPool, chunk_frames
from .clicks import ClickRemoval, clean_blocks
from .tasks.tokens import NEVER


class BufferedFilter(BufferedData):
    # A 2nd order Butterworth settles in a few hundred milliseconds, so this
    # is all the warm-up the filter needs.  It used to be 10 s, which was
    # never actually prepended (see BufferedData.load_buffer) but was paid
    # for in raw buffer size.
    warmup_time = 0.5

    #: Source bytes filtered per chunk; see `buffereddata.CHUNK_BYTES`.
    chunk_bytes = CHUNK_BYTES

    def __init__(
        self,
        name="filtered",
        source="data",
        panel="trace",
        color=None,
        lw_thin=None,
        lw_thick=None,
    ):
        if color is None:
            color = theme.trace_color("filtered")
        super().__init__(
            name,
            source,
            tbefore=BufferedFilter.warmup_time,
            panel=panel,
            panel_type="trace",
            color=color,
            lw_thin=lw_thin,
            lw_thick=lw_thick,
        )
        self.highpass_cutoff = 0
        self.lowpass_cutoff = 1
        self.filter_order = 2
        self.sos = None
        #: Repair clicks before the band filter; see `audian.clicks`.  Set
        #: through `set_click_removal`, which also claims the source the
        #: blocks are computed from.  Kept across `open`, unlike the
        #: cutoffs, because `Data.open` sizes the raw buffer from it first.
        self.remove_clicks = False
        self.click_threshold = ClickRemoval.threshold

    def open(self, source):
        super().open(source)
        self.highpass_cutoff = 0
        self.lowpass_cutoff = self.rate / 2
        self.filter_order = 2
        self.sos = None
        self.update()

    #: what a click is a multiple of the threshold to last for: wavetracker's
    #: 8 standard deviations for its 12
    click_extent = 8.0 / 12.0

    def click_config(self) -> ClickRemoval:
        """What `click_threshold` makes of the click removal settings."""
        threshold = float(self.click_threshold)
        return ClickRemoval(
            threshold=threshold, extent_threshold=threshold * self.click_extent
        )

    @staticmethod
    def click_reserve() -> float:
        """Source [s] click removal reads on each side of what it cleans.

        The block a frame is in and the margin beyond it: the worst case is
        a frame just short of a block's end, see `clicks.clean_blocks`.  One
        hundredth of a second more, so that rounding the block and the
        margin to frames can never come up a frame short.
        """
        cfg = ClickRemoval()
        return cfg.block + cfg.margin + 0.01

    def set_click_removal(self, on: bool) -> None:
        """Repair clicks or not, and claim the source that needs.

        The margins only grow while it is on: at 16 channels and 48 kHz
        they are another 5 s of raw buffer, which a jump reads from the
        file every time -- measured on iriri01-grid01-dev03 (WavPack), 11 s
        in 140 ms against 16 s in 203 ms.  `Data.update_margins` has to be
        run after this, before the next buffer move.
        """
        self.remove_clicks = bool(on)
        reserve = self.click_reserve() if self.remove_clicks else 0.0
        self.source_tbefore = BufferedFilter.warmup_time + reserve
        self.source_tafter = reserve

    def process(self, source, dest, nbefore, cancel=NEVER, progress=None, start=None):
        """Filter `source` into `dest`, in interruptible chunks.

        All channels go through one `sosfilt` call: the per-channel Python
        loop built 16 temporaries and cost 258.5 ms where `axis=0` costs
        225.3 ms on the same 16 channel buffer.

        The chunking carries the filter state `zi` across the seams, which
        is what makes the result **bit-identical** to filtering the whole
        buffer in one call -- verified to `max abs diff 0.000e+00` at three
        chunk sizes in `tests/test_chunked_dsp.py`.  Drop the `zi` and every
        seam grows a fresh filter transient, which looks like data rather
        than like a bug; that is what the test is there to stop.

        With click removal on, the chunks are the blocks `clean_blocks`
        repairs, fixed in absolute time -- `start` is the frame of the
        recording `source[0]` is, which is what lets a block come out the
        same however the buffer around it was loaded -- and the same `zi`
        carries the band filter across them.  Click removal reads all of
        `source`; the band filter only the last `warmup_time` of the warm-up,
        so that the reserve click removal claims does not change what the
        filter produces.
        """
        clicks = self.remove_clicks
        sos = self.sos
        if sos is None and not clicks:
            dest[:, :] = source[nbefore:, :]
            return None
        nsource = len(source)
        ndest = len(dest)
        warm = floor(BufferedFilter.warmup_time * self.source.rate)
        skip = max(0, nbefore - warm)
        if clicks:
            pool = TransformPool.get()
            pieces = clean_blocks(
                source,
                0 if start is None else start,
                self.source.rate,
                self.click_config(),
                lo=skip,
                check=cancel.check,
                pool=pool,
                batch=TransformPool.threads if pool is not None else 1,
            )
        else:
            pieces = self._chunks(source, skip)
        if sos is not None:
            zi = np.zeros((sos.shape[0], 2) + source.shape[1:])
        written = 0
        for i, j, block in pieces:
            cancel.check()
            if sos is not None:
                block, zi = sosfilt(sos, block, axis=0, zi=zi)
            lo = max(i, nbefore)
            hi = min(j, nbefore + ndest)
            if hi > lo:
                dest[lo - nbefore : hi - nbefore] = block[lo - i : hi - i]
                written = hi - nbefore
            if progress is not None:
                progress(j / nsource)
            if j >= nbefore + ndest:
                # what is left is look-ahead for others; nothing of `dest`
                break
        if written < ndest:
            dest[written:] = 0
        return None

    def _chunks(self, source, i):
        """`source[i:]` in chunks of `chunk_bytes`, as `(i, j, chunk)`."""
        chunk = chunk_frames(source, self.chunk_bytes)
        while i < len(source):
            j = min(len(source), i + chunk)
            yield i, j, source[i:j]
            i = j

    def prepare_update(self) -> bool:
        if (
            self.highpass_cutoff < 0.001 * self.rate / 2
            and self.lowpass_cutoff >= self.rate / 2 - 1e-8
        ):
            self.sos = None
        elif self.highpass_cutoff < 0.001 * self.rate / 2:
            self.sos = butter(
                self.filter_order,
                self.lowpass_cutoff,
                "lowpass",
                fs=self.rate,
                output="sos",
            )
        elif self.lowpass_cutoff >= self.rate / 2 - 1e-8:
            self.sos = butter(
                self.filter_order,
                self.highpass_cutoff,
                "highpass",
                fs=self.rate,
                output="sos",
            )
        else:
            self.sos = butter(
                self.filter_order,
                (self.highpass_cutoff, self.lowpass_cutoff),
                "bandpass",
                fs=self.rate,
                output="sos",
            )
        return True
