# Recon: analysis-plugins

- **cluster**: analysis-plugins
- **purpose**: This cluster is audian's extension point. `plugins.py` is a discovery + registry object that scans the *current working directory* for `audian*.py` files, imports them, and harvests any module-level callable named `audian_*` into one of two factory lists (`*traces` → trace factories, `*analyzer` → analyzer factories); those factories are then called once per `DataBrowser` with the browser as their sole argument. `analyzer.py` defines the `Analyzer` base class — the de-facto plugin API surface — which self-registers into `browser.analyzers` from its own constructor, owns a `thunderlab.TableData` of results, and can attach `pyqtgraph.ScatterPlotItem` event markers to traces or panels. `statisticsanalyzer.py` is the 26-line reference implementation shipped in-tree. `songdetector.py` at the repo root is *not* a plugin and not a consumer of this contract at all: it is a 777-line standalone matplotlib/argparse script from 2018 with zero Qt and zero `audian` imports, and it no longer runs on the installed matplotlib.
- **public_surface**:
  - **name**: Plugins
  - **file**: src/audian/plugins.py
  - **kind**: class
  - **base**: object
  - **summary**: Registry + discoverer. Constructed once in audian.main() (audian.py:5040) and threaded through Audian.__init__ (audian.py:1497) into every DataBrowser (databrowser.py:1170-1174, 1834). Holds `plugins` dict, `trace_factories` list (pre-seeded with default_setup_traces), `analyzer_factories` list. Methods: add_plugin, add_trace_factory, clear_trace_factories, add_analyzer_factory, clear_analyzer_factories, load_plugins, setup_traces(browser), setup_analyzer(browser).

  - **name**: default_setup_traces
  - **file**: src/audian/plugins.py
  - **kind**: function
  - **base**: 
  - **summary**: plugins.py:11-13. The built-in trace factory, registered unconditionally at plugins.py:20. Calls browser.add_trace(BufferedFilter()) and browser.add_trace(BufferedSpectrogram()). Its presence forces plugins.py to import the whole buffered-trace stack (and transitively theme.py/Qt) at module import.

  - **name**: Plugins.load_plugins
  - **file**: src/audian/plugins.py
  - **kind**: function
  - **base**: 
  - **summary**: plugins.py:38-55. Appends os.fspath(Path.cwd()) to sys.path, globs cwd for 'audian*.py', importlib.import_module(stem) on each, scans dir(module) for callables named audian_*, dispatches by suffix ('traces'/'analyzer'), prints a load line, pops sys.path. No try/except anywhere.

  - **name**: Analyzer
  - **file**: src/audian/analyzer.py
  - **kind**: class
  - **base**: object
  - **summary**: analyzer.py:31-315. The documented plugin base class. __init__(browser, name, source_name) stores browser, resolves source via self.trace(), creates a TableData, and self-registers with browser.add_analyzer(self) at line 106. Subclass hook: analyze(t0, t1, channel, traces). Helpers: clear(), traces(), trace(name), make_column(label, unit, formats), store(*args), make_trace_events(name, trace_name, symbol, color, size), make_panel_events(name, panel_name, symbol, color, size), set_events(name, channel, x, y), add_events(name, channel, x, y). Attributes browser/name/source_name/source/data/events are all public and read by DataBrowser.

  - **name**: PlainAnalyzer
  - **file**: src/audian/analyzer.py
  - **kind**: class
  - **base**: Analyzer
  - **summary**: analyzer.py:318-348. Always constructed first at databrowser.py:1832. Source trace 'data'. Adds columns tstart/tend/duration/channel with precision derived from source.rate (line 338). Because it always registers, `len(self.analyzers) == 0` at databrowser.py:1835 is unreachable and the analyze-region action is never auto-disabled.

  - **name**: style_result_table
  - **file**: src/audian/analyzer.py
  - **kind**: function
  - **base**: 
  - **summary**: analyzer.py:16-28. Only Qt-touching code in the cluster. Applies theme.font_mono() to a pg.TableWidget, a 10*TOOLBAR_HEIGHT minimum height, and small-size fonts to horizontal/vertical headers (both null-guarded). Imported by databrowser.py:99 and called at databrowser.py:8108.

  - **name**: StatisticsAnalyzer
  - **file**: src/audian/statisticsanalyzer.py
  - **kind**: class
  - **base**: Analyzer
  - **summary**: statisticsanalyzer.py:6-26. Constructed second at databrowser.py:1833 with the default source_name 'filtered'. Guards a missing source by returning early from __init__ *after* super() already registered it (lines 11-14), so it stays in browser.analyzers as a zero-column, no-op member. analyze() re-guards on `self.source_name not in traces` and stores mean/std of traces[name][1].

  - **name**: songdetector.SignalPlot / detect_songs / analyse_songs / threshold_estimates / env_freqs / clean_env_freqs / filter_envelopes / envelope / bandpass_filter / lowpass_filter / highpass_filter / main
  - **file**: songdetector.py
  - **kind**: class
  - **base**: object
  - **summary**: songdetector.py:25-777. Top-level defs of a standalone CLI script (entry at :684, `if __name__` at :775). Nothing in src/ imports it, it is not in pyproject scripts/gui-scripts, no test touches it, and `grep -n audian songdetector.py` matches only a comment at line 776. It defines NO audian_* hook and imports NO audian and NO Qt module. It is a signal-processing archive, not a plugin exemplar.

- **qt5_api_usage**:
  - **file**: src/audian/analyzer.py
  - **line**: 7
  - **api**: `import pyqtgraph as pg` with no binding pinned anywhere in the cluster
  - **qt6_replacement**: pyqtgraph binds to whichever Qt wrapper is already in sys.modules. Since load_plugins() imports arbitrary third-party modules (plugins.py:42) before/around this, the binding must be forced once at process start (import PySide6 before pyqtgraph, or set PYQTGRAPH_QT_LIB=PySide6) and load_plugins must refuse a plugin that pulls in PyQt5.
  - **severity**: breaking

  - **file**: src/audian/analyzer.py
  - **line**: 16
  - **api**: `def style_result_table(table: pg.TableWidget)` — the annotation is evaluated at def time, so importing analyzer.py forces the full pyqtgraph→Qt import chain
  - **qt6_replacement**: `from __future__ import annotations` (or quote the annotation) so the analyzer plugin API can be imported headlessly; keeps binding selection under the app's control instead of a plugin's.
  - **severity**: behavior-change

  - **file**: src/audian/analyzer.py
  - **line**: 21
  - **api**: `table.setFont(theme.font_mono())` → theme.font_mono → theme.py:677 `QFontDatabase().families()`
  - **qt6_replacement**: QFontDatabase is static-only in Qt6 and cannot be instantiated: `QFontDatabase.families()`. This is the only breaking Qt call reachable from the cluster, and it is reached on every analysis-table open.
  - **severity**: breaking

  - **file**: src/audian/analyzer.py
  - **line**: 22
  - **api**: `table.setMinimumHeight(10 * theme.TOOLBAR_HEIGHT)` — a raw px constant (36) with no devicePixelRatio consideration
  - **qt6_replacement**: Fine under Qt6 (Qt6 always does per-screen DPI scaling, unlike the Qt5 AA_EnableHighDpiScaling opt-in), but the 360 px floor must be re-verified on a fractional-scale display once the Qt5 high-DPI attributes are removed.
  - **severity**: cosmetic

  - **file**: src/audian/analyzer.py
  - **line**: 24
  - **api**: `table.horizontalHeader()` / `table.verticalHeader()` with explicit `is not None` guards
  - **qt6_replacement**: Unchanged in Qt6/PySide6; guards remain correct. No action.
  - **severity**: cosmetic

  - **file**: src/audian/analyzer.py
  - **line**: 222
  - **api**: `pg.ScatterPlotItem()` + setSymbol / setBrush(color) / setSize(size) — the plugin-visible marker API
  - **qt6_replacement**: API-stable across bindings, but `color` is an untyped pyqtgraph colour spec passed straight from plugin code into pg.mkBrush. Under PySide6 an invalid spec raises from deeper inside pyqtgraph; the contract should validate/normalise via pg.mkBrush at the boundary.
  - **severity**: cosmetic

  - **file**: src/audian/buffereddata.py
  - **line**: 8
  - **api**: `from PyQt5.QtCore import QObject, QTimer, pyqtSignal as Signal` — the base class every *trace* plugin must subclass
  - **qt6_replacement**: `from PySide6.QtCore import QObject, QTimer, Signal`. This is a hard plugin-compatibility break: any out-of-tree `audian_*_traces` factory that subclasses BufferedData and declares its own pyqtSignal is source-incompatible with Qt6, and importing PyQt5 alongside PySide6 in one process is a segfault risk, not a warning.
  - **severity**: breaking

  - **file**: src/audian/databrowser.py
  - **line**: 8059
  - **api**: `QApplication.setOverrideCursor(Qt.WaitCursor)` — unscoped enum, in the analyzer dispatch path
  - **qt6_replacement**: `Qt.CursorShape.WaitCursor`. Also note the paired restoreOverrideCursor at :8067 is not in a finally, so a raising plugin leaves the wait cursor permanently applied.
  - **severity**: breaking

  - **file**: src/audian/databrowser.py
  - **line**: 8101
  - **api**: `dialog.setWindowModality(Qt.NonModal)` and `dialog.setAttribute(Qt.WA_DeleteOnClose)` (:8102) — unscoped enums on the analysis results dialog
  - **qt6_replacement**: `Qt.WindowModality.NonModal`, `Qt.WidgetAttribute.WA_DeleteOnClose`.
  - **severity**: breaking

  - **file**: src/audian/databrowser.py
  - **line**: 8116
  - **api**: `QDialogButtonBox(QDialogButtonBox.Close | QDialogButtonBox.Save | QDialogButtonBox.Reset, dialog)` and `buttons.button(QDialogButtonBox.Reset)` (:8121-8122) — unscoped StandardButton enums, OR-ed
  - **qt6_replacement**: `QDialogButtonBox.StandardButton.Close | ... .Save | ... .Reset`; under PySide6 StandardButton is an enum.Flag so the `|` still works but the unscoped names are Qt5 spelling.
  - **severity**: breaking

  - **file**: src/audian/audian.py
  - **line**: 5033
  - **api**: `app.exec_()` — the loop the plugin-loaded factories run inside
  - **qt6_replacement**: `app.exec()`.
  - **severity**: breaking

  - **file**: src/audian/audian.py
  - **line**: 5041
  - **api**: `plugins.load_plugins()` runs at audian.py:5041, i.e. BEFORE `QApplication(...)` is constructed at audian.py:5015 (main() → audian_cli())
  - **qt6_replacement**: Ordering must be preserved: a plugin's module-level code executes with no QApplication, so it may not construct widgets, QFont, or (Qt6) query QFontDatabase. Document this explicitly; today it is an accident of call order, not a stated rule.
  - **severity**: behavior-change

  - **file**: songdetector.py
  - **line**: 8
  - **api**: `import matplotlib.pyplot as plt` at module scope with no backend selected; the default interactive backend is QtAgg, which binds to whichever Qt wrapper is importable
  - **qt6_replacement**: If this file is ever revived inside the app process it would pull a second Qt binding in. Standalone it is harmless. It contains no PyQt5 import — contrary to docs/qt6/01-plan.md:29, which lists songdetector.py as needing a PyQt5→PySide6 import rewrite; there is nothing there to rewrite.
  - **severity**: cosmetic

  - **file**: songdetector.py
  - **line**: 312
  - **api**: `plt.rcParams['keymap.all_axes'] = ''` — rcParam removed in matplotlib 3.5
  - **qt6_replacement**: Verified against the installed matplotlib 3.11.1: raises `KeyError: "'keymap.all_axes' is not a valid value for rcParam"`. The script cannot reach its first figure.
  - **severity**: breaking

  - **file**: songdetector.py
  - **line**: 316
  - **api**: `self.fig.canvas.set_window_title(...)` — deprecated in matplotlib 3.4, removed in 3.6
  - **qt6_replacement**: `self.fig.canvas.manager.set_window_title(...)`. Verified `hasattr(fig.canvas, 'set_window_title')` is False on matplotlib 3.11.1.
  - **severity**: breaking

- **architecture_problems**:
  - **title**: Plugin discovery is arbitrary code execution from the current working directory
  - **file**: src/audian/plugins.py
  - **line**: 38
  - **evidence**: load_plugins() does `sys.path.append(os.fspath(Path.cwd()))` (:40), `cwd.glob("audian*.py")` (:41), `importlib.import_module(module.stem)` (:42). No allowlist, no signature, no sandbox, no user plugin directory, no entry_points. Running `audian recording.wav` in a directory a colleague shared executes every `audian*.py` in it at import time. Tests do this too: tests/test_controlpanel.py:515 and tests/test_panelsplitter.py:125 call load_plugins() with cwd = wherever pytest was started.
  - **why_it_matters**: This is a field-science tool whose users run it inside downloaded data directories. It is also non-deterministic: glob order is filesystem order, so factory order — and therefore analysis-table column order — varies between machines.
  - **proposed_qt6_design**: Discover through `importlib.metadata.entry_points(group='audian.plugins')` for installed plugins, plus an explicit opt-in directory under `audian_dirs.user_config_path` (version.py:13 already has PlatformDirs) and an explicit `--plugin PATH` flag. Sort discovered entries by name for determinism. Keep cwd scanning only behind an explicit `--plugins-from-cwd` flag.
  - **effort**: medium

  - **title**: Zero error isolation: one bad plugin takes down startup, file-open, or the analysis run
  - **file**: src/audian/plugins.py
  - **line**: 42
  - **evidence**: `importlib.import_module` (:42) is unguarded — an ImportError in any cwd `audian*.py` kills main() before QApplication exists. `setup_traces` (:57-59) and `setup_analyzer` (:61-63) call factories bare inside DataBrowser.__init__ (databrowser.py:1173) and DataBrowser.open() (databrowser.py:1834) — a raising factory aborts opening the file. databrowser.py:8065-8066 calls `a.analyze(...)` in a bare loop between setOverrideCursor (:8059) and restoreOverrideCursor (:8067), so a raising analyzer leaves a permanent wait cursor and skips every analyzer after it.
  - **why_it_matters**: The whole point of a plugin architecture is that a third-party defect degrades one feature, not the application. Today a typo in a user's analyzer means audian will not open a file at all, with a traceback on stderr the GUI user never sees.
  - **proposed_qt6_design**: Wrap import, each factory call, and each analyze() call in try/except; on failure record the exception against the plugin name, disable that plugin for the session, and surface it through the existing status-bar notification path rather than stderr. Put restoreOverrideCursor in a `finally` (or use a context manager).
  - **effort**: small

  - **title**: `self.plugins` is keyed by a garbage name — the registry cannot identify what it loaded
  - **file**: src/audian/plugins.py
  - **line**: 53
  - **evidence**: `for k in dir(x): ...` (:44) then, outside the loop, `self.add_plugin(k, x)` (:53). `k` is the last name in `dir(x)` — alphabetically last module attribute, e.g. `np` or `sys` — not the module name and not a hook name. The print at :54 uses `module.stem`, so the log and the registry disagree.
  - **why_it_matters**: There is no way to list loaded plugins, disable one, report which one failed, or detect a name collision, because the dict key is meaningless. `self.plugins` is currently write-only: nothing in the tree ever reads it.
  - **proposed_qt6_design**: Key by `module.stem` and store a small record (module, source path, list of registered hook names, load status/exception) so a Plugins → About/Preferences view can list and toggle them.
  - **effort**: small

  - **title**: The plugin contract is a naming convention with no schema, no version, and no documentation outside a docstring
  - **file**: src/audian/plugins.py
  - **line**: 45
  - **evidence**: The entire contract is `k.startswith("audian_") and callable(...)` plus `k.endswith("traces")` / `k.endswith("analyzer")` (:45-50). A hook named `audian_my_analyzers` (plural) is silently ignored — `called` stays False, the module is imported for its side effects and then dropped, with no diagnostic. There is no example plugin in the tree (`find . -name 'audian*.py'` matches only src/audian/audian.py), README.md:203-205 documents only the three filenames, and songdetector.py — the file the brief expected to be the exemplar — is unrelated 2018 code.
  - **why_it_matters**: Migration cannot preserve a contract nobody has written down, and there is nothing to test the port against. The silent-ignore path makes plugin authoring a guessing game.
  - **proposed_qt6_design**: Define the contract as typed Protocols (`TraceFactory = Callable[[DataBrowser], None]`, `AnalyzerFactory = Callable[[DataBrowser], None]`) plus an explicit registration decorator/`register(plugins)` module function, carry an `AUDIAN_PLUGIN_API = 1` constant so mismatches are reported, warn on `audian_*` callables that match no suffix, and ship one worked example plugin under `examples/` that the test suite loads.
  - **effort**: medium

  - **title**: Analyzer registers itself as a side effect of its own constructor
  - **file**: src/audian/analyzer.py
  - **line**: 106
  - **evidence**: `Analyzer.__init__` ends with `self.browser.add_analyzer(self)`. Factory functions therefore read `MyAnalyzer(browser)` with the result discarded (cf. databrowser.py:1832-1833: `PlainAnalyzer(self)` / `StatisticsAnalyzer(self)` as bare statements). StatisticsAnalyzer then `return`s early from __init__ when its source is missing (statisticsanalyzer.py:11-14) — after registration — leaving a live, zero-column, half-constructed object in browser.analyzers.
  - **why_it_matters**: A constructor that mutates global state cannot be unit-tested, cannot fail cleanly, and cannot be re-ordered. Half-constructed objects survive in the list and must be defensively guarded at every use site (statisticsanalyzer.py:23 has to re-check `self.source is None`).
  - **proposed_qt6_design**: Make factories *return* analyzer instances and have `Plugins.setup_analyzer` collect and register them, so a raising constructor registers nothing. Alternatively give Analyzer a classmethod `create(browser) -> Optional[Analyzer]` returning None when its source is unavailable, and keep __init__ pure.
  - **effort**: medium

  - **title**: The analyzer's contract with the browser is unbounded reach-through into GUI internals
  - **file**: src/audian/analyzer.py
  - **line**: 221
  - **evidence**: Analyzers touch `self.browser.data.data.channels` (:221, :285, :313), `self.browser.data` as a mapping (:144, :159-161), `self.browser.panels[panel_name].axs` (:254-255) and `ax.add_item(spi)` (:261), `self.browser.add_to_panel_trace` (:227), `self.browser.add_analyzer` (:106). The docstring at analyzer.py:56-57 additionally invites plugins to open `a QDialog with self.browser as parent`.
  - **why_it_matters**: Every one of these is a public API a Qt6 rewrite of DataBrowser/Panels must keep bit-for-bit, or every out-of-tree plugin breaks. `browser.data.data` in particular is a three-level chain (Analyzer → DataBrowser → Data → thunderlab DataLoader) that the migration is otherwise free to restructure.
  - **proposed_qt6_design**: Interpose a narrow `AnalysisContext` facade handed to the analyzer instead of the raw browser: `channels`, `rate`, `trace_names()`, `trace(name)`, `add_marker_layer(...)`, `parent_widget` (for dialogs). Keep `self.browser` as a deprecated alias for one release. That gives the Qt6 rewrite of the widget tree freedom while pinning the plugin surface.
  - **effort**: large

  - **title**: The merged results table collides on duplicate column labels and is order-dependent
  - **file**: src/audian/databrowser.py
  - **line**: 8075
  - **evidence**: `get_analysis_table()` builds each row as a dict keyed by `a.data.label(c) + ('/'+unit if unit else '')` and calls `row.update({header: ...})` across all analyzers (:8080-8086). Two analyzers with a column both labelled e.g. `mean/mV` silently overwrite each other. Column order is `PlainAnalyzer` (databrowser.py:1832), `StatisticsAnalyzer` (:1833), then plugin factories in `analyzer_factories` order — which is glob order (plugins.py:41). The `setFormat` loop at :8110-8113 assumes a stable column index `c` that matches that dict's insertion order.
  - **why_it_matters**: Silent data loss in a scientific results table, and a CSV whose column order changes between machines.
  - **proposed_qt6_design**: Namespace headers by analyzer name (`statistics.mean/mV`), keep an explicit ordered column list built once at analyzer-registration time, and detect collisions at registration rather than at render.
  - **effort**: medium

  - **title**: save_analysis mutates the first analyzer's table in place, so a second save duplicates every column
  - **file**: src/audian/databrowser.py
  - **line**: 8151
  - **evidence**: `table = self.analyzers[0].data` is a live reference, then for every other analyzer `table.append(a.data.label(c), ...)` (:8152-8159) appends that analyzer's columns into PlainAnalyzer's TableData permanently. Saving twice appends them twice. Afterwards `PlainAnalyzer.store(t0, t1, t1-t0, channel)` (analyzer.py:348 → analyzer.py:194 `self.data.add(args, 0)`) writes 4 values into a table that now has 6+ columns, and `clear()` (analyzer.py:110) only calls `clear_data()`, which keeps the injected columns.
  - **why_it_matters**: A user who saves, analyses another region, and saves again gets a corrupted CSV. This is a plain correctness bug in the plugin cluster's only persistence path and has no test.
  - **proposed_qt6_design**: Build a fresh merged TableData in save_analysis from the ordered column list, never mutating any analyzer's own `data`.
  - **effort**: small

  - **title**: Trace-source resolution compares strings with `is`
  - **file**: src/audian/data.py
  - **line**: 309
  - **evidence**: `if self.traces[k] is not None and self.traces[k].source_name is sname:` in Data.setup_traces, called from databrowser.py:1174 immediately after `plugins.setup_traces(self)` at :1173. It works today only because the in-tree factories pass interned literals ('data', 'filtered').
  - **why_it_matters**: A plugin trace whose `source_name` is computed at runtime (f-string, config value, os.environ) fails the identity test, is left in the leftover list, and the user gets the stderr line `! ERROR: source "..." for trace "..." not found!` (data.py:319-321) with no GUI indication and a silently missing trace. This is exactly the failure mode a plugin author will hit first.
  - **proposed_qt6_design**: Use `==`. Then surface the leftover-trace error through the notification path instead of print().
  - **effort**: small

  - **title**: clear_trace_factories / clear_analyzer_factories are the only override mechanism and are destructive to peers
  - **file**: src/audian/plugins.py
  - **line**: 29
  - **evidence**: `clear_trace_factories` (:29-30) and `clear_analyzer_factories` (:35-36) replace the whole list. A plugin wanting to suppress `default_setup_traces` (:20, which hard-wires BufferedFilter + BufferedSpectrogram) must wipe every factory registered before it — and whether that includes another plugin's depends on glob order.
  - **why_it_matters**: There is no supported way to replace or reorder one factory, so plugins that want to customise the trace chain necessarily break each other.
  - **proposed_qt6_design**: Register factories under a name with an explicit priority; provide `remove_factory(name)` and `replace_factory(name, func)`. Move the two built-in traces out of plugins.py into a named built-in provider that a plugin can disable by name (which also breaks plugins.py's import dependency on bufferedfilter/bufferedspectrogram/theme).
  - **effort**: medium

  - **title**: One shared mutable Plugins instance is fanned out to every DataBrowser
  - **file**: src/audian/plugins.py
  - **line**: 61
  - **evidence**: A single Plugins() is built at audian.py:5040, stored on the window at audian.py:1497, and each browser stores the same object (databrowser.py:1170) and calls setup_traces (databrowser.py:1173) / setup_analyzer (databrowser.py:1834) on it. Any plugin that mutates factory lists during a factory call changes behaviour for every file opened afterwards; audian is multi-file/tabbed.
  - **why_it_matters**: Opening file A then file B can yield different trace sets, with no diagnostic. Also makes the registry impossible to snapshot for tests.
  - **proposed_qt6_design**: Freeze the factory lists after discovery (tuple), and pass an immutable view to browsers. Mutation belongs to a discovery/configuration phase, not to per-browser setup.
  - **effort**: small

  - **title**: Analyzers run synchronously on the GUI thread with an unbounded wait cursor
  - **file**: src/audian/databrowser.py
  - **line**: 8058
  - **evidence**: analyze_region: `QApplication.setOverrideCursor(Qt.WaitCursor)` (:8059), `self.data.get_region(t0, t1, channel)` (:8064) materialises every trace of the region into memory (data.py:282-298), then a bare loop over every analyzer (:8065-8066), then restoreOverrideCursor (:8067). No progress, no cancel, no time budget. Contrast BufferedData.request_update (buffereddata.py:231-252), which already debounces trace recomputes off the keystroke path.
  - **why_it_matters**: A plugin that does a real analysis (an FFT sweep over a 60 s 16-channel selection) freezes the whole application with no way out — on a machine in the field. Selecting a very long region also allocates the full region for every trace at once.
  - **proposed_qt6_design**: Run analyzers through QThreadPool/QRunnable with a results-ready signal, mirroring the sigUpdated pattern BufferedData already uses; keep a synchronous fast path for analyzers that declare themselves cheap. At minimum, add a modal-free progress indication and put the cursor restore in a finally.
  - **effort**: large

  - **title**: make_panel_events indexes panel plots by channel number
  - **file**: src/audian/analyzer.py
  - **line**: 253
  - **evidence**: make_panel_events builds `self.events[name]` with one ScatterPlotItem per `ax` in `panel.axs` (:255-261), but set_events/add_events index that list with `c in range(self.browser.data.data.channels)` (:285-289, :313-315). The two agree only for a panel whose axs are exactly one-per-channel.
  - **why_it_matters**: A plugin that adds a non-per-channel panel (which Panels supports — Panel.axs is just a list, panels.py:50) gets an IndexError from inside the framework, in the analyze() hot path.
  - **proposed_qt6_design**: Give the events registry an explicit indexing mode (per-channel vs per-panel-axis) recorded at make_*_events time, and have set_events dispatch on it rather than assuming channels.
  - **effort**: small

  - **title**: The whole cluster has no tests
  - **file**: src/audian/plugins.py
  - **line**: 38
  - **evidence**: `grep -rln 'Analyzer|analyze_region|analysis_table' tests/` returns nothing. The only test contact is `Plugins(); plugins.load_plugins()` in tests/test_controlpanel.py:514-515 and tests/test_panelsplitter.py:124-125, which discovers zero plugins because the repo root has no `audian*.py`. So the discovery loop, the hook-suffix dispatch, Analyzer registration, event markers, the results dialog, the merged table and the CSV writer are all untested.
  - **why_it_matters**: docs/qt6/01-plan.md commits to 'every commit leaves a runnable application' gated on pytest. That gate is blind to this entire cluster: the migration could silently break every plugin and every green run would still be green.
  - **proposed_qt6_design**: Add a tmp_path-based discovery test (write an audian_demo.py, chdir, assert both factory kinds registered and that a malformed hook is reported not raised), a headless Analyzer test over a synthetic browser double covering make_column/store/clear/set_events, and one test that runs analyze_region end to end and asserts the merged column order and the saved CSV — including the save-twice case.
  - **effort**: medium

  - **title**: songdetector.py is unrunnable dead code being carried into the migration plan
  - **file**: songdetector.py
  - **line**: 1
  - **evidence**: Verified on the installed matplotlib 3.11.1: line 312 `plt.rcParams['keymap.all_axes']` raises KeyError, and line 316 `fig.canvas.set_window_title` does not exist. Beyond that: `refine_detection(...)` is called at :619 and :626 but is never defined anywhere in the file (AST top-level defs confirm), `self.envelopeusefreq` used at :619/:626 is never assigned, `self.highpassfreq * 1.5` (:600) and `self.lowpassfreq * 1.5` (:610) are no-op expressions where an assignment was meant, `etime` at :412 and :429 reads a leaked loop variable `c` from a previous loop, `SignalPlot.__del__` (:363-367) is the same finaliser antipattern docs/qt6/01-plan.md is deleting from DataBrowser, and `matplotlib.widgets`/`matplotlib.colors`/`scipy.stats`/`highpass_filter` are unused. It also imports `audioio` (:14), which is not declared in pyproject.toml dependencies. docs/qt6/01-plan.md:29 schedules it for a `PyQt5.* -> PySide6.*` rewrite; it contains no Qt import at all.
  - **why_it_matters**: It is neither a plugin nor an exemplar of the plugin contract, so it teaches the migration nothing, and the plan currently allocates work to it that does not exist. Keeping it in the repo root also makes it look like a supported entry point.
  - **proposed_qt6_design**: Take it off the Qt6 work list (correct docs/qt6/01-plan.md:29). Either delete it, or move it to `archive/` with a header saying it is pre-2019 matplotlib code that does not run — and, if its detection algorithm is wanted, port `threshold_estimates`/`detect_songs`/`env_freqs`/`clean_env_freqs`/`analyse_songs` (all pure numpy/thunderlab, no GUI) into a real `SongAnalyzer(Analyzer)` as the missing worked example the plugin contract lacks.
  - **effort**: medium

  - **title**: sys.path is mutated by index, not by value
  - **file**: src/audian/plugins.py
  - **line**: 55
  - **evidence**: `sys.path.append(os.fspath(cwd))` at :40, `sys.path.pop()` at :55. If any imported plugin appends to sys.path at module scope (common in ad-hoc scientific code), the pop removes the plugin's entry and leaves cwd on the path permanently.
  - **why_it_matters**: cwd left on sys.path for the process lifetime means any later import can be shadowed by a file in the recording directory.
  - **proposed_qt6_design**: Use importlib.util.spec_from_file_location / module_from_spec to load the plugin file directly, never touching sys.path. This also removes the cwd-`audian.py` shadowing hazard and makes the loaded module name explicit.
  - **effort**: small

  - **title**: The dead-plugin branch: analyze_region can never be auto-disabled
  - **file**: src/audian/databrowser.py
  - **line**: 1835
  - **evidence**: `if len(self.analyzers) == 0: self.acts.analyze_region.setEnabled(False); setVisible(False)` at :1835-1837, but PlainAnalyzer (:1832) unconditionally registers one and StatisticsAnalyzer (:1833) registers even when inert (statisticsanalyzer.py:11-14). The list is never empty. The dependent guards at databrowser.py:7797-7798 therefore always read enabled/visible.
  - **why_it_matters**: Dead code that documents an intent the code does not implement; the migration should not preserve it as-is.
  - **proposed_qt6_design**: Either drop the branch, or change the test to 'no analyzer contributes a column or a marker layer' so an all-inert set really does hide the action.
  - **effort**: small

- **behavior_contract**:
  - Discovery: launching audian from a directory containing files matching `audian*.py`
  - imports each of them and prints one `loaded audian plugins from <stem>` line per module
  - that registered at least one hook (plugins.py:41-54). A module with no matching hook is
  - imported but produces no line. Discovery happens once per process, in main(), before the
  - QApplication exists (audian.py:5040-5041 vs 5015).
  - Hook naming: a module-level callable whose name starts with `audian_` and ends with
  - `traces` becomes a trace factory; one ending with `analyzer` becomes an analyzer factory
  - (plugins.py:45-51). Both are called with the DataBrowser as their single positional
  - argument and their return value is ignored.
  - Trace factories run once per DataBrowser at construction, before Data.setup_traces()
  - orders the chain (databrowser.py:1173-1174). The built-in `default_setup_traces` always
  - runs first and always adds a `filtered` BufferedFilter and a `spectrogram`
  - BufferedSpectrogram (plugins.py:11-13, 20) unless a plugin calls
  - clear_trace_factories().
  - Analyzer factories run once per DataBrowser inside open(), after all plots exist, and
  - always after PlainAnalyzer and StatisticsAnalyzer have been constructed in that order
  - (databrowser.py:1832-1834). Analysis-table column order is therefore: tstart, tend,
  - duration, channel, then `<source> mean`, `<source> stdev`, then plugin columns.
  - Constructing any Analyzer subclass registers it with the browser; nothing else is
  - required of the factory (analyzer.py:106).
  - An analyzer whose named source trace is not installed gets `self.source is None` from
  - Analyzer.__init__ (analyzer.py:103, 159-162) rather than an exception, and
  - StatisticsAnalyzer specifically stays registered but contributes no columns and does
  - nothing (statisticsanalyzer.py:11-14, 23).
  - Triggering an analysis: Alt+drag a region, or press `a` / pick Analyze in the toolbar to
  - enter analyze mode and drag, or drag in ask mode and choose &Analyze from the context
  - menu (databrowser.py:7781, 7796-7798, 7821-7822; audian.py:3009-3016, 2923). Region
  - bounds are clamped to [0, frames/rate] (databrowser.py:8060-8063).
  - During the run the cursor is the wait cursor and the GUI is blocked
  - (databrowser.py:8059, 8067).
  - Every registered analyzer's analyze(t0, t1, channel, traces) is called, in registration
  - order, with `traces` a dict keyed by trace name; each value is `(time, data)` for
  - ordinary traces and `(time, freqs, data)` for a BufferedSpectrogram, cut to the region
  - on the single selected channel (data.py:282-298).
  - The first analysis of a session pops a non-modal, delete-on-close 'Audian analysis
  - table' dialog; subsequent analyses append a row to the already-open table instead of
  - opening a second dialog (databrowser.py:8068-8073, 8091-8093). Closing the dialog resets
  - that state so the next analysis reopens it (databrowser.py:8124).
  - The results table is monospaced with aligned digits, is at least 360 px tall, and has
  - small-font column and row headers (analyzer.py:16-28). Per-column numeric formats come
  - from each analyzer's make_column() format strings (databrowser.py:8110-8113).
  - Column headers read `label` or `label/unit` when a unit was given
  - (databrowser.py:8083-8085). PlainAnalyzer's time columns carry a decimal precision
  - derived from the sample rate (analyzer.py:338-343).
  - The dialog's Reset button clears every analyzer's rows and the displayed table but keeps
  - the columns (databrowser.py:8121, 8128-8133 → analyzer.py:108-113); Reset also clears
  - any event markers those analyzers drew.
  - The dialog's Save button opens a Save-As dialog defaulting to `<recording
  - stem>-analysis.csv` in the last-used save directory, and writes a semicolon-delimited
  - CSV with units in the header (databrowser.py:8122, 8134-8167). The chosen directory
  - becomes the new default.
  - Event markers: an analyzer that called make_trace_events() draws pyqtgraph scatter
  - symbols of the requested symbol/colour/size on the panel that owns the named trace
  - (analyzer.py:196-227); make_panel_events() draws them into a named panel
  - (analyzer.py:229-261). set_events replaces the markers on non-matching channels with
  - nothing and sets them on the selected channel; `channel < 0` means all channels
  - (analyzer.py:263-289). add_events appends without erasing (analyzer.py:291-315).
  - An analyzer may open its own window with `self.browser` as parent — the docstring
  - promises this (analyzer.py:56-57) and it is part of the contract.
  - With no plugins installed at all, the application still shows the filtered trace and the
  - spectrogram, and Analyze still works and produces the four PlainAnalyzer columns plus
  - the two StatisticsAnalyzer columns.
  - songdetector.py has no behaviour to preserve: it raises before drawing its first figure
  - on any matplotlib >= 3.5.
- **risk**: medium — the direct Qt5 surface in these four files is almost nil (one pg.ScatterPlotItem, one pg.TableWidget styling helper, and zero Qt imports in plugins.py / statisticsanalyzer.py / songdetector.py), so the mechanical port is nearly free; the risk is entirely that this is a published extension point with no tests, no written contract and no error isolation, and that any out-of-tree plugin subclassing BufferedData is hard-broken by the PyQt5→PySide6 switch (buffereddata.py:8) with a mixed-binding crash rather than a clean ImportError.
- **notes**: Correction to the brief and to the existing plan: songdetector.py is NOT a plugin consumer and does not encode the de-facto plugin contract. It has no `audian` import, no `audian_*` hook, no Qt import, and nothing in src/, pyproject.toml, or tests/ references it (`grep -n audian songdetector.py` → only a shell-command comment at :776). It is a self-contained 2018 matplotlib CLI. docs/qt6/01-plan.md:29 lists it under "`PyQt5.*` -> `PySide6.*` across `src/`, `tests/`, `scripts/`, `songdetector.py`" — that line should be corrected; there is no PyQt5 in it. It is also broken independent of Qt: it raises KeyError at songdetector.py:312 on matplotlib >= 3.5 (verified against the installed 3.11.1) and calls a never-defined `refine_detection` at :619/:626.  Consequence for the migration: there is NO example of a real out-of-tree plugin anywhere in the repository. The only implementations of the contract are the two in-tree analyzers (analyzer.py:318 PlainAnalyzer, statisticsanalyzer.py:6 StatisticsAnalyzer) and the one in-tree trace factory (plugins.py:11). The contract's real constraints must be read off `Analyzer`'s docstring (analyzer.py:31-97) and the discovery loop (plugins.py:38-55), and the migration should treat writing an example plugin plus its test as part of the work, not as follow-up.  Cross-cluster dependencies the plugin surface pins, which other migration workstreams must not silently change: `DataBrowser.add_trace / remove_trace / clear_traces / add_analyzer / remove_analyzer / clear_analyzer / get_analyzer / add_to_panel_trace / get_trace` (databrowser.py:1507-1537), `DataBrowser.data` (a Data mapping) and `DataBrowser.panels` (a Panels dict), `Data.get_region`'s 2-tuple/3-tuple return shape (data.py:282-298), `Panel.axs` and `PlotItem.add_item` (panels.py:50, 181-186), and `BufferedData.__init__`'s keyword signature (buffereddata.py:29-40) — trace plugins subclass it directly.  Sequencing recommendation: this cluster is cheap to port (Stage A here is: fix theme.py:677 QFontDatabase, done) but should get its tests written BEFORE any DataBrowser restructuring, because there is currently no gate at all on the extension point — a Qt6 rewrite of DataBrowser could delete `add_to_panel_trace` or change `get_region`'s tuple shape and the full 792-test suite would still pass.
