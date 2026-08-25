"""Tests for the annotation panel: the chips, solo, the menu and the settings.

Runs offscreen::

    QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_annotationpanel.py -q

What the panel promises is that the ten layers of a session bundle each have
*exactly one* of everything -- one chip on the parameter bar, one entry in the
menu, one switch in the settings file -- and that all three are built by
walking the bundle, so none of them can drift from the others as the reader
changes.  On top of that sit the two gestures the reader actually spends the
day on: a click that leaves one layer showing, and one obvious way back.

Every browser here is a `DataBrowser` with only its annotation half built (see
`PanelBrowser`), because the rest of it needs a recording and none of what is
under test does.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from PyQt5.QtCore import QSize, Qt  # noqa: E402
from PyQt5.QtWidgets import (  # noqa: E402
    QApplication,
    QMainWindow,
    QMenu,
    QToolButton,
    QWidget,
)

from audian import theme  # noqa: E402
import audian.audian as audian_app  # noqa: E402
from audian.databrowser import (  # noqa: E402
    ANNOTATION_CHIP_ROWS,
    DataBrowser,
    ParameterGroup,
    annotation_chip_row,
)
from audian.eventoverlay import (  # noqa: E402
    LEGEND_H,
    LEGEND_W,
    SURFACE_ORDER,
    SURFACE_TRACE,
    AnnotationLayer,
    legend_icon,
    span_icon,
)
from audian.layers import KIND_SPAN  # noqa: E402

sys.path.insert(0, str(REPO / "tests"))
from test_session import pulse, simple, trial, write_bundle  # noqa: E402


def thin_bundle(directory: Path) -> Path:
    """A bundle whose writer left three of the five CSVs out.

    Five layers instead of ten, which is what tells a menu that walks the
    bundle from one that was written out by hand and happens to agree.
    """
    return write_bundle(
        directory,
        session_id="THIN",
        pulses=[pulse(1.0), pulse(3.0, "volley")],
        trials=[trial(1, "volley", 2.9, 3.1, 1)],
    )


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class PanelBrowser(DataBrowser):
    """A `DataBrowser` with only its annotation half constructed.

    `DataBrowser.__init__` opens a recording and builds fifty plots; none of
    that is what the panel is made of, and a test that needed a WAV to check
    a tool tip would be skipped on every machine that has no data beside it.
    So the widget is initialised as the plain `QWidget` it is and given the
    annotation attributes its own constructor would have set -- and every
    method under test is then the real one, off the real class.
    """

    def __init__(self):
        QWidget.__init__(self)
        self.said = []
        self.dialogs = 0
        self.annotations = AnnotationLayer(self)
        self.annotations.sigTableChanged.connect(self.rebuild_annotations)
        self.annotations.sigVisibilityChanged.connect(self.redraw_annotations)
        self.annotations.sigVisibilityChanged.connect(self.schedule_annotation_save)
        self.annotation_overlays = []
        self.annotation_group = None
        self.annotation_sourcew = None
        self.annotation_badgew = None
        self.annotation_rowboxes = []
        self.annotation_chips = []
        self.annotation_allw = None
        self.annotation_save_pending = False
        self.annotation_layer_chips = {}
        self.annotation_showw = None
        self.annotation_surfacew = {}
        self.annotation_hoverw = None
        self.annotation_layers_before_solo = None
        self.join_markers = []
        self.join_labels = []
        #: no recording behind this browser, so `recording_info()` has no
        #: opinion and the bundle's own provenance check stands
        self.data = None
        self.control_panel = None
        self.param_groups = []
        self.parambar = QWidget(self)
        self.param_groups = [self.setup_annotation_group()]

    def __del__(self):
        # DataBrowser.__del__ closes a recording and fifty plots; this one has
        # neither, and letting it try raises out of the garbage collector
        pass

    def notify(self, level, message):
        self.said.append((level, message))

    def open_annotations(self):
        # a key bound to a toggle must never reach this
        self.dialogs += 1

    def recording_path(self):
        return Path("rec.wav")


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


@pytest.fixture
def panel(app, scratch_settings, tmp_path):
    """A browser with the small ten-layer bundle loaded."""
    browser = PanelBrowser()
    browser.annotations.load(simple(tmp_path / "bundle").ref.metadata_path)
    app.processEvents()
    return browser


@pytest.fixture
def empty_panel(app, scratch_settings):
    """A browser with nothing loaded, as audian starts."""
    return PanelBrowser()


def chip_of(panel, layer_id) -> QToolButton:
    return panel.annotation_layer_chips[layer_id]


def switched_on(panel) -> list:
    return [i for i, on in panel.annotations.layers.items() if on]


# --- one chip per layer ------------------------------------------------------


def test_every_layer_of_the_bundle_gets_exactly_one_chip(panel):
    ids = [state.id for state in panel.annotations.layer_states()]
    assert len(ids) == 10
    assert list(panel.annotation_layer_chips) == ids
    assert len(panel.annotation_chips) == len(ids)


def test_a_layer_arrives_switched_on_or_off_as_the_bundle_says(panel):
    """`default_on` is read off the layer, never from a list in the browser.

    Localization runs cover 59% of the exp2 session and session events and the
    control track are not annotations of the waveform at all, so they arrive
    off -- but that is the reader's decision recorded in the bundle, and the
    panel only reports it.
    """
    for state in panel.annotations.layer_states():
        assert state.enabled == panel.annotations.bundle[state.id].default_on


def test_a_chip_is_drawn_with_the_pen_its_layer_is_drawn_with(panel):
    """The chip is the legend, so a span chip looks like a span.

    Compared against the very painters the overlay's legend uses, image for
    image: a chip that merely used the right colour would let a point layer
    and a span layer end up as the same glyph, and then the bar would need a
    key of its own to be read.
    """
    for state in panel.annotations.layer_states():
        drawn = chip_of(panel, state.id).icon().pixmap(LEGEND_W, LEGEND_H).toImage()
        if state.kind == KIND_SPAN:
            wanted = span_icon(
                state.color,
                panel.annotations.fill_alpha(state.id, SURFACE_TRACE),
                False,
            )
        elif state.kind == "point":
            wanted = legend_icon(state.color, True, False)
        else:
            continue
        assert drawn == wanted.pixmap(LEGEND_W, LEGEND_H).toImage(), state.id


def test_a_chips_icon_is_never_scaled(panel):
    """A legend icon scaled by one pixel loses the hairline that says dashed."""
    for chip in panel.annotation_chips:
        assert chip.iconSize() == QSize(LEGEND_W, LEGEND_H)


def test_the_count_a_chip_drops_survives_in_its_tool_tip(panel):
    """Ten chips carrying their counts are 1514 px of a 678 px field.

    So the count moves to the tip -- where a layer with no rows still reads
    `0 in session`, which is what tells "on and empty" from "switched off".
    """
    for state in panel.annotations.layer_states():
        tip = chip_of(panel, state.id).toolTip()
        assert f"{state.count} in session" in tip
        assert state.label in tip
        assert "solo" in tip.lower() or "alone" in tip


def test_the_chips_are_split_into_the_two_rows_by_their_own_track(panel):
    """Row membership is read off `Layer.track`, so a new track still lands."""
    sent, heard = panel.annotation_rowboxes
    for state in panel.annotations.layer_states():
        layer = panel.annotations.bundle[state.id]
        wanted = sent if annotation_chip_row(layer.track) == 0 else heard
        assert chip_of(panel, state.id).parent() is wanted
    assert annotation_chip_row("a track this build has never seen") == (
        len(ANNOTATION_CHIP_ROWS) - 1
    )


# --- solo, extend, and the way back ------------------------------------------


def test_clicking_a_chip_leaves_exactly_one_layer_drawing(panel):
    chip_of(panel, "pulses.volley").click()
    assert switched_on(panel) == ["pulses.volley"]
    assert panel.annotations.active_ids() == ["pulses.volley"]


def test_a_solo_moves_every_chip_and_not_only_the_one_clicked(panel):
    chip_of(panel, "pulses.volley").click()
    checked = [
        i for i, chip in panel.annotation_layer_chips.items() if chip.isChecked()
    ]
    assert checked == ["pulses.volley"]


def test_clicking_the_one_layer_that_is_left_puts_back_what_was_showing(panel):
    """The gesture that got the reader down to one layer is the way back up.

    Back to the set that was showing, not to every layer there is: the round
    trip is the channel rail's, whose solo is an overlay over the mute state
    and gives that state back untouched.
    """
    before = switched_on(panel)
    chip = chip_of(panel, "pulses.volley")
    chip.click()
    assert switched_on(panel) == ["pulses.volley"]
    chip.click()
    assert switched_on(panel) == before
    checked = [i for i, c in panel.annotation_layer_chips.items() if c.isChecked()]
    assert checked == before


def test_un_soloing_never_switches_on_a_layer_the_bundle_defaults_off(panel):
    """The three default-off layers are a decision, not an accident.

    Localization runs cover 59% of the exp2 session, and session events and
    the control track are not annotations of the waveform at all.  A round
    trip through a solo that switched them on would wash the lane and grow
    the control panel from 0 px to 74 px -- and `schedule_annotation_save`
    would then remember it.
    """
    off = [i for i, on in panel.annotations.layers.items() if not on]
    assert off == ["localization", "session_events", "controls"]
    chip = chip_of(panel, "trials.silence")
    chip.click()
    chip.click()
    assert [i for i, on in panel.annotations.layers.items() if not on] == off


def test_soloing_one_layer_after_another_still_comes_back_to_the_working_set(panel):
    """The set remembered is the reader's, never the solo before it."""
    before = switched_on(panel)
    panel.solo_annotation_layer("pulses.volley")
    panel.solo_annotation_layer("trials.silence")
    panel.solo_annotation_layer("trials.silence")
    assert switched_on(panel) == before


def test_a_hand_built_set_is_what_the_next_solo_comes_back_to(panel):
    """Switching a layer by hand makes that set the one to restore."""
    panel.set_annotation_layer("pulses.resting", False)
    wanted = switched_on(panel)
    panel.solo_annotation_layer("pulses.volley")
    panel.solo_annotation_layer("pulses.volley")
    assert switched_on(panel) == wanted


def test_the_set_a_solo_restores_is_the_set_that_is_saved(app, panel, scratch_settings):
    """What comes back has to survive the restart as well as the click."""
    before = switched_on(panel)
    panel.solo_annotation_layer("pulses.volley")
    panel.solo_annotation_layer("pulses.volley")
    app.processEvents()
    stored = json.loads(scratch_settings.read_text())[DataBrowser.ANNOTATION_SETTING]
    assert [i for i, on in stored["layers"].items() if on] == before


def test_a_modifier_click_switches_just_that_layer(panel):
    """The extend gesture: add a second layer to a solo without losing it."""
    panel.solo_annotation_layer("pulses.volley")
    panel.solo_annotation_layer("trials.silence", extend=True)
    assert switched_on(panel) == ["trials.silence", "pulses.volley"]
    panel.solo_annotation_layer("trials.silence", extend=True)
    assert switched_on(panel) == ["pulses.volley"]


def test_ctrl_and_shift_both_extend(app, panel, monkeypatch):
    """A reader reaching for either modifier is asking for the same thing."""
    panel.solo_annotation_layer("pulses.volley")
    for modifier in (Qt.ControlModifier, Qt.ShiftModifier):
        monkeypatch.setattr(QApplication, "keyboardModifiers", lambda m=modifier: m)
        panel.annotation_chip_clicked("trials.silence")
        assert "pulses.volley" in switched_on(panel)
        panel.annotation_chip_clicked("trials.silence")


def test_the_all_button_is_the_way_back_from_a_solo(panel):
    panel.solo_annotation_layer("pulses.volley")
    panel.annotation_allw.click()
    assert switched_on(panel) == [x.id for x in panel.annotations.bundle]


def test_the_all_button_says_how_many_layers_are_hidden(panel):
    """It stays enabled to say it: a disabled button gets no tool tip at all."""
    panel.solo_annotation_layer("pulses.volley")
    assert "9 hidden" in panel.annotation_allw.toolTip()
    assert panel.annotation_allw.isEnabled()
    panel.show_all_annotation_layers()
    assert "Every layer" in panel.annotation_allw.toolTip()


def test_a_solo_redraws_the_stack_once_however_many_layers_move(panel):
    """Ten switches, one signal: 32 lanes must not repaint ten times."""
    seen = []
    panel.annotations.sigVisibilityChanged.connect(lambda: seen.append(1))
    panel.solo_annotation_layer("pulses.volley")
    assert len(seen) == 1


def test_a_chip_that_asks_for_the_state_it_is_in_is_put_back(panel):
    """A checkable chip flips itself on click, before anyone reads the click.

    With one layer left on, clicking a *different* chip solos it and every
    other chip has to come back unchecked -- including the one whose own
    click did the flipping.
    """
    panel.solo_annotation_layer("pulses.volley")
    chip = chip_of(panel, "trials.silence")
    chip.click()
    assert chip.isChecked()
    assert not chip_of(panel, "pulses.volley").isChecked()


# --- what the pointer is on --------------------------------------------------


def test_the_readout_names_the_span_the_pointer_is_standing_in(panel):
    """The feature's only textual answer, and it used to say "nowhere near".

    `nearest()` measures a span from its START, so with the pointer at the
    middle of the 3 s localization run the readout read "(Δ -1.50 s)" -- a
    run the pointer is inside, reported as seconds away.  The question "which
    span am I in" is `spans_at()`, and it is asked here.
    """
    panel.solo_annotation_layer("localization")
    text = panel.annotation_under(3.0)
    assert "Localization runs" in text
    assert "inside, 1.500 s in" in text
    assert "Δ" not in text


def test_the_readout_reports_both_the_span_and_the_nearest_instant(panel):
    """They answer different questions and neither replaces the other."""
    text = panel.annotation_under(3.05)
    assert "Volley trials" in text and "inside," in text
    assert "Volley pulses" in text and "Δ -50.0 ms" in text


def test_outside_every_span_the_readout_still_measures_to_the_nearest_mark(panel):
    panel.solo_annotation_layer("pulses.volley")
    text = panel.annotation_under(3.05)
    assert "inside" not in text
    assert "Δ -50.0 ms" in text


def test_nothing_is_said_while_no_layer_is_switched_on(panel):
    panel.annotations.set_visible(False)
    assert panel.annotation_under(3.0) == ""


# --- nothing loaded ----------------------------------------------------------


@pytest.mark.parametrize(
    "gesture",
    [
        lambda b: b.solo_annotation_layer("pulses.volley"),
        lambda b: b.solo_annotation_layer("pulses.volley", extend=True),
        lambda b: b.show_all_annotation_layers(),
        lambda b: b.set_annotation_layer("pulses.volley", True),
        lambda b: b.toggle_annotations(),
    ],
)
def test_no_layer_toggle_opens_a_dialog_with_nothing_loaded(empty_panel, gesture):
    """A key bound to a toggle must not be able to raise a modal file chooser.

    Surprising from the keyboard, and a hang for anything driving audian with
    nobody in front of it.  Every gesture says so and returns instead.
    """
    gesture(empty_panel)
    assert empty_panel.dialogs == 0
    assert empty_panel.said and "Ctrl+Shift+A" in empty_panel.said[-1][1]


def test_the_group_hides_itself_while_nothing_is_loaded(empty_panel):
    assert not empty_panel.annotation_group.isVisible()
    assert empty_panel.annotation_layer_chips == {}


# --- what survives a restart -------------------------------------------------


def test_every_layer_is_written_under_the_one_settings_key(
    app, panel, scratch_settings
):
    panel.solo_annotation_layer("pulses.volley")
    app.processEvents()
    saved = json.loads(scratch_settings.read_text())
    assert list(saved) == [DataBrowser.ANNOTATION_SETTING]
    stored = saved[DataBrowser.ANNOTATION_SETTING]
    assert stored["version"] == DataBrowser.ANNOTATION_SETTING_VERSION
    assert set(stored["layers"]) == {x.id for x in panel.annotations.bundle}
    assert set(stored["surfaces"]) == set(SURFACE_ORDER)


def test_the_layer_switches_survive_a_save_and_a_restore(app, panel, tmp_path):
    panel.solo_annotation_layer("pulses.volley")
    panel.annotations.set_surface("spectrogram", False)
    app.processEvents()

    second = PanelBrowser()
    second.annotations.load(simple(tmp_path / "again").ref.metadata_path)
    second.restore_annotation_layers()
    assert switched_on(second) == ["pulses.volley"]
    assert second.annotations.surfaces["spectrogram"] is False
    assert [i for i, c in second.annotation_layer_chips.items() if c.isChecked()] == [
        "pulses.volley"
    ]


def test_one_settings_write_per_gesture_and_not_one_per_layer(app, panel, monkeypatch):
    """`save_setting` rewrites the whole file; a solo moves ten switches."""
    writes = []
    monkeypatch.setattr(audian_app, "save_setting", lambda k, v: writes.append(k))
    panel.solo_annotation_layer("pulses.volley")
    panel.solo_annotation_layer("trials.silence")
    app.processEvents()
    assert writes == [DataBrowser.ANNOTATION_SETTING]


def test_a_saved_layer_this_bundle_does_not_carry_is_not_resurrected(
    app, panel, tmp_path, scratch_settings
):
    """A switch for a layer that is not here would name something absent."""
    panel.solo_annotation_layer("pulses.volley")
    app.processEvents()
    saved = json.loads(scratch_settings.read_text())
    saved[DataBrowser.ANNOTATION_SETTING]["layers"]["trials.ghost"] = True
    scratch_settings.write_text(json.dumps(saved))

    second = PanelBrowser()
    second.annotations.load(thin_bundle(tmp_path / "thin"))
    second.restore_annotation_layers()
    assert "trials.ghost" not in second.annotations.layers
    assert "trials.ghost" not in second.annotation_layer_chips
    # and what this bundle does carry still came back off
    assert second.annotations.layers["trials.volley"] is False
    assert second.annotations.layers["pulses.volley"] is True


def test_a_layer_the_settings_never_saw_keeps_its_own_default(
    app, panel, tmp_path, scratch_settings
):
    """So a layer added in a later audian arrives visible, not silently off."""
    panel.solo_annotation_layer("pulses.volley")
    app.processEvents()
    saved = json.loads(scratch_settings.read_text())
    del saved[DataBrowser.ANNOTATION_SETTING]["layers"]["trials.silence"]
    scratch_settings.write_text(json.dumps(saved))

    second = PanelBrowser()
    bundle = simple(tmp_path / "later")
    second.annotations.load(bundle.ref.metadata_path)
    second.restore_annotation_layers()
    assert second.annotations.layers["trials.silence"] is True
    assert second.annotations.layers["pulses.volley"] is True
    assert second.annotations.layers["trials.volley"] is False


def test_the_master_switch_is_not_persisted(app, panel, tmp_path):
    """F8 is a glance, not a working set.

    A master left off and remembered comes back as an audian that draws no
    annotations at all and says nothing about why.
    """
    panel.annotations.set_visible(False)
    app.processEvents()

    second = PanelBrowser()
    second.annotations.load(simple(tmp_path / "master").ref.metadata_path)
    second.restore_annotation_layers()
    assert second.annotations.visible is True


def test_nothing_is_written_while_no_bundle_is_loaded(
    app, empty_panel, scratch_settings
):
    empty_panel.annotations.set_surface("trace", False)
    empty_panel.schedule_annotation_save()
    app.processEvents()
    assert not scratch_settings.exists()


def test_a_settings_value_from_another_version_is_ignored_not_half_read(
    app, panel, tmp_path, scratch_settings
):
    """`ANNOTATION_SETTING_VERSION` is written so it can be READ.

    The version is bumped when the shape of the value changes, so a value
    carrying another number holds keys this build would map onto the wrong
    switches -- and a reader who opens a bundle and finds three arbitrary
    layers off cannot tell that from a bug.  Defaults are a state audian can
    explain; a half-restored set is not.
    """
    panel.solo_annotation_layer("pulses.volley")
    app.processEvents()
    saved = json.loads(scratch_settings.read_text())
    saved[DataBrowser.ANNOTATION_SETTING]["version"] = (
        DataBrowser.ANNOTATION_SETTING_VERSION + 1
    )
    scratch_settings.write_text(json.dumps(saved))

    second = PanelBrowser()
    second.annotations.load(simple(tmp_path / "other").ref.metadata_path)
    second.restore_annotation_layers()
    assert switched_on(second) == [
        x.id for x in second.annotations.bundle if x.default_on
    ]
    assert second.annotations.surfaces["spectrogram"] is True


# --- the bundle against the recording that is open ---------------------------


class LoaderBrowser(PanelBrowser):
    """A panel browser that knows what the loader actually opened.

    The frame check is a comparison between two stated frame counts -- the
    bundle's and the loader's -- so the only thing it needs of a browser is a
    loader with a total, which is what this supplies.
    """

    def __init__(self, path, frames, rate, channels=1):
        super().__init__()
        self.data = SimpleNamespace(
            data=SimpleNamespace(
                rate=rate, frames=frames, channels=channels, start_indices=[0]
            )
        )
        self._recording = Path(path)

    def recording_path(self):
        return self._recording


def test_the_frame_check_is_made_against_what_the_loader_opened(
    app, tmp_path, scratch_settings
):
    """A split recording is not one file, and the check has to know it.

    On exp3 -- four WAVs opened as one recording -- the bundle's 173,809,152
    frames were compared against DR0000_0088.wav's 44,734,464 and a perfect
    match was reported as the wrong bundle.  A false alarm on the one check
    that exists to catch a genuinely wrong bundle is worse than no check.
    """
    soundfile = pytest.importorskip("soundfile")
    rate = 8000
    part = rate * 2
    first = tmp_path / "a.wav"
    soundfile.write(first, np.zeros(part, dtype=np.float32), rate)
    metadata = write_bundle(
        tmp_path / "split",
        session_id="SPLIT",
        alignment={
            "recording_file": None,
            "recording_files": '["a.wav", "b.wav"]',
            "recording_rate_hz": str(rate),
            "recording_frames": str(2 * part),
        },
        pulses=[pulse(1.0)],
        trials=[trial(1, "volley", 0.9, 1.1, 1)],
    )

    browser = LoaderBrowser(first, frames=2 * part, rate=rate)
    assert browser.load_annotations(metadata)
    # the bundle's own check read one file's header and did complain:
    assert any(
        "frames" in problem
        for problem in browser.annotations.bundle.recording_check.problems
    )
    # nothing of the sort reached the reader
    said = " ".join(message for _level, message in browser.said)
    assert "frames" not in said
    assert browser.annotations.recording_mismatch is None


def test_a_bundle_that_really_is_the_wrong_length_still_says_so(
    app, tmp_path, scratch_settings
):
    """The check has to keep its teeth: crying wolf and never barking are
    the same failure."""
    soundfile = pytest.importorskip("soundfile")
    rate = 8000
    first = tmp_path / "a.wav"
    soundfile.write(first, np.zeros(rate, dtype=np.float32), rate)
    metadata = write_bundle(
        tmp_path / "wrong",
        session_id="WRONG",
        alignment={
            "recording_file": '"a.wav"',
            "recording_rate_hz": str(rate),
            "recording_frames": "999999",
        },
        pulses=[pulse(0.5)],
    )
    browser = LoaderBrowser(first, frames=rate, rate=rate)
    browser.load_annotations(metadata)
    said = " ".join(message for _level, message in browser.said)
    assert "999999 frames" in said


# --- what the reader measured, said where a reader looks ---------------------


def test_the_badge_tool_tip_carries_the_readers_per_region_residuals(panel):
    """A global median is not a promise about the region on screen.

    exp3 states `residual_median_s = 9.5e-07` for the whole session and its
    last file is 38 ms out -- about ten volley pulses.  The reader measures
    that per region; the badge is where it is read.
    """
    tip = panel.annotation_badgew.toolTip()
    regions = panel.annotations.bundle.residuals
    assert len(regions)
    assert "Fit residual by region" in tip
    assert f"{1e3 * regions.tolerance_s:.3f} ms" in tip
    for region in regions:
        assert region.summary() in tip


def test_a_region_far_outside_the_fits_own_tolerance_is_said_out_loud(
    app, tmp_path, scratch_settings
):
    browser = PanelBrowser()
    metadata = write_bundle(
        tmp_path / "drift",
        session_id="DRIFT",
        pulses=[pulse(1.0, detected_time_s=1.05), pulse(2.0, detected_time_s=2.05)],
        trials=[trial(1, "volley", 0.9, 1.1, 1)],
    )
    browser.load_annotations(metadata)
    said = [message for level, message in browser.said if level == "warning"]
    assert any("match tolerance" in message for message in said)


# --- the retired alignment.csv reader ----------------------------------------


def test_the_superseded_events_reader_is_not_in_the_tree():
    """`events.py` read one alignment CSV keyed by a `#recording=` header.

    Nothing under src/ has imported it since the bundle reader landed, and a
    second reader that still loads is a second answer to "where does an
    annotation come from" -- which is what the manual followed into a load
    failure.
    """
    import importlib.util

    assert importlib.util.find_spec("audian.events") is None
    assert not (REPO / "src" / "audian" / "events.py").exists()
    assert not (REPO / "tests" / "test_events.py").exists()


def test_the_events_flag_documents_the_bundle_and_not_the_retired_csv(capsys):
    """A user who reads --help must reach the reader that is actually there."""
    with pytest.raises(SystemExit):
        audian_app.audian_cli(["--help"])
    text = capsys.readouterr().out
    assert "_metadata.toml" in text
    assert "#recording=" not in text
    assert "BUNDLE" in text


# --- the menu ----------------------------------------------------------------


class MenuHost(audian_app.Audian):
    """An `Audian` with only its annotation menu built, over one browser."""

    def __init__(self, browser):
        QMainWindow.__init__(self)

        class acts:
            pass

        self.acts = acts
        self.data_menus = []
        self.panel = browser
        # the browser has to be *inside* the window: `update_annotation_chips`
        # finds the menu to sync through `QWidget.window()`, and a parentless
        # browser is its own window
        browser.setParent(self)
        self.menu = QMenu(self)
        self.annotation_menu = self.setup_annotation_actions(self.menu)

    def __del__(self):
        pass

    def browser(self):
        return self.panel

    def require_browser(self):
        return self.panel


@pytest.fixture
def host(app, panel):
    window = MenuHost(panel)
    window.sync_annotation_actions(panel)
    return window


def test_every_layer_has_exactly_one_menu_action(host, panel):
    ids = [state.id for state in panel.annotations.layer_states()]
    assert list(host.acts.annotation_layers) == ids
    entries = [act for act in host.annotation_layer_menu.actions()]
    assert len(entries) == len(ids)


def test_the_menu_entries_are_walked_from_the_bundle(app, host, panel, tmp_path):
    """Never a list in the file: the chips and the menu are one set or none.

    A bundle whose writer left a CSV out has fewer layers, and the menu has to
    lose exactly those entries without anybody editing it.
    """
    panel.annotations.load(thin_bundle(tmp_path / "thin"))
    host.sync_annotation_actions(panel)
    ids = [state.id for state in panel.annotations.layer_states()]
    assert len(ids) == 5
    assert list(host.acts.annotation_layers) == ids
    assert list(panel.annotation_layer_chips) == ids


def test_the_menu_counts_follow_a_second_bundle_with_the_same_layers(
    app, host, panel, tmp_path
):
    """Every bundle this reader can open has the same ten layer ids.

    So keying the rebuild on the ids alone meant a second session left the
    menu showing the FIRST one's counts -- "Volley trials  (11)" over a
    bundle holding two -- while the chips beside them, rebuilt outright,
    showed the new ones.  The menu and the chips always agree.
    """
    assert "(1)" in host.acts.annotation_layers["trials.volley"].text()
    panel.annotations.load(
        simple(
            tmp_path / "second",
            trials=[
                trial(1, "volley", 2.9, 3.1, 1),
                trial(2, "volley", 3.9, 4.1, 1),
                trial(3, "silence", 5.0, 5.6, 0),
            ],
        ).ref.metadata_path
    )
    host.sync_annotation_actions(panel)
    assert "(2)" in host.acts.annotation_layers["trials.volley"].text()
    assert len(panel.annotations.bundle["trials.volley"]) == 2


def test_the_menu_names_every_surface_the_bar_does(host, panel):
    assert list(host.acts.annotation_surfaces) == list(SURFACE_ORDER)
    assert list(panel.annotation_surfacew) == list(SURFACE_ORDER)


def test_the_menu_check_follows_a_solo_driven_from_a_chip(host, panel):
    chip_of(panel, "pulses.volley").click()
    checked = [i for i, act in host.acts.annotation_layers.items() if act.isChecked()]
    assert checked == ["pulses.volley"]


def test_a_layer_switched_off_in_the_menu_leaves_the_stack(host, panel):
    host.acts.annotation_layers["trials.silence"].setChecked(False)
    assert panel.annotations.layers["trials.silence"] is False
    assert not chip_of(panel, "trials.silence").isChecked()


def test_the_menu_says_so_rather_than_going_empty_with_nothing_loaded(app, empty_panel):
    window = MenuHost(empty_panel)
    window.sync_annotation_actions(empty_panel)
    entries = window.annotation_layer_menu.actions()
    assert len(entries) == 1
    assert not entries[0].isEnabled()
    assert "no annotations" in entries[0].text()


def test_the_step_and_master_keys_are_still_bound(host):
    assert host.acts.toggle_annotations.shortcut().toString() == "F8"
    assert host.acts.show_all_annotation_layers.shortcut().toString() == "Shift+F8"
    assert host.acts.next_annotation.shortcut().toString() == "N"
    assert host.acts.previous_annotation.shortcut().toString() == "Shift+N"


# --- what the group costs the stack -----------------------------------------


def test_the_annotations_group_is_four_rows_and_never_a_fifth(app, panel):
    """Vertical space is the scarcest thing on screen.

    Measured offscreen at 1920x1080 with the real exp2 bundle: the group's
    grid is **104 px** (source 24, show 22, two chip rows 22 each, margins and
    spacing 14) against the 106 px the Filter group already spends, so the
    whole parameter bar stays at the 141 px it has with nothing loaded -- it
    used to grow to 180.  A fifth row would take 24 px out of every lane in
    the stack, so the ceiling is checked against a group built of four chip
    high rows rather than against a number that moves with the font.
    """
    group = panel.annotation_group
    assert group.rows == 2 + len(ANNOTATION_CHIP_ROWS)

    reference = ParameterGroup("Reference", panel.parambar)
    for _ in range(group.rows):
        row = QToolButton(reference)
        row.setFixedHeight(theme.CHIP_HEIGHT)
        reference.add_row("row", "", row)
    app.processEvents()
    assert (
        group.grid.totalSizeHint().height()
        <= reference.grid.totalSizeHint().height() + theme.S8
    )


def test_the_pointer_readout_costs_no_row_of_its_own(panel):
    """It rides at the end of the Show row: what the pointer is near is an
    aside, and a row of the bar is 24 px off every lane in the stack."""
    assert panel.annotation_hoverw.parent() is panel.annotation_showw.parent()


def test_a_chip_row_is_one_subline_and_never_wraps(app, panel):
    """The strip is bounded at one line per row, whatever is in it."""
    for box in panel.annotation_rowboxes:
        assert box.sizeHint().height() <= theme.CHIP_HEIGHT
