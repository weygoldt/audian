# Recon: state-ownership

# State ownership audit — audian (pre-Qt6)

## 0. The single most important finding

There is **no domain model and no application-service layer**. There are two God objects, both `QWidget`s:

- `DataBrowser(QWidget)` — `src/audian/databrowser.py:979`, 8253 lines, **147 attributes assigned in `__init__`** (`databrowser.py:1151-1441`). It is simultaneously the recording model, the view-state store, the settings reader/writer, the label store owner, the audio engine driver, the layout engine, and the widget tree.
- `Audian(QMainWindow)` — `src/audian/audian.py:1480`, **49 attributes in `__init__`** (`audian.py:1493-1630`). It holds the cross-tab *linkage policy* and, through `self.acts`, the *canonical copy of about 30 pieces of view state as QAction checkboxes*.

Everything below is a consequence. The migration cannot be mechanical because **most of the state has no Python home at all** — it is read back out of Qt objects (`isVisible()`, `isChecked()`, `viewRange()`, `InfiniteLine.value()`, `AxisItem._starttime_mode`). PySide6/pyqtgraph will change those objects' identity and lifetime semantics, and there is no model to fall back on.

Two structural amplifiers:

**(a) The shared `acts` namespace.** `audian.py:1499-1502`:
```python
class acts:
    pass
self.acts = acts
```
This is a *class object used as a mutable namespace*, created once and passed **by reference into every `DataBrowser`** (`audian.py:4707`, `4752`; stored at `databrowser.py:1154`). Every tab therefore mutates the same QActions: `databrowser.py:1836-1884` (35 `setEnabled`/`setVisible` calls), `2162-2166`, `2319-2322`, `7489`. Opening a mono file in tab 2 disables the amplitude actions for tab 1's 16-channel file. `disable_unused_range_actions` (`databrowser.py:1851`) only ever disables — nothing re-enables on tab switch.

**(b) The shared `PlayAudio`.** One instance (`audian.py:1524`) passed to every browser (`audian.py:4706`, `databrowser.py:1242`), but each browser keeps its **own** `audio_time`, `audio_tmax`, `audio_timer`, `audio_markers` (`databrowser.py:1245-1250`). Two tabs each believe they own the playback cursor.

---

## 1. Loaded recording / current file

| | where |
|---|---|
| Owner (nominal) | `Data` — `data.py:187`, `file_path` at `data.py:210`, **reassigned** by the loader at `data.py:408` |
| Second owner | `Audian.file_paths` — `audian.py:1513`, rewritten `audian.py:4692-4695`, **destructively consumed** `audian.py:4737`, `4741-4743` |
| Third owner | `Audian.browsers` list `audian.py:1512` + `QTabWidget` tab order `audian.py:1561` — two orderings of the same set, and `tabs.setMovable(True)` (`audian.py:1566`) lets the user desynchronise them |
| Fourth | `DataBrowser.data.data.file_paths` (thunderfish `AudioLoader`) — the real list for split recordings |
| Fifth | `RecentFiles.entries` — `audian.py:559`, its own JSON in the **cache** dir (`audian.py:564`) |

`Audian.file_paths` is used as a work *queue*, not as state: `load_data` (`audian.py:4717`) pops files off it while constructing browsers in a `QTimer.singleShot(100, self.load_data)` loop (`audian.py:4802`). Load progress is encoded in "is `browser.data.data` still None" (`audian.py:4719`, `4739`, `4757`). There is no load state machine.

`Data` also carries presentation: `data.py:390-405` writes `self.data.panel = "trace"`, `panel_type`, `color = theme.trace_color("raw")`, `lw_thin`, `lw_thick`, `plot_items = [None]*channels` onto the buffer object. `BufferedData` (`buffereddata.py:29-67`) declares `panel`, `color`, `lw_thin`, `lw_thick`, `plot_items` as first-class fields. **The data layer imports `theme`** (`data.py`, `buffereddata.py:49`).

**Canonical owner:** domain model `Recording` (path list, rate, channels, frames, start_time, meta) + application service `RecordingService` owning open/close/queue. Strip `panel`/`color`/`lw_*`/`plot_items` out of `BufferedData` entirely.

---

## 2. Channel selection / active channel

**Five independent holders.**

1. `DataBrowser.show_channels`, `.selected_channels`, `.current_channel` — `databrowser.py:1177-1186`. **26 direct assignment sites**: `1618-1631`, `2786`, `2862`, `2887`, `2917`, `7321-7323`, `7352`, `7370`, `7377`, `7391`, `7401`, `7432`, `7462-7483`, `7512-7534`.
2. `DataBrowser.solo_channels`, `.muted_channels`, `.maximized_channel`, `.channel_order` — `databrowser.py:1188-1191` — a *second, independent* visibility model layered over `show_channels`, resolved only at read time in `selected_channels_in_order()` (`databrowser.py:2710`) / `visible_channels()` (`databrowser.py:2739`).
3. `acts.channels[c].isChecked()` — the **source of truth for the toggle gesture**: `toggle_channel` branches on it (`databrowser.py:7501`) and `set_channels` writes back to it (`databrowser.py:7489`). Shared across all tabs (§0a).
4. `FullTracePlot.channel` and `.show_channels` — `fulltraceplot.py:404-405`, mutated by `set_channel` (`fulltraceplot.py:916`) and `update_layout` (`fulltraceplot.py:922`), pushed one-way from `databrowser.py:2986` and `6846`. Never read back; can silently drift.
5. `ChannelRailRow` widgets — `databrowser.py:721`; and `channel_names` is **mutated by the widget**: `databrowser.py:857` `self.browser.channel_names[self.channel] = self.name.text()`.

Cross-tab: `Audian.toggle_channel`/`show_channel`/`select_channels`/`hide_deselected_channels` (`audian.py:3804-3852`) each **read three attributes off one browser and write them into another** — `b.set_channels(self.browser().show_channels, self.browser().selected_channels, self.browser().current_channel)` — four copies of the same four lines.

**Canonical owner:** presentation state `ChannelSelection` value object (`shown: frozenset`, `selected: frozenset`, `current: int`, `solo`, `muted`, `maximized`, `order`, `names`) on a per-recording view-model, with `visible()` as a pure derivation. Actions and rail rows become *subscribers*, never sources.

---

## 3. Viewport time range (t0/t1, zoom)

**Four holders, two independent write paths, three re-entrancy guards.**

1. `PlotRange.r0[c] / r1[c]` — `plotranges.py:44-45`, the nominal model. Written by `set_ranges` (`plotranges.py:225`), `set_limits` (`plotranges.py:213-222`), and ~20 `zoom_*`/`move`/`home`/`end`/`snap` methods bound by `functools.partial` in a **string-keyed dispatch loop** (`plotranges.py:667-692`) — no static call graph.
2. Each pyqtgraph `ViewBox.viewRange()` — the actual rendered range.
3. Each `NavigatorRegion` — `fulltraceplot.py:309`, its own `getRegion()`.
4. `Data`'s loaded buffer window, via `data.update_times(t0, t1)` (`data.py:437`) called from `databrowser.py:7027`, `7041`, `7592`.

The navigator **bypasses `PlotRanges` entirely**: `FullTracePlot.update_time_range` (`fulltraceplot.py:1116-1133`) calls `ax.setXRange(xmin, xmax)` straight on the trace plots, and the value only reaches the model on the round trip back through pyqtgraph's `sigRangeChanged` → `DataBrowser.update_ranges` (`databrowser.py:6963`). The reverse path is `fulltraceplot.py:509` → `update_region` (`fulltraceplot.py:1135`).

Loop-breaking is done by hand with three uncoordinated flags: `DataBrowser.setting` (`databrowser.py:1195`, guarded by the `updating()` contextmanager `databrowser.py:1459-1474`), `FullTracePlot.no_signal` (`fulltraceplot.py:399`), `FullTracePlot._syncing_margin` (`fulltraceplot.py:459`) — plus `same_range()` epsilon comparison as a fallback (`databrowser.py:6997`). The docstring at `databrowser.py:1466-1473` records that leaking `setting` "silently freezes scrolling and zooming for the rest of the session"; `databrowser.py:7100-7104` and `7256-7261` record two shipped bugs of exactly that kind.

Cross-tab link: `Audian.link_timezoom` / `link_timescroll` (`audian.py:1526-1527`) fan out in `dispatch_ranges` (`audian.py:3322-3338`).

**Canonical owner:** application service `Viewport` (single `TimeRange` per recording, plus limits). ViewBoxes and the navigator region become pure renderers driven by one signal; the navigator emits an *intent*, never `setXRange`.

---

## 4. Viewport y range / amplitude scaling

**The clearest two-owners-of-one-bit case in the codebase.**

- `PlotRange.user_locked` — per-axspec, `plotranges.py:42`; set by `_user_zoomed` from `SelectViewBox.sigUserZoomed` (`plotranges.py:91-93`, `selectviewbox.py:28`); cleared at `plotranges.py:546`, `551`, `574`; read at `plotranges.py:510`.
- `DataBrowser.y_locked` — one bool for the whole browser, `databrowser.py:1314`; set at `3064`, `6992`, `7083`; read at `3105`, `7033`.

These are set by *different* events for the *same* gesture: a hand zoom sets `user_locked` via the ViewBox signal **and** `y_locked` via `update_ranges` (`databrowser.py:6992`). The 30-line docstring at `databrowser.py:3078-3103` documents the shipped bug this caused ("`v` and the double click on a trace's y axis therefore did nothing at all"). A third component reads it directly: `spectrogramplot.py:568` `getattr(prange, "user_locked", False)`.

Also here: `DataBrowser.y_mode` (`databrowser.py:1313`, mutated `3063`) is read by the plot through the back-reference at `timeplot.py:286` `self.browser.y_mode != self.browser.y_fixed`, and mirrored again into `acts.y_modes` / `acts.y_mode_group` (`audian.py:2371`, `2382`) and `Audian.update_amplitude_button` (`audian.py:2572`). `PlotRange.rdefault` / `rdefault_min` (`plotranges.py:35`, `39`) is a *fourth* piece of y state (the opening band).

`Audian.link_ranges` (`audian.py:1528-1530`) is a per-axspec dict of link flags, toggled wholesale by `toggle_link_amplitude` / `toggle_link_power` / `toggle_link_frequency` (`audian.py:3339`, `3601`, `3448`).

**Canonical owner:** the `Viewport` service, with one `AxisRange {r0, r1, limits, opening_band, locked_by_user}` per axis. `y_locked` deleted; `y_mode` becomes presentation state on the view-model.

---

## 5. Spectrogram parameters

| parameter | owner | duplicates |
|---|---|---|
| `nfft`, `overlap_frac`, `hop` | `BufferedSpectrogram` — `bufferedspectrogram.py:76-78`, mutated in `update()` at `151`, `158`, and in `set_hop()` at `136-137` | `DataBrowser.nfftw` / `ofracw` / `ofraclabelw` widget values, written under `blockSignals` (`databrowser.py:7113-7137`) |
| dynamic range (`zmin`/`zmax`) | `pg.ColorBarItem.levels()` — `spectrogramplot.py:180`; **the colorbar widget is the store**. `_applying_levels` (`spectrogramplot.py:564`) is a fourth ad-hoc re-entrancy flag; `_levels_fitted` / `_refit_pending` (`192`, `457-458`, `488`, `528`, `620-621`) is a hand-rolled state machine per plot | `PlotRange` for `Panel.powers` (`plotranges.py:592` `set_powers`), and `Audian.link_ranges['p'/'q']` |
| colormap | `DataBrowser.color_map` (int index) — `databrowser.py:1417`, mutated `7162`, `7164` | persisted to **`QSettings("audian","audian")`** (`databrowser.py:7172`, read `1448`) — the *only* preference not in `settings.json`; plus `DataBrowser.cmapw` combo index; plus `theme._CACHE["cmap:…"]` (`theme.py:2622`) |
| opening band (`min_hz`/`max_hz`) | `PlotRange.rdefault` / `rdefault_min` (`plotranges.py:35`, `39`) | `DataBrowser._spec_band_saved` (`databrowser.py:1399`), `fminw`/`fmaxw` widgets, and `settings.json["spectrogram-band"]` (`databrowser.py:6594`, `6656`). `Audian.set_spectrogram_band` (`audian.py:3297`) explicitly bypasses the `link_ranges` fan-out with a hand loop |
| which trace is "the" spectrogram | `DataBrowser.spectrogram` / `.spectrogram_power` (`databrowser.py:1219-1220`) | `spec_acts[i].isChecked()` (`databrowser.py:1578-1582`), re-parented into `Audian.spectrogram_group` on every tab switch (`audian.py:4622-4630`) |

`Audian.dispatch_resolution` (`audian.py:3583`) is `pass` under a commented-out body with a `TODO: should set nfft and hop for all spectrograms!!!` — nfft/overlap **do not link across tabs at all**, silently, while every other spectrogram parameter does.

**Canonical owner:** domain `SpectrogramParams {nfft, overlap, window}` on the analysis pipeline; `SpectrogramDisplay {levels, colormap, band}` as presentation state. The `ColorBarItem` must stop being the level store.

---

## 6. Filter parameters

**Four holders of `highpass_cutoff` / `lowpass_cutoff`.**

1. `BufferedFilter.highpass_cutoff` / `.lowpass_cutoff` — `bufferedfilter.py:37-38`, reset in `open()` at `44-45`.
2. `SpectrogramPlot.highpass_cutoff` / `.lowpass_cutoff` — **`spectrogramplot.py:212-213`, seeded by reaching into `browser.data["filtered"]`**, then mutated independently by `highpass_changed`/`lowpass_changed` (`spectrogramplot.py:641-647`) from the draggable `InfiniteLine`, whose `.value()` is a *fifth* copy.
3. `DataBrowser.pending_highpass` / `.pending_lowpass` — `databrowser.py:1305-1306`, the debounce staging area (`update_filter`, `databrowser.py:7193-7194`).
4. `Audian.highpass_cutoff` / `.lowpass_cutoff` — `audian.py:1516-1517`, CLI values, applied once at `databrowser.py:1607-1610`.

Plus widget copies `hpfw`/`lpfw`/`hpsliderw`/`lpsliderw` (`databrowser.py:1393-1397`), written under `blockSignals` in `set_filter_widgets` (`databrowser.py:7241-7251`).

The write is a **reach-through mutation into the domain object**: `databrowser.py:7219-7221`
```python
filtered.highpass_cutoff = self.pending_highpass
filtered.lowpass_cutoff  = self.pending_lowpass
```
and again at `databrowser.py:1607-1610`. `BufferedFilter` has an `update()` (`bufferedfilter.py:59`) that recomputes `sos` from those fields — i.e. the invariant "sos matches the cutoffs" is maintained only by callers remembering to call `update()`.

Identically for the envelope: `DataBrowser.pending_envelope` (`databrowser.py:1310`) → `envelope.envelope_cutoff = ...` (`databrowser.py:7286`), plus `envfw`/`envsliderw`.

`DataBrowser.link_band` (`databrowser.py:1400`) is *yet another* filter-related flag, unrelated to `Audian.link_filter` (`audian.py:1531`).

**Canonical owner:** domain `FilterSpec {highpass, lowpass, order}` as an immutable value; `BufferedFilter` recomputes `sos` on assignment of a whole spec. Handles and spinboxes render it; neither stores it.

---

## 7. Selection / cursor position

Three unrelated things share the word "selection":

**(a) Crosshair / marker.** `PlotRange.marker_channel/_ax/_pos` and `stored_marker_*` (`plotranges.py:49-54`), mutated at `plotranges.py:618-651`. `DataBrowser.cross_hair` bool (`databrowser.py:1318`, set at `3252`). `RangePlot.xline` / `yline` / `stored_marker` (`rangeplot.py:55-79`) hold the rendered position. `Audian.set_cross_hair` (`audian.py:2950`) fans out to every browser plus `set_crosshair_readouts_visible`, and `acts.cross_hair` (`audian.py:3092`) is the checkbox of record. `DataBrowser.hover_panel` / `.hover_channel` (`databrowser.py:1379-1380`) are a separate pointer-tracking pair used by `axis_under_pointer` (`databrowser.py:3281`).

**(b) Region-drag mode.** `Audian.current_region_mode()` (`audian.py:2918-2930`) **derives the mode by polling six QActions' `isChecked()`** — the QActionGroup is the store. `DataBrowser.region_mode` (`databrowser.py:1214`, set at `7739`) is a cached copy. `DataBrowser.region_mode_override` (`databrowser.py:1216`) is a one-shot written **by the ViewBox reaching through the browser**: `selectviewbox.py:56` `browser.region_mode_override = mode`.

**(c) Selected label.** `DataBrowser.selected_label` + `.selected_overlay` (`databrowser.py:1329-1339`) *and* `LabelOverlay.editor` / `editor.label` (`labeloverlay.py:430`, `labeloverlay.py:533`). The docstring at `databrowser.py:3902-3905` names the hazard outright: "a selection whose grips were never built is the one way the two can disagree". `revalidate_selection` (`databrowser.py:3923`) is a 40-line reconciliation scan that has to consult `plot.isVisible()` *and* `visible_channels()` because `QGraphicsItem.isVisible()` lies inside a hidden widget (`databrowser.py:3952-3960`).

Also: pyqtgraph's `axHistory` zoom stack (`selectviewbox.py:206`, `init_zoom_history` at `224`) is **per-ViewBox** — with 16 channels × 2 panels there are 32 independent zoom histories, and `zoom_back`/`zoom_forward`/`zoom_home` (`databrowser.py:7715-7729`) walk all of them.

**Canonical owner:** presentation state — `CursorState {crosshair_on, hover, marker, stored_marker}`, `InteractionMode` (single enum, not a QActionGroup), `LabelSelection {label_id, surface}` held once.

---

## 8. Annotations & labels

Cleanest part of the codebase — and still doubled at the edges.

**Labels (editable, reader-authored):** `LabelSet` (`labels.py:291`) is a real store: `revision`, `dirty`, `blocked`, `_undo` (`labels.py:305-320`), atomic write (`labels.py:677`). Good. But its *satellites* live on `DataBrowser`: `current_category` (`databrowser.py:1326`), `label_overlays`, `selected_label`, `selected_overlay`, `category_acts`, `label_chips`, `label_save_pending` (`databrowser.py:1345`), `label_error` (`databrowser.py:1348`) — 15 attributes. The category vocabulary is duplicated three ways: `LabelSet._categories`, `settings.json["labels"]` (`databrowser.py:3483`, `3518`), and `category_acts` QActions bound to digit keys (`databrowser.py:3713`).

**Annotations (read-only, from a bundle):** `AnnotationLayer` (`eventoverlay.py:343`) owns `bundle`, `layers: dict[str,bool]`, `visible`, `revision`. But **solo is implemented twice**: `AnnotationLayer.solo()` (`eventoverlay.py:475`) and `DataBrowser.solo_annotation_layer()` (`databrowser.py:4886`), the latter keeping its own undo state `annotation_layers_before_solo` (`databrowser.py:1360`) and doing bulk edits under `layer.blockSignals(True)` while writing through `layer.set_layer` (`databrowser.py:4874-4877`). A third copy lives in the per-layer QActions (`audian.py:4146` `build_annotation_layer_actions`, resynced at `audian.py:4189`), a fourth in the chips (`databrowser.py:1355` `annotation_layer_chips`), a fifth in `settings.json["annotations"]` (`databrowser.py:5050`). `DataBrowser` also reaches in to mutate: `databrowser.py:4694` `self.annotations.recording_mismatch = coverage.subject()`.

The `EventOverlay`/`ControlPanel` render caches (`eventoverlay.py:979-981` `_keys`/`_drawn`/`_blank`, `controlpanel.py:350-351`) are a further derived-state tier invalidated by hand.

**Canonical owner:** `LabelSet` and `SessionBundle` are already the right domain objects — keep them, and move `current_category`, `label_overlays`, solo-undo, chips and category actions to a presentation `AnnotationViewState`. Delete `DataBrowser.solo_annotation_layer`'s parallel implementation.

---

## 9. Panel/lane layout and visibility

**Panel visibility has no model. It is read out of Qt.**

- `Panel.is_visible(channel)` → `return self.axs[channel].isVisible()` (`panels.py:118`).
- `Panel.set_visible()` writes into the items and returns "changed" by diffing them (`panels.py:120-126`).
- `Data.is_visible(name)` walks `plot_items` and asks each `pi.isVisible()` (`data.py:265-270`); `BufferedData.is_visible` does the same (`buffereddata.py:182-186`).
- **`BufferedData.set_need_update()` derives the recompute decision from widget visibility** (`buffereddata.py:193-199`) and propagates it down `self.dests`. The data pipeline is driven by `QGraphicsItem` visibility flags.
- `Panel.has_visible_traces` had to switch to `isVisibleTo(plot)` because `isVisible()` is ancestor-effective — the docstring (`panels.py:128-138`) records the resulting shipped bug: "stepping to the next channel hid the old lane's spectrogram and no lane ever drew one again".

Layered on top, on `DataBrowser`: `show_traces`, `show_specs`, `show_powers`, `show_cbars`, `show_fulldata`, `mean_spec`, `traces_before_mean`, `grids` (`databrowser.py:1222-1233`); `spec_scale` (`databrowser.py:1208`, mutated `6425`, `6526`, `6570`); `lane_left_width` (`1212`), `lane_height` (`1432`), `rail_visible` (`1408`), `scrollable_stack` (`1409`), `scroll_focus_pending` (`1435`), `stack_spacer_row` (`1437`). Mirrored again into `acts.toggle_traces` / `toggle_spectrograms` / `toggle_mean_spec` / `toggle_power` / `toggle_cbars` / `toggle_fulldata` and pushed back by `Audian.sync_toolbar` (`audian.py:2607-2622`).

Navigator mode/overview live **only** on the widget: `FullTracePlot.mode` / `.overview` (`fulltraceplot.py:401-402`); `DataBrowser` reads them with `getattr(self.datafig, "mode", MODE_SINGLE)` (`databrowser.py:3004`, `3011`), and `Audian.sync_toolbar` with a **doubly-defensive** `getattr(getattr(browser, "datafig", None), "mode", "single") == "all"` (`audian.py:2616-2617`).

`starttime_mode` is the same pattern: `Audian.starttime_mode` (`audian.py:1622`, mutated `3131-3133`) → pushed to axes via `PlotRanges.set_starttime` (`plotranges.py:167`) → and read **back out of a private Qt attribute**: `databrowser.py:2047` `getattr(plot.getAxis("bottom"), "_starttime_mode", None)`, consumed by `SharedTimeAxis.sync_starttime_mode` at paint time (`databrowser.py:960-972`). `databrowser.py:2033` also pokes `taxis.picture = None` (a pyqtgraph internal).

Cross-tab: `Audian.link_panels` (`audian.py:1533`) copies five attributes off `prev_browser` (`audian.py:4779-4786`).

**Canonical owner:** presentation state `LayoutState {trace_rows, spectrogram_on, power_on, colorbar_on, navigator_on, mean_mode, split_scale, grids}` per recording view-model, with panel visibility *derived* and pushed to Qt one-way. `BufferedData.need_update` must be driven by an explicit "is this trace subscribed" flag, never by `isVisible()`.

---

## 10. Playback position and state

- `PlayAudio` — one instance, `audian.py:1524`, shared by every browser (§0b).
- `DataBrowser.audio_time`, `.audio_tmax`, `.audio_timer` (`databrowser.py:1245-1249`) — per browser. `audio_time` is advanced by dead reckoning in a 50 ms timer: `databrowser.py:8047` `self.audio_time += 0.05 / self.audio_rate_fac`. There is no query of the audio device; the cursor is an open-loop estimate.
- **The rendered marker is also the state.** `mark_audio` (`databrowser.py:8046-8057`) reads `vmarker.value() >= 0` to decide which channels are playing, and `-1` is the sentinel for "not playing". `audio_markers` is a nested list of `InfiniteLine`s (`databrowser.py:1250`, populated `1655`, `1705`, `1734`).
- "Is playing" is derived from `self.audio_timer.isActive()` (`databrowser.py:7844`).
- Playback routing: `audio_source`, `audio_left`, `audio_right` (`databrowser.py:1183-1185`), `audio_rate_fac`, `audio_use_heterodyne`, `audio_heterodyne_freq` (`databrowser.py:1246-1248`), mirrored into `audiofacw`/`audiosrcw`/`audioleftw`/`audiorightw`/`audiopairw` widgets (`databrowser.py:1383-1387`) and into `acts.use_heterodyne` (`audian.py:3084`), fanned out by `Audian.dispatch_audio*` (`audian.py:4514-4530`).
- `scroll_step` + `scroll_timer` (`databrowser.py:1237-1239`) is a *second* independent time-advancing timer (auto-scroll, `databrowser.py:7853`), unsynchronised with the audio timer.

**Canonical owner:** application service `PlaybackService` — one per application (matching the single `PlayAudio`), owning `{recording, source_routing, position, span, rate_factor, heterodyne, state: enum}`. Markers become subscribers. Tab switching must be able to answer "who is playing".

---

## 11. Settings / preferences

**Three separate persistence mechanisms, in two different directories.**

1. `settings.json` in `audian_dirs.user_config_path` — `audian.py:909-942`. `save_setting` does read-modify-**rewrite-whole-file** every call (`audian.py:934-941`), which is why the code invents composite keys and version stamps (`databrowser.py:995-1043`: `ANNOTATION_SETTING`, `LABEL_SETTING`, `PARAM_TAB_SETTING`, `PANEL_SPLIT_SETTING`, `SPEC_BAND_SETTING`, each with its own `*_VERSION`). Writers: `audian.py:1676` (theme), `databrowser.py:3518`, `5050`, `5579`, `6656`, `6685`.
2. **`QSettings("audian","audian")`** for the spectrogram colormap only — `databrowser.py:1448`, `7172`. Different backend, different location, no version stamp.
3. `recent.json` in `audian_dirs.user_**cache**_path` — `audian.py:564`, `581`. Recent files are treated as cache; the docstring at `audian.py:911-914` argues config-vs-cache for settings but recent files went the other way.

Migration/validation policy is inconsistent by design and documented as such: version 1 spec-band is *migrated* (`databrowser.py:1005-1020`), version 2 panel-split is *dropped* (`databrowser.py:1058-1063`).

Debounce flags for writes are per-concern browser attributes: `label_save_pending` (`databrowser.py:1345`), `annotation_save_pending` (`databrowser.py:1352`), `_param_tab_saved` (`databrowser.py:1370`), `_spec_band_saved` (`databrowser.py:1399`).

Every `DataBrowser` reads and writes these independently — with N tabs open, N browsers race on the same `settings.json`. `Audian.set_spectrogram_band` (`audian.py:3297-3320`) is the only place that noticed, and fixes it by hand: `save=b is current`.

**Canonical owner:** one application service `Preferences` — a single in-memory dict, one debounced writer, one schema/migration table. `QSettings` and `recent.json` folded into it or explicitly justified.

---

## 12. Theme state

- **Process-global mutable state**: `theme._ACTIVE = {"name": THEME_DARK}` (`theme.py:414`), `theme.TOKENS` mutated *in place* (`theme.py:440-442`), `theme._CACHE` (`theme.py:417`) partially invalidated by prefix match (`theme.py:443-445`).
- `theme.py:3082-3093` **temporarily flips the global theme and restores it** in a `try/finally` to compute colours for the other theme — because "the dimming path deliberately reads global state rather than taking a theme argument". Not re-entrant; not thread-safe.
- Second copy: `acts.daylight_mode.isChecked()` (`audian.py:1675`, `4557`).
- Third copy: `settings.json["theme"]` (`audian.py:1676`, read `audian.py:5017`).
- Fourth tier: every `QPen`/`QBrush`/`QIcon` bakes its colour at construction. Hence `Audian._glyph_targets` (`audian.py:1506`), `_readout_state` (`audian.py:1508`), `_toolbar_separators` (`audian.py:1510`) — three registries whose only purpose is replaying a theme switch — plus `refresh_glyph_icons` (`audian.py:1702`), `restyle_chrome` (`audian.py:2241`), `repolish` (`audian.py:2205`), `DataBrowser.apply_theme` (`databrowser.py:2587`), and a per-item `apply_theme`/`polish` protocol across `fulltraceplot.py:610`, `eventoverlay.py:1012`, `selectviewbox.py:58`, `spectrogramplot.py:236`. `StartupPage` cannot be restyled at all and is **rebuilt from scratch** (`audian.py:1662-1672`).
- `traceitem.py:130` and `eventoverlay.py:681` call `theme.current_theme()` at draw time — the global is read on the paint path.

**Canonical owner:** a `Theme` instance injected as presentation state, not a module global; token lookup at paint time from that instance, so no registry-and-replay is needed and no `try/finally` global flip exists.

---

## 13. Reach-through chains — full inventory

There are **no 3-deep assignment chains** (`self.a.b.c = x`) anywhere. The reach-through is 2-deep and pervasive.

### Writes through another object's attributes

| site | chain | why it matters |
|---|---|---|
| `selectviewbox.py:56` | `browser.region_mode_override = mode` | a `pg.ViewBox` writes interaction state onto the browser it found via `getattr(self, "browser", None)`, itself injected at `rangeplot.py:46` `view.browser = browser` |
| `databrowser.py:857` | `self.browser.channel_names[self.channel] = self.name.text()` | rail widget mutates the browser's dict |
| `databrowser.py:7219`, `7221` | `filtered.highpass_cutoff = …` / `.lowpass_cutoff = …` | widget layer writes domain filter params |
| `databrowser.py:1607`, `1610` | same, at open | |
| `databrowser.py:7286` | `envelope.envelope_cutoff = self.pending_envelope` | |
| `databrowser.py:4694` | `self.annotations.recording_mismatch = coverage.subject()` | |
| `databrowser.py:1821` | `self.datafig.overlay_enabled = False` | |
| `databrowser.py:1998` | `self.taxis.mode_source = self.lane_starttime_mode` | injects a *bound method* as a pull-source |
| `databrowser.py:2033` | `taxis.picture = None` | pokes a pyqtgraph internal to force repaint |
| `databrowser.py:4445`, `4450` | `ax.annotations = overlay` | monkey-patches state onto plot items |
| `databrowser.py:2173`, `2187`, `2256`, `2260` | `self.nfftw.tooltip = …` etc. | invents attributes on `pg.SpinBox` |
| `data.py:390-405` | `self.data.panel = "trace"`, `.color = …`, `.plot_items = …` | domain object given presentation fields |
| `rangeplot.py:46` | `view.browser = browser` | back-reference injection, the root enabler of the whole pattern |
| `audian.py:2371`, `2382`, and ~120 more | `self.acts.<name> = QAction(...)` | the shared namespace (§0a) |

### Reads through another object's attributes (state pulled, not pushed)

- `audian.py:4779-4786`, `4791-4794` — `pb.show_traces`, `pb.show_specs`, `pb.show_powers`, `pb.show_cbars`, `pb.show_fulldata`, `pb.show_channels`, `pb.selected_channels`, `pb.current_channel` read off one browser to seed another.
- `audian.py:3804-3852` — the same three-attribute read repeated in four methods.
- `audian.py:2607-2622`, `2755-2759` — toolbar reads `browser.show_*`, `browser.plot_ranges["t"].r0[0]`, `browser.panels["trace"].axs[0].getAxis("bottom")`.
- `audian.py:3596` `cm = self.browser().color_map`; `audian.py:3613-3614` `self.browser().data["filtered"].highpass_cutoff`; `audian.py:3757-3758` `self.browser().data["envelope"].envelope_cutoff`.
- `audian.py:3789`, `3803` `lambda x: self.browser().envfw.stepUp()` — a menu action driving a *widget method* on another object to change a filter parameter.
- `spectrogramplot.py:211-213`, `231` — `browser.data["filtered"].highpass_cutoff`, `browser.show_specs`.
- `spectrogramplot.py:566` — `getattr(self.browser, "plot_ranges", None)`.
- `timeplot.py:58-72` — `browser.data.data.file_start_times()`, `.file_paths`, `browser.data.start_time`.
- `timeplot.py:286`, `290`, `294` — `self.browser.y_mode`, `self.browser.auto_ampl()`, `self.browser.apply_ranges(...)`.
- `analyzer.py:144-160`, `221`, `254`, `285`, `313` — plugin API is "reach through `self.browser`".
- `databrowser.py:2047` — `getattr(plot.getAxis("bottom"), "_starttime_mode", None)`: reads a **private** attribute of a third-party class.
- `audian.py:2616` — `getattr(getattr(browser, "datafig", None), "mode", "single")`.

### Defensive `hasattr`/`getattr` as an ownership smell

`databrowser.py:2986`, `3004`, `3011`, `3017`, `3034`, `3116`, `3236`, `7230`, `7276`, `7292`, `1815`, `1819`; `audian.py:2616`; `spectrogramplot.py:566`; `plotranges.py:83-85`; `selectviewbox.py:48-52`. Each one is a place where the caller does not know whether the collaborator has the state yet. That is a lifecycle problem masquerading as a compatibility shim.

---

## 14. Proposed canonical owners

**Domain model** (no Qt, no `theme` import, testable headless — `session.py`/`layers.py`/`alignment.py` already achieve this and are the template):
- `Recording` — paths, rate, channels, frames, start_time, joins, meta.
- `TraceGraph` — the buffered pipeline; `BufferedData` minus `panel`, `panel_type`, `color`, `lw_*`, `plot_items`, and minus visibility-derived `need_update`.
- `FilterSpec`, `EnvelopeSpec`, `SpectrogramParams` — immutable value objects; assignment recomputes derived state (`sos`, `hop`, `fresolution`).
- `LabelSet`, `SessionBundle` — keep as-is.

**Application services** (one per application, or one per open recording; own the mutations, emit change events):
- `RecordingService` — open/close/queue, replaces `Audian.file_paths`-as-queue and the `QTimer.singleShot(100)` load loop.
- `Viewport` — the single owner of every axis range and limit. Replaces `PlotRange.r0/r1` + ViewBox ranges + navigator regions + `y_locked`/`user_locked`. Every gesture becomes an intent into it; the three re-entrancy flags (`setting`, `no_signal`, `_syncing_margin`) disappear because there is one write path.
- `PlaybackService` — one, matching the one `PlayAudio`. Owns position, span, routing, transport state.
- `Preferences` — one dict, one debounced writer, one migration table; absorbs `QSettings` and `recent.json`.
- `AnalysisService` — `analyzers`, `analysis_table`, `save_path` (kill the shared `[None]` cell at `audian.py:1623` / `databrowser.py:8139`).

**Presentation state** (plain dataclasses on a per-recording view-model; widgets render, never store):
- `ChannelSelection` — shown/selected/current/solo/muted/maximized/order/names, `visible()` derived.
- `LayoutState` — panel toggles, `spec_scale`, lane geometry, navigator mode/overview, rail visibility.
- `InteractionMode` — one enum replacing the six-QAction poll at `audian.py:2918` and `region_mode_override`.
- `CursorState` — crosshair on/off, hover, marker, stored marker.
- `LabelViewState` / `AnnotationViewState` — current category, overlays, solo-undo, chips.
- `LinkPolicy` — the `link_*` flags, moved off `Audian` onto an explicit multi-document coordinator.
- `Theme` — an injected instance, replacing `theme._ACTIVE`/`TOKENS`/`_CACHE` globals and the three replay registries.

**The invariant to enforce during the port:** *no application state may be read back out of a Qt object.* Concretely, these must all become model reads — `Panel.is_visible` (`panels.py:118`), `Data.is_visible` (`data.py:265`), `BufferedData.is_visible`/`set_need_update` (`buffereddata.py:182`, `193`), `Audian.current_region_mode` (`audian.py:2918`), `DataBrowser.toggle_channel`'s `acts.channels[c].isChecked()` (`databrowser.py:7501`), `lane_starttime_mode` (`databrowser.py:2047`), `mark_audio`'s `vmarker.value() >= 0` (`databrowser.py:8050`), `ColorBarItem.levels()` as the dynamic-range store (`spectrogramplot.py:180`, `577`), and `navigator_overview`/`toggle_navigator_mode`'s `getattr(self.datafig, …)` (`databrowser.py:3004-3011`). Each of these is a latent Qt6 behaviour change and each already has a documented shipped bug attached to it in the source comments.
