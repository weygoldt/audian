"""What click removal costs in the running application.

    QT_QPA_PLATFORM=offscreen .venv/bin/python tests/measure_clicks.py RECORDING

Opens RECORDING with a 10 s view and measures, with click removal off and
then on: twelve pan steps of 1 s, three jumps, and switching it on and off --
the part on the GUI thread and the part on the worker.  It also reports how
many of the filtered samples are repaired, which should be well above zero
with it on and exactly zero with it off.

Both settings stores are redirected into a scratch directory first, so the
reader's own settings are never touched.  Not a test: the numbers depend on
the machine and the recording.  The figures in `docs/usermanual.md` are from
iriri01-grid01-dev03-20260905T201837.wv (16 channels, 48 kHz).
"""

import os
import statistics
import sys
import tempfile
import time
from pathlib import Path


def main(path):
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    import pyqtgraph as pg
    from PySide6.QtCore import QEvent, QSettings
    from PySide6.QtWidgets import QApplication

    # not loaded outside a pytest run, see tests/conftest.py
    pg.setConfigOption("mouseRateLimit", 0)
    app = QApplication.instance() or QApplication([])
    sandbox = Path(tempfile.mkdtemp(prefix="audian-measure-clicks-"))
    import audian.audian as audian_app
    from audian import theme
    from audian.plugins import Plugins

    audian_app.settings_path = lambda: sandbox / "settings.json"
    for fmt in (QSettings.Format.NativeFormat, QSettings.Format.IniFormat):
        for scope in (QSettings.Scope.UserScope, QSettings.Scope.SystemScope):
            QSettings.setPath(fmt, scope, os.fspath(sandbox))

    def pump(seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            time.sleep(0.005)

    def until_idle(view, limit=60.0):
        t = time.perf_counter()
        while time.perf_counter() - t < limit:
            app.processEvents()
            if not view.tasks.busy and view._pending_recompute is None:
                return time.perf_counter() - t
            time.sleep(0.002)
        raise RuntimeError("the worker never went idle")

    theme.apply(app)
    plugins = Plugins()
    plugins.load_plugins()
    window = audian_app.Audian([path], {}, plugins, [], 0, None, False, 0, None)
    window.resize(1600, 1000)
    window.show()
    pump(4.0)
    view = window.browser()
    rate = view.data.rate
    print(
        f"{os.path.basename(path)}: {view.data.channels} channels, {rate:.0f} Hz, "
        f"{view.data.data.frames / rate:.0f} s"
    )

    def repaired():
        f = view.data["filtered"]
        raw = view.data.data
        lo = f.offset - raw.offset
        rows = raw.buffer[lo : lo + len(f.buffer)].astype(f.dtype)
        return 100 * float((f.buffer != rows).mean())

    def pan(label):
        view.set_times(toffset=60.0, twindow=10.0)
        until_idle(view)
        times = []
        for k in range(12):
            t = time.perf_counter()
            view.set_times(toffset=61.0 + k, twindow=10.0)
            times.append(time.perf_counter() - t)
            until_idle(view)
        print(
            f"{label}: pan steps of 1 s: {', '.join(f'{1000 * x:.0f}' for x in times)} ms "
            f"(median {1000 * statistics.median(times):.0f})"
        )

    def jump(label):
        times = []
        for where in (150.0, 30.0, 250.0):
            t = time.perf_counter()
            view.set_times(toffset=where, twindow=10.0)
            times.append(time.perf_counter() - t)
            until_idle(view)
            print(f"  after the jump to {where:.0f} s: {repaired():.2f} % repaired")
        print(f"{label}: jumps {', '.join(f'{1000 * x:.0f}' for x in times)} ms")

    def toggle(on):
        view.set_times(toffset=60.0, twindow=10.0)
        until_idle(view)
        t = time.perf_counter()
        view.set_click_removal(on)
        gui = time.perf_counter() - t
        worker = until_idle(view)
        print(
            f"switching {'on' if on else 'off'}: {1000 * gui:.0f} ms on the GUI thread, "
            f"{1000 * worker:.0f} ms on the worker; {repaired():.2f} % repaired; raw "
            f"buffer {len(view.data.data.buffer) / rate:.1f} s"
        )

    pan("off")
    jump("off")
    toggle(True)
    pan("on")
    jump("on")
    toggle(False)
    window.close()
    window.setParent(None)
    window.deleteLater()
    pump(0.5)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
