"""What happens when the window is closed the way a window manager closes it.

Audian defends two exit paths by hand: `Ctrl+Q` and closing a tab both call
`flush_labels()` before letting go.  `flush_labels`' own docstring says why --
"there is no `closeEvent` anywhere in audian and `Audian.quit` never goes
through Qt's close machinery at all".

The window manager's close button is a third path, and it goes through
exactly the close machinery that sentence says nothing uses.  It calls
neither.  So on that gesture:

* the pending label save is dropped with the event loop,
* `CompressedData.close()` never runs, leaving eight non-daemon children for
  `multiprocessing`'s exit handler to join -- which is a hang, not a leak,
  while a large recording is still compressing,
* `PlayAudio.close()` is reached only from `Audian.__del__`, i.e. never
  reliably.

The label window is narrow -- a queued zero-timer save fires on the next turn
of the loop, so it is lost only if nothing turns the loop again, which is what
`exec()` returning does.  The child processes are the part with teeth.

These are marked `xfail` because they describe the bug, not the fix.  They are
`strict`, so when the `closeEvent` lands they fail as XPASS and the marker has
to come off in the same commit.
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

from PySide6.QtCore import QEvent, QSettings  # noqa: E402
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
    original = audian_app.settings_path
    home = Path(QSettings("audian", "audian").fileName()).parent.parent
    audian_app.settings_path = lambda: tmp_path / "settings.json"
    for fmt in (QSettings.Format.NativeFormat, QSettings.Format.IniFormat):
        for scope in (QSettings.Scope.UserScope, QSettings.Scope.SystemScope):
            QSettings.setPath(fmt, scope, os.fspath(tmp_path))

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
    audian_app.settings_path = original
    for fmt in (QSettings.Format.NativeFormat, QSettings.Format.IniFormat):
        for scope in (QSettings.Scope.UserScope, QSettings.Scope.SystemScope):
            QSettings.setPath(fmt, scope, os.fspath(home))


@pytest.mark.xfail(
    strict=True,
    reason="Audian defines no closeEvent, so Qt's close machinery reaches nothing",
)
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
        f"runs no teardown at all (found on: {defined_in or 'nothing'})"
    )


@pytest.mark.xfail(
    strict=True,
    reason="no closeEvent: the browser's teardown never runs on this path",
)
def test_closing_the_window_tears_the_browser_down(window):
    """The close gesture reaches the teardown that frees the recording.

    `DataBrowser.close()` is what releases the loader and, on a recording
    big enough to have started one, terminates and joins the compression
    pool.  Spying on it says whether the gesture arrives without needing an
    8-million-sample file to make the workers real: `inline_samples` means
    anything smaller never spawns one, so a live-child assertion would pass
    vacuously on any recording a test can afford to write.
    """
    browser = window.browser()
    called = []
    original = browser.close
    browser.close = lambda *a, **k: (called.append(True), original(*a, **k))[1]

    QApplication.instance().sendEvent(window, QCloseEvent())
    pump(0.2)

    assert called, "closing the window never tore the browser down"


@pytest.mark.xfail(
    strict=True,
    reason="no closeEvent: the queued label save dies with the event loop",
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


@pytest.mark.xfail(
    strict=True,
    reason="compression workers are spawned without daemon=True",
)
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

    loader = open_files([str(recording)])
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
