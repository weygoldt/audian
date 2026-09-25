"""Region analysis: what a selection puts in the results table, and on disk.

Until this module the path had no test at all.  It pins the table the two
built-in analyzers fill, the clamping and error isolation in
`DataBrowser.analyze_region`, and the CSV `save_analysis` writes.  The
analyzer *event* API (`make_trace_events` and friends) is left untested on
purpose: it is on the decided-deletions list and nothing calls it.

    .venv/bin/python -m pytest tests/test_analyzer.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_panelsplitter import FRAMES, RATE, open_stack  # noqa: E402

#: Amplitude of the 1 kHz tone on each channel.  1 kHz is well inside the
#: default filter's pass band, so the filtered trace's standard deviation
#: is the tone's RMS, amplitude / sqrt(2).
AMPLITUDES = (0.3, 0.05)


@pytest.fixture
def browser(app, tmp_path):
    t = np.arange(FRAMES) / RATE
    tone = np.sin(2 * np.pi * 1000.0 * t)
    signal = np.stack([a * tone for a in AMPLITUDES], axis=1).astype(np.float32)
    yield from open_stack(app, tmp_path, len(AMPLITUDES), signal)


@pytest.fixture
def save_to(browser, tmp_path, monkeypatch):
    """Answer the save dialog with a path under the test's directory."""
    from audian import databrowser

    path = tmp_path / "analysis.csv"
    monkeypatch.setattr(
        databrowser.QFileDialog,
        "getSaveFileName",
        staticmethod(lambda *args, **kwargs: (str(path), "")),
    )
    return path


def rms(channel):
    return AMPLITUDES[channel] / np.sqrt(2)


def test_a_region_adds_one_row_holding_every_analyzers_columns(browser):
    browser.analyze_region(1.0, 2.5, 1)
    (row,) = browser.get_analysis_table()
    assert {k: row[k] for k in ("tstart/s", "tend/s", "duration/s", "channel")} == {
        "tstart/s": 1.0,
        "tend/s": 2.5,
        "duration/s": 1.5,
        "channel": 1,
    }
    assert row["filtered stdev/a.u."] == pytest.approx(rms(1), rel=1e-3)
    assert abs(row["filtered mean/a.u."]) < 1e-3


def test_each_region_is_measured_on_its_own_channel(browser):
    browser.analyze_region(0.5, 1.0, 0)
    browser.analyze_region(0.5, 1.0, 1)
    first, second = browser.get_analysis_table()
    assert first["filtered stdev/a.u."] == pytest.approx(rms(0), rel=1e-3)
    assert second["filtered stdev/a.u."] == pytest.approx(rms(1), rel=1e-3)


def test_a_region_past_either_end_is_clamped_to_the_recording(browser):
    browser.analyze_region(-1.0, 10.0, 0)
    (row,) = browser.get_analysis_table()
    assert row["tstart/s"] == 0
    assert row["tend/s"] == FRAMES / RATE


def test_a_raising_analyzer_costs_its_own_row_and_nothing_else(browser):
    from PySide6.QtWidgets import QApplication

    from audian.analyzer import Analyzer

    class Broken(Analyzer):
        def __init__(self, browser):
            super().__init__(browser, "broken", "data")
            self.make_column("never", "", "%g")

        def analyze(self, t0, t1, channel, traces):
            raise RuntimeError("on purpose")

    Broken(browser)
    browser.analyze_region(1.0, 2.0, 0)
    (row,) = browser.get_analysis_table()
    assert row["duration/s"] == 1.0
    assert row["filtered stdev/a.u."] == pytest.approx(rms(0), rel=1e-3)
    assert "never" not in row
    assert QApplication.overrideCursor() is None


def test_statistics_on_a_missing_trace_stays_registered_but_inert(browser):
    """Characterisation, not a specification.

    `StatisticsAnalyzer.__init__` returns early when its source trace is
    missing, *after* `Analyzer.__init__` has already registered it with the
    browser.  So it sits in `browser.analyzers` with no columns and never
    stores anything.  That is what it does today; whether it should
    register at all is an open question, and this test is here so that
    answering it is a deliberate change.
    """
    from audian.statisticsanalyzer import StatisticsAnalyzer

    ghost = StatisticsAnalyzer(browser, "no such trace")
    assert ghost in browser.analyzers
    assert ghost.source is None
    assert ghost.data.columns() == 0
    browser.analyze_region(1.0, 2.0, 0)
    assert ghost.data.rows() == 0
    (row,) = browser.get_analysis_table()
    assert not any(key.startswith("no such trace") for key in row)


def test_the_results_dialog_shows_each_column_in_its_own_format(browser):
    browser.analyze_region(1.0, 2.5, 1)
    table = browser.analysis_table
    assert table is not None
    headers = [table.horizontalHeaderItem(c).text() for c in range(table.columnCount())]
    assert headers[:4] == ["tstart/s", "tend/s", "duration/s", "channel"]
    # 8 kHz resolves 125 us, so PlainAnalyzer formats times to 3 decimals.
    assert [table.item(0, c).text() for c in range(4)] == ["1.000", "2.500", "1.500", "1"]


def test_saving_writes_every_analyzers_columns_once(browser, save_to):
    browser.analyze_region(1.0, 2.5, 1)
    browser.save_analysis()
    header, row = save_to.read_text().splitlines()
    assert header.split(";") == [
        "tstart/s",
        "tend/s",
        "duration/s",
        "channel",
        "filtered mean/a.u.",
        "filtered stdev/a.u.",
    ]
    assert row.split(";")[:4] == ["1.000", "2.500", "1.500", "1"]
    assert float(row.split(";")[5]) == pytest.approx(rms(1), rel=1e-3)

