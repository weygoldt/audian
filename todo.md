# Low hanging fruit

- [x] For spectrograms, keep the powerspec and the colorbar off by default.
  `show_powers` was already off; only `show_cbars` had to flip.  Doing it
  surfaced a real bug and its fix: the *first* time a colour bar is shown,
  its `pg.ColorBarItem` has never been laid out and publishes no width, so
  the one deferred pass `schedule_axis_alignment` made measured a lane
  59.5 px too wide and the shared time axis sat past the panel.  It never showed
  before because the bar was on at start-up, so there was no first time.
  The pass now repeats while it is still moving the margins.

- [x] Add a drop down menu for spectrogram smoothing.  `smoothing.py` holds
  the menu; the row is in the Spectrogram page under Colormap; the choice
  persists in the `spectrogram` settings block by key (not by position, so
  a newer audian's entry cannot re-point an older one's preference).
  **Bicubic is not on it, and that is measured rather than skipped.**  On
  the reference block -- 129x3000, one 1500 px lane at nfft 256, against
  the 8.19 ms `decibel()` already spends on it -- a Gaussian costs 5.03 ms
  and 80 ms across sixteen lanes, which a hysteresis-gated re-upload can
  afford.  `zoom(order=3)` costs 76.58 ms and 1.2 s across sixteen, and a
  3x3 median 58.85 ms and 941 ms.  Neither is affordable, and Qt has no
  bicubic to borrow -- `QPainter` offers fast and smooth, and smooth is
  bilinear.  So the menu answers bicubic with Bilinear and median with Box.

- [ ] Spectrogram denoising and synchrosqueezing, and a way to play with the
  parameters.  Deliberately left: the smoothing above is a *display* filter
  -- cheap, local, and reversible by picking None -- and these are not.
  ssqueezepy is not a dependency and would be the first one added for a
  single feature.  Two things the smoothing work settled that this should
  reuse rather than rediscover: a filter that changes the drawn numbers has
  to be fitted by `SpectrogramPlot._level_range` too, or the panel dims
  instead of sharpening (the loudest bin drops 5 dB under a Gaussian of one
  bin and 15 dB under two); and `SpecItem.get_power` has to read the drawn
  pixel rather than the raw bin, because the two are a median of 3.0 dB and
  up to 50.7 dB apart, the worst of it at the chirp onsets a reader points
  at.  A sigma slider is the small end of this and would fit beside the
  dropdown; `smoothing.METHODS` is where a new entry goes.

- [ ] StartupPage is the last floor at 734 px: three fixed columns capped at
  1100. It is what stops the window going below 734, so it is next if audian
  ever has to tile into half a laptop panel.  On Qt6 that floor is 695, and
  since the side panel landed it is the floor with a session bundle loaded
  too -- the parameter bar used to take it to 797 and no longer does.

- [ ] Only the light/dark bit is taken from the desktop, not its accent.
  Qt 6.6 exposes `QPalette.ColorRole.Accent` and it would be the one
  desktop colour worth adopting -- it is the reader's stated preference and
  it appears in exactly one audian role, `primary`.  It needs the contrast
  repair that already exists for it (measured, Breeze's accent scores 3.71
  on the window ground, under audian's 4.5 bar), and it needs a decision
  about whether a repaired accent that no longer matches the desktop's is
  better than one that does.

- [ ] Chrome margins are balanced for the tool bar and the status bar only.
  The parameter bar measured healthy -- 30 to 110 px around its rows -- and
  the plot rails were not looked at.  If anything else reads as crammed,
  `BAND_PAD_V` is the number to reach for, and the band's own height has to
  be derived from it: the tool bar's bug was that it was not, so the layout
  paid for the shortfall out of the padding.

- [ ] The rest of the unpersisted state, if it is wanted: panel visibility
  (`show_traces`, `show_specs`, `show_powers`, `mean_spec`), the y-range
  policy, the grid mode, the cross hair, rail visibility, the audio group
  and the eight cross-tab `link_*` switches.  The side panel's own width
  and open state are done, under `side-panel` v1.  Filter cutoffs are the
  awkward ones and were left alone deliberately: `BufferedFilter.open`
  resets all three unconditionally, so the only correct place to restore
  them is `DataBrowser.open` after the loader, which is where -f/-l already
  land -- so persisting them means deciding the CLI-versus-settings
  precedence, and that is a decision rather than a mechanism.  Channel
  selection is per-file and needs the clamp `open()` already applies.

# Spectrogram controls

Three additions to the Spectrogram page of the side panel, asked for
together and best done together: they share a `ParameterGroup`, and two of
them have to keep agreeing with keys that already work.

The page is built in `DataBrowser.setup_parameter_bar` (databrowser.py);
the row helper is `ParameterGroup.add_row(caption, shortcut, *widgets)`,
which only ever appends -- there is no insert API, so the order of the
rows is the order they are added.  Every combo box in this group goes
through `narrow_combo`, because this page already sets the width of the
whole side panel and a control that publishes a wide size hint widens the
window's minimum with it.

`Smoothing` is the worked example for all three: state on `DataBrowser`, a
setter shaped like `set_color_map` with `dispatch` and `save` flags,
persistence in the `spectrogram` settings block, a `sig...Changed` signal
so every tab agrees, and the choice pushed down through `Panel` to the
items.  **Do not bump `SPECTROGRAM_SETTING_VERSION`** to add a key:
`spectrogram_settings` drops the whole block on a version it does not
recognise, so a bump takes every reader's colormap, window and overlap
away to add a preference they have not set yet.

Two harnesses will complain, both by design.  `tests/test_actioninventory.py`
freezes every action into `tests/data/action-inventory.json`, so a new key
means regenerating it in the same commit -- `AUDIAN_REGENERATE_GOLDEN=1
.venv-qt6/bin/python -m pytest tests/test_actioninventory.py` -- and the
diff is the review.  Its sweep also fires every action on a loaded
recording, and puts the theme and the window state back afterwards; a new
action that leaves state behind has to join them.

- [x] **A checkbox for the filter cutoff lines.**  `Filter` / `Cutoff lines`,
  last row of the Spectrogram page, in the shape the Filter page's `Linked
  band` already has -- a checkable `QToolButton`, which carries its label
  without the indicator column a `QCheckBox` would add to the width of the
  widest page in the bar.  Measured on `data/Gryllus_campestris.wav` at
  1600x1000, traces off, both lanes on screen, cutoffs at 2000 and 3500 Hz
  of a 96 kHz recording: hiding them changes 11537 of 1600000 pixels, all
  of them between rows 400 and 805 and columns 114 and 1347 -- inside the
  two lanes -- and moves no number at all.  Showing them again is pixel for
  pixel the picture from before.

  A hidden handle is non-interactive too, and the two states are kept apart
  rather than folded together: the region mode writes movability and the
  checkbox writes visibility, and either may write while the other is off.
  `SpectrogramPlot._apply_handle_state` is where they meet, and the
  regression test drives all four corners of the pair.

  Persisted beside the colormap, as `cutoff-lines` at the **unchanged**
  version 1, and dispatched to every open tab.  It says what a spectrogram
  should look like rather than what this file is, which is the same reason
  `Smoothing` is a preference -- a reader who has just cleared two lines
  off sixteen lanes did not ask for them back on the next recording.  Only
  a real `bool` is believed, so a `0` or a `1` a hand-edited file offers is
  an unset preference and the lines are drawn.

- [x] **Sliders for power, max and min.**  Three rows on the Spectrogram
  page, under Smoothing and above Opens at: **Max** `K / ⇧K`, **Min**
  `J / ⇧J` and **Power** `D / ⇧D`, each a plain `QSlider` over the axis's
  own -200..20 dB and a `pg.SpinBox` beside it, in the shape the filter
  cutoffs have.  A plain slider and not `LogSlider`: dB is already a
  logarithm, and a log scale over one would spend a hundred of the two
  hundred and twenty positions inside the top decade of a quantity that is
  uniform end to end.

  **Three rows and not two, and that was the open question.**  Max and Min
  alone can reach every state the mapping has, so Power is redundant as a
  *state* -- but not as a gesture: sliding the ramp without changing its
  span is two drags with the other two and the span changes in between.
  Measured, the layout question the todo raised has no cost in it: this
  page's minimum width is **172 px with three rows and 172 px with two**,
  because `POWER  D / ⇧D` is narrower than the slider and number box every
  row here already carries, and the window's own minimum is 695 px either
  way -- the floor `StartupPage` sets.  So the third row costs one row of
  height and nothing else, and the six keys the reader named each have a
  row.  Two rows remains defensible; it was rejected on the gesture, not on
  the width.

  The rows **follow** the mapping and never hold it.  `set_level_range`
  writes through `PlotRange` like every other writer and
  `sync_level_widgets` reads back what actually landed, called from
  `SpectrogramPlot.setZRange` -- the one sink the keys, `fit_levels` and a
  colour-bar drag all end in.  Two things that had to be got right: the
  write is memoised, because a sixteen channel gesture calls `setZRange`
  sixteen times with the same pair and a `QSlider` handed its own value
  mid-drag interrupts the drag; and the memo is **dropped before every
  widget-driven write**, because the interesting case is the one that
  changes nothing -- a slider dragged past the other end asks for a range
  that is refused, and a memo keyed on the mapping alone leaves the slider
  parked at a number the picture is not drawn against.

  The two ends are held one `rstep` apart in `set_level_range`, because
  `PlotRange.min_step` only refuses to push the floor *past* the ceiling
  and a slider can ask for more than a key can.  A widget-driven change
  mirrors to the other tabs under the same `Link power` (`Alt+P`) switch
  the keys are gated on, as an absolute pair rather than a step.

- [x] **Peaking: show what is clipped at the top of the colour ramp.**  A
  `Clipping` / `Peaking` checkbox on the Spectrogram page and the `X` key,
  which are **one object** -- the button takes the action as its default
  action, so there is nothing to keep in step.  The colour map is the
  implementation and not a mask: `PeakingColorMap` (panels.py) replaces
  the **last LUT entry**, which `pg.ImageItem.setLevels` maps everything at
  or above `zmax` onto, so it marks exactly the clipped pixels at no
  per-frame cost.  Measured on data/Gryllus_campestris.wav at 1600x1000
  with the ramp pushed to -110..-70 so 0.89 % of the bins clip: turning it
  on changes **5954 pixels inside the stack and every one of them is the
  mark colour** -- nothing else in the picture moves.  Every LUT entry but
  the last is byte for byte the plain map's, at nPts 256 and 512, with and
  without alpha.

  Applied in `DataBrowser.set_color_map`, which is the sink `Shift+C`, the
  dropdown, `apply_theme` and the switch itself all end in, and pushed
  again at the end of `open` because `SpectrogramPlot.__init__` builds its
  colour bar before the browser has anything to push.  All three paths are
  driven in the tests.

  **The colour is measured, not chosen.**  `spec.clip` = `#26DAFF`, one
  value for both themes, scored as CIEDE2000 under the worst of four
  vision kinds against three things: the top 5 % of every ramp both themes
  offer (what surrounds the mark), the bottom 5 % (half a panel is at or
  below the floor by construction, and a mark that looks like the floor
  reads as a hole), and the four colours a lane already paints on the
  spectrogram -- `primary` (the cutoff lines and the rubber band), `accent`
  (the playback cursor) and the two annotation hues.  Worst of the six
  numbers: **15.91**, against `MIN_CATEGORY_SEPARATION`'s 15.0.  Red scores
  8.71 and orange 3.84, because the hot end of half these ramps *is* red or
  orange, and `primary` scores 0.71 because the daylight maps run white to
  blue.  Asking for separation from the *whole* ramp is unachievable for
  any colour -- a sequential map passes through 255 of them.

  `X`, and the letter is a decision: peaking's own initials are all spent
  (`P` plays a region, `C` centres the amplitude, `Shift+C` cycles the
  map), and `Alt+K` -- which would have read as "the other thing about the
  top of the ramp, whose key is `K`" -- was rejected because every one of
  the six `Alt+<letter>` bindings this application has means "link this
  across tabs".  Checkable, and safely so: it opens from the settings file,
  which `test_actioninventory` sandboxes, so the tick frozen into the
  golden file is the same on a desktop and headless.

  Persisted as `peaking` at the **unchanged** version 1 and dispatched to
  every tab, like the cutoff lines above.

- [x] **An input box for the overlap, not just a slider.**  Done: the
  Overlap row is now a `QSlider` (`ofracsliderw`, still whole percent, still
  the coarse grab) and a `pg.SpinBox` (`ofracw`) where the read-only
  `QLabel` was, which is the shape the two filter cutoffs and the three
  level rows already had.  The names follow that shape too -- `<x>w` is the
  box and `<x>sliderw` the slider everywhere else in this bar, so the slider
  took the longer name rather than the box taking a worse one.

  **The box is the precise writer and the slider is never read back from.**
  Written separately in `set_resolution` rather than one from the other: a
  62.5 % typed in and rounded through the slider comes back 62, on the pass
  that is supposed to confirm it.  Driven: 62.5 % typed gives
  `overlap_frac` 0.625, the box 62.5 and the slider 62.  No memo was needed
  -- unlike `setZRange`, `set_resolution` is already called once per
  gesture, because `update_resolution` debounces at 200 ms.  A box typed
  into goes through that same debounce, which a test pins by asserting the
  timer is running and `overlap_frac` has not moved yet.

  **Bounded 0..100 and not at the clamp's 99.999 %, which is the one real
  decision here.**  `set_hop` rounds the hop to whole frames and floors it
  at one, so the highest overlap the transform can actually reach is
  `1 - hop/nfft` -- measured over `NFFT_EXPONENTS`, 99.99923706 % at nfft
  131072, which is *above* the clamp.  A box bounded at the clamp would
  round that down and report a picture drawn at something else.  100 %
  typed in is refused by the clamp instead, and visibly: at nfft 256 the box
  comes back saying 99.609 %, which is what a hop of one frame is.

  `decimals=5`, and measured rather than picked: `pg.SpinBox` formats with
  `%g`, so at the level rows' 4 every window from 16384 up prints its own
  ceiling as "100 %".  5 is the fewest at which none of them does.  It is
  not enough to write the ladder `O` walks exactly -- 99.609375 % needs
  eight -- so the box rounds what it shows and keeps what it holds.

  The Δt readout the label carried moves into both tool tips, which is where
  the Window row above already keeps its own Δf.  It costs the page no
  width: measured on the four channel fixture, this group's minimum is
  172 px with the box and 172 px with the label, and the window's own
  minimum is 695 px either way.

  One thing the row cannot do and the box now can: the slider stops at 99,
  so no drag reaches the 99.609375 % a hop of one frame is.  `setValue`
  clamps that write itself; the test says so rather than hiding it.

# Spec stuff 

- [x] **Annotation marks hide the navigator's own waveform.**  Done, and by
  raising the waveform rather than lowering the marks.  `fulltraceplot` now
  names its own ladder -- `NAV_REGION_Z` 50, `NAV_TRACE_Z` **70**,
  `NAV_ACTIVITY_Z` 71, `NAV_ZERO_Z` 80 -- where it used to write 50, 10, 11
  and 20 as literals.  The overview and its zero line are above
  `eventoverlay.NAV_MARK_Z` (60) and `labeloverlay.LABEL_NAV_Z` (65), which
  are untouched, so the measurement `LABEL_NAV_Z` carries still holds: the
  marks still clear the translucent selection region.

  **Screenshotted both ways, as the entry asked.**  Driven on
  data/Gryllus_campestris.wav at 1600x1000 -- 17.951 s carrying 118 pulses,
  35 trials and 44 label spans, the picket fence -- with the window on
  4..10 s so the region covers x 361..734 of the 1232x96 strip.  Counting
  pixels that are exactly the overview's own pen colour, (87, 144, 174):

  | z of the overview  | in total | inside the region | outside it |
  | ------------------ | -------- | ----------------- | ---------- |
  | 10 (was)           | 2600     | 0                 | 2600       |
  | `NAV_TRACE_Z` (70) | 5363     | 1866              | 3497       |

  Twice the overview on screen, and read back rather than counted only: at
  10 the waveform is a set of fragments between the marks, and at 70 every
  burst reads as one shape with the marks passing behind it.  The marks stay
  perfectly legible -- they are full height and the waveform only covers
  them where it has amplitude.

  **The consequence nobody asked for, stated so it can be overruled.**  A
  trace above 65 is also above the region at 50, so the region's wash now
  tints the ground and no longer the trace.  The zero column above is what
  that looked like before: not one pixel inside the window the reader is
  working in was the waveform's own colour, because it sampled (85, 143,
  189) there -- exactly (87, 144, 174) under `theme.region_brush`.  The
  region is still obvious, since what marks it is the ground: (13, 18, 25)
  outside against (25, 40, 66) inside, the same pair `LABEL_NAV_Z` records.
  The cost is a little of its two edge lines, which the waveform now
  crosses: 120 pixels of (76, 141, 255) before against 54 after, in the same
  four columns, so both edges are still drawn and neither has moved.  This
  is what a minimap usually does and it looks better, but it is the one
  thing here a reader might want back.

  The fallback -- a thinner pen, a lower alpha or a density band -- was not
  taken.  It makes a mark cheaper without making the waveform visible: at
  118 marks over 17.951 s the fence is a fence at any alpha, and every one
  of those variants is a new appearance decision where a z is an ordering
  that was simply wrong.

  Two things had to be driven rather than reasoned about.  The zero line
  rises with the trace and not with the marks, because a reference chopped
  up by the annotations the trace has just cleared is worse than no
  reference.  And the selection region keeps the mouse: an item on top of a
  control is how a control stops working, but `EnvelopeItem` and
  `ActivityItem` are plain `pg.GraphicsObject`s with no `ItemIsMovable` and
  no `mousePressEvent`, so `QGraphicsItem`'s default ignores the press and
  the scene hands it down -- unlike the cutoff `pg.InfiniteLine`s
  `set_handles_movable` has to switch off.  A test presses at y=0, where the
  envelope is solid, and asserts the region moved.

- [ ] **F2 is a one-way door.**  Low priority, and *not* the bug it was
  found under: the report was "I cannot toggle off the spec", and F3
  (`toggle_spectrograms`) does exactly that, from the mean mode too --
  measured, from ``traces=0 specs=1 mean=1`` it gives ``1/0/0``.  That part
  was a misremembered key, Shift+F2 being `toggle_mean_spec`.

  Driving the three keys to check it turned up something that is not a
  misremembering.  From the opening state, printing
  `show_traces` / `show_specs` / `mean_spec` after each press:

  ====================  =====================================================
  key, pressed 4x       states, from ``traces=1 specs=0 mean=0``
  ====================  =====================================================
  F2                    ``0/1/0``, ``1/1/0``, ``0/1/0``, ``1/1/0``
  F3                    ``1/1/0``, ``1/0/0``, ``1/1/0``, ``1/0/0``
  Shift+F2              ``0/1/1``, ``1/1/0``, ``0/1/1``, ``1/1/0``
  ====================  =====================================================

  **F2 never comes back.**  Its first press turns the traces off, and
  `toggle_traces` forces `show_specs = 1` so the lanes are not left empty;
  from then on F2 oscillates between "spectrograms only" and "both", and
  the opening state -- traces alone -- is unreachable with that key.  A
  reader who pressed F2 once has a spectrogram they did not ask for, and
  getting back to where they were needs a different key.

  The fix that fits what is already here is to make F2 a round trip the way
  Shift+F2 already is one: `set_mean_spectrogram` remembers
  `traces_before_mean` and restores it, and `toggle_traces` wants the same
  memo for the spectrogram it switched on.  Then F2 is traces-only ->
  spectrograms-only -> traces-only, F3 stays the explicit switch, and no new
  key is spent.

  One inconsistency to settle while in there: the "never leave the stack
  empty" rule lives in the *toggles* and not in `set_panels`, so it is not
  an invariant.  Measured, `set_panels(traces=False, specs=0)` returns
  ``0/0/0`` and draws the empty stack the toggles exist to prevent --
  reachable from a plugin, from a linked tab, or from a settings file.

- [ ] **`theme.collect_orphan_widgets` can segfault the test suite.** It
  walks a snapshot of `QApplication.topLevelWidgets()` and calls
  `setParent(holder)` inside that same loop, so a reparent can make Qt
  destroy a widget a later iteration then dereferences -- and a dangling
  wrapper is a segmentation fault, not a `RuntimeError`. Measured while
  adding the band tests: pristine master runs the full suite 747 passed with
  no crash; the same branch with the band tests in a module of their own,
  which cost two more windows in the process, segfaulted in two runs out of
  four, both times at `widget.parentWidget()` (theme.py, the
  `if widget.parentWidget() is not None or widget.isVisible():` line), once
  while building `test_parameterbar`'s fixture and once while building the
  band module's own. The same three modules run alone pass, 165 of them, so
  it is the whole suite's accumulated widget state and not any one module.
  Worked around rather than fixed -- the band tests reuse a fixture that
  already exists, so the suite builds no more windows than before -- because
  the fix is a two-pass rewrite of that function (read, then reparent, with
  a validity check before each touch) and it deserves its own commit and its
  own measurement. It will bite again the next time anyone adds a test
  module that opens a browser.
  
- [ ] The colour ramp is fitted over the **whole** frequency axis, not over
  the band on screen: `visible_block` crops in time only
  (`spectrogramplot.py`), and `estimate_noiselevels` takes its floor from
  `db[:, -nf:]`, the top 1/16 of the full axis
  (`bufferedspectrogram.py`). So a 96 kHz recording opened at 0-2 kHz by
  the new *Opens at* field is coloured by statistics dominated by 94 kHz
  the reader cannot see. This is the thing most likely to be reported as
  "the new setting broke the spectrogram", and it should be settled before
  a narrow band is recommended for wideband files. Not measured -- the test
  fixture is 8 kHz and the effect needs a wideband recording.

- [ ] `hover_panel` is never cleared once the pointer leaves the stack: it
  is set to None only inside `mouse_moved` (databrowser.py), which does not
  fire on a Leave. So `Ctrl++`/`Ctrl+-`, which go through
  `pointer_axes`, already act on the last lane the pointer visited after a
  move onto the parameter bar. Pre-existing; found while deciding that the
  new frequency fit/reset keys should take `Panel.frequencies` rather than
  `pointer_axes`, which is why they do.
<!-- Not a todo: measured, decided, and recorded so it is not rediscovered
     as a bug.  `Ctrl+Shift+V` reaches the window action while focus is in a
     text field (measured: `Ctrl+V` fires it 0 times, so paste survives;
     `Ctrl+Shift+V` fires it once).  The only visible editable field in the
     main window is `ChannelRailRow`'s electrode label, and the owner does
     not rename channels -- numbers are enough -- so this is accepted rather
     than fixed.  Revisit only if electrode labels start being used. -->

- [ ] **The large spec panel should take the MAX over channels, not the mean.** The
  complaint is right and the fix first proposed is not: a signal that only ever
  sits on one or two electrodes -- a weakly electric fish moving over the grid --
  is averaged away by a panel that stands for sixteen. But *sum divided by the
  channel count is the mean*, exactly, so that swap is a no-op;
  `6e1d38c` records the same fact from the other side, that the sum and the mean
  are "the same picture 12.04 dB apart", and 10*log10(16) = 12.04.

  Neither reduction helps the single-electrode case. With signal power `P` on one
  electrode and noise `n` on the other fifteen: the mean gives `(P + 15n)/16`, so
  the signal drops 12 dB and the floor stays put; the undivided sum gives
  `P + 15n`, so the signal stays and the floor climbs 15x. One lowers the signal
  and the other raises the floor.

  **Max over channels per time-frequency bin** is the one that does what is
  wanted: a single-electrode signal keeps its full amplitude and the floor only
  rises by the max of N noise draws. It is close to a one-line change in
  `bufferedspectrogram.channel_power`, but four things downstream were tuned to
  the mean and each needs re-measuring: `BufferedSpectrogram.estimate_noiselevels`,
  `SpecItem.noise_levels` (whose docstring records "the mean's floor lands 2.3 dB
  from a single channel's and its top 37.5 dB from it"), `NOISE_FLOOR_MARGIN_DB`,
  and `SpecItem.get_power`, which has to keep agreeing with the pixel under the
  cursor. The panel caption says `MEAN 00-15` and would have to say something else.
  Worth considering whether the reduction should be a choice rather than a rule.
  Good test case: `logger09-20250916T164744.wav`, where electrodes 08-11 are
  recorded as exactly zero.

- [ ] Superimpose every channel's trace in one panel, under the array
  spectrogram. Separate from the reduction above and probably its own commit: it
  is a new panel *kind* rather than a change to an existing one, and `panels.py`,
  `LabelOverlay` and `EventOverlay` all key off what a panel is.

# Future larger dev sessions

- [ ] check spec backend. torch could be faster. 
- [ ] Write plugin interface, so that wavetracker, eeltracker, etc. algos can be visualized interactively in audian
