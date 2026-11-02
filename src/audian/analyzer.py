"""Analyze a selected region.

- class `Analyzer`: Base class for analyzing selected regions.
- class `PlainAnalyzer`: Implementation of an Analyzer that stores the analysis window into the table.
"""

import pyqtgraph as pg

from math import floor, log10
from typing import Optional
from thunderlab.tabledata import TableData

from . import theme


def style_result_table(table: pg.TableWidget) -> None:
    """Put a table of analysis results into the mono numeric face.

    Every number a user compares column-wise needs aligning digits.
    """
    table.setFont(theme.font_mono())
    table.setMinimumHeight(10 * theme.TOOLBAR_HEIGHT)
    header = table.horizontalHeader()
    if header is not None:
        header.setFont(theme.font_ui(theme.SIZE_SMALL_PT))
    vheader = table.verticalHeader()
    if vheader is not None:
        vheader.setFont(theme.font_mono(theme.SIZE_SMALL_PT))


class Analyzer(object):
    """Base class for analyzing selected regions.

    When selecting a region on a trace of some channel, you can do
    something with it by implementing a class derived from Analyzer.
    An Analyzer may compute some statistics, some numbers, from the
    selected data and store it in a table that can later on be saved
    to a file. Or it opens a new window where it displays whatever you
    want based on the selected data.

    Classes inheriting the `Analyzer` class need to reimplement the
    `analyze()` function. Their constructor takes an instance of the
    DataBrowser as the only argument. See class `PlainAnalyzer` as an
    example.

    The constructor might add columns to the table where analysis results
    are stored (`make_column()` function).

    The `analyze()` function then stores analysis results into the
    table (`store()` function).

    Or you open a new window using, for example, a QDialog with
    self.browser as parent.

    Parameters
    ----------
    browser: DataBrowser
        Instance of the data browser.
    name: str
        Name of the analyzer implementation.
    source_name: str
        Source trace on which the analyzer will work on.

    Attributes
    ----------
    browser: DataBrowser
        Instance of the data browser.
    name: str
        Name of the analyzer implementation.
    source_name: str
        Source trace on which the analyzer will work on.
    source: BufferedData
        Trace on which the analyzer will work on.
        You rarely need to access the full source trace, since
        `analyze()` provides all the traces of the selected region.
    data: thunderlab.TableData
        The table storing the analysis results.

    Methods
    -------
    - `analyze()`: Analysis function.
    - `traces()`: Names of all available data traces.
    - `trace()`: Full data trace of a given name
    - `make_column()`: Make a column for the table collecting the analysis results.
    - `store()`: Store analysis results in table.

    """

    def __init__(self, browser, name: str, source_name: str):
        self.browser = browser
        self.name = name
        self.source_name = source_name
        self.source = self.trace(self.source_name)
        self.data = TableData()
        self.browser.add_analyzer(self)

    def clear(self):
        """Clear the data table."""
        self.data.clear_data()

    def analyze(self, t0, t1, channel, traces):
        """Analysis function.

        This function is called whenever a region is selected for
        analysis. Reimplement it for your purposes.

        Parameters
        ----------
        t0: float
            Start time of the selected region.
        t1: float
            End time of the selected region.
        channel: int
            Channel of the selected region.
        traces: dict of arrays
            Dictionary with all data traces from `channel`
            cut out between `t0` and `t1`.
            Keys are the names of the data traces.
        """
        pass

    def traces(self):
        """Names of all available data traces.

        Returns
        -------
        traces: list of str
            Names of all available data traces.
        """
        return self.browser.data.keys()

    def trace(self, name: str) -> Optional[object]:
        """Full data trace of a given name.

        Parameters
        ----------
        name: str
            Name of the requested data trace.

        Returns
        -------
        trace: BufferedData or None
            Full data trace or None if name was not found.
        """
        if name in self.browser.data:
            return self.browser.data[name]
        else:
            return None

    def make_column(self, label, unit=None, formats=None):
        """Make a column for the table collecting the analysis results.

        Analysis results are stored in a table. Each Analyzer can add
        columns to this table in its constructor using this function.

        Parameters
        ----------
        label: str
            Label of the column.
        unit: str
            Unit of the values stored in this column.
        formats: str
            Format string for the values stored in this column.
        """
        self.data.append(label, unit, formats)

    def store(self, *args):
        """Store analysis results in table.

        Call this function once in `analyze()` with as many arguments
        as columns generated using `make_column()`.

        Parameters
        ----------
        *args: float, int, or str
            Values to be stored in the table.
            As many values as columns generated using `make_column()`.

        """
        self.data.add(args, 0)


class PlainAnalyzer(Analyzer):
    """Implementation of an Analyzer that stores the analysis window into the table.

    This analyzer is name 'plain' and simply stores start and end time
    of the slected region, the duration of the selected time interval,
    and the channel into the table.

    Parameters
    ----------
    browser: DataBrowser
        Instance of the data browser.

    Methods
    -------
    - `analyze()`: Analysis function.

    """

    def __init__(self, browser):
        super().__init__(browser, "plain", "data")
        nd = int(floor(-log10(1 / self.source.rate)))
        if nd < 0:
            nd = 0
        self.make_column("tstart", "s", f"%.{nd}f")
        self.make_column("tend", "s", f"%.{nd}f")
        self.make_column("duration", "s", f"%.{nd}f")
        self.make_column("channel", "", "%.0f")

    def analyze(self, t0, t1, channel, traces):
        """Analyze the selected region."""
        self.store(t0, t1, t1 - t0, channel)
