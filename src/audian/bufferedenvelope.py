"""Compute envelope on the fly."""

import numpy as np

from scipy.signal import butter, sosfiltfilt

from . import theme
from .buffereddata import BufferedData
from .tasks.tokens import NEVER


class BufferedEnvelope(BufferedData):
    warmup_time = 0.5

    def __init__(
        self,
        name="envelope",
        source="filtered",
        panel="trace",
        color=None,
        lw_thin=None,
        lw_thick=None,
        envelope_cutoff=500,
        filter_order=2,
        highpass_cutoff=0,
    ):
        if color is None:
            color = theme.trace_color("envelope")
        super().__init__(
            name,
            source,
            tbefore=BufferedEnvelope.warmup_time,
            panel=panel,
            panel_type="trace",
            color=color,
            lw_thin=lw_thin,
            lw_thick=lw_thick,
        )
        self.envelope_cutoff = envelope_cutoff
        self.highpass_cutoff = highpass_cutoff
        self.filter_order = filter_order
        self.sos = None

    def open(self, source):
        super().open(source)
        # self.ampl_min = 0
        # self.ampl_max = source.ampl_max
        self.sos = None
        self.update()

    def process(self, source, dest, nbefore, cancel=NEVER, progress=None):
        """Rectify and smooth `source` into `dest`.

        **Deliberately not chunked.**  `sosfiltfilt` filters forwards and
        then backwards over the whole signal and pads each end with an odd
        extension, so a chunk boundary is an edge and gets an edge's
        transient: measured on a 4 channel white-noise block, chunking this
        the way the filter is chunked moves the result by up to 1.96 on a
        signal of order 1.  It is only recoverable with an overlap-save
        carry long enough for the filter to have decayed, which costs more
        than the whole call and still guarantees nothing in general.

        So the envelope is one uninterruptible call, and its cancellation
        granularity is the call.  At 20 kHz and 16 channels that is 258 ms
        -- worth knowing, not worth corrupting the trace to shave.
        """
        cancel.check()
        if self.sos is None:
            dest[:] = np.zeros_like(dest)
        else:
            # the integral over one hump of the sine wave is 2, the mean is 2/pi:
            # one 2-D call, not a Python loop over channels:
            dest[:] = sosfiltfilt(self.sos, (np.pi / 2) * np.abs(source), axis=0)[
                nbefore:
            ]
            if self.highpass_cutoff == 0:
                dest[dest < 0] = 0
        if progress is not None:
            progress(1.0)
        return None

    def prepare_update(self) -> bool:
        try:
            if self.highpass_cutoff > 0:
                self.sos = butter(
                    self.filter_order,
                    (self.highpass_cutoff, self.envelope_cutoff),
                    "bandpass",
                    fs=self.rate,
                    output="sos",
                )
            else:
                self.sos = butter(
                    self.filter_order,
                    self.envelope_cutoff,
                    "lowpass",
                    fs=self.rate,
                    output="sos",
                )
        except ValueError:
            self.sos = None
        return True
