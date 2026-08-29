# Recon: theme-session

- **cluster**: theme-session
- **purpose**: Two unrelated modules that happen to share a review slot. `src/audian/theme.py` (3304 lines) is the application's entire design system and its only Qt-chrome entry point: token tables for a dark and a daylight theme, spacing/height metrics, font stacks, QPen/QBrush/QColor constructors, named role helpers, pyqtgraph appliers (`style_axis`/`style_plotitem`/`style_figure`), a ~500-line QSS template, a fully-populated QPalette, spectrogram and categorical colormaps, plus ~600 lines of colour science (WCAG contrast, Brettel-1997 CVD simulation, CIEDE2000) used as a design gate and a `python -m audian.theme` CLI. `src/audian/session.py` (1695 lines) is NOT a widget and not a UI session: it is the pure-data reader for a fakefish stimulator bundle (five pinned-schema CSVs beside a `*_metadata.toml`), turning them into `Layer` objects, load-time cross-checks and per-region fit-residual statistics. It contains zero Qt (imports at session.py:56-104) and re-exports the whole `alignment` + `layers` public surface. The Qt half of the session story lives in `eventoverlay.AnnotationLayer` (eventoverlay.py:342), a QObject holding the bundle plus per-layer visibility switches.
- **public_surface**:
  - **name**: theme (module-level __all__)
  - **file**: src/audian/theme.py
  - **kind**: constant
  - **base**: 
  - **summary**: theme.py:96-248 exports ~130 names; every consumer imports the MODULE (`from . import theme`) and reaches through it. Call-site counts: databrowser.py 164, audian.py 164, fulltraceplot.py 49, labeloverlay.py 42, eventoverlay.py 36, controlpanel.py 27, tests/test_theme.py 201.

  - **name**: apply
  - **file**: src/audian/theme.py
  - **kind**: function
  - **base**: 
  - **summary**: theme.py:2477-2503. The single Qt-chrome entry point: Fusion style, palette, app font, pyqtgraph config, app stylesheet, in that order. Called from audian.py:5020 (startup) and audian.py:1653 (live switch). Optionally calls set_theme first (theme.py:2495).

  - **name**: set_theme / current_theme / token / TOKENS
  - **file**: src/audian/theme.py
  - **kind**: function
  - **base**: 
  - **summary**: theme.py:420-457. Global active-theme state. set_theme (:425) mutates the module-level TOKENS dict IN PLACE and evicts only `palette:`/`stylesheet:` cache keys (:435-437). token(name) raises KeyError on a typo by design.

  - **name**: qcolor / pen / brush / no_pen
  - **file**: src/audian/theme.py
  - **kind**: function
  - **base**: 
  - **summary**: theme.py:773-817. Low-level constructors over pg.mkColor/mkPen/mkBrush; accept a dotted token name, a dark-theme constant, or any raw colour. Always return fresh objects (contract stated at theme.py:36-42).

  - **name**: trace_pen / waveform_pen / waveform_color / waveform_role / dim_color / is_dense / filter_is_active
  - **file**: src/audian/theme.py
  - **kind**: function
  - **base**: 
  - **summary**: theme.py:855-1273. The waveform emphasis rules: exactly one saturated trace (the selected channel, `primary`), all others mixed toward bg.plot with a hard contrast clamp (dim_color :1123-1153). waveform_role (:1184-1218) repaints a pass-through 'filtered' trace as raw.

  - **name**: annotation_pen / annotation_brush / annotation_color / annotation_letter / ANNOTATION_ROLES / CATEGORY_ROLES
  - **file**: src/audian/theme.py
  - **kind**: function
  - **base**: 
  - **summary**: theme.py:1310-1432. Annotation encoding: hue carries KIND only; predicted = [2,2] dash, unvalidated = DashLine pen + BDiagPattern hatch, never alpha; treatment carried by a knocked-out V/B/S letter. Consumed by eventoverlay.py.

  - **name**: style_axis / style_plotitem / style_figure / style_channel_figure / style_colorbar / style_spinbox / colorbar_pens / colorbar_ticks / overlay_textitem
  - **file**: src/audian/theme.py
  - **kind**: function
  - **base**: 
  - **summary**: theme.py:1542-1941. pyqtgraph appliers, all typed `Any`, all idempotent, all duck-typed via getattr/hasattr. Called from timeplot/spectrogramplot/rangeplot/fulltraceplot/controlpanel polish() bodies and from DataBrowser.apply_theme (databrowser.py:2587-2649).

  - **name**: tint / frame / band / restyle_tree / FG_PROPERTY / FRAME_PROPERTY / BAND_PROPERTY
  - **file**: src/audian/theme.py
  - **kind**: function
  - **base**: 
  - **summary**: theme.py:1440-1540. Per-widget inline stylesheets with baked hex plus a dynamic-property registry (`audianFgToken`, `audianFramed`, `audianBandEdge`); restyle_tree (:1506) walks findChildren(QWidget) and re-bakes on theme switch. Used by audian.py:2248 and databrowser.py:2619.

  - **name**: strip_pg_menus / collect_orphan_widgets
  - **file**: src/audian/theme.py
  - **kind**: function
  - **base**: 
  - **summary**: theme.py:1756-1911 (with private _menu_holder :1759 and _adopt_ctrl_widgets :1845). pyqtgraph PlotItem menu / ctrl-widget teardown (577 hidden top-level QMenus measured at startup on a 16-channel file). Not styling. Called from fulltraceplot.py:476, spectrogramplot.py:201, controlpanel.py:148, rangeplot.py:39, databrowser.py:1826.

  - **name**: palette / stylesheet / apply_pg_config
  - **file**: src/audian/theme.py
  - **kind**: function
  - **base**: 
  - **summary**: theme.py:1948-2475. The QPalette (20 roles plus the whole Disabled group, :1962-1997), the QSS built from a 500-line string.Template (:2185-2409) via a hand-maintained 40-argument substitute (:2422-2456), and pg.setConfigOptions(antialias=False) (:2470).

  - **name**: spectrogram_maps / spectrogram_map_labels / spectrogram_colormap / marker_colors / marker_color / SPECTROGRAM_MAPS / REVERSED_MAPS / MARKER_COLORS
  - **file**: src/audian/theme.py
  - **kind**: function
  - **base**: 
  - **summary**: theme.py:2505-2691. Theme-aware data palettes; the colormap list is a DIFFERENT list per theme (not a reversal), reversal is held separately in REVERSED_MAPS (:2569), and results are cached per theme. Consumed by databrowser.py (cmapw.populate, set_color_map) and labeloverlay.py.

  - **name**: contrast_ratio / relative_luminance / check_contrast / simulate_cvd / srgb_to_lab / delta_e2000 / check_separation / painted_trace_colors / okabe_ito_worst_pair
  - **file**: src/audian/theme.py
  - **kind**: function
  - **base**: 
  - **summary**: theme.py:2693-3304. The design gate: WCAG luminance/contrast, Brettel-1997 dichromat simulation, CIEDE2000, separation checks and the _report() CLI (:3246-3302). Runtime-imported (pulls numpy) but used only by tests/test_theme.py and `python -m audian.theme`.

  - **name**: SessionBundle
  - **file**: src/audian/session.py
  - **kind**: class
  - **base**: 
  - **summary**: session.py:430-739. The loaded bundle: .meta, .layers, .warnings, .dropped, .unlayered, .missing, .residuals, .trust, .recording_check, .t_min/.t_max, .summary(), plus viewer queries nearest (:645), step (:667), spans_at (:686), pulses_in (:702). Constructed only by the classmethod SessionBundle.load (:480-577). Consumed by eventoverlay.py:387 and databrowser.py:4764.

  - **name**: ResidualStats / ResidualRegion
  - **file**: src/audian/session.py
  - **kind**: class
  - **base**: 
  - **summary**: session.py:1378-1512 (ResidualRegion is @dataclass(frozen=True) at :1378). Per-region fit residual (median/IQR/matched/total); regions are the recording's files when the bundle declares joins, else RESIDUAL_BINS=8 equal bins. Exposes .at(t), .worst, .warnings. Surfaced by databrowser.py:4737-4759.

  - **name**: PULSE_TYPES / TRIAL_TYPES / EVENT_TYPES / DETECTION_TYPES / CONTROL_TYPES / CSV_TYPES
  - **file**: src/audian/session.py
  - **kind**: constant
  - **base**: 
  - **summary**: session.py:199-292. The pinned polars schemas — the load-bearing correctness artefact of this module, because head-only inference types six columns wrongly on the real bundle (leading nulls). Referenced by tests/test_session.py:128.

  - **name**: TIME_COLUMN / CONTROL_CHANNELS / RUN_STARTED / RUN_STOPPED / DEFAULT_MATCH_TOLERANCE_S / RESIDUAL_BINS / RESIDUAL_WARN_FACTOR
  - **file**: src/audian/session.py
  - **kind**: constant
  - **base**: 
  - **summary**: session.py:192, 294-302, 312, 1369, 1375. Reader constants. TIME_COLUMN = 'recording_time_s' and the stimulator's own `time_s` is NEVER a fallback (a row placed by it would sit ~29 s out and look plausible).

  - **name**: re-exports of audian.alignment and audian.layers
  - **file**: src/audian/session.py
  - **kind**: constant
  - **base**: 
  - **summary**: session.py:108-181 (__all__). Deliberately re-exports TRUST_*, KIND_*, CSV_KINDS, Alignment, Integrity, RecordingCheck, SessionMeta, BundleRef, find_bundle(s), verify_sha256, Layer, PointSeries, PointLayer, SpanLayer, StepTrack, LAYER_*, TRACK_* so `from audian.session import X` still answers for the whole reader after the three-way split.

- **qt5_api_usage**:
  - **file**: src/audian/theme.py
  - **line**: 79
  - **api**: from PyQt5.QtCore import Qt
  - **qt6_replacement**: from PySide6.QtCore import Qt
  - **severity**: breaking

  - **file**: src/audian/theme.py
  - **line**: 80
  - **api**: from PyQt5.QtGui import QBrush, QColor, QFont, QFontDatabase, QFontMetrics, QPalette, QPen (:80-88)
  - **qt6_replacement**: from PySide6.QtGui import ...
  - **severity**: breaking

  - **file**: src/audian/theme.py
  - **line**: 89
  - **api**: from PyQt5.QtWidgets import QApplication, QStyleFactory, QWidget, QWidgetAction (:89-94)
  - **qt6_replacement**: from PySide6.QtWidgets import ...
  - **severity**: breaking

  - **file**: src/audian/theme.py
  - **line**: 1513
  - **api**: function-local `from PyQt5.QtWidgets import QWidget` inside restyle_tree(), duplicating the module import at :92
  - **qt6_replacement**: delete the local import and use the module-level PySide6 QWidget
  - **severity**: breaking

  - **file**: src/audian/theme.py
  - **line**: 78
  - **api**: `import pyqtgraph as pg` at :78 precedes the PyQt5 import at :79; pyqtgraph picks its Qt binding implicitly from PYQTGRAPH_QT_LIB / already-imported modules / a fixed try order
  - **qt6_replacement**: set PYQTGRAPH_QT_LIB='PySide6' (or import PySide6 first from a single app bootstrap) before any `import pyqtgraph`. During a transition with both bindings installed pyqtgraph silently prefers PyQt5 and you end up with two bindings in one process
  - **severity**: breaking

  - **file**: src/audian/theme.py
  - **line**: 677
  - **api**: QFontDatabase().families() — instantiating QFontDatabase
  - **qt6_replacement**: QFontDatabase.families() (Qt6 removed the constructor; the class is fully static). CRITICAL: the `except Exception` at :678-679 swallows the resulting TypeError and caches frozenset(), so _first_installed (:684-697) then returns the LAST entry of every stack — 'sans-serif' / 'monospace' — and the whole app silently loses Inter and JetBrains Mono with no error and no log line
  - **severity**: breaking

  - **file**: src/audian/theme.py
  - **line**: 705
  - **api**: QFont(_first_installed(stack), pt) — single-family string constructor
  - **qt6_replacement**: QFont(list(stack), pt) — Qt6 has a families-list constructor and deprecates the single-family setFamily path; this also removes the need for the setFamilies dance at :706-708
  - **severity**: behavior-change

  - **file**: src/audian/theme.py
  - **line**: 706
  - **api**: hasattr(font, 'setFamilies') feature probe
  - **qt6_replacement**: delete the probe — setFamilies exists unconditionally in Qt6
  - **severity**: cosmetic

  - **file**: src/audian/theme.py
  - **line**: 711
  - **api**: QFont.PreferAntialias (unscoped enum)
  - **qt6_replacement**: QFont.StyleStrategy.PreferAntialias
  - **severity**: behavior-change

  - **file**: src/audian/theme.py
  - **line**: 713
  - **api**: QFont.Monospace (unscoped enum)
  - **qt6_replacement**: QFont.StyleHint.Monospace
  - **severity**: behavior-change

  - **file**: src/audian/theme.py
  - **line**: 795
  - **api**: annotation `style: Qt.PenStyle | None` on pen()
  - **qt6_replacement**: class path unchanged, but every VALUE passed into it (:922, :1392) is unscoped and must be fixed
  - **severity**: cosmetic

  - **file**: src/audian/theme.py
  - **line**: 817
  - **api**: QPen(Qt.NoPen) in no_pen()
  - **qt6_replacement**: QPen(Qt.PenStyle.NoPen)
  - **severity**: behavior-change

  - **file**: src/audian/theme.py
  - **line**: 922
  - **api**: pen(..., style=Qt.DashLine) in crosshair_pen()
  - **qt6_replacement**: Qt.PenStyle.DashLine
  - **severity**: behavior-change

  - **file**: src/audian/theme.py
  - **line**: 1392
  - **api**: p.setStyle(Qt.DashLine) in annotation_pen()
  - **qt6_replacement**: Qt.PenStyle.DashLine
  - **severity**: behavior-change

  - **file**: src/audian/theme.py
  - **line**: 1408
  - **api**: b.setStyle(Qt.BDiagPattern) in annotation_brush()
  - **qt6_replacement**: Qt.BrushStyle.BDiagPattern
  - **severity**: behavior-change

  - **file**: src/audian/theme.py
  - **line**: 1962
  - **api**: QPalette.Window / WindowText / Base / AlternateBase / Text / Button / ButtonText / BrightText / ToolTipBase / ToolTipText / PlaceholderText / Highlight / HighlightedText / Link / LinkVisited / Light / Midlight / Mid / Dark / Shadow (:1962-1981, repeated for the disabled table :1987-1994)
  - **qt6_replacement**: QPalette.ColorRole.* for all 20 roles. PySide6 forgiving-enum mode still accepts the short form, which is exactly why this will be missed — scope them all and lint for it
  - **severity**: behavior-change

  - **file**: src/audian/theme.py
  - **line**: 1997
  - **api**: p.setColor(QPalette.Disabled, role, qcolor(name))
  - **qt6_replacement**: QPalette.ColorGroup.Disabled
  - **severity**: behavior-change

  - **file**: src/audian/theme.py
  - **line**: 1960
  - **api**: palette() sets 20 roles but never QPalette.ColorRole.Accent (added in Qt 6.6), while its docstring at :1950-1953 claims every role is set explicitly
  - **qt6_replacement**: set ColorRole.Accent (to `primary`) when Qt >= 6.6, or Fusion derives it from Highlight and the module's own stated contract stops holding
  - **severity**: behavior-change

  - **file**: src/audian/theme.py
  - **line**: 2496
  - **api**: QStyleFactory.create('Fusion') + app.setPalette + app.setStyleSheet as the entire dark-mode mechanism
  - **qt6_replacement**: keep Fusion, but add QGuiApplication.styleHints().setColorScheme(Qt.ColorScheme.Dark/Light) (Qt 6.8; colorScheme() readable from 6.5). Without it, native title bars, native file dialogs and portal menus stay light around the dark application
  - **severity**: behavior-change

  - **file**: src/audian/theme.py
  - **line**: 2502
  - **api**: app.setStyleSheet(stylesheet()) — a ~500-line QSS whose px metrics (min-height / max-height / padding at :2185-2409) were tuned against Qt5 Fusion
  - **qt6_replacement**: API unchanged, but Qt6 Fusion has different intrinsic control metrics: TOOLBAR_BUTTON_BOX=30 (:1046, derived as TOOLBAR_BUTTON_HEIGHT + 2*S4 + 2*HAIRLINE), CONTROL_HEIGHT=26 (:1652) and CHIP_HEIGHT=22 (:1659) must be re-measured under Qt6 or the toolbar buttons clip again
  - **severity**: behavior-change

  - **file**: src/audian/theme.py
  - **line**: 1900
  - **api**: QApplication.topLevelWidgets() then widget.setParent(holder) at :1907 (collect_orphan_widgets)
  - **qt6_replacement**: API unchanged, but PySide6 ownership semantics differ from PyQt5's: setParent() hands the object to C++ and the Python wrapper is invalidated when the parent dies. Verify the adopted widgets and _MENU_HOLDER are torn down before QApplication exits
  - **severity**: behavior-change

  - **file**: src/audian/theme.py
  - **line**: 1803
  - **api**: menu.setParent(None); menu.deleteLater() on pyqtgraph's QMenus (:1803-1804 and :1834-1835), guarded by `except RuntimeError` at :1836
  - **qt6_replacement**: under PySide6, setParent(None) returns ownership to Python and deleteLater then schedules a C++ delete on an object Python still owns; use deleteLater() alone (or shiboken6.delete) and re-test the guard — PyQt5's 'wrapped C/C++ object has been deleted' message and timing are not PySide6's
  - **severity**: breaking

  - **file**: src/audian/theme.py
  - **line**: 1820
  - **api**: isinstance(action, QWidgetAction); action.releaseWidget(widget) (:1820-1823), plus direct reads of pyqtgraph privates plot_item.ctrlMenu (:1808), plot_item.ctrl and Ui_Form (:1856-1861)
  - **qt6_replacement**: the Qt API survives; the coupling to pyqtgraph internals does not survive a pyqtgraph version or binding change and must move behind a version-pinned compat shim
  - **severity**: behavior-change

  - **file**: src/audian/theme.py
  - **line**: 1756
  - **api**: module-global parentless QWidget `_MENU_HOLDER` created lazily (:1756-1772) and never destroyed
  - **qt6_replacement**: own it from the QApplication (create in apply(), destroy on app.aboutToQuit). A module-global Qt widget destroyed by the Python interpreter after QApplication is gone is a well-known PySide6 shutdown segfault
  - **severity**: breaking

  - **file**: src/audian/theme.py
  - **line**: 1720
  - **api**: mono_metrics(SIZE_SMALL_PT).horizontalAdvance(sample) in style_colorbar
  - **qt6_replacement**: already Qt6-correct — QFontMetrics.width() is removed in Qt6 and appears nowhere in this file
  - **severity**: cosmetic

  - **file**: src/audian/theme.py
  - **line**: 576
  - **api**: CONTROL_BAND_H=28 / CONTROL_BAND_PAD=3 (:604) / CONTROL_NOTE_H=18 (:612), documented at :572-575 as 'device-pixel counts, because the panel's y range is pinned to [0, height_in_device_pixels]'; consumed by controlpanel.py:252-253 setYRange(0, n*CONTROL_BAND_H + CONTROL_NOTE_H, padding=0)
  - **qt6_replacement**: Qt6 enables high-DPI scaling unconditionally (AA_EnableHighDpiScaling / AA_UseHighDpiPixmaps are gone; this tree never set them — grep finds only WA_* attributes) and defaults to PassThrough fractional rounding, so device px and logical px diverge by 1.25/1.5/1.75. Set QGuiApplication.setHighDpiScaleFactorRoundingPolicy explicitly, restate :475-643 as logical px, and derive the panel's device-pixel range from devicePixelRatioF() as eventoverlay.py:1096, traceitem.py:178, specitem.py:120 and controlpanel.py:405 already do
  - **severity**: behavior-change

  - **file**: src/audian/theme.py
  - **line**: 1667
  - **api**: style_spinbox() pokes pg.SpinBox's private opts['compactHeight'] and pins min/max height to defeat SpinBox.paintEvent's setMaximumHeight(QFontMetrics(font).height()) (:1669-1691)
  - **qt6_replacement**: pyqtgraph-internal rather than Qt5-specific, but must be re-verified against the PySide6 build of pyqtgraph 0.14: when it stops working the symptom is silently cropped digits ('0 Hz' rendering as 'A Hz'), not an exception
  - **severity**: behavior-change

  - **file**: src/audian/theme.py
  - **line**: 1562
  - **api**: pyqtgraph AxisItem API: setStyle(maxTextLevel=0), setPen, setTickPen, setTextPen, setTickFont, setLabel(color=) (:1562-1576)
  - **qt6_replacement**: unchanged in pyqtgraph 0.14 under PySide6; pin the pyqtgraph version — maxTextLevel and setTextPen are recent additions and are load-bearing for the 'major ticks only' contract
  - **severity**: cosmetic

  - **file**: src/audian/theme.py
  - **line**: 1926
  - **api**: pg.TextItem(color=QColor, border=QPen, fill=QBrush), with a deliberate NON-use of QGraphicsItem.ItemIgnoresTransformations documented at :1936-1940
  - **qt6_replacement**: if ever added it is QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations in Qt6; the comment explaining why it must NOT be set has to survive the port or the overlay paints off screen
  - **severity**: cosmetic

  - **file**: src/audian/theme.py
  - **line**: 2470
  - **api**: pg.setConfigOptions(background=token('bg.plot'), foreground=token('fg.muted'), antialias=False)
  - **qt6_replacement**: unchanged; antialias=False is a measured 170x paint-cost decision (:2466-2468) and must not be 'modernised' away
  - **severity**: cosmetic

  - **file**: src/audian/session.py
  - **line**: 56
  - **api**: NONE — session.py imports only logging, collections.abc, dataclasses, pathlib, typing, numpy, polars and the local alignment/windowing/layers (:56-104). No PyQt5, no pyqtgraph, no QObject, no signals, no widgets.
  - **qt6_replacement**: no migration work required; the module is binding-agnostic by design (docstring :9-14) and must stay that way
  - **severity**: cosmetic

- **architecture_problems**:
  - **title**: theme.py is a 3304-line god module holding six unrelated responsibilities
  - **file**: src/audian/theme.py
  - **line**: 1
  - **evidence**: Section banners: tokens :250, metrics :475, fonts :644, pen/brush :761, roles :820, waveform emphasis :1014, annotations :1275, pyqtgraph appliers :1434, Qt chrome :1943, data palettes :2505, contrast :2693, CVD/CIEDE2000 :2759, CLI report :3246. One file imports numpy, pyqtgraph, QtCore, QtGui and QtWidgets, and holds a 500-line QSS as a string.Template (:2185-2409).
  - **why_it_matters**: Every widget and plot item in the app depends on this one module (164 `theme.` references in databrowser.py, 164 in audian.py, 49 in fulltraceplot.py), so it is the largest source of import-time coupling and the file most likely to conflict during the port. ~600 lines of colour science plus a CLI report are pulled into every audian process purely to satisfy a test.
  - **proposed_qt6_design**: Convert to a package `audian/theme/`: tokens.py (pure data, no Qt, no numpy), metrics.py, fonts.py, paint.py (QPen/QBrush/QColor), roles.py, palette.py, qss/ (fragments), pgstyle.py (the pyqtgraph appliers), and audit.py (:2693-3304 — contrast, simulate_cvd, delta_e2000, check_separation, painted_trace_colors, okabe_ito_worst_pair, _report) imported only by the CLI and tests/test_theme.py. Keep theme/__init__.py re-exporting the current names so the 400+ call sites need not move in the same commit.
  - **effort**: medium

  - **title**: Theme state is a mutated module-global dict; reading the other theme means temporarily switching the running application
  - **file**: src/audian/theme.py
  - **line**: 425
  - **evidence**: set_theme (:425-445) does TOKENS.clear(); TOKENS.update(table) on a module-level dict and flips _ACTIVE (:415). painted_trace_colors (:3073-3095) calls set_theme(other), computes, then set_theme(previous) in a finally — its own docstring says the dimming path 'deliberately reads global state rather than taking a theme argument at 48 plots per repaint'. dim_color (:1141) and min_graphic_contrast (:1082) read current_theme() on the paint path.
  - **why_it_matters**: Non-reentrant and not thread-safe: any repaint, timer or queued signal firing inside the save/restore window paints in the wrong theme, and a future background renderer cannot ask 'what colour is this in dark?' without mutating what the GUI thread sees. It also makes the theme untestable in parallel.
  - **proposed_qt6_design**: An immutable frozen `Theme` dataclass (tokens + metrics) and a `ThemeService(QObject)` singleton owning `current: Theme` with `themeChanged = Signal(Theme)`; pure functions take a Theme, defaulting to the service's current. painted_trace_colors(theme) then needs no save/restore and a worker thread can resolve colours off a Theme value.
  - **effort**: medium

  - **title**: No theme-changed signal: live re-theming is a hand-written imperative cascade across three files
  - **file**: src/audian/audian.py
  - **line**: 1640
  - **evidence**: Audian.set_app_theme (audian.py:1640-1682) calls theme.apply, refresh_glyph_icons, restyle_chrome, then per-browser apply_theme(), then REBUILDS StartupPage from scratch (:1657-1670) because its tokens are baked into per-widget stylesheets, then repolish(). Audian.restyle_chrome (audian.py:2242-2262) re-bakes toolbar separators and the mode chip by hand. DataBrowser.apply_theme (databrowser.py:2587-2649) is a 60-line walk over figs, taxis, axes, borders, splitters, datafig, rail rows, colormap combo, annotation overlays, join markers, control panel, chips, badge, label overlays and param tabs. Nothing in theme.py is a QObject and the module defines no signal.
  - **why_it_matters**: Any widget or plot item added later and not manually appended to one of these three walks silently keeps the previous theme — the exact failure the tint() docstring at :1449-1454 records having already happened ('which is how the parameter captions and the group frames were missed the first time'). The cascade is also O(whole widget tree) on every Ctrl+Shift+L and rebuilds a whole page widget.
  - **proposed_qt6_design**: ThemeService.themeChanged Signal; every themable plot item/widget connects once at construction and implements apply_theme(theme); widget chrome instead responds to QEvent.Type.ApplicationPaletteChange / StyleChange in changeEvent() so Qt performs the walk. Delete restyle_chrome, the StartupPage rebuild and most of DataBrowser.apply_theme.
  - **effort**: large

  - **title**: Per-widget stylesheets with baked hex plus a dynamic-property registry re-scanned by a tree walk
  - **file**: src/audian/theme.py
  - **line**: 1446
  - **evidence**: tint (:1446-1459) sets widget.setStyleSheet(f"color: {token(...)};") and records the token in the audianFgToken property; frame (:1461-1476) and band (:1478-1504) do the same for audianFramed / audianBandEdge; restyle_tree (:1506-1540) walks [root] + root.findChildren(QWidget) and re-bakes each. Outside theme.py there are ~33 further raw setStyleSheet call sites the 'no literals outside theme.py' rule does not cover: audian.py:518, 781, 792, 813, 831, 956, 992, 999, 1005, 1022, 1056, 1060, 1070, 1090, 1109, 1356, 1373, 1387, 1391, 1441, 1832, 1900, 1928, 1946, 2099, 2117, 2196, 2253, 2258, 2267 and databrowser.py:881, 889, 5774.
  - **why_it_matters**: Theme state is duplicated onto every widget as a string property and re-derived by a full tree scan; a per-widget stylesheet also defeats QSS rule caching and forces an individual unpolish/polish. The colour of a label is not knowable from the stylesheet — you must read a dynamic property to find out.
  - **proposed_qt6_design**: Express all of it in the single application QSS using dynamic-property attribute selectors — `*[audianFgToken="fg.muted"] { color: ...; }`, `*[audianFramed="true"] { ... }`, `*[audianBandEdge="11|bg.surface"] { ... }` — so tint/frame/band only setProperty and never setStyleSheet. A theme switch becomes one app.setStyleSheet() plus unpolish/polish on the top-level windows, and restyle_tree disappears.
  - **effort**: medium

  - **title**: pyqtgraph internals surgery and a leaked module-global QWidget live inside the design system
  - **file**: src/audian/theme.py
  - **line**: 1756
  - **evidence**: _menu_holder (:1756-1772) lazily creates a module-global parentless QWidget never destroyed. strip_pg_menus (:1775-1843) disables PlotItem menus, nulls vb.menu, walks plot_item.ctrlMenu's submenu tree, calls QWidgetAction.releaseWidget, stashes widgets on plot_item._audian_ctrl_widgets, deletes every visited menu, then re-parents. _adopt_ctrl_widgets (:1845-1879) reaches into vars(plot_item.ctrl) and re-parents each widget's window(). collect_orphan_widgets (:1881-1911) scans every top-level widget in the application to adopt pyqtgraph's unreachable 640x480 Ui_Form hosts (32 on a 16-channel file).
  - **why_it_matters**: None of this is styling; it is memory and ownership management of a third-party library's private widget tree, and it is the strongest evidence in this cluster that pyqtgraph's PlotItem is being fought rather than used (577 hidden top-level QMenus at startup, ~450 stray control widgets). The module-global holder is a textbook PySide6 shutdown crash: a Qt widget owned by a Python module global, destroyed after QApplication is gone.
  - **proposed_qt6_design**: Move to audian/pgcompat.py against a pinned pyqtgraph version; make the holder a child of the QApplication and destroy it on aboutToQuit. Better still, stop creating the menus at all with a PlotItem subclass that skips ctrlMenu/Ui_Form construction, or replace the plotting layer so collect_orphan_widgets has nothing to collect.
  - **effort**: medium

  - **title**: Every applier and every styled object is typed `Any` and duck-probed, with two competing re-theme conventions
  - **file**: src/audian/theme.py
  - **line**: 1542
  - **evidence**: style_axis(axis_item: Any) :1542; style_plotitem(plot_item: Any) :1578 with getattr(plot_item, 'axes', {}) :1593 and a bare except KeyError :1598; style_figure(glw: Any) :1605 with getattr(ci, 'layout', None) :1618; style_channel_figure :1626; style_spinbox(spin: Any) :1667 probing spin.opts and hasattr(spin, 'lineEdit'); style_colorbar(cbar: Any) :1693; tint/frame/band(widget: Any) :1446/:1461/:1478. Consumers probe back: databrowser.py:2604-2609 does `if hasattr(ax, 'apply_theme') ... elif hasattr(ax, 'polish')`.
  - **why_it_matters**: No type checker can validate a single call site, and every guard fails SILENTLY: hand style_plotitem the wrong object and it returns having styled nothing, which surfaces as an unstyled axis three screens later. The hasattr dance also means there are two names for the same operation across the plot classes.
  - **proposed_qt6_design**: Declare typing.Protocols (Themable with apply_theme(theme), StyledAxis, Figure) and type the appliers with them; collapse polish() and apply_theme() into one name across every plot item. In the new plotting layer make theming a base-class responsibility wired to ThemeService.themeChanged rather than a function someone must remember to call.
  - **effort**: medium

  - **title**: The value-keyed reverse token map and the dark-valued module constants are a documented colour-corruption footgun
  - **file**: src/audian/theme.py
  - **line**: 410
  - **evidence**: _BY_VALUE = {v.upper(): k for k, v in DARK_TOKENS.items()} (:410) is inverted by VALUE, and _resolve (:459-473) silently remaps any string equal to a dark token value through the active theme. The comment at :1295-1300 records the consequence: an `ann.novel` token whose value equalled FG 'would silently re-point every theme.FG call through the annotation role and change the colour of unrelated text', which is why detection.novel is aliased to `fg` (:1310-1312) rather than given a token. Separately, the module constants BG_BASE..TRACE_ZERO (:254-283) always hold DARK values (docstring :64-68), so theme.PRIMARY under daylight is simply the wrong colour — and there are 4 direct theme.PRIMARY call sites outside theme.py.
  - **why_it_matters**: Adding a token can silently repaint unrelated widgets, and the rescue mechanism means a genuine hardcoded hex at a call site gets silently 'fixed' instead of failing. Both are accidental global coupling of exactly the kind this migration exists to remove.
  - **proposed_qt6_design**: Delete _BY_VALUE and the dark-valued module constants; token(name) and the role helpers become the only accessors and a raw hex reaching a helper raises. Keep tests/test_theme.py's grep gates (:265-277) and turn AUDIAN_THEME_STRICT on, since with the rescue gone those gates are the only thing catching a literal.
  - **effort**: small

  - **title**: Qt objects cached forever in a module-global dict that outlives QApplication
  - **file**: src/audian/theme.py
  - **line**: 417
  - **evidence**: _CACHE: dict[str, Any] = {} (:417) holds the installed-family frozenset (:672-682), QFont objects (:699-717), QFontMetrics (:737-742), QPalette (:1948-2000), dimmed QColors (:1123-1153) and pg.ColorMap objects (:2603-2634). set_theme (:435-437) evicts ONLY keys starting with 'palette:' or 'stylesheet:'.
  - **why_it_matters**: QFontMetrics survive an application-font change, a screen change and a devicePixelRatio change, so every layout number derived from them (theme.py:1720 colourbar width, control-panel and timeplot heights) can be stale on a second monitor. QFont/QPalette wrappers survive a QApplication teardown/rebuild — which the test suite does (tests/conftest.py builds one QApplication for the whole session) — and under PySide6 a stale wrapper over a destroyed C++ object is a hard RuntimeError.
  - **proposed_qt6_design**: Move the cache onto the ThemeService instance keyed by (theme, dpr, app font); clear it on themeChanged, on QEvent.Type.FontChange / ApplicationFontChange, on screen change, and on QGuiApplication.aboutToQuit. Key fonts and metrics by devicePixelRatio.
  - **effort**: small

  - **title**: Pixel-literal metrics assume Qt5's opt-in high-DPI world
  - **file**: src/audian/theme.py
  - **line**: 572
  - **evidence**: The control-panel banner at :572-575 states 'Every value below is a device-pixel count, because the panel's y range is pinned to [0, height_in_device_pixels]' — CONTROL_BAND_H=28 (:576), CONTROL_BAND_PAD=3 (:604), CONTROL_NOTE_H=18 (:612); controlpanel.py:252-253 pins setYRange(0, len(names)*CONTROL_BAND_H + CONTROL_NOTE_H, padding=0). The whole metrics section (:475-643) is integer px: CHANNEL_MIN_HEIGHT=80, CHANNEL_DENSE_HEIGHT=34, SPECTROGRAM_MIN_HEIGHT=120, PLOT_FRAME_HEIGHT=2, PANEL_SPLIT_MIN_HEIGHT=34, PANEL_SPLIT_HANDLE_HEIGHT=7, AXIS_LEFT_WIDTH=56, TOOLBAR_BUTTON_BOX=30. No AA_EnableHighDpiScaling / AA_UseHighDpiPixmaps is set anywhere in the tree (grep finds only WA_* attributes).
  - **why_it_matters**: Qt6 turns high-DPI scaling on unconditionally and defaults to PassThrough rounding for fractional factors, so 'device pixel' and the logical pixel these constants are actually spent in diverge by 1.25/1.5/1.75. The lane-geometry arithmetic (the SPECTROGRAM_MIN_HEIGHT allowance vs the height the panel actually opens at, documented at :516-527) and the control panel's band-boundary-is-a-pixel-count contract both break as wrong layout, not as an error.
  - **proposed_qt6_design**: Set QGuiApplication.setHighDpiScaleFactorRoundingPolicy explicitly in the bootstrap; restate the metrics as logical px in a Metrics object, and have the control panel derive its device-pixel y range from widget.devicePixelRatioF() as eventoverlay.py:1096, traceitem.py:178, specitem.py:120 and controlpanel.py:405 already do. Add a test that runs the lane-geometry solver at DPR 1.0, 1.25, 1.5 and 2.0.
  - **effort**: medium

  - **title**: The QSS is a stringly-typed 500-line Template substituted from a hand-maintained 40-argument call
  - **file**: src/audian/theme.py
  - **line**: 2185
  - **evidence**: _QSS = Template(...) spans :2185-2409; stylesheet() (:2411-2462) substitutes 40 named arguments by hand (bg_base, bg_surface, ..., s4_focus, scrollbar=10 — a literal metric with no token, :2455). Several rules exist purely to fight Qt's layout engine: the toolbar comment at :2247-2265 ('on a theme switch the app sheet landed first and the widget sheet second, and the bar re-laid its items 6 px lower'), the button-height rule at :2288-2299, and TOOLBAR_BUTTON_BOX's docstring at :1046-1058.
  - **why_it_matters**: A token added to the template but forgotten in the substitute call is a KeyError at theme-apply time — i.e. at startup or mid-session on Ctrl+Shift+L. The layout-fighting rules encode Qt5 Fusion's metrics and must be re-derived under Qt6, with no structure to re-derive them in.
  - **proposed_qt6_design**: Build the substitution mapping by iterating the token table plus the metrics object ({k.replace('.', '_'): v for k, v in theme.tokens.items()}), so adding a token cannot desync. Split the template into per-family fragments (toolbar, menu, inputs, tables, scrollbars, focus). Move the layout-correcting rules (fixed toolbar-button height, spinbox height) into a QProxyStyle overriding sizeFromContents/pixelMetric, where they are style logic rather than CSS.
  - **effort**: medium

  - **title**: apply() welds 'switch the global theme' to 'push it into this QApplication'
  - **file**: src/audian/theme.py
  - **line**: 2477
  - **evidence**: apply(app, theme_name) (:2477-2503) calls set_theme(theme_name) as a side effect at :2495 before touching the app. There is no way to ask what the other theme's colours are without changing the running application — which is precisely why painted_trace_colors performs the set_theme/restore dance at :3082-3094.
  - **why_it_matters**: Two responsibilities in one entry point means every query about a non-active theme is a mutation, and the single-entry-point discipline the docstring claims (:2479-2481) is what forces the workaround elsewhere in the same file.
  - **proposed_qt6_design**: Split into `resolve(name) -> Theme` (pure) and `ThemeService.set(theme)` which emits themeChanged, with `apply(app, theme)` doing only the Qt pushes (style, palette, font, pg config, stylesheet). Callers that want the daylight numbers ask resolve('light').
  - **effort**: small

  - **title**: SessionBundle.load is a synchronous ~100-line god-constructor run on the GUI thread
  - **file**: src/audian/session.py
  - **line**: 480
  - **evidence**: SessionBundle.load (:480-577) reads the TOML, reads up to five CSVs via _read (:496-501), reconciles expected row counts (:503-513), runs five builders (:518-530), the partition check (:531), the unlayered backstop (:536-566), residual statistics (:568) and meta.check_recording(recording) (:570-572), which calls soundfile.info() (alignment.py:785-792). The chain is eventoverlay.AnnotationLayer.load (eventoverlay.py:376-397) <- databrowser.load_annotations (databrowser.py:4761-4766), both on the GUI thread. There is no threading anywhere in src/audian: grep for QThread/QRunnable/threading/concurrent finds one comment (buffereddata.py:243, 'where a future QThreadPool').
  - **why_it_matters**: Load-time work scales with the bundle (exp3: 5281 pulses, four files) and includes filesystem IO plus an audio-header open; the window is frozen for its duration with no progress and no cancel. The migration mandate explicitly names background computation and potentially very large files.
  - **proposed_qt6_design**: Keep the reader pure and Qt-free. Add a SessionLoader(QObject) driver submitting SessionBundle.load to a QThreadPool/QRunnable (or concurrent.futures with a queued-connection signal), emitting loadStarted / progress(int, str) / loadFinished(SessionBundle) / loadFailed(Exception). Split load into _read_frames, _build_layers, _cross_check, _residuals so progress has something to report and each phase is independently testable.
  - **effort**: medium

  - **title**: Diagnostics are pre-formatted English strings, so the UI cannot group, count, filter or link them
  - **file**: src/audian/session.py
  - **line**: 467
  - **evidence**: self.warnings: tuple[str, ...] (:467) is accumulated as f-strings at ~25 sites: :505-513, :521-526, :548-553, :862-865, :871-875, :903-905, :909-911, :946-949, :1005-1008, :1015-1019, :1074-1077, :1129-1132, :1245-1248, :1615-1618, :1622-1624, :1630-1632, :1638, :1642-1645, :1655-1658, plus ResidualStats.warnings (:1491-1512). The UI re-derives structure by special case: databrowser.py:4776-4796 handles coverage / recording_mismatch / unvalidated separately, then :4797-4798 loops `for warning in self.bundle_problems(bundle): self.notify('warning', ...)` — one toast per string.
  - **why_it_matters**: Nothing can be grouped ('12 rows in no layer' across three kinds), counted into a badge, deduplicated, sorted by severity, or linked to the layer or time range it concerns; a reader with a messy bundle gets a stack of prose toasts. The only machine-readable severity in the whole reader is meta.trust.
  - **proposed_qt6_design**: A frozen Diagnostic(kind, severity, count, layer_id, t0, t1, message, detail) dataclass and bundle.diagnostics: tuple[Diagnostic, ...], with `warnings` kept as a derived property of message strings for one release. The annotation panel becomes a QAbstractTableModel over the diagnostics behind a QSortFilterProxyModel; the status badge counts by severity instead of parsing prose; double-clicking a diagnostic with a time range navigates there.
  - **effort**: medium

  - **title**: The layer set is an item-list with visibility state duplicated in the Qt layer
  - **file**: src/audian/session.py
  - **line**: 578
  - **evidence**: SessionBundle exposes __len__ :578, __iter__ :581, __getitem__ :584, __contains__ :587, get :590, points() :593, spans() :596, tracks() :599 — an item-based collection. Visibility lives separately as AnnotationLayer.layers: dict[str, bool] (eventoverlay.py:357-361), rebuilt from default_on on every load (eventoverlay.py:390), alongside surfaces: dict[str, bool] (:362) and a hand-rolled `revision` counter (:368-372) that overlays compare against to skip redraws. Chips are hand-built widgets (databrowser build_annotation_chips, re-run from apply_theme at databrowser.py:2635).
  - **why_it_matters**: Two sources of truth for 'which layers exist', kept in sync by hand; a hand-rolled change counter standing in for dataChanged; and a legend that is a pile of imperative widgets rebuilt wholesale on every theme switch. The mandate names 'inappropriate use of item-based widgets where model/view is more suitable' — this is the clearest instance in the cluster.
  - **proposed_qt6_design**: A LayerModel(QAbstractListModel) over bundle.layers with CheckStateRole for visibility, DecorationRole for the role swatch, ToolTipRole for layer.tip and custom roles for track/role/count. The chips, any future dock and the overlays become views/consumers; dataChanged replaces sigVisibilityChanged and the revision counter, and themeChanged invalidates the swatches through the model.
  - **effort**: medium

  - **title**: Query results are type-erased tuples and string-encoded composite keys
  - **file**: src/audian/session.py
  - **line**: 645
  - **evidence**: nearest (:645-665), step (:667-684) and spans_at (:686-700) return bare tuples (layer, series_index, row) / [(layer, index)]; pulses_in (:702-739) returns dict[str, tuple[int, int, int]] keyed by a hand-built f"{layer.id}#{si}" string (:738). _series_times (:741-750) is an isinstance switch over PointLayer/SpanLayer/StepTrack that every consumer must mirror.
  - **why_it_matters**: Every consumer re-parses the '#' key and re-switches on layer type; the tuple carries no names, so a transposed index is a silent wrong readout rather than a type error. This is what makes the overlay and readout code a pile of type switches.
  - **proposed_qt6_design**: Small frozen dataclasses — Mark(layer, series, row) and SeriesSlice(layer_id, series, start, stop) — returned from all four queries; push the isinstance switch into a Layer.series_times() method on the three layer classes in layers.py so _series_times disappears.
  - **effort**: small

  - **title**: Module-global mutable cache aliased across modules by name
  - **file**: src/audian/session.py
  - **line**: 184
  - **evidence**: _SHA_CACHE = alignment._SHA_CACHE (:184), documented at :182-183 as 'the same dict object under the name it had before the split, so a caller that clears it still clears the one verify_sha256 reads'.
  - **why_it_matters**: Two modules share one unbounded, never-invalidated global dict of SHA-256 results with no mtime component in the key, so a rewritten bundle keeps its old digest for the life of the process. Cross-module aliasing of a private global also means neither module owns its lifetime.
  - **proposed_qt6_design**: An explicit cache object owned by the loader/service (or functools.lru_cache(maxsize=...) on a _digest(path, mtime, size) function), passed in rather than reached for; delete the alias in session.py.
  - **effort**: small

  - **title**: Bundle loading has no lifecycle: no started/failed signals, blanket exception handling at the widget, no reuse across browsers
  - **file**: src/audian/eventoverlay.py
  - **line**: 387
  - **evidence**: AnnotationLayer.load (eventoverlay.py:376-397) calls SessionBundle.load inline, replaces self.bundle wholesale, resets self.layers and emits only sigTableChanged after the fact. Failure is caught one level up by a blanket `except Exception` (databrowser.py:4763-4768) turning any reader error into a single toast. Each DataBrowser loads its own bundle; there is no shared cache keyed by path.
  - **why_it_matters**: There is no observable 'loading' state to disable UI against, no way to distinguish 'no bundle' from 'load failed', and opening the same recording in two tabs re-reads and re-checks the same CSVs. Once loading moves off the GUI thread these gaps become mandatory to fill.
  - **proposed_qt6_design**: A SessionLoader(QObject) service with loadStarted(path) / loadFinished(bundle) / loadFailed(path, error), a bounded LRU keyed by (metadata path, mtime), and typed reader exceptions (BundleNotFound, BundleUnreadable, SchemaMismatch) instead of the blanket catch. AnnotationLayer becomes a consumer of the service rather than the thing that performs IO.
  - **effort**: medium

- **behavior_contract**:
  - Startup theme: the app opens in the theme named by `--theme` or, failing that, the saved
  - `theme` setting, defaulting to dark; an unknown value falls back to dark
  - (audian.py:5016-5020). The choice is written back on every switch (audian.py:1675) and
  - survives a restart.
  - Ctrl+Shift+L toggles dark/daylight live with no restart, and everything changes
  - together: menu bar, toolbars, status bar, tabs, inputs, plot backgrounds, axis text,
  - grid, waveform pens, spectrogram colormap, annotation marks, label markers, chips,
  - badges and glyph icons. There is no state in which light chrome wraps dark plots
  - (audian.py:1640-1682, databrowser.py:2587-2649).
  - After a theme switch, toolbar buttons occupy exactly the height and position they had at
  - startup: no button 6 px lower, no clipped bottom border or rounded corner
  - (theme.py:1046-1071, QSS :2247-2299).
  - Plot viewbox interiors are always `bg.plot` (never pure #000, never Qt grey); figure
  - gutters are always `bg.base`; `setBackground(None)` never appears outside theme.py
  - (theme.py:1605-1624, guarded by tests/test_theme.py:274).
  - A lane's figure spends no vertical padding: style_channel_figure leaves 0 px top/bottom
  - contents margin, so a 34 px dense row is drawn in 34 px and the bottom row is not
  - clipped off the screen (theme.py:1626-1665).
  - Axis text is the mono face at 9 pt in `fg.muted`; tick marks are `fg.faint` hairlines;
  - only MAJOR ticks carry labels — an axis asking for three ticks shows three labels, not
  - five (theme.py:1542-1576, maxTextLevel=0 at :1562).
  - Numeric fields render their digits uncropped: a pg.SpinBox reading `0 Hz` reads `0 Hz`,
  - never `A Hz` (theme.py:1667-1691).
  - The focus ring is a 2 px `primary` border that does not resize the control it lands on
  - or shift the layout beneath it (QSS :2390-2409, s4_focus/s6_focus/s8_focus at
  - :2447-2449).
  - Contrast gate: every text token clears 4.5:1 on every surface it is drawn on in BOTH
  - themes, and all eight marker colours clear 4.5:1 on bg.plot and bg.raised. `fg.faint` is
  - deliberately below the bar and is used only for non-text decoration and the Disabled
  - colour group. `python -m audian.theme` exits 0 (theme.py:2739-2757, :3246-3302).
  - Separation gate: no two annotation categories are closer than 15 dE2000 under the worst
  - of normal/protan/deutan/tritan vision except the named exempt pairs, and no category hue
  - is within 20 dE2000 of any colour a lane actually paints. The CVD simulator's
  - calibration — Okabe-Ito's worst mutual pair — must land between 7 and 9
  - (theme.py:3097-3138, :3222-3244, :3299-3302).
  - Waveform emphasis: in a channel stack exactly one trace is saturated — the selected
  - channel, painted `primary`, in every plot that draws it, main and navigator alike. All
  - others are dimmed toward the plot ground, deeper above 4 visible channels, and never
  - dimmed below 3:1 (dark) or 4.5:1 (daylight) (theme.py:1082-1102, :1123-1153,
  - :1220-1262).
  - A trace named 'filtered' whose high-pass is below 0.1% of Nyquist and whose low-pass is
  - at or above Nyquist is painted as RAW everywhere, so identical samples are never amber
  - in one plot and cyan in another (theme.py:1163-1218).
  - Pen widths: the selected channel gets LW_SELECTED (2.0) when sparse and LW_THIN (1.0)
  - when dense; every unselected trace stays at or below 1.0 so a 16-channel repaint stays
  - in Qt's raster fast path (~4.4 ms not ~28 ms; 5.4 ms vs 908 ms with antialiasing).
  - pyqtgraph antialiasing stays off globally (theme.py:1030-1042, :1239-1262, :2464-2475,
  - tests/test_theme.py:186).
  - Annotation encoding: hue carries KIND only. A predicted (unobserved) mark differs from
  - an observed one by a [2,2] dash and never by hue or opacity; an unvalidated alignment
  - dashes every pen and hatches every fill with BDiagPattern and never reduces alpha;
  - treatment is carried by a V/B/S letter knocked out of the span's start edge over a
  - `bg.plot` chip (theme.py:1349-1432).
  - Startup on a 16-channel file leaves no hundreds of hidden top-level QMenu/QWidget
  - windows, and PlotItem.showGrid / setLogMode / setDownsampling still work after the menus
  - are stripped (theme.py:1775-1911, tests/test_theme.py:124).
  - Spectrogram maps: the combo lists the ACTIVE theme's map list (a different list per
  - theme, not a reversal), the noise floor matches the page in both themes, and a theme
  - switch re-pushes the map so the panel is never a dark slab in a white window
  - (theme.py:2585-2634, databrowser.py:2620-2631). An unknown or unloadable map name falls
  - back to the first entry instead of raising.
  - Every pen/brush/colour helper returns a FRESH Qt object (no shared mutable state handed
  - to callers), every `style_*` applier is idempotent under repeated calls, and the theme
  - module imports cleanly without a QApplication (theme.py:33-42,
  - tests/test_theme.py:92-122, :179).
  - Loading a session bundle prints one status line naming what was loaded — `session_id:
  - Trials 36, Volley pulses 1279, ...` — with a `[warn]` / `[unvalidated]` suffix when
  - trust is not ok, and a trailing `N rows in no layer` clause whenever any row reached no
  - layer (session.py:627-643).
  - No row silently disappears. Rows with no usable `recording_time_s` are dropped, counted
  - per kind and reported (session.py:344-362, databrowser.py:4770-4775). Rows with an
  - unknown or null treatment / pulse_type are counted into `unlayered` and named in a
  - warning (session.py:895-915, :1030-1054). A backstop compares each CSV's height against
  - the layers built from it at every load and reports anything neither builder anticipated
  - (session.py:536-566).
  - Nulls are values, never zeros. A null treatment on a pulse means the ambient resting
  - train (the most common value in that column) and is not silence. A silence trial with a
  - null `pulses_emitted` reports 'the control condition is unverified, not verified as
  - silent' and is never filled with 0 (session.py:1650-1658). A null `records_lost` says
  - nothing about lost records and does not become a fault (session.py:1272-1281).
  - Column dtypes are pinned from CSV_TYPES on every read, so `records_lost > 0` is always a
  - numeric comparison and never a string one that is true for '10' and false for '9'
  - (session.py:199-292, :330-341).
  - The partition claim behind the treatment letter is re-checked at every load and any
  - violation is named: localization pulses inside a trial, baseline pulses outside every
  - baseline trial, volley pulses outside every volley trial, any pulse inside a silence
  - trial, and a silence trial reporting non-zero pulses_emitted (session.py:1601-1659).
  - The fit residual is reported per region — the recording's own files when the bundle
  - declares joins, otherwise 8 equal bins — as median, IQR and matched-of-total; a region
  - whose median sits more than 10x the fit's own match tolerance from zero produces a
  - warning naming the region and the multiple (session.py:1491-1512, :1514-1599, surfaced
  - at databrowser.py:4737-4759).
  - A bundle fitted against a different recording still LOADS and still reports its warnings
  - and summary, but nothing is drawn and the mismatch is stated by name
  - (session.py:570-572, eventoverlay.py:390-395, databrowser.py:4784-4790).
  - spans_at, SpanLayer.at, pulses_in and the load-time partition check all use the same
  - half-open [start, end) interval; 19 exp2 marks land bit-exactly on a trial end and must
  - be classified identically by all four (session.py:702-739).
  - Hover readout and keyboard stepping: nearest(t, ids) returns the closest mark and
  - step(t, forward, ids) the next/previous one strictly after/before t, restricted to the
  - enabled layer ids, with one (layer, series, row) shape for point, span and track layers
  - alike (session.py:645-684).
  - Default layer visibility on load: all three trial treatments, both pulse layers and both
  - detection layers ON; localization runs, session events and the control track OFF
  - (session.py:1272, :1301, :1360; applied at eventoverlay.py:390).
  - A control channel that holds one value for the whole session is not offered as a track
  - row, and the reason is written into the track's tooltip ('not offered: volley_amplitude
  - held 1 throughout') rather than silently omitted (session.py:1315-1329).
  - An explained detection is drawn in its parent PULSE's hue, not in an ink of its own, so
  - the visible x-offset between a PULSES tick and its HEARD stub is the literal fit
  - residual; an explained detection with no matched pulse within the fit's tolerance keeps
  - the `detection.novel` ink and is counted in a warning (session.py:1105-1167).
- **risk**: high — theme.py is the single import every widget and plot item depends on (164 `theme.` references in databrowser.py, 164 in audian.py, 49 in fulltraceplot.py, 201 in its own test), its QSS/palette/metrics numbers were empirically tuned against Qt5 Fusion and will move under Qt6's always-on high-DPI scaling, and its two most dangerous Qt6 breaks fail SILENTLY (QFontDatabase() at theme.py:677 swallowed by a bare except, so every font degrades to the generic fallback; unscoped enums accepted by PySide6's forgiving mode until they are not); session.py, by contrast, is entirely Qt-free and is the low-risk half of this cluster.
- **notes**: Answering the brief's question directly: session.py is NOT a widget and NOT a "UI session" — it is the domain model / reader for a fakefish stimulator bundle, deliberately Qt-free (docstring session.py:9-14; imports at :56-104 are logging / collections.abc / dataclasses / pathlib / typing / numpy / polars / alignment / windowing / layers only). It owns NO UI state and persists NOTHING: it is read-only and load-once. The only writes anywhere near it are the SHA cache alias (session.py:184) and `layer.unjoined` set at :1167, which is declared in layers.py:201 and so is not attribute injection. All session-adjacent UI state lives in eventoverlay.AnnotationLayer (eventoverlay.py:342-410): `bundle`, `visible`, `layers: dict[str, bool]`, `surfaces: dict[str, bool]`, `recording_mismatch`, `revision` and a window cache, exposed via sigTableChanged / sigVisibilityChanged. The only persistence in this whole cluster is `save_setting("theme", name)` (audian.py:1675) and the spectrogram colormap index (databrowser.py:1447).  Suggested ordering for the lead: (1) fix the two silent breaks first — QFontDatabase (theme.py:677, and delete the `except Exception` that hides it) and the implicit pyqtgraph binding selection (theme.py:78 before :79 — set PYQTGRAPH_QT_LIB in the bootstrap before any pyqtgraph import); (2) scope every enum (theme.py:711, 713, 817, 922, 1392, 1408, 1962-1997) mechanically and add a lint rule, because PySide6's forgiving mode will hide whatever you miss; (3) split the package and move the audit/colour-science half out of the runtime import path — the cheapest large win, and it unblocks parallel work; (4) introduce ThemeService(QObject) with themeChanged and delete the three imperative cascades; (5) leave session.py's reader logic alone and only wrap it in a threaded loader with structured diagnostics.  Three things must NOT be "modernised" away: `pg.setConfigOptions(antialias=False)` (theme.py:2464-2475) and `LW_THIN = 1.0` (theme.py:1030-1042) are measured performance contracts (170x and ~6x paint cost); the no-alpha rule for unvalidated annotations (theme.py:1370-1379) is a daylight-legibility ruling, not a style preference; and the deliberate absence of QGraphicsItem.ItemIgnoresTransformations on the overlay TextItem (theme.py:1936-1940) prevents a double transform correction that paints the overlay off screen.  tests/test_theme.py already encodes ~45 of the behaviours above as executable assertions, including grep gates for hex literals, named Qt colours and `setBackground(None)` outside theme.py (:265-277) that are currently informational and become hard failures under AUDIAN_THEME_STRICT=1. Turning that flag on before the port begins is the cheapest available guard against the port re-introducing literals. tests/test_session.py covers the reader's null / partition / dtype discipline against a synthesised bundle.  Not read as part of this cluster but load-bearing on it: alignment.py (SessionMeta, RecordingCheck, trust, join_times_s, match_tolerance_s, check_recording at :745-830), layers.py (Layer / PointLayer / PointSeries / SpanLayer / StepTrack), windowing.py (merge_spans, pair_runs, EMPTY) and eventoverlay.py (the Qt half of the session story). theme.py's real consumers are databrowser.py (8253 lines) and audian.py (5051 lines), both of which need their own analysis before ThemeService can land.
