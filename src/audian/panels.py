"""Manage plot panels.

`class Panel`: a single plot panel
`class Panels`: manage all plot panels
"""

import numpy as np
import pyqtgraph as pg

from . import theme
from .specitem import SpecItem
from .traceitem import TraceItem


def resolve_colormap(color_map):
    """Return a `pg.ColorMap` for a color map object, name or theme index.

    Names and indices go through :func:`theme.spectrogram_colormap`, which
    orients the ramp so the noise floor -- most of a spectrogram -- matches
    the page: the dark end under the dark theme, the light end under the
    daylight one.  Asking pyqtgraph directly would skip that for every name
    it happens to know, which is nearly all of them, and leave a black slab
    sitting in a white window.
    """
    if isinstance(color_map, pg.ColorMap):
        return color_map
    try:
        return theme.spectrogram_colormap(color_map)
    except Exception:
        pass
    if isinstance(color_map, str):
        try:
            return pg.colormap.get(color_map)
        except Exception:
            pass
    return theme.spectrogram_colormap(theme.DEFAULT_SPECTROGRAM_MAP)


class PeakingColorMap(pg.ColorMap):
    """A colour map whose LAST lookup-table entry is a warning colour.

    Peaking -- focus peaking in a camera, a zebra on a video scope -- marks
    the bins the picture has stopped telling apart.
    `pg.ImageItem.setLevels((zmin, zmax))` maps everything at or above
    `zmax` onto the last entry of the lookup table, so replacing that one
    entry marks exactly the clipped pixels and costs nothing per frame:
    there is no mask, no second image and no work in `update_plot`.

    With the 256 entry table both `ImageItem` and `pg.ColorBarItem` ask for,
    that entry also covers the top 0.4 % of the ramp -- 0.25 dB of a 65 dB
    scale.  That is what a scope's zebra does and it is the wanted
    behaviour: a bin one part in 256 below the ceiling is not being told
    apart from the ceiling either.

    The top end only.  Half of a panel is at or below the floor by
    construction -- `SpectrogramPlot.fit_levels` anchors it on the median --
    so marking the floor would mark half the picture.

    A subclass, and the mark applied in `getLookupTable`, because that is
    the only form that is exact.  Measured against every map both themes
    offer: every entry but the last is byte for byte the base map's, at
    nPts 256 and 512, with and without alpha.  Moving the base map's last
    *stop* instead would have blended the warning colour back across the
    whole final segment of the ramp, which is a picture in which the loud
    bins are gradually the wrong colour rather than one in which the
    clipped bins are marked.

    It has to be a real ``pg.ColorMap``: `ImageItem.setColorMap` raises
    ``TypeError`` on anything else, and `ColorBarItem` hands the object it
    was given straight to it.
    """

    def __init__(self, base, mark):
        # `base.color` is float RGBA in 0..1 and `pg.ColorMap.__init__`
        # feeds every entry through `mkColor`, which reads a 4-tuple as
        # bytes -- so the stops go over as bytes.  Verified round trip: the
        # reconstructed map's table equals the base's everywhere but the
        # entry this class exists to replace.
        colors = (np.clip(base.color, 0.0, 1.0) * 255).round().astype(int)
        super().__init__(
            base.pos,
            [tuple(int(v) for v in row) for row in colors],
            mapping=base.mapping_mode,
            name=f"{base.name}+peaking",
        )
        self.mark = pg.mkColor(mark).getRgbF()

    def getLookupTable(self, *args, **kwargs):
        table = super().getLookupTable(*args, **kwargs)
        # `mode=QCOLOR` hands back a list of QColor rather than an array.
        # Nothing here asks for it, and a wrong answer is worse than the
        # unmarked one, so it is left alone rather than guessed at.
        if not isinstance(table, np.ndarray) or table.size == 0:
            return table
        table = table.copy()
        scale = 255.0 if table.dtype.kind in "ui" else 1.0
        table[-1, :3] = [round(c * scale) for c in self.mark[:3]]
        return table


def peaking_colormap(color_map, peaking: bool):
    """Resolve a colour map, and mark its top with `theme`'s warning colour.

    Called wherever a map is *applied* and never where one is chosen, so
    that `Shift+C` and a theme switch cannot quietly drop the mark.  There
    is one caller, `DataBrowser.set_color_map`, and it is the sink both of
    those paths end in -- which is the whole reason this sits beside
    `resolve_colormap` rather than inside it: resolving happens in places
    that have no browser to ask.

    The colour comes from the token table and is not written here: the two
    themes have different grounds and different ramps, and the whole point
    of the mark is a colour that cannot be mistaken for a hot bin of the
    ramp itself.  See ``spec.clip``.
    """
    color_map = resolve_colormap(color_map)
    if not peaking:
        return color_map
    return PeakingColorMap(color_map, theme.token("spec.clip"))


class Panel(object):
    times = "t"
    amplitudes = "xyu"
    frequencies = "fw"
    powers = "pq"
    spacer = "spacer"

    def __init__(self, name, ax_spec, row):
        self.name = name
        self.ax_spec = ax_spec
        self.row = row
        self.axs = []
        self.axcs = []  # associated color bars

    def __str__(self):
        return f"{self.name:20}: {self.ax_spec:6} @ {self.row:2} with {len(self.axs):2} plots"

    def __len__(self):
        return len(self.axs)

    def __eq__(self, ax_spec):
        return self.ax_spec == ax_spec

    def x(self):
        return self.ax_spec[0]

    def y(self):
        return self.ax_spec[1]

    def z(self):
        return self.ax_spec[2] if len(self.ax_spec) > 2 else ""

    def is_time(self):
        return self.x() in self.times

    def is_xamplitude(self):
        return self.x() in self.amplitudes

    def is_yamplitude(self):
        return self.y() in self.amplitudes

    def is_xfrequency(self):
        return self.x() in self.frequencies

    def is_yfrequency(self):
        return self.y() in self.frequencies

    def is_xpower(self):
        return self.x() in self.powers

    def is_ypower(self):
        return self.y() in self.powers

    def is_zpower(self):
        z = self.z()
        return z and z in self.powers

    def is_trace(self):
        return self.is_time() and self.is_yamplitude()

    def is_spectrogram(self):
        return self.is_time() and self.is_yfrequency()

    def is_power(self):
        return self.is_xpower() and self.is_yfrequency()

    def is_spacer(self):
        return self.ax_spec == self.spacer

    def add_ax(self, row, ax, axc=None):
        self.row = row
        self.axs.append(ax)
        if axc is not None:
            self.axcs.append(axc)

    def is_used(self):
        return len(self.axs) > 0

    def is_visible(self, channel):
        return self.axs[channel].isVisible()

    def set_visible(self, visible):
        changed = False
        for ax in self.axs:
            if ax.isVisible() != visible:
                changed = True
            ax.setVisible(visible)
        return changed

    def has_visible_traces(self, channel):
        """Does this channel's panel have anything of its own to draw?

        Asked *of the panel*, not of the screen.  `QGraphicsItem.isVisible`
        is the effective answer -- false whenever any ancestor is hidden --
        so a panel that the layout hid once reported "nothing to draw"
        forever after, and the layout that hid it took that as the reason to
        keep it hidden.  On a sixteen channel stack, where the spectrogram
        follows the focused lane, stepping to the next channel hid the old
        lane's spectrogram and no lane ever drew one again.
        `isVisibleTo(plot)` asks only whether the item itself was hidden
        inside its own plot.
        """
        if self.is_spacer():
            return False
        plot = self.axs[channel]
        for di in plot.data_items:
            if di.isVisibleTo(plot):
                return True
        return False

    def has_viewbox(self, viewbox):
        for ax in self.axs:
            if ax.getViewBox() is viewbox:
                return True
        return False

    def show_grid(self, grids):
        if self.is_spacer():
            return False
        for ax in self.axs:
            ax.set_grid((grids & 1) > 0, (grids & 2) > 0)

    def is_cbar_visible(self, channel):
        return self.axcs[channel].isVisible()

    def set_cbar_visible(self, visible):
        changed = False
        for ax in self.axcs:
            if ax.isVisible() != visible:
                changed = True
            ax.setVisible(visible)
        return changed

    def set_colormap(self, color_map):
        """Set the color map of all color bars.

        Accepts a `pg.ColorMap`, a color map name or a theme color map index.
        """
        color_map = resolve_colormap(color_map)
        for ax in self.axcs:
            ax.setColorMap(color_map)

    def set_smoothing(self, key) -> bool:
        """Set how every spectrogram of this panel is smoothed.

        Asked of each plot with `hasattr` rather than of the panel's own
        `is_spectrogram()`, for the reason `has_visible_traces` records: a
        panel answers about its axis letters, and what can be smoothed is a
        property of the plot.  Returns whether anything changed, so the
        caller can redraw only when it has to.
        """
        changed = False
        for ax in self.axs:
            if hasattr(ax, "set_smoothing") and ax.set_smoothing(key):
                changed = True
        return changed

    def add_item(self, plot_item, channel=-1, is_data=False):
        if channel >= 0:
            self.axs[channel].add_item(plot_item, is_data)
        else:
            for ax in self.axs:
                ax.add_item(plot_item, is_data)

    def add_traces(self, channel, data):
        for trace in data.traces:
            if trace.panel != self.name:
                continue
            if self.is_trace():
                item = TraceItem(trace, channel)
            if self.is_spectrogram():
                item = SpecItem(trace, channel)
            self.add_item(item, channel, True)

    def get_amplitude(self, channel, t, x, t1=None):
        if not self.is_yamplitude() or len(self.axs[channel].data_items) == 0:
            return t, None
        trace = self.axs[channel].data_items[-1]
        return trace.get_amplitude(t, x, t1)

    def get_power(self, channel, t, f):
        if not self.is_yfrequency() or len(self.axs[channel].data_items) == 0:
            return None
        trace = self.axs[channel].data_items[0]
        return trace.get_power(t, f)

    def update_plots(self):
        for ax in self.axs:
            if ax.isVisible() and not self.is_spacer():
                ax.update_plot()


class Panels(dict):
    def __init__(self):
        super().__init__(self)

    def __str__(self):
        s = []
        for panel in self.values():
            s.append(str(panel))
        return "\n".join(s)

    def add(self, name, axes, row=None, adjust_rows=True):
        if row is None:
            row = self.max_row() + 1
        if adjust_rows:
            for panel in self.values():
                if panel.row >= row:
                    panel.row += 1
        self[name] = Panel(name, axes, row)
        if len(self) > 1:
            names = np.array(list(self.keys()))
            rows = [self[name].row for name in names]
            inx = np.argsort(rows)
            panels = dict(self)
            self.clear()
            for name in names[inx]:
                self[name] = panels[name]

    def add_trace(self, name="trace", row=None):
        # find amplitude that is not used yet:
        amps = [False] * len(Panel.amplitudes)
        for panel in self.values():
            if panel.is_trace():
                amps[Panel.amplitudes.index(panel.y())] = True
        axspec = Panel.times[0] + Panel.amplitudes[0]
        for k in range(len(amps)):
            if not amps[k]:
                axspec = axspec[0] + Panel.amplitudes[k]
                break
        self.add(name, axspec, row)

    def add_spectrogram(self, name="spectrogram", row=None):
        # find frequencies and powers that are not used yet:
        freqs = [False] * len(Panel.frequencies)
        pwrs = [False] * len(Panel.powers)
        for panel in self.values():
            if panel.is_spectrogram():
                freqs[Panel.frequencies.index(panel.y())] = True
                pwrs[Panel.powers.index(panel.z())] = True
        axspec = Panel.times[0] + Panel.frequencies[0] + Panel.powers[0]
        for k in range(len(freqs)):
            if not freqs[k]:
                axspec = axspec[0] + Panel.frequencies[k] + axspec[2]
                break
        for k in range(len(pwrs)):
            if not pwrs[k]:
                axspec = axspec[:2] + Panel.powers[k]
                break
        self.add(name, axspec, row)
        self.add(name + "-power", axspec[2] + axspec[1], self[name].row, False)

    def fill(self, data):
        for trace in data.traces:
            if trace.panel not in self:
                if trace.panel_type == "trace":
                    self.add_trace(trace.panel)
                elif trace.panel_type == "spectrogram":
                    self.add_spectrogram(trace.panel)

    def remove(self, name):
        del self[name]

    def max_row(self):
        if len(self) > 0:
            return np.max([panel.row for panel in self.values()])
        else:
            return -1

    def add_power_ax(self, name, row, ax):
        name = name + "-power"
        if name in self:
            self[name].add_ax(row, ax)

    def get_panel(self, viewbox):
        for panel in self.values():
            if panel.has_viewbox(viewbox):
                return panel
        return None

    def show_grid(self, grids):
        for panel in self.values():
            panel.show_grid(grids)

    def update_plots(self):
        for panel in self.values():
            panel.update_plots()

    def insert_spacers(self):
        """Put a zero-height row between every pair of panels.

        Which of them gets a grab band is `databrowser.split_spacers`'
        business, and it is a per-channel question: on a sixteen channel
        stack one lane has a trace / spectrogram boundary and fifteen do
        not.
        """
        panels = {}
        row = 0
        spacer = 0
        for name in self:
            if row > 0 and not self[name].is_power():
                panels[f"spacer{spacer}"] = Panel(f"spacer{spacer}", Panel.spacer, 0)
                spacer += 1
            panels[name] = self[name]
            row += 1
        self.clear()
        for name, value in panels.items():
            self[name] = value
