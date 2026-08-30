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

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QColor, QIcon  # noqa: E402
from PySide6.QtWidgets import QLabel, QSizePolicy, QToolButton  # noqa: E402

from audian import theme  # noqa: E402
from audian.databrowser import (  # noqa: E402
    LogSlider,
    ParameterGroup,
    ParameterTabs,
    SidePanel,
)
from audian.wraprow import WrapRow, pack_row  # noqa: E402
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


#: The `Sent` row of a loaded session bundle, which is the widest chip row
#: in the application and the one the wrapping was written for.
CHIP_NAMES = ("All", "Volley", "Baseline", "Silence",
              "Resting pulses", "Volley pulses")


def danger_pixels(button, checked=False):
    """How many pixels of the alert token a tab button really paints.

    Rendered rather than inspected: the whole point of the badge is that it
    is *visible*, and a property, a tool tip and an unpainted suffix are all
    things an implementation that draws nothing would still set.
    """
    icon = button.icon()
    state = QIcon.State.On if checked else QIcon.State.Off
    pixmap = icon.pixmap(button.iconSize(), QIcon.Mode.Normal, state)
    image = pixmap.toImage()
    want = QColor(theme.token("danger")).rgb()
    return sum(
        1
        for y in range(image.height())
        for x in range(image.width())
        if image.pixel(x, y) == want
    )


def chip_row(parent=None, names=CHIP_NAMES):
    """A `WrapRow` of chips the size the annotation chips really are."""
    row = WrapRow(parent)
    for name in names:
        chip = QToolButton(row)
        chip.setText(name)
        chip.setFont(theme.font_mono(theme.SIZE_SMALL_PT))
        chip.setFixedHeight(theme.CHIP_HEIGHT)
        row.add_widget(chip)
    return row


# ------------------------------------------------------ the wrapping row
#
# Unit tests: no browser, no window, only the `app` fixture -- the same
# shape as `test_a_bar_that_was_never_built_is_not_a_crash` below, and for
# the same reason a new module was not opened for them.


def test_a_wrapping_row_takes_another_line_rather_than_more_width(app):
    """The trade the side panel is made of.

    Every chip row in the bar today is a plain `QHBoxLayout`, so a row that
    outgrows its column widens the whole application -- the two annotation
    rows want 696 px and 555 px with a bundle loaded.  This one asks for a
    chip's worth of nothing and spends the height instead, which is the
    axis a panel has.

    Measured on these six chips: one line at 500 px, two at 400, three at
    320, four at 220, five at 180.
    """
    row = chip_row()
    row.resize(800, row.heightForWidth(800))
    settle()

    # it asks for nothing: the row can never be the term that sets a
    # minimum width, however many chips the reader's data puts in it
    assert row.minimumSizeHint().width() <= theme.S24
    assert row.sizeHint().width() <= theme.S24

    lines = {}
    for width in (500, 400, 320, 220, 180):
        row.resize(width, row.heightForWidth(width))
        settle()
        _placed, count = row.measured(width)
        lines[width] = count
        # the height is exactly the lines it says it needs
        assert row.heightForWidth(width) == (
            count * theme.CHIP_HEIGHT + (count - 1) * WrapRow.VGAP
        )
    # narrower is never fewer lines, and the ends really do differ
    counts = [lines[w] for w in (500, 400, 320, 220, 180)]
    assert counts == sorted(counts)
    assert counts[-1] > counts[0]


def test_a_wrapping_row_folds_nothing_and_overlaps_nothing(app):
    """The reason this is not `CategoryStrip`.

    `CategoryStrip` is two fixed lines and puts the overflow in a ``+N``
    menu, which is right for a vocabulary the reader chose and wrong for
    the annotation chips: those are the legend as well as the switch, and a
    layer whose chip is in a menu has no colour anybody can read off.
    """
    row = chip_row()
    for width in (500, 320, 220, 120, 60):
        row.resize(width, row.heightForWidth(width))
        settle()
        assert len(row.widgets()) == len(CHIP_NAMES)
        assert all(not chip.isHidden() for chip in row.widgets())
        placed = sorted(
            (chip.geometry().top(), chip.geometry().left(),
             chip.geometry().right())
            for chip in row.widgets()
        )
        for (top, left, _right), (prev_top, _prev_left, prev_right) in zip(
            placed[1:], placed
        ):
            if top == prev_top:
                assert left > prev_right, f"chips overlap at {width} px"


def test_the_packer_keeps_a_bounded_strip_a_prefix(app):
    """One line-breaker, two policies.

    `CategoryStrip` needs a bounded packer whose shown set is a *prefix* --
    the first nine categories are the ones with the digit keys, and a strip
    that hid the third to show the fourth would put the chips out of step
    with the keyboard.  A `WrapRow` needs an unbounded one that never drops
    anything.  Both are the same function.
    """
    items = [(name, 100) for name in "abcde"]

    placed, leftover = pack_row(items, 250, 4, rows=2)
    assert [key for key, _x, _line, _w in placed] == ["a", "b", "c", "d"]
    assert leftover == ["e"]  # a prefix is shown, the rest is left over

    # room held back on the last line for the fold marker
    placed, leftover = pack_row(items, 250, 4, rows=2, reserve=50)
    assert [key for key, _x, _line, _w in placed] == ["a", "b", "c"]
    assert leftover == ["d", "e"]

    # unbounded: three lines, and nothing left over
    placed, leftover = pack_row(items, 250, 4)
    assert len(placed) == len(items)
    assert leftover == []
    assert max(line for _k, _x, line, _w in placed) == 2

    # an item wider than the whole budget is placed anyway rather than
    # dropped, because an unbounded caller would rather overflow
    placed, leftover = pack_row([("wide", 400)], 250, 4)
    assert placed and leftover == []
    # ... where a bounded one folds it, which is what the +N menu is for
    placed, leftover = pack_row([("wide", 400)], 250, 4, rows=2)
    assert placed == [] and leftover == ["wide"]


# -------------------------------------------------------- the narrow row
#
# Also unit tests: `ParameterGroup` builds standalone, which is what
# `test_eventoverlay.py::test_equalize_regrows_a_group_whose_contents_changed`
# already relies on.


def stacked(narrow, fields=1):
    """A three-row group, wide or narrow, of `fields` fields per row."""
    group = ParameterGroup("Probe", None, caption=False, narrow=narrow)
    for name in ("High-pass", "Low-pass", "Band"):
        widgets = []
        for i in range(fields):
            box = QToolButton(group)
            box.setText(f"{name}{i}")
            box.setFixedHeight(theme.CHIP_HEIGHT)
            widgets.append(box)
        group.add_row(name, "H / ⇧H", *widgets)
    return group


def test_a_narrow_row_stacks_its_caption(app):
    """The caption goes above the field, on a grid line of its own.

    Measured, the captions of this bar are 25 to 114 px wide, and
    "HIGH-PASS  H / ⇧H" is 38 percent of a 300 px panel before any field
    exists.  Beside the field that is the whole reason a group cannot fit
    in a side panel; above it, it costs about 17 px of height, which is the
    axis a panel has.
    """
    wide = stacked(narrow=False)
    narrow = stacked(narrow=True)
    settle()

    # a row is a row either way: this is what the annotation suite counts
    assert wide.rows == narrow.rows == 3
    # but a narrow one spends two grid lines on it
    assert wide.gridrows == 3
    assert narrow.gridrows == 6

    # the caption really is on its own line, spanning, with the field below
    caption = narrow.grid.itemAtPosition(0, 0)
    field = narrow.grid.itemAtPosition(1, 0)
    assert caption is not None and field is not None
    assert isinstance(caption.widget(), QLabel)
    assert caption.widget().text().startswith("HIGH-PASS")
    assert field.widget() is not caption.widget()
    # nothing shares the caption's line
    assert narrow.grid.itemAtPosition(0, 1) is caption
    # ... where the wide one puts them side by side on one line
    assert wide.grid.itemAtPosition(0, 0).widget().text().startswith("HIGH-PASS")
    assert wide.grid.itemAtPosition(0, 1) is not None

    # and that is what it is for: narrower, taller
    assert narrow.minimumSizeHint().width() < wide.minimumSizeHint().width()
    assert narrow.grid.totalSizeHint().height() > wide.grid.totalSizeHint().height()


def test_a_narrow_group_keeps_the_row_contract(app):
    """`add_row` still returns caption-and-fields, and hiding one hides both.

    `set_pair_row_visible` hides a whole row through that return value, and
    a caption left beside nothing reads as a control that failed to load.
    In narrow mode the caption is on the line above rather than the column
    beside, and both lines have to collapse -- spacing included.
    """
    group = ParameterGroup("Probe", None, caption=False, narrow=True)
    field = QToolButton(group)
    field.setFixedHeight(theme.CHIP_HEIGHT)
    placed = group.add_row("Pair", "", field)
    tail = QToolButton(group)
    tail.setFixedHeight(theme.CHIP_HEIGHT)
    group.add_row("Speed", "", tail)
    settle()

    assert placed == [placed[0], field]  # caption first, then the fields
    full = group.grid.totalSizeHint().height()
    for widget in placed:
        widget.setVisible(False)
    settle()
    group.grid.invalidate()
    group.grid.activate()
    hidden = group.grid.totalSizeHint().height()
    # both grid lines go, and the spacing between them with them
    assert hidden < full
    assert full - hidden >= theme.CHIP_HEIGHT


def test_a_narrow_row_still_lets_a_field_ask_for_the_width(app):
    """`expanding()` is how width-is-resolution survives the stacking.

    The three sliders are wrapped in `ParameterGroup.expanding` precisely
    so they take the leftover; a narrow row hands that to the last field's
    column by default, but never over the head of a field that asked.
    """
    group = ParameterGroup("Probe", None, caption=False, narrow=True)
    slider = ParameterGroup.expanding(LogSlider(0, 48000, group))
    spin = QToolButton(group)
    spin.setText("1 kHz")
    group.add_row("High-pass", "H / ⇧H", slider, spin)
    settle()

    assert ParameterGroup.wants_width(slider)
    # the slider's column took it, not the last one
    assert group.grid.columnStretch(0) == 1
    assert group.grid.columnStretch(1) == 0
    assert group.grid.columnStretch(ParameterGroup.SPACER_COLUMN) == 0


# ------------------------------------------------------------------- width


def test_the_panel_asks_for_a_width_it_chose(browser):
    """The claim that replaces "the widest page plus two margins".

    That identity was the whole point of the `QStackedLayout` while the bar
    was a band under the stack: its minimum was the widest page's, never the
    sum, which is what got the window's floor from 2456 px back to 695.

    In a panel the arithmetic is better than that.  A `QScrollArea` with
    `setWidgetResizable(True)` decouples what the panel asks for from what
    its widest page needs, so the number is *chosen* -- and an explicit
    `setMinimumWidth` overrides a larger minimum coming up from the layout
    rather than being maxed with it, which is the property the whole width
    budget rests on.

    Measured on this fixture: the groups are Filter 163, Spectrogram 172,
    Audio 172, Fixed labels 110 and Editable labels 200, summing to 817 and
    peaking at 200, against a panel that asks for 220 whatever they do.
    """
    view = browser
    minimums = group_minimums(view)
    assert len(minimums) >= 4
    widest = max(minimums.values())
    total = sum(minimums.values())
    assert total > widest * 2  # or this test is not measuring anything

    panel = view.parambar
    assert panel.minimumWidth() == SidePanel.MIN_WIDTH
    # the floor is the panel's own number and not its contents' -- neither
    # the sum, which is what side-by-side groups cost, nor the widest page,
    # which is what the bar cost
    assert panel.minimumWidth() < total
    assert panel.minimumSizeHint().width() < widest
    # and every page fits inside it, so the scroll area is there for the
    # reader's own data rather than for the application's own controls
    assert widest <= SidePanel.MIN_WIDTH - 2 * theme.S8


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

    Behind tabs it cost one page instead of the sum, which was the fix: the
    bar went 517 -> 793 and the window 695 -> 797, so the annotations page
    became the widest thing in the application the moment a reader reached
    the second step of their own workflow.

    It now costs **nothing**.  The two chip rows wrap, so ten layers are
    ten more lines of height in a group that has height to spare rather
    than 696 px of width the window has to be wide enough for.  Measured:
    the bar stays at 517 and the window at 695 across the load, and the
    Fixed labels group stays at 407 where it used to reach 777 -- it is not
    even the widest page any more.  That is the assertion below, and it is
    stronger than the one it replaces.
    """
    view = browser
    before = view.parambar.minimumWidth()
    before_group = view.annotation_group.minimumSizeHint().width()
    before_window = view.window().minimumSizeHint().width()
    view.annotations.load(simple(tmp_path / "bundle").ref.metadata_path)
    settle()
    pump(0.5)
    try:
        assert len(view.annotation_chips) > 5
        # the chips are there, and they are all on screen -- wrapped, never
        # folded away, because they are the legend as well as the switch
        rows = view.annotation_rowboxes
        assert sum(len(row.widgets()) for row in rows) > len(view.annotation_chips)
        assert all(not chip.isHidden() for chip in view.annotation_chips)
        # and none of them cost a pixel of width
        assert view.annotation_group.minimumSizeHint().width() == before_group
        assert view.parambar.minimumWidth() == before
        assert view.window().minimumSizeHint().width() == before_window
        # the annotations page is not the widest page any more either
        assert before_group < max(group_minimums(view).values())
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
def test_a_tab_change_never_takes_width_from_the_channel_stack(fixture, request):
    """The same claim, on the axis the panel constrains.

    It used to be about height: `QStackedLayout` inherits
    `QLayout.expandingDirections()`, which is both directions, so without an
    explicit `Fixed` vertical policy the bar took every pixel the splitter
    would give it -- measured, 402 px of bar against a 154 px size hint and
    the stack's viewport down to 247 px over 616 px of content.

    Beside the lanes, height is exactly what the panel is *supposed* to
    take, and the height version of this test would pass while measuring
    nothing.  What must not move is the width: a tab change picks a page,
    and a page that asked for more width than the panel has would move the
    splitter under the reader's hand.

    Asserted on the SCROLL AREA and on the panel's own width, not on lane
    width: the lanes keep their width while the viewport narrows under
    them, so a lane-width assertion cannot see this defect at all.
    """
    view = request.getfixturevalue(fixture)
    panel = view.parambar
    viewport = view.stack_area.viewport()
    widths = {panel.width()}
    canvas = {viewport.width()}
    floors = {view.window().minimumSizeHint().width()}
    for group in view.param_groups:
        view.param_tabs.buttons[group.title].click()
        settle()
        pump(0.2)
        assert view.param_tabs.current_title() == group.title
        widths.add(panel.width())
        canvas.add(viewport.width())
        floors.add(view.window().minimumSizeHint().width())
    # One width across every tab, on both sides of the handle, and one
    # window floor: the panel is inert horizontally, which is the claim.
    assert len(widths) == 1, sorted(widths)
    assert len(canvas) == 1, sorted(canvas)
    assert len(floors) == 1, sorted(floors)


def test_the_panel_gives_its_height_back_to_the_stack(wide_browser):
    """The feature, measured on the case it is for.

    The bar was 168 px of the scarce axis -- five lanes at
    `theme.CHANNEL_DENSE_HEIGHT` -- spent whether or not anybody was looking
    at it, and there was no action anywhere in audian to hide it.  Measured
    on the sixteen channel stack: the scroll viewport was 483 px tall with
    the bar under it and is 651 with the panel beside it, exactly the 168
    the bar cost, and the range the reader has to scroll through to reach
    the last lane fell from 196 px to 28.
    """
    view = wide_browser
    settle()
    pump(0.3)
    viewport = view.stack_area.viewport()
    # the bar was 168 px tall, measured, and this is where it went
    assert viewport.height() >= 483 + 168, viewport.height()
    # and the lanes it bought are lanes the reader no longer scrolls to
    assert view.stack_area.verticalScrollBar().maximum() < 196


def test_the_panel_is_fixed_on_the_axis_it_constrains(browser):
    """The one line the test above exists to protect, stated directly.

    The constrained axis inverted with the move.  Under the stack the bar
    had to be `Fixed` vertically or it ate the lanes; beside them the panel
    must be free vertically -- that is the whole point -- and must not grow
    horizontally, which is what its own minimum and the scroll area under
    it enforce.
    """
    panel = browser.parambar
    assert panel.sizePolicy().verticalPolicy() == QSizePolicy.Policy.Expanding
    assert panel.sizePolicy().horizontalPolicy() != QSizePolicy.Policy.Expanding
    assert panel.minimumWidth() == SidePanel.MIN_WIDTH
    # the pages scroll rather than widening the panel to fit
    assert browser.param_tabs.area is not None
    assert browser.param_tabs.area.widgetResizable()


def test_a_hidden_panel_costs_the_window_no_width(browser):
    """Hidden means hidden, and that is load-bearing rather than tidy.

    A `QSplitter` child dragged to zero is *visible at width zero*, and Qt
    still charges its whole minimum to the splitter -- measured in isolation,
    524 px against the 300 the same splitter reports once the child is
    genuinely hidden.  So a panel closed by collapsing it would keep the
    laptop floor charged for a panel the reader believes is gone.

    Measured here: the browser asks for 337 px with the panel shown
    (110 of canvas + 220 of panel + 7 of handle) and 110 with it hidden,
    which is exactly what a browser with no panel at all would ask for.
    """
    view = browser
    panel = view.parambar
    shown = view.minimumSizeHint().width()
    try:
        panel.setVisible(False)
        settle()
        pump(0.2)
        hidden = view.minimumSizeHint().width()
        # the panel and the handle both stop counting
        assert hidden < shown
        assert hidden <= shown - SidePanel.MIN_WIDTH
        # and the canvas is all there is left
        assert hidden == view.side_split.widget(0).minimumSizeHint().width()
        assert view.side_split.handle(1).isHidden()
    finally:
        panel.setVisible(True)
        settle()
        pump(0.2)
    assert view.minimumSizeHint().width() == shown


# ------------------------------------------------------------------ content


def test_a_page_is_usable_the_first_time_its_tab_is_raised(browser):
    """A page that has never been current has never been given a width.

    Three things in this bar size themselves off their own `width()`: the
    Labels file row and the annotation pointer readout elide to it, and the
    category chip strip folds to it.  Measured on a page that had never been
    raised: 100 px against the 1162 it gets once it is.

    The assertion is on the *width* rather than on an un-elided line, and
    that is the move's doing.  A 1200 px band could show the whole file row;
    a 360 px panel cannot, and the row was always allowed to elide -- it is
    `QSizePolicy.Ignored` on purpose and keeps its full text in the tool
    tip.  So "not elided" was only ever a proxy for "the page has been given
    a real width", and now that the two have come apart the real claim is
    the one worth asserting.
    """
    view = browser
    view.param_tabs.buttons["Filter"].click()
    settle()
    view.update_label_status()
    settle()

    view.param_tabs.buttons["Editable labels"].click()
    settle()
    pump(0.3)
    # the page was given the panel's width, not the 100 px of a page that
    # has never been current
    content = view.parambar.width() - 2 * theme.S8
    assert view.label_group.width() == content
    assert view.label_statusw.width() > 100
    # the row said what it could of its line, and kept the whole of it
    assert view.label_statusw.toolTip() == view.label_status_text()
    assert view.label_statusw.text()
    assert view.label_status_text().startswith(
        view.label_statusw.text().rstrip("…")
    )
    # and the chips are folded against the width the strip really has
    strip = view.label_chipbox
    names = [c.name for c in view.labels.categories]
    shown = [n for n in names if not strip.chips[n].isHidden()]
    folded = [c.name for c in strip.folded]
    assert shown + folded == names
    assert shown  # not every category swept into the +N menu


def test_every_group_keeps_its_name(browser):
    """The tab carries the name the caption used to.

    `ParameterGroup.title` is the one place it lives now, and the tab's tool
    tip is built from it -- so a group cannot end up with a tab that says
    something else.

    The tool tip and not the text: the strip is icon-only, because six
    marks cost 220 px where six words cost 579 and a panel has 344.  Which
    means the tool tip is the *only* place the name is written, so it is
    asserted here rather than assumed, and every group must have a mark of
    its own -- two groups sharing one glyph would be two tabs a reader
    cannot tell apart.
    """
    view = browser
    marks = {}
    for group in view.param_groups:
        assert group.title
        button = view.param_tabs.buttons[group.title]
        assert not button.text()  # icon-only, so nothing invisible is set
        assert button.toolTip().startswith(group.title)
        kind = view.param_tabs.kinds[group.title]
        assert kind, group.title
        assert kind not in marks, f"{group.title} and {marks.get(kind)} share {kind}"
        marks[kind] = group.title
        assert not button.icon().isNull()


def test_a_lone_field_fills_its_row_but_never_the_window(browser):
    """The rule inverted with the panel, and the defect behind it did not.

    Measured before the spacer column, on a 1449 px window: the Audio Source
    and Speed combo boxes were 1327 px each.  A combo box reading "1" a
    metre wide is not a control, it is a defect, and `SPACER_COLUMN` is what
    stopped it -- the leftover goes to a dead column unless a field asks for
    it by name with `ParameterGroup.expanding`.

    A 344 px panel row has the opposite problem.  A combo box that stops at
    its 156 px size hint with 188 px of nothing beside it reads as a control
    that failed to lay out, so a narrow row hands the leftover to its last
    field.  What has not changed is the thing the spacer column was really
    defending: the field is as wide as its *row*, and the row is as wide as
    the panel, which cannot reach across the window however wide the window
    gets.  That is what is asserted here.
    """
    view = browser
    view.param_tabs.buttons["Audio"].click()
    settle()
    pump(0.3)
    page = view.param_groups[[g.title for g in view.param_groups].index("Audio")]
    # the page gets the panel's whole content width -- the absolute 600 this
    # used to assert was a bottom-bar number, and a panel narrower than the
    # window is the entire point
    content = view.parambar.width() - 2 * theme.S8
    assert page.width() == content
    assert page.width() >= SidePanel.MIN_WIDTH - 2 * theme.S8
    # the lone field fills its row ...
    assert view.audiosrcw.width() > page.width() / 2
    assert view.audiofacw.width() > page.width() / 2
    # ... and the row is the panel, not the window: the 1327 px combo box
    # is unreachable because the panel is a fraction of the window's width
    window = view.window().width()
    assert view.audiosrcw.width() <= content
    assert view.audiosrcw.width() < window / 2

    view.param_tabs.buttons["Filter"].click()
    settle()
    pump(0.3)
    # ... and the slider, which asked, still gets the width over its
    # neighbour rather than sharing it
    assert ParameterGroup.wants_width(view.hpsliderw)
    assert view.hpsliderw.width() > view.hpfw.width()


def test_the_tabs_are_not_in_the_keyboard_focus_chain(browser):
    """Space is play-window and the arrow keys nudge the view.

    A focused checkable QToolButton eats both, and a tab is clicked often --
    so the strip takes no focus, the way the channel rail's toggles do not.
    """
    for button in browser.param_tabs.buttons.values():
        assert button.focusPolicy() == Qt.FocusPolicy.NoFocus


def test_a_tab_marks_itself_when_its_page_is_saying_something_bad(browser):
    """READ-ONLY and SAVE FAILED are the two states that cost work.

    The mark is a shape and not a colour: the readers the spectrogram's
    colour map was chosen for would be told nothing by a hue.  It used to
    be a "!" appended to the tab's name; an icon tab has no name to append
    to, so it is a dot painted into the corner of the glyph -- the same
    statement in the one channel an icon has.

    The dot goes in the BOTTOM right.  The two label marks are told apart
    by a tag in their top right, so a badge there would hide which tab it
    is marking.

    Asserted on the painted pixels and not only on the property, because a
    property is exactly what an implementation that draws nothing would
    still set -- which is what keeping the invisible " !" suffix on an
    icon-only button would have done, measured: same 35 px hint, same ink,
    and `text()` still returning "EDITABLE LABELS !".
    """
    view = browser
    tabs = view.param_tabs
    button = tabs.buttons["Editable labels"]
    assert not button.text()

    # the tab already carries its group's shortcuts; the alert is added to
    # that rather than replacing it
    quiet = button.toolTip()
    assert "Show  F9" in quiet
    assert not button.property("alert")
    assert danger_pixels(button) == 0
    width = button.sizeHint().width()

    view.labels.blocked = "rec-editable-labels.csv could not be read (boom)"
    view.update_label_status()
    settle()
    try:
        assert button.property("alert") is True
        # the mark is really drawn, in both states -- an On-state pixmap
        # without it would make the dot vanish exactly when the tab is the
        # current one
        assert danger_pixels(button) >= 10
        assert danger_pixels(button, checked=True) >= 10
        # and it costs no width, so a state arriving never reflows the
        # strip and slides a tab out from under the pointer
        assert button.sizeHint().width() == width
        loud = button.toolTip()
        assert "could not be read" in loud
        assert quiet in loud  # the shortcuts are still there
        assert "READ-ONLY" in view.label_status_text()
    finally:
        view.labels.blocked = ""
        view.update_label_status()
        settle()
    assert not button.property("alert")
    assert danger_pixels(button) == 0
    assert button.toolTip() == quiet


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
            b.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonTextBesideIcon
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
