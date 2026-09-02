"""Every action, every key it answers to, pinned against a golden file.

The keyboard *is* the interface here: the behaviour contract for this
application is ~100 bindings deep, and almost none of them are reachable
from the offscreen smoke test, which clicks methods rather than pressing
keys.  A binding that quietly stops working is invisible to every other test
in this suite.

That matters for the Qt6 port specifically.  Qt6 changed how a shortcut is
arbitrated between a window action and a focused widget that wants the same
key, and audian leans on winning those arguments -- the digits `1`-`9` pick
a label category while a spin box in the parameter bar is perfectly happy to
consume a digit, and `S`/`M` are claimed by a rail row through a
`ShortcutOverride` filter.  None of that is expressed anywhere except in the
running application.

So this snapshots the whole inventory -- name, menu path, key sequences,
checkable, checked -- and compares it to a file generated on PyQt5 before
the migration started.  It is a characterisation test: it does not claim the
inventory is *right*, only that it is what it was.  When a binding is
deliberately changed, the golden file is regenerated in the same commit and
the diff is the review.

    AUDIAN_REGENERATE_GOLDEN=1 .venv/bin/python -m pytest \
        tests/test_actioninventory.py

`enabled` and `visible` are deliberately NOT recorded.  They depend on which
recording is open -- `disable_unused_range_actions` turns 20 actions off for
a file with no spectrogram -- and that is a separate bug with its own fix,
not something to freeze.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PySide6.QtCore import QEvent, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from audian import theme  # noqa: E402

#: First generated on PyQt5 at the commit the migration branched from, then
#: regenerated once on PySide6.  That regeneration moved exactly two lines:
#: `open_files` gains "Open" beside "Ctrl+O", because Qt6's
#: `QKeySequence.StandardKey.Open` includes the XF86Open media key and Qt5's
#: did not.  Ctrl+O is untouched, nothing else in 124 actions moved, and the
#: other nineteen StandardKey values the application uses are byte-identical
#: across the two versions.
GOLDEN = Path(__file__).parent / "data" / "action-inventory.json"

RATE = 8000
FRAMES = RATE * 4
CHANNELS = 2


def pump(seconds):
    end = time.monotonic() + seconds
    app = QApplication.instance()
    while time.monotonic() < end:
        app.processEvents()
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        time.sleep(0.005)


@pytest.fixture(scope="module")
def window(tmp_path_factory):
    """The whole application on a two-channel synthetic recording.

    Two channels rather than one: a single-channel file takes a different
    branch through the channel actions, and two is the smallest recording
    that builds the per-channel entries this inventory is meant to catch.
    """
    soundfile = pytest.importorskip("soundfile")
    import audian.audian as audian_app
    from audian.plugins import Plugins

    directory = tmp_path_factory.mktemp("actions")
    signal = np.zeros((FRAMES, CHANNELS), dtype=np.float32)
    for c in range(CHANNELS):
        signal[:, c] = 0.1 * np.sin(np.arange(FRAMES) / (50.0 + c))
    recording = directory / "rec.wav"
    soundfile.write(recording, signal, RATE)

    app = QApplication.instance() or QApplication([])

    theme.apply(app)
    plugins = Plugins()
    plugins.load_plugins()
    win = audian_app.Audian([str(recording)], {}, plugins, [], 0, None, False, 0, None)
    win.resize(1200, 900)
    win.show()
    pump(2.0)

    yield win

    win.close()
    win.setParent(None)
    win.deleteLater()
    pump(0.3)


def inventory(win) -> dict:
    """Name -> {path, keys, checkable, checked} for every action there is.

    Two sources, because they do not agree: `all_actions()` walks the menu
    tree and so finds what a reader can discover, while `vars(win.acts)`
    holds the attribute bag every module reaches into by name.  An action in
    the bag but not the menus is one only a key reaches; the difference is
    itself worth freezing.
    """
    by_id = {}
    for act, path in win.all_actions():
        by_id[id(act)] = {"path": path}

    rows = {}
    for name, act in sorted(vars(win.acts).items()):
        if name.startswith("__") or not hasattr(act, "shortcuts"):
            continue
        rows[name] = {
            "text": act.text().replace("&", ""),
            "path": by_id.get(id(act), {}).get("path", ""),
            "in_menus": id(act) in by_id,
            "keys": sorted(s.toString() for s in act.shortcuts()),
            "checkable": act.isCheckable(),
            "checked": act.isChecked(),
        }
    return rows


def test_the_action_inventory_is_what_it_was(window):
    """Every action still answers to the keys it answered to on PyQt5."""
    current = inventory(window)

    if os.environ.get("AUDIAN_REGENERATE_GOLDEN"):
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n")
        pytest.skip(f"regenerated {GOLDEN.name} with {len(current)} actions")

    assert GOLDEN.exists(), f"{GOLDEN} missing; regenerate it with AUDIAN_REGENERATE_GOLDEN=1"
    expected = json.loads(GOLDEN.read_text())

    # Report the whole difference at once.  A binding change usually moves
    # several entries, and finding them one failed run at a time is how a
    # migration loses an afternoon.
    missing = sorted(set(expected) - set(current))
    added = sorted(set(current) - set(expected))
    changed = {
        name: {"was": expected[name], "now": current[name]}
        for name in sorted(set(expected) & set(current))
        if expected[name] != current[name]
    }

    assert not missing, f"actions that no longer exist: {missing}"
    assert not added, f"actions that appeared: {added}"
    assert not changed, "bindings changed:\n" + json.dumps(changed, indent=2)


#: Actions this sweep must not fire, and why.  Everything not named here is
#: expected to survive being triggered on a loaded two-channel recording.
UNSWEEPABLE = {
    # Ends the process or the document under the sweep's feet.
    "quit": "quits",
    "close": "closes the tab being tested",
    # Opens a native modal dialog, which blocks with no event loop to close it.
    "open_files": "modal file dialog",
    "new_tab": "modal file dialog",
    "load_annotations": "modal file dialog",
    "save_window": "modal file dialog",
    "save_region": "modal file dialog",
    "screen_shot": "modal file dialog",
    "about": "modal message box",
}


def test_every_action_survives_being_triggered(window):
    """Fire all of them and report every one that raised, not just the first.

    The inventory above proves a key is still *bound*.  It says nothing
    about whether the thing behind it still runs, and the Qt6 changes most
    likely to break a handler -- a mouse button flag compared against an
    int, an event whose `pos()` became a `QPointF`, an enum that is no
    longer an int -- all fail inside the slot rather than at connection
    time.

    So: trigger everything, catch everything, and print the whole list.
    Actions that open a modal dialog or end the session are named in
    UNSWEEPABLE with a reason; that list is the honest statement of what
    this does not cover.

    The theme is put back afterwards.  `daylight_mode` is one of the actions
    fired, and the theme is process-wide state, so leaving it flipped makes
    every later module that switches themes assert against a switch that was
    already made -- which is a failure in a test that is not this one, blamed
    on a file that did nothing wrong.
    """
    app = QApplication.instance()
    failures = []
    fired = 0
    theme_before = theme.current_theme()
    # The window state is put back for the same reason the theme is: the
    # sweep fires `maximize_window` and `fullscreen_window`, and a window
    # left full screen is a different size for every test after this one --
    # in this module and, through the shared QApplication, in the geometry
    # a later module measures.
    state_before = window.windowState()

    for name, act in sorted(vars(window.acts).items()):
        if name.startswith("__") or not hasattr(act, "trigger"):
            continue
        if name in UNSWEEPABLE:
            continue
        try:
            act.trigger()
            app.processEvents()
            fired += 1
        except Exception as exc:  # noqa: BLE001 - the whole point is breadth
            failures.append(f"{name}: {type(exc).__name__}: {exc}")

        # Close whatever it opened, so the next trigger starts from the
        # window rather than from a stacked dialog.
        for widget in QApplication.topLevelWidgets():
            if widget is not window and widget.isVisible():
                widget.close()
        app.processEvents()

    if theme.current_theme() != theme_before:
        window.set_app_theme(theme_before)
        app.processEvents()
    if window.windowState() != state_before:
        window.setWindowState(state_before)
        app.processEvents()

    assert not failures, f"{len(failures)} of {fired} actions raised:\n  " + "\n  ".join(
        failures
    )
    # A sweep that silently stopped finding actions would pass forever.
    assert fired >= 100, f"only {fired} actions fired; the inventory has 128"
    assert theme.current_theme() == theme_before, "the sweep left the theme switched"
    assert window.windowState() == state_before, (
        f"the sweep left the window in {window.windowState()!r}, not {state_before!r}"
    )


def test_every_bound_key_is_unique_within_its_context(window):
    """Two actions on one key means one of them silently never fires.

    Qt resolves an ambiguous window-scoped shortcut by firing neither and
    warning, which is the kind of failure that reaches a user as "that key
    stopped working".  This is not a characterisation assert -- it is a
    property that should hold whatever the inventory says.
    """
    seen = {}
    clashes = []
    for name, row in inventory(window).items():
        for key in row["keys"]:
            if not key:
                continue
            if key in seen:
                clashes.append(f"{key}: {seen[key]} and {name}")
            else:
                seen[key] = name
    assert not clashes, "keys bound twice:\n  " + "\n  ".join(clashes)


# --- full screen -----------------------------------------------------------


def test_full_screen_puts_the_window_back_the_way_it_was(window):
    """F11 twice is a round trip, from maximized as well as from normal.

    The trap this pins is `showNormal()`, which is the obvious way to
    write the second half and the wrong one: measured on Qt 6.11, it
    clears `WindowMaximized` along with `WindowFullScreen`, so a maximized
    window comes back merely restored and the reader who pressed F11 twice
    has lost the size they started with.  Flipping the one bit -- what
    `Audian.toggle_fullscreen` does -- leaves the other alone.

    Offscreen reflects both flags back, so this is a real assertion here
    and not a statement about the platform plugin.
    """
    app = QApplication.instance()
    act = window.acts.fullscreen_window
    before = window.windowState()
    try:
        for maximized in (False, True):
            window.setWindowState(
                (before | Qt.WindowState.WindowMaximized)
                if maximized
                else (before & ~Qt.WindowState.WindowMaximized)
            )
            app.processEvents()
            start = window.windowState()
            assert not window.isFullScreen()

            act.trigger()
            app.processEvents()
            assert window.isFullScreen(), "F11 did not reach full screen"
            assert bool(window.windowState() & Qt.WindowState.WindowMaximized) is (
                maximized
            ), "going full screen changed the maximized state"

            act.trigger()
            app.processEvents()
            assert not window.isFullScreen(), "F11 did not leave full screen"
            assert window.windowState() == start, (
                f"from maximized={maximized}, the round trip landed in "
                f"{window.windowState()!r} rather than {start!r}"
            )
    finally:
        window.setWindowState(before)
        app.processEvents()


def test_the_way_out_of_full_screen_is_said_on_the_way_in(window):
    """With no title bar there is no close button, so the key is stated.

    The status bar is the only chrome that can carry it -- the reader has
    just asked for the window's own to go away.
    """
    app = QApplication.instance()
    before = window.windowState()
    try:
        window.messages.clear()
        window.acts.fullscreen_window.trigger()
        app.processEvents()
        said = " ".join(message for _level, message in window.messages)
        assert "F11" in said, f"entering full screen said {said!r}"

        window.messages.clear()
        window.acts.fullscreen_window.trigger()
        app.processEvents()
        assert not window.messages, "leaving full screen should say nothing"
    finally:
        window.setWindowState(before)
        app.processEvents()
