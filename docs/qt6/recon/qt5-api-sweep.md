# Recon: qt5-api-sweep

I have everything I need. Final report:

---

# Qt5→Qt6 mechanical sweep: audian

Scope: `src/audian/` (34 modules), `tests/` (21), `scripts/` (2), `runaudian.py`, `songdetector.py`.
Baseline: PyQt5 **5.15.11** (`.venv`) vs PySide6 **6.11.2** / shiboken6 6.11.2 (`.venv-qt6`), pyqtgraph **0.14.0** in both.

All claims below are **machine-verified**, not inferred: I ran an AST import-surface diff, resolved every `Class.ATTR` reference against both bindings' live namespaces, and executed a mechanically-rewritten copy of the tree (`/tmp/qt6scratch`) under PySide6.

## Headline

**The mechanical layer is small. The ownership layer is the real work.**

Exactly **7 edits** (5 distinct patterns) take all 34 modules from "ImportError on line 1" to **34/34 importing clean** under PySide6. Verified end-to-end.

The 545 unscoped enum references are **NOT breaking** — PySide6 6.11 ships forgiving-enum mode on by default (only `PYSIDE6_OPTION_PYTHON_ENUM=16` disables it; values 1/2/4/8/32 keep it). They are a cleanup, not a blocker.

What *does* break at runtime is one hand-written sip-ownership workaround, and one silent shortcut-binding change.

---

# BREAKING (crashes / raises)

## B1. `QAction`/`QActionGroup` moved `QtWidgets` → `QtGui` — 2 sites

| file:line | code | Qt6 |
|---|---|---|
| `src/audian/audian.py:21` | `from PyQt5.QtWidgets import QAction, QActionGroup, QPushButton` | split: `from PySide6.QtGui import QAction, QActionGroup` |
| `src/audian/databrowser.py:27` | `from PyQt5.QtWidgets import QAction, QMenu, QComboBox` | split: `from PySide6.QtGui import QAction` |

Verified: `PySide6.QtWidgets.QAction` → `AttributeError`. `QShortcut` also moved but **is not used anywhere** in this tree.

## B2. `QVariant` absent from `PySide6.QtCore` — 7 sites, 1 file

`from PySide6.QtCore import QVariant` → `AttributeError`. All uses are `return QVariant()` sentinels from model virtuals; Qt6 idiom is `return None`.

- `src/audian/labeloverlay.py:107` (import)
- `:894`, `:898`, `:914` — `CategoryModel.headerData` / `.data`
- `:1107`, `:1111`, `:1131` — `LabelTableModel.headerData` / `.data`

## B3. `QtCriticalMsg` / `QtFatalMsg` / `QtWarningMsg` not top-level in `PySide6.QtCore` — 1 import, 2 uses

- `scripts/smoke_test.py:33` `from PyQt5.QtCore import QtCriticalMsg, QtFatalMsg, QtWarningMsg`
- `:72` `if mode in (QtCriticalMsg, QtFatalMsg) ...`, `:74` `elif mode == QtWarningMsg:`

Qt6: `from PySide6.QtCore import QtMsgType` then `QtMsgType.QtCriticalMsg`. (`QtMsgType.QtCriticalMsg` resolves on PyQt5 too — a binding-neutral fix.)

## B4. `pyqtSignal` absent — 7 sites, 2 of them unguarded

Unguarded (hard ImportError):
- `src/audian/buffereddata.py:8` — `from PyQt5.QtCore import QObject, QTimer, pyqtSignal as Signal`
- `src/audian/fulltraceplot.py:26` — `from PyQt5.QtCore import pyqtSignal as Signal`

Already guarded by `try: from …QtCore import Signal / except ImportError:` — these fall through correctly once the module name changes: `selectviewbox.py:5-7`, `timeplot.py:7-9`, `eventoverlay.py:76-79`, `databrowser.py:15-17`, `spectrogramplot.py:9-11`. Note the `try` arm is **dead on PyQt5** (PyQt5.QtCore has no `Signal`), so this idiom is a no-op today and only pays off after the rename. No `pyqtSlot`, `pyqtProperty`, `pyqtBoundSignal`, `pyqtConfigure`, `pyqtRemoveInputHook`, or `PYQT_VERSION` anywhere.

## B5. `Qt.NoItemFlag` does not exist — 2 sites (latent on PyQt5 too)

- `src/audian/labeloverlay.py:918`, `:1135` — `if not index.isValid(): return Qt.NoItemFlag`

Verified `AttributeError` on **both** bindings — `Qt.ItemFlag` has no member `NoItemFlag`; the name is `NoItemFlags`. Pre-existing latent bug in a dead branch (`index.isValid()` is always true for a rendered index). Correct: `Qt.ItemFlag.NoItemFlags` (works on both) or `Qt.ItemFlag(0)`.

**Total breaking edits: 7 lines.** Applied to the scratch tree → `IMPORT OK: 34/34`.

---

# BEHAVIOR-CHANGE (silently different — the dangerous class)

## C1. ⚠️ `theme.strip_pg_menus()` relies on sip ownership semantics — crashes the app under shiboken

**This is the single highest-severity finding in the sweep.**

`src/audian/theme.py:1775-1841` releases pyqtgraph's `PlotItem.ctrl` widgets from their `QWidgetAction`s, holds a Python reference, then deletes the menus. Under sip that keeps them alive. Under shiboken it does not.

I isolated the exact diverging call by stepping the teardown:

```
                          PyQt5 (sip)              PySide6 (shiboken)
releaseWidget only        ctrl alive=True          ctrl alive=True
+ menu.clear()            ctrl alive=True          ctrl alive=FALSE   <-- diverges here
+ setParent(None)         ctrl alive=True          ctrl alive=False
+ deleteLater()           ctrl alive=True          ctrl alive=False
```

The offending line is **`src/audian/theme.py:1832` — `menu.clear()`**. `QWidgetAction.releaseWidget()` does not transfer ownership to Python under shiboken, so `clear()` destroys each action and takes its default widget's C++ object with it. The Python reference in `plot_item._audian_ctrl_widgets` (`theme.py:1825`) becomes a set of dead wrappers.

Consequence, reproduced:
```
tests/test_theme.py:145  plots[0].showGrid(x=True, y=True, alpha=theme.GRID_ALPHA)
pyqtgraph/.../PlotItem.py:434  self.ctrl.xGridCheck.setChecked(x)
E   RuntimeError: libshiboken: Internal C++ object (PySide6.QtWidgets.QCheckBox) already deleted.
```

**On the app path, not just tests**: `rangeplot.py:39` calls `strip_pg_menus(self)` and `rangeplot.py:117` calls `self.showGrid(...)`. Other callers: `fulltraceplot.py:476`, `controlpanel.py:148`, `spectrogramplot.py:201`.

Downstream consumers that assume the widgets survive: `controlpanel.py:152-154` (iterates `_audian_ctrl_widgets` and reparents), `theme.py:1844-1878` (`_adopt_ctrl_widgets`).

This whole subsystem needs redesigning against shiboken ownership — it is architectural, not mechanical.

## C2. ⚠️ `QKeySequence.Open` gains a second binding in Qt6 — breaks the golden test

`src/audian/audian.py:2856` — `self.acts.open_files.setShortcuts(QKeySequence.Open)`

I diffed all 20 `QKeySequence.StandardKey` values the codebase uses. **Exactly one differs:**

| StandardKey | Qt5 | Qt6 |
|---|---|---|
| `Open` | `['Ctrl+O']` | `['Ctrl+O', 'Open']` |

(`Quit`, `SelectAll`, `Delete`, and all 16 `MoveTo*`/`Select*` keys are byte-identical.)

`tests/data/action-inventory.json` records `open_files.keys == ["Ctrl+O"]`; `tests/test_actioninventory.py:141` will fail with `bindings changed`. The extra binding is the XF86Open media key. Either regenerate the golden with a documented note, or set the shortcut explicitly. The other 19 `QKeySequence.*` call sites (`audian.py:2881, 3188, 3196, 3202, 3208, 3217, 3227, 3494, 3500, 3506, 3512, 3909, 3916, 3924, 3931, 3937, 3943`) are safe.

## C3. Qt enums are **not** int-like in PySide6

Most PySide6 enums are plain `enum.Enum`/`enum.Flag`, not `IntEnum`/`IntFlag`. Verified:

| expression | PyQt5 | PySide6 |
|---|---|---|
| `int(Qt.LeftButton)` | `1` | **TypeError** |
| `Qt.LeftButton == 1` | `True` | **False** |
| `str(Qt.LeftButton)` | `'1'` | `'MouseButton.LeftButton'` |
| `int(QSizePolicy.Fixed)` | `0` | **TypeError** |
| `int(QPalette.Window)` | `10` | **TypeError** |
| `int(QDialogButtonBox.Close)` | `2097152` | **TypeError** |

Still int-like (asymmetrically!): `Qt.AlignmentFlag`, `Qt.ItemDataRole`, `Qt.Key`, `Qt.FocusPolicy`, `QEvent.Type`, `QDialog.DialogCode`, `QMessageBox.StandardButton`.

**Audit result: this codebase does not depend on int-ness.** No `int()`/`str()`/f-string/arithmetic is applied to any Qt enum. Every modifier test is `bool(x & Qt.Y)` or `== Qt.Y`, which is Flag-safe (`databrowser.py:904, 923, 3432, 3446, 4855, 7788`; `audian.py:2717, 2719`; `selectviewbox.py:36, 150`). Enums used as dict keys (`theme.py:1961-1994`) hash fine. Recorded here as a **constraint on new code**, not a defect list.

## C4. `QFontDatabase()` instantiation deprecated

`src/audian/theme.py:677` — `cached = frozenset(QFontDatabase().families())`

Works but emits `DeprecationWarning` on every call under PySide6 (observed in the test run). Qt6 made the methods static: `QFontDatabase.families()`. Qt6-only (`QFontDatabase.families()` as a static call raises `TypeError` on PyQt5), so this one cannot be written binding-neutrally.

## C5. `QMouseEvent.pos()/globalPos()/x()` deprecated (warn, don't fail)

Live-code sites on real `QMouseEvent`s:
- `src/audian/audian.py:464`, `:478` — `self._close_at(event.pos())` (QTabBar)
- `src/audian/databrowser.py:902`, `:909` — `event.pos()`, `event.pos().y()` (rail card QWidget)
- `src/audian/fulltraceplot.py:1151`, `:1176` — `self.mapToScene(ev.pos())` (QGraphicsView)

Qt6 replacement: `.position()` / `.globalPosition()` — **note these return `QPointF`, not `QPoint`**. `mapToScene` has a QPointF overload; `_close_at` → `close_rect(i).contains(pt)` needs `QRectF` or `.toPoint()`.

The remaining `ev.pos()` hits are pyqtgraph's own event classes and are unaffected: `selectviewbox.py:78, 107, 118, 153`, `yaxisitem.py:91`.

**Not affected:** `selectviewbox.py:104` `ev.delta()` — that's `QGraphicsSceneWheelEvent`, which keeps `delta()` in Qt6 (upstream pyqtgraph `ViewBox.py:1309` uses it identically).

---

# COSMETIC / DEPRECATED-BUT-WORKING

## D1. Unscoped enums — 545 references, 53 (owner, enum-class) pairs

Verified: **all resolve under PySide6 6.11** except the two `Qt.NoItemFlag` in B5. The forgiving-enum compat layer is a documented deprecation path — scope them, but on your own schedule, not as a blocker.

Full mapping (count — target Qt6 scoped class — top files):

| count | unscoped owner | Qt6 scoped class | concentration |
|---:|---|---|---|
| 101 | `Qt` | `Qt.MouseButton` | test_panelsplitter(44), test_labels(32), test_playback(6), databrowser(5) |
| 55 | `QEvent` | `QEvent.Type` | test_panelsplitter(24), test_labels(16), audian(5), smoke_test(3) |
| 43 | `QSizePolicy` | `QSizePolicy.Policy` | databrowser(23), audian(12), fulltraceplot(3), labeloverlay(2) |
| 31 | `Qt` | `Qt.KeyboardModifier` | databrowser(9), test_labels(9), selectviewbox(4) |
| 29 | `Qt` | `Qt.AlignmentFlag` | audian(20), labeloverlay(6), databrowser(3) |
| 28 | `QPalette` | `QPalette.ColorRole` | theme(28) |
| 25 | `Qt` | `Qt.ItemDataRole` | labeloverlay(23), audian(2) |
| 22 | `Qt` | `Qt.PenStyle` | theme(6), eventoverlay(5), test_eventoverlay(4), test_theme(3) |
| 20 | `QKeySequence` | `QKeySequence.StandardKey` | audian(20) |
| 13 | `QSettings` | `QSettings.Format` | test_actioninventory(4), test_panelsplitter(4), smoke_test(3) |
| 13 | `Qt` | `Qt.Key` | audian(7), databrowser(4), test_labels(2) |
| 12 | `QSettings` | `QSettings.Scope` | test_actioninventory(4), test_panelsplitter(4) |
| 12 | `Qt` | `Qt.ToolButtonStyle` | audian(8), databrowser(2) |
| 11 | `Qt` | `Qt.BrushStyle` | audian(2), theme(2), test_eventoverlay(2), test_theme(2) |
| 11 | `Qt` | `Qt.WidgetAttribute` | audian(5), databrowser(4), labeloverlay(2) |
| 11 | `QDialogButtonBox` | `QDialogButtonBox.StandardButton` | databrowser(6), labeloverlay(3), audian(2) |
| 9 | `QIcon` | `QIcon.Mode` | audian(8), labeloverlay(1) |
| 9 | `Qt` | `Qt.TextElideMode` | audian(5), databrowser(4) |
| 8 | `QPainter` | `QPainter.RenderHint` | fulltraceplot(3), audian(2), eventoverlay(2) |
| 8 | `Qt` | `Qt.WindowModality` | audian(4), databrowser(2), labeloverlay(2) |
| 7 | `Qt` | `Qt.Orientation` | labeloverlay(4), databrowser(3) |
| 6 | `Qt` | `Qt.CursorShape` | audian(2), databrowser, panelsplitter, yaxisitem |
| 6 | `Qt` | `Qt.ItemFlag` | labeloverlay(6) |
| 4 ea | `Qt.GlobalColor`, `QIcon.State`, `QToolButton.ToolButtonPopupMode`, `Qt.FocusPolicy`, `QMessageBox.StandardButton` | | |
| 3 ea | `Qt.WindowState`, `Qt.ShortcutContext` | | audian(3), databrowser(2)+test_labels(1) |
| 2 ea | `QTabWidget.TabPosition`, `QFrame.Shape`, `QAbstractItemView.SelectionBehavior`, `QPalette.ColorGroup`, `QGraphicsItem.GraphicsItemFlag` | | |
| 1 ea | `Qt.WindowType`, `Qt.PenCapStyle`, `QStyle.ControlElement`, `QStyle.StyleHint`, `Qt.TextFormat`, `QBuffer.OpenModeFlag`, `Qt.ScrollBarPolicy`, `QSlider.TickPosition`, `QAbstractSpinBox.ButtonSymbols`, `Qt.TextInteractionFlag`, `QDialog.DialogCode`, `QAbstractItemView.SelectionMode`, `QDialogButtonBox.ButtonRole`, `Qt.SizeHint`, `QFont.StyleStrategy`, `QFont.StyleHint` | | |

Per-file `Qt.*` density: `audian.py`(56), `databrowser.py`(42), `test_panelsplitter.py`(38), `labeloverlay.py`(38), `test_labels.py`(33), `test_eventoverlay.py`(9), `theme.py`(9), `eventoverlay.py`(8), `selectviewbox.py`(7), `test_playback.py`(6), `test_theme.py`(5), `panelsplitter.py`(5), rest ≤2.

**19 of these 545 are in comments/docstrings only** — do not rewrite them blind. Notably `QGraphicsItem.ItemIgnoresTransformations` at `theme.py:1936` and `timeplot.py:106` are both *warnings not to set the flag*; `QApplication.desktop()` at `traceitem.py:161` documents an API already migrated away from. Others: `theme.py:1383/1388/1401/816`, `labeloverlay.py:44/211`, `databrowser.py:3464(×2)/437`, `smoke_test.py:467`, `test_labels.py:1796`, `audian.py:611/647`, `fulltraceplot.py:980`, `theme.py:54/2207`, `test_annotationpanel.py:523`.

Already scoped today (5 refs, an inconsistency worth flattening): `fulltraceplot.py:1150`, `databrowser.py:3431?` — grep `Qt\.[A-Z][a-z]*\.` to find them. `theme.py:795` uses `Qt.PenStyle` as a **type annotation**, which is already correct Qt6.

## D2. `exec_()` → `exec()` — 1 site

`src/audian/audian.py:5033` — `app.exec_()`. PySide6 keeps `exec_` as an alias (verified on `QApplication`, `QDialog`, `QMenu`), so this works. `databrowser.py:7816` already uses `menu.exec(...)`. `tests/test_smoketest.py:45` `spec.loader.exec_module` is unrelated.

---

# VERIFIED CLEAN — no action needed

Machine-checked, zero hits or already Qt6-correct:

- **`QFontMetrics.width()`** — none; `horizontalAdvance()` used at all 8 sites (`audian.py:374, 821, 825, 893, 901, 1884, 1945`, `theme.py:1720`).
- **`QDesktopWidget` / `QApplication.desktop()`** — none in live code. Already on `QGuiApplication.primaryScreen()` (`audian.py:1552, 2748`, `fulltraceplot.py:695`) and `devicePixelRatioF()` (`eventoverlay.py:1096`, `fulltraceplot.py:693`, `traceitem.py:178`, `controlpanel.py:405`, `specitem.py:120`).
- **`AA_EnableHighDpiScaling` / `AA_UseHighDpiPixmaps`** — never set. The 11 `setAttribute(Qt.…)` calls are all `WA_*` widget attributes.
- **`QPainter.HighQualityAntialiasing`** — none (removed in Qt6); only `QPainter.Antialiasing`, which survives.
- **`QRegExp`** — none.
- **`setMargin()`** — none; `setContentsMargins()` throughout.
- **sip / shiboken APIs** — no `sip.delete`, `sip.isdeleted`, `PYQT_VERSION`, `pyqtRemoveInputHook`. The dead-object exception is `RuntimeError` in **both** bindings, so the 14 `except RuntimeError:` guards (`theme.py:1537/1836/1872`, `audian.py:748/1707/2220/2225/2254/2689`, `fulltraceplot.py:570/1021`, `traceitem.py:171`, `conftest.py:83`) port unchanged.
- **`QSettings` type coercion** — the classic bug is **absent**. Only one `.value()` call in live code: `databrowser.py:1451`, already wrapped in `int(...)` with `except (TypeError, ValueError)`. Both bindings return `str` from an INI file with no `type=` hint, and PySide6 6.11 **does** support the `type=` kwarg (contrary to older guidance). `songdetector.py:264-296` `cfg.value(...)` is thunderlab `ConfigFile`, not `QSettings`.
- **`QFileDialog` static return shapes** — identical `(path, filter)` tuples in both. All 5 sites unpack correctly: `databrowser.py:4809[0]`, `:8143 (path, _)`, `:8198[0]`, `audian.py:2771[0]`, `:4685[0]`.
- **Removed Qt6 signal overloads** — none used. All 6 `currentIndexChanged.connect` sites take `int` (`databrowser.py:2181, 2207, 2337, 2365`). `QButtonGroup` (`databrowser.py:472`) is used only for `addButton`/`setExclusive` — no `buttonClicked(int)`. No `QComboBox.activated(str)`, no `QHeaderView.setResizeMode`.
- **Shortcut construction** — no `Qt.CTRL + Qt.Key_X` arithmetic anywhere (the classic Qt6 break). All ~100 bindings are string literals or `QKeySequence.StandardKey`. `QKeySequence(Qt.Key_Escape)` (`test_labels.py:1809`) works identically.
- **Synthetic events in tests** — all 5 `QMouseEvent(...)` constructions (`test_panelsplitter.py:242, 1534`; `test_labels.py:355, 977, 1027`) already use the Qt6-compatible 6-arg `QPointF` form. No `QKeyEvent`/`QWheelEvent` construction.
- **float→int argument strictness** — PySide6 is *more* permissive (silently truncates where PyQt5 raises `TypeError`). Direction is safe; note it truncates rather than rounds.
- **`QByteArray` buffer protocol** — `io.BytesIO(buf.data())` identical in both (`audian.py:2786`).
- **Custom `QIconEngine` subclass** — `labeloverlay.py:799-820` `_SwatchEngine.paint()` override works unchanged; `QIcon(engine).pixmap()` verified.
- **`QProxyStyle.styleHint` override** — `audian.py:505-507` with `data=None` works in both.
- **`QTabBar.initStyleOption` (protected)** — accessible from a subclass in both; `audian.py:411-417` `QStylePainter`/`QStyleOptionTab`/`QStyle.CE_TabBarTabShape` all resolve.
- **`QPainter.drawText(QRect, Qt.AlignLeft | Qt.AlignVCenter, str)`** — accepts the flag combination in both (`audian.py:436-440`).
- **Signal connections** — 246 `.connect()` calls, 31 with lambdas, 1 `functools.partial` import (`plotranges.py:10`). No old-style `QObject.connect`, no `signal[type]` overload indexing, no explicit `Qt.QueuedConnection`. All portable.
- **`songdetector.py`** — 777 lines, **zero Qt**. Pure matplotlib/scipy/thunderlab. Out of scope entirely.
- **`runaudian.py`** (10 lines), `scripts/compare_shots.py` (147) — no Qt.
- **22 modules with no Qt import at all**: `activity.py`, `alignment.py`, `bufferedenvelope.py`, `bufferedfilter.py`, `bufferedspectrogram.py`, `compresseddata.py`, `data.py`, `labels.py`, `layers.py`, `plotranges.py`, `plugins.py`, `session.py`, `statisticsanalyzer.py`, `version.py`, `__init__.py`, + 7 test modules. A further 6 modules (`analyzer.py`, `windowing.py`, `panels.py`, `rangeplot.py`, `specitem.py`, `traceitem.py`) touch Qt only through pyqtgraph and are already binding-agnostic.

---

# Counts

| Category | Occurrences | Files | Severity |
|---|---:|---:|---|
| `QAction`/`QActionGroup` module move | 2 | 2 | breaking |
| `QVariant` | 7 | 1 | breaking |
| `QtCriticalMsg`/`QtFatalMsg`/`QtWarningMsg` | 3 | 1 | breaking |
| `pyqtSignal` unguarded | 2 | 2 | breaking |
| `pyqtSignal` in try/except (rename only) | 5 | 5 | breaking-after-rename |
| `Qt.NoItemFlag` | 2 | 1 | breaking (latent on PyQt5 too) |
| **Breaking total** | **21** | **8** | |
| sip-ownership teardown (`strip_pg_menus`) | 1 subsystem, ~70 LOC + 5 call sites | 5 | **behavior-change, crashes app** |
| `QKeySequence.Open` extra binding | 1 (+1 golden file) | 2 | behavior-change |
| `QMouseEvent.pos()` deprecated | 6 | 3 | behavior-change (QPointF) |
| `QFontDatabase()` deprecated | 1 | 1 | behavior-change (warning) |
| Enum non-int-ness | 0 actual deps | — | constraint on new code |
| Unscoped enums (live code) | **526** | 19 | cosmetic |
| Unscoped enums (comments/docstrings) | 19 | 12 | do not touch |
| `exec_()` | 1 | 1 | cosmetic |
| **Grand total** | **~580 references** | | |

Packaging: `pyproject.toml:16` `"PyQt5"` → `"PySide6"`. `src/audian.egg-info/requires.txt:8` is generated. `.github/workflows/uploaddocs.yml` builds docs only and **does not run the test suite** — no CI gate exists for this migration today.

# Recommended sequencing

1. **Mechanical, one commit, mostly `sed`**: 7 breaking edits (B1–B5) + `pyproject.toml`. Verified sufficient for `IMPORT OK: 34/34`.
2. **`theme.strip_pg_menus` redesign (C1)** — the actual architectural work. Blocks the app, not just tests. Isolate at `theme.py:1832`.
3. **Golden regeneration (C2)** — one entry, `open_files`, documented in the commit.
4. **Enum scoping (D1)** — 526 live references, mechanically safe, but a script must skip the 19 comment/docstring hits. Gate it behind `PYSIDE6_OPTION_PYTHON_ENUM=16` in CI once complete to prevent regression.
5. Deprecation cleanup (C4, C5, D2).

Caveat on empirical coverage: `tests/test_theme.py` runs clean under PySide6 (**42 passed, 2 failed** — one is C1, the other is a missing `ruff` binary in the qt6 venv, not a migration issue). The full suite did not complete within a 10-minute budget under PySide6; `tests/test_actioninventory.py` alone exceeded 110s. Whether that is a genuine Qt6 hang or just this suite's normal cost is **unverified** and worth a dedicated timing run against the PyQt5 baseline before reading anything into it.
