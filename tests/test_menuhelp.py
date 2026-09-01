"""Hover help in the menus: that it is shown, and that it says something.

Runs offscreen::

    QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_menuhelp.py -q

Two failures this pins, both of which the application shipped with and
neither of which is visible to any other test.

`QMenu` hides action tool tips unless asked, and nothing had ever asked --
so every sentence of hover help in the application was reaching nobody.  It
is one call per menu, submenus included, and a menu added later would
silently miss it.

And a tool tip that only repeats the entry's own label is worse than none:
it costs the reader a hover to learn nothing.  Qt makes that easy to ship by
accident, because `toolTip()` falls back to the text with its mnemonic
stripped -- so an action with an `&` in it *looks* documented from code that
never documented it.  `real_tip` is the check that does not fall for it,
and counting with the naive version put this page at 74% documented when it
was at 39%.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from test_panelsplitter import (  # noqa: E402
    app,  # noqa: F401  - the session QApplication fixture
    open_stack,
)

from audian.plugins import panel_menu_tip  # noqa: E402


@pytest.fixture(scope="module")
def view(app, tmp_path_factory):  # noqa: F811
    yield from open_stack(app, tmp_path_factory.mktemp("menuhelp"), 4)


def real_tip(act) -> bool:
    """A tool tip that says more than the entry's own label."""
    tip = act.toolTip().strip()
    return bool(tip) and tip != act.text().replace("&", "").strip()


def every_menu(window):
    """Each menu and submenu in the bar, with its path."""
    out = []

    def walk(menu, path):
        title = menu.title().replace("&", "")
        here = path + [title] if title else path
        out.append((" › ".join(here), menu))
        for act in menu.actions():
            sub = act.menu()
            if sub is not None:
                walk(sub, here)

    for menu in window.menus:
        walk(menu, [])
    return out


class TestToolTipsAreShown:
    def test_every_menu_shows_them(self, view):
        """Including submenus, which are separate QMenu objects."""
        window = view.window()
        hidden = [
            path
            for path, menu in every_menu(window)
            if not menu.toolTipsVisible()
        ]
        assert hidden == []


class TestTheSpectrogramPageIsDocumented:
    """The page most of the work is done on, and the one denoising is under."""

    def actions_of(self, window, wanted):
        return [act for act, path in window.all_actions() if path == wanted]

    def test_every_entry_says_something(self, view):
        window = view.window()
        acts = self.actions_of(window, "Spectrogram")
        assert acts, "no Spectrogram menu"
        bare = [a.text().replace("&", "") for a in acts if not real_tip(a)]
        assert bare == []

    def test_every_denoising_entry_says_something(self, view):
        window = view.window()
        acts = self.actions_of(window, "Spectrogram › Denoising")
        assert acts, "no Denoising submenu"
        bare = [a.text().replace("&", "") for a in acts if not real_tip(a)]
        assert bare == []

    def test_denoising_sits_with_what_is_computed(self, view):
        """Before the colour entries, not after them.

        Denoising changes the numbers in the buffer and costs a recompute,
        which is what it has in common with the window and the overlap and
        what a colour map has with neither.
        """
        window = view.window()
        spec = next(m for path, m in every_menu(window) if path == "Spectrogram")
        titles = []
        for act in spec.actions():
            if act.menu() is not None:
                titles.append(act.menu().title().replace("&", ""))
            elif not act.isSeparator():
                titles.append(act.text().replace("&", ""))
        assert titles.index("Denoising") < titles.index("Color map")
        assert titles.index("Denoising") > titles.index("Decrease overlap")


class TestPluginEntriesAreDocumented:
    def test_the_bundled_detector_says_what_it_is_for(self, view):
        window = view.window()
        acts = [
            act
            for act, path in window.all_actions()
            if path.startswith("Plugins")
        ]
        assert acts, "no Plugins menu"
        for act in acts:
            assert real_tip(act), act.text()

    def test_dynamic_entries_say_something(self, view):
        """Traces, spectrograms and channels are named after the data, so
        their labels are bare nouns and the tip has to carry the verb."""
        window = view.window()
        for wanted in ("View › Traces", "Spectrogram › Active",
                       "View › Channels › Show channels"):
            acts = [a for a, path in window.all_actions() if path == wanted]
            assert acts, wanted
            for act in acts:
                assert real_tip(act), f"{wanted} > {act.text()}"


class TestPanelMenuTip:
    """`menu_tip`, then the docstring, then a sentence built from the name."""

    def test_menu_tip_wins(self):
        def factory():
            """A docstring."""

        factory.menu_tip = "The explicit one"
        assert panel_menu_tip(factory) == "The explicit one"

    def test_the_docstring_speaks_when_there_is_no_menu_tip(self):
        def factory():
            """First paragraph, which
            runs over two lines.

            Second paragraph, which is not wanted.
            """

        assert panel_menu_tip(factory) == (
            "First paragraph, which runs over two lines."
        )

    def test_a_bare_factory_still_gets_a_sentence(self):
        def audian_thing_panel():
            pass

        tip = panel_menu_tip(audian_thing_panel)
        assert tip and "panel" in tip.lower()
