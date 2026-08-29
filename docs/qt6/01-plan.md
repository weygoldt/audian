# Qt6 migration: the order the work happens in

## The constraint that shapes everything

This application is going on a field trip.  The Qt5 build at `10c5004` is
tested and works, and it stays on `master` untouched; this branch is where
the migration is allowed to fail.

That is a licence to be aggressive, not a licence to be careless.  The
discipline is that **every commit on this branch leaves a runnable
application** with the gates in `00-foundation.md` green.  If the branch is
abandoned halfway, what is left behind is a working Qt6 application with
some old architecture still in it -- never a half-transplanted one.

So the port comes first and the architecture second, even though the brief
cares more about the architecture.  Not because the architecture matters
less, but because "PySide6, all 792 tests green" is a state worth being able
to return to while the interesting work happens.

## Stage A -- the application runs on PySide6

Everything that is *required* for the application to be correct on Qt6, and
nothing that is merely desirable.

1. **Dependency and entry point.** `PyQt5` out of `pyproject.toml`, `PySide6`
   in.  Binding chosen once, before any `pyqtgraph` import, because pyqtgraph
   picks whichever Qt is imported first.
2. **Imports.** `PyQt5.*` -> `PySide6.*` across `src/`, `tests/`, `scripts/`,
   `songdetector.py`.  `QAction` and `QShortcut` move to `QtGui`.  The six
   modules carrying a `try: Signal / except: pyqtSignal as Signal` shim lose
   it -- PySide6 spells it `Signal` natively, so the shim's whole reason to
   exist is gone.
3. **The APIs that are actually removed.** `QWheelEvent.delta()`,
   `QFontMetrics.width()`, `QPainter.HighQualityAntialiasing`.  The high-DPI
   application attributes, which Qt6 ignores.
4. **The flag comparisons that raise.** `(button() & Qt.LeftButton) > 0` is a
   `TypeError` under PySide6, where `Qt.MouseButton` is an `enum.Flag`.
5. **Scoped enums, everywhere.** Unscoped names still resolve in PySide6
   6.11, so this is not what makes the application run -- it is what makes
   the code stop being a PyQt5 application that happens to import PySide6.
6. **Teardown.** Delete `DataBrowser.__del__`; stop shadowing
   `QWidget.close()`; give the window and the browser a real `closeEvent`.
   Under PySide6 a finaliser that calls into a destroyed C++ object raises,
   and today `quit` never goes through Qt's close machinery at all.

Stage A ends when `pytest`, the smoke matrix and `compare_shots.py` all
agree with the PyQt5 baseline.

### No compatibility layer

The brief rules out `qtpy` and a hand-rolled `QtCompat`, and this migration
does not smuggle one in under another name.  There is no `audian/qt.py`
re-exporting `Signal` and `Qt`: every module says `from PySide6.QtCore
import ...` for itself.

A single re-export module is genuinely tempting -- it would make step 2 a
one-line change per file -- and that is exactly the trap.  It leaves the
tree looking like it could be pointed back at PyQt5, which is the state the
brief calls failure mode 2.

## Stage B -- the architectural fixes that are also bug fixes

Chosen because each one is both something the brief asks for and something
that is wrong today.  These are small enough to do individually, with the
gates green after each.

- **The shared `QAction` namespace.**  Every `DataBrowser` is handed the same
  `acts` object and `disable_unused_range_actions` permanently disables 20 of
  them.  Opening a recording with no spectrogram takes frequency zoom away
  from every other open tab, and nothing re-enables it.  The window should
  ask the current browser what it supports on tab change; the browser should
  never touch `acts`.
- **The circular import.**  `databrowser.py` imports `audian.py` from inside
  ten method bodies to avoid a cycle.  `settings()` and `save_setting()` move
  to a Qt-free `audian/settings.py` and both modules import it at the top.
- **One settings store.**  Five hand-rolled versioned schemas plus a second
  `QSettings` backend for a single colormap key, with two hand-written
  debouncers and two guard flags whose only job is stopping tabs from
  overwriting each other.
- **Dialog lifetimes.**  Four non-modal dialogs keep a Python attribute and
  clear it from a `finished` lambda.  `finished` is not emitted on every
  destruction path, and under PySide6 the wrapper outlives the C++ object, so
  `self.label_dialog.raise_()` can reach a deleted object.  `destroyed` is
  the correct signal.
- **`theme.collect_orphan_widgets`.**  Already documented in `todo.md` as a
  segfault source: it reparents widgets while iterating a live
  `topLevelWidgets()` snapshot, so a reparent can destroy a widget a later
  iteration then dereferences.  A dangling wrapper is a crash, not a
  `RuntimeError`.  Read first, then reparent, with a validity check.
- **Work off the GUI thread.**  Refiltering and re-spectrogramming 16
  channels costs ~1.5 s and today the only concession is a wait cursor and a
  debounce timer.  This is the one place the brief's threading section has
  real teeth.

## Stage C -- the extractions

The large structural work, in the order that buys the most per unit of risk:

- `LaneLayoutSolver` -- ~30 methods of pixel arithmetic that is already
  almost pure Python, and whose constants were tuned against Qt5's layout
  activation.  Extracting it makes it testable without a GUI, which is what
  the migration most needs, because this is the part that breaks as a
  wrong-looking picture rather than an exception.
- `LabelController` / `AnnotationController` -- the boundaries are already
  drawn by section comments in the file.  Domain logic including the refusal
  to draw a bundle against the wrong subset of a split recording currently
  lives on a `QWidget`.
- A `RecordingSession` model, so `DataBrowser` stops being both the view and
  the model.
- Typed signal payloads, and no more emitting `self`.

Stage C is where the brief's "if we were building this today" question gets
answered.  It is also the part most likely to be cut short, which is why it
is ordered by value and why Stage A and B stand on their own.

## What is deliberately not happening

- **pyqtgraph is not being replaced.**  It runs on Qt6 unmodified and nothing
  profiled justifies a rewrite.  It gets a boundary instead.
- **Nothing moves to QML.**  This is a dense, keyboard-driven desktop
  application and Qt Widgets is the right toolkit for it.
- **The `settings.json` format does not change.**  It holds the user's label
  categories and it is live data.
- **No event bus, no service locator.**  Both are named failure modes and
  both would be easy to reach for while splitting up the god object.
