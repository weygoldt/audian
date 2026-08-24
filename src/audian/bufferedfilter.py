"""Filter data on the fly."""

from scipy.signal import butter, sosfilt

from . import theme
from .buffereddata import BufferedData


class BufferedFilter(BufferedData):
    # A 2nd order Butterworth settles in a few hundred milliseconds, so this
    # is all the warm-up the filter needs.  It used to be 10 s, which was
    # never actually prepended (see BufferedData.load_buffer) but was paid
    # for in raw buffer size.
    warmup_time = 0.5

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

    def open(self, source):
        super().open(source)
        self.highpass_cutoff = 0
        self.lowpass_cutoff = self.rate / 2
        self.filter_order = 2
        self.sos = None
        self.update()

    def process(self, source, dest, nbefore):
        if self.sos is None:
            dest[:, :] = source[nbefore:, :]
        else:
            # Filter all channels in one call: the per-channel Python loop
            # built 16 temporaries and cost 258.5 ms where axis=0 costs
            # 225.3 ms on the same 16 channel buffer.
            dest[:] = sosfilt(self.sos, source, axis=0)[nbefore:]

    def update(self):
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
        self.recompute_all()
