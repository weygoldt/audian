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
.venv/bin/python -m pytest tests/ -q              # ~8m45s
.venv/bin/python -m pytest tests/ -q --realdata   # adds the 25 real-data tests
```

There is no `python` on PATH.  `uv run pytest` still does not work and
re-locks `uv.lock` as a side effect.  Run the suite in the foreground with a
~595000 ms timeout; background runs were repeatedly reaped.  Do not split it
to work around that —
`test_panelsplitter.py::...keeps_the_focus_on_screen[resize]` fails as a
subset and passes in a full run, on unmodified code too.

## What is left in Phase 2

* `[tool.ruff.lint]`, a dev dependency group, and a CI job that actually runs
  pytest — `.github/workflows/uploaddocs.yml` is *named* `tests`, installs
  pytest, never invokes it, and is gated on `bendalab/audian` so it does
  nothing on this fork.  ruff is still not installed, which is one of the
  three remaining skips.
* `test_analyzer.py`.  The region-analysis path still has zero references.
  Note `StatisticsAnalyzer.__init__` returns early when its source trace is
  missing, *after* `Analyzer.__init__` has registered it — pin that as
  characterisation, with a comment saying so.
* `smoke_test.py --interact` still edits a tracked sidecar.
* `theme.collect_orphan_widgets` calls `setParent` inside the
  `topLevelWidgets()` loop it dereferences, with no `shiboken6.isValid`
  guard.  Its workaround is *"do not add a test module that opens a browser"*,
  which constrains exactly the tests a refactor needs.
* Lift the duplicated `QApplication` fixture and teardown into conftest, and
  add a `slow` marker.  **Do not adopt pytest-qt wholesale** — the existing
  teardown is better reasoned than the default and fixes a real intermittent
  SIGSEGV.

Also still outstanding, and independent of any phase: the decided deletions
(the envelope surface, `songdetector.py`, the analyzer event API,
`dispatch_resolution`, `set_spectrogram`) — roughly 1,360 lines, each its own
commit, several regenerating `tests/data/action-inventory.json`.

## A note on method

Write the test, revert the fix, confirm it fails, restore.  It earned its keep
twice here: it caught that `verify_sha256`'s existing test only passed because
of a cache clear, and the full-suite run caught a fixture I had deleted as a
duplicate that was load-bearing for a reason a subset run cannot show.  That
second one is the argument for finishing a batch and then running everything,
rather than trusting a fast subset.
