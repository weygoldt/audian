# Where the migration is, and what to do next

Written to be picked up cold.  Last updated after the clean-shutdown
work, the first of the three items below.

## State: Stage A is done, Stage B and C have not started

Branch `qt6-migration`, worktree `.claude/worktrees/qt6-migration`, cut from
`master` at `10c5004`.  **`master` is untouched and is still the tested PyQt5
build.**  18 commits, 34 files, +2145/-146.

The application runs on PySide6 6.11.2 / Qt 6.11.2.  What has *not* happened
is everything the brief calls architectural modernisation: no threading, no
god-object split, no plugin boundaries, no model/view.  About nine of the 25
definition-of-done criteria are met; the list is in the section below.

Two environments, deliberately separate:

    .venv        PyQt5 5.15.11   -- the recorded baseline, nothing else
    .venv-qt6    PySide6 6.11.2  -- audian installed editable; the real one

`pyproject.toml` and `uv.lock` both name PySide6 and neither mentions PyQt5.
`.venv` is stale on purpose: it is what the baseline numbers were measured
in.  If you no longer need the comparison, delete it -- but see the hazards
section before reaching for `uv sync`, which from a worktree does not do what
it looks like it does.

## Running the gates

Both gates want a private user cache.  `~/.cache/audian` is machine-global,
holds `recent.json`, and the `--empty` startup screen *renders* it -- so a
suite run changes what the next screenshot draws, and two worktrees comparing
matrices disagree about a directory listing.

    XDG_CACHE_HOME=$PWD/.cache-gate QT_QPA_PLATFORM=offscreen \
      PATH=$PWD/.venv-qt6/bin:$PATH .venv-qt6/bin/python -m pytest tests/ -q
    XDG_CACHE_HOME=$PWD/.cache-shots PY=.venv-qt6/bin/python \
      ./scripts/baseline_matrix.sh .devshots/qt6
    .venv-qt6/bin/python scripts/compare_shots.py .devshots/baseline .devshots/qt6

| gate | PyQt5 baseline @ `10c5004` | PySide6 now |
| --- | --- | --- |
| `pytest tests/` | 791 passed, 1 failed, 197 s | **798 passed, 1 failed, 289 s** |
| action inventory | 124 actions, 102 bound | same, one key added (below) |
| action trigger sweep | 115 fire clean | 115 fire clean |
| `smoke_test --interact --census` | 56/56, 31 top-level, 0 parentless | **identical** |
| matrix | 8 configs green | 8 configs green |

The one failure is `test_theme_module_is_lint_clean`, and it is decided by
your environment rather than by the tree.  It shells out to a bare `ruff`
(`tests/test_theme.py`), which `.venv-qt6` does not contain unless you put it
there: run the way this document used to say, it dies `FileNotFoundError:
'ruff'` before ruff reads a line.  With ruff 0.16.5 on `PATH` it reports
**18** pre-existing findings in `theme.py`, not the 16 this table used to
claim -- the count moves with the ruff version, because `theme.py` carries no
ruff config and the defaults change under it.  It fails on `master` too.  Not
Qt, not this migration; fixing it is a separate decision about `theme.py`'s
house style, and pinning `ruff` is part of it.

Three corrections to what this table used to say.  The ruff count above is
one.  The second is that the suite also carried **one ERROR**, which no
version of this document mentioned: an `AttributeError` raised out of
`tests/conftest.py`'s session teardown by the `close` shadows described under
item 1.  It is gone with them.  The third is that
`tests/test_shutdown.py`'s four `strict` xfails are now four passes -- they
described the bug, and the bug is fixed, so the row above carries no xfail
column any more.

### The screenshot comparison needs reading, not believing

`compare_shots.py` reports 9-30% structural difference between the two
matrices.  That number is not a verdict.  A single offset explains only a
quarter to a third of it, so it is not purely a translation -- but the mean
absolute difference on the dark frame is 3.07 of 255, about 1.2%.  The
frames were inspected side by side and are the same picture: both
spectrograms, both colour bars, the navigator, the parameter bar, channel 01
in the same amber.

The tool cannot tell a few pixels of global drift from a break.  Use it to
notice that something moved, then look at the images.

That is the *cross-binding* reading.  Comparing two **Qt6** matrices is a far
tighter gate and should be used as one: at a fixed commit, seven of the eight
configs come out at exactly `0.00%` structural with `max_delta 0`, verified
across two worktrees.  Only `empty` differs, by about 0.05%, and only because
the startup screen draws the recent-files column and therefore the capturing
worktree's own path.  Anything else above 0.00% on a Qt6-to-Qt6 comparison is
a real regression, not rasteriser noise.

## Do this next

Three items, chosen by the owner, independent enough to run in parallel
worktrees.

### 1. Clean shutdown -- DONE

`Audian.closeEvent` exists now and is the only exit path.  The window
manager's button, `Ctrl+Q` and closing the last tab all run one teardown:
flush every browser's pending labels and annotation settings, `shutdown()`
each browser -- which stops its six timers *before* releasing the recording
and the compression pool -- then close the shared `PlayAudio` device.  `quit`
is `if self.close(): QApplication.quit()` rather than a second copy of that
list kept in step by hand.  The compression workers are `daemon=True`, so a
window closed over a recording that is still being reduced exits instead of
waiting for `multiprocessing`'s exit handler to join them.

Four things about the job were not in the recipe this section used to carry,
and each of them cost time.

- **`Audian.close(index=None)` had to be renamed**, and the recipe omitted it
  entirely.  It shadowed `QWidget.close`, so `self.close()` inside the class
  meant "close a tab" -- which would have left the new `closeEvent`
  unreachable from `quit` -- and `window.close()` in `tests/conftest.py`, in
  three fixtures and in `scripts/smoke_test.py` meant it too.  It is
  `close_tab` now.  Note what the shadow was *not*: `QWidget::close` is
  non-virtual, so the window manager's button always reached a `closeEvent`
  override.  There simply was not one.  Two independent defects, described
  here as one.
- **There were five `__del__` methods, not one.**  `DataBrowser.__del__`,
  `FullTracePlot.__del__` and `Audian.__del__` are gone -- the last was the
  one that actually raised, a `PortAudioError` out of `self.audio.close()` at
  interpreter shutdown, in every single suite run.  `Data.__del__` (the file
  handle) and `CompressedData.__del__` (the worker pool) are deliberately
  kept and now say so in a comment: plain Python, no Qt, and the last
  backstops for an object dropped without a shutdown.
- **The reason this section gave for deleting them was false.**  "Under
  PySide6 a finaliser calling into a destroyed C++ object raises" was tested:
  both wrappers were invalidated and both methods called, and neither raised.
  They did not even run.  The reasons that hold are that a `__del__` never
  runs at a predictable time, so nothing it owns is released on the gesture
  that matters; that on a subclass whose `__init__` did not finish it raises
  `AttributeError` from inside a finaliser; and that on a shiboken-wrapped
  `QObject` it defers Qt work to interpreter shutdown, the ordering
  `tests/conftest.py`'s own docstring blames for an intermittent SIGSEGV.
  The distinction is load-bearing: "it raises" would have argued for deleting
  the two that are worth keeping.
- **Two shadow traps, not one, and which one a run reported was luck.**
  `tests/conftest.py`'s teardown catches `RuntimeError` and nothing else, so
  the first `AttributeError` aborts the loop and hides every widget after it.
  `test_annotationpanel.py` builds two partly-constructed subclasses:
  `PanelBrowser` (reaching `DataBrowser.close`, which wants a `datafig`) and
  `MenuHost` (reaching `Audian.close`, which wants `tabs`).  Which surfaced
  depended on `topLevelWidgets()` ordering, so fixing the one you happen to
  see makes the other appear and look like a regression you caused.  Both
  shadows are gone.  `Audian.teardown` reads its own attributes through
  `getattr` for the same reason -- Qt delivers a close event to any window it
  is asked to close, `MenuHost` included, and a `closeEvent` that raises on a
  half-built subclass would have moved the trap rather than removed it.

A fifth thing, found only by measuring: `QTabWidget.removeTab` does not
destroy the page.  A closed tab's browser stayed a child of the tab widget's
internal `QStackedWidget` -- hidden, off the bar, out of `self.browsers`, and
alive with its fifty plots for the life of the window.  `close_tab` calls
`deleteLater()` now.  Note that `smoke_test --census` could never have caught
it: that census counts *parentless* top-level widgets and this one keeps a
parent throughout.

Do not reuse the line numbers this section used to carry; they had drifted by
+2, +6, +6 and -1, and the drift is not uniform.  Locate by symbol.

Still open on this path, and owned by nobody: `buffereddata.py`'s
`request_update` builds a parentless `QTimer` on a `BufferedData`, which is
not a `QObject` and is reachable from no widget, so `closeEvent` has no route
to it.  `docs/qt6/recon/data-layer.md` predicts a pending `flush_update`
firing into a closed `DataLoader` and recommends deleting that timer outright
-- the debounce it duplicates already lives in the controller.

Criteria 12, 19, 20.

### 2. Threading -- large, and the biggest user-visible win

**There is currently exactly one thread.**  `grep -rn "QThread|QRunnable|QThreadPool"`
over `src/` returns one hit and it is a comment (`buffereddata.py:243`).
Everything -- decoding, filtering, FFT, envelope, pyramid build, label IO,
region export, analyzer plugins -- runs on the GUI thread.  Refiltering and
re-spectrogramming 16 channels costs ~1.5 s, and the only concession today is
a wait cursor and a debounce timer.

`docs/qt6/recon/threading-audit.md` proposes a concrete architecture and is
worth reading in full: `QThreadPool`/`QRunnable` for the stateless jobs that
work from an immutable snapshot, and a worker `QObject` on a `QThread` for
the three things that own a handle -- the `DataLoader` (stateful, not
reentrant, exactly one thread may touch it), persistence, and playback.

Design cancellation, shutdown and stale-result handling explicitly; the brief
is specific about that.  Criteria 10, 11, 12, 16.

**Shutdown is now item 1's, and the two meet in one method.**  Whatever pool
or worker thread this adds has to be joined from the `Audian.closeEvent` that
already exists, as an edit to it rather than a second teardown path -- git
will not report a conflict between two methods of the same class, and the
result is a process that accepts the close event and exits with a filter still
running against a buffer whose owner is being torn down.  Order inside
`closeEvent`: discard in-flight results, drop queued runnables, wait with a
bounded timeout, quit and wait each worker thread, and only then the existing
`teardown()`.  `tests/test_shutdown.py` is the natural home for an assertion
that nothing is still running afterwards; it already has the window fixture.

Also unowned and on this path: `buffereddata.py`'s `request_update` timer,
described under item 1.

### 3. Scoped enums -- mechanical, but not a `sed`

194 `Qt.*` references in `src/audian/` alone, 317 across `src`, `tests` and
`scripts`.  They all *work*: PySide6 6.11 ships forgiving-enum mode by
default.  The brief asks for scoped enums anyway, and the point is that the
tree should stop reading as a PyQt5 application that imports PySide6.

Three traps:

- **`Qt.*` is not the job, it is about 58% of it.**  Ten other classes own
  unscoped enum members here, 141 more references in `src/audian` alone:
  QSizePolicy 40, QPalette 30, QKeySequence 20, QIcon 13, QDialogButtonBox
  12, QPainter 8, QEvent 7, QToolButton 4, QMessageBox 4, QAbstractItemView
  3.  An agent scoped to `Qt.*` will not touch `labeloverlay.py:813`'s
  `QIcon.Normal`, and under `PYSIDE6_OPTION_PYTHON_ENUM=16` that is one of
  the first things the import hits -- at which point the gate looks
  unachievable rather than half-done.  Enumerate the owners, do not take a
  number from this document.
- **19 of the references are inside comments and docstrings.**  Two of them
  (`theme.py:1936`, `timeplot.py:106`) are warnings *not* to set a flag.
  Rewriting those changes documentation into something false.
- The dangerous direction is not the rename, it is int-ness (below).

Verify with `PYSIDE6_OPTION_PYTHON_ENUM=16`, which turns forgiving mode off.
Today `import audian` fails immediately under it.  When it does not, the pass
is complete, and that env var is worth putting in CI -- there is no CI gate
at all right now (`.github/workflows/uploaddocs.yml` builds docs only).

Criterion 6.

## What Qt6 actually broke, and what it did not

Worth knowing before touching anything, because the shape repeats.

**Enums are no longer int-like.**  This was the single highest-yield bug of
the whole port.  `(evt[0].button() & Qt.LeftButton) > 0` is a `TypeError`
under PySide6, and that one line shape -- four occurrences in
`databrowser.py` -- accounted for **29 of 45** failing tests, because every
ctrl-click, grip-drag and lane-focus gesture goes through `mouse_clicked`.
Look for `int(...)`, `> 0`, `== 0`, `.count(0)` and dict lookups on anything
that came out of Qt.

**Two of the failures were test bugs, not app bugs.**  A stub returned `0`
where a real event returns a `KeyboardModifier`; an assertion counted
`QPainterPath` elements as `kinds.count(0)`.  Both were correct about the
application and wrong about the binding, and the second one failed on a path
that the failure output itself showed was perfectly correct.  Read what the
assertion is actually comparing before believing it found a regression.

**Shiboken does not own objects the way sip did.**  `theme.strip_pg_menus`
kept pyqtgraph's ctrl widgets alive with `releaseWidget()` plus a Python
reference.  That works under sip and does not under shiboken, and the failure
was total: the application would not open a file.  `releaseWidget` is the API
for widgets an action *created*; it leaves `defaultWidget` set, so
`~QWidgetAction` deletes the widget regardless, and re-parenting the widget
first does not help because the action still believes it owns it.  Stepped:

    after setParent(holder)         6 of 6 alive
    after menu.clear()              0 of 6 alive
    after menu.deleteLater() only   6 of 6 alive

The fix adopts the **action**, not the widget.  If more pyqtgraph teardown is
touched, this is the pattern to expect.

**Font metrics moved, so measured pixel constants moved.**  The Spectrogram
parameter group went 535 -> 501 px, the bar 551 -> 517, the window floor 734
-> 695.  The *relationships* held, so only the constants were re-measured.
Note the direction: the port did not cost width, it returned 39 px, and
`todo.md` records 734 as what would stop audian tiling into half a 14" laptop
panel.

**One keybinding changed in the whole application.**  Qt6's
`QKeySequence.StandardKey.Open` includes the XF86Open media key, so
`open_files` gains `"Open"` beside `"Ctrl+O"`.  The other nineteen
`StandardKey` values are byte-identical.  The golden was regenerated; the
diff is two lines across 124 actions.

**Two pixels of scroll jitter remain, deliberately.**  On the 16-channel
stack the scroll maximum moves between 178 and 180 across parameter tabs
while the bar's own height is identical (168) -- so the bar is not taking
height from the stack.  It is rounding in `lane_content_height`.  Recorded
with a tolerance and a measurement rather than chased, because the real fix
is the layout-solver extraction where that arithmetic becomes testable
without a GUI.

## Claims that were checked and did NOT hold

The reports in `docs/qt6/recon/` state some of these.  Do not re-adopt them.

- **A PySide6 finaliser calling into a destroyed C++ object does not
  raise.**  Both `DataBrowser.close` and `FullTracePlot.close` were called on
  invalidated wrappers and neither raised; run against a real window, neither
  finaliser ran at all.  They were removed anyway, for the three reasons
  under item 1.
- **`QFontDatabase()` is not removed in Qt6.**  It instantiates and warns.
  `theme.py:677` was a cleanup, not a crash.
- **The label-loss window is one turn of the event loop**, not everyday data
  loss.  The queued zero-timer save fires unless nothing turns the loop
  again, which is what `exec()` returning does.  Reproduced both ways.  The
  teardown being skipped is still real; the severity was not.
- **`QWheelEvent.delta()` is removed in Qt6, but the one `ev.delta()` here is
  a `QGraphicsSceneWheelEvent`,** which keeps it.  Upstream pyqtgraph uses it
  identically.
- **The suite does not hang under PySide6.**  One report said ">10 min"; it
  runs in 279 s against the baseline's 197 s.  Slower, not stuck.
- **pyqtgraph was never binding to the wrong library.**  A report framed the
  unforced binding as a live segfault risk.  `.venv-qt6` contains no PyQt6
  and no PyQt5, so the `libOrder` walk could only ever reach PySide6, and it
  did.  Forcing it in `__init__.py` makes an implicit choice explicit; it
  fixed no observed failure.
- **`QFontMetrics.width`, `QApplication.desktop`, `QRegExp`, `setMargin` and
  the high-DPI attributes have zero sites here.**  Someone had already
  migrated away from them.  The general Qt6 removal list is not this
  application's problem list.

## Hazards for whoever runs agents on this

- **Agents inherit the working tree.**  During the done-audit an agent ran
  `uv lock`, and a `git add -A` swept the regenerated lockfile into a commit
  whose message did not mention it.  The change was correct and needed, but
  it was committed unread.  Stage deliberately, or give audit agents a
  read-only copy.
- **Reports go stale while they are being written.**  The same audit reported
  four test regressions and an incomplete screenshot matrix; both had been
  fixed while it ran.  Check the tree before acting on a finding.
- **The suite runs about five minutes.**  Budget for it; `-x` is a false
  economy when one root cause produces thirty failures.
- **Never run `uv sync` from a worktree.**  `UV_PROJECT_ENVIRONMENT` is
  exported globally to the *main* checkout's `.venv`, so a bare `uv sync`
  from any worktree repoints that one shared venv's editable install at that
  worktree's `src`.  Two agents doing it in parallel silently import each
  other's source; this happened, and had to be repaired.  Build a private one
  instead (`uv venv --python 3.12 .venv-qt6`, then
  `VIRTUAL_ENV=$PWD/.venv-qt6 uv pip install -e . ...`) and check
  `__editable__.audian-2.5.pth` points at your own tree before believing a
  result.
- **`tests/test_panelsplitter.py`'s `[resize]` case is load-sensitive.**  It
  drives a window resize behind a 100 ms debounce with `pump(0.3)` of margin,
  and it fails under CPU contention and passes standalone.  Re-run the module
  alone before believing a change caused it.
- **`tests/test_joinmarkers.py` run on its own segfaults at session
  teardown.**  It passes in the full suite, and it behaved that way well
  before the shutdown work.  Do not read it as fallout from whatever you are
  holding.
- **`Audian(paths, ...)` opens ONE recording assembled from several files,
  not one tab per file.**  A second tab comes from `load_files()`.  Getting
  that wrong offscreen costs a wedged process rather than an error: the
  failure path in `load_data` raises a modal `QMessageBox.critical`, and
  there is no window manager to close it.

## Standing constraints

- Every commit leaves a runnable application.  The branch is allowed to fail;
  a half-transplanted tree is not.
- No compatibility layer.  No `audian/qt.py` re-exporting `Signal` and `Qt`,
  no `qtpy`.  The brief names it as a failure mode.
- pyqtgraph stays.  It runs on Qt6 unmodified and nothing profiled justifies
  replacing it.  Give it a boundary instead.
- `~/.config/audian/settings.json` is live user data holding label
  categories.  Its format does not change.
- Plugin compatibility **will** break: `BufferedData`, the base class every
  trace plugin subclasses, carries the signal import.  Any out-of-tree plugin
  declaring its own `pyqtSignal` is source-incompatible.  Release notes.
- `songdetector.py` is not part of this.  777 lines at the repo root, zero
  Qt, zero `audian` imports, referenced by nothing, not in `pyproject`, and
  it no longer runs against the installed matplotlib.  Deleting it is the
  owner's call.
