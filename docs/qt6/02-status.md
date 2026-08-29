# Where the migration is, and what to do next

Written to be picked up cold.

## State

Branch `qt6-migration`, worktree `.claude/worktrees/qt6-migration`, cut from
`master` at `10c5004`.  **`master` is untouched and still the tested PyQt5
build.**  Nothing in this branch has changed a line of `src/` yet: everything
committed so far is the safety net, the reconnaissance and the plan.

Two environments, deliberately separate so neither can break the other:

    .venv        PyQt5 5.15.11   -- the baseline, and what src/ still needs
    .venv-qt6    PySide6 6.11.2  -- ready, audian not yet installed into it

## Running the gates

    QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/ -q
    ./scripts/baseline_matrix.sh .devshots/qt6
    .venv/bin/python scripts/compare_shots.py .devshots/baseline .devshots/qt6

Baseline on PyQt5, all recorded at `10c5004`:

| gate | result |
| --- | --- |
| `pytest tests/` | 791 passed, 1 failed, ~197 s |
| action inventory | 124 actions, 102 bound |
| action trigger sweep | 115 fire clean |
| `smoke_test --interact --census` | 56/56 clean, 31 top-level, 0 parentless |
| matrix | 8 screenshots, incl. `QT_SCALE_FACTOR=2` |

The one failure is `test_theme_module_is_lint_clean`: `ruff` is not installed
in the project environment and a current one flags 17 pre-existing style
findings in `theme.py`.  It fails identically on `master` and has nothing to
do with Qt.

`tests/test_shutdown.py` holds four `strict` xfails describing a real bug (see
below).  They are expected to fail; when the fix lands they XPASS and the
markers must come off in the same commit.

## Do this next

### 1. Stage A, first commit -- 21 lines, 8 files

Machine-verified as sufficient to take all 34 modules from `ImportError` to
importing clean.  Details and exact line numbers in
`docs/qt6/recon/qt5-api-sweep.md`.

- `QAction`/`QActionGroup` `QtWidgets` -> `QtGui`: `audian.py:21`,
  `databrowser.py:27`
- `QVariant` -> `None`: `labeloverlay.py:107, 894, 898, 914, 1107, 1111, 1131`
- `QtCriticalMsg`/`QtFatalMsg`/`QtWarningMsg` -> `QtMsgType.*`:
  `scripts/smoke_test.py:33, 72, 74`
- `pyqtSignal` unguarded: `buffereddata.py:8`, `fulltraceplot.py:26`
  (five more sit behind a `try` that is dead on PyQt5 and starts working
  after the rename)
- `Qt.NoItemFlag` -> `Qt.ItemFlag.NoItemFlags`: `labeloverlay.py:918, 1135`
  (latent bug on PyQt5 too)
- `pyproject.toml:16` `PyQt5` -> `PySide6`
- then `PyQt5` -> `PySide6` across `src/`, `tests/`, `scripts/`

### 2. `theme.strip_pg_menus` -- the actual blocker

Not mechanical.  It keeps pyqtgraph's `PlotItem.ctrl` widgets alive across a
menu teardown by holding a Python reference; `QWidgetAction.releaseWidget()`
hands ownership back under sip and **not** under shiboken, so `menu.clear()`
at `theme.py:1832` destroys them and the next `showGrid()` raises on a dead
wrapper.  App path, not just tests: `rangeplot.py:39` strips, `:117` shows
the grid.  Callers: `fulltraceplot.py:476`, `controlpanel.py:148`,
`spectrogramplot.py:201`.  Consumers of the kept references:
`controlpanel.py:152`, `theme.py:1844`.

### 3. Regenerate one golden entry

`QKeySequence.Open` gains a second binding in Qt6 -- `['Ctrl+O']` becomes
`['Ctrl+O', 'Open']`, the XF86Open media key.  The other 19 `StandardKey`
values are byte-identical.  `tests/test_actioninventory.py` will fail with
`bindings changed`; regenerate with a note in the commit:

    AUDIAN_REGENERATE_GOLDEN=1 ... -m pytest tests/test_actioninventory.py

### 4. Then Stage B and C

Ordered in `01-plan.md`.  Start with the `closeEvent`, because it is the one
that loses user data.

## The findings that matter most

**No `closeEvent` anywhere in `src/`.**  `Audian.close()` is shadowed to mean
"close a tab", so Qt's close machinery reaches nothing.  Closing via the
window manager -- the most common exit gesture -- runs no teardown: no label
flush, no `CompressedData.close()`, no `PlayAudio.close()`.  The compression
children are not daemons, so `multiprocessing`'s exit handler *joins* them:
on a large recording still being reduced, a closed window becomes a process
that will not exit.  Four xfails in `tests/test_shutdown.py`.

**High-DPI is the risk the suite cannot see.**  Qt5 only scales when asked and
audian never asks; Qt6 always scales from the screen's DPI.  Every pixel
constant in the ~30-method lane-layout arithmetic sits downstream.  The
offscreen suite runs at DPR 1.0.  This machine is DPR 1.0 too (1920x1080 over
309 mm, GNOME scaling 1), so the field-trip laptop is not exposed -- an
external monitor would be.  Hence the `QT_SCALE_FACTOR=2` row in the matrix.

**A shared `QAction` namespace lets one tab disable another.**
`disable_unused_range_actions` (`databrowser.py:1851`) calls
`setEnabled(False)` on 20 actions of the `acts` bag that `audian.py:4707`
hands to *every* browser as the same object, and nothing re-enables them.
Opening a recording with no spectrogram takes frequency zoom away from every
open tab.  Pre-existing, nothing to do with Qt6.

**Plugins have no error isolation.**  `plugins.py:38` globs the *current
working directory* for `audian*.py` and imports each with no `try`/`except`.
A broken plugin, or any stray file matching that glob, takes down startup.
`add_plugin(k, x)` also registers under whatever name the `dir()` loop
happened to end on.

**Plugin compatibility will break, and that is unavoidable.**  `BufferedData`
-- the base class every trace plugin subclasses -- carries the signal import.
Any out-of-tree plugin declaring its own `pyqtSignal` is source-incompatible
after this migration.  Belongs in the release notes.

**`songdetector.py` is not part of this.**  777 lines at the repo root, zero
Qt, zero `audian` imports, no `audian_*` hook, referenced by nothing, not in
`pyproject`, and it no longer runs against the installed matplotlib.  A 2018
standalone script.  Left untouched; deleting it is the owner's call.

**There is no CI gate.**  `.github/workflows/uploaddocs.yml` builds docs and
does not run the suite.

## Claims that were checked and did not hold

Recorded so they are not re-adopted from the reports, which state them:

- `QFontDatabase()` is **not** removed in Qt6 -- it instantiates and warns.
  `theme.py:677` is a cleanup, not a crash.
- The label-loss window is **one turn of the event loop**, not everyday data
  loss: the queued zero-timer save fires unless nothing turns the loop again.
  Reproduced both ways.  The teardown being skipped is still real.
- `QWheelEvent.delta()` is removed in Qt6, but the one `ev.delta()` here is a
  `QGraphicsSceneWheelEvent`, which keeps it.
- One report's suite timing under PySide6 (">10 min") was measured without a
  baseline to compare against.  PyQt5 takes ~197 s; treat any Qt6 figure as
  unverified until both are measured the same way.
