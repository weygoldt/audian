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

from PyQt5.QtCore import QEvent, QSettings  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

from audian import theme  # noqa: E402

#: Generated on PyQt5 at the commit the migration branched from.
GOLDEN = Path(__file__).parent / "data" / "action-inventory.json"

RATE = 8000
FRAMES = RATE * 4
CHANNELS = 2


def pump(seconds):
    end = time.monotonic() + seconds
    app = QApplication.instance()
    while time.monotonic() < end:
        app.processEvents()
        app.sendPostedEvents(None, QEvent.DeferredDelete)
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

    original = audian_app.settings_path
    home = Path(QSettings("audian", "audian").fileName()).parent.parent
    audian_app.settings_path = lambda: directory / "settings.json"
    for fmt in (QSettings.NativeFormat, QSettings.IniFormat):
        for scope in (QSettings.UserScope, QSettings.SystemScope):
            QSettings.setPath(fmt, scope, os.fspath(directory))

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
    audian_app.settings_path = original
    for fmt in (QSettings.NativeFormat, QSettings.IniFormat):
        for scope in (QSettings.UserScope, QSettings.SystemScope):
            QSettings.setPath(fmt, scope, os.fspath(home))


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

    assert GOLDEN.exists(), f"{GOLDEN} missing; run with --regenerate on PyQt5 first"
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
