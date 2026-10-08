"""PlotDataItem for spectrogram."""

import numpy as np
import pyqtgraph as pg

from math import floor
from PySide6.QtGui import QPainter
from thunderlab.powerspectrum import decibel

from . import smoothing
from .bufferedspectrogram import channel_power, fast_decibel
from .dataitem import VisibleChannelMirror


class SpecItem(VisibleChannelMirror, pg.ImageItem):
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

    #: How much of the visible frequency band is uploaded above and below
    #: it, as a fraction of its height.  Only the band is converted to
    #: decibel and uploaded: a wavetracker view of 300-1300 Hz on a 48 kHz
    #: recording shows 6 of 129 bins, and converting the other 123 cost
    #: 14 ms per lane per pan step.  The pad makes a vertical pan or zoom
    #: free until the view leaves it, like `view_pad` does in time.
    band_pad = 0.5

    #: Bins always uploaded beyond the visible band, whatever its height, so
    #: that a smoothing filter sees the same neighbours at the edge of the
    #: view as it would over the whole axis: the widest, the strong Gaussian
    #: (sigma 2, truncated at 4 sigma), reaches 8 bins.
    band_margin = 10

    def __init__(self, data, channel, *args, **kwargs):
        pg.ImageItem.__init__(self, **kwargs)
        self.setOpts(axisOrder="row-major")

        self.data = data
        self.channel = channel
        # channels the image averages over; None means "just self.channel"
        self.mean_channels = None
        # visible time range as told by the panel; None means "whole buffer"
        self._view_range = None
        # visible frequency band as told by the panel; None means "all bins"
        self._band = None
        # index range and stride of what is currently uploaded
        self._image_range = None
        # frequency bins `[k0, k1)` of what is currently uploaded
        self._image_band = None
        # the decibel image before smoothing, and what it was made from:
        # see `_decibel_image`
        self._raw = None
        # how the image is smoothed on its way to the screen; see `smoothing`
        self.smoothing = smoothing.DEFAULT

        self.mirror_visibility()

    def set_view_range(self, t0: float, t1: float) -> None:
        """Tell the item which time range is visible.

        Additive API for the spectrogram panel's range handler.  Until it is
        called the item falls back to the whole buffer, which is exactly the
        old behaviour.
        """
        self._view_range = (float(t0), float(t1))

    def set_band(self, f0: float, f1: float) -> None:
        """Tell the item which frequency band is visible.

        Like `set_view_range`, for the other axis: until it is called the
        item uploads every bin.  Cheap; `update_plot` decides whether the
        uploaded band still covers it.
        """
        self._band = (float(f0), float(f1))

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

    def set_smoothing(self, key) -> bool:
        """Choose how the image is smoothed; see `smoothing`.

        Returns True when the choice actually changed, and throws the
        uploaded crop away when it did -- for the same reason
        `set_mean_channels` does.  `update_plot`'s hysteresis keys off the
        time range, the stride and the buffer's own change flag, and none of
        those knows what filter the pixels were computed *through*.  A
        method that only interpolates has no crop to throw away, but the
        reset costs one re-upload and buys not having a second rule.
        """
        key = smoothing.resolve(key)
        if key == self.smoothing:
            return False
        self.smoothing = key
        self._image_range = None
        return True

    def paint(self, painter, *args):
        """Draw, asking Qt to interpolate between bins when it was asked for.

        pyqtgraph 0.14's `ImageItem.paint` sets no transform hint at all, so
        an image is scaled nearest-neighbour and one bin reads as a block.
        The hint is set here rather than on the view, because the view
        carries every other item too -- the traces, the handles, the
        overlays -- and this is a statement about one image.

        Put back afterwards.  `QGraphicsScene` hands one painter to every
        item it draws and makes no promise about restoring render hints
        between them, so leaving it set would quietly re-render whatever is
        painted next.
        """
        want = smoothing.interpolates(self.smoothing)
        hint = QPainter.RenderHint.SmoothPixmapTransform
        was = painter.testRenderHint(hint)
        if want == was:
            return super().paint(painter, *args)
        painter.setRenderHint(hint, want)
        try:
            return super().paint(painter, *args)
        finally:
            painter.setRenderHint(hint, was)

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

    def drawn_power(self, t, f):
        """The dB value of the uploaded pixel at `(t, f)`, or None.

        None whenever the answer would be a guess: nothing uploaded yet, or
        a position outside the crop that is.  `update_plot` uploads a padded
        crop, so the pointer is inside it whenever the pointer is on screen,
        and the fallback is for the moment between a pan and its re-upload.

        The image is `(frequency, time)` -- `update_plot` uploads
        `block.T` and the item is `row-major` -- strided in time by
        whatever the widget's width asked for, cropped in frequency to
        `_image_band` and not strided there at all.
        """
        image = self.image
        if image is None or self._image_range is None or image.ndim != 2:
            return None
        i0, _i1, stride = self._image_range
        k0 = self._image_band[0] if self._image_band is not None else 0
        ti, fi = self.cell_at(t, f)
        col = (ti - self.data.offset - i0) // stride
        row = fi - k0
        if not (0 <= row < image.shape[0] and 0 <= col < image.shape[1]):
            return None
        return float(image[row, col])

    def time_shift(self) -> float:
        """Seconds from a column's left edge to the centre of its window.

        `BufferedSpectrogram.process` transforms frame *j* from samples
        ``[j*hop, j*hop + nfft)``, so the window is centred at
        ``(j*hop + nfft/2)/fs``.  Drawn from ``j*hop/fs`` and one hop wide,
        the *cell* is centred at ``(j*hop + hop/2)/fs`` -- ``(nfft-hop)/2``
        samples early.  At the band plugin's default nfft of 16384 with
        overlap 0.75 on a 20 kHz recording that is 0.31 s, and the smear
        reaches 0.61 s.

        Every label a reader drew took that bias and stored it as plain
        seconds, with nothing on disk saying which nfft produced it.
        thunderlab's own axis is window centres -- scipy's `_spectral_helper`
        leaves `boundary` None, so it never applies its half-window
        correction -- so the picture was the odd one out, not the tracker.

        One method rather than the arithmetic at each site: the bias existed
        because two files each invented their own convention.
        """
        return (self.data.nfft - self.data.hop) / (2.0 * self.data.source.rate)

    def cell_at(self, t, f):
        """``(frame, bin)`` of the drawn cell containing ``(t, f)``.

        Answers about the *picture*, which is what both readouts want: the
        number under the pointer must be the number in the pixel under the
        pointer.  Either index can be negative -- a centred frequency axis
        starts half a bin below zero, and the time axis starts a window
        earlier than it used to -- so callers must check both ends.
        """
        rate = self.data.rate
        ti = int(floor((t - self.time_shift()) * rate))
        # bin k is centred on k*df and drawn over [k*df - df/2, k*df + df/2),
        # so the cell containing f is the nearest bin rather than the one
        # below it
        fi = int(floor(f / self.data.fresolution + 0.5))
        return ti, fi

    def get_power(self, t, f):
        """Get power next to cursor position.

        Averaged over the same channels the image is, so the readout cannot
        disagree with the pixel it is standing on.

        A *filtering* smoothing breaks that agreement at the source -- the
        drawn number is a weighted mean of its neighbours, measured a median
        of 3.0 dB and up to 50.7 dB from the raw bin, the worst of it at the
        chirp onsets a reader points at -- so with one on, the pixel is what
        is read.  `smoothing.changes_values` is the question, not "is
        smoothing on": an interpolate-only method leaves every bin exactly
        where it was, keeps the exact path below, and returns bit for bit
        what this returned before smoothing existed.
        """
        if smoothing.changes_values(self.smoothing):
            drawn = self.drawn_power(t, f)
            if drawn is not None:
                return drawn
        ti, fi = self.cell_at(t, f)
        # Both ends, not just the upper one.  A centred axis reaches half a
        # bin below zero and a window before the first frame, so -1 is an
        # ordinary result here -- and it used to index the far end of the
        # buffer instead of saying "not on the picture".
        if not (0 <= ti < self.data.shape[0] and 0 <= fi < self.data.shape[2]):
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

    def visible_bins(self, nbins: int) -> tuple[int, int]:
        """Frequency bin range `[b0, b1)` that is on screen, clamped."""
        if self._band is None or nbins == 0:
            return 0, nbins
        fres = self.data.fresolution
        # bin k is drawn over [(k - 1/2) df, (k + 1/2) df); see `cell_at`
        b0 = int(floor(self._band[0] / fres + 0.5))
        b1 = int(floor(self._band[1] / fres + 0.5)) + 1
        b0 = max(0, min(nbins, b0))
        b1 = max(b0, min(nbins, b1))
        if b1 <= b0:
            return 0, nbins
        return b0, b1

    def _decibel_image(self, i0: int, i1: int, stride: int, k0: int, k1: int):
        """The decibel image of buffer rows `i0:i1:stride`, bins `k0:k1`.

        When the buffer has only moved since the last upload, the columns
        both crops share are taken from the last image instead of being
        converted again: on sixteen lanes of the full band every move of
        the buffer re-converted the whole padded crop of every lane, 6 ms a
        lane.  Only columns whose frames stayed in the buffer the whole
        time are taken (`BufferedData.stable_extent`), and a column's
        decibel depends on nothing but its own bins, so the image is
        identical to converting all of it.
        """
        data = self.data
        offset = data.offset
        ncols = (i1 - i0) // stride
        raw = self._raw
        self._raw = None
        reuse = None
        if (
            raw is not None
            and raw["stride"] == stride
            and raw["band"] == (k0, k1)
            and raw["source"] == (self.channel, self.mean_channels)
            and hasattr(data, "stable_extent")
        ):
            extent = data.stable_extent(raw["generation"], raw["seq"])
            if extent is not None:
                lo = max(extent[0], raw["start"])
                hi = min(extent[1], raw["start"] + raw["image"].shape[1] * stride)
                # absolute frames on both column grids (both are multiples
                # of the stride), inside both crops
                a = max(lo, offset + i0)
                b = min(hi, offset + i0 + ncols * stride)
                a += (-(a - raw["start"])) % stride
                if b > a:
                    reuse = (a, b)
        if reuse is None:
            block = self.power_block(data.buffer[i0:i1:stride, :, k0:k1])
            image = fast_decibel(block.T)
        else:
            a, b = reuse
            image = np.empty((k1 - k0, ncols), dtype=raw["image"].dtype)
            j0 = (a - offset - i0) // stride
            j1 = j0 + (b - a + stride - 1) // stride
            r0 = (a - raw["start"]) // stride
            image[:, j0:j1] = raw["image"][:, r0 : r0 + (j1 - j0)]
            for c0, c1 in ((0, j0), (j1, ncols)):
                if c1 > c0:
                    rows = data.buffer[i0 + c0 * stride : i0 + c1 * stride : stride]
                    block = self.power_block(rows[:, :, k0:k1])
                    image[:, c0:c1] = fast_decibel(block.T)
        self._raw = {
            "image": image,
            "start": offset + i0,
            "stride": stride,
            "band": (k0, k1),
            "source": (
                self.channel,
                None if self.mean_channels is None else list(self.mean_channels),
            ),
            "generation": getattr(data, "content_generation", None),
            "seq": getattr(data, "move_seq", None),
        }
        return image

    def update_plot(self):
        """Upload the visible part of the buffer, if it is not uploaded yet.

        Cropped in time to the view plus `view_pad` and in frequency to the
        band plus `band_pad`, and strided in time to the widget's width.  The
        stride is what the *visible* span needs, and its columns sit on
        absolute frame indices that are multiples of it: so a pan neither
        changes the stride nor which columns are drawn, and the picture of a
        view is the same whichever way the view was reached.  Nothing is
        re-uploaded while the uploaded crop still covers the view at that
        stride and the buffer has not changed.
        """
        n = len(self.data.buffer)
        if n == 0 or self.data.buffer.ndim < 3:
            return
        nbins = self.data.buffer.shape[2]
        v0, v1 = self.visible_indices()
        b0, b1 = self.visible_bins(nbins)
        columns = self.max_columns()
        # stride the visible range alone needs.  From the view's length in
        # seconds rather than from `v1 - v0`, which flips by one as the
        # view's edges cross frame boundaries during a pan.
        if self._view_range is not None:
            span = int(
                round((self._view_range[1] - self._view_range[0]) * self.data.rate)
            )
        else:
            span = v1 - v0
        needed = max(1, span // columns)
        # every channel of the buffer is refilled in one go, so this one
        # flag answers for the mean as well as for a single channel:
        changed = bool(self.data.buffer_changed[self.channel])
        if not changed and self._image_range is not None:
            i0, i1, stride = self._image_range
            k0, k1 = self._image_band if self._image_band is not None else (0, nbins)
            if i0 <= v0 and v1 <= i1 and k0 <= b0 and b1 <= k1 and stride == needed:
                # what is on screen is already uploaded at its detail
                return
        stride = needed
        pad = int(SpecItem.view_pad * max(1, v1 - v0))
        i0 = max(0, v0 - pad)
        i1 = min(n, v1 + pad)
        # columns on absolute multiples of the stride
        first = self.data.offset + i0
        i0 += (-first) % stride
        if i0 >= i1:
            i0 = max(0, i1 - stride)
        i1 = i0 + max(1, (i1 - i0) // stride) * stride
        if i1 > n:
            i1 = i0 + max(1, (n - i0) // stride) * stride
        if self._band is None:
            k0, k1 = 0, nbins
        else:
            fpad = int(SpecItem.band_pad * max(1, b1 - b0)) + SpecItem.band_margin
            k0 = max(0, b0 - fpad)
            k1 = min(nbins, b1 + fpad)
        image = self._decibel_image(i0, i1, stride, k0, k1)
        self._image_range = (i0, i1, stride)
        self._image_band = (k0, k1)
        # Filtered here and not in the buffer: this is the one array that is
        # already cropped to what is on screen and already decimated to the
        # widget's own width, so the filter runs over the pixels it is going
        # to affect and over nothing else.
        self.setImage(smoothing.smooth(image, self.smoothing), autoLevels=False)
        # rect covers the CROPPED extent, not data.spec_rect:
        rate = self.data.rate
        fres = self.data.fresolution
        self.setRect(
            (self.data.offset + i0) / rate + self.time_shift(),
            (k0 - 0.5) * fres,
            (i1 - i0) / rate,
            (k1 - k0) * fres,
        )
        self.data.buffer_changed[self.channel] = False
