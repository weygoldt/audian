"""Baseline-referenced activity metrics for the full-recording overview.

The navigator strip has to summarise an entire recording -- often 10^9
samples -- into roughly 10^3 pixel columns.  A min/max envelope is the
obvious reduction and it is what :mod:`audian.compresseddata` computes, but
on its own it *conflates the two kinds of event this tool is used to find*:

* **Transient** events -- an electric eel's near-delta pulse, a bat's FM
  click, the onset of a percussive call.  A single transient saturates its
  bin's maximum, so one and a thousand of them draw the same bar, and a bin
  holding one is indistinguishable from a bin holding a continuous signal of
  the same peak amplitude.
* **Sustained** events -- a wave-type fish's continuous EOD, a cricket's
  chirp, a bird's song phrase, a frog's call.  At moderate amplitude these
  read as nothing more than a slightly thicker noise band.

The two axes below are deliberately signal-descriptive rather than
taxonomic: the same measurement separates a cricket chirp from a bat click
exactly as it separates a wave-type EOD from an eel pulse.

Both also lose their meaning under autoscaling, because a min/max envelope
carries no reference level: a quiet recording and a loud one look alike.

This module computes, per bin, two quantities that separate the two classes
and are referenced to a *global* baseline so that bins remain comparable
across the whole recording:

``rms_excess_db``
    Bin variance relative to the global noise power.  A lone delta pulse of
    amplitude ``A`` contributes only ``A^2/N`` to a bin of ``N`` samples, so
    this responds to *sustained* energy -- wave-type fish, motor noise,
    sustained bursts -- and stays near 0 dB for sparse transients.

``peak_excess_db``
    Bin peak deviation relative to the global noise amplitude.  This is the
    complementary term: a delta pulse drives it hard while contributing
    almost nothing to the RMS.  It comes free from the min/max envelope that
    is already being computed.

Their difference is the **crest**, which is what actually discriminates:

=========================  ==================  ==================  ==========
signal                     ``rms_excess_db``   ``peak_excess_db``  crest (dB)
=========================  ==================  ==================  ==========
baseline noise only        ~0                  ~12                 ~12
sustained call or EOD      high                high                ~3-9
sparse transients          ~0                  high                >20
=========================  ==================  ==================  ==========

All per-bin accumulators are **additively composable** -- ``n``, ``sum``,
``sumsq`` add, ``min``/``max`` reduce -- so coarse pyramid levels can be
built from fine ones without touching the raw file again.  That is what
makes zooming the navigator cheap: bins shrink as you zoom in, and the
metric is recomputed at the resolution actually on screen rather than being
frozen at whatever bin size the file was first compressed with.

The global baseline is deliberately *not* estimated per bin.  A per-bin
baseline renormalises every bin to look equally active, which destroys
exactly the comparison the strip exists to support: a quiet stretch must
stay visibly quiet.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

#: Percentile of the per-bin RMS distribution taken as the noise floor.
#: Bins holding only baseline dominate the low percentiles, so this is
#: robust to sparse transients *and* to bins that are entirely signal.
#: It assumes at least this fraction of the recording is quiet, which holds
#: for field recordings; see :func:`global_baseline` for the alternative.
BASELINE_PERCENTILE = 10.0

#: Floor applied to the baseline so a digitally silent recording cannot
#: divide by zero.
BASELINE_EPS = 1e-12

#: Sustained energy above baseline power that counts as activity, in dB.
#: Exposed for callers that want a plain "is anything happening here" test.
SUSTAINED_RMS_DB = 3.0

#: How far a bin's peak must clear the *expected* noise peak for that bin
#: size before it counts as a genuine event, in dB.  See
#: :func:`noise_peak_db` for why a fixed threshold will not do.
EVENT_MARGIN_DB = 6.0


@dataclass
class BinStats:
    """Additively composable per-bin accumulators.

    Every array is shaped ``(nbins, channels)`` except :attr:`n`, which is
    ``(nbins,)`` because all channels share a bin layout.

    The four moment arrays are the complete sufficient statistics for both
    metrics, which is why a pyramid level can be built from the level below
    it by :meth:`combine` rather than by re-reading the recording.
    """

    n: np.ndarray
    total: np.ndarray
    total_sq: np.ndarray
    minimum: np.ndarray
    maximum: np.ndarray

    @property
    def nbins(self) -> int:
        return len(self.n)

    @property
    def channels(self) -> int:
        return self.total.shape[1]

    def mean(self) -> np.ndarray:
        """Per-bin mean, i.e. the local DC offset."""
        return self.total / self.n[:, None]

    def variance(self) -> np.ndarray:
        """Per-bin AC-coupled variance, clamped non-negative.

        DC is removed rather than ignored: electrode recordings carry an
        offset that would otherwise be counted as signal power.  The clamp
        absorbs the catastrophic cancellation that ``E[x^2] - E[x]^2``
        suffers when the offset dominates the variance.
        """
        mean = self.mean()
        return np.maximum(self.total_sq / self.n[:, None] - mean * mean, 0.0)

    def peak(self) -> np.ndarray:
        """Per-bin peak absolute deviation from the bin's own mean."""
        mean = self.mean()
        return np.maximum(np.abs(self.maximum - mean), np.abs(self.minimum - mean))

    def combine(self, factor: int) -> "BinStats":
        """Merge groups of `factor` adjacent bins into one coarser bin.

        This is the pyramid step, and it is what makes zooming cheap: every
        accumulator is either additive or a reduction, so a coarser level
        can be built from a finer one instead of re-reading the recording.

        ``n``, ``minimum`` and ``maximum`` come out bit-identical to binning
        the raw samples at the coarser size.  ``total`` and ``total_sq``
        agree only up to summation order -- adding four partial sums is not
        the same sequence of float64 additions as summing every sample -- so
        the derived variance and peak can differ in their last bit or two.
        That is ~1e-16 relative, far below anything a dB readout resolves.
        """
        if factor <= 1:
            return self
        nbins = self.nbins // factor
        if nbins < 1:
            raise ValueError(f"cannot combine {self.nbins} bins by {factor}")
        keep = nbins * factor

        def fold_sum(a):
            return a[:keep].reshape(nbins, factor, -1).sum(axis=1)

        return BinStats(
            n=self.n[:keep].reshape(nbins, factor).sum(axis=1),
            total=fold_sum(self.total),
            total_sq=fold_sum(self.total_sq),
            minimum=self.minimum[:keep].reshape(nbins, factor, -1).min(axis=1),
            maximum=self.maximum[:keep].reshape(nbins, factor, -1).max(axis=1),
        )


def reduce_block(block: np.ndarray, step: int) -> BinStats:
    """Reduce a raw ``(frames, channels)`` block into per-bin accumulators.

    The four reductions are the same four ``reduceat`` passes the min/max
    compression already makes, plus two more over ``x`` and ``x**2``.  The
    extra cost is a single squaring of the block.
    """
    if block.ndim != 2:
        raise ValueError("block must be (frames, channels)")
    frames = len(block)
    segments = np.arange(0, frames, step)
    counts = np.diff(np.append(segments, frames)).astype(np.float64)
    return BinStats(
        n=counts,
        total=np.add.reduceat(block, segments, axis=0),
        total_sq=np.add.reduceat(np.square(block), segments, axis=0),
        minimum=np.minimum.reduceat(block, segments, axis=0),
        maximum=np.maximum.reduceat(block, segments, axis=0),
    )


def global_baseline(
    stats: BinStats,
    percentile: float = BASELINE_PERCENTILE,
) -> np.ndarray:
    """Estimate one noise amplitude per channel for the whole recording.

    Returns the per-channel ``sigma`` that every metric below is referenced
    to, shaped ``(channels,)``.

    The estimator is a low percentile of the per-bin RMS distribution.  Two
    properties matter:

    * It is immune to sparse transients, because a bin holding an eel pulse
      has its RMS raised and is therefore *excluded* by a low percentile.
    * It is immune to stretches that are entirely signal, for the same
      reason -- those bins sit high in the distribution.

    It does assume that at least ``percentile`` per cent of the recording is
    baseline.  That holds comfortably for field recordings.  If it ever does
    not, the honest alternative is a median of per-bin MAD, which is robust
    without that assumption but needs a selection pass per bin rather than
    two running sums, and so cannot be built from composable accumulators.
    """
    rms = np.sqrt(stats.variance())
    sigma = np.percentile(rms, percentile, axis=0)
    return np.maximum(np.asarray(sigma, dtype=np.float64), BASELINE_EPS)


def rms_excess_db(stats: BinStats, sigma: np.ndarray) -> np.ndarray:
    """Sustained energy per bin, in dB above the global noise power.

    0 dB is baseline.  This is a *power* ratio, hence ``10*log10``.
    """
    ratio = stats.variance() / np.square(sigma)[None, :]
    return 10.0 * np.log10(np.maximum(ratio, 1.0))


def peak_excess_db(stats: BinStats, sigma: np.ndarray) -> np.ndarray:
    """Peak deviation per bin, in dB above the global noise amplitude.

    0 dB is baseline.  This is an *amplitude* ratio, hence ``20*log10``.
    """
    ratio = stats.peak() / sigma[None, :]
    return 20.0 * np.log10(np.maximum(ratio, 1.0))


def crest_db(stats: BinStats, sigma: np.ndarray) -> np.ndarray:
    """Peak-to-RMS excess per bin, the pulse/wave discriminator.

    Low values mean the bin's energy is spread across it (a call, a chirp, a
    continuous EOD); high values mean it is concentrated in a few samples (an
    eel pulse, a bat click, a percussive onset).
    """
    return peak_excess_db(stats, sigma) - rms_excess_db(stats, sigma)


#: Bin classes returned by :func:`classify`.  Named for what the signal
#: *does*, not for what produced it: SUSTAINED covers a wave-type EOD, a
#: cricket chirp and a bird phrase alike, TRANSIENT covers an eel pulse and
#: a bat click alike.
QUIET, SUSTAINED, TRANSIENT = 0, 1, 2


def noise_peak_db(n: np.ndarray | float) -> np.ndarray | float:
    """Peak a bin of `n` baseline samples is *expected* to reach, in dB.

    The maximum of ``n`` Gaussian samples grows as ``sigma*sqrt(2*ln n)``, so
    a bin's peak excess has a floor that depends on how many samples it
    holds: about 11 dB at 800 samples, 14 dB at 40 000.  A fixed "peak is
    above X dB" threshold therefore mislabels either the fine bins or the
    coarse ones -- at navigator resolution it would flag *every* bin of pure
    noise as a transient.  Thresholding against this floor instead keeps the
    classification stable as the pyramid level changes under zoom.
    """
    return 20.0 * np.log10(np.sqrt(2.0 * np.log(np.maximum(n, 2.0))))


def classify(
    stats: BinStats,
    sigma: np.ndarray,
    event_margin_db: float = EVENT_MARGIN_DB,
) -> np.ndarray:
    """Label every bin as quiet, sustained or transient.

    Used to colour the navigator so the two event classes are separable at a
    glance, rather than only by reading the band and the spikes against each
    other.

    Neither metric separates the classes on its own, which is worth being
    explicit about because both failure modes are tempting:

    * A continuous call or EOD reaches a high *peak* too -- a sinusoid's
      peak sits 3 dB above its own RMS -- so thresholding the peak alone
      reports sustained signals as transients.
    * A single transient raises its bin's *RMS* substantially when the bin
      is short (about 17 dB for one eel pulse in 40 ms), so thresholding the
      RMS alone reports transients as continuous signal.

    What separates them is how concentrated the energy is, i.e. the crest.
    Baseline noise in a bin of `n` samples already carries a crest of about
    :func:`noise_peak_db`, so that is the natural dividing line: a bin whose
    energy is spread more evenly than noise is continuous, and one whose
    energy is concentrated more than noise is transient.

    At navigator bin sizes a bin holding *both* a sustained signal and a
    transient reads as :data:`TRANSIENT`, because the transient dominates
    the crest.  That is not a failure to be papered over: zooming in shrinks
    the bins, the transient retreats into a few of them, and the stretches
    between resolve as :data:`SUSTAINED`.  A single label per bin cannot say
    more than that honestly.
    """
    peak = peak_excess_db(stats, sigma)
    crest = crest_db(stats, sigma)
    floor = np.asarray(noise_peak_db(stats.n))[:, None]
    active = peak >= floor + event_margin_db
    out = np.full(peak.shape, QUIET, dtype=np.int8)
    out[active & (crest < floor)] = SUSTAINED
    out[active & (crest >= floor)] = TRANSIENT
    return out
