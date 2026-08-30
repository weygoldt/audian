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
import shutil
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
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    theme.apply(app)
    theme.apply(app)
    assert app.font().pointSize() == theme.SIZE_PT
    assert theme.PRIMARY in app.styleSheet()


def test_style_helpers_are_idempotent():
    from PySide6.QtWidgets import QApplication

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
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QApplication, QMenu

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
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
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


# The rule set this tree actually holds -- pyflakes plus the pycodestyle
# errors -- named here rather than left to whatever the installed ruff
# defaults to.  That default is not a constant: ruff 0.16.5 enables 413 rules
# and finds 18 house-style opinions in theme.py, 209 across src/audian, so a
# bare `ruff check` returns a different verdict on every machine.  Under
# E4,E7,E9,F every module in src/audian passes today, theme.py included.
LINT_RULES = "E4,E7,E9,F"


def _find_ruff():
    """The ruff belonging to the interpreter running this test, else PATH's.

    The suite runs as ``.venv-qt6/bin/python -m pytest``, which leaves the
    venv off PATH, so a bare ``"ruff"`` raises ``FileNotFoundError`` while the
    ruff installed beside that very interpreter goes unused.
    """
    for name in ("ruff", "ruff.exe"):
        beside = Path(sys.executable).parent / name
        if beside.exists():
            return str(beside)
    return shutil.which("ruff")


def test_theme_module_is_lint_clean():
    """theme.py holds :data:`LINT_RULES`.

    A ruff that cannot be found is a missing developer tool, not a defect in
    theme.py, so this skips rather than errors -- and names both places it
    looked, because "ruff not found" with no path in it is the least
    actionable message there is.
    """
    ruff = _find_ruff()
    if ruff is None:
        message = (
            "ruff not found: looked beside the interpreter at "
            f"{Path(sys.executable).parent / 'ruff'} and on PATH"
        )
        if "pytest" in sys.modules:
            import pytest

            pytest.skip(message)
        print(f"[skipped] {message}", file=sys.stderr)
        return
    result = subprocess.run(
        [ruff, "check", "--select", LINT_RULES, str(SRC / "theme.py")],
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


def _relative_luminance(rgb):
    channels = [int(v) / 255.0 for v in rgb[:3]]
    linear = [
        c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def test_every_light_map_is_a_plain_pyqtgraph_name():
    """The reversal flag must not leak into the names.

    It used to be encoded as a '!r' suffix, which quietly made the list
    entries invalid arguments to pg.colormap.get.
    """
    import pyqtgraph as pg

    assert len(theme.SPECTROGRAM_MAPS_LIGHT) == len(theme.SPECTROGRAM_MAP_LABELS_LIGHT)
    for name in theme.SPECTROGRAM_MAPS_LIGHT:
        assert "!" not in name, name
        assert pg.colormap.get(name) is not None, name


def test_spectrogram_noise_floor_matches_the_page():
    """A spectrogram's low end is the noise floor, and it is most of the image.

    It has to sit at the dark end of the ramp under the dark theme and the
    light end under the daylight theme, or the plot is a slab of the opposite
    colour to the window around it -- and, in sun, unreadable.
    """
    try:
        for name, want_dark_floor in (
            (theme.THEME_DARK, True),
            (theme.THEME_LIGHT, False),
        ):
            theme.set_theme(name)
            for map_name in theme.spectrogram_maps():
                colors = theme.spectrogram_colormap(map_name).getColors()
                low = _relative_luminance(colors[0])
                high = _relative_luminance(colors[-1])
                if want_dark_floor:
                    assert low < high, (name, map_name, low, high)
                else:
                    assert low > high, (name, map_name, low, high)
    finally:
        theme.set_theme(theme.THEME_DARK)


def test_daylight_holds_graphics_to_a_higher_contrast_floor():
    """Glare eats contrast, so the light theme dims traces less far."""
    try:
        theme.set_theme(theme.THEME_LIGHT)
        light = theme.waveform_color("raw", selected=False, dense=True)
        light_ratio = theme.contrast_ratio(light.name(), theme.token("bg.plot"))
        assert light_ratio >= theme.MIN_GRAPHIC_CONTRAST_DAYLIGHT - 0.01
        theme.set_theme(theme.THEME_DARK)
        dark = theme.waveform_color("raw", selected=False, dense=True)
        dark_ratio = theme.contrast_ratio(dark.name(), theme.token("bg.plot"))
        assert dark_ratio >= theme.MIN_GRAPHIC_CONTRAST - 0.01
        assert light_ratio > dark_ratio
    finally:
        theme.set_theme(theme.THEME_DARK)


def test_on_primary_is_legible_on_a_checked_button():
    """A checked button is filled with primary.dim in both themes."""
    try:
        for name in (theme.THEME_DARK, theme.THEME_LIGHT):
            theme.set_theme(name)
            ratio = theme.contrast_ratio(
                theme.token("on.primary"), theme.token("primary.dim")
            )
            assert ratio >= 4.5, (name, ratio)
    finally:
        theme.set_theme(theme.THEME_DARK)


def test_every_selection_fill_states_a_legible_foreground():
    """A primary.dim fill must never leave its text to inherit $fg.

    In the daylight theme primary.dim is a dark navy and fg is black: menu
    items, pressed buttons and list selections all rendered black on navy at
    1.68:1.  The dark theme hid it, because there fg and on.primary happen to
    be the same colour.
    """
    import re

    try:
        for name in (theme.THEME_DARK, theme.THEME_LIGHT):
            theme.set_theme(name)
            qss = theme.stylesheet()
            dim = theme.token("primary.dim").lower()
            on_primary = theme.token("on.primary").lower()
            for selector, body in re.findall(r"([^{}]*)\{([^{}]*)\}", qss):
                selector = selector.strip().splitlines()[-1].strip()
                low = body.lower()
                if f"selection-background-color: {dim}" in low:
                    found = re.search(r"selection-color:\s*([^;]+);", body)
                    prop = "selection-color"
                elif f"background-color: {dim}" in low:
                    if "slider" in selector.lower():
                        continue  # a progress fill, it carries no text
                    found = re.search(r"(?<!-)color:\s*([^;]+);", body)
                    prop = "color"
                else:
                    continue
                assert found, f"{name}: {selector} sets no {prop}"
                got = found.group(1).strip().lower()
                assert got == on_primary, f"{name}: {selector} {prop}={got}"
    finally:
        theme.set_theme(theme.THEME_DARK)


def test_on_primary_is_legible_in_both_themes():
    try:
        for name in (theme.THEME_DARK, theme.THEME_LIGHT):
            theme.set_theme(name)
            ratio = theme.contrast_ratio(
                theme.token("on.primary"), theme.token("primary.dim")
            )
            assert ratio >= 4.5, (name, ratio)
    finally:
        theme.set_theme(theme.THEME_DARK)


def test_stacked_rail_row_fits_a_dense_lane():
    """The rail row's height becomes the lane's height.

    The stack grid grants a row whatever it asks for, so a rail row taller
    than CHANNEL_DENSE_HEIGHT does not get clipped -- it makes every lane
    taller.  When the stacked row first went in at its natural size it
    wanted 54 px against a 38 px lane and pushed five of sixteen channels
    below the scroll.
    """
    from audian.databrowser import LevelMeter

    budget = (
        theme.S2  # outer top margin
        + theme.RAIL_NUMBER_HEIGHT
        + theme.RAIL_TOGGLE_HEIGHT
        + LevelMeter.HEIGHT
    )
    assert budget <= theme.CHANNEL_DENSE_HEIGHT, budget


def test_a_chrome_band_is_tall_enough_for_its_own_padding():
    """The tool bar must fit its buttons plus the room it says it leaves.

    It did not.  TOOLBAR_HEIGHT was 36, which is 3 px short of a 30 px
    button with S4 above and below, so the layout paid for the shortfall
    out of the margins: measured, 32 px buttons in a 37 px strip with 2 px
    above and 3 px below, and the 3 px below included the band's own
    hairline -- so a button's bottom border sat two pixels off the rule and
    read as touching it.

    Stated as an equality rather than a `>=` because the band is pinned to
    exactly this height: anything left over would not become padding, it
    would become a gap the buttons are centred in and the numbers would
    stop describing the picture.
    """
    assert theme.TOOLBAR_HEIGHT == theme.TOOLBAR_BUTTON_BOX + 2 * theme.BAND_PAD_V


def test_the_status_bar_leaves_the_same_room_as_the_tool_bar():
    """Both bands are chrome, so both breathe by the same number.

    The status bar had no minimum at all and shrink-wrapped its tallest
    readout, which put its text 2 px off the rule above it.
    """
    qss = theme.stylesheet()
    wanted = theme.CHIP_HEIGHT + 2 * theme.BAND_PAD_V
    assert f"min-height: {wanted}px" in qss, wanted


def test_rail_toggles_have_room_for_their_glyph():
    """An 18x14 button with the generic S4/S8 padding renders empty."""
    qss = theme.stylesheet()
    assert "QToolButton#railToggle" in qss
    assert theme.RAIL_TOGGLE_HEIGHT >= theme.SIZE_SMALL_PT


# --- (e) the annotation palette --------------------------------------------


def test_the_cvd_simulator_reproduces_okabe_ito():
    """A simulator that quietly does nothing makes any palette look safe.

    Okabe-Ito is the most widely reproduced colour-blind-safe qualitative
    palette there is, so its worst mutual pair is a number that can be looked
    up rather than trusted: orange vs reddish purple, published at roughly 8
    dE2000.  This module measures 7.92 and identifies the deficiency as
    **tritanopia**, not deuteranopia -- under deuteranopia that same pair is
    34.1, because orange keeps almost all of its lightness and purple loses
    almost all of its chroma.  Cross-checked against libDaltonLens's published
    Brettel-1997 matrices, which agree to within two sRGB levels per channel.
    """
    a, b, score, kind = theme.okabe_ito_worst_pair()
    assert {a, b} == {"orange", "reddish purple"}, (a, b)
    assert kind == "tritan", kind
    assert 7.7 <= score <= 8.7, score
    # and the simulator must actually move a colour
    assert theme.simulate_cvd("#E69F00", "deutan") != "#E69F00"
    assert theme.simulate_cvd("#E69F00", "normal") == "#E69F00"


def test_a_grey_is_unchanged_by_every_deficiency():
    """The neutral axis is what the two half-planes meet along.

    A grey that moves under simulation means the projection is landing on the
    wrong plane, which is the failure mode that would silently inflate every
    separation score in the table.
    """
    for grey in ("#000000", "#404040", "#808080", "#C8C8C8", "#FFFFFF"):
        for kind in theme.VISION_KINDS:
            out = theme.simulate_cvd(grey, kind)
            r, g, b = (int(out[i : i + 2], 16) for i in (1, 3, 5))
            assert max(r, g, b) - min(r, g, b) <= 2, (grey, kind, out)


def test_delta_e2000_is_zero_for_a_colour_against_itself():
    for value in ("#FF253C", "#009A88", "#F575FF", "#000000"):
        assert theme.delta_e2000(value, value) == 0.0
    # and symmetric, which the rotation term makes non-obvious
    assert round(theme.delta_e2000("#FF253C", "#009A88"), 10) == round(
        theme.delta_e2000("#009A88", "#FF253C"), 10
    )


def test_annotation_palette_separation():
    """Every annotation category a reader has to tell apart clears the floor.

    Worst of {normal, protan, deutan, tritan}, in both themes.  The exempt
    pairs are named in :data:`theme.SEPARATION_EXEMPT` with their reason: a
    chromatic mark and a neutral mark never share a track, and `fault` is a
    filled triangle two tracks away from anything chromatic.
    """
    assert theme.CVD_MODEL == "Brettel-1997"
    for name in theme.THEMES:
        assert not theme.check_separation(name), (name, theme.check_separation(name))
    # the four data categories are the ones that share the top three tracks,
    # and none of them may be exempt from anything
    for a in theme.CATEGORY_ROLES:
        for b in theme.CATEGORY_ROLES:
            if a == b:
                continue
            assert (a, b) not in theme.SEPARATION_EXEMPT, (a, b)


def test_the_worst_category_pair_is_recorded_where_it_can_be_rechecked():
    """Anchors, so a hue edit that erodes the palette shows up as a diff.

    Measured on this machine.  With treatment off the colour channel there
    are three data categories rather than four, so there are three pairs to
    satisfy rather than six -- and the worst pair anywhere is now trial vs
    pulse under protanopia at 19.43 dark / 19.69 daylight, against volley vs
    silence under tritanopia at 17.93 / 17.44 before.  The palette got wider,
    not narrower, which is the point of spending colour on the kind.
    """
    worst = {}
    for name, table in theme.THEMES.items():
        scores = []
        for i, a in enumerate(theme.CATEGORY_ROLES):
            for b in theme.CATEGORY_ROLES[i + 1 :]:
                for kind in theme.VISION_KINDS:
                    scores.append(
                        (
                            theme.delta_e2000(
                                theme.simulate_cvd(
                                    table[theme._ANNOTATION_TOKENS[a]], kind
                                ),
                                theme.simulate_cvd(
                                    table[theme._ANNOTATION_TOKENS[b]], kind
                                ),
                            ),
                            a,
                            b,
                            kind,
                        )
                    )
        worst[name] = min(scores)
    assert worst["dark"][1:] == ("trial", "pulse", "protan"), worst["dark"]
    assert worst["light"][1:] == ("trial", "pulse", "protan"), worst["light"]
    assert round(worst["dark"][0], 2) == 19.43
    assert round(worst["light"][0], 2) == 19.69
    assert min(w[0] for w in worst.values()) >= theme.MIN_CATEGORY_SEPARATION


def test_every_exempt_pair_is_actually_below_the_floor():
    """An exemption that is not needed is a hole waiting for a future edit.

    Below in *either* theme is enough to need the entry: several of these
    clear 15.0 in one theme and not the other -- pulse vs fault is 14.53 dark
    and 22.75 daylight, detection.novel vs session is 17.78 dark and 14.68
    daylight.
    """
    for a, b in theme.SEPARATION_EXEMPT:
        assert a in theme.ANNOTATION_ROLES and b in theme.ANNOTATION_ROLES, (a, b)
        worst = min(
            theme.delta_e2000(
                theme.simulate_cvd(table[theme._ANNOTATION_TOKENS[a]], kind),
                theme.simulate_cvd(table[theme._ANNOTATION_TOKENS[b]], kind),
            )
            for table in theme.THEMES.values()
            for kind in theme.VISION_KINDS
        )
        assert worst < theme.MIN_CATEGORY_SEPARATION, (a, b, worst)


def test_the_palette_spends_colour_on_the_kind_and_not_on_the_treatment():
    """The encoding ruling, stated where the roles live.

    Three hues in the default view -- a trial happened here, a pulse was
    played here, something the log cannot account for was heard here -- and
    none of them names a treatment or a pulse type.  A role that did would be
    the seven-hue palette coming back, so asking for one has to fail loudly
    rather than resolve to something plausible.
    """
    assert theme.CATEGORY_ROLES == ("trial", "pulse", "detection.novel")
    for gone in ("volley", "resting", "silence", "baseline", "localization"):
        assert gone not in theme.ANNOTATION_ROLES
        try:
            theme.annotation_color(gone)
        except KeyError:
            pass
        else:  # pragma: no cover - only reached when the gate has rotted
            raise AssertionError(f"{gone} still resolves to a colour")
    for name in theme.THEMES:
        theme.set_theme(name)
        hues = {theme.annotation_color(r) for r in theme.CATEGORY_ROLES}
        assert len(hues) == 3, (name, hues)
    theme.set_theme(theme.THEME_DARK)


def test_a_treatment_letter_is_knocked_out_at_reading_contrast():
    """Treatment moved off the colour channel and onto a letter.

    A letter is READ, not seen, so it needs text contrast and not the graphic
    floor -- and it is drawn over a waveform, where a coloured glyph is at the
    mercy of whatever the signal is doing under it.  So it is knocked out of a
    solid chip in the layer's own hue, and what the chip has to clear is the
    glyph against itself: 4.99 / 5.35 in dark, 6.97 / 5.19 in daylight.
    """
    for name in theme.THEMES:
        theme.set_theme(name)
        for role in theme.CATEGORY_ROLES:
            chip, glyph = theme.annotation_letter(role)
            assert chip == theme.annotation_color(role)
            assert glyph == theme.token("bg.plot")
            ratio = theme.contrast_ratio(chip, glyph)
            assert ratio >= MIN_CONTRAST, (name, role, ratio)
    theme.set_theme(theme.THEME_DARK)


def test_annotation_colors_clear_the_graphic_floor():
    """Every annotation mark is a graphic on a plot ground, in both themes.

    Checked against bg.raised as well as bg.plot: the layer chips and the
    legend icons sit on the raised surface, and it is the deepest of the three.
    """
    for name in theme.THEMES:
        theme.set_theme(name)
        floor = theme.min_graphic_contrast()
        for role in theme.ANNOTATION_ROLES:
            value = theme.annotation_color(role)
            for ground in ("bg.plot", "bg.surface", "bg.raised"):
                ratio = theme.contrast_ratio(value, theme.token(ground))
                assert ratio >= floor, (name, role, ground, ratio)
    theme.set_theme(theme.THEME_DARK)


def test_no_annotation_hue_collides_with_a_painted_trace_colour():
    """The one confusion this design cannot tolerate is annotation-as-signal.

    Compared against what the lanes actually PAINT -- the dimmed traces
    :func:`theme.waveform_color` produces -- not against the undimmed tokens,
    because the dimmed value is the one on screen.
    """
    for name in theme.THEMES:
        painted = theme.painted_trace_colors(name)
        assert len(painted) == len(theme.PAINTED_TRACE_COLORS)
        table = theme.THEMES[name]
        for role in theme.CATEGORY_ROLES:
            value = table[theme._ANNOTATION_TOKENS[role]]
            for label, drawn in painted.items():
                assert value.upper() != drawn.upper(), (name, role, label)
                score = theme.delta_e2000(value, drawn)
                assert score >= theme.MIN_ANNOTATION_SEPARATION, (
                    name,
                    role,
                    label,
                    score,
                )
    assert theme.current_theme() == theme.THEME_DARK


def test_the_annotation_tokens_do_not_repoint_an_existing_one():
    """Why only the three chromatic roles get tokens.

    ``_BY_VALUE`` is value-keyed and the later key wins, so an ``ann.novel``
    token equal to FG would make every ``theme.FG`` call resolve through the
    annotation role and change the colour of unrelated text under the light
    theme.  (``on.primary`` already shadows ``fg`` that way, deliberately and
    harmlessly -- both are light in both themes.  A role that is dark in one
    theme would not be harmless.)  So the ink and the neutral roles stay as
    lookups into existing tokens and only the two chromatic hues are new.
    """
    ann = {k: v for k, v in theme.DARK_TOKENS.items() if k.startswith("ann.")}
    assert set(ann) == {"ann.trial", "ann.pulse"}
    others = {
        v.upper() for k, v in theme.DARK_TOKENS.items() if not k.startswith("ann.")
    }
    for name, value in ann.items():
        assert value.upper() not in others, name
        assert theme._BY_VALUE[value.upper()] == name
    for name, value in theme.LIGHT_TOKENS.items():
        if name.startswith("ann."):
            assert value.upper() not in {
                v.upper()
                for k, v in theme.LIGHT_TOKENS.items()
                if not k.startswith("ann.")
            }, name


def test_every_annotation_role_resolves_in_both_themes():
    for name in theme.THEMES:
        theme.set_theme(name)
        for role in theme.ANNOTATION_ROLES:
            assert re.fullmatch(r"#[0-9A-Fa-f]{6}", theme.annotation_color(role))
    theme.set_theme(theme.THEME_DARK)
    try:
        theme.annotation_color("volleys")
    except KeyError:
        pass
    else:  # pragma: no cover
        raise AssertionError("an unknown role must fail loudly, not fall back")


def test_a_predicted_mark_differs_by_dash_and_never_by_hue():
    """Colour is never the difference between predicted and observed.

    A predicted volley pulse in a different colour would read as a different
    stimulus, so the only pen difference is the [2, 2] dash.
    """
    from PySide6.QtCore import Qt

    observed = theme.annotation_pen("trial")
    predicted = theme.annotation_pen("trial", observed=False)
    assert observed.color().name() == predicted.color().name()
    assert observed.style() == Qt.PenStyle.SolidLine
    assert predicted.style() != Qt.PenStyle.SolidLine
    assert predicted.dashPattern() == [2.0, 2.0]


def test_an_unvalidated_alignment_is_dashed_and_hatched_never_faded():
    """Trust is carried by dash and hatch because alpha does not survive glare.

    Every daylight mark collapses to 1.48-1.62:1 under 640 nit veiling, so an
    opacity reduction stops meaning anything outdoors -- which is where this
    tool is used.
    """
    from PySide6.QtCore import Qt

    for name in theme.THEMES:
        theme.set_theme(name)
        for role in theme.ANNOTATION_ROLES:
            p = theme.annotation_pen(role, unvalidated=True)
            assert p.style() != Qt.PenStyle.SolidLine, (name, role)
            assert p.color().alpha() == 255, (name, role)
            b = theme.annotation_brush(role, unvalidated=True)
            assert b.style() == Qt.BrushStyle.BDiagPattern, (name, role)
            assert b.color().alpha() == 255, (name, role)
            assert theme.annotation_brush(role).style() == Qt.BrushStyle.SolidPattern
    theme.set_theme(theme.THEME_DARK)


def test_dim_color_constants_are_untouched():
    """Nothing here may buy legibility out of the waveform's contrast budget."""
    assert theme.TRACE_DIM_MIX == 0.65
    assert theme.TRACE_DIM_MIX_SPARSE == 0.35
    assert theme.MIN_GRAPHIC_CONTRAST == 3.0
    assert theme.MIN_GRAPHIC_CONTRAST_DAYLIGHT == 4.5
