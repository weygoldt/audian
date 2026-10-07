"""Editing tracks with the mouse: tools, the per-lane surface, the key router.

Design sections 5.3-5.6, 5.9 and 5.10.  Three layers:

``Tool`` subclasses (`SelectTool`, `EraseTool`, `CutTool`, `MergeTool`,
`AssignTool`, `AddTool`)
    The gesture logic, as plain methods -- ``press``, ``move``, ``release``,
    ``click``, ``hover_changed``, ``cancel`` -- that read the shared
    `SceneState` and the hit tests of `geometry`, ask the model for a *plan*
    on every hover or stroke update, put what the plan would do into
    ``scene.marks`` (the overlays draw it), and on commit hand the very same
    plan to the controller.  The preview is therefore by construction what
    will happen.  Tests drive these methods directly.

`ToolController`
    Owns the tools, the hover state, the selection order and the brush
    settings; turns plans into ``sigCommit`` and rejections into the hint
    line.  Holds the non-gesture actions too (Del, N, Shift+M, ...).

`ToolSurface`
    One per lane: an invisible `pg.GraphicsObject` child of the lane's view
    box that pre-claims the *left* button while edit mode is on (so middle-
    and right-drags still reach the view box), forwards pointer events to the
    controller, and draws the screen-space feedback -- the brush ring, the
    stroke as it is painted, the cut line and the hover label box.

`KeyRouter`
    An application event filter that claims the plugin's keys only in edit
    mode, only while the pointer is over a lane or the focus is in the panel,
    and never in a text field (design 5.9).

Deviations from 7.3, all additive: tools also have ``hover_changed`` and
``clear_state`` (Esc's second rung); the controller has a ``host`` (the
panel, or a stand-in in tests) for ``notify``, ``zoom_to`` and ``rename``;
`geometry.line_crossings` returns `Crossing` tuples ``(id, t, x, y)``.
"""

from __future__ import annotations

import dataclasses
import time
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QCursor, QFont, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QGraphicsEllipseItem,
    QGraphicsLineItem,
    QGraphicsPathItem,
    QGraphicsSimpleTextItem,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QTextEdit,
)

from audian.pluginapi import theme

from . import geometry as G
from .model import EditRejected, fmt_time
from .overlay import Colours, view_of

#: Brush radius limits and default, in screen pixels (5.4).
BRUSH_MIN = 3
BRUSH_MAX = 80
BRUSH_DEFAULT = 14

#: The hover label box follows the pointer at this offset.
LABEL_OFFSET = 14

#: How long a finished stroke takes to fade.
STROKE_FADE_MS = 150

#: Above the view box's own children (its child group, the rubber band).
SURFACE_Z = 1_000_000


# ----------------------------------------------------------------- helpers


@dataclass(frozen=True)
class Mods:
    shift: bool = False
    ctrl: bool = False
    alt: bool = False

    @classmethod
    def of(cls, mods) -> "Mods":
        if isinstance(mods, Mods):
            return mods
        if mods is None:
            return cls()
        km = Qt.KeyboardModifier
        try:
            value = mods.value if hasattr(mods, "value") else int(mods)
        except TypeError:
            value = 0

        def has(flag):
            return bool(value & flag.value)

        return cls(has(km.ShiftModifier), has(km.ControlModifier), has(km.AltModifier))


def _xy(pos) -> tuple[float, float]:
    if isinstance(pos, (tuple, list)):
        return float(pos[0]), float(pos[1])
    return float(pos.x()), float(pos.y())


def plural(n: int, word: str) -> str:
    return f"{n:,} {word}{'' if n == 1 else 's'}"


def id_text(ident) -> str:
    return str(int(ident))


# -------------------------------------------------------------------- tools


class Tool:
    """One editing tool.  Subclasses override what they need."""

    key = ""
    name = ""
    cursor = "arrow"  # "brush" | "cross" | "arrow"
    colour = "select"
    brush = False

    def __init__(self, ctl: "ToolController") -> None:
        self.ctl = ctl

    @property
    def scene(self) -> G.SceneState:
        return self.ctl.scene

    @property
    def ts(self):
        return self.ctl.scene.ts

    # ---- what the next click does

    def hint(self, scene, hover) -> str:
        return ""

    def hover_changed(self) -> None:
        """The hover (or the model) changed: refresh the preview marks."""

    # ---- gestures

    @property
    def active(self) -> bool:
        return False

    def press(self, lane, pos, mods) -> None:
        pass

    def move(self, lane, pos, mods) -> None:
        pass

    def release(self, lane, pos, mods) -> None:
        pass

    def click(self, lane, pos, mods, double: bool = False) -> None:
        pass

    def cancel(self) -> bool:
        """Drop a gesture in progress.  True if there was one."""
        return False

    def clear_state(self) -> bool:
        """Drop an anchor or target.  True if there was one."""
        return False

    def deactivate(self) -> None:
        self.cancel()
        self.clear_state()


class BrushTool(Tool):
    """A tool whose drag is a brush stroke (5.4)."""

    cursor = "brush"
    brush = True
    #: which rows a stroke captures
    capture_assigned = True
    capture_unassigned = False
    sticky_allowed = True

    def __init__(self, ctl) -> None:
        super().__init__(ctl)
        self._stroke = None

    @property
    def active(self) -> bool:
        return self._stroke is not None

    def _capture_unassigned(self) -> bool:
        return self.capture_unassigned and self.ctl.unassigned_visible()

    def press(self, lane, pos, mods) -> None:
        ts = self.ts
        if ts is None:
            return
        mods = Mods.of(mods)
        hover = self.scene.hover if self.ctl.hover_lane is lane else None
        stuck = None
        if (
            self.sticky_allowed
            and self.ctl.sticky
            and not mods.alt
            and hover is not None
            and np.isfinite(hover.id)
        ):
            stuck = float(hover.id)
        self._stroke = Stroke(
            lane=lane,
            view=lane.view(),
            points=[_xy(pos)],
            mods=mods,
            stuck=stuck,
            mask=np.zeros(ts.n, dtype=bool),
            chunks=[],
            start_hover=hover,
        )
        self.scene.stroke_mode = self.key_name
        self.scene.tool_colour = ("tool", self.colour)
        self._segment(_xy(pos), _xy(pos))
        lane.stroke_started(self)
        self.started()
        self.ctl.changed()

    @property
    def key_name(self) -> str:
        return self.colour

    def move(self, lane, pos, mods) -> None:
        stroke = self._stroke
        if stroke is None:
            return
        p = _xy(pos)
        prev = stroke.points[-1]
        if p == prev:
            return
        stroke.points.append(p)
        new = self._segment(prev, p)
        stroke.lane.stroke_moved(stroke.points)
        if len(new):
            self.captured(new)
        self.ctl.changed()

    def _segment(self, p0, p1) -> np.ndarray:
        stroke = self._stroke
        ts = self.ts
        ids = None if stroke.stuck is None else [stroke.stuck]
        rows = G.brush_segment(
            ts,
            stroke.view,
            p0,
            p1,
            self.ctl.brush_px,
            ids=ids,
            include_assigned=self.capture_assigned,
            include_unassigned=self._capture_unassigned(),
            hidden_ids=self.ctl.hidden_for_tools(),
        )
        allowed = self.scene.visible_ids()
        if allowed is not None and len(rows):
            rows = rows[np.isin(ts.ident[rows], allowed) | np.isnan(ts.ident[rows])]
        rows = rows[rows < len(stroke.mask)]
        new = rows[~stroke.mask[rows]]
        if len(new):
            stroke.mask[new] = True
            stroke.chunks.append(new)
            self.scene.stroke_rows = np.concatenate(stroke.chunks)
            self.scene.touch()
        return new

    def stroke_rows(self) -> np.ndarray:
        if self._stroke is None or not self._stroke.chunks:
            return np.zeros(0, dtype=np.int64)
        return np.unique(np.concatenate(self._stroke.chunks))

    def release(self, lane, pos, mods) -> None:
        stroke = self._stroke
        if stroke is None:
            return
        self.move(lane, pos, mods)
        rows = self.stroke_rows()
        self._end_stroke(fade=True)
        try:
            self.finish(rows, stroke)
        finally:
            self.ctl.changed()

    def _end_stroke(self, fade: bool) -> None:
        stroke = self._stroke
        self._stroke = None
        self.scene.stroke_rows = np.zeros(0, dtype=np.int64)
        self.scene.stroke_mode = ""
        self.scene.tool_colour = None
        self.scene.marks = G.Marks(outline_id=self.scene.marks.outline_id)
        self.scene.touch()
        if stroke is not None:
            stroke.lane.stroke_ended(fade)

    def cancel(self) -> bool:
        if self._stroke is None:
            return False
        self._end_stroke(fade=False)
        self.scene.preview = None
        self.hover_changed()
        self.ctl.changed()
        return True

    # ---- for subclasses

    def started(self) -> None:
        pass

    def captured(self, new_rows) -> None:
        pass

    def finish(self, rows, stroke) -> None:
        pass

    def stuck_text(self) -> str:
        s = self._stroke
        if s is not None and s.stuck is not None:
            return f" · stuck to {id_text(s.stuck)} (Alt: free)"
        return ""


@dataclass
class Stroke:
    lane: object
    view: G.View
    points: list
    mods: Mods
    stuck: Optional[float]
    mask: np.ndarray
    chunks: list
    start_hover: Optional[G.Hover]


class SelectTool(BrushTool):
    key = "V"
    name = "Select"
    colour = "select"
    capture_unassigned = True

    def hint(self, scene, hover) -> str:
        if self.active:
            n = len(self.scene.stroke_rows)
            mode = {"add": "add to", "remove": "remove from"}.get(
                self._mode(), "replace"
            )
            return (
                f"release: {mode} selection ({plural(n, 'point')})" + self.stuck_text()
            )
        if hover is not None:
            return f"click: select {id_text(hover.id)} · Ctrl+click: toggle · double-click: zoom"
        return "drag: brush-select (Shift adds, Ctrl removes) · click empty: clear"

    def _mode(self) -> str:
        s = self._stroke
        if s is None:
            return "replace"
        if s.mods.ctrl:
            return "remove"
        if s.mods.shift:
            return "add"
        return "replace"

    def finish(self, rows, stroke) -> None:
        mode = "replace"
        if stroke.mods.ctrl:
            mode = "remove"
        elif stroke.mods.shift:
            mode = "add"
        current = self.scene.selection
        if mode == "replace":
            chosen = rows
        elif mode == "add":
            chosen = np.union1d(current, rows)
        else:
            chosen = np.setdiff1d(current, rows)
        self.ctl.set_selection(chosen)

    def click(self, lane, pos, mods, double=False) -> None:
        mods = Mods.of(mods)
        hover = self.scene.hover
        ts = self.ts
        if ts is None:
            return
        if hover is None:
            if not mods.ctrl:
                self.ctl.set_selection([])
            return
        rows = ts.rows_of(hover.id)
        if double:
            self.ctl.zoom_to_ids([hover.id])
            return
        if mods.ctrl:
            current = self.scene.selection
            if len(current) and np.isin(rows, current).all():
                self.ctl.set_selection(np.setdiff1d(current, rows))
            else:
                self.ctl.set_selection(np.union1d(current, rows))
        else:
            self.ctl.set_selection(rows)


class EraseTool(BrushTool):
    key = "E"
    name = "Erase"
    colour = "erase"

    def hint(self, scene, hover) -> str:
        if self.active:
            n = len(self.scene.stroke_rows)
            return f"release: unassign {plural(n, 'point')}" + self.stuck_text()
        if hover is not None:
            return "click: unassign this point · drag: erase points"
        return "drag: erase (unassign) points under the brush"

    def captured(self, new_rows) -> None:
        self.scene.marks.ring_rows = self.scene.stroke_rows
        self.scene.touch()

    def finish(self, rows, stroke) -> None:
        ts = self.ts
        rows = rows[np.isfinite(ts.ident[rows])] if len(rows) else rows
        if len(rows) == 0:
            self.ctl.say("the stroke touched no assigned points")
            return
        self.ctl.commit_plan(lambda: ts.plan_unassign(rows))

    def click(self, lane, pos, mods, double=False) -> None:
        hover = self.scene.hover
        if hover is None or hover.row < 0:
            self.ctl.say("no point here to unassign")
            return
        ts = self.ts
        self.ctl.commit_plan(lambda: ts.plan_unassign([hover.row]))


class CutTool(Tool):
    key = "C"
    name = "Cut"
    cursor = "cross"
    colour = "cut"

    def __init__(self, ctl) -> None:
        super().__init__(ctl)
        self._line = None  # (lane, view, p0, p1)
        self._crossings: list = []
        self._hover_plan = None
        self._hover_key = None

    @property
    def active(self) -> bool:
        return self._line is not None

    def _part(self) -> str:
        return "before" if self.ctl.mods.shift else "after"

    def hint(self, scene, hover) -> str:
        if self.active:
            n = len({c.id for c in self._crossings})
            return (
                f"release: cut {plural(n, 'track')}"
                if n
                else "drag across tracks to cut them"
            )
        plan = self._hover_plan
        if hover is not None and plan is not None:
            new = plan.created[0] if plan.created else self.ts.next_id
            if self._part() == "before":
                return f"click: cut here → {new} | {id_text(hover.id)} (Shift: before)"
            return (
                f"click: cut here → {id_text(hover.id)} | {new} · Shift: new id before"
            )
        if hover is not None and self.ctl.last_reject:
            return self.ctl.last_reject
        return "click a track to cut it · drag a line to cut every track it crosses"

    def hover_changed(self) -> None:
        marks = self.scene.marks
        hover = self.scene.hover
        ts = self.ts
        if self.active:
            return
        if hover is None or ts is None:
            self._hover_plan = None
            self._hover_key = None
            if marks.cut_marker is not None or marks.recolour:
                marks.cut_marker = None
                marks.recolour = []
                self.scene.preview = None
                self.scene.touch()
            self.ctl.last_reject = ""
            return
        t = G.cut_time(ts, hover.id, hover.t)
        key = (ts.revision, hover.id, t, self._part())
        if key == self._hover_key:
            return
        self._hover_key = key
        self.ctl.last_reject = ""
        if t is None:
            self._hover_plan = None
            marks.cut_marker = None
            marks.recolour = []
            self.scene.preview = None
            self.ctl.last_reject = (
                f"can't cut {id_text(hover.id)} here: it would leave one side empty"
            )
            self.scene.touch()
            return
        try:
            plan = ts.plan_cut(hover.id, t, self._part())
        except EditRejected as exc:
            self._hover_plan = None
            self.ctl.last_reject = str(exc.args[0])
            marks.cut_marker = None
            marks.recolour = []
            self.scene.preview = None
            self.scene.touch()
            return
        self._hover_plan = plan
        self.scene.preview = plan
        new = plan.created[0] if plan.created else ts.next_id
        marks.recolour = [(plan.rows, ("id", float(new)))]
        marks.cut_marker = (t, _track_freq_at(ts, hover.id, t))
        self.scene.touch()

    def click(self, lane, pos, mods, double=False) -> None:
        hover = self.scene.hover
        if hover is None:
            self.ctl.say("no track here to cut")
            return
        ts = self.ts
        t = G.cut_time(ts, hover.id, hover.t)
        if t is None:
            self.ctl.reject(
                f"can't cut {id_text(hover.id)} here: it would leave one side empty"
            )
            return
        part = "before" if Mods.of(mods).shift else "after"
        self.ctl.commit_plan(lambda: ts.plan_cut(hover.id, t, part))

    def press(self, lane, pos, mods) -> None:
        if self.ts is None:
            return
        p = _xy(pos)
        self._line = (lane, lane.view(), p, p)
        self._crossings = []
        self.scene.marks.cut_marker = None
        self.scene.marks.recolour = []
        lane.cut_line(p, p)
        self.ctl.changed()

    def move(self, lane, pos, mods) -> None:
        if self._line is None:
            return
        lane0, view, p0, _p1 = self._line
        p1 = _xy(pos)
        self._line = (lane0, view, p0, p1)
        lane0.cut_line(p0, p1)
        gap = G.gap_frames_for(self.ts.times, self.scene.gap_break_s)
        hidden = self.ctl.hidden_for_tools()
        crossings = [
            c
            for c in G.line_crossings(self.ts, view, p0, p1, gap)
            if not hidden or c.id not in hidden
        ]
        allowed = self.scene.visible_ids()
        if allowed is not None:
            crossings = [c for c in crossings if c.id in set(allowed.tolist())]
        self._crossings = crossings
        marks = self.scene.marks
        marks.crosses = [(c.x, c.y) for c in crossings]
        plan = self._compose()
        self.scene.preview = plan
        marks.recolour = []
        if plan is not None:
            # each new part in the colour of the id it will get
            for new in plan.created[: G.N_SLOTS]:
                rows = plan.rows[plan.new == new]
                if len(marks.recolour) < 8:
                    marks.recolour.append((rows, ("id", float(new))))
        self.scene.touch()
        self.ctl.changed()

    def _compose(self):
        """One plan cutting every crossed track, as one history entry."""
        ts = self.ts
        if not self._crossings or ts is None:
            return None
        try:
            return compose_cuts(ts, self._crossings)
        except EditRejected:
            return None

    def release(self, lane, pos, mods) -> None:
        if self._line is None:
            return
        self.move(lane, pos, mods)
        lane0 = self._line[0]
        crossings = self._crossings
        self._line = None
        self._crossings = []
        lane0.cut_line(None, None)
        self.scene.marks.crosses = []
        self.scene.marks.recolour = []
        self.scene.preview = None
        self.scene.touch()
        if not crossings:
            self.ctl.say("the line crossed no track")
            self.ctl.changed()
            return
        ts = self.ts
        self.ctl.commit_plan(lambda: compose_cuts(ts, crossings))

    def cancel(self) -> bool:
        if self._line is None:
            return False
        self._line[0].cut_line(None, None)
        self._line = None
        self._crossings = []
        self.scene.marks.crosses = []
        self.scene.marks.recolour = []
        self.scene.preview = None
        self._hover_key = None
        self.hover_changed()
        self.ctl.changed()
        return True


def _track_freq_at(ts, ident, t) -> float:
    rows = ts.rows_of(ident)
    if len(rows) == 0:
        return float("nan")
    return float(np.interp(t, ts.times[ts.idx[rows]], ts.fund[rows]))


def compose_cuts(ts, crossings):
    """A single plan for several cuts (a cut line across many tracks).

    `plan_cut` hands every cut the same fresh id, because each is planned at
    the same revision; here the parts are numbered consecutively from
    ``ts.next_id`` instead, so one line across five tracks is one history
    entry creating five ids.  A track crossed twice is cut twice: the part
    after the first crossing gets one id, the part after the second another.
    """
    by_id: dict = {}
    for c in crossings:
        by_id.setdefault(float(c.id), []).append(float(c.t))
    rows_all, new_all, created = [], [], []
    fresh = int(ts.next_id)
    names = []
    for ident in sorted(by_id):
        rows = ts.rows_of(ident)
        times = ts.times[ts.idx[rows]]
        for t in sorted(set(by_id[ident])):
            after = times >= t
            if not after.any() or after.all():
                continue
            # rows after this cut (and before the next cut of the same
            # track, which overrides them below) take the next fresh id
            rows_all.append(rows[after])
            new_all.append(np.full(int(after.sum()), float(fresh)))
            created.append(fresh)
            names.append(f"{id_text(ident)}→{fresh}")
            fresh += 1
    if not rows_all:
        raise EditRejected("the line crossed no track where a cut is possible")
    rows = np.concatenate(rows_all)
    new = np.concatenate(new_all)
    # later cuts of the same track override earlier ones for their rows:
    # keep the last occurrence of each row
    _u, first_rev = np.unique(rows[::-1], return_index=True)
    keep = len(rows) - 1 - first_rev
    rows, new = rows[keep], new[keep]
    first = ts.plan_cut(float(crossings[0].id), float(crossings[0].t))
    o = np.argsort(rows, kind="stable")
    rows, new = rows[o], new[o]
    t = ts.times[ts.idx[rows]]
    f = ts.fund[rows]
    label = (
        first.label
        if len(created) == 1
        else f"Cut {len(created)} tracks ({', '.join(names[:4])}{'…' if len(names) > 4 else ''})"
    )
    return dataclasses.replace(
        first,
        label=label,
        rows=rows,
        new=new,
        created=tuple(created),
        next_id=fresh,
        span=(float(t.min()), float(t.max()), float(f.min()), float(f.max())),
    )


class MergeTool(BrushTool):
    key = "M"
    name = "Merge"
    colour = "merge"
    sticky_allowed = False

    def __init__(self, ctl) -> None:
        super().__init__(ctl)
        self.anchor: Optional[float] = None
        self._hover_key = None
        self._hover_plan = None
        self._stroke_ids: list = []
        self._stroke_plan = None

    def hint(self, scene, hover) -> str:
        if self.active:
            ids = self._stroke_ids
            into = self._into()
            others = [i for i in ids if i != into]
            if not others:
                return "brush across the tracks to merge into " + (
                    id_text(into) if into is not None else "the first one touched"
                )
            text = f"release: merge {', '.join(id_text(i) for i in others[:5])} into {id_text(into)}"
            if self._stroke_plan is not None and len(self._stroke_plan.dropped):
                text += f" · {plural(len(self._stroke_plan.dropped), 'conflicting point')} will be unassigned"
            return text
        if self.anchor is None:
            if hover is not None:
                return f"click: set {id_text(hover.id)} as the anchor to merge into"
            return "click the track to merge into (the anchor), then the pieces"
        if hover is None:
            return f"anchor {id_text(self.anchor)} · click a track to merge it in · Esc clears"
        if hover.id == self.anchor:
            return f"anchor {id_text(self.anchor)}"
        plan = self._hover_plan
        if plan is None:
            return self.ctl.last_reject or f"cannot merge {id_text(hover.id)}"
        n = len(plan.dropped)
        text = f"click: merge {id_text(hover.id)} into {id_text(self.anchor)}"
        if n:
            text += f" · {plural(n, 'conflicting point')} will be unassigned"
        return text

    def _into(self):
        if self.anchor is not None:
            return self.anchor
        return self._stroke_ids[0] if self._stroke_ids else None

    def hover_changed(self) -> None:
        if self.active:
            return
        marks = self.scene.marks
        hover = self.scene.hover
        ts = self.ts
        marks.outline_id = self.anchor
        if (
            ts is None
            or self.anchor is None
            or hover is None
            or hover.id == self.anchor
            or not len(ts.rows_of(self.anchor))
        ):
            changed = bool(
                marks.recolour or marks.connector is not None or len(marks.drop_rows)
            )
            marks.recolour = []
            marks.connector = None
            marks.drop_rows = np.zeros(0, np.int64)
            self._hover_plan = None
            self._hover_key = None
            self.scene.preview = None
            if (
                ts is not None
                and self.anchor is not None
                and not len(ts.rows_of(self.anchor))
            ):
                # the anchor was merged away or erased by another edit
                self.anchor = None
                marks.outline_id = None
                changed = True
            if changed:
                self.scene.touch()
            return
        key = (ts.revision, self.anchor, hover.id)
        if key == self._hover_key:
            return
        self._hover_key = key
        self.ctl.last_reject = ""
        try:
            plan = ts.plan_merge([self.anchor, hover.id], into=self.anchor)
        except EditRejected as exc:
            self._hover_plan = None
            self.ctl.last_reject = str(exc.args[0])
            marks.recolour = []
            marks.drop_rows = np.zeros(0, np.int64)
            marks.connector = None
            self.scene.preview = None
            self.scene.touch()
            return
        self._hover_plan = plan
        self.scene.preview = plan
        marks.recolour = [(ts.rows_of(hover.id), ("id", self.anchor))]
        marks.drop_rows = np.asarray(plan.dropped, dtype=np.int64)
        ends = G.nearest_ends(ts, self.anchor, hover.id)
        marks.connector = None if ends is None else (*ends[0], *ends[1])
        self.scene.touch()

    def click(self, lane, pos, mods, double=False) -> None:
        hover = self.scene.hover
        ts = self.ts
        if hover is None:
            if self.anchor is None:
                self.ctl.say("click a track to set the anchor")
            return
        if self.anchor is None or not len(ts.rows_of(self.anchor)):
            self.anchor = float(hover.id)
            self.scene.marks.outline_id = self.anchor
            self._hover_key = None
            self.scene.touch()
            self.ctl.changed()
            return
        if hover.id == self.anchor:
            return
        anchor, other = self.anchor, float(hover.id)
        self.ctl.commit_plan(lambda: ts.plan_merge([anchor, other], into=anchor))
        self._hover_key = None

    def started(self) -> None:
        self._stroke_ids = []
        self._stroke_plan = None
        hover = self._stroke.start_hover
        if hover is not None and self.anchor is None:
            self._stroke_ids.append(float(hover.id))

    def captured(self, new_rows) -> None:
        ts = self.ts
        ids = ts.ident[new_rows]
        changed = False
        for i in ids[np.isfinite(ids)]:
            if float(i) not in self._stroke_ids:
                self._stroke_ids.append(float(i))
                changed = True
        if not changed:
            return
        into = self._into()
        marks = self.scene.marks
        ids_all = sorted(
            set(self._stroke_ids) | ({into} if into is not None else set())
        )
        others = [i for i in ids_all if i != into]
        marks.outline_id = into
        marks.recolour = []
        if others:
            rows = np.concatenate([ts.rows_of(i) for i in others])
            marks.recolour = [(rows, ("id", into))]
            try:
                self._stroke_plan = ts.plan_merge(ids_all, into=into)
                marks.drop_rows = np.asarray(self._stroke_plan.dropped, dtype=np.int64)
                self.scene.preview = self._stroke_plan
            except EditRejected:
                self._stroke_plan = None
        self.scene.touch()

    def finish(self, rows, stroke) -> None:
        ts = self.ts
        into = self._into()
        ids = sorted(set(self._stroke_ids) | ({into} if into is not None else set()))
        self._stroke_ids = []
        self._stroke_plan = None
        if into is None or len(ids) < 2:
            self.ctl.say("the stroke touched only one track: nothing to merge")
            return
        if self.anchor is None:
            self.anchor = into
        self.ctl.commit_plan(lambda: ts.plan_merge(ids, into=into))
        self.scene.marks.outline_id = self.anchor
        self._hover_key = None

    def clear_state(self) -> bool:
        if self.anchor is None:
            return False
        self.anchor = None
        self._hover_key = None
        marks = self.scene.marks
        marks.outline_id = None
        marks.recolour = []
        marks.connector = None
        marks.drop_rows = np.zeros(0, np.int64)
        self.scene.preview = None
        self.scene.touch()
        self.ctl.changed()
        return True


class AssignTool(BrushTool):
    key = "A"
    name = "Assign"
    colour = "assign"
    capture_unassigned = True

    def __init__(self, ctl) -> None:
        super().__init__(ctl)
        self.target: Optional[float] = None
        self._hover_key = None
        self._hover_plan = None
        self._stroke_plan = None

    def hint(self, scene, hover) -> str:
        n_sel = len(scene.selection)
        if self.active:
            if self.target is None:
                return "click a track first to choose the target"
            n = len(scene.stroke_rows)
            text = f"release: assign {plural(n, 'point')} to {id_text(self.target)}"
            if self._stroke_plan is not None and len(self._stroke_plan.dropped):
                text += (
                    f" · {plural(len(self._stroke_plan.dropped), 'point')} displaced"
                )
            return text + self.stuck_text()
        if n_sel and hover is not None:
            plan = self._hover_plan
            text = f"click: assign {plural(n_sel, 'selected point')} to {id_text(hover.id)}"
            if plan is not None and len(plan.dropped):
                text += f" · {plural(len(plan.dropped), 'point')} displaced"
            if plan is None and self.ctl.last_reject:
                return self.ctl.last_reject
            return text
        if n_sel:
            return f"click the track to assign {plural(n_sel, 'selected point')} to"
        if self.target is not None:
            if hover is not None and hover.id != self.target:
                return f"target {id_text(self.target)} · click: make {id_text(hover.id)} the target · drag: assign"
            return f"target {id_text(self.target)} · brush points to assign them · Esc clears"
        if hover is not None:
            return f"click: make {id_text(hover.id)} the assign target"
        return "select points first, or click a track to make it the target"

    def hover_changed(self) -> None:
        if self.active:
            return
        marks = self.scene.marks
        marks.outline_id = self.target
        hover = self.scene.hover
        ts = self.ts
        sel = self.scene.selection
        if ts is None or hover is None or not len(sel):
            changed = bool(marks.recolour or len(marks.drop_rows))
            marks.recolour = []
            marks.drop_rows = np.zeros(0, np.int64)
            self._hover_plan = None
            self._hover_key = None
            self.scene.preview = None
            if changed:
                self.scene.touch()
            return
        key = (ts.revision, hover.id, self.scene.selection_revision)
        if key == self._hover_key:
            return
        self._hover_key = key
        self.ctl.last_reject = ""
        try:
            plan = ts.plan_assign(sel, hover.id)
        except EditRejected as exc:
            self._hover_plan = None
            self.ctl.last_reject = str(exc.args[0])
            marks.recolour = []
            marks.drop_rows = np.zeros(0, np.int64)
            self.scene.preview = None
            self.scene.touch()
            return
        self._hover_plan = plan
        self.scene.preview = plan
        marks.recolour = [(sel, ("id", float(hover.id)))]
        marks.drop_rows = _displaced(ts, plan, hover.id)
        self.scene.touch()

    def click(self, lane, pos, mods, double=False) -> None:
        hover = self.scene.hover
        ts = self.ts
        sel = self.scene.selection
        if hover is None:
            self.ctl.say("click on a track")
            return
        if len(sel):
            target = float(hover.id)
            if self.ctl.commit_plan(lambda: ts.plan_assign(sel, target)):
                self.ctl.set_selection([])
            return
        self.target = float(hover.id)
        self.scene.marks.outline_id = self.target
        self.scene.touch()
        self.ctl.changed()

    def started(self) -> None:
        self._stroke_plan = None
        if self.target is None:
            self.ctl.say("click a track first to choose the assign target")

    def captured(self, new_rows) -> None:
        if self.target is None:
            return
        ts = self.ts
        rows = self.scene.stroke_rows
        rows = rows[ts.ident[rows] != self.target]
        marks = self.scene.marks
        marks.recolour = [(rows, ("id", self.target))]
        try:
            self._stroke_plan = ts.plan_assign(rows, self.target)
            self.scene.preview = self._stroke_plan
            marks.drop_rows = _displaced(ts, self._stroke_plan, self.target)
        except EditRejected:
            self._stroke_plan = None
        self.scene.touch()

    def finish(self, rows, stroke) -> None:
        if self.target is None:
            self.ctl.reject("no assign target: click a track first")
            return
        ts = self.ts
        target = self.target
        rows = rows[ts.ident[rows] != target] if len(rows) else rows
        if not len(rows):
            self.ctl.say("the stroke touched no points to assign")
            return
        self.ctl.commit_plan(lambda: ts.plan_assign(rows, target))

    def clear_state(self) -> bool:
        if self.target is None:
            return False
        self.target = None
        self.scene.marks.outline_id = None
        self.scene.touch()
        self.ctl.changed()
        return True


def _displaced(ts, plan, target) -> np.ndarray:
    """What an assign preview marks ✕: the rows it unassigns (the target's
    own points in frames the selection covers) and every conflict loser."""
    rows = np.asarray(plan.rows, dtype=np.int64)
    unassigned = rows[np.isnan(np.asarray(plan.new, dtype=np.float64))]
    return np.union1d(unassigned, np.asarray(plan.dropped, dtype=np.int64))


class AddTool(BrushTool):
    key = "F"
    name = "Add"
    colour = "add"
    sticky_allowed = False
    capture_assigned = False

    def __init__(self, ctl) -> None:
        super().__init__(ctl)
        self._frames = np.zeros(0, np.int64)
        self._freqs = np.zeros(0)
        self._target = None
        self._pending = None

    def hint(self, scene, hover) -> str:
        source = self.ctl.add_source.name
        if self._pending is not None:
            return "finding the peak frequencies…"
        if self.active:
            n = len(self._frames)
            skip = int(np.asarray(self.scene.marks.add_skip).sum())
            to = (
                f"extend {id_text(self._target)}"
                if self._target is not None
                else "a new track"
            )
            return f"release: add {plural(n - skip, 'point')} as {to} · frequency from {source}"
        if hover is not None:
            return (
                f"drag from {id_text(hover.id)} along the gap to extend it · {source}"
            )
        return f"paint along a missed track to add detections · frequency from {source}"

    def started(self) -> None:
        stroke = self._stroke
        hover = stroke.start_hover
        self._target = float(hover.id) if hover is not None else None
        self._sample()

    def move(self, lane, pos, mods) -> None:
        super().move(lane, pos, mods)
        if self._stroke is not None:
            self._sample()

    def _sample(self) -> None:
        stroke = self._stroke
        ts = self.ts
        pts = np.asarray(stroke.points, dtype=np.float64)
        t = stroke.view.x0 + pts[:, 0] / stroke.view.sx
        f = stroke.view.y1 - pts[:, 1] / stroke.view.sy
        order = np.argsort(t, kind="stable")
        t, f = t[order], f[order]
        k0, k1 = G.frame_window(ts.times, float(t[0]), float(t[-1]))
        frames = np.arange(k0, k1, dtype=np.int64)
        if len(frames) == 0:
            self._frames = frames
            self._freqs = np.zeros(0)
        else:
            tk = ts.times[frames]
            if len(t) > 1 and t[-1] > t[0]:
                centre = np.interp(tk, t, f)
            else:
                centre = np.full(len(frames), float(f[0]))
            r_hz = stroke.view.df(self.ctl.brush_px)
            self._frames = frames
            self._freqs = self.ctl.add_source.preview(frames, centre, r_hz)
        marks = self.scene.marks
        marks.add_t = ts.times[self._frames] if len(self._frames) else np.zeros(0)
        marks.add_f = self._freqs
        target = self._target
        if target is not None:
            marks.add_skip = np.isin(self._frames, ts.idx[ts.rows_of(target)])
            marks.add_colour = ("id", target)
            marks.outline_id = target
        else:
            marks.add_skip = np.zeros(len(self._frames), dtype=bool)
            marks.add_colour = ("id", float(ts.next_id))
        marks.add_pending = False
        self.scene.touch()

    def finish(self, rows, stroke) -> None:
        ts = self.ts
        frames, centre = self._frames, self._freqs
        target = self._target
        if target is None:
            hover = self.scene.hover
            if hover is not None and self.ctl.hover_lane is stroke.lane:
                target = float(hover.id)
        if len(frames) == 0:
            self.ctl.say("the stroke spans no frame")
            return
        r_hz = stroke.view.df(self.ctl.brush_px)
        source = self.ctl.add_source
        if source.immediate:
            freqs, sign, cplx = source.estimate(frames, centre, r_hz)
            self.ctl.commit_plan(
                lambda: ts.plan_add(frames, freqs, target=target, sign=sign, cplx=cplx)
            )
            return
        # asynchronous (the runner's peaks): keep the dots, pulsing, until
        # the answer arrives; the edit commits then
        marks = self.scene.marks
        marks.add_t = ts.times[frames]
        marks.add_f = centre
        marks.add_pending = True
        marks.add_colour = ("id", target if target is not None else float(ts.next_id))
        marks.add_skip = (
            np.isin(frames, ts.idx[ts.rows_of(target)])
            if target is not None
            else np.zeros(len(frames), dtype=bool)
        )
        self._pending = (frames, target, ts)
        self.scene.touch()

        def done(freqs, sign, cplx, error=""):
            pending = self._pending
            self._pending = None
            self.scene.marks = G.Marks(outline_id=self.scene.marks.outline_id)
            self.scene.touch()
            if pending is None:
                return
            if error:
                self.ctl.reject(f"peak search failed: {error}")
                return
            p_frames, p_target, session = pending
            if self.ts is not session:
                self.ctl.reject(
                    "other results were opened; the added points were dropped"
                )
                return
            self.ctl.commit_plan(
                lambda: self.ts.plan_add(
                    p_frames, freqs, target=p_target, sign=sign, cplx=cplx
                )
            )

        source.request(frames, centre, r_hz, done)

    def cancel(self) -> bool:
        if self._pending is not None:
            self._pending = None
            self.ctl.add_source.cancel()
            self.scene.marks = G.Marks()
            self.scene.touch()
            self.ctl.changed()
            return True
        return super().cancel()

    def click(self, lane, pos, mods, double=False) -> None:
        self.ctl.say("drag along a gap to add detections there")


# ---------------------------------------------------------- frequencies


class CentreSource:
    """Add's fallback: the stroke centre is the frequency (5.5, case 3)."""

    name = "stroke centre"
    immediate = True

    def preview(self, frames, centre, r_hz):
        return np.asarray(centre, dtype=np.float64)

    def estimate(self, frames, centre, r_hz):
        return np.asarray(centre, dtype=np.float64), None, None

    def request(self, frames, centre, r_hz, done) -> None:
        done(*self.estimate(frames, centre, r_hz))

    def cancel(self) -> None:
        pass


class FineSpecSource(CentreSource):
    """The session's own fine spectrogram (5.5, case 1).

    ``fine_spec.npy`` is ``[time, freq]`` power, memory-mapped; each frame's
    peak within ``centre ± r_hz`` is refined with a parabola on the dB
    values.  Frames are matched to the spectrogram's own times by nearest.
    """

    name = "fine spectrogram"
    immediate = True

    def __init__(self, spec, freqs, times, frame_times) -> None:
        self.spec = spec
        self.freqs = np.asarray(freqs, dtype=np.float64)
        self.times = np.asarray(times, dtype=np.float64)
        self.frame_times = np.asarray(frame_times, dtype=np.float64)

    def preview(self, frames, centre, r_hz):
        return self.estimate(frames, centre, r_hz)[0]

    def estimate(self, frames, centre, r_hz):
        frames = np.asarray(frames, dtype=np.int64)
        centre = np.asarray(centre, dtype=np.float64)
        out = centre.copy()
        if len(self.times) == 0 or len(self.freqs) < 3:
            return out, None, None
        t = self.frame_times[frames]
        j = np.clip(np.searchsorted(self.times, t), 0, len(self.times) - 1)
        prev = np.clip(j - 1, 0, len(self.times) - 1)
        j = np.where(np.abs(self.times[prev] - t) < np.abs(self.times[j] - t), prev, j)
        for n, (row, f) in enumerate(zip(j, centre)):
            a = int(np.searchsorted(self.freqs, f - r_hz))
            b = int(np.searchsorted(self.freqs, f + r_hz, side="right"))
            if b - a < 1:
                continue
            power = np.asarray(self.spec[int(row), a:b], dtype=np.float64)
            if not np.isfinite(power).any():
                continue
            i = int(np.nanargmax(power)) + a
            out[n] = self.freqs[i]
            if 0 < i < len(self.freqs) - 1:
                y = 10 * np.log10(
                    np.maximum(
                        np.asarray(self.spec[int(row), i - 1 : i + 2], float), 1e-30
                    )
                )
                denom = y[0] - 2 * y[1] + y[2]
                if denom < 0:
                    delta = 0.5 * (y[0] - y[2]) / denom
                    out[n] += float(np.clip(delta, -1, 1)) * (
                        self.freqs[1] - self.freqs[0]
                    )
        return out, None, None


# ------------------------------------------------------------ controller


TOOL_CLASSES = (SelectTool, EraseTool, CutTool, MergeTool, AssignTool, AddTool)


class ToolController(QObject):
    """The tools, the hover, and the actions on the selection."""

    sigCommit = Signal(object)  # Plan, applied by the panel
    sigHint = Signal(str)
    sigSelection = Signal(object)  # rows
    sigChanged = Signal()  # the scene changed: overlays redraw
    sigTool = Signal(str)  # the active tool's key

    def __init__(self, scene: G.SceneState, host=None, parent=None) -> None:
        super().__init__(parent)
        self.scene = scene
        self.host = host
        self.tools = {cls.key: cls(self) for cls in TOOL_CLASSES}
        self.tool: Tool = self.tools["V"]
        self.surfaces: list = []
        self.hover_lane = None
        self.mods = Mods()
        self.brush_px = BRUSH_DEFAULT
        self.sticky = True
        self.add_source = CentreSource()
        self.last_reject = ""
        self.selection_order: list = []
        self._pointer = None  # (lane, (x, y), mods)
        self._hint = ""
        self._said = ""
        self._said_at = 0.0
        self.last_commit = None
        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.setInterval(0)
        self._hover_timer.timeout.connect(self.flush)
        #: the last pointer position in data coordinates (Shift+X's "cursor")
        self.cursor_t: Optional[float] = None
        self.last_hover_ms = 0.0

    # ---- state

    @property
    def ts(self):
        return self.scene.ts

    def set_tool(self, key: str) -> None:
        key = key.upper()
        if key not in self.tools:
            return
        if self.tool.key == key:
            self.sigTool.emit(key)
            return
        self.tool.deactivate()
        self.scene.marks = G.Marks()
        self.scene.preview = None
        self.tool = self.tools[key]
        self.last_reject = ""
        for surface in self.surfaces:
            surface.tool_changed()
        self.tool.hover_changed()
        self.scene.touch()
        self.sigTool.emit(key)
        self.changed()

    def escape(self) -> str:
        """The cancel ladder (5.6).  Returns what it cancelled."""
        if self.tool.cancel():
            return "gesture"
        if self.tool.clear_state():
            return "anchor"
        if len(self.scene.selection):
            self.set_selection([])
            return "selection"
        if self.tool.key != "V":
            self.set_tool("V")
            return "tool"
        return ""

    def unassigned_visible(self) -> bool:
        s = self.scene
        if s.unassigned_forced is not None:
            return bool(s.unassigned_forced)
        return bool(s.show_unassigned)

    def hidden_for_tools(self):
        return self.scene.hidden_ids or None

    # ---- feedback

    def changed(self) -> None:
        """The scene changed: redraw overlays, refresh hint and label box."""
        self._emit_hint()
        lane = self.hover_lane
        if lane is not None and self._pointer is not None:
            lane.update_label(self._pointer[1], self.label_lines())
        self.sigChanged.emit()

    def hint_text(self) -> str:
        now = time.monotonic()
        if self._said and now - self._said_at < 4.0:
            return self._said
        return self.tool.hint(self.scene, self.scene.hover)

    def _emit_hint(self) -> None:
        text = self.hint_text()
        if text != self._hint:
            self._hint = text
            self.sigHint.emit(text)

    def say(self, text: str) -> None:
        """A one-off message on the hint line (not a rejection)."""
        self._said = text
        self._said_at = time.monotonic()
        self._emit_hint()

    def reject(self, reason: str) -> None:
        """A rejected edit: the hint line and an info notification."""
        self.say(reason)
        if self.host is not None and hasattr(self.host, "notify"):
            self.host.notify("info", reason)

    def label_lines(self) -> list:
        """The hover label box: the hovered track, then the tool's hint."""
        hover = self.scene.hover
        ts = self.ts
        lines = []
        if hover is not None and ts is not None:
            label = getattr(ts, "labels", {}).get(int(hover.id), "")
            n = len(ts.rows_of(hover.id))
            row = hover.row
            f = float(ts.fund[row]) if 0 <= row < ts.n else hover.f
            t = float(ts.times[ts.idx[row]]) if 0 <= row < ts.n else hover.t
            text = f"id {id_text(hover.id)}"
            if label:
                text += f' "{label}"'
            text += f" · {f:.1f} Hz · {fmt_time(t)} · {n:,} pts"
            if len(hover.candidates) > 1:
                text += f" · {hover.cycle + 1}/{len(hover.candidates)} (Tab)"
            lines.append(text)
        lines.append(self.hint_text())
        return lines

    # ---- hover

    def hover_at(self, lane, pos, mods=None) -> None:
        """Pointer moved over `lane`; the query runs once per event-loop turn."""
        self.hover_lane = lane
        self.mods = Mods.of(mods)
        self._pointer = (lane, _xy(pos), self.mods)
        lane.pointer_moved(_xy(pos))
        if not self._hover_timer.isActive():
            self._hover_timer.start()

    def leave(self, lane) -> None:
        if self.hover_lane is lane:
            self.hover_lane = None
            self._pointer = None
            lane.pointer_left()
            if self.scene.hover is not None:
                self.scene.hover = None
                self.tool.hover_changed()
                self.scene.touch()
                self.changed()

    def flush(self) -> None:
        """Run the pending hover query now."""
        self._hover_timer.stop()
        pointer = self._pointer
        if pointer is None:
            return
        start = time.perf_counter()
        lane, (x, y), _mods = pointer
        ts = self.ts
        view = lane.view()
        self.cursor_t = view.to_data(x, y)[0]
        hover = None
        if ts is not None and ts.n:
            row, ids = G.nearest(
                ts,
                view,
                x,
                y,
                G.PICK_PX,
                visible_ids=self.scene.visible_ids(),
                hidden_ids=self.hidden_for_tools(),
            )
            t, f = view.to_data(x, y)
            if row >= 0:
                old = self.scene.hover
                cycle = 0
                ident = ids[0]
                if old is not None and old.candidates == ids and old.id in ids:
                    cycle = ids.index(old.id)
                    ident = old.id
                if ident != ids[0]:
                    row = G.nearest_of_id(ts, view, x, y, ident)
                hover = G.Hover(int(row), float(ident), list(ids), cycle, t, f)
        old = self.scene.hover
        same = (
            old is not None
            and hover is not None
            and old.id == hover.id
            and old.row == hover.row
        )
        self.scene.hover = hover
        if hover is not None and old is not None:
            # the pointer time matters for the cut preview even on one track
            same = same and abs(old.t - hover.t) < 1e-12
        if not same or (hover is None) != (old is None):
            self.tool.hover_changed()
            self.scene.touch()
        self.changed()
        self.last_hover_ms = 1000 * (time.perf_counter() - start)

    def cycle_hover(self) -> bool:
        """Tab: the next of the overlapping tracks under the pointer."""
        hover = self.scene.hover
        if hover is None or len(hover.candidates) < 2 or self._pointer is None:
            return False
        lane, (x, y), _m = self._pointer
        hover.cycle = (hover.cycle + 1) % len(hover.candidates)
        hover.id = float(hover.candidates[hover.cycle])
        hover.row = G.nearest_of_id(self.ts, lane.view(), x, y, hover.id)
        self.tool.hover_changed()
        self.scene.touch()
        self.changed()
        return True

    def refresh(self) -> None:
        """The model changed: re-run the hover query and the tool preview."""
        self.scene.hover = None
        tool = self.tool
        for t in self.tools.values():
            for name in ("_hover_key", "_hover_plan"):
                if hasattr(t, name):
                    setattr(t, name, None)
        if self._pointer is not None:
            self.flush()
        else:
            tool.hover_changed()
            self.changed()

    # ---- gestures (called by the surfaces)

    def press(self, lane, pos, mods) -> None:
        self.mods = Mods.of(mods)
        self._said = ""
        self.flush()
        self._guard(self.tool.press, lane, pos, mods)

    def move(self, lane, pos, mods) -> None:
        self.mods = Mods.of(mods)
        self._pointer = (lane, _xy(pos), self.mods)
        lane.pointer_moved(_xy(pos))
        self._guard(self.tool.move, lane, pos, mods)

    def release(self, lane, pos, mods) -> None:
        self.mods = Mods.of(mods)
        self._guard(self.tool.release, lane, pos, mods)
        self._said_keep()

    def click(self, lane, pos, mods, double: bool = False) -> None:
        self.mods = Mods.of(mods)
        self._pointer = (lane, _xy(pos), self.mods)
        self._said = ""
        self.flush()
        self._guard(self.tool.click, lane, pos, mods, double)
        self._said_keep()

    def _said_keep(self) -> None:
        self.changed()

    def _guard(self, fn, *args) -> None:
        """Run a tool method; an unexpected error is reported, not raised.

        PySide6 does not abort on an exception in a slot, but it prints it
        and leaves the gesture half done; this keeps the model at its last
        consistent revision and tells the reader (design 1.2, class P).
        """
        try:
            fn(*args)
        except EditRejected as exc:
            self.reject(str(exc.args[0]))
        except Exception as exc:  # noqa: BLE001 - a slot must not take the window
            self.tool.cancel()
            if self.host is not None and hasattr(self.host, "notify"):
                self.host.notify("error", f"wavetracker: {type(exc).__name__}: {exc}")
            else:
                raise

    # ---- committing

    def commit_plan(self, make) -> bool:
        """Make a plan and hand it to the panel; False if rejected."""
        try:
            plan = make()
        except EditRejected as exc:
            self.reject(str(exc.args[0]))
            return False
        self.last_commit = plan
        self.sigCommit.emit(plan)
        return True

    # ---- selection

    def set_selection(self, rows) -> None:
        rows = np.unique(np.asarray(rows, dtype=np.int64))
        ts = self.ts
        if ts is not None and len(rows):
            rows = rows[rows < ts.n]
        self.scene.set_selection(rows)
        ids = self.scene.selected_ids().tolist()
        order = [i for i in self.selection_order if i in ids]
        order += [i for i in ids if i not in order]
        self.selection_order = order
        self.tool.hover_changed()
        self.sigSelection.emit(rows)
        self.changed()

    def select_ids(self, ids, add: bool = False) -> None:
        ts = self.ts
        if ts is None:
            return
        ids = [float(i) for i in ids]
        rows = [ts.rows_of(i) for i in ids]
        rows = np.concatenate(rows) if rows else np.zeros(0, np.int64)
        if add:
            rows = np.union1d(self.scene.selection, rows)
        before = list(self.selection_order)
        self.set_selection(rows)
        # keep the order the reader picked them in
        self.selection_order = [i for i in before if i in self.selection_order] + [
            i for i in ids if i not in before and i in self.selection_order
        ]

    # ---- actions on the selection (5.3)

    def _selection_or_reject(self) -> Optional[np.ndarray]:
        sel = self.scene.selection
        if not len(sel):
            self.reject("nothing is selected")
            return None
        return sel

    def unassign_selected(self) -> None:
        sel = self._selection_or_reject()
        if sel is None:
            return
        ts = self.ts
        if self.commit_plan(lambda: ts.plan_unassign(sel)):
            self.set_selection([])

    def delete_selected_tracks(self) -> None:
        sel = self._selection_or_reject()
        if sel is None:
            return
        ts = self.ts
        ids = self.scene.selected_ids()
        if not len(ids):
            self.reject("the selection holds no assigned points")
            return
        if self.commit_plan(lambda: ts.plan_delete_ids(ids)):
            self.set_selection([])

    def new_id_from_selection(self) -> None:
        sel = self._selection_or_reject()
        if sel is None:
            return
        ts = self.ts
        if self.commit_plan(lambda: ts.plan_new_id(sel)):
            new = ts.ids()[-1] if len(ts.ids()) else None
            if new is not None:
                self.select_ids([new])

    def merge_selected(self) -> None:
        if self._selection_or_reject() is None:
            return
        ts = self.ts
        ids = [i for i in self.selection_order if len(ts.rows_of(i))]
        if len(ids) < 2:
            self.reject("merge needs two or more selected tracks")
            return
        into = ids[0]
        if self.commit_plan(lambda: ts.plan_merge(ids, into=into)):
            self.select_ids([into])

    def swap_after_cursor(self) -> None:
        ts = self.ts
        ids = [i for i in self.selection_order if ts is not None and len(ts.rows_of(i))]
        if len(ids) != 2:
            self.reject("swap needs exactly two selected tracks")
            return
        t = self.cursor_t
        if t is None:
            self.reject("point at the crossing to swap after")
            return
        a, b = ids
        self.commit_plan(lambda: ts.plan_swap_after(a, b, t))

    def zoom_selection(self) -> None:
        ids = self.scene.selected_ids().tolist()
        if not ids and self.scene.hover is not None:
            ids = [self.scene.hover.id]
        if not ids:
            self.reject("nothing selected or hovered to zoom to")
            return
        if len(self.scene.selection):
            self.zoom_to_rows(self.scene.selection)
        else:
            self.zoom_to_ids(ids)

    def zoom_to_ids(self, ids) -> None:
        ts = self.ts
        rows = [ts.rows_of(i) for i in ids]
        rows = np.concatenate(rows) if rows else np.zeros(0, np.int64)
        self.zoom_to_rows(rows)

    def zoom_to_rows(self, rows) -> None:
        ts = self.ts
        rows = np.asarray(rows, dtype=np.int64)
        if ts is None or not len(rows):
            return
        t = ts.times[ts.idx[rows]]
        f = ts.fund[rows]
        span = (float(t.min()), float(t.max()), float(f.min()), float(f.max()))
        if self.host is not None and hasattr(self.host, "zoom_to"):
            self.host.zoom_to(span)

    def brush_scale(self, factor: float) -> None:
        r = int(round(np.clip(self.brush_px * factor, BRUSH_MIN, BRUSH_MAX)))
        if r == self.brush_px:
            r = int(
                np.clip(self.brush_px + (1 if factor > 1 else -1), BRUSH_MIN, BRUSH_MAX)
            )
        self.set_brush(r)

    def set_brush(self, r: int) -> None:
        self.brush_px = int(np.clip(r, BRUSH_MIN, BRUSH_MAX))
        for surface in self.surfaces:
            surface.brush_changed()
        self.say(f"brush {self.brush_px} px")
        if self.host is not None and hasattr(self.host, "brush_changed"):
            self.host.brush_changed(self.brush_px)

    # ---- context menu

    def context_menu(self, lane, pos, screen_pos) -> Optional[QMenu]:
        """The edit menu on a lane (5.3); returns it after `exec`."""
        self.hover_at(lane, pos)
        self.flush()
        hover = self.scene.hover
        host = self.host
        menu = QMenu()
        if hover is not None:
            ident = hover.id
            menu.addAction(
                f"Select track {id_text(ident)}", lambda: self.select_ids([ident])
            )
            menu.addAction("Zoom to track", lambda: self.zoom_to_ids([ident]))
            if host is not None and hasattr(host, "rename_track"):
                menu.addAction("Rename track…", lambda: host.rename_track(ident))
            t = G.cut_time(self.ts, ident, hover.t)
            act = menu.addAction(
                f"Cut {id_text(ident)} here",
                lambda: self.commit_plan(lambda: self.ts.plan_cut(ident, t)),
            )
            act.setEnabled(t is not None)
            menu.addSeparator()
        has_sel = bool(len(self.scene.selection))
        n_ids = len(self.scene.selected_ids())
        for text, fn, ok in (
            ("Unassign selected points\tDel", self.unassign_selected, has_sel),
            (
                "Unassign selected tracks\tShift+Del",
                self.delete_selected_tracks,
                n_ids > 0,
            ),
            ("New id from selection\tN", self.new_id_from_selection, has_sel),
            ("Merge selected tracks\tShift+M", self.merge_selected, n_ids >= 2),
            ("Swap selected after cursor\tShift+X", self.swap_after_cursor, n_ids == 2),
            ("Zoom to selection\tShift+Z", self.zoom_selection, has_sel),
        ):
            act = menu.addAction(text, fn)
            act.setEnabled(ok)
        if screen_pos is not None:
            point = (
                screen_pos.toPoint() if hasattr(screen_pos, "toPoint") else screen_pos
            )
            menu.exec(point)
        return menu


# ---------------------------------------------------------------- surface


class ToolSurface(pg.GraphicsObject):
    """The invisible item on one lane that takes the tool's mouse (5.10)."""

    def __init__(self, ax, controller: ToolController) -> None:
        super().__init__()
        self.ax = ax
        self.vb = ax.getViewBox()
        self.ctl = controller
        self.armed = False
        self._rect = QRectF(self.vb.boundingRect())
        self.setParentItem(self.vb)
        self.setZValue(SURFACE_Z)
        self.vb.sigResized.connect(self._resized)
        controller.surfaces.append(self)

        # the brush ring: a tool-coloured ring over a wider contrast ring
        self.ring_under = QGraphicsEllipseItem(self)
        self.ring = QGraphicsEllipseItem(self)
        # the stroke as it is painted
        self.stroke = QGraphicsPathItem(self)
        self.stroke.setZValue(-1)
        self._stroke_points: list = []
        self._fade = QTimer()
        self._fade.setInterval(16)
        self._fade.timeout.connect(self._fade_step)
        self._fade_t0 = 0.0
        # the cut line
        self.line_under = QGraphicsLineItem(self)
        self.line = QGraphicsLineItem(self)
        # the hover label box
        self.box = QGraphicsPathItem(self)
        self.box.setZValue(10)
        self.text1 = QGraphicsSimpleTextItem(self.box)
        self.text2 = QGraphicsSimpleTextItem(self.box)
        for item in (
            self.ring_under,
            self.ring,
            self.stroke,
            self.line_under,
            self.line,
            self.box,
        ):
            item.setVisible(False)
            item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            item.setAcceptHoverEvents(False)
        self.arm(False)

    # ---- geometry

    def boundingRect(self):  # noqa: N802 - Qt's spelling
        return self._rect

    def paint(self, painter, option, widget=None) -> None:
        pass

    def _resized(self, *args) -> None:
        self.prepareGeometryChange()
        self._rect = QRectF(self.vb.boundingRect())

    def view(self) -> G.View:
        return view_of(self.vb)

    def colours(self) -> Colours:
        return Colours(self.ax)

    def detach(self) -> None:
        self._fade.stop()
        try:
            self.vb.sigResized.disconnect(self._resized)
        except (RuntimeError, TypeError):
            pass
        if self in self.ctl.surfaces:
            self.ctl.surfaces.remove(self)
        if self.ctl.hover_lane is self:
            self.ctl.hover_lane = None
        scene = self.scene()
        if scene is not None:
            scene.removeItem(self)
        self.setParentItem(None)

    # ---- arming

    def arm(self, on: bool) -> None:
        self.armed = bool(on)
        self.setVisible(self.armed)
        self.setAcceptHoverEvents(self.armed)
        self.setAcceptedMouseButtons(
            (Qt.MouseButton.LeftButton | Qt.MouseButton.RightButton)
            if self.armed
            else Qt.MouseButton.NoButton
        )
        if not self.armed:
            self.pointer_left()
            if self.ctl.hover_lane is self:
                self.ctl.leave(self)
        else:
            self.adopt_pointer()
        self.tool_changed()

    def adopt_pointer(self) -> bool:
        """Edit mode turned on with the pointer resting on this lane: Qt
        sends no hover until it moves, and the keys (which need the pointer
        over a lane) would not work until then."""
        scene = self.scene()
        if scene is None or not self.isVisible():
            return False
        for view in scene.views():
            vp = view.viewport()
            local = vp.mapFromGlobal(QCursor.pos())
            if not vp.rect().contains(local):
                continue
            p = self.mapFromScene(view.mapToScene(local))
            if self.boundingRect().contains(p):
                self.ctl.hover_at(self, p)
                return True
        return False

    def tool_changed(self) -> None:
        tool = self.ctl.tool
        if tool.cursor == "brush":
            self.setCursor(Qt.CursorShape.BlankCursor)
        elif tool.cursor == "cross":
            self.setCursor(Qt.CursorShape.CrossCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)
        if not tool.brush:
            self.ring.setVisible(False)
            self.ring_under.setVisible(False)
        self.brush_changed()

    def brush_changed(self) -> None:
        if self._last_pointer is not None:
            self._place_ring(self._last_pointer)

    _last_pointer = None

    # ---- pyqtgraph events

    def hoverEvent(self, ev):  # noqa: N802 - pyqtgraph's spelling
        if not self.armed:
            return
        if ev.isExit():
            self.ctl.leave(self)
            return
        ev.acceptDrags(Qt.MouseButton.LeftButton)
        ev.acceptClicks(Qt.MouseButton.LeftButton)
        ev.acceptClicks(Qt.MouseButton.RightButton)
        self.ctl.hover_at(self, ev.pos(), ev.modifiers())

    def mouseDragEvent(self, ev):  # noqa: N802
        if not self.armed or ev.button() != Qt.MouseButton.LeftButton:
            ev.ignore()
            return
        ev.accept()
        mods = ev.modifiers()
        if ev.isStart():
            self.ctl.press(self, ev.buttonDownPos(), mods)
            self.ctl.move(self, ev.pos(), mods)
        elif ev.isFinish():
            self.ctl.release(self, ev.pos(), mods)
        else:
            self.ctl.move(self, ev.pos(), mods)

    def mouseClickEvent(self, ev):  # noqa: N802
        if not self.armed:
            ev.ignore()
            return
        if ev.button() == Qt.MouseButton.LeftButton:
            ev.accept()
            self.ctl.click(self, ev.pos(), ev.modifiers(), double=ev.double())
        elif ev.button() == Qt.MouseButton.RightButton:
            ev.accept()
            self.ctl.context_menu(self, ev.pos(), ev.screenPos())
        else:
            ev.ignore()

    def wheelEvent(self, ev):  # noqa: N802 - Qt's spelling
        if not self.armed:
            ev.ignore()
            return
        if self.ctl.tool.active:
            ev.accept()  # the view must not change under a stroke
            return
        if ev.modifiers() & Qt.KeyboardModifier.AltModifier:
            delta = ev.delta()
            if delta:
                self.ctl.brush_scale(1.25 if delta > 0 else 0.8)
            ev.accept()
            return
        ev.ignore()

    # ---- feedback, called by the controller and the tools

    def pointer_moved(self, p) -> None:
        self._last_pointer = p
        self._place_ring(p)

    def pointer_left(self) -> None:
        self._last_pointer = None
        self.ring.setVisible(False)
        self.ring_under.setVisible(False)
        self.box.setVisible(False)

    def _place_ring(self, p) -> None:
        if not self.ctl.tool.brush or not self.armed:
            self.ring.setVisible(False)
            self.ring_under.setVisible(False)
            return
        r = float(self.ctl.brush_px)
        rect = QRectF(p[0] - r, p[1] - r, 2 * r, 2 * r)
        c = self.colours()
        tool = c.tool(self.ctl.tool.colour)
        under = QPen(theme.qcolor(c.contrast, alpha=0.85), 3.0)
        under.setCosmetic(True)
        over = QPen(theme.qcolor(tool), 1.5)
        over.setCosmetic(True)
        self.ring_under.setPen(under)
        self.ring.setPen(over)
        self.ring_under.setRect(rect)
        self.ring.setRect(rect)
        self.ring_under.setVisible(True)
        self.ring.setVisible(True)

    def stroke_started(self, tool) -> None:
        self._fade.stop()
        self.stroke.setOpacity(1.0)
        c = self.colours()
        colour = theme.qcolor(c.tool(tool.colour), alpha=0.30)
        pen = QPen(colour, 2.0 * self.ctl.brush_px)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        pen.setCosmetic(True)
        self.stroke.setPen(pen)
        self._stroke_points = []
        self.stroke.setPath(QPainterPath())
        self.stroke.setVisible(True)

    def stroke_moved(self, points) -> None:
        path = QPainterPath()
        if points:
            path.moveTo(QPointF(*points[0]))
            for p in points[1:]:
                path.lineTo(QPointF(*p))
            if len(points) == 1:
                path.lineTo(QPointF(points[0][0] + 0.01, points[0][1]))
        self._stroke_points = list(points)
        self.stroke.setPath(path)

    def stroke_ended(self, fade: bool) -> None:
        if not fade:
            self.stroke.setVisible(False)
            self.stroke.setPath(QPainterPath())
            return
        self._fade_t0 = time.perf_counter()
        self._fade.start()

    def _fade_step(self) -> None:
        elapsed = 1000 * (time.perf_counter() - self._fade_t0)
        if elapsed >= STROKE_FADE_MS:
            self._fade.stop()
            self.stroke.setVisible(False)
            self.stroke.setPath(QPainterPath())
            return
        self.stroke.setOpacity(1.0 - elapsed / STROKE_FADE_MS)

    def cut_line(self, p0, p1) -> None:
        if p0 is None:
            self.line.setVisible(False)
            self.line_under.setVisible(False)
            return
        c = self.colours()
        under = QPen(theme.qcolor(c.contrast, alpha=0.85), 4.0)
        under.setCosmetic(True)
        over = QPen(theme.qcolor(c.tool("cut")), 2.0)
        over.setCosmetic(True)
        over.setStyle(Qt.PenStyle.DashLine)
        for item, pen in ((self.line_under, under), (self.line, over)):
            item.setPen(pen)
            item.setLine(p0[0], p0[1], p1[0], p1[1])
            item.setVisible(True)

    def update_label(self, p, lines) -> None:
        """The hover label box, at a 14 px offset, kept inside the lane."""
        if not self.armed or p is None or not lines:
            self.box.setVisible(False)
            return
        font = theme.font_mono(theme.SIZE_SMALL_PT)
        bold = QFont(font)
        bold.setBold(True)
        fg = theme.qcolor(theme.token("fg"))
        muted = theme.qcolor(theme.token("fg.muted"))
        two = len(lines) > 1 and bool(lines[1])
        if len(lines) == 1:
            self.text1.setText(lines[0])
            self.text1.setFont(font)
            self.text1.setBrush(muted)
            self.text2.setVisible(False)
        else:
            self.text1.setText(lines[0])
            self.text1.setFont(bold)
            self.text1.setBrush(fg)
            self.text2.setText(lines[1])
            self.text2.setFont(font)
            self.text2.setBrush(muted)
            self.text2.setVisible(bool(lines[1]))
        pad = 5.0
        r1 = self.text1.boundingRect()
        # not `isVisible()`: the box itself may still be hidden
        r2 = self.text2.boundingRect() if two else QRectF()
        w = max(r1.width(), r2.width()) + 2 * pad
        h = r1.height() + (r2.height() + 1 if two else 0) + 2 * pad
        self.text1.setPos(pad, pad)
        self.text2.setPos(pad, pad + r1.height() + 1)
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, w, h), 4, 4)
        self.box.setPath(path)
        self.box.setBrush(theme.qcolor(theme.token("bg.raised"), alpha=0.93))
        border = QPen(theme.qcolor(theme.token("border.hi")), 1.0)
        border.setCosmetic(True)
        self.box.setPen(border)
        x = p[0] + LABEL_OFFSET
        y = p[1] + LABEL_OFFSET
        rect = self._rect
        if x + w > rect.right() - 2:
            x = p[0] - LABEL_OFFSET - w
        if y + h > rect.bottom() - 2:
            y = p[1] - LABEL_OFFSET - h
        self.box.setPos(max(rect.left() + 2, x), max(rect.top() + 2, y))
        self.box.setVisible(True)

    def label_text(self) -> list:
        lines = [self.text1.text()]
        if self.text2.isVisibleTo(self.box):
            lines.append(self.text2.text())
        return lines


# -------------------------------------------------------------- keys


K = Qt.Key
M = Qt.KeyboardModifier
NONE = 0
SHIFT = M.ShiftModifier.value
CTRL = M.ControlModifier.value
ALT = M.AltModifier.value
_MOD_MASK = SHIFT | CTRL | ALT | M.MetaModifier.value

#: (key, modifiers) -> (command name, needs edit mode, needs a lane under
#: the pointer, auto-repeat allowed).  Command names are methods of the
#: panel (`WavetrackerPanel.key_*`).
KEY_TABLE = {
    (K.Key_E.value, CTRL | SHIFT): ("toggle_edit", False, False, False),
    (K.Key_V.value, NONE): ("tool_V", True, False, False),
    (K.Key_E.value, NONE): ("tool_E", True, False, False),
    (K.Key_C.value, NONE): ("tool_C", True, False, False),
    (K.Key_M.value, NONE): ("tool_M", True, False, False),
    (K.Key_A.value, NONE): ("tool_A", True, False, False),
    (K.Key_F.value, NONE): ("tool_F", True, False, False),
    (K.Key_Z.value, CTRL): ("undo", True, False, True),
    (K.Key_Z.value, CTRL | SHIFT): ("redo", True, False, True),
    (K.Key_Y.value, CTRL): ("redo", True, False, True),
    (K.Key_S.value, CTRL): ("save", True, False, False),
    (K.Key_Delete.value, NONE): ("unassign_selected", True, False, False),
    (K.Key_Delete.value, SHIFT): ("delete_selected_tracks", True, False, False),
    (K.Key_N.value, NONE): ("new_id", True, False, False),
    (K.Key_M.value, SHIFT): ("merge_selected", True, False, False),
    (K.Key_X.value, SHIFT): ("swap_after", True, False, False),
    (K.Key_U.value, NONE): ("toggle_unassigned", True, False, False),
    (K.Key_I.value, NONE): ("toggle_isolate", True, False, False),
    (K.Key_Z.value, SHIFT): ("zoom_selection", True, False, False),
    (K.Key_G.value, NONE): ("next_issue", True, False, True),
    (K.Key_G.value, SHIFT): ("previous_issue", True, False, True),
    (K.Key_Return.value, NONE): ("accept_issue", True, False, False),
    (K.Key_Enter.value, NONE): ("accept_issue", True, False, False),
    (K.Key_Enter.value, M.KeypadModifier.value): ("accept_issue", True, False, False),
    (K.Key_BracketLeft.value, NONE): ("brush_smaller", True, False, True),
    (K.Key_BracketRight.value, NONE): ("brush_larger", True, False, True),
    (K.Key_Tab.value, NONE): ("cycle", True, True, True),
    (K.Key_Escape.value, NONE): ("escape", True, False, False),
}

#: For the panel's Keys disclosure: (keys, what, audian's binding shadowed).
KEY_HELP = (
    ("Ctrl+Shift+E", "edit mode on/off", "free"),
    (
        "V E C M A F",
        "Select, Erase, Cut, Merge, Assign, Add",
        "Fit Y, Envelope, Center, —, Analyze, —",
    ),
    ("Ctrl+Z", "undo", "Pan zoom"),
    ("Ctrl+Shift+Z, Ctrl+Y", "redo", "free"),
    ("Ctrl+S", "save track edits", "Save window as"),
    (
        "Del / Shift+Del",
        "unassign selected points / tracks",
        "Hide deselected channels",
    ),
    ("N", "new id from selection", "Next fixed label"),
    ("Shift+M", "merge selected tracks", "free"),
    ("Shift+X", "swap two selected tracks after the cursor", "free"),
    ("U", "show/hide unassigned points", "Decrease spatial threshold"),
    ("I", "show only selected tracks", "free"),
    ("Shift+Z", "zoom to selection", "free"),
    ("G / Shift+G", "next / previous issue", "Toggle grid"),
    ("Enter", "accept the issue's suggestion", "free"),
    ("[ / ]", "brush smaller / larger (or Alt+wheel)", "free"),
    ("Tab", "next of overlapping tracks (over a lane)", "focus traversal"),
    ("Esc", "cancel gesture → anchor → selection → Select", "free"),
)

TEXT_INPUTS = (QLineEdit, QAbstractSpinBox, QPlainTextEdit, QTextEdit)


def key_of(ev) -> tuple:
    mods = ev.modifiers()
    value = mods.value if hasattr(mods, "value") else int(mods)
    key = ev.key()
    key = key.value if hasattr(key, "value") else int(key)
    keypad = value & M.KeypadModifier.value
    value &= _MOD_MASK
    if key == K.Key_Backtab.value:
        key = K.Key_Tab.value
    if keypad and key == K.Key_Enter.value:
        return (key, M.KeypadModifier.value)
    return (key, value)


class KeyRouter(QObject):
    """The plugin's keys, claimed only where and when they apply (5.9)."""

    def __init__(self, panel) -> None:
        super().__init__(panel)
        self.panel = panel
        self._claimed = None
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        #: what the router ran, newest last (for tests and the Keys list)
        self.ran: list = []

    def uninstall(self) -> None:
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)

    def command_for(self, obj, ev) -> Optional[str]:
        entry = KEY_TABLE.get(key_of(ev))
        if entry is None:
            return None
        name, needs_edit, needs_lane, _repeat = entry
        panel = self.panel
        try:
            if not panel.router_live():
                return None
        except RuntimeError:
            return None
        if not self._window_ok(obj):
            return None
        if (
            QApplication.activeModalWidget() is not None
            or QApplication.activePopupWidget() is not None
        ):
            return None
        focus = QApplication.focusWidget()
        for widget in (focus, obj):
            if isinstance(widget, TEXT_INPUTS) or (
                isinstance(widget, QComboBox) and widget.isEditable()
            ):
                return None
        if name == "toggle_edit":
            return name
        if needs_edit and not panel.edit_mode:
            return None
        over_lane = panel.controller.hover_lane is not None
        in_panel = focus is not None and (focus is panel or panel.isAncestorOf(focus))
        if needs_lane and not over_lane:
            return None
        if not (over_lane or in_panel):
            return None
        if not panel.key_applies(name):
            return None
        return name

    def _window_ok(self, obj) -> bool:
        window = self.panel.window()
        if window is None:
            return False
        try:
            if hasattr(obj, "window") and callable(obj.window):
                w = obj.window()
                if w is window:
                    return True
            handle = window.windowHandle()
            if handle is not None and obj is handle:
                return True
        except RuntimeError:
            return False
        return False

    def eventFilter(self, obj, ev):  # noqa: N802 - Qt's spelling
        kind = ev.type()
        if kind == QEvent.Type.ShortcutOverride:
            name = self.command_for(obj, ev)
            if name is None:
                return False
            ev.accept()
            self._claimed = key_of(ev)
            return True
        if kind == QEvent.Type.KeyPress:
            name = self.command_for(obj, ev)
            if name is None:
                if self._claimed == key_of(ev):
                    self._claimed = None
                return False
            entry = KEY_TABLE[key_of(ev)]
            self._claimed = None
            if ev.isAutoRepeat() and not entry[3]:
                return True
            self.ran.append(name)
            self.panel.run_key(name)
            return True
        return False


__all__ = [
    "BRUSH_DEFAULT",
    "KEY_HELP",
    "KEY_TABLE",
    "AddTool",
    "AssignTool",
    "CentreSource",
    "CutTool",
    "EraseTool",
    "FineSpecSource",
    "KeyRouter",
    "MergeTool",
    "Mods",
    "SelectTool",
    "Tool",
    "ToolController",
    "ToolSurface",
    "compose_cuts",
]
