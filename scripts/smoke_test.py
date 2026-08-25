#!/usr/bin/env python
"""Durable offscreen smoke test for the audian GUI.

Builds the real Audian main window on the offscreen Qt platform, pumps the
event loop long enough for the deferred/threaded work (compression of the
navigator overview, buffered filter/envelope recomputes, debounced layout
passes) to land, optionally grabs a screenshot, and exits non-zero on any
exception or on any Qt message that indicates a real fault.

Usage::

    .venv/bin/python scripts/smoke_test.py data/Gryllus_campestris.wav
    .venv/bin/python scripts/smoke_test.py FILE.wav -o .devshots/shot.png
    .venv/bin/python scripts/smoke_test.py --empty -o .devshots/empty.png

Exit codes: 0 ok, 1 fault (exception, fatal Qt message, or timeout).
"""

import argparse
import faulthandler
import os
import sys
import time
import traceback

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# offscreen has no compositor; keep Qt from probing for one:
os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.fonts=false")

from PyQt5.QtCore import QEvent, qInstallMessageHandler  # noqa: E402
from PyQt5.QtCore import QtCriticalMsg, QtFatalMsg, QtWarningMsg  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

# Qt chatter that is noise on the offscreen platform and does not indicate a
# fault in the application itself:
BENIGN = (
    "QSocketNotifier",
    "Wayland",
    "wayland",
    "QStandardPaths",
    "propagateSizeHints",
    "This plugin does not support",
    "Populating font family aliases",
    "QFont::setPointSize",
    "libpng warning",
)

# Qt messages that always mean a real fault, whatever their level:
FATAL_MARKERS = (
    "Traceback (most recent call last)",
    "QPainter::begin",
    "QWidget::repaint: Recursive repaint",
    "must be a top level widget",
    "QLayout: Attempting to add QLayout",
    "already has a layout",
    "Cannot create children for a parent that is in a different thread",
    "QObject::startTimer",
    "QBackingStore::endPaint",
)

faults = []
messages = []


def _handler(mode, context, message):
    text = str(message)
    messages.append((mode, text))
    if any(b in text for b in BENIGN):
        return
    if mode in (QtCriticalMsg, QtFatalMsg) or any(m in text for m in FATAL_MARKERS):
        faults.append(text)
    elif mode == QtWarningMsg:
        # warnings are reported but only fail the run when they name a fault
        sys.stderr.write(f"[qt-warning] {text}\n")


def _excepthook(exc_type, exc, tb):
    faults.append("".join(traceback.format_exception(exc_type, exc, tb)))
    sys.stderr.write("".join(traceback.format_exception(exc_type, exc, tb)))


def pump(app, seconds, deadline_note=""):
    """Run the event loop for `seconds`, also flushing DeferredDelete."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        app.sendPostedEvents(None, QEvent.DeferredDelete)
        time.sleep(0.005)
    if deadline_note:
        sys.stderr.write(f"[pump] {deadline_note}\n")


INTERACTIONS = [
    # (label, attribute path on Audian.acts) - triggered in order.  Panel
    # toggles are done in pairs so the app ends up back where it started.
    ("toggle spectrograms", "toggle_spectrograms"),
    ("toggle spectrograms back", "toggle_spectrograms"),
    ("toggle power", "toggle_power"),
    ("toggle power back", "toggle_power"),
    ("toggle color bars", "toggle_cbars"),
    ("toggle color bars back", "toggle_cbars"),
    ("hide navigator", "toggle_fulldata"),
    ("show navigator", "toggle_fulldata"),
    ("navigator: all channels", "navigator_all_channels"),
    ("navigator: single channel", "navigator_all_channels"),
    ("next channel", "next_channel"),
    ("previous channel", "previous_channel"),
    ("zoom in (time)", "time_zoom_in"),
    ("zoom out (time)", "time_zoom_out"),
    ("fit amplitude", "auto_zoom_amplitude"),
    ("seek forward", "time_down"),
    ("seek backward", "time_up"),
    ("skip to end", "time_end"),
    ("skip to start", "time_home"),
    ("toggle grid", "toggle_grid"),
    ("hide annotations", "toggle_annotations"),
    ("show annotations", "toggle_annotations"),
    ("next annotation", "next_annotation"),
    ("previous annotation", "previous_annotation"),
    ("high-pass up", "highpass_up"),
    ("high-pass down", "highpass_down"),
    ("frequency resolution up", "frequency_resolution_up"),
    ("frequency resolution down", "frequency_resolution_down"),
    ("overlap up", "overlap_up"),
    ("cheat sheet", "cheat_sheet"),
    ("command palette", "command_palette"),
]


def run_interactions(app, main_win):
    """Trigger the actions a user reaches for, and report what broke.

    A screenshot only proves the paint path.  Most of the ways this
    application can break - a panel toggle that relayouts, a channel
    change that re-decimates, a dialog that has lost its parent - are
    reachable only by acting on it.
    """
    from PyQt5.QtWidgets import QApplication

    browser = main_win.browser()
    steps = list(INTERACTIONS)
    if browser is not None:
        steps += [
            ("hide channel rail", lambda: browser.toggle_rail()),
            ("show channel rail", lambda: browser.toggle_rail()),
            ("solo channel 0", lambda: browser.toggle_solo(0)),
            ("un-solo channel 0", lambda: browser.toggle_solo(0)),
            ("maximise channel 0", lambda: browser.toggle_maximize(0)),
            ("restore channel 0", lambda: browser.toggle_maximize(0)),
            ("y mode: per-channel", lambda: main_win.set_y_mode(1)),
            ("y mode: fixed", lambda: main_win.set_y_mode(2)),
            ("y mode: shared", lambda: main_win.set_y_mode(0)),
            (
                "hide an event class",
                lambda: browser.annotations.set_event("LOC", False),
            ),
            ("show an event class", lambda: browser.annotations.set_event("LOC", True)),
            (
                "hide predicted events",
                lambda: browser.annotations.set_status("unmatched", False),
            ),
            (
                "show predicted events",
                lambda: browser.annotations.set_status("unmatched", True),
            ),
            (
                "annotations off the traces",
                lambda: browser.set_annotation_surface("trace", False),
            ),
            (
                "annotations back on the traces",
                lambda: browser.set_annotation_surface("trace", True),
            ),
            (
                "annotations off the navigator",
                lambda: browser.set_annotation_surface("navigator", False),
            ),
            (
                "annotations back on the navigator",
                lambda: browser.set_annotation_surface("navigator", True),
            ),
            ("clear annotations", lambda: browser.clear_annotations()),
            ("metadata dialog", lambda: browser.show_metadata()),
            ("message log", lambda: main_win.show_log()),
            ("shrink window", lambda: main_win.resize(1000, 700)),
            ("grow window", lambda: main_win.resize(1600, 1000)),
        ]

    clean = 0
    for label, step in steps:
        before = len(faults)
        try:
            if isinstance(step, str):
                act = getattr(main_win.acts, step, None)
                if act is None:
                    faults.append(f"interaction {label!r}: no action {step!r}")
                    continue
                act.trigger()
            else:
                step()
            pump(app, 0.4)
        except Exception:
            faults.append(f"interaction {label!r}:\n" + traceback.format_exc())
        if len(faults) == before:
            clean += 1
        else:
            sys.stderr.write(f"[interact] FAULT during: {label}\n")

    # close anything the interactions opened, so the census stays honest
    for widget in QApplication.topLevelWidgets():
        if widget is not main_win and widget.isVisible():
            widget.close()
    pump(app, 0.5)
    print(f"interactions: {clean}/{len(steps)} clean")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("wav", nargs="?", help="audio file to open")
    ap.add_argument("-o", "--output", help="write a PNG screenshot here")
    ap.add_argument("--empty", action="store_true", help="start with no file")
    ap.add_argument("--width", type=int, default=1600)
    ap.add_argument("--height", type=int, default=1000)
    ap.add_argument(
        "--settle",
        type=float,
        default=6.0,
        help="seconds to pump the event loop before grabbing",
    )
    ap.add_argument(
        "--spectrogram", action="store_true", help="make the spectrogram panel visible"
    )
    ap.add_argument(
        "--theme",
        choices=["dark", "light"],
        help="switch to this theme AFTER the window is built, exercising the "
        "live re-theme path rather than only the startup path",
    )
    ap.add_argument(
        "--audio-pair",
        action="store_true",
        help="switch playback to the explicit left/right channel pair",
    )
    ap.add_argument(
        "--activity",
        action="store_true",
        help="switch the navigator to the baseline-referenced activity overview",
    )
    ap.add_argument(
        "--events",
        help="alignment CSV to draw over the recording (see audian.events)",
    )
    ap.add_argument(
        "--goto",
        type=float,
        help="centre the time window on this second of the recording",
    )
    ap.add_argument(
        "--window",
        type=float,
        help="width of the time window in seconds",
    )
    ap.add_argument("--census", action="store_true", help="report top-level widgets")
    ap.add_argument(
        "--interact",
        action="store_true",
        help="exercise the panel/channel/zoom/dialog actions after loading",
    )
    args = ap.parse_args(argv)

    faulthandler.enable()
    qInstallMessageHandler(_handler)
    sys.excepthook = _excepthook

    import audian.audian as A
    from audian import theme
    from audian.plugins import Plugins

    app = QApplication.instance() or QApplication(sys.argv[:1])
    theme.apply(app)

    plugins = Plugins()
    plugins.load_plugins()

    files = [] if (args.empty or not args.wav) else [args.wav]

    t_build = time.monotonic()
    main_win = A.Audian(files, {}, plugins, [], 0, None, False, 0, args.events)
    main_win.resize(args.width, args.height)
    main_win.show()
    build_ms = (time.monotonic() - t_build) * 1000

    pump(app, args.settle)

    if args.spectrogram:
        browser = main_win.browser()
        if browser is not None and not getattr(browser, "show_specs", 0):
            main_win.acts.toggle_spectrograms.trigger()
            pump(app, 4.0)

    if args.events or args.goto is not None or args.window is not None:
        browser = main_win.browser()
        if browser is None:
            faults.append("--events/--goto: no browser")
        else:
            if args.events and not browser.annotations.loaded:
                faults.append(f"--events: {args.events} was not loaded")
            if args.goto is not None or args.window is not None:
                width = args.window
                if width is None:
                    trange = browser.plot_ranges["t"]
                    width = trange.r1[0] - trange.r0[0]
                start = 0.0 if args.goto is None else args.goto - 0.5 * width
                browser.set_times(start, width)
            pump(app, 2.0)
            layer = browser.annotations
            if layer.loaded:
                table = layer.table
                print(
                    f"annotations: {table.name} -- {table.summary()}\n"
                    f"             trust={layer.trust} "
                    f"channel={table.header.recording_channel} "
                    f"dropped={table.dropped}"
                )
                trange = browser.plot_ranges["t"]
                t0, t1 = trange.r0[0], trange.r1[0]
                shown = sum(
                    table[key].count_between(t0, t1) for key in layer.active_keys()
                )
                print(f"             {shown} events in view {t0:.3f}..{t1:.3f} s")
                per_surface = {}
                for overlay in browser.annotation_overlays:
                    drawn = sum(
                        c.getData()[0].size // 2 for c in overlay.curves.values()
                    )
                    per_surface[overlay.surface] = (
                        per_surface.get(overlay.surface, 0) + drawn
                    )
                print(f"             marks drawn: {per_surface}")
                if not any(per_surface.values()):
                    faults.append("annotations loaded but nothing was drawn")

    if args.theme:
        from audian import theme as _theme

        main_win.set_app_theme(args.theme)
        pump(app, 2.0)
        if _theme.current_theme() != args.theme:
            faults.append(f"--theme: still on {_theme.current_theme()}")

    if args.audio_pair:
        from audian.databrowser import DataBrowser

        browser = main_win.browser()
        if browser is None:
            faults.append("--audio-pair: no browser")
        else:
            browser.set_audio_source(DataBrowser.AUDIO_PAIR)
            browser.set_audio_pair(left=0, right=min(1, browser.data.channels - 1))
            pump(app, 1.5)
            if browser.audio_source != DataBrowser.AUDIO_PAIR:
                faults.append("--audio-pair: refused to switch")

    if args.activity:
        from audian.fulltraceplot import OVERVIEW_ACTIVITY, FullTracePlot

        strips = main_win.findChildren(FullTracePlot)
        if not strips:
            faults.append("--activity: no navigator strip found")
        for strip in strips:
            if not strip.has_activity():
                faults.append("--activity: no activity overview available")
                continue
            strip.set_overview(OVERVIEW_ACTIVITY)
            if strip.overview != OVERVIEW_ACTIVITY:
                faults.append("--activity: navigator refused to switch mode")
        pump(app, 2.0)

    responsive_ms = (time.monotonic() - t_build) * 1000

    if args.interact:
        run_interactions(app, main_win)

    if args.census:
        # Wayland census.  The criterion is *parentless* and *visible*, not
        # "top-level": every QMenu carries Qt.Popup and so reports itself as
        # a window even when it is properly owned by the menu bar, and
        # pyqtgraph's hidden control widgets are parked on one deliberate
        # hidden holder.  What must never appear is an unowned window, or a
        # second visible one, or any top-level QLabel - that last one is the
        # hover-popup bug this overhaul existed to kill.
        from PyQt5.QtWidgets import QLabel

        for _ in range(5):
            app.sendPostedEvents(None, QEvent.DeferredDelete)
            app.processEvents()
        tops = QApplication.topLevelWidgets()
        visible = [w for w in tops if w.isVisible()]
        labels = [w for w in tops if isinstance(w, QLabel)]
        holder_names = {"audianMenuHolder"}
        orphans = [
            w
            for w in tops
            if w.parentWidget() is None
            and w is not main_win
            and w.objectName() not in holder_names
        ]
        print(f"top-level widgets:   {len(tops)}")
        print(
            f"  visible:           {len(visible)} -> "
            f"{[type(w).__name__ for w in visible]}"
        )
        print(f"  top-level QLabel:  {len(labels)}")
        print(
            f"  parentless (bad):  {len(orphans)} -> "
            f"{sorted({type(w).__name__ for w in orphans})}"
        )
        if labels:
            faults.append(f"{len(labels)} top-level QLabel(s) - hover popup leak")
        if len(visible) > 1:
            faults.append(
                f"{len(visible)} visible top-level widgets: "
                f"{[type(w).__name__ for w in visible]}"
            )
        if orphans:
            faults.append(
                f"{len(orphans)} parentless top-level widgets: "
                f"{sorted({type(w).__name__ for w in orphans})}"
            )

    if args.output:
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        pix = main_win.grab()
        if pix.isNull():
            faults.append("grab() returned a null pixmap")
        elif not pix.save(args.output):
            faults.append(f"could not write {args.output}")
        else:
            print(f"wrote {args.output} ({pix.width()}x{pix.height()})")

    print(f"construct: {build_ms:.0f} ms   responsive after: {responsive_ms:.0f} ms")

    main_win.close()
    pump(app, 0.5)
    app.sendPostedEvents(None, QEvent.DeferredDelete)

    if faults:
        sys.stderr.write("\nFAULTS:\n")
        for f in faults:
            sys.stderr.write(f"  - {f}\n")
        return 1
    print("smoke test OK")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except Exception:
        traceback.print_exc()
        code = 1
    # QTimer keeps a reference alive; hard-exit so a stuck thread cannot hang us
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)
