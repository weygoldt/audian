# Recon: app-shell

- **cluster**: app-shell
- **purpose**: `src/audian/audian.py` is the process entry point, the whole application chrome, and the god object that owns every cross-tab decision. It contains the argparse CLI, QApplication construction and theme bootstrap, the QMainWindow (`Audian`) with its 127 QActions / 12 menus / 5-stage adaptive tool bar / 6-field status bar, an ad-hoc widget library (glyph icon renderer, vertical tab bar, mnemonic proxy style, startup page, command palette, cheat sheet, shortcut rebinder, recent-files store, JSON settings store), and the multi-tab document model (list of `DataBrowser`s, per-axis link switches, signal fan-out between tabs). `runaudian.py` is a 4-line developer scratch script calling `audian.main(['-f','1000','-l','15000','feldgr.wav'])`; it carries no logic and only pins the `main(list[str])` signature.
- **public_surface**:
  - **name**: main
  - **file**: src/audian/audian.py:5036
  - **kind**: function
  - **base**: 
  - **summary**: Package-level entry; audian/__init__.py:6 re-exports it and runaudian.py:4 calls it. Sets the multiprocessing start method, sizes AudioLoader caches, loads plugins, then calls audian_cli. Signature main(cargs: list[str]).

  - **name**: run
  - **file**: src/audian/audian.py:5045
  - **kind**: function
  - **base**: 
  - **summary**: Entry point registered in pyproject.toml as [project.gui-scripts] audian = "audian.audian:run"; calls main(sys.argv[1:]) and returns 0.

  - **name**: audian_cli
  - **file**: src/audian/audian.py:4880
  - **kind**: function
  - **base**: 
  - **summary**: argparse + QApplication + theme.apply + Audian(...) + app.exec_(). Signature (cargs=[], plugins=None). The only call site of theme.apply() in the code base.

  - **name**: Audian
  - **file**: src/audian/audian.py:1479
  - **kind**: class
  - **base**: QMainWindow
  - **summary**: The god object, ~3400 lines. Owns actions, menus, tool bar, status bar, tab widget, PlayAudio device, browser list, link switches, theme switching, drag&drop, screenshot, recent files. Constructed by audian_cli and by scripts/smoke_test.py:350 with 9 positional args.

  - **name**: settings
  - **file**: src/audian/audian.py:926
  - **kind**: function
  - **base**: 
  - **summary**: Reads the JSON preferences file; never raises. Imported lazily (circularly) by databrowser.py at 3481, 4966, 5513, 6545, 6592.

  - **name**: save_setting
  - **file**: src/audian/audian.py:940
  - **kind**: function
  - **base**: 
  - **summary**: Writes one preference key. Imported lazily (circularly) by databrowser.py at 3516, 5048, 5577, 6654, 6683.

  - **name**: settings_path
  - **file**: src/audian/audian.py:917
  - **kind**: function
  - **base**: 
  - **summary**: audian_dirs.user_config_path / 'settings.json'. Monkeypatched by scripts/smoke_test.py:243 to isolate test runs.

  - **name**: glyph_pixmap
  - **file**: src/audian/audian.py:287
  - **kind**: function
  - **base**: 
  - **summary**: Renders one named glyph into a transparent QPixmap at a logical size (no devicePixelRatio). Imported by tests/test_parameterbar.py:458; used by VerticalTabBar._paint_close.

  - **name**: glyph_icon
  - **file**: src/audian/audian.py:297
  - **kind**: function
  - **base**: 
  - **summary**: Builds a 4-mode/2-state QIcon from a QPainterPath. Source of every toolbar and menu glyph; icons are rebuilt wholesale on a theme switch.

  - **name**: GLYPH_NORMAL
  - **file**: src/audian/audian.py:69
  - **kind**: constant
  - **base**: 
  - **summary**: Token name for the normal glyph ink; imported by tests/test_parameterbar.py:458. Siblings GLYPH_ACTIVE/GLYPH_DISABLED/GLYPH_ON/GLYPH_DISABLED_ALPHA at lines 70-79.

  - **name**: CheatSheet
  - **file**: src/audian/audian.py:1229
  - **kind**: class
  - **base**: QDialog
  - **summary**: Translucent key overlay grouping actions by 8 hardcoded name lists (GROUPS at line 1232, resolved by getattr on Audian.acts). Imported by tests/test_panelsplitter.py:1992.

  - **name**: CommandPalette
  - **file**: src/audian/audian.py:1158
  - **kind**: class
  - **base**: QDialog
  - **summary**: Fuzzy search over Audian.all_actions(); Enter triggers the QAction, which is cached in the list item's Qt.UserRole data.

  - **name**: ShortcutsDialog
  - **file**: src/audian/audian.py:1406
  - **kind**: class
  - **base**: QDialog
  - **summary**: Searchable list of every action with a per-row QKeySequenceEdit that rebinds live. The rebinding is not persisted anywhere.

  - **name**: StartupPage
  - **file**: src/audian/audian.py:962
  - **kind**: class
  - **base**: QWidget
  - **summary**: Empty state: title column, recent-files column, key-chip column, dashed drop target. Rebuilt from scratch on a theme switch (set_app_theme, line 1666) because it bakes tokens into per-widget stylesheets.

  - **name**: RecentFiles
  - **file**: src/audian/audian.py:562
  - **kind**: class
  - **base**: 
  - **summary**: JSON list of the 10 most recent files (path, name, parent, channels, duration, rate) in audian_dirs.user_cache_path/'recent.json'.

  - **name**: RecentRow
  - **file**: src/audian/audian.py:755
  - **kind**: class
  - **base**: QPushButton
  - **summary**: One recent-file row; hand-elides the name and, in elide_path (line 884), the directory on os.sep boundaries so units are never cut in half.

  - **name**: ToolStrip
  - **file**: src/audian/audian.py:605
  - **kind**: class
  - **base**: QWidget
  - **summary**: The tool bar widget: measures 6 layout stages once (set_stages, line 665) and picks the widest that fits on every resizeEvent (fit, line 723). minimumSizeHint always returns the tightest stage's baked width.

  - **name**: VerticalTabBar
  - **file**: src/audian/audian.py:328
  - **kind**: class
  - **base**: QTabBar
  - **summary**: Upright left-edge tab spine with hand-painted rotated labels and hand-hit-tested close marks; uses event.pos() in mouseMove/mousePress.

  - **name**: MnemonicStyle
  - **file**: src/audian/audian.py:484
  - **kind**: class
  - **base**: QProxyStyle
  - **summary**: Returns SH_UnderlineShortcut only while `reveal` is set; driven by an application-wide event filter that tracks the Alt key (installed at line 1794, never removed).

  - **name**: StatusSeparator
  - **file**: src/audian/audian.py:524
  - **kind**: class
  - **base**: QWidget
  - **summary**: 16px hairline slot painted between status-bar readouts, hidden together with its readout.

  - **name**: make_transparent
  - **file**: src/audian/audian.py:512
  - **kind**: function
  - **base**: 
  - **summary**: Gives a bare QWidget an object name and an inline `#name { background: transparent }` stylesheet so the global QSS does not paint it.

  - **name**: chip_style
  - **file**: src/audian/audian.py:545
  - **kind**: function
  - **base**: 
  - **summary**: Returns the inline stylesheet string for a key chip or status chip, with token colours baked in at call time.

  - **name**: fuzzy_score
  - **file**: src/audian/audian.py:1130
  - **kind**: function
  - **base**: 
  - **summary**: Subsequence scorer (lower is better) used by the command palette's filter.

  - **name**: action_keys
  - **file**: src/audian/audian.py:1153
  - **kind**: function
  - **base**: 
  - **summary**: Joins act.shortcuts() into a display string; used by the palette, the cheat sheet and the channel tooltips.

  - **name**: AUDIO_SUFFIXES
  - **file**: src/audian/audian.py:44
  - **kind**: constant
  - **base**: 
  - **summary**: The 20 audio suffixes accepted by drag&drop and by the startup page.

- **qt5_api_usage**:
  - **file**: src/audian/audian.py
  - **line**: 21
  - **api**: from PyQt5.QtWidgets import QAction, QActionGroup
  - **qt6_replacement**: QAction and QActionGroup moved to QtGui in Qt6: `from PySide6.QtGui import QAction, QActionGroup`. 127 QAction constructions in this file depend on it.
  - **severity**: breaking

  - **file**: src/audian/audian.py
  - **line**: 11
  - **api**: import pyqtgraph as pg placed BEFORE any Qt import
  - **qt6_replacement**: pyqtgraph picks its binding on first import. Under Qt6 either set PYQTGRAPH_QT_LIB=PySide6 before this line or import PySide6 first, or pg binds to whatever is installed. pg.ViewBox.RectMode/PanMode (lines 2907, 2911) are pyqtgraph constants and do not change.
  - **severity**: breaking

  - **file**: src/audian/audian.py
  - **line**: 5033
  - **api**: app.exec_()
  - **qt6_replacement**: app.exec(). PySide6 keeps exec_() as a deprecated alias; PyQt6 removed it. Change so the entry point does not rest on a shim.
  - **severity**: cosmetic

  - **file**: src/audian/audian.py
  - **line**: 4652
  - **api**: QFileDialog.getOpenFileNames(self, directory=os.fspath(path), filter=';;'.join(filters))
  - **qt6_replacement**: PySide6's static signature is getOpenFileNames(parent, caption, dir, filter, selectedFilter, options): the keyword is `dir`, not `directory`. As written this raises TypeError on PySide6. Rewrite positionally: getOpenFileNames(self, 'Open files', os.fspath(path), ';;'.join(filters)).
  - **severity**: breaking

  - **file**: src/audian/audian.py
  - **line**: 464
  - **api**: VerticalTabBar.mouseMoveEvent: self._close_at(event.pos())
  - **qt6_replacement**: QMouseEvent.pos() is removed in Qt6: use event.position().toPoint(). _close_at compares with QRect.contains(QPoint), so the QPointF must be converted or the tab close marks stop hover-highlighting.
  - **severity**: breaking

  - **file**: src/audian/audian.py
  - **line**: 478
  - **api**: VerticalTabBar.mousePressEvent: self._close_at(event.pos())
  - **qt6_replacement**: event.position().toPoint(); same conversion as line 464. Without it the hand-painted tab close X stops closing tabs.
  - **severity**: breaking

  - **file**: src/audian/audian.py
  - **line**: 3216
  - **api**: setShortcuts([QKeySequence.MoveToEndOfLine, QKeySequence.MoveToEndOfDocument]) - a Python list whose elements are StandardKey enums, not QKeySequence
  - **qt6_replacement**: PyQt5 converted StandardKey inside a list implicitly; PySide6 does not. Wrap each element: [QKeySequence(QKeySequence.StandardKey.MoveToEndOfLine), ...]. Same shape at 3226 (time_home), 3916 (next_channel), 3924 (previous_channel, which mixes StandardKey with QKeySequence('Alt+PgUp')).
  - **severity**: breaking

  - **file**: src/audian/audian.py
  - **line**: 2856
  - **api**: Unscoped QKeySequence StandardKey constants at 18 further sites: 2881, 3188, 3196, 3202, 3208, 3217, 3227, 3494, 3500, 3506, 3512, 3909, 3916, 3924, 3931, 3937, 3943
  - **qt6_replacement**: QKeySequence.StandardKey.Open / .Quit / .MoveToNextPage / .SelectAll / .Delete etc. PySide6 forgiveness mode still resolves the short form, but this is the single largest enum surface in the file and it decides Home/End/PageUp/PageDown/Ctrl+A/Delete behaviour.
  - **severity**: cosmetic

  - **file**: src/audian/audian.py
  - **line**: 3079
  - **api**: self.acts.play_window.setShortcut(" ")
  - **qt6_replacement**: QKeySequence::fromString trims whitespace; verify a bare space still yields Space in Qt6 and replace with QKeySequence(Qt.Key.Key_Space). If it degrades to an empty sequence, Space-plays-window is lost with no error anywhere.
  - **severity**: behavior-change

  - **file**: src/audian/audian.py
  - **line**: 2717
  - **api**: if modifiers & Qt.ShiftModifier (and Qt.AltModifier at 2719) in region_mode_for_modifiers
  - **qt6_replacement**: Qt.KeyboardModifier.ShiftModifier; in Qt6 the AND yields a flag object, so compare explicitly or use `in`. This is the Shift+drag=play / Alt+drag=analyze override, reached from selectviewbox.py:52.
  - **severity**: behavior-change

  - **file**: src/audian/audian.py
  - **line**: 4830
  - **api**: state & Qt.WindowMaximized / state & ~Qt.WindowMaximized / state | Qt.WindowMaximized in toggle_maximize
  - **qt6_replacement**: Qt.WindowState.WindowMaximized; the `~` on a Qt6 flag needs care in both bindings. Verify Ctrl+Shift+M still un-maximizes rather than only maximizing.
  - **severity**: behavior-change

  - **file**: src/audian/audian.py
  - **line**: 505
  - **api**: QStyle.SH_UnderlineShortcut in MnemonicStyle.styleHint
  - **qt6_replacement**: QStyle.StyleHint.SH_UnderlineShortcut, and re-check the override signature against Qt6's QProxyStyle::styleHint(StyleHint, const QStyleOption*, const QWidget*, QStyleHintReturn*). If the override stops matching, Alt-reveal silently reverts to permanent underlines.
  - **severity**: breaking

  - **file**: src/audian/audian.py
  - **line**: 415
  - **api**: painter.drawControl(QStyle.CE_TabBarTabShape, option) with QStyleOptionTab + self.initStyleOption(option, index)
  - **qt6_replacement**: QStyle.ControlElement.CE_TabBarTabShape. QStyleOptionTab survives Qt6, but QTabBar::initStyleOption is protected - confirm PySide6 exposes it on the Python subclass, or the whole vertical tab spine stops painting.
  - **severity**: breaking

  - **file**: src/audian/audian.py
  - **line**: 287
  - **api**: glyph_pixmap: QPixmap(size, size) with no devicePixelRatio, filled with Qt.transparent (line 290)
  - **qt6_replacement**: Qt.GlobalColor.transparent, and set pm.setDevicePixelRatio(dpr) with the pixmap rendered at size*dpr. Qt6 always applies high-DPI scaling (no opt-in attribute exists and none is set - see line 5024), so every toolbar/tab/status glyph stays 1x against crisp scaled text on any fractional-scale display.
  - **severity**: behavior-change

  - **file**: src/audian/audian.py
  - **line**: 5024
  - **api**: app = QApplication(sys.argv[:1] + qt_args) - no AA_EnableHighDpiScaling / AA_UseHighDpiPixmaps / setHighDpiScaleFactorRoundingPolicy anywhere in the tree, and no setApplicationName / setOrganizationName / setDesktopFileName / setWindowIcon
  - **qt6_replacement**: No attributes to delete (good), but Qt6 turns scaling on unconditionally with PassThrough rounding, so fractional scales now reach ToolStrip's six baked stage widths (line 665). Separately, add the application identity: without setDesktopFileName the Wayland compositor cannot match the window to its .desktop entry, and QSettings('audian','audian') is hardcoded in databrowser.py:1448 instead of derived from it.
  - **severity**: behavior-change

  - **file**: src/audian/audian.py
  - **line**: 2746
  - **api**: screen.grabWindow(app.winId()) in screen_shot()
  - **qt6_replacement**: In Qt6 QScreen::grabWindow(WId) is unsupported on Wayland and returns an empty or whole-screen pixmap. Use QWidget.grab() on the main window - which also drops the QGuiApplication.primaryScreen() dependency and works on the offscreen platform the smoke test uses.
  - **severity**: breaking

  - **file**: src/audian/audian.py
  - **line**: 2784
  - **api**: image_buffer.open(QBuffer.ReadWrite)
  - **qt6_replacement**: QIODevice.OpenModeFlag.ReadWrite (QIODeviceBase in Qt6). Verify PySide6 still resolves the inherited short name on QBuffer.
  - **severity**: cosmetic

  - **file**: src/audian/audian.py
  - **line**: 277
  - **api**: painter.drawText(QRectF(0, 0, size, size), Qt.AlignCenter, "?")
  - **qt6_replacement**: Qt.AlignmentFlag.AlignCenter, and the (QRectF, int, str) overload wants an int in PySide6: int(Qt.AlignmentFlag.AlignCenter). Same at line 434 (drawText(QRect, Qt.AlignLeft|Qt.AlignVCenter, label)) in the rotated tab label.
  - **severity**: breaking

  - **file**: src/audian/audian.py
  - **line**: 308
  - **api**: QIcon.Normal / .Active / .Selected / .Disabled and QIcon.Off / .On in glyph_icon (308, 319, 322)
  - **qt6_replacement**: QIcon.Mode.Normal and QIcon.State.Off/On. The four-mode/two-state construction is load-bearing: it exists so Qt never fakes a disabled or checked variant.
  - **severity**: cosmetic

  - **file**: src/audian/audian.py
  - **line**: 1566
  - **api**: self.tabs.setTabPosition(QTabWidget.West)
  - **qt6_replacement**: QTabWidget.TabPosition.West.
  - **severity**: cosmetic

  - **file**: src/audian/audian.py
  - **line**: 2362
  - **api**: QToolButton.InstantPopup at 2362 (amplitude), 2400 (channels), 2446 (overflow)
  - **qt6_replacement**: QToolButton.ToolButtonPopupMode.InstantPopup.
  - **severity**: cosmetic

  - **file**: src/audian/audian.py
  - **line**: 2269
  - **api**: Qt.ToolButtonIconOnly / TextBesideIcon / TextOnly at 1899, 2269, 2332, 2359, 2363, 2401, 2447, 2467
  - **qt6_replacement**: Qt.ToolButtonStyle.*. Line 2467 is inside the toolbar's `unlabel` stage closure, so a failure here breaks the narrow-window degradation rather than the normal layout.
  - **severity**: cosmetic

  - **file**: src/audian/audian.py
  - **line**: 780
  - **api**: QSizePolicy.Expanding / .Fixed / .Ignored / .Preferred at 780, 798, 835, 1846, 2396
  - **qt6_replacement**: QSizePolicy.Policy.*. Line 1846 (Ignored on the status message label) is the fix for the window-minimum-width bug documented at 1837-1848 and must not be lost.
  - **severity**: cosmetic

  - **file**: src/audian/audian.py
  - **line**: 1456
  - **api**: QDialogButtonBox(QDialogButtonBox.Close, self) at 1456 and 2168
  - **qt6_replacement**: QDialogButtonBox.StandardButton.Close.
  - **severity**: cosmetic

  - **file**: src/audian/audian.py
  - **line**: 2194
  - **api**: line.setFrameShape(QFrame.VLine) in toolbar_gap
  - **qt6_replacement**: QFrame.Shape.VLine.
  - **severity**: cosmetic

  - **file**: src/audian/audian.py
  - **line**: 1212
  - **api**: QEvent.KeyPress / KeyRelease / MouseButtonPress / WindowDeactivate at 1212, 1806, 1808, 1811
  - **qt6_replacement**: QEvent.Type.*.
  - **severity**: cosmetic

  - **file**: src/audian/audian.py
  - **line**: 1163
  - **api**: Qt.WA_DeleteOnClose at 1163, 1348, 1412, 2157 and Qt.WA_TransparentForMouseEvents at 532
  - **qt6_replacement**: Qt.WidgetAttribute.*. WA_DeleteOnClose plus the track_dialog/destroyed pattern (2690) is what keeps the palette and cheat sheet from leaking; verify it still fires under shiboken ownership.
  - **severity**: cosmetic

  - **file**: src/audian/audian.py
  - **line**: 1164
  - **api**: setWindowModality(Qt.NonModal) at 1164, 1349, 1414, 2158
  - **qt6_replacement**: Qt.WindowModality.NonModal. All four dialogs are deliberately browsable while the app runs.
  - **severity**: cosmetic

  - **file**: src/audian/audian.py
  - **line**: 1207
  - **api**: item.setData(Qt.UserRole, act) / item.data(Qt.UserRole) storing a live QAction in a QListWidgetItem (1207, 1225)
  - **qt6_replacement**: Qt.ItemDataRole.UserRole. PyQt5 wrapped this in a QVariant; PySide6 round-trips the Python object, but the stored QAction can outlive its C++ owner if menus are rebuilt while the palette is open (adapt_menu clears the Traces and Active submenus on every tab switch, 4620-4629). Guard run_current with shiboken6.isValid or re-resolve by id.
  - **severity**: behavior-change

  - **file**: src/audian/audian.py
  - **line**: 1213
  - **api**: Qt.Key_Down / Key_Up (1213, 1215), Qt.Key_Escape / Key_Question (1400), Qt.Key_Alt (1806, 1808)
  - **qt6_replacement**: Qt.Key.*.
  - **severity**: cosmetic

  - **file**: src/audian/audian.py
  - **line**: 428
  - **api**: metrics.elidedText(..., Qt.ElideMiddle / Qt.ElideRight, ...) at 428, 905, 2054, 2140, 803(via resizeEvent)
  - **qt6_replacement**: Qt.TextElideMode.*. Every elastic label in this file elides by hand, so a failure here is a visibly clipped tab label / status message / recent path.
  - **severity**: cosmetic

  - **file**: src/audian/audian.py
  - **line**: 477
  - **api**: event.button() == Qt.LeftButton
  - **qt6_replacement**: Qt.MouseButton.LeftButton.
  - **severity**: cosmetic

  - **file**: src/audian/audian.py
  - **line**: 1878
  - **api**: label.setTextFormat(Qt.RichText) with hand-built <span style='color:...'> markup from readout_markup (1996)
  - **qt6_replacement**: Qt.TextFormat.RichText. The baked colour strings are why refresh_readouts (2230) must replay every readout value on a theme switch - unchanged by Qt6, but it is the reason the status bar cannot be restyled by QSS alone.
  - **severity**: cosmetic

  - **file**: src/audian/audian.py
  - **line**: 138
  - **api**: Qt.NoBrush (138, 1121), Qt.NoPen (253), Qt.RoundCap (146), Qt.DashLine (1117)
  - **qt6_replacement**: Qt.BrushStyle.NoBrush, Qt.PenStyle.NoPen, Qt.PenCapStyle.RoundCap, Qt.PenStyle.DashLine.
  - **severity**: cosmetic

  - **file**: src/audian/audian.py
  - **line**: 292
  - **api**: painter.setRenderHint(QPainter.Antialiasing, True) at 292 and 1115
  - **qt6_replacement**: QPainter.RenderHint.Antialiasing.
  - **severity**: cosmetic

  - **file**: src/audian/audian.py
  - **line**: 779
  - **api**: self.setCursor(Qt.PointingHandCursor) at 779 and 1898
  - **qt6_replacement**: Qt.CursorShape.PointingHandCursor.
  - **severity**: cosmetic

  - **file**: src/audian/audian.py
  - **line**: 3886
  - **api**: cact.toggled.disconnect() with no argument in set_channel_action
  - **qt6_replacement**: Disconnects every slot on the signal and runs on every adapt_menu / tab switch. In PySide6 disconnecting a signal with no connections raises RuntimeError rather than returning False. Use blockSignals around setChecked, or disconnect the specific slot.
  - **severity**: breaking

  - **file**: src/audian/audian.py
  - **line**: 1794
  - **api**: app.installEventFilter(self) - application-wide Python event filter for Alt tracking, never removed
  - **qt6_replacement**: API-compatible in Qt6, but every event in the process crosses the Python boundary, and a destroyed Audian leaves a dangling filter on a still-live QApplication (scripts/smoke_test.py builds and tears down windows in one process). Scope the filter to the menu bar, or drop the reveal.
  - **severity**: behavior-change

  - **file**: src/audian/audian.py
  - **line**: 484
  - **api**: MnemonicStyle(QProxyStyle) built with super().__init__() then self.setParent(parent), installed via bar.setStyle(...)
  - **qt6_replacement**: QWidget::setStyle does not take ownership; the setParent is the only thing keeping the style alive under sip. Under shiboken keep the explicit Python reference (already held as self.mnemonic_style, 1782) and drop the setParent, which otherwise makes the QMenuBar own its own style.
  - **severity**: behavior-change

  - **file**: src/audian/audian.py
  - **line**: 1636
  - **api**: Audian.__del__ closing the PlayAudio device
  - **qt6_replacement**: __del__ on a QObject subclass is unreliable in either binding and worse under shiboken ownership. Move the audio close into a closeEvent / explicit shutdown path.
  - **severity**: behavior-change

  - **file**: src/audian/audian.py
  - **line**: 4849
  - **api**: def close(self, index=None) - shadows QWidget.close()
  - **qt6_replacement**: Rename to close_tab(). QWidget::close is non-virtual, so a window-manager close never reaches this override; self.close() elsewhere in the class means 'close a tab'; tabCloseRequested.connect(self.close) at line 1578 relies on the int argument.
  - **severity**: breaking

  - **file**: src/audian/audian.py
  - **line**: 5037
  - **api**: mp.set_start_method('forkserver' if os.name == 'posix' else 'spawn') called unconditionally in main()
  - **qt6_replacement**: Not a Qt change, but it breaks re-entry: a second main() in one process raises RuntimeError('context has already been set'), and compresseddata.py:565 sets it again in its own entry point. Use force=True or mp.get_context(). Keep the call before QApplication - forking a process that already owns a QGuiApplication is undefined on Qt6/Wayland, and CompressedData spawns workers while the event loop runs (compresseddata.py:343-371).
  - **severity**: behavior-change

  - **file**: src/audian/audian.py
  - **line**: 5038
  - **api**: AudioLoader.max_open_files = os.cpu_count() + 2
  - **qt6_replacement**: os.cpu_count() may return None, making this a TypeError at startup on an exotic host. Use (os.cpu_count() or 1) + 2 while the entry point is being restructured.
  - **severity**: cosmetic

- **architecture_problems**:
  - **title**: `Audian` is a 3400-line god object holding at least eight unrelated responsibilities
  - **file**: src/audian/audian.py
  - **line**: 1479
  - **evidence**: One QMainWindow subclass owns: 127 QActions across 12 menus (setup_file_actions 2855 .. setup_help_actions 4590); the tool bar build and its 6-stage overflow policy (setup_toolbar 2279, setup_toolbar_stages 2437); the status bar with six mono readouts, a message log, a progress slot and a mode chip (setup_statusbar 1818); the audio device (self.audio = PlayAudio(), 1524); the document list (self.browsers 1511, load_files 4691, load_data 4717); the cross-tab link matrix (self.link_ranges plus six link_* booleans, 1526-1537); theme switching and a full-tree restyle (set_app_theme 1640, repolish 2205, restyle_chrome 2239); drag&drop and PNG screenshot round-tripping through PIL (dropEvent 2811, screen_shot 2744); and the recent-files and settings JSON stores. 641 references to `self.acts` in this file alone.
  - **why_it_matters**: Every Qt6 fix lands in the same class, so the migration cannot be parallelised or reviewed piecewise, and the smallest testable unit is 'build the whole window' (scripts/smoke_test.py:350 does exactly that). It also forces the odd shapes below: the action bag, the hasattr back-channel, and the polling loader.
  - **proposed_qt6_design**: Split into ActionRegistry (owns QActions, shortcut declarations, enable/visible policy, rebinding persistence), chrome services (ToolbarController, StatusBarService, ThemeController), a SessionModel (open documents, link matrix, cross-tab dispatch), and a thin MainWindow that only wires them. Give each service a plain-Python constructor so it can be tested without a QMainWindow.
  - **effort**: large

  - **title**: `class acts: pass` - an untyped attribute bag of QActions, mutated by both the window and every DataBrowser
  - **file**: src/audian/audian.py
  - **line**: 1499
  - **evidence**: `class acts: pass` / `self.acts = acts` (1499-1502) is a bare class object used as a namespace, handed to every DataBrowser constructor (audian.py:4700, databrowser.py:1154). DataBrowser then mutates the window's actions directly: databrowser.py:1836-1884 disables and hides analyze_region, zoom_xamplitude_*, zoom_yamplitude_*, zoom_uamplitude_*, zoom_ffrequency_*, zoom_wfrequency_*, default_view_frequency and reset_frequency; 2162-2166 the filter actions; 2319-2322 the envelope actions; 7489/7501 read and write acts.channels[c] check state; 7797 copies analyze_region's enabled/visible onto a context-menu entry; 2408 sets acts.use_heterodyne as a button's default action.
  - **why_it_matters**: Action state is process-global but is written per document, so with two tabs open the last-opened file decides which zoom entries exist. There is no schema - attributes appear from ~20 different setup_* methods plus acts.y_modes created inside setup_toolbar (2372) and acts.annotation_layers created inside a menu builder (4176) - so a typo surfaces as an AttributeError at shortcut-press time, and CheatSheet's getattr(gui.acts, name, None) (1385) silently drops a renamed action from the help overlay.
  - **proposed_qt6_design**: An ActionRegistry with declared, named entries and an explicit state_for(document) API: documents publish capability flags (has_filter, has_envelope, amplitude_axes, frequency_axes) and the registry derives enabled/visible from the CURRENT document only. DataBrowser never touches a QAction. The registry also becomes the single source for the cheat sheet, palette and rebinder, replacing three independent discovery paths.
  - **effort**: large

  - **title**: Actions are wired to `self.browser()` with no guard, so a shortcut with no file open hits StartupPage
  - **file**: src/audian/audian.py
  - **line**: 2978
  - **evidence**: `self.acts.zoom_back.triggered.connect(lambda x=0: self.browser().zoom_back())` (2978) and ~25 identical lambdas: zoom_forward 2984, zoom_home 2992, analysis_results 3063, play_window 3080, use_heterodyne 3087, auto_scroll 3241, freq_resolution_up/down 3625/3631, overlap_up/down 3637/3643, color_map_cycler 3649, highpass_up/down 3702/3706, lowpass_up/down 3712/3716, envelope_up/down 3783/3788, toggle_grid 4545, plus apply_time_ranges 3138, apply_ranges 3287, auto_amplitude 3345 and the four toggle_* panel methods 3798-4063. browser() (2723) is just tabs.currentWidget(), which on the empty state is the StartupPage. The comment at 4364 acknowledges the hazard and applies require_browser (2727) to the label and annotation families only.
  - **why_it_matters**: Pressing Space or Backspace on the empty state is an unhandled AttributeError inside a slot: Qt prints a traceback and continues, so the app looks intact but the action is silently dead. PySide6 handles unhandled slot exceptions differently (and can be configured to abort), so this becomes louder after the migration.
  - **proposed_qt6_design**: Route every document-scoped action through one with_browser(fn) adapter - the existing require_browser - applied by the registry when the action is declared document-scoped, and derive enable state from `current_document is not None` instead of the two hand-maintained lists data_menus/data_acts (1602-1603).
  - **effort**: medium

  - **title**: Cross-tab linking is an ad-hoc `for b in self.browsers: if b is not self.browser()` fan-out repeated 15+ times
  - **file**: src/audian/audian.py
  - **line**: 3322
  - **evidence**: dispatch_ranges 3322, dispatch_colormap 3595, dispatch_filter 3610, dispatch_envelope 3747, dispatch_trace 3792, dispatch_audio_source 4513, dispatch_audio_pair 4519, dispatch_audio 4525, apply_time_ranges 3138, apply_ranges 3287, auto_amplitude 3344, set_spectrogram_band 3298, toggle_channel 3826, show_channel 3838, select_channels 3849, hide_deselected_channels 3860, and four verbatim copies of the same eight-line set_panels block in toggle_traces/toggle_spectrograms/toggle_powers/toggle_colorbars (3798-4063). Link state lives in eight separate members (1526-1537), each with its own one-line toggle method (3325, 3328, 3340, 3449, 3602, 3744, 3818, 4394). dispatch_resolution (3583) is a bare `pass` with the intended code sitting in a docstring.
  - **why_it_matters**: Re-entrancy is guarded by hand and inconsistently - dispatch_filter uses blockSignals (3615), toggle_channel uses browser.setting (3829), set_spectrogram_band uses a save= flag (3316). Adding one linkable property means editing five places, and none of this fan-out is covered by any test, so it is exactly where a migration regression will hide.
  - **proposed_qt6_design**: A LinkBus service: one dict of link-policy flags, one publish(source_document, channel, payload) that fans out to subscribers behind a single re-entrancy guard, and documents emitting typed dataclasses instead of positional Signal(object, object) tuples (databrowser.py:1127-1136).
  - **effort**: large

  - **title**: File loading is a 100 ms QTimer.singleShot polling loop that re-enters itself and mutates the tab list mid-iteration
  - **file**: src/audian/audian.py
  - **line**: 4717
  - **evidence**: load_files (4691) builds a DataBrowser, adds a tab, then arms QTimer.singleShot(100, self.load_data) (4715). load_data (4717) iterates `for browser in self.browsers`, calls browser.open() in a try, and on failure removes the tab and mutates self.browsers and self.file_paths inside the loop (4746-4752); it constructs another DataBrowser inside the same loop (4757); and it re-arms itself at 4759 and 4803, each path ending in `break`.
  - **why_it_matters**: Opening N files costs at least N*100 ms of wall clock regardless of I/O, the loop mutates the collection it iterates, there is no cancellation, and the only failure report is a modal QMessageBox plus notify(). The status bar already has a progress slot (set_progress 2126) that this path never uses.
  - **proposed_qt6_design**: A DocumentLoader service driven by signals: open_requested(paths) -> per-file worker (QThreadPool/QRunnable or the existing forkserver pool) -> opened(document) / failed(path, error). The window subscribes and adds tabs; progress goes to set_progress; cancellation is a flag. Removes both singleShot re-arms and the in-loop mutation.
  - **effort**: large

  - **title**: No closeEvent: closing the window through the window manager loses unsaved labels and never closes the audio device
  - **file**: src/audian/audian.py
  - **line**: 4849
  - **evidence**: close(self, index=None) (4849) flushes labels for one tab; quit() (4870) flushes all then calls QApplication.quit(). The comment at 4855-4862 states outright that 'there is no closeEvent anywhere in audian'. QWidget::close is non-virtual, so the window manager's close button reaches neither method. The PlayAudio device (1524) is closed only from __del__ (1636).
  - **why_it_matters**: Clicking the X drops editable-label edits queued behind a zero-timer and leaves the sound device open until interpreter teardown. This is an acceptance-criteria-level behaviour the migration must not inherit unchanged.
  - **proposed_qt6_design**: Implement closeEvent(self, ev): flush every document, close the audio service, stop the timers, then accept. Keep quit() as a thin caller of self.close(). Rename the tab closer to close_tab so the two verbs stop colliding.
  - **effort**: small

  - **title**: Theme switching is a full widget-tree walk plus a wholesale rebuild of the startup page, because colours are baked into per-widget stylesheet strings
  - **file**: src/audian/audian.py
  - **line**: 1640
  - **evidence**: set_app_theme (1640) calls theme.apply, then refresh_glyph_icons (1697, replaying the manually kept (widget, glyph) list built by _set_glyph 1685), then restyle_chrome (2239 -> theme.restyle_tree plus a hand-list of toolbar separators, the mode chip and two labels addressed by attribute name), then rebuilds StartupPage from scratch (1666-1677) because it 'bakes token values into per-widget stylesheets across several builders', then repolish (2205) which unpolishes and re-polishes every QWidget in the window and invalidates every layout, each step wrapped in its own try/except RuntimeError.
  - **why_it_matters**: Three hand-maintained registries (_glyph_targets 1494, _readout_state 1496, _toolbar_separators 1498) that any new widget silently misses; O(widgets) style churn on Ctrl+Shift+L; and RuntimeError swallowing that hides real lifetime bugs. Under shiboken the deleted-C++-object failure mode differs from sip's, so every one of those bare guards has to be re-verified.
  - **proposed_qt6_design**: One application QSS built from tokens plus dynamic properties - theme.py already has BAND_PROPERTY/FG_PROPERTY/FRAME_PROPERTY and restyle_tree (theme.py:1478-1530). Remove the inline setStyleSheet calls in this file (chip_style 545, make_transparent 512, RecentRow 781/793, StartupPage 1005/1021, error_button 1901, toolbar_gap 2196) so a theme switch becomes app.setStyleSheet(...) plus one unpolish/polish. Make icons a themed QIconEngine that repaints on a theme signal instead of a rebuild list.
  - **effort**: large

  - **title**: Three independent persistence stores with three different mechanisms, reached by circular imports
  - **file**: src/audian/audian.py
  - **line**: 917
  - **evidence**: (1) settings_path 917 / settings 926 / save_setting 940 - JSON in user_config_path, holding the theme (1680) and, through 11 lazy circular imports from databrowser.py (3481, 3516, 4966, 5048, 5513, 5577, 6545, 6592, 6654, 6683), browser preferences such as the spectrogram band. (2) RecentFiles 562 - JSON in user_cache_path. (3) QSettings('audian','audian') at databrowser.py:1448 and 7172 - the colormap, in ~/.config/audian/audian.conf, which settings_path never covers (scripts/smoke_test.py:243-248 has to redirect both). Window geometry is deliberately not persisted (1547-1553). Shortcut rebinds made in ShortcutsDialog.rebind (1463) are not persisted at all.
  - **why_it_matters**: `from .audian import settings` inside a DataBrowser method is a circular dependency between the god-object module and the browser, which makes any split of audian.py a chain reaction. Two config back-ends mean a test harness must know both. And a user's rebound keys silently vanish on restart, which is half a feature.
  - **proposed_qt6_design**: One Settings service (a single QSettings or a single JSON file) injected into the window and into documents; no module-level functions and no reverse imports. Move shortcut overrides into it so ShortcutsDialog survives a restart.
  - **effort**: medium

  - **title**: Documents reach the shell through self.window() + hasattr, an untyped duck-typed back-channel
  - **file**: src/audian/databrowser.py
  - **line**: 1485
  - **evidence**: databrowser.py:1485 `window = self.window(); if window is not None and hasattr(window, 'set_readout')`; same shape at 1491 (notify), 3168 (set_progress), 5717 (sync_annotation_actions), and 2485 (`gui = getattr(self, 'gui', None); ... hasattr(gui, 'set_spectrogram_band')`). selectviewbox.py:52 does `gui = getattr(browser, 'gui', None); ... hasattr(gui, 'region_mode_for_modifiers')`. The `gui` back-reference is only assigned inside DataBrowser.open (databrowser.py:1556), so it is None for the 100 ms between construction and the polled load. audian.py mirrors the pattern the other way with hasattr(browser, 'apply_theme') 1663, 'set_y_mode' 2586, 'axis_under_pointer' 3273, 'toggle_navigator_mode' 4098.
  - **why_it_matters**: Each of these silently no-ops when the attribute is absent - which is exactly what happens before open() runs and after a tab is removed - so a rename is undetectable by any tool, and the status bar cannot be tested without a real main window.
  - **proposed_qt6_design**: Declare the contract as signals on the document (sigReadout, sigNotify, sigProgress, sigAnnotationsChanged) that the shell connects when it adds the tab, and inject the two services the document actually needs (Settings, RegionModeResolver) through its constructor. No window() walks, no hasattr.
  - **effort**: medium

  - **title**: The adaptive tool bar measures six layout stages once at build time and publishes a fixed pixel floor forever
  - **file**: src/audian/audian.py
  - **line**: 665
  - **evidence**: ToolStrip.set_stages (665) applies each of the six stages declared at 2492-2517 ('full', 'glyphs', 'tight', 'no-amplitude', 'no-panels', 'no-modes'), forces layout.activate() and records totalMinimumSize().width(); minimumSizeHint (706) unconditionally returns self._stages[-1][2]; fit (723) compares those baked widths against self.width() on every resizeEvent (718). setup_toolbar caps every child's height in a one-shot loop over the layout (2415-2419), and setup_toolbar_stages (2437) exists only to insert the overflow button before that loop runs - a build-order constraint documented in a docstring rather than enforced.
  - **why_it_matters**: The widths are measured once, in whatever font, DPI and theme were current at construction. A theme switch changes the font metrics and repolish (2205) re-runs the layout but never re-runs set_stages; Qt6's PassThrough DPI rounding puts fractional scales into the same numbers. The bar then folds at the wrong width or refuses to shrink to the size it claims it can.
  - **proposed_qt6_design**: Re-measure on themeChanged / screenChanged / font-change, or replace the whole mechanism with a QToolBar plus QWidgetActions and an explicit overflow policy. At minimum make set_stages idempotent and call it from restyle_chrome, and express stage composition as data (a list of group names to fold) instead of the nested closures at 2456-2490.
  - **effort**: medium

  - **title**: Menus, cheat sheet, palette and rebinder each discover actions by a different mechanism
  - **file**: src/audian/audian.py
  - **line**: 2656
  - **evidence**: all_actions (2656) walks self.menus recursively and de-dupes by id(); CommandPalette (1160) snapshots that list filtered by isEnabled() at construction and caches the QActions in Qt.UserRole item data (1207); CheatSheet.GROUPS (1232-1345) is a hardcoded table of 8 groups naming ~60 action attributes as strings, resolved with getattr(gui.acts, name, None) (1385) and silently skipping misses; ShortcutsDialog (1406) walks all_actions again and writes back with act.setShortcut (1465) without persisting. command_palette and cheat_sheet belong to no menu and are appended by hand at 2674-2678.
  - **why_it_matters**: A renamed or moved action drops out of the cheat sheet with no error; a newly added action appears in the palette but has no cheat-sheet group; a rebind is lost on restart. Four consumers, four discovery paths, none authoritative.
  - **proposed_qt6_design**: Declare actions as data (id, text, default shortcut, group, scope, tooltip, glyph); build menus and the tool bar from the declaration; have the palette, cheat sheet and rebinder all read the registry. The cheat sheet's grouping becomes a field on the declaration rather than a parallel table.
  - **effort**: medium

  - **title**: The status bar is a hand-built six-field readout with baked-in font-metric widths and rich-text colour, and doubles as the only feedback channel
  - **file**: src/audian/audian.py
  - **line**: 1818
  - **evidence**: setup_statusbar (1818) builds: message_label, hand-elided by elide_message (2049) with a 137-character minimum-width bug documented at 1837-1848; six mono readouts sized once from READOUT_TEMPLATES (1750-1757) via fm.horizontalAdvance; StatusSeparator rules kept in _readout_separators so hiding a field does not leave a gap (1866, 1963); an error button that opens a QPlainTextEdit log (show_log 2154); a fixed-width progress slot (2108-2140); and a mode chip. Colours are written into HTML spans (readout_markup 1996), so refresh_readouts (2230) must replay every value on a theme switch. notify (2069) is simultaneously the UI channel and the logging front end, capping self.messages at 500.
  - **why_it_matters**: The widths are computed once from the construction-time font; a theme or DPI change invalidates them and nothing recomputes. The rich-text colour path is why the status bar cannot join the QSS pipeline. And because notify() is both UI and logging, no non-GUI code can report anything at all.
  - **proposed_qt6_design**: A StatusService with a typed readout API (field enum -> value + active) and a NotificationService that fans out to both a logging handler and the status widget. Render readouts as QSS-driven child labels using dynamic properties instead of inline HTML, and recompute widths on a font-change signal.
  - **effort**: medium

  - **title**: argparse and QApplication construction are entangled, and the application has no identity
  - **file**: src/audian/audian.py
  - **line**: 5013
  - **evidence**: `args, qt_args = parser.parse_known_args(cargs)` (5013) then `app = QApplication(sys.argv[:1] + qt_args)` (5024): Qt's arguments are whatever argparse did not recognise from cargs, recombined with the real sys.argv[0]. No setApplicationName / setOrganizationName / setApplicationVersion / setDesktopFileName / setWindowIcon anywhere in the tree, while QSettings('audian','audian') is hardcoded in databrowser.py:1448. logging.basicConfig is called inside audian_cli (4981); plugins.py:53 still uses print().
  - **why_it_matters**: A mistyped audian flag is silently forwarded to Qt instead of being rejected. On Wayland, a missing desktop file name means a generic window icon and no grouping - Qt6 makes this visible where X11 sometimes guessed. Hardcoded QSettings identifiers cannot be redirected for tests without monkeypatching, which scripts/smoke_test.py:247 has to do.
  - **proposed_qt6_design**: An app_bootstrap(argv) that (1) parses audian arguments with `--` separating Qt arguments explicitly, (2) constructs the QApplication and sets applicationName/organizationName/applicationVersion/desktopFileName/windowIcon, (3) configures logging, (4) applies the theme, and returns (app, options). Audian's constructor then takes an options object instead of 9 positional parameters - the second caller, scripts/smoke_test.py:350, currently passes them by position.
  - **effort**: small

  - **title**: Per-channel actions are created lazily and rewired by disconnecting every slot
  - **file**: src/audian/audian.py
  - **line**: 3866
  - **evidence**: set_channel_action (3866) creates `Channel &{c}` and `Show channel {c}` QActions on demand, appends them to acts.channels / acts.show_channels and to the Toggle/Show submenus, then on every re-sync does cact.toggled.disconnect() (3886), setChecked, and a fresh connect. It is called for every channel from adapt_menu (4616) on each tab switch and from databrowser.py:1784. Bindings are Alt+0..Alt+9 and Ctrl+0..Ctrl+9, with none above channel 9 (3885-3891); extras are only hidden, never destroyed (3879-3882).
  - **why_it_matters**: The actions are process-global but their checked state belongs to one document, so a tab switch re-derives all of them. The argument-less disconnect() also removes any future listener, and in PySide6 it raises when there is nothing connected. The Toggle/Show submenus grow to the largest channel count seen in the session and keep the surplus entries hidden forever.
  - **proposed_qt6_design**: Build the channel actions from the current document's channel count when the document becomes current, parented to the submenu so clear() destroys them - the pattern build_annotation_layer_actions already uses (4172-4176) - and drive check state with blockSignals rather than disconnect/connect.
  - **effort**: small

  - **title**: Screenshot save mixes Qt grabbing, PIL metadata and a file dialog in one method, and the drop handler parses that metadata back by string surgery
  - **file**: src/audian/audian.py
  - **line**: 2744
  - **evidence**: screen_shot (2744) grabs via QScreen.grabWindow(winId), reads the time axis through self.browser().panels['trace'].axs[0].getAxis('bottom') (2751), reaches into self.browser().plot_ranges['t'].r1[0] (2755), writes four PngInfo keys, opens a QFileDialog, then round-trips the pixmap QBuffer -> io.BytesIO -> PIL.Image -> save. dropEvent (2811) reverses it: absent 'ScreenshotFile' it splits the file stem on '-' and parses a duration character by character with a hand-rolled h/m/s/ms loop (2842-2850).
  - **why_it_matters**: The shell reaches three levels into the browser's internals (panels, axes, plot_ranges); a Qt6 grabWindow that returns nothing on Wayland breaks it silently; and the filename fallback will happily accept nonsense. This is the least-covered path in the file.
  - **proposed_qt6_design**: A ScreenshotService that asks the document for a typed ViewState (file, t0, window, channels) and returns bytes; the window owns only the file dialog. Grab with QWidget.grab(). Replace the filename fallback with an explicit 'no metadata, ignored'.
  - **effort**: medium

  - **title**: Two hand-maintained enable lists stand in for a document-scope concept, and they disagree with the shortcut system
  - **file**: src/audian/audian.py
  - **line**: 1602
  - **evidence**: self.data_menus / self.data_acts are appended to from ten different setup_* methods (3120, 3260, 3446, 3577, 3740, 3780, 3949-3951, 4290, 4386, 4476, 4568-4570; data_acts at 2884-2891 and 3074). show_startup (1704) disables everything in them; hide_startup (1727) re-enables. Actions reachable only by shortcut - zoom_back, play_window, toggle_grid, auto_scroll - sit inside a data_menu but are themselves still enabled.
  - **why_it_matters**: Disabling a QMenu disables its menuAction, not its children, so the menu entry greys out while the key still fires into the StartupPage. These two lists are the only gate on a 127-action surface against 'no document', and they are maintained by hand at 20 call sites.
  - **proposed_qt6_design**: Give each declared action a scope (application or document) and let the registry set isEnabled from `current_document is not None` once per tab change, deleting both lists and the show_startup/hide_startup loops.
  - **effort**: small

- **behavior_contract**:
  - `audian` with no file arguments opens the StartupPage empty state: a dashed drop frame
  - labelled 'Drop .wav files here', an 'Open files…\tCtrl+O' primary button, a RECENT
  - column of up to 10 rows (name, then a fixed mono grid of channels / duration / sample
  - rate, then an elided parent directory), and a GET STARTED column of six key chips. It is
  - not a tab and has no close button.
  - `audian FILE...` opens the files, hides the empty state and shows one tab per recording;
  - with a single file the tab strip is hidden entirely. Tabs sit down the LEFT edge,
  - upright, reading bottom-to-top, each with a hand-painted close X at the top of the tab
  - that closes that tab when clicked, and the strip costs ~30 px of width rather than ~180.
  - CLI contract: -c accepts comma lists and a-b ranges (5, 0,3, 0-7); -f / -l set high/low-
  - pass cutoffs in Hz; -i appends loader kwargs; -u / -U take an optional threshold
  - defaulting to 1.5 with -U selecting the clipping variant; -a/--events names a session
  - bundle applied to the FIRST browser only and then dropped (every other file looks for a
  - bundle beside itself); --theme dark|light overrides the persisted theme for that run;
  - --version prints the version; -v / -vv raise logging to INFO / DEBUG; unrecognised
  - arguments are forwarded to Qt; on Windows the file arguments are glob-expanded.
  - The theme chosen with --theme or Ctrl+Shift+L persists to settings.json in the user
  - config directory and is restored next launch; the default is dark. Ctrl+Shift+L flips
  - dark<->daylight and posts 'Daylight theme - high contrast for outdoor use' or 'Dark
  - theme' for 2.5 s. The switch must leave nothing in the old palette: menus, tool bar,
  - status bar, tab spine, startup page, dialogs AND the pyqtgraph plots change together,
  - and every glyph icon is re-rendered in the new ink.
  - The menu bar carries File, Region, Spectrogram, View and Help. View nests Time,
  - Amplitude, Frequency, Envelope, Channels (with Toggle channels and Show channels
  - submenus), Panels, Fixed labels (with a Layers submenu), Editable labels and Traces,
  - plus Toggle grid, Daylight mode and Toggle maximize. Mnemonic underlines appear only
  - while Alt is held (and remain while a menu popup is open); Alt+F and friends work
  - whether or not the reveal fires.
  - File and region keys, exactly as bound: Ctrl+O open, Ctrl+T new tab, Ctrl+S and
  - Ctrl+Shift+S save window, Alt+Ctrl+S screenshot, Ctrl+W close tab, Ctrl+Q quit; Ctrl+R
  - rectangle zoom, Ctrl+Z pan&zoom, Backspace / Alt+Left zoom back, Shift+Backspace /
  - Alt+Right zoom forward, Alt+Backspace zoom home; exclusive region modes z / P / a / s /
  - q / b (zoom, play, analyze, save, request, label) with zoom checked at start; Ctrl+C
  - cross hair; Space plays the window; Shift+P cycles the playback source.
  - Time keys: Alt+Z link time zoom, Ctrl+Shift+T toggle start time, Shift+T zoom in
  - centered, T zoom out centered, Alt+T link time scroll, PageDown / PageUp seek forward /
  - backward, Down / Up small step, End and Ctrl+End skip to end, Home and Ctrl+Home skip to
  - start, . snap, ! auto scroll.
  - Amplitude keys: Alt+A link amplitude, + and = zoom in, - zoom out (both acting on the
  - amplitude axis under the pointer), v Fit Y, Shift+V reset, C center. Frequency keys:
  - Ctrl++ and Ctrl+= zoom in, Ctrl+- zoom out (pointer-directed), Right / Left move up /
  - down, Ctrl+Right / Ctrl+Left home / end, Ctrl+V back to the band the spectrogram opens
  - at, Ctrl+Shift+V reset to 0 Hz - Nyquist.
  - Spectrogram keys: Shift+R / R resolution up / down, Shift+O / O overlap up / down,
  - Shift+C cycle colour map, Alt+P link power, Shift+D / D power up / down, Shift+K / K max
  - power up / down, Shift+J / J min power up / down; filter keys Shift+H / H highpass,
  - Shift+L / L lowpass. Envelope: Alt+E link, Ctrl+E show, Shift+E / E cutoff up / down.
  - Channel keys: Alt+C link channels, Ctrl+A select all, Down / Up (and Alt+PgDown /
  - Alt+PgUp) next / previous channel, PageDown / PageUp select next / previous, Delete
  - hides deselected channels, Alt+0..Alt+9 toggle a channel, Ctrl+0..Ctrl+9 show a channel.
  - Channels 10 and above deliberately get no binding, and a channel keeps its number-to-key
  - mapping regardless of how many channels the file has.
  - Panel keys: F2 traces, Shift+F2 mean spectrogram, F3 spectrograms, Shift+F3 reset the
  - trace/spectrogram split, F4 power, F5 colour bars, F6 navigator, Shift+F6 navigator all
  - channels, Alt+F6 navigator activity. Label keys: F8 fixed labels, Shift+F8 show all
  - layers, Ctrl+Shift+A load a bundle, n / Shift+N step fixed labels, F9 editable labels,
  - Ctrl+L categories, Ctrl+M label list, Ctrl+Delete delete the selected label, Shift+B
  - undo one label change. View: g grid, Ctrl+PgDown / Ctrl+PgUp next / previous tab,
  - Ctrl+Shift+L daylight, Ctrl+Shift+M maximize. Help: Ctrl+Shift+P palette, ? and Shift+/
  - cheat sheet, Ctrl+K shortcut list.
  - Ctrl+Shift+P opens a non-modal command palette: typing fuzzy-matches every enabled menu
  - action, rendered as 'Name    ·  Menu › Path    [keys]'; Up and Down move the selection
  - from inside the search field; Enter or a click closes the palette and triggers the
  - action. Only one palette exists at a time.
  - ? opens a non-modal translucent cheat sheet grouping the bound keys under Navigate /
  - Zoom / Filter / Spectrogram / Channels / Fixed labels / Regions / Editable labels;
  - Escape or ? closes it. Actions with no shortcut, or that are hidden, do not appear.
  - Ctrl+K opens a searchable, non-modal shortcut list: one row per action with its name,
  - its menu path and an editable key sequence. Editing a row rebinds that action
  - immediately and posts 'Name → Ctrl+X' (or 'unbound') in the status bar; the filter box
  - hides non-matching rows.
  - The tool bar shows, left to right: transport (home, seek back, seek forward, end, play),
  - a rule, six exclusive region-mode buttons with glyph and label, a rule, six panel
  - toggles (traces, spectrograms, mean spectrogram, power, colour bars, navigator)
  - reflecting the current tab, a rule, 'Fit Y' plus a 'Y: <policy>' popup carrying the
  - three y-mode entries and Link / Reset / Center amplitude, then a right-aligned channel
  - button reading 'ch NN' or 'ch NN  shown/total'. The whole bar is disabled while no file
  - is open.
  - As the window narrows the bar degrades in a fixed order and never forces the window
  - wider: first the labels leave the region-mode and Fit Y buttons (tool tips still carry
  - name and shortcut), then the 12 px gaps around the three rules collapse, then whole
  - groups fold right-to-left into an overflow '…' menu - amplitude, then panels, then
  - region modes - each group taking the rule in front of it. A folded control keeps its
  - name, glyph and checked state and gains a rendered shortcut. The transport and the
  - channel button never fold, and the overflow button's tool tip names which groups are off
  - the bar.
  - The status bar shows, left to right: a transient message (elided to its slot, full text
  - in the tool tip, cleared after 4 s), six fixed-width mono readouts t / Δt / A / f / P /
  - ch separated by hairlines, a persistent 'N error(s)' button that opens the message log,
  - a progress slot (hidden until a job runs, showing e.g. 'Building overview… 47%' and
  - clearing itself shortly after 100%), and a mode chip naming the region mode. The readout
  - row is hidden entirely while no file is open, and Δt / f / P plus their rules appear
  - only while the cross hair is on (Ctrl+C). No readout ever reflows the bar while the
  - pointer moves.
  - notify() levels colour the message - info fg, success green, warning accent, error
  - danger - and only an error raises the persistent error button; opening the log clears
  - the counter. Every notification is also written to the Python logger, and the log dialog
  - lists the last 500 messages.
  - Dragging files onto the window tints the empty state and changes its label to 'Release
  - to open'. Dropping audio files (any of the 20 accepted suffixes) opens them. Dropping a
  - PNG that audian wrote seeks the current recording to the recorded position, using the
  - embedded ScreenshotFile/ScreenshotTime keys or, failing those, the file name. Dropping
  - anything else reports 'dropped file is neither audio nor a screenshot'.
  - Alt+Ctrl+S writes a PNG of the window with ScreenshotFile / ScreenshotTime /
  - ScreenshotWindow / ScreenshotChannels embedded, defaulting to 'screenshot.png' beside
  - the recording or beside the last save, reporting success with a path relative to the
  - working directory and reporting a permission failure as an error rather than raising.
  - Opening a file adds it to the recent list (path, name, parent, channels, duration,
  - rate), most recent first, capped at 10, deduplicated by resolved path, persisted to
  - recent.json and shown on the empty state; clicking a row opens that file.
  - Link switches fan changes between tabs: time zoom on by default, time scroll off,
  - amplitude/frequency/power ranges on, and filter, envelope, channels, panels and audio
  - all on. Turning one off confines that change to the current tab. Opening two files with
  - different channel counts turns channel linking off automatically and unchecks its menu
  - entry. The spectrogram opening band is pushed to every tab but clamped per recording
  - against that recording's own Nyquist, and only the current tab writes it to settings.
  - Shift+drag plays the dragged region and Alt+drag analyses it, whatever region mode is
  - selected; the override applies to that one drag only.
  - The window opens at 70% of the primary screen's available geometry (1280x800 if no
  - screen is reported), titled 'Audian <version>'. Nothing about the geometry is persisted
  - or restored. Ctrl+Shift+M asks the window manager to toggle maximized and tolerates
  - being ignored.
  - A file that fails to open reports 'can not open <path>: <error>' in the status bar, logs
  - the traceback, shows a critical message box, removes its tab, and falls back to the
  - empty state if it was the last one. A successful open reports 'opened <name>'.
  - Closing a tab (Ctrl+W, the tab's X, or File > Close tab) flushes that recording's
  - editable labels to disk first; Ctrl+Q flushes every tab's labels before quitting.
  - The Traces and Spectrogram > Active submenus are rebuilt from the current tab on every
  - tab switch, and Active is hidden unless the recording has more than one spectrogram. The
  - per-axis zoom entries are renamed after the trace they act on ('Zoom filtered amplitude
  - in'), never after the axis letter. Fixed labels > Layers lists one checkable entry per
  - layer of the loaded bundle with its event count, or a single disabled 'no annotations
  - loaded'.
  - Plugins: any file matching audian*.py in the current working directory is imported at
  - startup and its audian_*_traces / audian_*_analyzer callables are registered as trace
  - and analyzer factories.
- **risk**: high - this file is the process entry point, the sole owner of 127 QActions that other modules mutate by attribute name, and the only place the theme, the audio device and the cross-tab link matrix live, so a mistake here is not localised; it has no unit tests beyond a whole-window offscreen smoke test (scripts/smoke_test.py) plus two symbol imports from tests/test_panelsplitter.py:1992 and tests/test_parameterbar.py:458.
- **notes**: Sequencing notes for the lead.  (1) Hard blockers that fail loudly on PySide6, in roughly the order they bite: the QAction/QActionGroup import move (audian.py:21); pyqtgraph binding selection, because `import pyqtgraph as pg` at line 11 precedes every Qt import in the file; QFileDialog.getOpenFileNames(directory=...) (4652); the four setShortcuts([StandardKey, ...]) lists (3216, 3226, 3916, 3924); event.pos() in VerticalTabBar (464, 478); and the argument-less toggled.disconnect() in set_channel_action (3886).  (2) Silent regressions to watch, none covered by any test: QScreen.grabWindow(winId) on Wayland (2746), which breaks Alt+Ctrl+S and transitively the PNG-drop seek; setShortcut(" ") for Space (3079); Qt6 flag arithmetic in region_mode_for_modifiers (2717) and toggle_maximize (4830); glyph icons rendered without devicePixelRatio (287) now that Qt6 scales unconditionally; and ToolStrip's six stage widths (665), measured once at one DPI and never re-measured, against Qt6's PassThrough rounding policy.  (3) The `except RuntimeError` guards at 700 (ToolStrip.fit), 1701 (refresh_glyph_icons), 2213/2222 (repolish), 2253 (restyle_chrome) and 2689 (close_dialog) all catch "wrapped C++ object has been deleted". PySide6 raises the same RuntimeError, but shiboken's ownership rules differ from sip's - particularly for QMenu/QAction and for the QProxyStyle at 484 - so each guard is a place where a latent lifetime bug turns into a crash rather than a swallowed exception. Audit with shiboken6.isValid() rather than widening the catch.  (4) Cheapest high-value refactors, worth doing BEFORE any Qt6 edit because they shrink the blast radius of everything else: (a) implement closeEvent and rename Audian.close -> close_tab (small; closes a real data-loss path); (b) extract app_bootstrap() from audian_cli so QApplication, logging and application identity are set in one place, and have Audian take an options object instead of 9 positionals (small; scripts/smoke_test.py:350 is the second caller and passes them positionally today); (c) route every document-scoped action through require_browser and derive enabled-ness from `a document exists` (small-to-medium; deletes data_menus/data_acts); (d) replace the `class acts` bag with an ActionRegistry (large, but it is the prerequisite for splitting the file at all, and it is where the DataBrowser -> window mutations at databrowser.py:1836-1884, 2162-2166, 2319-2322 and 7489-7501 have to be inverted).  (5) Circular imports to break as step zero: databrowser.py does `from .audian import settings` / `save_setting` lazily at ten sites (3481, 3516, 4966, 5048, 5513, 5577, 6545, 6592, 6654, 6683). Move settings/save_setting/settings_path into their own module or a Settings service first, or any split of audian.py cascades. Fold the stray QSettings('audian','audian') at databrowser.py:1448/7172 into the same store while you are there.  (6) Multiprocessing: audian.py:5037 and compresseddata.py:565 each call set_start_method unconditionally - forkserver on POSIX, spawn on Windows. The call is correctly placed before QApplication exists and must stay there, since forking a process that already owns a QGuiApplication is undefined on Qt6/Wayland. CompressedData spawns workers with shared ctypes Arrays while the event loop runs (compresseddata.py:334-371), and the shell polls their progress into the status bar through DataBrowser.report_overview_progress (databrowser.py:3162), which reaches the window via hasattr(window, 'set_progress') - so the loader refactor and the back-channel refactor touch the same seam.  (7) runaudian.py needs no migration work; it only pins `audian.main(list[str])`. If main() gains an options object, keep a list-of-strings overload or update the file.
