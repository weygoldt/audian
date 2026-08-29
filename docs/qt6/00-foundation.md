# Qt6 migration: measured foundation

Everything here was measured against PySide6 6.11.2 / Qt 6.11.2 and
pyqtgraph 0.14.0 on this machine, not read from a changelog.  The point is
that the decisions further down the migration rest on observed behaviour.

## The binding decision

PySide6, alone.  No `qtpy`, no compatibility shim, no second binding kept
alive "just in case" -- the migration brief rules that out and there is no
third-party dependency asking for it:

- `pyqtgraph` 0.14.0 detects and drives PySide6 directly (`pyqtgraph.Qt.QT_LIB`
  reports `PySide6`).  Plots, `ImageItem`, colormaps, `AxisItem`,
  `InfiniteLine` and `LinearRegionItem` all construct and paint.
- `thunderlab` 1.8.0 imports no Qt at all.
- Nothing else in the dependency set touches Qt.

The migration venv installs PySide6 and *not* PyQt5.  That is deliberate
beyond tidiness: pyqtgraph picks its binding by probing what is installed and
what is already imported, so a tree with both present is a tree where the
binding in use depends on import order.

## pyqtgraph stays

The brief says not to replace the plotting stack without profiling that
justifies it.  Nothing here justifies it: pyqtgraph runs on Qt6 unmodified,
and the interaction path the test suite depends on -- synthesised presses,
moves and releases delivered to a `PlotWidget` viewport -- still pans a
`ViewBox` under Qt6 (measured: x range `[-5.41, 104.41]` to `[-23.56, 86.26]`
across a 60 px drag).

What this migration owes the visualisation layer is a boundary, not a
replacement.

## APIs that are gone, and what replaces them

Measured by calling them:

| Qt5 API | Qt6 status | Replacement |
| --- | --- | --- |
| `QWheelEvent.delta()` | **removed** | `angleDelta()` -> `QPoint` |
| `QFontMetrics.width()` | **removed** | `horizontalAdvance()` |
| `QPainter.HighQualityAntialiasing` | **removed** | `RenderHint.Antialiasing` |
| `QMouseEvent.pos()` | deprecated, returns `QPoint` | `position()` -> `QPointF` |
| `QAction`, `QShortcut` | moved | `QtWidgets` -> `QtGui` |
| `AA_EnableHighDpiScaling`, `AA_UseHighDpiPixmaps` | present but inert | delete; Qt6 always scales |

Unscoped enum access (`Qt.AlignCenter`, `Qt.LeftButton`) still *resolves* in
PySide6 6.11, so it is not a crash -- but the brief asks for scoped enums and
they are what the new code speaks.

`exec_()` survives as an alias.  It goes anyway.

## Persistence: smaller risk than it looks

The classic Qt5->Qt6 persistence trap is `QSettings.value()` returning
strings where it used to return typed values.  It does not apply much here,
because `QSettings` holds exactly one key in this application:

    src/audian/databrowser.py:1448   QSettings("audian", "audian")
    src/audian/databrowser.py:7172   .setValue("spectrogram/colormap", ...)

An int, round-tripping as an int under PySide6 (measured: `bool`, `int`,
`float`, `list` and `str` all come back with their own types).

The real configuration is a hand-rolled, versioned JSON file at
`~/.config/audian/settings.json` -- theme, label categories, annotation
layers, parameter tab, panel split, spectrogram band.  It is binding-neutral,
it is the user's live data, and its format does not change in this migration.

## Multiprocessing was already safe

`compresseddata.py:565` and `audian.py:5037` both set the start method to
`forkserver` on posix and `spawn` elsewhere.  Neither forks a live Qt process,
which is the failure this would otherwise be prone to.  No change needed.

## The gates this migration has to pass

Recorded on PyQt5 at `10c5004`, before anything moved:

- `pytest tests/` -- **791 passed, 1 failed**.  The failure is
  `test_theme_module_is_lint_clean`, which shells out to `ruff`; ruff is not
  installed in the project environment and a current one flags 17 pre-existing
  style findings in `theme.py`.  Unrelated to Qt, and it fails identically on
  master.
- `scripts/smoke_test.py --interact --census` -- **56/56 interactions clean**,
  31 top-level widgets, 1 visible, **0 parentless**, construct ~160 ms.
- `scripts/baseline_matrix.sh` -- seven screenshots covering both themes, the
  spectrogram panel, the activity overview, the explicit audio pair, a zoomed
  window and the empty start.

`scripts/compare_shots.py` compares a later matrix against those, reporting
antialiasing noise separately from structural change so a different
rasteriser is not mistaken for a lane that moved.
