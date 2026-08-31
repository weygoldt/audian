"""What the Detector plugin promises, through a running browser.

Runs offscreen::

    QT_QPA_PLATFORM=offscreen .venv-qt6/bin/python -m pytest tests/test_detector_plugin.py -q

The arithmetic is pinned next door in ``tests/test_detection.py``, against
the reader's own annotation of a real cricket.  What is pinned *here* is
everything that only exists once there is a window: that the plugin is
found by the name it is given, that the controls agree with each other and
with the domain, that tuning does not rescore what it does not have to, and
that a preview nobody committed does not end up in the reader's file.

The plugin is imported from ``examples/`` rather than found the way a
reader finds it, and the discovery rule is exercised separately in its own
temporary directory.  A copy in the repository root would be loaded into
every browser the suite builds -- which is exactly how that was learnt.

The recording is synthetic on purpose.  Nineteen identical pulses at known
times is a claim a test can make exactly -- "it found the eleven the reader
drew" is the real recording's job, and a fixture whose answer is arithmetic
rather than opinion is what tells a regression from a disagreement.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "examples"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_panelsplitter import app, build_window, pump  # noqa: E402,F401

import audian_detector  # noqa: E402
from audian import detection  # noqa: E402
from audian.labels import KIND_SPAN, Label  # noqa: E402

RATE = 8000
DURATION_S = 8.0
#: A pulse every 400 ms, from 300 ms in.  Nineteen of them.
FIRST_S = 0.3
EVERY_S = 0.4
PULSE_S = 0.020
CARRIER_HZ = 1200.0
#: How many examples the reader is pretending to have drawn.
SHOTS = 3


def pulse_train():
    """Identical pulses at known times, and the times."""
    frames = int(DURATION_S * RATE)
    signal = np.zeros((frames, 2), dtype=np.float32)
    rng = np.random.default_rng(3)
    signal += rng.normal(0.0, 0.002, signal.shape).astype(np.float32)
    n = int(PULSE_S * RATE)
    envelope = np.hanning(n)
    tone = np.sin(2 * np.pi * CARRIER_HZ * np.arange(n) / RATE) * envelope
    onsets = []
    t = FIRST_S
    while t + PULSE_S < DURATION_S:
        i = int(round(t * RATE))
        signal[i:i + n, 0] += tone.astype(np.float32)
        signal[i:i + n, 1] += tone.astype(np.float32)
        onsets.append(t)
        t += EVERY_S
    return signal, onsets


ONSETS = pulse_train()[1]


@pytest.fixture(scope="module")
def panel(app, tmp_path_factory):
    """A browser on the pulse train, with the first few pulses marked."""
    pytest.importorskip("soundfile")
    import audian.audian as audian_app
    from PySide6.QtCore import QSettings

    original = audian_app.settings_path
    home = Path(QSettings("audian", "audian").fileName()).parent.parent
    directory = tmp_path_factory.mktemp("detector")
    signal, _ = pulse_train()
    window = build_window(app, directory, 2, signal)
    browser = window.browser()
    browser.set_panels(specs=1)
    pump(1.0)

    browser.labels.clear()
    browser.labels.add_category("pulse", KIND_SPAN, 0)
    for onset in ONSETS[:SHOTS]:
        browser.labels.add(Label("pulse", KIND_SPAN, None, onset,
                                 onset + PULSE_S, 600.0, 2000.0))
    browser.labels.forget_undo()

    title, widget = audian_detector.audian_detector_panel(browser)
    assert title == "Detector"
    widget.refresh_categories()
    widget.show()
    pump(0.5)
    yield widget

    widget.close()
    window.close()
    window.setParent(None)
    window.deleteLater()
    pump(0.3)
    audian_app.settings_path = original
    for fmt in (QSettings.Format.NativeFormat, QSettings.Format.IniFormat):
        for scope in (QSettings.Scope.UserScope, QSettings.Scope.SystemScope):
            QSettings.setPath(fmt, scope, os.fspath(home))


def _select(widget, category="pulse", domain=detection.SPECTROGRAM):
    """Pick a source the way a reader does, threshold refitted with it.

    The fixture is module-scoped, so a test that leaves the sensitivity
    somewhere odd would otherwise hand it to the next one: choosing a source
    the panel has already fitted skips the refit, and a `k` of 6 left behind
    by the slider test put the cut above 1.0 for whoever ran next.  Clearing
    the fit is what selecting a *fresh* source does in the application, so
    this is the honest reset rather than a convenience.
    """
    index = widget.sourcew.findData(category)
    if index >= 0:
        widget.sourcew.setCurrentIndex(index)
    widget.domainw.setCurrentIndex(widget.domainw.findData(domain))
    widget._calibrated_for = None
    pump(0.2)


# ------------------------------------------------------------- the plugin


def test_the_factory_is_found_by_the_name_the_convention_gives_it():
    """`load_plugins` binds ``audian_*panel``, and nothing else about it.

    The whole interface between audian and this file is a function name, so
    renaming the function is what breaks the plugin -- not an import, not a
    registration call that would fail loudly.
    """
    names = [k for k in dir(audian_detector)
             if k.startswith("audian_") and callable(getattr(audian_detector, k))]
    assert "audian_detector_panel" in names
    assert [n for n in names if n.endswith("panel")] == ["audian_detector_panel"]


def test_a_plugin_file_in_the_working_directory_is_discovered(tmp_path, monkeypatch):
    """The discovery rule itself, exercised rather than assumed."""
    from audian.plugins import Plugins

    (tmp_path / "audian_probe.py").write_text(
        "def audian_probe_panel(browser):\n    return 'Probe', None\n")
    monkeypatch.chdir(tmp_path)
    plugins = Plugins()
    plugins.load_plugins()
    assert [f.__name__ for f in plugins.panel_factories] == ["audian_probe_panel"]


def test_the_panel_offers_the_categories_that_have_examples_in_them(panel):
    """A category with nothing drawn in it cannot teach anything."""
    offered = [panel.sourcew.itemData(i) for i in range(panel.sourcew.count())]
    assert "pulse" in offered
    assert panel._category_name() not in offered


# ------------------------------------------------------------ the controls


@pytest.mark.parametrize("domain", detection.DOMAINS)
def test_the_combiners_offered_follow_the_domain(panel, domain):
    """Two of them measured badly on the trace and are not offered there."""
    _select(panel, domain=domain)
    offered = [panel.combinerw.itemData(i) for i in range(panel.combinerw.count())]
    assert offered == list(detection.combiners_for(domain))


def test_the_representation_row_goes_away_on_the_trace(panel):
    """It is a question about a picture, and the trace is not one."""
    _select(panel, domain=detection.SPECTROGRAM)
    assert all(w.isVisible() for w in panel._representation_row)
    _select(panel, domain=detection.TRACE)
    assert not any(w.isVisible() for w in panel._representation_row)


def test_the_slider_and_the_box_never_disagree(panel):
    """One control with two faces; neither may drift from the other."""
    _select(panel, domain=detection.SPECTROGRAM)
    for value in (10, 35, 50, 72, 95):
        panel.sensitivityw.setValue(value)
        pump(0.05)
        assert panel.kw.value() == pytest.approx(
            detection.k_from_sensitivity(value, detection.SPECTROGRAM), rel=1e-6)
    for k in (1.0, 2.5, 6.0):
        panel.kw.setValue(k)
        pump(0.05)
        assert panel.sensitivityw.value() == pytest.approx(
            round(detection.sensitivity_from_k(k, detection.SPECTROGRAM)), abs=1)


# ------------------------------------------------------------- the preview


def test_the_preview_finds_the_pulses_that_are_on_screen(panel):
    """The examples were three of nineteen identical events."""
    browser = panel.browser
    _select(panel)
    browser.set_times(0.0, 4.0)
    pump(0.5)
    panel.preview()
    pump(0.2)
    found = browser.labels.count_in(panel._category_name())
    expected = sum(1 for t in ONSETS if 0.0 <= t < 4.0)
    assert found == pytest.approx(expected, abs=2), (
        f"{found} found where {expected} pulses are on screen: "
        f"{panel.statusw.text()}")


def test_moving_the_sensitivity_does_not_rescore(panel):
    """The cache is what makes tuning feel like tuning.

    Rescoring a window costs 200-940 ms and reapplying a threshold to the
    curve already computed costs a fraction of a millisecond, so the
    difference between the two is the whole interaction.
    """
    _select(panel)
    panel.browser.set_times(0.0, 4.0)
    pump(0.4)
    panel.preview()
    stamp = panel._scored_for
    assert stamp is not None
    panel.sensitivityw.setValue(30)
    panel.preview()
    assert panel._scored_for == stamp, "the score curve was thrown away"


def test_changing_what_is_matched_does_rescore(panel):
    """And the other half: a new combiner is a new curve."""
    _select(panel)
    panel.browser.set_times(0.0, 4.0)
    pump(0.4)
    panel.preview()
    stamp = panel._scored_for
    other = [panel.combinerw.itemData(i) for i in range(panel.combinerw.count())]
    other = [c for c in other if c != panel.combinerw.currentData()]
    panel.combinerw.setCurrentIndex(panel.combinerw.findData(other[0]))
    pump(0.1)
    panel.preview()
    assert panel._scored_for != stamp


def test_the_threshold_is_fitted_to_a_fresh_source_without_being_asked(panel):
    """A panel that opens showing nothing teaches the reader it is broken.

    The default `k` is measured over a whole recording; a three second view
    of a dense signal has a much narrower spread, and the same `k` there put
    the cut at 1.507 -- above the highest value a correlation can reach.
    """
    _select(panel)
    panel._calibrated_for = None
    panel.browser.set_times(0.0, 4.0)
    pump(0.4)
    panel.preview()
    pump(0.1)
    cut = detection.threshold_of(panel._score, panel.kw.value())
    assert cut < 1.0, f"the cut is above any reachable score: {panel.statusw.text()}"
    assert panel.browser.labels.count_in(panel._category_name()) > 0


# ---------------------------------------------------- what is left behind


def test_a_preview_nobody_committed_is_taken_back_off_the_recording(panel):
    """Previews are real label rows, and audian saves the label set.

    So a reader who glanced at this tab and moved on would otherwise find a
    category they never asked for written into their sidecar.
    """
    browser = panel.browser
    _select(panel)
    browser.set_times(0.0, 4.0)
    pump(0.4)
    panel._committed = False
    panel.preview()
    assert browser.labels.count_in(panel._category_name()) > 0
    panel.hide()
    pump(0.2)
    assert browser.labels.count_in(panel._category_name()) == 0
    assert panel._category_name() not in [c.name for c in browser.labels.categories]
    panel.show()
    pump(0.2)


def test_a_run_that_finished_is_kept(panel):
    """The other half of the same rule: a committed result survives."""
    browser = panel.browser
    _select(panel)
    panel._committed = True
    panel.preview()
    pump(0.2)
    before = browser.labels.count_in(panel._category_name())
    panel.hide()
    pump(0.2)
    assert browser.labels.count_in(panel._category_name()) == before
    panel.show()
    pump(0.2)
    panel._committed = False


# --------------------------------------------------------- the whole file


def test_running_over_the_recording_finds_every_pulse_and_writes_a_csv(panel):
    """The sweep, its progress, and what it leaves on disk.

    Nineteen pulses, of which three were marked.  Block boundaries are the
    part worth pinning: a template straddling one is a detection nobody
    gets, and the same event seen from both sides is one counted twice.
    """
    browser = panel.browser
    _select(panel)
    browser.set_times(0.0, 4.0)
    pump(0.4)
    panel._calibrate()
    pump(0.2)

    panel._run_clicked()
    assert panel.runw.text() == "Stop"
    for _ in range(400):
        pump(0.05)
        if panel._thread is None:
            break
    assert panel._thread is None, "the sweep never finished"
    assert panel.runw.text() == "Run"
    assert panel.progressw.isHidden()

    found = browser.labels.count_in(panel._category_name())
    assert found == pytest.approx(len(ONSETS), abs=3), (
        f"{found} found against {len(ONSETS)} pulses: {panel.statusw.text()}")

    onsets = sorted(la.t0 for la in browser.labels
                    if la.category == panel._category_name())
    for wanted in ONSETS:
        assert any(abs(t - wanted) < 0.030 for t in onsets), (
            f"nothing found at {wanted:.3f} s")
    assert len(onsets) == len(set(round(t, 3) for t in onsets)), (
        "an event was counted twice, which is a block-overlap fault")

    recording = Path(panel.recording.path)
    csvs = list(recording.parent.glob("*-found.csv"))
    assert len(csvs) == 1, f"expected one output file, got {csvs}"
    rows = csvs[0].read_text().splitlines()
    assert rows[0] == ("category,kind,channel,t_start_s,t_end_s,"
                       "f_low_hz,f_high_hz,note")
    assert len(rows) - 1 == found
    panel._committed = False


def test_the_sweep_can_be_stopped(panel):
    """A run the reader gave up on has to give the button back."""
    _select(panel)
    panel._run_clicked()
    panel._stop_sweep()
    for _ in range(200):
        pump(0.05)
        if panel._thread is None:
            break
    assert panel._thread is None
    assert panel.runw.text() == "Run"
    assert panel.progressw.isHidden()
