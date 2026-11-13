"""The spectrogram's Fourier window, chosen by its frequency resolution.

The window is offered in Hz, which is what a reader sees on the frequency
axis, and not in samples, which is what the FFT needs.  A window of 256
samples is 12.8 ms and 78 Hz wide bins at 20 kHz, but 5.3 ms and 188 Hz at
48 kHz: a preference stored in samples opened every other sampling rate at a
picture nobody chose, and a key count ("five times Shift+R") meant something
different on every recording.  In Hz it means the same everywhere.

`LADDER` is the list the bar offers: resolutions that halve from 2.5 kHz to
0.038 Hz, ``20000 / 2**i`` for i = 3..19.  At 20 kHz these are exactly the
powers of two the bar used to list (8 .. 524288 samples), so nothing changes
there.  At any other rate the window for a resolution is the FFT size closest
to ``rate / resolution`` that is even and has no prime factor above 5 -- the
rule wavetracker's `SpectrogramConfig.resolve_nfft` uses, so that audian and
wavetracker agree on what "0.61 Hz" means.  The window therefore lasts about
``1 / resolution`` at any rate, and the bin width ``rate / nfft`` is the
resolution within the rounding to a fast size.

Pure functions, no Qt: `DataBrowser` builds its combo and its keys on them,
and `BufferedSpectrogram` stays in samples.
"""

from __future__ import annotations

import math

#: the rate at which every resolution of `LADDER` is a power-of-two window
REFERENCE_RATE = 20000.0
#: windows of 2**3 .. 2**19 samples at `REFERENCE_RATE`
EXPONENTS = range(3, 20)
#: the resolutions offered [Hz], coarsest first
LADDER = tuple(REFERENCE_RATE / 2**i for i in EXPONENTS)
#: the window a recording opens at without a preference: 256 samples at
#: 20 kHz, 12.8 ms at any rate
DEFAULT = REFERENCE_RATE / 2**8
#: the shortest window offered [samples]
NFFT_MIN = 8
#: the longest window offered [samples].  2**20 rather than the 2**19 that
#: used to end the list, so that at 96 kHz the finest step offered (0.15 Hz,
#: 629856 samples) is not coarser than the finest window of the old list
#: (2**19 samples, 0.18 Hz).
NFFT_MAX = 2**20
#: the window an older audian may have stored in samples: its list ended at
#: 2**19, so a larger number in the file is not one it wrote
LEGACY_NFFT_MAX = 2**19


def fast_fft_size(target: float) -> int:
    """The even number without prime factors above 5 closest to `target`.

    The same search as wavetracker's `config._fast_fft_size`, ties
    included, copied rather than imported: audian does not depend on
    wavetracker.  Checked against a brute-force scan of the even 5-smooth
    numbers for 20,000 targets from 3 to 7,400: no difference.
    """
    best, top = 2, 2.0 * max(target, 2.0)
    p2 = 2
    while p2 <= top:
        p3 = p2
        while p3 <= top:
            p5 = p3
            while p5 <= top:
                if abs(p5 - target) < abs(best - target):
                    best = p5
                p5 *= 5
            p3 *= 3
        p2 *= 2
    return best


def window_nfft(resolution: float, rate: float) -> int:
    """Samples of the window for `resolution` [Hz] at sampling rate `rate`."""
    return fast_fft_size(rate / resolution)


def windows(rate: float, frames: int | None = None) -> list[tuple[float, int]]:
    """The windows offered at `rate`: ``(resolution, nfft)``, coarsest first.

    Only those between `NFFT_MIN` and `NFFT_MAX` samples and, when `frames`
    is given, no longer than half the recording -- the length
    `BufferedSpectrogram.prepare_update` clamps a window to, so that an
    entry is never one the transform would quietly shorten.
    """
    top = NFFT_MAX if frames is None else min(NFFT_MAX, int(frames) // 2)
    out = []
    for resolution in LADDER:
        nfft = window_nfft(resolution, rate)
        if NFFT_MIN <= nfft <= top:
            out.append((resolution, nfft))
    return out


def snap(resolution: float) -> float:
    """The resolution of `LADDER` closest to `resolution` on a log scale."""
    return min(LADDER, key=lambda r: abs(math.log(r / resolution)))


def nearest(resolution: float, offered: list[tuple[float, int]]):
    """The entry of `offered` (see `windows`) closest to `resolution` on a
    log scale, or None when nothing is offered."""
    if not offered:
        return None
    return min(offered, key=lambda e: abs(math.log(e[0] / resolution)))


def valid_resolution(value) -> float | None:
    """`value` as a resolution [Hz] if a settings file may hold it, else
    None: a finite positive number, and not a bool."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    if not math.isfinite(value) or value <= 0.0:
        return None
    return value
