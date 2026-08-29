# Recon: databrowser

- **cluster**: databrowser
- **purpose**: `src/audian/databrowser.py` (8253 lines) holds `DataBrowser`, the per-recording tab widget that is the application's central god object: ~240 methods spanning data loading, the channel stack's whole layout engine, viewport/range arithmetic, spectrogram/filter/envelope parameter control, audio playback and its cursor, two independent annotation systems (a read-only session bundle and a writable label sidecar), the parameter bar's widget construction, five hand-rolled versioned settings schemas, plugin/analyzer registration, and file export. Seven support classes live in the same file (`ParameterGroup`, `ParameterTabs`, `LogSlider`, `ColorMapCombo`, `LevelMeter`, `ChannelRailRow`, `SharedTimeAxis`). One `Audian` main window owns N `DataBrowser` tabs, hands each the *same* mutable `acts` QAction namespace and `save_path` list, and fans state changes out across tabs by reading sibling browsers' public attributes directly. Construction is two-phase: `__init__` (1138-1441) builds nothing but state and timers with `setEnabled(False)`; `open()` (1552-1850, 300 lines) builds every plot, overlay, action and the parameter bar and re-enables the widget.
- **public_surface**:
  - **name**: DataBrowser
  - **file**: src/audian/databrowser.py:979
  - **kind**: class
  - **base**: QWidget
  - **summary**: The central per-recording widget. ~240 methods, class body 979-8253; imported by audian.py:35 and by 7 test modules. Emits 10 signals (1127-1136), all consumed by Audian.load_data (audian.py:4768-4777). Constructed with (file_path, load_kwargs, plugins, channels, audio, acts, save_path, events_path).

  - **name**: ParameterGroup
  - **file**: src/audian/databrowser.py:229
  - **kind**: class
  - **base**: QWidget
  - **summary**: A framed grid of parameter rows with a shortcut registry; equalize() (365) freezes every group to the tallest one's frame height so a tab change cannot resize the lane stack. Imported by tests/test_parameterbar.py, tests/test_annotationpanel.py, tests/test_eventoverlay.py.

  - **name**: ParameterTabs
  - **file**: src/audian/databrowser.py:404
  - **kind**: class
  - **base**: QWidget
  - **summary**: QStackedLayout + exclusive QButtonGroup tab strip over the parameter groups; emits sigTabChanged(str). set_alert (535) appends '!' to a tab whose page is in a data-losing state. Imported by tests/test_parameterbar.py.

  - **name**: LogSlider
  - **file**: src/audian/databrowser.py:574
  - **kind**: class
  - **base**: QSlider
  - **summary**: 1000-step logarithmic Hz slider with an exact 0 Hz first step; used for the high-pass, low-pass and envelope cutoffs.

  - **name**: ColorMapCombo
  - **file**: src/audian/databrowser.py:634
  - **kind**: class
  - **base**: QComboBox
  - **summary**: Spectrogram colormap picker rendering each map as a gradient swatch; populate()/refresh_swatches rebuilds on a live theme switch because the two themes offer different map lists.

  - **name**: LevelMeter
  - **file**: src/audian/databrowser.py:665
  - **kind**: class
  - **base**: QWidget
  - **summary**: 3 px peak-level bar, -60 dBFS floor, one per rail row; custom paintEvent (700). Imported by tests/test_theme.py:442.

  - **name**: ChannelRailRow
  - **file**: src/audian/databrowser.py:721
  - **kind**: class
  - **base**: QWidget
  - **summary**: One rail row: number badge, solo/mute toggles, LevelMeter, fold-out electrode-name QLineEdit. Calls back into the browser for solo/mute/maximise/reorder/select and writes browser.channel_names directly (857). Claims S and M via a ShortcutOverride event filter (918).

  - **name**: SharedTimeAxis
  - **file**: src/audian/databrowser.py:938
  - **kind**: class
  - **base**: TimeAxisItem
  - **summary**: The stack's one time axis, in a row below the last lane and outside the scroll area. Pulls the lanes' start-time mode back through a mode_source callback at tickStrings time (974) rather than being told.

  - **name**: RecordingInfo
  - **file**: src/audian/databrowser.py:163
  - **kind**: class
  - **base**: NamedTuple
  - **summary**: (samplerate, frames, channels) of the whole split recording as the loader has it, handed to SessionMeta.check_recording so a 4-file recording is not checked against one file's header.

  - **name**: ANNOTATION_SURFACE_TIPS
  - **file**: src/audian/databrowser.py:117
  - **kind**: constant
  - **base**: 
  - **summary**: Surface -> tooltip mapping; imported by audian.py:35 for the Annotations menu.

  - **name**: ANNOTATION_CHIP_ROWS
  - **file**: src/audian/databrowser.py:141
  - **kind**: constant
  - **base**: 
  - **summary**: The two captioned chip rows ('Sent'/'Heard') of the Fixed-labels group, as (caption, tip, tracks); last row's empty frozenset is the catch-all. Imported by tests/test_annotationpanel.py.

  - **name**: annotation_chip_row
  - **file**: src/audian/databrowser.py:190
  - **kind**: function
  - **base**: 
  - **summary**: Which chip row a layer's track belongs on. Imported by tests/test_annotationpanel.py.

  - **name**: set_axis_style
  - **file**: src/audian/databrowser.py:106
  - **kind**: function
  - **base**: 
  - **summary**: AxisItem.setStyle guarded by a comparison against axis.style; the guard exists because setStyle unconditionally drops the cached picture, re-measures and repaints (0.28 ms per axis per layout pass).

  - **name**: gap_text
  - **file**: src/audian/databrowser.py:178
  - **kind**: function
  - **base**: 
  - **summary**: Signed time delta rendered in ms below 1 s, seconds above; used for join gaps and the annotation pointer readout.

  - **name**: colormap_icon
  - **file**: src/audian/databrowser.py:616
  - **kind**: function
  - **base**: 
  - **summary**: Renders a pyqtgraph colormap into a QPixmap gradient swatch, column by column, with no devicePixelRatio.

  - **name**: caption_label
  - **file**: src/audian/databrowser.py:210
  - **kind**: function
  - **base**: 
  - **summary**: Small-caps QLabel carrying a parameter name plus its keyboard shortcut.

  - **name**: frame_widget
  - **file**: src/audian/databrowser.py:205
  - **kind**: function
  - **base**: 
  - **summary**: One-line delegation to theme.frame().

- **qt5_api_usage**:
  - **file**: src/audian/databrowser.py
  - **line**: 15
  - **api**: try: from PyQt5.QtCore import Signal / except ImportError: from PyQt5.QtCore import pyqtSignal as Signal — a binding shim hardcoded to PyQt5 either way. Repeated in selectviewbox.py:5, eventoverlay.py:77, spectrogramplot.py:9, timeplot.py:7, fulltraceplot.py:26.
  - **qt6_replacement**: One audian/qt.py compat module re-exporting Signal/Slot/Qt/QtWidgets from PySide6, the only file naming a binding; every other module imports from it.
  - **severity**: breaking

  - **file**: src/audian/databrowser.py
  - **line**: 27
  - **api**: from PyQt5.QtWidgets import QAction, QMenu, QComboBox
  - **qt6_replacement**: QAction moved to QtGui in Qt6: from PySide6.QtGui import QAction. Affects the 5 QAction constructions at 1571, 1579, 1954, 1966, 3732 and addAction/removeAction at 1957, 1970, 3727, 3738.
  - **severity**: breaking

  - **file**: src/audian/databrowser.py
  - **line**: 3431
  - **api**: (evt[0].button() & Qt.LeftButton) > 0 — a flag value compared with '>' against an int. Also 3445, 3459, 3463.
  - **qt6_replacement**: PySide6's Qt.MouseButton is an enum.Flag; Flag > int raises TypeError. Use evt[0].button() == Qt.MouseButton.LeftButton or bool(evt[0].button() & Qt.MouseButton.LeftButton).
  - **severity**: breaking

  - **file**: src/audian/databrowser.py
  - **line**: 1442
  - **api**: def __del__(self): self.close() — a Python finalizer on a QWidget that touches self.datafig and self.data.
  - **qt6_replacement**: Drop __del__. Under PySide6 the C++ object is often already destroyed when __del__ runs and any Qt call raises RuntimeError: Internal C++ object already deleted. Teardown belongs in an explicit shutdown() called from Audian.close/quit (audian.py:4861-4875), which already calls flush_labels() by hand.
  - **severity**: breaking

  - **file**: src/audian/databrowser.py
  - **line**: 3184
  - **api**: def close(self): — shadows QWidget.close() (returns bool, posts a QCloseEvent). Audian.close (audian.py:4849) shadows QMainWindow.close for the same reason and documents it at audian.py:4859.
  - **qt6_replacement**: Rename to shutdown()/release(); implement a real closeEvent that calls flush_labels() so the exit path is Qt's rather than two hand-written ones.
  - **severity**: breaking

  - **file**: src/audian/databrowser.py
  - **line**: 902
  - **api**: self.drag_origin = event.pos(); event.pos().y() (909) in ChannelRailRow.mousePressEvent/mouseMoveEvent
  - **qt6_replacement**: QMouseEvent.pos() is deprecated in Qt6; use event.position().toPoint(). The drag threshold arithmetic at 909-913 must then work in float or convert.
  - **severity**: behavior-change

  - **file**: src/audian/databrowser.py
  - **line**: 921
  - **api**: event.type() == QEvent.ShortcutOverride; QEvent.Resize at 5821
  - **qt6_replacement**: QEvent.Type.ShortcutOverride / QEvent.Type.Resize (scoped).
  - **severity**: behavior-change

  - **file**: src/audian/databrowser.py
  - **line**: 922
  - **api**: event.key() in (Qt.Key_S, Qt.Key_M); event.modifiers() == Qt.NoModifier (923); Qt.Key_S/Key_M again at 930/932
  - **qt6_replacement**: Qt.Key.Key_S / Qt.Key.Key_M / Qt.KeyboardModifier.NoModifier. PySide6 forgiving mode tolerates the short names; explicit scoping is what makes the file binding-agnostic.
  - **severity**: behavior-change

  - **file**: src/audian/databrowser.py
  - **line**: 295
  - **api**: QSizePolicy.Expanding / MinimumExpanding / Ignored / Preferred / Fixed / Minimum — 295, 305-307, 469, 470, 687, 1933, 2000, 2076, 2426, 4326, 5400
  - **qt6_replacement**: QSizePolicy.Policy.*. WIDE_POLICIES (304-308) is compared against sizePolicy().horizontalPolicy(), which returns a Policy member, so the tuple must hold Policy members not ints.
  - **severity**: behavior-change

  - **file**: src/audian/databrowser.py
  - **line**: 628
  - **api**: painter.setBrush(Qt.NoBrush) (628), painter.setPen(Qt.NoPen) (703), painter.setRenderHint(QPainter.Antialiasing, False) (702)
  - **qt6_replacement**: Qt.BrushStyle.NoBrush, Qt.PenStyle.NoPen, QPainter.RenderHint.Antialiasing.
  - **severity**: behavior-change

  - **file**: src/audian/databrowser.py
  - **line**: 616
  - **api**: colormap_icon: QPixmap(width, height) filled and painted column-by-column with no devicePixelRatio; same pattern feeds chip icons at 5677 (setIconSize(QSize(LEGEND_W, LEGEND_H)) with a comment that scaling by one pixel loses the hairline).
  - **qt6_replacement**: Build the pixmap at widget.devicePixelRatioF() scale and call setDevicePixelRatio; Qt6 defaults to per-screen high-DPI scaling, so the 64x12 pixmap is upscaled and the hairline the comment protects is lost anyway.
  - **severity**: cosmetic

  - **file**: src/audian/databrowser.py
  - **line**: 744
  - **api**: Qt.StrongFocus (744), Qt.NoFocus (487, 849)
  - **qt6_replacement**: Qt.FocusPolicy.StrongFocus / Qt.FocusPolicy.NoFocus.
  - **severity**: cosmetic

  - **file**: src/audian/databrowser.py
  - **line**: 748
  - **api**: setAttribute(Qt.WA_StyledBackground, True) (748, 766); setAttribute(Qt.WA_DeleteOnClose) (3229, 8103)
  - **qt6_replacement**: Qt.WidgetAttribute.*. WA_DeleteOnClose needs extra care under PySide6: the Python wrapper outlives the C++ object, so self.label_dialog.raise_() (3781) and self.label_table_dialog.model.refresh() (4244) can hit a deleted object if the finished lambda has not run.
  - **severity**: breaking

  - **file**: src/audian/databrowser.py
  - **line**: 787
  - **api**: Qt.AlignHCenter | Qt.AlignVCenter (787), Qt.AlignCenter (5315)
  - **qt6_replacement**: Qt.AlignmentFlag.*.
  - **severity**: cosmetic

  - **file**: src/audian/databrowser.py
  - **line**: 1679
  - **api**: border.setAcceptedMouseButtons(Qt.NoButton)
  - **qt6_replacement**: Qt.MouseButton.NoButton.
  - **severity**: cosmetic

  - **file**: src/audian/databrowser.py
  - **line**: 1896
  - **api**: QSplitter(Qt.Vertical, self) (1896); QSlider(Qt.Horizontal, parent) (585, 2186)
  - **qt6_replacement**: Qt.Orientation.Vertical / Qt.Orientation.Horizontal.
  - **severity**: cosmetic

  - **file**: src/audian/databrowser.py
  - **line**: 1916
  - **api**: self.stack_area.setFrameShape(QFrame.NoFrame) (1916); setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff) (1917)
  - **qt6_replacement**: QFrame.Shape.NoFrame / Qt.ScrollBarPolicy.ScrollBarAlwaysOff.
  - **severity**: cosmetic

  - **file**: src/audian/databrowser.py
  - **line**: 1968
  - **api**: act.setShortcutContext(Qt.WindowShortcut) (1968, 3734)
  - **qt6_replacement**: Qt.ShortcutContext.WindowShortcut. Also verify the 1-9 digit shortcuts (3733) still win against the parameter bar's focusable widgets under Qt6's changed shortcut arbitration.
  - **severity**: behavior-change

  - **file**: src/audian/databrowser.py
  - **line**: 2190
  - **api**: self.ofracw.setTickPosition(QSlider.TicksBelow)
  - **qt6_replacement**: QSlider.TickPosition.TicksBelow.
  - **severity**: cosmetic

  - **file**: src/audian/databrowser.py
  - **line**: 2409
  - **api**: setToolButtonStyle(Qt.ToolButtonTextOnly) (2409), Qt.ToolButtonTextBesideIcon (5675)
  - **qt6_replacement**: Qt.ToolButtonStyle.*.
  - **severity**: cosmetic

  - **file**: src/audian/databrowser.py
  - **line**: 2447
  - **api**: spin.setButtonSymbols(QAbstractSpinBox.NoButtons) applied to a pg.SpinBox
  - **qt6_replacement**: QAbstractSpinBox.ButtonSymbols.NoButtons; also depends on pyqtgraph's own Qt6 support for SpinBox.
  - **severity**: cosmetic

  - **file**: src/audian/databrowser.py
  - **line**: 3228
  - **api**: dialog.setWindowModality(Qt.NonModal) (3228, 8102); label.setTextInteractionFlags(Qt.TextSelectableByMouse) (3234)
  - **qt6_replacement**: Qt.WindowModality.NonModal / Qt.TextInteractionFlag.TextSelectableByMouse.
  - **severity**: cosmetic

  - **file**: src/audian/databrowser.py
  - **line**: 3238
  - **api**: QDialogButtonBox(QDialogButtonBox.Close, dialog) (3238); QDialogButtonBox.Close|Save|Reset and buttons.button(QDialogButtonBox.Reset/Save) (8117, 8121, 8122)
  - **qt6_replacement**: QDialogButtonBox.StandardButton.*.
  - **severity**: cosmetic

  - **file**: src/audian/databrowser.py
  - **line**: 3792
  - **api**: if result != QDialog.Accepted, inside the CategoryDialog finished handler
  - **qt6_replacement**: QDialog.DialogCode.Accepted. finished(int) still delivers an int, so only the name needs scoping.
  - **severity**: cosmetic

  - **file**: src/audian/databrowser.py
  - **line**: 3432
  - **api**: evt[0].modifiers() & Qt.ControlModifier (3432, 4855, 7788), & Qt.ShiftModifier (904, 3446, 4855)
  - **qt6_replacement**: Qt.KeyboardModifier.*. These are wrapped in bool()/if so they survive the Flag change; only the '> 0' comparisons at 3431/3445/3459/3463 actually break.
  - **severity**: cosmetic

  - **file**: src/audian/databrowser.py
  - **line**: 4415
  - **api**: metrics.elidedText(text, Qt.ElideRight, ...) (4415, 5295, 5605), Qt.ElideMiddle (5736)
  - **qt6_replacement**: Qt.TextElideMode.ElideRight / ElideMiddle. The metrics come from theme.mono_metrics(); QFontMetrics.horizontalAdvance is already used (785) rather than the removed width().
  - **severity**: cosmetic

  - **file**: src/audian/databrowser.py
  - **line**: 7816
  - **api**: act = menu.exec(self.region_menu_pos(vbox, scene_pos)) — already the Qt6 spelling, not exec_(). No exec_ anywhere in this file.
  - **qt6_replacement**: No change needed; note as already-migrated so the port does not regress it.
  - **severity**: cosmetic

  - **file**: src/audian/databrowser.py
  - **line**: 8059
  - **api**: QApplication.setOverrideCursor(Qt.WaitCursor) / restoreOverrideCursor (8067)
  - **qt6_replacement**: Qt.CursorShape.WaitCursor. Separately, the whole analyze_region body between them runs synchronously on the GUI thread.
  - **severity**: cosmetic

  - **file**: src/audian/databrowser.py
  - **line**: 1448
  - **api**: QSettings('audian','audian').value('spectrogram/colormap', default) (1451) and .setValue(...) (7172) — a second persistence backend beside audian.settings()/save_setting used by this file's five versioned schemas.
  - **qt6_replacement**: Qt6 QSettings.value() still returns a platform-dependent type and the int() guard at 1450-1453 handles it; the migration decision is to collapse onto one store, not keep both.
  - **severity**: behavior-change

  - **file**: src/audian/databrowser.py
  - **line**: 202
  - **api**: pg.setConfigOption('useNumba', True) executed at module import time; theme.py:2470 calls pg.setConfigOptions as well.
  - **qt6_replacement**: pyqtgraph picks its binding from the first Qt module already imported (or PYQTGRAPH_QT_LIB / QT_API). Set the binding in one entry point before any pg import and move this config call out of module scope.
  - **severity**: breaking

  - **file**: src/audian/databrowser.py
  - **line**: 3329
  - **api**: pixel_pos = evt[0]; pixel_pos.setX(pixel_pos.x() + 1); pixel_pos.setY(...) — mutates the QPointF delivered by pg.SignalProxy in place to probe one device pixel of view space.
  - **qt6_replacement**: Copy first (QPointF(evt[0])) or map through viewPixelSize(). PySide6 and PyQt differ in whether a signal argument is a fresh wrapper; mouse_clicked also re-enters mouse_moved with a synthesised tuple at 3436/3456.
  - **severity**: behavior-change

  - **file**: src/audian/databrowser.py
  - **line**: 3590
  - **api**: QTimer.singleShot(0, self.<bound method>) at 3590 (save_labels), 4361 / 5507 / 7932 (equalize_parameter_bar), 5031 (save_annotation_settings), 6103 (show_focused_lane), 6892 (align_time_axis)
  - **qt6_replacement**: Under PySide6 a queued bound method on a destroyed widget raises RuntimeError instead of silently no-op'ing. Use owned single-shot QTimer members (so they die with the widget) or guard with shiboken6.isValid(self). flush_labels (3610) exists precisely because the 3590 timer can be dropped with the event loop.
  - **severity**: behavior-change

  - **file**: src/audian/databrowser.py
  - **line**: 1715
  - **api**: axt.getViewBox().sigResized.connect(lambda *a: self.align_time_axis()) — one self-capturing lambda per trace panel per channel (16+ on the array file); plus fig.sigDeviceRangeChanged.connect(self.update_borders) (1682) and two pg.SignalProxy per channel (1754, 1760).
  - **qt6_replacement**: Lambdas capturing self create reference cycles PySide6's GC handles differently from PyQt5's, and each fires align_time_axis on every layout pass. Route through the one owned coalescing timer (schedule_axis_alignment, 6873) and connect bound slots so disconnection is possible.
  - **severity**: behavior-change

  - **file**: src/audian/databrowser.py
  - **line**: 2033
  - **api**: taxis.picture = None; taxis.update() — poking pyqtgraph AxisItem's private paint cache. SharedTimeAxis.resizeEvent (957) calls pg.AxisItem.resizeEvent(self, ev) explicitly to skip TimeAxisItem's override; lane_starttime_mode (2047) reads a private _starttime_mode off a lane's axis.
  - **qt6_replacement**: Give TimeAxisItem a public invalidate() and a real setter/getter for the start-time mode so no caller reaches into picture or _starttime_mode. pyqtgraph internals are the least stable part of the port.
  - **severity**: behavior-change

  - **file**: src/audian/databrowser.py
  - **line**: 1912
  - **api**: self.scrollable_stack = 'wheelEvent' in SelectViewBox.__dict__ — whether the stack scrolls depends on whether another class happens to define a method.
  - **qt6_replacement**: An explicit capability constant or ABC on SelectViewBox. Qt6's wheel delivery to QGraphicsScene differs enough that this probe must be re-validated rather than carried over.
  - **severity**: behavior-change

  - **file**: src/audian/databrowser.py
  - **line**: 1826
  - **api**: theme.collect_orphan_widgets() — sweeps the parentless control forms pyqtgraph leaves behind, one per PlotItem, onto a hidden holder.
  - **qt6_replacement**: Verify against the pyqtgraph version chosen for Qt6; if those orphan forms are gone or differently parented this becomes dead code that could hide real widgets.
  - **severity**: behavior-change

  - **file**: src/audian/databrowser.py
  - **line**: 6216
  - **api**: _, top, _, bottom = layout.getContentsMargins() on a QGraphicsGridLayout (lane_content_height)
  - **qt6_replacement**: Returns a 4-tuple in both bindings; no code change, but the surrounding pixel arithmetic (6195-6222) must be re-measured on Qt6 because PLOT_FRAME_HEIGHT and hidden-item row costs are empirical constants.
  - **severity**: cosmetic

- **architecture_problems**:
  - **title**: DataBrowser is a 7100-line god object with ~240 methods over 17 unrelated responsibilities
  - **file**: src/audian/databrowser.py
  - **line**: 979
  - **evidence**: Class body 979-8253. Buckets in one class: data/trace registry (1506-1551, 7086), plugin+analyzer registry (1518-1534, 8058-8169), widget construction (1886-2435, 4246-4336, 5299-5510), layout solver (5852-6962), viewport/range arithmetic (6963-7085, 3061-3159), spectrogram control (2450-2583, 6572-6664, 7086-7178), filter/envelope (7179-7302), channel selection (2652-3060, 7303-7543), panel modes (7544-7729), pointer/crosshair (3243-3472), region gestures (7730-7839), editable labels (3473-4421), fixed annotations (4422-5781), parameter-bar tabs (5511-5644), playback (7840-8057), export (8134-8250), settings persistence (1446, 3473-3527, 4951-5064, 5511-5586, 6530-6692).
  - **why_it_matters**: Every Qt6 change touches this file and there is no seam at which one can be tested in isolation: annotation provenance checks, panel-split pixel algebra and QMouseEvent handling all share one `self`. It is why 7 test modules must construct a full DataBrowser to test a chip row or a split ratio.
  - **proposed_qt6_design**: Split into a RecordingSession model (data, plot_ranges, panels, labels, annotations — no QWidget base), a ChannelStackView owning only figs/rail/taxis/splitter, a LaneLayoutSolver plain-Python class for 5852-6962 (already almost pure arithmetic), a LabelController and an AnnotationController (natural boundaries already marked by the section comments at 3467 and 4420), a PlaybackController, a SettingsStore for the five schemas, and a thin DataBrowser façade that wires them and keeps the 10 signals for audian.py.
  - **effort**: large

  - **title**: close() shadows QWidget.close() and __del__ calls it
  - **file**: src/audian/databrowser.py
  - **line**: 3184
  - **evidence**: def close(self) at 3184 closes datafig and data and returns None, overriding QWidget.close() which returns bool and posts a QCloseEvent. __del__ (1442-1445) calls it. audian.py:4859 documents the same shadowing on the main window and works around the absence of any closeEvent by calling `w.flush_labels(); w.close()` by hand at 4861-4865 and 4872-4875; flush_labels' docstring (3611-3618) states 'There is no closeEvent anywhere in audian and Audian.quit never goes through Qt's close machinery at all'.
  - **why_it_matters**: Under PySide6 a __del__ that calls into a destroyed C++ object raises RuntimeError during teardown. And any Qt-internal call to close() (window-manager close, a tab close button) silently runs data teardown instead of widget close, or vice versa.
  - **proposed_qt6_design**: Rename to shutdown(), delete __del__, implement closeEvent that calls flush_labels() then shutdown(), and let Audian.close/quit go through Qt's close machinery — keeping the guarantee that the last label reaches disk without a hand-rolled second exit path.
  - **effort**: small

  - **title**: A per-tab widget permanently disables the main window's shared QAction namespace
  - **file**: src/audian/databrowser.py
  - **line**: 1851
  - **evidence**: disable_unused_range_actions (1851-1885) calls setEnabled(False)/setVisible(False) on 20 actions of self.acts, which audian.py:4707 and 4752 hand to every DataBrowser as the same object. Same pattern at 1836-1837 (analyze_region), 2162-2166 (filter keys), 2319-2322 (envelope keys), 7489/7501 (per-channel check state).
  - **why_it_matters**: Opening a recording without a spectrogram hides the frequency-zoom actions for every already-open tab and nothing in the file ever re-enables them. A correctness bug today, and harder to see once actions move to QtGui.
  - **proposed_qt6_design**: The main window owns the actions and asks the current browser for a capability set on tab change (browser.available_axes()); the browser never touches acts. Same for save_path, passed as a mutable one-element list and written at 8168/8231.
  - **effort**: medium

  - **title**: Five hand-rolled versioned settings schemas plus a second QSettings backend, all inside the widget
  - **file**: src/audian/databrowser.py
  - **line**: 4951
  - **evidence**: ANNOTATION_SETTING v1 (read 4951-4982, written 5033-5064), LABEL_SETTING v1 (3473-3527), PARAM_TAB_SETTING v2 (5511-5586), PANEL_SPLIT_SETTING v3 (6530-6571 + 6665-6692), SPEC_BAND_SETTING v2 (6572-6664) — each repeats the same isinstance/version/log.warning ladder. Separately the colormap uses QSettings('audian','audian') at 1448-1457 and 7172. Debouncing is hand-rolled twice: label_save_pending (3579-3591) and annotation_save_pending (5021-5032), both QTimer.singleShot(0, ...).
  - **why_it_matters**: Six copies of one policy, two stores, two ad-hoc debouncers, and save_setting rewrites the whole file per call (documented 5023-5025) from a widget that may be one of N tabs. The _spec_band_saved (1276) and _param_tab_saved (1382) guards exist only to stop tabs overwriting each other.
  - **proposed_qt6_design**: One SettingsStore object with get(key, version)/set(key, version, value) and one coalescing writer; browsers ask it and never import audian.save_setting. The version ladders become a schema table or a decorator.
  - **effort**: medium

  - **title**: Circular import between audian.py and databrowser.py, worked around by 10 function-local imports
  - **file**: src/audian/databrowser.py
  - **line**: 3476
  - **evidence**: `from .audian import settings`/`save_setting` inside method bodies at 3476, 3510, 4972, 5045, 5513, 5576, 6543, 6595, 6655, 6679 — each with a comment 'Imported here and not at the top of the file: audian.py imports this module, so a module level import would be a cycle' (e.g. 4967-4970, 6541-6543). audian.py:35 imports DataBrowser.
  - **why_it_matters**: The widget depends on the application module, which depends on the widget. Nothing can import DataBrowser without pulling in the whole application; the tests do it anyway and pay for it.
  - **proposed_qt6_design**: Extract settings()/save_setting into audian/settings.py with no Qt and no application import; both modules import it top-level. This removes 10 deferred imports and one direction of the cycle.
  - **effort**: small

  - **title**: ~35 optional widget handles as None-sentinel attributes, guarded at every use site
  - **file**: src/audian/databrowser.py
  - **line**: 1259
  - **evidence**: 1259-1288 declares audiofacw, audiosrcw, audioleftw, audiorightw, audiopairw, nfftw, ofracw, ofraclabelw, cmapw, fmaxw, fminw, ymodew, hpfw, lpfw, envfw, hpsliderw, lpsliderw, envsliderw, linkbandw; 1345-1352 the label widgets; 1387-1391 the annotation widgets. setup_parameter_bar re-assigns them or re-sets them to None in else-branches (2155-2166, 2325-2331). Guards: 2540, 2556, 4410, 7113, 7120. audian.py:3702-3717 reaches straight through with self.browser().hpfw.stepUp() and no guard at all. ymodew (1278) and toolbar (1258) are set to None and never used again.
  - **why_it_matters**: Widget existence encodes a data capability ('this recording has a filtered trace'), so a Qt6 widget-construction change silently alters control flow far away. The unguarded external reach-through at audian.py:3702 AttributeErrors on a recording with no filter.
  - **proposed_qt6_design**: Model capability explicitly (session.has_filter), build parameter groups from a declarative spec, and expose commands (browser.step_highpass(+1)) instead of widget handles. Delete the two dead attributes.
  - **effort**: medium

  - **title**: One boolean re-entrancy guard (self.setting) protects six unrelated subsystems, and leaking it freezes the app
  - **file**: src/audian/databrowser.py
  - **line**: 1460
  - **evidence**: updating() contextmanager (1460-1475) with the docstring 'leaking it silently freezes scrolling and zooming for the rest of the session'. Checked at the top of set_times (7023), set_ranges (7065), update_ranges (6970), set_resolution (7101), set_spectrogram_band (2523), update_filter (7186), update_envelope (7262), set_channels (7455), toggle_channel (7498). Three methods carry comments about a bug where an early return leaked the flag: 2493-2496, 7098-7100, 7256-7260.
  - **why_it_matters**: A single global mutex over range, resolution, band, filter and channel changes means any one re-entering blocks all the others, and the failure mode is a silently dead UI rather than an exception. Qt6's different emission ordering will move where re-entry happens.
  - **proposed_qt6_design**: Per-subsystem guards owned by the guarded object (PlotRanges absorbs its own echo), or a model layer whose setters are idempotent so echo suppression is unnecessary. same_range (6998) already shows the model can compare instead of guard.
  - **effort**: medium

  - **title**: The whole channel-stack layout is Python pixel arithmetic across ~30 methods, with deferred remeasure passes
  - **file**: src/audian/databrowser.py
  - **line**: 6723
  - **evidence**: adjust_layout (6723-6872, 150 lines) plus lane_geometry (6000), update_stretches (6046), lane_content_height (6195), default_spec_height (6223), panel_split_limits (6258), panel_split_rows (6274), fit_figure_layout (6345), lane_axes (5852), align_time_axis (6894), size_splitter (6935), time_axis_height (6105), split_spacers (6163), lane_fallback (6693). Correctness depends on measured constants (PLOT_FRAME_HEIGHT, SPECTROGRAM_MIN_HEIGHT, TICK_VALUES_MIN_HEIGHT, CHANNEL_DENSE_HEIGHT) and on Qt's clamp-on-resize behaviour, documented at 6347-6356. Three deferred-measurement hacks: schedule_axis_alignment (6873, 'One turn of the event loop'), show_focused_lane (6103, with a measured 4-way table at 2820-2835) and the ParameterGroup.equalize deferral (4361/5507/7932); apply_rail_width (2665) defers for the same reason (2671-2676).
  - **why_it_matters**: Every one of these numbers was measured against Qt5 layout activation and pyqtgraph's Qt5 GraphicsView. Qt6 changes DPI handling and activation timing, so 'one turn of the event loop is enough' is exactly what breaks — and it breaks as a wrong-looking picture, not an error.
  - **proposed_qt6_design**: Extract the arithmetic into a pure LaneLayoutSolver(viewport_h, n_channels, spec_channels, spec_scale, floors) -> rows with no Qt in it and property-test it (tests/test_panelsplitter.py already pokes browser.spec_scale directly, so half the harness exists). Keep one apply step that writes row minima; replace the three singleShot(0) remeasures with one widget-owned timer plus a LayoutRequest handler.
  - **effort**: large

  - **title**: getattr/hasattr duck typing is the module boundary — 60 sites
  - **file**: src/audian/databrowser.py
  - **line**: 1483
  - **evidence**: hasattr(window,'set_readout') (1483), hasattr(window,'notify') (1492), hasattr(window,'set_progress') (3169), hasattr(window,'sync_annotation_actions') (5719), hasattr(self.plot_ranges,'auto_fit') (3113) with a fallback to a different API, hasattr(SpectrogramPlot,'can_render') (5975) with an inline reimplementation as the else branch, hasattr(axt,'sigHoverValue') (1709), hasattr(self.datafig,'sigHoverTime') (1815), hasattr(self.datafig,'refresh_colors') (7236), hasattr(filtered,'request_update') (7229), hasattr(ax,'set_current') (2975), getattr(self,'taxis',None) (2022) on the class's own attribute.
  - **why_it_matters**: There is no declared interface between the browser and its window, plots, ranges or navigator, so a Qt6 port cannot tell which path is live. Two of them (3113, 5975) carry a whole second implementation in the else branch that nothing exercises.
  - **proposed_qt6_design**: Declare Protocols for MainWindowShell (set_readout/notify/set_progress/sync_annotation_actions), RangeModel and LanePlot; delete the fallback branches. getattr(self,'taxis',None) becomes a plain attribute (already initialised at 1416).
  - **effort**: medium

  - **title**: Children reach up into the browser and mutate its state directly
  - **file**: src/audian/databrowser.py
  - **line**: 857
  - **evidence**: ChannelRailRow.rename writes self.browser.channel_names[self.channel] = self.name.text() (857-858) and reads browser.current_channel/solo_channels/muted_channels in update_state (870-874). selectviewbox.py:56 writes browser.region_mode_override = mode. panelsplitter.py:165/216/221/229/239 calls panel_split_heights/drag_panel_split/finish_panel_split/reset_panel_split. spectrogramplot.py:231 reads browser.show_specs, :479 calls browser.visible_channels(), :232 connects to browser.update_filter. timeplot.py:156 reads browser.show_channels, :286 reads browser.y_mode/y_fixed, :290/:294 calls browser methods. analyzer.py:100 stores the browser and reaches browser.data/browser.panels.
  - **why_it_matters**: Nine modules depend on DataBrowser's attribute names, so any rename during the split-up is a nine-file change and the browser has no way to know which of its fields are load-bearing.
  - **proposed_qt6_design**: Children emit signals (ChannelRailRow.sigRenamed(int,str), SelectViewBox.sigModeOverride(int)) and the browser connects them; plots receive a narrow LaneContext (channels, y policy, filter cutoffs) rather than the whole browser.
  - **effort**: medium

  - **title**: Domain logic for two annotation systems, including provenance checking and CSV/bundle I/O, lives in the widget
  - **file**: src/audian/databrowser.py
  - **line**: 4657
  - **evidence**: check_recording_coverage (4657-4701) decides whether a session bundle may be drawn at all, from filenames off the loader; bundle_problems (4702-4730) re-runs the bundle's own frame check; residual_tip (4731-4760) formats fit residuals; recording_joins (4455-4474) and declared_join_gaps (4475-4492) reconcile loader facts against bundle claims; marks_in (5174-5279, 105 lines) computes per-span pulse counts with domain vocabulary (sent / not heard / heard / unexplained). On the writable side: labels_path (3528), load_labels (3540), save_labels (3592), store_label (4121), label_edited (4016), fit_into (3986), undo_last_label_change (4192).
  - **why_it_matters**: None of this needs a QWidget but all of it is only reachable through one. The most safety-critical logic in the product — the refusal to draw a bundle against the wrong subset of a split recording (4657) — is a method on a GUI class and can only be tested by constructing a GUI.
  - **proposed_qt6_design**: AnnotationController and LabelController as plain QObjects (signals only, no widget base) holding the bundle/LabelSet; the browser holds the overlays and subscribes. check_recording_coverage, bundle_problems, marks_in and fit_into become pure functions in alignment.py/labels.py.
  - **effort**: large

  - **title**: Audio DSP and file writing run synchronously on the GUI thread
  - **file**: src/audian/databrowser.py
  - **line**: 7992
  - **evidence**: play_region (7992-8041) slices the buffer, mixes to stereo, and in heterodyne mode builds a full-length sine, applies scipy sosfiltfilt and decimates (8020-8031) before self.audio.play(...). analyze_region (8058-8074) wraps a full analyzer run in setOverrideCursor/restoreOverrideCursor. save_region (8170-8250) does metadata deep-copy, marker filtering and write_data inline. apply_filter (7213-7240) is debounced by filter_timer because 'refiltering plus respectrogramming 16 channels costs about 1.5 s' (7186-7188).
  - **why_it_matters**: A 1.5 s freeze is already the documented reason the debounce timers exist; a wait cursor is the only concession. Qt6 changes none of it, and the debounce timers are the only concurrency design in the file.
  - **proposed_qt6_design**: Move the recompute paths behind a worker (QThreadPool/QRunnable, or the BufferedData request_update machinery apply_filter already prefers at 7229) and report through the window's set_progress slot that overview_timer already uses (3169).
  - **effort**: large

  - **title**: Two-phase construction: __init__ builds nothing, open() builds everything in 300 lines
  - **file**: src/audian/databrowser.py
  - **line**: 1552
  - **evidence**: __init__ (1138-1441) sets ~120 attributes, creates 6 QTimers, and ends with self.setEnabled(False) (1256). open(gui, unwrap, unwrap_clip, highpass_cutoff, lowpass_cutoff) (1552-1850) then loads data, builds trace/spectrogram QActions, sets up plot_ranges, builds every figure/plot/border/proxy per channel (1662-1766), the parameter bar, the navigator, all overlays and join markers, registers analyzers, and calls setEnabled(True) at 1828. audian.py:4715 calls it from QTimer.singleShot(100, self.load_data) with an exception handler that removes the tab (4726-4735).
  - **why_it_matters**: Between construction and open() the object is a live QWidget in a QTabWidget with half its invariants unmet, so every method needs a `self.show_channels is None` / `self.data is None` guard (5809, 5830, 6440, 6729). The 100 ms timer is load-order-by-luck.
  - **proposed_qt6_design**: Load the recording first (a plain Recording.open() that can fail before any widget exists), then construct the view from a valid model; the tab shows a placeholder while loading rather than a disabled half-built browser.
  - **effort**: large

  - **title**: Signals are untyped (object) and several emit `self`
  - **file**: src/audian/databrowser.py
  - **line**: 1127
  - **evidence**: sigRangesChanged = Signal(object, object) (1127); sigFilenameChanged = Signal(object, str) emitted as emit(self, fn) at 7029, 7041, 7594; sigTraceChanged = Signal(object, object, object) emitted with (self, checked, name) at 1542; sigAudioChanged = Signal(object, object, object) (1134). All ten are connected in one block at audian.py:4768-4777, and the receivers then loop over Audian.browsers comparing `b is not self.browser()` (audian.py:3141, 3294, 3332).
  - **why_it_matters**: PySide6 is stricter about signal signatures and about passing a QWidget through an `object` slot; and sending `self` means the receiver reaches back into the sender's attributes, so the signal is really a broadcast 'something changed, go look'.
  - **proposed_qt6_design**: Type the payloads (Signal(str, float, float) for ranges, Signal(str) for the filename) and stop sending self — the window already tracks the current browser. Cross-tab fan-out becomes an explicit LinkPolicy object instead of N `if b is not current` loops in audian.py.
  - **effort**: medium

  - **title**: blockSignals is the coordination primitive across the whole widget layer
  - **file**: src/audian/databrowser.py
  - **line**: 2559
  - **evidence**: blockSignals(True)/(blocked) pairs at 610-613 (LogSlider.set_hz), 652-663 (ColorMapCombo.populate), 1576-1578 and 1846-1848 (trace acts), 2559-2568 (set_band_widget), 2579-2582 (set_nfft_widget), 3685-3688 (set_labels_visible), 4869-4877 (apply_annotation_layers, on the AnnotationLayer QObject), 5006-5014 (restore_annotation_layers), 5686-5703 (update_annotation_chips), 7122-7126 (ofracw), 7167-7170 (cmapw), 7245-7249 (set_filter_widgets), 7294-7297 (envfw), 7943-7948 (audio pair), 7965-7970 (audio source); plus audian.py:3619 blocking a whole browser.
  - **why_it_matters**: Every 'push a value into a widget without echoing' is hand-written, and each is a place where a missing pair becomes an infinite loop or a lost update. Qt6 changes nothing here, but the port must preserve all 17 of them exactly.
  - **proposed_qt6_design**: A set_widget_value(widget, value) helper, or model/view binding where the widget observes a model property and the model suppresses no-op sets (the same_range idea at 6998 generalised).
  - **effort**: small

  - **title**: Non-modal dialog lifetimes managed by hand with WA_DeleteOnClose plus attribute nulling in lambdas
  - **file**: src/audian/databrowser.py
  - **line**: 3778
  - **evidence**: edit_label_categories (3778-3807) keeps self.label_dialog and nulls it in a nested finished closure; show_label_table (3808-3833) keeps self.label_table_dialog and nulls it via `lambda _r=0: setattr(self,'label_table_dialog',None)`; analysis_results (8092-8127) keeps self.analysis_table (a child of a WA_DeleteOnClose dialog) nulled via `dialog.finished.connect(lambda _: setattr(self,'analysis_table',None))`; show_metadata (3190-3242) sets WA_DeleteOnClose and keeps no reference at all.
  - **why_it_matters**: In PySide6 the Python wrapper survives C++ deletion, so self.label_dialog.raise_() (3781) or self.label_table_dialog.model.refresh() (4244) can hit a deleted object if finished has not fired (it is not emitted on every destruction path). show_metadata's dialog is kept alive only by its C++ parent.
  - **proposed_qt6_design**: One SingletonDialog helper keyed by name that owns the reference and connects `destroyed` (not `finished`) to clear it. Applies to all four sites.
  - **effort**: small

  - **title**: What is on screen is discovered by scanning widget visibility rather than held in a model
  - **file**: src/audian/databrowser.py
  - **line**: 3923
  - **evidence**: revalidate_selection (3923-3960) must test three different things to answer 'can the reader still see the selected label?': labels.index_of(...) < 0, plot.isVisible(), and overlay.channel() not in self.visible_channels() — with a comment (3948-3958) explaining that a QGraphicsItem inside a hidden widget still reports isVisible() True. panel_split_heights (6366-6391) measures the boundary off ax.geometry().height() rather than recomputing it; split_spacers (6163-6194) reads p.axs[channel].isVisible(); trace_plot/time_plot (5917-5949) scan panels for the first visible one.
  - **why_it_matters**: Truth about what is on screen is spread across QWidget.isVisible, QGraphicsItem.isVisible and the browser's own channel lists, which the file documents as disagreeing. Qt6's visibility propagation is the same, but any restructuring of the stack changes which of the three answers.
  - **proposed_qt6_design**: A LaneVisibility model that is the single source of truth (visible_channels() already nearly is); widgets are told, never asked. revalidate_selection then asks one question.
  - **effort**: medium

  - **title**: spectrogram_channels() silently collapses the spectrogram onto one lane based on window height
  - **file**: src/audian/databrowser.py
  - **line**: 5961
  - **evidence**: spectrogram_channels (5961-5999) returns [focus] instead of every channel when SpectrogramPlot.can_render(self.height()/len(channels)) is false; focus_channel (2762-2791) re-runs the whole layout when that set changes; adjust_layout warns once through spec_warned (6739-6741); lane_fallback (6693-6722) exists as an assertion that no lane ends up empty as a consequence.
  - **why_it_matters**: A layout decision (row height) silently changes what data the user sees, and the decision reads self.height() — a widget geometry that is wrong during construction and after every Qt6 DPI change. The three-method dance (spectrogram_channels / lane_fallback / spec_warned) is the scar tissue.
  - **proposed_qt6_design**: Make the collapse an explicit, user-visible mode with a stated rule in the solver rather than a side effect of a height comparison; the solver returns a layout plan the view renders, and the plan names the collapse so the status bar and tests can assert on it.
  - **effort**: medium

- **behavior_contract**:
  - Opening N files: each recording gets its own tab; a split recording (several files
  - opened into one buffer) shows as one tab with one continuous time axis, and loader
  - warnings reach the status bar (databrowser.py:1560-1566).
  - The channel stack draws one lane per visible channel, every lane exactly the same
  - integer height, with the remainder pixels absorbed by a single spacer row at the bottom
  - — the lane pitch must not wobble between rows (update_stretches, 6046-6104).
  - Exactly one shared time axis sits below the last lane, outside the scroll area, and
  - stays on screen when the lanes do not all fit; its left/right margins line up with the
  - lane view boxes to the pixel (align_time_axis, 6894-6934), and the control panel gets
  - the same margins.
  - When the lanes outgrow the viewport the stack scrolls, a plain wheel scrolls it (rather
  - than zooming a view box), and the focused lane is scrolled into view with the least
  - movement possible (show_focused_lane, 2792-2854).
  - The channel rail (F7) shows number, solo, mute and a peak-level bar per lane; the
  - electrode-name field folds out only on the selected channel and only when its lane is
  - tall enough. The rail is forced off screen while the mean spectrogram is on, and F7
  - still flips the preference and says so (rail_shown 2652, toggle_rail 2687).
  - The current channel is marked three ways at once: a 1 px primary frame on its figure, a
  - raised view-box background and a bold caption, plus a 2 px rule down its rail row — and
  - no lane is marked while the mean spectrogram is showing (update_current_plot 2954,
  - update_borders 5782).
  - Clicking anywhere in a lane focuses that channel; Shift+click extends the selection;
  - clicking inside the already-current lane relayouts nothing (mouse_clicked 3443-3450).
  - S and M on a focused rail row solo and mute; double-click maximises; dragging a rail row
  - half its height reorders the stack. Solo is an overlay over mute and un-soloing restores
  - the mute state exactly (2867-2903).
  - Arrow keys step the focus only over lanes that are actually drawn, and at the end of the
  - window they scroll the shown-channel window instead (stepped_channel 7326, next_channel
  - 7348, previous_channel 7373).
  - Amplitude ranges: shared across channels by default, per-channel, or fixed ±1
  - (set_y_mode 3061). Under shared Y an amplitude operation applies to every channel; only
  - under per-channel does the selection scope it (range_channels 7047).
  - v and a double-click on a trace's y axis refit the amplitude range even after the reader
  - has hand-zoomed it, and release the per-range user lock so it keeps auto-fitting
  - afterwards (auto_fit_y 3074-3129). A time scroll never overrides a hand zoom.
  - F2 toggles the traces, F3 toggles the spectrograms; turning either off turns the other
  - on, so the stack is never all empty lanes (toggle_traces 7598, toggle_spectrograms
  - 7604). No visible lane is ever blank (lane_fallback 6693).
  - The trace/spectrogram boundary is draggable in any lane, and dragging it moves the same
  - boundary in every lane showing both; Shift+F3 returns to the default split, which
  - follows the lane height rather than a stored number (drag_panel_split 6392,
  - reset_panel_split 6516).
  - A dragged split survives a restart as the spectrogram's height over its fixed allowance
  - and replays to the same visual proportion on a 2-channel and a 16-channel recording; a
  - settings value written by an older version is dropped with a warning, never guessed at
  - (restore_panel_split 6530, save_panel_split 6665).
  - Shift+F2 shows one full-height mean spectrogram over the selected channels, turns the
  - traces off, hides the rail, and pressing it again restores exactly the previous state
  - including whether the traces were on (set_mean_spectrogram 7654). A label drawn on the
  - mean carries no channel (store_label 4147).
  - Spectrogram window length (R/⇧R), overlap (O/⇧O) and colormap (⇧C) are settable from the
  - parameter bar and from keys; the Window tooltip states Δf and the Overlap readout states
  - Δt and the hop (set_resolution 7095-7139).
  - The 'Opens at' band sets the frequency range a spectrogram opens on and that Ctrl+V
  - returns to; Ctrl+Shift+V still shows the full band to Nyquist and hand zoom/pan is never
  - limited by it (set_spectrogram_band 2491-2551). A band typed in one tab reaches every
  - open tab, each clamping against its own Nyquist, and only the tab that was typed in
  - writes the preference.
  - A band end sitting at its limit is stored as null, so an 8 kHz recording's preference
  - never caps a 96 kHz one (save_spectrogram_band 6638).
  - High-pass and low-pass cutoffs are settable by spin box, log slider, keys (H/⇧H, L/⇧L)
  - and by dragging the handles on the spectrogram; a burst of key repeats costs one
  - recompute, not one per key (update_filter 7179, filter_timer 200 ms). 'Linked band'
  - moves both cutoffs keeping the band width.
  - Envelope cutoff behaves the same way (E/⇧E, envelope_timer 200 ms); with no envelope
  - trace in the data the envelope keys and menu entries are disabled rather than silently
  - doing nothing (2319-2322).
  - The navigator strip below the stack shows the whole session, in one row or one row per
  - channel, as a waveform envelope or as the activity overview, and follows the current
  - channel in single mode (set_navigator_mode 2991, toggle_navigator_overview 3019). Its
  - build progress is reported in the status bar and it never claims to be done while still
  - computing (overview_timer, report_overview_progress 3160).
  - The cross hair (Ctrl+C) reports t, Δt (with its reciprocal in Hz/kHz/mHz), amplitude,
  - frequency and power in the status bar; left-click stores a marker, right-click clears
  - it, and Δ readouts appear once two markers exist (mouse_moved 3299-3417).
  - With the cross hair off, hovering a trace still reports t, the amplitude at that sample
  - and the channel; hovering the navigator reports t and the channel (show_hover_value
  - 3260, show_navigator_time 3276).
  - + / − zoom the axis of the panel the pointer is over; with the pointer off any plot they
  - fall back to every axis of that kind (axis_under_pointer 3281).
  - A rubber-band drag does what the region mode says: zoom, play, analyse, save-as, label,
  - or (default) pop a menu at the drag position — not at a stale cursor position, which is
  - unanswerable under Wayland (region_menu 7767, region_menu_pos 7830). Shift+drag plays
  - and Alt+drag analyses regardless of the mode, for that one region only.
  - The Ask menu carries a 'Label as' submenu built from the live category vocabulary, so a
  - single label can be made without leaving the current mode (7805-7814).
  - In label mode (b): a drag writes a label of the current category; Ctrl+click picks the
  - label under the pointer and grows drag grips on it; Ctrl+drag also picks rather than
  - writing, so a slow or wobbly Ctrl+click never creates a stray hairline label
  - (select_label_from_region 3874). Ctrl+Delete removes the selected label, Escape
  - deselects, Shift+B undoes the last add, delete or geometry edit.
  - Label mode disarms the spectrogram's filter handles for as long as it lasts, and leaving
  - the mode gives them back and drops the selection (set_region_mode 7730).
  - A dragged grip can never write a label outside the recording: a move slides the whole
  - box back inside, a resize clamps only the edge that was dragged (fit_into 3986-4015).
  - Digit keys 1-9 pick the first nine categories; with the cross hair on, a point
  - category's digit key places a point label exactly at the cross hair; with it off it only
  - picks and says how to place one (category_key 3753).
  - Labels are written to a CSV sidecar beside the recording, atomically, one write per turn
  - of the event loop however many mutations a gesture caused, and always before the browser
  - goes away (schedule_label_save 3579, flush_labels 3610). A sidecar with unreadable rows
  - makes the store read-only and the parameter bar says READ-ONLY; a failed save says SAVE
  - FAILED; both mark the Editable-labels tab with '!'.
  - The label vocabulary is a per-reader preference restored before any file is open; a
  - category found in a CSV is added to it and the reader is told (load_labels 3540).
  - F9 shows or hides the editable labels; hiding them drops the selection
  - (set_labels_visible 3678).
  - Editable labels are drawn on trace lanes, spectrogram lanes and the navigator rows; the
  - navigator copies are read-only (attach_label_overlays 3624).
  - A session bundle found beside the recording is loaded automatically and always
  - announced; Ctrl+Shift+A opens one by hand; F8 toggles the whole overlay and says so
  - instead of opening a dialog when nothing is loaded (init_annotations 4606,
  - toggle_annotations 4828).
  - A bundle fitted against a different recording draws nothing at all and the badge says
  - so. A bundle whose recording is only partly open draws nothing and the badge reads '1 OF
  - 4 FILES' — never 'WRONG RECORDING' (check_recording_coverage 4657,
  - update_annotation_badge 5721). An UNVALIDATED alignment is reported as a warning and
  - marked on the badge and the tab.
  - One chip per bundle layer, in two captioned rows (Sent / Heard), each chip drawn with
  - the layer's own pen or fill so it is the legend as well as the toggle. A plain click
  - solos a layer; Ctrl/Shift-click toggles it beside the others; clicking the soloed layer
  - restores the set that was showing before the solo, not every layer; Shift+F8 / 'All'
  - switches everything on (solo_annotation_layer 4886).
  - Which layers and which surfaces (trace / spectrogram / navigator) are on survives a
  - restart; the F8 master switch deliberately does not (save_annotation_settings 5033).
  - Hovering a lane with annotations loaded names, in the parameter bar, every span the
  - pointer is inside — each with its own sent / not-heard / heard / unexplained counts —
  - and the nearest instant with a signed Δ. Counts are over the switched-on layers only
  - (annotation_under 5118, marks_in 5174).
  - Stepping (annotation next/previous) centres the view on the next mark of a switched-on
  - layer and describes it (step_annotation 5092).
  - A split recording draws one quiet full-height rule at each join, on every trace lane and
  - every navigator row, positioned from the loader and never from a bundle; a bundle-
  - declared gap is printed beside the join on the current channel's lane only, and nothing
  - is printed if the bundle's join count disagrees with the loader's (attach_join_markers
  - 4493, update_join_markers 4559).
  - Playback: Space plays the visible window; a play marker runs down every channel that is
  - actually being heard and disappears at the end (play_region 7992, mark_audio 8046).
  - Pressing play again while playing stops it.
  - Playback source (⇧P) cycles selected channel -> explicit L/R pair -> all shown channels
  - mixed to stereo. The L/R pickers appear only in pair mode. Hiding a lane never changes
  - what an explicitly chosen pair plays; with 'selected' it falls back to a shown channel
  - rather than producing silence (audio_channels 7895).
  - Time-expansion factor and heterodyne (frequency + on/off) apply to playback; heterodyne
  - is only offered above a 50 kHz sample rate (2404-2411).
  - Auto-scroll doubles its step on each press and halves it on stop, and stops itself at
  - the end of the recording (auto_scroll 7853, scroll_further 7866).
  - Analysis over a region opens a non-modal results table with per-analyzer columns and
  - formats; Save writes it as semicolon-separated CSV; Reset clears both the table and the
  - analyzers (analyze_region 8058, analysis_results 8092, save_analysis 8134).
  - Save-region writes the selected channels of the region in the source encoding, updates
  - the start time, appends a BEXT coding-history entry naming the cut, and carries over
  - only the file markers that fall inside the region; a permission failure is reported, not
  - swallowed (save_region 8170).
  - The parameter bar shows one group at a time behind a tab strip; the bar's height does
  - not change with the tab and a tab change never resizes the lanes. The chosen tab
  - survives a restart by name (so a recording without a Filter group falls back rather than
  - opening the wrong page). Loading a bundle raises the Fixed-labels tab; pressing b raises
  - the Editable-labels tab (restore_parameter_tab 5529, build_annotation_chips 5504,
  - set_region_mode 7752).
  - A live theme switch repaints every plot, axis, overlay, join rule, chip icon and
  - colormap swatch without reopening the file, and the spectrogram is re-pushed with the
  - new theme's map so it does not stay a dark slab in a light window (apply_theme 2587).
  - Window resize is debounced: only the cheap lane-height pass runs per event, the full
  - relayout runs 100 ms after the drag stops (resizeEvent 5828, resize_timer). Resizing the
  - scroll area's viewport counts as a resize (eventFilter 5817).
  - Elided readouts (the Labels file row, the annotation pointer line) never widen the
  - parameter bar under the pointer; the full text is in the tooltip and is re-elided when a
  - tab is raised or the window is resized (show_annotation_under 5280,
  - reelide_annotation_hover 5592, apply_resize 5840).
- **risk**: high — this one file is the application's entire interaction surface (~240 methods, 10 outbound signals, 9 modules reaching into its attributes), its layout correctness rests on empirically measured pixel constants and on Qt5/pyqtgraph layout-activation timing that Qt6 changes, and it owns the only user-authored data in the product (the label sidecar) through a hand-rolled zero-timer save path with no closeEvent behind it.
- **notes**: == RESPONSIBILITY INVENTORY (DataBrowser, src/audian/databrowser.py:979-8253, ~240 methods) ==  A. LIFECYCLE / TWO-PHASE CONSTRUCTION   __init__ 1138-1441 · __del__ 1442-1445 · open 1552-1850 (300 lines: loads data, builds trace/spec QActions, plot_ranges, per-channel figures+plots+borders+proxies, parameter bar, navigator, overlays, join markers, analyzers) · close 3184-3189 (SHADOWS QWidget.close) · flush_labels 3610-3623 · showEvent 5808-5816 · name 1497-1505  B. DATA / TRACE REGISTRY (façade over Data)   get_trace 1506 · add_trace 1509 · remove_trace 1512 · clear_traces 1515 · add_to_panel_trace 1535 · toggle_trace 1539 · set_trace 1544 · set_spectrogram 7086-7094 · goto_time 7006-7021 · show_metadata 3190-3242  C. PLUGIN / ANALYZER REGISTRY + ANALYSIS UI   get_analyzer 1518 · add_analyzer 1524 · remove_analyzer 1527 · clear_analyzer 1532 · analyze_region 8058-8074 · get_analysis_table 8075-8091 · analysis_results 8092-8127 · clear_analysis 8128-8133 · save_analysis 8134-8169  D. WIDGET CONSTRUCTION (chrome)   setup_stack 1886-1971 · setup_time_axis 1972-2020 · setup_parameter_bar 2050-2435 (386 lines) · style_parameter_spinbox 2436-2449 · setup_label_group 4246-4336 · setup_annotation_group 5299-5438 · build_annotation_chips 5439-5510 · build_category_chips 4337-4362 · annotation_chip 5669-5681 · apply_theme 2587-2651  E. PARAMETER-BAR TAB STATE / ALERTS   parameter_tab_settings 5511 · restore_parameter_tab 5529 · parameter_tab_changed 5545 · save_parameter_tab 5566 · raise_parameter_tab 5587 · reelide_annotation_hover 5592 · update_label_alert 5608 · update_annotation_alert 5622 · equalize_parameter_bar 5640  F. CHANNEL SELECTION / RAIL / LANE VISIBILITY   rail_shown 2652 · apply_rail_width 2665 · toggle_rail 2687 · selected_channels_in_order 2710 · visible_channels 2739 · mean_channels 2753 · mean_spec_lane 2757 · focus_channel 2762 · show_focused_lane 2792 · rail_clicked 2855 · toggle_solo 2867 · toggle_mute 2874 · toggle_maximize 2881 · move_channel 2890 · apply_channel_visibility 2904 · apply_lane_visibility 2924 · update_rail 2948 · update_current_plot 2954 · update_levels 3038 · update_borders 5782 · add_to_show_channels 7303 · add_to_selected_channels 7311 · all_channels 7319 · stepped_channel 7326 · next_channel 7348 · previous_channel 7373 · select_next_channel 7394 · select_previous_channel 7425 · set_channels 7452 · toggle_channel 7496 · show_channel 7528 · hide_deselected_channels 7538  G. LAYOUT ENGINE (stack geometry + trace/spectrogram split)   lane_axes 5852 · row_shows_tick_values 5902 · trace_plot 5917 · time_plot 5926 · spectrogram_plots 5950 · spectrogram_channels 5961 · lane_geometry 6000 · update_stretches 6046 · time_axis_height 6105 · link_time_axis 6125 · visible_trace_panels 6151 · split_spacers 6163 · lane_content_height 6195 · default_spec_height 6223 · panel_split_limits 6258 · panel_split_rows 6274 · fit_figure_layout 6345 · panel_split_heights 6366 · drag_panel_split 6392 · apply_panel_split 6428 · finish_panel_split 6511 · reset_panel_split 6516 · restore_panel_split 6530 · save_panel_split 6665 · lane_fallback 6693 · adjust_layout 6723-6872 · schedule_axis_alignment 6873 · align_time_axis 6894 · size_splitter 6935 · eventFilter 5817 · resizeEvent 5828 · apply_resize 5840  H. PANEL MODE TOGGLES / NAVIGATOR MODE   set_panels 7544-7597 · toggle_traces 7598 · toggle_spectrograms 7604 · apply_mean_spectrogram 7630 · set_mean_spectrogram 7654 · toggle_mean_spectrogram 7681 · mean_spectrogram_message 7684 · toggle_colorbars 7692 · toggle_powers 7696 · toggle_fulldata 7700 · toggle_grids 7704 · set_navigator_mode 2991 · toggle_navigator_mode 3000 · navigator_overview 3007 · has_navigator_activity 3013 · toggle_navigator_overview 3019  I. VIEWPORT / RANGES / ZOOM / Y POLICY   updating 1460 · disable_unused_range_actions 1851 · set_starttime_mode 2021 · lane_starttime_mode 2036 · set_y_mode 3061 · auto_fit_y 3074 · report_y_range 3130 · axis_bounds 3971 · update_ranges 6963 · same_range 6998 · set_times 7022 · apply_time_ranges 7036 · range_channels 7047 · set_ranges 7064 · apply_ranges 7074 · auto_ampl 7081 · set_zoom_mode 7710 · zoom_back 7715 · zoom_forward 7720 · zoom_home 7725  J. SPECTROGRAM CONTROL (resolution, band, colormap)   read_color_map_setting 1446 · hz_label 2450 · spec_band_tooltip 2460 · dispatch_spectrogram_band 2472 · set_spectrogram_band 2491 · set_band_widget 2552 · nfft_label 2569 · set_nfft_widget 2576 · spectrogram_band 6572 · _band_value 6622 · save_spectrogram_band 6638 · set_resolution 7095 · freq_resolution_down 7141 · freq_resolution_up 7145 · overlap_frac_up 7149 · overlap_frac_down 7154 · set_color_map 7159 · color_map_cycler 7176  K. FILTER / ENVELOPE   set_link_band 2584 · update_filter 7179 · apply_filter 7213 · set_filter_widgets 7241 · update_envelope 7253 · apply_envelope 7280  L. POINTER / CROSSHAIR / STATUS READOUTS   set_readout 1476 · notify 1489 · report_overview_progress 3160 · set_cross_hair 3243 · show_hover_value 3260 · show_navigator_time 3276 · axis_under_pointer 3281 · mouse_moved 3299-3417 · mouse_clicked 3418-3472  M. REGION GESTURES   set_region_mode 7730 · region_menu_at 7757 · region_menu 7767 · region_menu_pos 7830  N. EDITABLE LABELS (writable sidecar) — 3473-4421   label_settings 3473 · restore_label_categories 3498 · save_label_settings 3508 · labels_path 3528 · load_labels 3540 · schedule_label_save 3579 · save_labels 3592 · flush_labels 3610 · attach_label_overlays 3624 · redraw_labels 3667 · set_labels_visible 3678 · labels_visible 3691 · toggle_labels 3694 · sync_category_state 3699 · rebuild_category_actions 3713 · category_key_for 3742 · set_current_category 3746 · category_key 3753 · edit_label_categories 3778 · show_label_table 3808 · overlay_for_axis 3834 · select_label_at 3841 · select_label_in 3861 · select_label_from_region 3874 · select_label 3896 · label_editor_dropped 3916 · revalidate_selection 3923 · deselect_label 3961 · fit_into 3986 · label_edited 4016 · delete_selected_label 4062 · label_from_region 4086 · store_label 4121 · add_point_label 4165 · undo_last_label_change 4192 · describe_label 4226 · refresh_label_table 4239 · chip_clicked 4363 · update_category_chips 4371 · label_status_text 4376 · update_label_status 4407  O. FIXED ANNOTATIONS (read-only session bundle) + JOIN MARKERS — 4422-5781   attach_annotation_overlays 4422 · recording_joins 4455 · declared_join_gaps 4475 · attach_join_markers 4493 · join_marker 4526 · update_join_markers 4559 · polish_join_markers 4579 · set_annotation_surface 4587 · recording_path 4591 · init_annotations 4606 · recording_info 4621 · open_file_names 4643 · check_recording_coverage 4657 · bundle_problems 4702 · residual_tip 4731 · load_annotations 4761 · open_annotations 4807 · clear_annotations 4818 · toggle_annotations 4828 · annotation_chip_clicked 4844 · apply_annotation_layers 4858 · solo_annotation_layer 4886 · set_annotation_layer 4927 · show_all_annotation_layers 4940 · annotation_settings 4951 · restore_annotation_surfaces 4983 · restore_annotation_layers 4993 · schedule_annotation_save 5021 · save_annotation_settings 5033 · rebuild_annotations 5065 · redraw_annotations 5080 · annotation_keys 5089 · step_annotation 5092 · annotation_under 5118 · marks_in 5174-5279 · show_annotation_under 5280 · annotation_chip_tip 5645 · update_annotation_chips 5682 · update_annotation_badge 5721  P. PLAYBACK / AUDIO   play_scroll 7840 · auto_scroll 7853 · scroll_further 7866 · set_audio 7875 · audio_channels 7895 · set_pair_row_visible 7913 · set_audio_pair 7934 · set_audio_source 7952 · toggle_audio_source 7983 · play_region 7992 · play_window 8042 · mark_audio 8046  Q. EXPORT   save_region 8170-8250 · save_window 8251-8253 · save_analysis 8134-8169  == THE SIX QTimers (constructed 1238-1310) == 1. scroll_timer   — 1238-1239, repeating, 50 ms (started 7864). Slot scroll_further (7866). Drives auto-scroll: advances the time window by scroll_step of the window per tick; stops itself at trange.at_end() (7868) and halves the step. Also stopped by play_scroll (7842, 7858). 2. audio_timer    — 1243-1244, repeating, 50 ms (started 8035 in play_region). Slot mark_audio (8046). Advances audio_time by 0.05/audio_rate_fac and moves every vmarker in every lane; stops itself and parks the markers at -1 when audio_time > audio_tmax (8052-8057). Also stopped by play_scroll (7846). 3. resize_timer   — 1297-1299, SINGLE-SHOT, 100 ms. Slot apply_resize (5840). Debounces the full adjust_layout after resizeEvent (5838) and after the QScrollArea viewport Resize seen in eventFilter (5825). Rationale at 5829-5834: Hyprland delivers uncoalesced resize events and adjust_layout repaints every channel scene. 4. filter_timer   — 1300-1302, SINGLE-SHOT, 200 ms (started 7211). Slot apply_filter (7213). Debounces the high/low-pass recompute stashed in pending_highpass/pending_lowpass; rationale 7184-7188 (refilter + respectrogram of 16 channels ≈ 1.5 s; key auto-repeat must cost one recompute). 5. envelope_timer — 1305-1307, SINGLE-SHOT, 200 ms (started 7270). Slot apply_envelope (7280). Same debounce for pending_envelope. 6. overview_timer — 1309-1310, repeating, 250 ms (started 1849, the LAST statement of open()). Slot report_overview_progress (3160). Polls datafig.compressed_data.is_busy()/progress() and pushes window.set_progress(fraction, "Building overview…"); stops itself when the compressed overview finishes or is absent (3165, 3178) and reports "overview unavailable" if the result is malformed.  Plus SEVEN fire-and-forget QTimer.singleShot(0, …): 3590 save_labels · 4361 equalize_parameter_bar · 5031 save_annotation_settings · 5507 equalize_parameter_bar · 6103 show_focused_lane · 6892 align_time_axis · 7932 equalize_parameter_bar. These are the deferred-measurement / deferred-write seams Qt6's layout-activation timing most threatens; 6103 carries a measured 4-way table (2820-2835) proving that deferral PLUS layout activation is what makes it correct.  == STATE DataBrowser OWNS THAT OTHERS MUTATE OR READ DIRECTLY == WRITTEN FROM OUTSIDE:  · region_mode_override ← selectviewbox.py:56 (browser.region_mode_override = mode); consumed and cleared by region_menu (7771-7773).  · channel_names[c] ← ChannelRailRow.rename, databrowser.py:857 (a child widget writing the parent's dict).  · gui ← set by the main window through open(gui, …) at 1553; read by timeplot.py:285 and selectviewbox.py:51 via getattr.  · spec_scale ← written directly by tests (tests/test_panelsplitter.py:255, 922, 926, 949, 953, 983, 1032, 1036); the production path is PanelSplitter -> drag_panel_split (6392).  · self.acts.* → OUTBOUND mutation of the main window's SHARED QAction namespace: 1836-1837, 1851-1884 (20 actions permanently disabled+hidden), 2162-2166, 2319-2322, 7489, 7501. One acts object is handed to every browser (audian.py:4707, 4752), so this leaks across tabs and is never undone.  · save_path → shared one-element list, written at 8168 and 8231. READ FROM OUTSIDE (attribute reach-through, not API):  · show_traces / show_specs / show_powers / show_cbars / show_fulldata — read off a SIBLING browser at audian.py:4782-4788 to seed a new tab.  · show_channels — audian.py:2759, timeplot.py:156. current_channel / selected_channels — audian.py:2567ff.  · y_mode, y_fixed — timeplot.py:286. show_specs — spectrogramplot.py:231.  · data, plot_ranges, panels, figs, labels, annotations, label_overlays, control_panel, datafig, param_groups, param_tabs, parambar, annotation_badgew, annotation_coverage, current_category, spec_acts, hpfw/lpfw/fmaxw — read by audian.py, analyzer.py and the test suite (hpfw/lpfw called unguarded at audian.py:3702-3717). INTERNALLY CROSS-CUTTING (set in one bucket, read in another):  · self.setting — set only by the updating() contextmanager (1460) but tested at the top of 9 unrelated methods.  · y_locked — set True as a side effect of ANY hand y-range change in update_ranges (6981); read by set_times (7034), auto_fit_y (3104), auto_ampl (7083), set_y_mode (3064).  · hover_panel / hover_channel — written by mouse_moved (3306-3336); read by axis_under_pointer (3287, called from audian's pointer_axes), select_label_at (3852), add_point_label (4177).  · lane_left_width — decided by adjust_layout (6748), re-used by the split drag through apply_panel_split (6503).  · spec_warned, scroll_focus_pending, label_save_pending, annotation_save_pending, _spec_band_saved, _param_tab_saved, annotation_layers_before_solo, traces_before_mean — eight one-off latches guarding against duplicate work or duplicate writes.  == BINDING FOOTPRINT OF THE WHOLE PACKAGE (for migration sequencing) == PyQt5 is imported by 14 modules: audian.py (14-27), databrowser.py (15-30), theme.py (79-89, 1513), labeloverlay.py (107-109, and QVariant at 107 — the only QVariant in the package), eventoverlay.py (73-79), fulltraceplot.py (25-28), timeplot.py (7), spectrogramplot.py (6-11), selectviewbox.py (5-9), panelsplitter.py (28-29), controlpanel.py (67), timeaxisitem.py (6), yaxisitem.py (7), buffereddata.py (8). pyproject.toml pins PyQt5 as a dependency. Five of these already carry the try/except Signal shim, so a single audian/qt.py compat module removes the shim from all of them and is the cheapest first step.
