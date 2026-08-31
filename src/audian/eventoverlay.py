"""Drawing a `SessionBundle` over the traces and the spectrograms.

Two objects, with a deliberate split:

`AnnotationLayer`
    One per browser.  Owns the loaded bundle, which of its ten layers are
    switched on, and -- the part that makes this scale -- the *shared* window
    cache.  A sixteen channel file has 32 plots showing the same time range,
    so the windowing, the decimation and the span merge are done once and
    every plot reads the same arrays back.

`EventOverlay`
    One per plot.  Turns those arrays into geometry.  It holds one
    `pg.PlotCurveItem` per drawn thing -- one per point series, two per span
    layer -- so a redraw never walks a row from Python, plus a fixed pool of
    `pg.TextItem` for the treatment letters that is never grown, never shrunk,
    and never touched by `addItem`/`removeItem` after it is built.

The one rule everything here obeys
----------------------------------
**Every annotation occupies the full height of the lane it is drawn in, and
is bounded only in x.**  There is no y-allocation per layer, no tracks, no
sub-rows.  Layers overlap, and that is expected: the reader runs one or two
at a time, so *toggling* is the interaction this is built around, not
simultaneous legibility of all ten.

What the drawing says
---------------------
* **Colour is the top-level KIND, and only that.**  Three hues in the default
  view: one for *a trial happened here*, one for *a pulse was played here*,
  and the page's own ink for *the log cannot account for this*.  Resolved per
  series through :func:`audian.theme.annotation_color`; a series' own role
  wins over its layer's, and a layer's role is only the fallback for a series
  that has none.  Explained detections draw in the PULSE hue, because an
  explained detection is a played pulse heard back and not a third thing.
* **Treatment is a letter, not a hue.**  `V` / `B` / `S` knocked out of a chip
  at the span's start edge, from a fixed pool of `pg.TextItem` that is built
  once and driven with `setText` / `setPos`.  Always present, subordinate,
  behind no mode switch.  Every per-treatment and per-type layer still has its
  own toggle: the letters answer *which treatment is this*, and soloing
  answers *show me only that treatment*, which are different questions.
* **A span** is an interior fill drawn *under* the trace plus two full-height
  edge lines drawn *over* it.  The fill shifts the ground, so the waveform
  keeps its own pen; the edges carry the extent when the fill is washed out
  by daylight -- and on the spectrogram, where an opaque image leaves no
  ground to shift, the edges carry it alone.  See :data:`SPAN_FILL_ALPHA` for
  the measurement and :data:`SURFACE_STYLE` for what each surface does with
  it.
* **A point** is a full-height vertical at full opacity, over the trace.
* **Predicted is not observed.**  A predicted point -- the fit says it is
  there, the recording never confirmed it -- is drawn at the same full height
  in the same hue, but dashed and with a hollow diamond cap.  Colour is never
  the difference: a predicted volley pulse in another hue would read as
  another stimulus.
* **Dashing everything** is what an *unvalidated* fit buys, and hatching every
  fill with it.  If the fit was never checked, every position on screen is a
  guess, and the drawing says so on its own rather than relying on a badge the
  reader may not look at.  The badge is there too (`AnnotationLayer.badge`).
* **A bundle fitted against another recording draws nothing at all.**  Every
  mark would land somewhere plausible and wrong, and a plausible wrong mark is
  worse than no mark.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import NamedTuple, Optional

import numpy as np
import pyqtgraph as pg

from PySide6.QtCore import QObject, QRect, Qt, Signal
from PySide6.QtGui import QIcon, QPainter, QPixmap


from . import theme, windowing
from .layers import (
    LAYER_RUNS,
    Layer,
    PointLayer,
    SpanLayer,
    StepTrack,
)
from .session import (
    TRUST_OK,
    TRUST_UNVALIDATED,
    TRUST_WARN,
    SessionBundle,
    find_bundle,
)


log = logging.getLogger(__name__)


#: The three places an annotation can be drawn.  They are separate switches
#: because they answer different questions: the trace and the spectrogram say
#: *what the signal was doing at this event*, the navigator says *where in the
#: session the events are*.  Wanting one is no reason to want the others.
SURFACE_TRACE = "trace"
SURFACE_SPECTROGRAM = "spectrogram"
SURFACE_NAVIGATOR = "navigator"

#: Order the surfaces are listed in, and the label each one gets.  The strip
#: at the bottom of the window is the *navigator* everywhere else in audian
#: -- the Panels menu, F6 -- and a chip that called the same thing something
#: else would be a second name for one object.  What the marks make of it is
#: a timeline of the session, and that belongs in the tool tip, not the label.
SURFACE_ORDER: tuple[str, ...] = (
    SURFACE_TRACE,
    SURFACE_SPECTROGRAM,
    SURFACE_NAVIGATOR,
)
SURFACE_LABELS: dict[str, str] = {
    SURFACE_TRACE: "Traces",
    SURFACE_SPECTROGRAM: "Spectrograms",
    SURFACE_NAVIGATOR: "Navigator",
}

#: Every mark -- point line, span edge -- is drawn at full opacity, in both
#: themes.  Opacity is not available as a channel here: under direct sun a
#: daylight mark at any alpha below 1.0 collapses to 1.5:1 against the plot
#: ground, so anything said with it is said only indoors.  What carries
#: meaning instead is the dash pattern (`theme.annotation_pen`) and the hue.
MARK_ALPHA = 1.0

#: The roles that actually paint an interior fill: the trials -- all three
#: treatments, one role -- and the localization runs.  Every other role in
#: `theme.ANNOTATION_ROLES` -- `pulse`, `detection.novel`, `session`, `fault`,
#: `control` -- belongs to a point layer or to the control track, which draw a
#: line or a staircase and never wash a ground.  Auditing a fill over those
#: measures a composite that is never rendered, so the measurement on
#: `SPAN_FILL_ALPHA` is taken over this set alone; a test pins it to the span
#: layers a bundle actually builds, so a new one cannot quietly fall outside it.
FILL_ROLES: frozenset[str] = frozenset({"trial", "run"})

#: Interior fill of a span, per theme.  This is the one place annotation ink
#: is laid across the waveform, so it is the one number that had to be
#: measured rather than chosen.
#:
#: Measured with `theme.contrast_ratio`, worst case over :data:`FILL_ROLES` x
#: every colour in `theme.painted_trace_colors`, the fill composited UNDER
#: the trace so only the ground shifts, and onto the ground each lane is
#: really painted: `databrowser.update_current_plot` paints the focused lane
#: `bg.lane` and leaves every other lane `bg.plot`, so both are audited.
#:
#:     ground                    a=0.00  a=0.05  a=0.10  a=0.14   floor
#:     dark  other   (bg.plot)     3.09    2.96    2.82    2.70     3.0
#:     dark  focused (bg.lane)     2.81    2.67    2.53    2.41     3.0
#:     light other   (#FFFFFF)     4.62    4.21    3.85    3.54     4.5
#:     light focused (bg.lane)     3.90    3.57    3.24    3.02     4.5
#:
#: Re-measured when the three treatment hues collapsed into one `trial` hue:
#: the fill set went from four roles to two, so the worst composite is a
#: different one and it is cheaper.  The old table read 2.69 / 2.41 / 3.81 /
#: 3.21 at the committed alphas, against 2.82 / 2.53 / 4.21 / 3.57 now.
#:
#: Read the `a=0.00` column first.  With no annotation on screen at all the
#: focused lane is already under its floor, in both themes: `theme.dim_color`
#: clamps a receded trace against `bg.plot`, while the lane that trace lands
#: in is painted `bg.lane`.  That is a pre-existing defect, it lives in
#: `theme`/`databrowser` and not here, and no fill alpha can repair it -- so
#: this constant does not claim to hold any lane above its floor, and nothing
#: in `tests/test_eventoverlay.py` asserts that it does.
#:
#: What 0.10/0.05 does claim, and what
#: `test_a_span_fill_costs_at_most_half_a_ratio_point_on_either_ground`
#: asserts, is that the fill is cheap: it costs the worst painted trace 0.28
#: of a contrast ratio in dark and 0.41 in daylight, on either ground.  It
#: can afford to be that weak because a span's extent is carried by its two
#: edge lines, drawn at `MARK_ALPHA` whatever the fill does.
SPAN_FILL_ALPHA: dict[str, float] = {
    theme.THEME_DARK: 0.10,
    theme.THEME_LIGHT: 0.05,
}

#: Layers whose interior fill is cut to `LOW_FILL_SCALE`.  A legibility
#: calibration for one layer, not a category encoding: the localization runs
#: reach 58 s each and cover 59% of the exp2 session, so at the trials' alpha
#: the whole overview greys over and the fill stops meaning anything.  The
#: edges are untouched -- a run's extent still reads exactly like a trial's.
LOW_FILL_LAYERS = frozenset({LAYER_RUNS})
LOW_FILL_SCALE = 0.5

#: A fixed pool of `pg.TextItem`, per overlay, for the treatment letters.
#:
#: Treatment is the THIRD-TIER refinement -- kind first (a trial happened
#: here), pulses second, which treatment only third -- so it is carried by a
#: letter at a span's start edge and never by hue.  The pool exists because
#: `pg.TextItem` is ruinous to build and to detach: measured on this machine,
#: **265 us per construction and 47 us per `removeItem`**, and superlinearly
#: worse as the scene fills.  Twelve labels built and dropped per pan would
#: cost more than the entire rest of the redraw, so nothing is ever
#: constructed or removed on the draw path: the pool is built once and driven
#: with `setText` / `setPos`, and a slot nobody needs is parked with
#: `setVisible(False)`.
#:
#: 24 is 1.5x the measured worst case.  Sweeping every view width from 0.5 s
#: to the whole session across both fixtures, at a 3840 px lane and
#: `MIN_LABEL_PX`, the most trial spans ever wide enough to label at once is
#: **16 (exp2, a 100 s view at 331 s) and 12 (exp3, a 100 s view at its
#: start)**.  Wider views hold more trials but each one narrower than the
#: glyph; narrower views hold fewer.
LABEL_POOL = 24

#: A span narrower than this many device pixels is left unlabelled: the chip
#: hangs from the span's start edge, so on a bar narrower than itself it would
#: reach over the gap and into the NEXT span, saying that one was a volley
#: when it was a silence.
#:
#: The chip measures 9.8 x 20 px with `theme.font_mono(SIZE_SMALL_PT,
#: bold=True)` at a 1 px document margin, measured offscreen on this machine.
#: 14 leaves four pixels of bar visible past the glyph, which is what keeps
#: the two edges legible as edges rather than as the sides of the chip.
MIN_LABEL_PX = 14

#: Above `LABEL_POOL` labelable spans the pool labels NOTHING.  Filling 24
#: slots out of 30 candidates would leave six spans bare with nothing to say
#: which six, and a reader who saw `V V V` would read the unlabelled ones as
#: the same treatment.  No labels at all is a state the reader can see; an
#: arbitrary subset is one they cannot.
#:
#: Above this many drawn points the diamond caps on predicted marks are
#: dropped: at that density they merge into a bar and say nothing the dashed
#: line does not already say.
CAP_LIMIT = 400

#: How far below the top of the view box a diamond cap is centred, in logical
#: pixels, on top of half the symbol's own height.  A cap centred exactly on
#: `y1` is clipped by the view box and renders as a chevron: measured on the
#: predicted mark at 138.432 s, nothing was painted above the first interior
#: row and only the lower four rows of the symbol survived.  Half of
#: `theme.S8` clears the symbol, this clears the pen that strokes it.
CAP_INSET_PX = 1.0

#: z of a trace, a spectrogram image or a navigator envelope.  pyqtgraph gives
#: every data item 0 and audian never sets one (`timeplot.py` sets z only on
#: its caption, its zero line and its playback marker), so 0 is the number the
#: two annotation z's are placed either side of.
TRACE_Z = 0

#: The span interior goes BELOW the data.  Drawn over it, the same fill costs
#: the trace 3 dB of contrast and the measurement in `SPAN_FILL_ALPHA` does
#: not hold; drawn under it, only the ground shifts and the waveform keeps its
#: own pen.  Below the zero line (-10) as well, so the y reference survives a
#: span too.
FILL_Z = -20

#: Marks -- point lines and span edges -- go above the traces and below the
#: crosshair and the playback cursor: an annotation must not hide where the
#: sound is playing.  Caps ride one above their own mark.
MARK_Z = 15
CAP_Z = 16

#: z of the navigator's selection region, of which `fulltraceplot.NAV_REGION_Z`
#: is the definition.  Written down rather than imported, because
#: `fulltraceplot` pulls the whole browser in and this is one integer; a test
#: asserts the two agree.
NAV_REGION_Z = 50

#: What a mark costs on the navigator.  The selection region above is a
#: translucent `pg.LinearRegionItem`, so at `MARK_Z` every mark inside the
#: window the reader is actually looking at was painted through it -- a
#: silence edge sampled (215,121,255) where its own colour is (245,117,255).
#: `MARK_ALPHA` says a mark is drawn at full opacity; on this surface that
#: costs a z above the region.  Still under the crosshair and the playback
#: marker, which are at 100.
#:
#: And under the navigator's own overview, which sits at
#: `fulltraceplot.NAV_TRACE_Z`.  Full-height marks over a waveform made a
#: densely annotated stretch a picket fence with nothing visible behind it;
#: the waveform was raised rather than these lowered, because lowering them
#: puts them back under the region and undoes the measurement above.
NAV_MARK_Z = NAV_REGION_Z + 10

#: How each surface draws.  Geometry is the full lane on all three -- that is
#: the rule -- so what varies is the fill and the z a mark needs to reach full
#: opacity against that surface's own furniture.
#:
#: `fill` scales `SPAN_FILL_ALPHA`:
#:
#: * The trace lane is 1.0.  That is the ground the alpha was measured on.
#: * The navigator is 1.0 too.  It was doubled, on the claim that its ground
#:   is not `bg.plot`; it is exactly `bg.plot` -- read off the running app,
#:   the navigator view box brush is `#0D1219`, the same token an unfocused
#:   trace lane gets -- so the measured alpha applies to it unchanged, and the
#:   doubling was an un-measured 1.36:1 against a 3.0 floor.
#: * The spectrogram is 0.0: it draws no interior fill at all.  `SpecItem` is
#:   a `pg.ImageItem` at pyqtgraph's default z=0 with a fully opaque LUT
#:   covering the lane, so a fill at `FILL_Z` is painted straight over and
#:   never composited -- measured in the app, an interior column inside a
#:   soloed span changed 0 pixels across the spectrogram's 121 rows while the
#:   trace lane below it changed 96.  Lifting the fill above the image
#:   instead would tint the spectrogram's own values, and its colormap spans
#:   the whole luminance range, so no alpha over it can be measured safe.
#:   The edges are above the image already and carry the extent there, which
#:   is the same thing they do outdoors on the trace.
#: `labels` is the treatment letter.  Off on the navigator for the same reason
#: `caps` is: that strip always shows the whole session, so at 3621 s across
#: 3840 px a 1 s trial is one pixel wide and no span could ever clear
#: `MIN_LABEL_PX` there -- the pool would be 24 items per navigator row that
#: are built, never used, and repainted with the rest of the scene.
SURFACE_STYLE: dict[str, dict] = {
    SURFACE_TRACE: {"fill": 1.0, "caps": True, "labels": True, "mark_z": MARK_Z},
    SURFACE_SPECTROGRAM: {
        "fill": 0.0,
        "caps": True,
        "labels": True,
        "mark_z": MARK_Z,
    },
    # a navigator row is about 60 px and a hollow diamond in it is a smudge
    # rather than a symbol
    SURFACE_NAVIGATOR: {
        "fill": 1.0,
        "caps": False,
        "labels": False,
        "mark_z": NAV_MARK_Z,
    },
}

#: Fallback width in device pixels when a view box has not been laid out yet.
DEFAULT_PIXELS = 1200

#: Below this the view box has not been laid out and its width is not a pixel
#: budget.  Cutting the decimation to a two pixel view would collapse a whole
#: window of events onto two lines and leave them there, because nothing
#: redraws until the next range change.
MIN_PIXELS = 16


class LayerState(NamedTuple):
    """What a chip needs to draw itself and say what it toggles."""

    id: str
    label: str
    short: str
    micro: str
    kind: str
    count: int
    color: str
    enabled: bool
    tip: str


class AnnotationLayer(QObject):
    """The annotation state of one browser: bundle, toggles, window cache."""

    #: the set of layers changed and every overlay must rebuild its items
    sigTableChanged = Signal()
    #: only visibility changed; overlays redraw but keep their items
    sigVisibilityChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.bundle: Optional[SessionBundle] = None
        self.visible = True
        #: one switch per layer, keyed by layer id.  Ten of them, and the
        #: reader runs one or two at a time, so this is the control that
        #: matters -- not a cross product of facets nobody thinks in.
        self.layers: dict[str, bool] = {}
        #: which surfaces draw at all.  A second axis alongside the layers:
        #: *where* to show them, not *which* to show.
        self.surfaces: dict[str, bool] = dict.fromkeys(SURFACE_ORDER, True)
        #: set when the bundle was fitted against a different recording
        self.recording_mismatch: Optional[str] = None
        #: bumped whenever anything an overlay draws from changes.  An
        #: overlay compares it against what it last drew and skips the whole
        #: redraw when neither the view nor this has moved -- which is most
        #: calls, because a pan delivers sigRangeChanged to every plot *and*
        #: goes through Panels.update_plots().
        self.revision = 0
        # shared window cache: (t0, t1, pixels) -> {draw key: arrays}
        self._cache_key: Optional[tuple] = None
        self._cache: dict[tuple, tuple] = {}

    # --- loading ---------------------------------------------------------

    def load(self, path, recording=None) -> SessionBundle:
        """Read the bundle at `path` and make it the current one.

        `path` is a ``*_metadata.toml``, a bundle directory, or a `BundleRef`.
        `recording` is the file the browser has open.  When the fit names a
        different one the bundle is still loaded -- its warnings and its
        summary are worth reading -- but `recording_mismatch` is set and
        nothing is drawn until it is cleared.  A fit belongs to exactly one
        recording; used against another, every mark lands somewhere plausible
        and wrong.
        """
        bundle = SessionBundle.load(path, recording=recording)
        self.bundle = bundle
        self.layers = {layer.id: bool(layer.default_on) for layer in bundle}
        self.visible = True
        self.recording_mismatch = None
        if bundle.recording_check.name is False:
            named = bundle.meta.alignment.recording_file
            self.recording_mismatch = Path(named).name if named else "another recording"
        self.invalidate()
        self.sigTableChanged.emit()
        return bundle

    def discover(self, recording) -> Optional[Path]:
        """Find a bundle that names `recording`, without loading it."""
        try:
            ref = find_bundle(recording)
        except OSError:
            return None
        return ref.metadata_path if ref is not None else None

    def clear(self) -> None:
        self.bundle = None
        self.layers = {}
        self.recording_mismatch = None
        self.invalidate()
        self.sigTableChanged.emit()

    # --- state -----------------------------------------------------------

    @property
    def loaded(self) -> bool:
        return self.bundle is not None

    @property
    def trust(self) -> str:
        return self.bundle.trust if self.bundle is not None else TRUST_OK

    @property
    def unvalidated(self) -> bool:
        return self.trust == TRUST_UNVALIDATED

    @property
    def drawable(self) -> bool:
        """Whether anything may be drawn anywhere.

        The wrong recording is not a detail for a tool tip.  Every position in
        the bundle comes from a fit against another file, so every mark would
        be misplaced by an amount nobody can see -- which looks exactly like
        data.  The badge says so and the lanes stay clean.
        """
        return self.visible and self.recording_mismatch is None

    def surface_enabled(self, surface: str) -> bool:
        """Whether annotations are drawn on `surface` at all."""
        return self.drawable and self.surfaces.get(surface, True)

    def set_surface(self, surface: str, on: bool) -> None:
        if self.surfaces.get(surface) == bool(on):
            return
        self.surfaces[surface] = bool(on)
        self.revision += 1
        self.sigVisibilityChanged.emit()

    def surface_states(self) -> list:
        """``(surface, label, enabled)`` per surface, in `SURFACE_ORDER`."""
        return [
            (name, SURFACE_LABELS[name], self.surfaces.get(name, True))
            for name in SURFACE_ORDER
        ]

    def is_enabled(self, layer_id: str) -> bool:
        return self.drawable and self.layers.get(layer_id, False)

    def active_ids(self) -> list[str]:
        if self.bundle is None or not self.drawable:
            return []
        return [x.id for x in self.bundle if self.layers.get(x.id, False)]

    def set_layer(self, layer_id: str, on: bool) -> None:
        if self.layers.get(layer_id) == bool(on):
            return
        self.layers[layer_id] = bool(on)
        self.revision += 1
        self.sigVisibilityChanged.emit()

    def toggle_layer(self, layer_id: str) -> None:
        self.set_layer(layer_id, not self.layers.get(layer_id, False))

    def solo(self, layer_id: str) -> None:
        """Leave `layer_id` on and switch every other layer off.

        The primary gesture, because the reader looks at one or two layers at
        a time.  It is one bump of `revision` and one signal however many
        layers change, so a solo costs a stack one redraw rather than ten.
        """
        wanted = {i: (i == layer_id) for i in self.layers}
        if wanted == self.layers:
            return
        self.layers = wanted
        self.revision += 1
        self.sigVisibilityChanged.emit()

    def show_all(self) -> None:
        """The one obvious way back from a solo."""
        if self.bundle is None or all(self.layers.values()):
            return
        self.layers = dict.fromkeys(self.layers, True)
        self.revision += 1
        self.sigVisibilityChanged.emit()

    def layer_states(self) -> list[LayerState]:
        """One `LayerState` per layer of the bundle, in bundle order."""
        if self.bundle is None:
            return []
        return [
            LayerState(
                id=layer.id,
                label=layer.label,
                short=layer.short,
                micro=layer.micro,
                kind=layer.kind,
                count=len(layer),
                color=theme.annotation_color(layer.role),
                enabled=self.layers.get(layer.id, False),
                tip=layer.tip,
            )
            for layer in self.bundle
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

    def _window_cache(self, t0: float, t1: float, pixels: int) -> dict:
        """The cache for one view, emptied when the view moves.

        Every plot in the stack shows the same time range, so the first one to
        ask pays for the search, the decimation and the merge, and the other
        31 read the answer.  Points and spans share the cache because they
        share the view: one dict per browser per window, not one per kind.
        """
        key = (round(float(t0), 9), round(float(t1), 9), int(pixels))
        if key != self._cache_key:
            self._cache_key = key
            self._cache = {}
        return self._cache

    def point_window(
        self, layer_id: str, series: int, t0: float, t1: float, pixels: int
    ) -> tuple:
        """``(x pairs, drawn, total)`` for one point series in one view.

        ``x pairs`` is already interleaved for ``connect='pairs'`` --
        ``[t, t, t', t', ...]`` -- because that too is identical in every plot.
        `total` is the true number in the window, so a readout can report the
        density it is looking at rather than the number of lines that survived.
        """
        if self.bundle is None:
            return _EMPTY, 0, 0
        cache = self._window_cache(t0, t1, pixels)
        key = ("point", layer_id, series)
        hit = cache.get(key)
        if hit is None:
            layer = self.bundle.get(layer_id)
            if not isinstance(layer, PointLayer) or series >= len(layer.series):
                hit = (_EMPTY, 0, 0)
            else:
                times, total = windowing.window_points(
                    layer.series[series].times, t0, t1, pixels
                )
                hit = (np.repeat(times, 2), int(times.size), total)
            cache[key] = hit
        return hit

    def span_window(self, layer_id: str, t0: float, t1: float, pixels: int) -> tuple:
        """``(fill x, edge x, bars, total)`` for one span layer in one view.

        `fill x` is the ``2n`` bin-edge array a ``stepMode='center'`` curve
        wants: ``[s0, e0, s1, e1, ...]``, so bin `2k` is a span and bin
        ``2k+1`` is the gap after it.  `edge x` is the ``4n`` array
        ``connect='pairs'`` wants, two verticals per bar.

        Spans are **merged** at one device pixel, never decimated with the
        point path's keep-first pass: at the 607 s view one pixel is 0.43 s
        against a 0.544 s median trial, so keeping the first span per column
        drops most of the 12 silence trials and the control condition
        disappears from the overview.  Merging turns a cluster into one bar
        that still covers everywhere a trial was running, and it bounds the
        drawn bars by the pixel width whatever the file holds.  `total` is the
        TRUE pre-merge count in the window and is what a readout must report.
        """
        if self.bundle is None:
            return _EMPTY, _EMPTY, 0, 0
        cache = self._window_cache(t0, t1, pixels)
        key = ("span", layer_id)
        hit = cache.get(key)
        if hit is None:
            layer = self.bundle.get(layer_id)
            if not isinstance(layer, SpanLayer):
                hit = (_EMPTY, _EMPTY, 0, 0)
            else:
                hit = _span_arrays(layer, t0, t1, pixels)
            cache[key] = hit
        return hit

    def label_window(self, layer_id: str, t0: float, t1: float, pixels: int):
        """Start times of the spans in this view wide enough to hold a letter.

        Cached beside the span arrays and for the same reason: every plot in
        the stack shows the same time range, so a sixteen channel file would
        otherwise run this comparison 32 times over identical inputs.  Measured
        at the whole-session view of exp3, where the merge leaves ~1200 bars a
        layer and no bar is wide enough to label, doing it per overlay cost
        20 us of a 390 us redraw; doing it once costs that to the first plot
        and a dict hit to the other 31.

        Taken over the MERGED bars, not the raw spans, because the merged bar
        is what is drawn -- and a bar that stands for several spans still gets
        the right letter, since every span in a trial layer is one treatment.
        """
        if self.bundle is None:
            return _EMPTY
        cache = self._window_cache(t0, t1, pixels)
        key = ("label", layer_id)
        hit = cache.get(key)
        if hit is None:
            fill_x, _edge_x, bars, _total = self.span_window(layer_id, t0, t1, pixels)
            if bars == 0 or t1 <= t0 or pixels <= 0:
                hit = _EMPTY
            else:
                # compared in SECONDS rather than scaling every bar into
                # pixels: one subtraction over the array instead of two
                min_dt = MIN_LABEL_PX * (t1 - t0) / pixels
                starts = fill_x[0::2]
                hit = starts[(fill_x[1::2] - starts) >= min_dt]
            cache[key] = hit
        return hit

    # --- appearance -------------------------------------------------------

    def role(self, layer: Layer, series: int = 0) -> str:
        """The colour role of one drawn thing.

        Resolved per SERIES, never per layer: an explained detection takes the
        hue of the pulse that explains it, and a layer's own role is only the
        fallback for a series that carries none.
        """
        if isinstance(layer, PointLayer) and series < len(layer.series):
            return layer.series[series].role or layer.role
        return layer.role

    def mark_pen(self, layer: Layer, series: int = 0):
        """Pen for a full-height point line: hue from the category, dash from
        the evidence."""
        observed = True
        if isinstance(layer, PointLayer) and series < len(layer.series):
            observed = layer.series[series].observed
        return theme.annotation_pen(
            self.role(layer, series),
            width=theme.LW_THIN,
            observed=observed,
            unvalidated=self.unvalidated,
            alpha=MARK_ALPHA,
        )

    def edge_pen(self, layer: Layer):
        """Pen for a span's two full-height edges.

        Solid and opaque whatever the fill does.  In direct sun the fill is
        near-invisible and these lines are the whole reading of where the span
        starts and stops.
        """
        return theme.annotation_pen(
            layer.role,
            width=theme.LW_THIN,
            unvalidated=self.unvalidated,
            alpha=MARK_ALPHA,
        )

    def fill_alpha(self, layer_id: str, surface: str = SURFACE_TRACE) -> float:
        base = SPAN_FILL_ALPHA[theme.current_theme()]
        base *= float(SURFACE_STYLE[surface]["fill"])
        if layer_id in LOW_FILL_LAYERS:
            base *= LOW_FILL_SCALE
        return base

    def fill_brush(self, layer: Layer, surface: str = SURFACE_TRACE):
        return theme.annotation_brush(
            layer.role,
            self.fill_alpha(layer.id, surface),
            unvalidated=self.unvalidated,
        )

    def color(self, layer: Layer, series: int = 0) -> str:
        return theme.annotation_color(self.role(layer, series))

    # --- what the reader asks of a position -------------------------------

    def nearest(self, t: float):
        """``(layer, series, row)`` closest to `t` among the switched-on layers."""
        if self.bundle is None or not self.drawable:
            return None
        return self.bundle.nearest(t, self.active_ids())

    def step(self, t: float, forward: bool = True):
        """``(layer, series, row)`` first after (or before) `t`."""
        if self.bundle is None:
            return None
        return self.bundle.step(t, forward, self.active_ids())

    # --- what the badge has to say ---------------------------------------

    def badge(self) -> tuple[str, str, str]:
        """``(text, token, tooltip)`` for the annotation status chip.

        This is the other half of the promise that an unvalidated fit is never
        shown quietly.  The chip is always present while a bundle is loaded --
        there is no state in which annotations are on screen and the reader has
        to go looking for their provenance.
        """
        if self.bundle is None:
            return ("", "fg.muted", "")
        fit_line = self.bundle.meta.alignment
        fit = fit_line.fit_summary() or "no fit parameters in the metadata"
        if self.recording_mismatch:
            return (
                "WRONG RECORDING",
                "danger",
                f"This bundle was fitted against {self.recording_mismatch}, "
                f"not against the open file.\nEvery annotation would be in the "
                f"wrong place, so none is drawn.\n{fit}",
            )
        trust = self.trust
        if trust == TRUST_UNVALIDATED:
            why = (
                "validated is not an explicit true"
                if fit_line.validated is not None
                else "no validated key in [alignment]"
            )
            return (
                "UNVALIDATED",
                "danger",
                f"The alignment fit was never validated ({why}).\n"
                f"Every annotation is positioned by that fit, so if it is "
                f"wrong they are all wrong and still look plausible.\n"
                f"Lines are drawn broken and fills hatched to say so.\n{fit}",
            )
        if trust == TRUST_WARN:
            return (
                "WARNINGS",
                "accent",
                "The alignment is validated but the writer recorded warnings:\n"
                + "\n".join(f"• {w}" for w in fit_line.warnings)
                + f"\n{fit}",
            )
        return ("validated", "success", f"Alignment validated.\n{fit}")


_EMPTY = np.empty(0, dtype=np.float64)

#: What a `stepMode='center'` curve is handed when it has nothing to draw.
#: pyqtgraph requires ``len(x) == len(y) + 1`` in that mode, so a pair of
#: empty arrays raises rather than clearing.
_EMPTY_STEP_X = np.zeros(1, dtype=np.float64)


def _span_arrays(layer: SpanLayer, t0: float, t1: float, pixels: int) -> tuple:
    """Window and merge one span layer into the two x arrays a plot draws."""
    s, e, _rows, _n = windowing.window_spans(
        layer.starts, layer.ends, layer.max_end, t0, t1, layer.disjoint
    )
    if s.size == 0:
        return _EMPTY, _EMPTY, 0, 0
    tol = (t1 - t0) / pixels if pixels > 0 and t1 > t0 else 0.0
    out_s, out_e, _first, total = windowing.merge_spans(s, e, tol)
    bars = int(out_s.size)
    fill_x = np.empty(2 * bars, dtype=np.float64)
    fill_x[0::2] = out_s
    fill_x[1::2] = out_e
    edge_x = np.empty(4 * bars, dtype=np.float64)
    edge_x[0::4] = out_s
    edge_x[1::4] = out_s
    edge_x[2::4] = out_e
    edge_x[3::4] = out_e
    return fill_x, edge_x, bars, total


def mark_time(layer: Layer, series: int, row: int) -> float:
    """The time of one row of one layer, whatever kind the layer is.

    One shape for every kind, so the hover readout and the step key do not
    each need a type switch.  A span reports where it *starts*: that is the
    edge the step key should put on screen.
    """
    if isinstance(layer, PointLayer):
        return float(layer.series[series].times[row])
    if isinstance(layer, SpanLayer):
        return float(layer.starts[row])
    if isinstance(layer, StepTrack):
        return float(layer.times[row])
    raise TypeError(f"{type(layer).__name__} carries no times")


def describe_mark(layer: Layer, series: int, row: int) -> str:
    """One line about one row, for the pointer readout or a tool tip."""
    if isinstance(layer, PointLayer):
        return layer.describe(series, row)
    if isinstance(layer, SpanLayer):
        return layer.describe(row)
    return f"{layer.label}, t {mark_time(layer, series, row):.6f} s"


def _passive(item) -> None:
    """Make a plot item invisible to the mouse.

    pyqtgraph hands every `PlotCurveItem` and `ScatterPlotItem` the full set
    of accepted mouse buttons, so an annotation lying under the pointer is an
    item the scene will offer the press to before the view box sees it
    -- and a rubber band drag that starts on an event is exactly the drag a
    reader is most likely to make.  An annotation states where something is;
    it is not a control.
    """
    item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
    item.setAcceptHoverEvents(False)


class EventOverlay:
    """The annotations of one plot.

    Not a `pg.GraphicsObject`: it is a small controller over pyqtgraph's own
    items -- one curve per point series, one fill and one edge curve per span
    layer, one scatter per predicted series.  Letting pyqtgraph own them means
    the numpy array goes straight into ``arrayToQPath`` and no Python loop
    ever sees a row.
    """

    def __init__(self, plot, layer: AnnotationLayer, surface: str = SURFACE_TRACE):
        style = SURFACE_STYLE[surface]
        self.plot = plot
        self.layer = layer
        #: which of the three surfaces this overlay draws on; the layer's
        #: per-surface switch is read through it
        self.surface = surface
        #: 0.0 on the spectrogram, where an opaque image would swallow the
        #: fill; see `SURFACE_STYLE`.  No fill item is built at all then.
        self.fill_scale = float(style["fill"])
        self.wants_caps = bool(style["caps"])
        #: whether this surface draws the third-tier treatment letters
        self.wants_labels = bool(style["labels"])
        #: z of this surface's point lines and span edges
        self.mark_z = float(style["mark_z"])
        self.cap_z = self.mark_z + 1.0
        #: The letters ride above every other annotation.  They are the only
        #: mark that has to be READ rather than seen, and a point line drawn
        #: through the glyph costs the reading; still under the crosshair and
        #: the playback marker at 100.
        self.label_z = self.cap_z + 1.0
        #: point marks, keyed ``(layer id, series index)``
        self.marks: dict[tuple[str, int], pg.PlotCurveItem] = {}
        #: hollow diamond caps on the predicted series, same keys
        self.caps: dict[tuple[str, int], pg.ScatterPlotItem] = {}
        #: span interiors and span edges, keyed by layer id
        self.fills: dict[str, pg.PlotCurveItem] = {}
        self.edges: dict[str, pg.PlotCurveItem] = {}
        #: the fixed `LABEL_POOL` of treatment letters, built once
        self.labels: list[pg.TextItem] = []
        #: what each pool slot currently says, so `setText` -- which relays a
        #: whole QTextDocument -- is called only when the letter changes.  A
        #: slot keeps its layer from pan to pan, so it usually does not.
        self._label_text: list[str] = []
        #: how many pool slots are visible right now; the rest are parked
        self._labels_live = 0
        #: ``(layer id, letter)`` for every span layer that carries one
        self._letter_keys: tuple = ()
        self._keys: tuple = ()
        #: what was last drawn: view range, pixel budget, layer revision
        self._drawn: Optional[tuple] = None
        #: keys whose items are currently empty, so that clearing one that is
        #: already clear costs nothing.  Most layers are off or off screen
        #: most of the time, and setData() is not free even with nothing in it.
        self._blank: set = set()
        view = plot.getViewBox()
        if view is not None:
            # a y-only zoom changes how tall every mark has to be, and a
            # resize changes the pixel budget the decimation is cut to
            view.sigRangeChanged.connect(self._view_changed)
            view.sigResized.connect(self._view_changed)

    # --- items ------------------------------------------------------------

    def _wanted(self) -> tuple:
        """The draw keys the loaded bundle asks for, in bundle order.

        A `StepTrack` contributes none.  The control track is a held value,
        not an instant or an interval, and a staircase across a waveform lane
        would be a second y axis nobody asked for -- it belongs in its own
        panel sharing the time axis.  It still has a toggle, so the chip can
        say the layer exists.
        """
        bundle = self.layer.bundle
        if bundle is None:
            return ()
        keys = []
        for layer in bundle:
            if isinstance(layer, PointLayer):
                keys.extend(("point", layer.id, i) for i in range(len(layer.series)))
            elif isinstance(layer, SpanLayer):
                keys.append(("span", layer.id, 0))
        return tuple(keys)

    def rebuild(self) -> None:
        """Match the item set to the loaded bundle."""
        keys = self._wanted()
        bundle = self.layer.bundle
        # Re-read on every rebuild, including the early-out one.  The draw
        # keys are layer IDs, so two bundles can match on them; the letters
        # are the treatments, and nothing guarantees a second bundle spells
        # them the same way.
        letters = (
            ()
            if bundle is None
            else tuple(
                (layer.id, layer.letter)
                for layer in bundle
                if isinstance(layer, SpanLayer) and layer.letter
            )
        )
        self._letter_keys = letters
        self._ensure_labels()
        if keys == self._keys:
            self.polish()
            return
        # `clear` parks the pool and drops the letters with everything else,
        # so they are put back after it rather than before
        self.clear()
        self._letter_keys = letters
        for kind, layer_id, series in keys:
            layer = bundle[layer_id]
            if kind == "point":
                curve = pg.PlotCurveItem(
                    connect="pairs", antialias=False, skipFiniteCheck=True
                )
                curve.setZValue(self.mark_z)
                _passive(curve)
                self.plot.addItem(curve, ignoreBounds=True)
                self.marks[(layer_id, series)] = curve
                if self.wants_caps and not layer.series[series].observed:
                    cap = pg.ScatterPlotItem(
                        symbol="d", size=theme.S8, pxMode=True, hoverable=False
                    )
                    cap.setZValue(self.cap_z)
                    _passive(cap)
                    self.plot.addItem(cap, ignoreBounds=True)
                    self.caps[(layer_id, series)] = cap
            else:
                if self.fill_scale > 0.0:
                    # pen=None is not decoration: a stepMode curve with a pen
                    # strokes its baseline across every gap and leaves a rule
                    # that reads as a grid line.  stepMode is set on the first
                    # setData rather than here, because the constructor's empty
                    # arrays fail its own len(x) == len(y) + 1 check.
                    fill = pg.PlotCurveItem(
                        pen=None,
                        antialias=False,
                        skipFiniteCheck=True,
                        fillLevel=0.0,
                    )
                    fill.setZValue(FILL_Z)
                    _passive(fill)
                    self.plot.addItem(fill, ignoreBounds=True)
                    self.fills[layer_id] = fill
                edge = pg.PlotCurveItem(
                    connect="pairs", antialias=False, skipFiniteCheck=True
                )
                edge.setZValue(self.mark_z)
                _passive(edge)
                self.plot.addItem(edge, ignoreBounds=True)
                self.edges[layer_id] = edge
        self._keys = keys
        self._drawn = None
        self._blank = set()
        self.polish()

    def _ensure_labels(self) -> None:
        """Build the treatment-letter pool, once per overlay and never again.

        Deliberately outside `rebuild`'s clear-and-recreate cycle.  A pool
        slot carries no identity -- it is a rectangle that is told what to say
        each pan -- so loading a second bundle has nothing to rebuild, and
        rebuilding one anyway would pay 24 x 265 us on every load, on every
        one of a sixteen channel file's 32 lanes.
        """
        if self.labels or not self.wants_labels or not self._letter_keys:
            return
        font = theme.font_mono(theme.SIZE_SMALL_PT, bold=True)
        for _ in range(LABEL_POOL):
            item = pg.TextItem(text="", anchor=(0.0, 0.0))
            item.setFont(font)
            # QGraphicsTextItem defaults to a 4 px document margin on every
            # side, which is 8 px of chip around a 7 px glyph -- wide enough
            # that a 1 s trial would need a 22 px bar to be labelled.  1 px
            # keeps the knockout readable and the width test honest.
            item.textItem.document().setDocumentMargin(1)
            item.setZValue(self.label_z)
            _passive(item)
            _passive(item.textItem)
            item.setVisible(False)
            self.plot.addItem(item, ignoreBounds=True)
            self.labels.append(item)
        self._label_text = [""] * LABEL_POOL

    def polish(self) -> None:
        """Re-resolve every pen and brush from the active theme and trust state.

        Called on a theme switch and after a rebuild, never from `update_plot`:
        resolving a colour is a dictionary walk and a QColor, and doing it per
        redraw would put it on the pan path of all 32 lanes.
        """
        bundle = self.layer.bundle
        if bundle is None:
            return
        for (layer_id, series), curve in self.marks.items():
            layer = bundle.get(layer_id)
            if layer is None:
                continue
            curve.setPen(self.layer.mark_pen(layer, series))
            cap = self.caps.get((layer_id, series))
            if cap is not None:
                # hollow, always: a filled dot reads as a measurement
                cap.setBrush(pg.mkBrush(None))
                cap.setPen(
                    theme.pen(
                        self.layer.color(layer, series),
                        width=theme.LW_THIN,
                        alpha=MARK_ALPHA,
                    )
                )
        for layer_id, edge in self.edges.items():
            layer = bundle.get(layer_id)
            if layer is None:
                continue
            edge.setPen(self.layer.edge_pen(layer))
            fill = self.fills.get(layer_id)
            if fill is not None:
                fill.setBrush(self.layer.fill_brush(layer, self.surface))
        if self.labels and self._letter_keys:
            # One colour for the whole pool, because every lettered layer is a
            # trial and every trial is one hue -- that is the ruling the
            # letters exist to serve.  So a slot can be handed from a `V` to
            # an `S` between pans without touching a brush.
            chip, glyph = theme.annotation_letter(bundle[self._letter_keys[0][0]].role)
            brush = theme.brush(chip)
            color = theme.qcolor(glyph)
            for item in self.labels:
                # `fill` is a plain attribute pyqtgraph reads in paint(), so
                # it takes an explicit update() where setColor() schedules one
                item.fill = brush
                item.setColor(color)
                item.update()

    def clear(self) -> None:
        for item in (
            list(self.marks.values())
            + list(self.caps.values())
            + list(self.fills.values())
            + list(self.edges.values())
        ):
            self.plot.removeItem(item)
        self.marks = {}
        self.caps = {}
        self.fills = {}
        self.edges = {}
        # The label pool is PARKED, not removed.  Its slots belong to the
        # overlay and not to the bundle, so unloading one and loading another
        # has nothing to rebuild -- and `removeItem` is 47 us an item, the
        # very cost the pool exists to avoid paying.
        for item in self.labels:
            item.setVisible(False)
        self._labels_live = 0
        self._letter_keys = ()
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

    def cap_y(self, y0: float, y1: float) -> float:
        """Where the diamond cap on a predicted mark is centred.

        Not `y1`.  A `pxMode` scatter is centred on its data point, so a cap
        sitting on the top of the view box is cut in half by it and renders as
        a chevron -- which is a different glyph, and the glyph is the whole
        difference between predicted and observed.  Dropped by half the
        symbol plus its pen, converted through the view box's own height so
        the inset stays a fixed number of pixels at any zoom.
        """
        view = self.plot.getViewBox()
        height = float(view.height()) if view is not None else 0.0
        if height <= 0.0:
            return y1
        inset = (theme.S8 / 2.0 + CAP_INSET_PX) * (y1 - y0) / height
        # a lane shorter than the symbol has no room for a cap anywhere; keep
        # it inside rather than pushing it out the bottom
        return y1 - min(inset, (y1 - y0) / 2.0)

    def update_plot(self) -> None:
        if self.layer.bundle is None or not self._keys:
            return
        # A hidden lane still gets its view box's sigRangeChanged, and
        # redrawing what nobody can see is the whole cost of hiding a channel
        # in a sixteen channel stack.  The last-drawn state is dropped rather
        # than kept: nothing promises a range signal when the lane is shown
        # again -- the navigator hides its rows through setVisible() alone --
        # so the next call has to redraw whatever it is handed.
        if not self.plot.isVisible():
            self._drawn = None
            return
        view = self.plot.getViewBox()
        if view is None:
            return
        (t0, t1), (y0, y1) = view.viewRange()
        pixels = self.pixels()
        # A pan reaches an overlay twice -- once through the view box's own
        # sigRangeChanged and once through Panels.update_plots() -- and a
        # y-only zoom reaches it without moving a single mark's x.  setData()
        # invalidates a QPainterPath and schedules a repaint whatever it is
        # handed, so the cheapest redraw is the one that does not happen.
        # the view box's own height is in here because `cap_y` is a *pixel*
        # inset from the top: a lane resized vertically keeps its y range, so
        # without this the cap would keep the inset of the old height.
        state = (t0, t1, y0, y1, pixels, view.height(), self.layer.revision)
        if state == self._drawn:
            return
        self._drawn = state
        on = self.layer.surface_enabled(self.surface)
        for key in self._keys:
            kind, layer_id, series = key
            live = on and self.layer.is_enabled(layer_id)
            if kind == "point":
                self._draw_points(key, series, live, t0, t1, y0, y1, pixels)
            else:
                self._draw_spans(key, live, t0, t1, y0, y1, pixels)
        self._draw_labels(on, t0, t1, y1, pixels)

    def _draw_points(self, key, series, live, t0, t1, y0, y1, pixels) -> None:
        layer_id = key[1]
        curve = self.marks[(layer_id, series)]
        cap = self.caps.get((layer_id, series))
        xpairs, drawn = _EMPTY, 0
        if live:
            xpairs, drawn, _total = self.layer.point_window(
                layer_id, series, t0, t1, pixels
            )
        if drawn == 0:
            if key not in self._blank:
                curve.setData(_EMPTY, _EMPTY)
                if cap is not None:
                    cap.setData([], [])
                self._blank.add(key)
            return
        self._blank.discard(key)
        # xpairs is the layer's cached array, shared with every other plot in
        # the stack; pyqtgraph keeps a reference and never writes to it, so one
        # window is one allocation for all 32 lanes.
        curve.setData(xpairs, np.tile((y0, y1), drawn), connect="pairs")
        if cap is not None:
            if drawn <= CAP_LIMIT:
                # every second entry of xpairs is one mark's time
                cap.setData(xpairs[::2], np.full(drawn, self.cap_y(y0, y1)))
            else:
                cap.setData([], [])

    def _label_plan(self, t0, t1, pixels) -> list:
        """``[(letter, starts)]`` for the spans wide enough to be labelled.

        Empty when more spans qualify than the pool can seat: labelling 24 of
        30 would leave six bare with nothing on screen to say which six, and a
        reader who saw `V V V` over three of four adjacent bars would read the
        fourth as a `V` too.  Nothing is a state they can see.

        The width test itself lives in `AnnotationLayer.label_window`, shared
        with every other plot showing the same range, so all this does per
        overlay is a dict hit per lettered layer.
        """
        plan = []
        seated = 0
        for layer_id, letter in self._letter_keys:
            if not self.layer.is_enabled(layer_id):
                continue
            starts = self.layer.label_window(layer_id, t0, t1, pixels)
            if starts.size == 0:
                continue
            seated += int(starts.size)
            if seated > LABEL_POOL:
                return []
            plan.append((letter, starts))
        return plan

    def _draw_labels(self, on, t0, t1, y1, pixels) -> None:
        """Seat the treatment letters for this view, and park the rest.

        Nothing here constructs a `pg.TextItem` and nothing removes one: the
        pool was built by `_ensure_labels` and every slot is driven by
        `setPos`, `setVisible` and -- only when the letter in that slot
        actually changes -- `setText`.  Slots are handed out in bundle order,
        so on a pan the same slot usually keeps the same letter and `setText`
        is not called at all.
        """
        if not self.labels:
            return
        plan = self._label_plan(t0, t1, pixels) if on and self._letter_keys else []
        used = 0
        for letter, starts in plan:
            for start in starts:
                item = self.labels[used]
                if self._label_text[used] != letter:
                    item.setText(letter)
                    self._label_text[used] = letter
                # the top of the view, at the span's own start edge: the chip
                # hangs down INTO the span it belongs to, never over the gap
                # before it
                item.setPos(float(start), y1)
                item.setVisible(True)
                used += 1
        for i in range(used, self._labels_live):
            self.labels[i].setVisible(False)
        self._labels_live = used

    def _draw_spans(self, key, live, t0, t1, y0, y1, pixels) -> None:
        layer_id = key[1]
        # None on the spectrogram, which paints no interior at all
        fill = self.fills.get(layer_id)
        edge = self.edges[layer_id]
        fill_x, edge_x, bars = _EMPTY, _EMPTY, 0
        if live:
            fill_x, edge_x, bars, _total = self.layer.span_window(
                layer_id, t0, t1, pixels
            )
        if bars == 0:
            if key not in self._blank:
                if fill is not None:
                    fill.setData(_EMPTY_STEP_X, _EMPTY, stepMode="center", fillLevel=y0)
                edge.setData(_EMPTY, _EMPTY)
                self._blank.add(key)
            return
        self._blank.discard(key)
        edge.setData(edge_x, np.tile((y0, y1), 2 * bars), connect="pairs")
        if fill is None:
            return
        # 2n bin edges, 2n-1 bins: the odd bins are the gaps between spans and
        # sit exactly at the fill level, so they enclose no area.
        fill_y = np.full(2 * bars - 1, y0, dtype=np.float64)
        fill_y[0::2] = y1
        fill.setData(fill_x, fill_y, stepMode="center", fillLevel=y0)


# --- legend ------------------------------------------------------------------
#
# The chips in the parameter bar are the only legend the annotations have, so
# their icons are drawn with the same pens and brushes the plot uses rather
# than with a generic swatch.  A reader can then match a mark on screen to a
# chip by looking at it, instead of by remembering a rule.

#: Icon size of a legend chip, in logical pixels.
LEGEND_W = 18
LEGEND_H = 12


def _mark_style(observed: bool, unvalidated: bool):
    if not observed:
        return Qt.PenStyle.DashLine
    return Qt.PenStyle.DashLine if unvalidated else Qt.PenStyle.SolidLine


def _legend_pixmap(color: str, observed: bool, unvalidated: bool) -> QPixmap:
    pixmap = QPixmap(LEGEND_W, LEGEND_H)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    painter.setPen(
        theme.pen(
            color,
            width=theme.LW_THIN,
            style=_mark_style(observed, unvalidated),
            cosmetic=False,
        )
    )
    x = LEGEND_W // 2
    # Full height for BOTH, top row to bottom row.  The chip is the only
    # legend the marks have, so a short predicted line here would teach the
    # reader a stub the lane never draws -- and a per-kind y allocation is
    # exactly what the drawing rule forbids.  Predicted is told apart by the
    # dash and the cap, never by height.
    painter.drawLine(x, 0, x, LEGEND_H - 1)
    if not observed:
        painter.setPen(theme.pen(color, width=theme.LW_HAIRLINE, cosmetic=False))
        painter.drawRect(QRect(x - 2, 1, 4, 4))
    painter.end()
    return pixmap


def legend_icon(color: str, observed: bool = True, unvalidated: bool = False) -> QIcon:
    """A point chip's icon, drawn with the pen the overlay itself uses."""
    return QIcon(_legend_pixmap(color, observed, unvalidated))


def _span_pixmap(color: str, alpha: float, unvalidated: bool) -> QPixmap:
    pixmap = QPixmap(LEGEND_W, LEGEND_H)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    inset = 3
    # the chip says what the lane says: a weak interior between two full
    # height edges, so a reader who can only see the edges outdoors still
    # matches the chip to the mark
    interior = theme.brush(color, alpha=alpha)
    if unvalidated:
        interior.setStyle(Qt.BrushStyle.BDiagPattern)
    painter.fillRect(QRect(inset, 0, LEGEND_W - 2 * inset, LEGEND_H), interior)
    painter.setPen(
        theme.pen(
            color,
            width=theme.LW_THIN,
            style=Qt.PenStyle.DashLine if unvalidated else Qt.PenStyle.SolidLine,
            cosmetic=False,
        )
    )
    painter.drawLine(inset, 0, inset, LEGEND_H - 1)
    painter.drawLine(LEGEND_W - inset - 1, 0, LEGEND_W - inset - 1, LEGEND_H - 1)
    painter.end()
    return pixmap


def span_icon(color: str, alpha: float, unvalidated: bool = False) -> QIcon:
    """A span chip's icon: two edges and the interior between them."""
    return QIcon(_span_pixmap(color, alpha, unvalidated))


def swatch_pixmap(color: str) -> QPixmap:
    """A filled square in a layer's colour, with a hairline ring.

    The ring is what makes a dark swatch visible on the dark theme's chrome
    and a light one visible on the daylight theme's.  Drawn on the same
    canvas as `legend_icon`, so every chip sits on one baseline.
    """
    pixmap = QPixmap(LEGEND_W, LEGEND_H)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setBrush(theme.brush(color))
    painter.setPen(theme.pen("border", width=theme.HAIRLINE, cosmetic=False))
    inset = (LEGEND_W - LEGEND_H) // 2
    painter.drawRect(inset + 1, 1, LEGEND_H - 3, LEGEND_H - 3)
    painter.end()
    return pixmap


def swatch_icon(color: str) -> QIcon:
    return QIcon(swatch_pixmap(color))
