"""Drawing an `EventTable` over the traces and the spectrograms.

Two objects, with a deliberate split:

`AnnotationLayer`
    One per browser.  Owns the loaded table, which classes are switched on,
    and -- the part that makes this scale -- the *shared* window cache.  A
    sixteen channel file has 32 plots showing the same time range, so the
    windowing and decimation are done once and every plot reads the same
    arrays back.

`EventOverlay`
    One per plot.  Turns those arrays into vertical lines.  It holds one
    `pg.PlotCurveItem` per event class, drawn with ``connect='pairs'``: the
    whole class is a single item and a single numpy array, so a redraw never
    walks a row from Python.

What the drawing says
---------------------
* **Colour** is the ``event`` label -- LOC, BASE, VOLLEY, MARKER -- from the
  theme's categorical marker palette, so it survives a theme switch and each
  hue is contrast-checked against both plot grounds.
* **Shape** is whether the event was *observed*.  A ``matched`` row was seen
  in the recording and is drawn as a solid line across the whole lane.  An
  ``unmatched`` or ``outside`` row was not: its time is what the fit
  predicts, and it is drawn as a short dashed stub near the top of the lane
  with a hollow diamond cap.  The two never look alike, at any zoom.
* **Dashing everything** is what an *unvalidated* header buys.  If the fit
  was never checked, every position on screen is a guess, and the overlay
  says so on its own rather than relying on a badge the reader may not look
  at.  The badge is there too (`AnnotationLayer.badge`).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import numpy as np
import pyqtgraph as pg

from PyQt5.QtCore import Qt, QObject, QRect
from PyQt5.QtGui import QIcon, QPainter, QPixmap

try:
    from PyQt5.QtCore import Signal
except ImportError:  # pragma: no cover - PyQt5 always has pyqtSignal
    from PyQt5.QtCore import pyqtSignal as Signal

from . import theme
from .events import (
    TRUST_OK,
    TRUST_UNVALIDATED,
    TRUST_WARN,
    EventClass,
    EventTable,
    find_alignment,
)


log = logging.getLogger(__name__)


#: Opacity of an event line over a waveform.  Well below 1: the point of the
#: overlay is to say where an event is *on the trace*, and an opaque line
#: hides the pulse it is pointing at.
TRACE_ALPHA = 0.62

#: Over a spectrogram the same line competes with an image rather than with
#: an empty ground, so it needs more of it.
SPECTROGRAM_ALPHA = 0.8

#: Everything is scaled by this when the alignment was never validated.
UNVALIDATED_ALPHA = 0.6

#: Vertical extent of a line, as a fraction of the lane, per kind.
MEASURED_SPAN = (0.0, 1.0)
PREDICTED_SPAN = (0.60, 0.90)

#: Above this many drawn events the diamond caps on predicted events are
#: dropped: at that density they merge into a bar and say nothing the dashed
#: stub does not already say.
CAP_LIMIT = 400

#: Fallback width in device pixels when a view box has not been laid out yet.
DEFAULT_PIXELS = 1200

#: Below this the view box has not been laid out and its width is not a pixel
#: budget.  Cutting the decimation to a two pixel view would collapse a whole
#: window of events onto two lines and leave them there, because nothing
#: redraws until the next range change.
MIN_PIXELS = 16


class AnnotationLayer(QObject):
    """The annotation state of one browser: table, toggles, window cache."""

    #: the set of drawn classes changed and every overlay must rebuild
    sigTableChanged = Signal()
    #: only visibility changed; overlays redraw but keep their items
    sigVisibilityChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.table: Optional[EventTable] = None
        self.visible = True
        # Toggles are held on the two axes a reader actually thinks in --
        # *what* an event is and *whether it was seen* -- rather than on the
        # cross product.  Four event chips and two status chips cover twelve
        # classes, and the two questions stay separable: "hide the volleys"
        # and "hide everything that was only predicted" are one click each.
        self.events: dict[str, bool] = {}
        self.statuses: dict[str, bool] = {}
        #: set when the file's ``#recording=`` names a different recording
        self.recording_mismatch: Optional[str] = None
        #: bumped whenever anything an overlay draws from changes.  An
        #: overlay compares it against what it last drew and skips the whole
        #: redraw when neither the view nor this has moved -- which is most
        #: calls, because a pan delivers sigRangeChanged to every plot *and*
        #: goes through Panels.update_plots().
        self.revision = 0
        # shared window cache: (t0, t1, pixels) -> {key: (x pairs, drawn, total)}
        self._cache_key: Optional[tuple] = None
        self._cache: dict[tuple[str, str], tuple] = {}

    # --- loading ---------------------------------------------------------

    def load(self, path, recording=None) -> EventTable:
        """Read `path` and make it the current table.

        `recording` is the file the browser has open.  When the header names
        a different recording the table is still loaded -- refusing to show
        it would help nobody -- but `recording_mismatch` is set and the
        caller is expected to say so loudly.  A fit belongs to exactly one
        recording; used against another, every line lands somewhere plausible
        and wrong.
        """
        table = EventTable.from_csv(path)
        self.table = table
        self.events = {}
        self.statuses = {}
        for event_class in table:
            self.events.setdefault(event_class.event, True)
            self.statuses.setdefault(event_class.status, True)
        self.visible = True
        self.recording_mismatch = None
        if recording is not None and table.matches_recording(recording) is False:
            self.recording_mismatch = table.header.recording
        self.invalidate()
        self.sigTableChanged.emit()
        return table

    def discover(self, recording) -> Optional[Path]:
        """Find an alignment file that names `recording`, without loading it."""
        try:
            return find_alignment(recording)
        except OSError:
            return None

    def clear(self) -> None:
        self.table = None
        self.events = {}
        self.statuses = {}
        self.recording_mismatch = None
        self.invalidate()
        self.sigTableChanged.emit()

    # --- state -----------------------------------------------------------

    @property
    def loaded(self) -> bool:
        return self.table is not None

    @property
    def trust(self) -> str:
        return self.table.trust if self.table is not None else TRUST_OK

    @property
    def unvalidated(self) -> bool:
        return self.trust == TRUST_UNVALIDATED

    def is_enabled(self, key) -> bool:
        event, status = key
        return (
            self.visible
            and self.events.get(event, False)
            and self.statuses.get(status, False)
        )

    def active_keys(self) -> list:
        if self.table is None or not self.visible:
            return []
        return [c.key for c in self.table if self.is_enabled(c.key)]

    def set_event(self, event: str, on: bool) -> None:
        if self.events.get(event) == bool(on):
            return
        self.events[event] = bool(on)
        self.revision += 1
        self.sigVisibilityChanged.emit()

    def set_status(self, status: str, on: bool) -> None:
        if self.statuses.get(status) == bool(on):
            return
        self.statuses[status] = bool(on)
        self.revision += 1
        self.sigVisibilityChanged.emit()

    def event_counts(self) -> list:
        """``(event, count, colour, enabled)`` per event label, in file order."""
        if self.table is None:
            return []
        counts: dict[str, int] = {}
        colors: dict[str, str] = {}
        for event_class in self.table:
            counts[event_class.event] = counts.get(event_class.event, 0) + len(
                event_class
            )
            colors.setdefault(event_class.event, self.color(event_class))
        return [
            (event, counts[event], colors[event], self.events.get(event, True))
            for event in counts
        ]

    def status_counts(self) -> list:
        """``(status, count, measured, enabled)`` per status, in file order."""
        if self.table is None:
            return []
        counts: dict[str, int] = {}
        measured: dict[str, bool] = {}
        for event_class in self.table:
            counts[event_class.status] = counts.get(event_class.status, 0) + len(
                event_class
            )
            measured.setdefault(event_class.status, event_class.measured)
        return [
            (
                status,
                counts[status],
                measured[status],
                self.statuses.get(status, True),
            )
            for status in counts
        ]

    def set_visible(self, on: bool) -> None:
        on = bool(on)
        if on == self.visible:
            return
        self.visible = on
        self.revision += 1
        self.sigVisibilityChanged.emit()

    def toggle(self) -> None:
        self.set_visible(not self.visible)

    def invalidate(self) -> None:
        self.revision += 1
        self._cache_key = None
        self._cache = {}

    # --- the shared window ------------------------------------------------

    def window(self, key, t0: float, t1: float, pixels: int) -> tuple:
        """``(x pairs, drawn, total)`` for one class in one view.

        Every plot in the stack shows the same time range, so the first one
        to ask pays for the search and the decimation and the other 31 read
        the answer.  ``x pairs`` is already interleaved for
        ``connect='pairs'`` -- ``[t0, t0, t1, t1, ...]`` -- because that too
        is identical in every plot.
        """
        if self.table is None:
            return _EMPTY, 0, 0
        cache_key = (round(float(t0), 9), round(float(t1), 9), int(pixels))
        if cache_key != self._cache_key:
            self._cache_key = cache_key
            self._cache = {}
        hit = self._cache.get(key)
        if hit is None:
            event_class = self.table.get(key)
            if event_class is None:
                hit = (_EMPTY, 0, 0)
            else:
                times, total = event_class.window(t0, t1, pixels)
                hit = (np.repeat(times, 2), int(times.size), total)
            self._cache[key] = hit
        return hit

    # --- appearance -------------------------------------------------------

    def color(self, event_class: EventClass):
        return theme.marker_color(event_class.color_index)

    def line_pen(self, event_class: EventClass, alpha: float):
        """Pen for one class: hue from the event, dashes from the evidence."""
        if self.unvalidated:
            alpha *= UNVALIDATED_ALPHA
            # Both patterns become broken, but they stay different from each
            # other: an unvalidated file must still let a reader tell an
            # observed row from a predicted one.
            style = Qt.DashLine if event_class.measured else Qt.DotLine
        else:
            style = Qt.SolidLine if event_class.measured else Qt.DashLine
        width = theme.LW_THIN if event_class.measured else theme.LW_HAIRLINE
        return theme.pen(self.color(event_class), width=width, alpha=alpha, style=style)

    def span(self, event_class: EventClass) -> tuple[float, float]:
        return MEASURED_SPAN if event_class.measured else PREDICTED_SPAN

    # --- what the badge has to say ---------------------------------------

    def badge(self) -> tuple[str, str, str]:
        """``(text, token, tooltip)`` for the annotation status chip.

        This is the other half of the promise that an unvalidated alignment
        is never shown quietly.  The chip is always present while a table is
        loaded -- there is no state in which the overlay is on screen and the
        reader has to go looking for its provenance.
        """
        if self.table is None:
            return ("", "fg.muted", "")
        header = self.table.header
        fit = header.fit_summary() or "no fit parameters in the header"
        if self.recording_mismatch:
            return (
                "WRONG RECORDING",
                "danger",
                f"This alignment was fitted against {self.recording_mismatch}, "
                f"not against the open file.\nEvery annotation is in the wrong "
                f"place.\n{fit}",
            )
        trust = header.trust
        if trust == TRUST_UNVALIDATED:
            why = (
                "validated=0 in the header"
                if header.validated is not None
                else "no validated key in the header"
            )
            return (
                "UNVALIDATED",
                "danger",
                f"The alignment fit was never validated ({why}).\n"
                f"Every annotation is positioned by that fit, so if it is "
                f"wrong they are all wrong and still look plausible.\n"
                f"Lines are drawn broken to say so.\n{fit}",
            )
        if trust == TRUST_WARN:
            return (
                "WARNINGS",
                "accent",
                "The alignment is validated but the writer recorded warnings:\n"
                + "\n".join(f"• {w}" for w in header.warnings)
                + f"\n{fit}",
            )
        return ("validated", "success", f"Alignment validated.\n{fit}")


_EMPTY = np.empty(0, dtype=np.float64)


class EventOverlay:
    """The annotation lines of one plot.

    Not a `pg.GraphicsObject`: it is a small controller over one curve item
    (plus, for predicted classes, one scatter item) per event class.  Letting
    pyqtgraph own the items means the numpy array goes straight into
    ``arrayToQPath`` and no Python loop ever sees an event.
    """

    def __init__(self, plot, layer: AnnotationLayer, alpha: float = TRACE_ALPHA):
        self.plot = plot
        self.layer = layer
        self.alpha = float(alpha)
        self.curves: dict[tuple[str, str], pg.PlotCurveItem] = {}
        self.caps: dict[tuple[str, str], pg.ScatterPlotItem] = {}
        self._keys: tuple = ()
        #: what was last drawn: view range, pixel budget, layer revision
        self._drawn: Optional[tuple] = None
        #: classes whose curve is currently empty, so that clearing one that
        #: is already clear costs nothing.  Most classes are off screen most
        #: of the time, and setData() is not free even with nothing in it.
        self._blank: set = set()
        view = plot.getViewBox()
        if view is not None:
            # a y-only zoom changes how tall every line has to be, and a
            # resize changes the pixel budget the decimation is cut to
            view.sigRangeChanged.connect(self._view_changed)
            view.sigResized.connect(self._view_changed)

    # --- items ------------------------------------------------------------

    def rebuild(self) -> None:
        """Match the item set to the loaded table."""
        table = self.layer.table
        keys = tuple(c.key for c in table) if table is not None else ()
        if keys == self._keys:
            self.polish()
            return
        for key in list(self.curves):
            if key in keys:
                continue
            self.plot.removeItem(self.curves.pop(key))
            cap = self.caps.pop(key, None)
            if cap is not None:
                self.plot.removeItem(cap)
        for key in keys:
            if key in self.curves:
                continue
            event_class = table[key]
            curve = pg.PlotCurveItem(
                connect="pairs", antialias=False, skipFiniteCheck=True
            )
            # Above the traces, below the crosshair and the playback cursor:
            # an annotation must not hide where the sound is playing.
            curve.setZValue(15)
            self.plot.addItem(curve, ignoreBounds=True)
            self.curves[key] = curve
            if not event_class.measured:
                cap = pg.ScatterPlotItem(
                    symbol="d", size=theme.S8, pxMode=True, hoverable=False
                )
                cap.setZValue(16)
                self.plot.addItem(cap, ignoreBounds=True)
                self.caps[key] = cap
        self._keys = keys
        self._drawn = None
        self._blank = set()
        self.polish()

    def polish(self) -> None:
        """Re-resolve every pen from the active theme and trust state."""
        table = self.layer.table
        if table is None:
            return
        for key, curve in self.curves.items():
            event_class = table.get(key)
            if event_class is None:
                continue
            curve.setPen(self.layer.line_pen(event_class, self.alpha))
            cap = self.caps.get(key)
            if cap is not None:
                color = self.layer.color(event_class)
                # hollow, always: a filled dot reads as a measurement
                cap.setBrush(pg.mkBrush(None))
                cap.setPen(theme.pen(color, width=theme.LW_THIN, alpha=self.alpha))

    def clear(self) -> None:
        for item in list(self.curves.values()) + list(self.caps.values()):
            self.plot.removeItem(item)
        self.curves = {}
        self.caps = {}
        self._keys = ()
        self._drawn = None
        self._blank = set()

    # --- drawing ----------------------------------------------------------

    def _view_changed(self, *args) -> None:
        self.update_plot()

    def pixels(self) -> int:
        """Device pixel width of this plot's view box."""
        view = self.plot.getViewBox()
        if view is None:
            return DEFAULT_PIXELS
        widget = self.plot.getViewWidget()
        ratio = widget.devicePixelRatioF() if widget is not None else 1.0
        pixels = int(view.width() * ratio)
        return pixels if pixels >= MIN_PIXELS else DEFAULT_PIXELS

    def update_plot(self) -> None:
        table = self.layer.table
        if table is None or not self.curves:
            return
        # A hidden lane still gets its view box's sigRangeChanged, and
        # redrawing what nobody can see is the whole cost of hiding a channel
        # in a sixteen channel stack.  `_drawn` is left alone, so the lane
        # redraws itself when it comes back.
        if not self.plot.isVisible():
            return
        view = self.plot.getViewBox()
        if view is None:
            return
        (t0, t1), (y0, y1) = view.viewRange()
        pixels = self.pixels()
        # A pan reaches an overlay twice -- once through the view box's own
        # sigRangeChanged and once through Panels.update_plots() -- and a
        # y-only zoom reaches it without moving a single line's x.  setData()
        # invalidates a QPainterPath and schedules a repaint whatever it is
        # handed, so the cheapest redraw is the one that does not happen.
        state = (t0, t1, y0, y1, pixels, self.layer.revision)
        if state == self._drawn:
            return
        self._drawn = state
        height = y1 - y0
        for key, curve in self.curves.items():
            cap = self.caps.get(key)
            drawn = 0
            if self.layer.is_enabled(key):
                xpairs, drawn, _total = self.layer.window(key, t0, t1, pixels)
            if drawn == 0:
                if key not in self._blank:
                    curve.setData(_EMPTY, _EMPTY)
                    if cap is not None:
                        cap.setData([], [])
                    self._blank.add(key)
                continue
            self._blank.discard(key)
            low, high = self.layer.span(table[key])
            segment = np.array([y0 + low * height, y0 + high * height])
            # xpairs is the layer's cached array, shared with every other
            # plot in the stack; pyqtgraph keeps a reference and never writes
            # to it, so one window is one allocation for all 32 lanes.
            curve.setData(xpairs, np.tile(segment, drawn), connect="pairs")
            if cap is not None:
                if drawn <= CAP_LIMIT:
                    # every second entry of xpairs is one event time
                    cap.setData(xpairs[::2], np.full(drawn, segment[1]))
                else:
                    cap.setData([], [])


# --- legend ------------------------------------------------------------------
#
# The chips in the parameter bar are the only legend the overlay has, so their
# icons are drawn with the same pens the plot uses rather than with a generic
# swatch.  A reader can then match a line on screen to a chip by looking at
# it, instead of by remembering a rule.

#: Icon size of a legend chip, in logical pixels.
LEGEND_W = 18
LEGEND_H = 12


def _legend_pixmap(color: str, measured: bool, unvalidated: bool) -> QPixmap:
    pixmap = QPixmap(LEGEND_W, LEGEND_H)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, False)
    if unvalidated:
        style = Qt.DashLine if measured else Qt.DotLine
    else:
        style = Qt.SolidLine if measured else Qt.DashLine
    width = theme.LW_THIN if measured else theme.LW_HAIRLINE
    painter.setPen(theme.pen(color, width=width, style=style, cosmetic=False))
    x = LEGEND_W // 2
    if measured:
        # full height, exactly as it is drawn in the lane
        painter.drawLine(x, 0, x, LEGEND_H - 1)
    else:
        # the stub, with its hollow cap
        painter.drawLine(x, 3, x, LEGEND_H - 1)
        painter.setPen(theme.pen(color, width=theme.LW_HAIRLINE, cosmetic=False))
        painter.drawRect(QRect(x - 2, 1, 4, 4))
    painter.end()
    return pixmap


def legend_icon(color: str, measured: bool, unvalidated: bool = False) -> QIcon:
    """A chip icon drawn with the pen the overlay itself uses."""
    return QIcon(_legend_pixmap(color, measured, unvalidated))


def swatch_pixmap(color: str) -> QPixmap:
    """A filled square in an event's colour, with a hairline ring.

    The ring is what makes a dark swatch visible on the dark theme's chrome
    and a light one visible on the daylight theme's.  Drawn on the same
    canvas as `legend_icon`, so the event chips and the status chips sit on
    one baseline instead of two.
    """
    pixmap = QPixmap(LEGEND_W, LEGEND_H)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setBrush(theme.brush(color))
    painter.setPen(theme.pen("border", width=theme.HAIRLINE, cosmetic=False))
    inset = (LEGEND_W - LEGEND_H) // 2
    painter.drawRect(inset + 1, 1, LEGEND_H - 3, LEGEND_H - 3)
    painter.end()
    return pixmap


def swatch_icon(color: str) -> QIcon:
    return QIcon(swatch_pixmap(color))
