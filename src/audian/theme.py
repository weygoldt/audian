"""audian design system: the single source of truth for the look of the app.

Every colour, font, pen, brush, spacing value and stylesheet in audian lives
here.  Nothing else in the code base may contain a hex literal, a named Qt
colour (``'white'``, ``'grey'``, ``'black'``), an RGB tuple, a pen width
literal, a font family string, a spacing pixel literal or a colormap name.

Direction
---------
Swiss-minimal, dark-first, high information density, zero ornament.  This is a
precision instrument for a scientist, not a consumer app.  Chrome recedes, data
dominates.  No gradients, no drop shadows on data, no rounded-everything.

How to use it
-------------
Import the module, never individual names::

    from . import theme

Then reach for the *named role helpers* (:func:`trace_pen`, :func:`cursor_pen`,
:func:`region_brush`, ...) and the *pyqtgraph appliers* (:func:`style_axis`,
:func:`style_plotitem`, :func:`style_figure`) rather than composing colours by
hand.  A ``polish()`` body should collapse to ``theme.style_plotitem(self)``
plus a couple of role-pen calls.

Layers, from most to least preferred at a call site:

1. ``theme.style_plotitem(plot)`` / ``theme.style_axis(axis)`` -- appliers.
2. ``theme.trace_pen('filtered')`` / ``theme.cursor_pen()`` -- role helpers.
3. ``theme.pen('primary', width=theme.LW_THIN)`` -- low-level constructors.
4. ``theme.PRIMARY`` / ``theme.token('primary')`` -- raw tokens.

Import-time contract
--------------------
The module imports cleanly **without a QApplication**.  All token constants are
plain module-level strings available at import time.  Anything that constructs
a ``QFont``, ``QPalette`` or reads the font database is lazy and memoised.
``QPen``/``QBrush``/``QColor`` objects are built fresh on every call -- they are
cheap, and handing out shared mutable Qt objects invites action at a distance.

Every public function is idempotent and safe to call repeatedly: live
re-theming is a design goal.

Contrast ruling (measured, do not re-derive)
--------------------------------------------
``FG_FAINT`` (``#6B7788``) scores 4.13:1 on ``BG_PLOT``, 3.99:1 on
``BG_SURFACE`` and 3.72:1 on ``BG_RAISED``.  It **fails** the 4.5:1 bar.
Therefore:

* Text that a user reads off the screen -- tick labels included -- uses
  ``FG_MUTED`` (7.69:1 on ``BG_PLOT``).  :func:`style_axis` uses ``FG_MUTED``
  for text, never ``FG_FAINT``.
* ``FG_FAINT`` is reserved for **non-text decoration** (tick marks, dashed
  crosshairs, inactive rules) and for the ``QPalette.Disabled`` colour group.

Verified ratios on ``BG_PLOT``: FG 15.93, FG_MUTED 7.69, PRIMARY 5.87,
ACCENT 9.25, SUCCESS 8.03, DANGER 6.21, TRACE_RAW 11.42, TRACE_FILTERED 12.06,
TRACE_ENVELOPE 7.79.  Run ``python -m audian.theme`` to re-check.

Themes
------
Dark is the default and the one that must be perfect.  A light theme exists as
a straightforward inversion with darkened data-series colours; it is supported,
not lavished upon.  Switch with :func:`set_theme`.  The module-level constants
(``theme.PRIMARY`` and friends) always hold the **dark** reference values; the
helpers resolve through the *active* table, and passing a dark constant into a
helper still maps to the active theme's equivalent.  Prefer helpers.
"""

from __future__ import annotations

import copy

from string import Template
from typing import Any, Iterable, Sequence

import pyqtgraph as pg
from PyQt5.QtCore import Qt
from PyQt5.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontDatabase,
    QFontMetrics,
    QPalette,
    QPen,
)
from PyQt5.QtWidgets import (
    QApplication,
    QStyleFactory,
    QWidget,
    QWidgetAction,
)

__all__ = [
    # themes
    "THEME_DARK",
    "THEME_LIGHT",
    "THEMES",
    "TOKENS",
    "set_theme",
    "current_theme",
    "token",
    # surfaces
    "BG_BASE",
    "BG_SURFACE",
    "BG_RAISED",
    "BG_PLOT",
    "BORDER",
    "BORDER_HI",
    # text
    "FG",
    "FG_MUTED",
    "FG_FAINT",
    # accents
    "PRIMARY",
    "PRIMARY_DIM",
    "ACCENT",
    "SUCCESS",
    "DANGER",
    # data series
    "TRACE_RAW",
    "TRACE_FILTERED",
    "TRACE_ENVELOPE",
    "TRACE_ZERO",
    "GRID_COLOR",
    "GRID_ALPHA",
    # metrics
    "SPACE",
    "S2",
    "S4",
    "S6",
    "S8",
    "S12",
    "S16",
    "S24",
    "RADIUS_CONTROL",
    "RADIUS_OVERLAY",
    "HAIRLINE",
    "TOOLBAR_HEIGHT",
    "CONTROL_HEIGHT",
    "style_spinbox",
    "collect_orphan_widgets",
    "NAVIGATOR_HEIGHT",
    "CHANNEL_MIN_HEIGHT",
    "CHANNEL_DENSE_HEIGHT",
    "SPECTROGRAM_MIN_HEIGHT",
    "AXIS_LEFT_WIDTH",
    "MOTION_MS",
    "LW_THIN",
    "LW_THICK",
    "LW_HAIRLINE",
    "LW_CURSOR",
    "FOCUS_WIDTH",
    # fonts
    "FONT_UI_FAMILIES",
    "FONT_MONO_FAMILIES",
    "SIZE_PT",
    "SIZE_SMALL_PT",
    "font_ui",
    "font_mono",
    "ui_metrics",
    "mono_metrics",
    # low level
    "qcolor",
    "pen",
    "brush",
    "no_pen",
    # roles
    "trace_color",
    "trace_pen",
    "trace_symbol_brush",
    "trace_symbol_pen",
    "zero_pen",
    "grid_pen",
    "crosshair_pen",
    "cursor_pen",
    "marker_pen",
    "marker_brush",
    "handle_pen",
    "selection_pen",
    "selection_brush",
    "region_pen",
    "region_brush",
    "region_hover_pen",
    "region_hover_brush",
    "power_pen",
    "power_fill_brush",
    "border_pen",
    # pyqtgraph appliers
    "style_axis",
    "style_plotitem",
    "style_figure",
    "style_colorbar",
    "strip_pg_menus",
    "overlay_textitem",
    # qt chrome
    "palette",
    "stylesheet",
    "apply",
    "apply_pg_config",
    # data palettes
    "SPECTROGRAM_MAPS",
    "SPECTROGRAM_MAP_LABELS",
    "DEFAULT_SPECTROGRAM_MAP",
    "spectrogram_colormap",
    "MARKER_COLORS",
    "LIGHT_MARKER_COLORS",
    "marker_colors",
    "marker_color",
    "MARKER_ICON_BG",
    "MARKER_ICON_RING",
    # contrast
    "relative_luminance",
    "contrast_ratio",
    "check_contrast",
]


# ---------------------------------------------------------------------------
# Section 1 -- tokens
# ---------------------------------------------------------------------------

# Surfaces.
BG_BASE = "#0B0F16"  # application background, figure gutters
BG_SURFACE = "#11161F"  # toolbars, panels, status bar
BG_RAISED = "#171D28"  # menus, dialogs, popovers, in-scene overlays
BG_PLOT = "#0D1219"  # plot viewbox interior (NEVER pure #000)
BORDER = "#232B38"  # hairlines
BORDER_HI = "#333F52"  # hovered / emphasised borders

# Text.
FG = "#E6EDF6"  # primary text
FG_MUTED = "#9AA7B8"  # axis labels, tick labels, secondary text
FG_FAINT = "#6B7788"  # NON-TEXT decoration and disabled roles only

# Accent / semantic.
PRIMARY = "#4C8DFF"  # selection, focus ring, active region, links
PRIMARY_DIM = "#2A5FB8"  # pressed / inactive variants
ACCENT = "#F0A828"  # playback cursor, current position
SUCCESS = "#3FBF7F"
DANGER = "#FF5C5C"

# Data series.
TRACE_RAW = "#7FD4FF"
TRACE_FILTERED = "#FFC65C"
TRACE_ENVELOPE = "#FF7AB6"
TRACE_ZERO = "#2B3546"

# Derived -- defined explicitly so no call site computes them inline.
GRID_COLOR = TRACE_ZERO
GRID_ALPHA = 0.35

THEME_DARK = "dark"
THEME_LIGHT = "light"

#: The dark token table.  Dotted names -> hex.  This is the reference theme.
DARK_TOKENS: dict[str, str] = {
    "bg.base": BG_BASE,
    "bg.surface": BG_SURFACE,
    "bg.raised": BG_RAISED,
    "bg.plot": BG_PLOT,
    "border": BORDER,
    "border.hi": BORDER_HI,
    "fg": FG,
    "fg.muted": FG_MUTED,
    "fg.faint": FG_FAINT,
    "primary": PRIMARY,
    "primary.dim": PRIMARY_DIM,
    "accent": ACCENT,
    "success": SUCCESS,
    "danger": DANGER,
    "trace.raw": TRACE_RAW,
    "trace.filtered": TRACE_FILTERED,
    "trace.envelope": TRACE_ENVELOPE,
    "trace.zero": TRACE_ZERO,
    # foreground for anything sitting ON a primary fill (checked toolbar
    # buttons, the open button).  Light in BOTH themes: primary.dim is dark
    # in the daylight theme, so $fg there would be black on navy.
    "on.primary": FG,
    # the selected lane's ground.  NOT bg.surface: that is a *chrome* value,
    # and using it inside the canvas made the selected lane the same colour
    # as the toolbar it sits under, so the two merged at the top edge.
    "bg.lane": "#151C28",
    # the rule that separates a chrome band from the canvas.  Heavier than
    # `border`, which is for hairlines *within* a band: at ~2.5:1 against
    # both grounds it reads as structure without shouting, where BORDER_HI
    # managed only 1.7:1 and simply disappeared.
    "edge": "#47566E",
}

#: The light token table -- a **daylight** theme, not a polite inversion.
#:
#: This exists because the tool is used outdoors in direct sun, where a
#: screen's own emission is competing with the sky.  The design rules are
#: therefore different from the dark theme's, and deliberately so:
#:
#: * plot and page grounds are pure ``#FFFFFF``.  Any tint costs luminance,
#:   and luminance is the entire budget under glare.
#: * ink is near-black.  There are no mid-greys: a 4.5:1 label that reads
#:   fine indoors disappears at 50 000 lux, so even ``fg.faint`` -- excluded
#:   from the contrast gate as decoration -- is held above 6:1 here.
#: * data-series colours are dark and saturated rather than bright.  On white
#:   a pale trace has almost no contrast; the dark theme's ``#7FD4FF`` scores
#:   1.4:1 on white against 8.9:1 for the value used below.
#: * borders are strong enough to survive washout instead of being hairlines.
LIGHT_TOKENS: dict[str, str] = {
    "bg.base": "#FFFFFF",
    "bg.surface": "#EDEFF3",
    "bg.raised": "#FFFFFF",
    "bg.plot": "#FFFFFF",
    "border": "#9AA6B4",
    "border.hi": "#5C6B7C",
    "fg": "#000000",
    "fg.muted": "#2B3440",
    "fg.faint": "#5A6675",
    "primary": "#0B3FA8",
    "primary.dim": "#082E7A",
    "accent": "#8A4200",
    "success": "#0B5C34",
    "danger": "#A11212",
    "trace.raw": "#0B4F8A",
    "trace.filtered": "#8A4200",
    "trace.envelope": "#8E1A5C",
    "trace.zero": "#AAB4C0",
    "on.primary": "#FFFFFF",
    "bg.lane": "#E8ECF3",
    "edge": "#7C8A9B",
}

#: All selectable themes, by name.
THEMES: dict[str, dict[str, str]] = {
    THEME_DARK: DARK_TOKENS,
    THEME_LIGHT: LIGHT_TOKENS,
}

#: The **active** token table.  Starts as the dark table and is updated in
#: place by :func:`set_theme`, so ``theme.TOKENS['primary']`` and
#: ``theme.token('primary')` always agree with what is on screen.  The
#: module-level constants keep the dark values regardless.
TOKENS: dict[str, str] = dict(DARK_TOKENS)

#: Reverse map from a dark constant back to its dotted name, so that passing
#: ``theme.PRIMARY`` into a helper still resolves correctly under the light
#: theme.  Built once; values collide harmlessly (GRID_COLOR is TRACE_ZERO).
_BY_VALUE: dict[str, str] = {v.upper(): k for k, v in DARK_TOKENS.items()}

_ACTIVE = {"name": THEME_DARK}

# Caches for anything expensive or QApplication-dependent.
_CACHE: dict[str, Any] = {}


def current_theme() -> str:
    """Return the name of the active theme, ``'dark'`` or ``'light'``."""
    return _ACTIVE["name"]


def set_theme(name: str = THEME_DARK) -> None:
    """Switch the active theme and drop every cached palette / stylesheet.

    Updates :data:`TOKENS` in place so that already-imported call sites see the
    new values.  Does **not** repaint anything: call :func:`apply` afterwards
    (and re-run the ``style_*`` appliers on live plot items) to push the change
    into a running application.

    Raises
    ------
    KeyError
        If *name* is not a known theme.
    """
    table = THEMES[name]
    _ACTIVE["name"] = name
    TOKENS.clear()
    TOKENS.update(table)
    for key in list(_CACHE):
        if key.startswith(("palette:", "stylesheet:")):
            del _CACHE[key]


def token(name: str) -> str:
    """Resolve a dotted token name (``'bg.plot'``, ``'primary'``) to a hex string.

    Raises
    ------
    KeyError
        On an unknown token name.  This is deliberate: a typo must fail loudly
        rather than silently paint the wrong colour.
    """
    return TOKENS[name]


def _resolve(c: Any) -> Any:
    """Map a token name, a dark token constant or a raw colour to a colour spec.

    Accepts, in order of preference: a dotted token name, a dark-theme token
    value (remapped to the active theme), any other ``'#rrggbb'`` string, or
    anything :func:`pyqtgraph.mkColor` understands (passed straight through).
    """
    if isinstance(c, str):
        if c in TOKENS:
            return TOKENS[c]
        name = _BY_VALUE.get(c.upper())
        if name is not None:
            return TOKENS[name]
    return c


# ---------------------------------------------------------------------------
# Section 2 -- metrics
# ---------------------------------------------------------------------------

#: The only legal spacing values in the application, in pixels.
SPACE: tuple[int, ...] = (2, 4, 6, 8, 12, 16, 24)
S2, S4, S6, S8, S12, S16, S24 = SPACE

RADIUS_CONTROL = 3  # buttons, inputs, scrollbars
RADIUS_OVERLAY = 4  # menus, dialogs, in-scene overlays
HAIRLINE = 1  # panel separators, in px
FOCUS_WIDTH = 2  # focus ring width, in px

TOOLBAR_HEIGHT = 36
NAVIGATOR_HEIGHT = 56
CHANNEL_MIN_HEIGHT = 80
"""Comfortable height of one channel row: a labelled y axis fits."""

CHANNEL_DENSE_HEIGHT = 34
"""Floor height of one channel row when every channel has to fit at once.

A sixteen electrode array is the case this application exists for, and
scrolling past half of it to see the other half defeats the point.  Below
`timeplot.TICK_VALUES_MIN_HEIGHT` a row drops its tick values and keeps the
zero line and its ``CH nn`` caption, which is still a readable trace - and
sixteen of those beat eight comfortable ones plus a scrollbar.
"""
SPECTROGRAM_MIN_HEIGHT = 120
AXIS_LEFT_WIDTH = 56

MOTION_MS = 150  # 120-180 ms ease-out band; nothing animates the data

# Line widths.
#
# LW_THIN is load-bearing for performance and MUST stay <= 1.0.  Qt's raster
# engine has a fast path for pen width <= 1.0; a width of 1.1 falls back to
# QStroker and measured 28.3 ms vs 4.4 ms per 16-channel repaint (908 ms vs
# 5.4 ms with antialiasing on).  This deliberately overrides the "1.1 px thin"
# line in the design direction.
LW_THIN = 1.0
LW_THICK = 1.8  # only for the sparse step==1 branch, where points are few
LW_HAIRLINE = 1.0
LW_CURSOR = 2.0
LW_CLOSE = 1.3
"""Stroke width of the tab close mark.

Thin on purpose: a close affordance sits next to every tab title and should
read as a mark, not as a control competing with the title beside it."""
LW_SELECTED = 2.0
"""Pen width of the **selected** channel's waveform.

Only ever one trace out of N carries it, so the QStroker cost noted above is
paid once per frame, not sixteen times.  In a dense stack it drops back to
:data:`LW_THIN` -- see :func:`waveform_pen`.
"""

DENSE_CHANNELS = 4
"""Above this many visible channels a waveform stack counts as *dense*.

Dense means: hairline pens everywhere (including the selected channel) and the
deeper of the two dim mixes, so that sixteen lanes recede into a background
texture out of which the selected lane can stand up.
"""

COLORBAR_WIDTH = 8
"""Width of the slim in-plot colour bar, in px.  A legend, not a control."""


# ---------------------------------------------------------------------------
# Section 3 -- fonts
# ---------------------------------------------------------------------------

#: UI text stack, first installed family wins.
FONT_UI_FAMILIES: tuple[str, ...] = (
    "Inter",
    "Adwaita Sans",
    "Noto Sans",
    "DejaVu Sans",
    "sans-serif",
)

#: Numeric readout stack.  Note the two spellings of the JetBrains Nerd Font:
#: fontconfig reports it without a space after "JetBrains" on this machine, but
#: other installations use the spaced name.  Both are listed on purpose.
FONT_MONO_FAMILIES: tuple[str, ...] = (
    "JetBrainsMono Nerd Font",
    "JetBrains Mono Nerd Font",
    "Adwaita Mono",
    "DejaVu Sans Mono",
    "monospace",
)

SIZE_PT = 10  # base UI size; the app is dense
SIZE_SMALL_PT = 9  # in-plot labels, chips; never go below 8


def _installed_families() -> frozenset[str]:
    """Return the set of font families Qt can actually see (cached)."""
    cached = _CACHE.get("families")
    if cached is None:
        try:
            cached = frozenset(QFontDatabase().families())
        except Exception:  # pragma: no cover - no QApplication / no fontconfig
            cached = frozenset()
        _CACHE["families"] = cached
    return cached


def _first_installed(stack: Sequence[str]) -> str:
    """Return the first family in *stack* that exists, else the last entry.

    Never hard-fails: the final entry of every stack is a generic CSS-style
    family (``'sans-serif'``, ``'monospace'``) that Qt resolves itself.
    """
    key = "family:" + "|".join(stack)
    cached = _CACHE.get(key)
    if cached is None:
        families = _installed_families()
        cached = next((f for f in stack if f in families), stack[-1])
        _CACHE[key] = cached
    return cached


def _font(stack: Sequence[str], size: int | None, bold: bool, mono: bool) -> QFont:
    """Build (and memoise) a QFont for a family stack."""
    pt = SIZE_PT if size is None else int(size)
    key = f"font:{'mono' if mono else 'ui'}:{pt}:{bold}"
    font = _CACHE.get(key)
    if font is None:
        font = QFont(_first_installed(stack), pt)
        if hasattr(font, "setFamilies"):
            # Keep Qt's own fallback chain alive for missing glyphs.
            font.setFamilies(list(stack))
        font.setPointSize(pt)
        font.setBold(bold)
        font.setStyleStrategy(QFont.PreferAntialias)
        if mono:
            font.setStyleHint(QFont.Monospace)
            font.setFixedPitch(True)
        _CACHE[key] = font
    return QFont(font)  # a copy: callers may mutate what they are given


def font_ui(size: int | None = None, bold: bool = False) -> QFont:
    """Return the UI font at *size* points (default :data:`SIZE_PT`).

    Used for every widget label, menu entry and button.  Not fixed pitch --
    proportional text reads better in chrome.
    """
    return _font(FONT_UI_FAMILIES, size, bold, mono=False)


def font_mono(size: int | None = None, bold: bool = False) -> QFont:
    """Return the monospaced font at *size* points (default :data:`SIZE_PT`).

    Every number the user compares or reads off an axis must use this face so
    digits align: tick labels, status-bar readouts, hover tooltips, tables.
    """
    return _font(FONT_MONO_FAMILIES, size, bold, mono=True)


def _metrics(font: QFont, key: str) -> QFontMetrics:
    cached = _CACHE.get(key)
    if cached is None:
        cached = QFontMetrics(font)
        _CACHE[key] = cached
    return cached


def ui_metrics(size: int | None = None) -> QFontMetrics:
    """Cached ``QFontMetrics`` for the UI font, for layout maths."""
    pt = SIZE_PT if size is None else int(size)
    return _metrics(font_ui(pt), f"metrics:ui:{pt}")


def mono_metrics(size: int | None = None) -> QFontMetrics:
    """Cached ``QFontMetrics`` for the mono font.

    Use this for anything that must line up digits: axis tick spacing,
    status-bar field widths, fixed-width readout boxes.
    """
    pt = SIZE_PT if size is None else int(size)
    return _metrics(font_mono(pt), f"metrics:mono:{pt}")


# ---------------------------------------------------------------------------
# Section 4 -- low-level colour / pen / brush helpers
# ---------------------------------------------------------------------------


def _alpha255(alpha: float | int) -> int:
    """Normalise an alpha given as a 0.0-1.0 float or a 0-255 int."""
    if isinstance(alpha, float):
        return max(0, min(255, int(round(alpha * 255))))
    return max(0, min(255, int(alpha)))


def qcolor(c: Any, alpha: float | int | None = None) -> QColor:
    """Return a ``QColor`` for a token name, a token constant or a raw colour.

    Parameters
    ----------
    c
        ``'primary'``, ``theme.PRIMARY``, ``'#4C8DFF'`` or anything
        ``pyqtgraph.mkColor`` accepts.
    alpha
        Optional opacity, either a ``0.0``-``1.0`` float or a ``0``-``255``
        int.  Both are accepted and told apart by type.
    """
    col = QColor(pg.mkColor(_resolve(c)))
    if alpha is not None:
        col.setAlpha(_alpha255(alpha))
    return col


def pen(
    c: Any,
    width: float = LW_HAIRLINE,
    alpha: float | int | None = None,
    style: Qt.PenStyle | None = None,
    cosmetic: bool = True,
) -> QPen:
    """Return a fresh ``QPen``.  Same colour arguments as :func:`qcolor`.

    ``cosmetic=True`` keeps the width in device pixels regardless of the view
    transform, which is what you want for every line in a plot except a line
    whose thickness carries data.
    """
    p = pg.mkPen(qcolor(c, alpha), width=width, cosmetic=cosmetic)
    if style is not None:
        p.setStyle(style)
    return p


def brush(c: Any, alpha: float | int | None = None) -> QBrush:
    """Return a fresh solid ``QBrush``.  Same colour arguments as :func:`qcolor`."""
    return pg.mkBrush(qcolor(c, alpha))


def no_pen() -> QPen:
    """Return a ``Qt.NoPen`` pen, for axis lines and unstroked shapes."""
    return QPen(Qt.NoPen)


# ---------------------------------------------------------------------------
# Section 5 -- named role helpers
# ---------------------------------------------------------------------------

_TRACE_ROLES: dict[str, str] = {
    "data": "trace.raw",
    "raw": "trace.raw",
    "trace": "trace.raw",
    "filtered": "trace.filtered",
    "filter": "trace.filtered",
    "envelope": "trace.envelope",
    "zero": "trace.zero",
}


def _trace_token(role: str | None) -> str:
    """Map a trace role to a token name, falling back to ``trace.raw``."""
    if not isinstance(role, str):
        return "trace.raw"
    key = role.strip().lower()
    if key.startswith("trace."):
        key = key[len("trace.") :]
    return _TRACE_ROLES.get(key, "trace.raw")


def trace_color(role: str) -> str:
    """Return the hex colour for a trace role.

    Roles: ``'data'``/``'raw'`` (both the raw waveform), ``'filtered'``,
    ``'envelope'``, ``'zero'``.  An unknown role falls back to the raw colour
    and never raises -- a mislabelled trace should still be drawn.
    """
    return TOKENS[_trace_token(role)]


def trace_pen(
    role: str,
    thick: bool = False,
    selected: bool = False,
    alpha: float | int | None = None,
) -> QPen:
    """Return the pen for a data trace.

    Parameters
    ----------
    role
        See :func:`trace_color`.
    thick
        Use :data:`LW_THICK` instead of :data:`LW_THIN`.  Only for the sparse
        ``step == 1`` branch, where the point count is small: thick pens are
        roughly six times more expensive to raster.
    selected
        Paint the trace in :data:`PRIMARY` instead of its role colour, marking
        the selected channel.  **Colour alone is not enough**: the caller must
        also change a label weight or add a text marker, because meaning may
        never be carried by colour alone.
    alpha
        Optional opacity, float 0-1 or int 0-255.

    Notes
    -----
    One colour per trace *type*, across all channels.  A per-channel rainbow at
    16 channels is noise, not signal; channel identity belongs in the label.
    """
    color = "primary" if selected else _trace_token(role)
    return pen(color, width=LW_THICK if thick else LW_THIN, alpha=alpha)


def trace_symbol_brush(role: str) -> QBrush:
    """Fill brush for scatter symbols drawn on a trace of *role*."""
    return brush(_trace_token(role))


def trace_symbol_pen(role: str) -> QPen:
    """Outline pen for scatter symbols drawn on a trace of *role*."""
    return pen(_trace_token(role), width=LW_HAIRLINE)


def zero_pen() -> QPen:
    """Pen for the zero line of a waveform: quiet, never competing with data."""
    return pen("trace.zero", width=LW_HAIRLINE)


def grid_pen() -> QPen:
    """Pen for plot grid lines: :data:`GRID_COLOR` at :data:`GRID_ALPHA`."""
    return pen("trace.zero", width=LW_HAIRLINE, alpha=GRID_ALPHA)


def crosshair_pen() -> QPen:
    """Pen for the dashed hover crosshair.  Decoration, hence ``fg.faint``."""
    return pen("fg.faint", width=LW_HAIRLINE, style=Qt.DashLine)


def cursor_pen() -> QPen:
    """Pen for the playback cursor.  :data:`ACCENT` is reserved for this."""
    return pen("accent", width=LW_CURSOR)


def marker_pen() -> QPen:
    """Outline pen for a stored crosshair marker."""
    return pen("primary", width=LW_HAIRLINE)


def marker_brush() -> QBrush:
    """Fill brush for a stored crosshair marker (primary at 0.35 alpha)."""
    return brush("primary", alpha=0.35)


HANDLE_WIDTH = 3.0
"""Width in device pixels of a grab handle drawn on a draggable boundary.

A 1 px region edge is visible but not findable by the mouse.  Handles are
painted this wide, centred on the boundary, so there is something to aim at.
"""

HANDLE_HEIGHT_FRACTION = 0.55
"""Fraction of the row height a boundary grab handle spans.

Short enough to read as a handle rather than as a second edge, long enough to
be an easy target.
"""


def handle_pen() -> QPen:
    """Pen for the spectrogram highpass / lowpass drag handles."""
    return pen("primary", width=LW_SELECTED)


def selection_pen() -> QPen:
    """Outline pen for the rubber-band scale box."""
    return pen("primary", width=LW_HAIRLINE)


def selection_brush() -> QBrush:
    """Fill brush for the rubber-band scale box (primary at 0.15 alpha)."""
    return brush("primary", alpha=0.15)


def region_pen() -> QPen:
    """Outline pen for the navigator ``LinearRegionItem``."""
    return pen("primary", width=LW_HAIRLINE)


def region_brush() -> QBrush:
    """Fill brush for the navigator region (primary at 0.18 alpha)."""
    return brush("primary", alpha=0.18)


def region_hover_pen() -> QPen:
    """Hovered outline for the navigator region -- *brighter* than idle.

    This used to return ``border.hi``, which is dimmer than the idle
    :func:`region_pen`; hovering the region made it harder to see, which is
    backwards.  Hover now thickens the same primary edge.
    """
    return pen("primary", width=LW_SELECTED)


def region_hover_brush() -> QBrush:
    """Hovered fill for the navigator region (primary at 0.30 alpha)."""
    return brush("primary", alpha=0.30)


def power_pen() -> QPen:
    """Pen for the power-spectrum curve."""
    return pen("trace.raw", width=LW_THIN)


def power_fill_brush() -> QBrush:
    """Fill brush under the power-spectrum curve (raw trace at 0.25 alpha)."""
    return brush("trace.raw", alpha=0.25)


def border_pen(selected: bool = False) -> QPen:
    """Pen for the per-channel frame: hairline border, or primary when selected."""
    return pen("primary" if selected else "border", width=LW_HAIRLINE)


# ---------------------------------------------------------------------------
# Section 5b -- multi-channel waveform emphasis
#
# The rule this section exists to enforce: in a stack of N channels exactly one
# trace is allowed to be a saturated colour, and that one is the *selected*
# channel.  Everything else is the same hue mixed down toward the plot
# background so the stack reads as texture.  Sixteen equally bright amber lanes
# are sixteen equally loud claims on the eye, which is the same as none.
# ---------------------------------------------------------------------------

TOOLBAR_BUTTON_BOX = 30
"""Outer height of a tool bar button, in px, borders included.

Pinned on the widget rather than left to the layout.  A QToolBar sizes its
items itself, and re-applying a style sheet to a laid-out bar makes it
re-centre them at a different height than it first chose: natively the bar
held buttons of 30 and 32 px, after a theme switch it held 30 px buttons
positioned 6 px lower, whose bottom border and rounded corners then fell
outside the bar and were clipped.  Stating the height makes the two paths
identical by construction.

Equals :data:`TOOLBAR_BUTTON_HEIGHT` plus the style sheet's ``S4`` padding
top and bottom and its hairline border on each side.
"""

TOOLBAR_BUTTON_HEIGHT = 20
"""Content height of a tool bar button, in px, excluding padding and border.

Stated so a re-theme cannot resize the bar (see the tool bar rule in the
style sheet), and kept well below :data:`CONTROL_HEIGHT`: a tool bar button
holds a 16 px glyph, not a line of editable text, and borrowing the input
height made every button 36 px tall in a bar with 35 px to give -- which
clipped their bottom border and rounded corners clean off.
"""

MIN_GRAPHIC_CONTRAST = 3.0
"""Floor for a *non-text* graphical object against its own background.

WCAG 2.1 SC 1.4.11.  Text keeps the stricter :data:`MIN_CONTRAST` (4.5:1); a
dimmed waveform is a graphic, so 3:1 is the line it may not cross.  Every
dimming request is clamped to it -- see :func:`dim_color`.
"""

MIN_GRAPHIC_CONTRAST_DAYLIGHT = 4.5
"""The same floor for the daylight theme, held a full step higher.

3:1 is the WCAG floor for non-text graphics and is right for a screen read
indoors.  It is not right for one read in direct sun: glare raises the
effective black level, so a line that measures 3:1 on the bench is far less
than 3:1 on a riverbank.  Unselected traces in the light theme are therefore
knocked back only as far as 4.5:1 -- they lose emphasis without becoming
unreadable, which is the whole point of dimming them.
"""


def min_graphic_contrast() -> float:
    """The graphic contrast floor for the *active* theme."""
    if current_theme() == THEME_LIGHT:
        return MIN_GRAPHIC_CONTRAST_DAYLIGHT
    return MIN_GRAPHIC_CONTRAST


TRACE_DIM_MIX = 0.65
"""How far an unselected trace is mixed toward the plot background when dense.

Requested value; :func:`dim_color` reduces it if 0.65 would take the trace
below :data:`MIN_GRAPHIC_CONTRAST`.
"""

TRACE_DIM_MIX_SPARSE = 0.35
"""The same mix for a sparse stack (<= :data:`DENSE_CHANNELS` channels).

With two channels on screen the unselected one is still a trace the user reads
values off, not background texture, so it is only knocked back, not dimmed out.
"""


def _mix(a: QColor, b: QColor, t: float) -> QColor:
    """Linear sRGB-component blend, ``t=0`` -> *a*, ``t=1`` -> *b*."""
    t = max(0.0, min(1.0, float(t)))
    return QColor(
        int(round(a.red() + (b.red() - a.red()) * t)),
        int(round(a.green() + (b.green() - a.green()) * t)),
        int(round(a.blue() + (b.blue() - a.blue()) * t)),
    )


def mix_colors(a: Any, b: Any, t: float) -> QColor:
    """Blend two colours.  Same colour arguments as :func:`qcolor`.

    ``t`` is the fraction of *b*: ``mix_colors('trace.raw', 'bg.plot', 0.65)``
    is the raw trace 65 % of the way to the plot background.
    """
    return _mix(qcolor(a), qcolor(b), t)


def dim_color(
    c: Any,
    amount: float = TRACE_DIM_MIX,
    onto: Any = "bg.plot",
    min_contrast: float | None = None,
) -> QColor:
    """Mix *c* toward *onto*, but never below *min_contrast* against *onto*.

    The clamp is what makes this safe to call with an aggressive *amount*: ask
    for 0.65 and, if that would leave the line at 2.4:1 on ``bg.plot``, you get
    the deepest mix that still clears 3:1 instead.  Cached per active theme.
    """
    if min_contrast is None:
        # resolved per call, not defaulted in the signature: the floor is a
        # property of the active theme and the theme changes at runtime.
        min_contrast = min_graphic_contrast()
    base = qcolor(c)
    ground = qcolor(onto)
    key = f"dim:{current_theme()}:{base.name()}:{ground.name()}:{amount}:{min_contrast}"
    hit = _CACHE.get(key)
    if hit is not None:
        return QColor(hit)
    t = max(0.0, min(1.0, float(amount)))
    out = _mix(base, ground, t)
    if min_contrast and min_contrast > 1.0:
        while t > 0.0 and contrast_ratio(out.name(), ground.name()) < min_contrast:
            t = max(0.0, round(t - 0.05, 2))
            out = _mix(base, ground, t)
    _CACHE[key] = QColor(out)
    return QColor(out)


def is_dense(n_channels: int) -> bool:
    """Is a stack of *n_channels* visible channels dense?  See :data:`DENSE_CHANNELS`."""
    try:
        return int(n_channels) > DENSE_CHANNELS
    except (TypeError, ValueError):
        return False


def filter_is_active(
    highpass: float | None = None,
    lowpass: float | None = None,
    rate: float | None = None,
) -> bool:
    """Is a band filter with these cutoffs doing anything at all?

    Mirrors the predicate ``BufferedFilter.update()`` uses to decide whether to
    build an SOS section at all: a high pass below 0.1 % of Nyquist and a low
    pass at or above Nyquist together mean *pass through*.  Unknown values
    (``None``) count as "not filtering", so a trace that carries no cutoffs is
    treated as raw rather than being mislabelled.
    """
    if rate is None or rate <= 0:
        return bool(highpass) or bool(lowpass)
    nyquist = rate / 2
    high_on = highpass is not None and highpass >= 0.001 * nyquist
    low_on = lowpass is not None and lowpass < nyquist - 1e-8
    return bool(high_on or low_on)


def waveform_role(trace: Any = None, role: str | None = None) -> str:
    """Resolve the trace role a waveform should actually be *painted* as.

    A trace named ``'filtered'`` whose filter is a pass-through carries exactly
    the samples of the raw trace, and painting the same numbers amber in one
    plot and cyan in another asserts a difference that does not exist.  So a
    filtered trace reports ``'raw'`` until a cutoff is actually engaged.

    Duck typed on purpose: *trace* only has to expose ``name``,
    ``highpass_cutoff``, ``lowpass_cutoff`` and ``rate``, any of which may be
    missing.  Pass *role* explicitly to skip the name lookup.

    Every waveform in the application -- main plots and navigator alike --
    must take its colour from this one function, otherwise the two can and
    will disagree again.

    Returns
    -------
    str
        A short role name (``'raw'``, ``'filtered'``, ``'envelope'``,
        ``'zero'``) accepted by :func:`trace_color` and :func:`trace_pen`.
    """
    if role is None:
        role = getattr(trace, "name", None)
    short = _trace_token(role).split(".", 1)[1]
    if short != "filtered":
        return short
    if not filter_is_active(
        getattr(trace, "highpass_cutoff", None),
        getattr(trace, "lowpass_cutoff", None),
        getattr(trace, "rate", None),
    ):
        return "raw"
    return "filtered"


def waveform_color(
    role: str | None = None,
    selected: bool = False,
    dense: bool = False,
    color: Any = None,
) -> QColor:
    """Colour of one waveform in a channel stack.

    *selected* wins over everything: the one channel the user is working on is
    ``primary``, at full saturation, in every plot that draws it.  All others
    are their role colour (or an explicit *color*, for plugin traces that bring
    their own) dimmed toward the plot background -- deeper when *dense*.
    """
    if selected:
        return qcolor("primary")
    base = color if color is not None else _trace_token(role)
    return dim_color(base, TRACE_DIM_MIX if dense else TRACE_DIM_MIX_SPARSE)


def waveform_pen(
    role: str | None = None,
    selected: bool = False,
    dense: bool = False,
    thick: bool = False,
    color: Any = None,
    alpha: float | int | None = None,
) -> QPen:
    """Pen for one waveform in a channel stack.  Colour from :func:`waveform_color`.

    Widths: the selected channel gets :data:`LW_SELECTED`, or :data:`LW_THIN`
    when *dense* -- at sixteen lanes a 2 px pen is a bar, not a line, and it is
    the one pen that would be re-stroked most often.  Unselected traces are
    always :data:`LW_THIN` unless *thick* is asked for and the stack is sparse.

    **Colour alone never carries the selection**: callers pair this with a bold
    caption and the rail's own highlight.
    """
    if selected:
        width = LW_THIN if dense else LW_SELECTED
    else:
        width = LW_THICK if (thick and not dense) else LW_THIN
    return pen(waveform_color(role, selected, dense, color), width=width, alpha=alpha)


def waveform_fill_brush(
    role: str | None = None,
    selected: bool = False,
    dense: bool = False,
    color: Any = None,
    alpha: float | int = 0.35,
) -> QBrush:
    """Fill brush for a min/max envelope band drawn under :func:`waveform_pen`."""
    return brush(waveform_color(role, selected, dense, color), alpha=alpha)


# ---------------------------------------------------------------------------
# Section 6 -- pyqtgraph application helpers
# ---------------------------------------------------------------------------


#: Dynamic property recording which token a widget's text colour came from.
FG_PROPERTY = "audianFgToken"

#: Dynamic property recording that a widget wears the hairline group frame.
FRAME_PROPERTY = "audianFramed"


def tint(widget: Any, token_name: str = "fg.muted") -> Any:
    """Colour a widget's text from a token, and remember which one.

    Qt stylesheets bake the colour string at the moment they are set, so a
    widget styled this way does not follow a live theme switch.  Recording
    the *token* on the widget is what lets :func:`restyle_tree` put it right
    again without every call site having to be re-found by hand -- which is
    how the parameter captions and the group frames were missed the first
    time.
    """
    widget.setProperty(FG_PROPERTY, token_name)
    widget.setStyleSheet(f"color: {token(token_name)};")
    return widget


def frame(widget: Any) -> Any:
    """Give a container the hairline group frame, re-appliably."""
    widget.setObjectName("audianGroup")
    widget.setProperty(FRAME_PROPERTY, True)
    widget.setStyleSheet(
        "#audianGroup { "
        f"border: {HAIRLINE}px solid {token('border')}; "
        f"border-radius: {RADIUS_CONTROL}px; "
        "}"
    )
    return widget


#: Dynamic property recording that a widget is a chrome band.
BAND_PROPERTY = "audianBandEdge"


def band(
    widget: Any,
    top: bool = False,
    bottom: bool = False,
    ground: str = "bg.surface",
) -> Any:
    """Style a widget as a chrome band: chrome ground plus a boundary rule.

    The application has three grounds -- ``bg.surface`` for chrome bands,
    ``bg.base`` for the canvas they sit around, and ``bg.plot`` inside the
    plots.  What was missing was a *stated* boundary: with the tab strip
    gone from the top, the tool bar and the channel stack met on a hairline
    that neither ground was far enough from to make visible.  The ``edge``
    token is that boundary, and it belongs to every chrome/canvas seam so
    they all read the same way.
    """
    widget.setProperty(BAND_PROPERTY, f"{int(bool(top))}{int(bool(bottom))}|{ground}")
    name = widget.objectName() or "audianBand"
    widget.setObjectName(name)
    rules = [f"background-color: {token(ground)}"]
    if top:
        rules.append(f"border-top: {HAIRLINE}px solid {token('edge')}")
    if bottom:
        rules.append(f"border-bottom: {HAIRLINE}px solid {token('edge')}")
    widget.setStyleSheet("#%s { %s; }" % (name, "; ".join(rules)))
    return widget


def restyle_tree(root: Any) -> int:
    """Re-apply every tagged colour under *root*.  Returns how many changed.

    Walks the widget tree rather than a registry, so a widget created after
    this was written is still covered as long as it was styled through
    :func:`tint` or :func:`frame`.
    """
    from PyQt5.QtWidgets import QWidget

    count = 0
    widgets = [root] + list(root.findChildren(QWidget))
    for widget in widgets:
        try:
            token_name = widget.property(FG_PROPERTY)
            if token_name:
                tint(widget, str(token_name))
                count += 1
            elif widget.property(FRAME_PROPERTY):
                frame(widget)
                count += 1
            else:
                spec = widget.property(BAND_PROPERTY)
                if spec:
                    edges, _, ground = str(spec).partition("|")
                    band(
                        widget,
                        top=edges[0] == "1",
                        bottom=edges[1] == "1",
                        ground=ground or "bg.surface",
                    )
                    count += 1
        except RuntimeError:
            continue
    return count


def style_axis(axis_item: Any, mono: bool = True, show_line: bool = False) -> None:
    """Apply the full axis style to a ``pg.AxisItem`` in one call.

    Replaces every ``setPen('white')`` and every hand-rolled ``polish()`` body.

    * axis line: hidden (``no_pen()``) unless *show_line*, then ``border``
    * tick marks: ``fg.faint`` hairline -- decoration, so faint is allowed
    * tick text: ``fg.muted`` -- read off the screen, so it must clear 4.5:1
    * tick font: :func:`font_mono` at :data:`SIZE_SMALL_PT` when *mono*
    * axis label: recoloured to ``fg.muted``, text and units preserved
    * tick text on **major ticks only** (``maxTextLevel=0``)

    pyqtgraph labels the first three tick levels by default, so an axis that
    asks for three major ticks still renders every minor tick's value as
    well - five labels where the caller wanted three, at half the intended
    spacing.  Minor ticks are meant to be unlabelled rulings.

    Safe on an axis with no label, and safe to call repeatedly.
    """
    if axis_item is None:
        return
    axis_item.setStyle(maxTextLevel=0)
    axis_item.setPen(pen("border", width=HAIRLINE) if show_line else no_pen())
    axis_item.setTickPen(pen("fg.faint", width=LW_HAIRLINE))
    axis_item.setTextPen(qcolor("fg.muted"))
    if mono:
        axis_item.setTickFont(font_mono(SIZE_SMALL_PT))
    text = getattr(axis_item, "labelText", "") or ""
    units = getattr(axis_item, "labelUnits", "") or ""
    if text or units:
        # Re-set with the same text so the label keeps its content but picks up
        # the themed colour.  Skipped when there is no label at all, because
        # setLabel('', '') would toggle label visibility for nothing.
        axis_item.setLabel(text, units, color=token("fg.muted"))


def style_plotitem(
    plot_item: Any,
    axes: Iterable[str] = ("left", "right", "top", "bottom"),
    mono_ticks: bool = True,
) -> None:
    """Theme a whole ``pg.PlotItem``: viewbox background plus every axis.

    This is what a ``polish()`` / ``apply_theme()`` body should reduce to, plus
    a couple of role-pen calls.  Axes that the plot item does not have are
    skipped.  Idempotent.
    """
    if plot_item is None:
        return
    vb = plot_item.getViewBox() if hasattr(plot_item, "getViewBox") else None
    if vb is not None:
        vb.setBackgroundColor(qcolor("bg.plot"))
    available = getattr(plot_item, "axes", {})
    for name in axes:
        if name not in available:
            continue
        try:
            axis = plot_item.getAxis(name)
        except KeyError:  # pragma: no cover - defensive
            continue
        style_axis(axis, mono=mono_ticks)


def style_figure(glw: Any) -> None:
    """Theme a ``pg.GraphicsLayoutWidget``: background and layout margins.

    Sets the background to ``bg.base`` -- **never** ``None``.  A
    ``setBackground(None)`` call is exactly the mechanism by which the light
    grey Qt chrome leaks through into the figure gutters.

    Margins are :data:`S4` on all sides and both spacings are zero.  Negative
    spacing is never acceptable.
    """
    if glw is None:
        return
    glw.setBackground(qcolor("bg.base"))
    ci = getattr(glw, "ci", None)
    layout = getattr(ci, "layout", None) if ci is not None else None
    if layout is not None:
        layout.setContentsMargins(S4, S4, S4, S4)
        layout.setHorizontalSpacing(0)
        layout.setVerticalSpacing(0)


CONTROL_HEIGHT = 26
"""Uniform height of a themed one-line input (spin box, combo, line edit).

Large enough for :data:`SIZE_SMALL_PT` mono digits plus the ``S4`` padding
and the hairline border that the style sheet adds.
"""


def style_spinbox(spin: Any, mono: bool = True) -> None:
    """Theme a ``pyqtgraph.SpinBox`` and undo its ``compactHeight`` hack.

    ``pg.SpinBox`` calls ``setMaximumHeight(QFontMetrics(font).height())``
    from its own ``paintEvent`` (the ``compactHeight`` option, on by
    default).  That assumes an unstyled spin box with no padding.  With the
    audian style sheet's ``S4`` vertical padding and hairline border it
    leaves an eight pixel content box for an eighteen pixel line, so every
    numeric field renders with its top third sliced off - ``0 Hz`` reads as
    ``A Hz``.  Turning the option off and pinning the height is the only
    reliable cure; the widget re-applies the cap on every repaint otherwise.
    """
    if spin is None:
        return
    opts = getattr(spin, "opts", None)
    if isinstance(opts, dict):
        opts["compactHeight"] = False
    if mono:
        spin.setFont(font_mono(SIZE_SMALL_PT))
    spin.setMaximumHeight(CONTROL_HEIGHT)
    spin.setMinimumHeight(CONTROL_HEIGHT)
    line_edit = spin.lineEdit() if hasattr(spin, "lineEdit") else None
    if line_edit is not None:
        line_edit.setFont(spin.font())


def style_colorbar(cbar: Any, slim: bool = False, unit: str = "") -> None:
    """Theme a ``pg.ColorBarItem``: themed axes and a ``bg.surface`` backdrop.

    With *slim*, the bar becomes a legend rather than a control: no rotated
    axis label (it costs more width than the bar itself), the tick strip
    narrowed to exactly what the widest mono tick label needs, and the frame
    pens pulled off pyqtgraph's white default.  Pass *unit* (``'dB'``) to
    reserve room for it in the widest-label measurement; the unit itself is
    written into the top tick by :func:`colorbar_ticks`.
    """
    if cbar is None:
        return
    vb = cbar.getViewBox() if hasattr(cbar, "getViewBox") else None
    if vb is not None:
        vb.setBackgroundColor(qcolor("bg.surface"))
    for name in ("right", "left", "top", "bottom"):
        if name in getattr(cbar, "axes", {}):
            style_axis(cbar.getAxis(name))
    if not slim:
        return
    for name in ("right", "left"):
        if name not in getattr(cbar, "axes", {}):
            continue
        axis = cbar.getAxis(name)
        axis.setLabel(None)
        axis.showLabel(False)
        sample = f"-000 {unit}".strip()
        axis.setWidth(mono_metrics(SIZE_SMALL_PT).horizontalAdvance(sample) + S8)


def colorbar_pens() -> dict[str, Any]:
    """Constructor keyword arguments that theme a ``pg.ColorBarItem``'s handles.

    They have to be passed to the constructor: ColorBarItem hands them straight
    to the ``LinearRegionItem`` it builds there and never looks at them again,
    so assigning ``cbar.pen`` afterwards leaves pyqtgraph's white default on
    screen.
    """
    return {
        "pen": pen("border.hi", width=HAIRLINE),
        "hoverPen": pen("primary", width=FOCUS_WIDTH),
        "hoverBrush": brush("primary", alpha=0.25),
    }


def colorbar_ticks(low: float, high: float, unit: str = "dB") -> list[list[tuple]]:
    """Exactly three tick labels -- bottom, middle, top -- for a slim colour bar.

    The unit rides on the top label instead of a rotated axis title, which is
    the difference between an 8 px legend and a 40 px one.  Feed the result to
    ``axis.setTicks()``; the middle entry is dropped when the three labels would
    not fit apart, which the caller decides by passing a two-point range.
    """
    low = float(low)
    high = float(high)
    values = [low, 0.5 * (low + high), high]
    major = [
        (v, f"{v:.0f} {unit}".strip() if i == len(values) - 1 else f"{v:.0f}")
        for i, v in enumerate(values)
    ]
    return [major, []]


_MENU_HOLDER = None


def _menu_holder() -> Any:
    """One hidden, never-shown parent for released pyqtgraph ctrl widgets.

    pyqtgraph's plot control widgets have to stay alive after their menus
    are deleted (``showGrid``/``setLogMode``/``setDownsampling`` reach into
    them), but a widget with no parent is a top-level window.  Parking them
    all on a single holder turns hundreds of top-level widgets into one.
    """
    global _MENU_HOLDER
    if _MENU_HOLDER is None:
        _MENU_HOLDER = QWidget()
        _MENU_HOLDER.setObjectName("audianMenuHolder")
        _MENU_HOLDER.hide()
    return _MENU_HOLDER


def strip_pg_menus(plot_item: Any) -> None:
    """Tear down pyqtgraph's per-``PlotItem`` context-menu tree.

    pyqtgraph builds roughly nine hidden top-level ``QMenu`` windows per
    ``PlotItem`` -- measured 577 of them at startup on a 16-channel file.  They
    are never shown once the menus are disabled, but they cost memory, they are
    styled by the application stylesheet, and on Wayland every one of them is a
    potential surface.

    Call this straight after ``setMenuEnabled(False)``.  Safe to call more than
    once and safe on a plot item whose menus are already gone.

    The control widgets behind the menu (``plot_item.ctrl``) are *released*
    from their ``QWidgetAction`` and kept alive on the plot item before the
    menus are deleted, because ``PlotItem.showGrid()``, ``setLogMode()`` and
    ``setDownsampling()`` all reach into them.  Deleting the menus naively
    takes those widgets with it and turns the next ``showGrid()`` into a
    ``RuntimeError``.
    """
    if plot_item is None:
        return
    # Disabling the ViewBox menu makes pyqtgraph drop its own ViewBoxMenu.
    if hasattr(plot_item, "setMenuEnabled"):
        plot_item.setMenuEnabled(False, False)
    vb = plot_item.getViewBox() if hasattr(plot_item, "getViewBox") else None
    if vb is not None and getattr(vb, "menu", None) is not None:
        menu = vb.menu
        vb.menu = None
        menu.setParent(None)
        menu.deleteLater()

    ctrl_menu = getattr(plot_item, "ctrlMenu", None)
    if ctrl_menu is None:
        _adopt_ctrl_widgets(plot_item)
        return
    kept: list[Any] = []
    visited: list[Any] = []
    stack = [ctrl_menu]
    while stack:
        current = stack.pop()
        visited.append(current)
        for action in current.actions():
            submenu = action.menu()
            if submenu is not None:
                stack.append(submenu)
            elif isinstance(action, QWidgetAction):
                widget = action.defaultWidget()
                if widget is not None:
                    action.releaseWidget(widget)
                    kept.append(widget)
    # Hold a Python reference so the released widgets outlive the menus.
    plot_item._audian_ctrl_widgets = kept
    plot_item.ctrlMenu = None
    # Every visited menu, not just the root: pyqtgraph creates each submenu
    # parentless (`QtWidgets.QMenu(name)`), so walking them without deleting
    # them leaves six top-level QMenu popups per PlotItem alive.
    for menu in visited:
        try:
            menu.clear()
            menu.setParent(None)
            menu.deleteLater()
        except RuntimeError:
            # already taken down with its owning action
            pass
    # Re-parenting has to happen *after* the menus are gone: tearing down a
    # QWidgetAction re-parents its default widget back to None, so parking
    # the widgets before the delete silently undoes itself.
    _adopt_ctrl_widgets(plot_item)


def _adopt_ctrl_widgets(plot_item: Any) -> None:
    """Park pyqtgraph's plot control widgets on the hidden holder.

    Two families of orphan come out of ``PlotItem.__init__``:

    * the six control groups (``transformGroup``, ``decimateGroup`` and
      friends), released from their ``QWidgetAction`` above, and
    * the 640x480 ``QWidget`` that ``Ui_Form.setupUi`` was built into.  It
      is a local in pyqtgraph's constructor and is kept alive only by its
      children, so it is unreachable from the ``PlotItem`` API - but it is
      reachable as the ``window()`` of any control widget that is still
      parented to it.

    Both are hidden and harmless on X11.  On Wayland a top-level widget is
    a surface waiting to happen, and there are roughly eight per PlotItem -
    about 450 on a 16 channel file.
    """
    holder = _menu_holder()
    ctrl = getattr(plot_item, "ctrl", None)
    candidates = list(getattr(plot_item, "_audian_ctrl_widgets", []))
    if ctrl is not None:
        candidates.extend(
            value for value in vars(ctrl).values() if isinstance(value, QWidget)
        )
    for widget in candidates:
        try:
            window = widget.window()
        except RuntimeError:
            continue
        if window is None or window is holder:
            continue
        if window.parentWidget() is None:
            window.setParent(holder)
            window.hide()


def collect_orphan_widgets() -> int:
    """Adopt pyqtgraph's unreachable control forms.  Returns how many.

    ``PlotItem.__init__`` does ``w = QWidget(); Ui_Form().setupUi(w)`` and
    never keeps a reference to ``w``.  Once :func:`strip_pg_menus` has moved
    the control groups off it, the form is an empty, hidden, parentless
    640x480 ``QWidget`` that nothing can reach through the pyqtgraph API -
    one per PlotItem, 32 of them on a 16 channel file - and a parentless
    widget is a top-level window.

    The signature matched here is deliberately narrow (exactly ``QWidget``,
    no parent, not visible, no children, no layout, no object name) so that
    an application's own hidden top-level widget can never be swept up.

    Call once after the plots are built, not per plot: it scans every
    top-level widget.
    """
    holder = _menu_holder()
    adopted = 0
    for widget in QApplication.topLevelWidgets():
        if type(widget) is not QWidget or widget is holder:
            continue
        if widget.parentWidget() is not None or widget.isVisible():
            continue
        if widget.objectName() or widget.children() or widget.layout():
            continue
        widget.setParent(holder)
        widget.hide()
        adopted += 1
    return adopted


def overlay_textitem(anchor: tuple[float, float] = (0, 1), mono: bool = True) -> Any:
    """Return a preconfigured in-scene hover-readout ``pg.TextItem``.

    **This is the replacement for every QLabel popup.**  Both the navigator
    overlay and the trace-plot readout use it, so the two look identical: a
    ``bg.raised`` panel at 0.92 opacity, a hairline ``border`` frame, ``fg``
    text in the mono face at :data:`SIZE_SMALL_PT`, z-order 1000, hidden until
    the caller shows it, and immune to the view transform so it never stretches
    with a zoom.

    Add it to a viewbox with ``vb.addItem(item, ignoreBounds=True)``, then
    ``item.setText(...)``, ``item.setPos(x, y)`` and ``item.setVisible(True)``.
    """
    item = pg.TextItem(
        text="",
        color=qcolor("fg"),
        anchor=anchor,
        border=pen("border", width=HAIRLINE),
        fill=brush("bg.raised", alpha=0.92),
    )
    item.setFont(font_mono(SIZE_SMALL_PT) if mono else font_ui(SIZE_SMALL_PT))
    item.setZValue(1000)
    item.setVisible(False)
    # NOTE: no QGraphicsItem.ItemIgnoresTransformations - pg.TextItem
    # already cancels its parent's transform in updateTransform(), and
    # setting the flag too applies that correction twice, which paints the
    # overlay off screen while sceneBoundingRect() still looks correct.
    return item


# ---------------------------------------------------------------------------
# Section 7 -- Qt chrome
# ---------------------------------------------------------------------------


def palette() -> QPalette:
    """Return the fully populated ``QPalette`` for the active theme (cached).

    Every role is set explicitly, including the ``Disabled`` colour group --
    Fusion derives disabled colours badly from a dark base if you leave it to
    guess, producing unreadable grey-on-grey.
    """
    key = f"palette:{current_theme()}"
    cached = _CACHE.get(key)
    if cached is not None:
        return QPalette(cached)

    p = QPalette()
    roles = {
        QPalette.Window: "bg.base",
        QPalette.WindowText: "fg",
        QPalette.Base: "bg.surface",
        QPalette.AlternateBase: "bg.raised",
        QPalette.Text: "fg",
        QPalette.Button: "bg.surface",
        QPalette.ButtonText: "fg",
        QPalette.BrightText: "danger",
        QPalette.ToolTipBase: "bg.raised",
        QPalette.ToolTipText: "fg",
        QPalette.PlaceholderText: "fg.faint",
        QPalette.Highlight: "primary",
        QPalette.HighlightedText: "bg.base",
        QPalette.Link: "primary",
        QPalette.LinkVisited: "primary.dim",
        QPalette.Light: "border.hi",
        QPalette.Midlight: "border",
        QPalette.Mid: "border",
        QPalette.Dark: "border.hi",
        QPalette.Shadow: "bg.base",
    }
    for role, name in roles.items():
        p.setColor(role, qcolor(name))

    disabled = {
        QPalette.WindowText: "fg.faint",
        QPalette.Text: "fg.faint",
        QPalette.ButtonText: "fg.faint",
        QPalette.Highlight: "primary.dim",
        QPalette.HighlightedText: "fg.muted",
        QPalette.Base: "bg.base",
        QPalette.Button: "bg.base",
        QPalette.Window: "bg.base",
    }
    for role, name in disabled.items():
        p.setColor(QPalette.Disabled, role, qcolor(name))

    _CACHE[key] = p
    return QPalette(p)


_QSS = Template("""
/* audian -- generated from theme tokens, no literals. */

/* Only *text* colour is set on every widget.  A blanket background rule
   paints plain container widgets -- layout hosts, spacers, the boxes that
   group a toolbar or status-bar row -- in bg.base even when they sit on a
   bg.surface parent, which is how dark rectangles used to appear inside the
   toolbar and the status bar.  Containers now inherit their parent's
   surface; the windows and scroll areas that really do own a ground still
   paint it below. */
QWidget {
    color: $fg;
}

QMainWindow, QDialog, QScrollArea, QScrollArea > QWidget > QWidget {
    background-color: $bg_base;
}

QLabel {
    background: transparent;
    color: $fg;
}

/* --- toolbars ------------------------------------------------------- */

QToolBar {
    background-color: $bg_surface;
    border: 0px;
    border-bottom: ${hairline}px solid $border;
    spacing: ${s4}px;
    padding: ${s2}px ${s6}px;
    min-height: ${toolbar_height}px;
}
/* The main tool bar states its own metrics.  It used to carry a second,
   widget-level style sheet set from audian.py, and the two were applied at
   different moments: on a theme switch the app sheet landed first and the
   widget sheet second, and the bar re-laid its items 6 px lower, far enough
   that their bottom borders and rounded corners fell outside the bar and
   were clipped.  One rule, one source, one moment. */
QToolBar#audian_toolbar {
    background-color: $bg_surface;
    border: 0px;
    border-bottom: ${hairline}px solid $border;
    /* No padding or spacing: the bar holds a single content widget that
       does its own layout.  Whatever padding is stated here gets folded
       into the bar's item margin, and that margin is recomputed when the
       application style sheet is re-applied -- which is how a theme switch
       used to push the buttons 6 px down and clip them. */
    padding: 0px;
    spacing: 0px;
}
QToolBar::handle, QToolBar::separator:hidden {
    width: 0px;
    height: 0px;
    image: none;
}
QToolBar::separator {
    background-color: $border;
    width: ${hairline}px;
    margin: ${s4}px ${s6}px;
}

/* --- buttons -------------------------------------------------------- */

QToolButton, QPushButton {
    background-color: $bg_surface;
    color: $fg;
    border: ${hairline}px solid $border;
    border-radius: ${radius_control}px;
    padding: ${s4}px ${s8}px;
}
/* Deterministic height, but ONLY on the main toolbar.  A QToolBar stretches
   its buttons to match the tallest item; re-applying a stylesheet to a
   laid-out toolbar drops that and every icon-only button collapses to its
   own hint (21 px against the 32 px it had a moment earlier).  Stating the
   height makes the switched and freshly-built windows agree.

   It must NOT apply to every QToolButton: the channel rail's solo and mute
   buttons are deliberately tiny, and giving them a 26 px floor grew the
   lane pitch from 36 to 44 px, which pushed two channels of a sixteen
   channel stack off the bottom of the window. */
QToolBar#audian_toolbar QToolButton {
    min-height: ${toolbar_button_height}px;
}
QToolButton:hover, QPushButton:hover {
    background-color: $bg_raised;
    border-color: $border_hi;
}
QToolButton:pressed, QPushButton:pressed {
    background-color: $primary_dim;
    border-color: $primary_dim;
    color: $on_primary;
}
QToolButton:checked, QPushButton:checked {
    background-color: $primary_dim;
    border-color: $primary;
    color: $on_primary;
}
QToolButton:disabled, QPushButton:disabled {
    color: $fg_faint;
    border-color: $border;
    background-color: $bg_base;
}
QToolButton::menu-indicator { image: none; }

/* --- menus ---------------------------------------------------------- */

QMenuBar {
    background-color: $bg_surface;
    color: $fg;
    border-bottom: ${hairline}px solid $border;
    padding: ${s2}px ${s4}px;
}
QMenuBar::item {
    background: transparent;
    padding: ${s4}px ${s8}px;
    border-radius: ${radius_control}px;
}
/* Every one of these fills with primary.dim, so every one of them has to
   state its foreground.  Inheriting $fg measured 1.68:1 in the daylight
   theme -- unreadable.  It looked fine in the dark theme only because $fg
   and $on_primary happen to coincide there. */
QMenuBar::item:selected { background-color: $primary_dim; color: $on_primary; }
QMenuBar::item:pressed  { background-color: $primary_dim; color: $on_primary; }

QMenu {
    background-color: $bg_raised;
    color: $fg;
    border: ${hairline}px solid $border;
    border-radius: ${radius_overlay}px;
    padding: ${s4}px;
}
QMenu::item {
    padding: ${s4}px ${s16}px ${s4}px ${s24}px;
    border-radius: ${radius_control}px;
}
QMenu::item:selected { background-color: $primary_dim; color: $on_primary; }
QMenu::item:disabled { color: $fg_faint; }
QMenu::separator {
    height: ${hairline}px;
    background-color: $border;
    margin: ${s4}px ${s6}px;
}
QMenu::indicator { width: ${s12}px; height: ${s12}px; margin-left: ${s6}px; }

QToolTip {
    background-color: $bg_raised;
    color: $fg;
    border: ${hairline}px solid $border;
    border-radius: ${radius_overlay}px;
    padding: ${s4}px ${s6}px;
}

/* --- tabs ----------------------------------------------------------- */

QTabWidget::pane {
    background-color: $bg_surface;
    border: ${hairline}px solid $border;
    top: -${hairline}px;
}
QTabBar {
    background-color: $bg_base;
}
/* Tabs sit on the LEFT (QTabWidget.West).  The selected tab is marked by a
   rule down its leading edge, the same device the channel rail uses, and the
   label is painted horizontally by VerticalTabBar -- Qt would rotate it. */
QTabBar::tab {
    background-color: $bg_base;
    color: $fg_muted;
    border: ${hairline}px solid $border;
    border-right: 0px;
    border-left: ${focus_width}px solid transparent;
    /* no padding: VerticalTabBar sizes and places its own upright label and
       close mark, and style sheet padding would shrink the rect underneath
       them without the bar knowing */
    padding: 0px;
    margin-bottom: ${s2}px;
}
QTabBar::tab:hover { color: $fg; background-color: $bg_surface; }
QTabBar::tab:selected {
    background-color: $bg_surface;
    color: $fg;
    border-left: ${focus_width}px solid $primary;
}
QTabWidget::pane {
    border: 0px;
    border-left: ${hairline}px solid $border;
}

/* --- status bar ----------------------------------------------------- */

QStatusBar {
    background-color: $bg_surface;
    color: $fg_muted;
    border-top: ${hairline}px solid $edge;
}
QStatusBar::item { border: 0px; }
QStatusBar QLabel { color: $fg_muted; }

/* --- inputs --------------------------------------------------------- */

QLineEdit, QAbstractSpinBox, QComboBox, QPlainTextEdit, QTextEdit {
    background-color: $bg_base;
    color: $fg;
    selection-background-color: $primary_dim;
    selection-color: $on_primary;
    border: ${hairline}px solid $border;
    border-radius: ${radius_control}px;
    padding: ${s2}px ${s6}px;
}
QLineEdit, QAbstractSpinBox, QComboBox {
    min-height: ${control_height}px;
    max-height: ${control_height}px;
}
QLineEdit:hover, QAbstractSpinBox:hover, QComboBox:hover {
    border-color: $border_hi;
}
QLineEdit:disabled, QAbstractSpinBox:disabled, QComboBox:disabled {
    color: $fg_faint;
    background-color: $bg_base;
}
QComboBox::drop-down {
    border: 0px;
    width: ${s16}px;
}
QComboBox QAbstractItemView {
    background-color: $bg_raised;
    color: $fg;
    border: ${hairline}px solid $border;
    selection-background-color: $primary_dim;
    selection-color: $on_primary;
    outline: none;
}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {
    background-color: $bg_surface;
    border-left: ${hairline}px solid $border;
    width: ${s16}px;
}
QAbstractSpinBox::up-button:hover, QAbstractSpinBox::down-button:hover {
    background-color: $bg_raised;
}

QCheckBox, QRadioButton { background: transparent; spacing: ${s6}px; }
QCheckBox::indicator, QRadioButton::indicator {
    width: ${s12}px;
    height: ${s12}px;
    border: ${hairline}px solid $border_hi;
    border-radius: ${radius_control}px;
    background-color: $bg_base;
}
QRadioButton::indicator { border-radius: ${s6}px; }
QCheckBox::indicator:checked, QRadioButton::indicator:checked {
    background-color: $primary;
    border-color: $primary;
}

/* --- sliders -------------------------------------------------------- */

QSlider::groove:horizontal {
    background-color: $border;
    height: ${s4}px;
    border-radius: ${s2}px;
}
QSlider::groove:vertical {
    background-color: $border;
    width: ${s4}px;
    border-radius: ${s2}px;
}
QSlider::handle:horizontal, QSlider::handle:vertical {
    background-color: $primary;
    border: 0px;
    border-radius: ${radius_control}px;
}
QSlider::handle:horizontal { width: ${s12}px; margin: -${s4}px 0px; }
QSlider::handle:vertical { height: ${s12}px; margin: 0px -${s4}px; }
QSlider::sub-page:horizontal { background-color: $primary_dim; }

/* --- tables --------------------------------------------------------- */

QTableView, QTreeView, QListView {
    background-color: $bg_surface;
    alternate-background-color: $bg_raised;
    color: $fg;
    gridline-color: $border;
    border: ${hairline}px solid $border;
    selection-background-color: $primary_dim;
    selection-color: $on_primary;
    outline: none;
}
QHeaderView::section {
    background-color: $bg_surface;
    color: $fg_muted;
    border: 0px;
    border-right: ${hairline}px solid $border;
    border-bottom: ${hairline}px solid $border;
    padding: ${s4}px ${s6}px;
}
QTableCornerButton::section {
    background-color: $bg_surface;
    border: 0px;
}

/* --- splitters ------------------------------------------------------ */

/* A divider inside the canvas, not a chrome seam: the handle keeps the
   canvas ground and carries a single rule, so it reads as a line with grab
   room around it rather than as a 4 px slab of border colour. */
QSplitter::handle {
    background-color: $bg_base;
}
QSplitter::handle:horizontal {
    width: ${s6}px;
    border-left: ${hairline}px solid $border;
}
QSplitter::handle:vertical {
    height: ${s6}px;
    border-top: ${hairline}px solid $border;
}
QSplitter::handle:hover { border-color: $edge; }

/* --- scrollbars ----------------------------------------------------- */

QScrollBar:horizontal, QScrollBar:vertical {
    background-color: $bg_base;
    border: 0px;
    margin: 0px;
}
QScrollBar:horizontal { height: ${scrollbar}px; }
QScrollBar:vertical { width: ${scrollbar}px; }
QScrollBar::handle:horizontal, QScrollBar::handle:vertical {
    background-color: $border_hi;
    border-radius: ${radius_control}px;
}
QScrollBar::handle:horizontal { min-width: ${s24}px; margin: ${s2}px; }
QScrollBar::handle:vertical { min-height: ${s24}px; margin: ${s2}px; }
QScrollBar::handle:hover { background-color: $fg_faint; }
QScrollBar::add-line, QScrollBar::sub-line {
    width: 0px;
    height: 0px;
    background: none;
    border: 0px;
}
QScrollBar::add-page, QScrollBar::sub-page { background: none; }

/* --- focus ---------------------------------------------------------- */
/* A focus ring is never removed without a replacement.  The padding is
   reduced by one pixel so the ${focus_width}px ring does not resize the
   control and shift the layout underneath it. */

*:focus { outline: none; }

QToolButton:focus, QPushButton:focus, QCheckBox:focus, QRadioButton:focus,
QLineEdit:focus, QAbstractSpinBox:focus, QComboBox:focus,
QPlainTextEdit:focus, QTextEdit:focus, QSlider:focus,
QTableView:focus, QTreeView:focus, QListView:focus, QTabBar::tab:focus {
    border: ${focus_width}px solid $primary;
    outline: none;
}
QToolButton:focus, QPushButton:focus {
    padding: ${s4_focus}px ${s8_focus}px;
}
QLineEdit:focus, QAbstractSpinBox:focus, QComboBox:focus {
    padding: ${s4_focus}px ${s6_focus}px;
}
""")


def stylesheet() -> str:
    """Return the application QSS for the active theme (cached).

    Covers only what ``QPalette`` cannot express: toolbars, menus, tabs, the
    status bar, inputs, tables, splitters, scrollbars and the focus ring.  It
    is built entirely by interpolating the token table -- there is not a single
    colour literal in it.  No gradients, no box-shadow, no border-radius on
    anything that contains a plot.
    """
    key = f"stylesheet:{current_theme()}"
    cached = _CACHE.get(key)
    if cached is None:
        cached = _QSS.substitute(
            bg_base=token("bg.base"),
            bg_surface=token("bg.surface"),
            bg_raised=token("bg.raised"),
            bg_plot=token("bg.plot"),
            border=token("border"),
            border_hi=token("border.hi"),
            fg=token("fg"),
            fg_muted=token("fg.muted"),
            fg_faint=token("fg.faint"),
            primary=token("primary"),
            primary_dim=token("primary.dim"),
            on_primary=token("on.primary"),
            edge=token("edge"),
            toolbar_button_height=TOOLBAR_BUTTON_HEIGHT,
            bg_lane=token("bg.lane"),
            accent=token("accent"),
            success=token("success"),
            danger=token("danger"),
            s2=S2,
            s4=S4,
            s6=S6,
            s8=S8,
            s12=S12,
            s16=S16,
            s24=S24,
            s4_focus=S4 - (FOCUS_WIDTH - HAIRLINE),
            s6_focus=S6 - (FOCUS_WIDTH - HAIRLINE),
            s8_focus=S8 - (FOCUS_WIDTH - HAIRLINE),
            hairline=HAIRLINE,
            focus_width=FOCUS_WIDTH,
            radius_control=RADIUS_CONTROL,
            radius_overlay=RADIUS_OVERLAY,
            toolbar_height=TOOLBAR_HEIGHT,
            control_height=CONTROL_HEIGHT,
            scrollbar=10,
        )
        _CACHE[key] = cached
    return cached


def apply_pg_config() -> None:
    """Push the pyqtgraph global defaults.  Safe without a ``QApplication``.

    ``antialias=False`` is not cosmetic: on dense polylines it is a 170x paint
    cost.  Individual items that genuinely need smoothing may opt in locally.
    """
    pg.setConfigOptions(
        background=token("bg.plot"),
        foreground=token("fg.muted"),
        antialias=False,
    )


def apply(app: QApplication, theme_name: str | None = None) -> None:
    """Apply the whole design system to *app*.  The single entry point.

    There is exactly one call site in the code base: ``audian.py``'s
    ``audian_cli``, immediately after the ``QApplication`` is constructed and
    before ``Audian()``.  Idempotent and safe to call again to re-theme a
    running application.

    Order matters:

    1. Fusion style -- the only cross-platform style that honours a custom
       ``QPalette`` under the Wayland platform theme.
    2. palette
    3. application font
    4. pyqtgraph config
    5. stylesheet (last, so it wins over the palette where they overlap)
    """
    if theme_name is not None:
        set_theme(theme_name)
    style = QStyleFactory.create("Fusion")
    if style is not None:
        app.setStyle(style)
    app.setPalette(palette())
    app.setFont(font_ui())
    apply_pg_config()
    app.setStyleSheet(stylesheet())


# ---------------------------------------------------------------------------
# Section 8 -- data / categorical palettes
# ---------------------------------------------------------------------------

#: Spectrogram colormaps, perceptually uniform first, jet last and never
#: default.  Verified to resolve with ``pg.colormap.get`` in pyqtgraph 0.14.0.
#: ``CET-L20`` does not exist in this build -- do not add it.
SPECTROGRAM_MAPS: list[str] = [
    # CET-CBL2 leads: colour-blind safe, and its low end is near-black, so
    # the noise floor -- most of a spectrogram -- disappears into the page
    # instead of filling it.  CET-L17 used to lead here and, reversed for a
    # dark floor, painted the whole panel saturated blue.
    "CET-CBL2",
    "viridis",
    "magma",
    "inferno",
    "CET-L16",
    "CET-L17",
    "CET-L1",
    "CET-R4",
]

#: Human labels for a swatch combo, index-aligned with SPECTROGRAM_MAPS.
SPECTROGRAM_MAP_LABELS: list[str] = [
    "CET-CBL2 (colour-blind safe)",
    "viridis (uniform)",
    "magma (uniform)",
    "inferno (uniform)",
    "CET-L16 (uniform)",
    "CET-L17 (uniform, blue)",
    "CET-L1 (greyscale)",
    "jet (legacy - non-uniform)",
]

#: Spectrogram colormaps for the **daylight** theme.  A separate list, not a
#: flipped version of the one above, because most sequential maps cannot be
#: flipped usefully: reversing viridis or magma puts their saturated *yellow*
#: end at the noise floor, and since the noise floor is most of a spectrogram
#: the result is a glaring yellow field -- the worst possible outcome for the
#: one theme that exists to be read in bright sun.
#:
#: These all run from a neutral near-white low end to a dark high end, which
#: is both readable under glare and the long-standing convention for printed
#: spectrograms.  Every entry is a plain pyqtgraph name; which of them get
#: flipped is held in :data:`REVERSED_MAPS`.
SPECTROGRAM_MAPS_LIGHT: list[str] = [
    "CET-L17",
    "CET-L18",
    "CET-L19",
    "CET-L1",
    "CET-CBL2",
]

#: Maps drawn reversed, per theme, so their *low* end -- the noise floor,
#: which is most of a spectrogram -- matches the page.
#:
#: Kept beside the lists rather than encoded into the names so that every
#: entry above stays a valid ``pg.colormap.get`` argument.  Only maps with
#: neutral or near-neutral ends are ever flipped: reversing viridis or magma
#: would put their saturated yellow end at the noise floor.
REVERSED_MAPS: dict[str, frozenset] = {
    # CET-L17 natively runs white -> blue, a light floor, which is a white
    # slab in a dark window
    THEME_DARK: frozenset({"CET-L17"}),
    # both of these natively run dark -> light
    THEME_LIGHT: frozenset({"CET-L1", "CET-CBL2"}),
}

#: Human labels, index-aligned with SPECTROGRAM_MAPS_LIGHT.
SPECTROGRAM_MAP_LABELS_LIGHT: list[str] = [
    # labels name the ramp as it is actually rendered, sampled at five
    # stops rather than guessed from the map's number
    "CET-L17 (uniform, warm to blue)",
    "CET-L18 (uniform, amber to red)",
    "CET-L19 (uniform, blue to red)",
    "greyscale (dark = loud)",
    "CET-CBL2 (colour-blind safe)",
]


def spectrogram_maps() -> list[str]:
    """The colormap names offered by the *active* theme."""
    if current_theme() == THEME_LIGHT:
        return SPECTROGRAM_MAPS_LIGHT
    return SPECTROGRAM_MAPS


def spectrogram_map_labels() -> list[str]:
    """The colormap labels offered by the *active* theme."""
    if current_theme() == THEME_LIGHT:
        return SPECTROGRAM_MAP_LABELS_LIGHT
    return SPECTROGRAM_MAP_LABELS


#: Index into SPECTROGRAM_MAPS used when nothing else is specified.
DEFAULT_SPECTROGRAM_MAP = 0


def spectrogram_colormap(index_or_name: int | str) -> Any:
    """Return a ``pg.ColorMap`` by index into :data:`SPECTROGRAM_MAPS` or by name.

    Out-of-range indices are clamped and an unknown or unloadable name falls
    back to ``SPECTROGRAM_MAPS[0]`` instead of raising -- a bad colormap in a
    config file must not stop the application from opening a file.  Cached.
    """
    maps = spectrogram_maps()
    if isinstance(index_or_name, str):
        name = index_or_name
    else:
        try:
            idx = int(index_or_name)
        except (TypeError, ValueError):
            idx = DEFAULT_SPECTROGRAM_MAP
        idx = max(0, min(len(maps) - 1, idx))
        name = maps[idx]
    reverse = name in REVERSED_MAPS.get(current_theme(), frozenset())

    key = f"cmap:{current_theme()}:{name}"
    cmap = _CACHE.get(key)
    if cmap is None:
        try:
            cmap = pg.colormap.get(name)
        except Exception:
            cmap = pg.colormap.get(SPECTROGRAM_MAPS[DEFAULT_SPECTROGRAM_MAP])
        if reverse:
            cmap = copy.deepcopy(cmap)
            cmap.reverse()
        _CACHE[key] = cmap
    return cmap


#: Eight categorical marker colours, every one measured at >= 4.5:1 against
#: both ``bg.plot`` and ``bg.raised``, ordered for maximum pairwise separation
#: and chosen not to collide with the trace or accent tokens.
#: (contrast on bg.plot / bg.raised)
MARKER_COLORS: list[str] = [
    "#FF6B6B",  # 6.77 / 6.09
    "#5FD98A",  # 10.54 / 9.48
    "#C49BFF",  # 8.48 / 7.63
    "#4FD1C5",  # 10.07 / 9.06
    "#F2E06B",  # 14.00 / 12.59
    "#FF8ACC",  # 8.73 / 7.85
    "#8AB4FF",  # 8.99 / 8.09
    "#FFA657",  # 9.70 / 8.73
]


#: The same eight hues darkened for the light theme, measured >= 4.5:1 on the
#: light ``bg.plot`` and ``bg.raised``.  Index-aligned with MARKER_COLORS.
LIGHT_MARKER_COLORS: list[str] = [
    "#C0392B",  # 5.25 / 5.44
    "#1D7A45",  # 5.17 / 5.35
    "#6B3FBF",  # 6.57 / 6.80
    "#0E7C74",  # 4.88 / 5.06
    "#7A6A00",  # 5.22 / 5.40
    "#B32E7A",  # 5.65 / 5.85
    "#2B6CB0",  # 5.23 / 5.42
    "#A34E00",  # 5.56 / 5.76
]

_MARKER_TABLES: dict[str, list[str]] = {
    THEME_DARK: MARKER_COLORS,
    THEME_LIGHT: LIGHT_MARKER_COLORS,
}


def marker_colors(theme_name: str | None = None) -> list[str]:
    """Return the categorical marker palette for a theme (active one by default)."""
    return _MARKER_TABLES[theme_name or current_theme()]


def marker_color(index: int) -> str:
    """Return a categorical marker colour, wrapping modulo the palette length.

    Theme aware: under the light theme the darkened variants are returned, so
    a marker stays legible without the caller knowing which theme is active.
    """
    palette_ = marker_colors()
    return palette_[int(index) % len(palette_)]


#: Backdrop and hairline ring for the marker swatch icon drawn by
#: ``markerdata.ColorIconEngine``, which used to hardcode ``QColor('black')``.
MARKER_ICON_BG = BG_RAISED
MARKER_ICON_RING = BORDER


# ---------------------------------------------------------------------------
# Contrast checking
# ---------------------------------------------------------------------------


def relative_luminance(color: str) -> float:
    """WCAG 2.1 relative luminance of a token name or ``'#rrggbb'`` string."""
    hex_str = str(_resolve(color)).lstrip("#")
    channels = [int(hex_str[i : i + 2], 16) / 255.0 for i in (0, 2, 4)]
    linear = [
        c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast_ratio(a: str, b: str) -> float:
    """WCAG 2.1 contrast ratio between two colours, from 1.0 to 21.0."""
    la, lb = relative_luminance(a), relative_luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


#: Foreground/background pairs that carry TEXT and must clear 4.5:1.
#: ``fg.faint`` is deliberately absent: it is non-text decoration only.
TEXT_CONTRAST_PAIRS: tuple[tuple[str, str], ...] = (
    ("fg", "bg.base"),
    ("fg", "bg.surface"),
    ("fg", "bg.raised"),
    ("fg", "bg.plot"),
    ("fg.muted", "bg.base"),
    ("fg.muted", "bg.surface"),
    ("fg.muted", "bg.raised"),
    ("fg.muted", "bg.plot"),
    ("primary", "bg.plot"),
    ("primary", "bg.base"),
    ("accent", "bg.plot"),
    ("success", "bg.plot"),
    ("danger", "bg.plot"),
    ("trace.raw", "bg.plot"),
    ("trace.filtered", "bg.plot"),
    ("trace.envelope", "bg.plot"),
)

MIN_CONTRAST = 4.5


def check_contrast(theme_name: str | None = None) -> list[tuple[str, str, float]]:
    """Return every ``(fg, bg, ratio)`` in the active theme that fails 4.5:1.

    An empty list means the palette passes.  Marker colours are checked against
    both ``bg.plot`` and ``bg.raised`` as well.
    """
    table = THEMES[theme_name] if theme_name else TOKENS
    failures: list[tuple[str, str, float]] = []
    for fg_name, bg_name in TEXT_CONTRAST_PAIRS:
        ratio = contrast_ratio(table[fg_name], table[bg_name])
        if ratio < MIN_CONTRAST:
            failures.append((fg_name, bg_name, ratio))
    for i, color in enumerate(marker_colors(theme_name)):
        for bg_name in ("bg.plot", "bg.raised"):
            ratio = contrast_ratio(color, table[bg_name])
            if ratio < MIN_CONTRAST:
                failures.append((f"marker[{i}] {color}", bg_name, ratio))
    return failures


def _report() -> int:
    """Print the contrast table for every theme; return a process exit code."""
    status = 0
    for name, table in THEMES.items():
        print(f"--- {name} theme " + "-" * 40)
        surfaces = ("bg.base", "bg.surface", "bg.raised", "bg.plot")
        header = "token".ljust(18) + "".join(s.ljust(13) for s in surfaces)
        print(header)
        for fg_name in (
            "fg",
            "fg.muted",
            "fg.faint",
            "primary",
            "primary.dim",
            "accent",
            "success",
            "danger",
            "trace.raw",
            "trace.filtered",
            "trace.envelope",
            "trace.zero",
        ):
            row = fg_name.ljust(18)
            for surface in surfaces:
                row += f"{contrast_ratio(table[fg_name], table[surface]):.2f}".ljust(13)
            print(row)
        print("markers:")
        for i, color in enumerate(marker_colors(name)):
            plot = contrast_ratio(color, table["bg.plot"])
            raised = contrast_ratio(color, table["bg.raised"])
            print(f"  {i} {color}  bg.plot {plot:5.2f}   bg.raised {raised:5.2f}")
        failures = check_contrast(name)
        if failures:
            status = 1
            print("FAIL:")
            for fg_name, bg_name, ratio in failures:
                print(f"  {fg_name} on {bg_name}: {ratio:.2f} < {MIN_CONTRAST}")
        else:
            print(f"OK: every text pair clears {MIN_CONTRAST}:1")
        print()
    print(
        "note: fg.faint is intentionally below 4.5:1 and is excluded -- it is\n"
        "      non-text decoration (tick marks, crosshairs) and disabled roles."
    )
    return status


if __name__ == "__main__":
    raise SystemExit(_report())
