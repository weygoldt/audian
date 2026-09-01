# audian internals

How the application is put together, and what is still missing.  Moved out
of the README, which is for somebody deciding whether to use audian; this is
for somebody about to change it.

`todo.md` at the repository root is the working list and is more current
than the checklist below.

## Development

I currently explore various possibilities for interactive analysis of
audio signals. In the end, audian should be easily extensible via
plugins that provide processing and analysis algorithms, and audian
handles all GUI aspects.

### Incomplete list of TODOs:

- [ ] Implement Model-View structure
  - [x] Handle all data (raw, filtered, spectrogram,...) in one class!
  - [x] Boil down the BufferedData code to a simple plugin interface.
  - [x] Add destination list to Buffered data.
  - [x] Smart updates of trace buffers.
  - [x] Move spectrum functions from Data to BufferedSpectrum.
  - [x] Move y-lim functions from TracItem and SpecItem to DataBrowser.
  - [x] Traces should be assignable to plots.
  - [x] TraceItems should get color and line width from data objects.
  - [x] Recompute derived data whenever a source is changed.
  - [x] Update plots of all items that have been recomputed.
  - [x] Recompute derived data only if visible or used by something visible.
  - [x] Automatically discover plugins.
  - [x] Analyzer plugins for analysing a select snippet of the data.
  - [ ] Test this plugin interface with
    - [ ] Subtract common mean
    - [x] Logarithmic and high-pass filtered envelope
    - [ ] Envelope from visible frequency range of spectrogram
    - [ ] Feature expansion (kernel filter)
- [ ] Improve zoom stack behavior!!!
- [ ] Add events and marker ranges to the Data class
  - [ ] Load events from csv files provide along with the raw data.
  - [x] Provide interface for event detectors
  - [ ] Provide interface for event filters?
- [ ] Implement a proper layout for showing the plot panels:
  - [x] Support additional plots from plugins
  - [ ] Proper y-labels for xt plots: channel not for single trace plot, otherwise plot name with unit.
  - [ ] add units to amplitude axes
  - [x] Amplitude ranges should consider ampl_min/ampl_max of all traces
  - [x] Add yt plot with independent y-axis key shortcut
  - [ ] Update cross-hair code to the new plot_ranges
  - [ ] Support optional grid layout
- [x] New plot widget showing power spectrum of visible range.
- [ ] Support horizontal power spectrum
- [ ] Cycle with Ctrl+P through no power plot, power spectrum to the right, power spectrum on top.
- [x] FullTracePlot:
  - [x] fix offset problem.
  - [x] indicate time under mouse cursor.
  - [x] compute full trace in the background.
  - [x] store and load full trace to user specific cache dir.
  - [x] background computation and file saving only if long enough.
  - [x] command line script for computing fulltrace plot
- [x] Add a toolbar widget for spectrum overlap
- [x] SpinBox for envelope cutoff frequency needs to show digits
  after decimal point.
- [ ] Interactive high- and low-pass filtering:
  - [ ] make the lines and the corrseponding toolbar widget a general property of the spectrogram plot
  - [ ] allow for flexible connections of these features to some bufferedata update function.
  - [ ] high- and low-pass filter lines must not cross! Update limits.
  - [ ] add a toolbar widget for setting filter order.
- [ ] Implement downsampling of spectrograms! Or make it even dependent on window size.
- [ ] Improve on the concept of current cursor:
  - [ ] play should not stop at visible range but keeps going and scrolls data.
  - [ ] make cursor moveable by mouse.
  - [ ] some key shortcuts for moving and handling cursor.
- [ ] Improve on marking cross hair, cues, regions, events:
  - [x] Cross hair should only be used for measuring! Just a single whitish color.
    Comments only in the table.
    Show points only fom active measurement.
    
  - Cues and regions have position data with labels. Same for all channels.
    - visualize them by infinite vertical lines/regions, both in plots and
      FullTracePlot (maybe in extra row?).
    - can be set from cursor position/marked region.
    - add key shortcuts to go to next/previous cue.
    - from cue table go to selected cue.
    - how does boris export them?
  - Events are channel specific points.
    - Plotted as dot at data amplitude.
    - Many events per label.
    - Result from some analysis.
    - But should be editable.
  - Event regions are channel specific:
    - Plotted as lines on top of data.
    - Result from some analysis.
    - But should be editable.
- [ ] Define plugin interfaces for analysis on full data, visible range,
  selected range.
- [ ] Have a dockable sidebar for showing metadata, cue tables etc.


### Structure

Eventually we want audian to be neatly separated into a data model,
widgets that display the data, and controllers.

#### Model

At the core of audian are time-series data that are loaded from a
file. In addition it supports varous derived time-series data, like
for example filtered data, computed envelopes, spectrograms,
etc. Audian can handle very large data sets, but holds only a small
part in memory (buffer).

- `class BufferedData`: Base class for computed data (`buffereddata.py`).
- `class BufferedFilter`: Filter source data on the fly (`bufferedfilter.py`).
- `class BufferedEnvelope`: Compute envelope on the fly (`bufferedenvelope.py`).
- `class BufferedSpectrogram`: Spectrogram of source data on the fly (`bufferedspectrogram.py`).

- `class Data`: Handles all the raw and derived data traces like filtered data, spectrogram data, etc (`data.py`).

- `labels.py`: The **editable labels** -- the store, and the
  `<stem>-editable-labels.csv` sidecar beside the recording. Pure data, no Qt.
  These are the ones a reader draws afterwards; the **fixed labels** are the
  session bundle read by `session.py` and drawn by `eventoverlay.py`, which
  audian never writes.
- `labeloverlay.py`: The Qt half of `labels.py`: one overlay per plot, the
  category editor and the label list.

Replaces `markerdata.py`, whose authoring path had been commented out for
long enough that its store could not delete a row and its per-category
scatter layers were frozen at file-open time. Markers embedded in a WAV are
still carried into a saved region; they are read off the file, which is
where they live.

#### View

All the data audian is dealing with are displayed in plots.

A few classes specializing some pyqtgraph features:

- `timeaxisitem.py`: Label time-axis of TimePlot.
- `yaxisitem.py`: Label y-axis of TimePlot.
- `selectviewbox.py`: Handles zooming and selection on all RangePlot.

Managing plots:

- `plotranges.py`: Manage ranges of plot axes.
- `panels.py`: Manage plot panels.

Basic plots for time-series data:

- `rangeplot.py`: Plot displaying any data with specified range type.
- `timeplot.py`: Plot displaying data as a function of time.
- `spectrogramplot.py`: Plot displaying spectrograms.

Basic plot items:

- `traceitem.py`: PlotDataItem for TimePlot.
- `specitem.py`: ImageItem for SpectrogramPlot.
- `fulltraceplot.py`: GraphicsLayoutWidget showing the full raw data traces.

#### Controller

- `audian.py`: Main GUI, handles DataBrowser widgets and key shortcuts.
  Its `ToolStrip` is the tool bar: it gives up the words on its buttons, then
  the space around its group rules, then whole groups into an overflow menu,
  so that the window's minimum width does not grow with the bar.
- `databrowser.py`: Each data file is displayed in a DataBrowser widget.
  Its `ParameterTabs` is the bottom bar: one tab per group of parameters and
  one group on screen, so the bar asks for the width of its widest page
  rather than the sum of all of them.
- `compresseddata.py`: Handle compressed and cached data for FullTracePlot.

#### Plugins

- `plugins.py`: Discover and manage plugins. Panel plugins appear as checkable
  entries in the Plugins menu and can be disabled either there or by closing
  their side-panel tab.
- `pluginapi.py`: What a plugin may import from audian. A plugin that stays
  inside this surface can be moved to a repository of its own without
  breaking; one that reaches into `databrowser` or `labels` directly is
  holding internals that move.
- `analyzer.py`: Base class for analyzer plugins.
- `statisticsanalyzer.py`: Compute basic descriptive statistics.

Plugins are found in three places, and all three bind the same way — a
module exposing callables named `audian_*panel`, `*analyzer` or `*traces`:

1. **Bundled**, by walking `src/audian_plugins/`. These ship with audian and
   need no installing.
2. **Installed**, through the `audian.plugins` entry point group. This is how
   a plugin that lives in its own repository announces itself.
3. **Local**, any `audian*.py` in the working directory — for trying
   something out on one recording without installing it.

#### Bundled plugins

- `audian_plugins/eventdetection/`: Few-shot normalized cross-correlation
  event detector for trace and spectrogram templates. `engine.py` is the
  arithmetic and imports no Qt; `panel.py` is the interface. Enable it at
  **Plugins > Event detection > Normalised cross-correlation**.

Taking a bundled plugin out into its own repository is meant to be
mechanical: move the package directory, give it a `pyproject.toml` declaring

``` toml
[project.entry-points."audian.plugins"]
eventdetection = "audian_plugins.eventdetection"
```

and delete it from here. No code changes, in either repository.
