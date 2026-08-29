# Recon: panels-layout

- **cluster**: panels-layout
- **purpose**: Three unrelated concerns share this cluster label. (1) The real panel/lane layout stack: `panels.py` models one plot row per channel as a 2-3 character axis-letter spec; `panelsplitter.py` is an in-scene `pg.GraphicsWidget` grab band that turns a drag on the trace/spectrogram boundary into a browser callback; `plotranges.py` keeps a per-axis-letter, per-channel copy of every view range and writes it into pyqtgraph view boxes. (2) `controlpanel.py` is NOT the app's parameter bar — it is an optional session-global step-plot strip drawing a bundle's `StepTrack`, built to mimic the shared time axis widget structure so it inherits that axis's measured margins. (3) `layers.py`, `windowing.py` and `alignment.py` are the annotation data model, the array windowing/decimation primitives, and the TOML session-bundle *time* alignment reader respectively — none is about Qt, layer stacking, FFT parameters, or axis alignment, and none imports Qt at all. The actual layout authority for this cluster lives outside it, in `DataBrowser.lane_geometry` / `update_stretches` / `adjust_layout` / `apply_panel_split` / `align_time_axis`.
- **public_surface**:
  - **name**: Panel
  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/panels.py
  - **kind**: class
  - **base**: object
  - **summary**: One plot row of one channel-stack lane, identified by a 2-3 char axis spec (e.g. 'ty', 'tfp', 'spacer'). Holds .row (grid row index), .axs (one RangePlot per channel), .axcs (colour bars). Predicates is_trace/is_spectrogram/is_power/is_spacer drive every layout decision in databrowser. Imported by databrowser.py:38, audian.py:39, spectrogramplot.py:16, timeplot.py:12, plotranges.py:12.

  - **name**: Panels
  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/panels.py
  - **kind**: class
  - **base**: dict
  - **summary**: Ordered name->Panel map; .fill(data) creates panels from traces, .insert_spacers() interleaves zero-height spacer rows, .get_panel(viewbox) reverse-maps a ViewBox to its Panel (the hook update_ranges uses), .add_power_ax, .show_grid, .update_plots. Dict insertion order IS the layout order.

  - **name**: resolve_colormap
  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/panels.py
  - **kind**: function
  - **base**: 
  - **summary**: ColorMap|name|theme index -> pg.ColorMap, routed through theme.spectrogram_colormap so the ramp matches the theme. Imported by spectrogramplot.py:16.

  - **name**: PanelSplitter
  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/panelsplitter.py
  - **kind**: class
  - **base**: pg.GraphicsWidget
  - **summary**: The only in-scene item in the app that accepts the mouse. Zero-height layout row with a PANEL_SPLIT_HANDLE_HEIGHT-tall boundingRect/shape so it overlaps both neighbours. Reports drags to DataBrowser via panel_split_heights / drag_panel_split / finish_panel_split / reset_panel_split. Constructed at databrowser.py:1697.

  - **name**: HANDLE_Z
  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/panelsplitter.py
  - **kind**: constant
  - **base**: 
  - **summary**: 500: above the two plots it straddles, below the lane frame's 1000, so the scene routes a press to the band rather than to the trace plot added last.

  - **name**: PlotRange
  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/plotranges.py
  - **kind**: class
  - **base**: object
  - **summary**: One axis letter's state: rmin/rmax/rstep/min_dr limits, rdefault/rdefault_min opening band, per-channel r0[]/r1[], the plots registered on it as x/y/z (axxs/axys/axzs), user_locked plus the sigUserZoomed subscriptions that set it, crosshair marker + stored marker, and ~20 navigation verbs (zoom_in, home, end, snap, auto, reset, default_view, center, set_powers).

  - **name**: PlotRanges
  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/plotranges.py
  - **kind**: class
  - **base**: dict
  - **summary**: Axis letter -> PlotRange for every letter in 't'+'xyu'+'fw'+'pq' (setup). add_plot(ax) registers a RangePlot on its x/y/z letters. __init__ installs 21 partial(_apply, self, name) instance attributes so plot_ranges.zoom_in('fw', ...) fans out over letters. Imported by databrowser.py:40.

  - **name**: ControlPanel
  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/controlpanel.py
  - **kind**: class
  - **base**: QWidget
  - **summary**: Session-global step-track strip below the lanes and above the shared time axis (databrowser.py:1946). Zero height while the controls layer is off. link_view() x-links it to a lane; set_margins()/set_rail_width() take the numbers align_time_axis measured; wanted_height() is pure theme arithmetic so one view unit is one pixel; update_plot() rebuilds staircases through windowing.window_steps.

  - **name**: Layer / PointSeries / PointLayer / SpanLayer / StepTrack
  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/layers.py
  - **kind**: class
  - **base**: object / dataclass / Layer
  - **summary**: The Qt-free annotation model: instants, intervals, held values, with float64 C-contiguous ascending arrays, precomputed SpanLayer.max_end and .disjoint, and StepTrack per-channel frozen ranges. Imported by databrowser.py:91, session.py:82, eventoverlay.py:82, controlpanel.py:71.

  - **name**: LAYER_* / TRACK_* / KIND_*
  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/layers.py
  - **kind**: constant
  - **base**: 
  - **summary**: Layer identity strings (LAYER_CONTROLS is what ControlPanel switches on) and the three shape kinds.

  - **name**: window_points / count_columns / window_spans / merge_spans / window_steps / step_envelope / pair_runs / PairResult
  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/windowing.py
  - **kind**: function
  - **base**: 
  - **summary**: Array-in/array-out windowing and decimation for the three annotation shapes; no Qt, no polars, no audian imports. window_spans raises ValueError(SPAN_NO_TIME) on a NaN span. Imported by session.py:61, eventoverlay.py, controlpanel.py:69.

  - **name**: SessionMeta / Alignment / Integrity / RecordingCheck / SplitCoverage / BundleRef
  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/alignment.py
  - **kind**: class
  - **base**: object / frozen dataclass
  - **summary**: Reads *_metadata.toml: the device-clock-to-recording-clock fit, its trust tri-state, log integrity, provenance checks against the open WAV, and split-recording coverage. Imported by databrowser.py:90 (SplitCoverage) and session.py:62.

  - **name**: verify_sha256 / find_bundles / find_bundle
  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/alignment.py
  - **kind**: function
  - **base**: 
  - **summary**: On-demand tier-3 content check with an mtime-keyed cache, and bundle discovery by recording file name.

- **qt5_api_usage**:
  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/panelsplitter.py
  - **line**: 28
  - **api**: from PyQt5.QtCore import QPointF, QRectF, QSizeF, Qt
  - **qt6_replacement**: from PySide6.QtCore import QPointF, QRectF, QSizeF, Qt
  - **severity**: breaking

  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/panelsplitter.py
  - **line**: 29
  - **api**: from PyQt5.QtGui import QPainterPath
  - **qt6_replacement**: from PySide6.QtGui import QPainterPath
  - **severity**: breaking

  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/panelsplitter.py
  - **line**: 62
  - **api**: self.setAcceptedMouseButtons(Qt.LeftButton) -- unscoped enum used as a MouseButtons flag
  - **qt6_replacement**: Qt.MouseButton.LeftButton (still resolves under PySide6 6.11 forgiveness mode; the brief requires scoped)
  - **severity**: cosmetic

  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/panelsplitter.py
  - **line**: 63
  - **api**: self.setCursor(Qt.SplitVCursor)
  - **qt6_replacement**: Qt.CursorShape.SplitVCursor
  - **severity**: cosmetic

  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/panelsplitter.py
  - **line**: 99
  - **api**: if which == Qt.MaximumSize (QGraphicsLayoutItem.sizeHint 'which' argument)
  - **qt6_replacement**: Qt.SizeHint.MaximumSize
  - **severity**: cosmetic

  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/panelsplitter.py
  - **line**: 175
  - **api**: if ev.button() != Qt.LeftButton (mousePressEvent)
  - **qt6_replacement**: Qt.MouseButton.LeftButton
  - **severity**: cosmetic

  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/panelsplitter.py
  - **line**: 235
  - **api**: if ev.button() != Qt.LeftButton (mouseDoubleClickEvent)
  - **qt6_replacement**: Qt.MouseButton.LeftButton
  - **severity**: cosmetic

  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/panelsplitter.py
  - **line**: 88
  - **api**: def sizeHint(self, which, constraint=QSizeF()) -- virtual override of QGraphicsLayoutItem::sizeHint
  - **qt6_replacement**: Unchanged signature, but PySide6 dispatches virtuals by arity/name rather than PyQt5's signature matching; keep both positional parameters and re-verify the zero-max-height contract under Qt6's layout engine (this is what keeps the spacer row 0 px)
  - **severity**: behavior-change

  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/panelsplitter.py
  - **line**: 101
  - **api**: return QSizeF(16777215.0, 0.0) -- QWIDGETSIZE_MAX hardcoded as a float literal
  - **qt6_replacement**: Still 16777215 in Qt6 and still not exposed to Python by PySide6; name the constant instead of repeating the literal
  - **severity**: cosmetic

  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/panelsplitter.py
  - **line**: 127
  - **api**: def setGeometry(self, rect) -- Python override collapses QGraphicsWidget's two setGeometry overloads to one
  - **qt6_replacement**: Keep the QRectF override (pyqtgraph's QGraphicsGridLayout calls that one) but accept *args so a 4-scalar call from Qt6/pyqtgraph does not TypeError; the prepareGeometryChange() inside it is what keeps the hit area on the painted line
  - **severity**: behavior-change

  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/panelsplitter.py
  - **line**: 139
  - **api**: def paint(self, painter, *args)
  - **qt6_replacement**: Fine as written; Qt6 still passes (painter, option, widget). No QPainter.HighQualityAntialiasing use here
  - **severity**: cosmetic

  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/panelsplitter.py
  - **line**: 215
  - **api**: ev.scenePos().y() on QGraphicsSceneMouseEvent (also line 178)
  - **qt6_replacement**: Unchanged in Qt6 -- QGraphicsSceneMouseEvent.scenePos()/pos() were NOT removed; only QMouseEvent.pos() was deprecated in favour of position(). No change needed, but do not let a blanket pos()->position() sweep touch these
  - **severity**: cosmetic

  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/panelsplitter.py
  - **line**: 210
  - **api**: Docstring invariant: 'The scene of a GraphicsLayoutWidget is its viewport at 1:1 ... so a scene delta IS a device-pixel delta'
  - **qt6_replacement**: False under Qt6. Qt5 here ran unscaled (the app never sets AA_EnableHighDpiScaling); Qt6 always scales and defaults the rounding policy to PassThrough, so scene units are LOGICAL px and a fractional DPR makes them non-integer device px. The drag stays self-consistent (browser geometry is logical too) but the comment and any px-exactness assertion must be restated in logical pixels
  - **severity**: behavior-change

  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/controlpanel.py
  - **line**: 67
  - **api**: from PyQt5.QtWidgets import QHBoxLayout, QWidget
  - **qt6_replacement**: from PySide6.QtWidgets import QHBoxLayout, QWidget
  - **severity**: breaking

  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/controlpanel.py
  - **line**: 405
  - **api**: ratio = widget.devicePixelRatioF()
  - **qt6_replacement**: API unchanged, VALUE changes: Qt5 returned 1.0 on a HiDPI screen because scaling was never enabled; Qt6 returns 1.5/2.0. The decimation budget in pixels() therefore changes on real hardware while the offscreen tests (DPR 1) stay green
  - **severity**: behavior-change

  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/controlpanel.py
  - **line**: 148
  - **api**: theme.strip_pg_menus(self.plot) plus the manual adoption of self.plot._audian_ctrl_widgets at 152-154
  - **qt6_replacement**: theme.strip_pg_menus walks pyqtgraph's PlotItem.ctrlMenu and calls QWidgetAction.releaseWidget (theme.py:1820-1826); QWidgetAction stays in QtWidgets but QAction moves to QtGui, and the menu tree pyqtgraph builds under PySide6 must be re-measured. Verify the 0-parentless-widget census still holds
  - **severity**: behavior-change

  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/controlpanel.py
  - **line**: 131
  - **api**: pg.GraphicsLayoutWidget() (also theme.style_figure, fig.ci.layout)
  - **qt6_replacement**: pyqtgraph 0.14.0 picks its binding by probing imports, so PyQt5 must be absent from the migration venv (or PYQTGRAPH_QT_LIB pinned) or the binding in use depends on import order -- see docs/qt6/00-foundation.md
  - **severity**: breaking

  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/controlpanel.py
  - **line**: 161
  - **api**: view.sigRangeChanged.connect(self._view_changed); view.sigResized.connect(self._view_changed)
  - **qt6_replacement**: No change needed -- _view_changed(*args) is arity-tolerant, which matters because pyqtgraph's sigRangeChanged emits a third 'changed' argument in some versions
  - **severity**: cosmetic

  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/controlpanel.py
  - **line**: 371
  - **api**: self.labels[name].fill = theme.brush('bg.plot'); .update() -- writing a private pyqtgraph TextItem attribute because there is no setFill
  - **qt6_replacement**: Unchanged by the binding, but it is an unversioned pyqtgraph internal that the migration should pin behind a helper
  - **severity**: cosmetic

  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/panels.py
  - **line**: 145
  - **api**: di.isVisibleTo(plot) / ax.isVisible() / ax.setVisible() -- QGraphicsItem visibility used as the layout's source of truth, reached by duck typing with no Qt import in this file
  - **qt6_replacement**: Unchanged in Qt6. Note the deliberate isVisibleTo-not-isVisible distinction (panels.py:128-147): a blanket refactor to isVisible() reintroduces the 16-channel bug where a lane that was once hidden never drew a spectrogram again
  - **severity**: cosmetic

  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/plotranges.py
  - **line**: 89
  - **api**: view.sigUserZoomed.connect(self._user_zoomed) -- connecting a bound method of a plain (non-QObject) PlotRange to a pyqtgraph ViewBox Signal
  - **qt6_replacement**: PySide6 and PyQt5 differ in how long a connection to a bound method of a non-QObject receiver survives; PlotRange is kept alive by the PlotRanges dict so this holds, but it must be asserted rather than assumed -- if the connection is dropped, user_locked never sets and every hand zoom is silently overwritten by the next auto-fit
  - **severity**: behavior-change

  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/layers.py
  - **line**: 1
  - **api**: (none) -- layers.py, windowing.py and alignment.py import no Qt whatsoever
  - **qt6_replacement**: No migration work. 3 of the 7 files in this cluster (2394 of its 3005 lines) are binding-neutral and should be excluded from the port's blast radius
  - **severity**: cosmetic

- **architecture_problems**:
  - **title**: Nothing in this cluster owns the layout it exists to express
  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/panels.py
  - **line**: 108
  - **evidence**: Panel.add_ax stores a grid row index and a plot, and Panel has no method that sets a height. Every height is written from DataBrowser: update_stretches (databrowser.py:6070-6088) writes QGridLayout row minima and figs[c].setFixedHeight; adjust_layout (6811-6838) writes QGraphicsGridLayout setRowMinimumHeight/setRowStretchFactor; apply_panel_split (6491-6509) writes the same rows again during a drag; lane_geometry (6000-6044), lane_content_height (6195-6221), default_spec_height (6223-6256), panel_split_limits (6258-6272) and panel_split_rows (6274-6343) hold the arithmetic. The 'current layout' cannot be read from any object -- panel_split_heights (6366-6390) recovers it by measuring QGraphicsWidget.geometry().
  - **why_it_matters**: The Qt6 port has to preserve pixel-exact behaviour that is spread over ~350 lines of one 8253-line QWidget, with the model objects (Panel, Panels) unable to state or validate any of it. Any layout regression is diagnosed by screenshot rather than by assertion, and every one of the six height functions has to be re-verified independently against Qt6's layout engine.
  - **proposed_qt6_design**: Extract a Qt-free LaneLayout solver: inputs (viewport height, channel count, spectrogram channels, visible panels per lane, spec_scale, theme constants) -> outputs (lane_h, per-panel row heights, which spacers carry a band, tick-value chrome per row). DataBrowser becomes the applier that walks the solution into QGridLayout/QGraphicsGridLayout. The solver is then testable at Qt5 numbers and Qt6 numbers with no widget, which is the only way to tell a rasteriser apart from a lane that moved.
  - **effort**: large

  - **title**: PanelSplitter is an in-scene item wired directly into DataBrowser by attribute
  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/panelsplitter.py
  - **line**: 57
  - **evidence**: def __init__(self, channel, browser): self.browser = browser, then four hard calls: browser.panel_split_heights (165, 216), browser.drag_panel_split (221), browser.finish_panel_split (229), browser.reset_panel_split (239). Constructed at databrowser.py:1697 with `PanelSplitter(c, self)`.
  - **why_it_matters**: A QGraphicsWidget holds a strong reference to the whole browser, so the splitter cannot be constructed, driven or tested without one; the drag protocol (latch, absolute travel, re-latch on lane change) is split across two files with no declared interface; and the reference cycle (browser -> splitters list -> browser) has to be broken by hand on close.
  - **proposed_qt6_design**: Make PanelSplitter a pg.GraphicsObject with Signals -- sigSplitDragged(float spec_h, float room), sigSplitFinished(), sigSplitReset() -- plus one injected callable `heights_provider(channel) -> (spec_h, room) | None`. DataBrowser connects them once at construction. The band then tests standalone against a stub provider, and the four browser methods become slots with an explicit contract.
  - **effort**: medium

  - **title**: The axis system is stringly typed with a hard capacity ceiling and a silent overflow
  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/panels.py
  - **line**: 40
  - **evidence**: times='t', amplitudes='xyu', frequencies='fw', powers='pq'. ax_spec is a 2-3 char string sliced positionally (x()/y()/z(), 62-69). PlotRanges.setup registers exactly those 12 letters (plotranges.py:700-702) and add_plot raises KeyError on anything else. add_trace (243-254) and add_spectrogram (256-274) search for a free letter and, when none is free, fall through to `axspec = Panel.times[0] + Panel.amplitudes[0]` -- a fourth trace panel silently shares range 'x' with the first. Panel.spacer = 'spacer' is parsed by the same positional accessors, so a spacer answers y()=='p' and is_ypower() is True.
  - **why_it_matters**: The letter is simultaneously an identity, a physical dimension, a dict key and a capacity limit. It caps the app at 3 trace panels and 2 spectrograms with no error, it is why controlpanel.py had to be built outside the Panel machinery at all (its own docstring, lines 13-24), and it makes every layout predicate a substring test that a spacer accidentally satisfies.
  - **proposed_qt6_design**: Replace the char code with an AxisId dataclass (dimension: Enum{TIME, AMPLITUDE, FREQUENCY, POWER}, slot: int) and make PanelSpec a dataclass of (x, y, z|None) or an explicit Spacer sentinel type. PlotRanges keys on AxisId, allocation returns an error instead of aliasing slot 0, and a Spacer is a distinct type that cannot answer is_ypower().
  - **effort**: large

  - **title**: Panel defines __eq__ against a string, making it unhashable and comparisons type-confused
  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/panels.py
  - **line**: 59
  - **evidence**: def __eq__(self, ax_spec): return self.ax_spec == ax_spec. Python then sets __hash__ = None, so no Panel can go in a set or be a dict key; and `panel_a == panel_b` is always False because a Panel is compared against another Panel's identity, not its ax_spec. databrowser.py:6181-6193 works around it by building a set of panel NAMES rather than of panels.
  - **why_it_matters**: A latent trap during a refactor: the natural rewrite of split_spacers or of adjust_layout's revealed-panel bookkeeping to use a set of Panel objects raises TypeError, and any `if panel in panels` membership test silently answers False.
  - **proposed_qt6_design**: Delete __eq__ (nothing calls `panel == 'ty'`; grep shows no such use) and let identity equality plus the default hash stand, or give Panel a proper __eq__/__hash__ pair over (name, ax_spec).
  - **effort**: small

  - **title**: PlotRanges installs 21 methods as instance attributes via functools.partial
  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/plotranges.py
  - **line**: 669
  - **evidence**: for m in ['zoom_in', ..., 'default_view', 'center']: setattr(self, m, partial(PlotRanges._apply, self, m)). Each partial closes over self, so the dict holds 21 references to itself. The methods are invisible to static analysis, which is why databrowser.py:3116 defensively writes `if hasattr(self.plot_ranges, 'auto_fit')` and carries a dead else-branch calling a differently-shaped `auto`. Callers reach them by string name too: apply_ranges(amplitudefunc, axspec) at databrowser.py:7074-7078 and audian.py:3536 pass 'default_view' as data.
  - **why_it_matters**: A self-referential cycle in an object that owns Qt plot references; no IDE, type checker or ruff pass can see the API; and a dict key colliding with a verb name would shadow it. During the port these 21 verbs are the entire keyboard navigation surface and none of them can be checked statically.
  - **proposed_qt6_design**: Write the fan-out as a real method: def apply(self, verb: str, axspec, *args) -> None, or better, generate explicit methods with a decorator so the names exist on the class. Keep the string-keyed dispatch only at the QAction boundary where it is genuinely data.
  - **effort**: small

  - **title**: Viewport state is duplicated between PlotRange.r0/r1 and the ViewBox, reconciled by one global reentrancy flag
  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/plotranges.py
  - **line**: 259
  - **evidence**: set_ranges writes ax.setXRange/setYRange/setZRange (259-265) from its own r0/r1; the view box echoes back through sigRangeChanged -> DataBrowser.update_ranges (databrowser.py:6963-6995), which compares against the stored copy with a 1e-6-relative epsilon (same_range, 6997-7004) and writes back. The loop is broken by a single browser-wide boolean, DataBrowser.setting, taken by the `updating()` context manager (1459-1474), whose own docstring says leaking it 'silently freezes scrolling and zooming for the rest of the session'.
  - **why_it_matters**: This is the answer to 'who owns viewport state': nobody -- there are two copies and a mutex. The flag is not per axis or per channel, so any programmatic range change suppresses every user-driven range change for its duration, and Qt6's different signal/relayout ordering changes when those echoes arrive. set_spectrogram_band (databrowser.py:2521) and set_resolution both carry hand-written comments about ordering their early returns before taking the flag, which is the shape of a bug that has already bitten twice.
  - **proposed_qt6_design**: Make the ViewBox range the single source of truth and turn PlotRange into a policy object (limits, opening band, min_dr, lock) that computes a target and applies it, reading current state back from the view rather than caching r0/r1. If a cache is kept for the 'time range is shared across channels' rule, scope the guard to the axis being written (a token per PlotRange) instead of one browser-wide bool.
  - **effort**: large

  - **title**: PlotRange carries four unrelated responsibilities
  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/plotranges.py
  - **line**: 15
  - **evidence**: One class holds: (a) the range model -- rmin/rmax/rstep/min_dr/rdefault/rdefault_min/r0/r1 and 20 navigation verbs; (b) a view writer -- set_limits calls ax.setLimits (190-205), set_ranges calls ax.set*Range; (c) crosshair state -- marker_channel/marker_ax/marker_pos plus stored_* and clear_marker/set_marker/store_marker/clear_stored_marker/update_crosshair (618-663), which reach into ax.xline, ax.yline and ax.stored_marker; (d) colour-ramp levels -- set_powers (592-616) walks ax.data_items looking for noise_levels().
  - **why_it_matters**: The crosshair has nothing to do with a range except that both are indexed by axis letter, and it is what forces PlotRange to know about three specific pyqtgraph items on every plot. It triples the surface that has to be re-verified against pyqtgraph-on-PySide6 and makes the class impossible to test without plots.
  - **proposed_qt6_design**: Split into PlotRange (pure model + policy, no Qt), a RangeApplier that writes limits/ranges into view boxes, and a CrosshairController that owns marker/stored-marker state across axes. set_powers belongs with the spectrogram item, not with a range.
  - **effort**: medium

  - **title**: set_limits and at_end/at_home crash on a used range with an open end
  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/plotranges.py
  - **line**: 181
  - **evidence**: set_limits guards only on is_used() (179) and then evaluates `np.isfinite(self.rmin) and np.isfinite(self.rmax)` -- but _add_axis (65-73) leaves rmin/rmax at None whenever the plot reports None, and TimePlot.range returns `0, None, 10` when the panel has no data items (timeplot.py:373-378). np.isfinite(None) raises TypeError. Same shape at 172-176: at_end does `self.r1[channel] >= self.rmax` and at_home `self.r0[channel] <= self.rmin`, both TypeError against None; at_end() is live at databrowser.py:7868 (auto-scroll).
  - **why_it_matters**: A trace panel registered before its data arrives takes the whole open path down with a TypeError from a ufunc, which reads as a numpy bug rather than as a missing None guard. It is latent today only because open() always has data items by the time it calls set_limits -- a port that reorders construction (very likely, since Qt6 changes when the first layout pass runs) walks straight into it.
  - **proposed_qt6_design**: Give PlotRange explicit Optional[float] limits with a single `_finite(x)` helper, and make is_used() mean 'has axes AND has finite bounds'. Add the missing-bounds case to the test suite rather than relying on construction order.
  - **effort**: small

  - **title**: Three separate owners hold strong references to every plot object
  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/plotranges.py
  - **line**: 44
  - **evidence**: PlotRange keeps axxs/axys/axzs (44-48, one list per channel), _zoom_views as a set of ViewBoxes that is never emptied (43, 88), and marker_ax/stored_marker_ax. Panel keeps axs and axcs (panels.py:50-51). DataBrowser keeps figs, axs, axgs, borders, splitters, audio_markers, sig_proxies (databrowser.py:1642-1648, 1698). Nothing clears any of them; DataBrowser.close is at 3184 and the browser is rebuilt per file.
  - **why_it_matters**: Under PyQt5 a stale wrapper over a deleted C++ object is tolerable in places; under PySide6 the ownership and wrapper-invalidation rules differ, and a stale entry in axys or _zoom_views turns the next ax.isVisible()/setLimits() into a RuntimeError on a code path (set_limits, update_crosshair) that runs on every file open. _zoom_views also permanently pins every ViewBox the app has ever created.
  - **proposed_qt6_design**: One owner (the figure/lane) holds the plots; PlotRange and Panel hold weakrefs or plain indices resolved through the owner. Give Panels/PlotRanges an explicit teardown that the browser calls on close, and assert 'no plot survives a close' in the smoke census (which already counts top-level widgets).
  - **effort**: medium

  - **title**: Layout persistence is a deferred import into the app module, and most layout state is not persisted at all
  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/databrowser.py
  - **line**: 6545
  - **evidence**: restore_panel_split does `from .audian import settings` inside the function body with the comment 'audian.py imports this module, so a module level import would be a cycle'; the same trick appears at 6592 (spectrogram_band), 6654 (save_spectrogram_band) and 6683 (save_panel_split). The persisted layout state is exactly two keys -- PANEL_SPLIT_SETTING v3 (a single float, 1055-1062) and SPEC_BAND_SETTING v2 (min_hz/max_hz, 1071-1090). Splitter sizes are recomputed every layout instead of saved (size_splitter, 6935-6961); channel visibility, F2/F3 panel modes, the rail toggle (F7), the navigator toggle (F6) and the maximised channel are not persisted.
  - **why_it_matters**: The persistence boundary runs through the middle of an import cycle, so the layout modules cannot read or write their own state and none of it can be tested without importing the application. It also means 'restore the panel layout' is not a thing the app can do -- only two scalars survive a restart -- which is a gap the port should decide about deliberately rather than inherit.
  - **proposed_qt6_design**: Move settings() / save_setting() into a leaf module (audian.config) that neither databrowser nor audian imports cyclically, and give the new LaneLayout solver a serialisable LayoutState dataclass with one version. Then 'what is the layout' and 'write the layout' are one object, versioned once.
  - **effort**: medium

  - **title**: Axis alignment is a measurement fed back through a zero-delay timer, with a per-plot lambda as a second trigger
  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/databrowser.py
  - **line**: 6894
  - **evidence**: align_time_axis measures a lane's view box (view.mapRectToScene(view.boundingRect())), maps two widget origins through mapTo(self.stack_pane, QPoint(0,0)) and writes QGraphicsGridLayout contents margins on the axis figure and on ControlPanel (6912-6933). It is triggered from four places: inline at the end of adjust_layout (6841), again one event-loop turn later via schedule_axis_alignment's QTimer.singleShot(0) (6892), from apply_theme (2603), and from a per-trace-plot lambda on sigResized created inside the construction loop (1715-1717). The singleShot exists because 'a lane's view box is only re-fitted when the widget itself resizes' -- an explicit admission that the measurement can read a stale frame.
  - **why_it_matters**: Correctness depends on the deferred call landing after Qt's last layout pass. Qt6 changes both the number of layout passes and the ordering of sigResized relative to them, and the failure mode is cosmetic-but-obvious (the F5 case documented at 6880-6887: ticks 136 px short of the spectrogram until the window is resized). The lambda-per-plot also creates one closure per channel that captures nothing but calls the browser.
  - **proposed_qt6_design**: Make the margin a computed number rather than a measured one where possible (the rail width and the view box inset are both known to the layout solver), and where measurement is unavoidable, drive it from a single QTimer(0) that the layout solver arms once per pass instead of from four call sites. Replace the per-plot lambda with one connection to a browser slot that takes the channel.
  - **effort**: medium

  - **title**: adjust_layout walks self.panels.values() eight times per channel
  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/databrowser.py
  - **line**: 6752
  - **evidence**: Inside the per-channel loop: 6754 (hide non-visible channels), 6769 (compute rows), 6780 (apply visibility), 6790 (spacer visibility), 6811 (apply heights), plus the calls it makes -- split_spacers (6181), lane_content_height (6216), visible_trace_panels (6154) and lane_fallback (6714) each iterate the same dict again. apply_panel_split (6491) does it once more per spectrogram channel. The cost is quoted in the code: 'A full adjust_layout on a sixteen channel stack is 5.2 ms of Python' (6431).
  - **why_it_matters**: 5.2 ms is a third of a 60 Hz frame before anything is drawn, and it is why a whole second code path (apply_panel_split, 0.03-0.06 ms) had to be written for the drag -- two implementations of 'set the row heights' that must agree. Any Qt6 change to relayout cost lands on top of this.
  - **proposed_qt6_design**: One pass that builds a per-lane plan (visibility, heights, chrome, band) in the solver, then one pass that applies it. The drag then reuses the same applier with a cheaper plan instead of a parallel implementation.
  - **effort**: medium

  - **title**: ControlPanel imports a private helper from another module and duplicates the height solver
  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/controlpanel.py
  - **line**: 70
  - **evidence**: `from .eventoverlay import _passive` -- a leading-underscore function (eventoverlay.py:813-824) that sets Qt.NoButton and disables hover events, called seven times here (265, 284, 307, 317). The panel also computes its own height from theme arithmetic (wanted_height 195-205, band() 207-215, total = n*CONTROL_BAND_H + CONTROL_NOTE_H at 252) while every other height in the stack comes from lane_geometry, and it takes its horizontal margins from a measurement made for a different widget (set_margins 182-187 <- align_time_axis 6932-6933).
  - **why_it_matters**: _passive is exactly the kind of cross-module private that a port breaks silently: if eventoverlay's Qt.NoButton becomes Qt.MouseButton.NoButton in one file and not the other, the control strip starts swallowing rubber-band drags. And two height solvers means a Qt6 metrics change can move the lanes and the control strip by different amounts.
  - **proposed_qt6_design**: Promote _passive to a public theme helper (theme.make_passive(item)) -- it is a theme-level policy statement, not an overlay detail -- and fold ControlPanel's height into the same layout solver that sizes the lanes, so the strip is one more row in one solution.
  - **effort**: small

  - **title**: resolve_colormap swallows every exception twice
  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/panels.py
  - **line**: 27
  - **evidence**: try: return theme.spectrogram_colormap(color_map) except Exception: pass -- then try: return pg.colormap.get(color_map) except Exception: pass -- then fall back to the default map. Any failure inside pyqtgraph's colormap machinery returns a plausible-looking default with nothing logged.
  - **why_it_matters**: If pyqtgraph's colormap API shifts under PySide6, every spectrogram silently draws in the default ramp and the baseline screenshot comparison (scripts/compare_shots.py) reports it as a colour change with no traceback to explain it.
  - **proposed_qt6_design**: Catch the specific exceptions the two lookups can raise, log at warning on the fallback path, and keep the default only for a genuinely unknown NAME rather than for any exception at all.
  - **effort**: small

  - **title**: Three modules in this cluster are named for something they are not
  - **file**: /home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/alignment.py
  - **line**: 1
  - **evidence**: alignment.py is the *_metadata.toml reader for device-clock-to-recording-clock fits (993 lines, zero Qt) -- axis alignment is databrowser.align_time_axis. windowing.py is annotation array windowing/decimation (592 lines, zero Qt) -- FFT windows are bufferedspectrogram.nfft/overlap_frac and the spectrogram parameter widgets in databrowser. layers.py is the annotation layer model (508 lines, zero Qt) -- layer stacking is Panels rows and z-values. controlpanel.py is a session step-track strip -- the parameter bar is databrowser.setup_parameter_bar (see databrowser.py:405).
  - **why_it_matters**: The cluster brief itself was written from the names and asked for axis alignment, layer stacking and FFT parameter UI from files that contain none of them; a migration plan built on the same reading would schedule Qt work against 2394 lines that need none and would miss that the real axis-alignment and parameter-bar code is in databrowser.py.
  - **proposed_qt6_design**: Rename on the way through: alignment.py -> sessionfit.py (or bundlemeta.py), windowing.py -> annotationwindow.py, controlpanel.py -> controltrackpanel.py. Cheap, mechanical, and it stops the next reader making the same mistake.
  - **effort**: small

- **behavior_contract**:
  - Dragging the grab band between a lane's trace and its spectrogram moves the boundary 1:1
  - with the pointer: +7, +15, -11, -19, +22 px of travel each land the boundary within 0.01
  - px of that offset (tests/test_panelsplitter.py:389).
  - The gesture works through a real pointer, not just the API: a press/move/release
  - delivered to the figure viewport routes to the band and moves the boundary, even though
  - its layout row is 0 px tall -- only its oversized boundingRect/shape make that pixel
  - hittable.
  - The grab band costs the lane no height. Its row is always 0 px (sizeHint returns a
  - maximum height of 0 with unbounded width), and the spectrogram row plus the trace rows
  - sum to exactly the lane's content height, on every stack size.
  - One drag moves the boundary in every channel that shows both panels -- there is a single
  - split for the whole browser, not one per channel.
  - The band exists only where a visible spectrogram meets a visible trace: not under the
  - last panel, not between two trace panels, not in a lane with no spectrogram of its own,
  - and not in a lane whose spectrogram has nothing to draw yet.
  - The band paints a hairline at rest and the brighter handle pen while hovered or dragged,
  - and the cursor becomes a SplitVCursor over it. It is the one in-scene item that accepts
  - the mouse; borders, overlays, labels and markers must keep passing clicks through.
  - A drag that pushes past a clamp and comes back lands on exactly the pixel it started
  - from (absolute travel from a latch, never summed increments).
  - A lane that changes size mid-drag -- F6 hiding the navigator, or a window resize -- does
  - not rescale the gesture: the boundary keeps moving 1:1 from that point on.
  - Neither row can be dragged under its floor, and the clamp always includes the split the
  - lane opens on, so the boundary never jerks away from the pointer on the first pixel of
  - travel.
  - Double-clicking the band, and Shift+F3, both return to the default split -- the
  - spectrogram at its SPECTROGRAM_MIN_HEIGHT allowance, which follows the lane height
  - rather than a stored number.
  - The split is written to settings once per gesture, never per mouse move; a split still
  - on its default is written as null; a value written by another setting version is ignored
  - with a logged warning; a split dragged on a 2-channel stack means the same thing when
  - replayed on a 16-channel one.
  - A drag never runs the full layout pass; the release runs it exactly once. A drag re-
  - states a row's tick-value chrome only for rows whose answer actually changed, and a
  - row's chrome always matches what the plot itself believes about its height.
  - A lane opens with the spectrogram at exactly the height the lane grew by, so a 4-channel
  - 1200x900 window opens 120/34; the spectrogram is never opened shorter than the height
  - the spectrogram item will refuse to draw at.
  - Every visible lane in the stack has the same integer height; the rounding remainder goes
  - to one spacer row at the bottom, never distributed by stretch (the lane pitch must not
  - wobble between 31 and 38 px down a 16-lane stack).
  - No visible lane is ever empty -- if visibility rules would blank a lane, a trace is put
  - back first, then a spectrogram.
  - The focused lane is scrolled back on screen after every gesture that re-flows the stack
  - (solo, mute, maximise, move channel, show/hide channels, arrow-key wrap, window resize).
  - When too many channels are visible for every lane to carry a spectrogram, the
  - spectrogram follows the focused lane and the reader is warned once ('spectrogram hidden
  - - too many channels visible'); the lane it moves to actually draws one.
  - The shared time axis below the stack lines up left and right with the lane view boxes
  - after every relayout, theme switch, rail toggle (F7) and mean-spectrogram toggle (F5),
  - and it never maps its ticks through a hidden plot.
  - The control-track strip takes the same measured margins as the time axis and follows the
  - lanes' x range through a single view link, so it can never be a frame behind them.
  - The control strip is invisible and 0 px tall unless the controls layer is switched on;
  - its switch is the same one the parameter-bar chip drives and the settings file persists,
  - so the two can never disagree.
  - Each control channel gets its own band with its own frozen scale, printed in-band as
  - 'name low-high unit'; a stretch where nothing is in force draws no line (NaN is a gap,
  - not a zero) and the band's floor rule still shows.
  - The time range is shared across all channels; amplitude ranges follow the y mode (shared
  - across lanes by default, per-channel when selected).
  - A hand zoom of an amplitude axis locks that range against automatic refitting; scrolling
  - time never fights it. 'v' and a double-click on a y axis refit and release the lock; the
  - frequency axis has Ctrl+V back to its opening band and Ctrl+Shift+V all the way out to
  - Nyquist.
  - A spectrogram opens at the stored frequency band but is never limited by it: it still
  - pans, zooms and 'end's to Nyquist. A stored band above this recording's Nyquist is
  - clamped, and a band still at its limit is stored as null so an 8 kHz recording cannot
  - cap a 96 kHz one.
  - Panning and zooming cannot leave rmin..rmax on any axis, and the deepest zoom is min_dr;
  - Home/End/snap/step land on the documented positions.
  - Crosshair lines and the stored marker track the pointer per axis; the readout reports
  - both the marker position and the delta from the stored marker for time, amplitude,
  - frequency and power.
  - Grid toggling, colour-map changes and colour-bar visibility apply to every matching
  - panel of every channel at once.
- **risk**: high -- the cluster's own Qt surface is tiny (two PyQt5 imports and five unscoped-enum sites, all in panelsplitter.py and controlpanel.py) and three of its seven files import no Qt at all, but everything it encodes is pixel arithmetic calibrated against measured Qt5 layout numbers, and the one change that breaks it -- Qt6's always-on high-DPI with PassThrough rounding, where Qt5 here ran unscaled because the app never set AA_EnableHighDpiScaling -- is invisible to the 2679-line offscreen test suite that runs at DPR 1.0.
- **notes**: Scope correction the architecture lead should propagate: the cluster brief's framing does not match the code. There is no axis-alignment code in alignment.py (it is TOML session-bundle time alignment, 993 lines, no Qt), no FFT parameter UI in windowing.py (it is annotation array windowing/decimation, 592 lines, no Qt), no layer stacking in layers.py (it is the annotation data model, 508 lines, no Qt), and no parameter bar in controlpanel.py (that is DataBrowser.setup_parameter_bar; see databrowser.py:405). 2394 of this cluster's 3005 lines need zero migration work.  Where the real work is, in order: (1) panelsplitter.py -- 240 lines, two imports and five enum sites, but it encodes the drag protocol whose correctness is geometric; (2) controlpanel.py -- 448 lines, one import, one devicePixelRatioF whose VALUE changes under Qt6, plus a dependency on theme.strip_pg_menus's pyqtgraph-internals surgery; (3) plotranges.py -- 815 lines, no Qt import at all but it writes into pyqtgraph view boxes and subscribes to a ViewBox Signal from a non-QObject, and it is the second half of the duplicated viewport-state problem; (4) panels.py -- 331 lines, no Qt import, duck-typed QGraphicsItem visibility as the layout's source of truth.  The load-bearing layout code is NOT in this cluster and should be clustered with it: databrowser.py lane_geometry (6000), update_stretches (6046), time_axis_height (6105), link_time_axis (6125), visible_trace_panels (6151), split_spacers (6163), lane_content_height (6195), default_spec_height (6223), panel_split_limits (6258), panel_split_rows (6274), fit_figure_layout (6345), panel_split_heights (6366), drag_panel_split (6392), apply_panel_split (6428), finish/reset/restore/save_panel_split (6511-6691), lane_fallback (6693), adjust_layout (6723), schedule_axis_alignment (6873), align_time_axis (6894), size_splitter (6935), update_ranges (6963), set_times (7022), set_ranges (7064), auto_fit_y (3074).  Two verification gaps worth closing before any code moves: (a) the offscreen suite runs at devicePixelRatio 1.0, so nothing in it can detect that Qt6 scaling makes the viewport hold fewer logical pixels and pushes 4-channel stacks into the collapsed-spectrogram mode earlier -- add a fractional-DPR run (QT_SCALE_FACTOR=1.25) to scripts/baseline_matrix.sh; (b) plotranges.py:89's sigUserZoomed connection to a bound method of a non-QObject is the single point on which 'a hand zoom is not overwritten by the next auto-fit' rests, and PySide6's bound-method connection lifetime differs from PyQt5's -- assert it directly rather than through the existing tests, which squash a lane with setYRange and therefore never set the lock.  Two live bug candidates found while reading, both independent of the port: plotranges.py:181 (np.isfinite(None) TypeError in set_limits when a used range has an open end, reachable via timeplot.py:377 returning `0, None, 10` for a panel with no data items) and plotranges.py:172-176 (at_end/at_home compare against a possibly-None rmax/rmin; at_end() is live at databrowser.py:7868).
