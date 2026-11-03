"""The Detector tab in the real window, with the runners replaced by a stand-in.

The tab labels nothing itself: events are labelled with audian's own label mode, and
these tests draw them through the browser as a reader's drag does.  No models and no
processes: `FakeClient` records each request and answers when told to.  What is asserted is what a reader can see and what is written down: labels in the
store, the view on the lane, the content of a request, the rows of a CSV.
"""

from __future__ import annotations

import csv
import json
import os
import shutil
import sys
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import QObject, QPointF, QRectF, Qt, Signal
from PySide6.QtWidgets import QApplication, QLineEdit, QPushButton

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_panelsplitter import RATE, build_window, pump  # noqa: E402

from audian.databrowser import DataBrowser  # noqa: E402
from audian.labels import KIND_POINT, KIND_SPAN, Label, LabelSet  # noqa: E402
from audian_plugins import specdetector  # noqa: E402
from audian_plugins.specdetector import models  # noqa: E402
from audian_plugins.specdetector import panel as panel_module  # noqa: E402
from audian_plugins.specdetector import project as project_  # noqa: E402
from audian_plugins.specdetector.dataset import Rect  # noqa: E402
from audian_plugins.specdetector.panel import (  # noqa: E402
    FOUND_SUFFIX,
    SpectrogramDetectorPanel,
)
from audian_plugins.specdetector.assistant import SamAssistant  # noqa: E402
from audian_plugins.specdetector.tools import (  # noqa: E402
    KEY_TABLE,
    KeyRouter,
    LaneSurface,
)

CLASS = "pip"
FOUND = f"{CLASS}{FOUND_SUFFIX}"
DURATION_S = 20.0


class FakeClient(QObject):
    """A `RunnerClient` that remembers what it was asked and answers on command."""

    sigProgress = Signal(str, str, int, int, str)
    sigResult = Signal(str, dict)
    sigError = Signal(str, str, str)

    def __init__(self):
        super().__init__()
        self.requests: list = []
        self.job = None
        self.last_error = None

    def submit(self, op, **params):
        self.requests.append((op, params))
        self.job = f"j{len(self.requests)}"
        return self.job

    def busy(self):
        return self.job is not None

    def answer(self, **message):
        job, self.job = self.job, None
        self.sigResult.emit(job, message)

    def fail(self, kind="exception", text="it broke"):
        job, self.job = self.job, None
        self.sigError.emit(job, kind, text)

    def cancel(self):
        self.fail("cancelled", "cancelled")

    def shutdown(self, *_args):
        self.job = None


def recording():
    rng = np.random.default_rng(1)
    return rng.normal(0, 0.01, (int(DURATION_S * RATE), 2)).astype(np.float32)


def box(t, score=0.5, cls=0, polygon=None):
    return dict(
        t0=t, t1=t + 0.1, f0=500.0, f1=900.0, cls=cls, score=score, polygon=polygon
    )


@pytest.fixture(scope="module")
def shared_window(app, tmp_path_factory):
    """One window for the module: building one takes most of four seconds."""
    import audian.audian as audian_app
    from PySide6.QtCore import QSettings

    original = audian_app.settings_path
    home = Path(QSettings("audian", "audian").fileName()).parent.parent
    directory = tmp_path_factory.mktemp("specdetector-panel")
    original_weights = panel_module.weights_directory
    weights = directory / "weights"
    panel_module.weights_directory = lambda: weights
    made = build_window(app, directory, 2, recording())
    made.browser().set_panels(specs=1)
    pump(0.5)
    yield made
    panel_module.weights_directory = original_weights
    made.close()
    made.setParent(None)
    made.deleteLater()
    pump(0.2)
    audian_app.settings_path = original
    for fmt in (QSettings.Format.NativeFormat, QSettings.Format.IniFormat):
        for scope in (QSettings.Scope.UserScope, QSettings.Scope.SystemScope):
            QSettings.setPath(fmt, scope, os.fspath(home))


@pytest.fixture()
def window(shared_window):
    """The window with nothing left over from the test before."""
    browser = shared_window.browser()
    weights = panel_module.weights_directory()
    weights.mkdir(exist_ok=True)
    for name in models.ALL_WEIGHTS:
        (weights / name).write_bytes(b"stand-in")
    browser.labels.clear()
    browser.labels.forget_undo()
    yield shared_window
    shared_window.set_region_mode(DataBrowser.MODE_ZOOM)
    for label in list(browser.plugin_panels):
        browser.close_plugin_panel(label)
    pump(0.05)
    for category in list(browser.labels.categories):
        browser.labels.remove_category(category.name)
    browser.labels.clear()
    browser.label_save_pending = False
    paths = [os.fspath(p) for p in browser.data.data.file_paths]
    shutil.rmtree(project_.default_directory(paths[0]), ignore_errors=True)
    for path in (
        browser.labels_path(),
        Path(paths[0]).with_name(f"{Path(paths[0]).stem}-detections.csv"),
    ):
        Path(path).unlink(missing_ok=True)
    browser.sync_category_state()


@pytest.fixture()
def panel(window):
    browser = window.browser()
    assert browser.open_plugin_panel("Spectrogram detector")
    widget = browser.plugin_panels["Spectrogram detector"]
    widget.batch = FakeClient()
    widget.batch.sigProgress.connect(widget._job_progress)
    widget.batch.sigResult.connect(widget._job_result)
    widget.batch.sigError.connect(widget._job_error)
    # A category of the reader's, chosen as a reader does: on the chips.
    browser.labels.add_category(CLASS, KIND_SPAN)
    browser.current_category = CLASS
    browser.sync_category_state()
    widget._ensure_project()
    widget.show()
    pump(0.2)
    yield widget


def lane(panel):
    return panel._lanes()[0]


def found_labels(panel):
    return [la for la in panel.browser.labels if la.category == FOUND]


def own_labels(panel):
    return [la for la in panel.browser.labels if la.category == CLASS]


def mask(t0=1.0, t1=1.1, f0=500.0, f1=900.0):
    polygon = [[t0, f0], [t1, f0], [t1, f1], [t0, f1]]
    return dict(box=[t0, t1, f0, f1], polygon=polygon, score=0.9, fraction=0.01)


def draw(panel, category, t0, t1, f0=500.0, f1=900.0, channel=0):
    """A label drawn with audian's own tool, on a spectrogram lane: what a drag does."""
    browser = panel.browser
    if browser.labels.category(category) is None:
        browser.labels.add_category(category, KIND_SPAN)
    ax = panel._lanes()[channel]
    return browser.store_label(
        browser.labels.category(category), ax, ax.channel, t0, t1, f0, f1
    )


def reviewing(panel):
    """Label mode, in an active window: where review takes clicks and keys."""
    activate(panel)
    panel.enter_label_mode()
    pump(0.05)
    assert panel._reviewing()


class FakeClick:
    """What pyqtgraph hands `mouseClickEvent`, and what the item did with it."""

    def __init__(self, scene_pos, modifiers=Qt.KeyboardModifier.NoModifier):
        self._pos = scene_pos
        self._modifiers = modifiers
        self.accepted = None

    def button(self):
        return Qt.MouseButton.LeftButton

    def modifiers(self):
        return self._modifiers

    def scenePos(self):  # noqa: N802 - pyqtgraph's spelling
        return self._pos

    def accept(self):
        self.accepted = True

    def ignore(self):
        self.accepted = False


class FakeHover:
    def __init__(self, scene_pos):
        self._pos = scene_pos
        self.clicks = []
        self.drags = []

    def isExit(self):  # noqa: N802
        return False

    def scenePos(self):  # noqa: N802
        return self._pos

    def acceptClicks(self, button):  # noqa: N802
        self.clicks.append(button)

    def acceptDrags(self, button):  # noqa: N802
        self.drags.append(button)


def scene_at(panel, t, f, channel=0):
    surface = panel.surfaces[channel]
    return surface, surface.vb.mapViewToScene(QPointF(t, f))


def propose(panel, boxes, source="examples"):
    panel.scopew.setCurrentIndex(panel.scopew.findData("all"))
    panel.batch.job = None
    panel.project.config = panel.project.config or config()
    panel._job_kind = "find"
    panel.batch.answer(boxes=boxes, source=source)


def config():
    from audian_plugins.specdetector import tiling

    return tiling.suggest_config([(1.0, 1.1, 500.0, 900.0)], RATE, channels=2)


# ------------------------------------------------------------------- the plugin


def test_it_is_offered_next_to_the_wavetracker_and_says_what_it_is_for(window):
    from audian.plugins import panel_menu_path, panel_menu_tip

    factory = specdetector.audian_spectrogram_detector_panel
    assert panel_menu_path(factory) == ("Spectrogram detector",)
    assert "train" in panel_menu_tip(factory)
    labels = window.browser().plugin_labels()
    assert "Spectrogram detector" in labels and "Wavetracker" in labels


def test_opening_it_does_not_widen_the_window(window):
    """The T470s is the budget: a tab must not raise the window's floor."""
    before = window.minimumSizeHint().width()
    browser = window.browser()
    assert browser.open_plugin_panel("Spectrogram detector")
    pump(0.3)
    assert window.minimumSizeHint().width() <= before


def test_opening_it_makes_no_window_of_its_own(window):
    """On a tiling compositor a stray top-level is a tile, and makes usage impossible."""
    before = {w for w in QApplication.topLevelWidgets() if w.isVisible()}
    window.browser().open_plugin_panel("Spectrogram detector")
    pump(0.3)
    after = {w for w in QApplication.topLevelWidgets() if w.isVisible()}
    assert after == before


def test_importing_the_panel_brings_no_model_library_into_audian():
    """torch, OpenCV and Ultralytics stay in the runner: OpenCV ships a Qt of its own."""
    import subprocess

    code = (
        "import sys, os\n"
        f"sys.path.insert(0, {str(REPO / 'src')!r})\n"
        "os.environ['QT_QPA_PLATFORM'] = 'offscreen'\n"
        "import audian_plugins.specdetector.panel\n"
        "import audian_plugins.specdetector.client\n"
        "import audian_plugins.specdetector.tools\n"
        "for name in ('torch', 'cv2', 'ultralytics'):\n"
        "    assert name not in sys.modules, name\n"
        "assert 'audian.audian' in sys.modules\n"
        "print('clean')\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=120
    )
    assert "clean" in done.stdout, done.stderr


def test_nothing_starts_until_it_is_asked_for(panel):
    """A tab looked at must not start a gigabyte of model, nor SAM its own."""
    assert panel.batch.requests == []
    assert panel.browser.label_assistant.client is None


# ------------------------------------------------------------------- labelling is audian's


def test_the_tab_has_no_label_tool_of_its_own(panel):
    """It had its own Label button, class picker and prompts: a second way to label."""
    texts = {button.text() for button in panel.findChildren(QPushButton)}
    assert "Label" not in texts and "+" not in texts
    status = SpectrogramDetectorPanel(panel.browser).statusw.text()
    assert "label mode (b)" in status and "SAM" in status


def test_a_label_drawn_with_audians_tool_is_a_class_and_an_example(panel):
    """The point of tying the two together: no label of the reader's is ignored."""
    draw(panel, "rise", 2.0, 2.4, 600.0, 800.0)
    add_label(panel)
    panel.find_more()
    shots = panel.batch.requests[-1][1]["shots"]
    names = panel.project.names
    assert sorted((names[s["cls"]], s["t0"]) for s in shots) == [
        (CLASS, 1.0),
        ("rise", 2.0),
    ]
    # and the tab says so at once, not after the next training
    assert panel.classesw.text() == f"{CLASS}, rise"


def test_training_learns_every_boxed_category_and_nothing_else(panel):
    labels = panel.browser.labels
    add_label(panel)
    draw(panel, "rise", 2.0, 2.4, 600.0, 800.0)
    labels.add_category("click", KIND_POINT)
    labels.add(Label("click", KIND_POINT, None, 3.0, None, 700.0, 700.0))
    labels.add_category("note", KIND_SPAN)
    labels.add(Label("note", KIND_SPAN, None, 4.0, 4.5))  # on a trace: no band
    labels.add_category(panel_module.REGION_CATEGORY, KIND_SPAN)
    labels.add(
        Label(panel_module.REGION_CATEGORY, KIND_SPAN, None, 7.0, 8.0, 0.0, 4000.0)
    )
    panel.train()
    request = panel.batch.requests[-1][1]
    assert request["names"] == [CLASS, "rise"]
    boxes = request["sources"][0]["boxes"]
    assert sorted((b["cls"], b["t0"]) for b in boxes) == [(0, 1.0), (1, 2.0)]


def test_a_category_left_out_is_not_learnt_and_its_labels_are_hidden(panel):
    """Not taught as background either: the reader did not say it is no event."""
    add_label(panel)
    draw(panel, "noise", 2.0, 2.4, 100.0, 300.0)
    panel.set_class_learnt("noise", False)
    panel.train()
    request = panel.batch.requests[-1][1]
    assert request["names"] == [CLASS]
    source = request["sources"][0]
    assert [b["t0"] for b in source["boxes"]] == [1.0]
    assert [2.0, 2.4, 100.0, 300.0] in [hidden[:4] for hidden in source["hidden"]]
    assert "not noise" in panel.classesw.text()
    assert project_.Project.open(panel.project.directory).left_out == ["noise"]
    panel.set_class_learnt("noise", True)
    panel.train()
    assert panel.batch.requests[-1][1]["names"] == [CLASS, "noise"]


def test_the_classes_menu_offers_the_readers_span_categories(panel):
    prepared(panel)
    labels = panel.browser.labels
    labels.add_category("click", KIND_POINT)
    labels.add_category(panel_module.REGION_CATEGORY, KIND_SPAN)
    labels.add_category("rise", KIND_SPAN)
    panel._fill_classes_menu()
    actions = {action.text(): action for action in panel.classes_menu.actions()}
    assert CLASS in actions and "rise" in actions
    assert not {"click", panel_module.REGION_CATEGORY, FOUND} & set(actions)
    assert all(action.isChecked() for action in actions.values())
    actions["rise"].setChecked(False)
    assert panel.project.left_out == ["rise"]


def test_an_outline_on_a_label_is_what_training_draws(panel):
    shape = ((1.0, 500.0), (1.1, 500.0), (1.05, 900.0))
    labels = panel.browser.labels
    labels.add(Label(CLASS, KIND_SPAN, None, 1.0, 1.1, 500.0, 900.0, "", shape))
    panel.trainonw.setCurrentIndex(1)
    panel.train()
    source = panel.batch.requests[-1][1]["sources"][0]
    assert source["boxes"][0]["polygon"] == [list(p) for p in shape]


def test_outlines_kept_in_the_project_move_onto_their_labels(panel):
    """Before labels carried outlines, this tab kept SAM's in the project."""
    label = add_label(panel)
    polygon = [[1.0, 500.0], [1.1, 500.0], [1.05, 900.0]]
    key = project_.mask_key(CLASS, 1.0, 1.1, 500.0, 900.0)
    elsewhere = project_.mask_key(CLASS, 3.0, 3.1, 500.0, 900.0)
    panel.project.masks.update({key: polygon, elsewhere: polygon})
    panel._adopt_masks()
    assert label.outline == tuple(map(tuple, polygon))
    assert list(panel.project.masks) == [elsewhere]
    panel.browser.flush_labels()
    read = LabelSet()
    read.read(panel.browser.labels_path())
    assert [la.outline for la in read] == [tuple(map(tuple, polygon))]
    # A recording not opened since keeps finding its own in the project.
    other = LabelSet()
    other.add_category(CLASS, KIND_SPAN)
    other.add(Label(CLASS, KIND_SPAN, None, 3.0, 3.1, 500.0, 900.0))
    boxes, _regions, _unusable = panel.labelled_boxes(other)
    assert boxes[0].polygon == tuple(map(tuple, polygon))


def test_sam_is_offered_in_the_labels_tab_and_answers_a_drag_there(window):
    """The whole path: a drag in label mode, the SAM runner asked, the label outlined."""
    browser = window.browser()
    assistant = browser.label_assistant
    assert isinstance(assistant, SamAssistant)
    assert browser.label_assistw.text() == "SAM"
    assert not browser.label_assistw.isHidden()
    client = FakeClient()
    assistant.client, assistant._wired = client, False
    browser.labels.add_category(CLASS, KIND_SPAN)
    browser.current_category = CLASS
    browser.sync_category_state()
    on = browser.label_assist_on
    browser.set_label_assist(True)
    window.set_region_mode(DataBrowser.MODE_LABEL)
    browser.assist_timer.stop()
    try:
        ax = browser.spectrogram_axes()[0]
        rect = QRectF(1.0, 500.0, 0.2, 400.0)
        assert browser.label_from_region(ax.channel, ax.getViewBox(), rect)
        op, request = client.requests[-1]
        assert op == "segment"
        assert request["box"] == [1.0, 1.2, 500.0, 900.0]
        assert request["reduction"] == str(ax.channel)
        label = browser.labels.labels[-1]
        assert (label.t0, label.t1) == (1.0, 1.2)
        answer = mask(1.02, 1.15, 550.0, 850.0)
        client.answer(mask=answer)
        assert (label.t0, label.t1, label.f0, label.f1) == (1.02, 1.15, 550.0, 850.0)
        assert label.outline == tuple(map(tuple, answer["polygon"]))
    finally:
        assistant.client, assistant._wired = None, False
        assistant.waiting.clear()
        assistant.asked = None
        browser.set_label_assist(on)


def test_get_models_asks_for_only_what_is_missing(panel):
    (panel.weights_dir / models.SEGMENT_WEIGHTS).unlink()
    panel.fetch_models()
    op, request = panel.batch.requests[-1]
    assert op == "fetch"
    assert request["names"] == [models.SEGMENT_WEIGHTS]


# ------------------------------------------------------------------- finding more


def add_label(panel, t=1.0):
    label = Label(CLASS, KIND_SPAN, None, t, t + 0.1, 500.0, 900.0)
    panel.browser.labels.add(label)
    panel.project.class_index(CLASS)
    return label


def test_find_more_learns_from_the_labels_and_suggests_the_images(panel):
    add_label(panel)
    panel.find_more()
    op, request = panel.batch.requests[-1]
    assert op == "propose"
    assert request["shots"] == [dict(t0=1.0, t1=1.1, f0=500.0, f1=900.0, cls=0)]
    assert "model" not in request
    assert request["config"]["tile_s"] > 0.1
    assert panel.project.config is not None


def test_find_more_with_nothing_labelled_says_what_to_do(panel):
    panel.find_more()
    assert panel.batch.requests == []
    assert "Label an example" in panel.statusw.text()


def test_find_more_uses_the_trained_model_once_there_is_one(panel):
    add_label(panel)
    directory = Path(panel.project.directory) / "models" / "m"
    directory.mkdir(parents=True)
    (directory / "best.pt").write_bytes(b"x")
    (directory / "model.json").write_text(
        json.dumps({"config": config().as_dict(), "threshold": 0.3, "names": [CLASS]})
    )
    panel.project.set_model(directory / "best.pt")
    panel.find_more()
    request = panel.batch.requests[-1][1]
    assert request["model"] == str(directory / "best.pt")
    assert "shots" not in request


def test_a_view_scope_looks_only_at_the_view(panel):
    add_label(panel)
    panel.goto(2.0, 5.0, 200.0, 3000.0)
    pump(0.1)
    panel.scopew.setCurrentIndex(panel.scopew.findData("view"))
    panel.find_more()
    assert panel.batch.requests[-1][1]["windows"] == [
        [pytest.approx(2.0), pytest.approx(5.0)]
    ]


def test_candidates_arrive_as_labels_of_their_own_with_their_scores(panel):
    add_label(panel)
    propose(panel, [box(5.0, 0.9), box(6.0, 0.8)], "model")
    panel.threshold = 0.1
    panel._write_found()
    found = found_labels(panel)
    assert len(found) == 2
    assert {la.note for la in found} == {"score=0.9000", "score=0.8000"}
    assert [la.category for la in own_labels(panel)] == [CLASS]


def test_the_cut_reads_the_scores_of_prompted_candidates(panel):
    """Prompted YOLOE's scores sit nowhere in particular."""
    add_label(panel)
    boxes = [box(2.0 + i * 0.2, 0.012) for i in range(30)]
    boxes += [box(9.0 + i * 0.2, 0.15) for i in range(10)]
    propose(panel, boxes, "examples")
    assert 0.012 < panel.threshold < 0.15
    assert len(found_labels(panel)) == 10


def test_moving_the_cut_runs_nothing_and_changes_what_is_shown(panel):
    add_label(panel)
    propose(panel, [box(5.0, 0.9), box(6.0, 0.2)], "model")
    panel.threshold = 0.5
    panel._write_found()
    assert len(found_labels(panel)) == 1
    asked = len(panel.batch.requests)
    panel.threshw.setValue(panel_module._slider_of_value(0.1))
    panel._threshold_settled()
    assert len(found_labels(panel)) == 2
    assert len(panel.batch.requests) == asked
    assert panel.threshlabel.text().startswith("0.1")


def test_moving_the_cut_does_not_spend_the_readers_undo(panel):
    label = add_label(panel)
    propose(panel, [box(5.0, 0.9)], "model")
    panel._write_found()
    assert panel.browser.labels.can_undo()
    assert panel.browser.labels.undo() == "add"
    assert label not in list(panel.browser.labels)


def test_what_has_been_answered_is_not_proposed_again(panel):
    add_label(panel, 1.0)
    paths = panel._paths()
    panel.project.reject(paths, [project_.box_from_json(box(3.0))])
    panel.project.mark_reviewed(paths, Rect(7.0, 8.0, 0.0, 4000.0))
    propose(
        panel,
        [box(1.0), box(3.0), box(7.5), box(10.0)],
        "model",
    )
    assert sorted(b.t0 for b in panel.candidates.values()) == [10.0]


def test_an_exhaustively_labelled_region_counts_as_reviewed(panel):
    """The chirp benchmark's sidecars are written this way: ready to train on as is."""
    labels = panel.browser.labels
    labels.add_category(panel_module.REGION_CATEGORY, KIND_SPAN)
    labels.add(
        Label(panel_module.REGION_CATEGORY, KIND_SPAN, None, 7.0, 8.0, 0.0, 4000.0)
    )
    add_label(panel, 7.2)
    boxes, regions, _ = panel.labelled_boxes()
    assert regions == [Rect(7.0, 8.0, 0.0, 4000.0)]
    assert [b.t0 for b in boxes] == [7.2]
    panel._fill_classes_menu()
    names = {action.text() for action in panel.classes_menu.actions()}
    assert panel_module.REGION_CATEGORY not in names


def test_a_huge_result_is_capped_on_screen_and_says_so(panel, monkeypatch):
    add_label(panel)
    monkeypatch.setattr(panel_module, "FOUND_LIMIT", 5)
    said = []
    monkeypatch.setattr(panel, "_notify", lambda level, text: said.append(text))
    propose(panel, [box(2.0 + i * 0.2, 0.9) for i in range(20)], "model")
    assert len(found_labels(panel)) == 5
    assert any("15 candidates" in text for text in said)
    assert len(panel.candidates) == 20


# ------------------------------------------------------------------- reviewing


def prepared(panel, boxes=None):
    add_label(panel)
    panel.project.config = config()
    propose(panel, boxes or [box(5.0, 0.9), box(5.2, 0.8), box(12.0, 0.7)], "model")
    panel.threshold = 0.1
    panel._write_found()


def test_clicking_a_candidate_rejects_it_and_remembers_why(panel):
    prepared(panel)
    reviewing(panel)
    assert panel.candidate_clicked(lane(panel), 5.05, 700.0)
    assert len(found_labels(panel)) == 2
    assert 5.0 not in {la.t0 for la in found_labels(panel)}
    assert panel.project.rejected_boxes(panel._paths())[0].t0 == 5.0


def test_backspace_takes_back_a_rejection(panel):
    prepared(panel)
    reviewing(panel)
    panel.candidate_clicked(lane(panel), 5.05, 700.0)
    panel.run_key("backspace")
    assert 5.0 in {la.t0 for la in found_labels(panel)}
    assert panel.project.rejected_boxes(panel._paths()) == []


def test_a_click_beside_a_candidate_or_outside_label_mode_is_audians(panel):
    """Review takes a click on a candidate in label mode, and nothing else."""
    prepared(panel)
    reviewing(panel)
    assert not panel.claims_click(lane(panel), 8.0, 700.0)
    assert not panel.candidate_clicked(lane(panel), 8.0, 700.0)
    assert panel.claims_click(lane(panel), 5.05, 700.0)
    panel.window().set_region_mode(DataBrowser.MODE_ZOOM)
    assert not panel.claims_click(lane(panel), 5.05, 700.0)
    assert not panel.candidate_clicked(lane(panel), 5.05, 700.0)
    assert len(found_labels(panel)) == 3


def test_the_surface_takes_a_plain_click_on_a_candidate_and_never_a_drag(panel):
    """A drag must reach audian's rubber band, which labels what the model missed;
    Ctrl+click must reach audian's label picking."""
    assert not hasattr(LaneSurface, "mouseDragEvent")
    prepared(panel)
    reviewing(panel)
    panel.goto(4.0, 6.0, 200.0, 3000.0)
    pump(0.1)
    surface, at = scene_at(panel, 5.05, 700.0)
    hover = FakeHover(at)
    surface.hoverEvent(hover)
    assert hover.clicks == [Qt.MouseButton.LeftButton] and hover.drags == []
    _surface, beside = scene_at(panel, 4.5, 700.0)
    hover = FakeHover(beside)
    surface.hoverEvent(hover)
    assert hover.clicks == [] and hover.drags == []
    ctrl = FakeClick(at, Qt.KeyboardModifier.ControlModifier)
    surface.mouseClickEvent(ctrl)
    assert ctrl.accepted is False
    assert len(found_labels(panel)) == 3
    plain = FakeClick(at)
    surface.mouseClickEvent(plain)
    assert plain.accepted is True
    assert 5.0 not in {la.t0 for la in found_labels(panel)}


def test_a_click_on_another_channels_lane_rejects_nothing(panel):
    prepared(panel, [box(5.0, 0.9)])
    uid = next(iter(panel.candidates))
    panel.candidates[uid] = panel.candidates[uid]._replace(channel=1)
    panel._write_found()
    reviewing(panel)
    lanes = {ax.channel: ax for ax in panel._lanes()}
    assert not panel.claims_click(lanes[0], 5.05, 700.0)
    assert not panel.candidate_clicked(lanes[0], 5.05, 700.0)
    assert panel.claims_click(lanes[1], 5.05, 700.0)


def test_a_candidate_deleted_with_the_ordinary_tools_counts_as_rejected(panel):
    prepared(panel)
    labels = panel.browser.labels
    target = next(la for la in found_labels(panel) if la.t0 == 5.0)
    labels.remove(labels.index_of(target))
    panel._write_found()
    assert [b.t0 for b in panel.project.rejected_boxes(panel._paths())] == [5.0]
    assert 5.0 not in {la.t0 for la in found_labels(panel)}


def test_a_candidate_moved_with_its_grips_keeps_the_new_box_when_accepted(panel):
    prepared(panel)
    labels = panel.browser.labels
    target = next(la for la in found_labels(panel) if la.t0 == 5.0)
    labels.set_geometry(labels.index_of(target), 5.01, 5.09, 600.0, 800.0)
    panel.goto(4.0, 6.0, 200.0, 3000.0)
    pump(0.1)
    panel.accept_view()
    kept = [la for la in own_labels(panel) if la.t0 == pytest.approx(5.01)]
    assert kept and kept[0].f0 == 600.0 and kept[0].f1 == 800.0


def test_the_view_being_done_accepts_what_is_shown_and_records_the_view(panel):
    prepared(panel)
    panel.goto(4.0, 6.0, 200.0, 3000.0)
    pump(0.1)
    panel.accept_view()
    accepted = sorted(la.t0 for la in own_labels(panel))
    assert accepted == [1.0, 5.0, 5.2]
    assert all(
        la.note.startswith("accepted") for la in own_labels(panel) if la.t0 != 1.0
    )
    rect = panel.project.reviewed_rects(panel._paths())[0]
    assert rect.t0 == pytest.approx(4.0) and rect.t1 == pytest.approx(6.0)
    assert rect.f0 == pytest.approx(200.0) and rect.f1 == pytest.approx(3000.0)
    assert [b.t0 for b in panel.candidates.values()] == [12.0]


def test_a_candidate_below_the_cut_in_a_finished_view_is_dropped_not_accepted(panel):
    prepared(panel, [box(5.0, 0.9), box(5.2, 0.05), box(12.0, 0.7)])
    panel.threshold = 0.1
    panel._write_found()
    panel.goto(4.0, 6.0, 200.0, 3000.0)
    pump(0.1)
    panel.accept_view()
    assert sorted(la.t0 for la in own_labels(panel)) == [1.0, 5.0]
    assert [b.t0 for b in panel.candidates.values()] == [12.0]


def test_rejecting_a_view_teaches_what_is_shown_as_not_events(panel):
    prepared(panel)
    panel.goto(4.0, 6.0, 200.0, 3000.0)
    pump(0.1)
    panel.reject_view()
    assert sorted(b.t0 for b in panel.project.rejected_boxes(panel._paths())) == [
        5.0,
        5.2,
    ]
    assert sorted(la.t0 for la in own_labels(panel)) == [1.0]
    assert panel.project.reviewed_rects(panel._paths())


def test_next_window_shows_a_window_holding_candidates_in_label_mode(panel):
    """Through the window's own Label action, so the tool bar says so too."""
    prepared(panel)
    panel.window().set_region_mode(DataBrowser.MODE_ZOOM)
    panel.next_window()
    pump(0.1)
    assert panel.browser.region_mode == DataBrowser.MODE_LABEL
    assert panel.window().acts.label_region.isChecked()
    t0, t1, _f0, _f1 = panel._view_of(lane(panel))
    assert any(t0 <= b.t0 and b.t1 <= t1 for b in panel.candidates.values())
    assert "windows to go" in panel.statusw.text()


def test_the_status_counts_the_candidates_a_reader_can_see(panel):
    """It counted those below the cut too: '14 candidates here' over three boxes."""
    prepared(panel, [box(5.0, 0.9), box(5.2, 0.02), box(5.4, 0.03)])
    panel.threshold = 0.5
    panel._write_found()
    panel.next_window()
    assert "1 candidates here" in panel.statusw.text()


def test_when_nothing_is_left_it_says_what_comes_next(panel):
    prepared(panel, [box(5.0, 0.9)])
    panel.goto(4.0, 6.0, 200.0, 3000.0)
    pump(0.1)
    panel.accept_view()
    assert "Nothing left to review" in panel.statusw.text()


# ------------------------------------------------------------------- training


def test_training_sends_labels_outlines_reviews_and_rejections(panel):
    prepared(panel)
    shape = ((1.0, 500.0), (1.1, 500.0), (1.05, 900.0))
    own_labels(panel)[0].outline = shape
    reviewing(panel)
    panel.candidate_clicked(lane(panel), 12.05, 700.0)  # rejected
    panel.project.mark_reviewed(panel._paths(), Rect(1.0, 2.0, 0.0, 4000.0))
    panel.trainonw.setCurrentIndex(1)
    panel.train()
    op, request = panel.batch.requests[-1]
    assert op == "train"
    assert request["names"] == [CLASS]
    assert request["segment"] is True
    source = request["sources"][0]
    assert source["paths"] == panel._paths()
    assert [b["t0"] for b in source["boxes"]] == [1.0]
    assert source["boxes"][0]["polygon"] == [[1.0, 500.0], [1.1, 500.0], [1.05, 900.0]]
    assert [b["t0"] for b in source["rejected"]] == [12.0]
    assert source["reviewed"] == [[1.0, 2.0, 0.0, 4000.0, None]]


def test_a_label_with_no_band_is_left_out_and_the_reader_is_told(panel):
    add_label(panel)
    labels = panel.browser.labels
    labels.add(Label(CLASS, KIND_SPAN, None, 2.0, 2.1))  # drawn on a trace
    labels.add(Label(CLASS, KIND_POINT, None, 3.0))
    panel.train()
    source = panel.batch.requests[-1][1]["sources"][0]
    assert [b["t0"] for b in source["boxes"]] == [1.0]
    assert "2 labels have no band" in panel.statusw.text()


def test_training_with_nothing_labelled_says_so(panel):
    panel.train()
    assert panel.batch.requests == []
    assert "Label some events" in panel.statusw.text()


def trained(panel, held_out=True):
    add_label(panel)
    directory = Path(panel.project.directory) / "models" / "m"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "best.pt").write_bytes(b"x")
    (directory / "model.json").write_text(
        json.dumps({"config": config().as_dict(), "threshold": 0.42, "names": [CLASS]})
    )
    panel.train()
    panel.batch.answer(
        model=str(directory / "best.pt"),
        threshold=0.42,
        metrics={"map50": 0.93},
        held_out=held_out,
        val_events=22,
        seconds=47.0,
    )


def test_a_finished_training_says_what_its_score_is_worth(panel):
    trained(panel)
    assert "mAP50 0.930" in panel.statusw.text()
    assert "22 held-out events" in panel.statusw.text()
    assert panel.project.model_path().endswith("best.pt")
    assert panel.threshold == 0.42
    assert panel.runw.isEnabled()


def test_a_score_on_its_own_training_data_says_that_it_is(panel):
    trained(panel, held_out=False)
    assert "its own training data" in panel.statusw.text()


def test_stopping_a_job_says_stopped_and_gives_back_the_buttons(panel):
    add_label(panel)
    panel.train()
    assert not panel.trainw.isEnabled()
    assert panel.stopw.isVisibleTo(panel)
    panel.stop_job()
    assert "Stopped" in panel.statusw.text()
    assert panel.trainw.isEnabled()
    assert not panel.stopw.isVisibleTo(panel)


def test_a_failed_job_says_why(panel):
    add_label(panel)
    panel.train()
    panel.batch.fail("oom", "CUDA out of memory")
    assert "CUDA out of memory" in panel.statusw.text()
    assert panel.trainw.isEnabled()


# ------------------------------------------------------------------- running


def test_run_needs_a_model(panel):
    assert not panel.runw.isEnabled()
    panel.run_detection()
    assert "Train a model" in panel.statusw.text()
    assert panel.batch.requests == []


def test_a_run_writes_results_in_the_project_and_shows_them(panel):
    trained(panel)
    panel.run_detection()
    op, request = panel.batch.requests[-1]
    assert op == "detect"
    assert request["paths"] == panel._paths()
    assert Path(request["out"]).parent.name == "detections"
    panel.batch.answer(
        boxes=[box(5.0, 0.9), box(6.0, 0.1)],
        count=2,
        out=request["out"],
        seconds=2.0,
        recording_seconds=20.0,
    )
    assert panel.threshold == 0.42
    assert [la.t0 for la in found_labels(panel)] == [5.0]
    assert "1 events above the cut" in panel.statusw.text()
    assert "10x real time" in panel.statusw.text()


def test_export_writes_what_is_above_the_cut_in_label_columns(panel):
    shape = ((5.0, 500.0), (5.05, 900.0), (5.1, 500.0))
    prepared(panel, [box(5.0, 0.9, polygon=shape), box(6.0, 0.05)])
    panel.threshold = 0.5
    panel._write_found()
    out = panel.export_csv()
    assert out.name.endswith("-detections.csv")
    with open(out, newline="") as fp:
        rows = list(csv.DictReader(fp))
    assert len(rows) == 1
    assert rows[0]["category"] == CLASS
    assert float(rows[0]["t_start_s"]) == 5.0
    assert rows[0]["note"] == "score=0.9000"
    read = LabelSet()
    assert read.read(out).read == 1
    assert read.labels[0].outline == shape


# ------------------------------------------------------------------- going away


def test_guesses_never_reach_the_sidecar_and_come_back_next_time(panel, window):
    prepared(panel)
    browser = window.browser()
    browser.flush_labels()
    with open(browser.labels_path(), newline="") as fp:
        categories = {row["category"] for row in csv.DictReader(fp)}
    assert categories == {CLASS}
    assert found_labels(panel) == []
    boxes, meta = panel.project.load_candidates(panel._paths())
    assert sorted(b.t0 for b in boxes) == [5.0, 5.2, 12.0]
    assert meta["source"] == "model"

    fresh = SpectrogramDetectorPanel(browser, weights_dir=panel.weights_dir)
    fresh.batch = FakeClient()
    fresh._restore()
    assert sorted(b.t0 for b in fresh.candidates.values()) == [5.0, 5.2, 12.0]
    assert len(found_labels(fresh)) == 3
    fresh.about_to_flush_labels()
    fresh.setParent(None)
    fresh.deleteLater()


def test_a_project_that_cannot_be_read_is_left_alone_and_reported(window):
    browser = window.browser()
    paths = [os.fspath(p) for p in browser.data.data.file_paths]
    directory = project_.default_directory(paths[0])
    directory.mkdir()
    garbage = '{"reviewed": ['
    (directory / project_.PROJECT_FILE).write_text(garbage)
    assert browser.open_plugin_panel("Spectrogram detector")
    widget = browser.plugin_panels["Spectrogram detector"]
    pump(0.1)
    assert widget.project is None
    widget._ensure_project()
    assert "cannot read" in widget.statusw.text()
    assert (directory / project_.PROJECT_FILE).read_text() == garbage


def test_closing_the_tab_stops_the_runners_and_the_keys(panel):
    closed = []
    panel.batch.shutdown = lambda *_a: closed.append("batch")
    ax = lane(panel)
    surface = panel.surfaces[0]
    assert surface.parentItem() is ax.getViewBox()
    assert panel.router._installed
    panel.close()
    assert closed == ["batch"]
    assert not panel.router._installed
    # Taken off the lane, and not only out of the panel's own lists.
    assert surface.parentItem() is None


# ------------------------------------------------------------------- what review found


def test_enter_with_no_candidate_in_view_is_not_reviews(panel):
    """It marked the whole view, teaching every unlabelled event as background."""
    prepared(panel)
    reviewing(panel)
    panel.goto(7.0, 9.0, 200.0, 3000.0)
    pump(0.1)
    assert panel.router.command_for(enter_key()) is None
    panel.goto(4.0, 6.0, 200.0, 3000.0)
    pump(0.1)
    assert panel.router.command_for(enter_key()) == "enter"


def test_enter_finishes_a_view_holding_candidates(panel):
    prepared(panel)
    panel.goto(4.0, 6.0, 200.0, 3000.0)
    pump(0.1)
    panel.run_key("enter")
    assert panel.project.reviewed_rects(panel._paths())
    assert sorted(la.t0 for la in own_labels(panel)) == [1.0, 5.0, 5.2]


def test_an_event_labelled_during_review_is_kept_and_its_class_reviewed(panel):
    """A miss is labelled the ordinary way, and the view is then reviewed for it too."""
    prepared(panel)
    panel.goto(4.0, 6.0, 200.0, 3000.0)
    pump(0.1)
    draw(panel, CLASS, 4.4, 4.5)
    draw(panel, "rise", 5.5, 5.9, 600.0, 800.0)
    panel.accept_view()
    assert 4.4 in {la.t0 for la in own_labels(panel)}
    assert panel.project.names == [CLASS, "rise"]
    assert panel.project.reviewed_rects(panel._paths())[0].classes == (0, 1)


def test_a_finished_view_can_be_taken_back_whole(panel):
    polygon = ((5.0, 500.0), (5.1, 500.0), (5.05, 900.0))
    prepared(panel, [box(5.0, 0.9, polygon=polygon), box(12.0, 0.7)])
    # drawn by audian with its outline, as any label's
    assert next(la for la in found_labels(panel) if la.t0 == 5.0).outline == polygon
    panel.goto(4.0, 6.0, 200.0, 3000.0)
    pump(0.1)
    panel.accept_view()
    accepted = next(la for la in own_labels(panel) if la.t0 == 5.0)
    assert accepted.outline == polygon
    panel.run_key("backspace")
    assert [la.t0 for la in own_labels(panel)] == [1.0]
    assert panel.project.reviewed_rects(panel._paths()) == []
    assert sorted(b.t0 for b in panel.candidates.values()) == [5.0, 12.0]
    assert 5.0 in {la.t0 for la in found_labels(panel)}


def test_a_rejected_view_can_be_taken_back_whole(panel):
    prepared(panel)
    panel.goto(4.0, 6.0, 200.0, 3000.0)
    pump(0.1)
    panel.reject_view()
    panel.run_key("backspace")
    assert panel.project.rejected_boxes(panel._paths()) == []
    assert panel.project.reviewed_rects(panel._paths()) == []
    assert sorted(b.t0 for b in panel.candidates.values()) == [5.0, 5.2, 12.0]


def test_a_new_proposal_keeps_what_the_reader_said_with_the_ordinary_tools(panel):
    """Find more forgot deletions, and proposed them again."""
    prepared(panel)
    labels = panel.browser.labels
    deleted = next(la for la in found_labels(panel) if la.t0 == 5.0)
    labels.remove(labels.index_of(deleted))
    moved = next(la for la in found_labels(panel) if la.t0 == 12.0)
    labels.set_geometry(labels.index_of(moved), 12.01, 12.09, 520.0, 880.0)
    propose(panel, [box(5.0, 0.9), box(12.0, 0.8), box(15.0, 0.8)], "model")
    assert [b.t0 for b in panel.project.rejected_boxes(panel._paths())] == [5.0]
    times = sorted(b.t0 for b in panel.candidates.values())
    assert times == [pytest.approx(12.01), 15.0]


def test_an_open_label_table_is_told_when_the_store_is_rewritten(panel, monkeypatch):
    """It addresses rows by position: Remove deleted a hand label instead of a guess."""
    calls = []
    monkeypatch.setattr(panel.browser, "refresh_label_table", lambda: calls.append(1))
    prepared(panel)
    assert calls


def test_guesses_never_reach_the_sidecar_on_any_autosave(panel):
    """The clean-up on closing was the only protection, and a crash skipped it."""
    prepared(panel)
    draw(panel, CLASS, 8.0, 8.1)
    panel.browser.save_labels()
    assert found_labels(panel)
    with open(panel.browser.labels_path(), newline="") as fp:
        categories = {row["category"] for row in csv.DictReader(fp)}
    assert categories == {CLASS}


def test_a_recording_with_only_guesses_writes_no_sidecar(panel):
    panel.browser.labels.clear()
    panel.project.config = config()
    panel.project.class_index(CLASS)
    propose(panel, [box(5.0, 0.9)], "model")
    panel.threshold = 0.1
    panel._write_found()
    assert found_labels(panel)
    panel.browser.save_labels()
    assert not Path(panel.browser.labels_path()).exists()


def test_a_guess_category_is_offered_nowhere(panel, window):
    """On the chips, it would be a place to draw a label that the next rewrite deletes."""
    prepared(panel)
    browser = window.browser()
    assert FOUND in {c.name for c in browser.labels.categories}
    assert FOUND not in {c.name for c in browser.labels.lasting_categories}
    browser.sync_category_state()
    assert browser.current_category != FOUND
    panel._fill_classes_menu()
    assert FOUND not in {action.text() for action in panel.classes_menu.actions()}


def test_settings_keep_only_the_readers_categories(panel, window, monkeypatch):
    prepared(panel)
    saved = {}
    import audian.databrowser as databrowser

    monkeypatch.setattr(
        databrowser,
        "categories_to_settings",
        lambda categories: saved.setdefault("names", [c.name for c in categories]),
    )
    window.browser().save_label_settings()
    assert CLASS in saved["names"] and FOUND not in saved["names"]


def test_paths_are_absolute_however_audian_was_started(panel, monkeypatch, tmp_path):
    """``audian rec.wav`` gave the runner, in another directory, a name it could not find."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(panel.browser.data.data, "file_paths", ["rec.wav"])
    assert panel._paths() == [str(tmp_path / "rec.wav")]


def test_a_view_with_a_hidden_channel_cannot_be_finished(panel, monkeypatch):
    """An event on it reached training unmasked and unboxed."""
    prepared(panel)
    monkeypatch.setattr(panel.browser, "visible_channels", lambda: [0])
    panel.goto(4.0, 6.0, 200.0, 3000.0)
    pump(0.1)
    panel.accept_view()
    assert panel.project.reviewed_rects(panel._paths()) == []
    assert len(panel.candidates) == 3
    assert "Channel 1 is hidden" in panel.statusw.text()


def test_a_hidden_channel_the_images_do_not_use_is_no_obstacle(panel, monkeypatch):
    from dataclasses import replace

    prepared(panel)
    panel.project.config = replace(panel.project.config, reduction="0")
    monkeypatch.setattr(panel.browser, "visible_channels", lambda: [0])
    panel.goto(4.0, 6.0, 200.0, 3000.0)
    pump(0.1)
    panel.accept_view()
    assert panel.project.reviewed_rects(panel._paths())


def test_a_view_says_which_classes_it_was_reviewed_for(panel):
    prepared(panel)
    panel.goto(4.0, 6.0, 200.0, 3000.0)
    pump(0.1)
    panel.accept_view()
    assert panel.project.reviewed_rects(panel._paths())[0].classes == (0,)


def test_a_new_class_is_still_proposed_inside_a_view_reviewed_for_the_old_one(panel):
    """It was dropped in silence, and its events taught as background."""
    prepared(panel)
    panel.goto(4.0, 6.0, 200.0, 3000.0)
    pump(0.1)
    panel.accept_view()
    panel.browser.labels.add_category("rise", KIND_SPAN)
    panel.project.class_index("rise")
    propose(panel, [box(5.5, 0.9, cls=1), box(5.5, 0.9, cls=0)], "model")
    assert [b.cls for b in panel.candidates.values()] == [1]


def test_a_label_with_no_band_is_masked_and_not_taught_as_background(panel):
    add_label(panel)
    labels = panel.browser.labels
    labels.add(Label(CLASS, KIND_SPAN, None, 2.0, 2.1))
    labels.add(Label(CLASS, KIND_POINT, None, 3.0))
    panel.train()
    hidden = panel.batch.requests[-1][1]["sources"][0]["hidden"]
    assert hidden[0][:2] == [2.0, 2.1]
    assert hidden[1][0] < 3.0 < hidden[1][1]


def test_a_class_that_fits_no_image_is_reported(panel):
    add_label(panel)
    panel.project.class_index("high")
    panel.train()
    panel.batch.answer(
        model=str(Path(panel.project.directory) / "m.pt"),
        threshold=0.5,
        metrics={"map50": 0.9},
        held_out=False,
        seconds=1.0,
        events_per_class=[10, 0],
    )
    assert "No event of high fits" in panel.statusw.text()


def test_candidates_above_the_cut_that_are_not_drawn_are_not_thrown_away(
    panel, monkeypatch
):
    """A view finished over them deleted them, unseen, unanswered."""
    monkeypatch.setattr(panel_module, "FOUND_LIMIT", 1)
    prepared(panel)
    panel.goto(4.0, 6.0, 200.0, 3000.0)
    pump(0.1)
    panel.accept_view()
    assert panel.project.reviewed_rects(panel._paths()) == []
    assert len(panel.candidates) == 3
    assert "not drawn" in panel.statusw.text()


def test_finishing_a_view_leaves_audians_undo_nothing_to_get_wrong(panel):
    """Undo removed one label of many, and said it was the label just added."""
    prepared(panel)
    panel.goto(4.0, 6.0, 200.0, 3000.0)
    pump(0.1)
    panel.accept_view()
    assert not panel.browser.labels.can_undo()


def test_a_class_removed_while_its_candidates_are_up_comes_back_when_one_is_accepted(
    panel,
):
    """It left a label under a name the vocabulary no longer had."""
    prepared(panel)
    labels = panel.browser.labels
    labels.remove_category(CLASS)
    panel.goto(4.0, 6.0, 200.0, 3000.0)
    pump(0.1)
    panel.accept_view()
    assert labels.category(CLASS) is not None
    assert {la.t0 for la in own_labels(panel)} == {5.0, 5.2}


def test_every_fifth_window_is_a_blind_one(panel):
    """The panel took the top of a fresh ranking each time, and never reached them."""
    prepared(panel, [box(float(t), 0.5) for t in range(1, 19, 2)])
    paths = panel._paths()
    for index in range(4):
        panel.project.mark_reviewed(paths, Rect(90.0 + index, 91.0 + index))
    seen = []
    original = panel.goto

    def remember(t0, t1, f0, f1):
        seen.append(t0)
        original(t0, t1, f0, f1)

    panel.goto = remember
    panel.next_window()
    assert seen
    t0 = seen[-1]
    grid_s = panel_module.tiling.grid_of(panel.project.config, RATE).tile_s
    # A chosen window starts just before its first candidate; this one need not.
    chosen = [b.t0 - 0.1 * grid_s for b in panel.candidates.values()]
    assert not any(abs(t0 - c) < 1e-6 for c in chosen)


def test_a_recording_can_join_another_project_and_train_with_it(panel, tmp_path):
    """A detector generalises from the recordings it has seen."""
    from audian.labels import LabelSet

    other_wav = tmp_path / "other.wav"
    sidecar = LabelSet()
    sidecar.add_category(CLASS, KIND_SPAN)
    sidecar.add(Label(CLASS, KIND_SPAN, None, 3.0, 3.1, 500.0, 900.0))
    from audian.labels import sidecar_path

    assert sidecar.write(sidecar_path(other_wav)) == ""
    theirs = project_.Project(directory=tmp_path / "other-specdetector")
    theirs.add_recording([other_wav])
    theirs.class_index(CLASS)
    theirs.config = config()
    theirs.save()

    add_label(panel)
    assert panel.join_project(theirs.directory)
    assert len(panel.project.recordings) == 2
    assert "one of 2" in panel.statusw.text()

    panel.train()
    sources = panel.batch.requests[-1][1]["sources"]
    by_file = {Path(s["paths"][0]).name: s for s in sources}
    assert [b["t0"] for b in by_file["other.wav"]["boxes"]] == [3.0]
    assert [b["t0"] for b in by_file[Path(panel._paths()[0]).name]["boxes"]] == [1.0]
    # The next time this recording is opened, it is in that project.
    assert panel._project_directory(panel._paths()[0]) == theirs.directory.resolve()


def test_a_recording_goes_home_when_the_project_it_joined_is_gone(panel, tmp_path):
    from audian_plugins.specdetector.panel import SETTINGS_KEY

    first = panel._paths()[0]
    panel_module.save_setting(
        SETTINGS_KEY, {"projects": {first: str(tmp_path / "gone")}}
    )
    assert panel._project_directory(first) == project_.default_directory(first)
    panel_module.save_setting(SETTINGS_KEY, {})


def test_joining_an_unreadable_project_is_refused(panel, tmp_path):
    (tmp_path / project_.PROJECT_FILE).write_text("{ not json")
    before = panel.project
    assert not panel.join_project(tmp_path)
    assert panel.project is before
    assert "cannot read" in panel.statusw.text()


def test_rejecting_many_candidates_writes_them_once(panel, monkeypatch):
    """With 20000 outlined candidates a write took more than a second per click."""
    prepared(panel, [box(5.0 + i, 0.9) for i in range(5)])
    writes = []
    monkeypatch.setattr(
        panel.project,
        "save_candidates",
        lambda *a, **k: writes.append(1),
    )
    reviewing(panel)
    for i in range(5):
        panel.candidate_clicked(lane(panel), 5.05 + i, 700.0)
    assert writes == []
    panel._persist_timer.stop()
    panel.about_to_flush_labels()
    assert len(writes) == 1


# ------------------------------------------------------------------- keys


def test_the_keys_are_claimed_only_while_review_can_act_on_them(panel):
    """Enter, Shift+Enter and Backspace stay audian's whenever review has no use for
    them: outside label mode, with no candidate in view, with nothing to take back."""
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QKeyEvent

    def key(code, modifiers=Qt.KeyboardModifier.NoModifier):
        return QKeyEvent(QEvent.Type.KeyPress, code, modifiers)

    prepared(panel)
    activate(panel)
    panel.goto(4.0, 6.0, 200.0, 3000.0)
    pump(0.1)
    panel.window().set_region_mode(DataBrowser.MODE_ZOOM)
    assert panel.router.command_for(key(Qt.Key.Key_Return)) is None
    panel.enter_label_mode()
    assert panel.router.command_for(key(Qt.Key.Key_Return)) == "enter"
    shifted = key(Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
    assert panel.router.command_for(shifted) == "shift_enter"
    assert panel.router.command_for(key(Qt.Key.Key_Backspace)) is None
    panel.candidate_clicked(lane(panel), 5.05, 700.0)
    assert panel.router.command_for(key(Qt.Key.Key_Backspace)) == "backspace"
    assert panel.router.command_for(key(Qt.Key.Key_Escape)) is None


def activate(panel):
    """No shortcut or active-window test means anything offscreen without this."""
    window = panel.window()
    window.activateWindow()
    pump(0.05)
    assert window.isActiveWindow()


def enter_key():
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QKeyEvent

    return QKeyEvent(
        QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier
    )


def test_enter_in_a_dialog_belongs_to_the_dialog(panel):
    """It was taken from every window, and finished a view behind the dialog."""
    from PySide6.QtWidgets import QDialog

    prepared(panel)
    reviewing(panel)
    panel.goto(4.0, 6.0, 200.0, 3000.0)
    pump(0.1)
    assert panel.router.command_for(enter_key()) == "enter"
    dialog = QDialog(panel.window())
    dialog.setModal(True)
    dialog.show()
    pump(0.05)
    assert panel.router.command_for(enter_key()) is None
    dialog.close()
    dialog.deleteLater()
    pump(0.05)


def test_enter_in_an_open_menu_belongs_to_the_menu(panel):
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QMenu

    prepared(panel)
    reviewing(panel)
    panel.goto(4.0, 6.0, 200.0, 3000.0)
    pump(0.1)
    menu = QMenu(panel.window())
    menu.addAction("something")
    menu.popup(QPoint(10, 10))
    pump(0.05)
    assert QApplication.activePopupWidget() is menu
    assert panel.router.command_for(enter_key()) is None
    menu.close()
    menu.deleteLater()
    pump(0.05)


def test_keys_do_nothing_when_another_window_is_active(panel):
    from PySide6.QtWidgets import QWidget

    prepared(panel)
    reviewing(panel)
    panel.goto(4.0, 6.0, 200.0, 3000.0)
    pump(0.1)
    other = QWidget()
    other.show()
    other.activateWindow()
    pump(0.05)
    assert not panel.window().isActiveWindow()
    assert panel.router.command_for(enter_key()) is None
    other.close()
    other.deleteLater()
    pump(0.05)


def test_the_digit_keys_stay_audians(panel):
    """They choose the category already, and one choice is enough."""
    assert not any(str(k[0]).endswith(("Key_1", "Key_9")) for k in KEY_TABLE)


def test_a_key_typed_in_a_text_field_is_not_taken(panel):
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QKeyEvent

    field = QLineEdit(panel)
    field.show()
    field.setFocus()
    panel.window().activateWindow()
    pump(0.05)
    if QApplication.focusWidget() is not field:
        pytest.skip("no focus in this environment")
    router = KeyRouter(lambda name: True, lambda name: None)
    press = QKeyEvent(
        QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier
    )
    assert router.command_for(press) is None
    field.deleteLater()


def test_find_looks_for_the_category_on_the_chips(panel):
    """The digit keys and the chips choose it; the panel follows, unless it is left out."""
    panel.browser.labels.add_category("rise", KIND_SPAN)
    panel.browser.current_category = CLASS
    assert panel.current_class() == CLASS
    panel.browser.current_category = "rise"
    assert panel.current_class() == "rise"
    panel.set_class_learnt("rise", False)
    assert panel.current_class() == ""


def test_a_machines_category_is_never_what_a_new_label_is(panel):
    prepared(panel)
    panel.browser.current_category = FOUND
    assert panel.current_class() != FOUND


def test_a_surface_turns_a_click_into_seconds_and_hertz(panel):
    surface = panel.surfaces[0]
    panel.goto(1.0, 3.0, 200.0, 3000.0)
    pump(0.1)
    vb = surface.vb
    scene = vb.mapViewToScene(QPointF(2.0, 1000.0))
    t, f = surface.to_data(scene)
    assert t == pytest.approx(2.0, abs=1e-6)
    assert f == pytest.approx(1000.0, abs=1e-3)
