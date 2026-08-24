import numpy as np

from .analyzer import Analyzer


class StatisticsAnalyzer(Analyzer):
    """Mean and standard deviation of the selected region."""

    def __init__(self, browser, source_name: str = "filtered"):
        super().__init__(browser, "statistics", source_name)
        if self.source is None:
            # the trace this analyzer works on is not installed - stay
            # registered but inert instead of crashing the browser:
            return
        nd = int(-np.floor(np.log10(self.source.ampl_max / 4e4)))
        if nd < 0:
            nd = 0
        us = self.source.unit
        self.make_column(f"{self.source_name} mean", us, f"%.{nd}f")
        self.make_column(f"{self.source_name} stdev", us, f"%.{nd}f")

    def analyze(self, t0: float, t1: float, channel: int, traces) -> None:
        if self.source is None or self.source_name not in traces:
            return
        source = traces[self.source_name][1]
        self.store(np.mean(source), np.std(source))
