# The side panel: moving the parameter bar to the right edge

**Built.**  All six steps landed in `ea75927..344e09a`, one commit each
plus a polish pass.  This document is the design as it was written, at
`cfe1c4e`; what actually got built differs from it in seven places, and
those are listed at the end under *What the build changed*.  The commit
messages carry the reasoning and the measurements.

Read `docs/qt6/recon/parameter-bar.md` first if you are picking this up
cold: it is the measured map of what the bar was and what was pinned to
it.  This document says what to do with it and why.


## What it solves

Vertical space is the scarce axis.  Horizontal is not: on a 16:9 panel
there is width to spare and never enough height, and a 16 channel stack
is where that bites hardest.

The parameter bar spends the scarce axis.  Measured on a 1200x900 window
with 4 channels, it is **168 px tall**, made of 8 (grid top) + 22 (tab
strip) + 2 + 130 (group stack) + 6 (grid bottom).  At
`CHANNEL_DENSE_HEIGHT` = 34 that is **five dense channel lanes**, gone,
whether or not anyone is looking at the bar.  It cannot even be hidden:
there is no action to show or hide it anywhere in audian today.

Moving it to the right edge spends the axis there is more of, and makes
it hideable, so browsing can have the whole window.


## The shape

```
┌────────────────────────────────────────────┬──────────────────┐
│                                            │ ◧ ◨ ◈ ◆ ⧉        │  ← icon strip
│                                            ├──────────────────┤
│                                            │ NFFT             │
│              channel stack                 │ [1024        ▾]  │
│                                            │ OVERLAP          │
│                                            │ ▬▬▬▬▬▬▬─────     │
│                                            │ MAP              │
│                                            │ [▨▨▨▨▨▨      ▾]  │
├────────────────────────────────────────────┤                  │
│              navigator                     ├══════════════════┤  ← drag
│                                            │ Wavetracker │ …  │  ← text tabs
└────────────────────────────────────────────┴──────────────────┘
        ↑ drag to resize the panel, or Ctrl+B to hide it entirely
```

Two regions, split vertically, because they are two different kinds of
thing:

**Top — the built-ins.**  Filter, Spectrogram, Envelope, Audio, Fixed
labels, Editable labels.  A closed set, known at build time, one shown at
a time.  Picked by an **icon strip**: six icons cost about 180 px on one
line where the current text tabs want 489, and the set is small and fixed
enough that an icon per entry is a thing a person can learn.

**Bottom — the plugins.**  An open set, unknown at build time.  Picked by
**text tabs**, because there is no icon to invent for a plugin nobody has
written yet, and asking every plugin author for one is a tax on writing a
plugin.  The region is **absent entirely while no plugin registers a
panel**, so today it costs nothing and nobody sees an empty box.

The two are a `QSplitter(Vertical)` so the boundary is draggable, and the
plugin region takes the space when it exists.


## Behaviour

### Hiding and showing

Both a mouse way and a keyboard way, because browsing and configuring are
different modes and the switch between them has to be cheap.

* **`Ctrl+B`** toggles the whole panel.  Verified free: it appears in no
  `setShortcut` call in `src/audian` and in no entry of
  `tests/data/action-inventory.json`.
* **View → Side panel**, checkable, the same action.
* **Mouse**: drag the splitter handle to the right edge, or click the
  chevron on the handle.
* Hidden means **width zero and the widget hidden** — not a rail left
  behind.  The whole point is that browsing gets the entire window.

Precedent to sit beside: the channel rail already has exactly this shape,
`F7`, built at `databrowser.py:2227-2231` with `self.addAction` on the
browser rather than through `self.acts`.  Note the consequence — that
shortcut is invisible to the golden action inventory.  The side panel
toggle should go through `self.acts` instead, so it *is* inventoried, and
`tests/data/action-inventory.json` regenerated in the same commit.

**Focus.**  Showing the panel must not take focus: the reader pressed a
key while browsing and expects the next key to keep browsing.  The tab
buttons keep `Qt.FocusPolicy.NoFocus`, which they already have and which
`tests/test_parameterbar.py:288-295` pins — Space is play-window and the
arrows nudge the view, and a focused checkable button eats both.  Hiding
the panel while focus is inside it must return focus to the stack, or the
next keystroke goes nowhere.

### Resizing

A `QSplitter(Horizontal)` between the canvas and the panel.  Width is
remembered.  Written **at the end of the drag only**, never per mouse
move — `finish_panel_split` (`databrowser.py:6511`) is the pattern, and
its docstring says why: `save_setting` rewrites the whole settings file
and one drag is a hundred mouse moves.

Multi-tab rule, which this codebase states three times and must not break
here: **only the browser whose panel the reader actually dragged writes.**
`save_parameter_tab` and `save_spectrogram_band` both carry a `save=` flag
or a memo for exactly this, so a browser being built does not overwrite
the choice made in the window beside it.

### The two regions

The built-in region behaves as the bar does today: one group visible, the
icon strip picks it, and the two auto-raise gestures survive unchanged —
label mode raises Editable labels
(`tests/test_parameterbar.py:331-354`), loading a bundle raises Fixed
labels (`:357-371`).  `set_alert` still appends its mark to a tab, but on
an icon it needs a dot or a badge rather than a trailing `!`.

The plugin region is a `QTabWidget` with `North` text tabs, hidden while
it has no tabs.


## The narrow column

The panel is about 320 px by default.  The owner's ruling, and it is the
right one: nothing here needs the width it currently takes — the sliders
are fine much shorter, and the wide rows wrap.

Measured minimum widths today, for reference: Filter 283, Spectrogram
515, Audio 302, Fixed labels 407 empty and 777 with a bundle loaded,
Editable labels 265, tab strip 489.

What each one does in the panel:

| what | today | in the panel |
|---|---|---|
| tab strip | 489 px of text buttons | icon strip, ~180 px, wraps |
| caption + field row | caption in col 0, field in col 1 | **caption stacked above the field**, both full width |
| `LogSlider` | min width 112 | unchanged; 112 fits |
| overlap slider | expanding | unchanged, just shorter |
| `ColorMapCombo` | 267, from a 64x12 gradient icon | narrow the icon to ~40x10 |
| Fixed-labels chip rows | 696 and 555 px of `QToolButton` in a plain `QHBoxLayout` | **wrap** |
| Editable-labels category strip | already wraps over 2 lines and folds to `+N` | unchanged |

Two mechanisms carry all of it:

**Stacked rows.**  `ParameterGroup.add_row` puts the caption in column 0
and fields in columns 1..N (`databrowser.py:332-348`).  It gains a
narrow mode that puts the caption on its own grid row spanning the width,
fields on the next.  A **construction-time flag**, not a width
measurement — reflowing on resize means layout thrash and re-running
`equalize` mid-drag.  The panel builds its groups narrow; the
`test_annotationpanel` stub keeps building them wide, which is one reason
the flag must be per-group rather than global.

**A wrapping row.**  `CategoryStrip` (`labeloverlay.py:1244`) already
hand-packs chips over two lines and folds the overflow into a `+N` menu,
and it is the only widget in the bar that reflows at all.  Lift that
packing into a reusable widget and give it to the annotation chip rows.
Do this **first, as its own commit, while everything is still in the
bottom bar** — it is testable there, and it is the piece most likely to
need iterating.

Vertical space inside the panel is nearly free, so stacked rows costing
height is not a problem.  A `QScrollArea` around each region's content
handles the case where it is not.


## The window width floor

`tests/test_parameterbar.py:609-622` asserts
`window.minimumSizeHint().width() < 1097` — a 14" 1920x1080 panel at
175% scale.  This is the number the whole file exists to defend, and a
right-hand panel is the one change that threatens it: stacked under the
stack the bar's width *overlaps* the window's, docked at the right it
*adds* to it.

Three things keep it:

1. Each region's content sits in a `QScrollArea` with
   `setWidgetResizable(True)`, so the panel's minimum width is a number
   we choose rather than the widest thing inside it.
2. `SidePanel.minimumWidth()` is small — 220 px — and the splitter can
   take it there.
3. **Hidden, the panel contributes nothing.**  `QSplitter` gives a
   hidden widget no minimum, so the floor when the panel is off is
   exactly today's floor.

Assert all three.  The arithmetic to keep true: canvas minimum + 220 <
1097, i.e. the canvas must stay under 877.  Today the whole window's
floor is 695 (`tests/test_panelsplitter.py:2446`), and the bar is a
*part* of that, so there is room — but it is not automatic and it is the
first thing to measure once the panel exists.


## Persistence

A new key, alongside the existing ones and not replacing them.

```json
"side-panel": {"version": 1, "width": 320, "open": true}
```

`parameter-tab` (version 2) is **left exactly as it is** and keeps
meaning "which built-in group is raised".  It works, it is versioned, and
`tests/test_parameterbar.py:374-387` pins its shape; there is nothing to
gain by folding it into a new key and a migration to get wrong.

Deliberately not stored in version 1: which plugin tab was open, and the
plugin/built-in split position.  Say so in the constant's docstring, so
the next person knows it was a decision and not an oversight.

Read defensively like every other key: `isinstance` gate, version gate,
`float`/`bool` coercion in a `try`, and clamp the width to something
sane (say 160..800) before it reaches a splitter.  A settings file is a
file a reader may edit by hand.


## Plugin registration

`plugins.py` already discovers plugins by naming convention
(`plugins.py:36-52`): any `audian*.py` in the working directory, and
within it any callable named `audian_*traces` or `audian_*analyzer`.  The
smallest thing that could work is one more suffix:

```python
def audian_wavetracker_panel(browser):
    """Return (title, widget), or None to add no panel."""
    return "Wavetracker", WavetrackerPanel(browser)
```

registered by `Plugins.add_panel_factory`, called by
`Plugins.setup_panels(browser)`, exactly mirroring `add_trace_factory` /
`setup_traces`.  No new discovery mechanism, no manifest, no entry
points — a plugin author who has written a trace factory already knows
how to write this.

**Isolation.**  Each factory call is wrapped: an exception is caught,
logged, surfaced through `notify("error", ...)`, and that plugin gets no
tab.  A broken plugin must cost its own panel and nothing else.  This is
the one place the panel needs a `try` around a call it does not own.


## What changes, and where

* `databrowser.py:2063` — `self.vbox.addWidget(self.parambar)` is where
  the bar becomes "the bottom bar".  This is the line the move deletes.
* `databrowser.py:2326-2712` `setup_parameter_bar` — keeps building
  groups; stops building a bottom band.
* `databrowser.py:2347` `theme.band(self.parambar, top=True)` — the rule
  moves to the panel's **leading (left)** edge.  No test asserts this; it
  will regress silently and only be visible in the running app.
* `databrowser.py:236` `ParameterGroup` — narrow row mode.
* `databrowser.py:411` `ParameterTabs` — grows an icon mode, or gains a
  sibling; keep `buttons` a title-keyed dict of `QToolButton` and keep
  `sigTabChanged`, `current_title`, `show_group`, `show_index` and
  `set_alert`, because the tests and the auto-raise gestures all reach
  for them by name.
* `plugins.py` — `add_panel_factory`, `setup_panels`, the `_panel`
  suffix.
* Keep the attribute name **`parambar`** pointing at something real.
  `tests/test_annotationpanel.py` hand-builds `parambar = QWidget(self)`
  with `param_tabs = None` and calls `setup_annotation_group()` outside
  `setup_parameter_bar`; about 1169 lines depend on that still working.


## Tests

**These break honestly and must be rewritten with the move, not after:**

* `test_parameterbar.py:210-213` — asserts `parambar` and `param_tabs`
  are `QSizePolicy.Policy.Fixed` *vertically*.  A right-hand panel
  inverts the constrained axis: Fixed horizontally, Expanding vertically.
* `test_parameterbar.py:263-285` — asserts a raised page is wider than
  600 px.  Re-point at the panel's width.
* `test_parameterbar.py:158-207` — the height test.  In a side panel this
  goes green while measuring nothing, which is the exact failure its own
  docstring warns about.  Re-point it at the panel's width and at the
  height the stack gained.
* `test_panelsplitter.py:2420-2446` — three **exact** pixel equalities
  (501 / 501 + 2*S8 / 695).  Re-baseline and record the new numbers in
  the docstring, which is what that file already did once for Qt6.
* `test_labels.py:691-705` — asserts every group body has one height,
  i.e. that `equalize` ran.  In a side panel the rationale genuinely
  evaporates: a tab change can no longer resize the channel lanes,
  because the panel does not own their height.  Stop equalizing, change
  the test, and record why.  Keep `equalize` itself and its unit test at
  `test_eventoverlay.py:977-999` — the function is still correct, it just
  has no caller.

**New tests, all in `tests/test_parameterbar.py`** — not a new module.
A new module costs two more top-level windows and has reproduced a
SIGSEGV in `theme.collect_orphan_widgets` (todo.md), once while building
this very file's fixture.  Reuse `browser` and `wide_browser`.

* `test_the_panel_gives_its_height_back_to_the_stack` — the 16 channel
  stack is at least 168 px taller than before.  This is the feature.
* `test_a_hidden_panel_costs_the_window_no_width` — with the panel
  hidden, `window.minimumSizeHint().width()` equals what it is with no
  panel at all.
* `test_the_window_still_fits_a_laptop_with_the_panel_open` — the 1097
  guarantee, with the panel shown.
* `test_a_narrow_row_stacks_its_caption` — the caption occupies its own
  grid row spanning the width.
* `test_the_chip_rows_wrap_rather_than_widen` — the annotation chip rows
  ask for no more width than the panel has.
* `test_the_plugin_region_is_absent_without_plugins` — nothing visible,
  no width taken.
* `test_a_broken_plugin_panel_does_not_take_the_window_down` — a factory
  that raises costs its own tab and nothing else.
* `test_the_panel_width_is_remembered` and
  `test_a_hand_edited_panel_width_is_clamped`.

`tests/data/action-inventory.json` regenerates with
`AUDIAN_REGENERATE_GOLDEN=1`, in the same commit that adds the toggle.
The sweep at `test_actioninventory.py:198` will trigger the new action
blind, so it must not open anything modal.


## Order of work

Each one leaves the application runnable and the suite green.

1. **A wrapping row widget**, lifted from `CategoryStrip`, used for the
   annotation chip rows.  Still in the bottom bar.  Testable on its own.
2. **Narrow row mode** on `ParameterGroup`, with a unit test.  Still in
   the bottom bar, still unused.
3. **The panel itself**: the horizontal splitter, `SidePanel`, the icon
   strip, the built-in region moved into it, the bottom band gone.  The
   big one, and where the tests above get rewritten.
4. **The toggle**: action, `Ctrl+B`, View menu entry, focus rules,
   inventory regenerated.
5. **Persistence**: `side-panel` v1, width and open, debounced to gesture
   end, single-writer.
6. **The plugin region**: `add_panel_factory`, `setup_panels`, the
   `QTabWidget`, the isolation `try`.

Steps 1 and 2 are worth doing even if the rest stalls: they make the bar
narrower and more robust wherever it lives.


## Risks

* **The width floor is the one that can fail.**  Everything else is
  layout work with a visible result; this one is a number in a test that
  stands for a laptop somebody actually uses.  Measure it at step 3, not
  at step 6.
* **`set_alert` on an icon.**  The current design appends `!` to the tab
  text and `tests/test_parameterbar.py:298-328` asserts exactly that
  string.  An icon strip needs a different mark, and that test needs a
  decision rather than a mechanical edit.
* **Three widgets size themselves off their own `width()`** — the Labels
  file row, the annotation pointer readout, the category strip.  Measured
  100 px on a page that was never raised versus 1162 once raised.  In a
  320 px panel they get far less than 1162, so the elision assertions at
  `test_parameterbar.py:219-245` and
  `test_annotationpanel.py:1122-1165` may fail on content even when every
  layout assertion passes.
* **Icons for six groups have to be drawn.**  `glyph_pixmap`
  (`audian.py`) already has `spectrogram`, `trace`, `label` and
  `analyze`; Filter, Audio and Envelope need new marks, and
  `audian.py:71-74` records that `QStyle` standard icons were measured at
  1.09:1 on `bg.surface` and rejected.
* **Not yet known**: whether the built-in region should scroll or the
  panel as a whole; whether 320 px is right, which only looking at it
  will answer; and whether the plugin region wants to be a sibling
  splitter pane or a tab set inside one scroll area.


## What the build changed

Seven places where the built thing differs from the design above.  Each
one is a measurement the spec could not have had.

**The panel opens at 360, not 320.**  The annotation pointer readout was
given a row of its own so its counts survive elision, and that clause
needs 310 px of mono metrics.  A row gets the panel less its own two `S8`
margins and the group's: 288 px at 320, which elides it, and 328 at 360,
which does not.  Everything else fits either way -- the widest built-in
group is 172 px -- so the number is set by the one thing that does not.

**Narrow rows put each row's fields in one cell, not in shared grid
columns.**  Sharing them couples unrelated rows: measured, the Heterodyne
button's column widened the Source row too, and the Audio group stopped at
265 px instead of 181.  A lone field spans its caption's columns; two sit
side by side under it.

**At most one column of a narrow row takes the stretch.**  `pg.SpinBox`
reports `Expanding` without being asked, so `claim_stretch` fired for the
spin box beside a filter slider as readily as for the slider -- 161 px each
in a 344 px row, where the slider's width *is* the frequency resolution.
That is the rule `SPACER_COLUMN`'s own docstring states, now in code.

**Four new glyphs, not three.**  The two label groups cannot share `label`:
an icon strip has no words, and `audian.py:260-263` already records what
happened when `play_region` shared `play`'s pixmap.  So `filter`,
`envelope`, `speaker` and `label-fixed`.

**`glyph_icon` gained an on-state colour, and that is a defect fixed
rather than a parameter added.**  It painted every checked state in
`on.primary`, which is right for a tool bar button filled with
`primary.dim` and measures **1.07:1** on `bg.surface` in the daylight
theme -- invisible, in light mode only, on the tab the reader is looking
at.  The strip passes `fg`, which is what the stylesheet already does for
a checked tab's text.

**`theme.band` was not extended.**  The design asked for the rule to move
to the panel's leading edge; the splitter handle already draws a hairline
on the canvas side of itself and lights it on hover, so `band(left=True)`
would have put two vertical lines six pixels apart.  The panel takes the
chrome ground and no edge.

**Three more rows had to wrap than the design listed** -- the annotation
Show row (326 px of surface chips, the widest thing in that group with
nothing loaded) and the Source row (whose text is whatever the bundle
called itself), on top of the two chip rows.  And two combo boxes had to
stop publishing their longest item as a minimum: the NFFT list ran to
`524288  (10922.67 ms)` and the colour map to a 64 px swatch beside a name.

### Answers to the "not yet known"

* **The built-in region scrolls, not the panel.**  `ParameterTabs` takes a
  `scroll=` flag and puts its own pages in the area, so the icon strip
  stays put while the pages move under it.
* **320 px was not right; 360 is.**  See above.
* **The plugin region is a sibling splitter pane**, made on first use, so
  with no plugins the splitter holds one child and shows no handle.

### What it came to

| | before | after |
|---|---|---|
| 16-channel stack viewport | 483 px | **651** |
| ... its scroll range | 196 px | **28** |
| window floor, empty | 695 | 695 |
| window floor, bundle loaded | 797 | **695** |
| browser minimum, panel shown | 517 | 337 |
| browser minimum, panel hidden | -- | **110** |
| widest built-in group | 501 | **172** |
| tab strip | 489 px of text | **220** of icons |

### Still open

* The `Envelope` mark is the weakest of the six -- two hulls about an axis,
  which reads as a lens.  It is distinct from everything else in the strip,
  and the Envelope tab does not exist in the default pipeline, so it was
  left rather than chased.
* The panel is per-browser, and nothing shares it across file tabs.  That
  was never asked for and matches how `parameter-tab` already behaves.
* `side-panel` v1 does not store which plugin tab was open or where the two
  regions divide.  Deliberate; the constant's docstring says why.
