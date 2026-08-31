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

import pyqtgraph as pg
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "tests"))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QColor, QIcon  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QLabel,
    QSizePolicy,
    QSlider,
    QTabWidget,
    QToolButton,
)

from audian import theme  # noqa: E402
from audian.databrowser import (  # noqa: E402
    DataBrowser,
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


class _StubSignal:
    """Enough of a `Signal` for the stub browser to emit into."""

    def __init__(self):
        self.emitted = 0

    def emit(self):
        self.emitted += 1


class _StubPanelBrowser:
    """Just enough browser for the plugin panel path to talk to.

    A real one costs a window and a recording, and what is under test here
    is the isolation around somebody else's callable -- so this is what
    that path touches: a panel to put tabs in, the real browser's own
    methods for offering and opening them, and somewhere for the
    notifications to go.
    """

    def __init__(self):
        self.parambar = SidePanel()
        self.said = []
        self.plugin_offers = []
        self.plugin_panels = {}
        self.sigPluginPanelsChanged = _StubSignal()

    offer_plugin_panel = DataBrowser.offer_plugin_panel
    plugin_labels = DataBrowser.plugin_labels
    plugin_panel_open = DataBrowser.plugin_panel_open
    open_plugin_panel = DataBrowser.open_plugin_panel
    close_plugin_panel = DataBrowser.close_plugin_panel
    plugin_tab_closed = DataBrowser.plugin_tab_closed

    def notify(self, level, message):
        self.said.append((level, message))


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


def test_a_chip_that_grows_is_re_placed(app):
    """A row that places by hand has to be told when a chip changes size.

    The annotation source line is whatever the bundle called itself and is
    set long after the row was built.  Placed at the `sizeHint` it had when
    it was empty, it came out as the two characters it had room for -- and
    only a later resize, which is not guaranteed to happen, put it right.
    `QWidget.updateGeometry` posts a `LayoutRequest` to the parent, which is
    the hook this row listens on.
    """
    row = WrapRow()
    label = QLabel("")
    row.add_widget(label)
    row.resize(400, row.heightForWidth(400))
    # shown, because Qt defers a hidden widget's layout requests and the
    # defect being pinned is exactly one that only shows on a live row
    row.show()
    settle()
    narrow = label.width()

    label.setText("TEST  fit ch 00 -- a bundle with a long name")
    settle()
    assert label.width() > narrow
    assert label.width() == label.sizeHint().width()

    # and shrinking again is the same path
    label.setText("x")
    settle()
    assert label.width() == label.sizeHint().width()


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


# ------------------------------------------------------------- the toggle


def test_the_panel_can_be_put_away_and_brought_back(browser):
    """The action the bar never had.

    Browsing and configuring are different modes and the switch between
    them has to be cheap, so the whole panel goes with one key.  Put away,
    the lanes get the entire window; brought back, they get the width the
    reader left it at rather than the default -- closing it is how a reader
    parks it, and a lossy toggle is one nobody uses twice.
    """
    view = browser
    window = view.window()
    act = window.acts.toggle_side_panel
    assert act.isChecked()
    assert view.side_panel_shown()
    wide = view.stack_area.viewport().width()
    width = view.side_panel_width
    try:
        act.setChecked(False)
        settle()
        pump(0.3)
        assert not view.side_panel_shown()
        # the lanes really did get the width, not just the panel's absence
        assert view.stack_area.viewport().width() > wide

        act.setChecked(True)
        settle()
        pump(0.3)
        assert view.side_panel_shown()
        assert view.stack_area.viewport().width() == wide
        assert view.side_split.sizes()[1] == width
    finally:
        act.setChecked(True)
        settle()
        pump(0.3)


def test_the_menu_says_what_the_panel_is_doing(browser):
    """One action, one panel per open file, so the action is never the store.

    It is told, the way `sync_annotation_actions` says the annotation
    switches are.  Asserted in both directions because the failure is
    silent either way: a tick that disagrees with the panel makes the next
    Ctrl+B appear to do nothing.
    """
    view = browser
    act = view.window().acts.toggle_side_panel
    try:
        for wanted in (False, True, False, True):
            view.set_side_panel(wanted)
            view.sync_side_panel()
            settle()
            assert act.isChecked() == wanted == view.side_panel_shown()
    finally:
        view.set_side_panel(True)
        view.sync_side_panel()
        settle()


def test_showing_the_panel_does_not_steal_the_keyboard(browser):
    """The reader pressed a key while browsing and expects to keep browsing.

    Measured: showing a splitter child never moves focus, even when it
    holds the only focusable widget in the window -- so this direction is
    free, and stays free only while the tab buttons keep `NoFocus`.

    Hiding is the direction that needs code.  Qt does move focus out of a
    widget it hides, but to the focus-chain *next*, which here is the
    navigator's `FullTracePlot` and not the stack the reader was looking
    at: the arrow keys would nudge something off screen.
    """
    view = browser
    stack = view.stack_area
    # `QApplication.focusWidget()` is None in a window the platform does not
    # consider active, and offscreen no window is until it is asked -- so
    # without this the assertions below would all read None and pass for a
    # reason unrelated to what they claim.
    view.window().activateWindow()
    settle()
    try:
        stack.setFocus(Qt.FocusReason.OtherFocusReason)
        settle()
        assert QApplication.focusWidget() is stack

        view.set_side_panel(False)
        settle()
        view.set_side_panel(True)
        settle()
        # showing took nothing
        assert QApplication.focusWidget() is stack

        # and a control inside the panel hands the keyboard back on the way
        # out rather than letting Qt pick the next widget along
        inner = view.param_tabs.buttons["Audio"]
        inner.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        inner.setFocus(Qt.FocusReason.OtherFocusReason)
        settle()
        assert view.parambar.isAncestorOf(QApplication.focusWidget())
        view.set_side_panel(False)
        settle()
        assert QApplication.focusWidget() is stack
    finally:
        view.param_tabs.buttons["Audio"].setFocusPolicy(Qt.FocusPolicy.NoFocus)
        view.set_side_panel(True)
        settle()
        pump(0.2)


def test_the_panel_width_is_remembered(browser):
    """Written at the end of the gesture, and never during it.

    `save_setting` reads, updates and rewrites the whole settings file, and
    one drag of the handle is a hundred mouse moves -- measured,
    `splitterMoved` fires once per mouse move and not at all on release, so
    the write hangs off the handle's release the way `finish_panel_split`
    does and not off the signal.

    And the width is kept while the panel is shut: closing it is how a
    reader parks it, and reopening on the default rather than on the width
    they chose would make the toggle lossy.
    """
    from audian.databrowser import DataBrowser

    view = browser
    before = view.side_panel_width
    try:
        # what a drag ends with: the splitter at a new size, then release
        view.side_split.setSizes([view.width() - 300, 300])
        settle()
        view.finish_side_panel_drag()
        settle()
        assert view.side_panel_width == 300
        saved = view.side_panel_settings()
        assert saved.get("width") == 300
        assert saved.get("open") is True
        assert saved.get("version") == DataBrowser.SIDE_PANEL_SETTING_VERSION

        # shut it: the state is written, the width is not forgotten
        view.set_side_panel(False)
        view.save_side_panel()
        settle()
        saved = view.side_panel_settings()
        assert saved.get("open") is False
        assert saved.get("width") == 300

        # and it comes back at the width the reader left, not the default
        view.set_side_panel(True)
        settle()
        pump(0.3)
        assert view.side_split.sizes()[1] == 300
    finally:
        view.set_side_panel(True)
        view.side_split.setSizes([view.width() - before, before])
        settle()
        view.finish_side_panel_drag()
        settle()
        pump(0.2)


def test_a_hand_edited_panel_width_is_clamped(browser):
    """A settings file is a file a reader may edit by hand.

    The ladder is `restore_panel_split`'s: a wrong shape is dropped rather
    than trusted, only a wrong *version* is worth a warning, and the number
    is clamped before it reaches a splitter -- a 5 would open a panel too
    narrow to grab and a 99999 would push the channel stack off the screen.
    """
    import audian.audian as audian_app
    from audian.databrowser import DataBrowser

    view = browser
    keep_width, keep_saved = view.side_panel_width, view._side_panel_saved
    key = DataBrowser.SIDE_PANEL_SETTING
    version = DataBrowser.SIDE_PANEL_SETTING_VERSION

    def restore_from(value):
        audian_app.save_setting(key, value)
        return view.restore_side_panel()

    try:
        # too narrow to grab, and too wide to leave the lanes anything
        restore_from({"version": version, "width": 5, "open": True})
        assert view.side_panel_width == DataBrowser.SIDE_PANEL_WIDTH_MIN
        restore_from({"version": version, "width": 99999, "open": True})
        assert view.side_panel_width == DataBrowser.SIDE_PANEL_WIDTH_MAX

        # a shape that is not a dict, and a value that is not a number:
        # dropped for the default rather than raising
        for junk in ("not a dict", 17, [], None):
            assert restore_from(junk) is True
            assert view.side_panel_width == DataBrowser.SIDE_PANEL_WIDTH
        assert restore_from({"version": version, "width": "wide"}) is True
        assert view.side_panel_width == DataBrowser.SIDE_PANEL_WIDTH

        # a version this build does not write is dropped whole, warning and
        # all -- never half-read
        assert restore_from({"version": 99, "width": 300, "open": False}) is True
        assert view.side_panel_width == DataBrowser.SIDE_PANEL_WIDTH
        assert view.side_panel_settings() == {}

        # and a value this build did write comes back untouched
        assert restore_from({"version": version, "width": 300, "open": False}) is False
        assert view.side_panel_width == 300
    finally:
        audian_app.save_setting(
            key, {"version": version, "width": keep_width, "open": True}
        )
        view.side_panel_width = keep_width
        view._side_panel_saved = keep_saved


def test_restoring_the_panel_writes_nothing(browser):
    """The single-writer rule, which this codebase states three times.

    A browser that wrote its own state at construction would overwrite the
    choice made in the window beside it, so `restore_side_panel` primes the
    memo and `save_side_panel` compares against it.  Two browsers open on
    one settings file is the case, and it is why `save_parameter_tab` and
    `save_spectrogram_band` both carry the same memo.
    """
    import audian.audian as audian_app
    from audian.databrowser import DataBrowser

    view = browser
    keep_width, keep_saved = view.side_panel_width, view._side_panel_saved
    key = DataBrowser.SIDE_PANEL_SETTING
    version = DataBrowser.SIDE_PANEL_SETTING_VERSION
    try:
        audian_app.save_setting(key, {"version": version, "width": 300, "open": True})
        view.restore_side_panel()
        # the window beside this one now changes its mind
        audian_app.save_setting(key, {"version": version, "width": 500, "open": True})
        # ... and a restore-shaped save from this one must not undo it
        view.save_side_panel()
        assert view.side_panel_settings().get("width") == 500
    finally:
        audian_app.save_setting(
            key, {"version": version, "width": keep_width, "open": True}
        )
        view.side_panel_width = keep_width
        view._side_panel_saved = keep_saved


# ------------------------------------------------------- the plugin region


def test_the_plugin_region_is_absent_without_plugins(browser):
    """Nobody has written one, so it costs nothing and shows nothing.

    An empty box with a tab bar and no tabs in it is a control saying the
    application is missing something, when what it means is that this
    reader has no plugins.  So the region is not built until a factory
    registers, and the vertical splitter that would hold it has one child
    and shows no handle.
    """
    view = browser
    assert not view.has_plugin_panels()
    assert view.parambar.plugins is None
    # one child, no handle, no height taken
    assert view.parambar.split.count() == 1
    assert view.parambar.split.widget(0) is view.param_tabs


def test_a_plugin_panel_lands_in_its_own_region(app, tmp_path):
    """The smallest thing that could work: one more naming convention.

    `plugins.py` already discovers `audian_*traces` and `audian_*analyzer`
    by name; a panel is `audian_*panel`, returning `(title, widget)`.  A
    plugin author who has written a trace factory already knows how to
    write this.
    """
    from PySide6.QtWidgets import QLabel

    from audian.plugins import Plugins

    made = []

    def audian_probe_panel(browser):
        made.append(browser)
        return "Probe", QLabel("a plugin's own controls")

    plugins = Plugins()
    plugins.add_panel_factory(audian_probe_panel)
    assert plugins.panel_factories == [audian_probe_panel]

    view = _StubPanelBrowser()
    plugins.setup_panels(view)
    # registering is an offer, not a tab: the factory has not been called
    # and the region does not exist until the reader asks for it
    assert made == []
    assert view.plugin_labels() == ["Probe"]
    assert view.parambar.plugins is None

    assert view.open_plugin_panel("Probe")
    assert made == [view]
    region = view.parambar.plugins
    assert region is not None
    assert region.count() == 1
    assert region.tabText(0) == "Probe"
    # and closing it takes the region away again, which is how a reader
    # turns a plugin off
    view.close_plugin_panel("Probe")
    assert view.parambar.plugins is None
    assert not view.plugin_panel_open("Probe")
    # text tabs, because there is no icon to invent for a plugin nobody has
    # written yet, and asking every author for one is a tax on writing one
    assert region.tabPosition() == QTabWidget.TabPosition.North


def test_a_broken_plugin_panel_does_not_take_the_window_down(app):
    """A plugin is somebody else's code on the reader's own path.

    A broken one costs its own tab and nothing else -- not the panel, not
    the plugins after it, and not the file the reader was opening when it
    raised.  This is the one place the panel wraps a call it does not own.
    """
    from PySide6.QtWidgets import QLabel

    from audian.plugins import Plugins

    def audian_broken_panel(browser):
        raise RuntimeError("boom")

    def audian_confused_panel(browser):
        return "not a pair"

    def audian_quiet_panel(browser):
        return None

    def audian_good_panel(browser):
        return "Good", QLabel("fine")

    plugins = Plugins()
    for factory in (
        audian_broken_panel,
        audian_confused_panel,
        audian_quiet_panel,
        audian_good_panel,
    ):
        plugins.add_panel_factory(factory)

    view = _StubPanelBrowser()
    plugins.setup_panels(view)
    for label in view.plugin_labels():
        view.open_plugin_panel(label)

    # the good one is there, and it is the only one
    region = view.parambar.plugins
    assert region is not None
    assert [region.tabText(i) for i in range(region.count())] == ["Good"]
    # and the reader was told about the two that failed, by name
    levels = [level for level, _msg in view.said]
    assert levels == ["error", "error"]
    assert any("audian_broken_panel" in msg for _lvl, msg in view.said)
    assert any("audian_confused_panel" in msg for _lvl, msg in view.said)
    # the one that declined said nothing, because declining is not failing
    assert not any("audian_quiet_panel" in msg for _lvl, msg in view.said)


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


# --- the filter cutoff lines -----------------------------------------------


def cutoff_handles(browser):
    """Every filter handle of the stack, both ends of every lane."""
    return [
        handle
        for ax in browser.spectrogram_plots()
        for handle in (ax.highpass_handle, ax.lowpass_handle)
    ]


def restore_cutoffs(browser):
    """Put the switch and the region mode back, whatever the test did."""
    browser.set_region_mode(DataBrowser.MODE_ZOOM)
    browser.set_cutoff_lines(True, dispatch=False, save=False)
    settle()


def test_the_spectrogram_page_carries_the_cutoff_switch(browser):
    """And it starts on: the lines have always been drawn."""
    assert browser.cutoffsw is not None
    assert browser.cutoffsw.isCheckable()
    assert browser.cutoffsw.isChecked()
    assert browser.show_cutoff_lines
    page = next(g for g in browser.param_groups if g.title == "Spectrogram")
    assert browser.cutoffsw in page.findChildren(QToolButton)
    handles = cutoff_handles(browser)
    assert len(handles) == 2 * len(list(browser.spectrogram_plots()))
    assert all(h.isVisible() for h in handles)


def test_unchecking_the_switch_takes_the_lines_off_every_lane(browser):
    """A four channel stack has eight of them, and all eight go."""
    try:
        browser.cutoffsw.setChecked(False)
        settle()
        assert not browser.show_cutoff_lines
        assert not any(h.isVisible() for h in cutoff_handles(browser))
        browser.cutoffsw.setChecked(True)
        settle()
        assert browser.show_cutoff_lines
        assert all(h.isVisible() for h in cutoff_handles(browser))
    finally:
        restore_cutoffs(browser)


def test_a_hidden_cutoff_line_does_not_keep_the_mouse(browser):
    """An invisible line that still swallows a rubber-band drag is worse
    than a visible one: nothing on screen explains what took the drag.

    `SpectrogramPlot.set_handles_movable` measures what a movable handle
    costs a drag started on it -- zero region signals against one.
    """
    try:
        assert all(h.movable for h in cutoff_handles(browser))
        browser.set_cutoff_lines(False, dispatch=False, save=False)
        settle()
        assert not any(h.movable for h in cutoff_handles(browser))
        browser.set_cutoff_lines(True, dispatch=False, save=False)
        settle()
        assert all(h.movable for h in cutoff_handles(browser))
    finally:
        restore_cutoffs(browser)


def test_the_switch_and_the_region_mode_do_not_overwrite_each_other(browser):
    """Two owners write movability and neither may clear the other's word.

    Label mode takes the mouse from the cutoffs for as long as it lasts
    (`DataBrowser.set_region_mode`); the checkbox takes it away for as long
    as the lines are hidden.  Leaving label mode must not hand the mouse
    back to a line nobody can see, and showing a line while labelling must
    not take the drag back from the labels.
    """
    try:
        browser.set_cutoff_lines(False, dispatch=False, save=False)
        browser.set_region_mode(DataBrowser.MODE_LABEL)
        settle()
        assert not any(h.movable for h in cutoff_handles(browser))
        # showing them again while labelling: seen, but still not grabbing
        browser.set_cutoff_lines(True, dispatch=False, save=False)
        settle()
        assert all(h.isVisible() for h in cutoff_handles(browser))
        assert not any(h.movable for h in cutoff_handles(browser))
        # hidden again, then out of label mode: still not grabbing
        browser.set_cutoff_lines(False, dispatch=False, save=False)
        browser.set_region_mode(DataBrowser.MODE_ZOOM)
        settle()
        assert not any(h.movable for h in cutoff_handles(browser))
        assert not any(h.isVisible() for h in cutoff_handles(browser))
    finally:
        restore_cutoffs(browser)


# --- the colour scale ------------------------------------------------------


@pytest.fixture
def levels(browser):
    """Give the colour scale back exactly as the test found it.

    The stack fixture is module scoped and the mapping is shared by every
    lane, so a test that left the ramp somewhere else would be handing the
    next one a spectrogram drawn against a range nobody asked for.
    """
    before = browser.level_range()
    yield browser
    if before is not None:
        browser.set_level_range(*before, dispatch=False)
        settle()


def level_widgets(browser):
    return (
        browser.zminsliderw,
        browser.zmaxsliderw,
        browser.zmidsliderw,
        browser.zminw,
        browser.zmaxw,
        browser.zmidw,
    )


def widgets_agree(browser):
    """Do all six rows say what the images are actually drawn against?"""
    zmin, zmax = browser.level_range()
    return (
        browser.zminsliderw.value() == int(round(zmin))
        and browser.zmaxsliderw.value() == int(round(zmax))
        and browser.zmidsliderw.value() == int(round(0.5 * (zmin + zmax)))
        and browser.zminw.value() == pytest.approx(zmin)
        and browser.zmaxw.value() == pytest.approx(zmax)
        and browser.zmidw.value() == pytest.approx(0.5 * (zmin + zmax))
    )


def test_the_colour_scale_has_a_row_for_each_of_the_three_key_pairs(browser):
    """Max, Min and Power, and every slider spans the axis's own limits."""
    assert all(w is not None for w in level_widgets(browser))
    rmin, rmax, rstep = browser.level_range_bounds()
    for slider in (browser.zminsliderw, browser.zmaxsliderw, browser.zmidsliderw):
        assert slider.minimum() == int(round(rmin))
        assert slider.maximum() == int(round(rmax))
        assert slider.singleStep() == int(round(rstep))
    for box in (browser.zminw, browser.zmaxw, browser.zmidw):
        assert box.opts["bounds"] == [rmin, rmax]
    assert widgets_agree(browser)


@pytest.mark.parametrize(
    "action",
    [
        "power_up",
        "power_down",
        "max_power_up",
        "max_power_down",
        "min_power_up",
        "min_power_down",
    ],
)
def test_the_six_keys_still_work_and_the_rows_follow_them(levels, action):
    """The rows follow the mapping; they do not own it.

    The number has three writers -- the keys, the rows, and `fit_levels` --
    and this is the one that existed first.  A row that held its own copy
    would be right until the first key press.
    """
    browser = levels
    window = browser.window()
    before = browser.level_range()
    getattr(window.acts, action).trigger()
    settle()
    pump(0.3)
    after = browser.level_range()
    assert after != before, f"{action} moved nothing"
    assert widgets_agree(browser), f"the rows did not follow {action}"


def test_a_slider_sets_the_level_it_is_dragged_to(levels):
    browser = levels
    browser.set_level_range(-110.0, -45.0, dispatch=False)
    settle()
    browser.zmaxsliderw.setValue(-40)
    settle()
    pump(0.3)
    assert browser.level_range() == (-110.0, -40.0)
    browser.zminsliderw.setValue(-120)
    settle()
    pump(0.3)
    assert browser.level_range() == (-120.0, -40.0)
    assert widgets_agree(browser)


def test_a_number_box_sets_the_level_that_is_typed_into_it(levels):
    """The whole point of the row: a level nobody could name before."""
    browser = levels
    browser.set_level_range(-110.0, -45.0, dispatch=False)
    settle()
    browser.zmaxw.setValue(-37.5)
    settle()
    pump(0.3)
    assert browser.level_range() == (-110.0, -37.5)
    assert browser.zmaxsliderw.value() == -38  # the slider is whole dB
    assert widgets_agree(browser)


def test_the_power_row_moves_both_ends_and_keeps_the_span(levels):
    """What `D` and `⇧D` have always done, and what Max and Min cannot do
    in one gesture: the two of them together change the span in between."""
    browser = levels
    browser.set_level_range(-120.0, -40.0, dispatch=False)
    settle()
    browser.zmidsliderw.setValue(-100)
    settle()
    pump(0.3)
    zmin, zmax = browser.level_range()
    assert zmax - zmin == pytest.approx(80.0)
    assert 0.5 * (zmin + zmax) == pytest.approx(-100.0)
    assert widgets_agree(browser)


def test_the_two_ends_cannot_cross_and_a_refused_drag_is_pushed_back(levels):
    """`PlotRange.min_step` only refuses to push the floor PAST the ceiling.

    A slider can ask for more than a key can, so the clamp lives in
    `set_level_range` -- and the interesting half is what the widget does
    afterwards.  A refused write changes the mapping by nothing, so a memo
    keyed on the mapping alone would leave the slider parked at a number
    the picture is not drawn against.
    """
    browser = levels
    rmin, rmax, rstep = browser.level_range_bounds()
    browser.set_level_range(-110.0, -45.0, dispatch=False)
    settle()
    browser.zminsliderw.setValue(int(rmax))
    settle()
    pump(0.3)
    zmin, zmax = browser.level_range()
    assert zmin < zmax, "the floor passed the ceiling"
    assert zmax - zmin >= rstep
    assert widgets_agree(browser), "the clamped slider was left where it was put"

    browser.zmaxsliderw.setValue(int(rmin))
    settle()
    pump(0.3)
    zmin, zmax = browser.level_range()
    assert zmin < zmax, "the ceiling passed the floor"
    assert widgets_agree(browser)


def test_a_refit_moves_the_rows_with_nothing_having_been_typed(levels):
    """`fit_levels` is the third writer, and the one a reader never sees.

    It runs whenever a panel is shown or the smoothing changes, so a row
    that only followed its own widget would be wrong exactly when the ramp
    had just moved under the reader.
    """
    browser = levels
    browser.set_level_range(-90.0, -40.0, dispatch=False)
    settle()
    pump(0.3)
    assert widgets_agree(browser)
    lane = next(
        ax for ax in browser.spectrogram_plots() if ax.fits_levels() and ax.isVisible()
    )
    lane._levels_fitted = False
    assert lane.fit_levels(), "the fit had nothing to say"
    settle()
    pump(0.3)
    assert browser.level_range() != (-90.0, -40.0)
    assert widgets_agree(browser)


# --- peaking ---------------------------------------------------------------


def colorbar_maps(browser):
    for panel in browser.panels.values():
        if panel.is_spectrogram() and not panel.is_power():
            return [cbar.colorMap() for cbar in panel.axcs]
    return []


def clip_mark():
    value = theme.token("spec.clip").lstrip("#")
    return [int(value[i : i + 2], 16) for i in (0, 2, 4)]


def is_marked(cmap):
    return list(cmap.getLookupTable(nPts=256)[-1][:3]) == clip_mark()


@pytest.fixture
def peaking_off(browser):
    """Leave the stack unmarked, whatever the test did to it."""
    yield browser
    browser.set_peaking(False, dispatch=False, save=False)
    settle()


def test_the_key_and_the_checkbox_are_one_object(browser):
    """Two controls for one switch is two things that can disagree.

    The button takes the action as its default action, so the tick it draws
    IS the action's -- there is nothing to keep in step.
    """
    act = browser.window().acts.toggle_peaking
    assert act.isCheckable()
    assert act.shortcut().toString() == "X"
    assert browser.peakingw is not None
    assert browser.peakingw.defaultAction() is act
    assert browser.peakingw.isChecked() == act.isChecked()


def test_peaking_marks_the_last_entry_of_every_colour_bar(peaking_off):
    """Every lane, not just the one that owns the level fit: the reader is
    looking at a stack, and a marked lane beside an unmarked one is worse
    than no mark at all."""
    browser = peaking_off
    maps = colorbar_maps(browser)
    assert maps and not any(is_marked(m) for m in maps)
    browser.peakingw.click()
    settle()
    pump(0.4)
    assert browser.spec_peaking
    assert all(is_marked(m) for m in colorbar_maps(browser))
    browser.peakingw.click()
    settle()
    pump(0.4)
    assert not browser.spec_peaking
    assert not any(is_marked(m) for m in colorbar_maps(browser))


def test_nothing_but_the_last_entry_moves(peaking_off):
    """The claim the whole implementation rests on, asked of the map that
    actually reached the colour bar rather than of a freshly built one."""
    browser = peaking_off
    plain = colorbar_maps(browser)[0].getLookupTable(nPts=256).copy()
    browser.set_peaking(True, dispatch=False, save=False)
    settle()
    pump(0.4)
    marked = colorbar_maps(browser)[0].getLookupTable(nPts=256)
    assert (marked[:-1] == plain[:-1]).all()
    assert list(marked[-1][:3]) == clip_mark()


def test_setting_peaking_with_no_argument_pushes_what_is_already_held(peaking_off):
    """The call `DataBrowser.open` makes once the panels exist.

    `SpectrogramPlot.__init__` builds its colour bar from the plain map --
    it runs before the browser has anything to push -- so a reader who has
    peaking stored would open every new recording unmarked while the box
    said otherwise.  Pinned through the state rather than through a second
    window: `todo.md` records that another browser in this process is what
    `theme.collect_orphan_widgets` segfaults on.
    """
    browser = peaking_off
    browser.spec_peaking = True  # what the constructor leaves behind
    assert not any(is_marked(m) for m in colorbar_maps(browser))
    browser.set_peaking(dispatch=False, save=False)
    settle()
    pump(0.4)
    assert all(is_marked(m) for m in colorbar_maps(browser))
    assert browser.window().acts.toggle_peaking.isChecked()


def test_cycling_the_colour_map_does_not_drop_the_mark(peaking_off):
    """`Shift+C` is a `set_color_map`, and a version that marked the map
    once rather than on every push loses it here."""
    browser = peaking_off
    window = browser.window()
    browser.set_peaking(True, dispatch=False, save=False)
    settle()
    before = browser.color_map
    window.acts.color_map_cycler.trigger()
    settle()
    pump(0.4)
    assert browser.color_map != before, "the map did not cycle"
    assert all(is_marked(m) for m in colorbar_maps(browser))


def test_a_theme_switch_does_not_drop_the_mark(peaking_off):
    """The other path that re-pushes the map, and the one a reader takes
    without meaning to -- the desktop changing at sunset does it."""
    browser = peaking_off
    window = browser.window()
    before = theme.current_theme()
    try:
        browser.set_peaking(True, dispatch=False, save=False)
        settle()
        other = theme.THEME_LIGHT if before == theme.THEME_DARK else theme.THEME_DARK
        window.set_app_theme(other)
        settle()
        pump(0.6)
        assert theme.current_theme() == other
        # the mark is the new page's colour, not the one it was applied in
        assert all(is_marked(m) for m in colorbar_maps(browser))
    finally:
        window.set_app_theme(before)
        settle()
        pump(0.6)


# --- the overlap row -------------------------------------------------------


@pytest.fixture
def overlap(browser):
    """Give the resolution back exactly as the test found it.

    The stack fixture is module scoped and a spectrogram is recomputed from
    the overlap, so a test that left it somewhere else would hand the next
    one a picture nobody asked for.
    """
    spectrogram = browser.data["spectrogram"]
    before = spectrogram.overlap_frac
    yield browser
    browser.set_resolution(overlap_frac=before, dispatch=False)
    settle()
    pump(0.3)


def overlap_widgets_agree(browser):
    """Do the two halves of the row say what the transform is computed at?

    The slider is whole percent AND stops at 99, so at the top of the range
    it sits one position below what the box reads: a hop of one frame at
    nfft 256 is 99.609375 %, which rounds to 100, and a 0..99 slider has no
    such position.  `QSlider.setValue` clamps that itself.  It is the
    clearest statement of why the row needed a box.
    """
    percent = 100 * browser.data["spectrogram"].overlap_frac
    slider, box = browser.ofracsliderw, browser.ofracw
    want = min(slider.maximum(), int(round(percent)))
    return slider.value() == want and box.value() == pytest.approx(percent)


def test_the_overlap_row_is_a_slider_and_a_box(browser):
    """The last number on this page that could only be read, not written."""
    assert isinstance(browser.ofracsliderw, QSlider)
    assert isinstance(browser.ofracw, pg.SpinBox)
    assert browser.ofracsliderw.minimum() == 0
    assert browser.ofracsliderw.maximum() == 99
    # 0..100 and not the 99.999 % `prepare_update` clamps to: the highest
    # overlap the transform can actually reach is `1 - hop/nfft`, which is
    # above the clamp for a long window, and a box that could not show it
    # would report a picture drawn at something else.
    assert browser.ofracw.opts["bounds"] == [0.0, 100.0]
    assert overlap_widgets_agree(browser)


def test_a_typed_overlap_is_not_rounded_by_the_slider_beside_it(overlap):
    """The whole point of the box, and the trap in adding it.

    The slider is whole percent where `overlap_frac` is a float, so a 62.5 %
    that went out through the slider would come back 62.  The box is the
    precise writer; the slider is written to and never read back from.
    """
    browser = overlap
    spectrogram = browser.data["spectrogram"]
    browser.ofracw.setValue(62.5)
    settle()
    pump(0.5)
    assert spectrogram.overlap_frac == pytest.approx(0.625)
    assert browser.ofracw.value() == pytest.approx(62.5)
    assert browser.ofracsliderw.value() == 62  # the slider is whole percent
    assert overlap_widgets_agree(browser)


def test_the_slider_still_sets_the_overlap_it_is_dragged_to(overlap):
    """The coarse grab keeps working, and the box follows what landed.

    Not the value it was dragged to: `set_hop` rounds the hop to whole
    frames, so 80 % of a 256 sample window is 80.078125 % and the box says
    so rather than repeating what was asked for.
    """
    browser = overlap
    spectrogram = browser.data["spectrogram"]
    browser.ofracsliderw.setValue(80)
    settle()
    pump(0.5)
    hop = int(round(0.2 * spectrogram.nfft))
    assert spectrogram.hop == hop
    assert spectrogram.overlap_frac == pytest.approx(1 - hop / spectrogram.nfft)
    assert overlap_widgets_agree(browser)


def test_a_box_typed_into_is_debounced_like_a_key_held_down(overlap):
    """`update_resolution` stashes and starts the 200 ms timer.

    Respectrogramming sixteen channels costs about 1.5 s, so the box has to
    come in through the same coalescing the keys and the slider do -- one
    recompute for a burst, not one per value.
    """
    browser = overlap
    spectrogram = browser.data["spectrogram"]
    browser.set_resolution(overlap_frac=0.5, dispatch=False)
    settle()
    browser.ofracw.setValue(70.0)
    browser.ofracw.setValue(75.0)
    assert browser.resolution_timer.isActive()
    assert browser.pending_overlap == pytest.approx(0.75)
    assert spectrogram.overlap_frac == pytest.approx(0.5), "recomputed per keystroke"
    settle()
    pump(0.5)
    assert spectrogram.overlap_frac == pytest.approx(0.75)
    assert overlap_widgets_agree(browser)


@pytest.mark.parametrize("action", ["overlap_up", "overlap_down"])
def test_the_two_keys_still_work_and_both_halves_follow_them(overlap, action):
    """`O` and `Shift+O` halve and double the hop, which is a gesture rather
    than a number -- the row follows the transform, it does not hold it."""
    browser = overlap
    browser.set_resolution(overlap_frac=0.5, dispatch=False)
    settle()
    pump(0.3)
    before = browser.data["spectrogram"].overlap_frac
    getattr(browser.window().acts, action).trigger()
    settle()
    pump(0.5)
    assert browser.data["spectrogram"].overlap_frac != before, f"{action} moved nothing"
    assert overlap_widgets_agree(browser)


def test_a_hundred_percent_is_refused_by_the_transform_and_the_box_says_so(overlap):
    """A hop of zero would be a column per sample, so `set_hop` floors it at
    one frame -- and the box reports the 1 - 1/nfft that really landed
    rather than the 100 % it was handed."""
    browser = overlap
    spectrogram = browser.data["spectrogram"]
    browser.ofracw.setValue(100.0)
    settle()
    pump(0.5)
    assert spectrogram.hop == 1
    assert spectrogram.overlap_frac == pytest.approx(1 - 1 / spectrogram.nfft)
    assert browser.ofracw.value() == pytest.approx(100 - 100 / spectrogram.nfft)
    # and the slider, which has no position for it, stops at its own top
    assert browser.ofracsliderw.value() == 99
    assert overlap_widgets_agree(browser)
