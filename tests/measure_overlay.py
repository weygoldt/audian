"""Cost of one redraw: overlay panel vs sixteen lanes, same 300 s window.

Run from the repo:  uv run python tests/measure_overlay.py

Not a test (no `test_` prefix, so pytest does not collect it).  The numbers
in `TimePlot.update_plot` came from it.
"""

import os
import sys
import time
import tempfile
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"  # the shell exports wayland
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tests"))
sys.path.insert(0, str(REPO / "src"))
import numpy as np  # noqa: E402
import pyqtgraph as pg  # noqa: E402

pg.setConfigOption("mouseRateLimit", 0)
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])
import test_panelsplitter as tp  # noqa: E402

CH, SECS = 16, 320
rng = np.random.default_rng(1)
sig = (0.05 * rng.standard_normal((tp.RATE * SECS, CH))).astype(np.float32)
sig += (np.arange(CH) * 0.05)[None, :].astype(np.float32)
tmp = Path(tempfile.mkdtemp(dir=os.environ.get("SCRATCH", None)))
window = tp.build_window(app, tmp, CH, sig)
b = window.browser()
b.set_panels(traces=True, specs=0)
tp.pump(1.0)
b.set_times(0.0, 300.0)
tp.pump(2.0)
print("window", b.plot_ranges["t"].r0[0], b.plot_ranges["t"].r1[0], "rate", tp.RATE)


def trial(label, n=15):
    figs = [b.figs[c] for c in b.visible_channels()]
    # warm
    b.panels.update_plots()
    app.processEvents()
    upd, paint = [], []
    for _ in range(n):
        t0 = time.perf_counter()
        b.panels.update_plots()
        t1 = time.perf_counter()
        for f in figs:
            f.grab()
        t2 = time.perf_counter()
        upd.append(t1 - t0)
        paint.append(t2 - t1)
    items = sum(
        len(ax.own_traces()) + len(ax.overlay_items)
        for ax in (b.panels["trace"].axs[c] for c in b.visible_channels())
        if ax.isVisible()
    )
    print(
        f"{label:10s} lanes={len(figs):2d} items={items:3d}  update {1e3 * np.median(upd):6.2f} ms"
        f"  paint {1e3 * np.median(paint):6.2f} ms  (median of {n})"
    )


for rep in range(3):
    trial("16 lanes")
    b.set_overlay_traces(True)
    tp.pump(1.0)
    trial("overlay")
    b.set_overlay_traces(False)
    tp.pump(1.0)
window.close()
tp.pump(0.3)
