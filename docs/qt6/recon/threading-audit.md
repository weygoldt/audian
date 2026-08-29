# Recon: threading-audit

# Concurrency & responsiveness audit — audian @ `/home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration`

## 0. Executive finding

**The application has exactly one thread.** `grep -rn "threading\.|QThread|QRunnable|QThreadPool|concurrent"` over `src/`, `tests/`, `scripts/`, `*.py` returns **one hit, and it is a comment**: `src/audian/buffereddata.py:243` ("…which is also where a future QThreadPool implementation would emit from"). There is no worker thread anywhere.

Concurrency in the app is therefore only:
1. **8 forkserver child processes** (`compresseddata.py:342-367`), min/max pyramid for the navigator only.
2. **The PortAudio callback thread** owned by `audioio`, which audian never touches (it supplies no callback of its own).

Everything else — decoding, filtering, FFT, decibel, envelope, pyramid build, image upload, label/annotation CSV IO, cache write, region export, analyzer plugins — runs **on the GUI thread**, mostly inside `QTimer` slots or range-changed signal handlers.

There are **no cross-thread GUI touches** (correctness is safe by virtue of there being no threads), but there are two shutdown hazards and one lock-ordering hazard, detailed in §5.

---

## 1. Expensive work inventory

Costs marked *(measured)* are the authors' own in-tree measurements on a 16-channel 20 kHz file; I cite the line that records them.

### 1.1 File decoding / loading

| Site | What | Cost | Thread | Blocks GUI |
|---|---|---|---|---|
| `data.py:364` `open_files()` ← `databrowser.py:1558` `Data.open()` ← `audian.py:4722` `browser.open()` | `DataLoader.open_multiple()` over N files: header parse, timestamp reconciliation | 10s–100s ms per file | GUI | **Yes**, inside a `QTimer.singleShot(100, load_data)` slot (`audian.py:4715/4759/4803`) |
| `data.py:39` `file_frames()` → `soundfile.sf.info()` per path | header read per file, called *unconditionally* on every multi-file open to validate the loader | ms/file, but 1 open FD per file | GUI | Yes (small) |
| `data.py:143` `join_gaps()` → `get_datetime(source.metadata())` per file | metadata parse per file | ms/file | GUI | Yes (small) |
| `buffereddata.py:147` `load_buffer()` ← `BufferedArray.update_buffer` | disk read of a 64 MB-budgeted raw buffer (`data.py:202 buffer_bytes = 64 MiB`, ~1.5× peak per `data.py:198-201`) | 10s–100s ms | GUI | **Yes** — reached from `Data.update_times` (`data.py:439`) on every scroll/zoom, and *implicitly* from every `BufferedArray.__getitem__` (§1.7) |
| `compresseddata.py:492/540` `load_audio(ft_path)` in `load_data()` ← `fulltraceplot.py:675` `prepare()` ← `databrowser.py:1848` | reads the whole cached `-fulltrace.wav` overview + `np.load` of the `.stats.npy` sidecar, plus a JSON scan over up to 1000 cache entries with `unlink()` on stale ones (`compresseddata.py:528-561`) | 10–200 ms + directory churn | GUI | **Yes**, synchronously inside `DataBrowser.open()` |

### 1.2 Min/max pyramid construction — two separate implementations

**(a) Navigator overview, `CompressedData` — the only multiprocessing in the app.**

- `compresseddata.py:342-367`: `nprocs = min(8, cpu_count())` (`max_procs = 8`, line 213) `multiprocessing.Process` children running `down_sample_worker` (line 27). Each child opens its **own** `DataLoader` over the same files (line 54/56) and reduces 30 s blocks (`compression_layout`, line 240) into a `multiprocessing.Array` of `2*max_pixel × channels` float64.
- Cost: one full pass over the entire recording. Documented pool-memory blowup at line 206-208 ("a 32-core machine allocated ~4.6 GB"), capped by `max_pool_bytes = 256 MiB` (line 209).
- Thread: **child processes** — this is the one thing that is genuinely off the GUI thread.
- **But three of its paths are inline on the GUI thread:**
  - `compresseddata.py:297-324` — file fully in memory: 4 × `reduceat` over the *entire* file, GUI thread.
  - `compresseddata.py:325-330` → `compress_inline()` (line 261): files up to `inline_samples = 8_000_000` samples (line 217) are decoded and decimated **block by block, entirely on the GUI thread**, inside `DataBrowser.open()`.
  - `compresseddata.py:733` `cdata.save_data()` is called **from the `_timer` slot** in `fulltraceplot.py:733`: `write_audio()` of the whole overview + `np.save` of the stats + JSON rewrite + up to 1000-entry LRU eviction with `unlink()`. Blocking file IO in a Qt slot.

**(b) Live-buffer pyramid, `MinMaxPyramid` (`buffereddata.py:278-423`).**

- Built lazily in the paint path: `traceitem.py:275` `pyramid.build(...)` inside `TraceItem.peaks()` inside `update_plot()`.
- `_base_level` (line 346) reduces the whole current buffer at `base_step = 32`; measured 25.0–79.6 ms depending on channel count (`buffereddata.py:341-343`), plus geometric coarser levels.
- Rebuild is keyed on `(offset, nframes, buffer_generation)` (`valid_for`, line 314), so **the first `update_plot()` after every buffer move pays the full rebuild synchronously**, and the 15 other channels then hit a warm pyramid. Thread: GUI. Blocks: **yes**, ~25–80 ms per buffer move, on top of the decode.

### 1.3 Filtering

- `bufferedfilter.py:57` `sosfilt(self.sos, source, axis=0)` — *(measured 225.3 ms, was 258.5 ms per-channel; `bufferedfilter.py:55-56)*.
- `bufferedfilter.py:59` `update()` → `butter()` + `recompute_all()` → `reload_buffer()` → `process()` for the whole buffer, and recursively for all dependents (`buffereddata.py:210-214`).
- Trigger path: `databrowser.py:7211 filter_timer.start(200)` → `apply_filter` (7213) → `filtered.request_update(0)` (7229) → `buffereddata.py:252 _update_timer.start(0)` → `flush_update` (254) → `update()`.
- Thread: GUI. Blocks: **yes**. The chain cost is recorded at `buffereddata.py:238-241`: **258 ms sosfilt + 857 ms spectrogram + 424 ms decibel + ~350 ms setImage ≈ 1.9 s**, and again at `databrowser.py:7185-7186` ("refiltering plus respectrogramming 16 channels costs about 1.5 s").

### 1.4 Envelope

- `bufferedenvelope.py:56` `sosfiltfilt(self.sos, (π/2)*|source|, axis=0)` — `filtfilt` is two passes plus an `abs` temporary of the whole buffer; strictly more expensive than the filter above.
- Trigger: `databrowser.py:7270 envelope_timer.start(200)` → `apply_envelope` (7280) → `request_update(0)`.
- Thread: GUI. Blocks: **yes**.

### 1.5 FFT / spectrogram

- `bufferedspectrogram.py:107` `thunderlab.powerspectrum.spectrogram(...)` over the whole source buffer, all channels — *(measured 857 ms, `buffereddata.py:239)*.
- `bufferedspectrogram.py:164` `update_step()` + `recompute_all()` on every nfft / overlap change (`databrowser.py:7110 spectrogram.update(nfft, overlap_frac)`), which also **reallocates** the buffer to `(frames, channels, nfft//2+1)` float64 (`dtype = np.float64`, line 54).
- Thread: GUI. Blocks: **yes**. nfft is driven by a spin widget in the parameter bar with **no debounce** — `set_resolution` (`databrowser.py:7095`) runs the whole recompute synchronously per widget emission, unlike filter/envelope which at least have their 200 ms timers.

### 1.6 decibel / image upload / power curve

- `specitem.py:163` `self.setImage(decibel(block.T), autoLevels=False)` — *(measured 23.4 ms decibel + 22 ms setImage per channel, ≈775 ms for 16; `specitem.py:20-22)*. Mitigated by the `view_pad` crop + `_image_range` hysteresis (`specitem.py:150-160`), so the full cost is only paid when the view leaves the pad or the buffer refills.
- `spectrogramplot.py:393-399` `np.mean(block, axis=0)` + `decibel(power)` per panel per `update_plot()` — runs on **every** range change, no hysteresis.
- `bufferedspectrogram.py:235` `estimate_noiselevels()` — `decibel()` over the whole 3-D buffer, *(measured 424 ms)*; guarded by the one-shot `self.init` latch (line 229). `estimate_noiselevels_visible` (line 184) is the cropped variant.
- Thread: GUI. Blocks: yes.

### 1.7 The hidden reload: `BufferedArray.__getitem__` on the GUI thread

`audioio/bufferedarray.py:236` `__getitem__` → `update_buffer` (346) → `move_buffer` (383) → `load_buffer` → for a `BufferedData` subclass, `process()` — i.e. **an array slice can trigger a disk read plus a full re-filter or re-FFT**. `traceitem.py:198-205` and `spectrogramplot.py:408-411` both document this and defend against it. Sites that still do it:

| Site | Slice | Reached from |
|---|---|---|
| `timeplot.py:414-415` `np.min/np.max(item.data[i0:i1, item.channel])` | visible window, **no pyramid**, O(visible samples), per item per channel | `PlotRange.auto` (`plotranges.py:518`) ← `auto_fit` (722) ← `DataBrowser.auto_fit_y` (3074) ← **`set_times` (7034) — every scroll and zoom** |
| `databrowser.py:3050` `np.abs(trace[i0:i1:step, :])` in `update_levels()` | visible window, all channels, strided | **`set_times` (7032) — every scroll and zoom** |
| `spectrogramplot.py:427/428` `self.spec_data[i0:i1, channel, :]` in `visible_block()` | clamped to `len(spec_data)` (total frames), **not to the buffer** | `update_plot` (390) — every range change |
| `databrowser.py:8005/8009/8010/8014` `data[i0:i1, ch]` in `play_region` | arbitrary user region, can far exceed the buffer | context menu / spacebar |
| `databrowser.py:8235` `self.data.data[i0:i1, self.selected_channels]` in `save_region` | arbitrary user region | Save-region dialog |
| `data.py:294` `t[i0:i1, channel]` in `get_region()`, for **every** trace | arbitrary user region × every trace incl. the 3-D spectrogram | `analyze_region` (`databrowser.py:8064`) |

`_buffer_position` (`audioio/bufferedarray.py:441`) returns the current position when the request is already inside the buffer, so the two `set_times` sites are usually no-ops — but they are one off-by-a-margin away from a full decode+refilter cascade inside a scroll handler. The three user-region sites are genuinely unbounded.

### 1.8 numba

**No numba in production code.** The only `njit` (`traceitem.py:343`) is inside `if __name__ == "__main__":` (line 325), a standalone micro-benchmark that is never imported by the app. `numba` is nonetheless a declared runtime dependency (`pyproject.toml:11`).

→ **There is no first-call JIT latency today.** The measured table at `traceitem.py:386-421` shows numba losing to `reduceat_out` at every step size, which is why. **Action for Qt6: drop `numba` from `dependencies`** — it is dead weight (~40 MB, LLVM) and its presence invites someone to reintroduce a 300 ms–2 s first-call stall on the GUI thread.

### 1.9 Playback

- `databrowser.py:8032` `self.audio.play(playdata, rate/rate_fac, blocking=False)`.
- Before it, **on the GUI thread**: heterodyne multiply + `butter` + `sosfiltfilt` + decimation over the whole region (`databrowser.py:8019-8030`), and `fade()` (8031).
- Inside `audioio.PlayAudio.play` (`playaudio.py:317-360`), **on the GUI thread**: `data - np.mean(data, axis=0)`, `*= scale`, `np.floor(...).astype(int16)` — three full-size temporaries; then `_play_sounddevice` (814) which may call `_down_sample` (413) → `scipy.signal.decimate` and/or a **per-channel `np.interp`** resample of the whole region (441-446).
- `play()` line 348 calls `self.stop()` if a stream exists → `_stop_sounddevice` (779): rewrites the fade-out into `self.data` sample-by-sample **in a Python `for` loop** (792-794), then `sounddevice.sleep(int(2000*fadetime))` = **200 ms hard sleep**, then a `while self.stream.active: sounddevice.sleep(10)` spin — all **on the GUI thread**. Same path from `databrowser.py:7845 self.audio.stop()`.
- **Audio callback**: `playaudio.py:744 _callback_sounddevice` runs on the PortAudio thread. It touches only `self.data`/`self.index`/`self.run` — numpy, no Qt. **Audian installs no callback of its own, so no GUI object is touched from the audio thread.** ✅
- **However**: `_stop_sounddevice` mutates `self.data` (790-794) while the callback reads it (752-762) with **no lock**. That is a data race in the dependency, exercised every time the user presses play twice or hits `play_scroll` (`databrowser.py:7845`).

### 1.10 Playback cursor

`databrowser.py:8046 mark_audio()` advances `self.audio_time += 0.05 / self.audio_rate_fac` per 50 ms `audio_timer` tick and writes every `vmarker`. It **never reads the stream position** (`PlayAudio.index` / `stream.time` are both available). A coarse `QTimer` under load drifts; the cursor and the sound diverge with no bound. Not a thread bug, but a correctness bug that a proper audio-clock architecture in Qt6 should fix.

### 1.11 Plugin / analyzer execution

- `databrowser.py:8058 analyze_region()`: `setOverrideCursor(WaitCursor)` → `self.data.get_region(t0, t1, channel)` (§1.7: slices **every** trace including the 3-D spectrogram, arbitrary length, can force a buffer move + full re-FFT) → `for a in self.analyzers: a.analyze(...)` → `restoreOverrideCursor()`. **Fully synchronous, unbounded, arbitrary third-party code on the GUI thread**, with only a wait cursor as UI. No cancellation, no progress, no timeout.
- `plugins.py:38 load_plugins()`: `importlib.import_module` of every `audian*.py` in **cwd**, executed at `audian.py:5041` before `QApplication` exists. Blocking, and a supply-chain surface.
- `analyzer.py:99` analyzers register into `browser.analyzers` and hold a direct `self.browser` reference — plugin code can call any GUI method. Any future move of analyzers to a worker thread requires severing this.

### 1.12 Label / annotation file IO

| Site | What | Thread | Blocks |
|---|---|---|---|
| `labels.py:704-711` `write()` | temp file + `fp.flush()` + **`os.fsync()`** + `os.replace` | GUI | Yes — an `fsync` is 1–20 ms on SSD, worse on network FS. Scheduled by `databrowser.py:3590 QTimer.singleShot(0, self.save_labels)` (debounced via `label_save_pending`, 3587) |
| `labels.py:654-655` `read()` | `csv.DictReader` of the sidecar | GUI | Yes, in `DataBrowser.open` (`databrowser.py:1809`) |
| `session.py:480 SessionBundle.load()` | TOML + up to 5 polars `scan_csv().collect()` (`session.py:344`) + `_build_trials/_build_pulses/_build_detections/_build_events/_build_controls/_build_residuals` | GUI | **Yes** — from `init_annotations` (`databrowser.py:4606`) at the tail of `Audian.load_data` (`audian.py:4802`). Pulse/detection tables are the largest data in the bundle; there is no size bound |
| `databrowser.py:5031 QTimer.singleShot(0, save_annotation_settings)` | `QSettings` write | GUI | Small |

---

## 2. QTimer audit

### `databrowser.py`

| Line | Timer | Interval | Single-shot | Drives | Fires when hidden? | Coalesces? |
|---|---|---|---|---|---|---|
| 1238 | `scroll_timer` | **50 ms repeating** (`:7864`) | no | `scroll_further` → `set_times` → full decode/filter/FFT/pyramid/repaint chain | **YES.** Stopped only at `trange.at_end()` (`:7869`) or an explicit toggle (`:7842/7858`). Switching tabs or hiding the browser does **not** stop it — `Data.update_times` (`data.py:437`) still moves the buffer and re-filters even though `Panel.update_plots` (`panels.py:212`) skips invisible axes | No. Fixed-rate. If one step exceeds 50 ms (it does — `fulltraceplot.py:500-502` records ~111 ms per step on 16 channels) the timer **backs up**: Qt coalesces missed `timeout`s for a non-precise timer, so the scroll silently slows instead of dropping frames, and the event loop is saturated |
| 1243 | `audio_timer` | **50 ms repeating** (`:8035`) | no | `mark_audio` → moves every `vmarker` on every channel | **YES** — no visibility gate; stops only at `audio_time > audio_tmax` (`:8052`) | No. Also the source of the playback drift in §1.10 |
| 1297 | `resize_timer` | 100 ms (`:5825`, `:5838`) | **yes** | `apply_resize` → `adjust_layout` + `set_need_update` + `update_label_status` | Started from `resizeEvent`/`eventFilter`, so effectively no | **Yes** — restart-on-each-event is the correct debounce |
| 1300 | `filter_timer` | 200 ms (`:7211`) | **yes** | `apply_filter` → the ~1.5–1.9 s chain | n/a | **Yes** — the documented reason (`:7183-7186`) |
| 1305 | `envelope_timer` | 200 ms (`:7270`) | **yes** | `apply_envelope` | n/a | **Yes** |
| 1309 | `overview_timer` | **250 ms repeating** (`:1849`) | no | `report_overview_progress` → `compressed.is_busy()` (a `waitpid` per child) + `window.set_progress()` | **YES** — no visibility gate. Stops when `not busy` (`:3178`) or `compressed is None` (`:3165`) | n/a (polling). This is a **polling loop standing in for a signal**; the child processes have no way to report progress except the shared `Value` counter (`compresseddata.py:334`) |
| 3590 | `singleShot(0, save_labels)` | 0 | yes | atomic CSV write + fsync | n/a | Yes, via the `label_save_pending` flag (`:3587`) |
| 4361, 5507, 7932 | `singleShot(0, equalize_parameter_bar)` | 0 | yes | layout | n/a | **No** — three call sites, no pending flag; three gestures in one turn queue three passes |
| 5031 | `singleShot(0, save_annotation_settings)` | 0 | yes | QSettings | n/a | No flag |
| 6103 | `singleShot(0, show_focused_lane)` | 0 | yes | scroll-into-view | n/a | No flag |
| 6892 | `singleShot(0, align_time_axis)` | 0 | yes | axis alignment | n/a | `schedule_axis_alignment` (`:6873`) — check its flag; called from `:2685` and `:6842` |

### `fulltraceplot.py`

| Line | Timer | Interval | Single-shot | Drives | Fires when hidden? | Coalesces? |
|---|---|---|---|---|---|---|
| 442 | `_timer` | **exponential backoff 250 → 2000 ms** (`RETRY_MIN_MS`/`RETRY_MAX_MS`, `:382/385`; `_schedule_retry`, `:700-704`) | yes, self-rearming | `plot_data` → `_plot_data` (`:723`): `is_busy()`, non-blocking `lock.acquire(block=False)` (`:738`), `np.array` copy of the shared arrays under the lock, `_store` → `_compute_activity` (`:771`) → `_draw` (`:860`); on completion, **`cdata.save_data()`** (`:733`) — synchronous `write_audio` + `np.save` + JSON LRU eviction | **YES.** No visibility check anywhere in `plot_data`/`_draw`. `_draw` (`:869`) iterates `visible_channels()`, which is the *channel mode*, not widget visibility — with `show_fulldata` off the navigator is hidden and this still polls, copies, recomputes activity and repaints | Self-rearming with backoff, so it does not pile up; but it is **polling instead of being signalled** by the workers |
| 450 | `_align_timer` | **0 ms** (`_schedule_sync`, `:1020`) | yes | `_sync_left_margin` → `ci.layout.activate()` + `sceneBoundingRect` + `mapToGlobal` per axtrace, then a corrective `setWidth` | Driven by `sigResized` of *every* axtrace (`:455`), plus `resizeEvent` (`:1000`) and `showEvent` (`:1004`) | **Yes** — `start(0)` restarts, so N resize signals collapse to one measurement. Re-entrancy guarded by `_syncing_margin` (`:1050`) and bounded by `MAX_ALIGN_STEPS = 4` (`:1063`) |

### `spectrogramplot.py:195` `_refit_timer`

0 ms single-shot, started from `setZRange` (`:622`) when `_refit_pending`; drives `_refit_levels` → `fit_levels`. Parented to the plot (`:193-195`), so it cannot fire into a deleted C++ object. Coalesces correctly. **One per channel** — 16 timers on a 16-channel stack.

### `audian.py`

- `:1850 message_timer` — single-shot, clears the status message. Fine.
- `:1855 progress_timer` — single-shot `lambda: self.set_progress(None)`. **The lambda captures `self`**, keeping the window alive; harmless here but a pattern to remove under PySide6 where lambda-connected slots are a known lifetime footgun.
- `:4715/4759/4803 QTimer.singleShot(100, self.load_data)` — this is the **file-open loop**: `load_data` opens *one* browser fully synchronously, then re-arms itself at 100 ms for the next file. So opening 4 files = 4 synchronous full-open stalls separated by 100 ms of event loop. This is a hand-rolled cooperative scheduler and is the single clearest candidate for replacement by a real task queue.

### Non-timer coalescers (already present, keep them)

- `fulltraceplot.py:503-507` `pg.SignalProxy(region.sigRegionChanged, rateLimit=30, ...)` — documented at `:500-502` (one step ≈ 111 ms on 16 channels).
- `databrowser.py:1754-1765` two `SignalProxy(rateLimit=60)` per channel for `sigMouseMoved` / `sigMouseClicked` — **2 × nchannels proxies**, each with its own internal timer. 32 on a 16-channel file.

---

## 3. GUI objects touched from a non-GUI thread

**None found.** Verified by exhaustion:

- `down_sample_worker` (`compresseddata.py:27-105`) runs in a `forkserver`/`spawn` child (`audian.py:5037`, `compresseddata.py:565`). It imports only `numpy`, `audioio.AudioLoader`, `thunderlab.DataLoader`. It touches only `multiprocessing.Array`/`Value`. It never imports Qt and never returns a Python object. ✅
- The PortAudio callback (`audioio/playaudio.py:744`) is owned entirely by `audioio`; audian passes no callback. ✅

**Adjacent hazards that are not thread bugs but will become them under any threading change:**

1. **`compresseddata.py:82-101` — the worker holds `array.get_lock()` across the `reduceat` calls**, i.e. for the whole reduction of a 30 s block, not just the store. The GUI's `lock.acquire(block=False)` (`fulltraceplot.py:738`) is non-blocking so it silently skips a paint rather than stalling — correct today, but it means the navigator's fill-in rate is governed by lock luck, and any future *blocking* acquire here stalls the GUI for a full block reduction.
2. **`compresseddata.py:82-101` vs `:93-101` — two separate locks** (`array.get_lock()` then `stats_array.get_lock()`) taken sequentially per block. The GUI reads `datas` under lock 1 (`fulltraceplot.py:737-744`) but reads `stats_datas` in `_compute_activity` → `bin_stats` (`compresseddata.py:169`) **with no lock at all** (`fulltraceplot.py:789`). So the activity overview can be computed from a half-written moment array. It is only ever wrong transiently and gets overwritten on the next retry tick, but it is an unsynchronised read of memory another process is writing.
3. `_stop_sounddevice` mutating `self.data` while the audio callback reads it, unlocked (§1.9).

---

## 4. Shutdown

### Paths

- `Audian.quit` (`audian.py:4870`): `flush_labels()` → `removeTab` → `w.close()` (which is `DataBrowser.close`, `databrowser.py:3184`) → `datafig.close()` (`fulltraceplot.py:565`) → `compressed_data.close()` (`compresseddata.py:198`) → `terminate(); join(); close()` per child → `QApplication.quit()`.
  **This path is correct**: children are terminated and joined.
- `Audian.close(index)` (`audian.py:4849`) — same, per tab.
- **`app.exec_()` returning because the window manager closed the last window** (`audian.py:5033`): **`Audian.quit` is never called.** There is no `closeEvent` anywhere (`databrowser.py:3613` and `audian.py:4855` both state this explicitly). So:
  - `DataBrowser.close()` never runs → `CompressedData.close()` never runs → children are **not** terminated.
  - `multiprocessing.util._exit_function` then runs at interpreter exit and, since the children are **non-daemon** (`compresseddata.py:343` sets no `daemon=`), it takes the `for p in active_children(): p.join()` branch — **the process hangs at exit until the full-file compression finishes.** On a multi-gigabyte recording closed 5 s after opening, that is minutes of an invisible zombie window.
  - `flush_labels` also never runs → the queued `singleShot(0, save_labels)` dies with the event loop → **the last label of the session is lost.** `databrowser.py:3613-3617` documents exactly this hazard but only defends the two explicit paths.
  - `PlayAudio.close()` is only reached via `Audian.__del__` (`audian.py:1636-1638`), i.e. never reliably.
- `FullTracePlot.__del__` → `close()` (`fulltraceplot.py:562`) is the only backstop, and `__del__` ordering at interpreter shutdown is not guaranteed. It already has to catch `RuntimeError` for a dead C++ side (`:570`).

### Verdict

**The app does not close cleanly on the most common exit gesture.** Fix is one `closeEvent` on `Audian` that calls `quit()`'s body, plus `daemon=True` on the workers so a hard exit cannot hang.

### Startup side-note

`mp.set_start_method("forkserver")` at `audian.py:5037` runs **before** `QApplication` is created (`:5015` is inside `audian_cli`, called at `:5042` — so actually after `set_start_method` but before any `Process.start()`; correct order). But the forkserver's default preload is `['__main__']`, and the console-script `__main__` does `from audian.audian import run`, so **the forkserver process imports the entire PyQt5 + pyqtgraph stack** at first `Process.start()` — paid once, on the GUI thread, inside `DataBrowser.open()`. Set `mp.set_forkserver_preload([])` or move the worker to a Qt-free module and preload that.

---

## 5. Proposed Qt6 task architecture

### 5.1 Choice per workload

Two mechanisms, chosen by whether the work owns long-lived state.

**A. `QThreadPool` + `QRunnable` — stateless, cancellable, fire-and-forget compute.**
Everything derived from an immutable input snapshot.

| Workload | Current site | Runnable |
|---|---|---|
| Filter a buffer | `bufferedfilter.py:57` | `FilterJob(sos, source_view, generation, token)` |
| Envelope | `bufferedenvelope.py:56` | `EnvelopeJob(...)` |
| Spectrogram | `bufferedspectrogram.py:107` | `SpectrogramJob(source_view, rate, nfft, hop, token)` |
| decibel + image crop | `specitem.py:161-163` | `DecibelJob` → emits a ready-to-`setImage` array |
| Live min/max pyramid | `buffereddata.py:322 build()` | `PyramidJob(buffer_view, offset, generation)` |
| `estimate_noiselevels*` | `bufferedspectrogram.py:184/217` | fold into `SpectrogramJob`'s result |
| Analyzer plugins | `databrowser.py:8065` | `AnalyzeJob(region_snapshot, analyzer)` — see §5.5 |

Pool sizing: `setMaxThreadCount(max(2, cpu_count()//2))`. All of these release the GIL in `scipy`/`numpy`/`pocketfft`, so real parallelism.

**B. Worker-`QObject`-moved-to-`QThread` — owns a handle, must be serialised.**
One thread, one object, one long-lived resource.

| Workload | Why it cannot be a `QRunnable` |
|---|---|
| **`IoWorker`** — owns the `DataLoader`/`BufferedArray`. All `load_buffer`, `update_buffer`, `open_files`, `file_frames`, `join_gaps` | The loader is stateful (buffer position, open FDs, `AudioLoader.max_open_files`) and is **not** reentrant. Exactly one thread may ever touch it. This is the single most important change: it removes §1.7 entirely, because the GUI can no longer reach `__getitem__` |
| **`PersistenceWorker`** — `labels.write` (incl. `fsync`), `SessionBundle.load`, `CompressedData.save_data`, `save_region`/`write_data` | Serialised writes; a second write must never overtake a first. Low priority thread |
| **`PlaybackController`** — owns `PlayAudio`, does the heterodyne/`sosfiltfilt`/`fade`/`int16` conversion and the 200 ms `stop()` sleep | The 200 ms sleep and the spin loop must leave the GUI thread. Emits `positionChanged(float)` from the **stream clock**, not a `QTimer` — this replaces `audio_timer` and fixes §1.10 |

**C. Keep `multiprocessing` for `CompressedData`**, but make it a first-class citizen:
- Workers become `daemon=True`.
- Replace the `overview_timer` (`databrowser.py:1309`) *and* `FullTracePlot._timer` (`fulltraceplot.py:442`) polling pair with a **`QSocketNotifier` on a `multiprocessing.Pipe` read end** (or a small `IoWorker`-thread blocking-recv → `Signal`). Progress and completion then arrive as signals; the two timers and the exponential backoff disappear.
- Move the `compress_inline` / fully-in-memory branches (`compresseddata.py:297-330`) off the GUI thread into a `QRunnable` — they are the ones that block `DataBrowser.open()` today.
- Hold the shared-array lock only around the `out=` stores, not around the reduction (`compresseddata.py:82-101`), and put `datas` and `stats_datas` under **one** lock so `bin_stats` cannot read a torn pair.

### 5.2 The dependency graph is the scheduler

`data → filtered → {envelope, spectrogram → specitem}` is already an explicit DAG (`BufferedData.source`/`.dests`, `buffereddata.py:52-55`; ordered by `Data.setup_traces`, `data.py:300`). Make it the pipeline:

```
ViewRequest(t0, t1, channels, params, epoch)
   │
   ▼ IoWorker (QThread)          raw buffer  ──► RawReady(gen, buf)
   ▼ QThreadPool                 FilterJob   ──► FilteredReady(gen, buf)
   ▼ QThreadPool (fan-out)       EnvelopeJob ──► EnvelopeReady
                                 SpectrogramJob ──► SpecReady
   ▼ QThreadPool                 PyramidJob / DecibelJob
   ▼ GUI thread                  setData / setImage only
```

The GUI thread does **nothing but `setData`/`setImage`/`setPen`**. `Panel.update_plots` (`panels.py:210`) becomes "apply the newest completed result", never "compute".

### 5.3 Cancellation

Cooperative, two levels:

1. **Epoch token.** One monotonic `int` per browser, bumped by every user gesture that invalidates in-flight work (`set_times`, `apply_filter`, `apply_envelope`, `set_resolution`, channel/panel changes). Every job carries the epoch it was created under and its result is dropped on arrival if `result.epoch != current_epoch`. This is the *only* mechanism needed for correctness.
2. **`QRunnable` cancel flag + `QThreadPool.tryTake()`.** A shared `threading.Event` per epoch; jobs poll it at chunk boundaries. `sosfilt`/`spectrogram` over a 60 s buffer is a single non-interruptible C call, so **chunk them**: process the buffer in ~1 s slices and check the flag between slices. This turns a 900 ms uninterruptible FFT into a ≤50 ms cancellation latency and is a prerequisite for the whole design feeling responsive. `tryTake()` removes not-yet-started runnables from the queue outright.

`buffereddata.py:231 request_update()` is already the right seam — its docstring at `:242-244` says so. Keep the signature; replace the `QTimer` body with `epoch += 1; pool.start(job)` and keep `sigUpdated` as the completion signal.

### 5.4 Stale-result handling

Three keys, all already present in the code, just not used as a contract:

- **`buffer_generation`** (`buffereddata.py:61`, incremented at `:153`; installed on the raw loader by `data.py:169 count_buffer_loads`). A result computed from generation *g* is dropped if `trace.buffer_generation != g`. `MinMaxPyramid.valid_for` (`buffereddata.py:314`) already does exactly this — generalise it.
- **`epoch`** — §5.3.
- **`(offset, nframes, stride)`** — `SpecItem._image_range` (`specitem.py:46, 150-160`) is already a correct containment/detail check. Keep it; it means a stale-but-sufficient upload is *reused*, not recomputed.

Rule: **results are idempotent and order-independent.** A late `FilteredReady` for a superseded epoch is discarded silently; it never mutates `trace.buffer`. This requires the jobs to write into **their own output array**, not into `self.buffer` in place — the current `process(source, dest, nbefore)` signature (`buffereddata.py:175`) is already out-of-place, so this is nearly free. The one thing that must change: `BufferedData.buffer` becomes GUI-thread-owned and is **swapped**, not filled, on result arrival.

### 5.5 Plugins / analyzers

`analyzer.py:99` gives every analyzer `self.browser`. Split it:

- `Analyzer.analyze(t0, t1, channel, traces) -> AnalysisResult` runs in the pool on an **immutable snapshot** (`Data.get_region`, `data.py:282`, executed by `IoWorker` and handed over as plain arrays).
- A separate `Analyzer.present(result, ui)` runs on the GUI thread and is the only place allowed to touch plots or tables.
- Declare `Analyzer.thread_safe: bool = False` so legacy plugins keep running inline (with progress + cancel) while new ones opt into the pool. Without this the plugin API breaks.
- `plugins.py:39-41` importing from `Path.cwd()` should also become explicit/opt-in during the migration.

### 5.6 Timer disposition under the new architecture

| Timer | Fate |
|---|---|
| `databrowser.py:1238 scroll_timer` | Keep as a 50 ms **request emitter**, but make it emit a `ViewRequest` and **skip emission while a request is in flight** (or drive it off `RawReady` instead of the clock). Add `if not self.isVisible(): return` — an auto-scrolling background tab must not decode |
| `databrowser.py:1243 audio_timer` | **Delete.** Replaced by `PlaybackController.positionChanged` off the stream clock |
| `databrowser.py:1297 resize_timer` | Keep (correct debounce) |
| `databrowser.py:1300/1305 filter_/envelope_timer` | Keep as input debounce; the slot now enqueues instead of computing. Add an equivalent for `set_resolution` (`:7095`), which currently has none |
| `databrowser.py:1309 overview_timer` | **Delete.** Replaced by pipe + `QSocketNotifier` |
| `fulltraceplot.py:442 _timer` | **Delete.** Same |
| `fulltraceplot.py:450 _align_timer` | Keep — pure layout, correct as is |
| `spectrogramplot.py:195 _refit_timer` | Keep; consider hoisting the level decision to one browser-level object so 16 channels do not own 16 timers |
| `audian.py:4715/4759/4803 singleShot(100, load_data)` | **Delete.** Replaced by a queue of `OpenFileJob`s on `IoWorker`, with a real progress bar |
| `databrowser.py:4361/5507/7932` `singleShot(0, equalize_parameter_bar)` | Add a pending flag like `label_save_pending` |

### 5.7 Ordered work plan

1. `closeEvent` on `Audian` → `quit()` body; `daemon=True` on the compression workers. *(Fixes the exit hang and the lost last label. Independent of everything else.)*
2. Drop `numba` from `pyproject.toml:11`; delete `traceitem.py:325-462` or move it to `scripts/`.
3. Introduce `epoch` + `buffer_generation` as the stale-result contract, still single-threaded. *(No behaviour change; makes step 4 mechanical.)*
4. `IoWorker` on a `QThread`. Make `BufferedArray.__getitem__` unreachable from the GUI thread — assert on `QThread.currentThread()` in a debug build. This alone removes §1.7 and the `analyze_region`/`play_region`/`save_region` stalls.
5. `QThreadPool` for `FilterJob` / `EnvelopeJob` / `SpectrogramJob` / `DecibelJob` / `PyramidJob`, chunked for cancellation.
6. `PlaybackController` thread; delete `audio_timer`.
7. Pipe + `QSocketNotifier` for `CompressedData`; delete `overview_timer` and `FullTracePlot._timer`; move `compress_inline` to the pool.
8. `PersistenceWorker` for `labels.write`/`SessionBundle.load`/`save_data`/`save_region`.
9. Analyzer snapshot/present split with the `thread_safe` opt-in.
