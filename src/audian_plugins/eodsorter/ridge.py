"""Add by ridge: the best continuous ridge inside a brushed region.

Design section 5.5.  An Add stroke in ridge mode does not say "here is the
frequency", it says "the fish is somewhere in here".  For every session frame
the brush footprint covers, the footprint's frequency extent in that frame is
the band the ridge may use (`brush_bands`).  The raw audio under the stroke
is turned into a power spectrogram on the *session's* frame grid
(`power_on_frames`: same frame starts, same nfft, same Hann window and PSD
scaling as wavetracker, power summed over electrodes as wavetracker's own
peak search does), and `extract_ridge` finds the path through the bands that
maximises

    sum_k dB[k, b_k]  -  centre_db * sum_k u_k**2  -  jump_db * sum_k ((f[b_k] - f[b_{k-1}]) / (max_slope * dt_k))**2

with ``|f[b_k] - f[b_{k-1}]| <= max_slope * dt_k`` as a hard limit, by
dynamic programming (Viterbi), where ``u_k`` is the distance of ``f[b_k]``
from the band's centre in half-widths of the band.  The jump penalty is in units of the slope, so the
same path costs the same whatever the frame step; a jump at the maximum
slope costs `jump_db`, a one-bin wobble almost nothing.  Each frame's
frequency is then refined with a parabola through the dB values of the peak
bin and its neighbours.  Frames where the path is not a spectral peak, or
not above the band's noise floor get no detection: a gap stays a gap.

The noise floor is the median power in a window of at least `noise_bins`
bins around the band, times the factor by which the *loudest* of the band's
bins in a noise-only frame would exceed that median with probability only
`p_false` (`noise_factor`).  A periodogram bin of Gaussian noise summed over
c electrodes is Gamma(c)-distributed, so the factor follows from the number
of electrodes and the band's width alone (two electrodes, eight bins, 1 %:
7.3 dB).  This replaces the "median + k MAD of the dB values" first
considered: in a dense recording the window is full of other fish, whose
peaks inflate the MAD so much that a clearly visible fish 13 dB above the
median failed a 3-MAD floor in two frames out of three (iriri, 2 electrodes).
The median itself is robust to that, and only errs on the safe side.

Numpy only (scipy's FFT when it is there), so it is tested in the fast suite.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

#: Cost of one frame-to-frame jump at the maximum slope, in dB.  A fish's
#: own frequency wobbles by about a bin between frames (a few per cent of the
#: maximum jump, so well under 1 dB); stepping onto a neighbour a few Hz
#: away takes jumps near the limit, each costing about as much as the
#: neighbour is louder per frame, so the path changes fish only when the
#: neighbour is much louder for many frames -- and the band (the brush) is
#: what keeps it on the reader's fish in the first place.
JUMP_DB = 12.0
#: Pull towards the stroke: a frequency at the edge of the brush costs this
#: much per frame (quadratic in the distance from the band's centre).  The
#: reader painted along *their* fish; on iriri (2 electrodes, neighbours
#: 3-7 Hz apart, 3 Hz brush) it cut the frames that went to the neighbour
#: from 3.7 % to 0.9 % with the stroke on the fish and from 10 % to 4 % with
#: the stroke 1 Hz off towards the neighbour, at no loss of correct frames.
CENTRE_DB = 6.0
#: Probability that a band of pure noise passes the noise floor in a frame.
P_FALSE = 0.01
#: The noise window is the band, widened to at least this many bins, so a
#: narrow brush does not take the fish's own main lobe for the background.
NOISE_BINS = 64


@dataclass(frozen=True)
class RidgeParams:
    """The ridge search's knobs; see the module docstring."""

    max_slope: float  # Hz/s
    jump_db: float = JUMP_DB
    p_false: float = P_FALSE
    noise_bins: int = NOISE_BINS
    n_channels: int = 1  # electrodes summed into the power
    centre_db: float = CENTRE_DB  # cost of a frequency at the band's edge [dB]


@dataclass
class Ridge:
    """`extract_ridge`'s answer, one entry per frame it was given."""

    freqs: np.ndarray  # refined frequency [Hz]; NaN where no detection
    bins: np.ndarray  # the path's bin (index into the given freqs), -1 if none
    db: np.ndarray  # path power [dB]
    floor: np.ndarray  # the noise floor it had to beat [dB]

    @property
    def kept(self) -> np.ndarray:
        return np.isfinite(self.freqs)


# ------------------------------------------------------------------ config


def max_slope_from(meta: Optional[dict], frame_dt: float) -> float:
    """The default maximum slope [Hz/s]: what the tracker itself would link
    between consecutive frames, ``tracking.freq_tolerance / frame step``.

    From the session's ``wavetracker.json`` (``config.tracking`` or the
    top-level ``tracking_config``), else wavetracker's default read from
    `wavetracker.config` (dataclasses and yaml only, no torch), else 2.5 Hz
    per frame, wavetracker's documented default."""
    tol = None
    meta = meta or {}
    for cfg in (
        (meta.get("config") or {}).get("tracking")
        if isinstance(meta.get("config"), dict)
        else None,
        meta.get("tracking_config"),
    ):
        if isinstance(cfg, dict) and cfg.get("freq_tolerance") is not None:
            try:
                tol = float(cfg["freq_tolerance"])
                break
            except (TypeError, ValueError):
                pass
    if tol is None:
        tol = wavetracker_default("tracking", "freq_tolerance", 2.5)
    return float(tol) / max(float(frame_dt), 1e-9)


def wavetracker_default(section: str, name: str, fallback):
    """A default of wavetracker's `Config`, without importing torch."""
    try:
        from wavetracker import config as wc

        cls = {
            "spectrogram": "SpectrogramConfig",
            "tracking": "TrackingConfig",
        }[section]
        return getattr(getattr(wc, cls)(), name)
    except Exception:  # noqa: BLE001 - not installed, or a different version
        return fallback


# ------------------------------------------------------------------ bands


def brush_bands(
    frame_t, pts_t, pts_f, r_t: float, r_f: float
) -> tuple[np.ndarray, np.ndarray]:
    """The brush footprint's frequency extent at each frame time.

    The stroke is the polyline ``(pts_t, pts_f)`` swept by an ellipse of
    half-axes ``r_t`` [s] and ``r_f`` [Hz] (the brush circle of r screen
    pixels in data units).  Returns ``(lo, hi)``; NaN where the footprint
    does not reach the frame.  Where the stroke doubles back the band is the
    hull of the footprint in that frame."""
    frame_t = np.asarray(frame_t, dtype=np.float64)
    t = np.asarray(pts_t, dtype=np.float64)
    f = np.asarray(pts_f, dtype=np.float64)
    lo = np.full(len(frame_t), np.nan)
    hi = np.full(len(frame_t), np.nan)
    if not len(t) or not len(frame_t) or r_t <= 0 or r_f <= 0:
        return lo, hi
    # resample the polyline so consecutive samples are at most a quarter
    # brush apart (in brush units): the swept ellipse is then the union of
    # the samples' ellipses to well within a pixel
    if len(t) > 1:
        d = np.hypot(np.diff(t) / r_t, np.diff(f) / r_f)
        n = np.maximum(1, np.ceil(d / 0.25).astype(np.int64))
        tt = [t[:1]]
        ff = [f[:1]]
        for i in range(len(d)):
            s = np.arange(1, n[i] + 1) / n[i]
            tt.append(t[i] + s * (t[i + 1] - t[i]))
            ff.append(f[i] + s * (f[i + 1] - f[i]))
        t = np.concatenate(tt)
        f = np.concatenate(ff)
    order = np.argsort(t, kind="stable")
    t, f = t[order], f[order]
    for k, tk in enumerate(frame_t):
        a = int(np.searchsorted(t, tk - r_t, side="left"))
        b = int(np.searchsorted(t, tk + r_t, side="right"))
        if b <= a:
            continue
        u = (tk - t[a:b]) / r_t
        h = r_f * np.sqrt(np.clip(1.0 - u * u, 0.0, 1.0))
        lo[k] = float(np.min(f[a:b] - h))
        hi[k] = float(np.max(f[a:b] + h))
    return lo, hi


# ------------------------------------------------------------ spectrogram


def _rfft(x, n):
    try:
        from scipy import fft as sfft

        return sfft.rfft(x, n=n, axis=-1, workers=-1)
    except ImportError:  # pragma: no cover - scipy is an audian dependency
        return np.fft.rfft(x, n=n, axis=-1)


def power_on_frames(
    audio,
    a0: int,
    starts,
    nfft: int,
    rate: float,
    f_lo: float,
    f_hi: float,
    chunk: int = 32,
    check=None,
):
    """Power spectra of the frames starting at samples `starts`, as
    wavetracker computes them (`PowerSpectrogram`: mean removed, symmetric
    Hann window, one-sided PSD scaling), between `f_lo` and `f_hi`.

    `audio` is ``(samples, channels)`` beginning at sample `a0`.  Returns
    ``(total, freqs, per_channel, spectrum)``: the power summed over
    channels ``(frames, bins)``, the bins' frequencies, the power per
    channel ``(frames, channels, bins)`` and the scaled complex spectrum
    (same shape, wavetracker's ``cplx``).  Frames that reach outside
    `audio` are NaN.  `check` is called between chunks (a cancel token)."""
    audio = np.asarray(audio)
    if audio.ndim == 1:
        audio = audio[:, None]
    starts = np.asarray(starts, dtype=np.int64)
    nc = audio.shape[1]
    freqs_all = np.fft.rfftfreq(nfft, 1.0 / rate)
    b0 = int(np.clip(np.searchsorted(freqs_all, f_lo, side="left"), 0, len(freqs_all)))
    b1 = int(
        np.clip(np.searchsorted(freqs_all, f_hi, side="right"), b0, len(freqs_all))
    )
    freqs = freqs_all[b0:b1]
    nb = b1 - b0
    k = len(starts)
    spec = np.full((k, nc, nb), np.nan + 1j * np.nan, np.complex64)
    window = np.hanning(nfft).astype(np.float32)
    scale = np.full(len(freqs_all), 2.0)
    scale[0] = 1.0
    if nfft % 2 == 0:
        scale[-1] = 1.0
    scale = np.sqrt(scale / (rate * float(np.sum(window.astype(np.float64) ** 2))))
    scale = scale[b0:b1].astype(np.float32)
    off = starts - int(a0)
    ok = np.flatnonzero((off >= 0) & (off + nfft <= len(audio)))
    for c0 in range(0, len(ok), chunk):
        if check is not None:
            check()
        sel = ok[c0 : c0 + chunk]
        idx = off[sel][:, None] + np.arange(nfft)[None, :]
        x = np.asarray(audio[idx], dtype=np.float32)  # (m, nfft, c)
        x = np.transpose(x, (0, 2, 1))
        x = x - x.mean(axis=-1, keepdims=True)
        x *= window
        X = _rfft(x, nfft)[..., b0:b1]
        spec[sel] = (X * scale).astype(np.complex64)
    per = spec.real.astype(np.float64) ** 2 + spec.imag.astype(np.float64) ** 2
    total = per.sum(axis=1)
    return total, freqs, per, spec


# ------------------------------------------------------------------ ridge


def _gamma_sf(x: float, c: int) -> float:
    """P(X > x) for X ~ Gamma(shape c, scale 1), c a positive integer."""
    term, total = 1.0, 1.0
    for i in range(1, c):
        term *= x / i
        total += term
    return float(np.exp(-x) * total)


def _gamma_isf(p: float, c: int) -> float:
    lo, hi = 0.0, 1.0
    while _gamma_sf(hi, c) > p:
        hi *= 2.0
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if _gamma_sf(mid, c) > p:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


_FACTORS: dict = {}


def noise_factor(n_channels: int, n_bins: int, p_false: float = P_FALSE) -> float:
    """How far above the median power the loudest of `n_bins` noise bins
    rises with probability `p_false` (power summed over `n_channels`
    electrodes: Gamma(n_channels))."""
    key = (int(n_channels), int(n_bins), float(p_false))
    f = _FACTORS.get(key)
    if f is None:
        c = max(1, key[0])
        n = max(1, key[1])
        per_bin = 1.0 - (1.0 - key[2]) ** (1.0 / n)
        f = _gamma_isf(per_bin, c) / _gamma_isf(0.5, c)
        _FACTORS[key] = f
    return f


def extract_ridge(
    power,
    freqs,
    lo,
    hi,
    times,
    params: RidgeParams,
) -> Ridge:
    """The best ridge through ``power[k, b]`` (linear power, frames by bins
    on the regular frequency axis `freqs`) inside ``[lo[k], hi[k]]`` per
    frame; see the module docstring.  `times` are the frame times (for the
    slope).  Frames whose power is all NaN (outside the recording) or
    whose band is NaN get no detection and do not constrain the path."""
    power = np.asarray(power, dtype=np.float64)
    freqs = np.asarray(freqs, dtype=np.float64)
    lo = np.asarray(lo, dtype=np.float64)
    hi = np.asarray(hi, dtype=np.float64)
    times = np.asarray(times, dtype=np.float64)
    k_n, b_n = power.shape
    out = Ridge(
        freqs=np.full(k_n, np.nan),
        bins=np.full(k_n, -1, np.int64),
        db=np.full(k_n, np.nan),
        floor=np.full(k_n, np.nan),
    )
    if k_n == 0 or b_n < 3:
        return out
    df = float(freqs[1] - freqs[0])
    with np.errstate(divide="ignore", invalid="ignore"):
        db = 10.0 * np.log10(np.maximum(power, 1e-30))
    valid_frame = np.isfinite(power).all(axis=1) & np.isfinite(lo) & np.isfinite(hi)
    allowed = np.zeros((k_n, b_n), bool)
    for k in np.flatnonzero(valid_frame):
        a = int(np.searchsorted(freqs, lo[k], side="left"))
        b = int(np.searchsorted(freqs, hi[k], side="right"))
        if b <= a:  # a band narrower than a bin: the bin nearest its centre
            c = int(
                np.clip(np.rint((0.5 * (lo[k] + hi[k]) - freqs[0]) / df), 0, b_n - 1)
            )
            a, b = c, c + 1
        allowed[k, max(a, 0) : min(b, b_n)] = True
    frames = np.flatnonzero(allowed.any(axis=1))
    if not len(frames):
        return out
    # the reward: dB, minus the pull towards the stroke's centre line
    reward = db.copy()
    if params.centre_db > 0:
        mid = 0.5 * (lo + hi)
        half_w = np.maximum(0.5 * (hi - lo), df)
        u = (freqs[None, :] - mid[:, None]) / half_w[:, None]
        reward -= params.centre_db * np.where(np.isfinite(u), u * u, 0.0)

    # Viterbi over the frames that have a band; a frame with no reachable
    # bin (the stroke jumped faster than the slope allows) starts afresh
    neg = -np.inf
    score = np.where(allowed[frames[0]], reward[frames[0]], neg)
    back = np.zeros((len(frames), b_n), np.int64)  # predecessor bin, -1: start
    back[0] = -1
    #: where a restarted segment's predecessor ended best
    restart = np.full(len(frames), -1, np.int64)
    scale = float(params.max_slope)
    for j in range(1, len(frames)):
        k = frames[j]
        dt = max(float(times[k] - times[frames[j - 1]]), 1e-9)
        lim = scale * dt
        jmax = max(1, int(np.floor(lim / df + 1e-9)))
        best = np.full(b_n, neg)
        arg = np.full(b_n, -1, np.int64)
        src = np.arange(b_n)
        for d in range(-jmax, jmax + 1):
            cost = params.jump_db * (d * df / lim) ** 2 if lim > 0 else 0.0
            # target bin b comes from b - d
            cand = np.full(b_n, neg)
            if d >= 0:
                cand[d:] = score[: b_n - d] - cost
                orig = np.full(b_n, -1, np.int64)
                orig[d:] = src[: b_n - d]
            else:
                cand[:d] = score[-d:] - cost
                orig = np.full(b_n, -1, np.int64)
                orig[:d] = src[-d:]
            better = cand > best
            best[better] = cand[better]
            arg[better] = orig[better]
        row = allowed[k]
        if not np.isfinite(best[row]).any():
            restart[j] = int(np.argmax(score))
            best = np.zeros(b_n)
            arg = np.full(b_n, -1, np.int64)
        score = np.where(row, best + reward[k], neg)
        back[j] = np.where(row, arg, -1)
    path = np.full(len(frames), -1, np.int64)
    b = int(np.argmax(score))
    for j in range(len(frames) - 1, -1, -1):
        path[j] = b
        prev = int(back[j, b])
        if prev < 0 and j > 0:
            prev = int(restart[j])  # a restart: where the segment before ended
        b = prev

    # gate and refine
    half = max(1, int(params.noise_bins) // 2)
    nch = max(1, int(params.n_channels))
    for j, k in enumerate(frames):
        b = int(path[j])
        out.bins[k] = b
        out.db[k] = db[k, b]
        cols = np.flatnonzero(allowed[k])
        c = 0.5 * (cols[0] + cols[-1])
        w = max(half, (cols[-1] - cols[0] + 1) // 2 + 1)
        a, e = max(0, int(c - w)), min(b_n, int(c + w) + 1)
        med = float(np.median(power[k, a:e]))
        factor = noise_factor(nch, len(cols), params.p_false)
        floor = 10.0 * np.log10(max(med * factor, 1e-30))
        out.floor[k] = floor
        if not 0 < b < b_n - 1:
            continue
        y0, y1, y2 = db[k, b - 1], db[k, b], db[k, b + 1]
        if not (y1 >= y0 and y1 >= y2) or y1 <= floor:
            continue
        den = y0 - 2.0 * y1 + y2
        delta = 0.5 * (y0 - y2) / den if den < 0 else 0.0
        out.freqs[k] = freqs[b] + float(np.clip(delta, -0.5, 0.5)) * df
    return out


# -------------------------------------------------------------- the job


def frame_starts(grid, frames, times, rate: float, nfft: int) -> np.ndarray:
    """First sample of each frame: the session's grid when it has one,
    else the frame centre time minus half a window."""
    frames = np.asarray(frames, dtype=np.int64)
    if grid is not None:
        return int(grid.s0) + frames * int(grid.step)
    t = np.asarray(times, dtype=np.float64)[frames]
    return np.rint(t * float(rate) - nfft / 2).astype(np.int64)


def find_ridge(
    read,
    n_total: int,
    rate: float,
    starts,
    nfft: int,
    times,
    lo,
    hi,
    max_slope: float,
    channels=None,
    check=None,
    **params,
):
    """Read the audio under a stroke and find its ridge.

    ``read(a, b)`` returns samples ``[a, b)`` as ``(samples, channels)``;
    `starts`, `times`, `lo`, `hi` are per frame.  Returns ``(freqs, sign,
    cplx, info)``: the refined frequency (NaN = no detection), the power per
    electrode and the complex spectrum at the ridge (wavetracker's
    ``sign_v`` / ``cplx_v``), and timings."""
    import time as _time

    t0 = _time.perf_counter()
    starts = np.asarray(starts, dtype=np.int64)
    lo = np.asarray(lo, dtype=np.float64)
    hi = np.asarray(hi, dtype=np.float64)
    k_n = len(starts)
    inside = (starts >= 0) & (starts + nfft <= int(n_total)) & np.isfinite(lo)
    freqs_out = np.full(k_n, np.nan)
    if not inside.any():
        return freqs_out, None, None, {"read": 0.0, "spectrum": 0.0, "ridge": 0.0}
    a0 = int(starts[inside].min())
    a1 = int(starts[inside].max()) + int(nfft)
    audio = np.asarray(read(a0, a1))
    if audio.ndim == 1:
        audio = audio[:, None]
    if channels is not None:
        audio = audio[:, list(channels)]
    t1 = _time.perf_counter()
    if check is not None:
        check()
    df = float(rate) / nfft
    pad = (NOISE_BINS // 2 + 2) * df
    use_starts = np.where(inside, starts, -(10**12))
    total, freqs, per, spec = power_on_frames(
        audio,
        a0,
        use_starts,
        int(nfft),
        float(rate),
        max(0.0, float(np.nanmin(lo)) - pad),
        float(np.nanmax(hi)) + pad,
        check=check,
    )
    t2 = _time.perf_counter()
    nc = audio.shape[1]
    ridge = extract_ridge(
        total,
        freqs,
        np.where(inside, lo, np.nan),
        np.where(inside, hi, np.nan),
        times,
        RidgeParams(max_slope=float(max_slope), n_channels=nc, **params),
    )
    kept = ridge.kept
    sign = np.full((k_n, nc), np.nan, np.float32)
    cplx = np.full((k_n, nc), np.nan + 1j * np.nan, np.complex64)
    rows = np.flatnonzero(kept)
    sign[rows] = per[rows, :, ridge.bins[rows]]
    cplx[rows] = spec[rows, :, ridge.bins[rows]]
    t3 = _time.perf_counter()
    info = {
        "read": t1 - t0,
        "spectrum": t2 - t1,
        "ridge": t3 - t2,
        "channels": nc,
        "kept": int(kept.sum()),
        "frames": int(inside.sum()),
    }
    return ridge.freqs, sign, cplx, info


__all__ = [
    "CENTRE_DB",
    "JUMP_DB",
    "P_FALSE",
    "NOISE_BINS",
    "Ridge",
    "RidgeParams",
    "brush_bands",
    "extract_ridge",
    "find_ridge",
    "frame_starts",
    "max_slope_from",
    "noise_factor",
    "power_on_frames",
    "wavetracker_default",
]
