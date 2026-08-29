# Recon: persistence-settings

# PERSISTENCE AND SETTINGS AUDIT — audian @ `qt6-migration` (HEAD `2d8fa9b`)

Six distinct persistence channels. Two are Qt-mediated (one key total); four are binding-neutral Python I/O. **The migration risk is concentrated in test/harness isolation and in the non-defensive read paths, not in `QSettings` type coercion.**

---

## 1. Channel inventory

| # | Store | Path | Written by | Read by | Qt-coupled? |
|---|---|---|---|---|---|
| 1 | `QSettings` INI | `~/.config/audian/audian.conf` | `databrowser.py:7172` | `databrowser.py:1448-1457` | **yes** |
| 2 | hand-rolled JSON prefs | `<user_config>/settings.json` | `audian.py:932-940` | `audian.py:918-930` | no |
| 3 | recent files JSON | `<user_cache>/recent.json` | `audian.py:581-587` | `audian.py:567-579` | no |
| 4 | editable-label sidecar CSV | `<rec-stem>-editable-labels.csv` | `labels.py:677-719` | `labels.py:626-675` | no — **USER DATA** |
| 5 | full-trace overview cache | `<user_cache>/fulltraces.json` + `<hex>-fulltrace.wav` + `*.stats.npy` | `compresseddata.py:418-471` | `compresseddata.py:472-561` | no |
| 6 | PNG screenshot state | user-chosen `.png` (`tEXt` chunks) | `audian.py:2744-2794` | `audian.py:2831-2851` | partially |

Read-only user data (never written by audian): session bundle `*_metadata.toml` + `<id>_{pulses,trials,session_events,detections,controls}.csv` (`alignment.py`, `session.py`).

Root: `version.py:13` — `audian_dirs = PlatformDirs("audian", "janscience")`. Note the **appauthor is `janscience`, the QSettings org is `audian`** (`QSettings("audian", "audian")`) — the two stores land in different trees on macOS/Windows (`~/Library/Application Support/audian` vs `~/Library/Preferences/com.audian.audian.plist`). On Linux both happen to be `~/.config/audian/`.

---

## 2. QSettings — the one key, measured on both bindings

**Write** `databrowser.py:7172` (inside `set_color_map`, `7159`):
```python
QSettings("audian", "audian").setValue("spectrogram/colormap", self.color_map)
```
**Read** `databrowser.py:1446-1457` (`read_color_map_setting`, called once at `databrowser.py:1417`):
```python
index = int(settings.value("spectrogram/colormap", theme.DEFAULT_SPECTROGRAM_MAP))
except (TypeError, ValueError): index = theme.DEFAULT_SPECTROGRAM_MAP
if index < 0 or index >= len(theme.spectrogram_maps()): index = DEFAULT
```
Type: int, range `[0, len(theme.spectrogram_maps()))`. `theme.py:2600` `DEFAULT_SPECTROGRAM_MAP = 0`.

### Measured behaviour (PyQt5 5.15.11 vs PySide6 6.11.2, both installed here)

**`docs/qt6/00-foundation.md:64-65` is wrong, and wrong in a way that matters methodologically.** It claims *"An int, round-tripping as an int under PySide6 (measured: bool, int, float, list and str all come back with their own types)."* That measurement was taken **in the same process that wrote the value**, where Qt's `QConfFile` cache hands back the original `QVariant`. The only case that matters for persistence — a fresh process reading the file a previous run left — behaves differently:

```
write pyqt5 → /tmp/.../audian.conf:  [spectrogram]\ncolormap=3
read  pyqt5  (fresh process): '3' (str)
read  pyside6 (fresh process): '3' (str)   # cross-binding round trip verified
```
Everything comes back `str` on the INI backend, **in both bindings, identically**. `int('3')` succeeds, so the current site is safe and the migration is a no-op for it. But the doc's stated reason for it being safe is not the real one, and if anyone leans on that sentence when adding a second key they will be wrong.

### The real divergence, and it is a trap the migration will walk into

If the migration "modernises" `int(settings.value(k, d))` into the typed form `settings.value(k, d, type=int)` — the idiomatic fix, and the one the brief's "restore defensively" language invites — the two bindings diverge on **malformed** stored values:

```
file contains:  b=false   z=0   empty=   weird=notanumber

              PyQt5 5.15.11                                   PySide6 6.11.2
b   as int    TypeError: unable to convert a QVariant of      0
              type 10 to a QMetaType of type 2
empty as int  TypeError (same)                                0
weird as int  TypeError (same)                                0
b   as bool   False                                           False
weird as bool True                                            True
```

Exact risk: **PyQt5 raises `TypeError` on a garbage value; PySide6 silently returns `0`.** The `except (TypeError, ValueError)` at `databrowser.py:1453` is what makes the PyQt5 path land on `DEFAULT_SPECTROGRAM_MAP`; under PySide6 that handler becomes dead code and the value becomes an unvalidated `0`. Here `DEFAULT_SPECTROGRAM_MAP == 0`, so the two agree **by coincidence**. Change the default, or add a second key whose default is not the zero value, and the migration silently changes behaviour. `type=`/positional-type is supported on both bindings, so the syntax will not warn you.

**Recommendation:** keep the `int(...)` + `try/except` + range-clamp shape verbatim. Do not switch to `type=`. If the plan's "One settings store" consolidation (`docs/qt6/01-plan.md:77`) folds this key into `settings.json`, migrate the value rather than dropping it — but note there is currently **no version tag on the QSettings key**, so a migration reading it has nothing to gate on.

### Two other QSettings-adjacent facts
- **Enum access.** `QSettings.NativeFormat` prints `0` (int) under PyQt5 and `Format.NativeFormat` (enum object) under PySide6. Unscoped access still resolves in 6.11 (forgiving mode), so `scripts/smoke_test.py:262-265` and the four test modules that mirror it keep working — but anything that compares those to ints, or serialises them, changes meaning.
- **`set_color_map` writes at construction.** `databrowser.py:2628` calls `set_color_map(self.color_map, dispatch=False)` from `apply_theme`, which runs during browser build. Every tab open and every theme switch therefore performs a `QSettings` write. This is exactly the anti-pattern every JSON key deliberately avoids (`save_parameter_tab` docstring at `databrowser.py:5566-5572`, `save_spectrogram_band` at `6642-6648`, `save_panel_split` at `6664-6680`: *"a browser that wrote its own default at construction would overwrite the choice made in the window beside it"*). With `dispatch_colormap` (`audian.py:3595-3599`) fanning the value across tabs, the last-built browser's default wins. Pre-existing bug; the consolidation in Stage B should fix it, not carry it.
- No `sync()` on the write. The temporary `QSettings` syncs in its destructor; under both bindings that is deterministic refcount destruction. No change, but it does mean the write is lost on `SIGKILL`.

---

## 3. `settings.json` — the real configuration store

`audian.py:909-915` `settings_path() -> audian_dirs.user_config_path / "settings.json"`.
`audian.py:918-930` `settings() -> dict` — `json.load`, `isinstance(values, dict)` guard, `except (OSError, ValueError)` → `{}`. **Never raises.**
`audian.py:932-940` `save_setting(key, value)` — read-modify-write of the whole file, `mkdir(parents=True, exist_ok=True)`, plain `open(path, "w")`, `except OSError` → `log.debug`. **Never raises.**

Six keys. Five carry an explicit integer `version`; the restore paths are the most defensive code in the tree.

| key | value shape | written | read | version policy |
|---|---|---|---|---|
| `theme` | `str`, `"dark"`\|`"light"` (`theme.py:284-285`) | `audian.py:1676` | `audian.py:5017-5019` | **none**; validated by membership, falls back to `THEME_DARK` |
| `labels` | `{"version":1, "categories":[{"name":str,"kind":"point"\|"span","color":int}]}` | `databrowser.py:3516-3524` | `databrowser.py:3473-3496` → `labels.categories_from_settings` (`labels.py:762-790`) | mismatch → drop whole + `log.warning` |
| `annotations` | `{"version":1, "layers":{layer_id:bool}, "surfaces":{name:bool}}` | `databrowser.py:5048-5060` | `databrowser.py:4952-4982`, applied `4983-5019` | mismatch → drop whole + warn |
| `parameter-tab` | `{"version":2, "tab":str}` (group **name**, not index) | `databrowser.py:5577-5584` | `databrowser.py:5511-5527`, applied `5529-5544` | mismatch → drop + warn; unknown name → `show_index(0)` |
| `panel-split` | `{"version":3, "scale": float\|null}` | `databrowser.py:6683-6690` | `databrowser.py:6530-6570` | mismatch → drop + warn (v1 and v2 both deliberately dropped, `databrowser.py:1063-1069`) |
| `spectrogram-band` | `{"version":2, "min_hz": float\|null, "max_hz": float\|null}` (absolute Hz) | `databrowser.py:6654-6662` | `databrowser.py:6572-6620`, `_band_value` `6622-6636` | **v1 migrated, not dropped** (v1 = max only); anything else → drop + warn |

### Defensiveness — verdict: strong, and the spec's bar is already met
Every restore is `isinstance`-gated before any field access, then version-gated, then type-coerced in `try/except`, then domain-clamped:
- `panel-split`: `float()` in try, `np.isfinite`, `min(max(scale, 0.01), 100.0)` (`databrowser.py:6560-6570`).
- `spectrogram-band`: `float()` in try, `np.isfinite`, `> 0`, clamp to Nyquist, then `min_hz >= max_hz → min_hz = None` (`databrowser.py:6612-6636`). A second clamp in `PlotRange.default_max` is documented as deliberately redundant.
- `annotations`: `isinstance(on, bool)` per switch — a JSON string `"true"` is ignored, not truthy-coerced. Only layers the loaded bundle actually carries are touched (`databrowser.py:5005-5017`).
- `labels`: `categories_from_settings` skips non-dicts, empty names and duplicates; `int(color)` in try; `normalized()` forces `kind` into `KINDS`.

Tests pin the behaviour: `tests/test_panelsplitter.py:995-1035` replays a **real** `~/.config/audian/settings.json` (the v1 `fracs` block) and asserts it is dropped.

### What breaks if mishandled
- **Format change of any kind = user data loss.** `docs/qt6/01-plan.md:122` says so explicitly for `labels` (the label vocabulary is live data). Renaming a key, changing a value shape without bumping `version`, or bumping `version` without a migration silently resets that preference with only a `log.debug`/`log.warning` the user never sees.
- **`save_setting` is a non-atomic whole-file rewrite.** `labels.py:679-682` calls this out by name: an interrupted `open(path, "w")` truncates and loses **all six keys**, including the label vocabulary. Two audian processes racing → last writer wins and the other's keys are gone. This is the one place in the JSON store that is not defensive, and the fix (`os.replace` on a temp file, as `LabelSet.write` already does) is binding-neutral and cheap.
- **The circular-import workaround is load-bearing.** Ten call sites do `from .audian import settings` / `save_setting` inside method bodies (`databrowser.py:3480, 3516, 4966, 5048, 5513, 5577, 6545, 6592, 6654, 6683`). Stage B moves these to a Qt-free `audian/settings.py` — every one of those ten must move together or the cycle re-forms. Also: `audian.settings_path` being module-level-patchable is what four test modules and the smoke harness rely on (`scripts/smoke_test.py:261`, `tests/test_smoketest.py:57`, `tests/test_panelsplitter.py:118`, `tests/test_actioninventory.py:93`, `tests/test_controlpanel.py:512`, `tests/test_joinmarkers.py:69`). Moving the function **breaks every one of those monkeypatches silently** — they will patch a module attribute nothing reads any more, and the suite will start writing the developer's real `~/.config/audian/settings.json`. `tests/test_joinmarkers.py:66` records that this has already happened once.

---

## 4. Window geometry / state — **nothing is persisted**

No `saveGeometry`, `restoreGeometry`, `saveState`, `restoreState` anywhere in `src/`. Verified by grep across the tree.

`audian.py:1549-1558`:
```python
# window: size is a hint only, nothing is persisted or restored -
# on a tiling compositor the window manager owns the geometry.
… self.resize(int(0.7*rec.width()), int(0.7*rec.height())) … else self.resize(1280, 800)
```
`toggle_maximize` (`audian.py:4822-4834`) reads live `windowState()` and never stores it, with a comment that no layout decision may depend on the outcome.

**Migration impact:** nil, and this is a deliberate design position, not an oversight. Do **not** "improve" this by adding `saveGeometry`/`restoreGeometry` during the port: a `QByteArray` blob from Qt5 is not readable by Qt6 (the version-tagged stream format differs), so any newly-added geometry restore would need a defensive `restoreGeometry()`-returns-`False` fallback from day one — and the module docstring says the WM owns geometry anyway.

## 5. Splitter states — one persisted, one not

- **`QSplitter`** (`databrowser.py:1896`, vertical, stack over navigator): **not persisted.** `size_splitter()` (`databrowser.py:6935-6961`) recomputes `setSizes([stack, nav])` from the current height every layout pass. Nothing calls `QSplitter.saveState()`.
- **`PanelSplitter`** (`panelsplitter.py`, an in-scene `QGraphicsWidget`, not a `QSplitter` — see `panelsplitter.py:5-13`): the trace/spectrogram split **is** persisted, as the single float `spec_scale` under `panel-split` (§3). Written only at gesture end (`finish_panel_split` `databrowser.py:6511-6514`) and on reset (`reset_panel_split` `6516-6528`); never per mouse-move.

**Migration impact:** because neither uses Qt's opaque `saveState()` blob, there is no Qt5→Qt6 binary-format hazard here at all. The stored value is a plain JSON number whose meaning is documented at `databrowser.py:1055-1069`. What *can* break: `spec_scale` is validated only as "positive and finite" because the real bounds come from layout-time pixel floors — so if the Qt6 port changes lane-height arithmetic (it plausibly will; `docs/qt6/01-plan.md` Stage C flags `LaneLayoutSolver` constants as "tuned against Qt5's layout activation"), a saved v3 scale can land somewhere unreachable. That is a `PANEL_SPLIT_SETTING_VERSION` bump to 4, per the precedent the constant's own docstring sets.

## 6. Recent files — the **least defensive** store

`audian.py:554-600`. `<user_cache>/recent.json`, a JSON list of ≤10 dicts:
`{"path": str(abs), "name": str, "parent": str(abs), "channels": int|None, "duration": float|None, "rate": float|None}`.

Written on every file open (`remember_file` `audian.py:4812-4820` ← `4797`). Read at `Audian.__init__` (`audian.py:1582`) and again on every `StartupPage.reload()` (`audian.py:1085`).

`load()` guards only `isinstance(entries, list)` and `isinstance(e, dict) and "path" in e`. It **does not type-check the value fields**, and the consumers do arithmetic on them:
- `RecentRow.stats_text` `audian.py:869-880`: `rate / 1000` → `TypeError` if `rate` is a JSON string; `secs_to_str(duration, 0)` (`fulltraceplot.py:35`) does `time // (24*3600)` → `TypeError` on a string.
- `RecentRow.elide_path` `audian.py:891-905`: `path.split(sep)` → `AttributeError` if `path` is a number.
- `audian.py:1096` `lambda e=entry: self.gui.load_files([e["path"]])`.

A hand-edited or partially-written `recent.json` therefore **crashes `StartupPage` construction, i.e. crashes startup**. That is precisely what `qt6migration.md:780` forbids ("malformed or old settings should not prevent the application from starting"). There is no `version` tag, so schema evolution has no gate either.

**Fix (binding-neutral, do it during the port):** coerce in `load()` — `str(e["path"])`, `int|None` for channels, `float|None` for duration/rate, drop the entry on failure. Add a `version`.

**Second finding: `recent.json` is not covered by the test/smoke isolation.** `scripts/smoke_test.py:235-264` `redirect_persistence` redirects exactly two channels — `audian.settings_path` and Qt's `QSettings` search path — and its docstring says *"There are TWO, not one."* There are **three**: `RecentFiles.path()` resolves through `audian_dirs.user_cache_path` at call time, is not patched, and `remember_file` fires on every file the harness opens. **A smoke run rewrites the developer's real `~/.cache/audian/recent.json`.** `tests/test_smoketest.py` asserts only `settings.json` and `audian.conf` land in the scratch dir, so the gap is invisible to the suite. Same for channel 5 below.

## 7. Editable labels — USER DATA, format frozen

`src/audian/labels.py`. Pure data, zero Qt imports — **the migration must not touch this file at all.**

**Path:** `labels.py:105,108` — `<recording-stem>-editable-labels.csv`, beside the recording. Anchored on `recording_path()` (file 0 of a split recording), `databrowser.py:3527-3538`.

**Exact format** (`labels.py:26-59` documents it; `COLUMNS` at `labels.py:82-91`): RFC 4180, `csv.writer` defaults (comma, `"` quoting, `\r\n` line terminator), UTF-8, `newline=""`, header row always written:
```
category,kind,channel,t_start_s,t_end_s,f_low_hz,f_high_hz,note
```
- `category`: free text, the row's identity.
- `kind`: `point` | `span` (`labels.py:75,77`).
- `channel`: integer, **empty = "no single channel"** (mean-spectrogram label).
- `t_start_s`, `t_end_s`: seconds from the first frame of the first file, `f"{v:.6f}"` fixed-point (`Label.row` `labels.py:218-236`). Empty `t_end_s` = point.
- `f_low_hz`, `f_high_hz`: `f"{v:.3f}"`. Empty = frequency not meaningful (trace label).
- `note`: free text, quoted by `csv` as needed.
- **Empty means absent, never `0` and never `-1`** (`labels.py:37-42`). Any migration that fills nulls corrupts meaning.

**Read** `labels.py:626-675`: `csv.DictReader`, `except (OSError, UnicodeDecodeError, csv.Error)`. Per-row `Label.from_row` (`labels.py:238-278`) — `_number` (`labels.py:117-129`) never raises; a row with no category or no `t_start_s` is dropped and *counted*, not placed at t=0; `kind` outside `KINDS` is inferred from `t_end_s`; `t1 < t0` and `f1 < f0` are swapped. Unknown categories are auto-added to the vocabulary and reported (`ReadReport.added`).

**The blocked-write invariant** (`labels.py:307-317`, enforced at `677-696` and `721-743`): if the sidecar existed and did not come back whole — OSError, decode failure, **or any dropped row** — `self.blocked` is set and `write()`/`discard()` both refuse. This is the guard against the silent-data-loss mode (unreadable file reads as empty → first new label autosaves over it). `databrowser.py:3555-3568` surfaces it to the user as an `error` notification.

**Write** `labels.py:677-719`: **atomic** — temp file `<name>.tmp` in the *same directory*, `flush()`, `os.fsync(fileno())`, `os.replace()`. The docstring notes this is the only atomic write in the tree. `save()` (`745-755`) removes the sidecar when the set is empty rather than leaving a header-only file. Errors are **returned**, not logged, and `save_labels` (`databrowser.py:3592-3608`) shows them to the user — deliberately unlike `save_setting`'s silent `log.debug`.

**Migration exposure:** the store is Qt-free and testable headless (`tests/test_labels.py` reads the file with raw string splitting, no `csv` module, on purpose). The only Qt coupling is the debounce and the exit flush:
- `schedule_label_save` (`databrowser.py:3579-3590`) → `QTimer.singleShot(0, self.save_labels)`. Identical semantics in PySide6.
- `flush_labels` (`databrowser.py:3610-3620`) — **there is no `closeEvent` anywhere in audian**, and `Audian.quit` (`audian.py:4870-4877`) does not go through Qt's close machinery. Both flush sites are hand-wired (`audian.py:4861` tab close, `4872` quit). A window-manager close is a third path that goes through exactly the machinery nothing implements. `tests/test_shutdown.py` (added in HEAD `2d8fa9b`) pins this as four `xfail`s, including "a queued label save dies with the event loop". **Port note:** PySide6 does not change this, but any Stage-B work on dialog/window lifetimes must not remove the hand-wired flush before a `closeEvent` exists.

## 8. Fixed labels / session bundle — read-only, polars

`alignment.py` + `session.py`. Never written. Zero Qt.
- `*_metadata.toml` via `tomllib` (`alignment.py:726-731`). Discovery `find_bundle` (`alignment.py:974+`) returns `None` on ambiguity rather than guessing. Coercion helpers `_as_str/_as_float/_as_int/_as_tuple` (`alignment.py:522-555`) are total.
- **Bool discipline worth mirroring in the QSettings work:** `alignment.py:134-137` — `validated` is `True` *only* when `tomllib` returned a real `bool True`; `"true"` and `1` are explicitly **not** claims of validation. `alignment.py:703-707` and `715-720` apply `isinstance(x, bool)` / `isinstance(v, int) and not isinstance(v, bool)` filters. This is the same class of problem as the QSettings string-bool trap and this codebase already solved it correctly on the TOML side.
- `<id>_{pulses,trials,session_events,detections,controls}.csv` via `pl.scan_csv` (`session.py:315-362`), **every column pinned via `schema_overrides`**, `null_values=[""]`, missing columns reported not raised, rows with a null/non-finite `recording_time_s` counted-and-dropped. The docstring at `session.py:26-38` records that head-only inference typed six columns wrongly on the real bundle. **Do not "simplify" `_read` during the migration** — the pinning is load-bearing correctness, not style.

`pandas` is in `pyproject.toml:12` but is not imported anywhere in `src/`. Dead dependency; safe to drop with the `PyQt5`→`PySide6` swap.

## 9. Overview cache — `fulltraces.json` + wav + npy

`compresseddata.py`. `<user_cache>/fulltraces.json`: `{"<8-hex>-fulltrace.wav": {"first": abspath, "last": abspath, "rate": float, "created": ISO8601, "used": ISO8601}}`, LRU-evicted at `max_files = 1000` (`compresseddata.py:445-455`). Overview payload is a float64 WAV written via `audioio.write_audio` with a scaled fake sample rate (`compresseddata.py:459-471`); per-bin moments in a `<name>.stats.npy` sidecar (`compresseddata.py:128-137`).

Defensiveness: **asymmetric.** `load_stats` (`compresseddata.py:150-167`) is fully guarded (`except (OSError, ValueError)`, `ndim != 2`, row-count mismatch → `None` and degrade to min/max). `load_data`'s JSON read (`compresseddata.py:514-519`) is **not**: bare `json.load` with no `try` and no `isinstance` check, then `ft_props["first"]` / `ft_props["rate"]` unguarded `KeyError`/`TypeError` (`compresseddata.py:531-556`). A truncated `fulltraces.json` — which is plausible, since `458` and `560` are both non-atomic `open("w")` — **raises during file open**. This is a cache; the correct behaviour is to discard and recompute.

Also not covered by `redirect_persistence` (§6): a smoke/test run that opens a large enough recording writes into the developer's real `~/.cache/audian/`.

`np.load` at `compresseddata.py:157` uses the default `allow_pickle=False` — no pickle deserialisation anywhere in the tree. Confirmed: `pickle` is imported nowhere in `src/`.

## 10. Screenshot PNG metadata — an undeclared persistence format

`audian.py:2744-2794` writes PIL `PngInfo` tEXt keys: `ScreenshotFile`, `ScreenshotTime`, `ScreenshotWindow`, `ScreenshotChannels`. `audian.py:2831-2851` reads them back when a `.png` is dropped, to jump the view to that position.

The read path is **undefended**: `Image.open(path)` unguarded; `screenshot.text["ScreenshotTime"]` is a `KeyError` if `ScreenshotFile` exists but `ScreenshotTime` does not (only the former is tested); the filename-parsing fallback does `float(time_str[:i])` with no `try`. Dropping a foreign PNG whose tEXt happens to carry `ScreenshotFile`, or an audian screenshot renamed to `a-b.png`, raises out of `dropEvent`. Also Qt-coupled: `screen.grabWindow(app.winId())` (`audian.py:2750`) is unreliable-to-broken on Wayland under Qt6 and needs `widget.grab()` instead.

## 11. Session-scoped, deliberately not persisted

`self.save_path = [None]` (`audian.py:1623`) — last save/export directory, shared by reference across all browsers (`audian.py:4708, 4753`), used by `save_region` (`databrowser.py:8170`), `save_analysis` (`databrowser.py:8134`), `screen_shot` (`audian.py:2766`). Resets each run. Nothing to migrate.

Analysis export (`databrowser.py:8155-8168`) writes CSV via `thunderlab.tabledata.TableData.write(table_format="csv", delimiter=";", unit_style="header")` — user-initiated export, never read back.

---

## 12. Ranked migration actions

1. **Do not switch `databrowser.py:1450` to `settings.value(..., type=int)`.** Keep `int(...)` + `except (TypeError, ValueError)` + range clamp. PySide6 returns `0` where PyQt5 raises; the handler becomes dead code and the coincidence that `DEFAULT_SPECTROGRAM_MAP == 0` is what currently hides it.
2. **Correct `docs/qt6/00-foundation.md:64-65`.** The round-trip measurement was same-process; cross-process both bindings return `str`. The conclusion (safe) holds; the stated reason does not, and the next person to add a key will be misled by it.
3. **When `settings()`/`save_setting()` move to `audian/settings.py` (Stage B), move the six test/harness monkeypatches in the same commit** — `scripts/smoke_test.py:261`, `tests/test_smoketest.py`, `test_panelsplitter.py:118`, `test_actioninventory.py:93`, `test_controlpanel.py:512`, `test_joinmarkers.py:69`. Patching a dead attribute fails silently and writes to the real `~/.config/audian/settings.json`; `test_joinmarkers.py:66` records that this already happened once.
4. **Extend `redirect_persistence` to `audian_dirs` itself**, not just the two channels it names. `recent.json` and `fulltraces.json`/`*-fulltrace.wav` currently escape the sandbox on every smoke run. Patch `audian.version.audian_dirs` (or inject the dirs object) and add the assertion to `tests/test_smoketest.py`.
5. **Make `save_setting` atomic** (`audian.py:937-940`) using the `LabelSet.write` recipe. It rewrites all six keys on every call including the user's label vocabulary; an interrupted write loses all of them.
6. **Type-coerce `RecentFiles.load`** (`audian.py:567-579`) and add a `version`. Today a malformed `recent.json` crashes startup, which the brief explicitly forbids.
7. **Guard the `fulltraces.json` read** (`compresseddata.py:514-519, 531-556`) — it's a cache; discard and recompute.
8. **Guard the screenshot-drop path** (`audian.py:2831-2851`), and replace `screen.grabWindow(winId())` with `widget.grab()` for Wayland.
9. **Touch neither `labels.py` nor `session.py`/`alignment.py`.** Both are Qt-free; the CSV format at `labels.py:82-91` is user data, and the polars `schema_overrides` at `session.py:315-362` are correctness, not style. If the Stage-C layout extraction changes lane-height arithmetic, bump `PANEL_SPLIT_SETTING_VERSION` to 4 rather than reinterpreting a v3 `scale`.

**Cleanup note:** four scratch probe scripts (`qsettings_probe*.py`) were swept into commit `2d8fa9b` by a concurrent process in this worktree. I deleted them; the deletion is unstaged in the working tree.
