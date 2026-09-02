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
from platformdirs import PlatformDirs

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


# --- proof that the redirect below actually holds --------------------------
#
# The first version of this guard stamped `(mtime_ns, content)` of the four
# real stores when the session began and compared them when it ended.  That
# compares a file against its past, which cannot distinguish *this* process
# from any other -- and on 2026-09-01 it did not.  The reader had audian open
# on their own recordings while the suite ran; the GUI saved a preference at
# 21:11:08 during a run that spanned 21:02 to 21:16; and the suite reported
# "the suite wrote to the real settings store" about a write it had not made.
# A guard that fails when the owner uses their own application is a guard
# everybody learns to re-run, which is the same lesson the teardown note above
# is written to avoid.
#
# So ask the causal question instead: did *this interpreter* write there?  An
# audit hook sees every `open` and every rename the process makes, and a
# directory prefix answers it in one string comparison.  Three things improve
# at once.  It cannot see another process, so the false failure is gone by
# construction.  It knows which test was running, so it names the culprit
# rather than only the file.  And it watches the two directories rather than
# four enumerated paths, so a store nobody thought to list is covered anyway
# -- which is exactly how `recent.json` and the fulltrace cache stayed hidden
# from the first version until someone went looking.
#
# The cost is 0.82 us per `open`, measured over 12,000 of them: about 0.08 s
# across a hundred thousand opens, against a suite that runs for 554 s.
#
# Both the JSON preferences and the QSettings INI live in `user_config_path`,
# and `recent.json` and the fulltrace cache in `user_cache_path`, so those two
# directories cover all four.  The fixture re-derives the four exact paths and
# refuses to run if any of them has escaped the watch, so this stays true by
# assertion rather than by memory.
_audian_dirs = PlatformDirs("audian", "janscience")
_REAL_STORE_DIRS = tuple(
    sorted(
        {
            os.fspath(directory) + os.sep
            for path in (_audian_dirs.user_config_path, _audian_dirs.user_cache_path)
            for directory in (path, Path(os.path.realpath(path)))
        }
    )
)

_store_writes: list[tuple[str, str]] = []
_current_test = "collection"


def pytest_runtest_logstart(nodeid, location):
    """Remember which test is running, so a store write can name it."""
    global _current_test
    _current_test = nodeid


def _record_store_write(event, args):
    """Note any write this process makes inside the reader's own audian dirs.

    Three events, because a store can be lost three ways.  `open` is the
    obvious one.  `os.rename` is the one that matters most: `replace_atomically`
    writes a temporary file beside the target and renames it over, so a hook
    watching only `open` would see the temporary name and miss every settings
    write there is.  And `os.remove`, because a test that deletes the reader's
    label vocabulary has destroyed it just as thoroughly as one that rewrites
    it, while opening nothing at all.
    """
    if event == "open":
        path = args[0]
        if not isinstance(path, str) or not path.startswith(_REAL_STORE_DIRS):
            return
        mode, flags = args[1], args[2]
        if (isinstance(mode, str) and any(c in mode for c in "wxa+")) or (
            isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT)
        ):
            _store_writes.append((path, _current_test))
    elif event in ("os.rename", "os.remove"):
        # rename names its destination second; remove names its target first.
        target = args[1] if event == "os.rename" else args[0]
        if isinstance(target, str) and target.startswith(_REAL_STORE_DIRS):
            _store_writes.append((target, _current_test))


sys.addaudithook(_record_store_write)


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

    The redirect is the fix; the audit hook above is what proves it held.
    The two are checked against each other on the way in -- every store this
    fixture redirects must fall inside a directory the hook watches, or the
    run stops -- so a fifth store cannot be redirected here and left with
    nothing watching whether the redirect worked.
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
    unwatched = [p for p in real if not os.fspath(p).startswith(_REAL_STORE_DIRS)]
    if unwatched:
        pytest.fail(
            "the write guard is not watching a store this fixture redirects: "
            + ", ".join(str(p) for p in unwatched)
            + ".  It watches "
            + ", ".join(sorted(_REAL_STORE_DIRS))
            + ", so a store that resolves outside them would be redirected "
            "with nothing left to prove the redirect held."
        )

    directory = tmp_path_factory.mktemp("settings")
    scratch = _ScratchDirs(directory)
    for module in (version, audian_app, compressed):
        module.audian_dirs = scratch
    audian_app.settings_path = lambda: directory / "settings.json"
    for fmt in (QSettings.Format.NativeFormat, QSettings.Format.IniFormat):
        for scope in (QSettings.Scope.UserScope, QSettings.Scope.SystemScope):
            QSettings.setPath(fmt, scope, os.fspath(directory))

    yield directory

    if _store_writes:
        seen = dict.fromkeys(_store_writes)
        pytest.fail(
            "the suite wrote to the reader's own audian directories:\n"
            + "\n".join(f"  {path}\n      written during {test}" for path, test in seen)
            + "\nSome code path reaches a store this fixture does not redirect "
            "-- find it rather than narrowing the watch."
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
