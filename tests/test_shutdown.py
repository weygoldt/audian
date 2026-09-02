"""What happens when the window is closed the way a window manager closes it.

Audian used to defend two exit paths by hand -- `Ctrl+Q` and closing a tab
both called `flush_labels()` before letting go -- and the window manager's
close button was a third that called neither.  It goes through Qt's close
machinery, and there was no `closeEvent` anywhere in the tree for that
machinery to reach.  So on the most common exit gesture of all:

* the pending label save went with the event loop,
* `CompressedData.close()` never ran, leaving non-daemon children for
  `multiprocessing`'s exit handler to join -- which is a hang, not a leak,
  while a large recording is still compressing,
* `PlayAudio.close()` was reachable only from `Audian.__del__`, i.e. never
  reliably.

`Audian.closeEvent` now runs that teardown, and `quit` goes through it
rather than beside it, so the three gestures are one path.

These assert on effects rather than on calls -- an override that exists, a
released recording, a sidecar on disk, a daemon flag -- because the names on
this path have moved once already and a spy on a method name is satisfied by
any rename that keeps it.

The label window was always narrow: the queued zero-timer save fires on the
next turn of the loop, so it is lost only if nothing turns the loop again,
which is what `exec()` returning does.  The child processes are the part with
teeth.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PySide6.QtCore import QEvent  # noqa: E402
from PySide6.QtGui import QCloseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from audian import theme  # noqa: E402

RATE = 8000
FRAMES = RATE * 4


def pump(seconds):
    end = time.monotonic() + seconds
    app = QApplication.instance()
    while time.monotonic() < end:
        app.processEvents()
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        time.sleep(0.005)


@pytest.fixture
def window(tmp_path):
    """A window on a one-channel recording, torn down by the test itself."""
    soundfile = pytest.importorskip("soundfile")
    import audian.audian as audian_app
    from audian.plugins import Plugins

    signal = (0.1 * np.sin(np.arange(FRAMES) / 50.0)).astype(np.float32)
    recording = tmp_path / "rec.wav"
    soundfile.write(recording, signal, RATE)

    app = QApplication.instance() or QApplication([])
    theme.apply(app)
    plugins = Plugins()
    plugins.load_plugins()
    win = audian_app.Audian([str(recording)], {}, plugins, [], 0, None, False, 0, None)
    win.resize(900, 600)
    win.show()
    pump(2.0)

    yield win

    try:
        win.setParent(None)
        win.deleteLater()
    except RuntimeError:
        pass
    pump(0.3)


def test_the_window_has_a_close_event():
    """Qt's close machinery has to reach something.

    This is the whole bug in one assertion: `QWidget.close()`, which is what
    a window manager's close button ends up calling, finds no override to
    run.  Everything else in this module is a consequence of it.
    """
    import audian.audian as audian_app

    # By `__dict__` rather than by comparing against the base: the binding
    # hands out a distinct built-in `closeEvent` object per class, so an
    # identity test reports "overridden" for every QMainWindow ever written.
    # What is being asked is whether Audian defines one of its own.
    defined_in = [
        klass.__name__
        for klass in audian_app.Audian.__mro__
        if "closeEvent" in vars(klass)
    ]
    assert defined_in and defined_in[0] == "Audian", (
        "Audian defines no closeEvent, so closing through the window manager "
        f"would run no teardown at all (found on: {defined_in or 'nothing'})"
    )


def test_closing_the_window_tears_the_browser_down(window):
    """The close gesture reaches the teardown that frees the recording.

    Asserted on the released loader rather than on a call to a named method:
    `Data.close()` sets `self.data` to `None`, so a browser whose teardown
    ran is one that no longer holds the file open.  A spy on the method name
    would be satisfied by any rename that happened to keep it, which is not
    the property this is about -- and on a recording a test can afford to
    write, `inline_samples` means no compression pool is ever spawned, so a
    live-child assertion would pass vacuously.
    """
    browser = window.browser()
    assert browser.data.data is not None

    QApplication.instance().sendEvent(window, QCloseEvent())
    pump(0.2)

    assert browser.data.data is None, (
        "closing the window never released the recording"
    )


def test_closing_the_window_writes_a_pending_label(window):
    """A label made and not yet flushed survives the close gesture.

    The save is queued on a zero-timer, so it lands on the next turn of the
    loop -- unless the close is what stops the loop turning.  `flush_labels`
    exists for exactly this and the close path does not call it.
    """
    browser = window.browser()
    sidecar = Path(browser.labels_path())
    assert not sidecar.exists()

    category = browser.labels.categories[0]
    browser.store_label(category, None, 0, 1.0, 2.0, None, None)
    browser.schedule_label_save()
    assert browser.labels.dirty or browser.label_save_pending

    # What a window manager delivers.  No pumping afterwards: `exec()` has
    # returned, and nothing turns the loop again.
    QApplication.instance().sendEvent(window, QCloseEvent())

    assert sidecar.exists(), "the label was never written to disk"


def test_the_compression_workers_are_daemons(monkeypatch, tmp_path):
    """A worker must not be able to hold the process open at exit.

    `multiprocessing`'s exit handler *joins* non-daemon children rather than
    killing them, so a pool still reducing a multi-gigabyte recording turns a
    closed window into a process that will not exit and shows no window.
    Daemonising them makes the worst case a lost overview instead of a hang.

    `inline_samples` is 8 million, so no recording a test can afford to write
    ever spawns a worker.  Dropping it to zero is what makes the pool real on
    a four-second file; the assertion is then about how the children are
    constructed, which is the thing that has to change.
    """
    soundfile = pytest.importorskip("soundfile")
    from audian import compresseddata as cd
    from audian.data import open_files

    signal = (0.1 * np.sin(np.arange(FRAMES) / 50.0)).astype(np.float32)
    recording = tmp_path / "rec.wav"
    soundfile.write(recording, signal, RATE)

    monkeypatch.setattr(cd.CompressedData, "inline_samples", 0)

    spawned = []
    real_process = cd.Process

    def spy(*args, **kwargs):
        spawned.append(kwargs)
        return real_process(*args, **kwargs)

    monkeypatch.setattr(cd, "Process", spy)

    loader = open_files([str(recording)], 60.0, 10.0)
    compressed = cd.CompressedData(loader)
    try:
        compressed.start(2000, {}, do_short=False)
        assert spawned, "no workers spawned even with inline_samples at 0"
        assert all(kw.get("daemon") for kw in spawned), (
            "compression workers are not daemons, so multiprocessing's exit "
            "handler joins them and the process hangs until they finish"
        )
    finally:
        try:
            compressed.close()
        except Exception:
            pass
        loader.close()
