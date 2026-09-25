# Phase 2 — making the safety net trustworthy

Branch `worktree-bandsorter-plugin`, on top of Phase 1 (`90f97fc`).  Written
for whoever picks this up; the plan it follows is the Phase 0 audit, and the
eight settled decisions in its section 13 still govern.

## Where the suite stands

| | default | `--realdata` |
|---|---|---|
| before (`90f97fc`) | 1237 passed, 3 skipped, **1 failed, 1 error** | — |
| after | **1229 passed, 28 skipped** | **1254 passed, 3 skipped** |

Both full runs were green, at ~8m45s each.  The 25 extra skips are the
real-data tests, now deselected by default rather than silently absent.

## Two things that were not bugs

Both baseline failures came from the reader using audian while the suite ran,
and both are the argument for the rest of this phase.

* The settings guard reported *"the suite wrote to the real settings store"*
  about a write the **GUI** made.  It compared a file's content against its
  content at session start, which cannot tell one process from another.
* `test_every_span_in_later_exp3_wavs_is_learned` failed `assert 0 >= 5`
  because the sidecar it reads had been relabelled that evening and no longer
  had a `pulse` category.

A test whose fixture is a file its owner edits by hand fails while they work.

## What landed

**The settings fixture.**  The end-of-session guard is now an in-process audit
hook on `open` / `os.rename` / `os.remove` under the reader's own config and
cache directories.  It cannot see another process, it names the test that did
it rather than only the file, and being directory-based it covers stores
nobody enumerated.  Measured at 0.82 µs per `open` — 0.08 s across a hundred
thousand, against a suite that runs for ~525 s.  `tests/test_settingsguard.py`
fails it on purpose, against a scratch directory.

`test_controlpanel.py` assigned `settings_path` and restored nothing, so from
the eighth module onward the store was its `stack0` directory and every later
fixture that "restored the original" reinstalled the hijack.  Deleted, and the
fixture now fails the run if a module leaves either store moved.

Six redundant per-module redirects are gone.  **Four stay, for reasons now
written into the fixtures themselves** — `test_settings` and `test_smoketest`
exercise the machinery, and `test_annotationpanel` and `test_joinmarkers` need
a store that is *empty per test*, which a session-scoped one stops being.
`test_panelsplitter`'s `build_window` keeps its redirect for per-window
freshness: `restore_panel_split` is read at construction, so a shared store
lets one window inherit another's dragged split.

**Synthetic session fixtures.**  `write_split_recording` in
`tests/test_session.py` writes a multi-file recording with the stamps a
recorder leaves — bext `OriginationTime` is a *close* time, so a part's stamp
is the session start plus every duration up to it plus every gap before it.
Written that way it reproduces exp3's real stamps to the second.

Three parts of one length and a short one last is load-bearing, not cosmetic:
the loader's continuity check measures `duration(i-1) - duration(i)`, which
cancels while the parts are equal and bites once, at the last join.  Measured
on the replica: stock thunderlab returns **288,000 of 312,000** frames,
dropping the last part exactly as it drops exp3's final 825 s.  That is now a
test, so the fixture cannot quietly stop provoking the bug it guards.

`test_dataloader.py`, `test_alignment.py` and the frequencybands ground truth
now run everywhere.  25 tests carry the `realdata` marker.

**One source fix.**  `verify_sha256` cached the *verdict* keyed on the file,
so the same recording under two disagreeing bundles returned the first answer
— the provenance check failing open.  It was known and stepped around: the
existing test called `_SHA_CACHE.clear()` between its cases.  It now caches
the digest; deleting that workaround is the proof.

## Running it

```
uv sync                                           # pytest, ruff, the bands extra
.venv/bin/python -m pytest tests/ -q              # ~6m55s
.venv/bin/python -m pytest tests/ -q -m "not slow"  # 622 tests, ~9 s
.venv/bin/python -m pytest tests/ -q --realdata   # adds the 25 real-data tests
.venv/bin/ruff check src tests
```

`uv run --locked pytest ...` works now that `uv.lock` is current, and no
longer re-locks.  Run the full suite in the foreground with a ~595000 ms
timeout.  `test_panelsplitter.py::...keeps_the_focus_on_screen[resize]`
still fails as a subset and passes in a full run.

## Second pass, 2026-09-25

All five open items are done.  The suite on this machine: **1249 passed,
27 skipped, 2 failed** (both explained below); `-m "not slow"` 622 in 8.6 s.

* **Environment.**  `.venv` had drifted to PyQt5 with no PySide6, and
  `uv.lock` was stale against pyproject (audioio's git source had never
  been locked, which is why `uv run` kept re-locking).  Re-locked and synced.
* **The suite ran on the desktop.**  conftest did
  `setdefault("QT_QPA_PLATFORM", "offscreen")`, and a Hyprland terminal
  exports `wayland`, so the run opened real windows and failed 13 tests.
  Now assigned outright.
* **ruff / dev group / CI.**  `[tool.ruff.lint]` holds E4,E7,E9,F; the tree
  is clean and `tests/test_lint.py` fails, rather than skips, without ruff.
  `.github/workflows/tests.yml` runs ruff and pytest; **it has not run on
  GitHub yet.**  The old `tests` workflow is renamed `docs`.
* **`app` fixture** lives once in conftest, replacing nine copies.
* **`slow` marker** is derived in conftest from use of `app`.  Making the
  fast subset stand alone found two tests that drew a QPixmap without an
  application and aborted Qt once the module that happened to make one was
  deselected.
* **`test_analyzer.py`** pins the region-analysis path, and found a real
  bug: `save_analysis` appended onto PlainAnalyzer's own table, so every
  save after the first wrote the statistics columns again, stale copies
  full of `-`.  Fixed.
* **`smoke_test.py --interact`** no longer edits the sidecar (md5 identical,
  70/70 clean).  The harness now snapshots each sidecar, restores it and
  reports a fault if a run changes it.
  `data/Gryllus_campestris-editable-labels.csv` got into git by accident in
  5020b8c.  Whether to untrack it is the owner's call.
* **`collect_orphan_widgets`: measured, not changed.**  With test_analyzer
  adding a browser per test, which is the condition todo.md says crashed 2
  runs in 4, three full runs were clean.  The prescribed `shiboken6.isValid`
  guard was not added: a *segfault* at `parentWidget()` means shiboken did
  not know the object was gone, and then `isValid` returns True as well.
  The guard could not have caught the crash it is meant for.  Leave the
  "do not add browser modules" comments until it reproduces.

**Still red: two pixel-exact layout tests**, on the unmodified tree too:
`test_the_clamp_always_includes_the_split_the_lane_opens_on` (161 against
162 px) and `test_the_panel_gives_its_height_back_to_the_stack` (647 against
651).  noto-fonts and fontconfig were upgraded on 2026-09-22, after the
green run on 09-02.  That is the likely cause, but it is unconfirmed: no old
package was left in the cache to test against.  Ubuntu's fonts differ again,
so CI will fail these two until they are either re-measured or made
font-independent.  That is a decision about what those tests are for.

Still outstanding: the decided deletions (the envelope surface,
`songdetector.py`, the analyzer event API, `dispatch_resolution`,
`set_spectrogram`).

## A note on method

Write the test, revert the fix, confirm it fails, restore.  It earned its keep
twice here: it caught that `verify_sha256`'s existing test only passed because
of a cache clear, and the full-suite run caught a fixture I had deleted as a
duplicate that was load-bearing for a reason a subset run cannot show.  That
second one is the argument for finishing a batch and then running everything,
rather than trusting a fast subset.
