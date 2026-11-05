"""The training curve: what each epoch of a detector's training did.

Two small charts on one epoch axis: the box loss on the training tiles and on the
held-out ones, and mAP50 on the held-out ones.  A dashed line marks the epoch
Ultralytics kept as best, which is the model that `best.pt` holds -- not the last epoch.

When nothing was held out, the "held-out" numbers are measured on the training tiles
and the caption says so: such a curve shows that training converged, and says nothing
about how well it generalises.

Drawn with a `QPainter` and not with pyqtgraph.  With two pyqtgraph plots in this tab,
the panel tests crashed in 3 of 4 runs as tabs were torn down -- a plot item deleted
with its scene and calling into what was already gone, with no Python frame -- and in
none of 3 once the charts were painted here.  Two line
charts of a few hundred points need none of a graphics scene, and drawing them here
also leaves no hidden pyqtgraph forms to adopt.  The colours are read from the theme
on every paint, so a theme switch is followed without anything being told.

Fed one point at a time while a training runs (`add`), or a whole run at once from a
model's ``model.json`` (`set_points`).  A point is a dict with ``epoch`` and any of
``train_loss``, ``val_loss`` and ``map50``.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QLabel, QSizePolicy, QVBoxLayout, QWidget

from audian.pluginapi import theme

#: point key -> (chart, marker colour index, what the caption calls it)
SERIES = {
    "train_loss": ("loss", 0, "training loss"),
    "val_loss": ("loss", 1, "held-out loss"),
    "map50": ("score", 2, "mAP50"),
}

#: Height of each of the two charts, in pixels: enough to read a trend from in a side
#: panel that has to leave room for the controls above it.
CHART_HEIGHT = 56


class Charts(QWidget):
    """The two charts, painted."""

    def __init__(self, owner: "TrainingCurve") -> None:
        super().__init__(owner)
        self.owner = owner
        self.setFixedHeight(2 * CHART_HEIGHT + theme.S8)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt's spelling
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(theme.token("bg.plot")))
        painter.setFont(theme.font_mono(theme.SIZE_SMALL_PT))
        width = float(self.width())
        top = QRectF(0, 0, width, CHART_HEIGHT)
        bottom = QRectF(0, CHART_HEIGHT + theme.S8, width, CHART_HEIGHT)
        self.chart(painter, top, "loss", None)
        self.chart(painter, bottom, "score", (0.0, 1.0))
        painter.end()

    def chart(self, painter: QPainter, area: QRectF, name: str, span) -> None:
        owner = self.owner
        keys = [key for key, (chart, _c, _n) in SERIES.items() if chart == name]
        lines = {key: owner.series(key) for key in keys}
        values = [v for _xs, ys in lines.values() for v in ys]
        label = "loss" if name == "loss" else "mAP50"
        muted = QColor(theme.token("fg.muted"))
        painter.setPen(QPen(muted))
        painter.drawText(
            area.adjusted(theme.S4, 0, 0, 0), Qt.AlignmentFlag.AlignTop, label
        )
        if not values or not owner.points:
            return
        low, high = span if span is not None else (min(values), max(values))
        if high <= low:
            high = low + 1.0
        first = owner.points[0]["epoch"]
        last = max(owner.points[-1]["epoch"], first + 1)
        inner = area.adjusted(theme.S4, theme.S12, -theme.S4, -theme.S2)

        def at(epoch, value) -> QPointF:
            x = inner.left() + (epoch - first) / (last - first) * inner.width()
            y = inner.bottom() - (value - low) / (high - low) * inner.height()
            return QPointF(x, y)

        if owner.best is not None and first <= owner.best <= last:
            pen = QPen(muted)
            pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            x = at(owner.best, low).x()
            painter.drawLine(QPointF(x, inner.top()), QPointF(x, inner.bottom()))
        for key, (xs, ys) in lines.items():
            if not xs:
                continue
            _chart, color, _name = SERIES[key]
            path = QPainterPath(at(xs[0], ys[0]))
            for epoch, value in zip(xs[1:], ys[1:]):
                path.lineTo(at(epoch, value))
            pen = QPen(QColor(theme.marker_color(color)))
            pen.setWidthF(theme.LW_THIN * 1.5)
            painter.setPen(pen)
            painter.drawPath(path)
        painter.setPen(QPen(muted))
        painter.drawText(
            area.adjusted(0, 0, -theme.S4, 0),
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight,
            f"{high:.3g}",
        )


class TrainingCurve(QWidget):
    """Losses and mAP50 per epoch, for one training."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(theme.S4)
        self.charts = Charts(self)
        box.addWidget(self.charts)
        # word-wrapped, not cut to the width: the note is what says whether the
        # numbers were measured on held-out tiles, and a narrow panel must show all of it
        self.captionw = QLabel("", self)
        self.captionw.setFont(theme.font_mono(theme.SIZE_SMALL_PT))
        self.captionw.setWordWrap(True)
        theme.tint(self.captionw, "fg.muted")
        box.addWidget(self.captionw)
        self.points: list = []
        self.best: Optional[int] = None
        self.note = ""

    # -- what it shows ---------------------------------------------------

    def clear(self, note: str = "") -> None:
        self.points = []
        self.best = None
        self.note = note
        self.redraw()

    def add(self, point: dict) -> None:
        """One more epoch, while training runs."""
        if "epoch" not in point:
            return
        self.points = [p for p in self.points if p["epoch"] != point["epoch"]]
        self.points.append(dict(point))
        self.points.sort(key=lambda p: p["epoch"])
        self.redraw()

    def set_points(self, points, best: Optional[int] = None, note: str = "") -> None:
        """A whole run: the curve kept in a model's ``model.json``."""
        self.points = sorted(
            (dict(p) for p in points or [] if "epoch" in p), key=lambda p: p["epoch"]
        )
        self.best = best
        self.note = note
        self.redraw()

    def series(self, key: str) -> tuple:
        """``(epochs, values)`` of one line, as drawn."""
        drawn = [(p["epoch"], p[key]) for p in self.points if key in p]
        return [e for e, _ in drawn], [v for _, v in drawn]

    def redraw(self) -> None:
        self.captionw.setText(self.caption())
        self.charts.update()

    def caption(self) -> str:
        if not self.points:
            return self.note
        last = self.points[-1]
        parts = [f"epoch {last['epoch']}"]
        if "map50" in last:
            parts.append(f"mAP50 {last['map50']:.3f}")
        if self.best is not None:
            parts.append(f"best epoch {self.best}")
        key = ", ".join(
            name for key, (_c, _i, name) in SERIES.items() if self.series(key)[0]
        )
        text = " · ".join(parts) + f"\n{key}"
        return f"{text}\n{self.note}" if self.note else text
