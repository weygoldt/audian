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

## APIs that are gone -- and which of them this tree actually uses

The general Qt6 removals are not the same list as this application's
problems, and conflating the two is how a migration invents work.  Swept
mechanically across all 34 modules, then each hit resolved against both
bindings' live namespaces:

| Qt5 API | Qt6 status | Sites here |
| --- | --- | --- |
| `QAction`, `QActionGroup` | moved to `QtGui` | **2** -- `audian.py:21`, `databrowser.py:27` |
| `QVariant` | absent from `PySide6.QtCore` | **7**, all in `labeloverlay.py`; return `None` |
| `QtCriticalMsg` and friends | not top-level | **3** in `scripts/smoke_test.py`; use `QtMsgType.*` |
| `pyqtSignal` | absent | **2** unguarded, 5 already behind a `try` |
| `Qt.NoItemFlag` | never existed | **2**; the name is `NoItemFlags`, latent bug on Qt5 too |
| `QFontMetrics.width()` | removed | **0** -- all 8 sites already use `horizontalAdvance()` |
| `QPainter.HighQualityAntialiasing` | removed | **0** |
| `QDesktopWidget`, `QApplication.desktop()` | removed | **0** -- already on `primaryScreen()` |
| `QRegExp`, `setMargin()`, `sip.*` | removed | **0** |
| `AA_EnableHighDpiScaling` etc. | inert | **0** -- never set |

Twenty-one lines, in eight files, take all 34 modules from `ImportError` to
importing clean under PySide6.  The application is in much better shape for
this than its age suggests: someone has already migrated away from
`QFontMetrics.width`, `QApplication.desktop` and `QRegExp`.

`QWheelEvent.delta()` *is* removed in Qt6, but the one `ev.delta()` here
(`selectviewbox.py:104`) is a `QGraphicsSceneWheelEvent`, which keeps it --
upstream pyqtgraph uses it the same way.  `QMouseEvent.pos()` survives as a
deprecation at six sites; `.position()` returns `QPointF`, not `QPoint`, so
those are not blind replacements.

Unscoped enum access (`Qt.AlignCenter`, `Qt.LeftButton`) still resolves in
PySide6 6.11 under its forgiving-enum mode, so the 545 references are a
cleanup and not a blocker.  Nineteen of them are inside comments and
docstrings -- two are warnings *not* to set a flag -- so the scoping pass has
to read, not `sed`.

`exec_()` survives as an alias.  The one site goes anyway.

### The one that actually crashes

`theme.strip_pg_menus()` is the real Qt6 problem in this tree, and it is not
mechanical.  It releases pyqtgraph's `PlotItem.ctrl` widgets from their
`QWidgetAction`s, keeps a Python reference, and deletes the menus.  Under sip
that keeps them alive.  Under shiboken it does not:

    releaseWidget only    sip: alive     shiboken: alive
    + menu.clear()        sip: alive     shiboken: DEAD   <- theme.py:1832

`QWidgetAction.releaseWidget()` does not hand ownership to Python under
shiboken, so `clear()` destroys each action and takes its widget's C++ object
with it.  Everything in `_audian_ctrl_widgets` becomes a dead wrapper, and the
next `showGrid()` raises.  That is the app path -- `rangeplot.py:39` strips
and `rangeplot.py:117` shows the grid -- not merely a test.

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
