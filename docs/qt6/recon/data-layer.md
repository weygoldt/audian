# Recon: data-layer

- **cluster**: data-layer
- **purpose**: The buffering + DSP core: a sliding-window pipeline over one recording. `Data` owns a thunderlab `DataLoader` (raw float64 window buffer) and an ordered DAG of derived `BufferedData` traces (filter -> envelope, filter -> spectrogram), each a subclass of audioio's `BufferedArray` that recomputes its own window from its source's window whenever the view moves. `MinMaxPyramid` gives O(pixels) peak decimation over the live buffer for waveform drawing; `CompressedData` builds a whole-file min/max + first/second-moment overview for the navigator, in a multiprocessing pool over shared memory, with a WAV+npy disk cache. Every recompute in the live path is synchronous on the GUI thread; the only async in the cluster is one debounce QTimer and the compressor's process pool, which is polled rather than signalled.
- **public_surface**:
  - **name**: Data
  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/data.py
  - **kind**: class
  - **base**: object
  - **summary**: Owns the DataLoader plus the ordered trace list/`sources` parent-index list. dict-like by trace name (`__getitem__`/`__contains__`/`keys`). Imported by databrowser.py:1159. Key methods: open(unwrap, unwrap_clip), close(), setup_traces(), add_trace/remove_trace/clear_traces, get_region(t0,t1,channel), is_visible/set_visible(name,...), set_need_update(), update_times(t0,t1)->basename, scale_buffer_time(rate,channels).

  - **name**: open_files
  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/data.py
  - **kind**: function
  - **base**: 
  - **summary**: data.py:45. Opens one or many files as one DataLoader, defeating thunderlab's timestamp-continuity `break` by presetting `loader._max_time_diff = 365*24*3600` on an empty loader before open_multiple(); raises ValueError if the loader returns fewer frames than sf.info reports. Covered by tests/test_dataloader.py.

  - **name**: join_gaps
  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/data.py
  - **kind**: function
  - **base**: 
  - **summary**: data.py:114. Generator of (index, path, expected_s, actual_s) for joins whose metadata timestamps look discontinuous by more than MAX_JOIN_GAP_S; accepts both open-time and close-time recorder conventions.

  - **name**: file_frames
  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/data.py
  - **kind**: function
  - **base**: 
  - **summary**: data.py:26. Header-only frame count per path via soundfile.info; None per file it cannot read.

  - **name**: count_buffer_loads
  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/data.py
  - **kind**: function
  - **base**: 
  - **summary**: data.py:169. Monkeypatches `loader.load_buffer` with a closure that bumps `loader.buffer_generation`, the shared invalidation token the MinMaxPyramid keys on.

  - **name**: MAX_JOIN_GAP_S
  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/data.py
  - **kind**: constant
  - **base**: 
  - **summary**: data.py:24. 1.0 s reporting threshold for join discontinuity; never a reason to drop audio.

  - **name**: BufferedData
  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/buffereddata.py
  - **kind**: class
  - **base**: audioio.BufferedArray
  - **summary**: buffereddata.py:24. Base for every derived trace. dtype=float32. Carries pipeline state (source, dests, need_update, source_tbefore/tafter), presentation state (name, panel, panel_type, color, lw_thin, lw_thick, plot_items), and the debounce machinery. Subclass contract: implement `process(source, dest, nbefore)` and optionally `update(**params)`. Public: open(source, step, more_shape), expand_times, update_step, allocate_buffer, align_buffer, load_buffer, recompute, recompute_all, is_visible/set_visible, set_need_update, request_update(delay_ms, **params), flush_update, update().

  - **name**: _Notifier
  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/buffereddata.py
  - **kind**: class
  - **base**: PyQt5.QtCore.QObject
  - **summary**: buffereddata.py:13. Sole Qt object in the cluster. Exists only because BufferedData cannot inherit QObject alongside BufferedArray under sip. Carries `sigUpdated = Signal(object)`, re-exported as `BufferedData.sigUpdated`.

  - **name**: MinMaxPyramid
  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/buffereddata.py
  - **kind**: class
  - **base**: 
  - **summary**: buffereddata.py:278. Channel-major interleaved min/max mip pyramid over a (frames, channels) buffer, base_step=32, levels doubling. build(buffer, offset, generation) is idempotent and cheap; decimate(channel, start, stop, step) -> (interleaved values, first_frame) or None. Consumed by traceitem.py:273-282; tested by tests/test_minmaxpyramid.py.

  - **name**: BufferedSpectrogram
  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/bufferedspectrogram.py
  - **kind**: class
  - **base**: BufferedData
  - **summary**: bufferedspectrogram.py:52. dtype float64, 3-D buffer (time, channel, freq). Public: nfft, hop, overlap_frac, frequencies, fresolution, tresolution, spec_rect, use_spec, init; open(source), process(), set_hop(), update(nfft, overlap_frac), visible_slice(t0,t1), estimate_noiselevels(channel), estimate_noiselevels_visible(channel,t0,t1). Consumed by specitem.py and spectrogramplot.py.

  - **name**: channel_power
  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/bufferedspectrogram.py
  - **kind**: function
  - **base**: 
  - **summary**: bufferedspectrogram.py:10. Reduces a (time, channel, freq) block to (time, freq): index for a scalar channel, mean of the POWER for a sequence. Fast path when the sequence is every channel in order. Imported by specitem.py:9 and spectrogramplot.py:15; tested by tests/test_meanspectrogram.py.

  - **name**: NOISE_FLOOR_MARGIN_DB
  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/bufferedspectrogram.py
  - **kind**: constant
  - **base**: 
  - **summary**: bufferedspectrogram.py:40. 3.0 dB headroom above the broadband median that clamps the colour-ramp floor.

  - **name**: BufferedFilter
  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/bufferedfilter.py
  - **kind**: class
  - **base**: BufferedData
  - **summary**: bufferedfilter.py:9. Butterworth SOS high/low/bandpass applied with sosfilt(axis=0) over all channels at once. Public: highpass_cutoff, lowpass_cutoff, filter_order, sos, warmup_time=0.5. `sos is None` means pass-through. Installed by plugins.py:12.

  - **name**: BufferedEnvelope
  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/bufferedenvelope.py
  - **kind**: class
  - **base**: BufferedData
  - **summary**: bufferedenvelope.py:10. sosfiltfilt over (pi/2)*|x|, lowpass at envelope_cutoff or bandpass with highpass_cutoff; clamps negatives to 0 when highpass_cutoff==0. Public: envelope_cutoff, highpass_cutoff, filter_order, sos.

  - **name**: CompressedData
  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/compresseddata.py
  - **kind**: class
  - **base**: 
  - **summary**: compresseddata.py:108. Whole-file min/max overview + per-bin sum/sum-of-squares. Public: times, datas, stats_datas, short_data, procs; start(max_pixel, load_kwargs, do_short), wait(), is_busy(), progress(), close(), get_lock(), compression_layout(max_pixel), pool_size(step, nblock), compress_inline(step,n), save_data()/save_data_local()/load_data(min_rows), bin_stats(step)->activity.BinStats|None, stats_path/save_stats/load_stats. Consumed by fulltraceplot.py:440,675-676,729-747,789 and databrowser.py:3167-3181; tested by tests/test_compresseddata.py.

  - **name**: down_sample_worker
  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/compresseddata.py
  - **kind**: function
  - **base**: 
  - **summary**: compresseddata.py:27. Process target. Opens its own DataLoader, strides blocks by proc_idx, reduceats min/max into the shared Array and sum/sum-sq into a second shared Array, bumps a shared Value progress counter.

  - **name**: main / run
  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/compresseddata.py
  - **kind**: function
  - **base**: 
  - **summary**: compresseddata.py:564/638. The `audian-compress` console entry point (pyproject [project.scripts]). Sets the mp start method, parses -i/-u/-U, compresses to 6000 bins and writes `<stem>-fulltrace.wav` + `.stats.npy` beside the recording.

- **qt5_api_usage**:
  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/buffereddata.py
  - **line**: 8
  - **api**: from PyQt5.QtCore import QObject, QTimer, pyqtSignal as Signal
  - **qt6_replacement**: from PySide6.QtCore import QObject, QTimer, Signal. Note this is the ONLY hard PyQt5 import in the cluster and it does NOT use the try/except `Signal`/`pyqtSignal as Signal` shim the rest of the codebase uses (databrowser.py:15-17, eventoverlay.py:76-79, selectviewbox.py:5-7, spectrogramplot.py:9-11, timeplot.py:7-9). Either adopt the shim here or, better, delete the Qt import entirely (see architecture_problems).
  - **severity**: breaking

  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/buffereddata.py
  - **line**: 13
  - **api**: class _Notifier(QObject) — signal-carrier workaround; docstring at :16-19 says BufferedData 'cannot inherit from QObject without dragging the sip metaclass into BufferedArray's hierarchy'
  - **qt6_replacement**: The stated reason is PyQt/sip-specific. PySide6's Shiboken metaclass cooperates with plain Python bases, so `class BufferedData(BufferedArray, QObject)` is viable in Qt6 — but only take that if the data layer keeps Qt at all. The migration-correct move is the opposite: drop _Notifier and give BufferedData a binding-free callback/observer list, with a thin QObject adapter in the view layer.
  - **severity**: behavior-change

  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/buffereddata.py
  - **line**: 21
  - **api**: sigUpdated = Signal(object)
  - **qt6_replacement**: PySide6 Signal(object) is source-compatible. Behavioural difference to verify: PyQt5 marshals the `object` payload by reference; PySide6 keeps a reference to the emitted BufferedData for the duration of the emit and, on a queued connection, until delivery — relevant if this ever becomes cross-thread as the docstring at :242-244 anticipates.
  - **severity**: behavior-change

  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/buffereddata.py
  - **line**: 64
  - **api**: self.sigUpdated = self._notifier.sigUpdated — a bound signal stored as an attribute of a NON-QObject
  - **qt6_replacement**: PySide6 returns a `SignalInstance` here, which is storable and has .connect/.emit, so this survives. But PySide6 SignalInstance holds a strong reference back to _Notifier and the lifetime rules differ from sip's on-demand bound signal; if _Notifier is ever destroyed (C++ side) the stored instance raises RuntimeError on emit rather than silently no-oping. Wrap the emit or drop the alias.
  - **severity**: behavior-change

  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/buffereddata.py
  - **line**: 248
  - **api**: self._update_timer = QTimer() — parentless QTimer created lazily inside a non-QObject, never stopped/deleted on close
  - **qt6_replacement**: In PySide6 a parentless QTimer is owned by Python and is destroyed when the BufferedData is collected — but Data.close() (data.py:445) closes the loader without touching pending timers, so a pending flush_update can fire against a closed DataLoader. Qt6 design: no QTimer in the data layer at all; the debounce belongs to the controller (databrowser already owns filter_timer/envelope_timer at databrowser.py:1300-1307), or to a QThreadPool submit with a generation token.
  - **severity**: breaking

  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/buffereddata.py
  - **line**: 184
  - **api**: pi.isVisible() / pi.setVisible(show) on pyqtgraph GraphicsObjects, from inside the data layer (also :191, :196)
  - **qt6_replacement**: No API break, but it makes the buffering layer depend on QGraphicsItem visibility semantics. Replace with a plain `self.visible: bool` pushed down by the view; `need_update` then becomes a pure data-layer predicate.
  - **severity**: cosmetic

  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/data.py
  - **line**: 13
  - **api**: from . import theme — verified to pull PyQt5.QtCore, PyQt5.QtGui, PyQt5.QtWidgets and pyqtgraph into sys.modules at import of audian.buffereddata
  - **qt6_replacement**: The data layer must not import a module that imports QtWidgets. Split the two constants it actually needs (theme.LW_THIN/LW_THICK, floats at theme.py:615-616) and theme.trace_color (a str lookup, theme.py:845) into a Qt-free `palette`/`tokens` module, or have the view assign colour/линewidth after construction. Also relevant to binding selection: pyqtgraph picks its binding from whatever Qt module is already imported, so this import decides pyqtgraph's binding as a side effect.
  - **severity**: breaking

  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/data.py
  - **line**: 268
  - **api**: pi.isVisible() (also :277, :279 pi.setVisible, :431 pi.isVisible) — Data reaches into plot items to decide visibility and to derive need_update
  - **qt6_replacement**: Same as buffereddata: a per-trace/per-channel visibility flag owned by the data layer and written by the view. Removes the last Qt dependency from data.py.
  - **severity**: cosmetic

  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/data.py
  - **line**: 394
  - **api**: self.data.color = theme.trace_color('raw'); self.data.lw_thin = theme.LW_THIN (:400); self.data.lw_thick = theme.LW_THICK (:401) — theme values written onto the DataLoader
  - **qt6_replacement**: Move to the view layer (traceitem.py already re-resolves colour via theme.waveform_role at traceitem.py:99-101, so the values on the loader are half-vestigial). Keeps the loader a pure data object.
  - **severity**: cosmetic

  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/compresseddata.py
  - **line**: 638
  - **api**: `run()` is the `audian-compress` console script, but `audian/__init__.py:6` does `from .audian import main`, so importing audian.compresseddata imports the whole PyQt5 GUI (verified: PyQt5 in sys.modules after `import audian.compresseddata`)
  - **qt6_replacement**: Empty out audian/__init__.py (or make the GUI import lazy) so the headless compressor runs without a Qt binding at all. Otherwise the Qt6 migration makes the CLI depend on PySide6 being installed and on a working platform plugin.
  - **severity**: breaking

  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/theme.py
  - **line**: 677
  - **api**: QFontDatabase().families() — instantiating QFontDatabase, which Qt6 made an all-static class (constructor removed)
  - **qt6_replacement**: QFontDatabase.families(). Listed here because the data layer's `from . import theme` import chain makes theme.py a load-bearing dependency of this cluster; the call itself belongs to the theme cluster.
  - **severity**: breaking

- **architecture_problems**:
  - **title**: The entire DSP chain runs synchronously on the GUI thread on every scroll, zoom and pan
  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/data.py
  - **line**: 437
  - **evidence**: databrowser.set_times() (databrowser.py:7022-7034) calls Data.update_times() inline; update_times() calls DataLoader.update_time() (disk read + audioio's _recycle_buffer memmove) then trace.align_buffer() for every derived trace, which is move_buffer -> BufferedData.load_buffer (buffereddata.py:147) -> process(). process() is sosfilt over the whole reloaded region (bufferedfilter.py:57), sosfiltfilt over |x| (bufferedenvelope.py:56) and thunderlab.spectrogram (bufferedspectrogram.py:107). The costs are documented in the code itself: buffereddata.py:236-241 says '258 ms of sosfilt plus 857 ms of spectrogram plus 424 ms of decibel plus ~350 ms of setImage, on the GUI thread'.
  - **why_it_matters**: Every one of those milliseconds is a frozen window. It is also why the buffer budget (data.py:196-205), the pyramid, the SpecItem crop and the lw_thin<=1.0 rule all exist — they are all workarounds for having no worker thread. A Qt6 rewrite that keeps the synchronous call graph inherits every one of those workarounds and gains nothing.
  - **proposed_qt6_design**: A `DataEngine` QObject facade owning the trace DAG, with recompute submitted to a QThreadPool as (trace, offset, nframes, generation) jobs. Results land back on the GUI thread via a queued signal carrying the finished buffer plus its generation; stale generations are dropped. numpy/scipy release the GIL for sosfilt/FFT, so this is real parallelism. Double-buffer each trace (compute into a spare array, swap the reference under the GUI thread) so the drawing path never reads a buffer mid-write.
  - **effort**: large

  - **title**: sigUpdated has no subscribers: the debounced recompute completes and nothing repaints
  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/buffereddata.py
  - **line**: 271
  - **evidence**: grep across src/ and tests/ finds `sigUpdated` only at buffereddata.py:21, 64, 243 (docstring) and 271 (emit) — zero connects. Meanwhile databrowser.apply_filter() does `filtered.request_update(0)` (databrowser.py:7229) and then `self.panels.update_plots()` (7232) in the same slot. request_update(0) starts a single-shot QTimer, so flush_update -> update() -> recompute_all() runs AFTER update_plots() has already redrawn from the old buffer, and no repaint follows. Same shape in apply_envelope (databrowser.py:7289 then 7293).
  - **why_it_matters**: A filter or envelope change repaints stale data and only becomes visible on the next pan/zoom/resize. The one piece of async machinery in the data layer is wired to nothing.
  - **proposed_qt6_design**: Make the completion signal the contract: the engine emits `traceUpdated(trace)`, the panel/plot items connect to it and call update_plot(). Remove the `request_update(...)` + immediate `update_plots()` pairing from apply_filter/apply_envelope entirely — the redraw becomes a consequence of the signal, not a hopeful call ordered before the work.
  - **effort**: small

  - **title**: Two independent debouncers for one gesture, and the spectrogram bypasses both
  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/buffereddata.py
  - **line**: 231
  - **evidence**: databrowser owns filter_timer/envelope_timer at 200 ms (databrowser.py:1300-1307, started at 7211 and 7270); their slots then call request_update(0), which is a second QTimer inside BufferedData. Meanwhile set_resolution() calls `spectrogram.update(nfft, overlap_frac)` directly (databrowser.py:7110) — no debounce, no deferral — so the ~857 ms spectrogram recompute runs inline on every nfft keystroke (R / Shift+R are auto-repeatable).
  - **why_it_matters**: Two coalescing layers with different owners means neither is authoritative and the third parameter (nfft) accidentally has none. Auto-repeat on R is the exact case request_update was written for.
  - **proposed_qt6_design**: One coalescing point. Parameter changes write into a pending-params dict on the engine and post a single compacted job; the engine decides delay. Delete BufferedData._update_timer and the databrowser timers together.
  - **effort**: medium

  - **title**: The data layer asks Qt widgets what to compute
  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/buffereddata.py
  - **line**: 193
  - **evidence**: BufferedData.set_need_update() (buffereddata.py:193-208) and Data.set_need_update() (data.py:426-435) derive `need_update` by iterating `self.plot_items` and calling `pi.isVisible()`; Data.is_visible/set_visible (data.py:265-280) and BufferedData.is_visible/set_visible (182-191) do the same. `plot_items` is populated by the view itself (traceitem.py:63, specitem.py:48 both assign `self.data.plot_items[self.channel] = self`).
  - **why_it_matters**: Buffering policy — the most expensive decision in the app — is read out of QGraphicsItem state. It cannot be unit-tested without a QApplication, cannot run off the GUI thread (QGraphicsItem is not thread-safe), and inverts ownership: the view writes itself into the model's array.
  - **proposed_qt6_design**: `BufferedData.visible: bool` (or a per-channel bool array) set by the view through the engine. `set_need_update()` becomes pure Python over the DAG. The view keeps its own list of items; the model keeps none.
  - **effort**: medium

  - **title**: Data.open() grafts a dozen pipeline and presentation attributes onto a third-party DataLoader
  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/data.py
  - **line**: 386
  - **evidence**: data.py:386-408 assigns name, panel, panel_type, plot_items, color, lw_thin, lw_thick, dests, need_update, mip_pyramid onto `self.data` (a thunderlab DataLoader), and count_buffer_loads() adds buffer_generation. The raw loader is then treated as a BufferedData everywhere: data.py:437 calls `self.data.need_update`, traceitem.py:273-278 reads `mip_pyramid`/`buffer_generation` via getattr, buffereddata.py:95 appends to `source.dests`, buffereddata.py:205 walks `trace.source` chains that terminate on it.
  - **why_it_matters**: There is an implicit interface (`name, rate, frames, buffer, offset, dests, need_update, buffer_generation, mip_pyramid, plot_items, panel, panel_type`) that exists nowhere as a declaration, is satisfied by monkeypatching, and breaks silently if thunderlab ever adds a colliding attribute. The getattr-with-default reads in traceitem.py are the symptom.
  - **proposed_qt6_design**: A `RawTrace` adapter class wrapping the DataLoader and implementing the same explicit `Trace` protocol as BufferedData (typing.Protocol or an ABC). Nothing is written onto the loader; `Data.traces[0]` is a real object of the same type as the rest.
  - **effort**: medium

  - **title**: count_buffer_loads monkeypatches a bound method and creates a reference cycle
  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/data.py
  - **line**: 169
  - **evidence**: data.py:177-185: `inner = loader.load_buffer; def load_buffer(...): loader.buffer_generation += 1; return inner(...); loader.load_buffer = load_buffer`. The closure captures `loader`, and is stored on `loader` — an unbreakable-by-refcount cycle on an object holding open file handles and a multi-MB buffer. A second call would double-wrap and double-count.
  - **why_it_matters**: The loader (and its buffer) is only freed by the cyclic GC, on a code path whose whole purpose is bounding memory (data.py:196-205). Data.__del__ (data.py:237) makes the cycle's collection order matter.
  - **proposed_qt6_design**: Put the generation counter where the write happens: the RawTrace adapter overrides load_buffer, or the engine bumps a generation as part of the job-completion handler. No rebinding of a foreign object's methods.
  - **effort**: small

  - **title**: The min/max pyramid is built inside the paint path
  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/buffereddata.py
  - **line**: 322
  - **evidence**: MinMaxPyramid.build() is called from TraceItem.peaks() (traceitem.py:275-279), which is called from TraceItem.update_plot(). The class docstring measures the base level at 25-28 ms for a 70 MB 16-channel buffer (buffereddata.py:341-343), and _base_level allocates `out` plus, on the reshape path, two full (nbins, channels) temporaries (:360-362).
  - **why_it_matters**: After every buffer move, the first channel drawn pays the whole rebuild synchronously; the other fifteen ride free on the generation check. The cost is invisible in profiles attributed per-channel and lands squarely in the frame that the user perceives as the scroll.
  - **proposed_qt6_design**: Build the pyramid as the last step of the recompute job, on the worker thread, and ship it with the buffer. `decimate()` stays a pure read in the paint path.
  - **effort**: small

  - **title**: Per-recompute array copies: every trace allocates one to four full-buffer float64 temporaries
  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/bufferedfilter.py
  - **line**: 57
  - **evidence**: bufferedfilter.py:57 `dest[:] = sosfilt(self.sos, source, axis=0)[nbefore:]` — sosfilt allocates a float64 output the full size of `source` (the raw float64 buffer), then the assignment casts it into the float32 dest: one full temporary plus a converting copy. bufferedenvelope.py:56 `sosfiltfilt(self.sos, (np.pi/2)*np.abs(source), axis=0)[nbefore:]` — np.abs allocates one, the scalar multiply another, sosfiltfilt pads and allocates more: three to four full-buffer float64 arrays. bufferedspectrogram.py:116 `dest[:n] = Sxx.transpose((1,2,0))` — a strided transposing write of the whole spectrogram block. specitem.py:161-163 `decibel(block.T)` allocates a fresh array per channel per upload, then setImage copies it again. compresseddata.py:320 `np.square(self.data.buffer)` squares an entire in-memory file.
  - **why_it_matters**: The buffer budget at data.py:196 is 64 MB for the raw window; the transient allocations during a single recompute are a multiple of that and are the reason the measured peak is far above the nominal figure. They are also pure GUI-thread time.
  - **proposed_qt6_design**: Once recompute is off-thread, give each trace a reusable scratch array sized with the buffer and pass `zi`/preallocated outputs where scipy allows; use out= for the abs/scale (np.abs(source, out=scratch); scratch *= pi/2). For the spectrogram, ask thunderlab for the (time, channel, freq) order or write per-channel slices into dest to avoid the transposing copy.
  - **effort**: medium

  - **title**: Analyse-region can allocate the whole selected span in every trace, synchronously, under a wait cursor
  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/data.py
  - **line**: 282
  - **evidence**: Data.get_region (data.py:282-298) does `data = t[i0:i1, channel]` for every trace. BufferedArray.__getitem__ calls update_buffer (audioio/bufferedarray.py:264), and _buffer_position (bufferedarray.py:441-465) only recentres a window when `nframes < self.bufferframes`; otherwise it returns `offset=start, nframes=stop-start` verbatim, so allocate_buffer sizes the buffer to the entire requested region. The caller is databrowser.analyze_region (databrowser.py:8058-8067), bracketed by QApplication.setOverrideCursor(Qt.WaitCursor).
  - **why_it_matters**: Analysing a ten-minute selection on a 16-channel 20 kHz file reallocates the raw buffer, the filtered buffer (re-running sosfilt over all of it) and the spectrogram buffer to that span — hundreds of MB and tens of seconds, with the UI frozen behind a wait cursor. Separately, the returned arrays are live views into those buffers, so an analyzer that keeps one sees it overwritten by the next scroll.
  - **proposed_qt6_design**: A dedicated block-wise read path that streams the region through the same `process()` functions without touching the interactive buffers, run as a cancellable job with a progress signal. Return owned copies, never views.
  - **effort**: medium

  - **title**: Buffer moves memmove and re-filter with no ring buffer and no cancellation
  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/buffereddata.py
  - **line**: 132
  - **evidence**: align_buffer (buffereddata.py:132-145) -> BufferedArray.move_buffer -> _recycle_buffer (audioio/bufferedarray.py:494-523), which slices the retained part and assigns it back into the (possibly same) array: a memmove of up to the whole buffer per scroll step. Only the newly exposed tail is re-read, but BufferedData.load_buffer (buffereddata.py:147-175) re-runs `process()` over that region plus the tbefore/tafter warm-up on every move. Nothing anywhere can abandon an in-flight move when the user scrolls again.
  - **why_it_matters**: Held-arrow scrolling issues one full move per repeat and each runs to completion. This is the mechanism behind the 35 ms-per-set_times figure quoted at buffereddata.py:281-284.
  - **proposed_qt6_design**: A genuine ring buffer (offset modulo capacity) removes the memmove; the drawing path already goes through the pyramid and SpecItem crop, both of which can be taught the wrap. Jobs carry a generation token and check a cancellation flag between blocks, so superseded scroll positions are abandoned rather than computed.
  - **effort**: large

  - **title**: The compressor holds the shared-memory lock across the reduction, serialising the only parallel work
  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/compresseddata.py
  - **line**: 82
  - **evidence**: compresseddata.py:82-88 wraps both `np.minimum.reduceat` and `np.maximum.reduceat` in `with array.get_lock():`, and :93-101 wraps the two `np.add.reduceat` calls plus `np.square(buffer)` in `with stats_array.get_lock():`. Workers stride disjoint block ranges (`range(proc_idx*nblock, frames, num_proc*nblock)`, :75) and index the shared array as `2*index//step` (:81), so their writes never overlap.
  - **why_it_matters**: The lock is unnecessary for correctness between workers and is held over the entire CPU-bound reduction of a 30-second block, so N workers do the reduction one at a time. np.square inside the lock also allocates a full block copy while every other worker waits. The only reader that needs the lock is the navigator's snapshot (fulltraceplot.py:737-744).
  - **proposed_qt6_design**: Drop the write locks; give the reader a per-block 'complete' flag array (or reuse the existing progress counter as a high-water mark) so it copies only finished bins. If a lock is still wanted for the reader, take it around the assignment only, not the reduction.
  - **effort**: small

  - **title**: Overview readiness is polled by two timers instead of signalled, and each poll copies the shared array
  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/compresseddata.py
  - **line**: 376
  - **evidence**: FullTracePlot._timer retries plot_data with exponential backoff 250 -> 2000 ms (fulltraceplot.py:442-444, 700-704); DataBrowser.overview_timer polls report_overview_progress every 250 ms (databrowser.py:1849, 3160-3182). Both interrogate CompressedData.is_busy() (compresseddata.py:376-386) and progress() (:388-399). Each busy poll takes the lock and does `np.array(times); np.array(datas)` — a full copy of the shared arrays (fulltraceplot.py:740-744).
  - **why_it_matters**: Two unsynchronised pollers over one piece of state, a completion path that depends on which timer notices first, and a repeated O(n*channels) copy that grows with screen width. is_busy() also has the side effect of reaping and clearing self.procs, so its truth value depends on who called it last.
  - **proposed_qt6_design**: Move the pool behind a QObject with `progressChanged(float)` and `finished()` — either a QThread that joins the processes, or keep multiprocessing and drive it from a single QTimer inside CompressedData that owns the polling and emits. Consumers connect; no consumer polls. Keep the incremental snapshot but copy only the newly completed bin range.
  - **effort**: medium

  - **title**: Cache load does file I/O and rewrites a JSON index on the GUI thread during window construction
  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/compresseddata.py
  - **line**: 473
  - **evidence**: CompressedData.load_data() (compresseddata.py:473-561) is called from FullTracePlot.prepare() (fulltraceplot.py:675), itself called at the end of DataBrowser.open() (databrowser.py:1848). It does load_audio() on the sidecar wav, np.load() on the stats, unlink()s stale entries, and rewrites fulltraces.json — all blocking. save_data() (compresseddata.py:418-471) does the same on the completion path from _plot_data (fulltraceplot.py:733).
  - **why_it_matters**: Opening a file blocks on cache I/O of unbounded size before the window is interactive, and a cache-eviction pass (:447-456) unlinks files inline.
  - **proposed_qt6_design**: Cache read/write as a job on the same pool as the compression; the navigator draws nothing until the `overviewReady` signal, which is the state it already handles (fulltraceplot.py:725-727).
  - **effort**: small

  - **title**: The overview cache is a WAV with a fabricated sample rate whose scale is guessed back on load
  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/compresseddata.py
  - **line**: 411
  - **evidence**: save_data_local (:411-415) and save_data (:461-463) compute `rate = 1/(times[1]-times[0])`, multiply by 1e6 and divide by 1e3 until it fits in 2**31, then write it as a WAV sample rate. load_data (:505-511) recovers the bin rate by trying rate/1e6, rate/1e3 and rate and picking whichever makes the duration closest to the recording's. The moments live in a separate .npy (:128-137) whose only validation is a row count (:161-165).
  - **why_it_matters**: A three-way guess stands between the cache and the numbers the navigator draws; a coincidence in durations picks the wrong bin rate and the whole overview is time-shifted. The two files can also be paired incorrectly whenever the row counts happen to match.
  - **proposed_qt6_design**: One .npz (or .npy + a small JSON header) holding minima/maxima/sums/sum-of-squares, the exact `step`, `rate`, `frames`, channel count and a source fingerprint. Reject on mismatch instead of inferring. Keep reading the old wav format for one release if existing caches matter.
  - **effort**: small

  - **title**: Dead and vestigial state computed on every recompute
  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/bufferedspectrogram.py
  - **line**: 122
  - **evidence**: `self.spec_rect` is rebuilt at the end of every process() (bufferedspectrogram.py:122-127) and initialised at :83 and :94; grep finds no reader — specitem.py:164 explicitly says 'rect covers the CROPPED extent, not data.spec_rect'. `self.use_spec` is set at :84 and :95 and never read anywhere. `sigUpdated` (buffereddata.py:21) has no connects. `BufferedData.update_step` maintains `self.size = self.frames*self.channels` (buffereddata.py:85) which ignores the frequency axis for the 3-D spectrogram.
  - **why_it_matters**: Vestigial view geometry on the data object is exactly the coupling the migration is meant to remove, and it invites a future reader to trust a stale rect.
  - **proposed_qt6_design**: Delete spec_rect and use_spec. Keep frequencies/fresolution/tresolution — those are measurements, not geometry.
  - **effort**: small

  - **title**: setup_traces links the DAG by string identity, not equality
  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/data.py
  - **line**: 309
  - **evidence**: data.py:309: `if self.traces[k] is not None and self.traces[k].source_name is sname:`. This works only because every source_name in-tree is a literal ('data' at :306, 'filtered' at bufferedspectrogram.py:65 and bufferedenvelope.py:106) and CPython interns literals. A plugin (plugins.py:26 add_trace_factory) whose source_name is built at runtime — f-string, config file, str concatenation — fails to match.
  - **why_it_matters**: The failure mode is a print to stdout (data.py:319-325) and a trace silently dropped from the pipeline, in a GUI where nobody reads stdout. Extension is the documented purpose of the plugin hook.
  - **proposed_qt6_design**: `==`, and case-fold it to match the lookup semantics of Data.__getitem__ (data.py:243). Raise or surface through the browser's notify() rather than printing.
  - **effort**: small

  - **title**: Failures are reported to stdout/stderr from inside the data layer
  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/data.py
  - **line**: 319
  - **evidence**: data.py:319-325 prints '! ERROR: source ... not found!' plus an availability list; compresseddata.py:148 prints 'could not write activity statistics'; :455 and :550 print bare exceptions from cache eviction. data.py already has a logger (data.py:17) and a `load_warnings` list surfaced to the browser (data.py:216, 370-377) — the good pattern exists but is used only for join gaps.
  - **why_it_matters**: In a GUI these messages go nowhere. The join-gap path shows the right design already.
  - **proposed_qt6_design**: Extend the `load_warnings`/log pattern to every failure in the cluster; the browser drains it into notify()/the status bar.
  - **effort**: small

  - **title**: Cache bounds are per-object and unbounded in aggregate
  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/data.py
  - **line**: 196
  - **evidence**: Data.buffer_bytes = 64 MB (data.py:196-207) budgets the RAW buffer of ONE trace and is applied by scale_buffer_time (data.py:288-299). Every derived trace then sizes itself from the source's buffer duration (buffereddata.py:86-89) with no budget of its own: filtered is float32 (half), spectrogram is float64 over (time, channel, nfft/2+1) and can exceed the raw buffer. On top of that each trace carries a MinMaxPyramid at ~25% of its buffer (buffereddata.py:298), and each SpecItem holds an uploaded crop of up to 4x the visible range (specitem.py:30, 156-163). The docstring at data.py:198-200 states the real total: 153.6 MB raw + the same filtered + 155 MB spectrogram for a 10 s view.
  - **why_it_matters**: The one number an operator could tune governs a third of the actual footprint, and the derived traces — the expensive ones — are sized indirectly through a duration.
  - **proposed_qt6_design**: A single memory budget owned by the engine, allocated across the DAG: each trace declares bytes-per-second (dtype x channels x rate x extra axes), the engine solves for the common window duration. The pyramid and the image crops are part of the same accounting.
  - **effort**: medium

  - **title**: Data.update_times dereferences self.data without the None guard its sibling has
  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/data.py
  - **line**: 437
  - **evidence**: data.py:426-428 `set_need_update()` opens with `if self.data is None: return`; data.py:437-447 `update_times()` goes straight to `self.data.need_update` and then `self.data.rate`, `self.data.frames`, `self.data.get_file_index(...)`. Data.close() (data.py:445-449) sets self.data = None, and Data.__del__ calls close().
  - **why_it_matters**: Any path that reaches a range change after close — a pending timer, a queued signal, a browser being torn down — raises AttributeError out of a Qt slot. The migration to queued cross-thread signals makes exactly that ordering more likely, not less.
  - **proposed_qt6_design**: Same guard, and make close() idempotent and explicit about cancelling in-flight jobs and pending timers before dropping the loader.
  - **effort**: small

  - **title**: Cross-layer index clamps are hand-tuned to avoid triggering a buffer move from the paint path
  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/bufferedspectrogram.py
  - **line**: 167
  - **evidence**: Three separate defences exist against __getitem__ moving a buffer during drawing: BufferedSpectrogram.visible_slice (bufferedspectrogram.py:167-182), SpecItem.visible_indices (specitem.py:124-137), TraceItem.buffer_range (traceitem.py:198-215) and get_amplitude's direct buffer read (traceitem.py:293-322). SpectrogramPlot.visible_block still indexes the BufferedArray itself (`self.spec_data[i0:i1, self.channel, :]`, spectrogramplot.py:427) behind a clamp whose comment reads 'the -1 is important to not move the spectrogram buffer at end of data' (spectrogramplot.py:418-419).
  - **why_it_matters**: The dangerous operation (a slice that silently performs disk I/O plus a full re-filter) is the DEFAULT behaviour of the type, and every call site has to remember not to use it. One forgotten clamp is a multi-hundred-millisecond stall inside a paint handler.
  - **proposed_qt6_design**: Split the interface: `Trace.window()` returns a read-only view of the currently loaded buffer with its offset and never does I/O (the drawing path uses only this), while `Trace.read(start, stop)` is the explicit, possibly-blocking accessor used by analysis and export. Then delete the four defensive clamps.
  - **effort**: medium

- **behavior_contract**:
  - A multi-file session shows every frame of every file the user named, including the final
  - short one that thunderlab's timestamp heuristic would drop; if the loader still comes
  - back short, opening FAILS with an error naming the missing files rather than displaying
  - a truncated recording (data.py:45-118, tests/test_dataloader.py).
  - A join whose metadata timestamp does not line up (>1 s, judged against both the open-
  - time and the close-time reading) produces a warning in Data.load_warnings that the
  - browser surfaces; an equal-length TASCAM session produces NO spurious warning at its
  - final short file (data.py:120-163, tests/test_dataloader.py:98).
  - Scrolling, zooming and panning keep the raw trace, the filtered trace, the envelope and
  - the spectrogram all showing the same time window; the user never sees a lane refresh at
  - a different position from its neighbours, and buffer refills are invisible apart from
  - latency.
  - The drawn waveform at any zoom level is the true per-pixel-column min/max envelope: no
  - peak is ever lost by decimation, and the drawn bins line up with the time axis they are
  - plotted on (buffereddata.py:278-423, tests/test_minmaxpyramid.py).
  - Below the pyramid's base step the waveform is drawn from actual samples; at step==1 with
  - more than ~10 pixels per sample the individual samples are marked with 'o' symbols and a
  - thicker pen (traceitem.py:244-258).
  - Changing the highpass/lowpass cutoff redraws the filtered trace AND everything derived
  - from it (spectrogram, envelope) and updates the navigator's colour; holding a cutoff key
  - on auto-repeat collapses the burst into a small number of recomputes rather than one per
  - keystroke (buffereddata.py:231-252, databrowser.py:7211/7229).
  - A pass-through filter (highpass < 0.001*nyquist and lowpass >= nyquist) yields exactly
  - the raw samples, and the trace is then painted with the raw role's colour rather than
  - the filtered one (bufferedfilter.py:60-64, traceitem.py:92-101).
  - The filtered trace shows no filter transient at a buffer seam: the 0.5 s warm-up region
  - is prepended before filtering and discarded afterwards (bufferedfilter.py:14,
  - buffereddata.py:162-175).
  - The envelope is sosfiltfilt over (pi/2)*|x| at envelope_cutoff, clamped to >= 0 when no
  - envelope highpass is set; an out-of-range cutoff leaves sos=None and draws a flat zero
  - line rather than raising (bufferedenvelope.py:50-60, 62-82).
  - Changing nfft or overlap reflows the spectrogram and updates the Δf and Δt labels and
  - the window/overlap widgets; nfft is clamped to [8, min(len(source)//2, 2**30)], overlap
  - to [0, 0.99999], and hop to [1, nfft] (bufferedspectrogram.py:129-165,
  - databrowser.py:7110-7137).
  - The spectrogram's frequency extent is 0 .. rate/2 + fresolution and the image rect
  - matches the buffer region actually uploaded (bufferedspectrogram.py:99/126,
  - specitem.py:166-171).
  - A mean-over-channels spectrogram is the mean of the POWER converted to decibel once —
  - never the mean of decibels. Channels recorded as exact zero must not blank the panel
  - (bufferedspectrogram.py:10-37, tests/test_meanspectrogram.py).
  - The startup colour ramp puts its floor at max(95th percentile of the top 1/16 of the
  - frequency axis, median + 3 dB), its top at floor + 0.95*(max-floor), and clamps the span
  - to [20, 80] dB; it is estimated ONCE per spectrogram (the `init` latch) and separately
  - for a mean panel versus a single channel (bufferedspectrogram.py:184-247).
  - The pointer/crosshair amplitude and power readouts never move a buffer: hovering outside
  - the loaded window returns no value instead of performing a disk read and a re-filter
  - (traceitem.py:293-322, specitem.py:101-113).
  - The navigator strip fills in progressively while the background compression runs, with a
  - 'Building overview…' progress fraction in the status bar that reaches 1.0 and clears; a
  - broken channel degrades the navigator without taking the window down
  - (compresseddata.py:388-399, fulltraceplot.py:706-747, databrowser.py:3160-3182).
  - The navigator's time vector and data array always have the same length — a mismatch used
  - to raise inside a QTimer slot and blank the strip (compresseddata.py:219-241,
  - tests/test_compresseddata.py:44).
  - A completed overview is cached and reused on the next open of the same recording; a
  - cache that is too coarse for the current screen, or that lacks the moments sidecar, is
  - discarded and recomputed rather than drawn under-resolved or paired with fabricated
  - statistics (compresseddata.py:473-561).
  - The activity overview is offered only when the second moments exist; without them the
  - navigator stays on the waveform envelope rather than showing a metric built on missing
  - data (compresseddata.py:169-193, fulltraceplot.py:771-808).
  - `audian-compress FILES` writes `<stem>-fulltrace.wav` plus
  - `<stem>-fulltrace.wav.stats.npy` beside the recording, and a later GUI session picks
  - them up (compresseddata.py:564-640, 405-416).
  - Raw buffer memory is bounded: buffer_time is scaled down toward a 64 MB budget as
  - channels x rate rises, clamped to [10 s, 60 s], and back_time stays buffer_time/3
  - (data.py:196-299).
  - Analyse-region returns, per trace, (time, data) — and (time, freqs, spec) for the
  - spectrogram — over the requested [t0, t1] and channel, clamped to the recording
  - (data.py:282-298, databrowser.py:8058-8073).
  - Closing a file terminates and reaps the background compression workers; no orphan
  - processes survive (compresseddata.py:195-203, fulltraceplot.py:574).
  - Toggling a trace's or a panel's visibility stops it being recomputed on scroll, and
  - turning it back on brings it back in step with the current window (data.py:426-435,
  - buffereddata.py:193-208, databrowser.py:7591-7596).
- **risk**: high — this is the performance-critical core that every panel reads directly, the entire pipeline is synchronous and stateful with hand-tuned index clamps at four call sites protecting against a slice that silently does I/O, and only three narrow test files (test_minmaxpyramid, test_compresseddata, test_dataloader) cover any of it; the filter/envelope/spectrogram recompute chain has no tests at all.
- **notes**: FILE-LIST CORRECTION: `src/audian/markerdata.py` does not exist. It was deleted in commit b52a5e1 ("Let the reader draw their own labels, beside the ones the log made", 2026-08-27) and replaced by a split pair — `src/audian/labels.py` (the Qt-free store) and `src/audian/labeloverlay.py` (the Qt half). Whoever assembled this cluster list should reassign labels.py/labeloverlay.py to whichever cluster owns annotations; labeloverlay.py:107 imports QVariant and QAbstractTableModel, both of which need Qt6 treatment (QVariant is gone from PySide6 — use None/plain Python objects — and QAbstractTableModel roles move to Qt.ItemDataRole).  QT-BINDING SHAPE OF THIS CLUSTER: exactly one file imports Qt directly (buffereddata.py:8). Everything else is transitive through `from . import theme`, which pulls QtCore+QtGui+QtWidgets+pyqtgraph (verified by importing audian.buffereddata in a clean interpreter). The cluster needs only three things from theme: LW_THIN, LW_THICK (floats, theme.py:615-616) and trace_color() (returns a str, theme.py:845). Extracting those into a Qt-free token module makes data.py, bufferedfilter.py, bufferedenvelope.py and bufferedspectrogram.py completely binding-free, which is the single highest-leverage change in the cluster and is a prerequisite for moving recompute onto worker threads.  BINDING-SELECTION HAZARD: pyqtgraph resolves its Qt binding from QT_LIB or from whichever Qt module is already in sys.modules. Because the data layer imports theme (which imports both pyqtgraph and PyQt5), the import order of this cluster currently participates in that decision. During the migration, set QT_LIB=PySide6 explicitly at the entry points (audian.py, compresseddata.py main) rather than relying on import order.  INCONSISTENT SHIM: five modules already use a `try: from ...QtCore import Signal / except ImportError: from ...QtCore import pyqtSignal as Signal` shim (databrowser.py:15-17, eventoverlay.py:76-79, selectviewbox.py:5-7, spectrogramplot.py:9-11, timeplot.py:7-9). buffereddata.py:8 does not. If a staged migration is planned, that shim is the existing convention; if a hard cut is planned, delete the shim everywhere at once rather than adding a sixth copy.  MULTIPROCESSING + Qt6: audian.py:5037 and compresseddata.py:565 both call set_start_method('forkserver' on posix, 'spawn' elsewhere) — that is already the safe choice and must not regress to fork, since forking a process with a live QGuiApplication is undefined. The workers receive only picklable arguments (paths, scalars, the shared Arrays, load_kwargs), so nothing Qt-shaped crosses the boundary today; keep it that way when the engine is introduced.  MEASUREMENTS QUOTED ABOVE come from the code's own docstrings, which record profiling on a 16-channel 20 kHz file (buffereddata.py:236-241, 281-296, 340-343; data.py:196-207, 395-399; bufferedfilter.py:54-56; specitem.py:19-23; bufferedspectrogram.py:186-191, 217-228; traceitem.py:158-166; compresseddata.py:205-217). They are unusually specific and are the best available baseline for judging whether a Qt6 rewrite actually improved anything — capture them as a benchmark before touching the code.  DEPENDENCY EDGES OUT OF THE CLUSTER (what will break if the interfaces change): traceitem.py:273-291 (mip_pyramid, buffer_generation, data.buffer, data.offset), specitem.py:9/48/85/98/107-113/140-172 (channel_power, plot_items assignment, estimate_noiselevels, data.buffer, buffer_changed, fresolution, source.rate), spectrogramplot.py:15/396/427 (channel_power, fresolution, __getitem__), fulltraceplot.py:31/440/675-676/729-747/789 (the whole CompressedData surface), databrowser.py:1159/1558/7028/7040/7110/7229/7289/7592/8064 and 3167-3181, plugins.py:11-13, activity.py (BinStats consumer). data.py:15 is the only in-cluster import of MinMaxPyramid outside buffereddata.py.
