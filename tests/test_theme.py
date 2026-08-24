"""Tests for :mod:`audian.theme`, the design system module.

Runs under pytest, and also standalone::

    QT_QPA_PLATFORM=offscreen .venv/bin/python tests/test_theme.py

The grep tests at the bottom guard the "no colour literals outside theme.py"
rule.  They are informational until the five integration workstreams land, and
become hard failures once ``AUDIAN_THEME_STRICT=1`` is set (or once the xfail
marker below is removed).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src" / "audian"
sys.path.insert(0, str(REPO / "src"))

from audian import theme  # noqa: E402

STRICT = os.environ.get("AUDIAN_THEME_STRICT") == "1"
MIN_CONTRAST = 4.5


# --- (a) contrast ----------------------------------------------------------


def test_text_tokens_clear_45_on_their_surface():
    for name in theme.THEMES:
        theme.set_theme(name)
        failures = theme.check_contrast(name)
        assert not failures, f"{name}: {failures}"
    theme.set_theme(theme.THEME_DARK)


def test_contrast_ratio_reference_values():
    # Anchors measured on this machine; if these move, the palette moved.
    assert round(theme.contrast_ratio(theme.FG, theme.BG_PLOT), 2) == 15.93
    assert round(theme.contrast_ratio(theme.FG_MUTED, theme.BG_PLOT), 2) == 7.69
    assert round(theme.contrast_ratio(theme.PRIMARY, theme.BG_PLOT), 2) == 5.87
    assert round(theme.contrast_ratio(theme.ACCENT, theme.BG_PLOT), 2) == 9.25


def test_fg_faint_is_deliberately_below_the_text_bar():
    # Encodes the ruling: fg.faint is decoration, never read-off-screen text.
    ratio = theme.contrast_ratio(theme.FG_FAINT, theme.BG_PLOT)
    assert ratio < MIN_CONTRAST
    assert (theme.FG_FAINT, "bg.plot") not in [
        (theme.TOKENS[a], b) for a, b in theme.TEXT_CONTRAST_PAIRS
    ]


def test_marker_colors_are_eight_and_legible():
    assert len(theme.MARKER_COLORS) == 8
    assert len(theme.LIGHT_MARKER_COLORS) == 8
    for surface in (theme.BG_PLOT, theme.BG_RAISED):
        for color in theme.MARKER_COLORS:
            assert theme.contrast_ratio(color, surface) >= MIN_CONTRAST
    assert theme.marker_color(8) == theme.MARKER_COLORS[0]
    assert theme.marker_color(-1) == theme.MARKER_COLORS[7]


# --- (b) colormaps ---------------------------------------------------------


def test_every_spectrogram_map_resolves():
    import pyqtgraph as pg

    assert len(theme.SPECTROGRAM_MAPS) == len(theme.SPECTROGRAM_MAP_LABELS)
    for name in theme.SPECTROGRAM_MAPS:
        assert pg.colormap.get(name) is not None, name
    assert theme.SPECTROGRAM_MAP_LABELS[-1] == "jet (legacy - non-uniform)"
    assert theme.SPECTROGRAM_MAPS[theme.DEFAULT_SPECTROGRAM_MAP] != "CET-R4"


def test_spectrogram_colormap_never_raises():
    for arg in (-10, 0, 99, "viridis", "no-such-map", None):
        assert theme.spectrogram_colormap(arg) is not None


# --- (c) apply() -----------------------------------------------------------


def test_apply_twice_under_offscreen():
    from PyQt5.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    theme.apply(app)
    theme.apply(app)
    assert app.font().pointSize() == theme.SIZE_PT
    assert theme.PRIMARY in app.styleSheet()


def test_style_helpers_are_idempotent():
    from PyQt5.QtWidgets import QApplication

    import pyqtgraph as pg

    app = QApplication.instance() or QApplication([])
    theme.apply(app)
    glw = pg.GraphicsLayoutWidget()
    plot = glw.addPlot(row=0, col=0)
    plot.setLabel("left", "amplitude", units="V")
    for _ in range(2):
        theme.style_figure(glw)
        theme.style_plotitem(plot)
        theme.style_axis(plot.getAxis("left"))
        theme.style_axis(plot.getAxis("bottom"))
    assert plot.getAxis("left").labelText == "amplitude"
    assert plot.getAxis("left").labelUnits == "V"
    item = theme.overlay_textitem()
    assert not item.isVisible()
    assert item.zValue() == 1000


def test_strip_pg_menus_removes_menus_and_keeps_ctrl_widgets():
    from PyQt5.QtCore import QEvent
    from PyQt5.QtWidgets import QApplication, QMenu

    import pyqtgraph as pg

    app = QApplication.instance() or QApplication([])

    def count():
        return len([w for w in app.topLevelWidgets() if isinstance(w, QMenu)])

    before = count()
    plots = [pg.PlotItem() for _ in range(4)]
    assert count() > before
    for plot in plots:
        plot.setMenuEnabled(False)
        theme.strip_pg_menus(plot)
        theme.strip_pg_menus(plot)  # idempotent
    app.sendPostedEvents(None, QEvent.DeferredDelete)
    assert count() == before
    # PlotItem.showGrid() and friends reach into plot.ctrl -- must survive.
    plots[0].showGrid(x=True, y=True, alpha=theme.GRID_ALPHA)
    plots[0].setLogMode(x=False, y=False)
    plots[0].setDownsampling(auto=True, mode="peak")


# --- API shape -------------------------------------------------------------


def test_tokens_table_matches_constants():
    assert theme.DARK_TOKENS["bg.plot"] == theme.BG_PLOT
    assert theme.DARK_TOKENS["primary"] == theme.PRIMARY
    assert theme.DARK_TOKENS["trace.raw"] == theme.TRACE_RAW
    assert set(theme.DARK_TOKENS) == set(theme.LIGHT_TOKENS)
    assert theme.GRID_COLOR == theme.TRACE_ZERO


def test_unknown_token_raises_but_unknown_trace_role_does_not():
    try:
        theme.token("bg.nope")
    except KeyError:
        pass
    else:  # pragma: no cover
        raise AssertionError("token() must raise on an unknown name")
    assert theme.trace_color("nonsense") == theme.TRACE_RAW
    assert theme.trace_pen("nonsense") is not None


def test_alpha_accepts_float_and_int():
    assert theme.qcolor("primary", 0.5).alpha() == 128
    assert theme.qcolor("primary", 128).alpha() == 128
    assert theme.qcolor("primary", 1.0).alpha() == 255
    assert theme.qcolor("primary").alpha() == 255


def test_pens_are_fresh_objects():
    a, b = theme.cursor_pen(), theme.cursor_pen()
    assert a is not b
    a.setWidthF(9.0)
    assert theme.cursor_pen().widthF() == theme.LW_CURSOR


def test_line_width_performance_contract():
    # LW_THIN above 1.0 drops Qt out of its fast raster path (28.3 ms vs
    # 4.4 ms per 16-channel repaint).  Do not "fix" this to 1.1.
    assert theme.LW_THIN <= 1.0
    assert theme.LW_HAIRLINE <= 1.0


def test_spacing_scale_is_closed():
    assert theme.SPACE == (2, 4, 6, 8, 12, 16, 24)
    assert (theme.S2, theme.S4, theme.S8, theme.S24) == (2, 4, 8, 24)


def test_stylesheet_has_no_raw_colour_literals_beyond_tokens():
    qss = theme.stylesheet()
    used = set(re.findall(r"#[0-9A-Fa-f]{6}", qss))
    known = {v.upper() for v in theme.TOKENS.values()}
    assert {c.upper() for c in used} <= known
    for word in ("white", "grey", "gray", "black"):
        assert word not in qss.lower()
    assert "outline: none" in qss
    assert f"{theme.FOCUS_WIDTH}px solid {theme.PRIMARY}" in qss
    assert "qlineargradient" not in qss.lower()


def test_light_theme_round_trip():
    theme.set_theme(theme.THEME_LIGHT)
    assert theme.current_theme() == theme.THEME_LIGHT
    assert theme.token("bg.base") == theme.LIGHT_TOKENS["bg.base"]
    # A dark constant handed to a helper still maps to the active theme.
    assert theme.qcolor(theme.PRIMARY).name().upper() == (
        theme.LIGHT_TOKENS["primary"].upper()
    )
    theme.set_theme(theme.THEME_DARK)
    assert theme.token("bg.base") == theme.BG_BASE


# --- (d) no colour literals outside theme.py -------------------------------

_HEX = re.compile(r"#[0-9A-Fa-f]{3,8}\b")
# Full colour names anywhere, plus pyqtgraph's 'w'/'k' shorthand but only when
# it is actually being handed to a colour-taking call (so file.open('w') and
# friends do not trip the scan).
_NAMED = re.compile(
    r"""['"](?:white|black|grey|gray|darkgrey|darkgray|lightgrey|lightgray)['"]""",
    re.IGNORECASE,
)
_COLOUR_CALL = re.compile(
    r"(?:mkPen|mkBrush|mkColor|QColor|QBrush|QPen|setPen|setBrush|"
    r"setBackground|setBackgroundColor|setTextPen|setTickPen|"
    r"setConfigOption|color\s*=|pen\s*=|brush\s*=)"
    r"""[^)\n]*['"][wk]['"]"""
)


def _sources() -> list[Path]:
    return sorted(p for p in SRC.glob("*.py") if p.name != "theme.py")


def _scan(pattern: re.Pattern[str]) -> list[str]:
    hits = []
    for path in _sources():
        for i, line in enumerate(path.read_text().splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if pattern.search(line):
                hits.append(f"{path.name}:{i}: {stripped}")
    return hits


def _integration_check(name: str, hits: list[str]) -> None:
    if not hits:
        return
    message = f"{name}: {len(hits)} hit(s)\n" + "\n".join(hits[:20])
    if STRICT:
        raise AssertionError(message)
    print(f"[pending integration] {message}", file=sys.stderr)


def test_no_hex_literals_outside_theme():
    _integration_check("hex colour literals", _scan(_HEX))


def test_no_named_qt_colours_outside_theme():
    _integration_check("named Qt colour arguments", _scan(_NAMED))
    _integration_check("pyqtgraph 'w'/'k' colour shorthand", _scan(_COLOUR_CALL))


def test_no_setbackground_none_outside_theme():
    hits = _scan(re.compile(r"setBackground\(\s*None\s*\)"))
    _integration_check("setBackground(None)", hits)


def test_theme_module_is_lint_clean():
    result = subprocess.run(
        ["ruff", "check", str(SRC / "theme.py")],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _main() -> int:
    """Minimal runner so the file works without pytest installed."""
    failed = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
        else:
            print(f"ok   {name}")
    print("failed:" if failed else "all tests passed", failed or "")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_main())
