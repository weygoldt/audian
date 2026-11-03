"""The whole loop, in the real application, with real models and a real runner.

Deselected by default, because it needs weights on disk and it trains::

    AUDIAN_TEST_WEIGHTS=<dir> uv run pytest tests/test_specdetector_app.py --realmodels

Everything is asserted against where the events really are in a synthetic recording,
so that this cannot pass by the panel merely having drawn something.
"""

from __future__ import annotations

import csv
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import QRectF

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_panelsplitter import RATE, build_window, pump  # noqa: E402

from audian.databrowser import DataBrowser  # noqa: E402
from audian.labels import KIND_SPAN, LabelSet  # noqa: E402
from audian_plugins.specdetector import panel as panel_module  # noqa: E402
from audian_plugins.specdetector.boxes import Box, match_events  # noqa: E402
from audian_plugins.specdetector.panel import (  # noqa: E402
    FOUND_SUFFIX,
    SpectrogramDetectorPanel,
)

pytestmark = pytest.mark.realmodels

DURATION_S = 40.0
FIRST_S = 1.0
EVERY_S = 0.73
PIP_S = 0.12
F_LOW, F_HIGH = 900.0, 1500.0
CLASS = "pip"


def pip_times():
    return np.arange(FIRST_S, DURATION_S - 1.0, EVERY_S)


def recording() -> np.ndarray:
    """Rising sweeps at known times, on two channels of noise."""
    rng = np.random.default_rng(5)
    frames = int(DURATION_S * RATE)
    signal = rng.normal(0.0, 0.01, (frames, 2)).astype(np.float32)
    n = int(PIP_S * RATE)
    t = np.arange(n) / RATE
    phase = 2 * np.pi * (F_LOW * t + 0.5 * (F_HIGH - F_LOW) / PIP_S * t**2)
    pip = (np.sin(phase) * np.hanning(n) * 0.5).astype(np.float32)
    for onset in pip_times():
        i = int(onset * RATE)
        signal[i : i + n, :] += pip[:, None]
    return signal


def truth() -> list:
    return [Box(t, t + PIP_S, F_LOW, F_HIGH) for t in pip_times()]


def wait_for(condition, client, timeout=240.0):
    """Pump until `condition`; a failed job fails here with what the runner said."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        pump(0.05)
        if condition():
            return
        if client.last_error is not None:
            pytest.fail(
                f"{client.last_error.get('message')}\n"
                f"{client.last_error.get('traceback', '')}\n{client.stderr_tail()}"
            )
    pytest.fail(f"timed out; runner said:\n{client.stderr_tail()}")


@pytest.fixture(scope="module")
def weights():
    directory = os.environ.get("AUDIAN_TEST_WEIGHTS")
    if not directory:
        pytest.skip("AUDIAN_TEST_WEIGHTS does not name a directory of weights")
    return Path(directory)


@pytest.fixture(scope="module")
def panel(app, weights, tmp_path_factory):
    import audian.audian as audian_app
    from PySide6.QtCore import QSettings

    original = audian_app.settings_path
    home = Path(QSettings("audian", "audian").fileName()).parent.parent
    directory = tmp_path_factory.mktemp("specdetector-app")
    window = build_window(app, directory, 2, recording())
    browser = window.browser()
    browser.set_panels(specs=1)
    pump(1.0)
    browser.labels.clear()
    # Through the door a reader uses, so that discovery and the flush are exercised:
    # a panel built by hand is not one `DataBrowser.flush_labels` knows about.
    original_weights = panel_module.weights_directory
    panel_module.weights_directory = lambda: weights
    assert "Spectrogram detector" in browser.plugin_labels()
    assert browser.open_plugin_panel("Spectrogram detector")
    widget = browser.plugin_panels["Spectrogram detector"]
    assert isinstance(widget, SpectrogramDetectorPanel)
    widget.show()
    pump(0.5)
    yield widget
    panel_module.weights_directory = original_weights
    widget.close()
    window.close()
    window.setParent(None)
    window.deleteLater()
    pump(0.3)
    audian_app.settings_path = original
    for fmt in (QSettings.Format.NativeFormat, QSettings.Format.IniFormat):
        for scope in (QSettings.Scope.UserScope, QSettings.Scope.SystemScope):
            QSettings.setPath(fmt, scope, os.fspath(home))


def test_a_drag_in_label_mode_is_tightened_and_outlined_by_sam(panel):
    """audian's own label tool, with the SAM assistant: no label scheme of the tab's.

    A rough box around each of three pips is stored as drawn, and SAM's answer then
    moves it onto the pip -- the claim a visible overlay could not make.
    """
    browser = panel.browser
    browser.labels.add_category(CLASS, KIND_SPAN)
    browser.current_category = CLASS
    browser.sync_category_state()
    browser.set_label_assist(True)
    panel.window().set_region_mode(DataBrowser.MODE_LABEL)
    panel.goto(FIRST_S - 0.5, FIRST_S + 2.5, 500.0, 2000.0)
    pump(0.5)
    assistant = browser.label_assistant
    client = assistant._client()
    ax = panel._lanes()[0]
    for onset in pip_times()[:3]:
        drawn = (onset - 0.03, onset + PIP_S + 0.03, F_LOW - 150.0, F_HIGH + 150.0)
        rect = QRectF(drawn[0], drawn[2], drawn[1] - drawn[0], drawn[3] - drawn[2])
        assert browser.label_from_region(ax.channel, ax.getViewBox(), rect)
        label = browser.labels.labels[-1]
        wait_for(lambda: label.outline is not None, client)
        assert onset - 0.04 <= label.t0 <= label.t1 <= onset + PIP_S + 0.04
        assert F_LOW - 300.0 <= label.f0 <= label.f1 <= F_HIGH + 300.0
        assert (label.t1 - label.t0) < (drawn[1] - drawn[0])
        assert len(label.outline) >= 3
    labels = [la for la in browser.labels if la.category == CLASS]
    assert len(labels) == 3
    assert all(la.kind == KIND_SPAN and la.outline for la in labels)


def test_find_more_proposes_the_events_it_was_shown_a_few_of(panel):
    panel.scopew.setCurrentIndex(panel.scopew.findData("all"))
    panel.find_more()
    wait_for(lambda: not panel.batch.busy(), panel.batch)
    found = list(panel.candidates.values())
    assert found, panel.statusw.text()
    rest = truth()[3:]
    match = match_events(found, rest, time_tol_s=0.1, frequency_overlap=True)
    assert match.recall > 0.8, match
    shown = [
        la for la in panel.browser.labels if la.category == f"{CLASS}{FOUND_SUFFIX}"
    ]
    assert len(shown) == len(panel._label_of) > 0
    assert all(la.note.startswith("score=") for la in shown)


def test_review_answers_a_window_and_goes_to_another(panel):
    panel.next_window()
    assert panel.browser.region_mode == DataBrowser.MODE_LABEL
    lane = panel._lanes()[0]
    first = panel._view_of(lane)
    before = len(panel.candidates)
    panel.accept_view()
    assert len(panel.candidates) < before
    assert panel._view_of(lane) != first
    assert panel.project.reviewed_rects(panel._paths())
    for _ in range(4):
        panel.accept_view()
    labels = [la for la in panel.browser.labels if la.category == CLASS]
    assert len(labels) > 3


def test_training_leaves_a_model_that_finds_the_events(panel):
    panel.train()
    wait_for(lambda: not panel.batch.busy(), panel.batch)
    path = panel.project.model_path()
    assert path and Path(path).is_file(), panel.statusw.text()
    meta = (Path(path).parent / "model.json").read_text()
    assert '"names"' in meta and CLASS in meta
    assert "mAP50" in panel.statusw.text()


def test_a_run_detects_the_whole_recording(panel):
    panel.run_detection()
    wait_for(lambda: not panel.batch.busy(), panel.batch)
    everything = list(panel.candidates.values())
    labelled, _regions, _unusable = panel.labelled_boxes()
    # What was reviewed was taken out of the candidates; those events are labels.
    match = match_events(everything + labelled, truth())
    assert match.recall > 0.9, (match, panel.statusw.text())
    above = [b for b in everything if b.score >= panel.threshold]
    assert above
    # What a reader sees at the default cut is right, if not all there is.
    assert match_events(above, truth()).precision > 0.9
    written = list((Path(panel.project.directory) / "detections").glob("*.csv"))
    assert len(written) == 1
    with open(written[0], newline="") as fp:
        rows = list(csv.DictReader(fp))
    assert rows and all(r["note"].startswith("score=") for r in rows)
    assert all(r["category"] == f"{CLASS}{FOUND_SUFFIX}" for r in rows)


def test_export_writes_what_is_above_the_cut(panel):
    out = panel.export_csv()
    assert out is not None and out.is_file()
    with open(out, newline="") as fp:
        rows = list(csv.DictReader(fp))
    above = [b for b in panel.candidates.values() if b.score >= panel.threshold]
    assert len(rows) == len(above) > 0
    assert all(r["category"] == CLASS for r in rows)


def test_a_detector_trained_on_outlines_finds_events_with_outlines(panel):
    panel.trainonw.setCurrentIndex(panel.trainonw.findData(True))
    panel._train_on_picked()
    panel.train()
    wait_for(lambda: not panel.batch.busy(), panel.batch)
    meta = json.loads(
        (Path(panel.project.model_path()).parent / "model.json").read_text()
    )
    assert meta["task"] == "segment"
    panel.scopew.setCurrentIndex(panel.scopew.findData("all"))
    panel.find_more()
    wait_for(lambda: not panel.batch.busy(), panel.batch)
    outlined = [b for b in panel.candidates.values() if b.polygon]
    assert outlined, panel.statusw.text()
    # An outline lies on its own box, and in the same units.
    for box in outlined[:20]:
        times = [p[0] for p in box.polygon]
        bands = [p[1] for p in box.polygon]
        assert box.t0 - 0.05 <= min(times) <= max(times) <= box.t1 + 0.05
        assert box.f0 - 200.0 <= min(bands) <= max(bands) <= box.f1 + 200.0
    # drawn by audian, as the outlines on the labels standing for them
    shown = [la for la in panel._label_of.values() if la.outline]
    assert shown


def test_guesses_never_reach_the_hand_authored_sidecar(panel):
    panel.browser.flush_labels()
    assert panel._closed
    sidecar = panel.browser.labels_path()
    with open(sidecar, newline="") as fp:
        categories = {row["category"] for row in csv.DictReader(fp)}
    assert CLASS in categories
    assert not any(name.endswith(FOUND_SUFFIX) for name in categories)
    # SAM's outlines are kept in the sidecar, with the labels they belong to.
    read = LabelSet()
    read.read(sidecar)
    assert sum(1 for la in read if la.outline) >= 3
