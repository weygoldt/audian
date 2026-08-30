# Recon: system theme + settings persistence

Machine-written notes from the mapping pass that preceded the
"follow the system theme" and "settings live in .config" work.  Kept
because the two features touch dozens of call sites between them and
the next person should not have to re-read `theme.py` end to end.

## theme-core

src/audian/theme.py (3336 lines) is a fully self-contained, hand-rolled design system with ZERO system-theme integration. It defines two hardcoded 23-key hex token tables (DARK_TOKENS theme.py:288-351, LIGHT_TOKENS theme.py:364-396) selected by name through THEMES (theme.py:398), copied into a mutable module-global TOKENS (theme.py:407) by set_theme() (theme.py:425-444), which also drops only the "palette:" and "stylesheet:" entries of _CACHE (theme.py:442-444). token() is a bare TOKENS[name] lookup that raises KeyError on typos (theme.py:447-457). _resolve() (theme.py:459-472) accepts a dotted token name, OR a dark-theme hex constant remapped through _BY_VALUE (theme.py:412), OR any raw colour passed through to pg.mkColor. _BY_VALUE has 22 entries for 23 keys because DARK "fg" and "on.primary" are both "#E6EDF6" and the later key wins: under the light theme theme._resolve(theme.FG) returns "#FFFFFF" (on.primary) instead of "#000000" (fg) — verified by running the module. Fonts are a pure hardcoded stack (FONT_UI_FAMILIES theme.py:649-655, FONT_MONO_FAMILIES theme.py:660-666, SIZE_PT=10 theme.py:668); the only system input is QFontDatabase.families() used to pick the first *installed* family from that stack (theme.py:672-698), and apply() pushes theme's own font onto the app with app.setFont(font_ui()) (theme.py:2532) — QApplication's/QFont's default is never read. Icons are all painted by hand from tokens: QPainterPath glyphs in audian.py:116-330 (glyph_icon/glyph_pixmap), a QIconEngine swatch in labeloverlay.py:800-824, pixmap chips in eventoverlay.py:1284-1367, a gradient swatch in databrowser.py:623-638. QIcon.fromTheme and QStyle.standardIcon are used NOWHERE (grep across src/audian returns no hits; audian.py:302-307 explicitly rejects platform standard icons). The global chrome is a Template QSS of 406 lines (theme.py:2034-2441) interpolated in stylesheet() (theme.py:2443-2493) plus a fully-populated QPalette (theme.py:1979-2031); apply() (theme.py:2509-2534) forces QStyleFactory.create("Fusion"), then palette, font, pg config and stylesheet, in that order. Live theme switching is a five-layer manual walk: theme.apply() reaches only app palette/font/QSS; Audian.set_app_theme (audian.py:1648-1690) then calls refresh_glyph_icons(), restyle_chrome(), each browser's apply_theme(), rebuilds StartupPage wholesale, and repolish()s every widget; DataBrowser.apply_theme (databrowser.py:2744-2806) re-runs style_channel_figure/style_figure/apply_theme/polish on every figure, axis, border, splitter, overlay and chip. restyle_tree (theme.py:1507-1541) only fixes widgets carrying the three dynamic properties set by tint()/frame()/band() (theme.py:1441-1505) — every other inline setStyleSheet with a baked token value (30 sites in audian.py, 3 in databrowser.py) is either individually re-applied by restyle_chrome/update_state or missed entirely (audian.py:1908 error_button is a confirmed miss).

### Facts

- **Module docstring declares theme.py the single source of truth: no hex literal, named Qt colour, RGB tuple, pen width, font family string, spacing literal or colormap name may exist anywhere else in the codebase.**
  `src/audian/theme.py:1-70`
  Also documents the layer preference order (appliers > role helpers > low-level constructors > raw tokens), the import-time contract (imports cleanly without a QApplication; token constants are plain module-level strings; anything touching QFont/QPalette/font DB is lazy and memoised), the measured contrast ruling for FG_FAINT, and that the module-level constants always hold the DARK reference values while helpers resolve through the active table.
- **__all__ exports 130+ names covering themes, surfaces, text, accents, data series, metrics, fonts, low-level constructors, role helpers, pyqtgraph appliers, qt chrome, data palettes, annotations, contrast and perceptual separation.**
  `src/audian/theme.py:96-247`
- **The 20 hardcoded dark hex constants are module-level strings defined before any table.**
  `src/audian/theme.py:255-285`
  BG_BASE #0B0F16, BG_SURFACE #11161F, BG_RAISED #171D28, BG_PLOT #0D1219, BORDER #232B38, BORDER_HI #333F52 (255-260); FG #E6EDF6, FG_MUTED #9AA7B8, FG_FAINT #6B7788 (263-265); PRIMARY #4C8DFF, PRIMARY_DIM #2A5FB8, ACCENT #F0A828, SUCCESS #3FBF7F, DANGER #FF5C5C (268-272); TRACE_RAW #7FD4FF, TRACE_FILTERED #FFC65C, TRACE_ENVELOPE #FF7AB6, TRACE_ZERO #2B3546 (275-278); GRID_COLOR = TRACE_ZERO, GRID_ALPHA = 0.35 (281-282). THEME_DARK='dark', THEME_LIGHT='light' (284-285).
- **DARK_TOKENS has exactly 23 keys, in this order.**
  `src/audian/theme.py:288-351`
  bg.base, bg.surface, bg.raised, bg.plot, border, border.hi, fg, fg.muted, fg.faint, primary, primary.dim, accent, success, danger, trace.raw, trace.filtered, trace.envelope, trace.zero, on.primary, bg.lane, edge, ann.trial, ann.pulse. The first 18 alias the module constants; the last 5 are hex literals defined only inside the table: on.primary = FG, bg.lane = #151C28, edge = #47566E, ann.trial = #FF253C, ann.pulse = #009A88.
- **LIGHT_TOKENS covers exactly the same 23 keys in exactly the same order — no key is present in one table and missing from the other.**
  `src/audian/theme.py:364-396`
  Verified by importing the module: len(DARK_TOKENS)==len(LIGHT_TOKENS)==23, set difference empty in both directions, key order identical. Values: bg.base #FFFFFF, bg.surface #EDEFF3, bg.raised #FFFFFF, bg.plot #FFFFFF, border #9AA6B4, border.hi #5C6B7C, fg #000000, fg.muted #2B3440, fg.faint #5A6675, primary #0B3FA8, primary.dim #082E7A, accent #8A4200, success #0B5C34, danger #A11212, trace.raw #0B4F8A, trace.filtered #8A4200, trace.envelope #8E1A5C, trace.zero #AAB4C0, on.primary #FFFFFF, bg.lane #E8ECF3, edge #7C8A9B, ann.trial #B60023, ann.pulse #007B6C.
- **THEMES maps theme name -> token table; TOKENS is a mutable module-global copy of DARK_TOKENS that set_theme updates IN PLACE so already-imported call sites see new values.**
  `src/audian/theme.py:398-407`
- **_BY_VALUE is {hex.upper(): dotted name} built once from DARK_TOKENS; it holds 22 entries for 23 keys because DARK 'fg' and 'on.primary' are the same value #E6EDF6 and the later key wins.**
  `src/audian/theme.py:409-412`
  Consequence, verified by running the module under set_theme('light'): theme._resolve(theme.FG) returns '#FFFFFF' (the on.primary value) while theme.token('fg') returns '#000000'. So any call site that passes the raw constant theme.FG into qcolor/pen/brush gets the WRONG colour in the light theme. The docstring at 1298-1303 documents the same hazard for annotation tokens but the fg/on.primary collision is live. The documented-harmless collision is GRID_COLOR is TRACE_ZERO (same key, same value).
- **_ACTIVE is a one-key dict holding the active theme name; current_theme() reads it.**
  `src/audian/theme.py:414-422`
- **_CACHE is one flat dict[str, Any] keyed by prefixed strings, holding every memoised object in the module.**
  `src/audian/theme.py:417`
  Key prefixes actually written: 'families' (674,681), 'family:<stack>' (692,696), 'font:{mono|ui}:{pt}:{bold}' (704,716), 'metrics:{ui|mono}:{pt}' (739,742), 'dim:{theme}:{base}:{ground}:{amount}:{min_contrast}' (1143,1152), 'palette:{theme}' (1987,2030), 'stylesheet:{theme}' (2453,2492), 'cmap:{theme}:{name}' (2655,2664).
- **set_theme(name) does exactly four things and repaints nothing.**
  `src/audian/theme.py:425-444`
  table = THEMES[name] (raises KeyError on an unknown name); _ACTIVE['name'] = name; TOKENS.clear() then TOKENS.update(table); then deletes ONLY the _CACHE keys whose prefix is 'palette:' or 'stylesheet:'. The font, family, metrics, dim: and cmap: caches are NOT dropped — dim: and cmap: are safe because their keys embed current_theme(); font/family/metrics are safe today only because fonts are theme-independent. The docstring states callers must call apply() afterwards and re-run the style_* appliers on live plot items.
- **token(name) is a bare TOKENS[name] lookup that deliberately raises KeyError on an unknown name.**
  `src/audian/theme.py:447-457`
- **_resolve(c) is the compatibility shim that makes hardcoded constants theme-aware.**
  `src/audian/theme.py:459-472`
  If c is a str: return TOKENS[c] when c is a dotted token name; else look c.upper() up in _BY_VALUE and return TOKENS[that name]; else return c unchanged (any other '#rrggbb', any pg.mkColor-able value). It is called from qcolor() (774-789) and relative_luminance() (2730-2738) only; every pen/brush/mix path reaches it through qcolor.
- **Hardcoded colour constants are used directly (not via token()) in only three places inside theme.py, and all three are theme-aware because they flow through _resolve.**
  `src/audian/theme.py:281-282,916-918,2721-2722`
  (1) GRID_ALPHA in grid_pen() — an alpha float, not a colour, theme-independent (theme.py:281-282, 916-918; the pen colour itself uses the 'trace.zero' token name). (2) MARKER_ICON_BG = BG_RAISED and MARKER_ICON_RING = BORDER (theme.py:2721-2722) — dark hex constants that _resolve remaps: verified under light they yield #FFFFFF and #9AA6B4. (3) The docstring/comment references. Everything else in theme.py uses dotted token names.
- **The only place OUTSIDE theme.py that consumes hardcoded theme constants as colours is labeloverlay._SwatchEngine.paint.**
  `src/audian/labeloverlay.py:814-815`
  painter.setBrush(theme.qcolor(theme.MARKER_ICON_BG)); painter.setPen(theme.pen(theme.MARKER_ICON_RING)). Both go through _resolve so they are theme-aware. Every other src/audian reference to a theme colour constant is in the docstrings of theme.py itself or in tests/test_theme.py.
- **MARKER_COLORS (8 dark hexes) and LIGHT_MARKER_COLORS (8 light hexes) are raw hex lists that are NOT tokens and are NOT in _BY_VALUE; they are selected by theme through _MARKER_TABLES / marker_colors() / marker_color().**
  `src/audian/theme.py:2672-2718`
  marker_color(index) wraps modulo the active palette length so a caller never knows which theme is live.
- **FONT_UI_FAMILIES is a hardcoded 5-entry stack ending in the generic 'sans-serif'.**
  `src/audian/theme.py:649-655`
  ('Inter', 'Adwaita Sans', 'Noto Sans', 'DejaVu Sans', 'sans-serif').
- **FONT_MONO_FAMILIES is a hardcoded 5-entry stack ending in the generic 'monospace'.**
  `src/audian/theme.py:660-666`
  ('JetBrainsMono Nerd Font', 'JetBrains Mono Nerd Font', 'Adwaita Mono', 'DejaVu Sans Mono', 'monospace') — two spellings of the Nerd Font on purpose.
- **Font sizes are hardcoded points, never read from the system: SIZE_PT = 10, SIZE_SMALL_PT = 9.**
  `src/audian/theme.py:668-669`
- **_installed_families() is the ONLY system input to font selection: it caches frozenset(QFontDatabase.families()) under _CACHE['families'], falling back to an empty frozenset if there is no QApplication or no fontconfig.**
  `src/audian/theme.py:672-682`
  That cache is never invalidated by set_theme.
- **_first_installed(stack) returns the first family of the stack that QFontDatabase reports, else the stack's last (generic) entry; cached under 'family:' + '|'.join(stack).**
  `src/audian/theme.py:685-698`
- **_font() builds and memoises a QFont from the resolved stack; font_ui/font_mono are thin wrappers and every caller gets a fresh COPY.**
  `src/audian/theme.py:700-735`
  Key 'font:{ui|mono}:{pt}:{bold}'. Sets setFamilies(list(stack)) to keep Qt's own fallback chain for missing glyphs, setPointSize(pt), setBold, StyleStrategy.PreferAntialias, and for mono StyleHint.Monospace + setFixedPitch(True). Returns QFont(font) so callers may mutate. NOTHING here reads QFont(), QApplication.font(), QFontDatabase.systemFont() or a platform theme hint.
- **ui_metrics/mono_metrics return cached QFontMetrics keyed 'metrics:ui:{pt}' / 'metrics:mono:{pt}' via _metrics().**
  `src/audian/theme.py:738-762`
- **apply() pushes theme's own font onto the application, overwriting whatever the platform theme supplied.**
  `src/audian/theme.py:2532`
  app.setFont(font_ui()) at theme.py:2532. There is no code anywhere in src/audian that reads the app/system font: grep for 'app.font()', 'QApplication.font', 'systemFont' returns nothing; QFontDatabase appears only at theme.py:84 (import) and theme.py:678.
- **Every icon in the application is painted at runtime from theme tokens; there are no image assets, no SVG, and QIcon.fromTheme / QStyle.standardIcon are used nowhere in src/audian.**
  `src/audian/audian.py:301-330`
  grep for 'fromTheme' and 'standardIcon' across src/audian returns zero hits; the only QStyle uses are QStyle.ControlElement.CE_TabBarTabShape (audian.py:418) and QStyle.StyleHint.SH_UnderlineShortcut (audian.py:508).
- **The toolbar glyph system lives in audian.py, is defined in unit-box polygons/paths, and takes its ink from four token names.**
  `src/audian/audian.py:71-330`
  GLYPH_NORMAL='fg.muted', GLYPH_ACTIVE='fg', GLYPH_DISABLED='fg.faint', GLYPH_ON='on.primary', GLYPH_DISABLED_ALPHA=None (audian.py:75-83). _FILLED_GLYPHS/_MIRRORED_GLYPHS (audian.py:87-114), _filled_glyph_path (116-136), _draw_glyph (138-288, using theme.pen/theme.brush/theme.font_ui throughout), glyph_pixmap (290-298), glyph_icon (301-330) which explicitly adds pixmaps for Normal/Active/Selected/Disabled x Off and Normal/Active/Selected/Disabled x On rather than letting Qt fade one pixmap. Docstring: 'above all no QStyle standard icon: those are pre-rendered pixmaps in the platform theme's own grey and never honour ours.'
- **A QIcon bakes its pixmaps at build time, so icons need an explicit rebuild pass on a theme switch; Audian keeps a (target, kind) registry for exactly that.**
  `src/audian/audian.py:1700-1717`
  _set_glyph appends to self._glyph_targets and sets the icon (audian.py:1700-1708); refresh_glyph_icons() re-runs glyph_icon(kind) over the registry, swallowing RuntimeError for deleted C++ objects (audian.py:1711-1717). Icons NOT in that registry (chips, swatches, colormap swatches) are rebuilt by their owners instead.
- **Other icon producers, all token-driven and all requiring an explicit rebuild.**
  `src/audian/eventoverlay.py:1284-1367`
  labeloverlay._SwatchEngine(QIconEngine) paints live from theme.MARKER_ICON_BG/RING/marker_color, so it follows a theme switch as soon as it repaints (labeloverlay.py:800-824, swatch_icon at 826-828). eventoverlay._legend_pixmap/legend_icon (1284-1313), _span_pixmap/span_icon (1316-1345), swatch_pixmap/swatch_icon (1348-1367) all bake a QPixmap from theme.pen/theme.brush — DataBrowser.apply_theme therefore re-runs build_annotation_chips() and build_category_chips(). databrowser.colormap_icon (623-638) bakes a gradient over theme.qcolor('bg.raised') with a theme.pen('border') frame — re-run via self.cmapw.populate().
- **There IS a single global QSS applied to the QApplication: a 406-line string.Template assigned to _QSS.**
  `src/audian/theme.py:2034-2441`
  Sections, in order: base QWidget colour only (no blanket background, deliberately), QMainWindow/QDialog/QScrollArea grounds, QLabel, toolbars incl. QToolBar#audian_toolbar, buttons incl. QToolButton#railToggle and QToolButton#paramTab, menus (QMenuBar/QMenu/QToolTip), tabs (QTabWidget/QTabBar, left-side spine), status bar, inputs (QLineEdit/QAbstractSpinBox/QComboBox/QPlainTextEdit/QTextEdit + QCheckBox/QRadioButton/QSlider), item views (QTableView/QTreeView/QListView/QHeaderView), splitters, scrollbars, and a focus-ring section.
- **stylesheet() interpolates the template with 35 substitution values and caches the result under 'stylesheet:{theme}'.**
  `src/audian/theme.py:2443-2493`
  Colour keys: bg_base, bg_surface, bg_raised, bg_plot, border, border_hi, fg, fg_muted, fg_faint, primary, primary_dim, on_primary, edge, bg_lane, accent, success, danger. Metric keys: toolbar_button_height, s2/s4/s6/s8/s12/s16/s24, s4_focus/s6_focus/s8_focus (each S minus (FOCUS_WIDTH - HAIRLINE)), hairline, focus_width, radius_control, radius_overlay, toolbar_height, control_height, scrollbar=10. Five of the passed values are never referenced in the template: bg_plot, bg_lane, accent, success, danger (verified programmatically).
- **palette() builds a fully populated QPalette from tokens, including an explicit Disabled colour group, cached under 'palette:{theme}' and returned as a copy.**
  `src/audian/theme.py:1979-2031`
  Active-group roles: Window=bg.base, WindowText=fg, Base=bg.surface, AlternateBase=bg.raised, Text=fg, Button=bg.surface, ButtonText=fg, BrightText=danger, ToolTipBase=bg.raised, ToolTipText=fg, PlaceholderText=fg.faint, Highlight=primary, HighlightedText=bg.base, Link=primary, LinkVisited=primary.dim, Light=border.hi, Midlight=border, Mid=border, Dark=border.hi, Shadow=bg.base. Disabled group: WindowText/Text/ButtonText=fg.faint, Highlight=primary.dim, HighlightedText=fg.muted, Base/Button/Window=bg.base. Docstring reason: 'Fusion derives disabled colours badly from a dark base if you leave it to guess'.
- **apply(app, theme_name) is the single entry point and forces the Fusion style.**
  `src/audian/theme.py:2509-2534`
  Order, documented as load-bearing: (1) QStyleFactory.create('Fusion') then app.setStyle(style) — 'the only cross-platform style that honours a custom QPalette under the Wayland platform theme'; (2) app.setPalette(palette()); (3) app.setFont(font_ui()); (4) apply_pg_config(); (5) app.setStyleSheet(stylesheet()) last so it wins over the palette.
- **apply_pg_config() pushes pyqtgraph globals: background='bg.plot' token value, foreground='fg.muted' token value, antialias=False.**
  `src/audian/theme.py:2496-2506`
  antialias=False is documented as a 170x paint cost on dense polylines, not a cosmetic choice.
- **theme.apply() has exactly two call sites: startup and the live switch.**
  `src/audian/audian.py:5105-5108`
  audian.py:5108 in audian_cli, right after QApplication is constructed, with theme_name = args.theme or settings().get('theme', theme.THEME_DARK), clamped to dark/light (audian.py:5105-5108). audian.py:1661 inside Audian.set_app_theme.
- **setStyle is called in one other place: a QProxyStyle installed on the menu bar only, unrelated to colour.**
  `src/audian/audian.py:489-511`
  MnemonicStyle(QProxyStyle) overrides SH_UnderlineShortcut so Alt-mnemonic underlines show only while Alt is held (audian.py:489-511); installed at audian.py:1798-1799.
- **tint(widget, token_name) sets an inline 'color:' stylesheet AND records the token name in the FG_PROPERTY dynamic property, because Qt stylesheets bake the colour string at set time.**
  `src/audian/theme.py:1441-1460`
- **frame(widget) sets objectName 'audianGroup', the FRAME_PROPERTY flag and a hairline border stylesheet; band(widget, top, bottom, ground) sets BAND_PROPERTY to a '<top><bottom>|<ground>' string plus a background + border-top/bottom rule.**
  `src/audian/theme.py:1462-1505`
- **restyle_tree(root) is the ONLY generic re-styler: it walks [root] + root.findChildren(QWidget) and re-applies tint/frame/band for widgets carrying FG_PROPERTY, FRAME_PROPERTY or BAND_PROPERTY, in that priority order (elif chain — a widget can only be fixed by one of the three), swallowing RuntimeError, and returns the count changed.**
  `src/audian/theme.py:1507-1541`
  Anything styled with a raw setStyleSheet and no property is invisible to it. Two call sites: DataBrowser.apply_theme (databrowser.py:2776) and Audian.restyle_chrome (audian.py:2256).
- **tint/frame/band are used at only 9 call sites in the whole application.**
  `src/audian/databrowser.py:212-232`
  theme.frame via databrowser.frame_widget (databrowser.py:212-214); theme.tint at databrowser.py:232 (caption_label), 2513, 4527, 5512, 5601; theme.band at databrowser.py:2228 (parambar top edge), audian.py:1611 (stack, ground bg.base), audian.py:2304 (toolbar bottom edge).
- **Audian.set_app_theme is the live-switch orchestrator and enumerates exactly what theme.apply() does NOT reach.**
  `src/audian/audian.py:1648-1690`
  Guards name in (dark, light); gets QApplication.instance(); theme.apply(app, name); self.refresh_glyph_icons(); self.restyle_chrome(); for each browser browser.apply_theme(); then REBUILDS StartupPage wholesale (constructs a fresh StartupPage, insertWidget(0), removeWidget + deleteLater the old, reload(), restore current) because 'StartupPage bakes token values into per-widget stylesheets across several builders'; then self.repolish(); sets the daylight_mode action check state; save_setting('theme', name); status message. Docstring: 'the plots are a pyqtgraph graphics scene whose pens and brushes were resolved when each item was built, so without the walk below a switch leaves light menus wrapped around dark plots'.
- **Audian.repolish() unpolishes and re-polishes every widget and invalidates+activates every layout, because a stylesheet re-apply repaints but does not re-run size calculations.**
  `src/audian/audian.py:2212-2236`
  Measured symptom recorded in the docstring: transport buttons came out 37x21 after a switch versus 37x32 when the theme was set before the window was built.
- **Audian.restyle_chrome() hand-patches the main window's own inline stylesheets after restyle_tree.**
  `src/audian/audian.py:2247-2276`
  theme.restyle_tree(self); refresh_readouts() (status readouts are rich text with the colour in the markup, so they must be re-pushed through set_readout — audian.py:2237-2245); self.tabs.tabBar().update(); re-sets every entry of self._toolbar_separators to 'background: <border>'; re-sets mode_chip via chip_style(...); re-sets message_label and progress_label to fg.muted.
- **DataBrowser.apply_theme() is the per-browser re-style walk and is explicit about every class of object that does not follow a token change on its own.**
  `src/audian/databrowser.py:2744-2806`
  style_channel_figure on every self.figs; style_figure on taxis_fig; taxis.apply_theme() + align_time_axis(); for every ax in self.axs call apply_theme() else polish(); border.setPen(theme.border_pen(selected=True)) for self.borders; splitter.polish() for self.splitters; datafig.apply_theme()/polish(); theme.restyle_tree(self); row.update_state() for self.rail_rows; cmapw.populate() and set_color_map(...) because the colormap is cached per theme and oriented to the page; overlay.polish() for annotation_overlays; polish_join_markers(); control_panel.polish(); build_annotation_chips(); update_annotation_badge(); redraw_annotations(); overlay.polish() for label_overlays; param_tabs.polish(); build_category_chips(); update_label_status(); update_current_plot() (because style_plotitem has just reset every viewbox to bg.plot).
- **The pyqtgraph appliers are the idempotent primitives every polish() body reduces to.**
  `src/audian/theme.py:1543-1647`
  style_axis (1543-1577): setStyle(maxTextLevel=0), axis pen no_pen() or border, tick pen fg.faint, text pen fg.muted, tick font font_mono(SIZE_SMALL_PT), and re-setLabel with the same text/units in fg.muted. style_plotitem (1579-1604): vb.setBackgroundColor(bg.plot) + style_axis on left/right/top/bottom that exist. style_figure (1606-1625): glw.setBackground(bg.base) — never None — plus S4 margins and zero spacings. style_channel_figure (1627-1647): style_figure with zero vertical margin. style_spinbox (1668-1692), style_colorbar (1694-1722), colorbar_pens (1724-1737, must be passed to the ColorBarItem CONSTRUCTOR because pyqtgraph never re-reads them), colorbar_ticks (1739-1755), overlay_textitem (1944-1976).
- **collect_orphan_widgets() has nothing to do with theming: it is a Wayland/memory hygiene sweep that adopts pyqtgraph's unreachable 640x480 Ui_Form widgets onto a single hidden holder.**
  `src/audian/theme.py:1912-1942`
  Iterates QApplication.topLevelWidgets() and adopts a widget only if type(widget) is exactly QWidget, it is not the holder, it has no parent, is not visible, has no objectName, no children and no layout — a deliberately narrow signature so an app's own hidden top-level widget is never swept. Returns the count adopted. Companion machinery: _MENU_HOLDER/_menu_holder (1757-1773), strip_pg_menus (1776-1873, releases plot_item.ctrl QWidgetActions onto the holder before deleting menus — the action owns the widget, so the ACTION must be adopted, not the widget), _adopt_ctrl_widgets (1876-1909). One call site: databrowser.py:1983.
- **Items that carry a resolved colour re-resolve it through a polish()/apply_theme() method; there are 21 such methods across the plot classes.**
  `src/audian/timeplot.py:137-179`
  timeaxisitem.py:44 (polish = apply_theme alias at :55), selectviewbox.py:54, fulltraceplot.py:140/231/612/657, databrowser.py:573/2744/4779, yaxisitem.py:38, timeplot.py:137, traceitem.py:151 (apply_theme = polish at :158), spectrogramplot.py:82/231, rangeplot.py:89/105, panelsplitter.py:80, labeloverlay.py:509, eventoverlay.py:1008, controlpanel.py:354.
- **TraceItem caches its pen by a key that INCLUDES theme.current_theme(), and polish() clears that key so a theme switch actually re-pens.**
  `src/audian/traceitem.py:126-158`
  self._pen_key = (effective_role, selected, dense, thick, theme.current_theme()); apply_pen returns early when the key is unchanged; polish() sets _pen_key = None first. TimePlot._style_traces(retheme=True) exists for the same reason: set_selected/set_dense no-op when the flag is unchanged, which makes them useless for a theme switch where the flags are identical but the colours are not (timeplot.py:160-179).
- **VerticalTabBar paints its own labels and re-resolves tokens at paint time, so it follows a theme switch on a plain update().**
  `src/audian/audian.py:411-444`
  painter.setPen(theme.qcolor('fg' if current else 'fg.muted')) inside _paint_label; restyle_chrome triggers it with self.tabs.tabBar().update() (audian.py:2255).
- **There are 30 setStyleSheet call sites in audian.py, 3 in databrowser.py and 4 in theme.py; the audian.py ones divide into rebuilt, hand-patched, and missed.**
  `src/audian/audian.py:1904-1917`
  REBUILT wholesale (StartupPage): 784, 795, 816, 834, 959, 995, 1002, 1008, 1025, 1059, 1063, 1073, 1093, 1112, 1359, 1376, 1390, 1394, 1444. HAND-PATCHED by restyle_chrome/refresh_readouts: 1840+2107 (message_label), 1936 (progress_label), 1954+2125+2266 (mode_chip), 2204+2261 (toolbar separators), 2275 (the restyle_chrome loop). THEME-INDEPENDENT: 521 (make_transparent, 'background: transparent'). MISSED: 1908 (self.error_button, which bakes theme.token('danger') twice) — grep of every error_button reference (1904-1917, 2113-2115, 2182) shows nothing ever re-applies its stylesheet, so after a theme switch it keeps the other theme's danger red.
- **databrowser's three inline stylesheets are covered by targeted re-runs, not by restyle_tree.**
  `src/audian/databrowser.py:5974-5981`
  888/896 = RailCard.update_state's current/not-current rules, re-run by the 'for row in self.rail_rows: row.update_state()' line of apply_theme; 5974 = the annotation badge, re-run by update_annotation_badge().
- **Persistence today: a JSON file in the platformdirs user config dir plus a separate QSettings store for the colormap.**
  `src/audian/audian.py:912-944`
  settings_path() = audian_dirs.user_config_path / 'settings.json' (audian.py:912-918, docstring: 'Config rather than cache: a wiped cache must cost the user nothing but recomputation, and a theme choice is not recomputable'); settings() reads it, never raises (921-932); save_setting(key, value) rewrites the WHOLE file per call (935-944). Theme is written at audian.py:1684 and read at audian.py:5105. The spectrogram colormap index uses a DIFFERENT mechanism: QSettings('audian','audian').value('spectrogram/colormap', theme.DEFAULT_SPECTROGRAM_MAP) in DataBrowser.read_color_map_setting (databrowser.py:1462-1475).
- **Theme-dependent data palettes that a settings layer must round-trip carefully: the colormap lists differ per theme and are not index-compatible.**
  `src/audian/theme.py:2544-2665`
  SPECTROGRAM_MAPS has 8 entries (2544-2558), SPECTROGRAM_MAPS_LIGHT has 5 (2582-2588); spectrogram_maps()/spectrogram_map_labels() switch on current_theme() (2617-2629); DEFAULT_SPECTROGRAM_MAP = 0 (2632); REVERSED_MAPS says which names are drawn reversed per theme (2597-2604); spectrogram_colormap clamps out-of-range indices and falls back on an unknown name rather than raising, and caches under 'cmap:{theme}:{name}' (2635-2665).
- **Theme-aware behaviour that is not colour: the graphic-contrast floor and the dim clamp change with the theme.**
  `src/audian/theme.py:1083-1153`
  min_graphic_contrast() returns MIN_GRAPHIC_CONTRAST_DAYLIGHT (4.5) under light and MIN_GRAPHIC_CONTRAST (3.0) under dark (1083-1087); dim_color resolves the floor per call precisely 'because the floor is a property of the active theme and the theme changes at runtime', and its cache key embeds current_theme() (1124-1153). painted_trace_colors() temporarily set_theme()s and restores, 'because the dimming path deliberately reads global state rather than taking a theme argument at 48 plots per repaint' (3105-3126).
- **tests/test_theme.py enforces the design-system invariants that any refactor has to keep passing.**
  `tests/test_theme.py:153-287`
  46 tests. Notably: test_tokens_table_matches_constants asserts DARK_TOKENS['bg.plot']==BG_PLOT, ['primary']==PRIMARY, ['trace.raw']==TRACE_RAW and GRID_COLOR==TRACE_ZERO (153-159); test_contrast_ratio_reference_values pins FG/BG_PLOT at 15.93, FG_MUTED 7.69, PRIMARY 5.87, ACCENT 9.25 (44-50); test_stylesheet_has_no_raw_colour_literals_beyond_tokens and the assertion f'{theme.FOCUS_WIDTH}px solid {theme.PRIMARY}' in qss (198-208); test_apply_twice_under_offscreen asserts theme.PRIMARY is in app.styleSheet() (92-100); and the grep guards test_no_hex_literals_outside_theme / test_no_named_qt_colours_outside_theme / test_no_setbackground_none_outside_theme, which scan every src/audian/*.py except theme.py line by line (222-276, gated by a STRICT flag). test_theme_module_is_lint_clean runs ruff on theme.py (279-287).

### Risks

- Feature A (follow the system theme) collides head-on with theme.py's core assumption that TOKENS is a closed 23-key table of literal hexes with MEASURED contrast ratios. Contrast values are asserted as constants in tests (tests/test_theme.py:44-50), baked into docstrings as rulings (theme.py:37-56), and gate dim_color, annotation_letter, check_contrast and check_separation. A palette taken from QPalette at runtime cannot satisfy those pinned numbers, so check_contrast/check_separation would have to become advisory (or the token table would have to stay authoritative and only be *seeded* from the system).
- theme.apply() unconditionally forces QStyleFactory.create('Fusion') (theme.py:2528-2530). Following the native style means NOT setting Fusion — but the QPalette-heavy design and the 406-line QSS were both written against Fusion's box model, and the docstring records Fusion as 'the only cross-platform style that honours a custom QPalette under the Wayland platform theme'. Dropping Fusion changes toolbar/button/spinbox metrics, which the code already fights (TOOLBAR_BUTTON_BOX/TOOLBAR_BUTTON_HEIGHT comments at theme.py:1038-1062 and the QToolBar#audian_toolbar rule at theme.py:2073-2083 both exist because a re-applied stylesheet re-laid the bar and clipped its buttons).
- app.setFont(font_ui()) at theme.py:2532 overwrites the platform font unconditionally. Using the system font means QFont() / QApplication.font() instead — but SIZE_PT=10 and SIZE_SMALL_PT=9 are hardcoded and 75 call sites outside theme.py pass explicit sizes (theme.font_ui(theme.SIZE_SMALL_PT) etc.), and dozens of layout constants (CONTROL_HEIGHT, CHIP_HEIGHT, RAIL_NUMBER_HEIGHT, TOOLBAR_BUTTON_HEIGHT, AXIS_LEFT_WIDTH, and the colorbar's mono_metrics().horizontalAdvance sizing at theme.py:1691) are pinned pixel values chosen for a 9-10pt face. A larger system font will overflow them.
- The _CACHE font keys ('families', 'family:…', 'font:…', 'metrics:…') are NOT invalidated by set_theme (theme.py:442-444). If fonts ever become system-derived, a system font change (or a theme change that carries a font) would serve stale QFont/QFontMetrics objects until process restart. A font-cache invalidation hook does not exist today.
- _BY_VALUE loses the 'fg' key to 'on.primary' because both are #E6EDF6 in DARK_TOKENS (theme.py:412 + 322-325). theme._resolve(theme.FG) already returns #FFFFFF under the light theme instead of #000000. Any new token whose dark value duplicates an existing one silently re-points every raw-constant call site — the module warns about this at theme.py:1298-1303 but the fg/on.primary case is live. Adding system-derived tokens multiplies the chance of value collisions (system palettes routinely repeat #FFFFFF and #000000 across roles), which would make _resolve actively wrong.
- restyle_tree only reaches widgets carrying FG_PROPERTY / FRAME_PROPERTY / BAND_PROPERTY, and only 9 call sites in the whole app use tint/frame/band. Every other inline setStyleSheet with a baked token (30 in audian.py, 3 in databrowser.py) is covered by a hand-written re-apply, by a wholesale widget rebuild (StartupPage), or not at all. audian.py:1908 (error_button, bakes theme.token('danger')) is a confirmed uncovered site. A system theme that can change while the app runs (a desktop switching light/dark at sunset) would exercise this path far more often than the current manual Ctrl+Shift+L, exposing every remaining gap.
- restyle_tree's property handling is an if/elif chain (theme.py:1517-1538): a widget can be tinted OR framed OR banded, never two of them. A widget needing both a text colour and a band ground silently gets only the first.
- Nothing in the app observes system theme changes today: there is no QStyleHints / colorScheme / palette-change handler anywhere in src/audian (grep for styleHints, colorScheme, standardPalette returns nothing but unrelated QStyle hits). Feature A needs a new signal path (QStyleHints.colorSchemeChanged and/or QEvent.ApplicationPaletteChange) wired into the existing set_app_theme walk, plus a way to express 'follow system' as a THIRD persisted theme value — audian.py:5105-5107 and audian.py:1655 both hard-reject anything that is not exactly 'dark' or 'light'.
- Icons will not follow a system theme for free. Every icon is a baked QPixmap built from tokens (audian.py glyph_icon, eventoverlay legend/span/swatch, databrowser colormap_icon); only labeloverlay._SwatchEngine (a QIconEngine) re-resolves at paint time. Any automatic system-theme switch must trigger refresh_glyph_icons() plus build_annotation_chips()/build_category_chips()/cmapw.populate(), or icons keep the previous theme's ink.
- Feature B (more settings in .config) has two competing stores that would drift: settings.json under audian_dirs.user_config_path (audian.py:912-944) and QSettings('audian','audian') for the colormap only (databrowser.py:1462-1475). save_setting rewrites the entire JSON file on every single key (audian.py:935-944), which databrowser.py:1002-1003 already calls out as the reason layer state is packed into one key rather than thirteen — so adding nfft, cmap, window function etc. as individual keys multiplies whole-file rewrites.
- A persisted spectrogram colormap cannot be stored as a bare index: SPECTROGRAM_MAPS has 8 entries and SPECTROGRAM_MAPS_LIGHT has 5 (theme.py:2544-2588), and spectrogram_maps() returns whichever matches the active theme. Index 6 saved under dark is out of range under light, and read_color_map_setting silently resets it to DEFAULT_SPECTROGRAM_MAP (databrowser.py:1471-1474), so a user's choice is lost across a theme switch. Persisting the map NAME (which spectrogram_colormap already accepts, theme.py:2645-2646) would be the safe change.
- The 'no colour literals outside theme.py' grep tests (tests/test_theme.py:222-276) scan every src/audian/*.py except theme.py for hex literals and named Qt colours. Any system-theme work that reads a QColor into another module, or any settings work that stores a hex string in a non-theme module, will trip them if STRICT is on.
- theme.py's docstring promises the module imports cleanly with NO QApplication (theme.py:57-64) and that all token constants are plain strings available at import time. Reading the system palette requires a live QGuiApplication, so any system-derived token must be lazy and memoised the way fonts are — and every existing module-level constant (BG_BASE … TRACE_ZERO, MARKER_ICON_BG/RING, GRID_COLOR) would have to become a function or stop being the reference values.

## theme-usage

The theme is a hand-rolled, process-global design system in `src/audian/theme.py` with exactly two named tables (`THEME_DARK="dark"` / `THEME_LIGHT="light"`, theme.py:284-285). Nothing in the codebase reads the OS colour scheme — `QStyleHints`/`colorScheme` appears nowhere in src/ or tests/ — and `theme.apply()` force-installs the Fusion style (theme.py:2528-2530) and a fully-populated custom `QPalette` (theme.py:1979-2032), overriding the platform theme by design.

CHOSEN: three inputs, in precedence order at audian.py:5105-5107 — `--theme {dark,light}` CLI flag (audian.py:5047-5053), else `settings().get("theme", theme.THEME_DARK)`, else dark; an unrecognised value is coerced to dark. At runtime the single user control is the checkable `View ▸ &Daylight mode` QAction bound to `Ctrl+Shift+L` (audian.py:4563-4570, added to the menu at 4590), which calls `Audian.toggle_daylight` (audian.py:1692-1698) → `Audian.set_app_theme` (audian.py:1648-1690). No settings dialog exists.

PERSISTED: key `"theme"` in `settings.json` (audian.py:912-918, `audian_dirs.user_config_path / "settings.json"`, platformdirs `PlatformDirs("audian","janscience")` at version.py:13 → `~/.config/audian/settings.json`). Read once at startup (audian.py:5105); written only from `set_app_theme` (audian.py:1684) via `save_setting` (audian.py:935-947), which is a read-modify-rewrite of the whole JSON file and never raises. There is a SECOND, separate store: `QSettings("audian","audian")` → `~/.config/audian/audian.conf`, holding `spectrogram/colormap` (databrowser.py:1465, 7421) — the exact gap feature (B) names.

STARTUP ORDER (audian.py:5103-5120): `QApplication(...)` → resolve theme name → `theme.apply(app, theme_name)` (which does `set_theme` → Fusion → palette → app font → `pg.setConfigOptions` → app stylesheet, theme.py:2509-2534) → `Audian(...)` builds every widget → `main.show()` → `app.exec()`. So the token table is final before any widget is constructed, and every widget/pen bakes its colours at build time.

LIVE SWITCH (audian.py:1648-1690, in order): `theme.apply(app, name)` → `refresh_glyph_icons()` (1662, rebuilds recorded `(widget, glyph kind)` pairs, audian.py:1700-1717) → `restyle_chrome()` (1663 → audian.py:2249-2275: `theme.restyle_tree(self)`, `refresh_readouts()`, tab bar update, toolbar separators, mode chip, message/progress labels) → `for browser in self.browsers: browser.apply_theme()` (1664-1666 → databrowser.py:2744-2806, a 30-step walk over figures, axes, borders, splitters, the full-trace figure, `restyle_tree`, rail rows, the colormap combo, `set_color_map`, annotation and label overlays, chips, control panel, param tabs) → StartupPage is destroyed and rebuilt wholesale (1670-1679) → `repolish()` (1682 → audian.py:2213-2237, unpolish/polish + `updateGeometry` + layout invalidate over every child) → action check state (1683) → `save_setting("theme", name)` (1684) → status message.

pyqtgraph theming: `apply_pg_config()` sets global `background=token('bg.plot')`, `foreground=token('fg.muted')`, `antialias=False` (theme.py:2496-2506); the appliers `style_axis` / `style_plotitem` / `style_figure` / `style_channel_figure` (theme.py:1543-1650) are called from timeaxisitem.py:15,51; yaxisitem.py:36,40; rangeplot.py:29,97; controlpanel.py:132,356-357; fulltraceplot.py:416,531,557,614,616,946; databrowser.py:1816,2142,2751,2755. `pg.setConfigOption("useNumba", True)` at databrowser.py:209 is unrelated to colour.

Fifteen modules under src/audian import `theme`: audian.py and databrowser.py (tokens, fonts, spacing, `band`/`tint`/`frame`/`restyle_tree`), the plot items (traceitem, timeplot, rangeplot, spectrogramplot, fulltraceplot, selectviewbox, timeaxisitem, yaxisitem — pens/brushes/appliers), the overlays (eventoverlay, labeloverlay, controlpanel — annotation colours, marker colours, chip geometry), the data classes (buffereddata, bufferedfilter, bufferedenvelope, data — `trace_color` + line widths), panels.py (colormap lookup only), panelsplitter.py (two pens + one geometry constant) and analyzer.py (fonts only).

### Facts

- **The two theme names are plain strings defined in theme.py and there is no third (no 'system'/'auto').**
  `src/audian/theme.py:284-285`
  THEME_DARK = "dark"; THEME_LIGHT = "light"; the THEMES dict maps them to DARK_TOKENS/LIGHT_TOKENS (theme.py:399-400).
- **Active theme is process-global mutable module state, not per-window.**
  `src/audian/theme.py:414, 420-422`
  _ACTIVE = {"name": THEME_DARK} and TOKENS: dict[str,str] = dict(DARK_TOKENS) (theme.py:405). current_theme() just returns _ACTIVE['name'].
- **set_theme() swaps the token table in place and clears ONLY the palette: and stylesheet: cache entries.**
  `src/audian/theme.py:425-444`
  Font, font-family, QFontMetrics, colormap and dim caches (keys 'families', 'family:*', 'font:*', 'metrics:*', 'cmap:*', 'dim:*') survive a theme switch. set_theme raises KeyError on an unknown name.
- **theme.apply() is the single entry point and hard-installs Fusion, overriding the platform style.**
  `src/audian/theme.py:2509-2534`
  Order: optional set_theme -> QStyleFactory.create('Fusion') + app.setStyle -> app.setPalette(palette()) -> app.setFont(font_ui()) -> apply_pg_config() -> app.setStyleSheet(stylesheet()). Docstring claims exactly one call site; there are in fact two in src (audian.py:5108 and 1661).
- **Startup order is: QApplication constructed -> theme resolved -> theme.apply() -> Audian() builds all widgets -> show() -> exec().**
  `src/audian/audian.py:5103-5121`
  app = QApplication(sys.argv[:1] + qt_args); theme_name = args.theme or settings().get("theme", theme.THEME_DARK); coerced to dark if unknown; theme.apply(app, theme_name); main = Audian(...); main.show(); app.exec().
- **The theme choice is persisted under the JSON key "theme" in settings.json.**
  `src/audian/audian.py:5105, 1684`
  Read at audian.py:5105 via settings().get("theme", theme.THEME_DARK); written only at audian.py:1684 via save_setting("theme", name) inside set_app_theme.
- **settings.json lives in the platformdirs user config dir; the docstring explicitly says config, not cache, because a theme choice is not recomputable.**
  `src/audian/audian.py:912-918`
  return audian_dirs.user_config_path / "settings.json"; audian_dirs = PlatformDirs("audian", "janscience") (version.py:13). On Linux this is ~/.config/audian/settings.json.
- **settings() never raises: a missing, unreadable or non-dict file reads as {}.**
  `src/audian/audian.py:921-932`
  try/except (OSError, ValueError), logs at debug level, returns {} on any failure or if the parsed value is not a dict.
- **save_setting() is a read-modify-rewrite of the entire JSON file and swallows OSError.**
  `src/audian/audian.py:935-947`
  values = settings(); values[key] = value; mkdir(parents=True, exist_ok=True); json.dump(values, df, indent=2). No locking, no atomic replace: two audian instances are last-writer-wins.
- **The user-facing theme control is a single checkable QAction, 'Daylight mode', on Ctrl+Shift+L, in the View menu.**
  `src/audian/audian.py:4563-4570`
  self.acts.daylight_mode = QAction("&Daylight mode", self); setCheckable(True); setChecked(theme.current_theme() == theme.THEME_LIGHT); setShortcut("Ctrl+Shift+L"); setToolTip("High-contrast light theme for reading the screen in direct sunlight"); triggered.connect(self.toggle_daylight). Added to the menu at audian.py:4590.
- **toggle_daylight flips the theme by reading the module-global current_theme().**
  `src/audian/audian.py:1692-1698`
  self.set_app_theme(theme.THEME_DARK if theme.current_theme() == theme.THEME_LIGHT else theme.THEME_LIGHT)
- **Audian.set_app_theme is the whole live-switch chain; it validates the name, bails out with no QApplication, and ends by persisting.**
  `src/audian/audian.py:1648-1690`
  1656-1657 name guard; 1658-1660 QApplication.instance() guard; 1661 theme.apply; 1662 refresh_glyph_icons; 1663 restyle_chrome; 1664-1666 per-browser apply_theme; 1670-1679 StartupPage rebuilt; 1682 repolish; 1683 daylight_mode.setChecked; 1684 save_setting; 1685-1690 status message.
- **QIcons do not follow a theme switch, so every glyph icon is registered in a list and rebuilt by hand.**
  `src/audian/audian.py:1700-1717`
  _glyph_targets = [] (audian.py:1508); _set_glyph records (target, kind) and sets glyph_icon(kind); refresh_glyph_icons re-sets every recorded icon, skipping RuntimeError for widgets whose C++ object died with a closed tab.
- **glyph_icon deliberately refuses QStyle standard icons because they never honour the app theme.**
  `src/audian/audian.py:301-328`
  Docstring: 'No emoji, no external assets, and above all no QStyle standard icon: those are pre-rendered pixmaps in the platform theme's own grey and never honour ours.' Icons are painted as QPainterPaths in four QIcon modes plus On states, coloured from tokens GLYPH_NORMAL='fg.muted', GLYPH_ACTIVE='fg', GLYPH_DISABLED='fg.faint', GLYPH_ON='on.primary' (audian.py:75-78).
- **restyle_chrome re-applies every inline stylesheet the main window owns after a switch.**
  `src/audian/audian.py:2249-2275`
  theme.restyle_tree(self); refresh_readouts(); self.tabs.tabBar().update(); each toolbar separator's background re-set from token('border'); mode_chip re-styled via chip_style(); message_label and progress_label re-coloured from 'fg.muted'.
- **Status-bar readouts are rich text with the colour written into the markup, so they must be re-pushed through set_readout, not restyled.**
  `src/audian/audian.py:2239-2247`
  refresh_readouts iterates self._readout_state (last (text, active) per field) and calls set_readout again.
- **repolish() exists because a stylesheet re-set repaints but does not re-run size calculations after a live switch.**
  `src/audian/audian.py:2213-2237`
  Docstring records the measured defect: transport buttons came out 37x21 after a switch versus 37x32 when the theme was set before the window was built. It unpolish/polish/updateGeometry over [self] + findChildren(QWidget) and then invalidate()+activate() every layout, tolerating RuntimeError.
- **StartupPage cannot be restyled and is destroyed and rebuilt on every theme switch.**
  `src/audian/audian.py:1667-1679`
  Comment: 'StartupPage bakes token values into per-widget stylesheets across several builders; rebuilding it is exact, where chasing each one would silently miss whichever gets added next.' A fresh StartupPage(self) is inserted at index 0, the old one removed and deleteLater'd, reload() called, and current-widget state preserved.
- **theme.restyle_tree only reaches widgets tagged by tint()/frame()/band(); everything else styled with a raw setStyleSheet is invisible to it.**
  `src/audian/theme.py:1507-1541`
  It walks [root] + root.findChildren(QWidget) and re-applies whichever of FG_PROPERTY ('audianFgToken'), FRAME_PROPERTY ('audianFramed') or BAND_PROPERTY ('audianBandEdge') the widget carries; returns a count. Called from audian.py:2256 and databrowser.py:2776.
- **There are 37 setStyleSheet call sites in src/audian: 30 in audian.py, 4 in theme.py, 3 in databrowser.py.**
  `src/audian/audian.py:521-2275`
  audian.py: 521, 784, 795, 816, 834, 959, 995, 1002, 1008, 1025, 1059, 1063, 1073, 1093, 1112, 1359, 1376, 1390, 1394, 1444, 1840, 1908, 1936, 1954, 2107, 2125, 2204, 2261, 2266, 2275. databrowser.py: 888, 896 (ChannelRailRow.update_state), 5974 (annotation badge).
- **DataBrowser.apply_theme is the single per-browser live-restyle entry point and is a 30-step hand-written walk.**
  `src/audian/databrowser.py:2744-2806`
  style_channel_figure per figure; style_figure(taxis_fig); taxis.apply_theme() + align_time_axis(); every ax.apply_theme() else ax.polish(); border.setPen(theme.border_pen(selected=True)); splitter.polish(); datafig.apply_theme()/polish(); theme.restyle_tree(self); rail_rows row.update_state(); cmapw.populate(); set_color_map(self.color_map, dispatch=False); annotation_overlays polish(); polish_join_markers(); control_panel.polish(); build_annotation_chips(); update_annotation_badge(); redraw_annotations(); label_overlays polish(); param_tabs.polish(); build_category_chips(); update_label_status(); update_current_plot().
- **A theme switch re-writes the spectrogram colormap to QSettings as a side effect.**
  `src/audian/databrowser.py:2785, 7421`
  DataBrowser.apply_theme calls self.set_color_map(self.color_map, dispatch=False) at databrowser.py:2785, and set_color_map unconditionally does QSettings("audian","audian").setValue("spectrogram/colormap", self.color_map) at databrowser.py:7421.
- **The two themes offer different colormap lists of different lengths, so the persisted colormap INDEX means a different map in each theme.**
  `src/audian/theme.py:2617-2629, 2632`
  SPECTROGRAM_MAPS has 8 entries (theme.py:2549-2566), SPECTROGRAM_MAPS_LIGHT has 5 (theme.py:2571-2582); spectrogram_maps()/spectrogram_map_labels() branch on current_theme(); set_color_map clamps an out-of-range index to DEFAULT_SPECTROGRAM_MAP=0.
- **ColorMapCombo must be fully rebuilt (not just repainted) on a theme switch because the map lists differ.**
  `src/audian/databrowser.py:651-670`
  populate() blocks signals, clears, re-adds colormap_icon(i) + theme.spectrogram_map_labels(), restores the index only if still in range; `refresh_swatches = populate` is kept as the name the theme switch calls.
- **The spectrogram colormap is the one preference persisted through QSettings rather than settings.json.**
  `src/audian/databrowser.py:1437, 1462-1474`
  read_color_map_setting() is a @staticmethod reading QSettings("audian","audian").value("spectrogram/colormap", theme.DEFAULT_SPECTROGRAM_MAP), coercing TypeError/ValueError and clamping against len(theme.spectrogram_maps()); called once at DataBrowser.__init__ (databrowser.py:1437).
- **Everything else persisted goes through settings.json under one versioned key per feature.**
  `src/audian/databrowser.py:1004-1090`
  ANNOTATION_SETTING='annotations' (v1), LABEL_SETTING='labels' (v1), PARAM_TAB_SETTING='parameter-tab' (v2), PANEL_SPLIT_SETTING='panel-split' (v3), SPEC_BAND_SETTING='spectrogram-band' (v2). The comment at databrowser.py:1002-1004 states the reason: save_setting rewrites the whole file, so thirteen keys would be thirteen rewrites.
- **The five settings.json writers all import save_setting lazily from .audian inside the method, to avoid a circular import.**
  `src/audian/databrowser.py:3716, 5248, 5777, 6854, 6883`
  save_label_settings (3708-3730), save_annotation_settings (5233-5262), the parameter-tab writer (5770-5790), save_spec_band (6841-6863) and save_panel_split (6865-6890) each do `from .audian import save_setting` in the body.
- **Settings are read fresh from disk at each DataBrowser construction; there is no in-process settings cache.**
  `src/audian/databrowser.py:3683, 5168, 5715, 6747, 6794`
  settings() is called at databrowser.py:3683 (labels), 5168 (annotations), 5715 (parameter tab), 6747 (panel split), 6794 (spectrogram band); each call re-opens and re-parses settings.json.
- **Persisted settings are written per-gesture and guarded against churn, a rule any new setting must follow.**
  `src/audian/databrowser.py:6841-6890`
  save_panel_split docstring: 'Once per gesture, never per mouse move. save_setting reads, updates and rewrites the whole settings file, and one drag is a hundred mouse moves.' save_spec_band short-circuits when (min_hz, max_hz) == self._spec_band_saved.
- **Both persisted-value shapes carry a rule that a value still on its default is written as null rather than as this session's incidental number.**
  `src/audian/databrowser.py:6845-6851, 6870-6884`
  Panel split: 'A split still on its default is written as null... freezing this window's answer into the settings file would open the next stack on a split nobody chose.' Spectrogram band: 'An end still sitting at its limit is written as null, not as this recording's number: an 8 kHz recording writing 4000 would cap every 96 kHz recording opened afterwards.'
- **pyqtgraph global config is set from tokens and only affects items constructed afterwards.**
  `src/audian/theme.py:2496-2506`
  apply_pg_config() calls pg.setConfigOptions(background=token('bg.plot'), foreground=token('fg.muted'), antialias=False). It is invoked only from theme.apply (theme.py:2533). Live items must be walked by the style_* appliers instead.
- **The four pyqtgraph appliers are style_axis, style_plotitem, style_figure and style_channel_figure.**
  `src/audian/theme.py:1543-1650`
  style_axis (1543) themes tick/text/label pens and re-sets the label with token('fg.muted'); style_plotitem (1579) sets the view box background to qcolor('bg.plot') and styles every present axis; style_figure (1606) sets glw background to qcolor('bg.base') -- never None -- and S4 margins with zero spacing; style_channel_figure (1627) is style_figure with zero vertical margins for a channel lane.
- **style_axis / style_plotitem / style_figure call sites outside theme.py, exhaustively.**
  `src/audian/fulltraceplot.py:416-946`
  timeaxisitem.py:15,51; yaxisitem.py:36,40; rangeplot.py:29,97; controlpanel.py:132,356,357; fulltraceplot.py:416,531,557,614,616,946; databrowser.py:1816,2142,2751,2755; theme.py:1603,1642,1711 (internal). Tests exercise them at tests/test_theme.py:113-116.
- **pg.setConfigOption('useNumba', True) at import time in databrowser.py is unrelated to theming.**
  `src/audian/databrowser.py:209`
  Module-level, alongside the other pyqtgraph imports; the only other setConfigOption in the tree is tests/conftest.py:95 setting mouseRateLimit to 0.
- **The plot-item restyle protocol is a duck-typed pair of names: apply_theme, falling back to polish.**
  `src/audian/databrowser.py:2762-2766`
  DataBrowser.apply_theme does `if hasattr(ax, 'apply_theme'): ax.apply_theme() elif hasattr(ax, 'polish'): ax.polish()` (databrowser.py:2762-2766); TimePlot._style_traces does `restyle = getattr(item, 'apply_theme', None) or getattr(item, 'polish', None)` (timeplot.py:174-178). timeaxisitem.py:55 sets `polish = apply_theme` and traceitem.py:158 sets `apply_theme = polish`, aliasing both directions.
- **Every polish()/apply_theme() definition outside theme.py, exhaustively.**
  `src/audian/timeplot.py:137-143`
  controlpanel.py:354; databrowser.py:573 (ParameterTabs strip metrics), 2744 (DataBrowser), 4779 (polish_join_markers); labeloverlay.py:509; panelsplitter.py:80; selectviewbox.py:54; eventoverlay.py:1008; fulltraceplot.py:140, 231, 612, 657; spectrogramplot.py:82, 231; timeplot.py:137; rangeplot.py:89 and 105 (apply_theme is a bare alias of polish); yaxisitem.py:38; timeaxisitem.py:44; traceitem.py:151.
- **TraceItem forces a pen re-resolve on a theme switch by putting current_theme() in its pen cache key.**
  `src/audian/traceitem.py:129-137`
  key = (self.effective_role(), self.selected, self.dense, bool(thick), theme.current_theme()); an unchanged key skips setPen because setPen invalidates the item and schedules a repaint.
- **TimePlot._style_traces takes a retheme flag precisely because set_selected/set_dense no-op when the flag is unchanged but the colours behind it are not.**
  `src/audian/timeplot.py:159-178, 143`
  Docstring: 'That makes them useless for a theme switch, where the flags are identical but the colours behind them are not, so retheme forces each item to re-resolve its pen.' TimePlot.polish calls _style_traces(retheme=True).
- **EventOverlay keys a per-theme alpha table off the theme constants and reads it at paint time.**
  `src/audian/eventoverlay.py:173-176, 676-681`
  SPAN_FILL_ALPHA = {theme.THEME_DARK: 0.10, theme.THEME_LIGHT: 0.05}; fill_alpha() does base = SPAN_FILL_ALPHA[theme.current_theme()], so an unknown third theme name would KeyError here.
- **Labels and layers store a colour INDEX or role NAME, never a resolved hex, explicitly so the colour follows a live theme switch.**
  `src/audian/labels.py:158-159`
  labels.py:158-159: 'Index into theme.marker_color, which wraps modulo eight. An index rather than a hex value so the colour follows a live theme switch.' layers.py:126 records the same for the name theme.annotation_color resolves.
- **Fifteen modules in src/audian import theme; audian.py and databrowser.py dominate the usage.**
  `src/audian/audian.py:34`
  Imports: selectviewbox.py:7, timeaxisitem.py:8, eventoverlay.py:77, bufferedenvelope.py:7, analyzer.py:13, yaxisitem.py:9, bufferedfilter.py:7, panels.py:10, spectrogramplot.py:9, audian.py:34, timeplot.py:8, databrowser.py:42, traceitem.py:8, controlpanel.py:69, labeloverlay.py:127, buffereddata.py:8, data.py:14, rangeplot.py:5, panelsplitter.py:31, fulltraceplot.py.
- **audian.py's theme use is 78 spacing constants, 34 token() reads, 25 SIZE_SMALL_PT, 20 font_ui, 11 font_mono, plus 7 THEME_LIGHT / 5 THEME_DARK and 3 theme.apply references.**
  `src/audian/audian.py:34`
  Also: HAIRLINE x9, brush x7, TOOLBAR_HEIGHT/TOOLBAR_BUTTON_BOX/RADIUS_CONTROL x4 each, pen x4, RADIUS_OVERLAY x3, mono_metrics/current_theme/band x2, and one each of ui_metrics, stylesheet, restyle_tree, qcolor, MOTION_MS, LW_THIN, LW_THICK, LW_CLOSE.
- **databrowser.py's theme use is 44 spacing constants, 36 SIZE_SMALL_PT, 15 font_ui, 14 font_mono, 12 CHIP_HEIGHT and only 9 raw token() reads.**
  `src/audian/databrowser.py:42`
  Geometry-heavy: SPECTROGRAM_MIN_HEIGHT x10, PLOT_FRAME_HEIGHT x6, CHANNEL_DENSE_HEIGHT x6, CHANNEL_MIN_HEIGHT x4, AXIS_LEFT_WIDTH x4. Theming: tint x5, spectrogram_maps x5, style_channel_figure x4, DEFAULT_SPECTROGRAM_MAP x4, style_figure x2, join_pen x2, border_pen x2, plus restyle_tree, frame, band, style_spinbox, collect_orphan_widgets, marker_color, spectrogram_colormap once each. Its two `theme.set_theme` / `theme.apply` hits are comments only (databrowser.py:1460, 2747).
- **panels.py touches theme only for colormap lookup; analyzer.py only for fonts; panelsplitter.py only two pens plus one geometry constant.**
  `src/audian/panels.py:28-36`
  panels.py:28,36 call theme.spectrogram_colormap; analyzer.py:21-28 set font_mono/font_ui on a table and its headers; panelsplitter.py:75-83 keep _rest_pen = theme.border_pen() and _active_pen = theme.handle_pen(), re-resolved in polish(), plus theme.PANEL_SPLIT_HANDLE_HEIGHT at :111.
- **The data-layer modules bake a resolved colour at construction time and are never re-themed.**
  `src/audian/buffereddata.py:76-78`
  buffereddata.py:76-78 self.color = theme.trace_color(name) plus LW_THIN/LW_THICK; bufferedfilter.py:32 theme.trace_color('filtered'); bufferedenvelope.py:28 theme.trace_color('envelope'); data.py:395-402 sets self.data.color = theme.trace_color('raw'). TraceItem overrides this with a role-based pen when self.role is set (traceitem.py:51).
- **The QPalette is fully populated including the Disabled group, deliberately, because Fusion derives disabled colours badly from a dark base.**
  `src/audian/theme.py:1979-2032`
  19 ColorRole entries plus 8 Disabled overrides, all resolved from tokens and cached under key f'palette:{current_theme()}'.
- **The application stylesheet is a string.Template interpolated from ~30 token and geometry values, with no colour literal, and is cached per theme.**
  `src/audian/theme.py:2408-2492`
  key = f'stylesheet:{current_theme()}'; substitutes bg_base, bg_surface, bg_raised, bg_plot, border, border_hi, fg, fg_muted, fg_faint, primary, primary_dim, on_primary, edge, bg_lane, accent, success, danger plus S2..S24, HAIRLINE, FOCUS_WIDTH, RADIUS_CONTROL, RADIUS_OVERLAY, TOOLBAR_HEIGHT, CONTROL_HEIGHT.
- **Fonts are chosen from hard-coded family stacks, not from the system UI font, and are cached without a theme component.**
  `src/audian/theme.py:649-717`
  FONT_UI_FAMILIES = ('Inter','Adwaita Sans','Noto Sans','DejaVu Sans','sans-serif'); FONT_MONO_FAMILIES = ('JetBrainsMono Nerd Font','JetBrains Mono Nerd Font','Adwaita Mono','DejaVu Sans Mono','monospace'); SIZE_PT=10, SIZE_SMALL_PT=9. _font caches under f"font:{'mono'|'ui'}:{pt}:{bold}" and _installed_families caches under 'families'.
- **Nothing in src/ or tests/ reads the OS colour scheme or system palette.**
  `src/audian/theme.py:2528-2530`
  grep for styleHints / colorScheme / ColorScheme across src and tests returns zero hits; the only QStyleFactory use is the Fusion install in theme.apply. Every setStyle( hit in src is either QPen/QBrush style or pg.AxisItem.setStyle.
- **The command-line flag is --theme with choices dark/light and default None, so 'not given' is distinguishable from an explicit choice.**
  `src/audian/audian.py:5047-5053`
  help text: "colour theme; 'light' is the high-contrast daylight theme for outdoor use (default: whatever was last chosen, else dark)". args.theme wins over the persisted value for that run and is NOT written back to settings.json.
- **Dialogs are parented to the main window, so restyle_tree and repolish reach them; they are tracked by attribute name.**
  `src/audian/audian.py:2690-2712`
  CommandPalette (audian.py:1160), CheatSheet (1234), ShortcutsDialog (1409) are QDialogs constructed with self as parent and held via track_dialog/close_dialog (audian.py:2690-2704), so they appear in self.findChildren(QWidget).
- **The smoke-test harness redirects BOTH persistence channels, and documents that redirecting only settings_path is how it once clobbered the user's real preferences.**
  `scripts/smoke_test.py:237-265`
  redirect_persistence replaces audian.audian.settings_path with a lambda and calls QSettings.setDefaultFormat + QSettings.setPath for both formats and both scopes; QSettings.setPath only affects objects constructed afterwards, so it must run before the app is built.
- **The smoke harness can drive a theme switch from the command line and asserts the switch actually took.**
  `scripts/smoke_test.py:424-430`
  if args.theme: main_win.set_app_theme(args.theme); pump(app, 2.0); if _theme.current_theme() != args.theme: faults.append(...).
- **tests/test_smoketest.py is the guard that both stores stay isolated, and names the colormap key explicitly.**
  `tests/test_smoketest.py:60-86`
  test_the_smoke_harness_redirects_the_qsettings_store_as_well; test_a_setting_written_after_the_redirect_lands_in_the_scratch_directory writes save_setting('theme','light') and QSettings.setValue('spectrogram/colormap', 3) and asserts both settings.json and audian.conf land in tmp_path.
- **The action-inventory sweep triggers daylight_mode and restores the theme afterwards, because the theme is process-wide state shared with other test modules.**
  `tests/test_actioninventory.py:212-251`
  theme_before = theme.current_theme(); ... if theme.current_theme() != theme_before: window.set_app_theme(theme_before); assert theme.current_theme() == theme_before, 'the sweep left the theme switched'. It also asserts fired >= 100 against an inventory of 124 actions.
- **A lint test forbids colour literals anywhere in src/audian outside theme.py.**
  `tests/test_theme.py:224-260`
  _HEX matches #rgb..#rrggbbaa, _NAMED matches quoted white/black/grey/gray variants, _COLOUR_CALL matches 'w'/'k' shorthand only when handed to mkPen/mkBrush/mkColor/QColor/QBrush/QPen/setPen/setBrush/setBackground/setBackgroundColor/setTextPen/setTickPen/setConfigOption/color=/pen=/brush=. _sources() is every src/audian/*.py except theme.py.
- **Tests assert both themes against measured contrast and colour-blind separation floors, so token values are not free to change.**
  `tests/test_theme.py:200-207, 356`
  test_daylight_holds_graphics_to_a_higher_contrast_floor (test_theme.py:356); the QSS lint asserts every hex in the stylesheet is a known token value and that the words white/grey/gray/black and 'qlineargradient' never appear (test_theme.py:200-207); check_separation() gates every annotation pair under CVD simulation (theme.py:3128-3172).
- **Live-switch behaviour is directly covered by tests that call theme.set_theme / theme.apply and then a polish().**
  `tests/test_controlpanel.py:437-447`
  tests/test_controlpanel.py:437-447 (control-panel pens re-resolved); tests/test_labels.py:1700-1730 (label editor outline and grips follow a switch, with the measured pre-fix defect recorded); tests/test_eventoverlay.py:634-719, 1242-1249 (per-theme alpha and marker colours); tests/test_theme.py:96-116 (apply is idempotent, style_* appliers run).
- **theme.collect_orphan_widgets, called once from DataBrowser after the plots are built, is a known segfault hazard already noted in todo.md.**
  `src/audian/databrowser.py:1983`
  It walks a snapshot of QApplication.topLevelWidgets() and reparents inside the loop; todo.md records segfaults in 2 of 4 full-suite runs at the widget.parentWidget() line.

### Risks

- (A) theme.apply() force-installs Fusion (theme.py:2528-2530) and overwrites the full QPalette including the Disabled group (theme.py:1979-2032). Following the system means NOT doing either on the platform where a native style exists, but the docstring records why Fusion was chosen: it is 'the only cross-platform style that honours a custom QPalette under the Wayland platform theme'. Dropping Fusion and keeping any custom palette is the combination that was already found not to work.
- (A) There is no listener for a system theme change anywhere: QStyleHints / colorScheme appear nowhere in src/ or tests/. Following the system needs QApplication.styleHints().colorSchemeChanged connected to something equivalent to Audian.set_app_theme, and set_app_theme currently also writes save_setting('theme', name) (audian.py:1684) — a system-driven switch must not overwrite the user's explicit preference, so the persistence needs a third state ('system'/'auto') that audian.py:5105-5107 and the two-value guard at audian.py:1656 currently reject.
- (A) The daylight_mode action is a two-state checkbox (audian.py:4563-4570) and toggle_daylight is a straight flip (audian.py:1692-1698). A third 'follow system' state cannot be expressed by it; the checked-state syncs at audian.py:1683 and 4565 both assume `checked == (theme == light)`.
- (A) set_theme() clears only the 'palette:' and 'stylesheet:' cache keys (theme.py:442-444). The font family probe ('families'), the resolved family per stack ('family:*'), every QFont ('font:*'), every QFontMetrics ('metrics:*'), every colormap ('cmap:*') and every dimmed colour ('dim:*') survive. If system FONTS are to be followed too, those caches must be invalidated as well, and font metrics feed layout maths (yaxisitem.py:166, timeaxisitem.py:96, databrowser.py mono_metrics x6, the fixed-width status readouts at audian.py:2130-2160) — a font change resizes the whole window's geometry budget, which is what repolish() (audian.py:2213-2237) exists to recompute and was measured to get wrong once already.
- (A) Colours are baked at widget-build time in ~37 setStyleSheet sites and hundreds of pen/brush constructions. Only widgets tagged via theme.tint/frame/band are recoverable by restyle_tree (theme.py:1507-1541); everything else is re-applied by hand in restyle_chrome (audian.py:2249-2275), DataBrowser.apply_theme (databrowser.py:2744-2806) and per-item polish(). StartupPage is not recoverable at all and is destroyed and rebuilt (audian.py:1670-1679). Any new source of colour (a system palette) inherits this whole manual propagation chain — nothing about it becomes automatic.
- (A) tests/test_theme.py:224-260 forbids any colour literal in src/audian outside theme.py, and the QSS lint (test_theme.py:200-207) asserts every hex in the stylesheet is a known token value. Reading colours from QPalette at runtime does not trip those lints, but the contrast-floor and CVD-separation tests (theme.py:3128-3172, test_theme.py:356 and the annotation-separation suite) assert measured ratios against fixed token tables — a system palette cannot be validated at build time, so either those guarantees are dropped or the system colours must be clamped/derived, which is a different feature from 'follow the system'.
- (A) glyph_icon explicitly refuses QStyle standard icons because 'those are pre-rendered pixmaps in the platform theme's own grey and never honour ours' (audian.py:302-306), and the On-state pixmap exists to fix a measured daylight defect (audian.py:317-321). 'Follow native icons' directly reverses a decision that has a recorded failure behind it.
- (A) EventOverlay indexes SPAN_FILL_ALPHA by theme.current_theme() with a bare dict lookup (eventoverlay.py:677) and theme.set_theme does THEMES[name] (theme.py:438); both KeyError on an unrecognised name. A 'system'/'auto' value must be resolved to 'dark' or 'light' before it ever reaches theme.set_theme or those tables.
- (A) apply_pg_config() sets pyqtgraph's global background/foreground (theme.py:2502-2506) and only affects items built afterwards; DataBrowser.apply_theme's walk is what fixes existing items. Any item type added without an apply_theme/polish method silently keeps the old theme — the fallback in databrowser.py:2762-2766 is hasattr-based, so a missing method is a no-op, not an error.
- (B) There are TWO persistence stores, not one: settings.json via platformdirs (audian.py:912-918) and QSettings('audian','audian') → ~/.config/audian/audian.conf (databrowser.py:1465, 7421). Moving spectrogram/colormap into settings.json means touching scripts/smoke_test.py:237-265 (which redirects both) and tests/test_smoketest.py:60-86 (which asserts audian.conf is written). If QSettings is emptied entirely, the harness's second redirect and its test become dead but must be removed deliberately, not silently.
- (B) The persisted colormap is an INDEX, and the two themes offer lists of different lengths and different contents (8 dark vs 5 light, theme.py:2549-2582). Index 3 is 'inferno' in dark and 'greyscale' in light. Persisting the cmap portably needs the NAME plus per-theme resolution, or two keys; storing the index as-is carries the existing per-theme ambiguity into settings.json.
- (B) set_color_map writes to the store on EVERY call including from apply_theme (databrowser.py:2785) and from color_map_cycler. If it moves to save_setting, a single theme switch or a key-repeat on the cmap cycler becomes a full read-modify-rewrite of settings.json per event — exactly what save_panel_split's docstring (databrowser.py:6865-6874) says must not happen. It needs the same per-gesture debounce and last-written guard as save_spec_band (databrowser.py:6849-6851).
- (B) nfft is sample-rate dependent: nfft_label divides by self.data.rate to show a duration (databrowser.py:2726-2731), so a persisted nfft means a different window LENGTH on a different recording. This is the same hazard SPEC_BAND_SETTING_VERSION 2 (absolute Hz, null at the limit) and PANEL_SPLIT_SETTING_VERSION 3 (measured against a fixed allowance, null on default) were both written to avoid; a raw nfft int repeats the mistake those two versions exist to record.
- (B) update_resolution is debounced and pending_nfft is cleared inside the timer (databrowser.py:7300-7330), and set_resolution triggers a full recompute (~1.5 s for 16 channels per the update_filter docstring). Persisting nfft must hook the settled value, not the pending one, or a key-repeat on R writes every intermediate window size.
- (B) save_setting is read-modify-write with no locking or atomic replace (audian.py:935-947), and settings() re-reads the file at every DataBrowser construction (databrowser.py:3683, 5168, 5715, 6747, 6794). Two open audian instances, or several tabs saving during one theme switch, are last-writer-wins. Adding more keys and more writers widens that window.
- (B) Every existing settings.json value carries a `version` int and a documented migration rule that differs per key: PANEL_SPLIT v3 DROPS a v2 value with a logged warning, SPEC_BAND v2 MIGRATES a v1 value. There is no shared migration framework — each reader hand-checks its own version. New keys must state and implement their own rule, and the reasoning for drop-vs-migrate is recorded at databrowser.py:1078-1090.
- (B) Settings values are per-preference, never per-recording, and that is deliberate (PARAM_TAB_SETTING's comment at databrowser.py:1018-1024: 'Not per recording either -- a reader walking the file spine of one session is doing one job, and a tab that flipped as they stepped would be a control moving under them'). Anything added must survive being applied to the NEXT recording opened, which is the constraint that killed the naive forms of both the panel split and the spectrogram band.

## system-theme

ENVIRONMENT (measured). PySide6 6.11.2 / QtCore.qVersion() == "6.11.2", pyqtgraph 0.14.0. Desktop is KDE Plasma on X11: XDG_CURRENT_DESKTOP=KDE, DESKTOP_SESSION=plasma, KDE_SESSION_VERSION=5, XDG_SESSION_TYPE=x11, DISPLAY=:0. QT_QPA_PLATFORMTHEME, QT_QPA_PLATFORM, QT_STYLE_OVERRIDE and GTK_THEME are all UNSET. A real display is reachable from this shell, so nothing had to run offscreen: QGuiApplication.platformName() == "xcb". The wheel ships platformthemes/{libqgtk3.so, libqxdgdesktopportal.so} and an EMPTY styles/ directory, so the KDE colours come from Qt's built-in QKdeTheme (compiled into QtGui, not a plugin) reading ~/.config/kdeglobals directly: app.palette() Window #2a2e32 == kdeglobals [Colors:Window] BackgroundNormal=42,46,50; Base #1b1e20 == [Colors:View] BackgroundNormal=27,30,32; Highlight #3daee9 == DecorationFocus=61,174,233. The palette ALREADY follows the desktop exactly; the app throws it away.

API RESULTS (all present and working). QStyleHints.colorScheme() -> Qt.ColorScheme.Dark. Qt.ColorScheme = {Unknown:0, Light:1, Dark:2}. colorSchemeChanged(Qt::ColorScheme) exists and was observed firing. setColorScheme() and unsetColorScheme() both exist and both work, but setColorScheme is ASYNCHRONOUS on xcb: immediately after setColorScheme(Light), colorScheme() still read Dark; only after app.processEvents() did it read Light with Window #efefef. setColorScheme(Qt.ColorScheme.Unknown) and unsetColorScheme() both restore the system value. QApplication.setDesktopSettingsAware exists (currently True). QPalette exposes 22 roles including Accent (0x15) -> #308cc6 here. QStyleFactory.keys() == ['Windows', 'Fusion'] only -- no Breeze: the wheel ships no style plugins and the system has no /usr/lib/x86_64-linux-gnu/qt6/plugins/styles directory at all (only Qt5 has breeze.so/oxygen.so). Fusion is what Qt picks on its own here, so the app's forced setStyle("Fusion") is a NO-OP on this machine. QIcon.themeName() == 'breeze-dark', fallbackThemeName() == 'breeze', QIcon.ThemeIcon has 151 members, fromTheme works for real freedesktop names. QFontDatabase.systemFont(GeneralFont) == "Sans Serif,9" and FixedFont == "monospace,9", but TitleFont and SmallestReadableFont both return 'Noto Color Emoji,12' -- garbage, do not use. kdeglobals carries no font= key, so 9pt Sans Serif is Qt's own default, not a user choice. QSS palette(<role>) IS honoured: a QLabel styled 'background: palette(highlight)' rendered #3daee9, exactly app.palette().Highlight. QEvent types available: ApplicationPaletteChange=38, PaletteChange=39, ThemeChange=210, ApplicationFontChange=36, StyleChange=100. app.setStyle() AFTER app.setPalette() does not reset the palette, and capturing app.palette() before overriding then restoring it later reproduces the system palette exactly.

HEADLESS DIVERGENCE. tests/conftest.py:31 and tests/test_theme.py:22 both force QT_QPA_PLATFORM=offscreen. Measured under offscreen: colorScheme() == Unknown, palette is Fusion's generic LIGHT (#efefef / #ffffff / #308cc6), QIcon.themeName() == '' and QIcon.fromTheme('document-open').isNull() == True.

WHAT THE APP OVERRIDES. audian.py:5103 builds the QApplication; audian.py:5105-5107 resolves the theme from --theme or ~/.config/audian/settings.json; audian.py:5108 calls theme.apply(app, theme_name), the single startup theming call. theme.apply (theme.py:2509-2534) performs four application-level overrides: QStyleFactory.create("Fusion") + app.setStyle (2528-2530), app.setPalette(palette()) (2531), app.setFont(font_ui()) (2532), app.setStyleSheet(stylesheet()) (2534), plus apply_pg_config() (2533). apply_pg_config (2496-2506) forces pg.setConfigOptions(background=token('bg.plot'), foreground=token('fg.muted'), antialias=False). Nothing anywhere in src/audian reads QStyleHints, colorScheme, or QIcon.fromTheme.

SCALE OF THE HAND-MADE LOOK. 23 colour tokens per theme, identical key sets in DARK_TOKENS (theme.py:288-350) and LIGHT_TOKENS (theme.py:364-393). palette() (theme.py:1979-2031) sets 19 Active roles and 8 Disabled roles from tokens. The QSS template (theme.py:2034-2442) is 409 lines / 144 rule blocks over 23 widget classes, including a blanket 'QWidget { color: $fg; }' (2044-2046) and 'QMainWindow, QDialog, QScrollArea... background-color: $bg_base' (2048-2050). Icons are 24 hand-painted QPainterPath glyph kinds drawn per QIcon mode (audian.py:301-333), with an explicit in-code ruling at audian.py:71-74 and 1645-1651 that QStyle standard icons were rejected at 1.09:1 on bg.surface. Metrics are absolute pixels tuned to Inter 10pt.

A LATENT SYSTEM LEAK ALREADY EXISTS. palette() starts from a bare QPalette() (theme.py:1991), and Qt's default constructor seeds from the application palette -- so every role the theme does NOT set inherits the desktop's value. Measured: theme.palette().color(Accent) == #308cc6, the KDE Breeze accent, in BOTH audian themes. Cached keyed only on theme name (theme.py:1985-1989), so the first call bakes whatever the platform palette happened to be.

CONCRETE OPTIONS.

Option 1 -- follow only the LIGHT/DARK preference, keep both hand-made token tables (auto-switch between the two existing themes). Code: read QGuiApplication.styleHints().colorScheme() at audian.py:5105 to pick the default when the stored setting says 'system'; connect colorSchemeChanged to the EXISTING Audian.set_app_theme (audian.py:1648-1689), which already re-applies palette+font+QSS, rebuilds every glyph icon (audian.py:1710-1717), walks theme.restyle_tree (theme.py:1507-1540) and calls DataBrowser.apply_theme (databrowser.py:2744+) plus seven other apply_theme methods; widen --theme choices (audian.py:5047-5054); turn the checkable daylight_mode QAction (audian.py:4563-4570) into a three-way choice; map ColorScheme.Unknown -> dark so offscreen/CI stays deterministic. Risk: LOW. The live re-theme path is already built and used. No token, contrast test, QSS or icon changes. Cost: the app agrees with the desktop about light vs dark only -- not accent, font or icons. This is the option that preserves the two themes the user likes, exactly.

Option 2 -- derive tokens from QPalette so accent/grounds match the desktop. Code: replace the 23-entry token table with a function reading QPalette roles (Window/Base/Button/Text/PlaceholderText/Highlight/Accent/Link) and derive by mixing the ~15 tokens with no QPalette equivalent (bg.plot, bg.lane, edge, border, border.hi, fg.faint, on.primary, primary.dim, 4x trace.*, 2x ann.*); rewrite the QSS against palette(<role>) (measured working) so it stops being regenerated per theme; re-point apply_pg_config (theme.py:2502) at QPalette.Base/Text; re-derive MARKER_COLORS/LIGHT_MARKER_COLORS (theme.py:2673-2700) and the annotation hues against an unknown ground. Risk: HIGH, measurably. On THIS desktop a naive QPalette-derived table already FAILS audian's own 4.5:1 gate: Accent #308cc6 scores 3.71 on Window and 3.31 on Button; Link #1d99f3 scores 4.50 and 4.01 (audian's own accent: 9.45/8.92/9.25). Every contrast test (tests/test_theme.py:36-49, 356-370, 421-431, 632-647) and Okabe-Ito separation test (507-565, 649-672) becomes a function of the user's desktop. The daylight theme's premise -- pure #FFFFFF grounds, near-black ink, dark saturated series for 50,000 lux (theme.py:352-363) -- is not derivable from any QPalette and would be lost.

Option 3 -- full native: drop tokens, QPalette everywhere. Code: delete both token tables, delete palette() (1979-2031), reduce the 409-line QSS to geometry-only rules, remove setStyle/setPalette/setStyleSheet from theme.apply, and touch ~100 theme.token/qcolor/pen/brush call sites across 7 modules plus 37 setStyleSheet sites. Risk: VERY HIGH and largely pointless here: QStyleFactory offers only Windows and Fusion, so 'native' buys colours but never native widget shapes -- Fusion draws the app either way. The plot-series colours, annotation hues, spectrogram colormaps (theme.py:2582-2603) and their per-theme reversal (2597) have no QPalette source and survive as tokens regardless, so the design system does not actually go away.

FONTS (orthogonal). app.setFont(font_ui()) at theme.py:2532 installs Inter (installed here) at SIZE_PT=10; the system font is Sans Serif 9pt. Adopting it changes family AND size and invalidates every absolute pixel metric; tests/test_theme.py:433-451 already asserts S2+RAIL_NUMBER_HEIGHT+RAIL_TOGGLE_HEIGHT+LevelMeter.HEIGHT <= CHANNEL_DENSE_HEIGHT, and 92-99 asserts app.font().pointSize() == theme.SIZE_PT. Middle path: keep font_mono for numeric readouts (digit alignment is a stated requirement, theme.py:729-736), take only the UI face/size from the system, after converting fixed heights to QFontMetrics expressions.

ICONS (orthogonal). 17 of 24 glyph kinds have a freedesktop name that resolves under breeze-dark now (play, pause, seek/skip fwd+back, forward/back, home, save, fit, zoom, more, label, analyze, ask, power); 7 have none (spectrogram, trace, meanspec, colorbar, navigator, channels, play-region). Mixing yields two visual languages in one toolbar. Worse, a themed icon carries the ICON THEME's colour: with breeze-dark system-wide those 17 would be light grey and vanish on the daylight theme's #EDEFF3 toolbar -- the defect class already documented at audian.py:71-74. Under offscreen fromTheme returns null, so glyph_icon() must stay as fallback.

qt6migration.md RECORDS NOTHING ON THIS. Zero hits for colorScheme, styleHints, setColorScheme, palette-as-theming, stylesheet or Fusion. Only near-matches: 'command palette' (qt6migration.md:641) and 'native dialogs' (910). Its 'Persistence and settings' section (771-789) is generic advice with no audian-specific decision.

### Facts

- **Installed Qt/PySide6 is 6.11.2, above the 6.8 floor for setColorScheme/unsetColorScheme**
  `/home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/.venv-qt6/lib/python3.12/site-packages/PySide6/__init__.py:1`
  PySide6.__version__ == '6.11.2'; PySide6.QtCore.qVersion() == '6.11.2'; pyqtgraph 0.14.0. Verified with .venv-qt6/bin/python.
- **Platform is KDE Plasma on X11 with no Qt theme env overrides set; a real display is reachable so nothing ran offscreen**
  `/home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/qt6migration.md:1`
  XDG_CURRENT_DESKTOP=KDE, XDG_SESSION_DESKTOP=KDE, DESKTOP_SESSION=plasma, KDE_FULL_SESSION=true, KDE_SESSION_VERSION=5, XDG_SESSION_TYPE=x11, DISPLAY=:0, XCURSOR_THEME=breeze_cursors. QT_QPA_PLATFORMTHEME, QT_QPA_PLATFORM, QT_STYLE_OVERRIDE, GTK_THEME all unset. QGuiApplication.platformName() == 'xcb'.
- **QStyleHints.colorScheme() returns Qt.ColorScheme.Dark here**
  `/home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/.venv-qt6/lib/python3.12/site-packages/PySide6/QtCore.pyi:9225`
  Measured with a real QApplication on xcb. Qt.ColorScheme members are Unknown=0, Light=1, Dark=2.
- **colorSchemeChanged, colorScheme, setColorScheme and unsetColorScheme all exist in this build and all work**
  `/home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/.venv-qt6/lib/python3.12/site-packages/PySide6/QtGui.pyi:8817-8869`
  Stubs: colorSchemeChanged : ClassVar[Signal] = colorSchemeChanged(Qt::ColorScheme) at 8817; colorScheme() -> Qt.ColorScheme at 8832; setColorScheme(scheme) at 8846; unsetColorScheme() at 8869. All four exercised at runtime; the signal was observed firing on each setColorScheme call.
- **setColorScheme() is ASYNCHRONOUS on xcb: the change is only visible after an event-loop turn**
  `/home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/.venv-qt6/lib/python3.12/site-packages/PySide6/QtGui.pyi:8846`
  Immediately after sh.setColorScheme(Qt.ColorScheme.Light), sh.colorScheme() still read ColorScheme.Dark and app.palette().Window was still #2a2e32. After one app.processEvents() it read ColorScheme.Light with Window #efefef. Set-then-read-back in the same block returns the stale value.
- **Both setColorScheme(Qt.ColorScheme.Unknown) and unsetColorScheme() restore the system scheme**
  `/home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/.venv-qt6/lib/python3.12/site-packages/PySide6/QtGui.pyi:8869`
  After forcing Light, either call plus processEvents returned colorScheme() to Dark and Window to #2a2e32.
- **Forcing ColorScheme.Light on this KDE box yields Fusion's generic light palette, not a KDE light scheme**
  `/home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/theme.py:364`
  After setColorScheme(Light): Window #efefef, Base #ffffff, Text #000000, Highlight #308cc6 -- Fusion defaults, because kdeglobals carries only the one (dark) scheme. Requesting a scheme the desktop has not configured does not produce a native-looking light theme.
- **The application palette already tracks KDE exactly, sourced from kdeglobals by Qt's built-in QKdeTheme**
  `/home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/theme.py:1991`
  app.palette() Active roles measured: Window #2a2e32, Base #1b1e20, AlternateBase #232629, Button #31363b, Text/WindowText/ButtonText #fcfcfc, Highlight #3daee9, HighlightedText #fcfcfc, Link #1d99f3, LinkVisited #9b59b6, ToolTipBase #31363b, ToolTipText #fcfcfc, PlaceholderText #a1a9b1, Light #181b1d, Midlight #25292c, Dark #626c76, Mid #41484e, Shadow #191919, BrightText #4b4b4b, Accent #308cc6. Matches ~/.config/kdeglobals [Colors:Window] BackgroundNormal=42,46,50 and [Colors:View] BackgroundNormal=27,30,32, DecorationFocus=61,174,233.
- **No KDE platformtheme or style PLUGIN is involved -- the wheel ships neither**
  `/home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/.venv-qt6/lib/python3.12/site-packages/PySide6/Qt/plugins/platformthemes/libqgtk3.so:1`
  PySide6/Qt/plugins/platformthemes/ contains only libqgtk3.so and libqxdgdesktopportal.so; PySide6/Qt/plugins/styles/ is EMPTY. The KDE palette therefore comes from QKdeTheme compiled into QtGui reading kdeglobals directly, which is why it works with no plugin present.
- **QStyleFactory.keys() offers only ['Windows', 'Fusion'] -- Breeze is unavailable at any price**
  `/home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/.venv-qt6/lib/python3.12/site-packages/PySide6/QtWidgets.pyi:6664`
  The wheel ships no style plugins, and the system has no /usr/lib/x86_64-linux-gnu/qt6/plugins/styles directory at all (only Qt5 has breeze.so and oxygen.so). 'Native widget shapes' is not reachable on this machine even with system PySide6.
- **The app's forced Fusion style is a NO-OP on this platform**
  `/home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/theme.py:2528-2530`
  Before any setStyle call, app.style().name() already returned 'fusion'; after QStyleFactory.create('Fusion') + app.setStyle it still returns 'fusion'. The justification comment at theme.py:2521-2522 ('the only cross-platform style that honours a custom QPalette under the Wayland platform theme') describes a choice that costs nothing here.
- **theme.apply() performs four QApplication-level overrides that each defeat a piece of the system theme**
  `/home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/theme.py:2509-2534`
  style = QStyleFactory.create("Fusion") / app.setStyle(style) at 2528-2530; app.setPalette(palette()) at 2531; app.setFont(font_ui()) at 2532; app.setStyleSheet(stylesheet()) at 2534; apply_pg_config() at 2533. This is the complete list of things that must change to follow a system palette. Docstring states the ordering rationale: 'stylesheet (last, so it wins over the palette where they overlap)'.
- **pyqtgraph's global background and foreground are forced from tokens, not from QPalette**
  `/home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/theme.py:2496-2506`
  pg.setConfigOptions(background=token('bg.plot'), foreground=token('fg.muted'), antialias=False). Following the system would mean background=QPalette.Base and foreground=QPalette.Text/PlaceholderText.
- **Startup: the theme is chosen from CLI or settings.json and applied right after QApplication is built; styleHints is never consulted**
  `/home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/audian.py:5103-5108`
  app = QApplication(sys.argv[:1] + qt_args) at 5103; theme_name = args.theme or settings().get('theme', theme.THEME_DARK) at 5105, clamped to dark/light at 5106-5107; theme.apply(app, theme_name) at 5108. grep for styleHints / colorScheme / fromTheme across src/audian returns zero hits.
- **--theme accepts only 'dark' and 'light'; there is no 'system'/'auto' value**
  `/home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/audian.py:5047-5054`
  parser.add_argument('--theme', dest='theme', default=None, choices=['dark','light'], help="colour theme; 'light' is the high-contrast daylight theme for outdoor use (default: whatever was last chosen, else dark)")
- **A complete, already-working live re-theme path exists -- the main asset for auto-switching**
  `/home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/audian.py:1648-1689`
  Audian.set_app_theme(name) calls theme.apply(app, name), refresh_glyph_icons(), restyle_chrome(), every browser's apply_theme(), rebuilds StartupPage, repolish(), sets daylight_mode's check state and save_setting('theme', name). Connecting colorSchemeChanged to this is a few lines.
- **Icons must be rebuilt on any theme change because QIcon bakes its pixmaps**
  `/home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/audian.py:1700-1717`
  _set_glyph records (target, kind) pairs in self._glyph_targets so refresh_glyph_icons can call target.setIcon(glyph_icon(kind)) again. Docstring: 'A QIcon bakes its pixmaps when it is built, so icons do not follow a live theme switch on their own.'
- **There are 23 colour tokens per theme, with identical key sets in both tables**
  `/home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/theme.py:288-393`
  DARK_TOKENS keys == LIGHT_TOKENS keys == {accent, ann.pulse, ann.trial, bg.base, bg.lane, bg.plot, bg.raised, bg.surface, border, border.hi, danger, edge, fg, fg.faint, fg.muted, on.primary, primary, primary.dim, success, trace.envelope, trace.filtered, trace.raw, trace.zero}. Only ~8 have any QPalette equivalent; bg.plot, bg.lane, edge, border, border.hi, fg.faint, on.primary, primary.dim, the 4 trace.* and the 2 ann.* have none.
- **palette() sets 19 Active roles and 8 Disabled roles from tokens, and caches per theme name**
  `/home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/theme.py:1979-2031`
  Active: Window, WindowText, Base, AlternateBase, Text, Button, ButtonText, BrightText, ToolTipBase, ToolTipText, PlaceholderText, Highlight, HighlightedText, Link, LinkVisited, Light, Midlight, Mid, Dark, Shadow. Disabled: WindowText, Text, ButtonText, Highlight, HighlightedText, Base, Button, Window. Cached at key f'palette:{current_theme()}'. Docstring reason: 'Fusion derives disabled colours badly from a dark base if you leave it to guess'.
- **LATENT BUG / partial system leak: roles the theme does not set inherit the desktop's colour, in BOTH themes**
  `/home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/theme.py:1991`
  palette() starts from a bare `p = QPalette()` at theme.py:1991. Qt's default QPalette constructor seeds from the application palette, so QPalette.ColorRole.Accent -- never assigned by theme.py -- measured #308cc6 (KDE Breeze) inside theme.palette() under BOTH audian themes. Because the result is cached on theme name alone (1985-1989), the first call permanently bakes whatever the platform palette was at that moment.
- **The generated QSS is 409 lines / 144 rule blocks covering 23 widget classes, applied last so it beats the palette**
  `/home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/theme.py:2034-2494`
  _QSS Template spans theme.py:2034-2442; stylesheet() interpolates 28 token/metric values at 2443-2494. Classes styled: QWidget, QMainWindow, QDialog, QScrollArea, QLabel, QToolBar, QToolButton, QPushButton, QMenu, QMenuBar, QStatusBar, QLineEdit, QAbstractSpinBox, QComboBox, QCheckBox, QRadioButton, QSlider, QSplitter, QScrollBar, QTabBar, QTabWidget, QTableView, QHeaderView, QPlainTextEdit, QToolTip, QTableCornerButton.
- **Two blanket QSS rules would fight any system palette directly**
  `/home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/theme.py:2044-2050`
  `QWidget { color: $fg; }` at 2044-2046 paints EVERY widget's text from the token table, and `QMainWindow, QDialog, QScrollArea, QScrollArea > QWidget > QWidget { background-color: $bg_base; }` at 2048-2050 paints the window ground. These two are the first things to remove for a native palette.
- **QSS palette(<role>) IS supported and honoured in this build -- measured, not assumed**
  `/home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/theme.py:2443`
  A QLabel with setStyleSheet('QLabel { background: palette(highlight); color: palette(base); }') rendered to a QPixmap gave pixel #3daee9, identical to app.palette().color(Highlight). The whole _QSS template could be rewritten against palette(window)/palette(base)/palette(highlight)/palette(text) and would stop needing per-theme regeneration.
- **MEASURED: a naive QPalette-derived token table fails audian's own 4.5:1 contrast gate on this very desktop**
  `/home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/tests/test_theme.py:36-49`
  Mapping bg.base<-Window #2a2e32, bg.surface<-Button #31363b, bg.plot<-Base #1b1e20, fg<-WindowText #fcfcfc, fg.muted<-PlaceholderText #a1a9b1, primary<-Highlight #3daee9, accent<-Accent #308cc6, link<-Link #1d99f3 gives: accent on bg.base 3.71 FAIL, accent on bg.surface 3.31 FAIL, link on bg.base 4.50 FAIL, link on bg.surface 4.01 FAIL. audian's own dark tokens score accent 9.45 / 8.92 / 9.25 on the same grounds. fg (13.33/11.89/16.33), fg.muted (5.75/5.13/7.04) and primary (5.49/4.90/6.73) do pass.
- **The test suite pins exact contrast numbers that only hold for the hand-made tokens**
  `/home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/tests/test_theme.py:36-49`
  assert round(contrast_ratio(FG, BG_PLOT),2) == 15.93; FG_MUTED 7.69; PRIMARY 5.87; ACCENT 9.25. Plus test_text_tokens_clear_45_on_their_surface asserts check_contrast() is empty for EVERY theme. A palette-derived table makes all of these a function of the user's desktop.
- **The test suite asserts the QSS contains only token hexes, and that PRIMARY appears in the applied stylesheet**
  `/home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/tests/test_theme.py:92-99`
  test_stylesheet_has_no_raw_colour_literals_beyond_tokens: {c.upper() for c in re.findall(r'#[0-9A-Fa-f]{6}', qss)} <= {v.upper() for v in theme.TOKENS.values()}, and asserts f'{FOCUS_WIDTH}px solid {PRIMARY}' is present. test_apply_twice_under_offscreen asserts theme.PRIMARY in app.styleSheet() and app.font().pointSize() == theme.SIZE_PT.
- **The whole test suite runs under QT_QPA_PLATFORM=offscreen, where the system theme does not exist**
  `/home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/tests/conftest.py:31`
  tests/conftest.py:31 and tests/test_theme.py:22 both call os.environ.setdefault('QT_QPA_PLATFORM','offscreen'). Measured under offscreen: colorScheme() == ColorScheme.Unknown, palette is Fusion LIGHT (Window #efefef, Base #ffffff, Highlight #308cc6), style 'fusion', QIcon.themeName() == '' and QIcon.fromTheme('document-open').isNull() == True.
- **The application font is Inter 10pt; the system font is Sans Serif 9pt**
  `/home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/theme.py:646-670`
  theme.apply calls app.setFont(font_ui()), which resolves FONT_UI_FAMILIES ('Inter','Adwaita Sans','Noto Sans','DejaVu Sans','sans-serif') at SIZE_PT=10. Measured installed: Inter True, Adwaita Sans False, Noto Sans True, DejaVu Sans True. Untouched, app.font() and QFontDatabase.systemFont(GeneralFont) both read 'Sans Serif,9'.
- **QFontDatabase.systemFont() is only trustworthy for GeneralFont and FixedFont in this build**
  `/home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/.venv-qt6/lib/python3.12/site-packages/PySide6/QtGui.pyi:2073`
  Measured: GeneralFont 'Sans Serif' 9.0pt, FixedFont 'monospace' 9.0pt, TitleFont 'Noto Color Emoji' 12.0pt, SmallestReadableFont 'Noto Color Emoji' 12.0pt. Do not wire SIZE_SMALL_PT to SmallestReadableFont.
- **KDE here has no configured font, so 'the system font' is Qt's own default and not a user preference**
  `/home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/theme.py:660`
  ~/.config/kdeglobals contains no font=, fixed=, menuFont=, toolBarFont=, smallestReadableFont= or activeFont= key. Following the system font would trade a deliberate Inter 10pt for an unconfigured fallback.
- **The mono font exists specifically so digits align, which a system font stack cannot guarantee**
  `/home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/theme.py:729-736`
  font_mono docstring: 'Every number the user compares or reads off an axis must use this face so digits align: tick labels, status-bar readouts, hover tooltips, tables.' FONT_MONO_FAMILIES resolves to DejaVu Sans Mono here (JetBrainsMono Nerd Font and Adwaita Mono are not installed).
- **Layout metrics are absolute pixels tuned to the current font, and a test already encodes one of the fits**
  `/home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/theme.py:488-493`
  TOOLBAR_HEIGHT=36 (theme.py:488), CHANNEL_DENSE_HEIGHT=34 (493), RAIL_NUMBER_HEIGHT=14 (1025), RAIL_TOGGLE_HEIGHT=14 (1035), TOOLBAR_BUTTON_BOX=30 (1038), TOOLBAR_BUTTON_HEIGHT=20 (1053), CONTROL_HEIGHT=26 (1649), CHIP_HEIGHT=22 (1656). tests/test_theme.py:433-451 asserts S2 + RAIL_NUMBER_HEIGHT + RAIL_TOGGLE_HEIGHT + LevelMeter.HEIGHT <= CHANNEL_DENSE_HEIGHT.
- **Icons are 24 hand-painted glyphs, drawn per QIcon mode, coloured from tokens**
  `/home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/audian.py:301-333`
  glyph_icon(kind, size, color) builds a QIcon with explicit pixmaps for Normal/Active/Selected/Disabled Off states plus a separate on.primary pixmap for the On states. Kinds registered via _set_glyph: analyze, ask, back, channels, colorbar, fit, forward, home, label, meanspec, more, navigator, play, play-region, power, save, seek-backward, seek-forward, skip-backward, skip-forward, spectrogram, trace, zoom (plus pause in _FILLED_GLYPHS).
- **The code already records a measured rejection of platform-theme icons**
  `/home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/audian.py:71-74`
  audian.py:71-74: 'a toolbar glyph is drawn three times, once per QIcon mode, so that Qt never has to fake a disabled or hovered variant from a single pre-rendered pixmap (which is what made the QStyle standard icons invisible at 1.09:1 on bg.surface)'. audian.py:1645-1651: 'above all no QStyle standard icon: those are pre-rendered pixmaps in the platform theme's own grey and never honour ours.'
- **QIcon.fromTheme and QIcon.ThemeIcon work here and cover 17 of the 24 glyph kinds**
  `/home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/.venv-qt6/lib/python3.12/site-packages/PySide6/QtGui.pyi:2675`
  QIcon.themeName() == 'breeze-dark', fallbackThemeName() == 'breeze', themeSearchPaths == ['~/.local/share/icons','/usr/share/icons','/var/lib/snapd/desktop/icons',':/icons']. QIcon.ThemeIcon has 151 members and QIcon.fromTheme(QIcon.ThemeIcon.DocumentOpen) resolves. Available under breeze-dark: media-playback-start, media-playback-pause, media-seek-forward, media-seek-backward, media-skip-forward, media-skip-backward, go-next, go-previous, go-home, document-save, zoom-fit-best, zoom-in, view-more-symbolic, tag, view-statistics, help-contents, system-shutdown. NO freedesktop equivalent for: spectrogram, trace, meanspec, colorbar, navigator, channels, play-region.
- **app.setStyle() after app.setPalette() does NOT reset the palette, and the system palette can be captured and restored exactly**
  `/home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/theme.py:2531`
  Measured: system Window #2a2e32 -> setPalette(custom) -> #0b0f16 -> setStyle('Fusion') -> still #0b0f16 -> setPalette(saved_system) -> back to #2a2e32 with Base #1b1e20. There is no QApplication.unsetPalette (dir() shows only palette/paletteChanged/setPalette); capturing app.palette() before the first override is the only way back.
- **Theme-change event types are all available for an event-filter approach**
  `/home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/.venv-qt6/lib/python3.12/site-packages/PySide6/QtCore.pyi:9225`
  QEvent.Type.ApplicationPaletteChange=38, PaletteChange=39, ThemeChange=210, ApplicationFontChange=36, StyleChange=100. QApplication also exposes a paletteChanged signal and QApplication.setDesktopSettingsAware / desktopSettingsAware (currently True; must be set before the QApplication is constructed).
- **daylight_mode is a two-state checkable QAction frozen in a golden test file**
  `/home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/audian.py:4563-4570`
  self.acts.daylight_mode = QAction('&Daylight mode', self); setCheckable(True); setChecked(theme.current_theme() == THEME_LIGHT); setShortcut('Ctrl+Shift+L'); triggered -> toggle_daylight; added to view_menu at 4590. tests/data/action-inventory.json:137-146 records {checkable:true, checked:false, keys:['Ctrl+Shift+L'], path:'View', text:'Daylight mode'}, and test_actioninventory.py triggers every action and restores the theme afterwards.
- **The spectrogram colormap set and its reversal are theme-dependent with no QPalette source**
  `/home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/theme.py:2617-2666`
  spectrogram_maps() returns SPECTROGRAM_MAPS_LIGHT (theme.py:2582) under the light theme; REVERSED_MAPS (2597) flips the ramp so the noise floor matches the page; spectrogram_colormap caches per f'cmap:{current_theme()}:{name}'. databrowser.apply_theme re-pushes the colormap for exactly this reason. Any theming scheme must keep a discrete light/dark notion for this alone.
- **The categorical marker palettes are two hand-measured 8-colour tables, per theme**
  `/home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/theme.py:2673-2700`
  MARKER_COLORS (dark, 8 hues each annotated with measured contrast on bg.plot/bg.raised) and LIGHT_MARKER_COLORS (the same 8 hues darkened, index-aligned). Neither is derivable from QPalette.
- **There are ~100 theme colour-helper call sites across 7 modules plus 37 setStyleSheet sites**
  `/home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/theme.py:1447-1505`
  grep -c of theme.(token|qcolor|pen|brush) across src/audian/*.py == 100 matches (audian.py 34 token lines, databrowser.py 9, fulltraceplot.py 3, controlpanel.py 2, timeplot.py 2, spectrogramplot.py 1, theme.py 2). setStyleSheet appears 37 times: audian.py (34), databrowser.py (888, 896, 5974), theme.py (tint 1458, frame 1466, band 1503). Eight modules define apply_theme(): rangeplot.py:105, selectviewbox.py:54, yaxisitem.py:38, timeaxisitem.py:44, fulltraceplot.py:140/231/612, databrowser.py:2744.
- **restyle_tree walks the widget tree by tagged property, so chrome re-theming is registry-free and already generic**
  `/home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/theme.py:1507-1540`
  Walks root + findChildren(QWidget) and re-applies tint()/frame()/band() for any widget carrying FG_PROPERTY, FRAME_PROPERTY or BAND_PROPERTY, catching RuntimeError for dead C++ objects. Returns how many changed.
- **DataBrowser.apply_theme is a deep object-graph walk, not a repaint -- an auto-switch triggers all of it**
  `/home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/databrowser.py:2744-2800`
  Re-styles every channel figure, the time-axis figure and axis, every plot axis (apply_theme or polish), every border pen, every splitter, the data figure, restyle_tree, every rail row, re-populates and re-pushes the colormap, polishes every annotation overlay, polishes join markers and the control panel, rebuilds annotation chips, updates the badge, redraws annotations, polishes every label overlay.
- **Settings live in ~/.config/audian/settings.json, but a second competing store (QSettings) also exists**
  `/home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/audian.py:912-944`
  settings_path() returns audian_dirs.user_config_path / 'settings.json'; audian_dirs = PlatformDirs('audian','janscience') at version.py:13. save_setting('theme', name) at audian.py:1684; five more save_setting calls in databrowser.py (3718, 5250, 5779, 6856, 6885). Separately QSettings('audian','audian') at databrowser.py:1465 and databrowser.py:7421 (key 'spectrogram/colormap'). Two stores, two locations.
- **qt6migration.md records NOTHING about theming, palettes or styleHints**
  `/home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/qt6migration.md:771-789`
  Zero grep hits for colorScheme, setColorScheme, styleHints, Fusion, stylesheet or theme.py across qt6migration.md, todo.md, CHANGELOG.md and README.md. Only word-level matches in qt6migration.md are 'command palette' (641) and 'native dialogs' (910). Its 'Persistence and settings' section (771-789) and 'UI/UX quality' section (854-891) are generic checklists.
- **The module docstring states the design intent that any 'go native' change would contradict**
  `/home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/src/audian/theme.py:1-70`
  'Swiss-minimal, dark-first, high information density, zero ornament. This is a precision instrument for a scientist, not a consumer app.' And: 'Nothing else in the code base may contain a hex literal, a named Qt colour, an RGB tuple, a pen width literal, a font family string, a spacing pixel literal or a colormap name.' The light theme is documented as a DAYLIGHT theme for direct sun (pure #FFFFFF grounds, near-black ink, dark saturated series, borders strong enough to survive washout) -- a property no QPalette supplies.
- **The user-facing feature request is recorded verbatim in todo.md and explicitly wants the two current themes kept**
  `/home/weygoldt/wrk/tools/claudian/.claude/worktrees/qt6-migration/todo.md:15`
  'Follow native system theme (colors, fonts, icons) instead of building a whole coustom theme from scratch. I like how the two themes currently look, they are beautiful. But I think following the system is just better practice.'

### Risks

- OFFSCREEN/CI DIVERGENCE is the single largest hazard. tests/conftest.py:31 and tests/test_theme.py:22 force QT_QPA_PLATFORM=offscreen, where colorScheme() == Unknown and the palette is Fusion LIGHT (#efefef). Any code that picks a theme from styleHints must map Unknown to an explicit default (dark, to match today) or the entire suite silently flips to the light theme and every contrast/geometry assertion changes meaning.
- CONTRAST GUARANTEES CANNOT SURVIVE OPTION 2 OR 3 AS WRITTEN. Measured on this exact desktop, a QPalette-derived table already fails audian's own 4.5:1 bar: Accent 3.71 on Window and 3.31 on Button; Link 4.50 and 4.01. tests/test_theme.py:36-49 pins 15.93/7.69/5.87/9.25 exactly and asserts check_contrast() is empty for every theme. Either those tests go (losing the guarantee theme.py:41-60 says was measured) or a runtime contrast-repair pass is written, tested, and shown to preserve the Okabe-Ito separation floors at tests/test_theme.py:507-565 and 649-672.
- setColorScheme() IS ASYNCHRONOUS on xcb. Setting it and reading colorScheme() back in the same block returns the OLD value; only after an event-loop turn does it take effect. Code that sets the scheme and immediately re-themes off the read-back will theme to the wrong scheme. Prefer reacting to colorSchemeChanged over set-then-read.
- THE APP ALREADY LEAKS A SYSTEM COLOUR AND CACHES IT. theme.palette() builds from a bare QPalette() (theme.py:1991), which seeds unset roles from the application palette -- QPalette.Accent measures #308cc6 (KDE Breeze) inside BOTH audian themes, cached on theme name alone (theme.py:1985-1989). Whatever option is chosen this needs an explicit decision: set every role, or stop treating the palette as self-contained. Today the daylight theme carries a dark-desktop accent.
- ICON THEMES CARRY THE DESKTOP'S INK, NOT THE APP'S. With breeze-dark selected system-wide, QIcon.fromTheme returns light-grey glyphs that would be invisible on the daylight theme's #EDEFF3 toolbar -- exactly the defect class documented at audian.py:71-74. Only 17 of 24 glyph kinds have a freedesktop name at all, so the toolbar would mix two visual languages. Under offscreen fromTheme returns null icons, so glyph_icon() must remain as a fallback regardless.
- ADOPTING THE SYSTEM FONT BREAKS FIXED PIXEL METRICS. app.setFont(font_ui()) installs Inter 10pt; the system font here is Sans Serif 9pt. TOOLBAR_HEIGHT=36, CHANNEL_DENSE_HEIGHT=34, TOOLBAR_BUTTON_BOX=30, RAIL_NUMBER_HEIGHT=14, RAIL_TOGGLE_HEIGHT=14, CONTROL_HEIGHT=26 and CHIP_HEIGHT=22 are absolute pixels chosen for that face. tests/test_theme.py:433-451 asserts one of these fits and 92-99 asserts app.font().pointSize() == theme.SIZE_PT. Converting the fixed heights to QFontMetrics expressions is a prerequisite. A user with a 12pt desktop font would push five of sixteen channels below the scroll -- the exact failure the CHANNEL_DENSE_HEIGHT comment records.
- QFontDatabase.systemFont(TitleFont) and (SmallestReadableFont) both return 'Noto Color Emoji' 12pt in this build. Do not wire SIZE_SMALL_PT to SmallestReadableFont; only GeneralFont and FixedFont return sane values.
- 'NATIVE' BUYS COLOURS BUT NEVER WIDGET SHAPES HERE. QStyleFactory.keys() == ['Windows','Fusion'] and the system has no Qt6 style plugins at all. Removing the forced Fusion (theme.py:2528-2530) changes nothing on this machine and would only alter behaviour on Windows/macOS, which are untested. Option 3 pays its cost without delivering its headline benefit.
- A THREE-WAY THEME CHOICE BREAKS THE GOLDEN ACTION INVENTORY. daylight_mode is a checkable QAction (audian.py:4563-4570) recorded in tests/data/action-inventory.json:137-146 with checkable/checked/keys/path/text, and test_actioninventory.py triggers every action. Making it system/dark/light requires regenerating that file.
- THE QSS IS APPLIED LAST SPECIFICALLY TO BEAT THE PALETTE (theme.py:2517-2519). The blanket QWidget{color:$fg} (2044-2046) and the QMainWindow/QDialog/QScrollArea ground (2048-2050) mean a system palette would still be overpainted even if setPalette were removed. A half-migration that drops setPalette but keeps the QSS is worse than doing nothing.
- PYQTGRAPH'S GROUND IS SET GLOBALLY AND SEPARATELY (theme.py:2502-2506) and plot items resolve pens at construction time -- which is why set_app_theme walks eight apply_theme implementations and databrowser.apply_theme (databrowser.py:2744-2800) re-pushes the colormap, redraws annotation overlays, rebuilds chips and repolishes join markers. An auto-switch fired by colorSchemeChanged runs that whole walk, possibly mid-interaction; it must be debounced and must be safe while a browser tab is loading.
- TWO COMPETING SETTINGS STORES ALREADY EXIST (json at ~/.config/audian/settings.json via audian.py:912-944, and QSettings('audian','audian') at databrowser.py:1465 and 7421). A 'theme: system' value should be settled together with feature B rather than adding a third convention.
- theme.collect_orphan_widgets is already recorded in todo.md as able to SEGFAULT the test suite while walking topLevelWidgets and reparenting. Any work that adds widget churn on a theme switch increases exposure to that known crash.

## settings-inventory

audian persists through FOUR independent channels, none of which know about each other.

(1) settings.json — the primary config store. `settings_path()` = `audian_dirs.user_config_path / "settings.json"` (src/audian/audian.py:912-918); `settings()` reads it, never raises, returns `{}` on any OSError/ValueError or non-dict (audian.py:921-932); `save_setting(key, value)` does a full read-modify-write of the whole JSON document, `mkdir(parents=True, exist_ok=True)` then `json.dump(..., indent=2)`, swallowing OSError into `log.debug` (audian.py:935-944). There is NO atomic write (no tmp+rename) and NO in-process cache — every read hits the disk, every write rewrites the entire file. Exactly six top-level keys are ever written: `"theme"` (audian.py:1684, read at audian.py:5105), `"labels"`, `"annotations"`, `"parameter-tab"`, `"panel-split"`, `"spectrogram-band"` (all from DataBrowser, constants at databrowser.py:1004/1014/1037/1062/1078). Five of the six carry a `"version"` int and a `_SETTING_VERSION` class constant; the version guard is the copy-pasted `log.warning("ignoring %s settings written in version %r; this audian writes version %d", ...)` + return-empty at databrowser.py:3687-3695, 5172-5180, 5718-5726, 6751-6759, 6800-6808. `"theme"` is the ONLY key with no version wrapper — it is a bare string.

(2) QSettings — a second, inconsistent store. `QSettings("audian", "audian")` (org=audian, app=audian) resolves to `/home/weygoldt/.config/audian/audian.conf` (verified by running .venv-qt6/bin/python with PySide6 6.11.2). Exactly ONE key: `"spectrogram/colormap"`, an int index. Read at databrowser.py:1462-1474 (`read_color_map_setting`, called from `DataBrowser.__init__` at databrowser.py:1437), written at databrowser.py:7421 inside `set_color_map`. The QSettings import is databrowser.py:18; these are the only three QSettings references in `src/`. On disk today: `[spectrogram]\ncolormap=0`.

(3) Cache dir `~/.cache/audian` — `RecentFiles` (audian.py:557-604) writes `recent.json` there, and `CompressedData` (compresseddata.py:434-484, 527-574) writes `fulltraces.json` (an index of source-file paths → cache filenames + created/used timestamps, LRU-evicted at `max_files = 1000`), plus `NNNNNNNN-fulltrace.wav` overview files and `.stats.npy` sidecars (compresseddata.py:129-137).

(4) Per-recording sidecars — hand-made labels go to a CSV beside the recording (labels.py:108-111 `sidecar_path`), never into settings; the label *vocabulary* (categories) is the settings key `"labels"`.

Concrete Linux paths (verified with .venv/bin/python, platformdirs 4.11.5, `PlatformDirs("audian", "janscience")` at src/audian/version.py:13): config `/home/weygoldt/.config/audian`, cache `/home/weygoldt/.cache/audian`, data `/home/weygoldt/.local/share/audian`, state `/home/weygoldt/.local/state/audian`. The author string "janscience" is inert on Linux but does affect Windows/macOS paths.

Test isolation is per-module and ad hoc: there is no global conftest fixture. tests/conftest.py isolates nothing about persistence. Seven test modules redirect `audian_app.settings_path`; four of those also move Qt's whole store with `QSettings.setPath` over both formats × both scopes. tests/test_controlpanel.py:520 assigns `audian_app.settings_path` with a plain module attribute write, never restores it, and never redirects QSettings. NOTHING in the suite isolates `audian_dirs.user_cache_path`, and the user's real `~/.cache/audian/recent.json` currently contains `/tmp/pytest-of-weygoldt/pytest-47/...` entries as proof.

### Facts

- **audian_dirs is PlatformDirs("audian", "janscience") — appname "audian", appauthor "janscience".**
  `src/audian/version.py:13`
  Verified by running .venv/bin/python (platformdirs 4.11.5): user_config_path=/home/weygoldt/.config/audian, user_cache_path=/home/weygoldt/.cache/audian, user_data_path=/home/weygoldt/.local/share/audian, user_state_path=/home/weygoldt/.local/state/audian. The author string is ignored on Linux but is part of the path on Windows (%LOCALAPPDATA%\janscience\audian) and macOS.
- **settings_path() returns audian_dirs.user_config_path / "settings.json" — i.e. ~/.config/audian/settings.json on Linux.**
  `src/audian/audian.py:912-918`
  Full text: `def settings_path() -> Path:` / docstring "Where the few persistent preferences live. Config rather than cache: a wiped cache must cost the user nothing but recomputation, and a theme choice is not recomputable." / `return audian_dirs.user_config_path / "settings.json"`. It resolves through platformdirs at import time, so no environment variable can redirect it — every test has to replace the function itself.
- **settings() reads the whole JSON file on every call, with no caching, and swallows every error into an empty dict.**
  `src/audian/audian.py:921-932`
  Body: try / path = settings_path() / if path.exists(): open(path); values = json.load(sf) / if isinstance(values, dict): return values / except (OSError, ValueError) as e: log.debug("could not read settings: %s", e) / return {}. A non-dict top level (e.g. a list) silently reads as {} with no warning at all.
- **save_setting(key, value) is a read-modify-write of the ENTIRE settings file, non-atomic, and silently fails on OSError.**
  `src/audian/audian.py:935-944`
  Body verbatim: `values = settings()` / `values[key] = value` / `try:` / `audian_dirs.user_config_path.mkdir(parents=True, exist_ok=True)` / `with open(settings_path(), "w") as df:` / `json.dump(values, df, indent=2)` / `except OSError as e:` / `log.debug("could not write settings: %s", e)`. No tmp-file + rename, so an interrupted write truncates the file; contrast labels.py:704 which does write to a tmp file. A file the reader edited by hand between two writes of one session is silently clobbered because `settings()` is re-read only at the moment of the write.
- **Key "theme": the only settings.json key with no version wrapper and no shape guard.**
  `src/audian/audian.py:1684`
  Value shape: a bare string, "dark" or "light" (theme.THEME_DARK/THEME_LIGHT, theme.py:284-285). Written by `save_setting("theme", name)` at the end of Audian.set_app_theme, after the whole re-theme walk. No constant holds the literal — it is a string literal at both ends.
- **"theme" is read exactly once, at startup, and the --theme CLI flag wins for that run without being written back.**
  `src/audian/audian.py:5105-5108`
  `theme_name = args.theme or settings().get("theme", theme.THEME_DARK)`, then an unknown value falls back to THEME_DARK, then `theme.apply(app, theme_name)`. Because the CLI value is never saved, `audian --theme light` shows light but leaves the stored preference alone; only the menu/Ctrl+Shift+L path (set_app_theme) persists.
- **Key "annotations" (ANNOTATION_SETTING), version 1 (ANNOTATION_SETTING_VERSION).**
  `src/audian/databrowser.py:1004-1007`
  Value shape: {"version": 1, "layers": {layer_id: bool, ...}, "surfaces": {"trace": bool, "spectrogram": bool, "navigator": bool}}. One key holding one entry per layer, explicitly because save_setting rewrites the whole file (comment at databrowser.py:1002-1003). The F8 master switch is deliberately NOT saved (databrowser.py:5234-5241).
- **"annotations" read path: annotation_settings() with a version guard, then restore_annotation_surfaces() and restore_annotation_layers().**
  `src/audian/databrowser.py:5152-5219`
  annotation_settings() at databrowser.py:5152-5181 does `settings().get(DataBrowser.ANNOTATION_SETTING)`, drops a non-dict, and on a version mismatch logs `"ignoring %s settings written in version %r; this audian writes version %d"` and returns {}. restore_annotation_surfaces() at 5183-5192 (called from databrowser.py:5545), restore_annotation_layers() at 5194-5219 (called from databrowser.py:5004). Layers not present in the current bundle are skipped; a layer the settings never saw keeps its own default_on.
- **"annotations" write path is debounced with QTimer.singleShot(0).**
  `src/audian/databrowser.py:5221-5262`
  schedule_annotation_save() sets `self.annotation_save_pending = True` and posts `QTimer.singleShot(0, self.save_annotation_settings)`, guarded by `if self.annotation_save_pending or not self.annotations.loaded: return`. Docstring: "`save_setting` reads, updates and rewrites the whole settings file, and one click on a chip moves up to ten switches, so writing per switch would rewrite that file ten times for one gesture." Connected to annotations.sigVisibilityChanged at databrowser.py:1385 and called at databrowser.py:5083.
- **The pending annotation write is flushed on application teardown.**
  `src/audian/audian.py:4922-4927`
  `for w in list(getattr(self, "browsers", ())): w.flush_labels(); if getattr(w, "annotation_save_pending", False): w.save_annotation_settings(); w.shutdown()` — otherwise the queued zero-timer dies with the event loop.
- **Key "labels" (LABEL_SETTING), version 1 (LABEL_SETTING_VERSION) — the label VOCABULARY only, not the labels.**
  `src/audian/databrowser.py:1009-1016`
  Value shape: {"version": 1, "categories": [{"name": str, "kind": "span"|"point", "color": int}, ...]} (confirmed against the live ~/.config/audian/settings.json). The labels themselves go to a CSV beside the recording, never here. Which category is current is deliberately not saved.
- **"labels" read path: label_settings() (version guard) → restore_label_categories(), called from DataBrowser.__init__ before any file is open.**
  `src/audian/databrowser.py:3672-3706, 1347`
  label_settings() at databrowser.py:3672-3695; restore_label_categories() at databrowser.py:3697-3706 returns `tuple(saved) if saved else DEFAULT_CATEGORIES`; call site is `self.labels = LabelSet(self.restore_label_categories())`.
- **"labels" write path: save_label_settings(), called directly (NOT debounced) from two sites.**
  `src/audian/databrowser.py:3708-3724`
  save_label_settings() at databrowser.py:3708-3724 writes {"version": LABEL_SETTING_VERSION, "categories": categories_to_settings(self.labels.categories)}. Called at databrowser.py:3775 and 3997. The debounced schedule_label_save()/QTimer.singleShot(0, self.save_labels) at databrowser.py:3779-3789 is for the CSV sidecar, a different file.
- **Key "parameter-tab" (PARAM_TAB_SETTING), version 2 (PARAM_TAB_SETTING_VERSION).**
  `src/audian/databrowser.py:1018-1041`
  Value shape: {"version": 2, "tab": str} where the string is a group NAME, not an index (the Filter and Envelope groups only exist for some recordings). Version 2 exists because the tab names changed: FIXED_TAB = "Fixed labels", EDITABLE_TAB = "Editable labels" (databrowser.py:1030-1035) replaced "Annotations"/"Labels".
- **"parameter-tab" read: parameter_tab_settings() + restore_parameter_tab(); write: save_parameter_tab(title) with a memo guard, never at construction.**
  `src/audian/databrowser.py:5711-5787`
  parameter_tab_settings() at databrowser.py:5711-5727 (version guard). restore_parameter_tab() at 5729-5744, called at databrowser.py:2589. save_parameter_tab() at 5766-5787 early-returns on `if not title or title == self._param_tab_saved` (memo initialised to "" at databrowser.py:1415), so no browser writes its own default at build time and does not overwrite the choice made in the window beside it. Only caller: parameter_tab_changed() at databrowser.py:5760.
- **Key "panel-split" (PANEL_SPLIT_SETTING), version 3 (PANEL_SPLIT_SETTING_VERSION).**
  `src/audian/databrowser.py:1043-1069`
  Value shape: {"version": 3, "scale": float|null}. `scale` is `spec_scale`, the spectrogram row over theme.SPECTROGRAM_MIN_HEIGHT. null means "the default" and is written deliberately rather than freezing this window's number. Version 1 stored trace/spectrogram (did not travel across channel counts); version 2 held up to four splits per F3 size and is DROPPED rather than migrated.
- **"panel-split" read: restore_panel_split() with a version guard AND a finite/positive check, called once from DataBrowser.__init__ before any figure exists.**
  `src/audian/databrowser.py:6729-6772, 1216`
  restore_panel_split() at databrowser.py:6729-6772: version mismatch logs the standard "ignoring %s settings…" warning and returns; then `float(saved.get("scale"))` in a try/except (TypeError, ValueError), then `np.isfinite`, then `self.spec_scale = min(max(scale, 0.01), 100.0)`. Call site databrowser.py:1216.
- **"panel-split" write: save_panel_split(), once per gesture — no debounce timer, but only called at gesture end and on reset.**
  `src/audian/databrowser.py:6865-6890`
  save_panel_split() at databrowser.py:6865-6890. Callers: finish_panel_split() at databrowser.py:6711-6714 ("End of the gesture: one full layout pass, one settings write") and reset_panel_split() at databrowser.py:6716-6728 (Shift+F3). Docstring: "`save_setting` reads, updates and rewrites the whole settings file, and one drag is a hundred mouse moves."
- **Key "spectrogram-band" (SPEC_BAND_SETTING), version 2 (SPEC_BAND_SETTING_VERSION) — the only key with a MIGRATION rather than a drop.**
  `src/audian/databrowser.py:1071-1103, 6794-6812`
  Value shape: {"version": 2, "min_hz": float|null, "max_hz": float|null}, absolute Hz (deliberately not a fraction of Nyquist). Version 1 was max-only and is accepted: the guard is `if version not in (1, DataBrowser.SPEC_BAND_SETTING_VERSION)` and min_hz is left None for version 1. This is the exact opposite policy to PANEL_SPLIT_SETTING_VERSION 3.
- **"spectrogram-band" read: spectrogram_band() clamps each end against this recording's Nyquist via _band_value().**
  `src/audian/databrowser.py:6774-6836`
  spectrogram_band() at databrowser.py:6774-6822; _band_value() at 6824-6836 rejects non-float, non-finite and <= 0 and returns `min(value, nyquist)`. A floor >= the ceiling drops the floor. Called from the browser build at databrowser.py:1752, which also seeds `self._spec_band_saved = (band_min, band_max)` at 1753.
- **"spectrogram-band" write: save_spectrogram_band(), guarded by the _spec_band_saved memo, and only ONE browser tab writes.**
  `src/audian/databrowser.py:6838-6863, src/audian/audian.py:3305-3326`
  save_spectrogram_band() at databrowser.py:6838-6863 early-returns when `(min_hz, max_hz) == self._spec_band_saved` (memo initialised None at databrowser.py:1284). set_spectrogram_band(min_hz, max_hz, save=True) at databrowser.py:2648-2707 stores an end sitting at its limit as None, and Audian.set_spectrogram_band at audian.py:3305-3326 fans out with `save=b is current` so only the tab the number was typed into writes — otherwise the stored value would depend on the order of Audian.browsers.
- **The version-guard pattern is five verbatim copies of the same log.warning, one per versioned key.**
  `src/audian/databrowser.py:3688-3694, 5173-5179, 5719-5725, 6752-6758, 6801-6807`
  `log.warning("ignoring %s settings written in version %r; this audian writes version %d", <KEY>, version, <KEY>_VERSION)` at databrowser.py:3688-3694 (labels), 5173-5179 (annotations), 5719-5725 (parameter-tab), 6752-6758 (panel-split), 6801-6807 (spectrogram-band). There is no shared helper; a sixth key would be a sixth copy.
- **QSettings is the SECOND persistence mechanism and holds exactly one key: "spectrogram/colormap".**
  `src/audian/databrowser.py:18, 1465, 7421`
  Only three QSettings references exist in src/: the import at databrowser.py:18, the read at databrowser.py:1465, the write at databrowser.py:7421. `QSettings("audian", "audian")` = organization "audian", application "audian". Verified with .venv-qt6/bin/python (PySide6 6.11.2): fileName() = /home/weygoldt/.config/audian/audian.conf, allKeys() = ['spectrogram/colormap']. The live file contains `[spectrogram]` / `colormap=0`. No QSettings organization/application name is ever set on the QApplication, so a bare QSettings() elsewhere would NOT find this store.
- **read_color_map_setting() is a staticmethod called from DataBrowser.__init__, so every browser constructed reads audian.conf.**
  `src/audian/databrowser.py:1462-1474`
  Full body: `settings = QSettings("audian", "audian")` / `try: index = int(settings.value("spectrogram/colormap", theme.DEFAULT_SPECTROGRAM_MAP))` / `except (TypeError, ValueError): index = theme.DEFAULT_SPECTROGRAM_MAP` / `if index < 0 or index >= len(theme.spectrogram_maps()): index = theme.DEFAULT_SPECTROGRAM_MAP` / `return index`. Call site: `self.color_map = self.read_color_map_setting()` at databrowser.py:1437. No version guard of any kind.
- **set_color_map() writes to QSettings unconditionally, including from apply_theme() — so every theme toggle rewrites audian.conf once per open browser.**
  `src/audian/databrowser.py:7408-7423, 2785`
  `QSettings("audian", "audian").setValue("spectrogram/colormap", self.color_map)` is the last statement before the signal dispatch. Callers: the ColorMapCombo signal at databrowser.py:2364, color_map_cycler (Shift+C) at databrowser.py:7425-7426, Audian's cross-tab fan-out at audian.py:3604-3607 (`b.set_color_map(cm, False)`), and apply_theme() at databrowser.py:2785 (`self.set_color_map(self.color_map, dispatch=False)`; apply_theme is defined at databrowser.py:2744).
- **The stored colormap is an INDEX into a theme-dependent list, so the same stored number means a different colormap under each theme.**
  `src/audian/theme.py:2617-2632, 2543-2585`
  theme.spectrogram_maps() returns SPECTROGRAM_MAPS_LIGHT (5 entries: CET-L17, CET-L18, CET-L19, CET-L1, CET-CBL2) under THEME_LIGHT and SPECTROGRAM_MAPS (8 entries: CET-CBL2, viridis, magma, inferno, CET-L16, CET-L17, CET-L1, CET-R4) otherwise. Index 3 is "inferno" under dark and "CET-L1 greyscale" under light; an index of 5-7 saved under dark is silently clamped to DEFAULT_SPECTROGRAM_MAP (0) by read_color_map_setting when the app next starts in light.
- **Cache dir holds RecentFiles: ~/.cache/audian/recent.json, max 10 entries, no version field.**
  `src/audian/audian.py:557-604`
  class RecentFiles at audian.py:557, `max_entries = 10` (560), `file_name = "recent.json"` (561), `path()` returns `audian_dirs.user_cache_path / self.file_name` (567-568), load() at 570-582 (drops anything that is not a list of dicts with a "path" key, truncates to max_entries, never raises), save() at 584-590 (mkdir + json.dump indent=2, swallows OSError). Entry shape: {"path", "name", "parent", "channels", "duration", "rate"} (add() at 592-604). Constructed once at audian.py:1594; fed by Audian.remember_file at audian.py:4820-4828.
- **Cache dir also holds the full-trace overview cache: fulltraces.json plus NNNNNNNN-fulltrace.wav and .stats.npy sidecars.**
  `src/audian/compresseddata.py:109-110, 128-137, 429-484, 483-574`
  `fulltraces_file = "fulltraces.json"`, `max_files = 1000` (compresseddata.py:109-110). save_data() at compresseddata.py:429-484 writes the index {ft_name: {"first", "last", "rate", "created", "used"}} with json.dump indent=4, LRU-evicts by the "used" timestamp, then write_audio()s the overview and save_stats()es a `<name>.stats.npy` (stats_path at compresseddata.py:128-137). load_data() at compresseddata.py:483-574 reads the index, prunes stale/too-coarse entries and rewrites the index. A second copy of the overview may also be written BESIDE the recording as `<stem>-fulltrace.wav` by save_data_local() (compresseddata.py:417-427).
- **Live on-disk state confirms the split: config holds settings.json + audian.conf; cache holds recent.json, fulltraces.json, the overview wav and its .npy.**
  `src/audian/audian.py:918, src/audian/audian.py:568`
  `ls ~/.config/audian` → audian.conf (25 B), settings.json (955 B). `ls ~/.cache/audian` → 00000001-fulltrace.wav, 00000001-fulltrace.wav.stats.npy, fulltraces.json, recent.json. The live settings.json contains exactly the six keys: theme, labels, annotations, parameter-tab, panel-split, spectrogram-band.
- **Nothing persists window geometry or toolbar state.**
  `src/audian/audian.py:1`
  grep for saveGeometry|restoreGeometry|saveState|restoreState over src/ and tests/ returns nothing. Qt's usual QMainWindow state persistence is entirely absent.
- **Nothing reads configuration from environment variables.**
  `src/audian/audian.py:1`
  grep for os.environ|getenv over src/audian/*.py returns nothing. Every persisted value comes from one of the two config files; every non-persisted one from a CLI flag or a hard-coded default.
- **Spectrogram nfft and overlap_frac are hard-coded constructor defaults with NO persistence and no CLI flag.**
  `src/audian/bufferedspectrogram.py:62-69, src/audian/plugins.py:13`
  `def __init__(self, name="spectrogram", source="filtered", panel="spectrogram", nfft=256, overlap_frac=0.5)`. The one construction site is `browser.add_trace(BufferedSpectrogram())` in plugins.py:13, always with the defaults. The reader changes them through the parameter bar (nfftw combo at databrowser.py:2329-2341, ofracw at 2343-2355) and via update_resolution/set_resolution (databrowser.py:7300-7346), and the choice dies with the session. This is exactly the gap todo.md names.
- **Filter cutoffs come only from the CLI and are not persisted.**
  `src/audian/audian.py:4979-5054`
  argparse dests in audian_cli: verbose, channels, highpass_cutoff, lowpass_cutoff, load_kwargs, unwrap, unwrap_clip, events_path, theme. Only `theme` has a persistence counterpart, and even that is read-only from the CLI side (audian.py:5105).
- **tests/conftest.py provides NO settings or cache isolation — it only handles Qt teardown, the scoped-enum gate and pyqtgraph's mouseRateLimit.**
  `tests/conftest.py:1-135`
  The single session fixture `_qt_teardown` closes top-level widgets and drains DeferredDelete. There is no autouse fixture pointing settings_path, QSettings or audian_dirs anywhere. Every module invents its own isolation.
- **Seven test modules redirect settings_path; the technique is either monkeypatch.setattr or a raw module-attribute write with manual restore.**
  `tests/test_annotationpanel.py:146-166`
  monkeypatch.setattr(audian_app, "settings_path", lambda: path) in tests/test_annotationpanel.py:147-166 (fixture `scratch_settings`, which also drains the pending QTimer with processEvents on teardown) and tests/test_joinmarkers.py:56-75 (identical copy). Raw assignment + saved `original` + manual restore in tests/test_actioninventory.py:97-121, tests/test_shutdown.py:75-101, tests/test_panelsplitter.py:118/139-157. tests/test_smoketest.py:47 re-registers the real function with monkeypatch so the harness's own overwrite is undone.
- **Only four modules also isolate the QSettings store, and they do it by moving Qt's global search path for both formats and both scopes.**
  `tests/test_shutdown.py:75-101, tests/test_panelsplitter.py:119-157`
  The idiom is `home = Path(QSettings("audian","audian").fileName()).parent.parent` then `for fmt in (NativeFormat, IniFormat): for scope in (UserScope, SystemScope): QSettings.setPath(fmt, scope, os.fspath(directory))`, restored to `home` on teardown. Present in tests/test_shutdown.py:76-101, tests/test_actioninventory.py:98-121, tests/test_panelsplitter.py:119-157, tests/test_smoketest.py:48-52. The comment explains why the whole store is moved rather than the one key: "whatever reaches for QSettings tomorrow lands there too".
- **tests/test_controlpanel.py redirects settings_path with a bare module-attribute write, never restores it, and never redirects QSettings.**
  `tests/test_controlpanel.py:520`
  `audian_app.settings_path = lambda: directory / "settings.json"` with no `original` saved and no teardown restore (the fixture's teardown at tests/test_controlpanel.py:530-538 only closes the window). It is the only settings_path redirect in the suite with no matching restore, and the only module that builds a real Audian window without also moving the QSettings path — so the colormap write at databrowser.py:7421 reaches the user's real ~/.config/audian/audian.conf during that test.
- **scripts/smoke_test.py has a redirect_persistence(scratch) helper that is the only place in the repo enumerating BOTH stores, and its docstring is the written record that there are two.**
  `scripts/smoke_test.py:235-264`
  `A.settings_path = lambda: scratch / "settings.json"` then `QSettings.setDefaultFormat(IniFormat)` and setPath over both formats × both scopes. Docstring: "There are TWO, not one, and redirecting only the first is how this harness came to claim more than it did… `QSettings("audian", "audian")` -- Qt's own store, at ``~/.config/audian/audian.conf``, which `settings_path` never covered." It also records that a previous run clobbered the user's own preferences.
- **tests/test_smoketest.py exists solely to assert that the smoke harness isolates both stores.**
  `tests/test_smoketest.py:1-88`
  Three tests: the JSON redirect (settings_path() == tmp_path/"settings.json"), the QSettings redirect (fileName() moves under tmp_path), and an end-to-end write through both channels asserting both "settings.json" and "audian.conf" appear under tmp_path. Module docstring: "It said so and covered ONE of the two stores they persist through."
- **NOTHING in the test suite isolates audian_dirs.user_cache_path — grep for user_cache_path matches only src/audian/compresseddata.py and src/audian/audian.py.**
  `src/audian/audian.py:568, src/audian/compresseddata.py:434`
  Proven on disk: the user's real ~/.cache/audian/recent.json currently contains entries for /tmp/pytest-of-weygoldt/pytest-47/split160/rec.wav, /tmp/pytest-of-weygoldt/pytest-47/split20/rec.wav and similar — pytest tmp recordings written into the reader's own recent-files list by a test run.
- **Settings coverage by tests is uneven: "annotations", "panel-split" and "spectrogram-band" have dedicated tests; "labels", "parameter-tab" and "spectrogram/colormap" have almost none.**
  `tests/test_annotationpanel.py:568-694, tests/test_panelsplitter.py:876-1035`
  tests/test_annotationpanel.py:568-694 covers the one-key/one-write rule, save+restore, unknown layers, missing layers, the unsaved master, the no-bundle case and the version-mismatch drop. tests/test_panelsplitter.py:876-1035 covers the split write, the null default, cross-channel-count restore and unreadable values; tests/test_panelsplitter.py:2101-2135 has stored_band/write_raw_band/clear_raw_band helpers for the band. "parameter-tab" has exactly one assertion (tests/test_parameterbar.py:374-387). "labels" (LABEL_SETTING) and the QSettings colormap key have no round-trip test at all outside test_smoketest's isolation check.
- **theme.apply() is the single entry point for the whole design system and forces the Fusion style — the mechanism feature A must displace.**
  `src/audian/theme.py:2509-2534`
  Order documented in the docstring: 1. Fusion style ("the only cross-platform style that honours a custom QPalette under the Wayland platform theme"), 2. app.setPalette(palette()), 3. app.setFont(font_ui()), 4. apply_pg_config() (which pushes background=token("bg.plot"), foreground=token("fg.muted"), antialias=False), 5. app.setStyleSheet(stylesheet()). "There is exactly one call site in the code base: audian.py's audian_cli" — though set_app_theme (audian.py:1661) and the tests call it too.
- **The label CSV sidecar is a fourth persistence channel, deliberately NOT in settings, and is the only one with an atomic write and a user-visible error.**
  `src/audian/labels.py:108-111, src/audian/databrowser.py:3779-3808`
  sidecar_path(recording) = recording.with_name(recording.stem + SIDECAR_SUFFIX) at labels.py:108-111. Writes go through a tmp file (labels.py:704). save_labels() at databrowser.py:3791-3808 notifies the reader on failure, with the explicit contrast: "`save_setting` swallowing an OSError costs a preference, this one would cost their work." Debounced by schedule_label_save()/QTimer.singleShot(0) at databrowser.py:3779-3789 and flushed at close by flush_labels() (called from audian.py:4923).

### Risks

- Two config stores in the same directory. Feature B will want to move "spectrogram/colormap" out of ~/.config/audian/audian.conf into settings.json. That is a one-time migration with no version field on the QSettings side to hang it off — read_color_map_setting (databrowser.py:1462-1474) has no version guard at all. A migration must read the old QSettings value, write it into settings.json, and ideally remove the key, or a user downgrading gets two disagreeing sources. Also note nothing sets QApplication organizationName/applicationName, so the store is only reachable via the explicit QSettings("audian","audian") constructor.
- The stored colormap is an INDEX into a list whose length and contents depend on the active theme (theme.py:2617-2622; dark has 8 entries, light has 5). Moving it to settings.json without changing the representation preserves the bug that index 3 is inferno under dark and greyscale under light, and that indices 5-7 saved under dark are silently clamped to 0 when read under light. Feature A (following the system theme) makes this worse: if the theme can flip with the OS at any moment, the reader's colormap changes under them with no gesture. Storing the map NAME rather than the index is the fix, and it needs a SETTING_VERSION.
- save_setting() is a non-atomic whole-file read-modify-write with no in-process cache (audian.py:935-944). Every new key added for feature B multiplies the rewrite traffic: nfft and overlap change on key auto-repeat (R/⇧R, O/⇧O at databrowser.py:2331, 2349), so they MUST get the QTimer.singleShot(0) debounce that schedule_annotation_save uses (databrowser.py:5221-5231), not a naive save-on-change. And an interrupted write truncates settings.json outright, losing every key including the reader's label vocabulary — labels.py:704 already shows the tmp+rename pattern that settings.py lacks.
- Multi-tab write races. Several browsers can be open at once and each is a full DataBrowser reading and writing the same settings.json. The existing code works around this per key: save_parameter_tab uses the _param_tab_saved memo so a fresh browser does not write its own default (databrowser.py:5766-5776), and Audian.set_spectrogram_band passes save=b is current so only one tab writes (audian.py:3326). Any new persisted parameter (nfft, cmap, overlap) fans out across tabs the same way — see Audian.set_color_map's fan-out at audian.py:3604-3607 — and needs the same "only the tab the reader touched writes" rule, or the last tab to be walked wins.
- There is no shared version-guard helper: the identical log.warning block is copy-pasted five times (databrowser.py:3688, 5173, 5719, 6752, 6801). Adding three or four more settings keys the current way triples that duplication. The two existing policies also conflict — PANEL_SPLIT drops an old version, SPEC_BAND migrates version 1 — so a new helper has to support both, not just one.
- No test isolates audian_dirs.user_cache_path, and the user's real ~/.cache/audian/recent.json already contains /tmp/pytest-of-weygoldt/... entries. Any new persisted state placed in the cache dir, or any new test that opens a file, keeps polluting the reader's own machine. Worse, if feature B moves anything from cache to config, the test suite has no fixture that would catch a write escaping the sandbox.
- tests/test_controlpanel.py:520 sets audian_app.settings_path with a raw module-attribute write and never restores it, and never redirects QSettings. It builds a real Audian window, so it writes the reader's real audian.conf today. If feature B routes the colormap through settings.json, that leak moves — a test that then writes settings.json outside the sandbox would clobber the six real keys. The isolation needs to become one autouse conftest fixture covering settings_path, QSettings.setPath and audian_dirs before new keys are added.
- Feature A must not lose the persisted preference. "theme" is a bare string with no version wrapper (audian.py:1684, read at 5105) and only two legal values. Adding a third state ("system"/follow-native) changes the value domain of a key that has no version field to guard it — an older audian reading "system" falls into the `if theme_name not in (THEME_DARK, THEME_LIGHT)` branch at audian.py:5106 and silently opens dark, which is survivable but silent. Wrapping "theme" in the versioned {"version": n, ...} shape the other five keys use is the consistent move, and it needs a read that still accepts the bare string.
- theme.apply() forces the Fusion style, a full custom QPalette, a custom app font, pyqtgraph background/foreground tokens and a whole application stylesheet (theme.py:2509-2534). Following the native system theme means giving up the Fusion pin — whose comment says it is "the only cross-platform style that honours a custom QPalette under the Wayland platform theme" — so a native-theme mode has to either stop setting a palette or accept that the palette will not take. The stylesheet is applied LAST and "wins over the palette where they overlap", so any token still hard-coded in stylesheet() will override system colors even after the palette is dropped.
- The spectrogram colormaps are chosen per theme for a reason that survives feature A: REVERSED_MAPS (theme.py:2597-2603) flips maps so the noise floor matches the page. If the theme can now follow the system and flip at runtime, apply_theme() (databrowser.py:2744) already re-pushes the colormap at databrowser.py:2785 — and that call currently WRITES to QSettings, so a system-driven theme flip would silently rewrite the reader's stored colormap. That write needs to become read-only on the theme path regardless of which store the key ends up in.

## params-inventory

## Persistence backends that exist today

audian has **two** stores plus a cache file, and neither covers the parameters feature B names.

1. **`settings.json`** — `audian_dirs.user_config_path / "settings.json"` (`src/audian/audian.py:912-918`, dirs from `PlatformDirs("audian","janscience")` at `src/audian/version.py:13`). Read whole-file by `settings()` (`audian.py:921-932`, never raises, broken file reads `{}`); written one key at a time by `save_setting(key, value)` (`audian.py:935-944`) which **reads, updates and rewrites the entire file**. Six keys exist: `"theme"` (`audian.py:1684`), `"annotations"` (`databrowser.py:1004`), `"labels"` (`databrowser.py:1014`), `"parameter-tab"` (`databrowser.py:1037`), `"panel-split"` (`databrowser.py:1062`), `"spectrogram-band"` (`databrowser.py:1078`). Every value is a dict carrying a `"version"` and is dropped whole with a `log.warning` on mismatch (`databrowser.py:5167-5180`, `6746-6758`, `5715-5727`, `6793-6810`, `3683-3695`).
2. **`QSettings("audian","audian")`** — exactly one key, `"spectrogram/colormap"`, read at `databrowser.py:1463-1474` and written at `databrowser.py:7421`. So **the cmap *is* persisted today**, just in the wrong store and as a theme-relative integer.
3. **`recent.json`** in `user_cache_path` (`audian.py:561,568,586`) and `fulltraces.json` (`compresseddata.py:109,434-484`) — cache, not preferences.
4. Window geometry is deliberately **not** persisted (`audian.py:1561-1562`).

## Not persisted, plausibly should be

**Spectrogram** — `nfft` (default 256, `bufferedspectrogram.py:67,77`), `overlap_frac` (0.5, `bufferedspectrogram.py:68,79`). Both live on the `BufferedSpectrogram` instance built fresh by `plugins.default_setup_traces` (`plugins.py:11-13`) on every browser. FFT **window function and detrend are not exposed at all** — `spectrogram()` is called with only `n_fft`/`n_overlap` (`bufferedspectrogram.py:139-146`) while thunderlab defaults `window='hann', detrend='constant'`. `windowing.py` is *not* about FFT windows; it is annotation view-windowing (`windowing.py:1-44`).

**Colormap** — persisted but in QSettings, and the stored int indexes `theme.spectrogram_maps()`, which returns **8 names in dark** (`theme.py:2543-2557`) and **5 in light** (`theme.py:2582-2589`). The same integer means a different map per theme, and `read_color_map_setting` clamps against whichever theme is active at browser construction (`databrowser.py:1472-1473`).

**Level mapping / dynamic range** — no persisted state and no parameter-bar widget; the `ColorBarItem` is `interactive=True` (`spectrogramplot.py:175-186`) and the panel refits automatically from `LEVEL_FLOOR_MARGIN_DB=3.0`, `MIN/MAX_LEVEL_SPAN_DB=20/80`, `LEVEL_FIT_SAMPLES=200_000` (`spectrogramplot.py:133-155`, fit at `485-547`) plus `NOISE_FLOOR_MARGIN_DB=3.0` (`bufferedspectrogram.py:41`).

**Filter** — `highpass_cutoff`, `lowpass_cutoff`, `filter_order` (`bufferedfilter.py:43-45`); `BufferedFilter.open()` **resets all three unconditionally** (`bufferedfilter.py:48-54`). `filter_order` has no widget. `link_band` (`databrowser.py:1293`).

**Envelope** — `envelope_cutoff=500`, `filter_order=2`, `highpass_cutoff=0` (`bufferedenvelope.py:23-25,39-41`). `BufferedEnvelope` is instantiated **nowhere in `src/`** — the default trace set is filter + spectrogram only (`plugins.py:11-13`), so the Envelope group only appears with a user plugin (`databrowser.py:2441-2478`).

**Panel visibility** — `show_traces=True`, `show_specs=0`, `show_powers=False`, `show_cbars=True`, `show_fulldata=True`, `mean_spec=False` (`databrowser.py:1230-1238`). Spectrograms are **off on a fresh start**. Within a session a new tab inherits them from the previous browser when `link_panels` (`audian.py:4788-4795`), so this is session-global state that dies on exit.

**Also unpersisted**: y-range policy `y_mode` (`databrowser.py:1333`), grid mode `grids` (`1229`), `cross_hair` (`1338`), rail visibility `rail_visible` (`1300`), `region_mode` (`1221`), the whole audio group (`1190-1192`, `1253-1255`), time-axis `starttime_mode` (`audian.py:1634`), and the eight cross-tab `link_*` switches (`audian.py:1538-1547`). Default time window is a hardcoded `min(10, tmax)` seconds (`timeplot.py:370-376`).

## Command line

`audian_cli` (`audian.py:4968-5057`) defines: `--version`, `-v/--verbose`, `-c CHANNELS`, `-f FREQ` (highpass), `-l FREQ` (lowpass), `-i KWARGS`, `-u/-U UNWRAP`, `-a/--events BUNDLE`, `--theme {dark,light}`, `files`. **Only `--theme` has a precedence rule today** — `args.theme or settings().get("theme", theme.THEME_DARK)` (`audian.py:5105`), i.e. CLI wins for the run and does not overwrite the stored value. `-f`/`-l` are applied inside `DataBrowser.open` after the loader and after `BufferedFilter.open()` has already reset them (`databrowser.py:1759-1770`); `-c` becomes `self.schannels` consumed at `databrowser.py:1774-1783`.

### Facts

- **settings.json is the config-dir preferences file; save_setting rewrites the whole file for one key**
  `src/audian/audian.py:912-944`
  settings_path() = audian_dirs.user_config_path / 'settings.json' (audian.py:912-918); settings() reads it and returns {} on any OSError/ValueError (921-932); save_setting(key, value) calls settings(), mutates one key, mkdirs and json.dumps the whole dict (935-944). Consequence for feature B: every additional top-level key is another whole-file rewrite, which is exactly why the existing five keys are one blob per feature and why saves are debounced onto QTimer.singleShot(0, ...).
- **The spectrogram colormap IS already persisted, but in QSettings and not in settings.json**
  `src/audian/databrowser.py:1463-1474, 7421`
  read_color_map_setting() reads QSettings('audian','audian').value('spectrogram/colormap', theme.DEFAULT_SPECTROGRAM_MAP) and clamps it into range(len(theme.spectrogram_maps())); set_color_map() writes it back on every call. Two stores for one preference set is the first thing feature B has to reconcile; a migration must read the old QSettings key once.
- **The stored colormap index means a different map in each theme**
  `src/audian/theme.py:2543-2589, 2617-2632`
  theme.spectrogram_maps() returns SPECTROGRAM_MAPS (8 entries, dark) or SPECTROGRAM_MAPS_LIGHT (5 entries, light). A reader who picks index 6 ('CET-L1') in dark and switches to light gets it clamped to DEFAULT_SPECTROGRAM_MAP=0 by read_color_map_setting/set_color_map. Persist the map NAME, or key it per theme.
- **set_color_map writes the setting on every call, including the one apply_theme makes**
  `src/audian/databrowser.py:2783-2785, 7408-7423`
  DataBrowser.apply_theme() ends with self.cmapw.populate(); self.set_color_map(self.color_map, dispatch=False) (databrowser.py:2783-2785), and set_color_map unconditionally does QSettings(...).setValue('spectrogram/colormap', self.color_map). So a theme toggle rewrites the stored colormap. If this becomes a settings.json write it becomes a full-file rewrite per theme toggle, per open tab.
- **Spectrogram nfft: default 256, widget is a fixed power-of-two combo, setter is debounced 200 ms**
  `src/audian/bufferedspectrogram.py:67,77; src/audian/databrowser.py:2329-2338,7300-7373`
  Default nfft=256 (bufferedspectrogram.py:67,77). Widget self.nfftw is a QComboBox filled with 2**i for i in range(3,20), i.e. 8..524288, itemData carrying the int (databrowser.py:2329-2338). Path to apply: update_resolution(nfft=) stashes into self.pending_nfft and starts a 200 ms timer (7300-7319) -> apply_resolution (7321-7330) -> set_resolution (7331-7373) -> request_recompute(spectrogram, nfft=...) (1597-1615) -> BufferedSpectrogram.prepare_update (191-214).
- **nfft's legal upper bound is per-file, so a globally stored nfft must be re-clamped on every open**
  `src/audian/bufferedspectrogram.py:191-201`
  prepare_update clamps nfft into [8, min(len(self.source)//2, 2**30)]. A 524288-sample window stored from a long recording silently becomes something else on a short one, and set_nfft_widget will then not find it.
- **set_nfft_widget silently no-ops when the value is not one of the combo entries**
  `src/audian/databrowser.py:2733-2739`
  index = self.nfftw.findData(nfft); if index < 0: return. A restored or clamped nfft that is not a power of two in 8..524288 leaves the combo showing a different number than the data is using, with no warning.
- **Overlap fraction: default 0.5, slider is integer percent 0..99, and the value is snapped by the hop**
  `src/audian/bufferedspectrogram.py:68,79,174-185,202-207; src/audian/databrowser.py:2340-2349`
  overlap_frac default 0.5 (bufferedspectrogram.py:68,79). Widget self.ofracw is a QSlider(0,99) emitting 0.01*v (databrowser.py:2340-2349). prepare_update clamps to [0.0, 0.99999] (202-207) and set_hop() then rewrites overlap_frac as 1 - hop/nfft with hop = round((1-overlap_frac)*nfft) clamped to [1, nfft] (174-185). So overlap depends on nfft: a restore must set both in one set_resolution call or the snapped value will differ.
- **There is no FFT window-function or detrend setting anywhere in audian**
  `src/audian/bufferedspectrogram.py:139-146`
  BufferedSpectrogram.process calls thunderlab.powerspectrum.spectrogram(source[lo:hi], rate, freq_resolution=None, overlap_frac=None, n_fft=self.nfft, n_overlap=self.nfft-self.hop) — window and detrend are never passed, so the library defaults window='hann', detrend='constant' apply. Adding a window-function preference is a new feature, not a persistence gap.
- **windowing.py is not about FFT windows**
  `src/audian/windowing.py:1-44`
  Its docstring: 'Turning a whole session's annotation arrays into what one view shows' — points, spans and hold-forward steps for the visible time range. Nothing in it touches the spectrogram.
- **The spectrogram opening frequency band is already persisted, versioned, and migrated — it is the model feature B should copy**
  `src/audian/databrowser.py:1078-1103, 6772-6863, 2381-2432`
  SPEC_BAND_SETTING='spectrogram-band', version 2 = {'version':2,'min_hz':float|None,'max_hz':float|None} in ABSOLUTE Hz (databrowser.py:1078-1103). Read by spectrogram_band() which clamps each end to this recording's Nyquist and drops a floor >= the ceiling (6772-6820); _band_value validates a hand-edited file (6825-6836); save_spectrogram_band writes an end still at its limit as null so an 8 kHz file cannot cap a 96 kHz one (6838-6863). Version 1 is migrated rather than dropped. Widgets fminw/fmaxw are pg.SpinBox on the 'Opens at' row (2381-2432).
- **Applying a restored spectrogram band has two entry points with different safety**
  `src/audian/databrowser.py:1749-1757, 2648-2711`
  At open, before set_limits: plot_ranges[s].set_default_max/min for every s in Panel.frequencies (databrowser.py:1749-1757) — this is the only place the band becomes the opening span. After open: set_spectrogram_band(min,max,save) (2648-2711), which early-returns if self.setting is set or if the spectrogram trace is absent (2678-2681) and must NOT call plot_ranges.set_limits() (it would reset the amplitude range to (-1,1) and destroy auto_fit_y's work).
- **Filter cutoffs are reset unconditionally by BufferedFilter.open(), so a restored value must be applied after the file is loaded**
  `src/audian/bufferedfilter.py:43-54; src/audian/databrowser.py:1759-1770`
  open(): highpass_cutoff = 0; lowpass_cutoff = self.rate/2; filter_order = 2; sos = None; update(). Instance defaults are highpass 0, lowpass 1, order 2 (43-45). DataBrowser.open applies the CLI values in exactly the right slot, immediately after self.data.open (databrowser.py:1759-1770); a restored preference belongs there too. filter_order has no widget anywhere.
- **Filter setter path and its per-file bounds**
  `src/audian/databrowser.py:2246-2300, 7428-7483`
  Widgets: hpfw/lpfw pg.SpinBox plus hpsliderw/lpsliderw LogSlider, bounds (0, nyquist) and (0.01*nyquist, nyquist) (databrowser.py:2246-2300). update_filter() stashes pending_highpass/pending_lowpass, applies the link_band coupling and starts a 200 ms timer (7428-7460) -> apply_filter() assigns onto the trace, pushes the spectrogram handles and widgets, and calls request_recompute(filtered) (7462-7483). apply_filter early-returns if 'filtered' not in data. Cutoffs are absolute Hz, so a stored value needs the same Nyquist clamp the spectrogram band already documents.
- **link_band (Linked band) is a user toggle with no persistence**
  `src/audian/databrowser.py:1293, 2303-2312, 2741-2742`
  self.link_band = False (databrowser.py:1293); QToolButton self.linkbandw wired to set_link_band (2303-2312, 2741-2742); read in update_filter to move both cutoffs together (7446-7459).
- **Envelope parameters exist but no envelope trace is created by default**
  `src/audian/bufferedenvelope.py:23-25,39-49; src/audian/plugins.py:11-13`
  BufferedEnvelope defaults envelope_cutoff=500, filter_order=2, highpass_cutoff=0 (bufferedenvelope.py:23-25, 39-41) and its open() does NOT reset them (44-49), unlike the filter. plugins.default_setup_traces adds only BufferedFilter and BufferedSpectrogram, and BufferedEnvelope is instantiated nowhere else in src/ — only mentioned in README.md:143 and docs/qt6/recon/data-layer.md:84. So the Envelope parameter group (databrowser.py:2441-2478, bounds (0, 0.5*nyquist)) is reachable only with a user plugin.
- **Envelope setter path is debounced the same way**
  `src/audian/databrowser.py:7497-7541`
  update_envelope(envelope_cutoff=) stashes pending_envelope and starts a 200 ms timer (7497-7520); apply_envelope assigns onto the trace, calls data.set_need_update() and request_recompute(envelope), and syncs envfw/envsliderw with blocked signals (7524-7541). Both early-return if 'envelope' not in data.
- **The dB level mapping is fitted, not stored, and its tuning constants are hardcoded**
  `src/audian/spectrogramplot.py:133-155,175-186,485-570; src/audian/bufferedspectrogram.py:41`
  SpectrogramPlot.MIN_LEVEL_SPAN_DB=20.0, MAX_LEVEL_SPAN_DB=80.0 (133-134), LEVEL_FLOOR_MARGIN_DB=3.0 (149), LEVEL_FIT_SAMPLES=200_000 (155). fit_levels/_level_range compute floor = median(dB)+margin, top = 95% of the way to the peak, snapped to 5 dB (485-547); _apply_levels pushes it through PlotRanges (549-570). The ColorBarItem is interactive=True with limits (-200,20) (175-186) so the reader can drag the handles, and nothing records the result. BufferedSpectrogram.NOISE_FLOOR_MARGIN_DB=3.0 is the parallel constant for the start-up estimate (bufferedspectrogram.py:41, used 256 and 287).
- **Level fitting requires loaded data and only one panel owns it**
  `src/audian/spectrogramplot.py:426-478, 485-501, 601-611`
  fit_levels returns False when self.spec_data is None or when this is not the first visible lane (fits_levels(), 454-478). setVisible() re-arms the fit (426-452) and setZRange consumes the re-arm through a zero-delay timer (601-611). So a restored dB range would have to be written through _apply_levels/PlotRanges after open and would be overwritten by the next automatic fit unless _levels_fitted is set.
- **The parameter-bar tab is persisted by group NAME with a version bump when the names change**
  `src/audian/databrowser.py:1027-1041, 2589, 5711-5784`
  PARAM_TAB_SETTING='parameter-tab', PARAM_TAB_SETTING_VERSION=2 (bumped when the tabs were renamed to FIXED_TAB/EDITABLE_TAB) (databrowser.py:1027-1041). restore_parameter_tab() falls back to index 0 on an unknown name and is called at the end of setup_parameter_bar (5729-5744, called at 2589); save_parameter_tab writes only on a reader's pick, never on restore or build (5766-5784).
- **The trace/spectrogram panel split is persisted as one dimensionless number, read in __init__ before any figure exists**
  `src/audian/databrowser.py:1043-1076, 1216, 6730-6770, 6865-6890`
  PANEL_SPLIT_SETTING='panel-split', version 3, value {'version':3,'scale': float|None} where scale is spec_scale = spectrogram row over theme.SPECTROGRAM_MIN_HEIGHT (databrowser.py:1043-1076). restore_panel_split() is called from __init__ (1216) and clamps to [0.01, 100.0] (6730-6770); save_panel_split writes null when the split is still on its default (6865-6890). Version 1 stored the trace-over-spectrogram ratio and is deliberately dropped — the docstring records the measured failure.
- **Panel visibility (traces, spectrograms, power, colorbars, navigator, mean) is not persisted at all**
  `src/audian/databrowser.py:1229-1238, 7784-7843, 7845-7945`
  Defaults show_traces=True, show_specs=0, show_powers=False, show_cbars=True, show_fulldata=True, mean_spec=False (databrowser.py:1229-1238) — note spectrograms are OFF on a fresh start. set_panels(traces, specs, powers, cbars, fulldata) is the apply path (7784-7843); it needs self.panels, self.plot_ranges and self.datafig, so it is only safe after DataBrowser.open. Toggles: toggle_traces 7845, toggle_spectrograms 7871, set_mean_spectrogram 7900-7922, toggle_colorbars 7936, toggle_powers 7940, toggle_fulldata 7944.
- **Panel visibility survives within a session by copying from the previous tab, then dies on exit**
  `src/audian/audian.py:4786-4805`
  When a new browser is added, if self.link_panels the window pushes pb.show_traces/show_specs/show_powers/show_cbars/show_fulldata onto it from the previous browser; otherwise it calls set_panels() with no arguments. Same shape for set_channels and set_region_mode.
- **Amplitude y-axis policy is a three-way mode with no persistence**
  `src/audian/databrowser.py:1105-1109, 1333-1334, 3218-3286`
  y_modes = ['shared','per-channel','fixed +-1'] as y_shared=0/y_per_channel=1/y_fixed=2 (databrowser.py:1105-1109); default self.y_mode = y_shared, self.y_locked = False (1333-1334). set_y_mode(mode) sets the flag then either forces every amplitude range to (-1.0, 1.0) or calls auto_fit_y(force=True) (3218-3231) — it touches plot_ranges, so it is only safe after open. The fitted numbers themselves are per-file (auto_fit_y, headroom=0.08 hardcoded, 3232-3286).
- **Audio playback settings: five values, none persisted, one of them per-file**
  `src/audian/databrowser.py:1124-1132, 1190-1192, 1253-1255, 2483-2566, 8118-8215`
  audio_source default AUDIO_SELECTED (1190; constants AUDIO_SELECTED/AUDIO_SHOWN/AUDIO_PAIR 1124-1132, order tuple 8217, labels 8219-8224); audio_left=0 and audio_right=min(1, channels-1) (1191-1192) — PER-FILE, clamped to channels-1 by set_audio_pair (8177-8193); audio_rate_fac=1.0 (1255, combo of 0.1..100 at 2534-2544); audio_use_heterodyne=False (1253); audio_heterodyne_freq=40000.0 (1254, SpinBox bounds (10000,100000), row only built when data.rate > 50000, 2546-2566). Setters: set_audio(rate_fac, use_heterodyne, heterodyne_freq) 8118-8136, set_audio_source 8195-8215.
- **Channel selection state is inherently per-file and is not persisted**
  `src/audian/databrowser.py:1184, 1193-1199, 1774-1790, 7692-7700`
  show_channels (None until open, then all channels or the CLI list, 1184 and 1774-1783), selected_channels (1193, 1786), solo_channels/muted_channels (1195-1196), maximized_channel (1197), channel_order (1198, 1787), channel_names (1199, 1788-1790). Setter set_channels(show_channels, selected_channels, current_channel) early-returns while self.setting is set (7692-7700). A global store must clamp against self.data.channels the way open() and set_audio_pair already do. todo.md records an existing IndexError in set_channels when shown and selected do not intersect.
- **Other unpersisted global view toggles**
  `src/audian/databrowser.py:1113-1121, 1221, 1229, 1300, 1338, 7947-7999`
  grids cycles 0..3 (1229, toggle_grids 7947-7951 -> panels.show_grid); cross_hair False (1338, set_cross_hair 3442-3455); rail_visible True, the F7 channel rail (1300, toggle 2853-2858); region_mode default MODE_ASK=4 (1221, constants MODE_ZOOM..MODE_LABEL 1113-1121, set_region_mode 7973-7999 which also takes the mouse away from the filter handles in MODE_LABEL).
- **The time-axis start-time mode is a window-level preference with no persistence**
  `src/audian/audian.py:1634,3139-3143,4786; src/audian/timeaxisitem.py:167-170`
  Audian.starttime_mode = 0 (audian.py:1634), cycled 0..2 and pushed to every browser (3139-3143), and pushed onto each new tab (4786). DataBrowser.set_starttime_mode forwards to each lane's TimeAxisItem (databrowser.py:2178-2191). Modes degrade gracefully: mode 1 needs a start timestamp and mode 2 needs more than one file, else they fall back to 0 (timeaxisitem.py:167-170).
- **Eight cross-tab link switches are user-tunable and none survive a restart**
  `src/audian/audian.py:1538-1547`
  link_timezoom=True (1538), link_timescroll=False (1539), link_ranges[s]=True for every amplitude/frequency/power axis (1540-1542), link_filter=True (1543), link_envelope=True (1544), link_channels=True (1545), link_panels=True (1546), link_audio=True (1547). Toggles at 3132, 3135, 3347, 3456, 3609, 3616, 3756, 3982.
- **Spectrogram parameters are NOT dispatched across open tabs — the fan-out is an unimplemented stub**
  `src/audian/audian.py:3591-3600`
  Audian.dispatch_resolution() is 'pass' with the whole intended body commented out as a TODO. By contrast dispatch_colormap (3603-3607) and dispatch_filter (3619-3627) do fan out, and dispatch_spectrogram_band uses an explicit per-browser loop so each recording re-clamps against its own Nyquist (databrowser.py:2629-2646).
- **The default opening time window is a hardcoded 10 s**
  `src/audian/timeplot.py:370-376`
  TimePlot.range() for the x axis returns (0, tmax, min(10, tmax)) when there is data and (0, None, 10) when there is not. Nothing lets a reader set it and nothing stores it.
- **Command-line arguments in full**
  `src/audian/audian.py:4968-5061`
  --version (4974); -v/--verbose count (4975-4982); -c CHANNELS comma/dash list (4983-4990, parsed 5072-5081); -f FREQ highpass (4991-4998); -l FREQ lowpass (4999-5006); -i KWARGS loader kwargs, append (5007-5014); -u UNWRAP (5015-5024) and -U UNWRAP unwrap+clip (5025-5034), folded into one value at 5085-5090; -a/--events BUNDLE (5035-5046); --theme {dark,light} (5047-5054); positional files (5055-5061). Unknown args are handed to QApplication (5063, 5095).
- **Only --theme has a documented CLI-versus-settings precedence rule**
  `src/audian/audian.py:5104-5108`
  theme_name = args.theme or settings().get('theme', theme.THEME_DARK), validated against THEME_DARK/THEME_LIGHT, then theme.apply(). The comment states the rule: 'the command line wins for this run; otherwise restore the last choice'. The CLI value is not written back — only Audian.set_app_theme writes 'theme' (audian.py:1684). Every parameter feature B adds needs this same rule stated.
- **CLI filter cutoffs are applied inside DataBrowser.open, after the loader and after BufferedFilter.open() has reset them**
  `src/audian/databrowser.py:1759-1770; src/audian/audian.py:4730-4736`
  if highpass_cutoff is not None: filtered.highpass_cutoff = ...; if lowpass_cutoff is not None: filtered.lowpass_cutoff = ...; then filtered.update() once. They arrive via Audian.__init__ (1490-1493, stored 1519-1520) and browser.open(...) (4730-4736). This is the exact site where a restored preference must also land, and where the CLI-beats-settings comparison has to be made.
- **The label vocabulary is persisted; the current category deliberately is not**
  `src/audian/databrowser.py:1014-1016, 1347, 3697-3724; src/audian/labels.py:757-799`
  LABEL_SETTING='labels' v1 (1014-1016). restore_label_categories() is called from __init__ before any file is open (1347, method 3697-3705), falling back to labels.DEFAULT_CATEGORIES (labels.py:796-799). save_label_settings writes only the categories (3708-3724) with the docstring stating why the current category is excluded. Serialisation round-trip in labels.categories_to_settings / categories_from_settings (labels.py:757-790), which skips a malformed entry rather than defaulting it.
- **Annotation layer and surface switches are persisted, with the F8 master deliberately excluded**
  `src/audian/databrowser.py:1004-1007, 5004, 5182-5259, 5545`
  ANNOTATION_SETTING='annotations' v1 (1004-1007). restore_annotation_surfaces (5182-5190, called 5545) and restore_annotation_layers (5192-5216, called 5004) only touch layers the loaded bundle actually carries. save_annotation_settings (5233-5259) is reached through schedule_annotation_save, a QTimer.singleShot(0, ...) coalescer, because one chip click can move ten switches and save_setting rewrites the whole file (5220-5231).
- **Restore-versus-save discipline the existing code follows: nothing writes at construction**
  `src/audian/databrowser.py:2648-2711, 5766-5784, 6838-6890`
  save_parameter_tab: 'Written on a reader's pick, never on restore and never at build: save_setting reads, updates and rewrites the whole settings file, and a browser that wrote its own default at construction would overwrite the choice made in the window beside it' (5766-5776). save_panel_split and save_spectrogram_band repeat the rule (6865-6879, 6838-6852). set_spectrogram_band takes save=False for every browser but the one whose field was typed in, so the stored value does not depend on the order of Audian.browsers (2648, 2696-2705).
- **Window geometry is explicitly not persisted, by decision**
  `src/audian/audian.py:1561-1570`
  '# window: size is a hint only, nothing is persisted or restored - on a tiling compositor the window manager owns the geometry.' It resizes to 70% of the available screen, or 1280x800 with no screen.
- **Hardcoded tuning constants with no UI at all, candidates for a config file rather than a widget**
  `src/audian/theme.py:502,640,668-669; src/audian/data.py:202-204; src/audian/specitem.py:31,34`
  SpecItem.view_pad=1.5 and pixel_oversample=2 (specitem.py:31,34); BufferedSpectrogram.lookahead_time=0.5 (60) and chunk_columns=128 (114); BufferedFilter.warmup_time=0.5 (17) and chunk_bytes (20); BufferedEnvelope.warmup_time=0.5 (13); Data.buffer_bytes=64MB, min_buffer_time=10.0, max_buffer_time=60.0 (data.py:202-204); DataBrowser.RAIL_WIDTH=48 and MAX_SPECTROGRAM_CHANNELS=4 (databrowser.py:998-999); theme.SPECTROGRAM_MIN_HEIGHT=120 (theme.py:502), COLORBAR_WIDTH=8 (640), SIZE_PT=10 and SIZE_SMALL_PT=9 (668-669).
- **theme.SIZE_PT / SIZE_SMALL_PT are the font sizes feature A would have to hand over to the system**
  `src/audian/theme.py:668-669`
  SIZE_PT = 10 ('base UI size; the app is dense'), SIZE_SMALL_PT = 9 ('in-plot labels, chips; never go below 8'). Every font_ui/font_mono call in the parameter bar and the plots passes one of these explicitly, so 'follow the system font' means changing what these resolve to rather than removing the calls.
- **The two spectrogram colormap lists are theme-specific by design, not by reversal**
  `src/audian/theme.py:2543-2662`
  SPECTROGRAM_MAPS (dark, 8 entries, CET-CBL2 first) at 2543-2557 with labels at 2559-2568; SPECTROGRAM_MAPS_LIGHT (5 entries, near-white low end) at 2582-2589 with labels at 2605-2613; REVERSED_MAPS says which are drawn flipped per theme (2596-2603). DEFAULT_SPECTROGRAM_MAP = 0 (2632). spectrogram_colormap() clamps an out-of-range index and falls back on an unloadable name rather than raising, 'a bad colormap in a config file must not stop the application from opening a file' (2635-2662).
- **Setters that early-return while the range guard is held**
  `src/audian/databrowser.py:7340-7343, 2678-2681`
  set_resolution and set_spectrogram_band both begin 'if self.setting: return' followed by the trace-membership check, and both docstrings record that an early return leaving self.setting set freezes every later scroll and zoom for the session. Restoring values from inside a self.updating() block would therefore silently do nothing.
- **ColorMapCombo repopulates itself per theme and its index is the stored value**
  `src/audian/databrowser.py:623-663, 2783-2785`
  ColorMapCombo.populate() refills from the active theme's list (databrowser.py:642-663) using colormap_icon() swatches (623-639); apply_theme calls populate() then set_color_map(self.color_map, dispatch=False) (2783-2785). Because the list length changes with the theme, the index carried across is not stable.

### Risks

- Two persistence backends already exist for one preference set: settings.json (audian.py:912-944) and QSettings for the colormap alone (databrowser.py:1465, 7421). Unifying them needs a one-time read of the old QSettings key, and the QSettings path is exercised by tests that redirect QSettings.setPath (tests/test_shutdown.py:76-101, tests/test_panelsplitter.py:119-157) — those fixtures will need the settings.json redirect instead.
- The stored colormap is a theme-relative index into an 8-entry dark list or a 5-entry light list (theme.py:2543-2589). Persisting the index unchanged means a theme switch silently changes which map is stored; persist the NAME, or key the value per theme. Feature A (follow the system theme) makes this worse, because the active theme can then change without a deliberate user gesture.
- save_setting rewrites the entire settings.json for one key (audian.py:935-944). Every new top-level key multiplies whole-file rewrites, and multiple tabs/windows write the same file with no locking. Follow the existing pattern: one blob per feature, and coalesce with QTimer.singleShot(0, ...) the way schedule_annotation_save does (databrowser.py:5220-5231).
- BufferedFilter.open() unconditionally resets highpass_cutoff, lowpass_cutoff and filter_order (bufferedfilter.py:48-54). Any restored cutoff applied before or during data.open() is silently discarded. The only correct site is DataBrowser.open at databrowser.py:1759-1770, which is exactly where the -f/-l CLI values already land — so that is also where the CLI-versus-settings precedence has to be decided.
- nfft's legal maximum is min(len(source)//2, 2**30) (bufferedspectrogram.py:191-201) and the combo only offers powers of two 8..524288 (databrowser.py:2333-2335). A restored value outside that is clamped by the data but NOT reflected in the widget, because set_nfft_widget returns silently when findData fails (2733-2739). Snap the restored value to a combo entry before applying it, or the bar and the picture disagree with no warning.
- Every frequency-valued preference (filter cutoffs, envelope cutoff, heterodyne frequency) is absolute Hz and outlives the recording it was typed beside. SPEC_BAND_SETTING already documents the full solution — clamp to this recording's Nyquist on read, write an end still at its limit as null so an 8 kHz file cannot cap a 96 kHz one, and validate a hand-edited file (databrowser.py:1093-1102, 6825-6863). Reusing that discipline is cheaper than rediscovering it per parameter.
- Multi-tab write races: set_spectrogram_band takes save=False for every browser but the one whose widget was touched, precisely because the clamp is per-recording and letting each tab write would make the stored value depend on the order of Audian.browsers (databrowser.py:2648, 2696-2705). Any new persisted parameter that is dispatched across tabs (colormap via dispatch_colormap audian.py:3603-3607, filter via dispatch_filter 3619-3627) needs the same save-flag split.
- Nothing may write a preference at construction time. save_parameter_tab, save_panel_split and save_spectrogram_band all state that a browser writing its own default at build would overwrite the choice made in the window beside it (databrowser.py:5766-5776, 6865-6879, 6838-6852). A naive 'save on every setter call' for nfft/overlap/colormap breaks this, because set_resolution and set_color_map are both called during setup (2589-2590, 1887, 2785).
- update_resolution, update_filter and update_envelope are all debounced by 200 ms timers (databrowser.py:7319, 7460, 7514). Restoring through them means the first recompute lands 200 ms after open and can be cancelled by a scroll in between; restore through set_resolution / apply_filter / apply_envelope directly. Both set_resolution and set_spectrogram_band also early-return while self.setting is held (7340, 2678), so a restore run inside a self.updating() block does nothing at all.
- Channel-shaped settings (show_channels, selected/solo/muted, audio_left/audio_right, mean_spec channel set) are per-file: the channel count and the meaning of channel 3 differ between recordings. Persisting them globally needs the clamp that open() (databrowser.py:1774-1783) and set_audio_pair (8179-8182) already apply, and set_channels has a known IndexError when the shown and selected sets do not intersect (recorded in todo.md).
- The level/dB mapping is refitted automatically whenever the panel becomes visible or the mean-channel set changes (spectrogramplot.py:426-452, 485-501). A restored dB range written through _apply_levels will be overwritten by the next fit unless _levels_fitted is set too, and fit ownership belongs to the first visible lane only (fits_levels, 454-478) — so a restore has to target the right panel.
- Adding a real FFT window-function preference means passing window=/detrend= to thunderlab's spectrogram() (bufferedspectrogram.py:139-146), which changes the transform. The chunked path is pinned bit-identical to a single whole-buffer call by tests/test_chunked_dsp.py; a non-rectangular window choice must not break that equality, and the docstring's measured chunk-size numbers (bufferedspectrogram.py:102-114) were taken with the default window.
- The envelope group only exists when a user plugin adds a BufferedEnvelope — the default trace set is filter + spectrogram only (plugins.py:11-13) and BufferedEnvelope is instantiated nowhere in src/. Persisting envelope settings is dead code for the stock application, and a restored envelope value must be applied defensively behind an 'envelope' in self.data check.
- Every existing settings.json value is a versioned dict dropped whole on mismatch, with a logged warning naming the key (databrowser.py:1004-1007, 1014-1016, 1037-1041, 1062-1076, 1078-1103). New keys that skip the version stamp will make the file half-typed and unmigratable; the SPEC_BAND version-1 migration (6800-6812) shows when carrying a value forward is right and PANEL_SPLIT version 3 (1069-1076) shows when dropping it is.
- Feature A and feature B collide on the colormap and on theme.SIZE_PT/SIZE_SMALL_PT: following the system theme means the active theme can flip without a user gesture, and both the stored colormap index and the ColorMapCombo's contents are theme-dependent (databrowser.py:642-663, 2783-2785). set_color_map already writes the store on every apply_theme pass (7421), so a system-theme change would rewrite a preference nobody touched.
- A settings dialog or preferences window adds a top-level widget, and todo.md records that theme.collect_orphan_widgets can segfault the test suite once the process holds extra top-level widgets (it reparents inside a loop over a snapshot of QApplication.topLevelWidgets()). That fix — a two-pass read-then-reparent with a validity check — should land before, not after, a new window is introduced.

## conventions


