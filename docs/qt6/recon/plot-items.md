# Recon: plot-items

- **cluster**: plot-items
- **purpose**: The pyqtgraph visualization layer: every pixel the reader looks at is drawn by these nine files. `RangePlot` is the common `pg.PlotItem` base (crosshair, stored marker, grid, themed viewbox, menu teardown) over a `SelectViewBox` that reimplements pyqtgraph's wheel and drag handlers to add modifier-gated zoom and rubber-band region selection; `TimePlot` adds the amplitude lane (in-plot `CH nn` caption, zero line, playback marker, y-axis reset gesture), and `SpectrogramPlot` subclasses it into a frequency lane with a colour bar, a power sidebar, filter-cutoff handles and an automatic dB level fit. `TraceItem` (min/max-decimated waveform) and `SpecItem` (cropped, strided dB image) are the two data items; `TimeAxisItem`/`YAxisItem` are tick-spacing and tick-string overrides. `FullTracePlot` is a standalone `pg.GraphicsLayoutWidget` navigator with its own hand-written `QGraphicsObject` painters, a two-way region/lane range sync, a polling timer over a multiprocessing compression job, and a measure-write-remeasure column-alignment loop.
- **public_surface**:
  - **name**: TraceItem
  - **file**: src/audian/traceitem.py:23
  - **kind**: class
  - **base**: pg.PlotDataItem
  - **summary**: One channel of one trace. Imported by panels.py:12, instantiated in Panel.add_traces (panels.py:193). Contract used elsewhere: update_plot(), get_amplitude(x,y,x1), set_selected(), set_dense(), apply_theme()/polish(), effective_role(), and the attributes .data, .rate, .channel, .step, .ax (assigned by RangePlot.add_item).

  - **name**: TRACE_ROLES
  - **file**: src/audian/traceitem.py:13
  - **kind**: constant
  - **base**: 
  - **summary**: trace name -> theme role map; an unknown name keeps the trace's own colour so third-party plugin traces still work.

  - **name**: SpecItem
  - **file**: src/audian/specitem.py:12
  - **kind**: class
  - **base**: pg.ImageItem
  - **summary**: Spectrogram image of one channel, or of the mean over several. Imported by panels.py:11. Contract: update_plot(), set_view_range(t0,t1), set_mean_channels(chs)->bool, get_power(t,f), noise_levels(), inherited setLevels(); class knobs view_pad=1.5 (line 30) and pixel_oversample=2 (line 33).

  - **name**: RangePlot
  - **file**: src/audian/rangeplot.py:9
  - **kind**: class
  - **base**: pg.PlotItem
  - **summary**: Base plot panel managed by PlotRange. Public: x()/y()/z() axis-spec accessors (127-134), add_item(item,is_data), range(axspec), amplitudes(t0,t1), get_marker_pos(...), set_stored_marker(x,y), set_grid(x,y), update_plot(), polish()/apply_theme(), and .data_items, .annotations (assigned externally at databrowser.py:4445), .channel, .aspec.

  - **name**: SelectViewBox
  - **file**: src/audian/selectviewbox.py:14
  - **kind**: class
  - **base**: pg.ViewBox
  - **summary**: ViewBox with modifier-gated wheel zoom, rubber-band region selection and a zoom history. Imported by rangeplot.py:6 and by databrowser.py:50 (only to feature-detect wheelEvent at databrowser.py:1912). Signals sigSelectedRegion, sigSelectedRegionAt, sigHoverValue, sigUserZoomed; methods zoom_region/zoom_back/zoom_forward/zoom_home/hide_region/init_zoom_history/apply_theme; carries an externally assigned .browser (set at rangeplot.py:45).

  - **name**: TimePlot
  - **file**: src/audian/timeplot.py:39
  - **kind**: class
  - **base**: RangePlot
  - **summary**: Amplitude-vs-time lane, constructed at databrowser.py:1704. Public: sigHoverValue(int,float,float), Y_TOP_PAD, set_current(), set_caption(), caption_text(), data_unit(), reset_y_range(), update_axis_label(), visible_channels(), amplitudes(t0,t1), set_starttime(mode), range(axspec).

  - **name**: TICK_VALUES_MIN_HEIGHT
  - **file**: src/audian/timeplot.py:23
  - **kind**: constant
  - **base**: 
  - **summary**: 48 px view-box height below which y tick values and the in-plot caption are dropped. Imported by databrowser.py:52 and re-applied there at databrowser.py:5915 and 6043.

  - **name**: si_prefixable / SI_UNITS
  - **file**: src/audian/timeplot.py:29
  - **kind**: function
  - **base**: 
  - **summary**: Whitelist of units pyqtgraph may SI-prefix; everything else (notably 'a.u.') must be shown verbatim with unscaled ticks.

  - **name**: SpectrogramPlot
  - **file**: src/audian/spectrogramplot.py:113
  - **kind**: class
  - **base**: TimePlot
  - **summary**: Frequency-vs-time lane, constructed at databrowser.py:1725. Public: sigUpdateFilter(object,object), .cbar (pg.ColorBarItem), .powerax (PowerPlot), setZRange(zmin,zmax) called by PlotRange.set_ranges (plotranges.py:265), set_mean_channels(), set_handles_movable(), set_filter_handles(), fit_levels(), fits_levels(), visible_block(), and the staticmethod can_render(height).

  - **name**: PowerPlot
  - **file**: src/audian/spectrogramplot.py:61
  - **kind**: class
  - **base**: RangePlot
  - **summary**: The power-spectrum sidebar beside a spectrogram; registered as its own panel through Panels.add_power_ax (databrowser.py:1737).

  - **name**: channel_range_label
  - **file**: src/audian/spectrogramplot.py:35
  - **kind**: function
  - **base**: 
  - **summary**: Collapses a channel set to one short caption line ('00-15', '00-03,09', '11 ch'), capped by MAX_CHANNEL_LABEL_CHARS=17.

  - **name**: TimeAxisItem
  - **file**: src/audian/timeaxisitem.py:11
  - **kind**: class
  - **base**: pg.AxisItem
  - **summary**: Time axis with three start-time modes. Imported by timeplot.py:14, fulltraceplot.py:32 and databrowser.py:51 (subclassed there as SharedTimeAxis, databrowser.py:1990). Public: set_start_time(), set_starttime_mode(), set_left_margin(), get_file_pos(), makeStrings(...) which doubles as the navigator's hover formatter (called from fulltraceplot.py:1204), apply_theme()/polish().

  - **name**: YAxisItem
  - **file**: src/audian/yaxisitem.py:12
  - **kind**: class
  - **base**: pg.AxisItem
  - **summary**: Linear y axis with a max_major_ticks cap, label-independent SI prefixing and a double-click reset hook. Public: set_reset(cb), set_max_major_ticks(n), set_si_unit(unit), si_unit_label(), .si_prefix, apply_theme().

  - **name**: FullTracePlot
  - **file**: src/audian/fulltraceplot.py:360
  - **kind**: class
  - **base**: pg.GraphicsLayoutWidget
  - **summary**: The navigator strip, constructed at databrowser.py:1794. Public: sigHoverTime(int,float), prepare(), start_plotting(), polish()/apply_theme(), refresh_colors(), set_mode(), set_channel(), set_overview(), has_activity(), update_layout(channels,data_height), sync_layout(), close(), and .axs (per-channel pg.PlotItem rows given .channel and .annotations from outside), .time_info, .overlay_enabled.

  - **name**: MODE_SINGLE / MODE_ALL / OVERVIEW_WAVEFORM / OVERVIEW_ACTIVITY
  - **file**: src/audian/fulltraceplot.py:79
  - **kind**: constant
  - **base**: 
  - **summary**: Navigator mode and content selectors; imported by databrowser.py:42-49 and audian.py:37.

  - **name**: secs_to_str
  - **file**: src/audian/fulltraceplot.py:35
  - **kind**: function
  - **base**: 
  - **summary**: Duration formatter ('1h2m3s', '4.20ms'); imported by databrowser.py:48 and audian.py:37.

  - **name**: EnvelopeItem / ActivityItem / NavigatorRegion
  - **file**: src/audian/fulltraceplot.py:105
  - **kind**: class
  - **base**: pg.GraphicsObject / pg.GraphicsObject / pg.LinearRegionItem
  - **summary**: Custom painters for the navigator (lines 105, 201, 309): a closed min/max polygon, an RMS band with transient spikes, and a LinearRegionItem with visible grab handles. Not imported elsewhere, but they are the only hand-written paint()/boundingRect() implementations in the cluster and therefore the only direct QPainter surface to port.

- **qt5_api_usage**:
  - **file**: src/audian/timeplot.py
  - **line**: 6
  - **api**: try: from PyQt5.QtCore import Signal / except ImportError: from PyQt5.QtCore import pyqtSignal as Signal
  - **qt6_replacement**: One shared compat module: `from PySide6.QtCore import Signal`. Note the shim is already dead code - both branches name PyQt5 and PyQt5.QtCore has no `Signal`, so the except branch always runs. Identical block at selectviewbox.py:4-7, spectrogramplot.py:8-11, and outside the cluster at eventoverlay.py:76-79 and databrowser.py:14-17.
  - **severity**: breaking

  - **file**: src/audian/spectrogramplot.py
  - **line**: 6
  - **api**: from PyQt5.QtCore import QTimer
  - **qt6_replacement**: PySide6.QtCore.QTimer. The timer is parented to `self`, a pg.PlotItem (QGraphicsWidget) - a valid QObject parent in both bindings, but PySide6 keeps a strong reference to the receiver of `timeout.connect(self._refit_levels)` (line 197), so verify the plot is still collected when a browser tab closes.
  - **severity**: breaking

  - **file**: src/audian/fulltraceplot.py
  - **line**: 25
  - **api**: from PyQt5.QtCore import QPointF, QRectF, Qt, QTimer; from PyQt5.QtGui import QGuiApplication, QPainter, QPainterPath; from PyQt5.QtWidgets import QSizePolicy
  - **qt6_replacement**: PySide6 equivalents; all of these classes exist unchanged in Qt6. QPainterPath.closeSubpath/isEmpty (lines 173, 187) are unchanged.
  - **severity**: breaking

  - **file**: src/audian/timeaxisitem.py
  - **line**: 6
  - **api**: from PyQt5.QtCore import QPointF
  - **qt6_replacement**: PySide6.QtCore.QPointF.
  - **severity**: breaking

  - **file**: src/audian/yaxisitem.py
  - **line**: 7
  - **api**: from PyQt5.QtCore import Qt
  - **qt6_replacement**: PySide6.QtCore.Qt.
  - **severity**: breaking

  - **file**: src/audian/selectviewbox.py
  - **line**: 9
  - **api**: from PyQt5.QtGui import QTransform
  - **qt6_replacement**: PySide6.QtGui.QTransform; QTransform.fromScale at line 197 is unchanged in Qt6.
  - **severity**: breaking

  - **file**: src/audian/selectviewbox.py
  - **line**: 36
  - **api**: self.drag_modifiers = Qt.NoModifier (also line 150)
  - **qt6_replacement**: Qt.KeyboardModifier.NoModifier. PyQt6 does not expose the unscoped form at all; PySide6 still resolves it through forgiveness mode but it is deprecated.
  - **severity**: breaking

  - **file**: src/audian/selectviewbox.py
  - **line**: 91
  - **api**: if mods & Qt.ControlModifier: ... elif mods & Qt.ShiftModifier:
  - **qt6_replacement**: Qt.KeyboardModifier.ControlModifier / .ShiftModifier. In Qt6 KeyboardModifiers is a real Python enum.Flag, so `&` still yields a truthy/falsy value and the logic survives, but prefer `Qt.KeyboardModifier.ControlModifier in mods`. Note the existing precedence is Ctrl-wins: Ctrl+Shift+wheel zooms time, never y.
  - **severity**: behavior-change

  - **file**: src/audian/selectviewbox.py
  - **line**: 104
  - **api**: s = 1.02 ** (ev.delta() * self.state["wheelScaleFactor"])
  - **qt6_replacement**: The event here is a QGraphicsSceneWheelEvent, whose delta() survives into Qt6 (unlike QWheelEvent.delta(), which is removed), so this compiles - but QGraphicsSceneWheelEvent has no pixelDelta(), so a high-resolution trackpad on Qt6/Wayland can deliver delta()==0 and the zoom silently does nothing. Best fix: delete the arithmetic and delegate to `super().wheelEvent(ev, axis=zoom_axis)`; pyqtgraph ViewBox.py:1297-1316 already implements exactly this body with the same 1.02 constant.
  - **severity**: behavior-change

  - **file**: src/audian/selectviewbox.py
  - **line**: 106
  - **api**: pg.functions.invertQTransform(self.childGroup.transform()) (also lines 156, 180)
  - **qt6_replacement**: Still present in pyqtgraph 0.14 and binding-agnostic, but it is private pyqtgraph surface reached from application code. Removing the forked handlers removes all three call sites.
  - **severity**: cosmetic

  - **file**: src/audian/selectviewbox.py
  - **line**: 130
  - **api**: Qt.MouseButton.LeftButton / MiddleButton (scoped) vs Qt.LeftButton at yaxisitem.py:90 (unscoped)
  - **qt6_replacement**: The codebase is inconsistent: selectviewbox.py:130/169/185 already use the scoped form, yaxisitem.py:90 does not. Normalise to scoped everywhere so the mechanical pass has no exceptions.
  - **severity**: cosmetic

  - **file**: src/audian/selectviewbox.py
  - **line**: 169
  - **api**: elif ev.button() & Qt.MouseButton.RightButton:
  - **qt6_replacement**: ev.button() returns a single MouseButton, so `&` is a bitwise test on a scalar; in Qt6 MouseButton is an enum.Flag and `&` still works, but `== Qt.MouseButton.RightButton` is what is meant and what the left/middle branch on line 130 already does.
  - **severity**: cosmetic

  - **file**: src/audian/selectviewbox.py
  - **line**: 124
  - **api**: pyqtgraph private state: self.state["mouseEnabled"] (100, 124, 165, 188), self.childGroup.transform() (155, 179), self._resetTarget() (162, 186), self.rbScaleBox (33-34, 60-61, 196-199), self.axHistory / axHistoryPointer (205-206), self.showAxRect() / scaleHistory() (210, 216-222)
  - **qt6_replacement**: Not Qt5 API but unversioned pyqtgraph internals, and the load-bearing coupling in this cluster. Any pyqtgraph bump taken as part of the Qt6 move can shift them with no import error. Delegate to ViewBox.mouseDragEvent/wheelEvent and keep only the region emission.
  - **severity**: behavior-change

  - **file**: src/audian/yaxisitem.py
  - **line**: 53
  - **api**: self.setCursor(Qt.PointingHandCursor)
  - **qt6_replacement**: Qt.CursorShape.PointingHandCursor.
  - **severity**: breaking

  - **file**: src/audian/yaxisitem.py
  - **line**: 90
  - **api**: ev.button() == Qt.LeftButton
  - **qt6_replacement**: Qt.MouseButton.LeftButton. Note `ev` here is a pyqtgraph MouseClickEvent, not a QMouseEvent, so ev.double() and ev.pos() on lines 89-91 are pyqtgraph API and are unaffected by Qt6.
  - **severity**: breaking

  - **file**: src/audian/yaxisitem.py
  - **line**: 131
  - **api**: updateAutoSIPrefix() reimplemented over AxisItem private state: self.logMode, self.range, self.scale, self.unitPower, self.autoSIPrefixScale, self.labelUnitPrefix, self._updateLabel(); plus self.picture = None at line 105 and timeaxisitem.py:52/260/268
  - **qt6_replacement**: Binding-independent, but it must be re-verified against whichever pyqtgraph version the Qt6 build pins - `unitPower` and `labelUnitPrefix` are instance-only attributes with no class-level default, so a rename fails at first paint, not at import.
  - **severity**: behavior-change

  - **file**: src/audian/fulltraceplot.py
  - **line**: 193
  - **api**: p.setRenderHint(QPainter.Antialiasing, False) (also lines 297 and 351)
  - **qt6_replacement**: QPainter.RenderHint.Antialiasing.
  - **severity**: breaking

  - **file**: src/audian/fulltraceplot.py
  - **line**: 303
  - **api**: p.setBrush(Qt.NoBrush)
  - **qt6_replacement**: Qt.BrushStyle.NoBrush.
  - **severity**: breaking

  - **file**: src/audian/fulltraceplot.py
  - **line**: 985
  - **api**: self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
  - **qt6_replacement**: QSizePolicy.Policy.Preferred / QSizePolicy.Policy.Maximum. Same pattern outside the cluster at databrowser.py:2000.
  - **severity**: breaking

  - **file**: src/audian/fulltraceplot.py
  - **line**: 1151
  - **api**: spos = self.mapToScene(ev.pos()) in mousePressEvent (also line 1176 in mouseMoveEvent)
  - **qt6_replacement**: QMouseEvent.pos() is deprecated in Qt6 (QSinglePointEvent) and returns an integer QPoint; under Qt6's default fractional device-pixel scaling that quantises the hit test deciding which navigator row was clicked and where the region recentres. Use ev.position() (QPointF) with the QPointF overload of mapToScene.
  - **severity**: behavior-change

  - **file**: src/audian/fulltraceplot.py
  - **line**: 1037
  - **api**: return float(view.mapToGlobal(view.mapFromScene(QPointF(left, 0.0))).x())
  - **qt6_replacement**: QGraphicsView.mapFromScene(QPointF) returns an integer QPoint in Qt5 and Qt6 alike, so the alignment measurement is already rounded before the sub-pixel comparisons at lines 1084 and 1098 see it. Qt6 adds QWidget.mapToGlobal(QPointF); use view.viewportTransform().map(scenePoint) plus the QPointF overload so the MAX_ALIGN_STEPS loop stops chasing rounding.
  - **severity**: behavior-change

  - **file**: src/audian/fulltraceplot.py
  - **line**: 693
  - **api**: ratio = float(self.devicePixelRatioF() or 1.0); screen = QGuiApplication.primaryScreen(); width = max(width, int(screen.geometry().width() * ratio))
  - **qt6_replacement**: Mixes this widget's DPR with the *primary* screen's logical geometry - wrong on a mixed-DPI multi-monitor setup, and primaryScreen() is arbitrary under Wayland. Use self.screen() (QWidget.screen()) and that screen's own devicePixelRatio(). Qt6 also honours fractional scaling by default (nothing sets AA_EnableHighDpiScaling anywhere - grep is clean), so this bin count changes on 125%/150% desktops.
  - **severity**: behavior-change

  - **file**: src/audian/traceitem.py
  - **line**: 177
  - **api**: widget = self.getViewWidget(); dpr = widget.devicePixelRatioF() if widget is not None else 1.0
  - **qt6_replacement**: Unchanged API, but Qt6 turns on fractional high-DPI scaling by default, so max_pixel and therefore the decimation step (traceitem.py:227) take new values on scaled desktops. Same at specitem.py:120 for the spectrogram column budget. Re-measure the paint cost that motivated the comment at traceitem.py:158-166.
  - **severity**: behavior-change

  - **file**: src/audian/fulltraceplot.py
  - **line**: 280
  - **api**: pg.functions.arrayToQPath(hx, hy, connect=<int32 ndarray>) for the transient spikes (line 172 and 267 use connect='all')
  - **qt6_replacement**: connect='all' takes pyqtgraph's QPolygonF path and is safe. The int32-array form goes through pyqtgraph's QDataStream serialisation of QPainterPath's Qt-version-dependent binary layout, with a PySide6-specific QByteArray workaround at pyqtgraph/functions.py:2113-2126. It works on pyqtgraph 0.14 + PySide6, but it is the single most Qt-version-fragile call in the cluster: smoke-test the activity overview on a recording that actually has transient bins.
  - **severity**: behavior-change

  - **file**: src/audian/fulltraceplot.py
  - **line**: 562
  - **api**: def __del__(self): self.close()
  - **qt6_replacement**: Delete it. Under PySide6 __del__ on a QWidget subclass can run after the C++ object is gone; only the timer stop is guarded by try/except RuntimeError (lines 567-572), while self.compressed_data.close() at line 574 is not. Tear down explicitly from DataBrowser or via closeEvent.
  - **severity**: breaking

  - **file**: src/audian/traceitem.py
  - **line**: 40
  - **api**: Attributes assigned before the base constructor: self.data/self.rate/... at 41-61 and self.data.plot_items[self.channel] = self at 63, with pg.PlotDataItem.__init__ only at line 65
  - **qt6_replacement**: Same shape at rangeplot.py:11-25 and timeplot.py:54-90. PySide6/Shiboken is stricter than PyQt5 about touching a wrapper whose C++ base __init__ has not run, and traceitem.py:63 publishes a half-constructed `self` into a shared registry. Move every super().__init__() to the top of each constructor before anything else in the port.
  - **severity**: breaking

  - **file**: src/audian/spectrogramplot.py
  - **line**: 124
  - **api**: Class-level defaults _levels_fitted/_refit_pending/_applying_levels/mean_channels because "pg.PlotItem's constructor can reach setVisible() before __init__"; same rationale at fulltraceplot.py:368-380 for resizeEvent
  - **qt6_replacement**: A virtual-called-during-construction hazard that PySide6 reproduces and can escalate from AttributeError to RuntimeError. Keep the class-level defaults and add an explicit _ready guard so setVisible()/resizeEvent() no-op until construction finishes.
  - **severity**: behavior-change

  - **file**: src/audian/spectrogramplot.py
  - **line**: 180
  - **api**: pg.ColorBarItem(colorMap=..., width=..., interactive=True, rounding=1, limits=(-200,20), **theme.colorbar_pens())
  - **qt6_replacement**: pyqtgraph 0.14 adds keyword-only colorMapMenu=True, and ColorBarItem.mouseClickEvent (pyqtgraph/graphicsItems/ColorBarItem.py:358-368) lazily builds a parentless ColorMapMenu with sub-menus on right-click - bypassing both setMenuEnabled(False) at line 200 and theme.strip_pg_menus at line 201. With 16 colour bars on Wayland this reintroduces exactly the top-level-surface problem theme.py:1775-1910 exists to solve. Pass colorMapMenu=False.
  - **severity**: behavior-change

  - **file**: src/audian/timeaxisitem.py
  - **line**: 256
  - **api**: def resizeEvent(self, ev=None) - a QGraphicsWidget virtual that is also invoked by hand with no argument from set_left_margin (line 42)
  - **qt6_replacement**: Matches pyqtgraph's own AxisItem.resizeEvent(self, ev=None) so it stays valid, but under PySide6 hand-calling a virtual is easy to get wrong. Split the geometry work into a plain method and have resizeEvent call it.
  - **severity**: cosmetic

  - **file**: src/audian/selectviewbox.py
  - **line**: 16
  - **api**: sigSelectedRegion = Signal(object, object, object) and sigSelectedRegionAt = Signal(object, object, object, object), carrying (channel, self, QRectF, QPointF)
  - **qt6_replacement**: Legal in PySide6, but passing `self` (a ViewBox) through an untyped `object` argument defeats PySide6's ownership tracking and leaves the payload undocumented. Type the arguments (int, ViewBox, QRectF, QPointF), or collapse the pair into one signal carrying a small dataclass.
  - **severity**: cosmetic

  - **file**: src/audian/timeplot.py
  - **line**: 41
  - **api**: sigHoverValue = Signal(int, float, float), emitted with self.channel, float(x), float(y) at line 369
  - **qt6_replacement**: Fine as-is, but PySide6 is stricter about numpy scalars in typed signals. Keep the explicit float()/int() conversions at timeplot.py:369 and fulltraceplot.py:1184 and audit any new emit site.
  - **severity**: cosmetic

  - **file**: src/audian/selectviewbox.py
  - **line**: 96
  - **api**: ev.ignore() so the plain wheel reaches the enclosing QScrollArea (databrowser.py:1910-1918)
  - **qt6_replacement**: Depends on QGraphicsScene -> QGraphicsView -> QAbstractScrollArea wheel propagation, which Qt changed around 5.14/6.x (a scroll area at its limit no longer always forwards to its parent); pyqtgraph's GraphicsView.wheelEvent (widgets/GraphicsView.py:312) calls super() first and forces both scroll bars off. No code change is implied - this is a functional acceptance test: verify plain-wheel scrolling of a 16-channel stack on Qt6.
  - **severity**: behavior-change

- **architecture_problems**:
  - **title**: SelectViewBox.mouseDragEvent is a verbatim fork of pyqtgraph's, pinning audian to ViewBox internals
  - **file**: src/audian/selectviewbox.py
  - **line**: 114
  - **evidence**: Lines 114-191 reproduce pg.ViewBox.mouseDragEvent almost character for character (the '## if axis is specified...' and '# print "vb.rightDrag"' comments are pyqtgraph's) with three additions: recording ev.modifiers() on isStart (135-136), emitting sigSelectedRegion/sigSelectedRegionAt on isFinish (145-149), and emitting sigUserZoomed (166, 189). It reads self.state['mouseEnabled'] (124), self.childGroup.transform() (155, 179), self._resetTarget() (162, 186), self.axHistory/axHistoryPointer (205-206) and self.rbScaleBox (196-199).
  - **why_it_matters**: The Qt6 move is the natural moment to move pyqtgraph forward too, and every one of these is unversioned private surface. A change in ViewBox.state or childGroup breaks pan, rubber-band selection and the zoom history at once, with no import error to catch it.
  - **proposed_qt6_design**: Reduce the override to an interception: on isStart record modifiers; on isFinish in RectMode compute the rect and emit; otherwise `return super().mouseDragEvent(ev, axis)`, and derive sigUserZoomed from pyqtgraph's own sigRangeChangedManually, which the base class already emits. Keep only the region-selection behaviour pyqtgraph does not provide.
  - **effort**: medium

  - **title**: SelectViewBox.wheelEvent duplicates the base implementation to add a two-line modifier gate
  - **file**: src/audian/selectviewbox.py
  - **line**: 84
  - **evidence**: Lines 99-112 are pg.ViewBox.wheelEvent (ViewBox.py:1297-1316) rewritten, including the 1.02**delta scaling constant and the invertQTransform/Point centre computation. pyqtgraph's own signature already accepts axis=0/1, which is exactly what the mask at 99-101 recomputes by hand.
  - **why_it_matters**: It is the only place ev.delta() is called in the cluster - the one wheel API whose Qt6 semantics differ for trackpads - and it hard-codes a scale constant that pyqtgraph tunes.
  - **proposed_qt6_design**: def wheelEvent(self, ev, axis=None): resolve the axis from the modifiers or ev.ignore(); then `super().wheelEvent(ev, axis=resolved)`; emit sigUserZoomed off sigRangeChangedManually. Four lines instead of twenty-nine, and no Qt event API touched.
  - **effort**: small

  - **title**: RangePlot.x() and RangePlot.y() shadow QGraphicsItem.x() / y()
  - **file**: src/audian/rangeplot.py
  - **line**: 127
  - **evidence**: `def x(self): return self.aspec[0]` and `def y(self): return self.aspec[1]` return single-character axis specs ('t', 'x', 'f'). Consumers depend on the string: timeplot.py:286 `self.y() in Panel.amplitudes`, plotranges.py:705-708, spectrogramplot.py:597-606, panels.py:62-65.
  - **why_it_matters**: Every pg.PlotItem is a QGraphicsWidget whose x()/y() are documented to return scene coordinates. Nothing in pyqtgraph 0.14 calls them on a PlotItem today, but the shadowing is silent and one new pyqtgraph call site turns a coordinate into the character 't'. It also makes the class unusable with any third-party graphics code.
  - **proposed_qt6_design**: Rename to xspec()/yspec()/zspec(), or expose one `aspec` object with named fields, and update the ~12 call sites in plotranges.py, panels.py, timeplot.py and spectrogramplot.py.
  - **effort**: small

  - **title**: Application range policy lives inside the plot item (TimePlot.reset_y_range)
  - **file**: src/audian/timeplot.py
  - **line**: 285
  - **evidence**: `gui = getattr(self.browser, 'gui', None)` then branches on `self.y() in Panel.amplitudes and self.browser.y_mode != self.browser.y_fixed` and calls gui.auto_amplitude() / gui.apply_ranges('default_view', self.y()) / browser.auto_ampl() / browser.apply_ranges(...). The 54-line docstring above it (231-284) is an application-policy specification, not a method description.
  - **why_it_matters**: A plot item reaches two levels up (plot -> browser -> gui) and dispatches range commands by method-name string. It is untestable without a whole Audian window, and each getattr fallback is a second code path nobody exercises.
  - **proposed_qt6_design**: YAxisItem already takes a callback (yaxisitem.py:42). Push the decision one level further out: the axis calls plot.request_reset(), the plot emits sigResetRequested(axspec), and DataBrowser/Audian owns the amplitude-vs-frequency and shared-vs-per-channel policy. Delete the gui/browser fallback pair.
  - **effort**: medium

  - **title**: The spectrogram level fit is application policy plus a timer-sequenced race workaround inside the plot item
  - **file**: src/audian/spectrogramplot.py
  - **line**: 432
  - **evidence**: setVisible() arms _refit_pending (454-458); setZRange() consumes it and fires self._refit_timer.start(0) (618-622) because, per the docstring at 435-452, PlotRanges.set_powers() overwrites the fit after update_plots(); _apply_levels() mutates browser.plot_ranges[z].rmin/rmax directly (566-575); fits_levels() asks browser.visible_channels() to decide which lane owns the shared mapping (478-482).
  - **why_it_matters**: Three flags (_levels_fitted, _refit_pending, _applying_levels), a zero-delay QTimer and a re-entrancy guard exist solely to sequence two writers of the same state. It is the least obvious code in the cluster and the most likely thing to break when Qt6 changes visibility and paint ordering during construction.
  - **proposed_qt6_design**: Make the level mapping a single owned value on PlotRanges with an explicit precedence (fitted > estimated > default) applied once at the end of set_panels(). The plot item then only reports a candidate range; nothing needs a timer, and setVisible() stops carrying business logic.
  - **effort**: large

  - **title**: Two writers of the same axis state, with the threshold constant duplicated across modules
  - **file**: src/audian/timeplot.py
  - **line**: 354
  - **evidence**: TimePlot._view_resized sets `self.getAxis('left').setStyle(showValues=show)` from the view-box height against TICK_VALUES_MIN_HEIGHT (timeplot.py:23, 354-357). DataBrowser.lane_axes sets showValues on the same axis from the row height (databrowser.py:5891-5899) via row_shows_tick_values, whose docstring (databrowser.py:5903-5914) records that the two disagreed over a 2 px band until PLOT_FRAME_HEIGHT was subtracted.
  - **why_it_matters**: The fix is 'keep two independent computations numerically equal' - the classic shape of a regression waiting for the next layout change. Qt6 changes widget metrics (fractional DPR, Fusion), so they will diverge again.
  - **proposed_qt6_design**: One owner. Let the plot decide from its own view box, expose `shows_tick_values` as a read-only property plus a signal, and have the browser read it for the caption/rail decision instead of recomputing it. Delete row_shows_tick_values and the TICK_VALUES_MIN_HEIGHT import at databrowser.py:52.
  - **effort**: medium

  - **title**: The data-item protocol is duck-typed by hasattr, with no declared interface
  - **file**: src/audian/timeplot.py
  - **line**: 172
  - **evidence**: `hasattr(item, 'set_selected')` / `hasattr(item, 'set_dense')` / `getattr(item,'apply_theme') or getattr(item,'polish')` (timeplot.py:172-181); `hasattr(item,'set_view_range')` (spectrogramplot.py:387); `hasattr(item,'setLevels')` (spectrogramplot.py:614); `hasattr(browser,'region_menu_at')` (rangeplot.py:51); `hasattr(taxis,'makeStrings')` (fulltraceplot.py:1202); `hasattr(self.cbar,'sigLevelsChanged')` (spectrogramplot.py:190); `hasattr(axt,'sigHoverValue')` (databrowser.py:1709); and `"wheelEvent" in SelectViewBox.__dict__` (databrowser.py:1912).
  - **why_it_matters**: There is no single statement of what a data item is, so a plugin trace that forgets update_plot() fails silently at paint time rather than at registration, and the migration has no checklist of methods to preserve. databrowser.py:1912 in particular feature-detects a sibling class it imports directly.
  - **proposed_qt6_design**: Declare typing.Protocol classes - DataItem (update_plot, isVisible, apply_theme), Selectable (set_selected/set_dense), Viewported (set_view_range), Levelled (setLevels) - and check them once at add_item() time. Replace the hasattr chains with protocol checks and delete the __dict__ probe.
  - **effort**: medium

  - **title**: Navigator column alignment is a measure-write-remeasure convergence loop over rounded global coordinates
  - **file**: src/audian/fulltraceplot.py
  - **line**: 1072
  - **evidence**: _do_sync_left_margin() activates the layout (1075), measures a lane's view-box left edge in global pixels via mapToGlobal(mapFromScene(...)) (1027-1037), measures its own, writes the delta into every row's left axis width (1100-1106), re-activates the layout and re-schedules itself (1107-1108). MAX_ALIGN_STEPS=4 (1063) is a fuse against non-convergence; the 0 ms _align_timer (line 450) coalesces the many sigResized signals feeding it (455).
  - **why_it_matters**: A feedback loop whose input is quantised to integer pixels (mapFromScene returns QPoint) and whose damping is a hard step limit. Qt6's fractional scaling changes the rounding, so the loop's convergence behaviour changes even though not one line of it does.
  - **proposed_qt6_design**: Stop measuring. The browser already computes the stack-wide lane_left_width (databrowser.py:6748) and already aligns the shared time axis (align_time_axis, databrowser.py:6894). Push that one number into FullTracePlot.set_left_margin() and delete _viewbox_left, _align_target, _align_steps, MAX_ALIGN_STEPS and the align timer.
  - **effort**: medium

  - **title**: The navigator polls a multiprocessing job with a backoff QTimer because CompressedData is not a QObject
  - **file**: src/audian/fulltraceplot.py
  - **line**: 442
  - **evidence**: self._timer (442-444) drives plot_data(); _plot_data() checks cdata.is_busy(), copies shared memory under a non-blocking lock (737-744) and calls _schedule_retry() (747), which doubles the interval from RETRY_MIN_MS=250 to RETRY_MAX_MS=2000 (700-704). CompressedData (compresseddata.py:108) is a plain object over multiprocessing.Process + Array exposing only is_busy()/progress()/get_lock() - no completion signal.
  - **why_it_matters**: The navigator can be up to two seconds stale, a cross-process lock is taken in the GUI thread, and a whole error-swallowing wrapper exists (706-721) precisely because the work runs in a timer callback. It is the only place in the cluster where an exception is printed to stderr and the feature silently degrades.
  - **proposed_qt6_design**: Make the compression job report completion - a QObject wrapper with sigProgress/sigFinished driven by a pipe + QSocketNotifier, or concurrent.futures with a watcher. The navigator then redraws on a signal; the backoff timer, the try/except and the lock-in-paint all disappear.
  - **effort**: medium

  - **title**: FullTracePlot.close() overrides QWidget.close() incompatibly and is called from __del__
  - **file**: src/audian/fulltraceplot.py
  - **line**: 565
  - **evidence**: `def close(self):` stops two timers and calls self.compressed_data.close(); it never calls super().close() and returns None where QWidget.close() returns bool. __del__ (562-563) calls it. Only the timer stop sits inside try/except RuntimeError (567-572).
  - **why_it_matters**: Anything calling datafig.close() expecting the widget to close gets a resource teardown and a still-open widget. Under PySide6 the __del__ path can run after the C++ object is destroyed, and the unguarded compressed_data.close() will then raise during interpreter shutdown.
  - **proposed_qt6_design**: Rename to shutdown() (or override closeEvent), call it explicitly from DataBrowser teardown, drop __del__, and guard the whole body rather than one statement.
  - **effort**: small

  - **title**: Navigator rows are bare pg.PlotItem with attributes bolted on from outside, while lanes are RangePlot
  - **file**: src/audian/fulltraceplot.py
  - **line**: 470
  - **evidence**: `axt.channel = channel` is monkey-patched onto a plain pg.PlotItem, with a five-line comment (463-469) explaining that LabelOverlay would otherwise draw channel 0 on every row; databrowser.py:4448-4451 then does `ax.annotations = overlay` on the same objects - the very attribute RangePlot declares as a real slot (rangeplot.py:19).
  - **why_it_matters**: Two unrelated plot types satisfy the same overlay contract by convention alone. The overlay code cannot type-check either, and extending the contract means remembering a second, undeclared place.
  - **proposed_qt6_design**: Give the navigator row a small declared class (NavigatorRow(pg.PlotItem) with channel and annotations), or reuse RangePlot with mouse interaction disabled. EventOverlay/LabelOverlay then take one declared type.
  - **effort**: medium

  - **title**: SpectrogramPlot inherits TimePlot's amplitude vocabulary and then overrides each member to mean something else
  - **file**: src/audian/spectrogramplot.py
  - **line**: 113
  - **evidence**: Y_TOP_PAD is zeroed (133) because a frequency axis needs no caption headroom; update_axis_label() is replaced (277-300) because data_unit() is the wrong question; amplitudes(t0,t1) returns the frequency range (608-610); range() adds a z branch (597-606); and the inherited reset_y_range() branches on `self.y() in Panel.amplitudes` (timeplot.py:286) to undo the inheritance at runtime.
  - **why_it_matters**: Every shared method carries an if-frequency-else-amplitude somewhere, discriminated by a single-character axis-spec string. That is a type test spelled as a character comparison, and it is why reset_y_range needs 54 lines of docstring.
  - **proposed_qt6_design**: Extract the genuinely shared parts of TimePlot (caption, zero line, playback marker, hover, dense/selected styling, tick-value threshold) into a LanePlot base; make TracePlot and SpectrogramPlot siblings on it so amplitudes()/range()/update_axis_label() each have exactly one meaning.
  - **effort**: large

  - **title**: TimeAxisItem.tickStrings mutates the axis label as a side effect of formatting ticks
  - **file**: src/audian/timeaxisitem.py
  - **line**: 242
  - **evidence**: tickStrings() calls makeStrings() and then setLabel('Time','s') / setLabel(units) / setLabel(f'{label} ({units})') (247-253) - the label changes from inside pyqtgraph's paint-time tick-string callback depending on how far the view is zoomed. makeStrings() (139-240) doubles as a public hover formatter called from fulltraceplot.py:1204 with min_spacing=0.01.
  - **why_it_matters**: setLabel invalidates layout, so a paint can trigger a relayout; and the label is only correct after a paint, which is one reason align_time_axis needs a QTimer.singleShot re-run (databrowser.py:6892).
  - **proposed_qt6_design**: Split the pure formatter (values, mode, spacing) -> strings out of the axis into a module-level function used by both tickStrings and the navigator readout; recompute the label in set_starttime_mode() and on range change, never inside tickStrings.
  - **effort**: medium

  - **title**: TraceItem manages a signal connection from inside a geometry getter
  - **file**: src/audian/traceitem.py
  - **line**: 158
  - **evidence**: max_pixel(vb) disconnects the previous viewbox's sigResized inside a bare try/except (TypeError, RuntimeError), connects the new one, and caches _max_pixel - all as a side effect of being asked how wide the view box is (167-180). _vb_resized then calls update_plot() (196), so a resize re-enters the draw path through a getter.
  - **why_it_matters**: Connection lifetime is tied to a call that looks pure, the except swallows real errors, and nothing disconnects when the item is removed from a plot - a stale connection into a deleted item is exactly the PySide6 failure mode that differs from PyQt5.
  - **proposed_qt6_design**: Connect once when the item is added to a plot (RangePlot.add_item already knows) and disconnect in an explicit detach; make max_pixel() a pure read of the cached value.
  - **effort**: small

  - **title**: SelectViewBox writes application state through an externally injected attribute
  - **file**: src/audian/selectviewbox.py
  - **line**: 39
  - **evidence**: publish_region_mode() does getattr(self,'browser') / getattr(browser,'gui') / hasattr(gui,'region_mode_for_modifiers') and then assigns browser.region_mode_override = mode (49-56). The `browser` attribute is injected from rangeplot.py:45 (`view.browser = browser`) with a comment explaining why.
  - **why_it_matters**: A view box mutates a browser field to smuggle a value into the browser's next slot invocation, deliberately to avoid widening a signal - an implicit temporal coupling between two signals emitted one line apart (selectviewbox.py:145-149).
  - **proposed_qt6_design**: Widen the signal: emit one sigRegionSelected(channel, rect, scene_pos, modifiers) and let the browser resolve the mode. Delete view.browser, region_mode_override and publish_region_mode.
  - **effort**: small

  - **title**: pyqtgraph menu and orphan-widget teardown is a global workaround the cluster depends on, with one hole left open
  - **file**: src/audian/rangeplot.py
  - **line**: 39
  - **evidence**: Every plot calls setMenuEnabled(False) + theme.strip_pg_menus() (rangeplot.py:33-39, spectrogramplot.py:200-201, fulltraceplot.py:475-482), which walks pyqtgraph's ctrlMenu tree, releases QWidgetActions, deletes ~9 parentless QMenus per PlotItem and reparents the leftover 640x480 Ui_Form (theme.py:1775-1910), plus a global sweep at databrowser.py:1826. But pg.ColorBarItem builds its ColorMapMenu lazily on right-click regardless (ColorBarItem.py:358-368), so 16 colour bars can still each spawn a menu tree.
  - **why_it_matters**: This is the Wayland-surface mitigation the whole design leans on; it depends on pyqtgraph's private ctrl/ctrlMenu layout, and it has a documented hole. A pyqtgraph bump taken with the Qt6 move can invalidate the walk with no error at all.
  - **proposed_qt6_design**: Pass colorMapMenu=False at spectrogramplot.py:180 to close the hole now. Longer term add one smoke test that counts QApplication.topLevelWidgets() after opening a 16-channel file, so the workaround has a regression guard instead of a comment.
  - **effort**: medium

  - **title**: A 140-line micro-benchmark ships inside the trace item module
  - **file**: src/audian/traceitem.py
  - **line**: 325
  - **evidence**: `if __name__ == '__main__':` from line 325 to 463 - a numba import, seven alternative min/max implementations and a timeit harness - in a 463-line module whose class ends at line 322.
  - **why_it_matters**: It is 30% of the file and hides how small the class actually is; during a migration it is 140 lines of noise in every diff of this file.
  - **proposed_qt6_design**: Move to benchmarks/decimation.py or a slow-marked test. The measured conclusion (reduceat with out= is fastest) is already encoded in peaks() at traceitem.py:289-290.
  - **effort**: small

  - **title**: The Signal import shim is duplicated five times and can never take its first branch
  - **file**: src/audian/timeplot.py
  - **line**: 6
  - **evidence**: `try: from PyQt5.QtCore import Signal / except ImportError: from PyQt5.QtCore import pyqtSignal as Signal` at timeplot.py:6-9, selectviewbox.py:4-7, spectrogramplot.py:8-11, and outside the cluster at eventoverlay.py:76-79 and databrowser.py:14-17. Both branches name PyQt5, which exposes no `Signal`, so the except branch always runs.
  - **why_it_matters**: It looks like a binding-abstraction layer and is not one, so the migration will 'update the shim' five times and still leave PyQt5 hard-coded in the other import lines of the same files.
  - **proposed_qt6_design**: One module, audian/qt.py, re-exporting Signal/Slot/Qt/QtCore/QtGui/QtWidgets from PySide6 and setting PYQTGRAPH_QT_LIB before pyqtgraph is imported. Every cluster file imports from it and the try/except blocks disappear.
  - **effort**: small

- **behavior_contract**:
  - Waveform decimation: when more than one sample falls on a device pixel the trace is
  - drawn as interleaved per-bin min/max at half-step x spacing (traceitem.py:229-243); at
  - one sample per pixel raw samples are drawn with a thicker pen (254), and individual
  - sample dots appear once there are >=10 device pixels per sample (255-258). The step is
  - derived from the view box's device width, never the screen's (158-180).
  - Resizing a lane re-decimates it: without the sigResized-driven update_plot a lane keeps
  - the step it computed at first layout (traceitem.py:182-196), so widening a window must
  - not leave a lane drawing 18 points across ten seconds.
  - Nothing in a paint, hover or timer path ever indexes outside the loaded buffer:
  - buffer_range() clamps and step-aligns (traceitem.py:198-215), get_amplitude() reads
  - data.buffer directly and returns (x, None) outside it (293-322), and visible_block()
  - clamps with the deliberate -1 at the end of data (spectrogramplot.py:415-425). Breaking
  - this causes a disk read plus a full re-filter inside a repaint.
  - The selected channel is the only trace painted in the saturated primary colour; with
  - more than theme.DENSE_CHANNELS on screen everything goes hairline and unselected lanes
  - dim harder (traceitem.py:76-88, timeplot.py:162-186). Colour is never the only cue - the
  - caption also goes bold (timeplot.py:322-328) and the channel rail marks the same row.
  - A 'filtered' trace whose filter is a pass-through is painted in the raw colour, and the
  - navigator strip follows it, so one recording is never two colours in one window
  - (traceitem.py:99-101, fulltraceplot.py:578-608, 629-637).
  - Spectrogram upload: only the visible time range plus 1.5x of it on each side is
  - converted to dB and uploaded, at >=2 columns per device pixel (specitem.py:25-33,
  - 115-172). Panning inside the pad must not re-upload; entering or leaving mean mode must
  - always re-upload (specitem.py:59-76).
  - Mean mode: the caption reads 'MEAN 00-15' with runs collapsed and folded to 'N ch' past
  - 17 characters (spectrogramplot.py:35-58, 304-326); the mean gets its own colour mapping
  - rather than a single channel's (328-346, 491-530); and the power readout and the colour
  - ramp are computed over exactly the channels the image averages (specitem.py:78-113).
  - Colour mapping is fitted once, by the first *visible* channel's panel
  - (spectrogramplot.py:460-482): floor = median of the in-view dB distribution + 3 dB, top
  - = 95% of the way to the peak, snapped to 5 dB, span clamped to 20-80 dB (491-555).
  - Showing a hidden spectrogram re-arms the fit, and the refit survives
  - PlotRanges.set_powers() overwriting it (432-458, 612-622).
  - The slim colour bar shows exactly three mono tick labels with the unit riding on the top
  - one (spectrogramplot.py:579-585); dragging its handles relabels it but must NOT switch
  - off the automatic fit (587-595).
  - Filter cutoff handles are draggable only in filter mode; in label/region mode a rubber-
  - band drag that starts on a cutoff line selects a region instead of moving the cutoff
  - (spectrogramplot.py:245-273).
  - The power sidebar curve is the mean over the same in-view block the image draws, floored
  - at -200 dB and filled to a -200 dB zero line (spectrogramplot.py:390-399, 77-85).
  - Y axis: at most three labelled ticks (yaxisitem.py:26-31, 169-170), and never exactly
  - one - the spacing steps back a rung rather than leave only the zero line (181-191).
  - Below 48 px of view-box height the tick values, the y label and the in-plot caption all
  - disappear together (timeplot.py:348-366).
  - Frequency ticks are SI-prefixed (10, not 10000) even when the rotated label is hidden,
  - and the corner caption then carries 'frequency (kHz)' and tracks the prefix as the view
  - zooms (yaxisitem.py:108-149, spectrogramplot.py:304-326, 348-353).
  - Amplitude units: a real SI unit is prefixed by pyqtgraph and shown as 'amplitude (mV)';
  - a non-SI unit such as 'a.u.' is shown verbatim with the ticks NOT rescaled - 'ma.u.'
  - with ticks multiplied by 1000 is the bug this guards (timeplot.py:29-36, 296-320).
  - Time axis: three start-time modes - seconds from the start of the recording ('REC (s)'),
  - absolute time of day ('h:m:s'), and per-file offsets ('File') - with exactly as many
  - sub-second digits as the tick spacing resolves (timeaxisitem.py:57-70, 205-239). The
  - axis caption is placed at -left_margin so it lines up with the lanes' left column
  - (256-268, 31-42).
  - Double-clicking a y axis resets it, and only when the press lands inside the axis's own
  - boundingRect - not on the 2 px pyqtgraph click fringe over the waveform, and not at all
  - on a collapsed axis (yaxisitem.py:55-96). Amplitude resets to a fit of the data (or
  - stays at +-1 in fixed mode); frequency resets to the configured opening band
  - (timeplot.py:231-294). A plain single click on the axis still pans as before.
  - An amplitude auto-fit reserves 6% headroom at the top so the topmost tick never renders
  - on the same scan line as the 'CH nn' caption (timeplot.py:43-51, 420-422); the caption
  - sits S8 from the left and S4 from the top of the view box and follows every pan, zoom
  - and resize (331-346).
  - Wheel: a plain wheel scrolls the channel-stack scroll area (the view box must ignore
  - it), Ctrl+wheel zooms time, Shift+wheel zooms y (selectviewbox.py:84-112,
  - databrowser.py:1910-1918).
  - Left/middle drag draws a rubber band and finishes as a region action; the modifiers that
  - decide the action are the ones held when the drag *started* (selectviewbox.py:135-136),
  - and Shift = play / Alt = analyse override the toolbar's region mode for that one drag
  - (39-56). Exactly one of sigSelectedRegion / sigSelectedRegionAt is connected so a region
  - is never acted on twice (rangeplot.py:49-54).
  - Right-drag scales about the press point; finishing a pan or a right-drag pushes a zoom-
  - history entry, so Backspace / Shift+Backspace / Alt+Backspace step back, forward and
  - home through the zooms (selectviewbox.py:169-222).
  - Any deliberate zoom - wheel, right-drag or rubber band - sets a user lock that stops
  - automatic amplitude refits from fighting it (selectviewbox.py:112, 166, 189, 213;
  - plotranges.py:82-97; databrowser.py:6992).
  - Hovering a lane reports channel, time and value to the status bar (timeplot.py:368-369,
  - databrowser.py:1709-1710); hovering the navigator reports the time in every applicable
  - mode plus the file name (fulltraceplot.py:1175-1224).
  - No pyqtgraph context menu is reachable anywhere - on any plot, view box or colour bar
  - (rangeplot.py:82-87, selectviewbox.py:63-69, theme.strip_pg_menus).
  - The navigator defaults to one NAVIGATOR_HEIGHT row showing the selected channel; 'all'
  - mode stacks every visible channel one row each, and the selected-row highlight exists
  - only in 'all' mode (fulltraceplot.py:639-653, 894-926). Only the bottom-most visible row
  - shows time tick values and the axis label (947-969).
  - The navigator envelope is a closed min/max polygon sampled at bin centres, never a
  - polyline through interleaved min/max points - the latter produced a regular sawtooth
  - that is not in the file (fulltraceplot.py:105-198, 860-882).
  - The activity overview shows a filled band from 0 dB to the RMS excess plus vertical
  - spikes to the peak excess on transient bins only, with the SAME dB range on every
  - channel so bins stay comparable across the array (fulltraceplot.py:201-306, 845-858). It
  - is unavailable, and the strip stays on the envelope, when the cached second moment is
  - missing (771-808).
  - The navigator region has two visible grab handles centred on its edges
  - (fulltraceplot.py:309-357). Dragging it moves every lane in single mode and only its own
  - lane in 'all' mode; dragging a lane moves the region back; neither direction loops
  - (fulltraceplot.py:1112-1145). Drags are coalesced to 30 Hz with the final position
  - delivered undelayed (503-508).
  - Clicking the navigator more than two pixels outside the region recentres the region on
  - the click, keeping its width and clamping to [0, tmax]; clicking inside it does nothing
  - (fulltraceplot.py:1149-1173).
  - The navigator's data area stays pixel-aligned with the lanes above it whatever the
  - channel rail and the stack's scroll bar do (fulltraceplot.py:1039-1108), and it never
  - shrinks below one row plus the time axis (947-996).
  - The navigator draws progressively while background compression runs and stops polling
  - once it completes; a failure prints once to stderr and degrades the strip rather than
  - taking the window down (fulltraceplot.py:663-747).
  - A live theme switch re-resolves every pen, brush, font and background on traces, axes,
  - crosshairs, stored markers, colour bars, filter handles, navigator envelopes, activity
  - items and the region - without rebuilding a single plot (traceitem.py:147-154,
  - rangeplot.py:89-107, timeplot.py:140-146, spectrogramplot.py:236-243,
  - timeaxisitem.py:44-55, yaxisitem.py:38-40, fulltraceplot.py:610-659).
  - Startup and layout produce no top-level Qt windows beyond the application's own: every
  - PlotItem's menu tree is torn down and its orphaned control widgets adopted
  - (rangeplot.py:33-39, fulltraceplot.py:475-482, databrowser.py:1826). On Wayland a stray
  - parentless widget becomes a compositor surface.
- **risk**: high - three pyqtgraph event handlers are forked verbatim against private ViewBox/AxisItem state, every interaction gesture lives in an overridden Qt or pyqtgraph event method whose dispatch and coordinate semantics are exactly what Qt6 changes, and the two most delicate behaviours (spectrogram level fitting, navigator column alignment) are timer-sequenced races whose timing assumptions exist only in docstrings.
- **notes**: KEEP PYQTGRAPH. The spec's bar (do not replace without profiling justification) is not met, and the evidence runs the other way. (1) pyqtgraph 0.14.0 is already installed and already supports PySide6 first-class - pyqtgraph/Qt/__init__.py:228 selects PySide6, and functions.py:2113-2126 carries a PySide6-specific QByteArray workaround for arrayToQPath, i.e. the library is actively maintained against this exact target. (2) Three of the nine files - traceitem.py, specitem.py, rangeplot.py - contain no PyQt5 import at all and port for free. (3) All of the cluster's performance work sits *above* pyqtgraph rather than around it: the min/max pyramid decimation (traceitem.py:261-291), the cropped strided dB upload with containment hysteresis (specitem.py:139-172), pen-change memoisation (traceitem.py:118-135), setStyle write-elision (databrowser.py:106-118), SignalProxy coalescing (fulltraceplot.py:503-508, databrowser.py:1754-1765). Replacing pyqtgraph discards all of that and re-earns nothing. (4) The measured costs quoted throughout the docstrings (23.4 ms decibel + 22 ms setImage per channel; 44.4 vs 15.4 ms per repaint; ~111 ms per region-drag step on 16 channels) are data-preparation costs, not raster costs, so a faster renderer would not move them.\n\nWhat the migration should do to pyqtgraph instead: (a) pin the version explicitly in pyproject.toml (currently unpinned at line 15) and set PYQTGRAPH_QT_LIB=PySide6 before the first pyqtgraph import, so binding selection is deterministic rather than dependent on what else happens to be installed; (b) delete the three forked handlers - SelectViewBox.wheelEvent, SelectViewBox.mouseDragEvent, YAxisItem.updateAutoSIPrefix - in favour of delegation, which removes every private-API touch point in one pass and is the highest-value single change in this cluster; (c) pass colorMapMenu=False at spectrogramplot.py:180.\n\nOn the two QTimers the brief calls out: fulltraceplot.py:442 (_timer) is a 250->2000 ms backoff poll over a multiprocessing job that has no completion signal because CompressedData (compresseddata.py:108) is a plain object - fixable by making the job report completion, nothing Qt6-specific. fulltraceplot.py:450 (_align_timer) is a 0 ms coalescer for a self-re-scheduling geometry convergence loop (line 1108) fused at four steps (line 1063); it is the one place where a Qt6 metric change (fractional DPR, Fusion axis widths) can alter behaviour without any code changing, and it should be replaced by pushing the browser's already-computed lane_left_width (databrowser.py:6748) into the navigator. Neither timer is a Qt5-ism; both are architecture.\n\nSuggested ordering: (1) audian/qt.py compat module plus PYQTGRAPH_QT_LIB; (2) mechanical scoped-enum pass over the twelve unscoped sites listed above; (3) constructor ordering (super().__init__ first) at traceitem.py:40, rangeplot.py:11 and timeplot.py:54, before anything else runs under PySide6; (4) delete the forked pyqtgraph handlers; (5) then the architectural items. Steps 1-3 are prerequisites for the application even starting; step 4 is what makes the port durable.\n\nCoverage note: I found no exec_(), QRegExp, QDesktopWidget, QVariant, QFontMetrics.width() or QWheelEvent.delta() usage in this cluster, and no high-DPI attribute setup anywhere in the package. QApplication.desktop() was already removed (see the docstring at traceitem.py:158-166); QVariant survives only in labeloverlay.py:894-1131 and app.exec_() only at audian.py:5033, both outside this cluster.
