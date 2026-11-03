# Changelog

## Unreleased: this fork

Everything since forking [bendalab/audian](https://github.com/bendalab/audian)
at v2.5. Install it from source; `pip install audian` gets upstream.

### Added

- **Wavetracker plugin:** run wavetracker from inside audian and correct its
  tracks on the spectrogram, with undo history, autosave and harmonic flags.
- **Spectrogram detector plugin:** label a few events with audian's own label
  tool, review what a model proposes, train a YOLO detector on the recording in
  about a minute and run it over the whole file. Every labelled span category is
  a class. It replaces the template matcher that shipped under **Plugins > Event
  detection**, which matched on the waveform as well; this one works on the
  spectrogram only.
- **SAM in the label tool:** with the detector plugin installed, a **SAM**
  toggle in the Labels tab tightens each box you drag and outlines the event in
  it. Labels carry the outline in a new last column of the sidecar.
- **Label assistants:** `audian_*label_assistant` plugins can answer a drawn
  label with a better box and a shape (`audian.labelassist`).
- **Denoiser plugins** for the spectrogram: mains hum and spatial coherence.
- **Plugin API:** `audian.pluginapi`, plugins bundled in `audian_plugins`, and
  an `audian.plugins` entry point group for installed ones.
- **Labels:** editable labels drawn with the mouse into a CSV sidecar, beside
  fixed labels that audian never writes (<kbd>B</kbd>, <kbd>F8</kbd>,
  <kbd>F9</kbd>).
- **Session bundles** drawn over the recording (`-a`/`--events`).
- **Daylight theme** for reading a screen in the sun (`--theme light`,
  <kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>L</kbd>).
- **Side panel** for parameters, which plugins can add tabs to
  (<kbd>Ctrl</kbd>+<kbd>B</kbd>).
- **Navigator activity overview** (<kbd>Alt</kbd>+<kbd>F6</kbd>), which tells
  sustained signals from transients.
- **Mean spectrogram** (<kbd>Shift</kbd>+<kbd>F2</kbd>) and **overlay traces**
  (<kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>F2</kbd>) for reading an array in one
  lane.
- **Spectrogram controls:** smoothing, peaking (<kbd>X</kbd>), and sliders for
  the colour scale and overlap.
- **Command palette** (<kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>P</kbd>),
  shortcut list (<kbd>Ctrl</kbd>+<kbd>K</kbd>) and cheat sheet (<kbd>?</kbd>).

### Changed

- PySide6 and Qt6 instead of PyQt5.
- Derived traces are computed off the GUI thread.
- The playback speed is a multiplier: 2 plays twice as fast and an octave up.
- Python 3.11 to 3.14, with dependencies locked by uv.
- The UI font, Inter, ships with audian.

### Removed

- The envelope trace controls, which no recording could reach since upstream
  took the envelope out of the default plugins.
- The analyzer event API (`make_trace_events` and friends), which nothing used.
- `songdetector.py`, `runaudian.py` and upstream's documentation site.


## v2.5

- Time is indicated in all formats when hovering over time axis or
  full trace plots
- Added screenshot function
- Dropping screenshot file onto audian moves displayed window to
  position of screenshot
- Fixed power spectra for time windows smaller than time resolution
- Fixed setting cross hair when zoomed in too much
- Fixed offset problem in full trace plot


## v2.4 - 2025.07.25

- Adapted to new TableData interface


## v2.3

- Improved time-axis label
- Generate full trace plot in background processes


## v2.2 - 2025.02.16

- Support of opening multiple files in a single buffer
- Fixed clearing of analysis data
- Handle selected regions that start at negative times or end beyond the data
- Properly close file


## v2.1 - 2024.07.04

- Centered zooming of time axis with T and Shift+T
- No symbols on trace plots when zooming out
- Fixed save region error
- Wildcard expansion for windows


## v2.0

First release with flexible management of data traces and panels.
