# Low hanging fruit

- [x] `set_channels` falls back to the shown channels when the channels left
  showing hold none of the selected ones, instead of asking an empty list for
  its last element.  The clamp is the one that block already ran -- the
  nearest candidate at or after the current channel, else the highest -- with
  the candidates widened from shown-and-selected to merely shown when the
  intersection comes out empty.  Two ordinary gestures produced it: clicking
  lane 2 makes `[2]` the whole selection, and hiding lane 2 leaves the two
  sets disjoint.

  Not "leave the current channel where it was", which is what the guard at
  `select_next_channel` does and was the obvious answer: `stepped_channel`
  reads `show_channels.index(self.current_channel)`, so a current channel
  left on a lane that has just gone is a `ValueError` on the reader's next
  arrow key, and an `IndexError` traded for a `ValueError` two gestures later
  is not a fix.  `set_channels` is the only place that invariant is enforced,
  which is exactly what lets `select_next_channel` and
  `select_previous_channel` go on leaving their own anchor alone.  With
  nothing shown at all it does stay where it was: `current_channel` is an
  index -- the borders, the focused spectrogram lane and the tool bar's `%d`
  all take it -- and there is no "no channel" value to reach for.

  The workaround in `test_hiding_the_lane_the_grips_are_on_drops_the_selection`
  is gone with it, and a regression test in the same file takes both gestures
  and then presses the arrow key the `ValueError` would have been waiting on.

- [ ] StartupPage is the last floor at 734 px: three fixed columns capped at
  1100. It is what stops the window going below 734, so it is next if audian
  ever has to tile into half a laptop panel.  On Qt6 that floor is 695, and
  since the side panel landed it is the floor with a session bundle loaded
  too -- the parameter bar used to take it to 797 and no longer does.

- [x] `toggle_channel` names the scroll area now, and the entry this replaces
  had the diagnosis backwards.  `DataBrowser.setFocus()` is **not** a no-op:
  `QWidget.setFocus` ignores the focus policy -- the policy decides only what
  a Tab or a click may focus -- so the browser took the keyboard every time
  it was told to, `NoFocus` and all.  The old measurement that said otherwise
  was made in a window the offscreen platform never activated, where
  `app.focusWidget()` reads `None` whatever the code does; activated, the
  focus really does land on the `DataBrowser`, which has no `keyPressEvent`
  and no scroll bar, so Page Up and the arrows did nothing after every
  channel toggle until the reader clicked the stack back.

  Not removed, because the call had a job and it is one this file already
  records for `set_side_panel`: hiding a channel hides its rail row, the rail
  row is `StrongFocus` because it answers S and M itself, and Qt's own choice
  for where the keyboard goes when that row is hidden is -- measured -- the
  side panel's scroll area, across the window from the stack the reader was
  rearranging.  So it hands it to `stack_area`, unconditionally, the way it
  always meant to.  Two tests in `test_panelsplitter.py`, both of which read
  the browser itself as the focus widget before the fix.

- [x] `theme.restyle_tree` reads the band spec with `edges[0:1]` and
  `edges[1:2]` now.  A slice cannot raise, and a missing edge reads as an
  absent one -- the same answer the widget would have got had nobody
  recorded that edge.  Defence rather than a repair: the only writer is
  still `band()`, which always writes both digits, so nothing reachable
  today produces a short spec.  Worth the two characters anyway, because the
  cost of being wrong is a theme switch that stops half way through the
  window and leaves the rest of it on the palette the reader just left.

  The test asserts what happens to the widgets *after* the short one, which
  is the actual claim -- a test that only said "does not raise" would go
  green on a walk that restyled nothing at all.

- [ ] A dead assertion in `tests/test_annotationpanel.py`.  The last line of
  `test_the_span_counts_never_ask_the_parameter_bar_for_more_width` is
  `assert panel.parambar.sizeHint().width() == before`, and the stub's
  `parambar` is a bare `QWidget` with no layout: both sides are `-1`, so it
  has never tested anything.  Re-point it at something with a real hint --
  `annotation_group.minimumSizeHint().width()` before and after -- because
  in a panel the readout genuinely is the widest thing in that group and
  the `Ignored` policy is what stops it dragging the panel open.

- [x] Follow the system theme, for dark and light.  A third theme
  preference, `system`, now the default: it reads
  `QStyleHints.colorScheme()` at startup, follows `colorSchemeChanged`
  while running, and loads audian's own dark or daylight table accordingly.
  Pinning a theme by hand turns the following off, because otherwise the
  desktop's next change would undo the choice just made.

  The colours stay audian's, and that was arrived at the long way round.
  Deriving the tokens from the platform palette works and was built --
  grounds from Window and Base, ink from WindowText, contrast-repaired
  against audian's own 4.5:1 gate -- and the result was reverted, because
  the two hand-made tables are designed: gated for contrast, separated for
  colour vision deficiency, and in the daylight theme's case built for a
  screen at 50,000 lux, which is not derivable from any desktop.  Going
  further and dropping the application stylesheet as well -- the textbook
  Qt way to look native -- was worse still: the metrics, radii, hairlines
  and control heights all live in that sheet, and without it what is left
  is stock Fusion chrome.  What the desktop gets to decide is which of the
  two tables the reader is looking at.

  `push_color_scheme` still tells Qt which scheme we are painting, so the
  title bar, the portal file dialog and platform menus stop being light
  around a dark application.  `Qt.ColorScheme.Unknown` maps to dark: that
  is what the offscreen platform reports and the suite runs offscreen.

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

- [x] Store user settings in .config directory.  The spectrogram colormap
  moved out of the QSettings INI it had to itself and into settings.json
  under a versioned `spectrogram` key, together with nfft and the overlap.
  It is stored as a **name per theme** rather than as the index it was: the
  two themes offer different lists, so the same index meant a different map
  on each and an index past the end of the shorter one was silently clamped
  to zero -- and now that the theme can follow the desktop and flip with no
  gesture at all, that would have been a preference resetting itself twice
  a day.  `save_setting` also became atomic; it rewrites the whole document
  for one key, and an interrupted write took the label vocabulary with it.

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

# Spec stuff 

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
