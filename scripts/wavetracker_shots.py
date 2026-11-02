#!/usr/bin/env python
"""Regenerate every screenshot of the wavetracker plugin in the docs.

    uv run python scripts/wavetracker_shots.py [--out docs/shots]

Walks docs/wavetracker-quickstart.md on the demo recording
(`wavetracker_demo.py`), offscreen, starting from empty settings the way a new
user starts, with real mouse and key events on the real window and the real
wavetracker.  The callouts are placed from the widgets' and the tracks'
actual positions, so they move when the interface does.

It is also a test of the document: it stops with an error if a key the
quickstart names has been rebound, if the tracker's result changes so that the
track numbers in the text are wrong, or if the corrected tracks do not match
the ground truth at the end.
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

#: the window in logical pixels, and how much sharper it is drawn
WIDTH, HEIGHT, SCALE = 1280, 900, 1.5
#: the README's wider window
README_WIDTH, README_HEIGHT = 1600, 1000
#: colour of the callouts: not used by the interface, the tracks or the map
CALLOUT = "#ff3d81"
#: where the demo seems to live in the screenshots
SHOWN_HOME = "~/wavetracker-demo"
FISH_RANGE = (350.0, 1000.0)
BAND = (380.0, 960.0)
#: what the tracker makes of the demo, which the text relies on
TRACKS = {0, 1, 2, 3, 4, 5, 6, 7, 8}
PIECES = (1, 6, 7, 8)
CROSSING = (0, 2, 94.0)  # the two tracks and when their fish cross
#: keys the quickstart names, and the audian actions they must trigger
KEYS = {
    "toggle_spectrograms": "F3",
    "toggle_traces": "F2",
    "time_zoom_out_centered": "T",
    "time_zoom_in_centered": "Shift+T",
    "frequency_resolution_up": "Shift+R",
    "reset_frequency": "Ctrl+Shift+V",
    "zoom_forward": "Shift+Backspace",
}


def isolate(tmp: Path, width: int, height: int, scale: float) -> None:
    """Empty settings and a screen of the size asked for, before Qt starts."""
    screen = {
        "synchronousWindowSystemEvents": False,
        "windowFrameMargins": False,
        "screens": [
            {
                "name": "shot",
                "x": 0,
                "y": 0,
                "width": int(width * scale),
                "height": int(height * scale),
                "logicalDpi": 96,
                "logicalBaseDpi": 96,
                "dpr": 1,
            }
        ],
    }
    (tmp / "screen.json").write_text(json.dumps(screen))
    os.environ["QT_QPA_PLATFORM"] = f"offscreen:configfile={tmp / 'screen.json'}"
    os.environ["QT_SCALE_FACTOR"] = str(scale)
    for key in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME"):
        os.environ[key] = str(tmp / key)


class Walk:
    """The window and everything needed to use it like a person."""

    def __init__(
        self, recording: Path, width: int, height: int, out: Path, theme="dark"
    ):
        from PySide6.QtWidgets import QApplication

        self.app = QApplication.instance() or QApplication([])
        import audian.audian as audian_app
        from audian.plugins import Plugins

        self.out = out
        out.mkdir(parents=True, exist_ok=True)
        audian_app.apply_theme_preference(self.app, theme)
        plugins = Plugins()
        plugins.load_plugins()
        self.window = audian_app.Audian(
            [str(recording)], {}, plugins, [], 0, None, False, 0, None, theme
        )
        self.window.resize(width, height)
        self.window.show()
        self.window.activateWindow()
        self.pump(2.0)
        self.browser = self.window.browser()
        self.panel = None

    # ---- time

    def pump(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.app.processEvents()
            time.sleep(0.005)

    def until(self, condition, limit: float, what: str) -> None:
        end = time.monotonic() + limit
        while time.monotonic() < end:
            self.app.processEvents()
            if condition():
                self.pump(0.3)
                return
            time.sleep(0.02)
        raise SystemExit(f"timed out waiting for {what}")

    # ---- audian's own actions, which is what its shortcuts run

    def act(self, name: str, times: int = 1) -> None:
        for _ in range(times):
            getattr(self.window.acts, name).trigger()
            self.pump(0.25)

    def check_keys(self) -> None:
        for name, key in KEYS.items():
            shortcuts = [
                s.toString() for s in getattr(self.window.acts, name).shortcuts()
            ]
            if key not in shortcuts:
                raise SystemExit(f"{name} is {shortcuts}; the quickstart says {key}")

    def open_plugin(self) -> None:
        from PySide6.QtGui import QAction

        action = next(
            a
            for a in self.window.findChildren(QAction)
            if a.text().replace("&", "") == "Wavetracker"
        )
        action.trigger()
        self.pump(1.0)
        self.panel = self.browser.plugin_panels["Wavetracker"]
        self.panel.ask = lambda title, text, buttons: "Apply"

    def solo(self, channel: int) -> None:
        from PySide6.QtWidgets import QToolButton

        buttons = [b for b in self.window.findChildren(QToolButton) if b.text() == "S"]
        buttons[channel].click()
        self.pump(1.0)

    # ---- the lane

    def _axes(self):
        """The spectrogram lane on screen (one channel is soloed)."""
        return next(
            ax
            for ax in self.browser.spectrogram_axes()
            if ax.isVisible() and ax.getViewBox().isVisible()
        )

    def _view(self):
        return self._axes().scene().views()[0]

    def at(self, t: float, f: float):
        """The viewport position of time `t` and frequency `f`."""
        from PySide6.QtCore import QPointF

        scene = self._axes().getViewBox().mapViewToScene(QPointF(t, f))
        return QPointF(self._view().mapFromScene(scene))

    def on_window(self, t: float, f: float):
        """The window position of time `t` and frequency `f`."""
        view = self._view()
        return view.viewport().mapTo(self.window, self.at(t, f).toPoint())

    def _send(self, kind, pos, button=None, buttons=None, mods=None) -> None:
        from PySide6.QtCore import QPointF, Qt
        from PySide6.QtGui import QMouseEvent
        from PySide6.QtWidgets import QApplication

        none = Qt.MouseButton.NoButton
        vp = self._view().viewport()
        event = QMouseEvent(
            kind,
            pos,
            pos,
            QPointF(vp.mapToGlobal(pos.toPoint())),
            button or none,
            buttons or none,
            mods or Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(vp, event)

    def hover(self, t: float, f: float, settle: float = 0.5) -> None:
        from PySide6.QtCore import QEvent

        self._send(QEvent.Type.MouseMove, self.at(t, f))
        self.pump(settle)

    def click(self, t: float, f: float, mods=None) -> None:
        from PySide6.QtCore import QEvent, Qt

        left = Qt.MouseButton.LeftButton
        pos = self.at(t, f)
        self._send(QEvent.Type.MouseMove, pos, None, None, mods)
        self.pump(0.2)
        self._send(QEvent.Type.MouseButtonPress, pos, left, left, mods)
        self.pump(0.03)
        self._send(QEvent.Type.MouseButtonRelease, pos, left, None, mods)
        self.pump(0.5)

    def drag(self, points, release: bool = True, mods=None) -> None:
        """Left-drag through (t, f) `points`."""
        from PySide6.QtCore import QEvent, Qt

        left = Qt.MouseButton.LeftButton
        positions = [self.at(t, f) for t, f in points]
        self._send(QEvent.Type.MouseMove, positions[0], None, None, mods)
        self.pump(0.05)
        self._send(QEvent.Type.MouseButtonPress, positions[0], left, left, mods)
        for p in positions[1:]:
            self._send(QEvent.Type.MouseMove, p, None, left, mods)
            self.pump(0.01)
        self.pump(0.4)
        self._last_drag = (positions[-1], mods)
        if release:
            self.release()

    def release(self) -> None:
        from PySide6.QtCore import QEvent, Qt

        pos, mods = self._last_drag
        self._send(
            QEvent.Type.MouseButtonRelease,
            pos,
            Qt.MouseButton.LeftButton,
            None,
            mods,
        )
        self.pump(0.6)

    def key(self, key, mods=None) -> None:
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        view = self._view()
        view.setFocus()
        if self.panel is not None and self.panel.controller.hover_lane is None:
            # the plugin's keys need the pointer over a lane, as a hand's is
            (t0, t1), (f0, f1) = self._axes().getViewBox().viewRange()
            self.hover(t0 + 0.5 * (t1 - t0), f0 + 0.1 * (f1 - f0), 0.1)
        QTest.keyClick(view.viewport(), key, mods or Qt.KeyboardModifier.NoModifier)
        self.pump(0.3)

    # ---- pictures

    def rect_of(self, widget):
        """Where `widget` is in the window."""
        from PySide6.QtCore import QPoint, QRect

        return QRect(widget.mapTo(self.window, QPoint(0, 0)), widget.size())

    def _panel_scrollbar(self):
        from PySide6.QtWidgets import QScrollArea

        parent = self.panel.parentWidget()
        while parent is not None and not isinstance(parent, QScrollArea):
            parent = parent.parentWidget()
        return parent.verticalScrollBar()

    def clipped(self, rect):
        """`rect` cut to what the panel's scroll area shows of it."""
        area = self._panel_scrollbar().parentWidget().parentWidget()
        return rect.intersected(self.rect_of(area))

    def away(self) -> None:
        """The pointer leaves the lane: no ring, no hover box in the picture."""
        for surface in self.panel.surfaces:
            self.panel.controller.leave(surface)
        self.pump(0.3)

    def scroll_panel_to_end(self) -> None:
        bar = self._panel_scrollbar()
        bar.setValue(bar.maximum())
        self.pump(0.3)

    def scroll_panel_to_start(self) -> None:
        self._panel_scrollbar().setValue(0)
        self.pump(0.3)

    def grab(self, settle: float = 0.7):
        self.pump(settle)
        return self.window.grab()

    def save(self, pixmap, name: str, crop=None) -> Path:
        """Write `pixmap`, cut to the logical rectangle `crop`, as a compact
        256 colour PNG."""
        import io

        from PIL import Image
        from PySide6.QtCore import QBuffer, QIODevice, QRect

        if crop is not None:
            ratio = pixmap.devicePixelRatio()
            pixmap = pixmap.copy(
                QRect(
                    round(crop.x() * ratio),
                    round(crop.y() * ratio),
                    round(crop.width() * ratio),
                    round(crop.height() * ratio),
                )
            )
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        pixmap.save(buffer, "PNG")
        image = Image.open(io.BytesIO(bytes(buffer.data()))).convert("RGB")
        image = image.quantize(colors=256, dither=Image.Dither.NONE)
        path = self.out / f"{name}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path, optimize=True)
        print(f"{path}  {path.stat().st_size // 1024} KB  {image.size}", flush=True)
        return path


def callouts(pixmap, *notes, radius: float = 12.0):
    """Draw numbered callouts on a copy of `pixmap`.

    Each note is ``(label, target, bubble)``: `target` a QRect (outlined) or a
    QPoint (marked with a dot), `bubble` the (dx, dy) of the numbered disc from
    the target's centre, with a line between them.  Logical pixels.
    """
    from PySide6.QtCore import QPointF, QRect, QRectF, Qt
    from PySide6.QtGui import QColor, QFont, QPainter, QPen

    out = pixmap.copy()
    colour = QColor(CALLOUT)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    font = QFont("Inter", 11)
    font.setBold(True)
    painter.setFont(font)
    for label, target, (dx, dy) in notes:
        if isinstance(target, QRect):
            centre = QPointF(target.center())
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(colour, 2.0))
            painter.drawRoundedRect(QRectF(target).adjusted(-2, -2, 2, 2), 4, 4)
            half_w, half_h = target.width() / 2 + 2, target.height() / 2 + 2
            # the line starts on the outline, not inside the widget
            scale = min(
                half_w / abs(dx) if dx else 1e9, half_h / abs(dy) if dy else 1e9
            )
            start = centre + QPointF(dx, dy) * min(scale, 1.0)
        else:
            centre = QPointF(target)
            start = centre
            painter.setBrush(colour)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(centre, 3.5, 3.5)
        bubble = centre + QPointF(dx, dy)
        if dx or dy:
            # a dark line under the coloured one: readable on any background
            painter.setPen(QPen(QColor(0, 0, 0, 170), 4.0))
            painter.drawLine(start, bubble)
            painter.setPen(QPen(colour, 2.0))
            painter.drawLine(start, bubble)
        painter.setPen(QPen(QColor("white"), 2.0))
        painter.setBrush(colour)
        painter.drawEllipse(bubble, radius, radius)
        painter.setPen(QColor("white"))
        painter.drawText(
            QRectF(bubble.x() - radius, bubble.y() - radius, 2 * radius, 2 * radius),
            Qt.AlignmentFlag.AlignCenter,
            str(label),
        )
    painter.end()
    return out


def rect_around(*rects, margin: int = 0):
    """The rectangle around `rects`, grown by `margin`."""
    box = rects[0]
    for r in rects[1:]:
        box = box.united(r)
    return box.adjusted(-margin, -margin, margin, margin)


def fish_matches(ts, truth) -> dict:
    """For each track: (fish it follows, share of its points on that fish)."""
    import numpy as np

    result = {}
    for ident in ts.ids():
        rows = ts.rows_of(ident)
        t = ts.times[ts.idx[rows]]
        f = ts.fund[rows]
        errors = [
            np.abs(f - np.interp(t, truth["times"], fish)) for fish in truth["freqs"]
        ]
        shares = [float(np.mean(e < 1.5)) for e in errors]
        best = int(np.argmax(shares))
        result[int(ident)] = (best, shares[best], len(rows))
    return result


def quickstart(demo: Path, out: Path) -> None:
    """The quickstart, step by step; the README's pictures on the way."""
    import numpy as np
    from PySide6.QtCore import QRect, Qt, QTimer
    from PySide6.QtGui import QColor, QPainter
    from PySide6.QtWidgets import QApplication, QToolButton

    w = Walk(demo, WIDTH, HEIGHT, out)
    w.check_keys()
    print("default window:", w.browser.nfftw.currentText(), flush=True)

    # ------------------------------------------------ 2. a readable picture
    w.act("toggle_spectrograms")
    w.act("toggle_traces")
    w.act("time_zoom_out_centered", 4)
    w.solo(1)
    # the box around the fish, in audian's Zoom mode, which is how it opens
    w.drag([(0.05, BAND[1]), (60.0, 650.0), (119.95, BAND[0])])
    w.pump(0.5)
    (t0, t1), (f0, f1) = w._axes().getViewBox().viewRange()
    if not (abs(f0 - BAND[0]) < 30 and abs(f1 - BAND[1]) < 30 and t1 - t0 > 110):
        raise SystemExit(f"the box zoom went to {t0, t1, f0, f1}")
    # the way out of a bad box the text names
    w.act("reset_frequency")
    f0, f1 = w._axes().getViewBox().viewRange()[1]
    if f0 > 1.0 or f1 < 9000.0:
        raise SystemExit(f"Ctrl+Shift+V shows {f0} to {f1} Hz")
    w.browser.set_ranges("f", *BAND)  # exactly, so that the pictures repeat
    w.browser.set_times(0.0, 120.0)
    w.act("frequency_resolution_up", 5)
    print("after five Shift+R:", w.browser.nfftw.currentText(), flush=True)
    w.pump(1.5)
    solo = [
        b
        for b in w.window.findChildren(QToolButton)
        if b.text() == "S" and b.isVisible()
    ][0]
    navigator = w.rect_of(w.browser.datafig)
    w.save(
        callouts(
            w.grab(),
            (1, w.rect_of(solo), (46, 56)),
            (2, w.on_window(40.0, 735.0), (0, -46)),
            (3, w.on_window(40.0, 830.0), (0, -46)),
            (4, navigator, (navigator.width() / 2 + 28, 0)),
        ),
        "quickstart/view",
    )

    # ------------------------------------------------ 3. the plugin
    w.open_plugin()
    p = w.panel
    gutter = w.rect_of(p).x() - 34  # where the discs beside the panel sit

    def noted(number, *widgets):
        """A disc in the column left of the panel, for these widgets."""
        rect = w.clipped(rect_around(*(w.rect_of(x) for x in widgets)))
        return (number, rect, (gutter - rect.center().x(), 0))

    w.save(
        callouts(
            w.grab(),
            noted(1, p.trackvisw, p.trackrecw),
            noted(2, p.rangew),
            noted(3, p.cleanupw),
            noted(4, p.editw),
            noted(5, p.toolbtns["V"].parentWidget()),
        ),
        "quickstart/panel",
    )

    # the fish range is at the bottom of the tab
    w.scroll_panel_to_end()
    picture = w.grab()
    # no slice of spectrogram at the edge of this crop
    painter = QPainter(picture)
    painter.fillRect(
        QRect(gutter - 20, 0, w.rect_of(p).x() - gutter + 12, HEIGHT),
        QColor(w.window.palette().window().color()),
    )
    painter.end()
    section = w.rect_of(p.sections["Run settings"])
    w.save(
        callouts(
            picture,
            noted(1, p.fminw, p.fmaxw),
            noted(2, p.rangewarnw),
        ),
        "quickstart/fish-range",
        crop=QRect(
            gutter - 18,
            section.y() - 8,
            WIDTH - gutter + 18,
            w.clipped(section).height() + 16,
        ),
    )
    p.fminw.setValue(FISH_RANGE[0])
    p.fmaxw.setValue(FISH_RANGE[1])
    w.pump(0.3)
    if p.rangewarnw.isVisible():
        raise SystemExit("the fish range warning stays after narrowing the band")

    # ------------------------------------------------ 4. Track visible
    w.scroll_panel_to_start()
    started = time.monotonic()
    p.trackvisw.click()
    w.until(lambda: p.scene.snippet is not None, 240, "Track visible")
    print(f"Track visible took {time.monotonic() - started:.1f} s", flush=True)
    w.save(
        callouts(
            w.grab(),
            noted(1, p.acceptw, p.discardw),
            (2, w.on_window(14.0, 735.0), (0, -40)),
        ),
        "quickstart/snippet",
    )
    p.discardw.click()
    w.pump(0.5)

    # ------------------------------------------------ 5. Track recording
    p.trackrecw.click()
    w.until(lambda: p.ts is not None and p._job is None, 300, "Track recording")
    ts = p.ts
    found = set(int(i) for i in ts.ids())
    if found != TRACKS:
        raise SystemExit(f"the tracks are {found}, the text says {TRACKS}")
    if p.folder.name != "demo-wavetracker":
        raise SystemExit(f"results went to {p.folder}")
    truth = np.load(demo.with_name("demo_truth.npz"))

    # what Clean up says about these tracks: the text quotes it
    asked = []

    def ask(title, text, buttons):
        asked.append(text)
        return "Cancel"

    p.ask = ask

    def accept_dialog():
        dialog = QApplication.activeModalWidget()
        if dialog is None:
            QTimer.singleShot(100, accept_dialog)
        else:
            dialog.accept()

    QTimer.singleShot(200, accept_dialog)
    p.cleanupw.click()
    w.until(lambda: p._job is None and asked, 120, "Clean up")
    print("Clean up says:", asked[-1], flush=True)
    if not asked[-1].startswith("kept 6 ids") or len(ts.history.entries):
        raise SystemExit(f"Clean up: {asked}, history {ts.history.entries}")

    w.browser.set_ranges("f", *BAND)
    w.browser.set_times(0.0, 120.0)
    p.set_edit_mode(True)
    w.pump(1.0)
    readme(w, ts)
    ts = p.ts
    w.window.resize(WIDTH, HEIGHT)
    w.browser.set_ranges("f", *BAND)
    w.browser.set_times(0.0, 120.0)
    w.pump(4.5)  # the harmonic's message on the hint line has timed out
    w.hover(60.0, 480.0, 0.2)
    w.away()
    if len(ts.history.entries):
        raise SystemExit("the README's pictures left an edit behind")
    w.save(
        callouts(
            w.grab(),
            (1, w.on_window(25.6, 575.0), (44, -36)),
            (2, w.on_window(94.0, 659.0), (-10, -56)),
            (3, w.on_window(61.0, 652.0), (-30, 40)),
            (4, w.on_window(60.0, 830.0), (0, -40)),
            noted(5, p.editw),
        ),
        "quickstart/result",
    )

    # ------------------------------------------------ G and Enter
    c = p.controller
    w.hover(60.0, 480.0, 0.2)
    print("issues:", [(i.kind, i.ids, round(i.t)) for i in p.issues()], flush=True)
    w.key(Qt.Key.Key_G)
    issue = p.current_issue
    print("G:", p.issuetextw.text(), flush=True)
    if issue is None or issue.kind != "gap":
        raise SystemExit(f"G should go to a gap, it says {issue}")
    w.key(Qt.Key.Key_Return)
    print("Enter on a gap:", c.hint_text(), flush=True)
    if c.tool.key != "F" or len(ts.history.entries):
        raise SystemExit("Enter on a gap should choose Add and edit nothing")
    w.key(Qt.Key.Key_Escape)
    w.key(Qt.Key.Key_Escape)
    p.current_issue = None
    p.scene.span_outline = None
    w.browser.set_ranges("f", *BAND)
    w.browser.set_times(0.0, 120.0)
    w.pump(1.0)

    # ------------------------------------------------ 6a. merge the pieces
    w.key(Qt.Key.Key_M)
    assert c.tool.key == "M"
    t_piece = {1: 12.0, 6: 50.0, 7: 85.0, 8: 110.0}

    def freq_of(track, t):
        rows = ts.rows_of(float(track))
        times = ts.times[ts.idx[rows]]
        return float(ts.fund[rows][np.argmin(np.abs(times - t))])

    w.click(t_piece[1], freq_of(1, t_piece[1]))
    assert c.tool.anchor == 1.0, c.tool.anchor
    w.hover(t_piece[6], freq_of(6, t_piece[6]))
    assert "merge 6 into 1" in c.hint_text(), c.hint_text()
    anchor = w.on_window(t_piece[1], freq_of(1, t_piece[1]))
    tools = w.rect_of(p.toolbtns["V"].parentWidget())
    w.save(
        callouts(
            w.grab(),
            noted(1, p.toolbtns["V"].parentWidget()),
            (2, anchor, (0, -44)),
            (3, w.on_window(t_piece[6], freq_of(6, t_piece[6])), (34, -44)),
        ),
        "quickstart/merge",
        crop=QRect(0, anchor.y() - 90, WIDTH, tools.bottom() - anchor.y() + 130),
    )
    for piece in PIECES[1:]:
        w.click(t_piece[piece], freq_of(piece, t_piece[piece]))
    assert not any(len(ts.rows_of(float(i))) for i in PIECES[1:]), "pieces left"
    # undo and redo, as the text says
    ctrl = Qt.KeyboardModifier.ControlModifier
    w.key(Qt.Key.Key_Z, ctrl)
    assert len(ts.rows_of(float(PIECES[-1]))), "Ctrl+Z did not bring the piece back"
    w.key(Qt.Key.Key_Z, ctrl | Qt.KeyboardModifier.ShiftModifier)
    assert not len(ts.rows_of(float(PIECES[-1]))), "Ctrl+Shift+Z did not redo"
    w.key(Qt.Key.Key_Escape)

    # ------------------------------------------------ 6b. swap at a crossing
    a, b, t_cross = CROSSING
    w.key(Qt.Key.Key_V)
    w.browser.set_ranges("f", 625.0, 695.0)
    w.browser.set_times(70.0, 50.0)
    w.pump(1.5)
    ta = 75.0
    w.click(ta, freq_of(a, ta))
    assert set(c.scene.selected_ids()) == {float(a)}, c.scene.selected_ids()
    w.click(ta, freq_of(b, ta), Qt.KeyboardModifier.ControlModifier)
    assert set(c.scene.selected_ids()) == {float(a), float(b)}
    # only the time of the pointer counts: below the tracks, it hides nothing
    w.hover(t_cross, 634.0)
    w.save(
        callouts(
            w.grab(),
            (1, w.on_window(ta, freq_of(a, ta)), (-34, 40)),
            (2, w.on_window(ta, freq_of(b, ta)), (-34, -40)),
            (3, w.on_window(t_cross, 634.0), (56, -30)),
            noted(4, p.stripw),
        ),
        "quickstart/swap",
    )
    w.key(Qt.Key.Key_X, Qt.KeyboardModifier.ShiftModifier)
    assert "wap" in ts.history.entries[-1].label, ts.history.entries[-1].label
    w.key(Qt.Key.Key_Escape)
    w.away()
    w.save(w.grab(), "quickstart/swap-after", crop=w.rect_of(w._view()))

    # ------------------------------------------------ 6c. the gap in a track
    w.browser.set_ranges("f", 625.0, 695.0)
    w.browser.set_times(40.0, 40.0)
    w.pump(1.0)
    rows = ts.rows_of(float(a))
    times = ts.times[ts.idx[rows]]
    jump = np.argmax(np.diff(times))
    g0, g1 = float(times[jump]), float(times[jump + 1])
    print(f"the gap in track {a}: {g0:.1f} - {g1:.1f} s", flush=True)
    f0, f1 = float(ts.fund[rows][jump]), float(ts.fund[rows][jump + 1])
    w.key(Qt.Key.Key_F)
    before = len(rows)
    path = [
        (t, f0 + (f1 - f0) * (t - g0) / (g1 - g0))
        for t in np.linspace(g1 + 0.5, g0 - 0.5, 40)
    ]
    w.drag(path, release=False)
    middle = w.on_window(*path[20])
    w.save(
        callouts(
            w.grab(),
            noted(1, p.toolbtns["V"].parentWidget()),
            (2, middle, (0, -60)),
        ),
        "quickstart/add",
        crop=QRect(0, middle.y() - 110, WIDTH, tools.bottom() - middle.y() + 150),
    )
    w.release()
    w.until(lambda: len(ts.rows_of(float(a))) != before, 30, "the ridge")
    print(f"track {a}: {before} -> {len(ts.rows_of(float(a)))} points", flush=True)
    w.key(Qt.Key.Key_Escape)

    # ------------------------------------------------ 6d. a harmonic
    w.browser.set_ranges("f", *BAND)
    w.browser.set_times(0.0, 120.0)
    w.pump(1.0)
    w.key(Qt.Key.Key_F)
    harmonic = 2 * float(np.median(ts.fund[ts.rows_of(4.0)]))
    w.drag([(t, harmonic) for t in np.linspace(40.0, 70.0, 40)])
    w.until(lambda: "harmonic" in c.hint_text(), 30, "the harmonic warning")
    w.hover(55.0, harmonic - 60.0, 1.0)
    w.away()
    w.save(
        callouts(
            w.grab(),
            (1, w.on_window(62.0, harmonic), (0, -46)),
            noted(2, p.hintw),
        ),
        "quickstart/harmonic",
    )
    w.hover(55.0, harmonic - 60.0, 0.5)
    w.key(Qt.Key.Key_Return)
    print("after Enter:", c.hint_text(), flush=True)
    w.key(Qt.Key.Key_V)

    # ------------------------------------------------ 7. the result, saved
    matches = fish_matches(ts, truth)
    print("track: fish, share on it, points", matches, flush=True)
    fish = [m[0] for m in matches.values()]
    if sorted(fish) != list(range(6)) or min(m[1] for m in matches.values()) < 0.95:
        raise SystemExit(f"the corrected tracks do not match the truth: {matches}")
    w.away()
    w.scroll_panel_to_start()
    w.save(
        callouts(
            w.grab(),
            noted(1, p.dirtyw, p.savew),
            noted(2, p.sections["History"]),
        ),
        "quickstart/done",
    )
    assert p.save(), "save failed"
    w.pump(0.5)
    saved = sorted(x.name for x in p.folder.iterdir())
    print("saved", saved, flush=True)
    if "ident_v.tracked.npy" not in saved:
        raise SystemExit("saving kept no ident_v.tracked.npy")
    from wavetracker.results import Results

    results = Results.load(p.folder)
    if sorted(np.unique(results.ident_v[np.isfinite(results.ident_v)])) != sorted(
        ts.ids()
    ):
        raise SystemExit("Results.load does not see the corrected identities")
    one = results.ident_v == 1
    print("fish 1:", one.sum(), results.times[results.idx_v[one]][:2], flush=True)
    sys.stdout.flush()


def readme(w: Walk, ts) -> None:
    """The README's three pictures, on a wider window; leaves nothing behind."""
    import numpy as np
    from PySide6.QtCore import Qt

    p = w.panel
    w.window.resize(README_WIDTH, README_HEIGHT)
    w.pump(1.5)
    w.away()
    w.save(w.grab(), "wavetracker-overview")
    w.browser.set_ranges("f", 620.0, 885.0)
    w.browser.set_times(20.0, 60.0)
    w.pump(1.5)
    w.key(Qt.Key.Key_F)
    harmonic = 2 * float(np.median(ts.fund[ts.rows_of(4.0)]))
    wavy = [(t, harmonic + 4 * np.sin(t / 3)) for t in np.linspace(30.0, 52.0, 60)]
    w.drag(wavy, release=False)
    w.save(w.grab(), "wavetracker-add")
    w.release()
    w.until(lambda: "harmonic" in p.controller.hint_text(), 30, "the harmonic")
    w.hover(47.0, harmonic - 22.0, 1.0)
    w.save(w.grab(), "wavetracker-harmonic")
    # back to the tracker's output as if nothing happened (ids included: a
    # track number used and undone is not used again within a session)
    w.key(Qt.Key.Key_V)
    assert p.open_results(p.folder, ask=False), "could not reopen the results"
    w.pump(0.5)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=Path("docs/shots"))
    parser.add_argument("--demo", type=Path, help="an existing demo.wav to reuse")
    args = parser.parse_args()
    out = args.out.resolve()
    tmp = Path(tempfile.mkdtemp(prefix="wavetracker-shots-"))
    isolate(tmp, README_WIDTH, README_HEIGHT, SCALE)
    sys.path.insert(0, str(Path(__file__).parent))
    from wavetracker_demo import write_demo

    demo = write_demo(tmp / "wavetracker-demo")

    import audian_plugins.eodsorter.panel as panel_module

    elide = panel_module.elide_middle
    panel_module.elide_middle = lambda text, *a: elide(
        text.replace(str(demo.parent), SHOWN_HOME), *a
    )
    quickstart(demo, out)
    shutil.rmtree(tmp, ignore_errors=True)
    sys.stdout.flush()
    # past Qt's teardown, which can abort at exit with windows still open
    os._exit(0)


if __name__ == "__main__":
    main()
