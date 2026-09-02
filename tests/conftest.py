"""Shared teardown for the Qt-backed tests.

Four test modules build real widgets -- browsers, panels, plot items -- against
one `QApplication` that lives for the whole session.  Nothing tears those
widgets down, so at the end of a run they are still alive, owned by Python,
and destroyed by the interpreter's own shutdown rather than by Qt.

That is a race, and it is the one that produced an intermittent SIGSEGV:
every test reported `passed`, then the process died during finalization with a
faulthandler dump naming no test at all.  It reproduced perhaps one run in
five, which is worse than a reliable failure -- a suite that is green four
times out of five teaches everyone to re-run it.

The order matters.  Qt has to finish with an object before Python frees the
wrapper: `deleteLater` alone is not enough, because the events it posts are
only delivered while an event loop or an explicit `sendPostedEvents` runs, and
by the time the interpreter is tearing down there is neither.  So this closes
every remaining top level widget, drains `DeferredDelete` until it stops
producing work, and only then lets the run end.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Qt6 spells every enum member inside its enum class -- Qt.AlignmentFlag.AlignLeft
# rather than Qt.AlignLeft.  PySide6 still mirrors the bare names into the owning
# class as a porting courtesy, so the old spelling keeps working and nothing warns.
# 16 turns that mirror off, which is the only way a test run can tell the two
# apart.  audian itself never sets this: production behaviour is identical either
# way, and this is a lint, not a runtime requirement.
#
# It must be set before anything imports PySide6, and the failure when it is not
# is invisible: shiboken writes its OWN resolved value into this same variable as
# it loads -- the string "True" for the default mode -- after which setdefault
# does nothing and the gate is off while the variable looks deliberately set.
# So "was it already in the environment" cannot tell a developer's choice from
# shiboken's stamp; only "was PySide6 already imported" can.  Hence the two
# lines below and the check further down.
#
# One consequence to know before putting this in CI: under 16, PySide6 6.11.2
# ABORTS the interpreter (SIGABRT, exit 134, no traceback) when a QtWidgets call
# gets a wrong argument type, instead of raising TypeError.  Shiboken builds the
# signature map for its own error path by reading unscoped names, and one of them,
# QListWidgetItem.Type, is gone in this mode.  Nothing here takes that path -- the
# suite has no pytest.raises(TypeError) -- but a test that adds one will die
# rather than fail.
_qt_preloaded = any(m == "PySide6" or m.startswith("PySide6.") for m in sys.modules)
_enum_mode_chosen = None if _qt_preloaded else os.environ.get("PYSIDE6_OPTION_PYTHON_ENUM")
os.environ.setdefault("PYSIDE6_OPTION_PYTHON_ENUM", "16")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pyqtgraph as pg  # noqa: E402

if _enum_mode_chosen is None:
    from PySide6.QtCore import Qt as _Qt

    if hasattr(_Qt, "AlignLeft"):
        raise RuntimeError(
            "scoped-enum gate is off: PySide6 was imported before this conftest "
            "ran, so PYSIDE6_OPTION_PYTHON_ENUM=16 never took effect and the "
            "unscoped enum names it exists to catch would pass unnoticed.  "
            "Set PYSIDE6_OPTION_PYTHON_ENUM explicitly to choose a mode on "
            "purpose."
        )

# --- synthetic drags -------------------------------------------------------
#
# pyqtgraph DROPS mouse move events that arrive too soon after the last one.
# `GraphicsScene._moveEventIsAllowed` compares the clock against
# `1000 / mouseRateLimit` ms -- 10 ms at its default of 100 -- and a move that
# loses that race is delivered to `QGraphicsScene` and to nothing else, so the
# scene never adds the button to `dragButtons` and no drag is ever begun.
#
# A test that posts a whole drag inside one turn of the event loop is well
# inside 10 ms, so whether the drag happens at all comes down to how much
# other work landed between the events.  It reproduced exactly that way: the
# same two drags passed under `-s` and failed under pytest's output capture,
# with the lane geometry identical to the pixel in both runs and
# `dragButtons` simply staying empty.  Earlier notes on this codebase read
# that as "a second drag in the same scene is swallowed", which is the
# symptom rather than the cause -- the first drag is just as fragile, and a
# slow enough machine loses it too.
#
# Zero disables the limit outright, which is what a test wants: every event
# it sends is delivered, and the result stops depending on the clock.  It is
# a process-wide pyqtgraph option and nothing in `src/audian` reads it.
pg.setConfigOption("mouseRateLimit", 0)


def _settings_stamp(path: Path):
    """What the file is now, or None if it is not there.

    Content as well as mtime: a test that writes and then restores the
    timestamp would pass an mtime check, and the failure this guards against
    is the file's *content* being replaced by a test's idea of it.
    """
    try:
        return (path.stat().st_mtime_ns, path.read_bytes())
    except OSError:
        return None


class _ScratchDirs:
    """Stand-in for the `platformdirs` object the application asks for paths.

    `PlatformDirs.user_cache_path` is a property, so it cannot be pointed
    somewhere else on the instance; the instance is replaced instead, in
    every module that imported it by name.
    """

    def __init__(self, base: Path) -> None:
        self.user_config_path = base / "config"
        self.user_cache_path = base / "cache"
        self.user_config_path.mkdir(parents=True, exist_ok=True)
        self.user_cache_path.mkdir(parents=True, exist_ok=True)


@pytest.fixture(scope="session", autouse=True)
def _isolate_settings(tmp_path_factory):
    """Point every persistent store at a scratch directory for the whole run.

    Ten test modules used to do this themselves, five of them covering only
    the JSON half, one never restoring what it replaced, and three
    module-scoped fixtures racing to decide which directory was live when a
    deferred `save_panel_split` finally fired -- so which store a run wrote
    to depended on collection order.  It was not hypothetical: the user's own
    `settings.json` held a `panel-split` value a test had put there.

    Doing it once here, session-scoped and autouse, runs before any module's
    own fixture and makes those redirects harmless duplicates rather than the
    thing standing between a test run and the reader's preferences.  The
    modules that deliberately exercise the redirect machinery -- test_settings
    and test_smoketest -- still work, because they capture and restore
    whatever is installed when they run, which is now this instead of the real
    path.

    Four stores, not two.  The first version of this covered the JSON
    preferences and the QSettings INI and called them "both" -- while
    `RecentFiles` went on writing `~/.cache/audian/recent.json` for every
    window the suite builds, and `CompressedData` went on writing the
    navigator's `-fulltrace.wav` cache beside it.  The developer's own
    recent-files list was ten rows of deleted pytest temporaries.  Both go
    through `version.audian_dirs`, which is therefore what has to move.

    The assertion at the end is the part that cannot rot, but only over the
    files it names: a store nobody redirected is invisible to it by
    construction, which is exactly how the two cache files stayed hidden.
    So it stamps every store this fixture claims to cover, and a new one has
    to be added here as well as redirected.
    """
    from PySide6.QtCore import QSettings

    import audian.audian as audian_app
    import audian.compresseddata as compressed
    import audian.version as version

    real_dirs = version.audian_dirs
    real = [
        audian_app.settings_path(),
        Path(QSettings("audian", "audian").fileName()),
        real_dirs.user_cache_path / audian_app.RecentFiles.file_name,
        real_dirs.user_cache_path / compressed.CompressedData.fulltraces_file,
    ]
    before = {p: _settings_stamp(p) for p in real}

    directory = tmp_path_factory.mktemp("settings")
    scratch = _ScratchDirs(directory)
    for module in (version, audian_app, compressed):
        module.audian_dirs = scratch
    audian_app.settings_path = lambda: directory / "settings.json"
    for fmt in (QSettings.Format.NativeFormat, QSettings.Format.IniFormat):
        for scope in (QSettings.Scope.UserScope, QSettings.Scope.SystemScope):
            QSettings.setPath(fmt, scope, os.fspath(directory))

    yield directory

    moved = [p for p, was in before.items() if _settings_stamp(p) != was]
    if moved:
        pytest.fail(
            "the suite wrote to the real settings store: "
            + ", ".join(str(p) for p in moved)
            + ".  Some code path reaches a store this fixture does not "
            "redirect -- find it rather than widening the comparison."
        )


@pytest.fixture(scope="session", autouse=True)
def _qt_teardown():
    """Let Qt destroy its own objects before the interpreter frees them."""
    yield

    try:
        from PySide6.QtCore import QEvent
        from PySide6.QtWidgets import QApplication
    except Exception:  # pragma: no cover - no Qt in this environment
        return

    app = QApplication.instance()
    if app is None:
        return

    for widget in list(QApplication.topLevelWidgets()):
        try:
            widget.close()
            # Reparenting to None first is what makes the delete reachable for
            # a widget some test left parented to another that is already
            # closed; without it the child outlives the drain below.
            widget.setParent(None)
            widget.deleteLater()
        except RuntimeError:
            # already gone on the C++ side, which is the outcome we want
            pass

    # Drain until it stops producing work rather than a fixed number of
    # passes: deleting one object posts the DeferredDelete for its children.
    for _ in range(10):
        app.processEvents()
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        if not QApplication.topLevelWidgets():
            break
