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

- [ ] **Sliders for power, max and min.**  Six keys drive the colour scale
  and none of them can be given a number: `D`/`Shift+D` (`power_down` /
  `power_up`, both ends together), `K`/`Shift+K` (`max_power_down` /
  `max_power_up`) and `J`/`Shift+J` (`min_power_down` / `min_power_up`).
  They run `Audian.apply_power_ranges(name)` -> `apply_ranges(name,
  browser.spectrogram_power)` -> `PlotRange.step_up` / `max_up` / `min_up`
  and their opposites (plotranges.py:371-436), each one `rstep` followed by
  `set_ranges`.

  Wanted: the shape the filter cutoffs already have -- a slider and a
  `pg.SpinBox` on one row, the slider wrapped in
  `ParameterGroup.expanding(...)`; see the High-pass and Low-pass rows.
  Two rows is probably right, **Max** and **Min** as the two ends in dB,
  with **Power** as the pair moving together -- but three sliders in a
  group that is already the widest page is a layout question worth
  measuring before it is a code question.

  The hard part is not the widgets.  It is that the number then has three
  writers: the keys, the sliders, and `fit_levels`, which refits the ramp
  whenever a panel is shown or the smoothing changes.  So the widgets have
  to *follow* the range and not own it.  The sink every path ends in is
  `SpectrogramPlot.setZRange`; `_applying_levels` and
  `_cbar_levels_changed` beside it record why "the widget changed, so the
  reader changed it" is wrong, and `set_color_map` and
  `set_spec_smoothing` show the `blockSignals` sandwich to write a widget
  back with.

  `LogSlider` (databrowser.py) is the filter's slider and is logarithmic
  in Hz.  dB is already logarithmic, so these want a plain `QSlider` over
  `rmin..rmax`, not that class.

- [ ] **Peaking: show what is clipped at the top of the colour ramp.**  A
  checkbox and a key, after focus peaking in a camera: the reader wants to
  see which bins sit at or above the top of the current ramp, because
  those are the ones whose differences the picture has stopped showing.
  The top end only -- half the panel is at or below the floor by
  construction (`fit_levels`), so marking the floor would mark everything.

  The colour map is the cheap correct implementation, not a mask.
  `pg.ImageItem.setLevels((zmin, zmax))` maps everything at or above
  `zmax` onto the **last LUT entry**, so a map whose final stop is a
  warning colour marks exactly the clipped pixels at no per-frame cost.
  With a 256 entry LUT that entry also covers the top 0.4 % of the ramp,
  which is what a video scope's zebra does and is the wanted behaviour
  anyway.

  It has to be applied where the map is applied, or `Shift+C` or a theme
  switch will quietly drop it.  `Panel.set_colormap` (panels.py) pushes to
  `self.axcs`, the colour bars, and `pg.ColorBarItem` forwards the LUT to
  the image it was handed by `setImageItem`.  `resolve_colormap`
  (panels.py) is the one place a name, an index and a `pg.ColorMap` all
  become a `pg.ColorMap`, so a "and then redden the top stop" wrapper
  belongs beside it.  `DataBrowser.apply_theme` re-pushes the map after a
  theme change, and is the path that catches a version which only applied
  it once.

  Take the warning colour from `theme` rather than writing a literal: the
  two pages have different grounds, and the whole point is a colour that
  cannot be mistaken for a hot bin of the ramp itself.

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
