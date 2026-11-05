"""From samples to the image a model sees, and from its pixels back to seconds.

This module is the contract between a recording and every model in the plugin:
SAM is prompted on these images, the dataset is written from them, and a whole
recording is detected through them.  Pure numpy, importing neither Qt nor
torch, because it runs in the model runner and in the tests alike and has to
give the same answer in both.

The geometry is exact, and it has to be
---------------------------------------

Everything a model says comes back through `TileGeometry`, so a half-window
slip here would be a bias in every detection ever written, with nothing on
screen to disagree with it.  Three rules keep the mapping exact:

* A frame's window is **centred** on its column.  Column ``k`` covers the
  time ``[s0 + k * hop, s0 + (k + 1) * hop)`` samples and its window is centred
  on the middle of that, so the window reaches ``(nfft - hop) / 2`` samples
  outside the tile on both sides.  Those samples are read, and zero past the
  ends of the recording.
* The band is **snapped to bin edges**.  Bin ``k`` covers ``(k +- 0.5) * df``,
  so a band of bins ``k0..k1-1`` covers exactly ``(k0 - 0.5, k1 - 0.5) * df``,
  and that, not the band asked for, is what a tile reports.
* Resampling averages **power over area**, in `_resample`.  Pixel ``i`` of ``n``
  covers exactly ``[i, i + 1) / n`` of the axis, whichever way the sizes differ,
  so a pixel's edge means the same thing at every zoom.

``tests/test_specdetector_tiling.py`` places clicks and tones at known times and
frequencies and finds them through this mapping.

Why images are normalised per tile, and how
-------------------------------------------

A model trained on one stretch has to work on a stretch nobody looked at, and
the absolute level of a recording is a property of the gain knob.  The image is
therefore relative to the tile's own floor: `whitened` takes the median of each
frequency bin over the tile, in decibels, and an event is its height above that.
The numbers it was picked by are in ``docs/specdetector-design.md``.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import NamedTuple, Optional, Sequence

import numpy as np

try:
    from scipy import fft as _fft
except ImportError:  # pragma: no cover - scipy is a dependency of audian
    _fft = None

#: Square, the size the YOLO family is pretrained on.  Tiles are not letterboxed:
#: they are rendered square, so no pixel is spent on padding.
DEFAULT_IMAGE_SIZE = 640

#: What an image can be made of.  See `to_image`.
REPRESENTATIONS = ("whitened", "db")
DEFAULT_REPRESENTATION = "whitened"

#: The range of `whitened`: decibels above each bin's median that map to black
#: and to white.  Slightly below the median so that noise keeps its texture
#: instead of clipping to a flat black, which is what a model would otherwise
#: learn as "not an event" and meet nowhere else.
WHITENED_FLOOR_DB = -6.0
WHITENED_CEILING_DB = 40.0

#: Percentiles of the tile that span the `db` image.
DB_PERCENTILES = (5.0, 99.9)

#: Added to power before the logarithm: a recording can hold digital zeros (four
#: of sixteen electrodes in one real session), and ``log(0)`` must not reach an
#: image as a NaN.
EPS = 1e-20

#: The reductions of several channels into one image, besides a channel index.
REDUCTIONS = ("max", "mean")
DEFAULT_REDUCTION = "max"


def _next_fast(n: int) -> int:
    """The size to transform at: at least `n`, and fast."""
    if _fft is not None:
        return int(_fft.next_fast_len(int(n), real=True))
    return int(n)


@dataclass(frozen=True)
class SpecConfig:
    """How a recording becomes images.  Saved with every model trained on it.

    In seconds and hertz and never in samples, so that a model trained at 48 kHz
    sees the same picture of a recording made at 96 kHz.
    """

    #: STFT window length
    window_s: float
    #: STFT step, and the time one image column is built from at native size
    hop_s: float
    #: length of a tile
    tile_s: float
    #: how much neighbouring tiles share; see `tiles`
    overlap_s: float
    #: the band; `fmax_hz` None is the Nyquist frequency of each recording
    fmin_hz: float = 0.0
    fmax_hz: Optional[float] = None
    #: side of the square image
    image_size: int = DEFAULT_IMAGE_SIZE
    #: see `to_image`
    representation: str = DEFAULT_REPRESENTATION
    #: "max", "mean", or a channel index as a decimal string
    reduction: str = DEFAULT_REDUCTION

    def validated(self) -> "SpecConfig":
        """This config, or ValueError naming what is wrong with it."""
        if not self.window_s > 0 or not self.hop_s > 0:
            raise ValueError(f"window and hop must be positive: {self}")
        if not self.tile_s > 0:
            raise ValueError(f"the tile must have a length: {self}")
        if not 0 <= self.overlap_s < self.tile_s:
            raise ValueError(f"overlap must be shorter than the tile: {self}")
        if self.fmax_hz is not None and self.fmax_hz <= self.fmin_hz:
            raise ValueError(f"the band is empty: {self}")
        if self.representation not in REPRESENTATIONS:
            raise ValueError(f"unknown representation: {self.representation}")
        if self.image_size < 32:
            raise ValueError(f"image too small: {self.image_size}")
        parse_reduction(self.reduction)
        return self

    def as_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(values: dict) -> "SpecConfig":
        """A config off the disk.  Unknown keys are ignored, so a newer project
        opens in an older audian rather than refusing to."""
        known = SpecConfig.__dataclass_fields__
        return SpecConfig(
            **{key: value for key, value in values.items() if key in known}
        ).validated()


def parse_reduction(reduction) -> object:
    """``"max"``, ``"mean"`` or an int, or ValueError."""
    text = str(reduction)
    if text in REDUCTIONS:
        return text
    try:
        channel = int(text)
    except ValueError:
        raise ValueError(f"unknown channel reduction: {reduction!r}") from None
    if channel < 0:
        raise ValueError(f"unknown channel reduction: {reduction!r}")
    return channel


class Grid(NamedTuple):
    """A `SpecConfig` at one sampling rate: everything in whole samples and bins."""

    rate: float
    nfft: int
    hop: int
    #: frames in a tile
    frames: int
    #: bins ``k0 .. k1-1`` are kept
    k0: int
    k1: int
    #: the band those bins cover, in Hz
    f_low: float
    f_high: float
    #: the tile's true length in seconds, whole hops
    tile_s: float
    #: the distance between tile starts in seconds, whole hops
    step_s: float

    @property
    def df(self) -> float:
        return self.rate / self.nfft

    @property
    def margin(self) -> int:
        """How far the first and the last window reach outside a tile."""
        return (self.nfft - self.hop) // 2


def grid_of(config: SpecConfig, rate: float) -> Grid:
    """`config` at `rate`, snapped to whole samples and bins."""
    rate = float(rate)
    if not rate > 0:
        raise ValueError(f"bad sampling rate: {rate}")
    # A window of 2 samples has one useful bin.  Eight is the shortest that
    # still resolves anything, and only a hopeless config is held up by it.
    nfft = max(_next_fast(max(int(round(config.window_s * rate)), 8)), 8)
    hop = max(int(round(config.hop_s * rate)), 1)
    hop = min(hop, nfft)
    # A window and a column can share a centre only if these two have the same parity;
    # otherwise every window sits half a sample late.  A step of one sample is the
    # cheaper thing to give up, and the geometry below reads the hop actually used.
    if (nfft - hop) % 2:
        hop += 1
    frames = max(int(round(config.tile_s * rate / hop)), 1)
    df = rate / nfft
    nyquist_bin = nfft // 2
    # The bins that hold the band's two edges are both kept, so that a tile always
    # covers at least what was asked for.
    k0 = int(np.clip(math.floor(config.fmin_hz / df + 0.5), 0, nyquist_bin))
    if config.fmax_hz is None:
        k1 = nyquist_bin + 1
    else:
        k1 = int(
            np.clip(
                math.ceil(float(config.fmax_hz) / df - 0.5) + 1, k0 + 1, nyquist_bin + 1
            )
        )
    tile_s = frames * hop / rate
    overlap_frames = int(round(config.overlap_s * rate / hop))
    step_frames = max(frames - overlap_frames, 1)
    return Grid(
        rate=rate,
        nfft=nfft,
        hop=hop,
        frames=frames,
        k0=k0,
        k1=k1,
        f_low=(k0 - 0.5) * df,
        f_high=(k1 - 0.5) * df,
        tile_s=tile_s,
        step_s=step_frames * hop / rate,
    )


class TileGeometry(NamedTuple):
    """Where one image lies on the recording's time-frequency plane.

    x runs with time and y runs *against* frequency: row 0 is the top of the
    image and the highest frequency, as on the screen.
    """

    t0: float
    t1: float
    f0: float
    f1: float
    width: int
    height: int

    def x_of(self, t):
        return (np.asarray(t, dtype=float) - self.t0) / (self.t1 - self.t0) * self.width

    def y_of(self, f):
        return (
            (self.f1 - np.asarray(f, dtype=float)) / (self.f1 - self.f0) * self.height
        )

    def t_of(self, x):
        return self.t0 + np.asarray(x, dtype=float) / self.width * (self.t1 - self.t0)

    def f_of(self, y):
        return self.f1 - np.asarray(y, dtype=float) / self.height * (self.f1 - self.f0)

    def box_to_pixels(self, t0, t1, f0, f1) -> tuple:
        """A time-frequency box as ``(x0, y0, x1, y1)``, top-left first."""
        return (
            float(self.x_of(t0)),
            float(self.y_of(f1)),
            float(self.x_of(t1)),
            float(self.y_of(f0)),
        )

    def box_from_pixels(self, x0, y0, x1, y1) -> tuple:
        """Pixels ``(x0, y0, x1, y1)`` as a box ``(t0, t1, f0, f1)``."""
        return (
            float(self.t_of(x0)),
            float(self.t_of(x1)),
            float(self.f_of(y1)),
            float(self.f_of(y0)),
        )

    def as_dict(self) -> dict:
        return self._asdict()


def geometry_of(grid: Grid, s0: int, size: int) -> TileGeometry:
    """Where the tile starting at sample `s0` lies."""
    return TileGeometry(
        t0=s0 / grid.rate,
        t1=(s0 + grid.frames * grid.hop) / grid.rate,
        f0=grid.f_low,
        f1=grid.f_high,
        width=size,
        height=size,
    )


def reduce_channels(power: np.ndarray, reduction, channels=None) -> np.ndarray:
    """A ``(frames, channels, bins)`` block as ``(frames, bins)``.

    Over *power*, and before the decibels: a mean of decibels is -inf wherever
    one electrode recorded a digital zero (`bufferedspectrogram.channel_power`
    has the measurement).

    ``max`` is the default because of what a recording array looks like: a
    signal on one electrode of sixteen keeps its whole height under the maximum
    and drops 12 dB under the mean.
    """
    if power.ndim == 2:
        return power
    how = parse_reduction(reduction)
    if channels is not None:
        power = power[:, list(channels), :]
    if isinstance(how, int):
        if how >= power.shape[1]:
            raise ValueError(
                f"channel {how} does not exist: this recording has "
                f"{power.shape[1]} channels"
            )
        return power[:, how, :]
    if how == "mean":
        return power.mean(axis=1)
    return power.max(axis=1)


def stft_power(samples: np.ndarray, grid: Grid) -> np.ndarray:
    """``(frames, channels, bins)`` of power, for hop-aligned `samples`.

    `samples` is ``(n, channels)`` or ``(n,)``, where ``n`` is at least
    ``(frames - 1) * hop + nfft``.  Bins outside the band are not returned.
    """
    if samples.ndim == 1:
        samples = samples[:, None]
    window = np.hanning(grid.nfft + 1)[:-1].astype(np.float32)
    norm = float(np.sum(window**2)) * grid.rate
    starts = np.arange(grid.frames) * grid.hop
    # sliding_window_view keeps this a view, and the fancy index copies only
    # the frames wanted: on a tile that is most of the cost in memory.
    view = np.lib.stride_tricks.sliding_window_view(samples, grid.nfft, axis=0)
    frames = np.asarray(view[starts], dtype=np.float32) * window
    if _fft is not None:
        spectra = _fft.rfft(frames, axis=-1, workers=-1)
    else:  # pragma: no cover - scipy is a dependency of audian
        spectra = np.fft.rfft(frames, axis=-1)
    spectra = spectra[..., grid.k0 : grid.k1]
    return (spectra.real**2 + spectra.imag**2) / norm


def _resample(image: np.ndarray, n: int, axis: int) -> np.ndarray:
    """`image` at `n` samples along `axis`, by the average over each new pixel.

    An exact area average: pixel ``i`` of ``n`` is the mean of the input over
    ``[i, i + 1) * m / n``, taken from a cumulative sum, so that a thin event
    keeps its energy when several columns fall into one pixel and a coarse one
    keeps its edges when one column spreads over several.
    """
    m = image.shape[axis]
    if m == n:
        return image
    moved = np.moveaxis(image, axis, 0).astype(np.float64)
    # In float64 throughout: power spans many decades, and a float32 running sum loses
    # everything small after one loud frame (a flat 1e-3 after a 1e6 spike read 0.0).
    total = np.concatenate([np.zeros_like(moved[:1]), np.cumsum(moved, axis=0)])
    edges = np.arange(n + 1) * (m / n)
    lo = np.minimum(edges.astype(np.int64), m - 1)
    frac = (edges - lo).reshape((-1,) + (1,) * (moved.ndim - 1))
    # The cumulative sum at a fractional position, by linear interpolation --
    # which is the exact integral of a piecewise-constant signal.
    at = total[lo] + frac * moved[lo]
    out = (at[1:] - at[:-1]) * (n / m)
    return np.moveaxis(out, 0, axis)


def to_image(
    power: np.ndarray, size: int, representation: str, columns: Optional[int] = None
) -> np.ndarray:
    """``(frames, bins)`` of power as a ``(size, size, 3)`` uint8 image.

    Row 0 is the highest frequency.  The three colour channels are identical: nothing
    on a spectrogram is coloured, and three is only what the pretrained weights expect.

    Power is resampled first and the decibels taken after, because averaging decibels
    all but erases a brief event: one frame at +40 dB among the eight of a pixel is
    +31 dB as power and +5 dB as decibels.

    * ``whitened``: decibels above each bin's median over the tile.
    * ``db``: decibels, stretched between two percentiles of the tile.

    `columns` is how many columns hold recording, when the rest of the tile lies past
    its end.
    """
    resampled = _resample(_resample(power, size, 0), size, 1)
    db = 10.0 * np.log10(resampled + EPS)
    if representation == "db":
        plane = _stretch_percentiles(db, columns)
    elif representation == "whitened":
        plane = _stretch_whitened(db, columns)
    else:
        raise ValueError(f"unknown representation: {representation}")
    plane = plane.T[::-1]  # (bins, frames), highest frequency first
    out = np.clip(plane * 255.0 + 0.5, 0, 255).astype(np.uint8)
    return np.dstack([out, out, out])


def _stretch_whitened(db: np.ndarray, columns: Optional[int] = None) -> np.ndarray:
    span = WHITENED_CEILING_DB - WHITENED_FLOOR_DB
    above = db - np.median(db[:columns], axis=0, keepdims=True)
    return np.clip((above - WHITENED_FLOOR_DB) / span, 0.0, 1.0)


def _stretch_percentiles(db: np.ndarray, columns: Optional[int] = None) -> np.ndarray:
    low, high = np.percentile(db[:columns], DB_PERCENTILES)
    return np.clip((db - low) / max(high - low, 1e-9), 0.0, 1.0)


def tile_samples(reader, grid: Grid, s0: int) -> np.ndarray:
    """What a tile starting at `s0` must read, zero-filled past the recording.

    `reader` is anything with ``frames`` and ``read(a, b)`` returning
    ``(b - a, channels)``; see `recording.Recording`.
    """
    a = s0 - grid.margin
    b = a + (grid.frames - 1) * grid.hop + grid.nfft
    lo, hi = max(a, 0), min(b, reader.frames)
    block = reader.read(lo, hi) if hi > lo else None
    channels = reader.channels
    out = np.zeros((b - a, channels), dtype=np.float32)
    if block is not None and len(block):
        out[lo - a : lo - a + len(block)] = block
    return out


def render_tile(reader, grid: Grid, s0: int, config: SpecConfig, channels=None):
    """The tile at sample `s0`: ``(image, geometry)``."""
    power = stft_power(tile_samples(reader, grid, s0), grid)
    power = reduce_channels(power, config.reduction, channels)
    # The floor is measured on what was recorded.  In a recording shorter than a tile
    # the zeros past its end would otherwise be the median, and turn the rest white.
    length = grid.frames * grid.hop
    inside = int(np.clip(reader.frames - s0, grid.hop, length))
    columns = int(math.ceil(inside / length * config.image_size))
    image = to_image(power, config.image_size, config.representation, columns)
    return image, geometry_of(grid, s0, config.image_size)


def tile_starts(duration_s: float, grid: Grid) -> list:
    """The sample each tile of a whole recording starts at.

    The last tile is moved back to end with the recording rather than running
    past it, so it holds no invented silence for a model to learn from, unless
    the whole recording is shorter than one tile.
    """
    frames = int(round(duration_s * grid.rate))
    length = grid.frames * grid.hop
    step = max(int(round(grid.step_s * grid.rate)), 1)
    if frames <= length:
        return [0]
    starts = list(range(0, frames - length, step))
    last = frames - length
    if not starts or starts[-1] != last:
        starts.append(last)
    return starts


def owned_spans(starts: Sequence[int], grid: Grid, frames_total: int) -> list:
    """The part of each tile whose detections it answers for, ``(t0, t1)`` in seconds.

    Each instant belongs to exactly one tile, the one in which it lies furthest from
    an edge, so a detection is kept by the tile that saw it whole and its neighbour's
    copy of it is dropped.  The overlap has to be longer than an event for that to
    hold, which `suggest_config` sees to.

    Worked out from the neighbours and not from the nominal step: `tile_starts` moves
    the last tile back to fit, so the last overlap is usually longer than the others.
    A tile with no neighbour touching it, as in a sweep over separate windows, answers
    for everything it shows.
    """
    length = grid.frames * grid.hop
    rate = grid.rate
    spans = []
    for index, s0 in enumerate(starts):
        low = s0
        high = min(s0 + length, max(frames_total, s0 + length))
        if index > 0 and starts[index - 1] + length > s0:
            low = 0.5 * (s0 + starts[index - 1] + length)
        if index + 1 < len(starts) and s0 + length > starts[index + 1]:
            high = 0.5 * (starts[index + 1] + s0 + length)
        spans.append((low / rate, high / rate))
    return spans


# ------------------------------------------------------------- suggesting one

#: An event should span this many pixels along time at the image's own size.  With one
#: region's 90 chirp labels, a 16 s tile, 7 px on a chirp, scored F1 0.857 on regions it
#: had not seen, and 2-4 s tiles, 27-54 px, scored 0.90-0.94.  From a few shots, this
#: and `BAND_MARGIN` were chosen together; the table is in the design document.
EVENT_PIXELS_T = 27.0
#: The window is this many times the one balancing an event's two resolutions,
#: ``sqrt(duration / bandwidth)``.  On chirps that is 108 ms, between the 100 and 205
#: ms that generalised best to other recordings (F1 0.69-0.71; 50 ms gave 0.52).
WINDOW_BALANCE = 3.0
#: Without a band to balance against, a window this many times shorter than an event.
WINDOWS_PER_EVENT = 8.0
#: How many bandwidths to keep around the labelled band.  Not only so that an event a
#: little higher than the examples stays on the image: an event that fills the height
#: of a tile is a poor picture for both proposers.  From a few shots with no training,
#: going from 1 to 3 took YOLOE from F1 0.43-0.68 to 0.78-0.79 on chirps and from 0.36
#: to 0.99 on the cricket, at 27 px per event.
BAND_MARGIN = 3.0
#: The overlap is at least this many times the longest example.
OVERLAP_EVENTS = 2.0


def suggest_config(
    boxes: Sequence[Sequence[float]],
    rate: float,
    *,
    channels: int = 1,
    image_size: int = DEFAULT_IMAGE_SIZE,
    representation: str = DEFAULT_REPRESENTATION,
) -> SpecConfig:
    """A config suited to examples ``(t0, t1, f0, f1)``.

    A reader who has marked three chirps should not be asked for an FFT size.
    Frequency may be None in a box: an example that does not say what band it
    occupies leaves the whole band on the image.
    """
    if not len(boxes):
        raise ValueError("no examples to suggest a configuration from")
    durations = np.array([float(b[1]) - float(b[0]) for b in boxes])
    durations = durations[durations > 0]
    if not len(durations):
        raise ValueError("the examples have no length")
    typical = float(np.median(durations))
    longest = float(np.max(durations))
    nyquist = rate / 2
    # Every example must fit, with room around it.  One far longer than the rest
    # costs the short ones resolution, and that is the reader's data speaking.
    tile_s = max(typical * image_size / EVENT_PIXELS_T, longest * 4.0)
    overlap_s = min(max(longest * OVERLAP_EVENTS, tile_s * 0.1), tile_s * 0.5)
    lows = [b[2] for b in boxes if b[2] is not None and b[3] is not None]
    highs = [b[3] for b in boxes if b[2] is not None and b[3] is not None]
    fmin_hz, fmax_hz = 0.0, None
    window_s = typical / WINDOWS_PER_EVENT
    if lows and max(highs) > min(lows):
        width = float(np.median(np.array(highs) - np.array(lows)))
        fmin_hz = max(float(min(lows)) - BAND_MARGIN * width, 0.0)
        top = float(max(highs)) + BAND_MARGIN * width
        fmax_hz = None if top >= nyquist else top
        if width > 0:
            window_s = WINDOW_BALANCE * math.sqrt(typical / width)
    window_s = max(window_s, 8.0 / rate)
    # One column to a pixel: a finer hop is thrown away by the resampling, and a
    # coarser one is a blurred picture at the same cost.
    hop_s = max(tile_s / image_size, 1.0 / rate)
    return SpecConfig(
        window_s=window_s,
        hop_s=hop_s,
        tile_s=tile_s,
        overlap_s=overlap_s,
        fmin_hz=fmin_hz,
        fmax_hz=fmax_hz,
        image_size=image_size,
        representation=representation,
        reduction=DEFAULT_REDUCTION if channels > 1 else "0",
    ).validated()


def view_config(
    t0: float, t1: float, f0: float, f1: float, rate: float, size: int, reduction="max"
) -> tuple:
    """A config rendering exactly the rectangle in view, and the sample it starts at.

    For prompting SAM on what the reader is looking at.  The window balances the
    two resolutions: the product of time and frequency resolution is fixed, so
    the window that makes a pixel worth the same in both is the geometric mean
    of the time a pixel covers per hertz.
    """
    t1 = max(t1, t0 + 1.0 / rate)
    dt = t1 - t0
    df = max(f1 - f0, 1e-6)
    window_s = math.sqrt(dt / df)
    window_s = float(np.clip(window_s, 16.0 / rate, 1.0))
    hop_s = max(dt / size, 1.0 / rate)
    hop_s = min(hop_s, window_s)
    config = SpecConfig(
        window_s=window_s,
        hop_s=hop_s,
        tile_s=dt,
        overlap_s=0.0,
        fmin_hz=max(f0, 0.0),
        fmax_hz=None if f1 >= rate / 2 else f1,
        image_size=size,
        reduction=str(reduction),
    )
    return config.validated(), int(round(t0 * rate))
