# Recon: test-inventory

> **This file is not a test inventory.**  Its body is `dependency-graph.md`
> with a different first line -- an intra-package import and layering audit.
> There is no test inventory in `docs/qt6/`.  To see what gates your work, read
> `tests/` directly: `test_shutdown.py` covers the exit path,
> `test_thread_boundary.py` the data/GUI split, `test_responsiveness.py` the
> event loop under load, and `test_panelsplitter.py` the lane layout.

# audian intra-package dependency graph & layering audit

Package root: `/home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/` — 35 modules, 32,364 LOC.

## 1. Adjacency list (module-level imports, `file:line` of the import)

```
__init__            -> audian(6), version(7)
activity            -> (none)
alignment           -> (none)
analyzer            -> theme(13)
audian              -> theme(33), version(34), databrowser(35), eventoverlay(36),
                       fulltraceplot(37), plugins(38), panels(39)
buffereddata        -> theme(10)
bufferedenvelope    -> theme(7), buffereddata(8)
bufferedfilter      -> theme(5), buffereddata(6)
bufferedspectrogram -> buffereddata(7)
compresseddata      -> activity(18), version(24)
controlpanel        -> windowing(69), theme(69), eventoverlay(70), layers(71)
data                -> theme(14), buffereddata(15), bufferedspectrogram(16)
databrowser         -> theme(36), data(37), panels(38), panelsplitter(39), plotranges(40),
                       bufferedspectrogram(41), fulltraceplot(42), selectviewbox(50),
                       timeaxisitem(51), timeplot(52), spectrogramplot(53), labels(54,66),
                       labeloverlay(67), eventoverlay(74), controlpanel(89), alignment(90),
                       layers(91), analyzer(99), statisticsanalyzer(100)
eventoverlay        -> theme(81), windowing(81), layers(82), session(89)
fulltraceplot       -> theme(30), activity(30), compresseddata(31), timeaxisitem(32)
labeloverlay        -> theme(127), eventoverlay(128), labels(134)
labels              -> (none)
layers              -> (none)
panels              -> theme(10), specitem(11), traceitem(12)
panelsplitter       -> theme(31)
plotranges          -> panels(12)
plugins             -> bufferedfilter(7), bufferedspectrogram(8)
rangeplot           -> theme(5), selectviewbox(6)
selectviewbox       -> theme(11)
session             -> windowing(61), alignment(61,62), layers(82)
specitem            -> bufferedspectrogram(9)
spectrogramplot     -> theme(14), bufferedspectrogram(15), panels(16), rangeplot(17),
                       specitem(18), timeplot(19)
statisticsanalyzer  -> analyzer(3)
theme               -> (none)
timeaxisitem        -> theme(8)
timeplot            -> theme(11), panels(12), rangeplot(13), timeaxisitem(14), yaxisitem(15)
traceitem           -> theme(8)
version             -> (none)
windowing           -> (none)
yaxisitem           -> theme(9)
```

## 2. Cycles

**Static (module-level) graph is acyclic.** Tarjan SCC over module-level edges returns `[]`.

**One cycle exists, broken only by deferred import:** `audian ↔ databrowser`.
- `audian.py:35` `from .databrowser import DataBrowser` (module level)
- `databrowser.py` imports back at **10 in-function sites**, all for the same two symbols:
  - `from .audian import settings` — `databrowser.py:3481, 4966, 5513, 6545, 6592`
  - `from .audian import save_setting` — `databrowser.py:3516, 5048, 5577, 6654, 6683`

Root cause is a placement error, not a real dependency: `settings()`/`save_setting()`/`settings_path()` are a 30-line JSON preference store (`audian.py:909-941`) that reads `audian_dirs.user_config_path`. They have zero coupling to the `Audian` QMainWindow. Extracting them to a `settings.py` (which would import only `json`, `pathlib`, `version`) removes the cycle and 10 deferred imports outright.

Note the package has **two competing persistence mechanisms**: the JSON store above, and `QSettings("audian","audian")` used at `databrowser.py:1448, 7172`.

**No other deferred intra-package imports exist.** The only other in-function import of any kind is `theme.py:1513` (`from PyQt5.QtWidgets import QWidget` inside `restyle_tree`) and `alignment.py:787` (`import soundfile`, a lazy optional probe).

## 3. Layering violations

### 3a. `theme` is a presentation-token module that pulls in QtWidgets, and the data layer imports it

`theme.py:78-93` imports `pyqtgraph`, `QtCore.Qt`, 7 `QtGui` classes, and `QtWidgets.{QApplication, QStyleFactory, QWidget, QWidgetAction}`. It has **20 inbound edges** — every module except the 7 leaf-pure ones.

The violation is that four data-layer modules import it **for scalars only**:
- `buffereddata.py:10` → uses `theme.trace_color(name)` (`:49`), `theme.LW_THIN` (`:50`), `theme.LW_THICK` (`:51`)
- `bufferedfilter.py:5` → `theme.trace_color("filtered")` (`:26`)
- `bufferedenvelope.py:7` → `theme.trace_color("envelope")` (`:27`)
- `data.py:14` → `theme.trace_color("raw")` (`:394`), `theme.LW_THIN/LW_THICK` (`:400-401`)

`theme.trace_color()` returns a **hex string** (`theme.py:845-852`); `LW_THIN=1.0`, `LW_THICK=1.8` (`theme.py:615-616`). No Qt object crosses the boundary. These four modules import a QtWidgets-loading module to obtain four constants and one dict lookup.

### 3b. `buffereddata` — the data buffer base class — imports QtCore directly

`buffereddata.py:8`: `from PyQt5.QtCore import QObject, QTimer, pyqtSignal as Signal`, feeding a `_Notifier(QObject)` at `:13-21` that exists purely to carry `sigUpdated = Signal(object)`. The docstring at `:15-19` states it cannot inherit `QObject` directly because of sip metaclass conflict with `audioio.BufferedArray`. This is the one place where a Qt event-loop dependency is genuinely embedded in the buffering layer; a plain callback list or a `weakref` observer would decouple it entirely.

### 3c. Transitive Qt closure

Only **8 of 35 modules** are free of Qt and pyqtgraph transitively:
`activity, alignment, compresseddata, labels, layers, session, version, windowing`.

Modules that are **directly** Qt-clean but transitively contaminated (i.e. would become clean with the `theme` split in 3a plus 3b):

| module | contaminated via |
|---|---|
| `data` | buffereddata, theme |
| `bufferedspectrogram` | buffereddata, theme |
| `bufferedfilter`, `bufferedenvelope` | buffereddata, theme |
| `plugins` | bufferedfilter → buffereddata → theme |
| `plotranges` (815 LOC, zero Qt/pg of its own) | `plotranges.py:12` `from .panels import Panel` → theme, specitem, traceitem |
| `statisticsanalyzer` | analyzer → theme |
| `specitem` | buffereddata, theme |

`plotranges.py` is the cleanest extraction candidate in the tree: pure axis-range arithmetic (`numpy`, `math`, `functools`) whose only package edge is one `Panel` type reference.

### 3d. Does anything low-level import `databrowser` or `audian`?

**No module-level import** of `databrowser` exists outside `audian.py:35`, and none of `audian` outside `__init__.py:6` and the 10 deferred sites in `databrowser`.

But there is a **pervasive inverted runtime dependency**: low-level plot items take a `browser` object in their constructor and call/connect to `DataBrowser` methods duck-typed, without importing it:
- `rangeplot.py:10` `def __init__(self, aspec, channel, browser, ...)`; `:46` `view.browser = browser`; `:47` `self.sigRangeChanged.connect(browser.update_ranges)`; `:52,54` connect to `browser.region_menu_at` / `browser.region_menu`
- `selectviewbox.py:48-56` reaches `browser.gui` and writes `browser.region_mode_override`
- `analyzer.py:100-105` stores `self.browser` and calls `self.browser.add_analyzer(self)` — 20 `browser` references in the file
- `timeplot.py` (16 refs), `spectrogramplot.py` (17), `panelsplitter.py` (9), `eventoverlay.py` (5), `fulltraceplot.py` (2), `labeloverlay.py` (2), `controlpanel.py` (3)

The import graph is acyclic only because this back-edge is untyped. Any attempt to add type annotations for `browser` will reintroduce cycles (and will need `TYPE_CHECKING` guards).

## 4. De-facto core (inbound)

| module | inbound | importers |
|---|---|---|
| **theme** | **20** | everything except activity, alignment, compresseddata, labels, layers, plotranges, plugins, session, specitem, statisticsanalyzer, version, windowing, bufferedspectrogram |
| bufferedspectrogram | 5 | data, databrowser, plugins, specitem, spectrogramplot |
| panels | 5 | audian, databrowser, plotranges, spectrogramplot, timeplot |
| buffereddata | 4 | bufferedenvelope, bufferedfilter, bufferedspectrogram, data |
| eventoverlay | 4 | audian, controlpanel, databrowser, labeloverlay |
| layers | 4 | controlpanel, databrowser, eventoverlay, session |
| timeaxisitem / version / windowing | 3 | — |

`theme`'s API surface is also enormous: **433 entries in `__all__`**, **113 distinct `theme.X` symbols** referenced package-wide. Fan-out per consumer: databrowser 44 distinct, audian 32, fulltraceplot 26, labeloverlay 20, eventoverlay 20, controlpanel 16 — but data-layer consumers use 1–3 (§3a). It is at least three modules fused: color tokens (pure data), Qt font/pen/brush factories, and widget-tree restyling (`restyle_tree`, `tint`, `frame`).

## 5. De-facto god objects (outbound)

| module | LOC | outbound intra-pkg | Qt symbols imported | pg symbols |
|---|---|---|---|---|
| **databrowser** | **8253** | **19** | 38 (25 QtWidgets, 4 QtGui, 9 QtCore) | 10 distinct |
| **audian** | **5051** | 7 | **48 (33 QtWidgets, 7 QtGui, 8 QtCore)** | 1 (`pg.ViewBox`) |
| theme | 3304 | 0 | 12 | 12 distinct |
| spectrogramplot | 647 | 6 | 3 | 5 |
| timeplot | 448 | 5 | 2 | 2 |
| controlpanel / eventoverlay / fulltraceplot | — | 4 each | 2 / 8 / 9 | 5 / 7 / 7 |

`databrowser.py` alone is 25% of the package, holds 10 of the 17 `Signal` declarations (`:1127-1136`, `:445`), and imports 19 of the other 34 modules. It and `audian.py` together carry 86 of the 148 Qt symbol imports.

## 6. pyqtgraph coupling

18 modules import `pyqtgraph as pg`. Distinct API surface used package-wide (33 symbols, occurrence counts):

```
TextItem 17, PlotCurveItem 11, ROI 10, PlotItem 10, InfiniteLine 10, ViewBox 8,
SpinBox 8, ScatterPlotItem 7, Point 7, functions 7, GraphicsLayoutWidget 6, AxisItem 6,
colormap 5, SignalProxy 4, ColorMap 4, RectROI 3, LinearRegionItem 3, ImageItem 3,
GraphicsObject 3, ColorBarItem 3, TableWidget 2, PlotDataItem 2, mkBrush 2, GraphicsWidget 2,
siFormat 1, setConfigOptions 1, setConfigOption 1, MouseDragHandler 1(docstring only),
mkPen 1, mkColor 1, GraphicsScene 1, FillBetweenItem 1
```

**Swapping pyqtgraph is not feasible without rewriting the plotting half of the app.** 13 classes subclass pyqtgraph types directly:

```
traceitem.py:23      TraceItem(pg.PlotDataItem)
specitem.py:12       SpecItem(pg.ImageItem)
selectviewbox.py:14  SelectViewBox(pg.ViewBox)
rangeplot.py:9       RangePlot(pg.PlotItem)
timeaxisitem.py:11   TimeAxisItem(pg.AxisItem)
yaxisitem.py:12      YAxisItem(pg.AxisItem)
panelsplitter.py:41  PanelSplitter(pg.GraphicsWidget)
labeloverlay.py:219  LabelEditor(pg.ROI)
fulltraceplot.py:105 EnvelopeItem(pg.GraphicsObject)
fulltraceplot.py:201 ActivityItem(pg.GraphicsObject)
fulltraceplot.py:309 NavigatorRegion(pg.LinearRegionItem)
fulltraceplot.py:360 FullTracePlot(pg.GraphicsLayoutWidget)
databrowser.py:976   (anonymous AxisItem subclass overriding tickStrings)
```

Depth of coupling, worst first:

1. **`selectviewbox.py` is a hand-fork of `pg.ViewBox` internals.** `wheelEvent` (`:100-106`) and `mouseDragEvent` (`:124-195`) read `self.state["mouseEnabled"]`, `self.state["wheelScaleFactor"]`, `self.state["mouseMode"]`, `self.state["aspectLocked"]`, manipulate `self.childGroup.transform()` / `mapRectFromParent`, and call `pg.functions.invertQTransform`. `selectviewbox.py:104` is byte-identical in structure to upstream `pyqtgraph/graphicsItems/ViewBox/ViewBox.py:1309`. This file breaks on any pyqtgraph internal refactor, not just on a binding change.
2. **`yaxisitem.py`** overrides `updateAutoSIPrefix` (`:131`) reimplementing upstream logic with `pg.functions.siScale(value, power=self.unitPower)` (`:144`) and `tickSpacing` (`:160`); its `mouseClickEvent` docstring (`:73`) reasons about `pg.GraphicsScene` click-vs-drag dispatch internals.
3. **`labeloverlay.py`** — `LabelEditor(pg.ROI)` depends on documented-but-private `pg.ROI` behavior: `translatable` gating in `ROI.hoverEvent`/`MouseDragHandler` (`:58-59, 252`), `ROI.movePoint` ignoring the movable flags (`:274`), `ROI.translate` writing `state['pos']` (`:340`), `invertible=False` default (`:75`), z-order default of 10 (`:165`), `addScaleHandle` (`:314-318`).
4. **`timeaxisitem.py`** overrides `tickSpacing` (`:79`) and `tickStrings` (`:242`).
5. `fulltraceplot.py`, `panelsplitter.py` implement `paint`/`boundingRect`/`shape` against `QPainter`/`QPainterPath` on `pg.GraphicsObject`/`pg.GraphicsWidget`.
6. Widespread `getViewBox()` / `getAxis()` / `.scene()` traversal in `databrowser.py` (17 sites), `fulltraceplot.py` (11), `eventoverlay.py` (4), `controlpanel.py` (5), `labeloverlay.py` (3), `plotranges.py:83`, `panels.py:151`.

No `from pyqtgraph.x import y` submodule imports and no monkeypatching of pg classes — the coupling is entirely via `pg.*` and inheritance.

## 7. External dependency usage

| dep | declared in pyproject | used | where | load-bearing |
|---|---|---|---|---|
| **matplotlib** | yes | **NO — stale** | zero hits in `src/audian/`. Only `songdetector.py:8-11` at repo root, a standalone script not in the package (`[tool.setuptools]` ships `src/`, script is outside). Also pulled transitively by `thunderlab` (`Requires-Dist: matplotlib`). | **Remove from `[project.dependencies]`** — it is not an audian dependency. |
| **pandas** | yes | **NO — stale** | zero hits anywhere in `src/`, `tests/`, `scripts/`, `songdetector.py`. | **Remove.** |
| **numba** | yes | effectively no | `traceitem.py:326` `from numba import njit`, but **inside `if __name__ == "__main__":`** (`:325`) — a decimation micro-benchmark (`:344-433`). Never executed by the app. Also a `thunderlab` transitive dep. | Demote to a dev/bench extra or drop. |
| **sounddevice** | yes | **indirect only** | zero direct imports. Consumed by `audioio.playaudio.open_sounddevice` (`audioio/playaudio.py:681`) which `audian.py:31` reaches via `PlayAudio`. | Load-bearing at runtime (audio output backend), but the dependency is on `audioio`'s behalf. Keep, and comment why. |
| **soundfile** | yes | **lazy, 2 sites** | `data.py:34` `import soundfile as sf` (inside a function, header probe); `alignment.py:787` `import soundfile` (optional duration probe, guarded). Also `audioio`'s file backend. | Low direct load. |
| **scipy** | yes | **yes, 3 modules** | `bufferedfilter.py:3` (`butter`, `sosfilt` → `:57,66,74,82`); `bufferedenvelope.py:5` (`butter`, `sosfiltfilt` → `:56,65,73`); `databrowser.py:12` (`butter`, `sosfiltfilt` → `:8025,8029`, playback anti-alias). | Load-bearing, narrow API (`scipy.signal.butter/sosfilt/sosfiltfilt` only). |
| **polars** | yes | **yes, 2 modules** | `session.py:59` — 78 `pl.*` references, schema definitions at `:200-222`, layer construction "one polars pass each" (`:771`); `layers.py:39` — 3 refs. | Load-bearing and central to the session/annotation domain. Both modules are Qt-free (`session.py:8` explicitly documents "No Qt"). |
| **platformdirs** | yes | **yes, 1 definition, 2 consumers** | `version.py:1,13` `audian_dirs = PlatformDirs("audian","janscience")`; consumed by `compresseddata.py:421-535` (cache) and `audian.py:565-937` (cache + `settings.json`). | Load-bearing, single point of contact. |
| **thunderlab** | yes (`>=1.6.0`) | **yes, 6 modules** | `analyzer.py:11` `TableData` (→ `:104`); `bufferedspectrogram.py:5` `decibel, spectrogram` (→ `:107,204,235`); `spectrogramplot.py:12` `decibel`; `specitem.py:7` `decibel`; `compresseddata.py:22` `DataLoader` (→ `:54,56,630`); `data.py:12` `DataLoader`; `databrowser.py:34` `datawriter.available_formats, write_data`. | Load-bearing. Small, stable API surface (`DataLoader`, `TableData`, `decibel`, `spectrogram`, `datawriter`). Also the sole source of the `matplotlib`/`numba`/`scikit-learn` transitive weight. |
| **audioio** | **NOT declared** | **yes, 5 modules, 10 imports** | `buffereddata.py:6` `BufferedArray` (base class of the whole data layer); `compresseddata.py:19-21`; `audian.py:30-31` `PlayAudio, AudioLoader, available_formats, parse_load_kwargs`; `data.py:11` `get_datetime`; `databrowser.py:31-33` `fade, update_starttime, bext_history_str, add_history`. | **Undeclared direct dependency**, satisfied only transitively (`thunderlab` → `audioio>=2.6`). Add to `[project.dependencies]`. |
| **Pillow (PIL)** | **NOT declared** | **yes, 1 module** | `audian.py:28-29` `from PIL import Image`, `from PIL.PngImagePlugin import PngInfo`; used at `:2761, 2786, 2832` (screenshot PNG metadata). | **Undeclared direct dependency**, satisfied transitively via matplotlib. If matplotlib is dropped per above, **this breaks** — add `Pillow` explicitly. |
| numpy | yes | 24 modules | ubiquitous | core |

## 8. PySide6 vs pyqtgraph 0.14.0 — verified empirically

Two venvs exist in the worktree: `.venv` (PyQt5 5.15.11 + pyqtgraph 0.14.0) and `.venv-qt6` (PySide6 6.11.2 + pyqtgraph 0.14.0, no PyQt at all).

**No conflict. pyqtgraph 0.14.0 fully supports PySide6.** Verified by running in `.venv-qt6`:
```
pyqtgraph 0.14.0 / QT_LIB PySide6 / QtVersion 6.11.2 / "PySide6 6.11.2 Qt 6.11.2"
```

Binding detection, `pyqtgraph/Qt/__init__.py`:
- `:24-33` honors `PYQTGRAPH_QT_LIB` env var (hard-fails if the named module is missing).
- `:36-43` if any binding is already in `sys.modules`, it wins — **order `[PyQt6, PySide6, PyQt5, PySide2]`**. Consequence: importing `PySide6` before `pyqtgraph` pins it; but if a stray `PyQt6` is installed and imported first, it wins over PySide6. Set `PYQTGRAPH_QT_LIB=PySide6` explicitly during the migration to remove ambiguity, especially since `.venv` still has PyQt5.
- `:45-53` otherwise probes `<lib>.QtCore` in the same order.
- `:228-251` PySide6 branch: `_copy_attrs` from `PySide6.QtCore/QtGui/QtWidgets`, plus optional `QtSvg`, `QtOpenGLWidgets`, `QtTest`.
- `:258-266` Qt6 branch installs `QtWidgets.QOpenGLWidget` and works around PySide6 misplacing `QFileSystemModel`.
- `:286-300` PySide branch sets `isQObjectAlive = shiboken.isValid`, `compat.wrapinstance = shiboken.wrapInstance`, adds a `QTest.qWait` shim.
- `:303-323` — **note**: the `sys.excepthook` override and the `QtCore.Signal = QtCore.pyqtSignal` alias are installed **only on the PyQt branch**. Under PySide6 pyqtgraph does not touch `excepthook`; an exception in a slot behaves per PySide6 semantics, not the PyQt5 semantics the codebase has been developed against.
- `:344` upstream comment: *"subclassing QApplication causes segfaults on PySide{2,6} / Python 3.8.7+"* — **audian does not subclass `QApplication`** (grepped; zero hits), so this does not apply.
- `:368-380` `mkQApp` skips the Qt5 HiDPI attribute dance on Qt6 and forces `setStyle("fusion")`. `theme.py` calls `pg.setConfigOptions`/`setConfigOption` and `QStyleFactory` — check for interaction with that forced Fusion style.

Runtime smoke test in `.venv-qt6` confirmed every pyqtgraph symbol audian uses is present and constructible under PySide6: `SpinBox`, `TableWidget`, `ColorBarItem`, `FillBetweenItem`, `LinearRegionItem`, `RectROI`/`ROI` (`translatable` True), `ScatterPlotItem`, `TextItem`, `PlotCurveItem`, `ImageItem`, `GraphicsLayoutWidget`, `PlotItem`, `AxisItem`, `ViewBox`, `InfiniteLine`, `SignalProxy`, `GraphicsObject`, `GraphicsWidget`, `GraphicsScene`, `PlotDataItem`, `Point`, `ColorMap`, `functions.invertQTransform`, `functions.siScale`. Full `ImageItem.setImage → render → grab` path succeeds (`Format_Indexed8`).

**One exception:** `pg.MouseDragHandler` **does not exist** as a top-level attribute in 0.14.0 under any binding — but the two references (`labeloverlay.py:59, 252`) are docstring prose, not code. No runtime break; it does document a behavioral dependency on `ViewBox`-internal drag dispatch.

Two PySide-specific paths inside pyqtgraph 0.14.0 worth knowing:
- `pyqtgraph/Qt/internals.py:232-250` `qbytearray_leaks()` — guards pyqtgraph issue #3265 / PYSIDE-3031 (memoryview on `QByteArray` leaks when PySide is built without `Py_LIMITED_API`). Probed on the installed 6.11.2: **returns False** (no leak; official Qt wheels use the limited API). Consumed at `functions.py:2116` in `arrayToQPath` — the trace-drawing hot path.
- `internals.py:126-136` `PrimitiveArray` — PySide gets the fast `use_ptr_to_array` path when `pyside_version_info >= (6,4,3)`. 6.11.2 qualifies, so `ScatterPlotItem`/`drawPixmapFragments` (the annotation-marker hot path) has no PySide performance penalty.

### Real Qt5→Qt6 API breaks in audian's own code (not pyqtgraph's problem)

| break | sites | note |
|---|---|---|
| **`QVariant` removed from PySide6** | `labeloverlay.py:107` (import), `:894, 898, 914, 1107, 1111, 1131` (7 `return QVariant()`) | Verified `hasattr(PySide6.QtCore,'QVariant') == False`. Replace with `None` returns from `data()`/`headerData()`. |
| **`QAction`/`QActionGroup` moved `QtWidgets`→`QtGui`** | `audian.py:21`, `databrowser.py:27` | Verified: absent from PySide6 `QtWidgets`, present in `QtGui`. |
| **`app.exec_()` → `app.exec()`** | `audian.py:5033` | Only occurrence. |
| **Dead `Signal` shims** | `databrowser.py:14-17`, `eventoverlay.py:76-79`, `selectviewbox.py:4-7`, `spectrogramplot.py:8-11`, `timeplot.py:6-9` | `try: from PyQt5.QtCore import Signal / except ImportError: from PyQt5.QtCore import pyqtSignal as Signal`. PyQt5 has no `Signal`, so the `except` branch **always** runs. These are half-finished ports that already anticipate PySide naming; under PySide6 the `try` becomes the live branch. |
| non-issues (verified present in PySide6 6.11.2) | `QStyleOptionTab`, `QProxyStyle`, `QStylePainter`, `QKeySequenceEdit`, `QGuiApplication`, `QIcon.Normal`, forgiving enums (`Qt.LeftButton`, `Qt.AlignLeft`) | |
| clean | no `pyqtSlot`, no `sip`, no `QRegExp`, no `QDesktopWidget`, no `MidButton`, no `AA_EnableHighDpiScaling`, no `QFontMetrics.width(str)`, **no multiple-inheritance class definitions anywhere** | the last one matters — PySide6's stricter MRO rules cost nothing here |

Test suite: 22 test files, all still on PyQt5 (`tests/conftest.py:34`, `test_controlpanel.py:39-40`, `test_panelsplitter.py:41-43`, `test_labels.py:50-53`, `test_shutdown.py:42-44`, `test_actioninventory.py:48-49`, `test_smoketest.py:29`, `test_annotationpanel.py:35-36`, `test_eventoverlay.py:33-34`, `test_joinmarkers.py:36-37`, `test_meanspectrogram.py:34`, `test_parameterbar.py:35-36`). Note `test_minmaxpyramid.py:19` imports `audian.buffereddata` and `test_dataloader.py:30` imports `audian.data` — both pull Qt in solely because of §3a/§3b, so fixing those layering violations also makes two test modules binding-agnostic.
