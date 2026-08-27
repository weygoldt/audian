"""PlotDataItem for spectrogram."""

import numpy as np
import pyqtgraph as pg

from math import floor
from thunderlab.powerspectrum import decibel

from .bufferedspectrogram import channel_power


class SpecItem(pg.ImageItem):
    """Spectrogram image of one channel of a BufferedSpectrogram.

    Or, once `set_mean_channels()` has been called, of the mean power over
    several of them: the item is the same object with the channel axis
    reduced instead of indexed, which is all a mean spectrogram is.

    Only the part of the buffer that can actually be seen is converted to
    decibel and uploaded.  Uploading the whole buffer cost 23.4 ms of
    decibel plus 22 ms of setImage per channel -- ~775 ms for 16 channels --
    for a view showing 10 s of a 60 s buffer.
    """

    #: How much of the visible width is uploaded on each side as slack.
    #: 1.5 means the upload covers four times the visible range, so panning
    #: is free until the view leaves it, and a buffer only gets cropped when
    #: it is more than four times wider than the view -- which is exactly
    #: when uploading all of it is wasteful.
    view_pad = 1.5

    #: uploaded columns per device pixel of widget width
    pixel_oversample = 2

    def __init__(self, data, channel, *args, **kwargs):
        pg.ImageItem.__init__(self, **kwargs)
        self.setOpts(axisOrder="row-major")

        self.data = data
        self.channel = channel
        # channels the image averages over; None means "just self.channel"
        self.mean_channels = None
        # visible time range as told by the panel; None means "whole buffer"
        self._view_range = None
        # index range and stride of what is currently uploaded
        self._image_range = None

        self.data.plot_items[self.channel] = self

    def set_view_range(self, t0: float, t1: float) -> None:
        """Tell the item which time range is visible.

        Additive API for the spectrogram panel's range handler.  Until it is
        called the item falls back to the whole buffer, which is exactly the
        old behaviour.
        """
        self._view_range = (float(t0), float(t1))

    def set_mean_channels(self, channels) -> bool:
        """Draw the mean power over `channels`, or `None` for own channel.

        Returns True when the source actually changed, and throws the
        uploaded crop away when it did.  That is not housekeeping: the
        hysteresis in `update_plot` keys off the time range and the buffer's
        own change flag, and neither knows what the pixels were computed
        *from*.  Measured with the reset taken back out, sixteen channels:
        the range, the stride and the flag are all unchanged by the switch,
        so `update_plot` returns early and the panel comes up captioned
        `MEAN 00-15` with channel 0 in it.
        """
        channels = None if channels is None else [int(c) for c in channels]
        if channels == self.mean_channels:
            return False
        self.mean_channels = channels
        self._image_range = None
        return True

    def power_block(self, rows):
        """Reduce `rows` -- a (time, channel, freq) slice -- to (time, freq).

        `channel_power` carries the measurement: averaging the power and
        converting once is the only correct order, and on this array the
        other order draws nothing at all.
        """
        return channel_power(
            rows, self.channel if self.mean_channels is None else self.mean_channels
        )

    def noise_levels(self):
        """Colour ramp the data suggests for what this item actually draws.

        The mean's floor lands 2.3 dB from a single channel's and its top
        37.5 dB from it, so asking per channel and using the answer for the
        mean gets the dark end right and throws the contrast away; see
        `BufferedSpectrogram.estimate_noiselevels`.
        """
        if self.mean_channels is None:
            return self.data.estimate_noiselevels(self.channel)
        return self.data.estimate_noiselevels(self.mean_channels)

    def get_power(self, t, f):
        """Get power next to cursor position.

        Averaged over the same channels the image is, so the readout cannot
        disagree with the pixel it is standing on.
        """
        ti = int(floor(t * self.data.rate))
        fi = int(floor(f / self.data.fresolution))
        if ti >= self.data.shape[0] or fi >= self.data.shape[2]:
            return None
        if self.mean_channels is None:
            return decibel(self.data[ti, self.channel, fi])
        return decibel(float(np.mean(self.data[ti, self.mean_channels, fi])))

    def max_columns(self) -> int:
        """Number of image columns worth uploading for our own width."""
        vb = self.getViewBox()
        width = vb.width() if isinstance(vb, pg.ViewBox) else 0
        widget = self.getViewWidget()
        dpr = widget.devicePixelRatioF() if widget is not None else 1.0
        pixels = int(width * dpr) if width > 0 else 2000
        return max(64, SpecItem.pixel_oversample * pixels)

    def visible_indices(self) -> tuple[int, int]:
        """Buffer index range that is on screen, clamped to the buffer."""
        n = len(self.data.buffer)
        if self._view_range is None or n == 0:
            return 0, n
        rate = self.data.rate
        offset = self.data.offset
        i0 = int(np.floor(self._view_range[0] * rate)) - offset
        i1 = int(np.ceil(self._view_range[1] * rate)) + 1 - offset
        i0 = max(0, min(n, i0))
        i1 = max(i0, min(n, i1))
        if i1 <= i0:
            return 0, n
        return i0, i1

    def update_plot(self):
        n = len(self.data.buffer)
        if n == 0 or self.data.buffer.ndim < 3:
            return
        v0, v1 = self.visible_indices()
        columns = self.max_columns()
        # stride the visible range alone would need; panning must not change
        # it, otherwise every pan invalidates the upload:
        needed = max(1, (v1 - v0) // columns)
        # every channel of the buffer is refilled in one go, so this one
        # flag answers for the mean as well as for a single channel:
        changed = bool(self.data.buffer_changed[self.channel])
        if not changed and self._image_range is not None:
            i0, i1, stride = self._image_range
            if i0 <= v0 and v1 <= i1 and stride <= needed:
                # what is on screen is already uploaded at enough detail
                return
        pad = int(SpecItem.view_pad * max(1, v1 - v0))
        i0 = max(0, v0 - pad)
        i1 = min(n, v1 + pad)
        stride = max(1, (i1 - i0) // columns)
        self._image_range = (i0, i1, stride)
        block = self.power_block(self.data.buffer[i0:i1:stride])
        with np.errstate(all="ignore"):
            self.setImage(decibel(block.T), autoLevels=False)
        # rect covers the CROPPED extent, not data.spec_rect:
        rate = self.data.rate
        self.setRect(
            (self.data.offset + i0) / rate,
            0,
            (i1 - i0) / rate,
            self.data.source.rate / 2 + self.data.fresolution,
        )
        self.data.buffer_changed[self.channel] = False
