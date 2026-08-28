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

- [ ] Toggling spec should not toggle through different spec vs trace panel scalings. That scaling can now be done continuously by dragging, so it should just enable or diable spec, nothing more
- [ ] double clicking the y axis (on spec and trace) should reset it. Generally, double-clicking on sliders or any movable thing should reset it in my opition. The y axis limit could live next to the nfft in the bottom spec panel
- [ ] Move the mean spec to a sum spec. Mean spec is nice for noise supression but only if the recorded signal happens on many channels. If a sgnal of interest, such as that of a weakly electric fish, only moves over single channels over time, it gets averaged out. So instead, lets do a sum spec and divide by the channel count to land in a similar power scale to a single spec. And when we have that, we can also visualize the trace for all channels superimposed on a single timeline panel below. So sum spec becomes top panel the sum spec, bottom panel the trace, i..e all traces superimposed in one panel

# Future larger dev sessions

- [ ] check spec backend. torch could be faster. 
- [ ] Write plugin interface, so that wavetracker, eeltracker, etc. algos can be visualized interactively in audian
