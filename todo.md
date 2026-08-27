# Low hanging fruit

- [x] Implemet sum spectrogram toggle: When all specs on all channels are shown and trace is disabled, a more useful representation is simply showing a single, summed spectrogram fullscreen. I think in that setting, it would be best to have a toggle to toggle between per-channel spec and sum-spec. (Shift+F2. Built as the *mean*, not the sum: they are the same picture 12.04 dB apart, and the mean lands 0.07 dB from a single channel's median where the sum lands +12.12, so the colour bar's dB ticks and the noise-floor heuristic keep meaning what they meant. Solo and mute choose what is averaged.)
- [x] Add manual labeling interface: The labels loaded with the toml stay immutable. But a user could still add manual labels. Bounding boxes on specs to constrain time and frequency and time ranges only on the trace. They could be saved along the existing labels or along the wav dataset as simple csv files with a col for time, a col for freq and a col for label or something along those lines. (`b` for label mode, then drag: on a spectrogram the box bounds time AND frequency, on a trace time alone. Categories are a preference in the settings file, `1`-`9` pick them, Ctrl+L edits them. Point categories are placed at the cross hair. Written to `<stem>-labels.csv` beside the recording -- atomically, which nothing else in this tree does -- and never over a sidecar this session could not read whole. The immutable annotations are untouched: separate store, separate file, separate box in the bar.)
- Add a setting to constrain default frequency axis on spec view. We only want to see 0-2kHz in 99% of time. 

# Future larger dev sessions

- [ ] check spec backend. torch could be faster. 
- [ ] Write plugin interface, so that wavetracker, eeltracker, etc. algos can be visualized interactively in audian
