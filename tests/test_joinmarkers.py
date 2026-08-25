"""Tests for the rules that mark where a recording's files butt together.

Runs offscreen::

    QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_joinmarkers.py -q

A join is a fact about the FILES, not about the log: exp3 is four WAVs opened
as one recording, and it has three joins whether or not a bundle was ever
fitted to it.  So the position of every rule comes from the loader
(``start_indices``) and never from a bundle; a bundle only ever *annotates* a
rule with the gap its writer measured there (exp3 declares +32 ms, +32 ms,
-120 ms, and 120 ms is about thirty pulses of a 4 ms volley interval).  Nothing
here corrects for a gap or moves a mark.

The browser under test is a `DataBrowser` with only the three things a join
rule is made of -- a loader, some lanes and a navigator -- because building a
sixteen channel window to place three vertical lines would test the layout
rather than the lines.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

import pyqtgraph as pg  # noqa: E402
from PyQt5.QtCore import Qt  # noqa: E402
from PyQt5.QtWidgets import QApplication, QWidget  # noqa: E402

import audian.audian as audian_app  # noqa: E402
from audian.databrowser import DataBrowser  # noqa: E402
from audian.eventoverlay import FILL_Z, AnnotationLayer  # noqa: E402

sys.path.insert(0, str(REPO / "tests"))
from test_session import pulse, trial, write_bundle  # noqa: E402


RATE = 48000.0


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def scratch_settings(tmp_path, monkeypatch):
    """Point the settings file at the sandbox, never at the real one.

    `audian.settings_path` resolves through platformdirs at import, so no
    environment variable isolates it: a test that toggles a layer and does
    not redirect this writes the user's own preferences.

    The queued write is drained here rather than left to the next test.
    `schedule_annotation_save` posts a zero timer, and a timer that only
    fires once the patch has been undone lands in the real file after all --
    which is exactly how ~/.config/audian/settings.json got clobbered once
    already.
    """
    path = tmp_path / "settings.json"
    monkeypatch.setattr(audian_app, "settings_path", lambda: path)
    yield path
    running = QApplication.instance()
    if running is not None:
        running.processEvents()


class FakePanel:
    """One row of the stack: what `attach_join_markers` asks a panel for."""

    def __init__(self, axs, trace: bool):
        self.axs = axs
        self._trace = trace

    def is_trace(self) -> bool:
        return self._trace


class JoinBrowser(DataBrowser):
    """A `DataBrowser` with only its join-marker half constructed.

    `DataBrowser.__init__` opens a recording and builds fifty plots.  A join
    rule needs a loader that says where the files start, some lanes to draw
    in and a navigator, so those are what this supplies -- and every method
    under test is then the real one, off the real class.
    """

    def __init__(self, starts, channels=2, navigator_rows=2):
        QWidget.__init__(self)
        self.said = []
        self.data = SimpleNamespace(
            file_path=Path("a.wav"),
            data=SimpleNamespace(
                start_indices=list(starts),
                rate=RATE,
                frames=int(starts[-1]) + int(RATE),
                channels=channels,
            ),
        )
        self.annotations = AnnotationLayer(self)
        self.annotation_layers_before_solo = None
        self.current_channel = 0
        self.join_markers = []
        self.join_labels = []
        self.lanes = [pg.PlotItem() for _ in range(channels)]
        self.specs = [pg.PlotItem() for _ in range(channels)]
        self.panels = {
            "trace": FakePanel(self.lanes, True),
            "spectrogram": FakePanel(self.specs, False),
        }
        self.datafig = SimpleNamespace(
            axs=[pg.PlotItem() for _ in range(navigator_rows)]
        )

    def __del__(self):
        # DataBrowser.__del__ closes a recording this browser never opened
        pass

    def notify(self, level, message):
        self.said.append((level, message))


def joins_of(browser, ax):
    """The join rules drawn in one plot, in the order they were added."""
    return [i for i in browser.join_markers if i.getViewBox() is ax.getViewBox()]


def positions(browser, ax):
    return [round(i.value(), 6) for i in joins_of(browser, ax)]


@pytest.fixture
def split(app):
    """Three files of one second each: joins at 1.0 s and 2.0 s."""
    browser = JoinBrowser([0, int(RATE), int(2 * RATE)])
    browser.attach_join_markers()
    return browser


def split_bundle(directory: Path, gaps: str, files: str, frames: str) -> Path:
    return write_bundle(
        directory,
        session_id="SPLIT",
        alignment={
            "recording_file": None,
            "recording_files": files,
            "recording_file_frames": frames,
            "recording_join_gaps_s": gaps,
            "recording_rate_hz": str(int(RATE)),
            "recording_frames": str(int(3 * RATE)),
        },
        pulses=[pulse(0.5)],
        trials=[trial(1, "volley", 0.4, 0.6, 1)],
    )


# --- where the rules are ----------------------------------------------------


def test_every_join_the_loader_reports_gets_one_rule_in_every_lane(split):
    for ax in split.lanes:
        assert positions(split, ax) == [1.0, 2.0]


def test_the_navigator_carries_the_joins_too(split):
    """The whole session is in view there, which is where a join matters."""
    for ax in split.datafig.axs:
        assert positions(split, ax) == [1.0, 2.0]


def test_the_spectrogram_gets_no_rule(split):
    """`SpecItem` is an opaque image at z=0.

    A rule below the annotations would not be composited under it at all, and
    one above them would read as an event rather than as chrome.
    """
    for ax in split.specs:
        assert joins_of(split, ax) == []


def test_a_single_file_recording_has_nothing_to_mark(app):
    browser = JoinBrowser([0])
    browser.attach_join_markers()
    assert browser.recording_joins() == []
    assert browser.join_markers == []


def test_a_rule_sits_below_every_annotation_and_never_takes_the_mouse(split):
    """Chrome, not data: a mark is never obscured by one, and a click on the
    lane is a click on the lane."""
    for line in split.join_markers:
        assert line.zValue() < FILL_Z
        assert line.acceptedMouseButtons() == Qt.NoButton
        assert not line.acceptHoverEvents()


# --- what a bundle may and may not say about them ---------------------------


def test_a_bundle_never_moves_a_rule(app, split, tmp_path, scratch_settings):
    """The bundle's own join arithmetic disagrees here on purpose.

    Its `recording_file_frames` put the joins at 2 s and 4 s; the loader
    opened files that butt together at 1 s and 2 s.  What is on screen is
    what the loader read, always -- a viewer that positioned a rule from an
    alignment file would be drawing a claim about the files out of a claim
    about the log.
    """
    metadata = split_bundle(
        tmp_path / "moved",
        gaps="[0.032, -0.12]",
        files='["a.wav", "b.wav", "c.wav"]',
        frames=f"[{int(2 * RATE)}, {int(2 * RATE)}, {int(2 * RATE)}]",
    )
    split.annotations.load(metadata, Path("a.wav"))
    split.update_join_markers()
    assert split.annotations.bundle.meta.alignment.join_times_s == (2.0, 4.0)
    for ax in split.lanes:
        assert positions(split, ax) == [1.0, 2.0]


def test_a_declared_gap_labels_the_rule_on_the_lane_being_read(
    app, split, tmp_path, scratch_settings
):
    """One label per join, not one per join per lane.

    Three joins in a sixteen lane stack are forty-eight labels all saying the
    same thing about the recording, so the label follows the lane the reader
    is on, the way its frame and its bold caption already do.
    """
    metadata = split_bundle(
        tmp_path / "gaps",
        gaps="[0.032, -0.12]",
        files='["a.wav", "b.wav", "c.wav"]',
        frames=f"[{int(RATE)}, {int(RATE)}, {int(RATE)}]",
    )
    split.annotations.load(metadata, Path("a.wav"))
    split.update_join_markers()

    # the rendered text, not just the format string: pyqtgraph drops a
    # setFormat on a hidden label without a word, and an empty label on
    # screen is exactly what that looks like
    shown = [
        (channel, label.toPlainText())
        for channel, _which, label in split.join_labels
        if label.isVisible()
    ]
    assert shown == [(0, "+32.0 ms"), (0, "-120.0 ms")]

    split.current_channel = 1
    split.update_join_markers()
    shown = [
        (channel, label.toPlainText())
        for channel, _which, label in split.join_labels
        if label.isVisible()
    ]
    assert shown == [(1, "+32.0 ms"), (1, "-120.0 ms")]


def test_clearing_the_bundle_takes_its_gaps_and_leaves_the_rules(
    app, split, tmp_path, scratch_settings
):
    """The joins are the loader's; only the declared gap was the bundle's."""
    metadata = split_bundle(
        tmp_path / "cleared",
        gaps="[0.032, -0.12]",
        files='["a.wav", "b.wav", "c.wav"]',
        frames=f"[{int(RATE)}, {int(RATE)}, {int(RATE)}]",
    )
    split.annotations.load(metadata, Path("a.wav"))
    split.update_join_markers()
    assert any(label.isVisible() for _c, _w, label in split.join_labels)

    split.clear_annotations()
    for ax in split.lanes:
        assert positions(split, ax) == [1.0, 2.0]
    assert not any(label.isVisible() for _c, _w, label in split.join_labels)


def test_nothing_is_labelled_while_no_bundle_declares_a_gap(split):
    assert split.declared_join_gaps() == []
    assert split.join_labels
    assert not any(label.isVisible() for _c, _w, label in split.join_labels)


def test_gaps_that_do_not_match_the_joins_are_reported_and_never_guessed(
    app, split, tmp_path, scratch_settings
):
    """Two sources that disagree about how many joins there are cannot be
    matched up join by join, and a gap printed against the wrong join is
    worse than no gap at all."""
    metadata = split_bundle(
        tmp_path / "mismatch",
        gaps="[0.032, -0.12, 0.5]",
        files='["a.wav", "b.wav", "c.wav", "d.wav"]',
        frames=f"[{int(RATE)}, {int(RATE)}, {int(RATE)}, {int(RATE)}]",
    )
    assert split.load_annotations(metadata)
    said = " ".join(message for _level, message in split.said)
    assert "no gap is labelled" in said
    assert split.declared_join_gaps() == []
    assert not any(label.isVisible() for _c, _w, label in split.join_labels)
