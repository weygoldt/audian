# Where the migration is, and what to do next

Written to be picked up cold.  Last updated at `8fd3a11`.

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
in.  If you no longer need the comparison, delete it and `uv sync`.

## Running the gates

    QT_QPA_PLATFORM=offscreen .venv-qt6/bin/python -m pytest tests/ -q
    ./scripts/baseline_matrix.sh .devshots/qt6
    .venv-qt6/bin/python scripts/compare_shots.py .devshots/baseline .devshots/qt6

| gate | PyQt5 baseline @ `10c5004` | PySide6 now |
| --- | --- | --- |
| `pytest tests/` | 791 passed, 1 failed, 197 s | **794 passed, 1 failed, 4 xfailed, 279 s** |
| action inventory | 124 actions, 102 bound | same, one key added (below) |
| action trigger sweep | 115 fire clean | 115 fire clean |
| `smoke_test --interact --census` | 56/56, 31 top-level, 0 parentless | **identical** |
| matrix | 8 configs green | 8 configs green |

The one failure is `test_theme_module_is_lint_clean`, which shells out to
`ruff` and finds 16 pre-existing style findings in `theme.py`.  It fails the
same way on `master`.  Not Qt, not this migration; fixing it is a separate
decision about `theme.py`'s house style.

`tests/test_shutdown.py` holds four `strict` xfails describing a real bug.
They are *expected* to fail.  When the fix lands they XPASS and the markers
must come off in the same commit.

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

## Do this next

Three items, chosen by the owner, independent enough to run in parallel
worktrees.

### 1. Clean shutdown -- small, and fixes a bug that exists today

There is no `closeEvent` anywhere in `src/`.  `Audian.close()` is shadowed to
mean "close a tab", so Qt's own close machinery reaches nothing.  Closing via
the window manager -- the most common exit gesture -- runs no teardown: no
label flush, no `CompressedData.close()`, no `PlayAudio.close()`.  The
compression children are not daemons, so `multiprocessing`'s exit handler
*joins* them; on a large recording still being reduced that is a process that
will not exit and shows no window.

- add `closeEvent` to `Audian` (`audian.py:1479`) that runs `quit()`'s body
- `daemon=True` on the workers (`compresseddata.py:343`)
- delete `DataBrowser.__del__` (`databrowser.py:1442`) -- under PySide6 a
  finaliser calling into a destroyed C++ object raises
- rename `DataBrowser.close` (`databrowser.py:3184`) to `shutdown()` so it
  stops shadowing `QWidget.close()`; same for `FullTracePlot.close`
  (`fulltraceplot.py:565`)
- the four xfails in `tests/test_shutdown.py` turn green; remove the markers

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

### 3. Scoped enums -- mechanical, but not a `sed`

194 `Qt.*` references in `src/audian/` alone, more in `tests/`.  They all
*work*: PySide6 6.11 ships forgiving-enum mode by default.  The brief asks
for scoped enums anyway, and the point is that the tree should stop reading
as a PyQt5 application that imports PySide6.

Two traps:

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
- **The suite runs about 4.5 minutes.**  Budget for it; `-x` is a false
  economy when one root cause produces thirty failures.

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
