"""End-to-end responsiveness of the wavetracker plugin's edit mode at 4K.

Not a test: run it by hand, through ``tests/measure_eodsorter.py --4k``::

    .venv/bin/python tests/measure_eodsorter.py --4k             # dpr 1
    .venv/bin/python tests/measure_eodsorter.py --4k --dpr 2     # 200 %
    .venv/bin/python tests/measure_eodsorter.py --4k \\
        --recording take.wav --results take-wavetracker/        # real data

A real `Audian` window, offscreen, on a 3840 x 2160 screen: at ``--dpr 1``
the window is 3840 x 2160 logical pixels, at ``--dpr 2`` it is 1920 x 1080
at a device pixel ratio of 2 (a 4K panel at 200 %), so the same 8.3 M
pixels are painted either way.  Without ``--recording`` a four-channel,
ten-minute synthetic recording with ~2,400 track fragments and 30,000
unassigned detections is written to a temporary directory; ``--results``
is copied before it is opened, so the original is never written.  Settings
go to a temporary directory too.

Input is real `QMouseEvent`s sent to the lanes' viewports on a schedule
(``--hz``, 240 by default): hover sweeps, brush strokes of every tool,
a cut line, a middle-drag pan.  Each pointer event gets

* **lat** -- from when it was due to the end of the first viewport paint
  that shows its effect (the controller and the overlays have processed it);
* **lag** -- how late it was dispatched: the input backlog;
* **handler** -- the Python time of its own dispatch.

``paint`` is the duration of whole-window paints (`UpdateRequest`).  Commit,
undo and redo are timed from the call to the first frame showing the new
model revision ("shown") and to the end of all follow-up work ("settled").
Medians and 95th percentiles; targets (design 6.1, 4K addendum): hover and
strokes <= 16 ms p95, no backlog, commit/undo <= 100 ms.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def parse(argv):
    ap = argparse.ArgumentParser(prog="measure_eodsorter.py --4k")
    ap.add_argument("--dpr", type=int, default=1, choices=(1, 2))
    ap.add_argument("--recording", type=Path, default=None)
    ap.add_argument("--results", type=Path, default=None)
    ap.add_argument("--span", type=float, default=60.0, help="view, seconds")
    ap.add_argument("--start", type=float, default=None, help="view start, s")
    ap.add_argument("--fmin", type=float, default=300.0)
    ap.add_argument("--fmax", type=float, default=1300.0)
    ap.add_argument("--hz", type=float, default=240.0, help="pointer rate")
    ap.add_argument("--theme", default="dark", choices=("dark", "light"))
    ap.add_argument("--json", type=Path, default=None, help="write results")
    return ap.parse_args(argv)


def synthetic(folder: Path):
    """A four-channel recording and its wavetracker results directory."""
    import numpy as np
    import soundfile

    rate, dur, channels, nfft, step = 20000, 600.0, 4, 4096, 410
    n = int(rate * dur)
    rng = np.random.default_rng(3)
    base = 400 + 900 * rng.random(40)
    tt = np.arange(n) / rate
    sig = np.zeros((n, channels), np.float32)
    for i, f0 in enumerate(base):
        phase = 2 * np.pi * np.cumsum(f0 + 3 * np.sin(2 * np.pi * tt / (20 + 7 * i)))
        for c in range(channels):
            sig[:, c] += 0.02 * (1 + np.sin(i + c)) * np.sin(phase / rate)
    sig += 0.005 * rng.standard_normal(sig.shape).astype(np.float32)
    recording = folder / "synthetic.wav"
    soundfile.write(recording, sig, rate)
    frames = (n - nfft) // step + 1
    times = (np.arange(frames) * step + nfft / 2) / rate
    fund, idx, ident = [], [], []
    for i, f0 in enumerate(base):
        fk = f0 + 3 * np.sin(2 * np.pi * times / (20 + 7 * i))
        k = np.flatnonzero(rng.random(frames) > 0.1)
        cuts = np.sort(rng.choice(frames, 60, replace=False))
        fund.append(fk[k] + rng.normal(0, 0.2, len(k)))
        idx.append(k)
        ident.append(61.0 * i + np.searchsorted(cuts, k))
    fund.append(300 + 1200 * rng.random(30000))
    idx.append(rng.integers(0, frames, 30000))
    ident.append(np.full(30000, np.nan))
    fund, idx, ident = map(np.concatenate, (fund, idx, ident))
    o = np.argsort(idx, kind="stable")
    results = folder / "synthetic-wavetracker"
    results.mkdir()
    np.save(results / "fund_v.npy", fund[o])
    np.save(results / "idx_v.npy", idx[o].astype(np.int64))
    np.save(results / "ident_v.npy", ident[o])
    np.save(results / "sign_v.npy", rng.random((len(o), channels)).astype(np.float32))
    np.save(results / "times.npy", times)
    meta = {
        "rate": rate,
        "start": 0.0,
        "frame_step": step / rate,
        "freq_resolution": rate / nfft,
        "low_threshold": 10.0,
        "high_threshold": 20.0,
        "config": {
            "spectrogram": {"nfft": nfft, "overlap_frac": 0.9},
            "harmonic_groups": {"min_freq": 300.0, "max_freq": 1500.0},
            "tracking": {"freq_tolerance": 2.5, "max_dt": 10.0},
        },
    }
    (results / "wavetracker.json").write_text(json.dumps(meta))
    return recording, results


def main(argv=None) -> None:
    args = parse(sys.argv[1:] if argv is None else argv)
    tmp = Path(tempfile.mkdtemp(prefix="measure-4k-"))
    # a 3840 x 2160 screen; at dpr 2 Qt sees 1920 x 1080 at 192 dpi
    screen = {
        "synchronousWindowSystemEvents": False,
        "windowFrameMargins": False,
        "screens": [
            {
                "name": "4k",
                "x": 0,
                "y": 0,
                "width": 3840,
                "height": 2160,
                "logicalDpi": 96 * args.dpr,
                "logicalBaseDpi": 96,
                "dpr": 1,
            }
        ],
    }
    (tmp / "screen.json").write_text(json.dumps(screen))
    os.environ["QT_QPA_PLATFORM"] = f"offscreen:configfile={tmp / 'screen.json'}"
    os.environ.setdefault("PYSIDE6_OPTION_PYTHON_ENUM", "16")
    for key in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME"):
        os.environ[key] = str(tmp / key)
    sys.path.insert(0, str(REPO / "src"))
    try:
        run(args, tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        # audian's worker threads and Qt's teardown are not what is measured,
        # and tearing a window down mid-computation can abort the process
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(0)


def run(args, tmp: Path) -> None:  # noqa: C901 - one scripted session
    import numpy as np
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication

    frames: list = []  # whole-window paints: (start, end)
    views: list = []  # viewport paints: (start, end, events processed)
    seen = {"due": 0, "ctl": 0, "drawn": 0}
    # when the overlays first drew a model revision other than "from"
    watch = {"from": None, "at": None}

    class App(QApplication):
        def notify(self, obj, ev):  # noqa: N802
            if ev.type() != QEvent.Type.UpdateRequest:
                return super().notify(obj, ev)
            t0 = time.perf_counter()
            try:
                return super().notify(obj, ev)
            finally:
                frames.append((t0, time.perf_counter()))

    app = App([])

    import pyqtgraph as pg

    import audian.audian as audian_app
    from audian.plugins import Plugins
    from audian_plugins.eodsorter import overlay as OV
    from audian_plugins.eodsorter import tools as TL

    # where an event has got to: the controller took it, the overlays drew it
    def wrap(cls, name, stage):
        orig = getattr(cls, name)

        def hooked(self, *a, **k):
            if stage == "drawn":
                seen["drawn"] = max(seen["drawn"], seen["ctl"])
                ts = self.scene.ts
                revision = getattr(ts, "revision", None)
                if watch["from"] is not None and watch["at"] is None:
                    if revision != watch["from"]:
                        watch["at"] = time.perf_counter()
            else:
                seen["ctl"] = seen["due"]
            return orig(self, *a, **k)

        setattr(cls, name, hooked)

    for name in ("flush", "move", "press", "release", "click"):
        wrap(TL.ToolController, name, "ctl")
    wrap(TL.ToolSurface, "pan", "ctl")
    wrap(OV.TrackOverlay, "update_plot", "drawn")
    paint_event = pg.GraphicsView.paintEvent

    def timed_paint(self, ev):
        t0 = time.perf_counter()
        drawn = seen["drawn"]
        try:
            return paint_event(self, ev)
        finally:
            views.append((t0, time.perf_counter(), drawn))

    pg.GraphicsView.paintEvent = timed_paint

    def pump(seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.002)

    def settle(limit=5.0):
        end = time.perf_counter() + limit
        quiet = 0
        while time.perf_counter() < end and quiet < 3:
            n = len(frames) + len(views)
            app.processEvents()
            busy = ctl._hover_timer.isActive() or any(
                o._timer.isActive() for o in panel.overlays
            )
            quiet = quiet + 1 if not busy and n == len(frames) + len(views) else 0
            time.sleep(0.001)

    if args.recording is None:
        recording, results = synthetic(tmp)
    else:
        recording, results = args.recording, args.results
    work = tmp / "session"
    shutil.copytree(results, work)

    audian_app.apply_theme_preference(app, args.theme)
    plugins = Plugins()
    plugins.load_plugins()
    window = audian_app.Audian(
        [str(recording)], {}, plugins, [], 0, None, False, 0, None, args.theme
    )
    geometry = app.primaryScreen().geometry()
    window.resize(geometry.width(), geometry.height())
    window.show()
    pump(2.0)
    browser = window.browser()
    browser.set_side_panel(True)
    browser.set_panels(traces=0, specs=1)
    pump(0.5)
    assert browser.open_plugin_panel("Wavetracker")
    panel = browser.plugin_panels["Wavetracker"]
    panel.ask = lambda *a, **k: "Discard"
    assert panel.open_results(work, ask=False)
    ctl = panel.controller
    ts = panel.ts
    t_start = args.start
    if t_start is None:
        t_start = float(ts.times[len(ts.times) // 3])
    browser.set_ranges("f", args.fmin, args.fmax)
    browser.set_times(t_start, args.span)
    pump(3.0)
    panel.set_edit_mode(True)
    pump(2.0)
    print(
        f"window {window.width()}x{window.height()} at dpr "
        f"{window.devicePixelRatioF():g}; {ts.n:,} rows, {len(ts.ids()):,} ids, "
        f"{len(panel.surfaces)} lanes; pointer at {args.hz:g} Hz"
    )

    surface = panel.surfaces[0]
    view = surface.scene().views()[0]
    vp = view.viewport()
    v0 = surface.view()

    def at(t, f):
        x, y = v0.to_px(t, f)
        return QPointF(view.mapFromScene(surface.mapToScene(QPointF(x, y))))

    def send(kind, pos, button=None, buttons=None, mods=None):
        none = Qt.MouseButton.NoButton
        ev = QMouseEvent(
            kind,
            pos,
            pos,
            QPointF(vp.mapToGlobal(pos.toPoint())),
            button or none,
            buttons or none,
            mods or Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(vp, ev)

    def drive(schedule):
        frames.clear()
        views.clear()
        seen.update(due=0, ctl=0, drawn=0)
        start = time.perf_counter() + 0.01
        sent = []
        i = 0
        while i < len(schedule):
            while i < len(schedule) and start + schedule[i][0] <= time.perf_counter():
                a = time.perf_counter()
                seen["due"] = i + 1
                schedule[i][1]()
                sent.append((start + schedule[i][0], a, time.perf_counter() - a))
                i += 1
            app.processEvents()
            if i < len(schedule):
                wait = start + schedule[i][0] - time.perf_counter()
                if wait > 0.0003:
                    time.sleep(min(wait, 0.001) * 0.8)
        settle()
        painted = sorted(views)
        lat, lag, handler = [], [], []
        p = 0
        for j, (due, dispatched, took) in enumerate(sent):
            while p < len(painted) and (
                painted[p][0] < dispatched or painted[p][2] <= j
            ):
                p += 1
            if p < len(painted):
                lat.append(1000 * (painted[p][1] - due))
            lag.append(1000 * (dispatched - due))
            handler.append(1000 * took)
        paint = [1000 * (b - a) for a, b in frames]
        return {"lat": lat, "lag": lag, "handler": handler, "paint": paint}

    results_out = {}

    def stat(xs):
        if not xs:
            return "     -  /     -  "
        return f"{np.median(xs):6.1f} / {np.percentile(xs, 95):6.1f}"

    def report(name, r, extra=""):
        print(
            f"{name:<24s} lat {stat(r['lat'])}  lag {stat(r['lag'])}  "
            f"handler {stat(r['handler'])}  paint {stat(r['paint'])}{extra}"
        )
        results_out[name] = {
            k: [float(np.median(v)), float(np.percentile(v, 95))] if v else None
            for k, v in r.items()
        }

    dt = 1.0 / args.hz

    def sweep(seconds):
        n = int(seconds * args.hz)
        out = []
        for j in range(n):
            u = j / max(1, n - 1)
            t = v0.x0 + (v0.x1 - v0.x0) * (0.1 + 0.8 * u)
            f = v0.y0 + (v0.y1 - v0.y0) * (0.5 + 0.4 * np.sin(6 * np.pi * u))
            pos = at(t, f)
            out.append((j * dt, lambda p=pos: send(QEvent.Type.MouseMove, p)))
        return out

    def drag(seconds, f, t0=0.3, t1=0.7, wiggle=6.0, button=None, mods=None):
        button = button or Qt.MouseButton.LeftButton
        n = int(seconds * args.hz)
        pts = []
        for j in range(n):
            u = j / max(1, n - 1)
            t = v0.x0 + (v0.x1 - v0.x0) * (t0 + (t1 - t0) * u)
            pts.append(at(t, f + wiggle * np.sin(8 * np.pi * u)))
        move, press = QEvent.Type.MouseMove, QEvent.Type.MouseButtonPress
        out = [(0.0, lambda: send(move, pts[0]))]
        out.append((dt, lambda: send(press, pts[0], button, button, mods)))
        for j, p in enumerate(pts[1:], start=2):
            out.append((j * dt, lambda p=p: send(move, p, None, button, mods)))
        release = QEvent.Type.MouseButtonRelease
        out.append(
            ((len(pts) + 1) * dt, lambda: send(release, pts[-1], button, None, mods))
        )
        return out

    def timed(fn):
        """ms to the first frame showing a new model revision, and to rest."""
        settle()
        frames.clear()
        views.clear()
        watch.update({"from": ts.revision, "at": None})
        a = time.perf_counter()
        try:
            fn()
            settle()
        finally:
            changed = watch["at"]
            watch.update({"from": None, "at": None})
        shown = None
        if changed is not None:
            shown = next((e for s, e, _d in sorted(views) if s >= changed), None)
        ends = [e for _s, e in frames] + [e for _s, e, _d in views]
        rest = max(ends) if ends else time.perf_counter()
        return 1000 * ((shown or rest) - a), 1000 * (rest - a)

    # a frequency with tracks on it, at the middle of the view
    k0, k1 = ts.grid.frame_range(0.5 * (v0.x0 + v0.x1) - 1, 0.5 * (v0.x0 + v0.x1) + 1)
    rows = ts.rows_in_frames(k0, k1)
    rows = rows[np.isfinite(ts.ident[rows])]
    fs = np.sort(ts.fund[rows])
    fs = fs[(fs > v0.y0 + 20) & (fs < v0.y1 - 20)]
    f_track = float(np.median(fs)) if len(fs) else 0.5 * (v0.y0 + v0.y1)

    for key, name in (("V", "Select"), ("A", "Add"), ("M", "Merge"), ("C", "Cut")):
        ctl.set_tool(key)
        report(f"hover sweep ({name})", drive(sweep(2.0)))
    for key, name in (("V", "Select"), ("M", "Merge"), ("E", "Erase")):
        ctl.set_tool(key)
        schedule = drag(3.0, f_track)
        release = schedule.pop()
        report(f"stroke 3 s ({name})", drive(schedule))
        shown, rest = timed(release[1])
        print(f"{'  release':<24s} {shown:6.1f} ms to the frame, {rest:6.1f} to rest")
        results_out[f"release ({name})"] = [shown, rest]
        ctl.escape()
        ctl.escape()
    undo, redo = timed(panel.undo), timed(panel.redo)
    print(f"{'undo / redo':<24s} {undo[0]:6.1f} / {redo[0]:6.1f} ms to the frame")
    results_out["undo"], results_out["redo"] = undo, redo
    timed(panel.undo)
    ctl.set_tool("A")
    report("stroke 2 s (Add)", drive(drag(2.0, f_track + 13, 0.45, 0.6, 2.0)))
    timed(panel.undo)
    ctl.set_tool("C")
    report("cut line 1.5 s", drive(drag(1.5, f_track, 0.5, 0.52, 0.0)))
    timed(panel.undo)
    ctl.set_tool("V")
    steps = []
    set_times = browser.set_times

    def counted(*a, **k):
        steps.append(time.perf_counter())
        return set_times(*a, **k)

    browser.set_times = counted
    try:
        middle = Qt.MouseButton.MiddleButton
        r = drive(drag(2.0, 0.5 * (v0.y0 + v0.y1), 0.7, 0.3, 0.0, middle))
    finally:
        browser.set_times = set_times
    rate = (len(steps) - 1) / (steps[-1] - steps[0]) if len(steps) > 1 else 0.0
    report("middle-drag pan 2 s", r, f"  view updates {rate:4.1f}/s")
    results_out["pan updates/s"] = rate
    jumps = [timed(lambda: panel.goto_issue(+1))[1] for _ in range(4)]
    print(f"{'G issue jumps':<24s} " + " / ".join(f"{x:6.1f}" for x in jumps) + " ms")
    results_out["G"] = jumps
    if args.json is not None:
        args.json.write_text(json.dumps(results_out, indent=1))
    panel.set_session(None)
    window.close()
    app.processEvents()


if __name__ == "__main__":
    main()
