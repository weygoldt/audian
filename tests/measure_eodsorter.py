"""Benchmark of the wavetracker plugin against design section 6.1.

Not a test: run it by hand, ``.venv/bin/python tests/measure_eodsorter.py``.
Builds the reference dataset synthetically -- 1,000,000 detections, 2,000
ids, a 4 h recording at wavetracker's default frame step -- and prints, per
operation, the median time and the target:

* geometry of the overlay at full zoom-out and at ~50,000 visible points
  (pure numpy, `geometry.compute_geometry`);
* the same through four real `TrackOverlay` lanes (pyqtgraph `setData`
  included), offscreen;
* a hover query and a brush segment;
* committing an edit of ~50,000 rows, including the redraw, and undoing it.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYSIDE6_OPTION_PYTHON_ENUM", "16")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from audian_plugins.eodsorter import geometry as G  # noqa: E402
from audian_plugins.eodsorter import model as M  # noqa: E402

N_ROWS = 1_000_000
N_IDS = 2_000
HOURS = 4.0
FRAME_STEP = 3276 / 20000.0


def reference_set(n_rows=N_ROWS, n_ids=N_IDS, hours=HOURS, seed=0):
    rng = np.random.default_rng(seed)
    n_frames = int(hours * 3600 / FRAME_STEP)
    times = FRAME_STEP * np.arange(n_frames) + 0.82
    per = n_rows // n_ids
    fund, idx, ident = [], [], []
    for i in range(n_ids):
        start = int(rng.integers(0, max(1, n_frames - per * 2)))
        frames = start + np.sort(rng.choice(per * 2, per, replace=False))
        base = 400 + 800 * rng.random()
        fund.append(base + np.cumsum(rng.normal(0, 0.05, per)))
        idx.append(frames)
        ident.append(np.full(per, float(i)))
    fund, idx, ident = map(np.concatenate, (fund, idx, ident))
    o = np.argsort(idx, kind="stable")
    sign = np.ones((len(o), 1), np.float32)
    return M.TrackSet.from_arrays(fund[o], idx[o], ident[o], sign, times)


def timed(fn, repeat=7):
    out = []
    for _ in range(repeat):
        t0 = time.perf_counter()
        fn()
        out.append(1000 * (time.perf_counter() - t0))
    return float(np.median(out))


def report(name, ms, target):
    flag = "ok " if ms <= target else "SLOW"
    print(f"{flag} {name:<52s} {ms:8.2f} ms   (target {target} ms)")


def main() -> None:
    t0 = time.perf_counter()
    ts = reference_set()
    print(
        f"reference set: {ts.n:,} rows, {len(ts.ids()):,} ids, "
        f"{len(ts.times):,} frames ({time.perf_counter() - t0:.1f} s to build)"
    )
    scene = G.SceneState(ts=ts)
    t_end = float(ts.times[-1])
    full = G.View(0.0, t_end, 300.0, 1300.0, 1400.0, 220.0)
    # a view holding about 50,000 points
    span = t_end * 50_000 / ts.n
    mid = G.View(5000.0, 5000.0 + span, 300.0, 1300.0, 1400.0, 220.0)

    def geo(view):
        return lambda: G.compute_geometry(ts, scene, view)

    report("geometry, full zoom-out (pure)", timed(geo(full)), 30)
    g = G.compute_geometry(ts, scene, mid)
    report(f"geometry, {g.n_rows:,} visible points (pure)", timed(geo(mid)), 10)

    cx, cy = 700.0, 110.0
    report(
        "hover query (nearest), full zoom-out",
        timed(lambda: G.nearest(ts, full, cx, cy, 10.0), 21),
        4,
    )
    report(
        "hover query (nearest), 50k view",
        timed(lambda: G.nearest(ts, mid, cx, cy, 10.0), 21),
        4,
    )
    report(
        "brush segment, 50k view, r=14 px",
        timed(lambda: G.brush_segment(ts, mid, (cx, cy), (cx + 12, cy + 3), 14.0), 21),
        4,
    )

    # through real overlays
    from PySide6.QtWidgets import QApplication
    import pyqtgraph as pg

    app = QApplication.instance() or QApplication([])
    from audian import theme

    theme.apply(app)
    from audian_plugins.eodsorter.overlay import TrackOverlay

    win = pg.GraphicsLayoutWidget()
    win.resize(1500, 1000)
    plots = []
    for i in range(4):
        p = win.addPlot(row=i, col=0)
        plots.append(p)
    win.show()
    app.processEvents()
    cache = G.RenderCache()
    overlays = [TrackOverlay(p, scene, cache) for p in plots]

    def show(view):
        for p in plots:
            p.getViewBox().setRange(
                xRange=(view.x0, view.x1), yRange=(view.y0, view.y1), padding=0
            )
        app.processEvents()

    def redraw(view):
        def run():
            cache.clear()
            for o in overlays:
                o.invalidate()
                o.update_plot()

        show(view)
        return run

    report("redraw 4 lanes, full zoom-out (setData incl.)", timed(redraw(full), 5), 30)
    report("redraw 4 lanes, 50k visible (setData incl.)", timed(redraw(mid), 5), 10)

    def paint(view):
        show(view)
        for o in overlays:
            o.update_plot()

        def run():
            win.grab()

        return run

    # the frame without the overlay, so the line below is the overlay's share
    for o in overlays:
        o.scene.ts = None
    base = timed(paint(full), 5)
    scene.ts = ts
    report("Qt paint of 4 bare lanes (reference)", base, 30)
    report("Qt paint of 4 lanes, full zoom-out", timed(paint(full), 5), 30)
    report("Qt paint of 4 lanes, 50k visible", timed(paint(mid), 5), 30)

    # hover: query + preview layer of one lane
    show(mid)
    for o in overlays:
        o.update_plot()
    view = overlays[0].view()

    def hov():
        row, ids = G.nearest(ts, view, cx, cy, 10.0)
        scene.hover = G.Hover(row, ids[0], ids, 0) if row >= 0 else None
        scene.touch()
        for o in overlays:
            o.update_plot()

    report("hover query + highlight on 4 lanes", timed(hov, 21), 4)

    # commit and undo of a ~50,000-row edit, including the redraw
    big_ids = ts.ids()[:100]

    def commit():
        plan = ts.plan_delete_ids(big_ids)
        change = ts.apply(plan)
        cache.note_change(change)
        for o in overlays:
            o.invalidate(change)
            o.update_plot()
        return change

    t0 = time.perf_counter()
    change = commit()
    ms = 1000 * (time.perf_counter() - t0)
    report(f"commit of {len(change.rows):,} rows incl. redraw", ms, 50)

    def undo():
        change = ts.undo()
        cache.note_change(change)
        for o in overlays:
            o.invalidate(change)
            o.update_plot()

    t0 = time.perf_counter()
    undo()
    report("undo of the same incl. redraw", 1000 * (time.perf_counter() - t0), 50)


if __name__ == "__main__":
    main()
