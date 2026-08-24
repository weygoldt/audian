import datetime as dt
import numpy as np
import pyqtgraph as pg

from math import floor, log10
from PyQt5.QtCore import QPointF

from . import theme


class TimeAxisItem(pg.AxisItem):
    def __init__(self, file_times, file_paths, left_margin, *args, **kwargs):
        self._left_margin = left_margin
        super().__init__(*args, **kwargs)
        theme.style_axis(self)

        self._file_times = file_times
        self._file_paths = file_paths
        self._starttime = None
        self._starttime_mode = 0
        # 0: tick values are recording time starting with zero
        #    at the beginning of the first file.
        # 1: tick values are absolute times of the day,
        #    i.e. the recording's start time is added.
        # 2: tick values are relative to each file's beginning.

    def setLogMode(self, *args, **kwargs):
        # no log mode!
        pass

    def set_left_margin(self, left_margin) -> None:
        """Re-place the axis caption for a changed left-axis width.

        The margin is a constructor argument, but the data plots decide their
        left width at runtime (zero in dense lanes, `theme.AXIS_LEFT_WIDTH`
        otherwise), so anything that wants its caption to line up with them
        needs to be able to follow.
        """
        if left_margin == self._left_margin:
            return
        self._left_margin = left_margin
        self.resizeEvent()

    def apply_theme(self) -> None:
        """Re-resolve pens and fonts from the current token table.

        `style_axis()` bakes the tick, text and label pens when the axis is
        built, so without this a theme switch leaves a dark axis strip under
        light plots.
        """
        theme.style_axis(self)
        self.picture = None
        self.update()

    polish = apply_theme

    def set_start_time(self, time):
        """Set time of first data element.

        Parameters
        ----------
        time: datetime or None
            A datetime object for the data and time of the first data element.
        """
        self._starttime = time
        self.enableAutoSIPrefix(self._starttime is None or self._starttime_mode == 0)

    def set_starttime_mode(self, mode):
        self._starttime_mode = mode
        self.enableAutoSIPrefix(self._starttime is None or self._starttime_mode == 0)

    def get_file_pos(self):
        time = self.linkedView().viewRange()[0][0]
        fidx = np.nonzero(self._file_times <= time)[0][-1]
        toffs = self._file_times[fidx]
        filename = self._file_paths[fidx]
        return filename, time - toffs

    def tickSpacing(self, minVal, maxVal, size):
        diff = abs(maxVal - minVal)
        if diff == 0:
            return []

        if self._starttime_mode == 2:
            min_idx = np.nonzero(self._file_times <= minVal)[0][-1]
            max_idx = np.nonzero(self._file_times <= maxVal)[0][-1]
            if min_idx != max_idx:
                max_value = self._file_times[max_idx] - self._file_times[min_idx]
            else:
                max_value = maxVal - self._file_times[max_idx]
        else:
            max_value = maxVal

        # estimate width of xtick labels; the ticks render in the mono face,
        # so the mono metrics are the ones that matter here:
        xwidth = max(1, theme.mono_metrics(theme.SIZE_SMALL_PT).averageCharWidth())
        if self._starttime and self._starttime_mode == 1:
            nx = 8
        elif max_value < 1.0:
            nx = 0
        elif max_value >= 3600:
            nx = 8
        elif max_value >= 60:
            nx = 5
        else:
            nx = 2
        spacing = diff / 5
        if spacing < 0.00001:
            nx += 7
        elif spacing < 0.0001:
            nx += 6
        elif spacing < 0.001:
            nx += 5
        elif spacing < 1.0:
            nx += 4
        nx += 4

        # minimum spacing:
        max_ticks = max(2, int(size / (nx * xwidth)))
        min_spacing = diff / max_ticks
        p10unit = 10 ** floor(log10(min_spacing))

        # major ticks:
        factors = [1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0]
        for fac in factors:
            spacing = fac * p10unit
            if spacing >= min_spacing:
                break

        # minor ticks:
        factors = [100.0, 10.0, 1.0, 0.1]
        for fac in factors:
            minor_spacing = fac * p10unit
            if minor_spacing < spacing:
                break

        return [(spacing, 0), (minor_spacing, 0)]

    def makeStrings(
        self,
        values,
        scale,
        spacing,
        starttime_mode,
        add_date=False,
        min_spacing=None,
    ):
        """Format time values as axis tick labels or as a hover readout.

        `spacing` is the distance between the values and decides how many
        sub-second digits are shown.  Hover readouts want milliseconds
        whatever the tick spacing happens to be; they ask for that
        explicitly with ``min_spacing=0.01``.  Tick rendering passes the
        real spacing and so gets ``5`` rather than ``5.000`` on a file
        that is ticked once per second.
        """
        label = None
        units = None
        filename = self._file_paths[0] if len(self._file_paths) > 0 else None

        if len(values) == 0:
            return label, units, [], filename

        if scale > 1:
            return "Time", "s", [f"{v * scale:.5g}" for v in values], filename

        if starttime_mode == 1 and not self._starttime:
            starttime_mode = 0
        if starttime_mode == 2 and len(self._file_times) <= 1:
            starttime_mode = 0

        if starttime_mode == 1:
            label = "Time"
        elif starttime_mode == 2:
            label = "File"
            fidx = np.nonzero(self._file_times <= values[0])[0][-1]
            filename = self._file_paths[fidx]
            vals = []
            for time in values:
                fidx = np.nonzero(self._file_times <= time)[0][-1]
                toffs = self._file_times[fidx]
                vals.append(time - toffs)
            values = vals
        else:
            # starttime_mode == 0
            label = "REC"
        max_value = np.max(values)

        if starttime_mode == 1:
            if add_date:
                units = "Y-M-D h:m:s"
                fs = "{year:04d}-{month:02d}-{day:02d} {hours:.0f}:{mins:02.0f}:{secs:02.0f}"
            else:
                units = "h:m:s"
                fs = "{hours:.0f}:{mins:02.0f}:{secs:02.0f}"
        elif max_value > 3600:
            units = "h:m:s"
            fs = "{hours:.0f}:{mins:02.0f}:{secs:02.0f}"
        elif max_value > 60:
            units = "m:s"
            fs = "{mins:.0f}:{secs:02.0f}"
        else:
            units = "s"
            fs = "{secs:.0f}"
        if min_spacing is not None:
            spacing = min(spacing, min_spacing)
        if spacing < 1:
            fs += ".{micros}"

        basetime = dt.datetime(1, 1, 1, 0, 0, 0, 0)
        if starttime_mode == 1:
            basetime = self._starttime
        vals = []
        for time in values:
            t = basetime + dt.timedelta(seconds=time)
            # exactly as many sub-second digits as the spacing resolves:
            # at half-second ticks '0.5' is right and '0.500' is noise.
            if spacing < 0.00001:
                micros = f"{1.0 * t.microsecond:06.0f}"
            elif spacing < 0.0001:
                micros = f"{0.1 * t.microsecond:05.0f}"
            elif spacing < 0.001:
                micros = f"{0.01 * t.microsecond:04.0f}"
            elif spacing < 0.01:
                micros = f"{0.001 * t.microsecond:03.0f}"
            elif spacing < 0.1:
                micros = f"{0.0001 * t.microsecond:02.0f}"
            else:
                micros = f"{0.00001 * t.microsecond:01.0f}"
            time = dict(
                year=t.year,
                month=t.month,
                day=t.day,
                hours=t.hour,
                mins=t.minute,
                secs=t.second,
                micros=micros,
            )
            vals.append(fs.format(**time))
        return label, units, vals, filename

    def tickStrings(self, values, scale, spacing):
        label, units, vals, _ = self.makeStrings(
            values, scale, spacing, self._starttime_mode
        )
        if len(vals) == 0:
            return []
        if units == "s":
            self.setLabel(label, units=units)
        elif label == "Time":
            self.setLabel(units, units=None)
        else:
            self.setLabel(f"{label} ({units})", units=None)
        return vals

    def resizeEvent(self, ev=None):
        # overwrite the AxisItem resizeEvent to place the label somewhere else
        # self.label is set to None on close, but resize events can still occur.
        if self.label is None:
            self.picture = None
            return

        br = self.label.boundingRect()
        p = QPointF(-self._left_margin, 0)
        if self.orientation == "top":
            p.setY(br.height())
        self.label.setPos(p)
        self.picture = None
