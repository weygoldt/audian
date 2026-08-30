# Low hanging fruit

- [ ] `set_channels` raises `IndexError` when the channels left showing hold
  none of the selected ones: `show_selected_channels[-1]` on an empty list
  (databrowser.py, the "current channel must be in shown and selected
  channels" block).  Hit while writing the label-editing tests -- select only
  channel 2, then hide it.  Pre-existing; the test works around it by
  selecting channel 0 first.

- [ ] StartupPage is the last floor at 734 px: three fixed columns capped at
  1100. It is what stops the window going below 734, so it is next if audian
  ever has to tile into half a laptop panel.

- [x] Follow native system theme (colors, fonts, icons).  Done as a third
  theme preference, `system`, which is now the default.  It takes the
  desktop's palette, its UI font and its icon pack, and follows
  `colorSchemeChanged` live.  The standard Qt way turned out to be *stop
  overriding*: while it is on, audian forces no style, sets no palette and
  applies no application stylesheet, and `tint`/`frame`/`band` use
  setPalette / QFrame shapes / setBackgroundRole instead of per-widget
  sheets.  Pinning dark or light still loads the two hand-made tables.

  What is still ours, and has to be: pyqtgraph resolves pens at
  construction, so the plot layer needs explicit colours and a QPalette has
  no opinion about what colour a filtered trace is.  Those tokens are
  derived from the platform palette where a role exists and kept from the
  matching hand-made table where none does -- the data series, the
  annotation hues, the spectrogram colormaps.  Derived tokens are then
  pushed away from their ground until they clear 4.5:1, because a desktop
  is under no obligation to: measured here, Breeze's accent scores 3.71 on
  the window ground and arrives as #4f9dcf rather than #308cc6.

- [ ] Seven toolbar glyphs have no freedesktop name -- spectrogram, trace,
  meanspec, colorbar, navigator, channels, play-region -- so in system mode
  the bar mixes the desktop's icon pack with seven drawn glyphs of our own.
  They agree on ink, since the drawn ones use the derived tokens, but not
  on drawing style.  Either find names that exist across the common packs
  or accept the mix; it is visible in the tool bar and nowhere else.

- [ ] The absolute pixel metrics were tuned to Inter 10pt and system mode
  now runs them against whatever face and size the desktop has -- 9pt Sans
  Serif here.  TOOLBAR_HEIGHT 36, CHANNEL_DENSE_HEIGHT 34,
  TOOLBAR_BUTTON_BOX 30, RAIL_NUMBER_HEIGHT 14, CONTROL_HEIGHT 26 and
  CHIP_HEIGHT 22 are the ones that would have to become QFontMetrics
  expressions before a reader with a 12pt desktop font is safe: at that
  size five of sixteen channels go below the scroll, which is the failure
  CHANNEL_DENSE_HEIGHT's own comment records.  Not hit here because the
  desktop font is smaller than Inter, not larger.

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
  and the eight cross-tab `link_*` switches.  Filter cutoffs are the
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
