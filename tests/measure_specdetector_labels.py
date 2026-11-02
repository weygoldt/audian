"""What candidates cost in the label store and on the lanes, by how many there are.

Not a test.  Sets `panel.FOUND_LIMIT`.  Offscreen, on a two-channel recording of 60 s
in a 1200 x 900 window, median of five, candidates spread evenly over the recording::

    .venv/bin/python tests/measure_specdetector_labels.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "tests"))

import numpy as np  # noqa: E402
import pyqtgraph as pg  # noqa: E402
from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

pg.setConfigOption("mouseRateLimit", 0)


def median_ms(call, repeat=5) -> float:
    times = []
    for _ in range(repeat):
        start = time.perf_counter()
        call()
        times.append(1e3 * (time.perf_counter() - start))
    return float(np.median(times))


def main() -> None:
    sandbox = Path(tempfile.mkdtemp(prefix="audian-measure-"))
    import audian.audian as audian_app

    audian_app.settings_path = lambda: sandbox / "settings.json"
    for fmt in (QSettings.Format.NativeFormat, QSettings.Format.IniFormat):
        for scope in (QSettings.Scope.UserScope, QSettings.Scope.SystemScope):
            QSettings.setPath(fmt, scope, os.fspath(sandbox))

    from test_panelsplitter import RATE, build_window, pump

    from audian_plugins.specdetector import panel as panel_module
    from audian_plugins.specdetector.boxes import Box

    app = QApplication.instance() or QApplication([])
    weights = sandbox / "weights"
    weights.mkdir()
    panel_module.weights_directory = lambda: weights
    rng = np.random.default_rng(1)
    signal = rng.normal(0, 0.01, (60 * int(RATE), 2)).astype(np.float32)
    window = build_window(app, sandbox, 2, signal)
    browser = window.browser()
    browser.set_panels(specs=1)
    pump(1.0)
    browser.open_plugin_panel("Spectrogram detector")
    widget = browser.plugin_panels["Spectrogram detector"]
    widget.add_class("x")
    widget._ensure_project()
    widget.project.class_index("x")
    panel_module.FOUND_LIMIT = 10**9
    vb = browser.spectrogram_axes()[0].getViewBox()
    print(f"{'candidates':>10} {'rewrite ms':>11} {'redraw ms':>10} {'save ms':>8}")
    for count in (1000, 5000, 20000, 50000):
        widget.candidates = {
            i: Box(t, t + 0.05, 500.0, 900.0, 0, 0.9)
            for i, t in enumerate(np.linspace(0.0, 59.0, count))
        }
        widget.threshold = 0.1
        rewrite = median_ms(widget._write_found, 3)
        browser.set_times(10.0, 5.0)
        pump(0.2)
        redraw = median_ms(browser.redraw_labels)
        path = browser.labels_path()
        save = median_ms(lambda: browser.labels.save(path), 3)
        print(f"{count:10d} {rewrite:11.1f} {redraw:10.1f} {save:8.1f}")
        assert vb.viewRange()[0][0] < 11.0
    widget.about_to_flush_labels()
    window.close()


if __name__ == "__main__":
    main()
