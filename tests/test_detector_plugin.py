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

import csv
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtWidgets import QScrollArea  # noqa: E402

from test_panelsplitter import app, build_window, pump  # noqa: E402,F401

from audian_plugins import eventdetection  # noqa: E402
from audian_plugins.eventdetection import engine as detection  # noqa: E402
from audian_plugins.eventdetection import panel as detector_panel  # noqa: E402
from audian.labels import KIND_POINT, KIND_SPAN, Label  # noqa: E402

RATE = 8000
DURATION_S = 8.0
#: A pulse every 400 ms, from 300 ms in.  Nineteen of them.
FIRST_S = 0.3
EVERY_S = 0.4
PULSE_S = 0.020
CARRIER_HZ = 1200.0
#: How many examples the reader is pretending to have drawn.
SHOTS = 3

EXP3 = Path("/home/weygoldt/wrk/analyses/fakefish/experiments/exp3")
EXP3_FILES = sorted(EXP3.glob("DR0000_00*.wav"))
EXP3_LABELS = EXP3 / "DR0000_0088-editable-labels.csv"
needs_exp3_labels = pytest.mark.skipif(
    len(EXP3_FILES) != 4 or not EXP3_LABELS.is_file(),
    reason="the labelled four-file exp3 session is not on this machine",
)


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

    title, widget = eventdetection.audian_event_detection_panel(browser)
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
    names = [k for k in dir(eventdetection)
             if k.startswith("audian_") and callable(getattr(eventdetection, k))]
    assert "audian_event_detection_panel" in names
    assert [n for n in names if n.endswith("panel")] == ["audian_event_detection_panel"]


def test_a_plugin_file_in_the_working_directory_is_discovered(tmp_path, monkeypatch):
    """The oldest of the three rules, kept for trying something out."""
    from audian.plugins import Plugins

    (tmp_path / "audian_probe.py").write_text(
        "def audian_probe_panel(browser):\n    return 'Probe', None\n")
    monkeypatch.chdir(tmp_path)
    plugins = Plugins()
    plugins.load_local()
    assert [f.__name__ for f in plugins.panel_factories] == ["audian_probe_panel"]


def test_a_bundled_plugin_is_found_from_any_directory(tmp_path, monkeypatch):
    """The fix for the complaint that a merged plugin did not appear.

    Discovery used to be the working directory alone, so installing a
    plugin meant copying a file into every directory a reader launched
    from -- and one that was present but not copied looked exactly like a
    feature that had not been merged, because the Plugins menu is absent
    when nothing registers.
    """
    from audian.plugins import Plugins

    monkeypatch.chdir(tmp_path)          # nothing here at all
    plugins = Plugins()
    plugins.load_plugins()
    assert "audian_event_detection_panel" in [
        f.__name__ for f in plugins.panel_factories]


def test_the_same_plugin_found_twice_is_registered_once(tmp_path, monkeypatch):
    """The three paths overlap on purpose, and must not double the menu.

    A plugin part-way out of this tree is installed *and* still bundled;
    a reader testing one keeps a copy beside their recording.  Two entries
    opening two identical tabs is the failure that would cause.
    """
    from audian.plugins import Plugins

    monkeypatch.chdir(tmp_path)
    plugins = Plugins()
    plugins.load_plugins()
    once = len(plugins.panel_factories)
    plugins.load_plugins()
    assert len(plugins.panel_factories) == once


def test_an_extracted_plugin_would_be_found_by_its_entry_point():
    """What extraction costs: a pyproject stanza, and no code change.

    The group is scanned with the same `bind` the bundled walk uses, so a
    package that leaves this tree keeps working by declaring::

        [project.entry-points."audian.plugins"]
        eventdetection = "audian_plugins.eventdetection"
    """
    from audian.plugins import PLUGIN_ENTRY_POINT, Plugins

    assert PLUGIN_ENTRY_POINT == "audian.plugins"
    plugins = Plugins()
    # binding the package directly is what `load_installed` does once the
    # entry point resolves, so this pins the contract without needing a
    # second distribution installed to prove it
    assert plugins.bind(eventdetection, "eventdetection")
    assert [f.__name__ for f in plugins.panel_factories] == [
        "audian_event_detection_panel"]


def test_the_detector_reopens_every_file_in_a_split_recording(panel):
    """The sidecar's timebase spans the whole ordered WAV sequence."""
    first = "/recording/part-1.wav"
    second = "/recording/part-2.wav"
    original = panel.browser.data
    panel.browser.data = SimpleNamespace(
        # This is deliberately only the first file after Data.open().
        file_path=first,
        # The live loader is the authoritative complete sequence.
        data=SimpleNamespace(file_paths=[first, second]),
    )
    try:
        assert panel._paths() == [first, second]
    finally:
        panel.browser.data = original


@pytest.mark.realdata
@needs_exp3_labels
def test_every_span_in_later_exp3_wavs_is_learned():
    """The user's exact failure: file one ends at 932 s, labels start at 1785 s.

    Covered synthetically by the `session` fixture below, which puts one
    example in each of three files and asserts a template comes back for every
    one.  This is the same proposition against the real four-file session and
    the reader's own hand-drawn sidecar, which is worth having and is why it is
    behind --realdata rather than deleted.

    It takes whichever category the reader has actually drawn in rather than a
    fixed name, because that sidecar is a live file.  Naming `pulse` failed it
    on 2026-09-01 with `assert 0 >= 5`: nothing had regressed, they had spent
    the evening relabelling and the file held `volley` spans by then.  What is
    under test is that a span past the first file's end is learned at all, and
    any category the reader drew can show that.
    """
    soundfile = pytest.importorskip("soundfile")
    info = soundfile.info(str(EXP3_FILES[0]))
    first_end = info.frames / info.samplerate

    with EXP3_LABELS.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    drawn = {}
    for row in rows:
        # "pulse (found)" and the like are a detector's own export written back
        # beside the recording, not something the reader placed.
        if "(" in row["category"]:
            continue
        drawn.setdefault(row["category"], []).append(row)
    beyond = {
        name: found
        for name, found in drawn.items()
        if any(float(row["t_start_s"]) > first_end for row in found)
    }
    if not beyond:
        pytest.skip("no hand-drawn span past the first file in the sidecar")
    category = max(beyond, key=lambda name: len(beyond[name]))

    examples = [
        detection.Example(float(row["t_start_s"]), float(row["t_end_s"]))
        for row in beyond[category]
    ]
    assert len(examples) >= 2
    assert any(example.t0 > first_end for example in examples)

    recording = detector_panel.Recording(EXP3_FILES)
    try:
        templates = detection.learn_from_reader(
            lambda t0, t1: recording.samples(t0, t1, 0),
            recording.rate,
            examples,
            detection.Settings(domain=detection.TRACE),
        )
        assert recording.duration == pytest.approx(3621.024)
        assert templates.ok and len(templates) == len(examples)
    finally:
        recording.close()


def test_the_panel_offers_the_categories_that_have_examples_in_them(panel):
    """A category with nothing drawn in it cannot teach anything."""
    offered = [panel.sourcew.itemData(i) for i in range(panel.sourcew.count())]
    assert "pulse" in offered
    assert panel._category_name() not in offered


def test_five_point_labels_are_templates_not_silently_discarded(panel):
    """The built-in pulse category is a point category by default.

    A point has no duration of its own, so the panel gives it the explicit
    Point width and treats the mark as the centre.  This is the ordinary GUI
    path: five digit-key clicks must not end in "no examples to learn from".
    """
    labels = panel.browser.labels
    category = "five clicks"
    labels.add_category(category, KIND_POINT, labels.next_color())
    centres = [onset + 0.5 * PULSE_S for onset in ONSETS[:5]]
    for centre in centres:
        labels.add(Label(category, KIND_POINT, None, centre, None,
                         CARRIER_HZ, CARRIER_HZ))
    labels.forget_undo()

    try:
        panel.refresh_categories()
        _select(panel, category)
        panel.pointwidthw.setValue(20.0)
        examples = panel.examples()
        assert len(examples) == 5
        assert all(e.t1 - e.t0 == pytest.approx(0.020) for e in examples)
        assert all(0.5 * (e.t0 + e.t1) == pytest.approx(c)
                   for e, c in zip(examples, centres))
        assert all(widget.isVisible() for widget in panel._point_width_row)

        templates = panel._learn()
        assert templates is not None and templates.ok
        assert len(templates) == 5
    finally:
        labels.remove_category(category)
        labels.forget_undo()
        panel.refresh_categories()
        _select(panel)


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
    for _ in range(400):
        pump(0.05)
        if panel._thread is None:
            break
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


def test_the_level_slider_and_box_share_an_off_position(panel):
    """The floor is a real off state, and both faces always say the same."""
    panel.levelw.setValue(-35)
    pump(0.05)
    assert panel.leveldbw.value() == pytest.approx(-35.0)
    assert panel.settings().power_floor_db == pytest.approx(-35.0)

    panel.leveldbw.setValue(-42.0)
    pump(0.05)
    assert panel.levelw.value() == -42
    assert panel.settings().power_floor_db == pytest.approx(-42.0)

    panel.levelw.setValue(detector_panel.LEVEL_FLOOR_DB)
    pump(0.05)
    assert panel.settings().power_floor_db is None


def test_nms_has_a_toggle_and_synchronised_overlap_controls(panel):
    panel._fit_recall = "training recall 3/3 (100.0%)"
    panel.nmsw.setChecked(False)
    assert panel.settings().nms_enabled is False
    assert not any(widget.isEnabled() for widget in panel._nms_overlap_row)
    assert panel._fit_recall == "", "recall from the old NMS settings was retained"

    panel.nmsw.setChecked(True)
    panel.nmsoverlapw.setValue(37)
    assert panel.nmsoverlapbox.value() == pytest.approx(37.0)
    assert panel.settings().nms_overlap == pytest.approx(0.37)

    panel.nmsoverlapbox.setValue(62.0)
    assert panel.nmsoverlapw.value() == 62
    assert panel.settings().nms_overlap == pytest.approx(0.62)

    panel._write_nms_overlap(100.0 * detection.DEFAULT_NMS_OVERLAP)


def test_the_postprocessing_controls_reach_the_engine(panel):
    """The rows are not decoration: milliseconds become seconds at the seam."""
    panel.tolerancew.setValue(3.0)
    panel.gapw.setValue(125.0)
    settings = panel.settings()
    assert settings.duration_tolerance == pytest.approx(3.0)
    assert settings.merge_gap_s == pytest.approx(0.125)

    panel.tolerancew.setValue(detection.DURATION_TOLERANCE)
    panel.gapw.setValue(0.0)


# ------------------------------------------------------------- the preview


def test_threshold_fitting_uses_all_labels_not_only_the_visible_window(
        panel, monkeypatch):
    """The viewport is a tuning surface, never the training-set boundary."""
    _select(panel, domain=detection.TRACE)
    panel.browser.set_times(0.0, 0.1)
    pump(0.4)
    panel.templates = panel._learn()
    expected = {example.t0 for example in panel.examples()}
    assert expected and all(t0 > 0.1 for t0 in expected)

    seen = set()
    original = detection.calibrate_k

    def recording_calibration(score, times, examples, templates, margin=None):
        examples = list(examples)
        seen.update(example.t0 for example in examples)
        if margin is None:
            return original(score, times, examples, templates)
        return original(score, times, examples, templates, margin)

    monkeypatch.setattr(detection, "calibrate_k", recording_calibration)
    from audian.tasks.tokens import CancelToken

    progress = []
    result = detector_panel._fit(
        panel._paths(), panel.templates, panel.examples(), panel.settings(),
        panel._channel(), CancelToken(), progress.append,
    )
    assert result.k is not None
    assert seen == expected
    assert progress and progress[-1] == pytest.approx(1.0)


def test_fitting_reports_progress_and_training_recall(panel):
    _select(panel, domain=detection.TRACE)
    for _ in range(400):
        pump(0.05)
        if panel._thread is None:
            break
    panel._calibrate()
    assert panel._thread is not None
    assert panel.calibratew.text() == "Stop fitting"
    assert not panel.progressw.isHidden()
    for _ in range(400):
        pump(0.05)
        if panel._thread is None:
            break
    assert panel._thread is None, "fitting never finished"
    assert panel.progressw.isHidden()
    assert "training recall" in panel.statusw.text()
    assert panel._fit_recall in panel.statusw.text()


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


def test_turning_on_the_level_gate_scores_levels_once(panel):
    """An off curve has no level array; a live gate must not silently do nothing."""
    _select(panel)
    panel.levelw.setValue(detector_panel.LEVEL_FLOOR_DB)
    panel._scored_for = None
    panel._level = None
    panel.browser.set_times(0.0, 4.0)
    pump(0.4)
    panel.preview()
    assert panel._level is None

    panel.levelw.setValue(-40)
    panel.preview()
    assert panel._level is not None
    level = panel._level

    panel.levelw.setValue(-30)
    panel.preview()
    assert panel._level is level, "moving a live gate rescored the recording"
    panel.levelw.setValue(detector_panel.LEVEL_FLOOR_DB)


def test_moving_an_example_invalidates_the_template_cache(panel):
    """A category name can stay put while the boxes that teach it change."""
    _select(panel)
    panel.browser.set_times(0.0, 4.0)
    panel.preview()
    before = panel._scored_for
    example = Label("pulse", KIND_SPAN, None, 1.5, 1.5 + PULSE_S,
                    600.0, 2000.0)
    panel.browser.labels.add(example)
    panel.preview()
    assert panel._scored_for != before
    index = panel.browser.labels.index_of(example)
    assert index >= 0
    panel.browser.labels.remove(index)
    panel.browser.labels.forget_undo()


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
    for _ in range(400):
        pump(0.05)
        if panel._thread is None:
            break
    assert panel._thread is None, "automatic fitting never finished"
    cut = detection.threshold_of(panel._score, panel.kw.value())
    assert cut < 1.0, f"the cut is above any reachable score: {panel.statusw.text()}"
    assert panel.browser.labels.count_in(panel._category_name()) > 0


# ---------------------------------------------------- what is left behind


def test_detections_survive_the_panel_being_hidden(panel):
    """They used not to, and that was the bug behind two complaints.

    Detections are written into the label set so the existing overlay draws
    them, and the panel used to sweep them away again whenever it was
    hidden -- so shutting the side panel took a drawn preview from fifty
    marks to nought and reopening it put them back.  From outside that reads
    as a detector that works only sometimes, and only on what is on screen.

    They are editable labels in a category of their own, which is what they
    were asked to be: output to keep, correct and save.
    """
    browser = panel.browser
    _select(panel)
    browser.set_times(0.0, 4.0)
    pump(0.4)
    panel.preview()
    drawn = browser.labels.count_in(panel._category_name())
    assert drawn > 0

    panel.hide()
    pump(0.2)
    assert browser.labels.count_in(panel._category_name()) == drawn, (
        "hiding the panel emptied the category")
    panel.show()
    pump(0.4)
    assert browser.labels.count_in(panel._category_name()) >= drawn


def test_clearing_is_the_only_thing_that_removes_them(panel):
    """One deliberate way out, since nothing sweeps them away any more."""
    browser = panel.browser
    _select(panel)
    browser.set_times(0.0, 4.0)
    pump(0.4)
    panel.preview()
    assert browser.labels.count_in(panel._category_name()) > 0

    panel._clear_clicked()
    pump(0.4)
    assert browser.labels.count_in(panel._category_name()) == 0
    assert panel._category_name() not in [c.name for c in browser.labels.categories]


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
    for _ in range(400):
        pump(0.05)
        if panel._thread is None:
            break
    panel._calibrate()
    for _ in range(400):
        pump(0.05)
        if panel._thread is None:
            break
    assert panel._thread is None, "fitting never finished before the run"

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


def test_closing_the_panel_stops_its_sweep(panel):
    """The tab is the plugin's off switch, including work already in flight."""
    _select(panel)
    panel._run_clicked()
    assert panel._thread is not None
    panel.close()
    assert panel._thread is None
    assert panel.runw.text() == "Run"
    panel.show()
    pump(0.2)


# ------------------------------------------------------ the Plugins menu


@pytest.fixture(scope="module")
def window(app, tmp_path_factory):
    """A whole window whose `Plugins` has been told about the detector.

    `build_window` builds its own `Plugins` and lets it scan the working
    directory, which finds nothing here on purpose -- the plugin lives in
    ``examples/``.  Registering the factory through `load_plugins` is the
    same door discovery uses, so what the menu is built from is what a
    reader's copy in their data directory would put there.
    """
    pytest.importorskip("soundfile")
    import audian.audian as audian_app
    from PySide6.QtCore import QSettings
    from audian.plugins import Plugins

    original_load = Plugins.load_plugins
    original_path = audian_app.settings_path
    home = Path(QSettings("audian", "audian").fileName()).parent.parent
    Plugins.load_plugins = lambda self: self.add_panel_factory(
        eventdetection.audian_event_detection_panel)
    try:
        directory = tmp_path_factory.mktemp("detector-menu")
        signal, _ = pulse_train()
        win = build_window(app, directory, 2, signal)
    finally:
        Plugins.load_plugins = original_load
    pump(0.5)
    yield win
    win.close()
    win.setParent(None)
    win.deleteLater()
    pump(0.3)
    audian_app.settings_path = original_path
    for fmt in (QSettings.Format.NativeFormat, QSettings.Format.IniFormat):
        for scope in (QSettings.Scope.UserScope, QSettings.Scope.SystemScope):
            QSettings.setPath(fmt, scope, os.fspath(home))


def _detector_action(win):
    return dict(win.plugin_acts)["Event detection"]


def test_an_installed_plugin_gets_a_menu_entry_and_not_a_tab(window):
    """Installed is not the same as on screen, and used to be.

    Every registered factory took a tab at startup, so a reader who wanted
    the recording rather than the plugin had nowhere to put it.
    """
    assert [label for label, _act in window.plugin_acts] == ["Event detection"]
    assert "&Plugins" in [m.title() for m in window.menus]
    browser = window.browser()
    assert browser.plugin_labels() == ["Event detection"]
    assert not browser.plugin_panel_open("Event detection")
    assert browser.parambar.plugins is None, "a tab appeared unbidden"
    assert not _detector_action(window).isChecked()


def test_the_menu_entry_opens_and_closes_the_panel(window):
    """Both directions from the same tick."""
    browser = window.browser()
    act = _detector_action(window)

    act.setChecked(True)
    pump(0.5)
    assert browser.plugin_panel_open("Event detection")
    region = browser.parambar.plugins
    assert region is not None
    assert [region.tabText(i) for i in range(region.count())] == ["Detector"]
    # the tab holds a scroll area so a tall plugin scrolls rather than
    # squeezing itself; the plugin's own widget is inside it
    assert isinstance(region.widget(0), QScrollArea)
    assert isinstance(browser.plugin_panels["Event detection"],
                      detector_panel.DetectorPanel)

    act.setChecked(False)
    pump(0.5)
    assert not browser.plugin_panel_open("Event detection")
    assert browser.parambar.plugins is None, "the empty region was left behind"


def test_closing_the_tab_turns_the_plugin_off_and_the_menu_agrees(window):
    """The reader's other way out, and the tick has to follow it.

    A tab closed by its own cross that left the menu still ticked would be
    a switch that lies about the thing it switches.
    """
    browser = window.browser()
    act = _detector_action(window)
    act.setChecked(True)
    pump(0.5)
    assert act.isChecked() and browser.plugin_panel_open("Event detection")

    browser.plugin_tab_closed(0)
    pump(0.5)
    assert not browser.plugin_panel_open("Event detection")
    assert not act.isChecked(), "the tab closed but the menu still claims it is open"
    assert browser.parambar.plugins is None


def test_a_panel_opened_twice_is_still_one_panel(window):
    """Asking for what is already there raises it rather than doubling it."""
    browser = window.browser()
    assert browser.open_plugin_panel("Event detection")
    assert browser.open_plugin_panel("Event detection")
    region = browser.parambar.plugins
    assert region.count() == 1
    browser.close_plugin_panel("Event detection")
    pump(0.3)


def test_closing_a_panel_nobody_opened_is_not_an_error(window):
    """The menu can be unticked from a state where nothing is open."""
    browser = window.browser()
    browser.close_plugin_panel("Event detection")
    browser.close_plugin_panel("no such plugin")
    assert not browser.open_plugin_panel("no such plugin")


def test_the_entry_is_filed_under_a_heading_and_named_for_the_method(window):
    """`Plugins > Event detection > Normalised cross-correlation`.

    "Detector" says nothing about which of several a reader is turning on,
    and the next plugin to be written will be a detector too -- the heading
    is what makes the second one cheap to add rather than a second flat
    line competing with the first.
    """
    from audian.plugins import panel_menu_path

    path = panel_menu_path(eventdetection.audian_event_detection_panel)
    assert path == ("Event detection", "Normalised cross-correlation")

    plugin_menu = [m for m in window.menus if m.title() == "&Plugins"][0]
    submenus = [a.menu() for a in plugin_menu.actions() if a.menu() is not None]
    assert [m.title() for m in submenus] == ["Event detection"]
    entries = [a.text() for a in submenus[0].actions()]
    assert entries == ["Normalised cross-correlation"]
    # and it is still the same action the tick drives
    assert _detector_action(window).text() == "Normalised cross-correlation"


def test_a_plugin_that_says_nothing_stays_at_the_top_level(app):
    """A heading is an option, not a tax on writing a plugin."""
    from audian.plugins import panel_label, panel_menu_path

    def audian_probe_panel(browser):
        return "Probe", None

    assert panel_menu_path(audian_probe_panel) == ("Probe",)
    assert panel_label(audian_probe_panel) == "Probe"


def test_the_tab_carries_audians_own_close_mark(window):
    """Qt's close button is the platform's heavy X in a bar audian draws.

    `setTabsClosable` would install it; the panel installs a flat tool
    button instead, styled with the rest of the chrome in `theme.py`.
    """
    from PySide6.QtWidgets import QTabBar, QToolButton

    browser = window.browser()
    act = _detector_action(window)
    act.setChecked(True)
    pump(0.5)
    region = browser.parambar.plugins
    assert not region.tabsClosable(), "Qt's own close button is back"
    button = region.tabBar().tabButton(0, QTabBar.ButtonPosition.RightSide)
    assert isinstance(button, QToolButton)
    assert button.objectName() == "audianPluginClose"

    # and pressing it turns the plugin off, menu tick and all
    button.click()
    pump(0.5)
    assert not browser.plugin_panel_open("Event detection")
    assert not act.isChecked()
    assert browser.parambar.plugins is None


# ------------------------------------------------------- a globbed session


SESSION_FILES = 3
SESSION_FILE_S = 6.0


def session_signal(index):
    """One file of a session, with the pulse train running through it."""
    n = int(PULSE_S * RATE)
    tone = (np.sin(2 * np.pi * CARRIER_HZ * np.arange(n) / RATE)
            * np.hanning(n)).astype(np.float32)
    rng = np.random.default_rng(100 + index)
    frames = int(SESSION_FILE_S * RATE)
    signal = rng.normal(0.0, 0.002, (frames, 2)).astype(np.float32)
    onsets, t = [], FIRST_S
    while t + PULSE_S < SESSION_FILE_S:
        i = int(round(t * RATE))
        signal[i:i + n, 0] += tone
        signal[i:i + n, 1] += tone
        onsets.append(index * SESSION_FILE_S + t)
        t += EVERY_S
    return signal, onsets


@pytest.fixture(scope="module")
def session(app, tmp_path_factory):
    """A browser over three files audian joins into one timeline.

    The timestamps are the whole fixture.  `soundfile` writes no metadata,
    and without a start time `DataLoader.open_multiple` stops after the
    first file and `audian.data.open_files` refuses the set outright -- so
    they go in through `audioio` at creation, the way a recorder writes
    them.  Getting that wrong does not produce a subtly short recording; it
    produces a `ValueError` naming the files it could not open.
    """
    pytest.importorskip("soundfile")
    import datetime

    import audian.audian as audian_app
    from audioio import write_audio
    from PySide6.QtCore import QSettings
    from audian import theme
    from audian.plugins import Plugins

    directory = tmp_path_factory.mktemp("detector-session")
    start = datetime.datetime(2026, 8, 31, 10, 0, 0)
    paths, onsets = [], []
    for index in range(SESSION_FILES):
        signal, marks = session_signal(index)
        onsets.extend(marks)
        stamp = start + datetime.timedelta(seconds=index * SESSION_FILE_S)
        path = directory / f"rec-{index:02d}.wav"
        write_audio(str(path), signal, RATE, metadata=dict(BEXT=dict(
            OriginationDate=stamp.strftime("%Y-%m-%d"),
            OriginationTime=stamp.strftime("%H:%M:%S"),
        )))
        paths.append(str(path))

    original_load = Plugins.load_plugins
    theme.apply(app)
    plugins = Plugins()
    plugins.add_panel_factory(eventdetection.audian_event_detection_panel)
    win = audian_app.Audian(paths, {}, plugins, [], 0, None, False, 0, None)
    win.resize(1200, 900)
    win.show()
    pump(2.0)
    Plugins.load_plugins = original_load

    browser = win.browser()
    browser.labels.clear()
    browser.labels.add_category("pulse", KIND_SPAN, 0)
    # one example in each file, so a detector that sees only the first
    # cannot learn all three
    for index in range(SESSION_FILES):
        onset = onsets[index * len(onsets) // SESSION_FILES + 1]
        browser.labels.add(Label("pulse", KIND_SPAN, None, onset,
                                 onset + PULSE_S, 600.0, 2000.0))
    browser.labels.forget_undo()

    browser.open_plugin_panel("Event detection")
    pump(1.0)
    panel = browser.plugin_panels["Event detection"]
    yield panel, browser, onsets

    browser.close_plugin_panel("Event detection")
    win.close()
    win.setParent(None)
    win.deleteLater()
    pump(0.3)


def test_the_detector_sees_the_same_timeline_the_browser_does(session):
    """It opens the recording a second time, so the two can disagree.

    A globbed session is several files joined by their timestamps.  A loader
    that drops one leaves the detector scanning a shorter recording than the
    reader is looking at, silently, with every label past the join landing
    nowhere.
    """
    panel, browser, _onsets = session
    recording = panel._open()
    assert recording is not None
    assert len(recording.paths) == SESSION_FILES
    shown = len(browser.data.data) / browser.data.rate
    assert recording.duration == pytest.approx(shown, abs=0.01)
    assert recording.duration == pytest.approx(
        SESSION_FILES * SESSION_FILE_S, abs=0.01)


def test_examples_are_learned_from_every_file_of_the_session(session):
    """One example per file, and a template has to come back for each."""
    panel, _browser, _onsets = session
    _select(panel)
    assert len(panel.examples()) == SESSION_FILES
    templates = panel._learn()
    assert templates is not None
    assert len(templates) == SESSION_FILES


def test_a_run_crosses_every_file_boundary(session):
    """The complaint was that this breaks; it must find all of them.

    Per file rather than in total, because a detector that reads only the
    first file still returns a respectable-looking count.
    """
    panel, browser, onsets = session
    _select(panel)
    panel._calibrate()
    for _ in range(400):
        pump(0.05)
        if panel._thread is None:
            break
    panel._run_clicked()
    for _ in range(600):
        pump(0.05)
        if panel._thread is None:
            break
    assert panel._thread is None, "the sweep never finished"

    found = sorted(la.t0 for la in browser.labels
                   if la.category == panel._category_name())
    assert found, f"nothing found: {panel.statusw.text()}"
    for index in range(SESSION_FILES):
        lo = index * SESSION_FILE_S
        hi = lo + SESSION_FILE_S
        here = [t for t in found if lo <= t < hi]
        wanted = [t for t in onsets if lo <= t < hi]
        assert len(here) == pytest.approx(len(wanted), abs=2), (
            f"file {index}: {len(here)} found against {len(wanted)} present")
    assert found[-1] > (SESSION_FILES - 1) * SESSION_FILE_S, (
        "nothing was found in the last file of the session")


def test_a_finished_run_survives_seeking_through_the_recording(panel):
    """The preview and the run share a category and mean different things.

    "Candidates in this window" and "results for the whole recording" are
    drawn by the same code into the same rows, and `_draw` replaces that
    category outright -- it has to, or a preview could never withdraw a mark
    it no longer stands by.  Leaving the preview live after a run therefore
    let the next pan replace a whole recording's results with one window's:
    measured, 87 detections spanning 10.0-17.6 s became 21, and only in
    view.  That is the marks vanishing as a reader seeks.
    """
    browser = panel.browser
    _select(panel)
    browser.set_times(0.0, 3.0)
    pump(0.4)
    panel._calibrate()
    for _ in range(400):
        pump(0.05)
        if panel._thread is None:
            break
    panel._run_clicked()
    for _ in range(600):
        pump(0.05)
        if panel._thread is None:
            break
    assert panel._thread is None, "the sweep never finished"

    name = panel._category_name()
    after_run = browser.labels.count_in(name)
    assert after_run > 0, panel.statusw.text()
    assert not panel._drawing, "the preview is still live after a run"

    # seeking, and the preview that a pan would schedule
    for start in (2.0, 4.0, 6.0):
        browser.set_times(start, 2.0)
        pump(0.3)
        panel.preview()
        pump(0.2)
        assert browser.labels.count_in(name) == after_run, (
            f"seeking to {start} s cost "
            f"{after_run - browser.labels.count_in(name)} detections")

    panel._committed = False
    panel._drawing = True


def test_touching_a_control_after_a_run_says_that_it_replaces_it(panel):
    """Going back to previewing costs the run, and must not do so quietly.

    A count dropping from a whole recording's worth to one window's is
    alarming when it is unexplained and ordinary when it is not.
    """
    panel._committed = True
    panel._drawing = False
    panel._resume()
    assert panel._drawing
    assert not panel._committed
    assert "Run" in panel.statusw.text(), panel.statusw.text()


def test_a_tall_plugin_scrolls_instead_of_squeezing_itself(window):
    """A plugin is somebody else's layout and can be any height it likes.

    Unwrapped it got exactly the room the splitter gave it and compressed
    its own controls to fit, so a tall panel either dominated the side bar
    or made itself unusable -- and opening a second one made both worse.
    Measured at three region heights: the panel holds ~604 px throughout and
    the scrollbar takes up the difference.
    """
    from PySide6.QtWidgets import QScrollArea

    browser = window.browser()
    act = _detector_action(window)
    act.setChecked(True)
    pump(0.6)

    panel = browser.plugin_panels["Event detection"]
    frame = browser.plugin_frames["Event detection"]
    assert isinstance(frame, QScrollArea)
    assert frame.widgetResizable()
    natural = panel.minimumSizeHint().height()
    assert natural > 200, "the fixture panel is too short to prove anything"

    split = browser.parambar.split
    ranges = []
    for region_h in (400, 200, 120):
        total = sum(split.sizes())
        split.setSizes([max(total - region_h, 60), region_h])
        pump(0.4)
        assert panel.height() >= natural, (
            f"squeezed to {panel.height()} px with {natural} px asked for")
        ranges.append(frame.verticalScrollBar().maximum())

    assert ranges == sorted(ranges), (
        f"a smaller region must scroll further, got {ranges}")
    assert ranges[-1] > 0, "nothing scrolls even at 120 px"

    act.setChecked(False)
    pump(0.4)


def test_the_panel_divider_is_wider_than_the_lane_divider(window):
    """Audian's controls and somebody else's need a seam between them.

    At the shared handle width the two regions read as one list with a line
    through it.  Scoped by object name rather than raised globally, because
    `panelsplitter` divides lanes of the same picture where every pixel is
    lane height.
    """
    browser = window.browser()
    assert browser.parambar.split.objectName() == "audianPanelSplit"
    assert browser.parambar.split.handleWidth() >= 12


def test_a_preview_does_not_spend_the_reader_s_undo(panel):
    """The one undo belongs to the reader, not to a debounce.

    `_draw` used to call `forget_undo` after rewriting its category, on the
    honest reasoning that fifty steps of "the slider moved" is not an edit
    history -- but the slot it cleared was the reader's, so a box drawn by
    hand stopped being undoable the moment a timer fired.
    """
    browser = panel.browser
    _select(panel)
    browser.set_times(0.0, 4.0)
    pump(0.4)

    browser.labels.add(Label("pulse", KIND_SPAN, None, 9.0, 9.1, 600.0, 2000.0))
    assert browser.labels.can_undo()

    panel.preview()
    pump(0.2)
    assert browser.labels.count_in(panel._category_name()) > 0

    assert browser.labels.can_undo(), (
        "the preview consumed the undo of a label the reader drew by hand"
    )
    assert browser.labels.undo() == "add"


def test_closing_the_tab_takes_an_uncommitted_preview_with_it(panel):
    """Preview marks are this panel's working state, not the reader's work.

    They are written into the reader's own store so tuning is drawn by the
    code that draws a committed run.  Left there when the tab closes,
    `flush_labels` writes up to PREVIEW_LIMIT machine-made spans into the
    hand-authored sidecar beside the recording, where nothing distinguishes
    them from marks the reader made.
    """
    browser = panel.browser
    _select(panel)
    browser.set_times(0.0, 4.0)
    pump(0.4)
    panel.preview()
    pump(0.2)
    name = panel._category_name()
    assert browser.labels.count_in(name) > 0
    assert not panel._committed
    # Counted per category rather than by total, and the reader's count is
    # read here rather than assumed to be SHOTS: the fixture is module
    # scoped, so an earlier test may have left a mark of its own, and a
    # test that fails because of its neighbour is worse than no test.
    mine = browser.labels.count_in("pulse")

    panel.close()
    pump(0.3)

    assert browser.labels.count_in(name) == 0
    # and the reader's own marks are all still there.  Not a total: other
    # tests sharing this fixture leave categories of their own behind, and
    # this is a claim about the found category and the source category, not
    # about the whole store.
    assert browser.labels.count_in("pulse") == mine


def test_flushing_the_labels_takes_an_uncommitted_preview_with_it(panel):
    """The path a reader actually leaves by.

    The first version of this hooked `closeEvent`, and a test that called
    `panel.close()` passed while the fix did nothing in the application: Qt
    delivers no close event to a child when its parent closes, and neither
    `Audian.close_tab` nor `Audian.teardown` closes plugin panels -- both
    call `flush_labels` and then tear the browser down.  So the preview was
    still written into the sidecar on every quit.

    This drives `flush_labels` directly, which is what both of those do.
    """
    browser = panel.browser
    _select(panel)
    browser.set_times(0.0, 4.0)
    pump(0.4)
    panel.preview()
    pump(0.2)
    name = panel._category_name()
    assert browser.labels.count_in(name) > 0
    mine = browser.labels.count_in("pulse")

    # The fixture builds the panel directly; the application puts it in this
    # registry through `open_plugin_panel`, and that is what `flush_labels`
    # walks to find panels with something to tidy.
    browser.plugin_panels.setdefault("Detector", panel)
    try:
        browser.flush_labels()
    finally:
        browser.plugin_panels.pop("Detector", None)

    assert browser.labels.count_in(name) == 0, (
        "an uncommitted preview reached the reader's sidecar"
    )
    assert browser.labels.count_in("pulse") == mine


def test_a_saved_run_survives_a_nudged_control_and_a_flush(panel):
    """`_committed` is not "was written"; clearing on it deletes real work.

    Run writes the whole recording's results and saves them.  Touching any
    control then sets `_committed = False`, because the panel starts
    previewing again -- so a reader who ran the detector, nudged the
    sensitivity and quit would have had those results deleted from their
    sidecar by the tidy-up.
    """
    browser = panel.browser
    _select(panel)
    name = panel._category_name()

    # stand in for a finished Run: results drawn with previewing off, then
    # marked as written, which is what `_run_finished` does
    panel._drawing = False
    panel._draw([detection.Candidate(1.0, 1.2, 0.9), detection.Candidate(2.0, 2.2, 0.9)])
    panel._persisted = True
    panel._committed = True
    assert browser.labels.count_in(name) == 2

    # the reader nudges a control: previewing resumes, `_committed` drops
    panel._resume()
    assert not panel._committed
    panel._debounce.stop()  # before the preview redraws over them

    browser.plugin_panels.setdefault("Detector", panel)
    try:
        browser.flush_labels()
    finally:
        browser.plugin_panels.pop("Detector", None)
    assert browser.labels.count_in(name) == 2, (
        "a saved run was deleted because a control had been touched"
    )
