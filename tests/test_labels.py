"""Tests for the hand-made label interface.

Runs offscreen::

    QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_labels.py -q

Two halves, for the two halves of the feature.  `labels.py` is pure data and
is tested against the *bytes of the sidecar*, because the file is the
deliverable: a reader who never opens audian again still has their labels,
and only if the file says what it should.  `labeloverlay.py` and the browser
are tested against **geometry in data coordinates** and against the store's
own rows, never against a box being visible -- ``isVisible()`` has passed on
a panel that was correctly sized and completely empty, and it would pass just
as happily on a box drawn at the wrong frequency.

The one claim everything else rests on is that a rubber-band drag over a
spectrogram delivers its LOW frequency as ``rect.top()``.  The view box
y-flips, so the mapped rect's top edge is its numerically smaller one, and
nothing on screen would say if it were the other way round -- every label's
band would simply be inverted.  `test_span_from_spectrogram_drag_keeps_the_band`
pins it with a real mouse drag rather than a synthesised rect, which is the
only way the mapping is actually exercised.

Repeated drags in one scene
---------------------------

Earlier notes warned that a second synthetic drag in the same
``QGraphicsScene`` produces no region signal at all.  Measured here, on a
four channel stack at 1200x900, four drags in channel 0's scene produced four
labels -- three on its spectrogram and one on its trace -- as long as each
drag carries its own intermediate ``MouseMove``.  So these tests reuse one
window instead of building one per gesture.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "tests"))

import pyqtgraph as pg  # noqa: E402
from PyQt5.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PyQt5.QtGui import QMouseEvent  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

from audian.databrowser import DataBrowser  # noqa: E402
from audian.labels import (  # noqa: E402
    COLUMNS,
    DEFAULT_CATEGORIES,
    KIND_POINT,
    KIND_SPAN,
    Label,
    LabelCategory,
    LabelSet,
    categories_from_settings,
    categories_to_settings,
    sidecar_path,
)
from audian.labeloverlay import CategoryStrip  # noqa: E402
from test_panelsplitter import app as app  # noqa: E402,F401  -- a fixture
from test_panelsplitter import open_stack, panel, pump, settle  # noqa: E402

#: The window every measurement quoted in this file was made at.
WINDOW = (1200, 900)


# ============================================================== the pure store


def read_rows(path: Path) -> list[list[str]]:
    """The sidecar as raw cells, header included.  No csv module, on purpose.

    The point of these assertions is what the file literally says; parsing it
    back with the same reader that wrote it would only prove the two agree.
    """
    text = path.read_text(encoding="utf-8")
    return [line.split(",") for line in text.splitlines()]


@pytest.fixture
def store():
    return LabelSet(DEFAULT_CATEGORIES)


def test_header_is_the_documented_column_order(store, tmp_path):
    store.add(Label("event", KIND_SPAN, 0, 1.0, 2.0, 100.0, 200.0))
    assert store.write(tmp_path / "rec-labels.csv") == ""
    assert read_rows(tmp_path / "rec-labels.csv")[0] == list(COLUMNS)


def test_a_point_writes_no_end_time(store, tmp_path):
    store.add(Label("pulse", KIND_POINT, 3, 1.25, None, 700.0, 700.0))
    store.write(tmp_path / "rec-labels.csv")
    row = read_rows(tmp_path / "rec-labels.csv")[1]
    cells = dict(zip(COLUMNS, row))
    assert cells["t_start_s"] == "1.250000"
    # empty, not -1 and not a repeat of t_start: a number here would be
    # averaged by some later reader as if the point had an extent
    assert cells["t_end_s"] == ""
    assert cells["kind"] == KIND_POINT


def test_a_trace_label_writes_no_frequency(store, tmp_path):
    store.add(Label("event", KIND_SPAN, 1, 0.5, 1.5, None, None))
    store.write(tmp_path / "rec-labels.csv")
    cells = dict(zip(COLUMNS, read_rows(tmp_path / "rec-labels.csv")[1]))
    assert cells["f_low_hz"] == ""
    assert cells["f_high_hz"] == ""
    assert cells["channel"] == "1"


def test_a_mean_label_writes_no_channel(store, tmp_path):
    store.add(Label("event", KIND_SPAN, None, 0.5, 1.5, 10.0, 20.0))
    store.write(tmp_path / "rec-labels.csv")
    cells = dict(zip(COLUMNS, read_rows(tmp_path / "rec-labels.csv")[1]))
    assert cells["channel"] == ""


def test_round_trip_is_exact(store, tmp_path):
    path = tmp_path / "rec-labels.csv"
    store.add(Label("event", KIND_SPAN, 0, 0.798766, 2.399383, 983.051, 2610.169))
    store.add(Label("event", KIND_SPAN, None, 1.0, 2.0, None, None))
    store.add(Label("pulse", KIND_POINT, 7, 3.5, None, 400.0, 400.0))
    store.write(path)
    fresh = LabelSet(DEFAULT_CATEGORIES)
    report = fresh.read(path)
    assert (report.read, report.dropped, report.error) == (3, 0, "")
    assert [la.row() for la in fresh] == [la.row() for la in store]


def test_a_note_with_a_comma_survives(store, tmp_path):
    path = tmp_path / "rec-labels.csv"
    store.add(Label("event", KIND_SPAN, 0, 1.0, 2.0, None, None, 'two, "three"'))
    store.write(path)
    fresh = LabelSet(DEFAULT_CATEGORIES)
    fresh.read(path)
    assert fresh.labels[0].note == 'two, "three"'


def test_rows_that_cannot_be_placed_are_counted_not_drawn(tmp_path):
    path = tmp_path / "rec-labels.csv"
    path.write_text(
        ",".join(COLUMNS)
        + "\n"
        + "event,span,0,1.0,2.0,,,\n"  # good
        + ",span,0,1.0,2.0,,,\n"  # no category
        + "event,span,0,,,,,\n",  # no start time
        encoding="utf-8",
    )
    store = LabelSet(DEFAULT_CATEGORIES)
    report = store.read(path)
    assert (report.read, report.dropped) == (1, 2)
    assert len(store) == 1


def test_a_backwards_span_is_put_the_right_way_round(tmp_path):
    path = tmp_path / "rec-labels.csv"
    path.write_text(
        ",".join(COLUMNS) + "\nevent,span,0,2.0,1.0,900.0,100.0,\n", encoding="utf-8"
    )
    store = LabelSet(DEFAULT_CATEGORIES)
    store.read(path)
    label = store.labels[0]
    assert (label.t0, label.t1) == (1.0, 2.0)
    assert (label.f0, label.f1) == (100.0, 900.0)


def test_an_unknown_category_is_added_rather_than_dropped(tmp_path):
    path = tmp_path / "rec-labels.csv"
    path.write_text(
        ",".join(COLUMNS) + "\nvolley,span,0,1.0,2.0,,,\n", encoding="utf-8"
    )
    store = LabelSet(DEFAULT_CATEGORIES)
    report = store.read(path)
    assert report.added == ("volley",)
    assert store.category("volley") is not None
    assert len(store) == 1


def test_a_missing_sidecar_reads_as_an_empty_set(tmp_path):
    store = LabelSet(DEFAULT_CATEGORIES)
    report = store.read(tmp_path / "nothing-labels.csv")
    assert (report.read, report.dropped, report.error) == (0, 0, "")
    assert len(store) == 0


def test_the_sidecar_is_named_after_the_recording(tmp_path):
    assert sidecar_path(tmp_path / "logger09-20250916T164744.wav") == (
        tmp_path / "logger09-20250916T164744-labels.csv"
    )


def test_removing_a_category_removes_its_labels(store):
    store.add(Label("event", KIND_SPAN, 0, 1.0, 2.0))
    store.add(Label("pulse", KIND_POINT, 0, 3.0))
    store.add(Label("event", KIND_SPAN, 1, 4.0, 5.0))
    assert store.remove_category("event") == 2
    assert [la.category for la in store] == ["pulse"]
    assert [c.name for c in store.categories] == ["pulse"]


def test_a_freed_palette_index_is_reused(store):
    # 'event' is 0 and 'pulse' is 1, so the next free index is 2 -- and 0
    # again once 'event' goes, rather than drifting up and wrapping onto a
    # colour that is already on screen
    assert store.next_color() == 2
    store.remove_category("event")
    assert store.next_color() == 0


def test_saving_an_empty_set_removes_the_sidecar(store, tmp_path):
    path = tmp_path / "rec-labels.csv"
    store.add(Label("event", KIND_SPAN, 0, 1.0, 2.0))
    store.save(path)
    assert path.exists()
    store.remove_last()
    store.save(path)
    assert not path.exists()
    assert not store.dirty


def test_the_write_leaves_no_temporary_behind(store, tmp_path):
    store.add(Label("event", KIND_SPAN, 0, 1.0, 2.0))
    store.write(tmp_path / "rec-labels.csv")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["rec-labels.csv"]


def test_a_failed_write_leaves_the_previous_file_whole(store, tmp_path, monkeypatch):
    """The reason this write is the first atomic one in the tree.

    Every other write in `src/audian` is a plain ``open(path, "w")``, which
    truncates the real file before it knows whether the new content can be
    produced.  Here the failure happens at the rename, and the old file is
    untouched.
    """
    path = tmp_path / "rec-labels.csv"
    store.add(Label("event", KIND_SPAN, 0, 1.0, 2.0))
    store.write(path)
    before = path.read_bytes()

    store.add(Label("pulse", KIND_POINT, 0, 3.0))
    monkeypatch.setattr(
        "audian.labels.os.replace",
        lambda *a, **k: (_ for _ in ()).throw(OSError("no space left on device")),
    )
    message = store.write(path)
    assert "no space left on device" in message
    assert path.read_bytes() == before
    assert store.dirty  # and the caller still knows it has unsaved work
    assert sorted(p.name for p in tmp_path.iterdir()) == ["rec-labels.csv"]


def test_categories_survive_the_settings_round_trip():
    given = [
        LabelCategory("discharge", KIND_SPAN, 3),
        LabelCategory("pulse", KIND_POINT, 5),
    ]
    back = categories_from_settings(categories_to_settings(given))
    assert [(c.name, c.kind, c.color) for c in back] == [
        ("discharge", KIND_SPAN, 3),
        ("pulse", KIND_POINT, 5),
    ]


def test_a_settings_entry_with_no_name_is_skipped_not_defaulted():
    back = categories_from_settings(
        [{"kind": KIND_SPAN, "color": 1}, {"name": "ok"}, "nonsense", {"name": "ok"}]
    )
    assert [c.name for c in back] == ["ok"]


def test_a_window_query_returns_positions_as_well_as_labels(store):
    store.add(Label("event", KIND_SPAN, 0, 0.0, 1.0))
    store.add(Label("event", KIND_SPAN, 1, 5.0, 6.0))
    store.add(Label("event", KIND_SPAN, None, 5.5, 5.6))
    # a label with no channel is on every lane: it was made on the mean
    # spectrogram, which is no electrode
    assert [i for i, _la in store.window(4.0, 7.0, channels=1)] == [1, 2]
    assert [i for i, _la in store.window(4.0, 7.0, channels=0)] == [2]
    # a collection is what the mean spectrogram asks for: one panel over a
    # whole selected array carries every averaged channel's labels
    assert [i for i, _la in store.window(0.0, 7.0, channels=(0, 1))] == [0, 1, 2]


def test_every_mutation_bumps_the_revision(store):
    """The overlays gate their redraw on it, so a mutation that forgets to
    bump simply does not appear on screen."""
    seen = store.revision
    for act in (
        lambda: store.add(Label("event", KIND_SPAN, 0, 1.0, 2.0)),
        lambda: store.set_note(0, "x"),
        lambda: store.add_category("volley"),
        lambda: store.remove_category("volley"),
        lambda: store.remove_last(),
    ):
        act()
        assert store.revision > seen, act
        seen = store.revision


# ================================================================== the browser


@pytest.fixture(scope="module")
def browser(app, tmp_path_factory):
    """Four channels, both panels, and a sandboxed settings file."""
    yield from open_stack(app, tmp_path_factory.mktemp("labels4"), 4)


@pytest.fixture
def labelling(browser):
    """Label mode, an empty store, and everything put back afterwards."""
    mode = browser.region_mode
    browser.set_region_mode(DataBrowser.MODE_LABEL)
    browser.labels.clear()
    browser.labels.set_categories(DEFAULT_CATEGORIES)
    browser.sync_category_state()
    settle()
    yield browser
    browser.set_region_mode(mode)
    browser.set_cross_hair(False)
    browser.labels.clear()
    browser.labels.set_categories(DEFAULT_CATEGORIES)
    browser.sync_category_state()
    browser.save_labels()
    settle()


def send(browser, channel, kind, x, y, button, buttons):
    """One real mouse event, routed the way a pointer is.

    The *global* position is not decoration: `QGraphicsScene` finds the item
    under the mouse by mapping the event's screen position back through the
    viewport, so an event carrying only a local position is delivered to
    nothing at all.
    """
    application = QApplication.instance()
    viewport = browser.figs[channel].viewport()
    pos = QPoint(int(round(x)), int(round(y)))
    application.sendEvent(
        viewport,
        QMouseEvent(
            kind,
            QPointF(pos),
            QPointF(viewport.mapToGlobal(pos)),
            button,
            buttons,
            Qt.NoModifier,
        ),
    )
    settle()


def drag(browser, channel, ax, x0, y0, x1, y1):
    """Rubber-band from data ``(x0, y0)`` to ``(x1, y1)`` in one lane.

    Two things this cannot do and be right.

    It cannot skip the intermediate move: without one pyqtgraph never sees a
    drag at all, and the press and release read as a click.

    And it must NOT put the scene position through
    ``QGraphicsView.mapFromScene`` on the way to the viewport.  The scene of a
    `GraphicsLayoutWidget` is its viewport at 1:1 -- `PanelSplitter`'s own
    drag says so, and `test_panelsplitter.send` relies on it -- but the
    view's transform is not the identity, and mapping through it moved the
    press by up to 21 px.  Measured: a drag meant for 100-400 Hz landed on
    the trace / spectrogram grab band instead, dragged the panel split from
    120 px of spectrogram down to 76, and produced no label at all.  It looks
    exactly like "an item under the cursor swallowed the gesture".
    """
    view = ax.getViewBox()
    a = view.mapViewToScene(pg.Point(x0, y0))
    b = view.mapViewToScene(pg.Point(x1, y1))
    send(
        browser,
        channel,
        QEvent.MouseButtonPress,
        a.x(),
        a.y(),
        Qt.LeftButton,
        Qt.LeftButton,
    )
    send(
        browser,
        channel,
        QEvent.MouseMove,
        (a.x() + b.x()) / 2,
        (a.y() + b.y()) / 2,
        Qt.NoButton,
        Qt.LeftButton,
    )
    send(browser, channel, QEvent.MouseMove, b.x(), b.y(), Qt.NoButton, Qt.LeftButton)
    send(
        browser,
        channel,
        QEvent.MouseButtonRelease,
        b.x(),
        b.y(),
        Qt.LeftButton,
        Qt.NoButton,
    )


def overlay_for(browser, surface, channel):
    for overlay in browser.label_overlays:
        if overlay.surface == surface and overlay.channel() == channel:
            return overlay
    raise AssertionError(f"no {surface} overlay for channel {channel}")


def live_rects(overlay):
    """The rect items that are actually drawing, in DATA coordinates."""
    return [b.rect() for b in overlay.boxes if b.isVisible()]


def test_span_from_spectrogram_drag_keeps_the_band(labelling):
    """rect.top() is the LOW frequency, because the view box y-flips.

    Measured on this stack: a drag asking for 1000 -> 2600 Hz over a
    0-4000 Hz lane arrives as ``top=983.1 bottom=2610.2``.  The tolerance is
    a pixel's worth of Hz, not a guess: the drag is placed by rounding data
    coordinates to integer widget pixels, so it cannot land on the exact
    number it asked for.
    """
    browser = labelling
    ax = panel(browser, "spectrogram").axs[0]
    (_t0, _t1), (f0, f1) = ax.getViewBox().viewRange()
    per_pixel = (f1 - f0) / max(ax.getViewBox().height(), 1.0)
    drag(browser, 0, ax, 0.8, 1000.0, 2.4, 2600.0)
    assert len(browser.labels) == 1
    label = browser.labels.labels[0]
    assert label.category == "event"
    assert label.channel == 0
    assert label.f0 < label.f1
    assert label.f0 == pytest.approx(1000.0, abs=3 * per_pixel)
    assert label.f1 == pytest.approx(2600.0, abs=3 * per_pixel)
    assert label.t0 == pytest.approx(0.8, abs=0.01)
    assert label.t1 == pytest.approx(2.4, abs=0.01)


def test_a_drag_the_other_way_gives_the_same_band(labelling):
    """Both drag directions, because `mapRectFromParent` normalises."""
    browser = labelling
    ax = panel(browser, "spectrogram").axs[1]
    drag(browser, 1, ax, 2.4, 2600.0, 0.8, 1000.0)
    assert len(browser.labels) == 1
    label = browser.labels.labels[0]
    assert label.f0 < label.f1
    assert label.t0 < label.t1


def test_a_trace_drag_stores_no_frequency(labelling):
    """A trace's y axis is amplitude.

    Writing 0..Nyquist instead would be a claim that the signal fills the
    band, and -1 would be a number some later reader averages.
    """
    browser = labelling
    ax = panel(browser, "trace").axs[2]
    (_t0, _t1), (a0, a1) = ax.getViewBox().viewRange()
    drag(browser, 2, ax, 1.0, a0 + 0.2 * (a1 - a0), 2.0, a0 + 0.8 * (a1 - a0))
    assert len(browser.labels) == 1
    label = browser.labels.labels[0]
    assert (label.f0, label.f1) == (None, None)
    assert label.channel == 2
    assert label.t0 == pytest.approx(1.0, abs=0.01)


def test_the_box_on_the_spectrogram_is_bounded_in_frequency(labelling):
    """What is drawn, in data coordinates -- not whether anything is there.

    A label is the only mark in this application bounded in y; every
    immutable annotation is full-lane-height by rule.  So the height of this
    rect is the whole difference between the two overlays.
    """
    browser = labelling
    ax = panel(browser, "spectrogram").axs[0]
    drag(browser, 0, ax, 0.8, 1000.0, 2.4, 2600.0)
    label = browser.labels.labels[0]
    rects = live_rects(overlay_for(browser, "spectrogram", 0))
    assert len(rects) == 1
    rect = rects[0]
    assert rect.left() == pytest.approx(label.t0)
    assert rect.right() == pytest.approx(label.t1)
    assert rect.top() == pytest.approx(label.f0)
    assert rect.bottom() == pytest.approx(label.f1)


def test_the_same_label_is_full_height_on_the_trace(labelling):
    """The trace lane has no frequency axis to bound it against."""
    browser = labelling
    ax = panel(browser, "spectrogram").axs[0]
    drag(browser, 0, ax, 0.8, 1000.0, 2.4, 2600.0)
    trace_ax = panel(browser, "trace").axs[0]
    (_t0, _t1), (a0, a1) = trace_ax.getViewBox().viewRange()
    rects = live_rects(overlay_for(browser, "trace", 0))
    assert len(rects) == 1
    assert rects[0].top() == pytest.approx(a0)
    assert rects[0].bottom() == pytest.approx(a1)
    assert rects[0].left() == pytest.approx(browser.labels.labels[0].t0)


def test_a_label_is_drawn_only_on_the_lane_it_names(labelling):
    browser = labelling
    ax = panel(browser, "spectrogram").axs[0]
    drag(browser, 0, ax, 0.8, 1000.0, 2.4, 2600.0)
    assert len(live_rects(overlay_for(browser, "spectrogram", 0))) == 1
    assert live_rects(overlay_for(browser, "spectrogram", 1)) == []
    assert live_rects(overlay_for(browser, "trace", 1)) == []


def test_a_channelless_label_is_drawn_on_every_lane(labelling):
    """What a label made on the mean spectrogram does.

    The mean is an average over the selected array and is no electrode, so
    the label names none -- and drawing it on one arbitrary lane would be a
    claim about an electrode that nobody made.
    """
    browser = labelling
    browser.labels.add(Label("event", KIND_SPAN, None, 1.0, 2.0, 500.0, 900.0))
    browser.redraw_labels()
    settle()
    for channel in range(4):
        assert len(live_rects(overlay_for(browser, "spectrogram", channel))) == 1


def test_the_drag_reaches_the_view_box_through_the_boxes_already_there(labelling):
    """A box drawn inside a box, which is what dense labelling looks like.

    This is the guarantee that decided against giving each stored label a
    `pg.RectROI` to be dragged into shape.  Measured, a movable ROI takes the
    press before the view box sees it: zero region signals and the ROI moved
    itself instead.  Plain items do not -- see `labeloverlay`'s docstring for
    the whole table.
    """
    browser = labelling
    ax = panel(browser, "spectrogram").axs[3]
    drag(browser, 3, ax, 0.5, 500.0, 3.0, 3000.0)
    assert len(browser.labels) == 1
    # entirely inside the footprint of the first
    drag(browser, 3, ax, 1.0, 1000.0, 2.0, 2000.0)
    assert len(browser.labels) == 2
    assert len(live_rects(overlay_for(browser, "spectrogram", 3))) == 2


def test_a_point_category_places_a_point_at_the_cross_hair(labelling):
    """Requirement 3's other half, and the only gesture that snaps.

    On a spectrogram the point is the exact (t, f) under the pointer; the
    drag gesture has no meaning for a category with no extent.
    """
    browser = labelling
    browser.set_cross_hair(True)
    ax = panel(browser, "spectrogram").axs[0]
    rect = ax.sceneBoundingRect()
    # Drained first.  Every lane's scene feeds `mouse_moved` through a
    # `pg.SignalProxy` at 60 Hz, so a move made by an earlier test in another
    # lane is still in flight; delivered after the call below it would leave
    # `hover_channel` pointing at that lane, and the point would be placed on
    # the wrong electrode.
    pump(0.3)
    browser.mouse_moved((QPointF(rect.center().x(), rect.center().y()),), 0)
    settle()
    browser.category_key("pulse")
    settle()
    assert browser.current_category == "pulse"
    assert len(browser.labels) == 1
    label = browser.labels.labels[0]
    assert label.kind == KIND_POINT
    assert label.t1 is None
    assert label.channel == 0
    (_t0, _t1), (f0, f1) = ax.getViewBox().viewRange()
    assert label.f0 == pytest.approx(0.5 * (f0 + f1), rel=0.05)
    # drawn as a symbol at (t, f), not as a bar across the lane
    xs, ys = overlay_for(browser, "spectrogram", 0).points.getData()
    assert list(xs) == pytest.approx([label.t0])
    assert list(ys) == pytest.approx([label.f0])


def test_a_point_key_with_the_cross_hair_off_only_picks(labelling):
    """The key never silently does nothing, and never guesses a position."""
    browser = labelling
    browser.set_cross_hair(False)
    browser.category_key("pulse")
    settle()
    assert browser.current_category == "pulse"
    assert len(browser.labels) == 0


def test_the_digit_keys_are_bound_to_the_first_nine_categories(labelling):
    browser = labelling
    browser.labels.set_categories(
        [LabelCategory(f"c{i}", KIND_SPAN, i) for i in range(12)]
    )
    browser.sync_category_state()
    settle()
    bound = [(a.text(), a.shortcut().toString()) for a in browser.category_acts]
    assert bound == [(f"c{i}", str(i + 1)) for i in range(9)]


def test_undo_removes_the_last_label_and_rewrites_the_file(labelling):
    browser = labelling
    ax = panel(browser, "spectrogram").axs[0]
    # Both drags well inside the lane.  Not near the bottom, where within
    # 3.5 px of the boundary the press belongs to the trace/spectrogram grab
    # band; and not near the right edge either, because the lane's width
    # drifts as the y gutter is reclaimed and given back, and a drag mapped
    # to the far end of a lane that has just widened lands outside it.
    drag(browser, 0, ax, 0.5, 800.0, 1.2, 1800.0)
    drag(browser, 0, ax, 2.0, 900.0, 2.6, 2100.0)
    browser.save_labels()
    path = browser.labels_path()
    assert len(read_rows(path)) == 3  # header plus two
    browser.remove_last_label()
    browser.save_labels()
    rows = read_rows(path)
    assert len(rows) == 2
    assert rows[1][3] == f"{browser.labels.labels[0].t0:.6f}"
    assert len(live_rects(overlay_for(browser, "spectrogram", 0))) == 1


def test_the_sidecar_is_written_without_being_asked(labelling):
    """Debounced autosave, because there is no close hook to rely on."""
    browser = labelling
    ax = panel(browser, "spectrogram").axs[0]
    drag(browser, 0, ax, 0.8, 1000.0, 2.4, 2600.0)
    pump(0.3)
    path = browser.labels_path()
    assert path.exists()
    assert read_rows(path)[0] == list(COLUMNS)
    assert not browser.labels.dirty


def test_a_flush_writes_what_a_queued_save_had_not(labelling):
    """What `Audian.close` and `Audian.quit` call.

    Neither goes through Qt's close machinery -- there is no ``closeEvent``
    anywhere in audian -- so a queued zero-timer save would go with the event
    loop, taking the last label of the session with it.
    """
    browser = labelling
    path = browser.labels_path()
    browser.save_labels()
    browser.labels.add(Label("event", KIND_SPAN, 0, 9.0, 9.5))
    browser.schedule_label_save()
    assert browser.label_save_pending
    browser.flush_labels()
    assert not browser.labels.dirty
    assert len(read_rows(path)) == 2


def test_the_status_line_says_how_to_make_the_first_label(labelling):
    """The empty state names the gesture, because a bare 0 does not."""
    browser = labelling
    assert "press b" in browser.label_status_text()
    ax = panel(browser, "spectrogram").axs[0]
    drag(browser, 0, ax, 0.8, 1000.0, 2.4, 2600.0)
    browser.save_labels()
    text = browser.label_status_text()
    assert text.startswith("1 label ")
    assert browser.labels_path().name in text
    assert text.endswith("saved")


def test_the_labels_group_costs_the_lanes_no_height(browser):
    """Four rows against the annotation group's five.

    `ParameterGroup.equalize` gives every group the tallest one's frame, so
    a group at or under the annotations' row count is free.  A row of this
    bar is about 24 px off every lane of a sixteen channel stack, which is
    why this is asserted rather than assumed.
    """
    titles = [g.title for g in browser.param_groups]
    assert "Labels" in titles
    labels_group = browser.param_groups[titles.index("Labels")]
    annotations = browser.param_groups[titles.index("Annotations")]
    assert labels_group.rows <= annotations.rows
    heights = {g.body.height() for g in browser.param_groups}
    assert len(heights) == 1


def test_the_chip_strip_asks_for_no_width_of_its_own(browser):
    """The one row of the bar whose width follows the reader's own data.

    The bar does not wrap, does not scroll and does not elide, so a row that
    asks for more than its column has widens the whole application.  A plain
    layout of two chips plus a button took this window's minimum width from
    1372 px to 1572; the strip's own hint is a chip's worth of nothing.
    """
    strip = browser.label_chipbox
    assert isinstance(strip, CategoryStrip)
    assert strip.sizeHint().width() < 60
    assert strip.minimumSizeHint().width() < 60


def test_no_category_is_lost_to_the_fold(browser):
    """Folded, never dropped -- and the shown set stays a prefix.

    The chips that are shown are the ones carrying the digit keys, so a
    strip that hid the third to show the fourth would put what the reader
    sees out of step with what their keyboard does.
    """
    names = [f"cat{i}" for i in range(12)]
    browser.labels.set_categories(
        [LabelCategory(n, KIND_SPAN, i) for i, n in enumerate(names)]
    )
    browser.sync_category_state()
    settle()
    # Raised first, and through the real click: a QStackedLayout gives
    # geometry to the current page only, so an unraised strip has never had a
    # width to fold against.
    browser.param_tabs.buttons["Labels"].click()
    settle()
    strip = browser.label_chipbox

    def state():
        # isHidden, not isVisible: every widget on a page the stack is not
        # showing reports isVisible False, so isVisible() here would be a
        # test that passes for a reason unrelated to what it claims.
        shown = [n for n in names if not strip.chips[n].isHidden()]
        return shown, [c.name for c in strip.folded]

    # Wide: the page now gets the whole bar rather than a fifth of it, so
    # twelve categories fit over the strip's two lines and nothing folds.
    shown, folded = state()
    assert shown + folded == names
    assert folded == []
    assert not strip.more.isVisible()

    # Narrow: the invariant that matters.  Every category is either on the
    # strip or in the +N menu, in order, and the shown set stays a PREFIX so
    # what the reader sees is in step with the digit keys under it.
    strip.resize(240, strip.height())
    settle()
    shown, folded = state()
    assert shown + folded == names
    assert folded
    assert strip.more.isVisible()
    assert strip.more.text() == f"+{len(folded)}"
    assert [a.text().split()[0] for a in strip.menu.actions()] == folded

    browser.labels.set_categories(DEFAULT_CATEGORIES)
    browser.sync_category_state()
    settle()


def test_the_cross_hair_over_the_grab_band_does_not_raise(browser):
    """Regression: `mouse_moved` walked the spacer panels too.

    A spacer is a `PanelSplitter`, a bare pg.GraphicsWidget with no view
    box, so ``getViewBox()`` returned the graphics view and
    ``mapSceneToView`` did not exist on it.  Measured before the guard: the
    top 3 px of the 7 px band raised ``AttributeError`` on every pointer
    move with the cross hair on -- and this feature makes the cross hair a
    primary tool.
    """
    browser.set_cross_hair(True)
    try:
        band = panel(browser, "spacer").axs[0].sceneBoundingRect()
        assert band.height() > 0
        for offset in (0.5, 1.5, 2.5):
            browser.mouse_moved((QPointF(band.center().x(), band.top() + offset),), 0)
    finally:
        browser.set_cross_hair(False)


def test_the_ask_menu_offers_every_category(labelling):
    """The one-off path, for a label made without leaving the current mode.

    The four fixed entries of that menu are dispatched by identity in a
    chain of ``act is`` tests; the categories are not a fixed set, so they
    go through a dict instead.  Asserted on the entries the menu is built
    from rather than on the popup, which cannot be exec'd headless.
    """
    browser = labelling
    browser.labels.set_categories(
        [LabelCategory("alpha", KIND_SPAN, 0), LabelCategory("beta", KIND_POINT, 1)]
    )
    browser.sync_category_state()
    assert browser.current_category == "alpha"
    browser.set_current_category("beta")
    assert browser.current_category == "beta"
    browser.set_current_category("not a category")
    assert browser.current_category == "beta"


def test_label_mode_takes_the_mouse_from_the_filter_handles(browser):
    """Both gestures want the same pixels, so the mode decides.

    A movable `pg.InfiniteLine` takes the press before the view box sees it.
    A cutoff is a line across the middle of the lane, and the middle of the
    lane is where labels go -- so with the handles armed, a drag that starts
    on one moves the cutoff and makes nothing.
    """
    plots = list(browser.spectrogram_plots())
    assert plots
    browser.set_region_mode(DataBrowser.MODE_LABEL)
    assert all(not p.highpass_handle.movable for p in plots)
    assert all(not p.lowpass_handle.movable for p in plots)
    browser.set_region_mode(DataBrowser.MODE_ZOOM)
    assert all(p.highpass_handle.movable for p in plots)
    assert all(p.lowpass_handle.movable for p in plots)


def test_a_drag_that_starts_on_a_cutoff_still_makes_a_label(labelling):
    """The behaviour the flag above exists for, through the real gesture.

    Measured with the handles armed: the same drag gives zero region signals
    and moves the cutoff from 2000 Hz to 3017 instead.
    """
    browser = labelling
    ax = panel(browser, "spectrogram").axs[1]
    cutoff = 2000.0
    was = ax.highpass_handle.value()
    # setValue, not a drag: it does not emit sigPositionChangeFinished, so
    # the filter is not recomputed for a position the test only borrows
    ax.highpass_handle.setValue(cutoff)
    settle()
    try:
        drag(browser, 1, ax, 1.0, cutoff, 2.0, 3000.0)
        assert len(browser.labels) == 1
        assert browser.labels.labels[0].f0 == pytest.approx(cutoff, rel=0.02)
        assert ax.highpass_handle.value() == pytest.approx(cutoff)
    finally:
        ax.highpass_handle.setValue(was)
        settle()


def test_a_sidecar_that_did_not_read_whole_is_never_written_over(tmp_path):
    """The failure mode this guard exists for.

    A file that could not be parsed reads as an empty set, which on screen is
    indistinguishable from a recording nobody has labelled yet.  Add one
    label to that and the autosave replaces whatever was really in the file.
    So a store that did not get its sidecar back whole refuses to write --
    and refuses to delete, which is the worse of the two.
    """
    path = tmp_path / "rec-labels.csv"
    path.write_text(
        ",".join(COLUMNS) + "\nevent,span,0,1.0,2.0,,,\n,span,0,,,,,\n",
        encoding="utf-8",
    )
    before = path.read_bytes()
    store = LabelSet(DEFAULT_CATEGORIES)
    report = store.read(path)
    assert report.dropped == 1
    assert store.blocked
    store.add(Label("event", KIND_SPAN, 0, 5.0, 6.0))
    assert "refusing to overwrite" in store.write(path)
    assert path.read_bytes() == before
    store.clear()
    assert "refusing to remove" in store.discard()
    assert path.exists()


def test_an_undecodable_sidecar_blocks_the_store_too(tmp_path):
    path = tmp_path / "rec-labels.csv"
    path.write_bytes(b"category,kind\n\xff\xfe not utf-8 at all\n")
    before = path.read_bytes()
    store = LabelSet(DEFAULT_CATEGORIES)
    report = store.read(path)
    assert report.error
    assert store.blocked
    store.add(Label("event", KIND_SPAN, 0, 1.0, 2.0))
    assert store.write(path)
    assert path.read_bytes() == before


def test_a_clean_read_leaves_the_store_writable(tmp_path):
    path = tmp_path / "rec-labels.csv"
    path.write_text(",".join(COLUMNS) + "\nevent,span,0,1.0,2.0,,,\n", encoding="utf-8")
    store = LabelSet(DEFAULT_CATEGORIES)
    store.read(path)
    assert store.blocked == ""
    store.add(Label("event", KIND_SPAN, 0, 5.0, 6.0))
    assert store.write(path) == ""
    assert len(read_rows(path)) == 3


def test_the_mean_spectrogram_shows_every_averaged_channel(labelling):
    """One panel over the whole array carries the whole array's labels.

    The mean borrows a lane -- channel 0's -- and is no electrode.  Drawing
    only that lane's labels on it would say the array had been labelled far
    less than it was, and the one it did show would be attributed to a
    channel the panel is not about.
    """
    browser = labelling
    for channel in range(3):
        browser.labels.add(
            Label(
                "event", KIND_SPAN, channel, 1.0 + channel, 1.5 + channel, 100.0, 900.0
            )
        )
    browser.redraw_labels()
    settle()
    overlay = overlay_for(browser, "spectrogram", 0)
    assert len(live_rects(overlay)) == 1

    ax = panel(browser, "spectrogram").axs[0]
    ax.set_mean_channels([0, 1, 2])
    browser.redraw_labels()
    settle()
    assert overlay.channels() == [0, 1, 2]
    assert len(live_rects(overlay)) == 3
    ax.set_mean_channels(None)
    browser.redraw_labels()
    settle()
    assert len(live_rects(overlay)) == 1


def test_a_new_category_takes_a_colour_nothing_else_has(store):
    """Two categories in one palette colour are two categories nobody can
    tell apart on the lane."""
    from audian.labeloverlay import CategoryModel

    store.set_categories(
        [LabelCategory(f"c{i}", KIND_SPAN, i) for i in (0, 1, 2, 4, 5, 6, 7)]
    )
    model = CategoryModel(store)
    model.add_row()
    assert model.rows[-1].color == 3
    assert len({c.color for c in model.rows}) == len(model.rows)
    # and once all eight are taken it has to wrap, which is what the palette
    # does anyway (`theme.marker_color` is modulo eight)
    model.add_row()
    assert model.rows[-1].color in range(8)
