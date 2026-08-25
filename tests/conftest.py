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

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session", autouse=True)
def _qt_teardown():
    """Let Qt destroy its own objects before the interpreter frees them."""
    yield

    try:
        from PyQt5.QtCore import QEvent
        from PyQt5.QtWidgets import QApplication
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
        app.sendPostedEvents(None, QEvent.DeferredDelete)
        if not QApplication.topLevelWidgets():
            break
