"""Manage ranges of plot axes.

`class PlotRange`: a single axis range
`class PlotRanges`: manage all ranges
"""

import numpy as np

from math import ceil, log
from functools import partial

from .panels import Panel


#: The widest stretch of a recording the trace and spectrogram lanes will show
#: at once, in seconds.
#:
#: Zooming out is one keystroke to ask for and a great deal of work to answer:
#: every lane reads its whole span out of the buffer and every spectrogram is
#: recomputed over it, so pulling the window out to an hour on a sixteen
#: channel recording asks for sixteen hours of spectrogram in one gesture.  It
#: is easy to do by accident -- a key held a moment too long -- and until it
#: finishes the application is simply unresponsive, with nothing on screen to
#: say why.
#:
#: Five minutes is the reader's own number, and their own measure of it is
#: that sixteen channels with a spectrogram each is already demanding at that
#: width on a small laptop.
#:
#: This caps the WINDOW and not the recording.  `rmax` stays the end of the
#: file, so every part of it is still reachable by panning, `end` and `home`;
#: and the navigator is not one of these ranges, so the overview still draws
#: the whole recording at once.
MAX_TIME_WINDOW_S = 300.0


class PlotRange(object):
    def __init__(self, axspec, nchannels):
        self.axspec = axspec
        self.rmin = None
        self.rmax = None
        self.rstep = None
        self.min_dr = None
        #: Upper end this range *opens* at, when that is not `rmax`.
        #:
        #: `rmax` is the hard ceiling: `set_ranges` clips to it and
        #: `set_limits` hands it to `setLimits`, so a range can never be
        #: panned or zoomed past it.  That makes it the wrong place to put
        #: a preference like "show 0-2 kHz of a 24 kHz spectrogram" -- the
        #: reader would lose Nyquist rather than merely start below it.
        #:
        #: So the opening span is kept apart from the limit, which is a
        #: distinction this class already draws for time: a recording an
        #: hour long is clipped to an hour and opens at ten seconds (see
        #: `set_limits`).  This is the same idea for frequency, and None
        #: means "open at the limit", which is what every range did before.
        self.rdefault = None
        #: Lower end this range *opens* at, when that is not `rmin`.  The
        #: mirror of `rdefault`, and a limit no more than it is: a band of
        #: 500-2000 Hz still pans and zooms down to 0.
        self.rdefault_min = None
        # set as soon as the user zooms this range by hand.  An automatic
        # fit must never fight a deliberate zoom:
        self.user_locked = False
        self._zoom_views = set()
        self.r0 = [None] * nchannels
        self.r1 = [None] * nchannels
        self.axxs = [[] for i in range(nchannels)]
        self.axys = [[] for i in range(nchannels)]
        self.axzs = [[] for i in range(nchannels)]
        self.marker_channel = None
        self.marker_ax = None
        self.marker_pos = None
        self.stored_marker_channel = None
        self.stored_marker_ax = None
        self.stored_marker_pos = None

    def __str__(self):
        rmins = f"{'-':>8}" if self.rmin is None else f"{self.rmin:8.5g}"
        rmaxs = f"{'-':>8}" if self.rmax is None else f"{self.rmax:8.5g}"
        rsteps = f"{'-':>8}" if self.rstep is None else f"{self.rstep:8.5g}"
        mindrs = f"{'-':>8}" if self.min_dr is None else f"{self.min_dr:8.3g}"
        r0s = f"{'-':>8}" if self.r0[0] is None else f"{self.r0[0]:8.5g}"
        r1s = f"{'-':>8}" if self.r1[0] is None else f"{self.r1[0]:8.5g}"
        return f"{self.axspec}: rmin={rmins} rmax={rmaxs} rstep={rsteps} min_dr={mindrs} r0={r0s} r1={r1s}"

    def _add_axis(self, axs, ax):
        rmin, rmax, rstep = ax.range(self.axspec)
        if rmin is not None and (self.rmin is None or rmin < self.rmin):
            self.rmin = rmin
        if rmax is not None and (self.rmax is None or rmax > self.rmax):
            self.rmax = rmax
        if rstep is not None and (self.rstep is None or rstep < self.rstep):
            self.rstep = rstep
        axs.append(ax)

    def add_xaxis(self, ax, channel):
        self._add_axis(self.axxs[channel], ax)

    def add_yaxis(self, ax, channel):
        self._add_axis(self.axys[channel], ax)
        self._watch_user_zoom(ax)

    def _watch_user_zoom(self, ax):
        view = ax.getViewBox() if hasattr(ax, "getViewBox") else None
        if view is None or not hasattr(view, "sigUserZoomed"):
            return
        if view in self._zoom_views:
            return
        self._zoom_views.add(view)
        view.sigUserZoomed.connect(self._user_zoomed)

    def _user_zoomed(self, xzoom, yzoom):
        if yzoom:
            self.user_locked = True

    def set_user_locked(self, locked=True):
        """Lock or unlock this range against automatic fits."""
        self.user_locked = bool(locked)

    def add_zaxis(self, ax, channel):
        self._add_axis(self.axzs[channel], ax)

    def is_used(self):
        n = 0
        for axx in self.axxs:
            n += len(axx)
        for axy in self.axys:
            n += len(axy)
        for axz in self.axzs:
            n += len(axz)
        return n > 0

    def is_time(self):
        return self.axspec in Panel.times

    def is_amplitude(self):
        return self.axspec in Panel.amplitudes

    def is_frequency(self):
        return self.axspec in Panel.frequencies

    def is_power(self):
        return self.axspec in Panel.powers

    def default_max(self):
        """The upper end this range opens at, which is not always its limit.

        `rdefault` when one was set and it is below the limit, `rmax`
        otherwise.  Clamped rather than trusted: a preference file outlives
        the recording it was written beside, and a 2 kHz ceiling asked of a
        recording whose Nyquist is 1 kHz would otherwise open the lane on a
        band that does not exist.
        """
        if self.rmax is None:
            return self.rmax
        if self.rdefault is None or not np.isfinite(self.rdefault):
            return self.rmax
        if not np.isfinite(self.rmax):
            return self.rdefault
        return min(self.rdefault, self.rmax)

    def default_min(self):
        """The lower end this range opens at, which is not always its limit.

        Clamped into `rmin`..`default_max()` rather than trusted, for the
        reason `default_max` is: a preference outlives the recording it was
        written beside.  A floor at or above the ceiling would open the lane
        on nothing at all, so it loses and the range opens at `rmin`.
        """
        if self.rmin is None:
            return self.rmin
        if self.rdefault_min is None or not np.isfinite(self.rdefault_min):
            return self.rmin
        floor = max(self.rdefault_min, self.rmin)
        ceiling = self.default_max()
        if ceiling is not None and np.isfinite(ceiling) and floor >= ceiling:
            return self.rmin
        return floor

    def max_dr(self):
        """The widest span this range may show at once, or None if unknown.

        The whole range for everything but time, which is what `set_limits`
        handed `setLimits` before this existed.  Time is capped at
        `MAX_TIME_WINDOW_S`, or at the recording's own length when that is
        shorter -- a two minute recording is still shown whole, and nothing
        changes for one.
        """
        if self.rmin is None or self.rmax is None:
            return None
        if not (np.isfinite(self.rmin) and np.isfinite(self.rmax)):
            return None
        span = self.rmax - self.rmin
        return min(span, MAX_TIME_WINDOW_S) if self.is_time() else span

    def set_default_max(self, rdefault):
        """Set the upper end this range opens at.  None restores the limit."""
        self.rdefault = rdefault

    def set_default_min(self, rdefault_min):
        """Set the lower end this range opens at.  None restores the limit."""
        self.rdefault_min = rdefault_min

    def set_starttime(self, mode):
        for axx in self.axxs:
            for ax in axx:
                ax.set_starttime(mode)

    def at_end(self, channel=0):
        return self.r1[channel] >= self.rmax

    def at_home(self, channel=0):
        return self.r0[channel] <= self.rmin

    def set_limits(self):
        if not self.is_used():
            return
        if np.isfinite(self.rmin) and np.isfinite(self.rmax):
            # TODO: min_dr should eventually come from the data!!!
            if self.is_time():
                self.min_dr = 0.001
            else:
                self.min_dr = (self.rmax - self.rmin) / 2**16
        else:
            self.min_dr = 2 / 2**16
        # limits:
        for axx in self.axxs:
            for ax in axx:
                if np.isfinite(self.rmin):
                    ax.setLimits(xMin=self.rmin)
                if np.isfinite(self.rmax):
                    ax.setLimits(xMax=self.rmax)
                if np.isfinite(self.rmin) and np.isfinite(self.rmax):
                    ax.setLimits(minXRange=self.min_dr, maxXRange=self.max_dr())
        for axy in self.axys:
            for ax in axy:
                if np.isfinite(self.rmin):
                    ax.setLimits(yMin=self.rmin)
                if np.isfinite(self.rmax):
                    ax.setLimits(yMax=self.rmax)
                if np.isfinite(self.rmin) and np.isfinite(self.rmax):
                    ax.setLimits(minYRange=self.min_dr, maxYRange=self.max_dr())
        # ranges:
        #
        # What the range OPENS at, which the loop above has just established
        # is not the same question as what it is clipped to.  Time has always
        # opened at ten seconds of a recording clipped to its whole length;
        # `default_max` lets a frequency range open at a band of a
        # spectrogram clipped to Nyquist, and answers `rmax` for every range
        # that was given no preference.
        for c in range(len(self.r0)):
            self.r0[c] = self.rmin if self.is_time() else self.default_min()
            if self.is_time():
                self.r1[c] = 10
            else:
                self.r1[c] = self.default_max()
            if not np.isfinite(self.r0[c]):
                self.r0[c] = -1
            if not np.isfinite(self.r1[c]):
                self.r1[c] = +1

    def set_ranges(self, r0=None, r1=None, dr=None, channels=None, do_set=True):
        if not self.is_used():
            return
        # time ranges are all the same over all the channels!
        if channels is None or self.is_time():
            channels = range(len(self.r0))
        cc = -1
        for c in channels:
            if len(self.axxs[c]) + len(self.axys[c]) + len(self.axzs[c]) == 0:
                continue
            if cc >= 0:
                self.r0[c] = self.r0[cc]
                self.r1[c] = self.r1[cc]
            else:
                if r0 is not None:
                    self.r0[c] = r0
                if r1 is not None:
                    self.r1[c] = r1
                if dr is not None:
                    if r1 is None:
                        self.r1[c] = self.r0[c] + dr
                    else:
                        self.r0[c] = self.r1[c] - dr
                dr = self.r1[c] - self.r0[c]
                # Every widening gesture arrives here -- the zoom keys, the
                # navigator, `reset`, `set_times` from a plugin -- so the cap
                # goes here rather than in each of them.  The left edge is
                # kept, which is what makes it stable: an anchor that moved
                # would pan the view a little further on every press of a key
                # that is already doing nothing.
                cap = self.max_dr() if self.is_time() else None
                if cap is not None and dr > cap:
                    self.r1[c] = self.r0[c] + cap
                    dr = cap
                if self.r0[c] < self.rmin:
                    self.r0[c] = self.rmin
                    self.r1[c] = self.rmin + dr
                if self.r1[c] > self.rmax and not self.is_time():
                    self.r1[c] = self.rmax
                    self.r0[c] = self.rmax - dr
                if self.r0[c] < self.rmin:
                    self.r0[c] = self.rmin
                if self.is_time():
                    cc = c
            if do_set:
                for ax in self.axxs[c]:
                    ax.setXRange(self.r0[c], self.r1[c])
                for ax in self.axys[c]:
                    ax.setYRange(self.r0[c], self.r1[c])
                for ax in self.axzs[c]:
                    ax.setZRange(self.r0[c], self.r1[c])

    def zoom_in(self, channels=None, do_set=True):
        if not self.is_used():
            return
        if channels is None:
            channels = range(len(self.r0))
        if self.is_time():
            channels = [0]
        for c in channels:
            if self.rmin < 0:
                h = 0.25 * (self.r1[c] - self.r0[c])
                m = 0.5 * (self.r1[c] + self.r0[c])
                if 4 * h > self.min_dr:
                    self.set_ranges(m - h, m + h, None, [c], do_set)
            else:
                dr = self.r1[c] - self.r0[c]
                if dr > self.min_dr:
                    dr *= 0.5
                    self.set_ranges(self.r0[c], None, dr, [c], do_set)

    def zoom_out(self, channels=None, do_set=True):
        if not self.is_used():
            return
        if channels is None:
            channels = range(len(self.r0))
        if self.is_time():
            channels = [0]
        for c in channels:
            if self.rmin < 0:
                h = self.r1[c] - self.r0[c]
                m = 0.5 * (self.r1[c] + self.r0[c])
                self.set_ranges(m - h, m + h, None, [c], do_set)
            else:
                dr = 2 * (self.r1[c] - self.r0[c])
                self.set_ranges(self.r0[c], None, dr, [c], do_set)

    def zoom_in_centered(self, channels=None, do_set=True):
        if not self.is_used():
            return
        if channels is None:
            channels = range(len(self.r0))
        if self.is_time():
            channels = [0]
        for c in channels:
            h = 0.25 * (self.r1[c] - self.r0[c])
            m = 0.5 * (self.r1[c] + self.r0[c])
            if 4 * h > self.min_dr:
                self.set_ranges(m - h, m + h, None, [c], do_set)

    def zoom_out_centered(self, channels=None, do_set=True):
        if not self.is_used():
            return
        if channels is None:
            channels = range(len(self.r0))
        if self.is_time():
            channels = [0]
        for c in channels:
            h = self.r1[c] - self.r0[c]
            m = 0.5 * (self.r1[c] + self.r0[c])
            # Clamped about the centre here, rather than left to the backstop
            # in `set_ranges`, which keeps the left edge: keeping the left edge
            # of a range that was asked for by its centre walks the window off
            # to the left a little further on every press.
            cap = self.max_dr() if self.is_time() else None
            if cap is not None:
                h = min(h, 0.5 * cap)
            self.set_ranges(m - h, m + h, None, [c], do_set)

    def goto(self, pos, channels=None, do_set=True):
        if not self.is_used():
            return
        if channels is None:
            channels = range(len(self.r0))
        if self.is_time():
            channels = [0]
        for c in channels:
            if self.r0[c] != pos:
                dr = self.r1[c] - self.r0[c]
                self.set_ranges(pos, pos + dr, None, [c], do_set)

    def move(self, move_fac, channels=None, do_set=True):
        if not self.is_used():
            return
        if channels is None:
            channels = range(len(self.r0))
        if self.is_time():
            channels = [0]
        for c in channels:
            if (move_fac > 0 and self.r1[c] < self.rmax) or (
                move_fac < 0 and self.r0[c] > self.rmin
            ):
                dr = self.r1[c] - self.r0[c]
                self.set_ranges(
                    self.r0[c] + move_fac * dr,
                    self.r1[c] + move_fac * dr,
                    None,
                    [c],
                    do_set,
                )

    def down(self, channels=None, do_set=True):
        self.move(-0.5, channels, do_set)

    def up(self, channels=None, do_set=True):
        self.move(+0.5, channels, do_set)

    def small_down(self, channels=None, do_set=True):
        self.move(-0.05, channels, do_set)

    def small_up(self, channels=None, do_set=True):
        self.move(+0.05, channels, do_set)

    def step(self, step_fac, channels=None, do_set=True):
        if not self.is_used():
            return
        if channels is None:
            channels = range(len(self.r0))
        if self.is_time():
            channels = [0]
        for c in channels:
            if (step_fac > 0 and self.r1[c] < self.rmax) or (
                step_fac < 0 and self.r0[c] > self.rmin
            ):
                self.set_ranges(
                    self.r0[c] + step_fac * self.rstep,
                    self.r1[c] + step_fac * self.rstep,
                    None,
                    [c],
                    do_set,
                )

    def step_down(self, channels=None, do_set=True):
        self.step(-1, channels, do_set)

    def step_up(self, channels=None, do_set=True):
        self.step(+1, channels, do_set)

    def min_step(self, step_fac, channels=None, do_set=True):
        if not self.is_used():
            return
        if channels is None:
            channels = range(len(self.r0))
        if self.is_time():
            channels = [0]
        for c in channels:
            if (step_fac > 0 and self.r0[c] < self.r1[c]) or (
                step_fac < 0 and self.r0[c] > self.rmin
            ):
                self.set_ranges(
                    self.r0[c] + step_fac * self.rstep, self.r1[c], None, [c], do_set
                )

    def min_down(self, channels=None, do_set=True):
        self.min_step(-1, channels, do_set)

    def min_up(self, channels=None, do_set=True):
        self.min_step(+1, channels, do_set)

    def max_step(self, step_fac, channels=None, do_set=True):
        if not self.is_used():
            return
        if channels is None:
            channels = range(len(self.r0))
        if self.is_time():
            channels = [0]
        for c in channels:
            if (step_fac > 0 and self.r1[c] < self.rmax) or (
                step_fac < 0 and self.r1[c] > self.r0[c]
            ):
                self.set_ranges(
                    self.r0[c], self.r1[c] + step_fac * self.rstep, None, [c], do_set
                )

    def max_down(self, channels=None, do_set=True):
        self.max_step(-1, channels, do_set)

    def max_up(self, channels=None, do_set=True):
        self.max_step(+1, channels, do_set)

    def home(self, channels=None, do_set=True):
        if not self.is_used():
            return
        if channels is None:
            channels = range(len(self.r0))
        if self.is_time():
            channels = [0]
        for c in channels:
            if self.r0[c] > self.rmin:
                dr = self.r1[c] - self.r0[c]
                self.set_ranges(self.rmin, None, dr, [c], do_set)

    def end(self, channels=None, do_set=True):
        if not self.is_used():
            return
        if channels is None:
            channels = range(len(self.r0))
        if self.is_time():
            channels = [0]
        for c in channels:
            if self.r1[c] < self.rmax:
                dr = self.r1[c] - self.r0[c]
                r1 = ceil(self.rmax / (0.5 * dr)) * (0.5 * dr)
                self.set_ranges(None, r1, dr, [c], do_set)
        """
        Former time range:
        n2 = np.floor(self.tmax / (0.5*self.twindow))
        toffs = max(0, n2-1)  * 0.5*self.twindow
        if self.toffset < toffs:
            self.toffset = toffs
            return True
        return False

        """

    def snap(self, channels=None, do_set=True):
        if not self.is_used():
            return
        if channels is None:
            channels = range(len(self.r0))
        if self.is_time():
            channels = [0]
        for c in channels:
            dr = self.r1[c] - self.r0[c]
            dr = 10 * 2 ** round(log(dr / 10) / log(2))
            r0 = round(self.r0[c] / (dr / 2)) * (dr / 2)
            self.set_ranges(r0, None, dr, [c], do_set)

    def auto(
        self, t0, t1, channels=None, do_set=True, headroom=0.08, respect_lock=False
    ):
        """Fit the range to the data between t0 and t1.

        Parameters
        ----------
        t0: float
            Start of the time range the data are taken from.
        t1: float
            End of the time range the data are taken from.
        channels: list of int or None
            Channels to fit.  All channels if None.
        do_set: bool
            Apply the new range to the plots.
        headroom: float
            Fraction of the fitted range added on both sides, so that peaks
            do not touch the frame.
        respect_lock: bool
            Skip ranges the user zoomed by hand.  An explicit auto-fit
            (respect_lock=False) clears that lock again.
        """
        if not self.is_used() or self.is_time():
            return
        if respect_lock and self.user_locked:
            return
        if channels is None:
            channels = range(len(self.r0))
        rmin = None
        rmax = None
        for c in channels:
            for ax in self.axxs[c] + self.axys[c]:
                r0, r1 = ax.amplitudes(t0, t1)
                if r0 is None or r1 is None:
                    continue
                if rmin is None or r0 < rmin:
                    rmin = r0
                if rmax is None or r1 > rmax:
                    rmax = r1
        if rmin is None or rmax is None:
            return
        dr = rmax - rmin
        if dr <= 0:
            # silence: keep a symmetric window around the constant value
            dr = self.min_dr if self.min_dr else 1e-8
            rmin -= 0.5 * dr
            rmax += 0.5 * dr
            dr = rmax - rmin
        rmin -= headroom * dr
        rmax += headroom * dr
        if self.min_dr is not None and rmax - rmin < self.min_dr:
            center = 0.5 * (rmax + rmin)
            rmin = center - 0.5 * self.min_dr
            rmax = center + 0.5 * self.min_dr
        if self.rmin is not None and np.isfinite(self.rmin):
            rmin = max(rmin, self.rmin)
        if self.rmax is not None and np.isfinite(self.rmax):
            rmax = min(rmax, self.rmax)
        self.set_ranges(rmin, rmax, None, channels, do_set)
        if not respect_lock:
            self.user_locked = False

    def reset(self, channels=None, do_set=True):
        if not self.is_used():
            return
        self.user_locked = False
        rmin = self.rmin
        if not np.isfinite(rmin):
            rmin = -1
        rmax = self.rmax
        if not np.isfinite(rmax):
            rmax = +1
        self.set_ranges(rmin, rmax, None, channels, do_set)

    def default_view(self, channels=None, do_set=True):
        """Back to the span this range opened at.

        `reset` goes all the way out to the limits; this goes back to
        `default_min`..`default_max`, which is the same thing for every
        range that was given no preference and is the preferred band for
        one that was.

        The two are kept apart rather than folded together because the
        reader needs both: a spectrogram that opens at 0-2 kHz has to have
        a way back to Nyquist, and that way is `reset`.
        """
        if not self.is_used():
            return
        self.user_locked = False
        rmin = self.default_min()
        if rmin is None or not np.isfinite(rmin):
            rmin = -1
        rmax = self.default_max()
        if rmax is None or not np.isfinite(rmax):
            rmax = +1
        self.set_ranges(rmin, rmax, None, channels, do_set)

    def center(self, channels=None, do_set=True):
        if not self.is_used() or self.is_time():
            return
        if channels is None:
            channels = range(len(self.r0))
        for c in channels:
            r = max(np.abs(self.r0[c]), np.abs(self.r1[c]))
            self.set_ranges(-r, +r, None, [c], do_set)

    def set_powers(self):
        """Start the colour ramp off at what the data suggests.

        Each item is asked for its own levels rather than being looked up by
        channel index: an item drawing the mean over the array is not
        channel `c`, and a per-channel answer would leave its ramp short of
        the span the mean actually has.
        """
        if not self.is_power() or not self.is_used():
            return
        zmin = None
        zmax = None
        for c in range(len(self.axzs)):
            for ax in self.axzs[c]:
                if hasattr(ax, "data_items"):
                    for item in ax.data_items:
                        if hasattr(item, "noise_levels"):
                            z0, z1 = item.noise_levels()
                            if z0 is not None and z1 is not None:
                                if zmin is None or z0 < zmin:
                                    zmin = z0
                                if zmax is None or z1 > zmax:
                                    zmax = z1
        if zmin is not None and zmax is not None:
            self.set_ranges(zmin, zmax)

    def clear_marker(self):
        self.marker_channel = None
        self.marker_ax = None
        self.marker_pos = None

    def set_marker(self, channel, ax, pos):
        self.marker_channel = channel
        self.marker_ax = ax
        self.marker_pos = pos

    def store_marker(self):
        self.stored_marker_channel = self.marker_channel
        self.stored_marker_ax = self.marker_ax
        self.stored_marker_pos = self.marker_pos
        if self.stored_marker_channel is None:
            return None, None, None
        for ax in self.axxs[self.stored_marker_channel]:
            if ax is self.stored_marker_ax:
                return self.stored_marker_ax, self.stored_marker_pos, None
        for ax in self.axys[self.stored_marker_channel]:
            if ax is self.stored_marker_ax:
                return self.stored_marker_ax, None, self.stored_marker_pos
        return None, None, None

    def clear_stored_marker(self):
        for axx in self.axxs:
            for ax in axx:
                ax.stored_marker.setVisible(False)
        for axy in self.axys:
            for ax in axy:
                ax.stored_marker.setVisible(False)
        self.stored_marker_channel = None
        self.stored_marker_ax = None
        self.stored_marker_pos = None

    def update_crosshair(self):
        for axx in self.axxs:
            for ax in axx:
                if self.marker_pos is not None:
                    ax.xline.setPos(self.marker_pos)
                ax.xline.setVisible(self.marker_pos is not None)
        for axy in self.axys:
            for ax in axy:
                if self.marker_pos is not None:
                    ax.yline.setPos(self.marker_pos)
                ax.yline.setVisible(self.marker_pos is not None)


class PlotRanges(dict):
    def __init__(self):
        super().__init__()
        for m in [
            "zoom_in",
            "zoom_out",
            "zoom_in_centered",
            "zoom_out_centered",
            "down",
            "up",
            "small_down",
            "small_up",
            "step_down",
            "step_up",
            "min_down",
            "min_up",
            "max_down",
            "max_up",
            "home",
            "end",
            "snap",
            "auto",
            "reset",
            "default_view",
            "center",
        ]:
            setattr(self, m, partial(PlotRanges._apply, self, m))

    def __str__(self):
        s = []
        for r in self.values():
            s.append(str(r))
        return "\n".join(s)

    def setup(self, nchannels):
        for s in Panel.times + Panel.amplitudes + Panel.frequencies + Panel.powers:
            self[s] = PlotRange(s, nchannels)

    def add_plot(self, ax):
        self[ax.x()].add_xaxis(ax, ax.channel)
        self[ax.y()].add_yaxis(ax, ax.channel)
        if ax.z():
            self[ax.z()].add_zaxis(ax, ax.channel)

    def set_limits(self):
        for r in self.values():
            r.set_limits()

    def set_ranges(self):
        for r in self.values():
            r.set_ranges()

    def set_powers(self):
        for r in self.values():
            r.set_powers()

    def auto_fit(
        self, t0, t1, channels=None, respect_lock=True, do_set=True, headroom=0.08
    ):
        """Fit all amplitude ranges to the data between t0 and t1.

        This is what turns a signal that peaks at 7% of the file format's
        full scale into a signal that fills its panel.  Call it on load and
        whenever the time range changed.

        Ranges the user zoomed by hand are left alone unless respect_lock
        is False.
        """
        for s in Panel.amplitudes:
            r = self.get(s)
            if r is None:
                continue
            r.auto(t0, t1, channels, do_set, headroom, respect_lock)

    def clear_user_locks(self):
        """Allow automatic fits on all ranges again."""
        for r in self.values():
            r.set_user_locked(False)

    def _apply(self, rfunc, axspec, *args, **kwargs):
        for s in axspec:
            getattr(self[s], rfunc)(*args, **kwargs)

    def clear_marker(self):
        for r in self.values():
            r.clear_marker()

    def store_marker(self):
        axm = None
        xpos = None
        ypos = None
        for r in self.values():
            r.clear_stored_marker()
            ax, x, y = r.store_marker()
            if ax is not None:
                if axm is None:
                    axm = ax
                    xpos = x
                    ypos = y
                elif axm is ax:
                    if xpos is None and x is not None:
                        xpos = x
                    if ypos is None and y is not None:
                        ypos = y
        if axm is not None and xpos is not None and ypos is not None:
            axm.set_stored_marker(xpos, ypos)

    def clear_stored_marker(self):
        for r in self.values():
            r.clear_stored_marker()

    def _marker_pos(self, ranges):
        for r in ranges:
            if self[r].marker_pos is not None:
                return r, self[r].marker_pos
        return None, None

    def marker_time(self):
        return self._marker_pos(Panel.times)

    def marker_amplitude(self):
        return self._marker_pos(Panel.amplitudes)

    def marker_frequency(self):
        return self._marker_pos(Panel.frequencies)

    def marker_power(self):
        return self._marker_pos(Panel.powers)

    def _marker_delta(self, ranges):
        for r in ranges:
            if self[r].marker_pos is not None and self[r].stored_marker_pos is not None:
                return r, self[r].marker_pos - self[r].stored_marker_pos
        return None, None

    def marker_delta_time(self):
        return self._marker_delta(Panel.times)

    def marker_delta_amplitude(self):
        return self._marker_delta(Panel.amplitudes)

    def marker_delta_frequency(self):
        return self._marker_delta(Panel.frequencies)

    def marker_delta_power(self):
        return self._marker_delta(Panel.powers)

    def update_crosshair(self):
        for r in self.values():
            r.update_crosshair()
