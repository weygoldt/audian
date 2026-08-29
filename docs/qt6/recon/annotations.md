# Recon: annotations

- **cluster**: annotations
- **purpose**: Two structurally parallel but deliberately disjoint annotation systems, plus one unrelated numeric module. (1) FIXED annotations: `eventoverlay.py` holds `AnnotationLayer` (per-browser: loaded `SessionBundle`, per-layer/per-surface toggles, shared window cache) and `EventOverlay` (per-plot: turns cached numpy arrays into pyqtgraph curve/scatter/text items). Read-only — audian never writes a bundle. (2) EDITABLE labels: `labels.py` is a pure-Python store (`LabelSet`, `Label`, `LabelCategory`) with no Qt import, persisted to a `<stem>-editable-labels.csv` sidecar; `labeloverlay.py` is its Qt half (`LabelOverlay` drawing, `LabelEditor` grips, two `QAbstractTableModel` dialogs, `CategoryStrip` chips). (3) `activity.py` is pure numpy with zero Qt — baseline-referenced per-bin activity metrics for the navigator strip; it is in this cluster by filename only and has nothing to do with annotations. NOTE: `markerdata.py` listed in the task DOES NOT EXIST — it was deleted in commit b52a5e1 when the editable-label feature replaced it.
- **public_surface**:
  - **name**: LabelSet
  - **file**: src/audian/labels.py:291
  - **kind**: class
  - **base**: 
  - **summary**: The store: categories + labels of one open recording. Mutations bump `revision` (the int the overlays gate redraw on) and set `dirty`. Holds `blocked` (refuses to write over a sidecar that did not read whole) and a ONE-SLOT `_undo` tuple. Methods: add/remove/remove_last/clear/set_note/set_geometry/index_of (by identity)/window/read/write/discard/save/undo/can_undo/forget_undo, plus the category API (add_category/remove_category/set_categories/category/category_index/color_of/next_color/count_in). Imported by databrowser.py:54.

  - **name**: Label
  - **file**: src/audian/labels.py:172
  - **kind**: class
  - **base**: dataclass
  - **summary**: One mark: category, kind, channel (None = mean spectrogram / all lanes), t0, t1 (None = point), f0, f1 (None = no band), note. `row()` writes 8 CSV cells at 6dp seconds / 3dp Hz; `from_row()` parses and normalises. Mutable dataclass, addressed by identity everywhere.

  - **name**: LabelCategory
  - **file**: src/audian/labels.py:142
  - **kind**: class
  - **base**: frozen dataclass
  - **summary**: name (the identity -- it is what goes in the CSV), kind (point|span), color (index into theme.marker_color, mod 8). Persisted in audian settings, not in the recording.

  - **name**: ReadReport
  - **file**: src/audian/labels.py:278
  - **kind**: class
  - **base**: frozen dataclass
  - **summary**: read / dropped / added / error -- what LabelSet.read found, for the caller to report to the reader.

  - **name**: sidecar_path
  - **file**: src/audian/labels.py:108
  - **kind**: function
  - **base**: 
  - **summary**: recording -> `<stem>-editable-labels.csv`. SIDECAR_SUFFIX defined at labels.py:105.

  - **name**: categories_to_settings / categories_from_settings
  - **file**: src/audian/labels.py:715
  - **kind**: function
  - **base**: 
  - **summary**: Vocabulary <-> JSON-able settings values. `categories_from_settings` (labels.py:720) never raises; skips malformed entries rather than defaulting them. DEFAULT_CATEGORIES at labels.py:796 = (event/span/0, pulse/point/1).

  - **name**: KIND_POINT / KIND_SPAN / KINDS / COLUMNS
  - **file**: src/audian/labels.py:74
  - **kind**: constant
  - **base**: 
  - **summary**: "point"/"span"; COLUMNS (labels.py:86) is the 8-column CSV order (category,kind,channel,t_start_s,t_end_s,f_low_hz,f_high_hz,note). databrowser.py:66 aliases KIND_POINT as LABEL_POINT.

  - **name**: LabelOverlay
  - **file**: src/audian/labeloverlay.py:399
  - **kind**: class
  - **base**: 
  - **summary**: Per-plot drawing of editable labels. NOT a QObject. Pool of QGraphicsRectItem + one ScatterPlotItem for points. Self-driven: connects viewbox sigRangeChanged/sigResized in __init__ (labeloverlay.py:466-469). Public: update_plot/invalidate/polish/clear/set_visible/pick/start_editing/stop_editing/editing/channel/channels. Raw callbacks on_edit(overlay,label,t0,t1,f0,f1,resized) and on_dropped(overlay).

  - **name**: LabelEditor
  - **file**: src/audian/labeloverlay.py:219
  - **kind**: class
  - **base**: pg.ROI
  - **summary**: The grips on the ONE selected label. movable=False/rotatable=False/resizable=False/invertible=False so its body passes drags through to the viewbox and only the 12px grips are control. build_grips/sync(finish=False)/resized()/repen()/region(). Holds `label` by identity, plus `syncing` and `dragging` flags.

  - **name**: CategoryModel / CategoryDialog
  - **file**: src/audian/labeloverlay.py:868
  - **kind**: class
  - **base**: QAbstractTableModel / QDialog
  - **summary**: Ctrl+L vocabulary editor. Model edits a COPY of store.categories; `store_rows()` (labeloverlay.py:1003) pushes on OK and returns dropped names. CategoryDialog at labeloverlay.py:1013, non-modal, WA_DeleteOnClose.

  - **name**: LabelTableModel / LabelTable
  - **file**: src/audian/labeloverlay.py:1082
  - **kind**: class
  - **base**: QAbstractTableModel / QDialog
  - **summary**: Ctrl+M label list, over the LIVE store (not a copy). Only the note column is editable; remove_rows() deletes and calls forget_undo() when more than one row went. refresh() is beginResetModel/endResetModel. LabelTable at labeloverlay.py:1175.

  - **name**: CategoryStrip / category_chip / category_tip / swatch_icon
  - **file**: src/audian/labeloverlay.py:1244
  - **kind**: class
  - **base**: QWidget
  - **summary**: The category chips in the parameter bar. Hand-rolled two-row flow layout (pack/relayout at labeloverlay.py:1355-1433) folding overflow into a `+N` QMenu. swatch_icon (labeloverlay.py:825) builds a QIcon from the _SwatchEngine QIconEngine (labeloverlay.py:800). category_chip at :1220, category_tip at :1207.

  - **name**: AnnotationLayer
  - **file**: src/audian/eventoverlay.py:343
  - **kind**: class
  - **base**: QObject
  - **summary**: Per-browser fixed-annotation state. Signals sigTableChanged / sigVisibilityChanged (eventoverlay.py:347,349). Owns bundle, per-layer dict, per-surface dict, `revision` int, and the SHARED window cache (_window_cache/point_window/span_window/label_window). Also load/discover/clear/solo/show_all/set_layer/set_surface/layer_states/surface_states/nearest/step/badge and the pen/brush resolvers (mark_pen/edge_pen/fill_brush/fill_alpha/color/role).

  - **name**: EventOverlay
  - **file**: src/audian/eventoverlay.py:827
  - **kind**: class
  - **base**: 
  - **summary**: Per-plot fixed-annotation drawing. NOT a QObject. One PlotCurveItem per point series, one fill + one edge curve per span layer, ScatterPlotItem caps on predicted series, fixed 24-slot pg.TextItem pool for treatment letters. rebuild/clear/polish/update_plot/pixels/cap_y. Self-driven off viewbox signals (eventoverlay.py:947-951).

  - **name**: SURFACE_TRACE / SURFACE_SPECTROGRAM / SURFACE_NAVIGATOR / SURFACE_ORDER / SURFACE_LABELS / SURFACE_STYLE
  - **file**: src/audian/eventoverlay.py:104
  - **kind**: constant
  - **base**: 
  - **summary**: The three drawing surfaces and their per-surface fill/caps/labels/mark_z policy (SURFACE_STYLE at eventoverlay.py:305). labeloverlay.py:126-131 imports SURFACE_* and NAV_REGION_Z from here -- the editable-label module depends on the fixed-annotation module for its vocabulary.

  - **name**: z-order constants
  - **file**: src/audian/eventoverlay.py:240
  - **kind**: constant
  - **base**: 
  - **summary**: TRACE_Z=0 (:240), FILL_Z=-20 (:246), MARK_Z=15 (:252), CAP_Z=16 (:253), NAV_REGION_Z=50 (:259), NAV_MARK_Z=60 (:268). labeloverlay adds LABEL_Z=25 (:137), LABEL_NAV_Z=NAV_REGION_Z+15 (:165), LABEL_EDIT_Z=30 (:171). Also SPAN_FILL_ALPHA (:166), FILL_ROLES (:139), LABEL_POOL=24 (:209), MIN_LABEL_PX=14 (:219), CAP_LIMIT=400 (:230).

  - **name**: legend_icon / span_icon / swatch_icon / swatch_pixmap
  - **file**: src/audian/eventoverlay.py:1315
  - **kind**: function
  - **base**: 
  - **summary**: QPixmap-based legend chip icons drawn with the same pens/brushes the plot uses, so a reader matches a mark to a chip by looking. span_icon at :1347, swatch_pixmap at :1352, swatch_icon at :1370. Consumed by the parameter bar and controlpanel.

  - **name**: mark_time / describe_mark
  - **file**: src/audian/eventoverlay.py:787
  - **kind**: function
  - **base**: 
  - **summary**: Kind-agnostic time (a span reports its start) and one-line description for one (layer, series, row); used by the hover readout and the step key so neither needs a type switch. describe_mark at eventoverlay.py:803.

  - **name**: LayerState
  - **file**: src/audian/eventoverlay.py:329
  - **kind**: class
  - **base**: NamedTuple
  - **summary**: id/label/short/micro/kind/count/color/enabled/tip -- what a legend chip needs to draw itself and say what it toggles.

  - **name**: _passive
  - **file**: src/audian/eventoverlay.py:812
  - **kind**: function
  - **base**: 
  - **summary**: setAcceptedMouseButtons(Qt.NoButton) + setAcceptHoverEvents(False). PRIVATE BY NAME BUT IMPORTED ACROSS MODULES: controlpanel.py:70 does `from .eventoverlay import _passive`; labeloverlay.py:203 defines a second copy of the same function.

  - **name**: BinStats
  - **file**: src/audian/activity.py:91
  - **kind**: class
  - **base**: dataclass
  - **summary**: Additively composable per-bin accumulators (n, total, total_sq, minimum, maximum) shaped (nbins, channels). mean/variance/peak/combine(factor) -- combine is the pyramid step that makes navigator zoom cheap. Pure numpy, no Qt. Built by compresseddata.py:187, consumed by fulltraceplot.py:792.

  - **name**: reduce_block / global_baseline / rms_excess_db / peak_excess_db / crest_db / noise_peak_db / classify
  - **file**: src/audian/activity.py:169
  - **kind**: function
  - **base**: 
  - **summary**: The activity metric pipeline: reduce_block (:169), global_baseline (:190), rms_excess_db (:218), peak_excess_db (:227), crest_db (:236), noise_peak_db (:253), classify (:267) returning QUIET/SUSTAINED/TRANSIENT (:250). Constants BASELINE_PERCENTILE=10.0, BASELINE_EPS=1e-12, SUSTAINED_RMS_DB=3.0, EVENT_MARGIN_DB=6.0 (:74-87).

- **qt5_api_usage**:
  - **file**: src/audian/labeloverlay.py
  - **line**: 107
  - **api**: `from PyQt5.QtCore import QAbstractTableModel, QModelIndex, QRectF, QSize, Qt, QVariant`. VERIFIED HARD BREAK: PySide6 6.11.2 has no `QtCore.QVariant` at all (`AttributeError: module 'PySide6.QtCore' has no attribute 'QVariant'`). The import itself fails, so the whole module fails to import.
  - **qt6_replacement**: Drop QVariant from the import list entirely; PySide6 uses None as the null variant. Other five names port unchanged.
  - **severity**: breaking

  - **file**: src/audian/labeloverlay.py
  - **line**: 894
  - **api**: `return QVariant()` as the null return from model data()/headerData(). Six sites: 894, 898, 914 (CategoryModel) and 1107, 1111, 1131 (LabelTableModel).
  - **qt6_replacement**: `return None` at all six sites. PySide6 maps None to an invalid QVariant.
  - **severity**: breaking

  - **file**: src/audian/labeloverlay.py
  - **line**: 918
  - **api**: `return Qt.NoItemFlag` in flags(). VERIFIED THIS IS ALREADY A LATENT BUG: `Qt.NoItemFlag` (singular) does not exist in PyQt5 EITHER -- the Qt5 spelling is `Qt.NoItemFlags`. Both sites (918 CategoryModel, 1135 LabelTableModel) raise AttributeError today whenever flags() is called with an invalid index. Neither name exists in PySide6.
  - **qt6_replacement**: `Qt.ItemFlag.NoItemFlags` (verified present in PySide6 6.11.2). Fix, do not port, this line.
  - **severity**: breaking

  - **file**: src/audian/labeloverlay.py
  - **line**: 108
  - **api**: `from PyQt5.QtGui import QColor, QIcon, QIconEngine`
  - **qt6_replacement**: `from PySide6.QtGui import QColor, QIcon, QIconEngine`
  - **severity**: breaking

  - **file**: src/audian/labeloverlay.py
  - **line**: 109
  - **api**: `from PyQt5.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QGraphicsRectItem, QHBoxLayout, QMenu, QMessageBox, QPushButton, QSizePolicy, QStyledItemDelegate, QTableView, QToolButton, QVBoxLayout, QWidget)`
  - **qt6_replacement**: `from PySide6.QtWidgets import ...`. All 15 names exist in Qt6.
  - **severity**: breaking

  - **file**: src/audian/labeloverlay.py
  - **line**: 813
  - **api**: `def paint(self, painter, rect, mode=QIcon.Normal, state=QIcon.Off)` -- QIconEngine virtual override with unscoped enum defaults.
  - **qt6_replacement**: `QIcon.Mode.Normal` / `QIcon.State.Off`. PySide6 6.11 still resolves the unscoped aliases (verified), so cosmetic, but the override must stay 4-arg positional for PySide6 virtual dispatch.
  - **severity**: cosmetic

  - **file**: src/audian/labeloverlay.py
  - **line**: 825
  - **api**: `return QIcon(_SwatchEngine(index))` -- a Python-subclassed QIconEngine handed to QIcon with NO Python reference retained. QIcon takes C++ ownership; historically a PySide6 use-after-free pattern.
  - **qt6_replacement**: Verified to survive on PySide6 6.11.2 (icon painted correctly after gc.collect()), but fragile. Cache engines/icons in a module-level dict keyed by palette index -- it also removes the per-call rebuild on every chip repaint.
  - **severity**: behavior-change

  - **file**: src/audian/labeloverlay.py
  - **line**: 215
  - **api**: `item.setAcceptedMouseButtons(Qt.NoButton)`; duplicated at eventoverlay.py:823.
  - **qt6_replacement**: `Qt.MouseButton.NoButton`
  - **severity**: cosmetic

  - **file**: src/audian/labeloverlay.py
  - **line**: 818
  - **api**: `painter.setPen(Qt.NoPen)`
  - **qt6_replacement**: `Qt.PenStyle.NoPen`
  - **severity**: cosmetic

  - **file**: src/audian/labeloverlay.py
  - **line**: 987
  - **api**: `QMessageBox.question(...)` with `QMessageBox.Cancel | QMessageBox.Yes` and default `QMessageBox.Cancel` (lines 993-996).
  - **qt6_replacement**: `QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes`. Qt6 removed the 6-arg question() overload; the 5-arg form used here is fine.
  - **severity**: cosmetic

  - **file**: src/audian/labeloverlay.py
  - **line**: 1022
  - **api**: `self.setWindowModality(Qt.NonModal)` (also 1184); `self.setAttribute(Qt.WA_DeleteOnClose)` (1023, 1185).
  - **qt6_replacement**: `Qt.WindowModality.NonModal`; `Qt.WidgetAttribute.WA_DeleteOnClose`. The WA_DeleteOnClose + manual `self.label_dialog = None` bookkeeping in databrowser.py:3787-3800 is the ownership pattern to replace.
  - **severity**: cosmetic

  - **file**: src/audian/labeloverlay.py
  - **line**: 1032
  - **api**: `QAbstractItemView.SelectRows` (1032, 1192) and `QAbstractItemView.ExtendedSelection` (1193).
  - **qt6_replacement**: `QAbstractItemView.SelectionBehavior.SelectRows`; `QAbstractItemView.SelectionMode.ExtendedSelection`
  - **severity**: cosmetic

  - **file**: src/audian/labeloverlay.py
  - **line**: 1048
  - **api**: `QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Ok, self)`; `QDialogButtonBox(QDialogButtonBox.Close, self)` (1197); `box.addButton("&Remove", QDialogButtonBox.DestructiveRole)` (1198).
  - **qt6_replacement**: `QDialogButtonBox.StandardButton.Cancel|Ok|Close`; `QDialogButtonBox.ButtonRole.DestructiveRole`
  - **severity**: cosmetic

  - **file**: src/audian/labeloverlay.py
  - **line**: 1236
  - **api**: `chip.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)`
  - **qt6_replacement**: `Qt.ToolButtonStyle.ToolButtonTextBesideIcon`
  - **severity**: cosmetic

  - **file**: src/audian/labeloverlay.py
  - **line**: 1297
  - **api**: `self.more.setPopupMode(QToolButton.InstantPopup)`
  - **qt6_replacement**: `QToolButton.ToolButtonPopupMode.InstantPopup`
  - **severity**: cosmetic

  - **file**: src/audian/labeloverlay.py
  - **line**: 1304
  - **api**: `self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)`
  - **qt6_replacement**: `QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed`
  - **severity**: cosmetic

  - **file**: src/audian/labeloverlay.py
  - **line**: 889
  - **api**: `def headerData(self, index, orientation, role=Qt.DisplayRole)` -- first parameter named `index` rather than Qt's `section` (also line 1102). PySide6 dispatches virtuals positionally so it works, but any keyword call breaks and it reads as a QModelIndex.
  - **qt6_replacement**: Rename to `section: int`; add `-> object` typing while touching it.
  - **severity**: cosmetic

  - **file**: src/audian/labeloverlay.py
  - **line**: 872
  - **api**: `def rowCount(self, parent=None)` / `columnCount(self, parent=None)` (also 1094, 1097). Qt always passes a QModelIndex; the None default hides that a table model must return 0 for a valid parent. Current code returns the full row count for ANY parent.
  - **qt6_replacement**: `def rowCount(self, parent: QModelIndex = QModelIndex()) -> int: return 0 if parent.isValid() else len(...)`
  - **severity**: behavior-change

  - **file**: src/audian/labeloverlay.py
  - **line**: 912
  - **api**: `return Qt.AlignLeft | Qt.AlignVCenter` from data() for TextAlignmentRole (also 1127-1130).
  - **qt6_replacement**: Under PySide6 this returns a `Qt.AlignmentFlag` object rather than an int; Qt6 styles want `int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)`. Verify alignment still applies after the port.
  - **severity**: behavior-change

  - **file**: src/audian/eventoverlay.py
  - **line**: 73
  - **api**: `from PyQt5.QtCore import Qt, QObject, QRect` and `from PyQt5.QtGui import QIcon, QPainter, QPixmap` (line 74).
  - **qt6_replacement**: `from PySide6.QtCore import ...` / `from PySide6.QtGui import ...`
  - **severity**: breaking

  - **file**: src/audian/eventoverlay.py
  - **line**: 76
  - **api**: Half-finished binding shim: `try: from PyQt5.QtCore import Signal / except ImportError: from PyQt5.QtCore import pyqtSignal as Signal`, commented '# pragma: no cover - PyQt5 always has pyqtSignal'. VERIFIED the try branch ALWAYS raises under PyQt5 (`cannot import name 'Signal' from 'PyQt5.QtCore'`), so the except branch is the only live path. The same dead shim is copy-pasted in selectviewbox.py:5-7 and spectrogramplot.py:9-11, while buffereddata.py:8 and fulltraceplot.py:26 import pyqtSignal directly -- three spellings of one idea.
  - **qt6_replacement**: Delete the try/except; `from PySide6.QtCore import Signal, Slot`. Adopt one import convention repo-wide and add `@Slot` decorators on connected callbacks (PySide6 benefits; PyQt5 did not require them).
  - **severity**: breaking

  - **file**: src/audian/eventoverlay.py
  - **line**: 347
  - **api**: `sigTableChanged = Signal()` / `sigVisibilityChanged = Signal()` on `AnnotationLayer(QObject)`. Untyped, argumentless notification signals; consumers (databrowser.py:1363-1365) re-query the whole object.
  - **qt6_replacement**: `Signal()` ports unchanged. Architecturally give them payloads (e.g. `Signal(str, bool)` for a layer toggle) so a slot need not re-read all state, and rename to Qt6 convention (`tableChanged`, `visibilityChanged`) -- `sig*` is a pyqtgraph convention, not a Qt one.
  - **severity**: cosmetic

  - **file**: src/audian/eventoverlay.py
  - **line**: 1284
  - **api**: `Qt.DashLine` / `Qt.SolidLine` (lines 1284, 1285, 1337)
  - **qt6_replacement**: `Qt.PenStyle.DashLine` / `Qt.PenStyle.SolidLine`
  - **severity**: cosmetic

  - **file**: src/audian/eventoverlay.py
  - **line**: 1290
  - **api**: `pixmap.fill(Qt.transparent)` (lines 1290, 1322, 1360)
  - **qt6_replacement**: `Qt.GlobalColor.transparent`
  - **severity**: cosmetic

  - **file**: src/audian/eventoverlay.py
  - **line**: 1292
  - **api**: `painter.setRenderHint(QPainter.Antialiasing, False)` (lines 1292, 1324)
  - **qt6_replacement**: `QPainter.RenderHint.Antialiasing`
  - **severity**: cosmetic

  - **file**: src/audian/eventoverlay.py
  - **line**: 1331
  - **api**: `interior.setStyle(Qt.BDiagPattern)` -- the hatch that marks an unvalidated fit.
  - **qt6_replacement**: `Qt.BrushStyle.BDiagPattern`
  - **severity**: cosmetic

  - **file**: src/audian/eventoverlay.py
  - **line**: 1289
  - **api**: `QPixmap(LEGEND_W, LEGEND_H)` at devicePixelRatio 1.0 (verified default), painted with logical-pixel coordinates. Three sites: 1289, 1321, 1359. Qt5 could run with high-DPI scaling disabled; Qt6 always enables it and removed AA_EnableHighDpiScaling/AA_DisableHighDpiScaling, so on any fractional-scale display these legend/swatch chips are upscaled and blurry.
  - **qt6_replacement**: Either `QPixmap(int(W*dpr), int(H*dpr))` + `setDevicePixelRatio(dpr)` from the target widget's `devicePixelRatioF()`, or -- better and consistent with the sibling module -- convert all three to QIconEngine subclasses like `labeloverlay._SwatchEngine`, which are resolution-independent by construction.
  - **severity**: behavior-change

  - **file**: src/audian/eventoverlay.py
  - **line**: 1004
  - **api**: `item.textItem.document().setDocumentMargin(1)` reaches into `pg.TextItem`'s private `textItem` QGraphicsTextItem; `item.fill = brush` at line 1055 writes a plain attribute pyqtgraph reads in paint(), needing an explicit `item.update()`.
  - **qt6_replacement**: Not a Qt5/Qt6 break (verified present in pyqtgraph 0.14.0 under PySide6: TextItem.textItem is a QGraphicsTextItem, .fill is a QBrush), but a pyqtgraph-private dependency. Pin pyqtgraph in pyproject.toml and keep a test on it, or replace with a QGraphicsSimpleTextItem the overlay owns.
  - **severity**: behavior-change

  - **file**: src/audian/eventoverlay.py
  - **line**: 1096
  - **api**: `widget.devicePixelRatioF()` on `plot.getViewWidget()`, None-guarded, used as the decimation pixel budget.
  - **qt6_replacement**: Unchanged in Qt6; keep. Worth noting it is the only DPI-aware code in the cluster while the legend pixmaps above are not.
  - **severity**: cosmetic

  - **file**: src/audian/labeloverlay.py
  - **line**: 219
  - **api**: `class LabelEditor(pg.ROI)` with `movable=False, rotatable=False, resizable=False, invertible=False`, `self.handleSize = GRIP_PX`, addScaleHandle/addTranslateHandle, sigRegionChanged/sigRegionChangeFinished, `sync(finish=False)`, and direct writes to `self.handles[i]['item'].pen/hoverPen/currentPen` in `repen` (lines 370-375). The entire editing gesture rests on measured pyqtgraph internals documented at labeloverlay.py:33-100.
  - **qt6_replacement**: VERIFIED INTACT on pyqtgraph 0.14.0 + PySide6 6.11.2: translatable/rotatable/resizable/invertible all False; handles expose pen/hoverPen/currentPen; two `finish=False` calls emitted 2x sigRegionChanged and 0x sigRegionChangeFinished, one `finish=True` emitted 1 finished. The migration's largest unknown is de-risked -- but pin pyqtgraph and keep the grip tests in tests/test_labels.py as the regression gate.
  - **severity**: behavior-change

  - **file**: src/audian/activity.py
  - **line**: 1
  - **api**: None. This module imports only `dataclasses` and `numpy` -- zero Qt surface across all 308 lines.
  - **qt6_replacement**: No migration work. Move to Domain/Core unchanged; it does not belong in an 'annotations' cluster at all.
  - **severity**: cosmetic

- **architecture_problems**:
  - **title**: Renaming a label category silently deletes every label under it (verified data loss)
  - **file**: src/audian/labeloverlay.py
  - **line**: 1004
  - **evidence**: `store_rows()` computes `kept = {c.name for c in self.rows}` then `gone = [c.name for c in self.store.categories if c.name not in kept]` and calls `store.remove_category(name)` for each -- which labels.py:395 documents as 'Deliberately destructive'. A rename is indistinguishable from a removal because `LabelCategory` has no identity but its name. Reproduced in-session: a store with 2 labels under 'event', renamed via `CategoryModel.setData(index(0,0), 'call')` then `store_rows()`, gives `labels: 0, categories: ['call','pulse'], gone: ['event']`. The QMessageBox confirmation at labeloverlay.py:987 lives only in `remove_rows()` (the Remove button), so the rename path destroys rows with no prompt, and databrowser.py:3800 then calls `schedule_label_save()`, rewriting the sidecar. `LabelSet.set_categories` (labels.py:363) documents the OPPOSITE contract: 'Labels are left alone. A category can be renamed out from under its rows ... and the rows are kept rather than dropped.' `remove_category` also calls `forget_undo()` (labels.py:404), so Shift+B cannot recover it.
  - **why_it_matters**: Editable labels are the only user-authored data audian holds. This destroys them on the single most ordinary edit a reader makes in the Ctrl+L dialog, with no prompt and no undo, and the atomic autosave then makes it permanent on disk within one turn of the event loop.
  - **proposed_qt6_design**: Give `LabelCategory` a stable opaque id (uuid4 hex) distinct from its display name, and have `Label` reference the id. `CategoryModel` rows then carry ids, a rename is an id whose name changed, and `store_rows()` computes genuine removals as an id-set difference. Keep `name` as the CSV column (write it from the id's current name; resolve name->id on read, minting a category for an unknown name exactly as `LabelSet.read` already does). Model the dialog as edits against a real QAbstractItemModel over the LIVE store wrapped in a `QUndoStack` macro, so Cancel is an undo rather than a discarded copy -- which also removes the copy-vs-live inconsistency with LabelTableModel.
  - **effort**: medium

  - **title**: Ad-hoc one-slot undo tuple where the migration spec explicitly asks for QUndoStack
  - **file**: src/audian/labels.py
  - **line**: 539
  - **evidence**: Undo is a single `self._undo: Optional[tuple]` holding `('add', label)`, `('remove', at, label)` or `('geometry', label, (t0,t1,f0,f1))`, replayed by an if/elif chain in `undo()` (labels.py:561-590) returning a bare string the caller turns into a sentence via a dict literal (databrowser.py:4211-4218). Every bulk operation must remember `forget_undo()` by hand: labels.py:404 (remove_category), :456 (clear), :640 (read), and labeloverlay.py:1156 (multi-row delete, with a 4-line comment explaining why). `set_geometry` returns False for a no-op specifically so a slow click does not 'spend the one undo slot' (labels.py:511-518). qt6migration.md:649 states: 'If undo/redo is relevant to annotations, edits or project modifications, evaluate QUndoStack / QUndoCommand rather than implementing ad-hoc undo state.'
  - **why_it_matters**: One level of undo across add/remove/geometry, silently discarded by any bulk change, is the weakest possible guarantee on the app's only user-authored data. The discipline of remembering `forget_undo()` at four scattered call sites is exactly the invariant that rots. It cannot support redo, cannot coalesce a drag, and cannot give the UI an enabled-state beyond `can_undo()`.
  - **proposed_qt6_design**: One `QUndoStack` per open recording, owned by a `LabelDocument` service (not a widget). `AddLabelCommand`, `RemoveLabelsCommand` (one command for an N-row delete, deleting the forget_undo hack), `SetGeometryCommand` with `mergeWith()`+id so a continuous grip drag coalesces into one undo, `SetNoteCommand`, `RenameCategoryCommand`, `RemoveCategoryCommand` (a macro over its labels). `stack.createUndoAction()`/`createRedoAction()` replace the hand-rolled Shift+B action (audian.py:4343-4350) and the manual `label_undow.setEnabled(self.labels.can_undo())` (databrowser.py:4417). `stack.isClean()`/`cleanChanged` replace the `LabelSet.dirty` flag driving the saved/unsaved text at databrowser.py:4397.
  - **effort**: large

  - **title**: Two parallel overlay stacks duplicate every mechanism, and the label module imports its vocabulary from the annotation module
  - **file**: src/audian/labeloverlay.py
  - **line**: 126
  - **evidence**: `labeloverlay.py` imports NAV_REGION_Z, SURFACE_NAVIGATOR, SURFACE_SPECTROGRAM, SURFACE_TRACE from `eventoverlay`, and does z arithmetic against it (`LABEL_NAV_Z = NAV_REGION_Z + 15`, line 165). Beyond that the two are near-copies: `_passive()` is defined twice (eventoverlay.py:812, labeloverlay.py:203) and imported a third time by controlpanel.py:70 through its private name; both classes hold a `self._drawn` tuple and early-return when it matches (eventoverlay.py:1143-1146, labeloverlay.py:461/762); both connect sigRangeChanged+sigResized in __init__ (eventoverlay.py:947-951, labeloverlay.py:466-469); both drop `_drawn = None` when `plot.isVisible()` is False with near-identical 5-line comments (eventoverlay.py:1126-1131, labeloverlay.py:718-723); both grow item pools that are never shrunk; both carry an invalidate()/polish()/clear()/update_plot() quartet; both re-derive channels()/mean-channel handling. databrowser.py:3624 `attach_label_overlays` and :4422 `attach_annotation_overlays` are the same 20-line panel walk written twice.
  - **why_it_matters**: About 2800 lines carrying one design in two copies. Every fix to redraw-gating, DPI, z-order or passivity must be made twice and demonstrably has been -- the identical comments are the evidence. The cross-import also means the 'pure data / Qt half' split the modules claim in their docstrings is already broken at the top of labeloverlay.py.
  - **proposed_qt6_design**: Extract an `overlays/base.py`: a `SurfaceKind` enum + SURFACE_STYLE, the full z-order table, `passive(item)`, and an abstract `PlotOverlay` holding the `_drawn` gating, the visibility short-circuit, the viewbox signal wiring and the item pool. Make `PlotOverlay` a QObject parented to its plot (see the ownership finding) so EventOverlay and LabelOverlay become two subclasses over one lifecycle. The surface/z vocabulary lives there, so neither annotation system imports the other and controlpanel imports a public name.
  - **effort**: large

  - **title**: Overlays are plain Python objects holding Qt connections and a strong reference to the browser -- no Qt ownership, no teardown
  - **file**: src/audian/labeloverlay.py
  - **line**: 399
  - **evidence**: `class LabelOverlay:` and `class EventOverlay:` (eventoverlay.py:827) are not QObjects. Each connects two viewbox signals to its own bound methods in __init__ and NEVER disconnects: `grep disconnect` across both files returns only labeloverlay.py:602-603, which detaches the transient LabelEditor's own signals. `LabelOverlay` additionally stores `on_edit=self.label_edited` and `on_dropped=self.label_editor_dropped` (databrowser.py:3656-3658), bound methods of DataBrowser, so viewbox -> overlay -> DataBrowser is a strong chain Qt's parent-child destruction never reaches. Browsers are torn down by hand with `w.close(); del w` (audian.py:4863-4865, :4874-4875) inside `Audian.close()`, a method whose own comment (audian.py:4858) admits it SHADOWS `QWidget.close`.
  - **why_it_matters**: Nothing owns an overlay, so nothing destroys one. A closed tab leaves its DataBrowser reachable through 32+ overlay callbacks, holding the loaded recording, the LabelSet and the bundle -- a real leak in an app whose stated use case is 'long-running sessions' and 'potentially very large files'. It is also why exit-time persistence must be called by hand.
  - **proposed_qt6_design**: Make the overlay base a QObject parented to the PlotItem/ViewBox it draws on, so Qt destroys it with the plot and QObject::destroyed breaks the connections automatically. Replace the on_edit/on_dropped/on_change callback attributes with real Signals (`geometryEdited(...)`, `editorDropped()`) connected by the browser -- which also gives ConnectionType control and makes the graph inspectable. Remove the `close()` shadow and the `del w`.
  - **effort**: medium

  - **title**: Persistence of the only user-authored data depends on hand-placed flush calls; there is no closeEvent anywhere in the app
  - **file**: src/audian/databrowser.py
  - **line**: 3610
  - **evidence**: `flush_labels()`'s own docstring: 'There is no `closeEvent` anywhere in audian and `Audian.quit` never goes through Qt's close machinery at all, so both exit paths call this by hand. A queued zero-timer save would otherwise be dropped with the event loop, and the last label of a session is exactly the one a reader has not written down anywhere else.' Confirmed: `grep -rn closeEvent src/audian/` returns only these comments. The save is `QTimer.singleShot(0, self.save_labels)` guarded by a `label_save_pending` bool (databrowser.py:3576-3586); the two flush sites are audian.py:4861 and :4872. Anything ending the process without passing through those two methods loses the pending write.
  - **why_it_matters**: The save core is correct -- LabelSet.write (labels.py:663-711) is temp-file + fsync + os.replace, and is the ONLY atomic write in the tree. But correct durability behind an unreliable trigger is worse than it looks, because it reads as safe.
  - **proposed_qt6_design**: Put the sidecar behind a document service using `QSaveFile`, which is the Qt-native form of the temp+fsync+rename this file hand-rolls, and a real QTimer member instead of singleShot+bool. Implement `QWidget.closeEvent` on the browser to flush and, when `QUndoStack.isClean()` is False, offer save/discard/cancel. Connect the service's flush to `QCoreApplication.aboutToQuit` as the backstop, and stop shadowing `QWidget.close` in `Audian` (audian.py:4847).
  - **effort**: medium

  - **title**: Change notification is a hand-incremented int whose failure mode is an invisible missing repaint
  - **file**: src/audian/labels.py
  - **line**: 291
  - **evidence**: `LabelSet.revision` is incremented by hand in 12 places (labels.py:377, 391, 407, 424, 434, 445, 454, 466, 538, 588, 630, 654) and read into the overlay's redraw-gate tuple at labeloverlay.py:459. Both docstrings state the hazard outright -- labels.py:295 'a mutation that forgets to bump simply does not appear', labeloverlay.py:100 'a mutation that does not bump `LabelSet.revision` will not repaint.' `AnnotationLayer.revision` (eventoverlay.py:365) is a second independent counter with the same discipline. Redraw is then forced from outside by `redraw_labels()` walking every overlay calling invalidate()+update_plot() (databrowser.py:3665-3673), from 8 call sites.
  - **why_it_matters**: A change-notification system whose failure mode is invisible: a new mutation method that omits one line yields an app that looks correct until a lane repaints for an unrelated reason. It also cannot say WHAT changed, so every change costs a full re-window and redraw of all 32 lanes.
  - **proposed_qt6_design**: `LabelSet` becomes a QObject document emitting typed signals -- labelAdded(int), labelRemoved(int), labelChanged(int), categoriesChanged() -- that the overlays connect to. Keep a monotonic revision only as the cheap view-state gate, but derive it inside one private `_touch()` that bumps AND emits, so no mutation can do one without the other. Longer term make `LabelTableModel` the canonical QAbstractItemModel and have overlays observe its dataChanged/rowsInserted/rowsRemoved, removing the second notification system entirely.
  - **effort**: medium

  - **title**: Selection state is duplicated across three owners and reconciled by a manual identity scan
  - **file**: src/audian/databrowser.py
  - **line**: 3921
  - **evidence**: The selected label lives simultaneously in `DataBrowser.selected_label` + `DataBrowser.selected_overlay`, in `LabelOverlay.editor` (labeloverlay.py:429), and in `LabelEditor.label` (labeloverlay.py:288) -- all by object identity, which is why `LabelSet.index_of` must scan with `is` rather than value (labels.py:469-480). Keeping them in step requires: `revalidate_selection()` (databrowser.py:3921-3959) scanning three separate invalidation causes; a reverse callback on_dropped -> `label_editor_dropped` (databrowser.py:3913) for when the overlay notices first; `_sync_editor`'s own `store.index_of(...) < 0` check (labeloverlay.py:692); and `start_editing` returning a bool the caller MUST act on because 'a selection whose grips were never built is the one way the two can disagree' (labeloverlay.py:571). `deselect_label()` is called from 8 sites.
  - **why_it_matters**: Three copies of one fact, reconciled by a scan that must be invoked manually after every operation that could invalidate it (category removal, table delete, F9, hiding a lane, turning spectrograms off, opening a file). tests/test_labels.py spends six tests pinning exactly these reconciliation paths -- the signature of accidental complexity.
  - **proposed_qt6_design**: One `QItemSelectionModel` shared between the label table view and the overlays, over the single QAbstractItemModel fronting LabelSet. Selection then addresses rows by `QPersistentModelIndex`, which Qt keeps valid across insertions and invalidates automatically on removal -- deleting revalidate_selection, on_dropped, the identity scan in index_of, and the bool return from start_editing. Overlays become selection observers; grips are built and torn down in one selectionChanged slot.
  - **effort**: large

  - **title**: Domain decisions live in the god-object browser and are reached from the overlay through raw Python callbacks
  - **file**: src/audian/labeloverlay.py
  - **line**: 421
  - **evidence**: `LabelOverlay.on_edit` is documented as 'A callback rather than a reference to the browser, for the reason this module exists: nothing here decides what a label means' -- but the callback IS `DataBrowser.label_edited` (databrowser.py:4014-4059), so the indirection buys a type-check, not decoupling. DataBrowser is ~8000 lines and owns the sidecar path (:3686), read/save/flush (:3697/:3595/:3610), the save debounce (:3576), the settings round-trip (:3473-3523), category key binding (:3729), all six dialog lifecycles, the selection, `fit_into()` -- pure geometry about clamping a dragged box back inside the recording (:3975-4013, a @staticmethod on a QWidget) -- and `describe_label()` (:4224). `store_label` (:4121) and `label_edited` each hand-run the same 5-step sequence (mutate, redraw_labels, refresh_label_table, schedule_label_save, update_label_status) with a comment at :4016 warning what breaks if a step is missed.
  - **why_it_matters**: A five-step sequence repeated at four call sites with a comment explaining the failure of each omission is a missing transaction boundary. Nothing about label semantics is testable without constructing a DataBrowser, and `fit_into` -- pure arithmetic with a measured contract -- is only reachable through a widget.
  - **proposed_qt6_design**: A `LabelDocumentService` in Application Services owning the LabelSet, the QUndoStack, the sidecar I/O and the debounce timer. Commands pushed onto the stack perform the mutation and the document's signals drive redraw, table refresh and status -- so the five-step sequence collapses to one `stack.push(cmd)`. Move `fit_into`, `describe_label` and the bounds normalisation into labels.py (or labels/geometry.py) as free functions where they are unit-testable with no Qt at all; labels.py already proves that split works.
  - **effort**: large

  - **title**: CategoryStrip reimplements a flow layout by hand with setGeometry
  - **file**: src/audian/labeloverlay.py
  - **line**: 1244
  - **evidence**: About 190 lines of manual layout: `pack()` (1355-1379) computes placements over two fixed rows with a width reserve for the `+N` button, `relayout()` (1381-1433) calls `chip.setGeometry(x, row*pitch, w, CHIP_HEIGHT)` per chip, resizeEvent re-runs it, sizeHint/minimumSizeHint are overridden, setFixedHeight pins the height, and chips are destroyed and rebuilt wholesale in `set_categories` (1330-1352) with setParent(None)+deleteLater(). The class docstring justifies it: 'the chips are placed by hand rather than by a layout -- a layout would re-impose exactly the minimum this exists to avoid', citing a measurement where two chips took the window minimum from 1372px to 1572px.
  - **why_it_matters**: The underlying problem is real (a QHBoxLayout propagates its minimum width up and widens the whole window), but the fix is a custom QLayout, not bypassing layouts. As written the widget cannot participate in layout invalidation, is re-measured on every resize, rebuilds every QToolButton on any vocabulary change, and duplicates the fold logic against the QMenu built at 1414-1425.
  - **proposed_qt6_design**: A proper QLayout subclass implementing hasHeightForWidth()/heightForWidth() with minimumSize() returning only the fold button's width -- Qt's documented FlowLayout pattern, which gives exactly the 'take the width you are given, never ask for more' behaviour this hand-rolls. Alternatively make the strip a QToolBar with setMovable(False) and let Qt's built-in extension button do the fold. Either way drive chips from the category model with a delegate instead of destroying and recreating QToolButtons.
  - **effort**: medium

  - **title**: LabelTableModel resets the whole model on any external change; CategoryModel edits a disconnected copy
  - **file**: src/audian/labeloverlay.py
  - **line**: 1170
  - **evidence**: `LabelTableModel.refresh()` is `beginResetModel(); endResetModel()` and is called from `DataBrowser.refresh_label_table` (databrowser.py:4248) after every add, geometry edit, delete and undo -- so drawing one label on a lane resets an open list. Meanwhile `CategoryModel` (labeloverlay.py:868) holds `self.rows = list(store.categories)`, a snapshot pushed only on OK via store_rows(). The two sibling models in one file use opposite strategies, documented with opposing justifications at 869-873 and 1083-1087. LabelTableModel also indexes `self.store.labels[index.row()]` directly with no guard against a store mutated between signals.
  - **why_it_matters**: A full model reset destroys selection, scroll position and any open editor on every single label the reader draws -- visible as the list jumping while they work. The copy-vs-live split means the category dialog cannot show live label counts, and is the direct cause of the rename data loss above.
  - **proposed_qt6_design**: One live QAbstractItemModel per concept (labels, categories) over the document, emitting dataChanged/beginInsertRows/beginRemoveRows driven by the document's typed signals -- no refresh(), no reset. Dialog Cancel becomes QUndoStack.undo() of a macro (beginMacro/endMacro around the editing session), not a discarded snapshot. Add a QSortFilterProxyModel so sorting and filtering the label list is free.
  - **effort**: medium

  - **title**: The category vocabulary is a global user preference silently mutated by opening a file, and forked per tab
  - **file**: src/audian/labels.py
  - **line**: 626
  - **evidence**: `LabelSet.read` adds any category a loaded CSV names that the settings do not know (labels.py:645-648), and `DataBrowser.load_labels` then calls `save_label_settings()` (databrowser.py:3730), writing it into the user's global settings under `LABEL_SETTING = 'labels'` (databrowser.py:1007). Each `DataBrowser.__init__` seeds its own `LabelSet(self.restore_label_categories())` (databrowser.py:1327) from that same global setting, but the copies are then INDEPENDENT -- two tabs can diverge and whichever saves last wins the settings file. The digit keys are rebound from this list on every vocabulary change (databrowser.py:3729-3741).
  - **why_it_matters**: Opening one recording permanently changes the reader's palette and 1-9 key bindings for every other recording, as a side effect of a file open they did not initiate. With multiple tabs open the last writer silently overwrites the others' vocabulary edits.
  - **proposed_qt6_design**: One application-scoped `CategoryRegistry` service (single instance, QSettings-backed, emitting categoriesChanged) that every document observes, so tabs cannot diverge and there is exactly one writer. Categories imported from a CSV should be staged and confirmed, or scoped to that document, rather than written into the global preference silently -- at minimum, report them through the existing notify path without auto-persisting.
  - **effort**: medium

  - **title**: activity.py is unrelated to annotations and is clustered here by filename alone
  - **file**: src/audian/activity.py
  - **line**: 1
  - **evidence**: 308 lines importing only `dataclasses` and `numpy`; zero Qt symbols. It computes per-bin RMS/peak excess in dB above a global baseline and a QUIET/SUSTAINED/TRANSIENT classification for the NAVIGATOR STRIP. Its consumers are compresseddata.py:18/187 (builds BinStats from the compressed sidecar) and fulltraceplot.py:30/792-799 (derives the overview and colours the strip). Nothing in labels.py, labeloverlay.py or eventoverlay.py references it, and it references nothing in them.
  - **why_it_matters**: Migration planning that treats it as annotation code will either schedule work that does not exist or block the annotations cluster on a module with no Qt5 surface at all. Its real coupling is to the navigator/compression cluster.
  - **proposed_qt6_design**: Move to Domain/Core unchanged (e.g. audian/core/activity.py) in the first mechanical pass; it needs no migration. Track it with compresseddata.py and fulltraceplot.py. Its `combine()` pyramid contract is the thing to keep under test -- it is the sole reason navigator zoom is cheap, and it is the natural unit to push onto a worker thread when the navigator is made async.
  - **effort**: small

- **behavior_contract**:
  - SIDECAR FORMAT: editable labels round-trip through `<recording stem>-editable-
  - labels.csv` with the exact header
  - `category,kind,channel,t_start_s,t_end_s,f_low_hz,f_high_hz,note`, times as 6-decimal
  - fixed-point seconds from the first frame of the FIRST file of a split recording,
  - frequencies as 3-decimal Hz, written with stdlib csv so a note containing a comma or a
  - quote survives an RFC 4180 round-trip.
  - EMPTY MEANS ABSENT: a point writes an empty `t_end_s`; a label drawn on a trace writes
  - empty `f_low_hz`/`f_high_hz`; a label drawn on the mean spectrogram writes an empty
  - `channel`. Never -1, never 0..Nyquist, never 0.
  - READ TOLERANCE: a missing sidecar reads as an empty set with no message. A row naming no
  - category or no start time is counted and dropped, never drawn at t=0. A backwards span
  - (t1<t0) and a backwards band (f1<f0) are swapped into order. A span row that lost its
  - end becomes a point. An unrecognised `kind` is inferred from whether t_end_s is present.
  - BLOCKED STORE: if the sidecar existed and did not come back whole (OSError,
  - UnicodeDecodeError, csv.Error, or ANY dropped row), `LabelSet.blocked` is set, `write()`
  - and `discard()` both refuse, and the parameter bar's File row leads with `READ-ONLY --
  - <reason>`. An unreadable sidecar must never be overwritten or deleted.
  - UNKNOWN CATEGORY IS ADOPTED: a category named by a loaded CSV that the settings do not
  - know is added with the lowest free palette index and reported to the reader; no row is
  - ever dropped for naming an unknown category.
  - ATOMIC WRITE: the sidecar is written to `<name>.tmp` in the SAME directory, flushed,
  - fsynced and os.replace'd into position. A failed write leaves the previous file whole
  - and leaves no .tmp behind, and the error reaches the reader in the UI, not a log.
  - EMPTY SET REMOVES THE FILE: saving a set whose last label was deleted unlinks the
  - sidecar rather than leaving a header-only file behind that would repopulate nothing at
  - the next open.
  - AUTOSAVE: every mutation schedules exactly one write for the end of the current turn of
  - the event loop (debounced), and a pending write is flushed when a tab closes or the
  - application quits, so the last label of a session reaches disk.
  - CREATE GESTURE: `b` enters label mode; a rubber-band drag over a lane writes a label of
  - the current category. On a spectrogram the box bounds time AND frequency; on a trace it
  - bounds time only. Dragging the box in either direction yields the identical stored band.
  - POINT PLACEMENT: a point category has no extent to drag, so its digit key places one at
  - the cross hair -- on a trace at the extreme sample of the pointer's own pixel column, on
  - a spectrogram at the exact (t, f). With the cross hair off the key only picks the
  - category and says how to place one; it never silently does nothing.
  - CATEGORY PICKING: `1`-`9` select the first nine categories; the chips in the parameter
  - bar show which key is which and act as the legend (a chip's colour is the colour that
  - category draws in). Categories past the ninth have no key and say so in their tool tip.
  - Chips that do not fit fold into a `+N` menu -- folded, never dropped -- and what is
  - shown is always a PREFIX of the vocabulary so visible chips stay in step with the digit
  - keys.
  - ONE-OFF LABELLING: in the ask/request region mode, a `Label as` submenu offers every
  - category and applies it to the dragged region without leaving the current mode.
  - DRAWING RULE (editable): a label is the ONLY mark in the application bounded in y. A
  - label carrying a band, drawn on a spectrogram, is drawn inside that band; every other
  - case is drawn full lane height. Outlines only -- labels are never filled.
  - DRAWING RULE (fixed): every fixed annotation occupies the FULL height of its lane and is
  - bounded only in x. No per-layer y allocation, no tracks, no sub-rows. A span is a weak
  - interior fill UNDER the trace plus two full-height edge lines OVER it; a point is a
  - full-height vertical at full opacity. On the spectrogram no interior fill is drawn at
  - all and the edges carry the extent alone.
  - PREDICTED vs OBSERVED: a predicted point draws at the same full height in the same hue
  - but dashed and with a hollow diamond cap; colour is never the difference. Caps are
  - dropped above 400 drawn points. The cap is inset from the top of the view box so it
  - renders as a diamond rather than a clipped chevron, and the inset stays the same pixel
  - count at any zoom.
  - UNVALIDATED FIT: an unvalidated alignment draws every line dashed and hatches every
  - fill, AND shows an `UNVALIDATED` badge. A bundle fitted against a different recording
  - draws NOTHING at all and badges `WRONG RECORDING`.
  - TREATMENT LETTERS: V/B/S chips at a span's start edge, from a fixed 24-item pool. A span
  - narrower than 14 device pixels is not labelled, and if more spans qualify than the pool
  - can seat, NOTHING is labelled -- never an arbitrary subset.
  - SELECTION: Ctrl+click in label mode picks the label under the pointer and grows grips on
  - it. The SMALLEST box under the pointer wins (measured as a fraction of the view, so time
  - and frequency extents are comparable); a point always wins over what it sits in; ties go
  - to the label added last. Ctrl+click on an empty lane drops the selection. Ctrl+click
  - does nothing outside label mode. A Ctrl+DRAG reaches for a label at the drag's centre
  - and never writes one.
  - GRIPS DO NOT BLOCK NEW BOXES: a drag starting inside the selected label's body still
  - starts a NEW box -- only the 12px grips are control. Shift+drag (play) and Alt+drag
  - (analyse) over the selected box still reach the lane and are never taken by the ROI as a
  - scale or a rotate.
  - GRIP GEOMETRY: a banded span on a spectrogram gets four corner grips plus a centre grip
  - (time and frequency); an unbanded span gets two vertical-edge grips plus a centre grip
  - and CANNOT move in y; a point gets the centre grip alone and never grows an end time. A
  - grip drag can never invert the box -- t1>=t0 and f1>=f0 always.
  - A LANE MAY ONLY CHANGE WHAT IT CAN SHOW: editing a trace-made (bandless) label on a
  - spectrogram must NOT give it a frequency band; editing a banded label on a trace must
  - preserve its existing band unchanged. Getting either wrong writes 0..Nyquist into rows
  - nobody claimed.
  - BOUNDS: a grip dragged past the recording's extent is put back -- a MOVE slides the
  - whole box preserving its width, a RESIZE clamps only the edge that was dragged. A
  - label's t_start_s can never be written negative.
  - NO-OP EDITS ARE NOT EDITS: a click on a grip that Qt promotes to a drag (five device
  - pixels of travel, or any travel after half a second) but that changes no geometry must
  - not rewrite the sidecar, must not consume the undo slot, and must leave the grips ON the
  - label. A purely vertical nudge on a trace is exactly this case.
  - RESYNC IS NOT AN EDIT: the grips follow the label when the view pans, zooms or the theme
  - changes, and none of that re-syncing reaches the write-back or the sidecar.
  - DELETE: Ctrl+Delete removes the selected label (plain Delete hides deselected channels
  - and Backspace zooms back, so neither is available). The label list (Ctrl+M) removes any
  - selected rows. Removing the selected label from anywhere takes the grips off.
  - UNDO: Shift+B takes back the LAST change -- one added, one removed, or one moved/resized
  - -- and reports which in words. It does nothing on a recording just opened; it must never
  - delete a label the reader never touched. Removing several rows at once is NOT one undo
  - (the slot is cleared rather than restoring one row of several). Removing a category
  - clears the undo.
  - SELECTION IS DROPPED WHEN NOBODY CAN ACT ON IT: hiding the labels (F9), leaving label
  - mode, Escape, hiding the lane the grips are on, turning the spectrograms off, or the
  - label leaving the store all deselect. A selection nobody can see must never be what the
  - next Ctrl+Delete acts on.
  - CATEGORY EDITOR (Ctrl+L): add, rename, re-kind and recolour categories. Removing a
  - category ALSO removes its labels and must ask first, naming the count. Recolouring a
  - category immediately repens both the drawn labels and the selected label's grips. NOTE:
  - renaming currently destroys the rows -- see architecture_problems; the migrated
  - behaviour must be the one labels.py:363 documents, which is that the rows are kept.
  - VOCABULARY PERSISTENCE: categories survive a restart via the settings file, versioned
  - and whole-value-or-nothing -- a value written under another version is dropped entirely,
  - never half-read. WHICH category is current is deliberately NOT saved. A malformed
  - settings entry is skipped, never defaulted.
  - VISIBILITY: F9 takes the editable labels off every lane AND off the navigator, and puts
  - them back. The annotation master toggle does the same for fixed annotations but is
  - deliberately NOT persisted across restarts, while per-layer and per-surface annotation
  - switches ARE (per bundle layer id, with an unknown layer keeping its own default_on).
  - NAVIGATOR: every navigator row draws the labels and annotations of ITS OWN channel -- a
  - row knows its channel and must not fall back to channel 0. Marks on the navigator draw
  - ABOVE the translucent window-selection region so they keep their true colour inside the
  - window the reader is working in. The navigator is read-only for labels: no grips are
  - ever built there.
  - MEAN SPECTROGRAM: shows the labels of EVERY channel it averages, and a label drawn on it
  - carries no channel and therefore draws on every lane; such a channelless label gets
  - exactly one editor across the whole window.
  - PERFORMANCE INVARIANTS: the windowing, decimation and span merge for one time range are
  - computed ONCE per browser and shared by all 32 lanes of a 16-channel stack; a hidden
  - lane is never redrawn; an unchanged view is not redrawn; 100k points in view draw a
  - bounded number of lines; spans are merged at one device pixel rather than decimated; no
  - pg.TextItem is ever constructed or removed on the draw path.
  - MOUSE PASSIVITY: no annotation or label item ever takes a mouse press or a hover -- a
  - rubber-band drag that starts on top of a mark, a filter cutoff handle, or an existing
  - box still reaches the view box and still makes a label.
  - STATUS: the parameter bar's File row states the label count, the sidecar name,
  - saved/unsaved, and which label is currently selected -- and leads with READ-ONLY or SAVE
  - FAILED when either applies. Empty states name the gesture that fixes them ('no labels
  - yet -- press b, then drag over a lane'). The Editable labels tab raises an alert when
  - the store is blocked or a save failed.
  - ACTIVITY OVERVIEW (activity.py, navigator only): per-bin rms_excess_db and
  - peak_excess_db are referenced to ONE global per-channel baseline (10th percentile of
  - per-bin RMS), never a per-bin baseline, so a quiet stretch stays visibly quiet.
  - classify() thresholds crest against noise_peak_db(n) so the QUIET/SUSTAINED/TRANSIENT
  - labelling stays stable as bins shrink under zoom. BinStats.combine(factor) must return
  - n/minimum/maximum bit-identical to binning the raw samples at the coarser size.
- **risk**: medium — the CSV format, the atomic-write and blocked-store rules, and the whole pure-data LabelSet port with almost no change, and the single largest unknown was verified rather than assumed: on PySide6 6.11.2 + pyqtgraph 0.14.0 the measured pg.ROI contract that the entire label-editing gesture rests on still holds (movable/rotatable/resizable/invertible all False, handles expose pen/hoverPen/currentPen, and setPos/setSize with finish=False emit sigRegionChanged but not sigRegionChangeFinished). What keeps it above low is that ~88 tests in tests/test_labels.py plus ~60 in tests/test_eventoverlay.py pin measured pixel-, contrast- and signal-count-level behaviour that any restructuring of the overlay lifecycle, the selection model or the undo mechanism must reproduce exactly; and that two of the three hard defects (QVariant, Qt.NoItemFlag) sit in code paths — model null returns and invalid-index flags — that only fire under specific view interactions, so a port that merely imports cleanly is not evidence of correctness.
- **notes**: FILE LIST CORRECTION: `src/audian/markerdata.py` does not exist. It was deleted in commit b52a5e1 ('Let the reader draw their own labels, beside the ones the log made'), which introduced labels.py + labeloverlay.py as its replacement. Nothing imports it. `tests/test_joinmarkers.py` concerns recording-file join markers, not markerdata.  CLUSTER BOUNDARY CORRECTION: `activity.py` has zero Qt and zero coupling to the other three files. Its consumers are compresseddata.py and fulltraceplot.py. It belongs with the navigator/compression cluster; scheduling it as annotations work will mislead the plan.  VERIFIED IN-SESSION (not inferred), using the repo's own .venv-qt6 (PySide6 6.11.2, pyqtgraph 0.14.0) and .venv (PyQt5): - `PySide6.QtCore.QVariant` does not exist -> labeloverlay.py:107 fails at import time. - `Qt.NoItemFlag` exists in NEITHER PyQt5 nor PySide6 (the correct spelling is `NoItemFlags`) -> labeloverlay.py:918 and :1135 are pre-existing latent AttributeErrors, not migration damage. - `from PyQt5.QtCore import Signal` always raises -> the try/except at eventoverlay.py:76-79 has a dead try branch; the identical dead shim is copy-pasted into selectviewbox.py:5-7 and spectrogramplot.py:9-11, while buffereddata.py:8 and fulltraceplot.py:26 import pyqtSignal directly. - Every OTHER unscoped enum in the cluster (Qt.NoButton, Qt.NoPen, Qt.DashLine, Qt.transparent, Qt.BDiagPattern, Qt.WA_DeleteOnClose, Qt.NonModal, Qt.ToolButtonTextBesideIcon, QIcon.Normal/Off, QMessageBox.Cancel, QDialogButtonBox.Ok/DestructiveRole, QAbstractItemView.SelectRows/ExtendedSelection, QSizePolicy.Ignored, QToolButton.InstantPopup, QPainter.Antialiasing) still resolves through PySide6's forgiving aliases. The port will RUN before it is correct — do not use "it imports and launches" as the migration gate. - `QIcon(PythonQIconEngineSubclass)` with no retained Python reference survives gc on 6.11.2 (labeloverlay.py:825); still worth caching. - `QPixmap(w,h)` defaults to devicePixelRatio 1.0 — eventoverlay.py:1289/1321/1359 legend icons will be blurry under Qt6's mandatory high-DPI scaling, while labeloverlay's QIconEngine swatches stay sharp. Fixing it also makes the two legend systems consistent. - CATEGORY RENAME DATA LOSS reproduced directly: a LabelSet with 2 labels under 'event' -> `CategoryModel.setData(index(0,0),'call')` -> `store_rows()` -> `labels: 0`. No confirmation on that path, and remove_category clears the undo slot. Highest-severity finding in the cluster, and it is present in the PyQt5 code today.  RECOMMENDED SEQUENCING: (1) fix the rename data loss and the two Qt.NoItemFlag sites on the PyQt5 side first, so the Qt6 branch does not carry a known data-loss bug across a rewrite; (2) mechanical binding port with fully scoped enums (the forgiving aliases make skipping tempting — they are a deprecation surface); (3) extract the shared PlotOverlay base and make overlays QObjects parented to their plots; (4) LabelDocumentService + QUndoStack + QSaveFile + closeEvent; (5) collapse selection onto one QItemSelectionModel over one model. Steps 4 and 5 are what qt6migration.md:649 explicitly asks for and are the two that most reduce line count.  TEST ASSETS: tests/test_labels.py (88 tests) and tests/test_eventoverlay.py (~60) are an unusually complete behavioural spec and should be ported to PySide6 BEFORE any restructuring — between them they already encode most of the behavior_contract above, including z-order, pixel-geometry, contrast-ratio and signal-count assertions.  CROSS-CLUSTER DEPENDENCIES the annotations work will touch: databrowser.py (owns all label/annotation orchestration, ~lines 3466-5075 and 7765-7830), audian.py (label actions and shortcuts at 4300-4360; exit-path flush at 4847-4876), theme.py (marker_color, annotation_color/pen/brush/letter, handle_pen, strip_pg_menus), controlpanel.py (imports eventoverlay._passive), fulltraceplot.py (navigator rows carry `.channel` for the overlays; activity consumer), session.py/layers.py/alignment.py (the immutable bundle model behind AnnotationLayer), windowing.py (window_points/window_spans/merge_spans behind the shared cache).
