# Where the migration is, and what to do next

Written to be picked up cold.  Last updated after all three of the items
below landed on `master`.

## State: Stage A is done, Stage B is most of the way

**Everything described here is on `master`.**  The port landed first, then
the three items below as three branches merged in the order clean shutdown ->
threading -> scoped enums.  There is no separate migration branch to chase:
`qt6-migration` is a stale pointer at the pre-Stage-B commit and `master` is
the tested build.  The three items together are 23 commits, 42 files,
+2537/-782.

The application runs on PySide6 6.11.2 / Qt 6.11.2.  Since the port:

- **Shutdown is one path.**  `Audian.closeEvent` exists and the window
  manager's button, `Ctrl+Q` and the last tab all run the same teardown.
- **The DSP runs off the GUI thread.**  There is a `src/audian/tasks/`
  package with a task manager and one compute worker; a refilter no longer
  freezes the event loop, and a test holds that to a number.
- **The enums are scoped.**  All 528 code references, and the suite runs
  under `PYSIDE6_OPTION_PYTHON_ENUM=16` by default so the pass cannot rot.

What still has *not* happened is the rest of the brief's architectural
modernisation: no god-object split, no plugin boundaries, no model/view.
`databrowser.py` is 385 KB and `audian.py` 214 KB, both larger than before.
Roughly fifteen of the 25 definition-of-done criteria are met and several
more are partly met; the honest scoring is under "What is still missing".

Two environments, deliberately separate:

    .venv        PyQt5 5.15.14   -- the recorded baseline, nothing else
    .venv-qt6    PySide6 6.11.2  -- audian installed editable; the real one

Both live *inside a worktree*, not in the main checkout, and each worktree
needs its own -- see the hazards section.  (5.15.14, not the 5.15.11 this
document used to say.)

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
| `pytest tests/` | 791 passed, 1 failed, 197 s | **837 passed, 1 failed, 276 s** |
| action inventory | 124 actions, 102 bound | same, one key added (below) |
| action trigger sweep | 115 fire clean | 115 fire clean |
| `smoke_test --interact --census` | 56/56, 31 top-level, 0 parentless | **identical** |
| matrix | 8 configs green | 8 configs green |
| `import audian` under `PYSIDE6_OPTION_PYTHON_ENUM=16` | n/a | **OK** |

Measured on merged `master` at `4e35927`.  837 is 791 plus the four
shutdown tests that used to be `xfail`, plus the 39 the threading work added
(`test_thread_boundary`, `test_chunked_dsp`, `test_tasks`,
`test_responsiveness`), plus the port's own additions.  There is no `xfail`
column any more and no ERROR row: both are gone with the `close` shadows.

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

## The three items, and what they turned into

Chosen by the owner and done in three parallel worktrees, then merged in the
order below.  The order mattered: clean shutdown is the only one that clears
the suite's ERROR, so until it landed neither of the others could tell a
regression from the baseline, and it renames methods the threading work would
otherwise have called wrongly.  Enums went last because a rename is the
easiest thing to replay over someone else's edits -- and because its gate can
simply be re-run afterwards to catch anything the other two introduced.

The merge was clean: no textual conflicts, and every enum reference the
threading work added was already scoped.

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

### 2. Threading -- PARTLY DONE

The compute path is off the GUI thread and gated.  The I/O, playback and
region paths are not; they are the largest single thing still owed, and they
are itemised under "What is still missing".

There is now a `src/audian/tasks/` package: `tokens.py` (a `CancelToken`
wrapping a `threading.Event`, deliberately not a Qt object so it still works
with no event loop running), `manager.py` (`TaskManager` -- epoch counter,
cancel token, in-flight count, bounded join, shutdown) and `compute.py`
(`ComputeWorker` on one `QThread`).  `plan_chain()` decides on the GUI thread
what a recompute would do, `run_job()` executes it on the worker into **fresh
arrays**, and the GUI swaps them in with one assignment each.

Three decisions are load-bearing and should not be undone casually.

- **One worker thread and a serial pipeline, not a pool sized to cores.**
  The kernels do release the GIL, but at 16 channels each buffer is 34-68 MB
  and they are DRAM-bandwidth-bound: four threads measured **1.44x** for
  `sosfilt`, **1.07x** for `spectrogram` and **0.92x** for `decibel` on this
  four-core box.  What threading buys here is event-loop availability, not
  throughput.  `recon/threading-audit.md` §5.1 recommends
  `setMaxThreadCount(max(2, cpu_count()//2))`; that recommendation is wrong
  for this workload and was not followed.
- **The worker allocates its own output, never filling a live buffer.**  The
  GUI paints from those buffers for the whole time the worker runs.  The
  recon's proposed `RawReady(gen, buf)`, which hands the loader's own buffer
  to a worker, is a torn read: `_recycle_buffer` shifts that array *in place*.
- **The GUI joins before it touches shared memory.**  `cancel_and_wait()`
  runs before every buffer move and every parameter change, and
  `TaskManager.shutdown()` is the **first** statement of `Audian.teardown()`,
  before any browser lets go of the recording the worker is reading.

Chunking landed before any thread existed, which is what made the rest safe:
`sosfilt` chunked at 1 MB carrying `zi`, the spectrogram at 128 hop-aligned
columns with an `(nfft-hop)` carry-back.  Both are bit-identical to the
whole-buffer call (`array_equal`, three chunk sizes each) and both are
*faster* -- `sosfilt` -24%, spectrogram -19.5%, the whole chain 513 -> 407 ms.
Cancellation granularity is 1.4 ms / 10.6 ms instead of a whole 27 s buffer.
The envelope is the exception: `sosfiltfilt` is one call, so its granularity
is 247 ms, which is stated in the code rather than hidden.

Criteria 10, 11 and 16 are met; 12 is met for the workers that exist.

### 3. Scoped enums -- DONE

**528 code references across 23 files, over 20 owner classes.**  Rewritten by
an AST-only tool that touches `ast.Attribute` nodes and therefore cannot see
comments or strings; the 19 prose lines were done by hand in a separate
commit.  The resolver now reports zero unscoped names in the tree.

`tests/conftest.py` sets `PYSIDE6_OPTION_PYTHON_ENUM=16` for the whole suite,
so the pass cannot rot silently.  audian itself never sets it: production
behaviour is identical either way, and this is a lint, not a runtime
requirement.

Three things this turned up that the section it replaces got wrong.

- **`Qt.*` was about 56% of the job, not the job.**  297 of the 528 are
  rooted at `Qt.`; the rest belong to QEvent, QSizePolicy, QSettings,
  QPalette, QKeySequence, QIcon, QDialogButtonBox, QPainter and twelve more.
  The first thing the import hit under the flag was `QIcon.Normal`.
- **The two "do not set" warnings are at `theme.py:1966` and
  `timeplot.py:103`**, not the 1936 and 106 this document used to give -- and
  they drift in *opposite* directions, so no uniform correction finds them.
  Both are `QGraphicsItem.` references, and both were deliberately left
  unscoped: they warn against setting a flag, and scoping them would turn
  documentation into a claim about code that does not exist.
- **The gate cannot be trusted to have taken effect just because the
  environment variable is set.**  Shiboken writes its *own* resolved value
  into `PYSIDE6_OPTION_PYTHON_ENUM` as it loads -- the string `"True"` for
  the default mode -- after which `setdefault` does nothing while the
  variable looks deliberately set.  Only "was PySide6 already imported" can
  tell a developer's choice from shiboken's stamp, which is why `conftest.py`
  checks that and raises rather than passing vacuously.

Criterion 6.

## What is still missing

Against `qt6migration.md`'s 25 criteria.  Fifteen are met, five partly, five
not at all.

| # | criterion | state |
| --- | --- | --- |
| 1-5 | runs on PySide6/Qt6, PyQt5 gone, workflows and science preserved, shims removed | met |
| 6 | Qt6 enum conventions | met -- 528 references, gated under the strict flag |
| 7 | UI/domain coupling reviewed | **partly** -- the data layer lost its plot items; the widgets still own the domain |
| 8 | main window not a god object | **no** -- `audian.py` 214 KB, `databrowser.py` 385 KB, both grew |
| 9 | model/view where appropriate | **no** |
| 10 | deliberate threading/task architecture | met for compute; I/O and playback still synchronous |
| 11 | GUI objects only touched from the GUI thread | met, and enforced by `tests/test_thread_boundary.py` |
| 12 | worker cancellation/shutdown safe | met for the workers that exist |
| 13 | explicit plugin API boundaries | **no** |
| 14 | visualisation separated from domain | **partly** |
| 15 | large data not copied unnecessarily | **partly** -- swap-don't-fill costs one spare buffer per trace |
| 16 | core interactions responsive | met, and held to a number |
| 17-18 | representative tests, compared against baseline | met |
| 19-21 | lifecycle clean, shutdown clean, dead Qt5 code gone | met |
| 22 | understandable package boundaries | **partly** -- `tasks/` is new and clean; the two big modules are not |
| 23 | decisions documented | met -- commit bodies and this file |
| 24 | more maintainable and testable | **partly** |
| 25 | feels like professional desktop software | **partly** -- see the playback freeze below |

The detail, in rough order of how much a user would feel it.

### Still on the GUI thread

- **Playback.**  The largest single item.  `PlayAudio.play()` calls `stop()`
  whenever a stream exists, and `stop()` is a Python fade loop plus
  `sounddevice.sleep(200)` plus a spin on the caller's thread -- so pressing
  play twice is an unconditional **~200 ms freeze**, the only fixed-length one
  left.  `mark_audio` also guesses the cursor position from a 50 ms `QTimer`
  instead of reading `PlayAudio.index`.  This was skipped deliberately rather
  than for lack of a plan: **neither gate can see audio behaviour** -- both
  run offscreen with no device -- so a regression here would ship unnoticed.
  Anyone taking it on should write the observability first.
- **Decoding on scroll.**  Crossing a buffer boundary still decodes on the GUI
  thread (22-43 ms on the two-channel file, more at 16).  The torn-read hazard
  is currently handled by *joining* the workers before every buffer move,
  which is correct but means the scroll is synchronous and now also pays a
  bounded ~11 ms join when a refilter happens to be in flight.  The recon's
  `IoWorker` owning the `DataLoader` is the intended fix.
- **Region read and export.**  `analyze_region`, `save_region` and
  `play_region` still slice through `BufferedArray.__getitem__` on the GUI
  thread under a wait cursor, with no progress and no cancel.  These are the
  only operations whose cost is **unbounded by design** -- a region can be
  arbitrarily long.
- **The decibel half of the spectrogram upload** (~15.8 ms per channel, ~240
  ms for 16) is worker-safe and still runs on the GUI thread.  Moving it means
  splitting `SpecItem`'s upload into a worker-side decibel and a GUI-side
  `setImage`; the render half cannot move at all, because `render()` runs
  inside `paint()`.

### Polling that should be a signal

`overview_timer` still `waitpid()`s every compression child every 250 ms, and
`FullTracePlot._timer` still self-rearms on a backoff -- both while the
navigator may be hidden.  Nothing here is a measured freeze, which is why it
was the first thing dropped.

### Not started at all

The brief's remaining architecture: the god-object split (criterion 8),
model/view for the important collections (9), explicit plugin boundaries (13),
and the package-boundary work (22).  `databrowser.py` grew from 373 KB to
385 KB and `audian.py` from 207 KB to 214 KB over this work -- the threading
made them *more* capable, not smaller.  `01-plan.md`'s Stage C is untouched:
no `LaneLayoutSolver`, no `LabelController`, no `RecordingSession`.

### Known-flaky tests

Two, and they are different in kind.

- `tests/test_panelsplitter.py::test_every_gesture_...[resize]` is
  **pre-existing** and load-sensitive: a window resize behind a 100 ms
  debounce with only `pump(0.3)` of margin.  It failed once for each of three
  independent agents under concurrent load and passed standalone every time
  (150 passed).  The real fix is the layout-solver extraction that makes the
  arithmetic testable without a GUI.
- `tests/test_responsiveness.py` was **introduced by the threading work** and
  is now fixed.  Its guard asserted the synthetic chain took longer than
  `MIN_JOB_MS`, calibrated at 120 ms from a *standalone* run.  Inside the full
  suite the same chain takes 119 ms -- faster, because scipy's caches are
  already warm -- so the guard fired on a 1 ms margin.  The floor is now four
  tick intervals with the chain sized to leave real headroom.  Worth
  remembering as a shape: a threshold measured cold does not hold warm.

### Loose ends worth an hour each

- **`tests/test_theme.py` shells out to a bare `ruff`.**  It is on no `PATH`
  this repo controls, so the same test reports `FileNotFoundError`, or 18
  findings, or a pass, depending on whose shell runs it.  `sys.executable -m
  ruff` and a pinned version would make it a gate instead of a coin flip.
- **`PYSIDE6_OPTION_PYTHON_ENUM=16` is a hazardous CI gate.**  Under it,
  PySide6 6.11.2 **aborts the interpreter** (SIGABRT, exit 134, no traceback)
  when a QtWidgets call gets a wrong argument type, instead of raising
  `TypeError` -- shiboken builds its own error path by reading unscoped names.
  Nothing takes that path today (the suite has no `pytest.raises(TypeError)`),
  and `conftest.py` says so where someone adding one would see it.  There is
  still no CI at all: `.github/workflows/uploaddocs.yml` builds docs only.
- **Peak RSS is not gated.**  Swapping result buffers instead of filling them
  in place raises peak memory by one buffer per derived trace (34 MB filtered,
  68 MB spectrogram at 16 channels).  That is stated in a docstring and
  measured by nothing.
- **`recon/threading-audit.md` now contradicts the tree** in four places: the
  1.5 s refilter, the 424 ms decibel, the `setImage` cost and the pool-sizing
  recommendation.  The corrections are in the commit bodies and in the new
  modules' docstrings, but that report still reads as authoritative.
  `recon/test-inventory.md` is worse: it is a copy of
  `recon/dependency-graph.md` with a different first line, so the one document
  named for the test suite hands you an import graph.

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
Several were stated in *this* file, which is why it now carries the
corrections rather than the claims.

- **`PYSIDE6_OPTION_PYTHON_ENUM=16` has nothing to do with int-ness.**  This
  is the one most worth reading twice, because this document used to put the
  flag and the int-ness warning in the same breath and they are unrelated.
  The flag removes the bare aliases from the owning class namespace and
  changes nothing else; int-ness semantics are byte-identical with and
  without it.  So the flag cannot surface a single int-ness bug, and every
  int-ness bug the port could still have is already live at default settings.
- **"Enums are no longer int-like" is false as a blanket rule.**  Ten of the
  fifty enum types audian touches still are.  The blanket version is worse
  than the truth: it makes the safe cases look unsafe and hides that the
  asymmetry is per-enum.  For the record, the shape that caused 29 of 45
  failures during the port -- `(button() & Qt.LeftButton) > 0` -- has **zero**
  survivors, and the tree applies no `int()`, arithmetic or int-comparison to
  any Qt enum.
- **A pool sized to cores is the wrong shape for this DSP.**  The kernels do
  release the GIL, but at 16 channels the buffers are 34-68 MB and the work is
  DRAM-bandwidth-bound: four threads measured 1.44x / 1.07x / 0.92x.
  `recon/threading-audit.md` §5.1 recommends sizing to cores; that buys
  jitter, memory and lock contention for nothing.
- **The refilter does not cost ~1.5 s.**  It was 513 ms before the chunking
  and 407 ms after, on the 16-channel/20 kHz/27 s buffer this document's own
  number was supposed to describe.  The *shape* of the finding held -- a
  total, blocking freeze -- and the number did not.  Two in-tree comments are
  wrong the same way and in the same direction: `buffereddata.py`'s "424 ms of
  decibel" is 24.5 ms measured, and `specitem.py`'s "~22 ms of `setImage` per
  channel" is 0.1 ms, because pyqtgraph defers everything to `render()`.
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
- **A duration threshold measured standalone does not hold in the suite.**
  `test_responsiveness.py` asserted its synthetic chain took longer than
  120 ms, measured at 163 ms on its own.  In the full suite the same chain
  takes 119 ms -- *faster*, because scipy's caches are already warm -- and the
  guard fired on a 1 ms margin.  Calibrate any such floor against the warm
  process, and leave the margin in the work rather than in the constant.
- **Verify the `recon/` reports before acting on them.**  They are dense and
  genuinely useful and they are also where most of the disproved claims in
  this file came from.  Across this round about twenty-five stated claims
  were checked and did not hold -- including several in *this* document and
  several in the instructions handed to the agents.  Their line numbers have
  drifted non-uniformly (+30 in `theme.py`'s back half, -3 in `timeplot.py`),
  and at least one citation points at a different statement than the one it
  describes.  Re-locate everything by symbol.
- **Three agents in three worktrees worked, and the cap is the machine.**
  Concurrency is `min(16, cpus - 2)`, so a four-core box runs two at a time
  whatever you ask for -- and that contention is what surfaces the
  `[resize]` flake above.

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
