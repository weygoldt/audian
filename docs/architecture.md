# audian internals

How the application is put together. The README is for somebody deciding
whether to use audian; this is for somebody about to change it. `todo.md` at
the repository root is the working list.

## Structure

### Model

At the core of audian are time-series data loaded from a file, and data derived
from them, like filtered traces and spectrograms. Audian can handle very large
data sets, but holds only a small part in memory (a buffer).

- `data.py`: `class Data` handles the raw and all derived traces.
- `buffereddata.py`: `class BufferedData`, the base class for computed data.
- `bufferedfilter.py`: `class BufferedFilter` filters source data on the fly,
  and removes clicks from it first when asked.
- `clicks.py`: Click removal, a numpy port of `wavetracker.clicks`, in blocks
  fixed in absolute time so that a frame comes out the same however its buffer
  was loaded. While it is on, the filter reads a block and a margin more of
  the raw buffer on each side, and `Data.update_margins` resizes the raw buffer
  for that.
- `bufferedspectrogram.py`: `class BufferedSpectrogram` computes spectrograms of
  source data on the fly.
- `compresseddata.py`: Compressed and cached data for the navigator.
- `activity.py`: Baseline-referenced activity metrics for the navigator.
- `denoise.py`: What a spectrogram denoiser is, and the registry plugins add
  them to.
- `tasks/`: Work that does not belong on the GUI thread: the derived-trace
  pipeline, who owns the worker threads, and cancellation.

Two kinds of label, kept apart:

- `labels.py`: The **editable labels**: the store, and the
  `<stem>-editable-labels.csv` sidecar beside the recording. Pure data, no Qt.
  These are the ones a reader draws afterwards, each with an optional outline.
- `labelassist.py`: The hook a plugin's label assistant fills, such as SAM: the
  browser stores a drawn label, then writes the assistant's answer onto it.
- `session.py`, `alignment.py`, `layers.py`: The **fixed labels**: a fakefish
  session bundle, the fit that places it in the recording and how far to trust
  it, and what the bundle offers the browser to switch on. Audian never writes
  these.

### View

All the data audian is dealing with are displayed in plots.

A few classes specializing some pyqtgraph features:

- `timeaxisitem.py`: Label time-axis of TimePlot.
- `yaxisitem.py`: Label y-axis of TimePlot.
- `selectviewbox.py`: Handles zooming and selection on all RangePlot.

Managing plots:

- `plotranges.py`: Manage ranges of plot axes.
- `panels.py`: Manage plot panels.
- `panelsplitter.py`: The draggable boundary between a channel's trace and its
  spectrogram.

Basic plots for time-series data:

- `rangeplot.py`: Plot displaying any data with specified range type.
- `timeplot.py`: Plot displaying data as a function of time.
- `spectrogramplot.py`: Plot displaying spectrograms.
- `fulltraceplot.py`: The navigator: a GraphicsLayoutWidget showing the full raw
  data traces.
- `controlpanel.py`: The control track: what the stimulator was set to over the
  whole session.

Basic plot items:

- `dataitem.py`: What a plot item owes the data layer: is it on screen?
- `traceitem.py`: PlotDataItem for TimePlot.
- `specitem.py`: ImageItem for SpectrogramPlot.
- `smoothing.py`: How a spectrogram image is smoothed on its way to the screen.

Drawing labels:

- `eventoverlay.py`: Draws the fixed labels over the traces and spectrograms.
- `labeloverlay.py`: The Qt half of `labels.py`: one overlay per plot, the
  category editor and the label list.
- `windowing.py`: Turns a whole session's annotation arrays into what one view
  shows.

Everything else on screen:

- `theme.py`: The design system, and the single source of truth for the look of
  the app. `python src/audian/theme.py` prints its contrast tables.
- `wraprow.py`: A row of chips that wraps onto another line instead of asking
  for width.

### Controller

- `audian.py`: Main GUI, handles DataBrowser widgets and key shortcuts.
  Its `ToolStrip` is the tool bar: it gives up the words on its buttons, then
  the space around its group rules, then whole groups into an overflow menu,
  so that the window's minimum width does not grow with the bar.
- `databrowser.py`: Each data file is displayed in a DataBrowser widget.
  Its `ParameterTabs` sits in the `SidePanel` on the right edge: one tab per
  group of parameters and one group on screen, so adding a page does not widen
  the window.

### Plugins

- `plugins.py`: Discover and manage plugins. Panel plugins appear as checkable
  entries in the Plugins menu and can be disabled either there or by closing
  their side-panel tab.
- `pluginapi.py`: What a plugin may import from audian. A plugin that stays
  inside this surface can be moved to a repository of its own without
  breaking; one that reaches into `databrowser` or `labels` directly is
  holding internals that move.
- `analyzer.py`: Base class for analyzer plugins.
- `statisticsanalyzer.py`: Compute basic descriptive statistics.

Plugins are found in three places, and all three bind the same way: a module
exposing callables named `audian_*panel`, `audian_*analyzer`, `audian_*traces`,
`audian_*denoisers` or `audian_*label_assistant` (`labelassist.py`: help with
drawing a label, which answers each drag with a better box and a shape):

1. **Bundled**, by walking `src/audian_plugins/`. These ship with audian and
   need no installing.
2. **Installed**, through the `audian.plugins` entry point group. This is how
   a plugin that lives in its own repository announces itself.
3. **Local**, any `audian*.py` in the working directory, for trying something
   out on one recording without installing it.

Bundled plugins:

- `audian_plugins/specdetector/`: Trains a detector on the recording from a few
  labels, at **Plugins > Spectrogram detector**: SAM as audian's label assistant
  (`assistant.py`), YOLOE to propose, YOLO to detect, all through Ultralytics in
  a child process. The tab has no label tool of its own; it reads audian's
  labels, and every labelled span category is a class. `tiling.py` turns
  samples into images and pixels back into seconds and hertz. `dataset.py`,
  `review.py`, `boxes.py` and `project.py` are the rest of the logic and import no
  Qt. `models.py` and `modelrunner.py` run out of process and are the only files
  that touch Ultralytics. The design and its measurements are in
  [`specdetector-design.md`](specdetector-design.md), and
  `tests/measure_specdetector.py` reproduces them.
- `audian_plugins/eodsorter/`: Runs wavetracker and corrects its tracks on the
  spectrogram, at **Plugins > Wavetracker**. The design is in
  [`eodsorter-design.md`](eodsorter-design.md).
- `audian_plugins/denoisers/`: The two spectrogram denoisers audian ships, mains
  hum and spatial coherence, as a plugin like any other.

Taking a bundled plugin out into its own repository is meant to be
mechanical: move the package directory, give it a `pyproject.toml` declaring

``` toml
[project.entry-points."audian.plugins"]
specdetector = "audian_plugins.specdetector"
```

and delete it from here. No code changes, in either repository.

## Changing it

Two harnesses complain about a new action or setting, both by design.

- `tests/test_actioninventory.py` freezes every action into
  `tests/data/action-inventory.json`, so a new key means regenerating it in the
  same commit, and the diff is the review:

  ``` sh
  AUDIAN_REGENERATE_GOLDEN=1 uv run pytest tests/test_actioninventory.py
  ```

  Its sweep also fires every action on a loaded recording and puts the theme
  and window state back afterwards. A new action that leaves state behind has
  to join them.
- The settings guard in `tests/conftest.py` fails the run if a test touches the
  reader's real config or cache directories.

**Do not bump `SPECTROGRAM_SETTING_VERSION` to add a key.**
`spectrogram_settings` drops the whole block on a version it does not recognise,
so a bump takes every reader's colormap, window and overlap away to add a
preference they have not set yet.

**Every combo box on the Spectrogram page goes through `narrow_combo`,** because
that page already sets the width of the whole side panel, and a control that
publishes a wide size hint widens the window's minimum with it.
