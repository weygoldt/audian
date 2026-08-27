"""Tests for the bottom bar's tabs, and for the width they were built to save.

Runs offscreen::

    QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_parameterbar.py -q

The bar's groups used to sit side by side in equal grid columns, which made
the bar's minimum width the SUM of theirs.  On a 14" laptop that was not a
crowded bar, it was a window that could not be made to fit the screen: with a
session bundle loaded -- the second step of this fork's own workflow -- the
window's minimum reached 2456 px and ``resize(1200, 900)`` returned a 2456 px
window.

So the assertions here are about **minimum widths and heights in pixels**,
which is the only thing that decides whether the window fits.  Nothing here
asserts that a group is visible: every widget on a page a ``QStackedLayout``
is not showing reports ``isVisible() == False``, so such a test would pass
for a reason unrelated to what it claims.
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

from PyQt5.QtCore import Qt  # noqa: E402
from PyQt5.QtWidgets import QSizePolicy  # noqa: E402

from audian import theme  # noqa: E402
from audian.databrowser import ParameterGroup, ParameterTabs  # noqa: E402
from test_panelsplitter import app as app  # noqa: E402,F401  -- a fixture
from test_panelsplitter import open_stack, pump, settle  # noqa: E402
from test_session import simple  # noqa: E402

#: The window every measurement quoted in this file was made at.
WINDOW = (1200, 900)


@pytest.fixture(scope="module")
def browser(app, tmp_path_factory):
    """Four channels, both panels, a sandboxed settings file."""
    yield from open_stack(app, tmp_path_factory.mktemp("parambar4"), 4)


@pytest.fixture(scope="module")
def wide_browser(app, tmp_path_factory):
    """Sixteen channels: the eel array, and the case whose cost is measured."""
    yield from open_stack(app, tmp_path_factory.mktemp("parambar16"), 16)


def group_minimums(view):
    return {g.title: g.minimumSizeHint().width() for g in view.param_groups}


# ------------------------------------------------------------------- width


def test_the_bar_asks_for_the_widest_group_and_not_for_all_of_them(browser):
    """The whole point of the stack.

    Measured on this fixture: the groups are Filter 288, Spectrogram 535,
    Audio 274, Annotations 529 and Labels 284, which sum to 1910 and peak at
    535.  Side by side the bar asked for 1445 px.
    """
    view = browser
    minimums = group_minimums(view)
    assert len(minimums) >= 4
    widest = max(minimums.values())
    total = sum(minimums.values())
    assert total > widest * 2  # or this test is not measuring anything
    bar = view.parambar.minimumSizeHint().width()
    # the widest page plus the bar's own left and right margins, and nothing
    # else -- in particular, not the sum
    assert bar == widest + 2 * theme.S8
    assert bar < total


def test_the_tab_strip_is_not_what_sets_the_floor(browser):
    """A strip that outgrew the widest page would re-create the problem.

    It is a real possibility: the strip's width is the sum of the group
    names, so a sixth group makes it wider.  Asserted so that the next group
    added cannot quietly become the binding term.
    """
    view = browser
    strip = view.param_tabs.strip.minimumSizeHint().width()
    assert strip <= max(group_minimums(view).values())


def test_a_loaded_bundle_does_not_widen_the_window(browser, tmp_path):
    """The case the reader actually hits.

    Loading a bundle grows the annotations group by one chip per layer.  Side
    by side that took the bar's minimum from 1445 px to 2452 and the window's
    from 1449 to 2456, and `resize(1200, 900)` then returned a 2456 px window.
    """
    view = browser
    before = view.parambar.minimumSizeHint().width()
    window = view.window()
    view.annotations.load(simple(tmp_path / "bundle").ref.metadata_path)
    settle()
    pump(0.5)
    try:
        assert len(view.annotation_chips) > 5
        after = view.parambar.minimumSizeHint().width()
        # the annotations page is now the widest, so the bar does grow --
        # but by the width of ONE page, not by the sum of all of them
        assert after == view.annotation_group.minimumSizeHint().width() + 2 * theme.S8
        assert after < 2 * before
        # and the bar is not what the window's minimum comes from
        assert window.minimumSizeHint().width() > after
    finally:
        view.annotations.clear()
        settle()
        pump(0.3)


def test_a_long_message_does_not_widen_the_window(browser):
    """A QLabel at the default policy publishes its whole text as a minimum.

    The status bar's transient message sits in the bar's stretch slot, so
    that minimum became the status bar's and then the window's.  Measured
    before the fix: a 137 character error took the status bar from 1184 px to
    2148.  The full line is still reachable -- it is the tool tip.
    """
    window = browser.window()
    window.notify("info", "short")
    settle()
    narrow = window.statusBar().minimumSizeHint().width()
    long = (
        "can not read annotations from "
        "/home/weygoldt/data/flona/site9/logger09-20250916T164744_metadata.toml: "
        "[Errno 2] No such file or directory"
    )
    window.notify("error", long)
    settle()
    assert window.statusBar().minimumSizeHint().width() < narrow + 200
    assert window.message_label.toolTip() == long
    assert window.message_label.text() != long  # it was elided
    assert window.message_label.text().endswith("…")
    window.clear_message()
    settle()
    assert window.message_label.toolTip() == ""


# ------------------------------------------------------------------ height


@pytest.mark.parametrize("fixture", ["browser", "wide_browser"])
def test_a_tab_change_never_takes_height_from_the_channel_stack(fixture, request):
    """The bar must not grow into the lanes when a tab is picked.

    `QStackedLayout` inherits `QLayout.expandingDirections()`, which is both
    directions, and `QWidgetItem` hands that to its widget -- so without an
    explicit `Fixed` vertical policy the bar takes every pixel the splitter
    will give it.  Measured without it: 402 px of bar against a 154 px size
    hint, and the stack's scroll viewport down to 247 px over 616 px of
    content.

    Asserted on the SCROLL AREA and on the bar's own height, not on lane
    height: the lanes keep their height while the viewport shrinks under
    them, so a lane-height assertion cannot see this defect at all.
    """
    view = request.getfixturevalue(fixture)
    bar = view.parambar
    scroll = view.stack_area.verticalScrollBar()
    assert bar.height() == bar.sizeHint().height()
    heights = {bar.height()}
    scrolls = {scroll.maximum()}
    for group in view.param_groups:
        view.param_tabs.buttons[group.title].click()
        settle()
        pump(0.2)
        assert view.param_tabs.current_title() == group.title
        assert bar.height() == bar.sizeHint().height()
        heights.add(bar.height())
        scrolls.add(scroll.maximum())
    # one height and one scroll range across every tab: the bar is inert
    assert len(heights) == 1
    assert len(scrolls) == 1


def test_the_bar_is_fixed_vertically(browser):
    """The one line the test above exists to protect, stated directly."""
    for widget in (browser.parambar, browser.param_tabs):
        assert widget.sizePolicy().verticalPolicy() == QSizePolicy.Fixed


# ------------------------------------------------------------------ content


def test_a_page_is_usable_the_first_time_its_tab_is_raised(browser):
    """A page that has never been current has never been given a width.

    Three things in this bar size themselves off their own `width()`: the
    Labels file row and the annotation pointer readout elide to it, and the
    category chip strip folds to it.  Measured on a page that had never been
    raised: 100 px against the 1162 it gets once it is.
    """
    view = browser
    view.param_tabs.buttons["Filter"].click()
    settle()
    view.update_label_status()
    settle()

    view.param_tabs.buttons["Editable labels"].click()
    settle()
    pump(0.3)
    # the file row says its whole line, not a sliver of it
    assert view.label_statusw.text() == view.label_status_text()
    assert not view.label_statusw.text().endswith("…")
    # and the chips are folded against the width the strip really has
    strip = view.label_chipbox
    names = [c.name for c in view.labels.categories]
    shown = [n for n in names if not strip.chips[n].isHidden()]
    folded = [c.name for c in strip.folded]
    assert shown + folded == names
    assert shown  # not every category swept into the +N menu


def test_every_group_keeps_its_name(browser):
    """The tab carries the name the caption used to.

    `ParameterGroup.title` is the one place it lives now, and the tab
    button's text is built from it -- so a group cannot end up with a tab
    that says something else.
    """
    view = browser
    for group in view.param_groups:
        assert group.title
        assert (
            view.param_tabs.buttons[group.title].text().startswith(group.title.upper())
        )


def test_a_lone_field_does_not_stretch_across_the_window(browser):
    """A group gets the whole bar now, not a fifth of it.

    Measured before the spacer column, on a 1449 px window: the Audio Source
    and Speed combo boxes were 1327 px each.  A combo box reading "1" a
    metre wide is not a control, it is a defect.  The sliders are the
    exception and say so themselves with `ParameterGroup.expanding`.
    """
    view = browser
    view.param_tabs.buttons["Audio"].click()
    settle()
    pump(0.3)
    page = view.param_groups[[g.title for g in view.param_groups].index("Audio")]
    assert page.width() > 600  # the page really does have the whole bar
    assert view.audiosrcw.width() < page.width() / 2
    assert view.audiofacw.width() < page.width() / 2

    view.param_tabs.buttons["Filter"].click()
    settle()
    pump(0.3)
    # ... and the slider, which asked, does get the width
    assert ParameterGroup.wants_width(view.hpsliderw)
    assert view.hpsliderw.width() > view.hpfw.width()


def test_the_tabs_are_not_in_the_keyboard_focus_chain(browser):
    """Space is play-window and the arrow keys nudge the view.

    A focused checkable QToolButton eats both, and a tab is clicked often --
    so the strip takes no focus, the way the channel rail's toggles do not.
    """
    for button in browser.param_tabs.buttons.values():
        assert button.focusPolicy() == Qt.NoFocus


def test_a_tab_marks_itself_when_its_page_is_saying_something_bad(browser):
    """READ-ONLY and SAVE FAILED are the two states that cost work.

    The mark is a glyph appended to the name and not a colour: the readers
    the spectrogram's colour map was chosen for would be told nothing by a
    hue.  The full line is the tab's tool tip.
    """
    view = browser
    tabs = view.param_tabs
    assert tabs.buttons["Editable labels"].text() == "EDITABLE LABELS"

    # the tab already carries its group's shortcuts; the alert is added to
    # that rather than replacing it
    quiet = tabs.buttons["Editable labels"].toolTip()
    assert "Show  F9" in quiet

    view.labels.blocked = "rec-editable-labels.csv could not be read (boom)"
    view.update_label_status()
    settle()
    try:
        assert tabs.buttons["Editable labels"].text() == "EDITABLE LABELS !"
        loud = tabs.buttons["Editable labels"].toolTip()
        assert "could not be read" in loud
        assert quiet in loud  # the shortcuts are still there
        assert "READ-ONLY" in view.label_status_text()
    finally:
        view.labels.blocked = ""
        view.update_label_status()
        settle()
    assert tabs.buttons["Editable labels"].text() == "EDITABLE LABELS"
    assert tabs.buttons["Editable labels"].toolTip() == quiet


def test_label_mode_raises_the_labels_tab(browser):
    """One of the two gestures worth moving the reader for.

    Pressing b, or clicking Label on the tool bar, says "I am about to write
    labels", and which category the next drag writes is on that page.
    """
    from audian.databrowser import DataBrowser

    view = browser
    mode = view.region_mode
    try:
        view.param_tabs.buttons["Filter"].click()
        settle()
        assert view.param_tabs.current_title() == "Filter"
        view.set_region_mode(DataBrowser.MODE_LABEL)
        settle()
        assert view.param_tabs.current_title() == "Editable labels"
        # and leaving the mode does not move them again
        view.set_region_mode(DataBrowser.MODE_ZOOM)
        settle()
        assert view.param_tabs.current_title() == "Editable labels"
    finally:
        view.set_region_mode(mode)
        settle()


def test_a_loaded_bundle_raises_the_annotations_tab(browser, tmp_path):
    """The other one: the reader just asked for that data."""
    view = browser
    view.param_tabs.buttons["Filter"].click()
    settle()
    try:
        view.annotations.load(simple(tmp_path / "raise").ref.metadata_path)
        settle()
        pump(0.5)
        assert view.param_tabs.current_title() == "Fixed labels"
        assert view.annotation_sourcew.text() != "—"
    finally:
        view.annotations.clear()
        settle()
        pump(0.3)


def test_the_open_tab_is_remembered(browser):
    """By name, and only when the reader picks one."""
    from audian.databrowser import DataBrowser

    view = browser
    view.param_tabs.buttons["Audio"].click()
    settle()
    saved = view.parameter_tab_settings()
    assert saved.get("tab") == "Audio"
    assert saved.get("version") == DataBrowser.PARAM_TAB_SETTING_VERSION
    # a name this recording does not have falls back rather than showing
    # nothing at all
    assert not view.param_tabs.show_group("Nonexistent")
    assert view.param_tabs.current_title() == "Audio"


def test_a_bar_that_was_never_built_is_not_a_crash(app, tmp_path):
    """`ParameterTabs` with nothing in it answers every question.

    The bar is built once, in `open()`, and a browser before that point --
    and the test fakes in the annotation suite, which never build one at all
    -- go through the same guards.
    """
    tabs = ParameterTabs()
    assert tabs.current_title() == ""
    assert not tabs.show_group("Filter")
    tabs.show_index(0)
    tabs.set_alert("Filter", True, "nothing to mark")
    tabs.polish()


# --------------------------------------------------------------- the chrome
#
# The parameter bar was the biggest single term in the window's minimum
# width, but it was never the only one.  Measured on this fixture, hiding one
# piece of chrome at a time: everything 1449, without the bar 1372 (the tool
# bar), without that 1176 (the status bar), without that 734.  So the bar
# alone bought 77 px and the reader saw nothing.


def test_the_tool_bar_publishes_a_constant_floor(browser):
    """And it is the tightest stage's width, not the current stage's.

    A hint that tracked the current stage would raise the window's own
    minimum every time the bar relaxed, and Qt would push the window back
    out again the moment it had the room.
    """
    tb = browser.window().toolbar
    widths = tb.stage_widths()
    assert len(widths) >= 4
    assert list(widths.values()) == sorted(widths.values(), reverse=True)
    tightest = min(widths.values())
    assert tb.minimumSizeHint().width() == tightest
    # and it does not move when the bar changes stage
    tb.fit()
    assert tb.minimumSizeHint().width() == tightest


def test_the_tool_bar_gives_up_words_before_controls(browser):
    """339 px of the bar was text wrapped around glyphs that were there.

    Measured: the six region-mode buttons and Fit Y take the bar from 1395 px
    to 1056 by dropping their words alone, with every control still on the
    bar and still one click away.
    """
    window = browser.window()
    widths = window.toolbar.stage_widths()
    names = list(widths)
    assert names[0] == "full"
    assert names[1] == "glyphs"
    # dropping the words is worth more than a fifth of the bar
    assert widths["full"] - widths["glyphs"] > 250
    # and nothing has folded yet at that stage
    assert "no-" not in names[1]


def test_every_button_that_loses_its_word_keeps_a_tool_tip_with_its_key(browser):
    """A tip that repeats the label the reader can no longer see is not a tip.

    Qt falls back to the mnemonic-stripped text when no tool tip is set, so
    these five read exactly "Zoom", "Play", "Analyze", "Save", "Request"
    before this -- naming neither what they do nor the key that does it.
    """
    window = browser.window()
    for button in list(window.mode_buttons) + [window.fit_y_button]:
        act = button.defaultAction()
        tip = act.toolTip()
        assert tip
        assert tip != act.text().replace("&", "")
        key = act.shortcut().toString()
        assert key
        # case-insensitively: QKeySequence normalises "z" to "Z", and the
        # tips print the key the way the reader types it
        assert f"({key})".lower() in tip.lower(), (act.text(), tip)


def test_the_two_region_play_glyphs_are_not_the_same_mark(browser):
    """`play_region` shared the transport's pixmap with `play_window`.

    The words told them apart; icon-only they were two identical triangles
    one hairline apart.
    """
    from audian.audian import glyph_pixmap, GLYPH_NORMAL

    a = glyph_pixmap("play", 16, GLYPH_NORMAL).toImage()
    b = glyph_pixmap("play-region", 16, GLYPH_NORMAL).toImage()
    assert a != b


def test_a_folded_control_is_still_reachable(browser):
    """Folded, never dropped -- and the menu names the shortcut the bar did not."""
    window = browser.window()
    original = window.width()
    try:
        window.resize(900, window.height())
        settle()
        pump(0.4)
        assert window._folded_groups
        assert window.overflow_button.isVisible()
        assert "Moved off the bar" in window.overflow_button.toolTip()
        window.build_overflow_menu()
        entries = [a for a in window.overflow_menu.actions() if not a.isSeparator()]
        assert entries
        # the same QActions the buttons carry, so the check state survives
        folded = {
            b.defaultAction()
            for b in list(window.mode_buttons)
            + list(window.panel_buttons)
            + [window.fit_y_button]
            if not b.isVisible()
        }
        assert folded
        assert folded <= set(entries) | {
            a.menu().menuAction() for a in entries if a.menu()
        } | set(entries)
    finally:
        window.resize(original, window.height())
        settle()
        pump(0.4)
    assert not window._folded_groups
    assert not window.overflow_button.isVisible()


def test_the_bar_comes_back_when_the_window_does(browser):
    """Every stage is reversible, or the ladder is a one-way trip."""
    window = browser.window()
    original = window.width()
    try:
        # down the ladder and back up, so no stage can be a one-way trip
        for width in (900, 1000, 1100, 1600):
            window.resize(width, window.height())
            settle()
            pump(0.4)
        # 1600 clears the widest stage, so the bar is back to what it was
        # built as: absolute, not "the same as before this test", because a
        # test that captured a compact bar would assert nothing at all
        assert window.toolbar.width() >= max(window.toolbar.stage_widths().values())
        assert all(
            b.toolButtonStyle() == Qt.ToolButtonTextBesideIcon
            for b in window.mode_buttons
        )
        assert all(b.isVisible() for b in window.mode_buttons)
        assert all(b.isVisible() for b in window.panel_buttons)
        assert not window._folded_groups
        assert not window.overflow_button.isVisible()
    finally:
        window.resize(original, window.height())
        settle()
        pump(0.3)


def test_the_idle_progress_slot_reserves_nothing(browser):
    """`set_progress(None)` hid the bar inside a 160 px box and left the box.

    Which is 160 px of the status bar's minimum width, held for the whole
    session against a job that is almost never running.
    """
    window = browser.window()
    window.set_progress(None)
    settle()
    assert not window.progress_box.isVisible()
    idle = window.statusBar().minimumSizeHint().width()
    window.set_progress(0.5, "reading")
    settle()
    assert window.progress_box.isVisible()
    assert window.statusBar().minimumSizeHint().width() > idle
    window.set_progress(None)
    settle()
    assert window.statusBar().minimumSizeHint().width() == idle


def test_the_crosshair_readouts_are_there_only_with_the_cross_hair(browser):
    """Three of the six say "--" for the life of a default session.

    Every write to them is inside `if self.cross_hair:`, and the cross hair
    is off by default and is not persisted.  Measured, they and their rules
    are 482 px of a 909 px row.
    """
    window = browser.window()
    window.set_cross_hair(False)
    settle()
    off = window.statusBar().minimumSizeHint().width()
    for field in window.CROSSHAIR_READOUTS:
        assert not window.readouts[field].isVisible()
    # the ones that carry a value without it stay
    assert window.readouts["t"].isVisible()

    window.set_cross_hair(True)
    settle()
    pump(0.2)
    for field in window.CROSSHAIR_READOUTS:
        assert window.readouts[field].isVisible()
    on = window.statusBar().minimumSizeHint().width()
    assert on > off + 300

    window.set_cross_hair(False)
    settle()
    pump(0.2)
    assert window.statusBar().minimumSizeHint().width() == off


def test_a_hidden_readout_comes_back_current(browser):
    """Nothing is lost by hiding one: the value is recorded and replayed."""
    window = browser.window()
    window.set_cross_hair(False)
    settle()
    window.set_readout("f", "f=1234Hz")
    window.set_cross_hair(True)
    settle()
    pump(0.2)
    assert "1234" in window.readouts["f"].text()
    window.set_cross_hair(False)
    settle()


def test_the_window_fits_a_laptop(browser):
    """The number this whole change is about.

    A 14 inch 1920x1080 panel at KDE's 150% scale is 1280 logical pixels
    wide, and at 175% it is 1097.  Before: 1449 with nothing loaded and 2456
    with a session bundle, so the window could not be made to fit at all.
    """
    window = browser.window()
    floor = window.minimumSizeHint().width()
    assert floor < 1097, floor
    # and every one of the three bars is out of the way, not just one
    assert window.toolbar.minimumSizeHint().width() < floor
    assert window.statusBar().minimumSizeHint().width() < floor
    assert browser.parambar.minimumSizeHint().width() < floor
