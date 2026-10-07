# The wavetracker plugin: design

This is the design of `audian_plugins.eodsorter`, the plugin that replaces
wavetracker's legacy EOD sorter (`wavetracker/gui/eodsorter.py`).  It runs
wavetracker from inside audian, draws what it found on the spectrogram, and
gives the reader the tools to correct it.  It is written for the people who
will implement it, and they are expected to follow it literally: where a
choice had to be made, it has been made here, and a deviation is a change to
this document first.

Paths are relative to the claudian repository unless they start with
`wavetracker/`, which means `/home/weygoldt/wrk/tools/wavetracker`.  Line
numbers are as of claudian `wavetracker-plugin` and wavetracker `53353a2`.

Contents:

1. What the legacy sorter is, and what was wrong with it
2. Decisions this design rests on
3. The data model
4. Running wavetracker
5. Interaction design
6. Drawing and performance
7. Modules and interfaces (three parallel work packages)
8. Saving, recovery, and the unsaved-changes rule
9. Test plan
10. Out of scope, and open questions

---------------------------------------------------------------------------

## 1. The legacy EOD sorter

### 1.1 How it works

`wavetracker sorter DIR` asks for the recording date on stdin, then opens a
PyQt5 window with one matplotlib `FigureCanvasQTAgg`.  It loads the
per-detection arrays (`fund_v`, `idx_v`, `sign_v`, `ident_v`, `times`) and a
background spectrogram, and draws **one `Line2D` with markers per identity**
over a gaussian-interpolated `jet` image.

Editing is a modal tool palette of eight checkable icons with no text:
create-from-spectrogram (fill), connect, group connect, re-assign, cut,
delete trace, group delete, zoom.  Every gesture is an invisible rectangle in
data coordinates (no rubber band is drawn), the picked trace is "the first
detection in array order inside the rectangle", and most tools need a second
step, **Return**, to commit.  Fill commits on release.  Undo is one level,
snapshots `ident_v` on every Return press whether or not anything happened,
and does not cover fill.  Save overwrites the tracker's own `.npy` files in
place, with no backup, no confirmation and no prompt on exit.

Every redraw is a full `canvas.draw()` of every line and marker in the
recording, often two or three times per action.  With thousands of
identities and millions of points this is why the tool is slow; with the
modal, invisible, two-phase gestures it is why it is hard to use; and with
PyQt5's `qFatal` on an exception in a slot, several of the bugs below kill
the process and take every unsaved edit with it.

### 1.2 Bugs, and how this design removes each class

Severity as in the analysis: **Critical** = data loss or corruption, or a
main workflow unusable; **High** = abort or wrong edit in a common case;
**Medium** = edge case or stale UI; **Low** = cosmetic.

The right-hand column names the class of fix.  The classes are:

* **F (format)**: the plugin reads and writes exactly wavetracker's
  directory format through one module (`model.py`), never reorders rows,
  appends to *every* per-detection array including `cplx_v`, and draws on
  audian's own correctly registered spectrogram instead of re-implementing
  one.
* **P (pure ops)**: every edit is a pure function that validates its input
  and either returns a complete plan or raises `EditRejected` with a reason,
  *before* anything is mutated.  A rejected edit is a hint line, never a
  traceback, never a half-applied change.  PySide6 does not `qFatal` on a
  slot exception, and every slot that touches the model is additionally
  wrapped so an unexpected error is reported through `browser.notify` and
  leaves the model at its last consistent revision.
* **I (ids)**: ids are compared with `np.isnan`/`==`, never truthiness; new
  ids come from a monotonic counter that is never derived from `nanmax`;
  id 0 and all-NaN inputs are explicit test cases.
* **S (selection)**: picking is by proximity in screen pixels, with hover
  feedback before the click; there is no hidden selection state; every
  gesture shows what it will do while it is being made; Esc cancels.
* **V (invariant)**: at most one detection per identity per frame is
  enforced by every operation with a stated, deterministic conflict rule,
  and asserted by the test suite after every operation.
* **U (undo)**: a multi-level command history of exact diffs, covering
  appended rows.
* **R (render)**: the overlay is a pure function of the model revision and
  the view; there are no per-trace handles to forget.
* **E (environment)**: no stdin prompt, no application-wide key filter, no
  writes outside the results directory, no pyplot.

| # | Bug (condensed) | Sev | Fix |
|---|---|---|---|
| B1 | Fine spectrogram loader incompatible with the current pipeline; sorter cannot open `--save-spec` results | Critical | F: audian's spectrogram is the background; `fine_spec.npy` is only read (as a real `.npy`, `[time, freq]`) for peak snapping |
| B2 | Fill reorders and lengthens arrays, `cplx_v.npy` not rewritten, `Results.load` misaligned | Critical | F: append-only, every per-detection array including `cplx_v` |
| B3 | `if not active_id`: id 0 cannot be deleted, cut or connected | High | I |
| B4 | Connecting a trace with itself unassigns it, then double-removes a line and aborts | Critical | P, V: `merge` of one id is rejected ("nothing to merge") |
| B5 | Save mode from an unconnected combobox; channel switch drops or overwrites edits | Critical | F: one format, no legacy `all_*` channel format |
| B6 | Undo snapshot on every Return; undo after fill restores a shorter `ident_v` and aborts | High | U |
| B7 | `nanmax(ident_v)+1` on untracked data assigns NaN | High | I |
| B8 | `max` of empty array on all-NaN data | Medium | I |
| B9 | Group connect on an empty rectangle aborts | High | P: empty selections reject with a hint |
| B10 | Cut point is "last detection before x"; left of the trace start raises | Medium | S, P: cut position is hovered and previewed; a cut that leaves one side empty is rejected |
| B11 | Release outside the axes gives `None` coordinates and a `TypeError` | Medium | S: gestures are owned by a per-lane item and clipped to it |
| B12 | Sticky selections; second rectangle ignored; wrong trace connected | High | S |
| B13 | Re-assign and fill create duplicate detections per identity and frame | Medium | V |
| B14 | Undo and channel change reset "zoom home" to the current view | Medium | R: navigation is audian's |
| B15 | Arrow-key pan draws before ticks are recomputed | Medium | R: navigation is audian's |
| B16 | Fine-spectrogram button enabled without one; aborts | High | F |
| B17 | Fine spectrogram off by one, misregistered, unbounded memory | Medium-High | F |
| B18 | File → Open never re-shows the dialog; cancel loads the CWD or aborts | High | P, E: open is a dialog with an explicit cancel path and a structural check that reports instead of raising |
| B19 | Fill leaves a ghost line after undo | Low-Medium | R |
| B20 | Unassigned overlay never refreshed | Medium | R |
| B21 | Coarse spectrogram shifted by up to one pooled bin | Low-Medium | F |
| B22 | `spec.npy` assumed 0-2000 Hz | Low | F; no hard-coded frequency limits anywhere |
| B23 | Clock tick labels off by the recording's seconds | Low | R: audian's time axis |
| B24 | App-wide arrow-key filter swallows keys in every widget | Low-Medium | E: the key router (5.9) claims keys only in edit mode, only over a lane or the panel, never in a text field |
| B25 | Plot export walks `..`, writes relative to CWD, may abort | Medium | E: no export of its own; audian's screenshot (Ctrl+Alt+S) |
| B26 | Dead `C` shortcut | Low | E |
| B27 | `reset_variables` defined twice | Low | E |
| B28 | `plt.figure()` leaks a pyplot figure | Low | E |
| B29 | Empty re-assign leaks plot handles | Low | R |
| B30 | Date prompt on stdin; non-tty stdin crashes | Medium | E: audian knows the recording's time |
| B31 | Connect silently keeps the second trace's points at shared frames | Medium | V: local-median rule, conflicts shown in the preview and reported |
| B32 | Group connect merges any id a stray point touched; order-dependent | Medium | S, V: merge previews every id it will take; order-independent rule |

The performance and usability pains (section 4 of the analysis) are
answered in sections 5 and 6.

---------------------------------------------------------------------------

## 2. Decisions this design rests on

1. **Package** `src/audian_plugins/eodsorter/`, panel factory
   `audian_wavetracker_panel`, menu **Plugins → Wavetracker**, side-panel tab
   titled **Tracks**.  `frequencybands` stayed where it was at first; it has
   since been removed (2026-10-07), and this plugin replaces it.
2. **wavetracker runs out of process, from the same environment.**
   wavetracker is a regular dependency of claudian (a git source in
   `[tool.uv.sources]`, never its PyQt5 `gui` extra), so `uv sync` installs
   both into one venv and there is nothing to discover.  The tracker still
   holds the GIL for its whole run, so a runner script is executed in a
   child process of `sys.executable` (4.1).  The in-process plugin imports
   numpy, PySide6, pyqtgraph, soundfile and `audian.pluginapi`; of
   wavetracker only its top-level package (for the version, which costs no
   torch or numba), and never `torch`.
3. **The data model is wavetracker's own arrays**: `fund_v`, `idx_v`,
   `ident_v` (NaN = unassigned), `sign_v`, `times`, and `cplx_v` when
   present.  Edits change `ident_v`, except adding detections, which
   **appends** rows to every per-detection array and never reorders.  Rows
   are never deleted; they are unassigned.  Id 0 is a valid id.  New ids
   never reuse old ones, across sessions.
4. **Invariant**: at most one detection per identity per frame.  Every
   operation states its conflict rule (3.4).
5. **Undo/redo** is a command history of diffs, at least 100 levels, with an
   edit-history list in the panel that jumps to any entry.
6. **Saving** is atomic, never destroys the tracker's original identities
   (`ident_v.tracked.npy`), and leaves a directory that
   `wavetracker.results.Results.load` still reads.

---------------------------------------------------------------------------

## 3. The data model

All of this lives in `model.py`, which imports numpy and the standard
library and nothing else (no Qt, no `audian`, no `wavetracker`), so it is
tested in the fast suite.

### 3.1 Arrays

| attribute | dtype, shape | meaning |
|---|---|---|
| `fund` | float64 (n,) | fundamental [Hz] |
| `idx` | int64 (n,) | frame index into `times` |
| `ident` | float64 (n,) | identity; NaN = unassigned; integer-valued |
| `tracked` | float64 (n,) | the tracker's identity for this row (the backup); NaN for rows the reader added by hand |
| `sign` | float32 (n, c) | power per electrode; NaN rows for hand-added detections |
| `cplx` | complex64 (n, c) or None | complex spectrum; NaN+NaNj rows for hand-added detections |
| `times` | float64 (frames,) | frame centre times [s] relative to the start of the recording audian has open |

`c` is the number of electrodes wavetracker used.  An empty session has
`c = None` until the first rows arrive.

Rows are stored in growable buffers (capacity grows by 1.5x); the public
attributes are read-only views of the first `n` rows
(`flags.writeable = False`), so nothing outside the model can mutate them.

Indexes kept up to date incrementally:

* `by_frame`: row numbers sorted by `(idx, row)`.  Built once with a stable
  argsort; appended rows are merged in with `np.searchsorted` + `np.insert`,
  never by re-sorting.
* `rows_of(id)`: for every id, its rows sorted by frame, in a dict
  `float -> np.ndarray[int64]`.  An edit rebuilds only the entries of the ids
  it touched.

### 3.2 The frame grid

wavetracker's frames are regular: `times[k] = (s0 + k*step + nfft/2) / rate`
(`wavetracker/io.py`, `FrameLayout.times`).  The model keeps that grid,
because it is what lets a snippet run line up with the session frame by
frame (4.3).

```python
@dataclass(frozen=True)
class FrameGrid:
    rate: float      # Hz
    nfft: int        # samples
    step: int        # samples, = max(1, int(nfft * (1 - overlap_frac)))
    s0: int          # first sample of frame 0
    n_frames: int

    def times(self) -> np.ndarray: ...
    def frame_range(self, t0: float, t1: float) -> tuple[int, int]:
        """Frames whose centre lies in [t0, t1]: (k0, k1), k1 exclusive,
        clipped to [0, n_frames)."""
    def sample_range(self, k0: int, k1: int) -> tuple[int, int]:
        """Samples a run of frames k0..k1-1 needs:
        [s0 + k0*step, s0 + (k1-1)*step + nfft)."""

    @classmethod
    def for_recording(cls, rate, n_samples, nfft, overlap_frac) -> "FrameGrid":
        """The grid wavetracker would use for the whole recording (s0 = 0)."""
    @classmethod
    def from_results(cls, meta: dict, times: np.ndarray) -> "FrameGrid | None":
        """From wavetracker.json (rate, start, frame_step, config.spectrogram.nfft).
        None when `times` does not match the formula within 1e-6 s at every
        frame -- such a session can be edited but not merged with snippets."""
```

The step formula is copied from `wavetracker.spectrogram.step_size`; a test
pins it against hard-coded values from wavetracker.

### 3.3 Identities

* `next_id: int` starts at `1 + max(ident, tracked, the next_id recorded in
  eodsorter.json)` over everything ever seen, or 0 for an empty session.  It
  only grows.  Undoing an edit that created ids does **not** lower it, so an
  id that was ever handed out is never handed out again, also not after
  saving and reopening (the counter is persisted, 8.1).
* Ids are integer-valued floats.  Every comparison is
  `np.isnan(x)` or `x == id`; truthiness of an id is a lint-level bug in
  review.
* Per-id metadata: `labels: dict[int, str]` (a free-text name, e.g. "female
  A") and `notes: dict[int, str]`.  Optional, undoable, saved in
  `eodsorter.json`.

### 3.4 Operations and their conflict rules

Every operation is split into **plan** and **apply**:

```python
plan = ts.plan_merge([7, 12], into=7)   # pure: reads the model, mutates nothing
change = ts.apply(plan)                  # mutates, pushes the plan onto the history
```

`plan_*` raises `EditRejected(reason: str)` for an edit that cannot be
done; the reason is written for the reader ("cut at 01:12.4 would leave id
12 empty after the cut").  The UI calls `plan_*` on every hover or stroke
update to draw the preview, and `apply` on commit, so the preview is by
construction exactly what will happen.

```python
@dataclass(frozen=True)
class Plan:
    label: str                 # one line for the history, e.g. "Merge 12 into 7 (3 conflicts)"
    rows: np.ndarray           # int64, rows whose ident changes, sorted
    new: np.ndarray            # float64, their new ident (NaN = unassign)
    dropped: np.ndarray        # int64 subset of rows unassigned *because of a conflict*
    append: Append | None      # rows to add (only add_detections, replace_span)
    created: tuple[int, ...]   # ids this plan creates
    span: tuple[float, float, float, float]  # t0, t1, f0, f1 of everything touched
    revision: int              # the model revision the plan was made at
```

**The local-median rule.**  Wherever two candidate rows would end up with
the same id in the same frame, keep the one whose frequency is closest to
the median frequency of that id's *other* rows within ±`W` frames
(`W = 30`, wavetracker's `resolve_duplicates` default), computed on the
result of the operation; if there are no other rows in that window, the
median of the candidates.  This is the rule wavetracker itself applies
after stitching (`wavetracker/stitching.py:166`), so a corrected track and a
tracked track mean the same thing.  It does not depend on the order ids
were picked (B31, B32).  The model re-implements it in numpy; a test
compares it against a frozen copy of wavetracker's function.

| Operation | Effect | Conflict rule |
|---|---|---|
| `plan_unassign(rows)` | `ident[rows] = NaN` | none |
| `plan_delete_ids(ids)` | unassign every row of these ids | none |
| `plan_new_id(rows)` | rows get one fresh id | several selected rows in one frame: local-median rule among them; losers **keep their old id** and are listed in `dropped` |
| `plan_assign(rows, target)` | rows get the existing id `target` | the reader pointed at these rows, so **selected rows win** over the target's existing row in the same frame; that displaced row is unassigned (`dropped`). Several selected rows in one frame: local-median rule among them; losers keep their old id |
| `plan_merge(ids, into)` | every row of `ids` gets `into` (`into` must be one of `ids`; the UI passes the first-picked id) | frames where two or more of the merged ids have a row: local-median rule on the merged track; losers are unassigned (`dropped`) |
| `plan_cut(id, t, new_part="after")` | rows of `id` with frame time `>= t` (or `< t` for `"before"`) get a fresh id | none; rejected if either side would be empty |
| `plan_swap_after(a, b, t)` | for frame time `>= t`, rows of `a` become `b` and rows of `b` become `a` | none (both already satisfy the invariant) |
| `plan_add(frames, freqs, target=None, sign=None, cplx=None)` | appends one row per frame, assigned to `target` or to a fresh id | frames where `target` already has a row are **skipped** (not appended); several candidates in one frame: the one closest to the stroke centre |
| `plan_replace_span(snippet, stitch=True)` | accept a snippet run (4.3): unassign every *assigned* session row with frame in `[k0, k1)`, append the snippet's rows with fresh ids, then optionally stitch at the edges | none inside the span (the session's rows there were just unassigned); edge stitching rule in 4.3 |
| `plan_apply_ident(ident, label)` | replace the whole `ident` (cleanup result, a `ident_v_cleaned_n*.npy` variant) | the input is checked for duplicates; any found are resolved with the local-median rule and reported |
| `plan_revert(rows=None)` | `ident = tracked` (for all rows, or the given rows) | none |
| `plan_set_label(id, text)`, `plan_set_note(id, text)` | metadata only | none |

`apply` asserts the invariant on the ids it touched (cheap: only those ids'
rows) in every build; the test suite asserts it for the whole model after
every operation.

### 3.5 Change notifications

The model is not a `QObject`.  `apply`, `undo`, `redo` and `jump` return a
`Change`, and the panel re-emits it as a Qt signal:

```python
@dataclass(frozen=True)
class Change:
    revision: int
    ids: np.ndarray            # every id whose rows changed (old and new), no NaN
    rows: np.ndarray           # rows whose ident changed
    appended: int              # rows added (>0) or removed by undo (<0)
    frames: tuple[int, int]    # [k0, k1) touched
    unassigned: bool           # whether the unassigned pool changed
    label: str
```

### 3.6 History

```python
@dataclass
class Command:
    label: str
    rows: np.ndarray       # int32 if n < 2**31, else int64
    old: np.ndarray        # float64
    new: np.ndarray | float  # a scalar when every row got the same id (merge, new_id, cut)
    append: Append | None  # the appended row block, kept for redo
    meta: tuple | None     # (key, old, new) for label/note edits
    span: tuple            # t0, t1, f0, f1
    time: float            # wall clock, for the history list

class History:
    MAX_DEPTH = 200
    MAX_BYTES = 256 * 2**20
    entries: list[Command]   # oldest first
    position: int            # number of applied entries; entries[position:] are the redo branch
    def can_undo(self) -> bool
    def can_redo(self) -> bool
```

* Undo writes `old` back into `rows`, then, if the command appended rows,
  truncates every array back to its previous length.  This is safe because
  appended rows are always the last rows and the history is a stack.
* Redo writes `new` and re-appends the stored block.
* A new edit after an undo discards the redo branch.
* `TrackSet.jump(i)` undoes or redoes until `position == i`, and returns one
  combined `Change`.
* When either limit is exceeded, the oldest entries are dropped.  Dropping
  is a fact the panel shows ("history starts at 14:02"), not a silent
  truncation.
* `saved_position` marks the entry the file on disk corresponds to;
  `is_dirty()` is `position != saved_position` or unsaved metadata.  If the
  saved entry is dropped from the history, the model is dirty until saved.

Memory: about 20 bytes per changed row (int32 row + two float64), so 200
edits of 50,000 rows each is 200 MB, inside the cap.

### 3.7 Snippets

```python
@dataclass(frozen=True)
class Snippet:
    k0: int                # session frames covered: [k0, k1)
    k1: int
    fund: np.ndarray
    idx: np.ndarray        # already in session frames
    ident: np.ndarray      # the snippet's own ids (local; remapped on accept)
    sign: np.ndarray
    cplx: np.ndarray | None
    meta: dict             # the run's wavetracker.json (thresholds, config)

def load_snippet(run_dir, grid: FrameGrid, k0: int) -> Snippet
    """Read a run's directory, add k0 to idx_v, and check that the run's
    times + offset equal grid.times()[k0:k0+len(times)] within 1e-6 s.
    Raises ResultsError if not."""
```

### 3.8 Queries the UI needs

```python
ts.ids() -> np.ndarray                       # sorted, no NaN
ts.rows_of(id) -> np.ndarray                 # sorted by frame
ts.rows_in_frames(k0, k1) -> np.ndarray      # slice of by_frame
ts.stats(ids=None) -> IdStats                # structured array: id, n, k_first, k_last,
                                             #   t_first, t_last, f_min, f_median, f_max
ts.issues(kinds, params) -> list[Issue]      # 5.8
```

`stats` is computed for all ids once (one `lexsort` by `(fund, ident)` for
the median, `np.add.reduceat` for the rest) and then incrementally for the
ids in each `Change`.

---------------------------------------------------------------------------

## 4. Running wavetracker

### 4.1 The interpreter

There is no choice to make: the runner is `sys.executable`.  claudian
depends on wavetracker, so the interpreter running audian has it, and
`runner.RunnerClient` starts `sys.executable -u wtrunner.py`.  (Its
keyword-only `program` argument exists for the tests, which run the runner
against the stub in `tests/data/fake_wavetracker`; the panel never sets it.)

Earlier versions searched for a separate Python (a panel setting,
`WAVETRACKER_PYTHON`, the `wavetracker` command on PATH) and had a
**Python** field with **Choose...** and **Check**.  All of that is gone,
and a stale `python` key in the settings is ignored and dropped on the next
save.

`runner.wavetracker_status()` imports `wavetracker` (its `__init__` only
reads the package version, so neither torch nor numba is loaded) and
returns its version, or, when the import fails, the error
"wavetracker is not installed in this environment: <exception>".  The
Run settings line shows "wavetracker 0.3.0 · Python 3.14.7", which becomes
"✓ wavetracker 0.3.0 · Python 3.14.7 · cpu, cuda" in green once the
runner's `hello` (4.4) has reported its devices.  With a broken install the
line is that error in red, and **Track visible** / **Track recording** open
the Run settings at it instead of running; opening existing results and
every edit still work.

To work on wavetracker itself, install the checkout over the locked one
with `uv pip install -e ../wavetracker`; to move the lock to wavetracker's
latest commit, `uv lock --upgrade-package wavetracker`.

**Settings.**  Plugin preferences are kept with audian's own settings store,
under the key `"eodsorter"` as a versioned dict:

```json
{"version": 1, "device": "auto",
 "brush_px": 14, "sticky_brush": true, "show_unassigned": true,
 "point_px": 3, "gap_break_s": 0.5, "results_dirs": {"<recording path>": "<results dir>"}}
```

This needs two names added to `src/audian/pluginapi.py`:
`from .audian import save_setting, settings` (both exist in
`src/audian/audian.py:1097,1148`, never raise, write atomically, and are
already redirected by `tests/conftest.py`).  That two-line core change is
part of work package C.  Until it lands the plugin falls back to
`QSettings("audian", "eodsorter")`.

### 4.2 Whole-recording runs

* **Input**: `source_paths()` (copied from `frequencybands/panel.py:882`,
  the full ordered file list of the browser's timeline).  A single path is
  passed as a string; several as a list, which needs the upstream change in
  4.6.  If the runner's `hello` lacks the `multi_input` capability and the
  session has several files, "Track recording" is disabled with the hint
  "needs a wavetracker with list input (see docs/eodsorter-design.md 4.6)".
* **Range**: the whole recording, or "Track range…" with start and duration
  fields prefilled from the view.
* **Output directory**: `<dir of first file>/<stem of first file>-wavetracker/`,
  editable in the panel.  If it already holds results, a dialog offers
  **Open existing**, **Replace** (the old directory is renamed to
  `<name>.old-<YYYYmmdd-HHMMSS>`, never deleted) or **Choose another**.
* The runner writes into `<output>.partial-<pid>/` and renames it to
  `<output>` only after detection, tracking and saving succeeded, so a
  cancelled or failed run never leaves a half directory where results are
  expected.  The panel removes the partial directory after a cancel.
* **Result**: the panel loads the directory as the session (`TrackSet.load`).
  If the current session has unsaved edits when the result arrives, a dialog
  offers **Save edits, then load**, **Discard edits and load** or **Keep
  current** (the new results stay on disk; "Open results…" reaches them).
  Editing is never locked while a run is going.

### 4.3 Snippet runs ("Track visible")

The reader zooms to a region where the tracking is wrong and asks
wavetracker to try again on just that.

1. **Grid.**  If a session exists and has a regular grid, the snippet uses
   the session's `nfft`, `overlap_frac` and `exclude_channels` (those panel
   fields are locked while a session is loaded, with the reason in their
   tooltip).  Without a session, the grid is
   `FrameGrid.for_recording(rate, frames, nfft, overlap_frac)` from the
   panel's fields, and the snippet creates the session.
2. **Frames.**  `(k0, k1) = grid.frame_range(view_t0, view_t1)`.  Rejected
   with a hint if `k1 - k0 < 2` ("zoom out: wavetracker needs at least two
   FFT windows, 1.8 s here").  A hint (not a rejection) when
   `k1 - k0 < interference.min_frames` (30): "comb removal needs 30 frames;
   this snippet runs without it".
3. **Length cap.**  Snippets longer than `MAX_SNIPPET_S = 300` s are
   refused with "use Track range… for spans this long" (the export would be
   gigabytes of WAV).
4. **Export.**  `SnippetExporter` (a `QObject` on the panel's own `QThread`)
   opens its own loader with `open_files(paths, 10.0, 0.0)` (never
   `browser.data`), reads samples `grid.sample_range(k0, k1)` from all
   channels in 10 s chunks, and streams them into a temporary
   `snippet.wav` with `soundfile.SoundFile(..., subtype="FLOAT")`.  Reading
   from audian's loader means the snippet is exactly the audio audian shows,
   including multi-file joins.  Temporary files live in
   `tempfile.mkdtemp(prefix="audian-wt-")` and are deleted once the result
   is loaded, or on cancel.
5. **Thresholds.**  A snippet's noise estimate differs from a whole run's.
   The panel offers **Thresholds: from loaded results** (the default when the
   session's `wavetracker.json` has `low_threshold`/`high_threshold`; they are
   passed as config overrides), **estimate from snippet**, or **fixed** with
   two fields.
6. **Run.**  The runner runs `detect` + `track_results` on the WAV (4.4,
   op `detect`) into a temporary directory.
7. **Load.**  `load_snippet(tmpdir, grid, k0)` offsets `idx_v` by `k0` and
   checks the times line up (they do by construction; the check catches a
   wrong rate or a non-integer rate rounded by soundfile, which is then
   reported).
8. **Provisional layer.**  The snippet is drawn on top, dashed, in its own
   colours, while session tracks inside `[k0, k1)` are dimmed to 35 %
   opacity.  A bar in the panel says "Snippet 12:00.0–12:45.3 · 8 tracks ·
   1,203 points" with **Accept** and **Discard**.  Running another snippet
   replaces an unaccepted one.  Edit tools act on the session, not the
   provisional layer.  Because the layer looks like tracks but is not one
   (a reader who never pressed Accept found nothing selectable, on
   2026-10-07), a pending snippet is never a silent dead end: over its span
   the hover box and the hint line say "Snippet pending — press Enter (or
   Accept) to edit these tracks", a click or drag there does nothing but
   repeat that, **Enter** accepts while a snippet is pending (before the
   issue suggestion it otherwise accepts), and the Accept button is drawn
   in the primary colour.  Accepting stays explicit: an edit gesture never
   accepts implicitly, since accepting replaces the session's tracks in the
   span.
9. **Accept** is one undoable command, `plan_replace_span(snippet,
   stitch=True)`:
   * every assigned session row with frame in `[k0, k1)` is unassigned (not
     deleted);
   * the snippet's rows are appended; snippet id `j` becomes a fresh id;
   * **edge stitching** (on by default; checkbox "Join to tracks at the
     edges"): a snippet track whose first row is within `g` frames of `k0`
     adopts the id of the session track that has a row in
     `[k0 - g, k0)` whose last frequency is within `tol` Hz of the snippet
     track's first frequency, where `g = round(tracking.max_dt / frame_step)`
     and `tol = tracking.freq_tolerance`, both from the session config.  The
     closest candidate by |Δf| wins, and each session id is used at most once
     per edge.  The right edge works the same way with `[k1, k1 + g)`.  A
     snippet track matched at the left edge to `A` and at the right edge to a
     different `B`: it adopts `A`, and `B`'s rows at frames `>= k1` are
     relabelled to `A` only if `A` has no rows at frames `>= k1`; otherwise
     the right edge is left unjoined and the history line says so ("2 edges
     left unjoined: ambiguous").
   * Without a session, accept creates the session from the snippet.

### 4.4 The runner process and its protocol

`wtrunner.py` lives in the plugin package but is never imported by it.  It
is executed as `[python, "-u", "<path to wtrunner.py>"]` with `QProcess`, and
imports only the standard library, numpy and wavetracker.  It must run on
Python ≥ 3.11 (wavetracker's floor) and must not import `audian` or
`audian_plugins`.

**Lifetime.**  One runner per panel, started on the first job (not at panel
open, so opening the tab costs nothing).  It stays alive between jobs, so
the torch import (1.3 s) and the numba cache load are paid once; it exits
after 10 minutes idle or on `shutdown`.  Cancel = `terminate()` (SIGTERM),
then `kill()` after 2 s; the next job starts a new process.  Cleanup runs
in a separate **one-shot** runner (`wtrunner.py --oneshot`), so a memory
limit can be applied without torch's address space in the way (4.5).

**Stdout is the protocol channel and nothing else.**  The first thing the
script does, before importing wavetracker:

```python
_proto = os.fdopen(os.dup(1), "w", buffering=1)  # private handle to the real stdout
os.dup2(2, 1)                                     # fd 1 now points at stderr
sys.stdout = sys.stderr                            # prints from wavetracker/cleanup go to stderr
```

Every protocol message is one JSON object per line on `_proto`.  stderr is
read by the client and forwarded as `log` lines (level `debug`), and kept in
a 200-line ring buffer that is attached to any error report.  The
`wavetracker` logger gets a handler that emits `log` messages.

**Messages from the client (stdin), one JSON object per line:**

```json
{"id": "j3", "op": "detect",
 "input": "/data/rec.wav" | ["/data/a.wav", "/data/b.wav"],
 "output_dir": "/data/rec-wavetracker.partial-1234",
 "final_dir": "/data/rec-wavetracker" | null,
 "config_path": "/path/config.yaml" | null,
 "config": {"harmonic_groups": {"min_freq": 400.0, "max_freq": 1200.0}},
 "start": 0.0, "duration": null, "device": "auto", "track": true}

{"id": "j4", "op": "peaks",
 "input": "/data/rec.wav" | [...],
 "nfft": 32768, "step": 3276, "s0": 0, "channels": [0, 1, 2, 3],
 "frames": [1200, 1201, 1202], "fmin": [801.0, 801.2, 801.4], "fmax": [809.0, 809.2, 809.4],
 "out": "/tmp/audian-wt-x/peaks.npz", "device": "auto"}

{"id": "j5", "op": "cleanup", "dir": "/tmp/audian-wt-y", "n_fish": 2,
 "params": {"stride_minutes": 10, "overlap_frac": 0.2, "freq_tolerance": 2.5,
            "time_tolerance_minutes": 5, "density_threshold": 0.1},
 "mem_limit_bytes": 34359738368}

{"op": "shutdown"}
```

* `detect`: `cfg = Config.load(config_path)`, then the `config` dict is
  deep-merged onto `cfg.to_dict()` and rebuilt with `Config.from_dict`
  (unknown keys come back as a `bad_request` error).  Then
  `detect(input, output_dir, cfg, start, duration, device, progress=cb)`,
  `track_results` if `track`, `results.save(output_dir)`, and if
  `final_dir` is given, `os.replace(output_dir, final_dir)`.
* `peaks`: for each requested frame, reads `nfft` samples at
  `s0 + frame*step` from `input` (with `open_recording`), computes the
  power spectrum summed over `channels` with wavetracker's
  `PowerSpectrogram`, takes the maximum within `[fmin, fmax]`, refines it
  with a parabolic fit on the dB spectrum, and writes `fund`, `sign`
  (per-electrode power at the peak bin) and `cplx` to `out` (`.npz`).  Large
  arrays travel by file, never inside JSON.
* `cleanup`: sets `MPLBACKEND=Agg` before any import, sets
  `wavetracker.postprocessing.cleanup.show_results = False`, applies
  `resource.setrlimit(RLIMIT_AS, mem_limit_bytes)` (Linux; skipped
  elsewhere), runs `cleanup.main(dir, n_fish=..., **params)`, then checks
  that `idx_v_cleaned_n{N}.npy` and `fund_v_cleaned_n{N}.npy` equal the
  input arrays (cleanup must only change identities; anything else is an
  `error`).

**Messages from the runner (stdout):**

```json
{"type": "hello", "protocol": 1, "python": "3.12.3", "wavetracker": "0.3.0",
 "capabilities": ["detect", "peaks", "cleanup", "multi_input"],
 "devices": ["cpu", "cuda"], "default_config": {...Config().to_dict()...}}
{"type": "progress", "id": "j3", "stage": "detect", "done": 1830, "total": 87840, "text": "75x realtime"}
{"type": "progress", "id": "j3", "stage": "track", "done": 0, "total": -1, "text": "tracking identities"}
{"type": "log", "level": "info", "text": "Noise std 2.31 dB -> thresholds ..."}
{"type": "result", "id": "j3", "output_dir": "/data/rec-wavetracker",
 "n_detections": 21034, "n_ids": 14, "timings": {"total": 8.0, "tracking": 1.07}}
{"type": "error", "id": "j3", "kind": "bad_request" | "exception" | "oom" | "input",
 "message": "Selected data is shorter than two FFT windows.", "traceback": "...", "stderr_tail": "..."}
```

* `hello` is sent after importing `wavetracker` and `wavetracker.config`
  only (not torch).  `devices` is filled lazily: `["auto", "cpu"]` in
  `hello`, and the first `detect` sends a second `hello` with the probed
  list.  `multi_input` is reported when `wavetracker.io.MULTI_INPUT` exists
  (4.6).
* `total = -1` means indeterminate (tracking and cleanup have no progress
  hooks); the panel shows a busy bar with the stage text.
* A `MemoryError` is reported as `oom`; a non-zero exit without an `error`
  line is reported by the client as `exception` with the stderr tail; exit
  by our own `terminate()` is reported as `cancelled`.
* `protocol` mismatches: the client refuses a runner whose `protocol` is not
  `1` and tells the reader to update claudian or wavetracker.

### 4.5 Cleanup ("Clean up…")

Cleanup walks windows of `stride` from time 0 and keeps the ids whose
frequency density beats a threshold scaled by the number of frames in the
first window.  A session built from a snippet keeps the whole recording's
frame grid, so with the default 10-minute stride the threshold counted
minutes of frames the snippet never had and every id failed: "no identity
passed the frequency-density selection" (2026-10-07, a 30 s window of the
Iriri recording).  `cleanup_setup(ts)` therefore fits the run to the
session: the arrays are cut to the frames that hold detections (times from
0), `stride` is the config's 10 min or the tracks' span if shorter, the time
tolerance is 5 min or half the stride, and `n_fish` defaults to the median
number of ids with a detection in a frame (how many fish the tracker sees
at once), not a fixed 2.  The dialog says what the defaults were fitted to;
it also asks for the remaining parameters and a memory limit (default half
of physical RAM, an `RLIMIT_AS` in the runner).  If cleanup still refuses
the data (its `ValueError` beginning `cleanup:`), the panel shows that
sentence as a warning and on the hint line, not as an exception.  The panel
writes the *current* `fund_v`, `idx_v`, `ident_v` (with the reader's edits),
`sign_v` and `times` into a temporary directory, runs the one-shot runner, reads
`ident_v_cleaned_n{N}.npy`, and shows a summary ("kept 2 ids, 31,200 points
reassigned, 4,102 unassigned") with **Apply** and **Cancel**.  Apply is
`plan_apply_ident`, one undoable command.

### 4.6 Upstream change in wavetracker: list input

Whole-recording runs of a multi-file session must read the same joined
timeline audian shows.  `wavetracker.io.open_recording` uses a bare
`DataLoader(resolve_input(path))`, which accepts a file or a directory, and
for a directory applies thunderlab's continuity heuristic that drops the
tail of TASCAM sessions (see `src/audian/data.py:47`, `open_files`).  The
change is additive; existing call sites and the directory behaviour are
unchanged.  It is made on a branch in a worktree, because the analysis repo
editable-installs this checkout.

In `wavetracker/io.py`:

```python
#: Feature flag the audian runner checks: sequences of paths are accepted.
MULTI_INPUT = True

def resolve_input(path: str | Path | Sequence[str | Path]) -> str | list[str]:
    """... A list or tuple of paths is a recording split over several files,
    in the order given; each must be an existing file.  A one-element
    sequence is the same as that path."""
    if isinstance(path, (list, tuple)):
        files = [Path(p) for p in path]
        missing = [str(p) for p in files if not p.is_file()]
        if missing:
            raise FileNotFoundError(", ".join(missing))
        if not files:
            raise FileNotFoundError("empty list of recordings")
        return str(files[0]) if len(files) == 1 else [str(p) for p in files]
    ...  # unchanged

def open_recording(path, buffersize: float = 60.0) -> DataLoader:
    if isinstance(path, (list, tuple)) and len(path) > 1:
        return _open_joined(resolve_input(path), buffersize)
    return DataLoader(resolve_input(path), buffersize=buffersize)

def _open_joined(files: list[str], buffersize: float) -> DataLoader:
    """Concatenate every file, whatever their timestamps say.

    thunderlab compares each file's start time with the end of the previous
    one and drops that file and every later one on a mismatch, which loses
    the tail of TASCAM sessions (bext time is when a file was closed).  The
    caller named these files; all of them are read."""
    loader = DataLoader()
    loader._max_time_diff = 365 * 24 * 3600   # a year, not inf: timedelta overflows on inf
    try:
        loader.open_multiple(files, buffersize, 0.0)
    except Exception:
        loader.close()
        raise
    expected = 0
    for f in files:
        with DataLoader(f, buffersize=1.0) as one:
            expected += len(one)
    if len(loader) != expected:
        loader.close()
        raise ValueError(f"joined {len(loader)} of {expected} frames from {len(files)} files")
    return loader
```

`recording_info(path)` accepts the same types (it already calls
`open_recording`).  In `wavetracker/pipeline.py`, `detect(input_path: str |
Path | Sequence[str | Path], ...)`; only the meta lines change:

```python
"input": ([str(Path(p).resolve()) for p in input_path]
          if isinstance(input_path, (list, tuple)) else str(Path(input_path).resolve())),
"files": resolve_input(input_path),
```

The CLI is not changed (`wavetracker run` keeps treating several inputs as
several recordings).  Tests in `wavetracker/tests/test_io.py` (new):

* `resolve_input([a])` equals `resolve_input(a)`; a missing file in the
  list raises `FileNotFoundError`.
* Two synthetic WAVs written with `audioio.write_audio` whose metadata
  timestamps do not line up: `open_recording([a, b])` has
  `len(a) + len(b)` frames, and samples across the join equal the
  concatenated arrays.
* `detect([a, b], ...)` on a split synthetic recording gives the same
  `fund_v`/`idx_v` as `detect` on the concatenated file (bit-identical with
  `device="cpu"`).

No `nogil=True` change is needed: tracking runs in its own process.

---------------------------------------------------------------------------

## 5. Interaction design

### 5.1 Principles

* **Everything shows what it will do before it does it.**  Hover highlights
  the track the next click acts on; a brush paints its path as it goes; a
  cut shows its line and the new id's colour; a merge shows the connector
  and marks the points the conflict rule will drop.  The commit is then the
  plain mouse release or click.
* **Commit is immediate; undo is cheap.**  Gesture tools commit on release
  or click, with no Return step.  Confirmation dialogs exist only for things
  that are not a single gesture: accepting a snippet, applying cleanup,
  reverting to the tracker's output, replacing a session with a new run.
* **Navigation stays at hand.**  In edit mode a middle-drag grabs the
  spectrogram and moves it, in time and frequency, the way audian's Pan mode
  does (audian's own middle-drag is a zoom box in its Zoom mode, which left
  edit mode without a way to drag the view).  Right-drag (scale), Ctrl+wheel
  (time zoom), Shift+wheel (frequency zoom), plain wheel (scroll the channel
  stack) and every navigation key keep working.  The left button belongs to
  the tool, and only while edit mode is on.
* **Esc always gets you out**, one level at a time (5.6).
* **Never surprise.**  The hint line always says what the next click does;
  the history says what every edit did; rejected edits explain why.

### 5.2 Edit mode

The overlay (tracks drawn on the spectrogram) is shown whenever the panel is
open and a session is loaded.  **Edit mode** is a toggle (the large
**Edit tracks** button at the top of the edit group, or `Ctrl+Shift+E`).
When it is on:

* a `ToolSurface` is armed on every spectrogram lane and takes left-button
  clicks and drags (5.10);
* the plugin's key bindings (5.9) are live while the pointer is over a lane
  or the focus is in the panel;
* the cursor over a lane is the active tool's cursor (5.4).

When it is off, the lanes behave exactly as without the plugin, the track
table still selects and zooms, and hover highlighting is off.

### 5.3 Tools

One tool is active at a time; the default when entering edit mode is
**Select**.  Tools are switched with single keys (5.9) or the toolbar row in
the panel (icon plus letter, e.g. "⌖ V").  The active tool's button is
checked and the hint line names it.

| Tool | Key | Click | Drag | Commit |
|---|---|---|---|---|
| **Select** | `V` | select the hovered track (all its points); Ctrl+click toggles it in the selection; click on empty lane clears the selection; double-click zooms to the track | **brush-select**: points under the stroke are selected. Plain = replace selection, Shift = add, Ctrl = remove | selection only (not an edit, not in the history) |
| **Erase** | `E` | unassign the single hovered point | brush: unassign every point under the stroke | on release, one history entry per stroke |
| **Cut** | `C` | cut the hovered track at the cursor's time; the part after the cut gets a new id (Shift+click: the part before) | draw a straight cut line; every track it crosses is cut where it crosses | on click / release |
| **Merge** | `M` | first click sets the **anchor** track (outlined); each further click on another track merges it into the anchor; the anchor stays, so a fragmented track is joined by clicking its pieces left to right | brush: every track the stroke touches is merged into the anchor (or into the first track touched, which becomes the anchor) | on each click / on release |
| **Assign** | `A` | with points selected: assign them to the clicked track (the selection is cleared). With nothing selected: the clicked track becomes the "assign target" and the next brush stroke assigns to it | brush: points under the stroke are assigned to the target track | on click / release |
| **Add** | `F` | — | brush over a gap where the detector missed the fish. *Track ridge in brush* (default): the stroke marks a region and the best continuous ridge of the raw spectrogram inside it becomes the detections, frames without a ridge stay empty; `Ctrl`+drag, or the option off: one detection per frame under the stroke, frequency from "Add from" (5.5). If the stroke starts or ends on a track, the new points extend that track; otherwise they become a new id | on release (ridge: when the search answers) |

Actions that do not need a gesture work on the current selection:

| Action | Key | Does |
|---|---|---|
| Unassign selected points | `Del` | `plan_unassign(selection)` |
| Unassign selected tracks | `Shift+Del` | `plan_delete_ids(ids touched by the selection)` |
| New id from selection | `N` | `plan_new_id(selection)`: splits the selected points off into one new track |
| Merge selected tracks | `Shift+M` | `plan_merge(ids touched by the selection, into=the first-selected)` |
| Swap after cursor | `Shift+X` | with exactly two tracks selected: `plan_swap_after(a, b, t_cursor)`, the fix for an identity swap at a crossing |

All of these are also in a right-click context menu on a lane in edit mode
(which also offers "Rename track…", "Zoom to track", "Select track") and in
the track table's context menu.

*As built:* plain Select exists for these actions, and keys alone hid them.
So while the selection is non-empty a **selection strip** sits in the Edit
group under the tool row and hint: "3 tracks · 214 points" and the buttons
Unassign `Del`, Unassign tracks `⇧Del`, New id `N`, Merge `⇧M`, Swap `⇧X`,
Zoom `⇧Z` and Clear `Esc`.  Each button's tooltip says what it does and its
key; a button that does not apply is disabled and its tooltip says why
("swap needs exactly two selected tracks").  The strip's Swap cannot know
where the pointer was on the lane, so it swaps after the two tracks'
closest approach and says when in its tooltip; `Shift+X` and the lane menu
swap after the pointer.  The lane's right-click menu (edit mode only: the
`ToolSurface` accepts the right button only while armed, so audian's own
lane menu is untouched outside it) lists the same actions under a
"Selection: …" header, with the same reasons as tooltips; with nothing
selected it offers the hovered track's actions instead, including
"Unassign track N".  Strip and menu are built from one list,
`ToolController.selection_actions`.

### 5.4 Live feedback, tool by tool

**Hover (every tool).**  Every pointer move over a lane runs a nearest-point
query (6.3) with a radius of `PICK_PX = 10` screen pixels.  The track under
the pointer is drawn 2 px wider with a 1 px halo in the lane's contrast
colour, and a small label box follows the pointer at a 14 px offset:

```
id 12 "female A" · 812.4 Hz · 03:12.4 · 1,204 pts
click: cut here → 12 | 431
```

The second line is the hint for the active tool.  Where several tracks are
within the radius (crossings), `Tab` cycles through them (the label shows
"1/3"); the click acts on the shown one.

**Brush (Select, Erase, Assign, Add, Merge drag).**

* The cursor is a circle of radius `brush_px` screen pixels (default 14,
  range 3–80), drawn as a 1.5 px ring in the tool colour over a 3 px ring in
  the lane's contrast colour, so it reads on both ends of every colour map.
  The system cursor is hidden over the lane while a brush tool is active.
* The stroke is drawn as it is painted: a `QGraphicsPathItem` in screen
  coordinates with pen width `2 * brush_px`, round caps and joins, in the tool
  colour at 30 % alpha.  It stays until release (then fades out over 150 ms).
* The points the stroke has captured so far change appearance *during* the
  stroke: selection colour for Select and Assign, a hollow dim ring for
  Erase ("will be unassigned"), the anchor's colour for Merge, and for Add
  the provisional new points are drawn as they are computed.
* Radius: `[` and `]` (×0.8 / ×1.25), or Alt+wheel.  The ring resizes in
  place and the hint line shows "brush 14 px".  Radius is in screen pixels,
  so it does not change with zoom.
* **Sticky brush** (option, on by default): a stroke that starts on a track
  only captures points of that track, so erasing noise from one fish's
  track never nicks the fish crossing it.  Holding Alt during the stroke
  turns stickiness off for that stroke.  The hint line says which id the
  stroke is stuck to.

**Cut.**  Over a track, a vertical marker (a short line spanning ±20 px
around the track, in the tool colour) snaps to the midpoint between the two
consecutive detections of the hovered track nearest the cursor.  The part
that will get the new id is drawn in the colour the new id will have
(`slot_of(next_id)`), so the reader sees the result before clicking.
Dragging draws a straight line from press to pointer; every crossing with a
track is marked with a small ✕ and listed in the hint ("cuts 3 tracks").

**Merge.**  After the anchor is set, hovering another track draws a dashed
connector from the anchor's nearest end to the hovered track's nearest end,
recolours the hovered track to the anchor's colour, and marks every point the
local-median rule would drop with a red ✕.  The hint says "click: merge 31
into 12 · 4 conflicting points will be unassigned".  Merging a track with
itself is not offered (the hover simply says "anchor").

**Assign.**  Like merge, with the selected points shown in the target's
colour and displaced points (the target's own rows in frames the selection
covers) marked ✕.

**Add.**  The stroke centre line is sampled at every session frame it spans.
Per frame, the candidate frequency is shown as a hollow dot as soon as it is
known.  Frames where the target track already has a point are shown as a
grey dot and are skipped.  On release the dots become filled when committed.
In peak mode with the runner (5.5), dots are first drawn at the stroke
centre with a pulsing outline, then move to the peak frequencies when the
runner answers (≤ 2 s warm); the edit commits at that moment as one
history entry.

**Selection rendering.**  Selected points are drawn on top in the selection
colour (the lane colour map's opposed hue, or `#FF2BD6` on an achromatic
map, as `frequencybands/overlay.py:193` does), points 2 px larger than
normal, lines 1.5 px wider.  The panel shows "412 points in 3 tracks
selected".

**After a commit.**  The touched points flash (150 ms brighten) so the reader
sees what changed, and the history list scrolls to the new entry.  After
undo/redo, the affected span flashes if it is visible; if not, the hint line
says where ("undid Merge 31 into 12 at 01:12:30 — Shift+Z to go there").

### 5.5 Where "Add" gets its frequencies

In this order:

1. **The session's own fine spectrogram**, if the results directory has
   `fine_spec.npy` (memmap `[time, freq]`, float32) with `fine_freqs.npy`
   and `fine_times.npy`: the peak within `[f - r, f + r]` per frame, where
   `r` is the brush radius converted to Hz, refined parabolically.  In
   process, immediate.
2. **The runner's `peaks` op** on the raw audio with the session's grid
   (4.4).  Same accuracy as the tracker's own detections, and it also
   returns `sign_v` and `cplx_v` for the new rows.
3. **The stroke centre** ("Trace by hand" mode, also selectable explicitly
   in the panel): frequency = the stroke centre line at each frame,
   `sign`/`cplx` NaN.

The panel's Add options show which source is in use.

**Ridge tracking (as built, the default).**  The list above made Add in a
snippet session (no fine spectrogram, often no warm runner) literal painting
along the stroke centre.  With *Track ridge in brush* on (Edit group,
persisted as `ridge_add`), a stroke only marks a region (`ridge.py`,
`ridgeadd.py`):

1. *Bands.*  For every session frame the brush footprint covers, the band
   is the footprint's frequency extent in that frame: the stroke swept by
   the brush circle, converted per frame from screen pixels to seconds and
   Hz (`brush_bands`).  Frames within one brush radius of the stroke's ends
   are covered too.
2. *Spectrum.*  On release a worker thread (`RidgeSource`, one `QThread`
   for the panel's life) reads the stroke's samples, all of the session's
   electrodes (`session_channels`, else every channel), through audian's
   `open_files` -- never `browser.data` -- and computes the power on the
   **session's own frames**: same frame starts (`grid.s0 + k*step`, or
   `t_k*rate - nfft/2` without a grid), same nfft (the grid's, else the
   results' `config.spectrogram.nfft`, else wavetracker's default read from
   `wavetracker.config`, which does not import torch), and wavetracker's
   window and PSD scaling.  No smaller nfft is used for short strokes: the
   session's nfft *is* its frequency resolution, and a shorter window would
   blur neighbours 3 Hz apart.  Power is **summed over electrodes**, as
   wavetracker's own peak search does; on the iriri take (20 neighbour
   pairs 3-7 Hz apart, 10 s strokes) the sum put 1549 frames on the right
   fish and 188 on the neighbour against 1532 and 189 for the maximum over
   electrodes, and the sum's noise statistics are known, which the floor
   below needs.
3. *Ridge.*  Viterbi over the band's bins: reward = dB power minus
   `centre_db`·u² (u = distance from the band's centre in half-widths,
   6 dB at the edge: the reader painted along *their* fish), transition
   cost = `jump_db`·(Δf / (max_slope·Δt))² with `jump_db` = 12 dB, and
   |Δf| ≤ max_slope·Δt as a hard limit.  `max_slope` defaults to
   `tracking.freq_tolerance / frame step` -- what the tracker itself links
   between consecutive frames -- from the session's config, else
   wavetracker's default; nothing is a fixed frequency.  A frame the
   reachable path cannot enter (the stroke jumped faster than that) starts
   a new segment.
4. *Gate.*  A frame gets a detection only if the path's bin is a spectral
   peak and its power exceeds the noise floor: the median power of the
   band (widened to at least 64 bins) times the factor by which the loudest
   of the band's bins in a noise-only frame would exceed the median with
   probability 1 % (power summed over c electrodes is Gamma(c): 7.3 dB for
   two electrodes and an 8-bin band).  "Median + k·MAD" was tried first and
   failed in dense recordings: the window is full of other fish, the MAD
   balloons, and a clearly visible fish 13 dB above the median failed in
   two frames out of three.  Frames that fail stay empty -- a gap is a gap.
5. *Refine and commit.*  Each frequency is refined with a parabola on the
   dB values of the peak bin and its neighbours; `sign_v` and `cplx_v` of
   the new rows are the electrodes' power and complex spectrum at that bin.
   The rows go through `plan_add` with the same target rule as before
   (sticky to the track the stroke starts or ends on, else a new id), one
   history entry, undoable.

While the search runs the band centres are drawn as pulsing dots and the
hint says so; Esc or a new stroke cancels it (a superseded answer is
dropped).  If no frame passes, nothing is added and the hint says "no ridge
found in the brushed region (nothing above the noise floor) · Ctrl+drag
paints literally" -- it never silently falls back.  **`Ctrl`+drag** paints
literally for one stroke (the "Add from" source): Alt was taken by the
brush-size wheel and is what many Linux window managers use to drag
windows, Shift extends audian's channel selection on clicks, and Ctrl has
no meaning for Add.  Measured on the iriri take (48 kHz, 2 electrodes,
nfft 32768, 153 frames): a 10 s stroke answers in 0.16-0.19 s (read
< 0.02 s, spectrum 0.15 s, Viterbi < 0.01 s).  Brushing 0.5 Hz off along
two fish 3.8 Hz apart that the snippet run had missed, the ridge put 79 of
85 and 68 of 70 points within 0.5 Hz of the whole-recording run's tracks of
the brushed fish (median error 0.16 and 0.08 Hz) and none on the
neighbour, leaving the faint frames empty; literal painting added a point
in all 147 frames with a median error of 0.35-0.49 Hz.

### 5.6 Esc and cancel

Esc unwinds one level per press:

1. a gesture in progress (stroke, cut line): discarded, nothing committed;
2. a merge anchor or assign target: cleared;
3. the selection: cleared;
4. a tool other than Select: back to Select.

Esc does not leave edit mode (that is `Ctrl+Shift+E` or the button), and it
does not cancel a running wavetracker job (that is the panel's **Cancel**
button, because a stray Esc must not throw away a twenty-minute run).

### 5.7 Colours

* **Per-id colour slot**: `slot = (int(id) * 7) % 10` over a palette of 10
  hues (36° apart, starting at 15°).  Stable for an id for its whole life,
  across sessions, and consecutive ids (which a tracker hands to neighbouring
  fragments) land 3 slots apart.  7 is coprime to 10, so every slot is used.
* The palette is derived from the lane's colour map, as
  `frequencybands/overlay.py:157-203` does (`map_ends`): saturation 200 and
  value 255 when the map's floor is dark, value 150 when it is light; hues
  within 25° of the map's bright end are shifted 40° away.  Pens are re-read
  on every redraw so a theme or colour-map switch takes effect immediately.
* **Legibility on the ridge** (as built): every track line, snippet line and
  dot has a dark edge in the map's floor colour, one pixel each side, also
  at overview zoom; slot hues also avoid the chromatic hues of the map's
  upper half (audian's default map paints ridges yellow on their flanks,
  even though its peak is white).  "Dim spectrogram" lays the floor colour
  at 60 % over the lane, under the tracks.
* **Unassigned points**: 3 px dots in the map's mid-grey at 45 % alpha,
  hidden by default above 200,000 visible points (the toggle `U` overrides).
* Ids with a reader-given label show the label in the hover box and in the
  track table; colour does not change.

### 5.8 Panel layout

*As built (after the first QA pass):* the order is session header (with
**Track visible** / **Track recording** and the progress row, so a run is
reachable and visible whatever is folded), the snippet bar, **Edit** (toggle,
tools, hint, the selection strip while something is selected (5.3), Undo/Redo,
brush, sticky, "Dim spectrogram", "Track ridge in brush" (5.5), and a
"Display and keys" disclosure for the rest), **History**, **Tracks**, **Issues** (folded)
and **Run settings** (the wavetracker line, fish range, device, config,
advanced, cleanup).  Every group below the header folds; the folds are remembered, and
Run settings folds itself after the first run.  When no spectrogram lane is
shown, the header says "Tracks are drawn on spectrograms" with a Show button.
The original plan follows.

The panel is a narrow side tab (≥ 220 px) built from
`ParameterGroup(title, self, caption=False, narrow=True)` groups:

1. **Session header**: the results directory (elided in the middle) or
   "Unsaved session"; a dirty marker `●` and "unsaved changes"; **Save**
   (Ctrl+S in edit mode); a **⋯** menu with "Open results…", "Save as…",
   "Revert to tracker output…", "Show in file manager".
2. **Run**: wavetracker status line; **Fish range** min/max Hz (prefilled
   from the loaded `wavetracker.json` or the runner's default config; if left
   at wavetracker's broad default the field turns amber with "set a narrow
   band for your species", the same warning the CLI prints); device; config
   file (optional YAML); an "Advanced" disclosure with nfft, overlap,
   thresholds, block duration, tracking `freq_tolerance`/`max_dt`, stitching
   on/off.  Buttons **Track visible**, **Track recording**, **Clean up…**.
   A progress bar with stage text and **Cancel** while a job runs.
3. **Snippet bar** (only while a provisional snippet exists): summary,
   **Accept**, **Discard**, "Join to tracks at the edges".
4. **Edit**: the **Edit tracks** toggle; the tool row (V E C M A F); brush
   size slider; options: sticky brush, show unassigned (`U`), show only
   selected (`I`), point size (0–8 px, 0 = lines only), break lines at gaps
   longer than [s], show ids on tracks.
5. **Tracks** table: id, label, start, end, duration, median Hz (f range in
   the tooltip), points.  Sortable.  "Only tracks in view" checkbox (on by
   default).  Click selects the track (and the selection in the lanes);
   Ctrl/Shift extend; double-click zooms to it; F2 or the context menu
   renames.  The table follows the lane selection and scrolls to it.
6. **History**: one line per edit, newest at the bottom, e.g.
   "14:03:12  Merge 31 into 12 (4 conflicts unassigned)".  The current
   position is marked; entries after it (the redo branch) are greyed.
   Clicking an entry jumps the model to the state just after it; the redo
   branch survives until the next new edit.  Undo/Redo buttons beside it.
7. **Hint line**, always visible at the bottom: what the next click does,
   or the reason the last edit was rejected.

### 5.9 Keys and how they are scoped

audian already binds 119 key sequences (`tests/data/action-inventory.json`);
`Ctrl+Z` is **Pan zoom**, not undo.  The plugin defines **no window
`QAction`s**: plugin keys cannot appear in audian's cheat sheet or command
palette without a core change anyway (`audian.py:1473`, `:3083`), and
window actions would change the golden inventory.

Instead, a `KeyRouter` (in `tools.py`) is an event filter installed on the
`QApplication` while the panel exists.  It acts on
`QEvent.Type.ShortcutOverride` and the `KeyPress` that follows.  It claims a
key only when **all** of these hold:

* the key is in the router's table below, for the current state;
* the event's window is this browser's window, and this browser is the
  visible tab;
* edit mode is on (except `Ctrl+Shift+E`, which only needs the panel to be
  open);
* the pointer is over one of this browser's spectrogram lanes (tracked from
  `ToolSurface` hover enter/leave), **or** the keyboard focus is inside the
  panel;
* the focus widget is not a text input (`QLineEdit`, `QAbstractSpinBox`,
  `QPlainTextEdit`, `QTextEdit`, editable `QComboBox`) and no modal dialog or
  popup is open.

When it claims a key it accepts the `ShortcutOverride` (so audian's action
does not fire), runs the plugin's command, and consumes the matching
`KeyPress` (so no widget sees it).  When any condition fails it does
nothing, and the key behaves exactly as without the plugin.  This is the
narrowly scoped variant of the channel rail's own `ShortcutOverride` trick,
not the legacy sorter's application-wide arrow filter (B24).

| Key | In edit mode does | audian's binding it shadows there |
|---|---|---|
| `Ctrl+Shift+E` | toggle edit mode (panel open is enough) | free |
| `V` `E` `C` `M` `A` `F` | tools Select, Erase, Cut, Merge, Assign, Add | Fit Y, Envelope cutoff down, Center, (rail `M`), Analyze, free |
| `Ctrl+Z` | undo | Pan zoom |
| `Ctrl+Shift+Z`, `Ctrl+Y` | redo | free |
| `Ctrl+S` | save track edits | Save window as (still on the menu, and on `Ctrl+Shift+S`) |
| `Del` / `Shift+Del` | unassign selected points / tracks | Hide deselected channels / free |
| `N` | new id from selection | Next fixed label |
| `Shift+M` | merge selected tracks | free |
| `Shift+X` | swap selected two tracks after the cursor | free |
| `U` | show/hide unassigned points | Decrease spatial threshold |
| `I` | show only selected tracks | free |
| `Shift+Z` | zoom to selection (or hovered track) | free |
| `G` / `Shift+G` | next / previous issue (5.11) | Toggle grid / free |
| `Enter` | accept the suggestion of the current issue | free |
| `[` / `]` | brush smaller / larger | free |
| `Ctrl`+drag (Add) | paint literally instead of tracking the ridge (5.5) | — (a modifier, not a key) |
| `Tab` | cycle among overlapping tracks under the pointer | focus traversal (only over a lane) |
| `Esc` | the cancel ladder (5.6) | free |

Deliberately **not** shadowed, because they are used while editing: play
(`P`, `Space`), zoom and pan (`Z`, `T`, `Shift+T`, arrows, PgUp/PgDn,
Home/End, Backspace, Alt+Left/Right), and the spectrogram contrast and
resolution keys (`D`, `J`, `K`, `R`, `O` and their Shift forms, `Shift+C`).
`Ctrl+Z` returns to Pan zoom the moment edit mode is off, the pointer leaves
the lanes, or the panel closes.  The panel's **Keys** disclosure lists this
table, including the shadowed bindings.

Undo/redo also work from the panel's buttons and history list at any time.

### 5.10 How tool mouse events coexist with audian's

`ToolSurface(pg.GraphicsObject)` is a child of each lane's `SelectViewBox`
(`setParentItem(vb)`, not `ax.addItem`), so its local coordinates are the
view box's pixels.  Its `boundingRect()` is `vb.boundingRect()`, updated on
`vb.sigResized`; its z value is above the view box's child group.  It paints
nothing itself; the brush ring, stroke path, cut line and label box are its
child items.

* `hoverEvent(ev)`: when armed, `ev.acceptDrags(Qt.MouseButton.LeftButton)`
  and `ev.acceptClicks(Qt.MouseButton.LeftButton)`, which pre-claims the
  left button for this item and nothing else (the mechanism
  `labeloverlay.py:31-80` measured), plus `acceptDrags(MiddleButton)`:
  a middle-drag pans the view (`ToolSurface.pan`, `translateBy` in data
  coordinates and the signals audian's Pan mode emits).  Right drags still
  reach the view box.  The hover also updates the pointer state the
  `KeyRouter` reads and runs the hover query.
* `mouseDragEvent(ev)`: `ev.ignore()` unless armed and the button is left;
  otherwise start/continue/finish the active tool's gesture.  A stroke is
  clipped to the lane; leaving it does not end the stroke, releasing does.
* `mouseClickEvent(ev)`: left clicks only; right click opens the edit
  context menu at `ev.screenPos()`.
* `wheelEvent(ev)`: Alt+wheel resizes the brush (read whichever of the
  angle-delta components is non-zero, since Qt swaps them under Alt) and
  accepts; any other wheel is ignored and reaches the view box.  During a
  stroke every wheel is ignored, so the view cannot change under a stroke.
* When disarmed, the surface does not accept hover, clicks or drags, and is
  hidden.

Known side effect: audian's `DataBrowser.mouse_clicked` handles every click
from `scene.sigMouseClicked` regardless of acceptance and focuses the
clicked channel (`databrowser.py:5108`).  A plain click on a track in
another lane therefore also focuses that lane's channel, which is
harmless and arguably right.  Shift+click would also extend the channel
selection, which is why no click in this design uses Shift except Cut's
"before" variant (open question 10.2).

All gesture logic lives in tool classes with plain methods
(`press(lane, pos, mods)`, `move(lane, pos, mods)`, `release(lane, pos,
mods)`, `click(lane, pos, mods, double=False)`), which the Qt handlers call.
Tests drive gestures through those methods without synthesising Qt mouse
events.

### 5.11 Issues: finding what needs fixing

`TrackSet.issues(kinds, params)` (pure) finds places a reader should look at,
sorted by time:

| kind | finds | suggestion (`Enter`) |
|---|---|---|
| `join` | track A ends and track B starts within `tracking.max_dt` and `tracking.freq_tolerance` (both from the session config), B not overlapping A | merge B into A |
| `gap` | a gap inside a track longer than `gap_break_s` | Add tool armed, stroke hint |
| `short` | tracks with fewer than `min_points` (default 10, panel field) points | unassign track |
| `crossing` | two tracks within 1 frequency bin of each other at some frame | none (zoom; swap or cut is the reader's call) |

`G` / `Shift+G` move the view to the next / previous issue after the view
centre (keeping the current zoom unless the issue does not fit), highlight
it, and put its text in the hint line ("possible join: 12 → 31, gap 2.1 s,
Δf 0.8 Hz — Enter merges").  The panel has an "Issues" checkbox list to
choose which kinds `G` visits.  Issues are recomputed lazily after edits
(only for the touched ids).

### 5.12 Small things that make it pleasant

* **Zoom to selection** (`Shift+Z`): time and frequency span of the selection
  plus 10 % margin; with nothing selected, the hovered track.  Uses
  `browser.set_times` and `browser.set_ranges("f", f0, f1)` (both internal;
  wrapped in `panel._zoom_to` with a per-lane `setYRange` fallback).
* **Double-click a track** zooms to it; double-click a table row too.
* **Show only selected** (`I`) hides every other track; unassigned points
  stay as they are.  The hint line says "isolated: 3 tracks".
* **Hide unassigned** (`U`).
* **Point size** 0–8 px and **line gap break**: a track's line is broken
  wherever two consecutive detections are more than `gap_break_s` apart
  (default 0.5 s), so gaps look like gaps (legacy lines bridged them).
* **Id labels on tracks**: the id (or label) at the left end of each visible
  track, when 40 or fewer tracks are visible.
* **Remembered results**: the results directory last used for a recording is
  remembered (settings `results_dirs`) and opened automatically with the
  recording; otherwise, if `<stem>-wavetracker/` exists beside the
  recording, the panel offers "Found tracking results — Open".
* **Status in the panel header**: tracks, points, unassigned points, edits
  since save.
* **Rejected edits** say why, in the hint line and as an `info` notification,
  never as a dialog.
* **History hover**: hovering a history entry outlines its span on the lanes.
* **Brush size and options persist** across sessions (settings).
* **Track rename** (F2 in the table): labels are saved in
  `eodsorter.json`, not in the wavetracker arrays.
* **Busy feedback**: the run button turns into a progress bar with stage,
  percentage, realtime factor and ETA; "Cancel" beside it.

---------------------------------------------------------------------------

## 6. Drawing and performance

### 6.1 Targets

Reference dataset: 1,000,000 detections, 2,000 ids, a 4 h, 8-channel
recording, 4 spectrogram lanes visible, on the developer's machine.

| operation | target |
|---|---|
| hover query + highlight update | ≤ 4 ms per pointer event (one 60 Hz frame with room for audian) |
| brush stroke, per pointer move | ≤ 4 ms incremental |
| pan/zoom redraw of the overlay, all lanes | ≤ 30 ms at full zoom-out, ≤ 10 ms at ≤ 50,000 visible points |
| commit of an edit touching ≤ 50,000 rows, including redraw | ≤ 50 ms |
| undo / redo of the same | ≤ 50 ms |
| load a 1M-row results directory | ≤ 1.5 s (on a worker thread when > 300,000 rows) |
| save | ≤ 1 s (only `ident_v` unless rows were appended) |

`tests/measure_eodsorter.py` (a script like `tests/measure_overlay.py`, not
a test) builds the reference dataset synthetically and prints these
numbers; slow-suite tests assert generous multiples (×5) of a smaller
dataset so CI catches order-of-magnitude regressions.

### 6.2 Rendering

`overlay.py` copies and adapts the machinery of
`frequencybands/overlay.py`: passive items (`_passive`), `ignoreBounds=True`,
NaN-joined polylines, gated redraws, colours from the lane map, `detach()`.

* **Geometry is computed once per view, not per lane.**  Lanes share the
  time axis; a `RenderCache` (pure numpy, `geometry.py`) keyed by
  `(model revision, provisional revision, x-range, y-range, width_px,
  height_px, display options)` computes the geometry, and every lane's
  `TrackOverlay` calls `setData` with it.  Lanes with a different frequency
  range (per-lane Shift+wheel) get their own cache entry.
* **Only the visible time span is drawn**: rows come from
  `ts.rows_in_frames(k0, k1)` with `k0, k1` from the view's x-range plus one
  frame each side.
* **One `PlotCurveItem(connect="finite")` per colour slot** (10), plus one for
  the selection, one for hover, one for the provisional layer, one dashed
  item for previews, one `ScatterPlotItem` for points (if point size > 0),
  one for unassigned points, and a pool of `TextItem`s for id labels.  About
  16 items per lane regardless of the number of ids.  Items are created once
  and hidden when empty.
* **Building the polylines** (vectorised): take the visible assigned rows,
  `np.lexsort((idx, ident))`, insert NaN wherever the id changes or the frame
  gap exceeds `gap_break_s`, then split by colour slot.  When the visible
  rows exceed 150,000, switch to the per-id path: for each visible id,
  `searchsorted` its `rows_of(id)` to the view and stride it to ≤ 2 vertices
  per screen pixel (`stride_for`), so the vertex count is bounded by screen
  width × visible ids, not by detections.  Selected and hovered tracks are
  drawn undecimated.
* **Points are thinned to one per screen pixel** before drawing: quantise
  `(px, py)` to integers and keep `np.unique` of the cell index.  This bounds
  the scatter at the number of screen pixels covered, whatever the zoom.
* **Antialiasing** on when fewer than 50,000 vertices are drawn in a lane,
  off above.
* **Incremental redraw after edits**: a `Change` invalidates only the slots
  of its ids (old and new), the selection and hover items, and only if
  `Change.frames` intersects the view.  Untouched slot curves keep their
  data.
* **Redraw scheduling**: view-box `sigRangeChanged` / `sigResized` and model
  changes mark the overlay dirty; the actual update runs once in a
  zero-timeout `QTimer` (coalescing bursts of events into one redraw per
  event-loop turn).  Hover updates are coalesced the same way.

### 6.3 Hit-testing

Pure numpy in `geometry.py`, all in screen-pixel space so tolerances are
pixels at every zoom:

* `View(x0, x1, y0, y1, w_px, h_px)` converts data to pixels:
  `px = (t - x0) * w / (x1 - x0)`, `py = (y1 - f) * h / (y1 - y0)`.
* **Nearest point**: candidate rows are `rows_in_frames` over the frames
  whose centre lies within `PICK_PX` horizontally of the pointer (a
  `searchsorted` on the frame grid), filtered to the visible id set;
  distance in pixels, vectorised; return the closest within `PICK_PX`, plus
  the sorted list of distinct ids within it (for `Tab`).
* **Brush**: each pointer move adds a segment `(p_prev, p_now)`.  Candidates
  are rows in the frames spanned by the segment ± radius; the
  point-to-segment distance is computed vectorised; rows within the radius
  are OR-ed into the stroke's boolean mask over the candidate set.  Cost per
  move is proportional to the points near the new segment, never to the
  stroke's total length.
* **Cut line crossings**: for each visible track, the sign of the cross
  product of its consecutive vertices against the line changes where it
  crosses; vectorised over the track's visible vertices.

---------------------------------------------------------------------------

## 7. Modules and interfaces

```
src/audian_plugins/eodsorter/
    __init__.py     the panel factory, nothing else                   (C)
    model.py        TrackSet, FrameGrid, Plan, Change, History,
                    Snippet, load/save, issues; numpy only              (A)
    runner.py       wavetracker_status, RunnerClient, SnippetExporter,
                    job builders; QtCore only                           (B)
    wtrunner.py     the script run in a child process of sys.executable;
                    stdlib + numpy + wavetracker only                   (B)
    geometry.py     View, RenderCache, hit tests, polylines; numpy only (C)
    overlay.py      TrackOverlay per lane (pyqtgraph)                   (C)
    tools.py        ToolSurface, tool classes, KeyRouter                (C)
    panel.py        WavetrackerPanel                                    (C)
tests/
    test_eodsorter_model.py      fast                                   (A)
    test_eodsorter_runner.py     fast (protocol) + slow (RunnerClient)  (B)
    data/fake_wavetracker/       a stub `wavetracker` package for (B)'s tests
    test_eodsorter_geometry.py   fast                                   (C)
    test_eodsorter_panel.py      slow                                   (C)
    measure_eodsorter.py         benchmark script                       (C)
wavetracker/ (upstream, branch `list-input`)
    wavetracker/io.py, pipeline.py, tests/test_io.py                    (B)
```

Every module that is not `overlay.py`, `tools.py` or `panel.py` must import
without Qt widgets: `model.py` and `geometry.py` import no Qt at all;
`runner.py` imports only `PySide6.QtCore`.  `test_eodsorter_model.py`
checks `model` and `geometry` with the same `sys.modules` probe
`tests/test_thread_boundary.py` uses.

### 7.1 Package A: `model.py`

```python
class EditRejected(ValueError): ...      # .args[0] is the reader-facing reason
class ResultsError(ValueError): ...      # structural problems loading a directory

@dataclass(frozen=True)
class Append:
    fund: np.ndarray; idx: np.ndarray; ident: np.ndarray; tracked: np.ndarray
    sign: np.ndarray; cplx: np.ndarray | None

class TrackSet:
    # construction
    @classmethod
    def empty(cls, grid: FrameGrid, meta: dict | None = None) -> "TrackSet": ...
    @classmethod
    def from_arrays(cls, fund, idx, ident, sign, times, cplx=None, meta=None,
                    tracked=None, next_id=None, grid=None) -> "TrackSet": ...

    # read-only state
    n: int; n_channels: int | None; has_cplx: bool
    fund; idx; ident; tracked; sign; cplx; times      # read-only views
    grid: FrameGrid | None
    meta: dict                                        # wavetracker.json contents
    labels: dict[int, str]; notes: dict[int, str]
    next_id: int
    revision: int                                     # +1 per apply/undo/redo step
    history: History
    def is_dirty(self) -> bool: ...
    def frame_time(self, k) -> np.ndarray: ...
    def ids(self) -> np.ndarray: ...
    def rows_of(self, id: float) -> np.ndarray: ...
    def rows_in_frames(self, k0: int, k1: int) -> np.ndarray: ...
    def stats(self, ids=None) -> np.ndarray: ...      # structured, fields in 3.8
    def issues(self, kinds=("join", "gap", "short", "crossing"), **params) -> list["Issue"]: ...
    def check_invariant(self, ids=None) -> None: ...  # raises AssertionError naming id and frame

    # plans (pure; raise EditRejected)
    def plan_unassign(self, rows) -> Plan: ...
    def plan_delete_ids(self, ids) -> Plan: ...
    def plan_new_id(self, rows) -> Plan: ...
    def plan_assign(self, rows, target: float) -> Plan: ...
    def plan_merge(self, ids, into: float) -> Plan: ...
    def plan_cut(self, id: float, t: float, new_part: str = "after") -> Plan: ...
    def plan_swap_after(self, a: float, b: float, t: float) -> Plan: ...
    def plan_add(self, frames, freqs, target: float | None = None,
                 sign=None, cplx=None) -> Plan: ...
    def plan_replace_span(self, snippet: Snippet, stitch: bool = True) -> Plan: ...
    def plan_apply_ident(self, ident: np.ndarray, label: str) -> Plan: ...
    def plan_revert(self, rows=None) -> Plan: ...
    def plan_set_label(self, id: float, text: str) -> Plan: ...
    def plan_set_note(self, id: float, text: str) -> Plan: ...

    # mutation (the only methods that mutate)
    def apply(self, plan: Plan) -> Change: ...   # rejects a plan made at another revision
    def undo(self) -> Change | None: ...
    def redo(self) -> Change | None: ...
    def jump(self, position: int) -> Change | None: ...

    # persistence (section 8)
    @classmethod
    def load(cls, folder, ident_file: str = "ident_v.npy") -> tuple["TrackSet", list[str]]: ...
    def save(self, folder, recording_paths: list[str] | None = None) -> None: ...
    def mark_saved(self) -> None: ...
    def write_autosave(self, folder) -> None: ...

def ident_variants(folder) -> list[str]           # ["ident_v.npy", "ident_v_cleaned_n2.npy", ...]
def finish_interrupted_save(folder) -> bool       # 8.2
def read_autosave(folder) -> Autosave | None      # 8.3
def load_snippet(run_dir, grid: FrameGrid, k0: int) -> Snippet

@dataclass(frozen=True)
class Issue:
    kind: str; t: float; f: float; ids: tuple[float, ...]; text: str
    suggestion: Callable[[TrackSet], Plan] | None
```

`apply` refuses a plan whose `revision` differs from the model's (the plan
records it), so a stale preview can never be committed.

### 7.2 Package B: `runner.py`, `wtrunner.py`, upstream

```python
def wavetracker_status() -> tuple[str | None, str | None]: ...   # (version, error)

class RunnerError(RuntimeError): ...

class RunnerClient(QObject):
    sigState = Signal(str)                      # "stopped" "starting" "idle" "busy" "failed"
    sigHello = Signal(dict)
    sigProgress = Signal(str, str, int, int, str)   # job, stage, done, total (-1 unknown), text
    sigResult = Signal(str, dict)               # job, the result message
    sigError = Signal(str, str, str)            # job, kind ("cancelled" included), message
    sigLog = Signal(str, str)                   # level, text

    def __init__(self, oneshot: bool = False, parent: QObject | None = None,
                 *, program: str | None = None): ...   # program: tests only; default sys.executable
    state: str
    hello: dict | None
    def ensure_started(self) -> None: ...       # starts the process; hello arrives as a signal
    def submit(self, op: str, **params) -> str: # job id; raises RunnerError when busy
    def cancel(self) -> None: ...               # terminate, then kill after 2 s
    def shutdown(self, timeout_ms: int = 2000) -> None: ...

class SnippetExporter(QObject):
    sigProgress = Signal(int, int)              # samples written, total
    sigDone = Signal(str, str)                  # wav path, error ("" on success)
    def __init__(self, paths: list[str], sample_range: tuple[int, int],
                 out_path: str, token: CancelToken): ...
    def run(self) -> None: ...                  # called on the panel's QThread

def detect_job(input, output_dir, final_dir, config, config_path, start, duration,
               device, track=True) -> dict: ...
def peaks_job(input, grid: FrameGrid, channels, frames, fmin, fmax, out, device) -> dict: ...
def cleanup_job(dir, n_fish, params, mem_limit_bytes) -> dict: ...
```

`wtrunner.py`: `main(argv)` with `--oneshot`; the stdout reservation of
4.4 at the top; one function per op; every exception is turned into an
`error` line; the process exits 0 after `shutdown` or end of stdin.

### 7.3 Package C: `geometry.py`, `overlay.py`, `tools.py`, `panel.py`

```python
# geometry.py (numpy only)
@dataclass(frozen=True)
class View:
    x0: float; x1: float; y0: float; y1: float; w_px: float; h_px: float
    def to_px(self, t, f) -> tuple[np.ndarray, np.ndarray]: ...
    def to_data(self, px, py) -> tuple[float, float]: ...
N_SLOTS = 10
def slot_of(ids) -> np.ndarray: ...                     # (int(id) * 7) % 10
def polylines(ts, rows, view, gap_frames, max_rows=150_000) -> dict[int, tuple[np.ndarray, np.ndarray]]: ...
def thin_points(px, py) -> np.ndarray: ...              # mask, one per pixel cell
def nearest(ts, view, x_px, y_px, r_px, visible_ids=None) -> tuple[int, list[float]]: ...
def brush_segment(ts, view, p0, p1, r_px, ids=None) -> np.ndarray: ...   # rows
def line_crossings(ts, view, p0, p1) -> list[tuple[float, float]]: ...   # (id, t)
class RenderCache: def get(self, ts, scene_state, view) -> Geometry: ...

# overlay.py
class TrackOverlay:
    def __init__(self, ax, scene: "SceneState", cache: RenderCache): ...
    def invalidate(self, change: Change | None = None) -> None: ...
    def schedule(self) -> None: ...          # coalesced update_plot
    def update_plot(self) -> None: ...
    def flash(self, rows) -> None: ...
    def detach(self) -> None: ...

@dataclass
class SceneState:                            # shared by every lane's overlay and the tools
    ts: TrackSet | None
    snippet: Snippet | None
    selection: np.ndarray                    # rows
    hidden_ids: frozenset; isolate: bool; show_unassigned: bool
    point_px: int; gap_break_s: float; show_ids: bool
    hover: Hover | None                      # row, id, candidates, cycle index
    preview: Plan | None                     # what the active gesture would do
    stroke_rows: np.ndarray                  # captured so far
    revision: int                            # bumped on any change of the above

# tools.py
class Tool:                                  # Select, Erase, Cut, Merge, Assign, Add subclass this
    key: str; name: str; cursor: str         # "brush" | "cross" | "arrow"
    def hint(self, scene, hover) -> str: ...
    def press(self, lane, pos, mods) -> None: ...
    def move(self, lane, pos, mods) -> None: ...
    def release(self, lane, pos, mods) -> None: ...
    def click(self, lane, pos, mods, double: bool = False) -> None: ...
    def cancel(self) -> bool: ...            # True if there was something to cancel

class ToolController(QObject):
    sigCommit = Signal(object)               # Plan, to be applied by the panel
    sigHint = Signal(str)
    sigSelection = Signal(object)            # rows
    def set_tool(self, key: str) -> None: ...
    def escape(self) -> None: ...

class ToolSurface(pg.GraphicsObject):        # one per lane (5.10)
    def __init__(self, ax, controller: ToolController): ...
    def arm(self, on: bool) -> None: ...

class KeyRouter(QObject):                    # 5.9
    def __init__(self, panel: "WavetrackerPanel"): ...
    def eventFilter(self, obj, ev) -> bool: ...

# panel.py
class WavetrackerPanel(QWidget):
    sigModelChanged = Signal(object)         # Change
    def __init__(self, browser, parent=None): ...
    # lifecycle: showEvent attaches overlays and surfaces; closeEvent and
    # about_to_flush_labels() handle unsaved edits (8.4) and stop the runner
    def open_results(self, folder, ident_file="ident_v.npy") -> None: ...
    def apply_plan(self, plan: Plan) -> None: ...   # the single place apply() is called
    def undo(self) -> None: ...
    def redo(self) -> None: ...
    def save(self) -> bool: ...
    def track_visible(self) -> None: ...
    def track_recording(self) -> None: ...
```

`__init__.py`:

```python
def audian_wavetracker_panel(browser):
    """Run wavetracker on the recording and correct its tracks."""
    from .panel import WavetrackerPanel
    return "Tracks", WavetrackerPanel(browser)

audian_wavetracker_panel.menu_path = ("Wavetracker",)
audian_wavetracker_panel.menu_tip = (
    "Track wave-type electric fish with wavetracker and correct the tracks "
    "on the spectrogram: merge, cut, erase, assign and add detections."
)
```

### 7.4 Working in parallel

The three packages meet only at the interfaces above.

* **A** needs nothing from B or C.
* **B** needs `FrameGrid` (A) for `peaks_job` only; until A lands it uses a
  local namedtuple with the same fields.
* **C** builds against A's interface with a synthetic `TrackSet`
  (`TrackSet.from_arrays` on generated tracks; a stand-in implementing the
  read side can be written in an hour if A is not there yet), and against
  B with a `FakeRunnerClient` (same signals; `submit` writes a canned
  results directory and emits `sigResult` from a single-shot timer).

Order of merging: A, then B, then C.  Each lands with its tests green and
`tests/test_lint.py` clean.

---------------------------------------------------------------------------

## 8. Saving, recovery, and the unsaved-changes rule

### 8.1 What a save writes

Into the session's results directory (or a directory chosen with "Save as…"
for a session built from snippets):

| file | when | content |
|---|---|---|
| `ident_v.npy` | every save | current identities |
| `ident_v.tracked.npy` | first save into this directory, then extended when rows are appended | the tracker's identities (`tracked`); **never overwritten for existing rows** |
| `fund_v.npy`, `idx_v.npy`, `sign_v.npy`, `cplx_v.npy` | when the row count on disk differs from `n` | all rows; the first rows are byte-identical to before (append-only) |
| `times.npy`, `wavetracker.json` | only when the directory has none (session built from snippets) | the full grid's times; meta merged from the first accepted snippet's `wavetracker.json`, with `start`, `duration` and `input`/`files` set to the whole recording |
| `eodsorter.json` | every save | see below |

`wavetracker.json` of an existing run is never modified.

```json
{"version": 1,
 "n_tracked_rows": 1000000,
 "next_id": 2417,
 "labels": {"12": "female A"},
 "notes": {},
 "recording": ["/data/rec.wav"],
 "saved_at": "2026-10-07T14:03:12",
 "history": ["14:01:02 Merge 31 into 12 (4 conflicts unassigned)", "..."]}
```

`history` is for the record only; undo does not survive a reload.  The
directory still loads with `wavetracker.results.Results.load`, because the
five required files are present and parallel and the extra files are
ignored by it; a test (8.5) checks this against a frozen copy of
`Results.load`.

`ident_v.npy` always holds the *current* identities, so cleanup,
positioning and every downstream tool read the corrected tracks without
knowing about this plugin.  "Revert to tracker output…" applies `tracked`
as an undoable command (it does not touch files until the next save).

### 8.2 Atomicity

A save writes several files, and `Results.load` needs them consistent.

1. Every file is written to a temporary neighbour
   `.<name>.tmp<pid>`, flushed and fsynced (as `audian.atomicwrite.
   replace_atomically` does; the plugin copies the 20-line helper into
   `model.py`, since `atomicwrite` is not in `pluginapi` and `model.py` must
   not import `audian`).
2. `eodsorter.commit.json` is written (atomically) listing the pending
   `(tmp, final)` pairs.
3. Each pair is `os.replace`d.
4. `eodsorter.commit.json` is removed.

`finish_interrupted_save(folder)`, run by `TrackSet.load` first, completes
step 3 for every pair whose temporary file still exists and removes the
commit file; the panel notifies "completed an interrupted save".  A crash
before step 2 leaves only stray temporary files, which are removed.

### 8.3 Autosave

Two seconds after the last edit (debounced, on the panel's thread; the
write is under 50 ms for 1M rows), `write_autosave` writes
`.eodsorter-autosave.npz` into the results directory (or the user cache
directory for an unsaved session): `ident`, the appended rows since the
last save, `next_id`, labels, the history labels, and the `ident_v.npy`
mtime it was based on.  Saving or discarding deletes it.  On load, if an
autosave newer than `ident_v.npy` exists and differs, the panel asks
"Recover unsaved edits from 14:03?" (**Recover** / **Discard**); recovery
applies it as one undoable command "Recovered unsaved edits".

### 8.4 Unsaved changes

* The header shows `●` and "unsaved changes"; the tab title becomes
  "Tracks ●".
* **Opening other results, running a whole recording, or "New session"**
  with unsaved edits: **Save**, **Discard**, **Cancel**.
* **Closing the tab** (`closeEvent`; audian does not let a panel veto it):
  **Save** or **Discard**.  If the dialog cannot be shown, the autosave is
  kept, so nothing is lost.
* **Closing the file or quitting** (`about_to_flush_labels()`, the hook
  audian calls on each panel; `closeEvent` is not delivered on quit): the
  same **Save** / **Discard** question.
* Never saved silently into the results directory, and never written
  anywhere outside it except the autosave in the cache directory for
  unsaved sessions.

### 8.5 Loading

`TrackSet.load(folder, ident_file)`:

* requires `fund_v.npy`, `idx_v.npy`, `sign_v.npy`, `times.npy`; `ident_v.npy`
  missing means all NaN (as `Results.load`);
* raises `ResultsError` for non-parallel arrays, `idx_v` outside `times`,
  `sign_v`/`cplx_v` row counts that differ, or non-integer ids;
* returns complaints (shown as warnings, loading continues) for: recording
  file names in `wavetracker.json` that differ from the open recording's;
  `times[-1]` beyond the recording's duration; an irregular grid (snippet
  runs disabled); duplicates per id and frame in the input (left alone, and
  `crossing`-style issues point at them; a "Resolve duplicates" button
  applies the local-median rule as one command);
* `ident_file` other than `ident_v.npy` (e.g. `ident_v_cleaned_n2.npy`, chosen
  in the open dialog from `ident_variants`) loads `ident_v.npy` as the base
  and applies the variant as the first, undoable, history entry.  Legacy
  `all_*` files are not read.

---------------------------------------------------------------------------

## 9. Test plan

Every test file follows the suite's conventions: `QT_QPA_PLATFORM=offscreen`
and scoped enums (`Qt.MouseButton.LeftButton`) are forced by
`tests/conftest.py`; tests using the `app` fixture are marked `slow`
automatically; nothing writes into the real settings store (the conftest
redirects it) or beside real recordings (use `tmp_path`).

### 9.1 Fast, pure (`-m 'not slow'`, target under 5 s in total)

`test_eodsorter_model.py` (A):

* every `plan_*` on small hand-built track sets, with the expected
  `rows/new/dropped` written out; id 0 as source, target and anchor; an
  all-NaN session (first id is 0); a session whose highest id was undone
  (the next id is still higher);
* conflict rules: merge with overlapping frames keeps the point closer to
  the local median (and the same result for every pick order); assign
  displaces the target's point; new-id with two selected points in one
  frame; add skips occupied frames;
* `EditRejected` for: merge of one id, cut leaving an empty side, swap with
  one id, empty selections, a plan applied at the wrong revision;
* **random-edit property test**: 300 random plans on a random 5,000-row set
  (fixed seeds); after each, `check_invariant()` holds, every array has `n`
  rows, the first `n_tracked` rows of `fund/idx/sign/cplx` are unchanged;
  then undo everything and compare to the start byte for byte, then redo
  everything and compare to the end state;
* history: depth and byte caps drop the oldest entries; a new edit after
  undo drops the redo branch; `jump` to every position equals undo/redo
  step by step; dirty state across save, undo past save, redo to save;
* local-median rule equals a frozen copy of wavetracker's
  `resolve_duplicates` on random duplicate sets;
* `FrameGrid`: `step` equals wavetracker's `step_size` for a table of
  `(nfft, overlap)`; `frame_range`/`sample_range` round trips;
  `from_results` rejects irregular times;
* `load_snippet` + `plan_replace_span`: offsets, fresh ids, edge stitching
  (left only, right only, both to the same id, both to different ids, the
  ambiguous case);
* save/load round trip; the saved directory loads with a frozen copy of
  `Results.load`; `ident_v.tracked.npy` is written once and never
  overwritten; appended rows extend it; an interrupted save (commit file plus
  temporaries, simulated) is completed by `finish_interrupted_save`;
  autosave write/read/recover; `next_id` survives a reload;
* `model` and `geometry` import no Qt (the `sys.modules` probe).

`test_eodsorter_geometry.py` (C):

* `polylines` breaks at id changes and at gaps, and the per-id path gives
  the same vertex set as the vectorised path for strides of 1;
* `nearest`, `brush_segment` and `line_crossings` against brute-force
  versions on random data, at several zoom levels;
* `thin_points` keeps exactly one point per pixel cell;
* `slot_of` uses all 10 slots for ids 0..9 and is stable.

`test_eodsorter_runner.py`, fast part (B), without torch or wavetracker:
`tests/data/fake_wavetracker/` is a stub package (`wavetracker/__init__.py`
with `__version__`, `io.py` with `MULTI_INPUT`, `config.py` with a minimal
`Config`, `pipeline.py` whose `detect` writes a tiny results directory and
calls `progress` three times, `results.py`) put on `PYTHONPATH` for a
`subprocess.Popen([sys.executable, "-u", wtrunner.py])`:

* `hello` first, with the stub's version and capabilities;
* `detect` streams `progress` then `result`, and the partial directory is
  renamed to the final one;
* a `print()` inside the stub's `detect` ends up on stderr, never on the
  protocol channel;
* a stub that raises gives one `error` line with the traceback, and the
  runner keeps serving;
* unknown config keys give `bad_request`;
* `wavetracker_status`: wavetracker imports in this environment without
  loading torch or numba, and a package that fails to import gives the
  one-line "not installed in this environment" error.

### 9.2 Slow, Qt (`app` fixture, offscreen)

`test_eodsorter_runner.py`, slow part (B): `RunnerClient` with the stub —
state transitions, signals, `cancel()` terminates a stub that sleeps and
reports `cancelled`, restart on the next job, `SnippetExporter` writes a WAV
with the right samples from a multi-file test recording.

`test_eodsorter_panel.py` (C), with a module-scoped `browser` fixture as in
`tests/test_frequencybands.py` and a results directory generated with numpy
in `tmp_path` (no wavetracker needed):

* the plugin is discovered, its menu entry has a tip
  (`tests/test_menuhelp.py` covers this too), the tab opens and closes, and
  closing removes every item it added to the lanes;
* overlays attach one per spectrogram lane; the number of graphics items per
  lane does not grow with the number of ids;
* each tool, driven through its `press/move/release/click` methods: the
  model changes as specified, the history gains one entry, the preview
  during the gesture equals the committed plan;
* one real drag with `QTest`/`pg` mouse events on a `ToolSurface` reaches
  the tool (the Qt event path works), and a middle-drag pans the view (no
  zoom box, no edit, same zoom);
* `KeyRouter`: with edit mode on and the pointer over a lane, `Ctrl+Z`
  undoes and audian's Pan zoom does not toggle; with edit mode off, it
  toggles Pan zoom; with focus in a `QLineEdit`, `V` types a "v";
* the action inventory is unchanged with the panel open (no window actions
  were added);
* undo/redo/jump through the history list;
* snippet flow with `FakeRunnerClient`: provisional layer drawn, accept is
  one history entry, discard leaves the model untouched;
* unsaved-changes prompts (dialogs patched to return a fixed button);
* performance smoke: a 100,000-detection set pans and hovers within ×5 of
  the 6.1 targets scaled down.

### 9.3 With real wavetracker (opt-in)

Tests marked `wavetracker` (wavetracker is installed with claudian, so
nothing needs to be set):
a synthetic recording from `wavetracker synth` run through the real runner
for `detect` (whole and snippet), `peaks`, and `cleanup`; the snippet's
detections equal the whole run's in frames away from the snippet edges.
Upstream tests live in the wavetracker repository (4.6).

### 9.4 Lint

`tests/test_lint.py` runs `ruff check` and `ruff format --check` over
`src tests scripts`; `wtrunner.py` and the stub package are included and
must be clean.  Before each commit:
`.venv/bin/ruff format src tests scripts && .venv/bin/ruff check --fix src tests scripts`.

---------------------------------------------------------------------------

## 10. Out of scope, and open questions

### 10.1 Out of scope for the first version

* Moving detections in frequency (editing `fund_v` values).  Detections are
  measurements; a wrong one is unassigned and, if needed, replaced by an
  added one.
* The legacy `all_*` / single-channel format, `meta.npy`, `spec.npy`.
* Its own figure export (audian's screenshot, `Ctrl+Alt+S`, exists).
* `merge-by-position` and electrode-position views (showing `sign_v` of the
  hovered track as an electrode map is a natural follow-up).
* Undo across reloads (the history in `eodsorter.json` is a record, not a
  journal).
* Several results directories open at once for one recording.

### 10.2 Open questions

1. **Plugin keys in the cheat sheet and command palette.**  audian has no
   hook for a panel to contribute actions.  A small core API
   (`browser.register_plugin_actions(title, [(key, text, callable)])`) would
   let the router's table appear there; it is not needed for this design.
2. **Click side effects.**  `DataBrowser.mouse_clicked` ignores
   `ev.isAccepted()`, so every click on a track also focuses that channel,
   and a Shift+click (Cut's "before" variant) also extends the channel
   selection.  If that proves annoying, the fix is a one-line core check of
   `ev.isAccepted()`; the alternative is moving the "before" variant to
   `Alt+click`.
3. **Directory inputs in wavetracker** still go through thunderlab's
   continuity heuristic and can drop the tail of a TASCAM session.  The list
   input of 4.6 avoids it for audian; whether the CLI's directory input
   should use `_open_joined` too is wavetracker's decision.
4. **Snippet thresholds.**  "From loaded results" is the default when there
   are results; whether a fresh session's snippets should instead estimate
   thresholds from a longer stretch around the view (e.g. ±60 s) needs real
   data to decide.
5. **Edge-stitching tolerance.**  The design uses the session's
   `tracking.freq_tolerance` and `max_dt`; dense field recordings may need a
   stricter value, exposed as a field if so.
6. **GPU contention.**  A whole-recording run on CUDA while audian is
   rendering is fine on the developer's machine; a "CPU only while I work"
   option is cheap to add if it is not elsewhere.
7. **Cleanup** assumes a small known number of persistent fish; on dense
   field data it should not be offered at all.  Whether to hide it based on
   the number of ids is open.
