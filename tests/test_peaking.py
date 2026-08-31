"""What peaking promises about the colour map, pinned.

Runs offscreen::

    QT_QPA_PLATFORM=offscreen .venv-qt6/bin/python -m pytest tests/test_peaking.py -q

No window is built, for the reason ``tests/test_smoothing.py`` states: every
claim here is about a lookup table or a colour distance, and answering it
through a browser would cost two more top-level widgets in the process,
which is the accumulated state ``todo.md`` records `theme.collect_orphan_widgets`
segfaulting on.  A `QApplication` is needed and nothing else -- `pg.mkColor`
and `pg.colormap.get` are QtGui.

The behaviour *through* a running stack -- that the checkbox and the key are
one object, and that neither ``Shift+C`` nor a theme switch drops the mark --
is pinned in ``tests/test_parameterbar.py``, on a fixture that already
exists.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

import pyqtgraph as pg  # noqa: E402

from audian import theme  # noqa: E402
from audian.panels import PeakingColorMap, peaking_colormap, resolve_colormap  # noqa: E402


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def keep_the_theme():
    """The theme is process-wide; see `tests/test_settings.py`'s own copy."""
    name = theme.current_theme()
    yield
    theme.set_theme(name)


def mark_rgb() -> list[int]:
    value = theme.token("spec.clip").lstrip("#")
    return [int(value[i : i + 2], 16) for i in (0, 2, 4)]


# --------------------------------------------------------------- the wrapper


def test_peaking_off_is_the_plain_map(app):
    """Not an equal map -- the same object `resolve_colormap` hands back.

    The switch is off for every reader who has not asked for it, so the
    common path has to cost one boolean and nothing else.
    """
    for spec in (0, "viridis", theme.spectrogram_maps()[0]):
        assert peaking_colormap(spec, False) is resolve_colormap(spec)
    cmap = resolve_colormap(0)
    assert peaking_colormap(cmap, False) is cmap


def test_peaking_on_is_still_a_pyqtgraph_colormap(app):
    """`ImageItem.setColorMap` raises TypeError on anything else, and
    `ColorBarItem` hands the object it was given straight to it."""
    marked = peaking_colormap(0, True)
    assert isinstance(marked, pg.ColorMap)
    assert isinstance(marked, PeakingColorMap)


@pytest.mark.parametrize("npts", [256, 512])
@pytest.mark.parametrize("alpha", [None, True, False])
def test_only_the_last_lookup_entry_moves(app, npts, alpha):
    """The whole implementation, and the reason it costs nothing per frame.

    `pg.ImageItem.setLevels((zmin, zmax))` maps everything at or above
    `zmax` onto the last entry, so replacing that one entry marks exactly
    the clipped pixels.  Moving the base map's last *stop* instead would
    have blended the warning colour back across the final segment of the
    ramp, which is a different picture: the loud bins gradually the wrong
    colour rather than the clipped ones marked.

    Both consumers ask for 256; 512 is here because nothing in pyqtgraph
    promises they always will.
    """
    for name in theme.spectrogram_maps():
        plain = resolve_colormap(name)
        marked = peaking_colormap(name, True)
        a = plain.getLookupTable(nPts=npts, alpha=alpha)
        b = marked.getLookupTable(nPts=npts, alpha=alpha)
        assert a.shape == b.shape
        assert np.array_equal(a[:-1], b[:-1]), f"{name} moved more than the top"
        assert list(b[-1, :3]) == mark_rgb(), f"{name} was not marked"
        if b.shape[1] == 4:
            assert b[-1, 3] == a[-1, 3], "the mark changed the alpha"


def test_the_mark_does_not_touch_the_map_it_was_made_from(app):
    """`theme.spectrogram_colormap` caches, so a mutated base would mark
    every unpeaked spectrogram in the process as well."""
    plain = resolve_colormap(0)
    before = plain.getLookupTable(nPts=256).copy()
    peaking_colormap(plain, True).getLookupTable(nPts=256)
    assert np.array_equal(plain.getLookupTable(nPts=256), before)
    assert np.array_equal(
        theme.spectrogram_colormap(0).getLookupTable(nPts=256), before
    )


def test_a_lookup_table_of_qcolors_is_handed_back_untouched(app):
    """Nothing asks for it, and a wrong answer is worse than an unmarked one."""
    marked = peaking_colormap(0, True)
    table = marked.getLookupTable(nPts=8, mode=pg.ColorMap.QCOLOR)
    assert len(table) == 8
    assert all(hasattr(c, "red") for c in table)


# ------------------------------------------------------------- the colour


#: Fraction of a 256 entry ramp counted as "the end" of it, in entries.
#:
#: Five per cent.  The pixels the mark sits among are the ones just below
#: the ceiling, not the whole ramp -- a sequential map passes through 255
#: colours and comes close to anything, so a separation asked of the whole
#: ramp is unachievable for every colour there is.
END_SLICE = 13

#: What a lane already paints ON a spectrogram: the two filter cutoff lines
#: and the rubber band (`primary`), the playback cursor (`accent`) and the
#: two annotation hues.  A fill the colour of the cutoff line is a fill that
#: reads as a cutoff line.
OVERLAY_TOKENS = ("primary", "accent", "ann.trial", "ann.pulse")


def lut_hexes(name):
    lut = theme.spectrogram_colormap(name).getLookupTable(nPts=256, alpha=False)
    return ["#%02X%02X%02X" % tuple(int(v) for v in row) for row in lut]


@pytest.mark.parametrize("theme_name", [theme.THEME_DARK, theme.THEME_LIGHT])
def test_the_mark_is_told_from_both_ends_of_every_ramp(app, theme_name):
    """Three constraints, each of them able to sink a colour.

    The mark has to be told from the TOP of every ramp, because that is
    what surrounds it; from the FLOOR, because half a panel is at or below
    the floor by construction (`SpectrogramPlot.fit_levels`) and a mark
    that looks like the floor reads as a hole rather than as a warning; and
    from what the lane already paints on top.

    Scored under the worst of the four vision kinds, against
    `MIN_CATEGORY_SEPARATION`.  This is what rules out the obvious answers:
    red scores 8.71 and orange 3.84 worst-of-six, because the hot end of
    half these ramps IS red or orange.
    """
    theme.set_theme(theme_name)
    clip = theme.token("spec.clip")
    worst = None
    where = ""
    for name in theme.spectrogram_maps():
        hexes = lut_hexes(name)
        ends = hexes[255 - END_SLICE : 255] + hexes[:END_SLICE]
        for kind in theme.VISION_KINDS:
            seen = theme.simulate_cvd(clip, kind)
            for other in ends:
                d = theme.delta_e2000(seen, theme.simulate_cvd(other, kind))
                if worst is None or d < worst:
                    worst, where = d, f"{name}/{kind}"
    assert worst >= theme.MIN_CATEGORY_SEPARATION, (
        f"the clip mark is {worst:.2f} dE2000 from the ramp at {where}, "
        f"under the {theme.MIN_CATEGORY_SEPARATION} floor"
    )


@pytest.mark.parametrize("theme_name", [theme.THEME_DARK, theme.THEME_LIGHT])
def test_the_mark_is_told_from_what_the_lane_already_paints(app, theme_name):
    theme.set_theme(theme_name)
    clip = theme.token("spec.clip")
    for token in OVERLAY_TOKENS:
        worst = min(
            theme.delta_e2000(
                theme.simulate_cvd(clip, kind),
                theme.simulate_cvd(theme.token(token), kind),
            )
            for kind in theme.VISION_KINDS
        )
        assert worst >= theme.MIN_CATEGORY_SEPARATION, (
            f"the clip mark is {worst:.2f} dE2000 from {token}"
        )


def test_the_mark_is_a_token_and_not_a_literal(app):
    """It is read from the table both themes carry, so a page with a
    different ground can answer differently -- and so that
    `tests/test_theme.py`'s no-hex-literals gate covers it."""
    for table in (theme.DARK_TOKENS, theme.LIGHT_TOKENS):
        assert "spec.clip" in table
    for name in theme.THEMES:
        theme.set_theme(name)
        assert theme.token("spec.clip").startswith("#")
