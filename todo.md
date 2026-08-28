# Low hanging fruit

- [x] Implemet sum spectrogram toggle: When all specs on all channels are shown and trace is disabled, a more useful representation is simply showing a single, summed spectrogram fullscreen. I think in that setting, it would be best to have a toggle to toggle between per-channel spec and sum-spec. (Shift+F2. Built as the *mean*, not the sum: they are the same picture 12.04 dB apart, and the mean lands 0.07 dB from a single channel's median where the sum lands +12.12, so the colour bar's dB ticks and the noise-floor heuristic keep meaning what they meant. Solo and mute choose what is averaged.)
- [x] Add manual labeling interface: The labels loaded with the toml stay immutable. But a user could still add manual labels. Bounding boxes on specs to constrain time and frequency and time ranges only on the trace. They could be saved along the existing labels or along the wav dataset as simple csv files with a col for time, a col for freq and a col for label or something along those lines. (`b` for label mode, then drag: on a spectrogram the box bounds time AND frequency, on a trace time alone. Categories are a preference in the settings file, `1`-`9` pick them, Ctrl+L edits them. Point categories are placed at the cross hair. Written to `<stem>-editable-labels.csv` beside the recording -- atomically, which nothing else in this tree does -- and never over a sidecar this session could not read whole. The fixed labels are untouched: separate store, separate file, separate tab.  A label can be corrected as well as made: Ctrl+click one in label mode to put grips on it, drag a grip to move or resize it, Ctrl+Delete to remove it, Escape to put it down.  Shift+B is now one level of undo over any of those rather than a pop of the last row.)
- [ ] Add a setting to constrain default frequency axis on spec view. We only want to see 0-2kHz in 99% of time. 
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
- [ ] A y axis limit field next to the nfft in the spec group of the bottom bar. This is the same want as "constrain default frequency axis" above and should be done once, as one control. Read `21169f6` before adding a widget to that bar: its width is the binding constraint on the 14 inch laptop, so measure the group's `minimumSizeHint` before and after.
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
