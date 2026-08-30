"""How a spectrogram image is smoothed on its way to the screen.

A spectrogram at the window lengths anybody actually browses at is a noisy
picture: one bin is one periodogram estimate, its variance does not fall
with `nfft`, and the speckle it produces is what a reader is looking
*through* rather than *at*.  Smoothing trades a little resolution for that
variance, and which trade is right is a matter of what is being looked for
-- so it is a control rather than a constant.

Two kinds of smoothing, and the difference matters
--------------------------------------------------

**Interpolation** is a question about *pixels*: the image has one bin per
cell and the lane has some other number of pixels, so something has to
decide what lands between the bin centres.  Qt answers it with
``SmoothPixmapTransform``, which costs no Python at all and, crucially,
does not touch a single number -- the readout under the cursor is still
the power that was measured there.

**Filtering** is a question about *values*: the drawn number becomes a
weighted mean of its neighbours.  That is the part that actually kills
speckle, and it is also the part that makes the picture and the
measurement two different things -- see `SpecItem.get_power`, which reads
the drawn pixel rather than the raw bin whenever a filter is on, because a
readout that disagrees with the pixel it is standing on is worse than
either answer alone.  Measured on ``data/Gryllus_campestris.wav``, 10 s of
channel 0 at nfft 256, raw dB against filtered dB: the median bin moves
3.0 dB, the 95th percentile 9.1 dB and the worst bin 50.7 dB.  The worst
bins are the chirp onsets -- exactly the ones a reader points at.

Every filtering entry interpolates as well.  A reader who asked for a
smoother picture did not ask for smooth numbers drawn as hard blocks.

What is not here, and why
-------------------------
**Bicubic** and **median** were both measured and both rejected on cost.
On the reference block -- 129 x 3000 float64, which is one 1500 px lane at
nfft 256 -- against 5.03 ms for a Gaussian and the 8.19 ms that
``decibel()`` already spends on the same block:

===========================  =========  ==========
filter                        one lane   16 lanes
===========================  =========  ==========
``uniform_filter`` size 3      2.57 ms     41 ms
``gaussian_filter`` sigma 1    5.03 ms     80 ms
``gaussian_filter`` sigma 2    7.66 ms    123 ms
``median_filter`` size 3      58.85 ms    941 ms
``zoom`` x2 freq, order 3     76.58 ms   1225 ms
``zoom`` x2 both, order 3    138.00 ms   2208 ms
===========================  =========  ==========

A re-upload is hysteresis-gated -- `SpecItem.update_plot` only rebuilds
when the view leaves its pad -- so 80 ms on sixteen lanes is a cost paid
per pan, not per paint, and that is affordable.  Nine hundred is not, and
neither is two seconds.  Qt has no bicubic to borrow either: ``QPainter``
offers fast and smooth, and smooth is bilinear.

So bicubic is answered by ``Bilinear`` and median by ``Box``: not the same
filters, but the same two things asked for -- pixels that are not blocks,
and speckle that is not there.

Strength is in the menu rather than beside it
---------------------------------------------
``Gaussian`` and ``Gaussian (strong)`` are one dropdown and not a dropdown
plus a sigma box.  Two entries cost no width in a parameter group that is
already the widest page in the side panel (see `DataBrowser.narrow_combo`),
and sigma is not a number anybody reads off a spectrogram -- it is a knob
turned until the picture looks right, which is a choice between a few
positions rather than a continuum.
"""

from __future__ import annotations

from typing import Callable, NamedTuple, Optional

import numpy as np
from scipy import ndimage

#: dB that non-finite bins are pulled to before any filter runs.
#:
#: ``decibel(0)`` is ``-inf`` and a recording really does contain
#: exactly-zero channels -- `bufferedspectrogram.channel_power` records
#: four of the sixteen electrodes of the flona block being written as
#: zero.  Inside a convolution kernel a ``-inf`` is not a dark bin, it is a
#: ``-inf`` smeared across every bin the kernel reaches, so an unguarded
#: blur turns one dead electrode into an empty panel -- the same failure
#: `channel_power` exists to prevent one step earlier.
#:
#: -200 rather than the array's own minimum, because -200 is already this
#: application's floor: it is the lower limit of the colour bar
#: (``pg.ColorBarItem(limits=(-200, 20))``) and the value the power curve
#: clamps to.  A bin pulled to it draws exactly as dark as ``-inf`` drew.
FLOOR_DB = -200.0

#: Side of the box filter, in bins.
BOX_SIZE = 3

#: Standard deviations of the two Gaussians, in bins, isotropic.
#:
#: Isotropic in *bins* and not in pixels, because a filter is a statement
#: about the data.  One consequence worth knowing: `SpecItem` decimates in
#: time before it uploads, so a sigma of one column is one column of the
#: *upload* -- roughly one screen pixel, whatever the zoom -- while a sigma
#: of one row is always one frequency bin.  Horizontally the blur is
#: therefore constant on screen and vertically it is constant in the data,
#: which is the way round a display filter wants it.
GAUSSIAN_SIGMA = 1.0
GAUSSIAN_STRONG_SIGMA = 2.0


class Method(NamedTuple):
    """One entry of the smoothing menu."""

    #: what the settings file stores; never shown to a reader
    key: str
    #: what the dropdown shows
    label: str
    #: the tool tip
    tip: str
    #: ask Qt to interpolate between bins rather than draw them as blocks
    interpolate: bool
    #: what to do to the dB image, or None to leave the numbers alone
    filter: Optional[Callable[[np.ndarray], np.ndarray]]


METHODS: tuple[Method, ...] = (
    Method(
        "none",
        "None",
        "Raw bins, drawn as blocks of pixels",
        False,
        None,
    ),
    Method(
        "bilinear",
        "Bilinear",
        "Interpolate between bins; the numbers are untouched",
        True,
        None,
    ),
    Method(
        "box",
        "Box",
        f"Mean over {BOX_SIZE}x{BOX_SIZE} bins, then interpolate",
        True,
        lambda db: ndimage.uniform_filter(db, BOX_SIZE),
    ),
    Method(
        "gaussian",
        "Gaussian",
        f"Gaussian blur over {GAUSSIAN_SIGMA:g} bin, then interpolate",
        True,
        lambda db: ndimage.gaussian_filter(db, GAUSSIAN_SIGMA),
    ),
    Method(
        "gaussian-strong",
        "Gaussian (strong)",
        f"Gaussian blur over {GAUSSIAN_STRONG_SIGMA:g} bins, then interpolate",
        True,
        lambda db: ndimage.gaussian_filter(db, GAUSSIAN_STRONG_SIGMA),
    ),
)

#: What a spectrogram opens at, and what an unreadable setting falls back to.
DEFAULT = "none"

_BY_KEY = {m.key: m for m in METHODS}


def method(key) -> Method:
    """The named method, or the default one.

    Anything unrecognised resolves to `DEFAULT` rather than raising: the key
    can come from a settings file written by another audian, and a
    preference nobody can read is a reason to draw the picture plainly, not
    a reason to fail to draw it.
    """
    return _BY_KEY.get(key, _BY_KEY[DEFAULT])


def resolve(key) -> str:
    """The named method's key, or the default one's."""
    return method(key).key


def keys() -> tuple[str, ...]:
    """Every key, in the order the dropdown offers them."""
    return tuple(m.key for m in METHODS)


def labels() -> tuple[str, ...]:
    """Every label, in the order the dropdown offers them."""
    return tuple(m.label for m in METHODS)


def index(key) -> int:
    """Position of the named method in `METHODS`."""
    return keys().index(resolve(key))


def interpolates(key) -> bool:
    """Should Qt interpolate between this method's bins?"""
    return method(key).interpolate


def changes_values(key) -> bool:
    """Does this method make the drawn number differ from the measured one?

    The question `SpecItem.get_power` and `SpectrogramPlot._level_range`
    both ask: an interpolate-only method leaves every bin exactly where it
    was, so neither has anything to do.
    """
    return method(key).filter is not None


def smooth(db: np.ndarray, key) -> np.ndarray:
    """Filter a dB image, or hand it straight back.

    `db` is never modified: the caller may still be holding it, and every
    filter here returns a new array anyway.  A method with no filter
    returns the very same object, so the common case costs one dict lookup.
    """
    fn = method(key).filter
    if fn is None:
        return db
    finite = np.isfinite(db)
    if not finite.all():
        db = np.where(finite, db, FLOOR_DB)
    return fn(db)
