# Low hanging fruit

- [x] Implemet sum spectrogram toggle: When all specs on all channels are shown and trace is disabled, a more useful representation is simply showing a single, summed spectrogram fullscreen. I think in that setting, it would be best to have a toggle to toggle between per-channel spec and sum-spec. (Shift+F2. Built as the *mean*, not the sum: they are the same picture 12.04 dB apart, and the mean lands 0.07 dB from a single channel's median where the sum lands +12.12, so the colour bar's dB ticks and the noise-floor heuristic keep meaning what they meant. Solo and mute choose what is averaged.)
- [x] Add manual labeling interface: The labels loaded with the toml stay immutable. But a user could still add manual labels. Bounding boxes on specs to constrain time and frequency and time ranges only on the trace. They could be saved along the existing labels or along the wav dataset as simple csv files with a col for time, a col for freq and a col for label or something along those lines. (`b` for label mode, then drag: on a spectrogram the box bounds time AND frequency, on a trace time alone. Categories are a preference in the settings file, `1`-`9` pick them, Ctrl+L edits them. Point categories are placed at the cross hair. Written to `<stem>-editable-labels.csv` beside the recording -- atomically, which nothing else in this tree does -- and never over a sidecar this session could not read whole. The fixed labels are untouched: separate store, separate file, separate tab.  A label can be corrected as well as made: Ctrl+click one in label mode to put grips on it, drag a grip to move or resize it, Ctrl+Delete to remove it, Escape to put it down.  Shift+B is now one level of undo over any of those rather than a pop of the last row.)
- [ ] `set_channels` raises `IndexError` when the channels left showing hold
  none of the selected ones: `show_selected_channels[-1]` on an empty list
  (databrowser.py, the "current channel must be in shown and selected
  channels" block).  Hit while writing the label-editing tests -- select only
  channel 2, then hide it.  Pre-existing; the test works around it by
  selecting channel 0 first.
- [ ] StartupPage is the last floor at 734 px: three fixed columns capped at
  1100. It is what stops the window going below 734, so it is next if audian
  ever has to tile into half a laptop panel.


- [ ] Follow native system theme instead of building a whole coustom theme from scratch

# Spec stuff 

- [x] Toggling spec should not toggle through different spec vs trace panel scalings. That scaling can now be done continuously by dragging, so it should just enable or diable spec, nothing more. (F3 is on/off. `show_specs` was doing two jobs -- whether there is a spectrogram and how tall it is -- so every reader had to know which one it meant and the dragged split was stored four times over under a key nobody could see. One split now, and it survives the spectrogram being toggled off and back on, which it did not before. `PANEL_SPLIT_SETTING_VERSION` goes to 3: a version 2 file holds up to four splits and nothing says which one the reader wanted, so it is dropped with a logged warning.)
- [x] double clicking the y axis (on spec and trace) should reset it. (Both axes of every lane, and it is not the same call on the two: a frequency axis opens at its full range, an amplitude axis opens *fitted to the data*. `reset` on an amplitude is what Shift+V does and goes to the format's full scale -- measured, a trace sitting in -0.117..0.129 goes to -1.000..1.000 -- which on recordings that peak at a few percent of full scale is a flat line, not a reset. So amplitude refits and frequency resets: both mean "the way the lane opened", which is what the same gesture already means on the panel splitter.)
- [ ] The rest of "double-clicking any movable thing should reset it": the highpass and lowpass cutoff handles on the spectrogram and their two sliders, the envelope cutoff slider, the overlap slider, the navigator's window region, and the colour bar. Left out of the pass that did the axes because those have no existing reset to point the gesture at -- an axis had `PlotRange.reset` and `auto_fit_y` already, and "reset a highpass" has to be decided (0 Hz, i.e. the filter off, is the obvious answer and is still a decision).
- [x] A y axis limit field next to the nfft in the spec group of the bottom bar. This is the same want as "constrain default frequency axis" above and should be done once, as one control. (One control, the Spectrogram tab's *Opens at*. It sets `PlotRange.rdefault`, which is read by `set_limits`' `# ranges:` block and by the new `default_view` operation and by nothing else -- deliberately **not** `rmax`, which is what `set_ranges` clips to and what `setLimits` hands pyqtgraph, so a band written there would take Nyquist away rather than merely open below it. Measured with a 2 kHz band: the lane opens 0-2000, `yLimits` is still `[0, 4000]`, a hand `setYRange(3000, 4000)` sticks, `end` reaches (2000, 4000), and `min_dr` stays 0.06103515625. The width budget `21169f6` asked about: the Spectrogram group is 535 px before and **535 px after** -- the row costs no width at all, the bar stays 551 and the window floor stays 734 -- and the price is 14 px of bar height, 154 to 168, because this group was tied-tallest at three rows. Ships with no band set. Absolute Hz, not a fraction of Nyquist: 0-2 kHz is a statement about the fish, and a dimensionless preference would open a 96 kHz recording at 0-24 kHz.)
- [x] `v`/`Ctrl+V` and the double click now mean the same thing on both lanes. The frequency axis had the gesture and no key at all -- `setup_frequency_actions` defined link, two zooms, up, down, home and end and neither a fit nor a reset -- while the amplitude axis had `v` and `Shift+V` as well. `Ctrl+V` was bound to nothing anywhere in the tree. So: `Ctrl+V` is Fit on the frequency axis and `Ctrl+Shift+V` is Reset, the `Ctrl` pairing `+`/`-` vs `Ctrl++`/`Ctrl+-` already uses, and the double click calls whatever that lane's bare key calls. The amplitude branch of `reset_y_range` was NOT folded in with it: `10b8832` measured that a `reset` there gives (-1.0, 1.0) on a trace sitting in -0.117..0.129, which is a flat line and not a reset, and the `y_fixed` guard is what keeps the gesture from breaking a reader out of a mode the tool bar goes on claiming.
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
- [ ] **The array panel should take the MAX over channels, not the mean.** The
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
