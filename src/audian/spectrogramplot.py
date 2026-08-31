"""PlotItem for interactive display of spectrograms."""

import numpy as np
import pyqtgraph as pg

from PySide6.QtCore import QTimer, Signal
from thunderlab.powerspectrum import decibel

from . import smoothing, theme
from .bufferedspectrogram import channel_power
from .panels import Panel, resolve_colormap
from .rangeplot import RangePlot
from .specitem import SpecItem
from .timeplot import TimePlot

#: Longest a caption's channel list may get before it is folded to a count.
#:
#: Measured in the mono face at `theme.SIZE_SMALL_PT`: `MEAN 00-15` is 78 px
#: and the longest caption this cap allows -- `MEAN 00,03,06,09,12,15`, every
#: third electrode of sixteen -- is 172 px, against the 1532 px view box the
#: mean panel measured at in a 1748 px window.  Without a cap the list has no
#: bound the panel can rely on: every other electrode of sixteen is already
#: `00,02,04,06,08,10,12,14`, 23 glyphs, and a wider array only grows it.  A
#: caption is one line, so the overflow is folded rather than wrapped, and
#: the toggle names the full set in the status bar -- the folded form is
#: never the only place the selection is stated.
MAX_CHANNEL_LABEL_CHARS = 17


def channel_range_label(channels) -> str:
    """Name a set of channels in one short line: `00-15`, `00-03,09`, `11 ch`.

    Runs are collapsed because the interesting selections are contiguous --
    an electrode grid solo'd down to one row, a muted pair at the end of the
    array -- and a run written out is three glyphs where the channels are
    two each plus a comma.  `MAX_CHANNEL_LABEL_CHARS` carries what happens
    when the selection is scattered enough that collapsing does not help.
    """
    channels = sorted({int(c) for c in channels})
    if not channels:
        return "none"
    runs = [[channels[0], channels[0]]]
    for c in channels[1:]:
        if c == runs[-1][1] + 1:
            runs[-1][1] = c
        else:
            runs.append([c, c])
    text = ",".join(
        f"{lo:02d}" if lo == hi else f"{lo:02d}-{hi:02d}" for lo, hi in runs
    )
    if len(text) > MAX_CHANNEL_LABEL_CHARS:
        return f"{len(channels)} ch"
    return text


class PowerPlot(RangePlot):
    def __init__(self, aspec, channel, browser, *args, **kwargs):
        super().__init__(aspec, channel, browser, *args, **kwargs)
        self.getAxis("left").showLabel(False)
        self.getAxis("left").setStyle(showValues=False)
        self.getAxis("bottom").showLabel(False)
        self.getAxis("bottom").setStyle(showValues=False)
        for axis in ["left", "right", "bottom", "top"]:
            self.getAxis(axis).setVisible(False)
        # data:
        self.power_item = pg.PlotCurveItem(
            connect="all", antialias=False, skipFiniteCheck=True
        )
        self.power_item.setPen(theme.power_pen())
        self.add_item(self.power_item)
        # the zero item only anchors the fill, it is not a data curve:
        self.zero_item = pg.PlotCurveItem(
            connect="all", antialias=False, skipFiniteCheck=True
        )
        self.zero_item.setPen(theme.zero_pen())
        self.add_item(self.zero_item)
        self.fill_item = pg.FillBetweenItem(
            self.zero_item, self.power_item, theme.power_fill_brush()
        )
        self.add_item(self.fill_item)

    def polish(self) -> None:
        super().polish()
        self.power_item.setPen(theme.power_pen())
        self.zero_item.setPen(theme.zero_pen())
        self.fill_item.setBrush(theme.power_fill_brush())

    def range(self, axspec):
        if axspec == self.x():
            return -100, 20, 5
        elif axspec == self.y():
            return super().range(axspec)

    def get_marker_pos(self, x, dx, y, dy):
        xdata, ydata = self.power_item.getData()
        i0 = np.argmin(np.abs(ydata - y))
        i1 = np.argmin(np.abs(ydata - (y + dy)))
        if i1 > len(ydata):
            i1 = len(ydata)
        if i1 <= i0:
            i0 = max(0, i1 - 1)
        if i0 >= i1:
            i1 = i0 + 1
        i = i0 + np.argmax(xdata[i0:i1])
        return xdata[i], ydata[i], None


class SpectrogramPlot(TimePlot):
    sigUpdateFilter = Signal(object, object)

    # SI prefix currently shown on the frequency axis:
    _caption_prefix = None

    #: Channels this panel averages, or None while it is one channel's own.
    #: A class level default for the same reason the flags below are: the
    #: PlotItem constructor reaches setVisible() before __init__ runs.
    mean_channels = None

    # Class level defaults: pg.PlotItem's constructor can reach setVisible()
    # before __init__ gets to the instance attributes.
    _levels_fitted = False
    _refit_pending = False
    _applying_levels = False

    #: The two writers of the filter handles' state, kept apart -- see
    #: `set_handles_visible`.  Class level for the same reason as the flags
    #: above, and both default to what the handles are built as.
    _handles_movable = True
    _handles_shown = True

    # The y axis here is frequency, with a hard Nyquist ceiling.  Padding
    # above it would only add an empty strip, so the caption headroom that
    # TimePlot reserves for an amplitude axis is switched off.
    Y_TOP_PAD = 0.0

    #: Smallest and largest span of the level mapping, in dB.  Same bounds
    #: `BufferedSpectrogram.estimate_noiselevels()` uses, so that whichever of
    #: the two estimates is in force the bar covers a comparable range.
    MIN_LEVEL_SPAN_DB = 20.0
    MAX_LEVEL_SPAN_DB = 80.0

    #: The automatic floor is the *median* of the in-view power distribution
    #: plus this margin.
    #:
    #: Measured on data/Gryllus_campestris.wav, 10 s window: the existing
    #: floor (95th percentile of the top 1/16 of the frequency axis, i.e. of
    #: an assumed-empty band) lands at -122.6 dB, but the broadband noise
    #: floor is 10 dB louder than that band -- the median of the whole
    #: distribution is -113 dB, the 75th percentile -101.6 and the 90th
    #: -95.1.  Against a -122.6..-47.2 mapping that puts half the panel above
    #: 13 % of the ramp and a quarter of it above 28 %, which is the
    #: saturated mid-hue the panel is drowning in.  Anchoring the floor at
    #: the median instead sends that half to the near-black end where it
    #: belongs and leaves the ramp to the signal.
    LEVEL_FLOOR_MARGIN_DB = 3.0

    #: Cap on the number of samples the fit looks at.  A ten second view of
    #: one channel is about a million bins and `decibel()` over all of them
    #: costs tens of milliseconds; a strided read of a fifth of them moves
    #: none of the percentiles that matter.
    LEVEL_FIT_SAMPLES = 200_000

    def __init__(
        self, aspec, channel, browser, xwidth, color_map, show_cbars, show_powers
    ):
        super().__init__(aspec, channel, browser, xwidth)

        # axis:
        self.getAxis("bottom").showLabel(False)
        self.getAxis("bottom").setStyle(showValues=False)
        # no rotated 'frequency (kHz)' label: at 16 channels it runs straight
        # through the tick values.  The unit goes into the corner caption
        # instead, the axis only keeps the SI scaling.
        self.getAxis("left").set_si_unit("Hz")
        self._update_caption()

        # color bar: a slim legend on the right edge of the spectrogram, not
        # a control panel.  A 25 px bar plus a rotated "Power (dB)" title cost
        # more width than the tick numbers it exists to explain; the unit rides
        # on the top tick label instead.
        self.cbar = pg.ColorBarItem(
            colorMap=resolve_colormap(color_map),
            width=theme.COLORBAR_WIDTH,
            interactive=True,
            rounding=1,
            limits=(-200, 20),
            **theme.colorbar_pens(),
        )
        self.cbar.setVisible(show_cbars)
        theme.style_colorbar(self.cbar, slim=True, unit="dB")
        if hasattr(self.cbar, "sigLevelsChanged"):
            self.cbar.sigLevelsChanged.connect(self._cbar_levels_changed)
        self._levels_fitted = False  # set by fit_levels(), re-armed by setVisible()
        # Parented to self, so it is destroyed with the plot and can never
        # fire into a deleted C++ object:
        self._refit_timer = QTimer(self)
        self._refit_timer.setSingleShot(True)
        self._refit_timer.timeout.connect(self._refit_levels)
        # ColorBarItem is a PlotItem, so it comes with the same hidden QMenu
        # tree.  16 channels build 16 of them:
        self.cbar.setMenuEnabled(False)
        theme.strip_pg_menus(self.cbar)

        # how the images are smoothed on their way to the screen; the
        # browser pushes its own choice down through `set_smoothing` once
        # the items exist -- see `DataBrowser.set_spec_smoothing`:
        self.smoothing = smoothing.DEFAULT

        # power spectrum:
        self.spec_data = None
        self.powerax = PowerPlot(self.z() + self.y(), channel, browser)
        self.powerax.setVisible(show_powers)

        # filter handles:
        self.highpass_handle = None
        self.lowpass_handle = None
        if "filtered" in browser.data:
            self.highpass_cutoff = browser.data["filtered"].highpass_cutoff
            self.lowpass_cutoff = browser.data["filtered"].lowpass_cutoff
            self.highpass_handle = pg.InfiniteLine(angle=0, movable=True)
            self.highpass_handle.setPen(theme.handle_pen())
            self.highpass_handle.addMarker("o", position=0.75, size=6)
            self.highpass_handle.setZValue(100)
            self.highpass_handle.setValue(self.highpass_cutoff)
            self.highpass_handle.sigPositionChangeFinished.connect(
                self.highpass_changed
            )
            self.addItem(self.highpass_handle, ignoreBounds=True)
            self.lowpass_handle = pg.InfiniteLine(angle=0, movable=True)
            self.lowpass_handle.setPen(theme.handle_pen())
            self.lowpass_handle.addMarker("o", position=0.75, size=6)
            self.lowpass_handle.setZValue(100)
            self.lowpass_handle.setValue(self.lowpass_cutoff)
            self.lowpass_handle.sigPositionChangeFinished.connect(self.lowpass_changed)
            self.addItem(self.lowpass_handle, ignoreBounds=True)

        self.setVisible(browser.show_specs > 0)
        self.sigUpdateFilter.connect(browser.update_filter)

    # --- theme -----------------------------------------------------------

    def polish(self) -> None:
        super().polish()
        theme.style_colorbar(self.cbar, slim=True, unit="dB")
        self._update_cbar_ticks(*self.cbar.levels())
        self.powerax.polish()
        for handle in (self.highpass_handle, self.lowpass_handle):
            if handle is not None:
                handle.setPen(theme.handle_pen())

    def set_handles_movable(self, movable: bool) -> None:
        """Hand the filter cutoffs the mouse, or hand it back to the lane.

        A movable `pg.InfiniteLine` takes the press before the view box sees
        it, so a rubber-band drag that starts on a cutoff moves the cutoff
        instead of selecting anything.  Measured on an 8 kHz recording with
        the highpass parked at 2000 Hz, one drag from 2000 Hz to 3000 Hz in
        each of two lanes:

        =============  ================  ==============
        handle          region signals    cutoff after
        =============  ================  ==============
        movable         0                 3017 Hz
        not movable     1                 2000 Hz
        =============  ================  ==============

        The same drag started 500 Hz clear of the handle gave one region
        signal either way, so it is the handle and nothing else.

        Swallowing the drag is the right behaviour while the reader is
        filtering and the wrong one while they are labelling, and there is no
        way to tell those apart from the pixels -- a cutoff is a line across
        the middle of the lane, which is exactly where the labels go.  So
        `DataBrowser.set_region_mode` hands the pixels to whichever the
        reader is currently in.
        """
        self._handles_movable = bool(movable)
        self._apply_handle_state()

    def set_handles_visible(self, visible: bool) -> None:
        """Show or hide the two filter cutoff lines.

        They earn their place while the reader is filtering and they are two
        lines across every lane while the reader is looking at the picture.
        Measured on ``data/Gryllus_campestris.wav`` -- 1600x1000, traces
        off, both spectrogram lanes on screen, cutoffs at 2000 and 3500 Hz
        of a 96 kHz recording: hiding them changes 11537 of the window's
        1600000 pixels, all of them between rows 400 and 805 and columns 114
        and 1347, which is inside the two lanes, and moves no number at all.
        Showing them again is pixel for pixel the picture from before.

        A hidden handle is also made non-interactive, which is the whole
        reason this is not one `setVisible` loop.  `set_handles_movable`
        records what a movable `pg.InfiniteLine` costs: it takes the press
        before the view box sees it, so an *invisible* movable line
        swallows a rubber-band drag that starts on a frequency the reader
        cannot see a reason for.  That is strictly worse than the visible
        case, which at least explains itself.

        The two states are kept apart rather than folded together because
        they answer to different owners -- the region mode writes one, the
        reader's checkbox the other -- and either may write while the other
        is off.  `_apply_handle_state` is where they meet.
        """
        self._handles_shown = bool(visible)
        self._apply_handle_state()

    def _apply_handle_state(self) -> None:
        """Push visibility, and movability gated on it, to both handles."""
        for handle in (self.highpass_handle, self.lowpass_handle):
            if handle is None:
                continue
            handle.setVisible(self._handles_shown)
            handle.setMovable(self._handles_movable and self._handles_shown)

    # --- axis label -------------------------------------------------------

    def update_axis_label(self) -> None:
        """Label the left axis "frequency", the way the trace panel says
        "amplitude".

        Not the trace's amplitude unit, which is what this inherited from
        TimePlot: the y axis here is frequency, and a spectrogram's amplitude
        is its colour, carried by the colour bar in dB.

        Only when the axis is showing tick values.  In a dense stack it is
        collapsed to zero width and a rotated label would run straight
        through the ticks of a 34 px lane -- which is why the unit lives in
        the corner caption there; see :meth:`caption_text`.

        ``Hz`` is handed over as a real unit so pyqtgraph does the prefixing
        and the label tracks the zoom: "frequency (kHz)" over ticks of 20 and
        40, "frequency (Hz)" when zoomed into a narrow band.
        """
        axis = self.getAxis("left")
        if self._show_tick_values:
            axis.setLabel("frequency", "Hz", color=theme.token("fg.muted"))
        # No else: clearing it would drop the axis's labelUnits, and that is
        # what pyqtgraph's auto SI prefixing keys off.  The dense path never
        # sets a label in the first place, and YAxisItem.set_si_unit() keeps
        # the ticks scaled there without one.

    # --- caption ----------------------------------------------------------

    def caption_text(self) -> str:
        """Name what the panel draws: one channel, or the mean of several.

        An averaged panel must not claim to be a channel.  `MEAN 00-15` is
        as long as `CH 00` plus three glyphs and stays on the one line the
        caption has, and it names the channels that were *actually*
        averaged, so that a solo or a mute narrowing the set is visible in
        the picture rather than only in the rail that mean mode hides.
        `channel_range_label` keeps it to one line whatever the selection
        is; the toggle says the full set in the status bar, so the
        abbreviated form is never the only place it exists.
        """
        if self.mean_channels is None:
            text = f"CH {self.channel:02d}"
        else:
            text = f"MEAN {channel_range_label(self.mean_channels)}"
        if self._show_tick_values:
            # the axis says it; saying it twice is clutter
            return text
        unit = self.getAxis("left").si_unit_label()
        if unit:
            text += f"   frequency ({unit})"
        return text

    def set_mean_channels(self, channels) -> bool:
        """Draw the mean power over `channels`, or `None` for own channel.

        Pushed on to every `SpecItem` the panel holds, and the level fit is
        re-armed either way: the mean and a single channel share a noise
        floor but not a span, so the mapping fitted for one is wrong for the
        other by the whole top half of the ramp -- 30 dB of the 60 the mean
        needs, measured.  See `fit_levels`.
        """
        channels = None if channels is None else [int(c) for c in channels]
        if channels == self.mean_channels:
            return False
        self.mean_channels = channels
        for item in self.data_items:
            if isinstance(item, SpecItem):
                item.set_mean_channels(channels)
        self._levels_fitted = False
        self._update_caption()
        return True

    def set_smoothing(self, key) -> bool:
        """Push a smoothing choice on to every `SpecItem`; see `smoothing`.

        The level fit is re-armed whenever the choice changed, because a
        filter moves the top of the ramp and nothing else.  Measured on
        ``data/Gryllus_campestris.wav``, 10 s of channel 0 at nfft 256: the
        floor does not move at all -- the median shifts at most 1.2 dB and
        the 5 dB snap in `_level_range` swallows it -- while the loudest bin
        drops 5 dB under a Gaussian of one bin and 15 dB under two.  Left
        unfitted, a strongly smoothed panel is drawn against a ramp whose
        top nothing in the image reaches, and the top quarter of the colour
        map goes unused: the picture dims rather than smooths.

        The refit goes through the same zero-delay timer `setVisible()` uses
        and not through `_refit_pending`, which is a flag for *reacting to
        somebody else's* write and is read only inside `setZRange`.  Nothing
        writes the mapping when a dropdown changes, so a flag armed here
        would sit armed until something unrelated came along.
        """
        key = smoothing.resolve(key)
        if key == self.smoothing:
            return False
        self.smoothing = key
        for item in self.data_items:
            if isinstance(item, SpecItem):
                item.set_smoothing(key)
        self._levels_fitted = False
        if self.fits_levels():
            self._refit_timer.start(0)
        return True

    def _place_caption(self) -> None:
        prefix = self.getAxis("left").si_prefix
        if prefix != self._caption_prefix:
            self._caption_prefix = prefix
            self.channel_label.setText(self.caption_text())
        super()._place_caption()

    @staticmethod
    def can_render(height: float) -> bool:
        """Is there room for a readable spectrogram of that height?

        A 32 px stripe carries no information; the browser should leave the
        panel out and say so in the status bar instead of drawing it.
        Deliberately a static method, so it can be asked before the plot
        exists: `SpectrogramPlot.can_render(row_height)`.
        """
        return height >= theme.SPECTROGRAM_MIN_HEIGHT

    # --- data -------------------------------------------------------------

    def add_item(self, item, is_data=False):
        super().add_item(item, is_data)
        if is_data and isinstance(item, SpecItem):
            self.spec_data = item.data
            self.cbar.setImageItem(item)
            # TODO: this should go into the realm of PlotRanges:
            if self.highpass_handle is not None:
                self.highpass_handle.setBounds((item.data.ampl_min, item.data.ampl_max))
            if self.lowpass_handle is not None:
                self.lowpass_handle.setBounds((item.data.ampl_min, item.data.ampl_max))

    def update_plot(self):
        # Tell the image item what is actually on screen *before* it
        # redraws: it then uploads a padded crop at the widget's own
        # stride instead of the whole buffer.  It keeps its own
        # containment hysteresis, so calling this on every range change
        # is cheap and only re-uploads when the view leaves the pad.
        t0, t1 = self.getViewBox().viewRange()[0]
        for item in self.data_items:
            if hasattr(item, "set_view_range"):
                item.set_view_range(t0, t1)
        super().update_plot()
        block = self.visible_block()
        if block is None:
            return
        power = np.mean(block, axis=0)
        power = decibel(power)
        power[power < -200] = -200
        freqs = np.arange(len(power)) * self.spec_data.fresolution
        zeros = np.zeros(len(freqs)) - 200
        self.powerax.power_item.setData(power, freqs)
        self.powerax.zero_item.setData(zeros, freqs)
        if not self._levels_fitted:
            self.fit_levels(block)

    def visible_block(self):
        """The in-view slice of the spectrogram this panel draws, or None.

        The same reduction the image gets, so the power curve beside it and
        the level fit made from it are all statements about one picture.

        Never index `spec_data` outside this: a slice of a `BufferedArray`
        triggers `update_buffer()`, so an unclamped range would re-read from
        disk and re-transform inside a paint or a timer callback.
        """
        if self.spec_data is None:
            return None
        t0, t1 = self.getViewBox().viewRange()[0]
        i0 = max(0, int(t0 * self.spec_data.rate))
        i1 = max(int(t1 * self.spec_data.rate) - 1, i0 + 1)
        # the -1                          ^^^ is important to not move the
        # spectrogram buffer at end of data.
        if i1 > len(self.spec_data):
            i1 = len(self.spec_data)
            if i1 == i0:
                i0 = max(0, i1 - 1)
        if i1 <= i0:
            return None
        if self.mean_channels is None:
            return self.spec_data[i0:i1, self.channel, :]
        return channel_power(self.spec_data[i0:i1], self.mean_channels)

    # --- level mapping ----------------------------------------------------

    def setVisible(self, visible: bool) -> None:
        """Re-arm the level fit when the panel is shown.

        `DataBrowser.set_panels()` ends with `PlotRanges.set_powers()`, which
        replaces the level mapping with `estimate_noiselevels()`'s -- a floor
        read off the top 1/16 of the frequency axis, which measures 10 dB
        below the broadband noise floor and is exactly the mapping this class
        exists to correct.  That call lands *after* the `update_plots()` pass
        of the same function, so a fit made inline is overwritten.

        Arming a flag here and consuming it in `setZRange()` reacts to the
        overwrite itself rather than guessing at a delay, and nothing happens
        at all if `set_powers()` turns out to have nothing to say.  The refit
        is then handed to a zero-delay timer, because `setZRange()` runs from
        *inside* the writer's per-channel loop: refitting synchronously would
        set every channel and then be overwritten again for every channel the
        loop had not reached yet.

        It cannot ping-pong: `estimate_noiselevels()` latches itself off after
        its first successful call, and our own writes are excluded via
        `_applying_levels`.
        """
        rearm = bool(visible) and not self.isVisible()
        super().setVisible(visible)
        if rearm and self.fits_levels():
            self._levels_fitted = False
            self._refit_pending = True

    def fits_levels(self) -> bool:
        """Is this the panel that owns the shared colour mapping?

        The *first lane drawn* owns it, which on an untouched stack is
        channel 0 and was until now spelt that way.  Solo and mute can take
        channel 0 off the screen, and a rule that named it anyway left the
        whole stack on whatever ramp was last in force with no panel able to
        replace it -- soloing electrodes 3 and 5 of the array is an ordinary
        gesture, not a corner.

        It also puts the mean panel and the level owner on the same lane by
        construction: mean mode draws on the first visible channel, so the
        panel that has the averaged block in its hands is the one allowed to
        fit a ramp to it, entering and leaving alike.

        Falls back to channel 0 before the browser has opened a file: this is
        reachable from `pg.PlotItem`'s constructor, ahead of `self.browser`.
        """
        browser = getattr(self, "browser", None)
        channels = browser.visible_channels() if browser is not None else []
        if not channels:
            return self.channel == 0
        return self.channel == channels[0]

    def _refit_levels(self) -> None:
        """Deferred half of the refit armed by `setVisible()`."""
        if not self.isVisible():
            return
        self._levels_fitted = False
        self.fit_levels()

    def fit_levels(self, block=None) -> bool:
        """Map the broadband noise floor to the dark end of the colormap.

        The floor is the *median* of the in-view power distribution plus
        `LEVEL_FLOOR_MARGIN_DB`, so that by construction half of the panel is
        at or below the darkest colour and the ramp is spent on what is
        actually above the noise.  The top follows the same rule the existing
        estimator uses -- 95 % of the way from the floor to the loudest bin --
        so switching between the two does not change the apparent gain.

        Runs from one panel only -- see `fits_levels` -- because all
        channels of one recording share the mapping, so that a quiet
        electrode still *looks* quieter than a loud one rather than being
        normalised into looking the same.

        A mean panel has to make its own.  Averaging the array pushes the
        noise floor down while a signal coherent across it stays put, so the
        mean has close to twice the contrast of any one electrode: measured
        on the flona block, 99.9th percentile minus median is 23.50 dB for
        channel 0 and 46.25 dB for the mean.  The fitted ramp follows --
        -75..-45 dB for the channel, -75..-15 dB for the mean, the same
        floor and twice the span.  A mean drawn against a channel's mapping
        is still a spectrogram, just a flatter one, which is why entering
        and leaving mean mode re-arms this rather than trusting the ramp
        that is already there.

        Returns True when a new mapping was applied.
        """
        if self.spec_data is None or not self.fits_levels():
            return False
        if block is None:
            block = self.visible_block()
        if block is None:
            return False
        levels = self._level_range(block)
        if levels is None:
            return False
        self._levels_fitted = True
        self._apply_levels(*levels)
        return True

    def _level_range(self, block) -> tuple[float, float] | None:
        """Level range fitted to *block*, snapped to 5 dB, or None if too small.

        Fitted to the numbers that are **drawn**, which under a filtering
        smoothing are not the numbers that were measured: the loudest bin of
        the Gryllus block drops 5 dB under a Gaussian of one bin and 15 dB
        under two, so a ramp fitted to the raw block tops out above anything
        in the image and the panel dims instead of smoothing.  The floor is
        indifferent -- the median moves at most 1.2 dB and the 5 dB snap
        below swallows it -- but the top is not.

        Two costs and one subtlety keep the plain path exactly as it was
        when nothing is filtering.  The costs: `decibel` has to run over the
        whole block rather than over the strided fifth of it this samples,
        and then the filter runs too -- about 13 ms together on a 1500 px
        lane, on the one panel that `fits_levels`.  The subtlety: `smoothing`
        pulls the non-finite bins to `smoothing.FLOOR_DB` so that one dead
        electrode cannot smear a ``-inf`` across its neighbours, and those
        bins would then survive the `isfinite` filter below and drag the
        median to the floor.  So the mask is taken from the *raw* dB, which
        selects exactly the bins the plain path selects.

        Transposed to `(frequency, time)` before filtering, which is the
        orientation `SpecItem` uploads in.  Every filter on offer today is
        isotropic and would not notice, but one that is not would otherwise
        be fitted along one axis and drawn along the other.
        """
        if smoothing.changes_values(self.smoothing):
            with np.errstate(all="ignore"):
                raw = decibel(np.asarray(block, dtype=float)).T
            values = smoothing.smooth(raw, self.smoothing)[np.isfinite(raw)]
            if values.size > self.LEVEL_FIT_SAMPLES:
                values = values[:: 1 + values.size // self.LEVEL_FIT_SAMPLES]
            return self._fit_levels_to(values)
        values = np.asarray(block, dtype=float).reshape(-1)
        if values.size > self.LEVEL_FIT_SAMPLES:
            values = values[:: 1 + values.size // self.LEVEL_FIT_SAMPLES]
        with np.errstate(all="ignore"):
            values = decibel(values)
        values = values[np.isfinite(values)]
        return self._fit_levels_to(values)

    def _fit_levels_to(self, values) -> tuple[float, float] | None:
        """The fit itself, over dB values that are already sampled and clean."""
        if values.size < 64:
            return None
        floor = float(np.median(values)) + self.LEVEL_FLOOR_MARGIN_DB
        peak = float(np.max(values))
        zmin, zmax, _ = self.range(self.z())
        # snap to 5 dB, so the three colour bar labels are round numbers and
        # do not jitter as the buffer scrolls:
        lo = max(float(zmin), 5.0 * np.floor(floor / 5.0))
        hi = min(float(zmax), 5.0 * np.ceil((lo + 0.95 * (peak - lo)) / 5.0))
        if hi - lo < self.MIN_LEVEL_SPAN_DB:
            hi = min(float(zmax), lo + self.MIN_LEVEL_SPAN_DB)
        if hi - lo > self.MAX_LEVEL_SPAN_DB:
            lo = hi - self.MAX_LEVEL_SPAN_DB
        if hi <= lo:
            return None
        return float(lo), float(hi)

    def _apply_levels(self, zmin: float, zmax: float) -> None:
        """Apply a level range to every channel, through PlotRanges if we can.

        Going through the shared range keeps the browser's idea of the z range
        in step with what the images are actually showing; without it the next
        level command would snap back to the constant the range still holds.
        """
        self._applying_levels = True
        try:
            ranges = getattr(self.browser, "plot_ranges", None)
            prange = ranges.get(self.z()) if ranges is not None else None
            if prange is None or getattr(prange, "user_locked", False):
                self.setZRange(zmin, zmax)
                return
            if prange.rmin is None or zmin < prange.rmin:
                prange.rmin = zmin
            if prange.rmax is None or zmax > prange.rmax:
                prange.rmax = zmax
            prange.set_ranges(zmin, zmax)
        finally:
            self._applying_levels = False

    def _update_cbar_ticks(self, zmin, zmax) -> None:
        """Three mono tick labels on the slim colour bar, unit on the top one."""
        if zmin is None or zmax is None:
            return
        for name in ("right", "left"):
            if name in getattr(self.cbar, "axes", {}):
                self.cbar.getAxis(name).setTicks(theme.colorbar_ticks(zmin, zmax))

    def _cbar_levels_changed(self, *args) -> None:
        """Relabel the colour bar after an interactive drag of its handles.

        Deliberately does *not* touch `_levels_fitted`: ColorBarItem emits this
        for programmatic `setLevels()` too, so using it as "the user changed
        the levels" would latch on the range machinery's own start-up call and
        the automatic fit would never run.
        """
        self._update_cbar_ticks(*self.cbar.levels())

    def range(self, axspec):
        if axspec == self.x():
            return super().range(axspec)
        elif axspec == self.y():
            return super().range(axspec)
        elif axspec == self.z():
            if self.y() == Panel.frequencies[1]:
                return -80, 0, 5
            else:
                return -200, 20, 5

    def amplitudes(self, t0, t1):
        amin, amax, astep = self.range(self.y())
        return amin, amax

    def setZRange(self, zmin, zmax):
        for item in self.data_items:
            if hasattr(item, "setLevels"):
                item.setLevels((zmin, zmax), update=True)
        self.cbar.setLevels((zmin, zmax))
        self._update_cbar_ticks(zmin, zmax)
        if self._refit_pending and not self._applying_levels:
            # someone else just replaced the mapping - see setVisible()
            self._refit_pending = False
            self._levels_fitted = False
            self._refit_timer.start(0)

    def get_marker_pos(self, x, dx, y, dy):
        for item in reversed(self.data_items):
            if item.isVisible() and isinstance(item, SpecItem):
                z = item.get_power(x, y)
                return x, y, z
        return x, y, None

    # --- filter handles ---------------------------------------------------

    def set_filter_handles(self, highpass_cutoff=None, lowpass_cutoff=None):
        if highpass_cutoff is not None:
            self.highpass_cutoff = highpass_cutoff
            self.highpass_handle.setValue(self.highpass_cutoff)
        if lowpass_cutoff is not None:
            self.lowpass_cutoff = lowpass_cutoff
            self.lowpass_handle.setValue(self.lowpass_cutoff)

    def highpass_changed(self):
        self.highpass_cutoff = self.highpass_handle.value()
        self.sigUpdateFilter.emit(self.highpass_cutoff, self.lowpass_cutoff)

    def lowpass_changed(self):
        self.lowpass_cutoff = self.lowpass_handle.value()
        self.sigUpdateFilter.emit(self.highpass_cutoff, self.lowpass_cutoff)
