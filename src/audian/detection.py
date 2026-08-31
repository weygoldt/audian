"""Finding the rest of the events, from the few a reader drew by hand.

A reader marks three or four examples of something -- a chirp, a pulse, an
EOD -- and the recording holds hundreds more.  This module is the arithmetic
that finishes the job: it cuts the marked examples out as templates, slides
them along the recording under a normalised cross-correlation, and hands
back the places that matched.  Nothing here imports Qt, so the detector can
be exercised without a window; `audian_detector.py` is its interface half.

Normalised, and why the normalisation is the whole thing
--------------------------------------------------------

A raw correlation answers "how much signal is here", which is a question
about loudness.  What a detector needs is "how much does this *look like*
the template", which is a question about shape, and the difference is the
denominator: the window's own mean and variance, recomputed at every
offset.  With it the score is a correlation coefficient in ``[-1, 1]``,
independent of how loud that stretch happens to be, and a template cut from
a loud chirp still finds the quiet ones.  Without it every threshold becomes
a level control in disguise.

The sliding denominator is the part that is easy to skip and expensive to
get wrong, so it is computed here from running sums -- two convolutions with
a box, giving :math:`\\sum x` and :math:`\\sum x^2` over the window -- rather
than by recomputing a mean per offset.

The threshold is relative, because an absolute one does not survive
-------------------------------------------------------------------

This is the measurement that shaped the module.  The reader's workflow is to
tune on a window they can see and then run on the whole recording, so a
threshold has to mean the same thing in a part of the file they did not
look at.  An absolute correlation cut does not.  Measured on
``data/Gryllus_campestris.wav`` against the reader's own pulse labels,
leave-one-syllable-out, with white noise mixed in at a controlled SNR --
``best`` is the threshold retuned at that noise level, ``held`` the clean
threshold carried over, which is what the reader actually does:

=========  =========================  ======  ======
condition  domain / combiner            best    held
=========  =========================  ======  ======
clean      envelope / mean-scores      1.000   1.000
+10 dB     envelope / mean-scores      1.000   0.675
+5 dB      envelope / mean-scores      1.000   0.000
0 dB       envelope / max-templates    0.963   0.000
-5 dB      waveform / max-templates    0.952   0.000
=========  =========================  ======  ======

Not degraded -- *zero*.  The detector is still perfectly able at 0 dB, but
the number the reader tuned has stopped meaning anything, because the whole
score curve has slid down beneath it.  The best cut moves from about 0.7 on
clean audio to about 0.1 at 0 dB.

So the cut is expressed as `k` robust deviations above the score curve's own
median -- median plus ``k * 1.4826 * MAD``, the MAD scaled to read as a
standard deviation for Gaussian noise.  The same held-out test, with only
that changed:

===============================  ==============  ===============
domain / combiner                 held, absolute  held, relative
===============================  ==============  ===============
envelope / mean-scores                    0.335            0.926
envelope / mean-template                  0.514            0.867
waveform / mean-scores                    0.744            0.861
waveform / max-templates                  0.583            0.816
envelope / max-templates                  0.479            0.735
waveform / mean-template                  0.222            0.272
===============================  ==============  ===============

Every combination improved and the best nearly tripled.  `k` is what the
sensitivity control moves; `MIN_K` and `MAX_K` bound it.

How far that transfer goes, and where it stops
----------------------------------------------

It is worth being exact about what the table above shows, because the
obvious generalisation of it is false.  A relative `k` transfers across
**noise conditions for one template set**.  It does *not* transfer across
**template sets**: on this recording the reader's pulses want ``k = 4.5``
and their syllables about 2.4, since three large heterogeneous syllable
templates peak at 0.546 where eleven small pulse templates reach 0.821.
Carried over unchanged, the pulse setting finds one syllable in three.

So `calibrate_k` reads the cut off the marked examples instead -- the `k`
that clears the weakest of them with `CALIBRATION_MARGIN` to spare.  It
arrives at 4.46 and 1.99 for the pulses, against the 4.5 and 2.0 that the
leave-one-out sweep chose by a completely different route, and at 3.80 and
3.59 for the syllables, where it recovers all three.  Both categories then
report a song of about 3.6 events per second, which is also what grouping
the pulse detections gives.  `DEFAULT_K_SPECTROGRAM` and `DEFAULT_K_TRACE`
remain as the fallback for when there is nothing to calibrate against.

Combining several examples
--------------------------

Given `K` marked examples there are three obvious things to do, and they do
not measure alike.

**Averaging the templates** is safe on a magnitude image and broken on a
waveform.  A cricket pulse is a 4.4 kHz carrier sampled at 96 kHz; averaging
examples that are not aligned to a fraction of that period cancels the
carrier and leaves a blur.  On the spectrogram it ties for first (held-F1
1.000); on the raw trace it scores 0.272 and one fold found nothing at any
threshold.  `combiners_for` therefore does not offer it on the trace.

**Taking the maximum over the templates** wins on clean audio and is the
worst choice everywhere else, for a reason worth stating: the maximum over
`K` curves is also the maximum over `K` draws of the noise.  With no signal
present at all, the background's 99th percentile climbs from 0.062 at
``K=1`` to 0.388 at ``K=11``, a six-fold rise in the floor the signal has to
clear, while the mean's baseline stays flat at -0.003.  Worse, the `k` that
suits it drifts with the noise -- 11.5, 16.0, 10.5, 6.0, 4.5 across the SNR
sweep -- so it is the one combiner whose setting also fails to transfer.

**Averaging the score curves** -- correlate each example separately, then
average the curves -- is what `DEFAULT_COMBINER` is.  It ties for first on
the spectrogram, wins on the trace, and is the only one that is safe in both
domains, which matters because the reader picks the domain.

What the pictures are
---------------------

On the spectrogram the template slides in **time only**.  The band is
already known -- the reader drew it -- and sliding in frequency as well
would only invite a match an octave away from a call that is not there.
With the template spanning the full band, a two-dimensional ``valid``
convolution collapses the frequency axis by itself, so the numerator is one
call rather than a loop over bins.

Three representations are offered.  ``pcen`` measured best and is the
default; ``db`` is what the reader is looking at; ``whitened`` is dB with a
per-bin median removed, the cheap classic.  Held-out F1 averaged over the
SNR sweep: PCEN 1.000, dB 0.981, whitened 0.981.  PCEN earns its keep at the
bottom -- at -5 dB it is the only one that does not sag.

The resolution matters more than any of this
--------------------------------------------

`WINDOWS_PER_EVENT` turned out to be worth more than the choice of
combiner, which is worth writing down because it is the parameter nobody
would think to tune.  Laying three analysis windows across a marked event
rather than eight gives a 23.9 ms cricket pulse a 10.7 ms window, and every
spectrogram combiner drops with it:

=========================  ==========  ==========
combiner                     ``/3``      ``/8``
=========================  ==========  ==========
mean-template                   0.905       0.962
mean-scores                     0.905       0.952
subspace                        0.920       0.949
max-templates                   0.869       0.924
=========================  ==========  ==========

These events are transients, and a window a third as long as the event
spends its resolution on frequency detail the correlation was not asking
for.  With the resolution right the three good combiners land within 0.013
of each other -- inside the noise of a three-fold test on one recording --
so `SUBSPACE` is offered as a real alternative rather than advertised as an
improvement, and `DEFAULT_COMBINER` stays the one that is safe in both
domains.

A caution that belongs in the code rather than in a commit message: that
sweep used **white** noise, which is the friendliest kind for a detector
restricted to a band.  The cricket occupies 3.9-14.9 kHz and everything
outside it is discarded for free.  Interference *inside* the band -- a
second animal, wind, rain -- is not represented in any number above, and
these defaults should be read as a good starting point rather than as a
claim about hard recordings.
"""

from dataclasses import dataclass, field, replace
from typing import Iterable, NamedTuple, Optional, Sequence

import numpy as np
from scipy.signal import find_peaks, hilbert, lfilter, lfilter_zi, oaconvolve

__all__ = [
    "Candidate",
    "Example",
    "Settings",
    "Templates",
    "calibrate_k",
    "combiners_for",
    "default_k",
    "detect",
    "k_from_sensitivity",
    "learn",
    "margin_s",
    "pcen",
    "represent",
    "score_curve",
    "sensitivity_from_k",
    "threshold_of",
]


#: Domains a template can live in.  ``spectrogram`` is the default because
#: it measured better and its `k` is stable across every condition tested.
SPECTROGRAM = "spectrogram"
TRACE = "trace"
DOMAINS = (SPECTROGRAM, TRACE)

#: Spectrogram representations, best first.  See the module docstring for
#: the held-out F1 each of them earned.
REPRESENTATIONS = ("pcen", "db", "whitened")

#: How several examples become one score curve.
MEAN_SCORES = "mean-scores"
MEAN_TEMPLATE = "mean-template"
MAX_TEMPLATES = "max-templates"
SUBSPACE = "subspace"

DEFAULT_COMBINER = MEAN_SCORES

#: How many directions of the template set `SUBSPACE` keeps.  One is the
#: mean template over again; more than the examples can support is fitting
#: their noise.
SUBSPACE_COMPONENTS = 3

#: `k` robust deviations above the score curve's median, per domain.  These
#: differ by more than a factor of two and that is not noise: the two score
#: curves have different backgrounds, so the same `k` is a different cut.
#: Held-out best across the noise sweep was 4.5 on the spectrogram and 2.0
#: on the trace.  Three folds of one recording -- a starting point, not a
#: constant of nature.
DEFAULT_K_SPECTROGRAM = 4.5
DEFAULT_K_TRACE = 2.0
DEFAULT_K = DEFAULT_K_SPECTROGRAM
MIN_K = 0.25
MAX_K = 20.0


def default_k(domain: str) -> float:
    """The `k` the sensitivity control centres on, for this domain."""
    return DEFAULT_K_TRACE if domain == TRACE else DEFAULT_K_SPECTROGRAM

#: How far under the weakest marked example `calibrate_k` puts the cut.
#: Exactly at it would make every example a detection by construction and
#: leave no room for one the reader drew a little loosely.
CALIBRATION_MARGIN = 0.9

#: The sensitivity control is geometric about `DEFAULT_K`, so that the
#: midpoint of the slider is the default and each end is a factor of
#: `SENSITIVITY_SPAN` away from it.
SENSITIVITY_SPAN = 4.0

#: Detections closer together than this fraction of the template duration
#: are the same event seen twice.
NMS_FRACTION = 0.5

#: An event shorter or longer than the marked examples by more than this
#: factor is not one of them.
DURATION_TOLERANCE = 2.5

#: PCEN, at the parameters the bioacoustics literature settled on.
PCEN_ALPHA = 0.8
PCEN_DELTA = 10.0
PCEN_R = 0.25
PCEN_S = 0.025

#: Frames of context a template needs before it, so PCEN's background
#: estimate has settled.  Its time constant is ``1 / PCEN_S`` = 40 frames;
#: this is comfortably over three of those.
PCEN_SETTLE_FRAMES = 128

#: A denominator this small is silence, not shape.
EPS = 1e-12


def combiners_for(domain: str) -> tuple:
    """Which ways of combining examples are honest in this domain.

    Two are missing from the trace deliberately, both because they measured
    badly there rather than because they are unimplemented.

    `MEAN_TEMPLATE`: averaging waveforms that are not phase-aligned to a
    fraction of the carrier period cancels the carrier.  It measured 0.272
    against 0.861 for the alternative, and one fold of the held-out test
    found nothing at any threshold.

    `SUBSPACE`: an envelope template set is already alike, so the first
    direction is the mean template over again and the rest are the
    examples' noise -- scoring against those lets anything resembling the
    noise through.  It measured 0.620 against 0.953 on the trace while
    *winning* on the spectrogram at 0.920, where 3776-dimensional patches
    give the extra directions something real to hold.  Its `k` on the trace
    swings from 1.0 to 8.0 across the noise sweep, which is the same
    signature of a setting that will not transfer.

    An option that is wrong three times out of four is a trap rather than a
    choice, so neither is offered where it loses.
    """
    if domain == TRACE:
        return (MEAN_SCORES, MAX_TEMPLATES)
    return (SUBSPACE, MEAN_SCORES, MEAN_TEMPLATE, MAX_TEMPLATES)


def k_from_sensitivity(sensitivity: float, domain: str = SPECTROGRAM) -> float:
    """The slider's percentage as the `k` the arithmetic actually uses.

    Geometric about the domain's default so that 50 is that default, 0 is
    `SENSITIVITY_SPAN` times stricter and 100 that much looser.  Linear in
    `k` would have put the default at 89% of the way along the slider, which
    reads as a setting already pushed to its limit.  Centring on the domain
    rather than on one number is what keeps the slider meaning the same
    thing after the reader switches between spectrogram and trace.
    """
    s = float(np.clip(sensitivity, 0.0, 100.0))
    k = default_k(domain) * SENSITIVITY_SPAN ** ((50.0 - s) / 50.0)
    return float(np.clip(k, MIN_K, MAX_K))


def sensitivity_from_k(k: float, domain: str = SPECTROGRAM) -> float:
    """`k` back as a slider percentage, so the two controls stay in step."""
    k = float(np.clip(k, MIN_K, MAX_K))
    return float(np.clip(50.0 - 50.0 * np.log(k / default_k(domain))
                         / np.log(SENSITIVITY_SPAN), 0.0, 100.0))


def pcen(power: np.ndarray, s: float = PCEN_S, alpha: float = PCEN_ALPHA,
         delta: float = PCEN_DELTA, r: float = PCEN_R) -> np.ndarray:
    """Per-channel energy normalisation of a ``(freq, time)`` power image.

    Divides each bin by a running estimate of its own background before
    compressing, which is what makes it hold up where dB sags: a bin whose
    floor has risen is measured against the risen floor rather than against
    an absolute one.  The running estimate is a first-order low pass along
    time, run with `lfilter` rather than a Python loop because a long
    recording has millions of frames.
    """
    power = np.asarray(power, dtype=float)
    if power.ndim != 2 or power.shape[1] == 0:
        return power
    b = np.asarray([s])
    a = np.asarray([1.0, -(1.0 - s)])
    # start the filter already settled on the first frame, so the opening
    # of every block is not a transient the detector would score
    zi = lfilter_zi(b, a)[None, :] * power[:, :1]
    smoothed, _ = lfilter(b, a, power, axis=1, zi=zi)
    return (power / (1e-6 + smoothed) ** alpha + delta) ** r - delta ** r


def represent(power: np.ndarray, how: str) -> np.ndarray:
    """A ``(freq, time)`` power image as the thing the detector matches on."""
    if how == "pcen":
        return pcen(power)
    with np.errstate(divide="ignore", invalid="ignore"):
        db = 10.0 * np.log10(np.asarray(power, dtype=float) + 1e-20)
    if how == "whitened":
        return db - np.median(db, axis=1, keepdims=True)
    if how == "db":
        return db
    raise ValueError(f"unknown representation {how!r}")


def _window_sum(flat: np.ndarray, n: int) -> np.ndarray:
    """The sum over every window of `n`, by one convolution with a box.

    The sliding denominator is built from these rather than from a mean
    recomputed per offset, which is the difference between a detector that
    runs on a whole recording and one that does not.
    """
    return oaconvolve(flat, np.ones(n), mode="valid")


def _sliding_norm(sum_x: np.ndarray, sum_xx: np.ndarray, count: int):
    """The window's own spread, which is what makes the score a coefficient.

    Kept apart from the numerator because it depends only on the signal.
    With `K` examples averaged into one curve the numerator runs `K` times
    and this must not: computing it inside each correlation measured 3.34 s
    against 1.66 s for eleven templates over the reference recording, for
    exactly the same answer.
    """
    return np.sqrt(np.maximum(sum_xx - sum_x * sum_x / count, EPS))


def _scores_1d(signal: np.ndarray, patches: Sequence[np.ndarray]) -> list:
    """One correlation curve per template, over a waveform or envelope."""
    n = patches[0].size
    if signal.size < n:
        return []
    den = _sliding_norm(_window_sum(signal, n),
                        _window_sum(signal * signal, n), n)
    out = []
    for p in patches:
        t = p - p.mean()
        tn = float(np.sqrt(np.sum(t * t)))
        if tn < EPS:
            out.append(np.zeros(den.size))
            continue
        out.append(oaconvolve(signal, t[::-1], mode="valid") / (den * tn))
    return out


def _scores_patch(image: np.ndarray, patches: Sequence[np.ndarray]) -> list:
    """One curve per template, slid along the time axis of a spectrogram.

    The template spans the whole band, so a two-dimensional ``valid``
    convolution already collapses frequency and leaves one value per time
    offset -- the numerator is a single call rather than a loop over bins.
    The denominator only ever needs column sums, so frequency collapses
    there first and the running sums stay one-dimensional.  Summing the
    squares is not the square of the sum, and confusing the two makes a
    "correlation" that runs to six figures rather than to one.
    """
    nf, n = patches[0].shape
    if image.shape[0] != nf:
        raise ValueError("template and image must cover the same bins")
    if image.shape[1] < n:
        return []
    den = _sliding_norm(_window_sum(image.sum(axis=0), n),
                        _window_sum((image * image).sum(axis=0), n), nf * n)
    out = []
    for p in patches:
        t = p - p.mean()
        tn = float(np.sqrt(np.sum(t * t)))
        if tn < EPS:
            out.append(np.zeros(den.size))
            continue
        out.append(oaconvolve(image, t[::-1, ::-1], mode="valid")[0]
                   / (den * tn))
    return out


class Example(NamedTuple):
    """One span a reader marked, in seconds and hertz."""

    t0: float
    t1: float
    f0: Optional[float] = None
    f1: Optional[float] = None


@dataclass
class Settings:
    """Everything the reader can turn, and what it does.

    Held apart from `Templates` because the reader retunes these constantly
    while the templates stay put -- rescoring on a slider move must not mean
    recutting the examples.
    """

    domain: str = SPECTROGRAM
    representation: str = "pcen"
    combiner: str = DEFAULT_COMBINER
    #: `None` means the domain's own default.  It is not a float with a
    #: default value because the right `k` differs between the domains by a
    #: factor of two, so one number is wrong for whichever domain did not
    #: pick it -- `Settings(domain=TRACE)` inheriting 4.5 put the cut above
    #: the highest score the trace can produce, and found nothing at all.
    k: Optional[float] = None
    #: Bounds on how long a detection may be, as a factor of the median
    #: marked example.  `None` on either side means "do not check".
    duration_tolerance: float = DURATION_TOLERANCE
    #: Detections separated by less than this are merged.  `None` or zero
    #: does not merge at all, which is the default: see `_tidy` for why a
    #: gap derived from the template duration eats pulse trains.
    merge_gap_s: Optional[float] = None
    #: Reject matches sitting in near-silence.  Nearly inert on a clean
    #: recording -- the correlation is already level-independent, so there is
    #: nothing for it to reject -- and load-bearing on a noisy one.
    power_floor_db: Optional[float] = None
    #: Spectrogram resolution.  Derived from the examples when left `None`,
    #: rather than inherited from the display, so that changing how the
    #: picture looks does not silently change what is detected.
    nfft: Optional[int] = None
    hop: Optional[int] = None

    def normalized(self) -> "Settings":
        """This, resolved: a `k` for the domain and a combiner it allows.

        Idempotent, and every entry point runs it, so no code below here
        has to wonder whether `k` is a number yet.
        """
        allowed = combiners_for(self.domain)
        combiner = self.combiner if self.combiner in allowed else allowed[0]
        k = default_k(self.domain) if self.k is None else float(self.k)
        if combiner == self.combiner and k == self.k:
            return self
        return replace(self, combiner=combiner, k=k)


@dataclass
class Templates:
    """The marked examples, cut out and ready to slide.

    Carries the band and the duration they were drawn with, because almost
    every other parameter is derived from those rather than invented: the
    frequency range to match in, how long a detection may be, how close two
    may sit, and how much of the neighbouring block a streamed run must see.
    """

    patches: list = field(default_factory=list)
    domain: str = SPECTROGRAM
    representation: str = "pcen"
    rate: float = 1.0
    duration_s: float = 0.0
    f_low_hz: Optional[float] = None
    f_high_hz: Optional[float] = None
    nfft: Optional[int] = None
    hop: Optional[int] = None
    channel: Optional[int] = None

    def __len__(self) -> int:
        return len(self.patches)

    @property
    def ok(self) -> bool:
        """Whether there is anything to detect with."""
        return len(self.patches) > 0 and self.duration_s > 0.0


class Candidate(NamedTuple):
    """One place the recording looked like the examples."""

    t0: float
    t1: float
    score: float
    f_low_hz: Optional[float] = None
    f_high_hz: Optional[float] = None
    channel: Optional[int] = None


def _band_of(examples: Sequence[Example]) -> tuple:
    """The band to match in, as the median of the ones drawn.

    Median rather than union: one example dragged a little too tall should
    widen the search by nothing, and a union over five examples is the
    tallest of them by construction.
    """
    lows = [e.f0 for e in examples if e.f0 is not None]
    highs = [e.f1 for e in examples if e.f1 is not None]
    if not lows or not highs:
        return None, None
    return float(np.median(lows)), float(np.median(highs))


#: How many analysis windows to lay across a marked event, and how many
#: frames.  The window count is the one that matters: at `/3` a 23.9 ms
#: cricket pulse got a 10.7 ms window and the spectrogram scored 0.920
#: held-F1, below the trace's 0.953; the events these templates describe are
#: transients, and a window a third as long as the event trades away exactly
#: the time detail the correlation is looking for.
WINDOWS_PER_EVENT = 8
FRAMES_PER_EVENT = 32


def _resolution_for(duration_s: float, rate: float) -> tuple:
    """An nfft and hop that put enough frames across the shortest example."""
    span = duration_s * rate
    n = max(int(2 ** np.round(np.log2(max(span / WINDOWS_PER_EVENT, 8.0)))), 16)
    hop = max(int(round(span / FRAMES_PER_EVENT)), 1)
    return int(n), int(min(hop, n))


def margin_s(templates: Templates) -> float:
    """How much of the neighbouring block a streamed run has to see.

    A template straddling a block edge is a detection nobody gets unless the
    block is read with this much overlap on each side.
    """
    return float(max(templates.duration_s, 0.0) * 2.0)


def _spectrogram_of(samples: np.ndarray, rate: float, nfft: int, hop: int):
    """A ``(freq, time)`` power image and its axes, via thunderlab.

    The same routine `bufferedspectrogram` draws with, so the detector and
    the picture cannot drift apart in their idea of what a bin is.  Its
    `times` are segment *centres*; the templates are cut by the same axis
    they are later scored against, so the two agree and no half-window
    correction is owed.  Getting that wrong is a silent miss of every event
    rather than a visible error, which is why it is written down here.

    Both `freq_resolution` and `overlap_frac` must be passed as `None`.
    They are not merely defaults -- each *overrides* the explicit `n_fft` and
    `n_overlap` when set, so leaving them alone silently substitutes a 1 Hz
    resolution: 65537 bins and, across this recording, 25 frames in 18
    seconds instead of the 23922 asked for.  Nothing raises; the templates
    simply come back empty.
    """
    from thunderlab.powerspectrum import spectrogram

    freqs, times, power = spectrogram(
        np.asarray(samples, dtype=float), rate,
        freq_resolution=None, overlap_frac=None,
        n_fft=int(nfft), n_overlap=int(max(nfft - hop, 0)))
    return np.asarray(power), np.asarray(freqs), np.asarray(times)


def _band_slice(freqs: np.ndarray, f0, f1) -> np.ndarray:
    if f0 is None or f1 is None:
        return np.ones(freqs.size, dtype=bool)
    keep = (freqs >= f0) & (freqs <= f1)
    return keep if keep.any() else np.ones(freqs.size, dtype=bool)


def learn(samples: np.ndarray, rate: float, examples: Iterable[Example],
          settings: Settings, t_offset: float = 0.0) -> Templates:
    """Cut the marked examples out of `samples` as templates.

    `t_offset` is the time of the first sample, so that example times taken
    from labels -- which are absolute -- land in the right place in a block
    that starts somewhere else.
    """
    settings = settings.normalized()
    examples = [e for e in examples if e.t1 > e.t0]
    if not examples:
        return Templates(domain=settings.domain, rate=rate,
                         representation=settings.representation)

    duration = float(np.median([e.t1 - e.t0 for e in examples]))
    f0, f1 = _band_of(examples)
    samples = np.asarray(samples, dtype=float)

    if settings.domain == TRACE:
        n = max(int(round(duration * rate)), 2)
        patches = []
        for e in examples:
            i = int(round((e.t0 - t_offset) * rate))
            if i < 0 or i + n > samples.size:
                continue
            # the envelope rather than the waveform: a correlation against a
            # carrier is a correlation against its phase, and the phase of
            # the next pulse is not the phase of this one.  Taken over a
            # padded slice, because `hilbert` of a whole recording is a
            # transform of millions of samples to keep a few thousand.
            pad = min(n, i, samples.size - i - n)
            piece = np.abs(hilbert(samples[i - pad:i + n + pad]))
            patches.append(piece[pad:pad + n])
        return Templates(patches=patches, domain=TRACE, rate=rate,
                         representation=settings.representation,
                         duration_s=duration, f_low_hz=f0, f_high_hz=f1)

    nfft, hop = _resolution_for(duration, rate)
    nfft = settings.nfft or nfft
    hop = settings.hop or hop
    nt = max(int(round(duration * rate / hop)), 2)
    # enough context for PCEN's background estimate to have settled before
    # the window reaches the example.  Its smoother has a time constant of
    # 1/PCEN_S frames, so a template cut from an unsettled opening would be
    # matched against blocks where the same filter had long since converged
    # -- the same sound, described differently.
    pad = int(nfft + hop * PCEN_SETTLE_FRAMES)
    patches = []
    for e in examples:
        i = int(round((e.t0 - t_offset) * rate))
        lo = max(i - pad, 0)
        hi = min(i + int(duration * rate) + pad, samples.size)
        if hi - lo < nfft * 2:
            continue
        power, freqs, times = _spectrogram_of(samples[lo:hi], rate, nfft, hop)
        keep = _band_slice(freqs, f0, f1)
        image = represent(power[keep], settings.representation)
        j = int(np.searchsorted(times, (i - lo) / rate))
        if j + nt <= image.shape[1]:
            patches.append(image[:, j:j + nt])
    if patches:
        # a ragged patch would be scored against a mismatched image
        rows = min(p.shape[0] for p in patches)
        patches = [p[:rows] for p in patches]
    return Templates(patches=patches, domain=SPECTROGRAM, rate=rate,
                     representation=settings.representation,
                     duration_s=duration, f_low_hz=f0, f_high_hz=f1,
                     nfft=nfft, hop=hop)


def score_curve(samples: np.ndarray, rate: float, templates: Templates,
                settings: Settings, t_offset: float = 0.0):
    """Score every offset of `samples`, as ``(score, times, level_db)``.

    `times` are the **onsets** a match would have: a ``valid`` correlation
    index is the offset the template starts at, and the templates were cut
    at the marked onsets, so the two agree without a correction.  Getting
    that half a template wrong is a silent miss of every event.

    `level_db` is the windowed level at the same offsets, for the power
    gate; `None` when no gate is asked for.
    """
    if not templates.ok:
        return np.zeros(0), np.zeros(0), None
    samples = np.asarray(samples, dtype=float)
    settings = settings.normalized()

    if templates.domain == TRACE:
        signal = np.abs(hilbert(samples))
        n = templates.patches[0].size
        if signal.size < n:
            return np.zeros(0), np.zeros(0), None
        score = _combine(_scores_1d(signal, _to_score(templates, settings)),
                         settings.combiner)
        if score.size == 0:
            return np.zeros(0), np.zeros(0), None
        times = t_offset + np.arange(score.size) / rate
        level = None
        if settings.power_floor_db is not None:
            s2 = _window_sum(samples * samples, n)
            rms = np.sqrt(np.maximum(s2 / n, EPS))
            with np.errstate(divide="ignore"):
                level = 20.0 * np.log10(np.maximum(rms, EPS)
                                        / max(np.abs(samples).max(), EPS))
        return score, times, level

    nfft = templates.nfft or settings.nfft
    hop = templates.hop or settings.hop
    power, freqs, times_ax = _spectrogram_of(samples, rate, nfft, hop)
    keep = _band_slice(freqs, templates.f_low_hz, templates.f_high_hz)
    band = power[keep]
    if band.shape[0] != templates.patches[0].shape[0]:
        # the block gave a different number of bins than the examples did;
        # trim both to the shorter rather than scoring a mismatched patch
        m = min(band.shape[0], templates.patches[0].shape[0])
        band = band[:m]
        templates = replace(templates, patches=[p[:m] for p in templates.patches])
    image = represent(band, templates.representation)
    n = templates.patches[0].shape[1]
    if image.shape[1] < n:
        return np.zeros(0), np.zeros(0), None
    score = _combine(_scores_patch(image, _to_score(templates, settings)),
                     settings.combiner)
    if score.size == 0:
        return np.zeros(0), np.zeros(0), None
    times = t_offset + times_ax[:score.size]
    level = None
    if settings.power_floor_db is not None:
        band_power = band.sum(axis=0)
        s1 = _window_sum(band_power, n)
        with np.errstate(divide="ignore"):
            level = 10.0 * np.log10(np.maximum(s1 / n, EPS)
                                    / max(band_power.max(), EPS))
        level = level[:score.size]
    return score, times, level


def _subspace_basis(patches: Sequence[np.ndarray],
                    components: int = SUBSPACE_COMPONENTS) -> list:
    """An orthonormal basis for what the marked examples have in common.

    Averaging asks the examples to agree on one shape.  This asks only that
    they span a small space, and scores a window by how much of it lies in
    that space -- which is the honest generalisation when the examples
    differ, and the reader's own pulses differ by a factor of 2.3 in length.

    Each patch is zero-meaned first, because that is what the correlation
    does to its window; the basis is then already zero-mean, so the squared
    correlations against it add up to a squared cosine and the combined
    score stays in ``[0, 1]`` like every other one here.

    `numpy.linalg.svd` rather than `sklearn.decomposition.PCA`: PCA centres
    across the examples, which removes the mean template -- the very
    direction most of the signal is in -- and its `TruncatedSVD` sibling
    wraps the same LAPACK call this does in one line.
    """
    flat = np.stack([np.asarray(p, dtype=float).ravel() for p in patches])
    flat = flat - flat.mean(axis=1, keepdims=True)
    keep = min(int(components), flat.shape[0])
    if keep < 1:
        return []
    _, _, vt = np.linalg.svd(flat, full_matrices=False)
    shape = np.asarray(patches[0]).shape
    return [vt[i].reshape(shape) for i in range(keep)]


def _to_score(templates: Templates, settings: Settings) -> list:
    """The patches to correlate, which is not always the ones that were cut.

    `MEAN_TEMPLATE` averages first and correlates once, `SUBSPACE`
    correlates against a handful of directions instead of every example,
    and the rest correlate them all.  Deciding here rather than inside the
    correlation keeps every path down to a single sliding denominator.
    """
    patches = list(templates.patches)
    if len(patches) < 2:
        return patches
    if settings.combiner == MEAN_TEMPLATE:
        return [np.mean(patches, axis=0)]
    if settings.combiner == SUBSPACE:
        return _subspace_basis(patches) or patches
    return patches


def _combine(curves: list, how: str) -> np.ndarray:
    """One score curve out of the several the examples produced."""
    if not curves:
        return np.zeros(0)
    if how == SUBSPACE:
        # the squared cosine onto the span of the basis, back as a cosine
        return np.sqrt(np.clip(np.sum(np.square(np.stack(curves)), axis=0),
                               0.0, 1.0))
    if len(curves) == 1:
        return curves[0]
    stack = np.stack(curves)
    if how == MAX_TEMPLATES:
        return stack.max(axis=0)
    return stack.mean(axis=0)


def threshold_of(score: np.ndarray, k: float) -> float:
    """`k` robust deviations above the curve's own median.

    The MAD is scaled by 1.4826 so that `k` reads as standard deviations for
    Gaussian background, which is what makes a value learned on one
    recording mean roughly the same on the next.
    """
    if score.size == 0:
        return 0.0
    med = float(np.median(score))
    mad = float(np.median(np.abs(score - med))) * 1.4826
    return med + float(k) * max(mad, EPS)


def calibrate_k(score: np.ndarray, times: np.ndarray,
                examples: Iterable[Example], templates: Templates,
                margin: float = CALIBRATION_MARGIN) -> Optional[float]:
    """The `k` that puts the cut just under the weakest marked example.

    A detector that cannot find the events it was handed is set too high,
    and the examples say so without the reader having to discover it by
    dragging a slider.  This reads their scores off the curve and returns
    the `k` that clears the worst of them with `margin` to spare.

    It exists because the relative threshold transfers less far than the
    noise sweep suggested.  Holding `k` across changing SNR works -- that
    was 0.335 against 0.926 -- but holding it across a *different template
    set* does not: on this recording the reader's pulses want 4.5 and their
    syllables want about 2.4, because three large heterogeneous templates
    peak at 0.546 where eleven small ones reach 0.821.  Tuned on the pulses
    and applied to the syllables, `k = 4.5` finds one of three; calibrated,
    it finds all three and lands on 3.62 events per second, which is what
    the pulse detector independently reports for the same song.

    `None` when nothing can be measured, so the caller keeps its default.
    """
    if score.size == 0 or times.size == 0:
        return None
    reach = max(templates.duration_s * NMS_FRACTION, EPS)
    peaks = []
    for e in examples:
        lo = int(np.searchsorted(times, e.t0 - reach))
        hi = int(np.searchsorted(times, e.t0 + reach)) + 1
        lo, hi = max(lo, 0), min(max(hi, lo + 1), score.size)
        if hi > lo:
            peaks.append(float(score[lo:hi].max()))
    if not peaks:
        return None
    med = float(np.median(score))
    mad = float(np.median(np.abs(score - med))) * 1.4826
    if mad < EPS:
        return None
    return float(np.clip((min(peaks) * margin - med) / mad, MIN_K, MAX_K))


def _extent(times: np.ndarray, peak: int, duration_s: float) -> tuple:
    """A peak as a span: the template's own length, laid down at the peak.

    A ``valid`` correlation index is the offset the template *starts* at and
    the templates were cut at the marked onsets, so the peak is an onset and
    the span is one template long.  This is the extent the whole evaluation
    was done against, and it is what scored 1.000.

    Reading the extent off the score curve's own shoulders was tried first
    and is worse in both directions: on the spectrogram the shoulders close
    early and give 10.5 ms for a 23.9 ms pulse, and on the smooth envelope
    curve they do not close at all, so a syllable of four pulses comes back
    as one 100 ms event.  Estimating onset and offset properly wants the
    signal's own energy, not the correlation's, and that is a refinement
    rather than a default.
    """
    t0 = float(times[peak])
    return t0, t0 + float(duration_s)


def _pick(score: np.ndarray, times: np.ndarray, level, templates: Templates,
          settings: Settings) -> list:
    """Peaks of a score curve as candidate events."""
    if score.size == 0:
        return []
    settings = settings.normalized()
    cut = threshold_of(score, settings.k)
    rate = 1.0 / max(np.median(np.diff(times)), EPS) if times.size > 1 else 1.0
    distance = max(int(templates.duration_s * NMS_FRACTION * rate), 1)
    peaks, _ = find_peaks(score, height=cut, distance=distance)
    if settings.power_floor_db is not None and level is not None:
        peaks = peaks[level[np.minimum(peaks, level.size - 1)]
                      > settings.power_floor_db]

    out = []
    for p in peaks:
        t0, t1 = _extent(times, int(p), templates.duration_s)
        out.append(Candidate(t0, t1, float(score[p]),
                             templates.f_low_hz, templates.f_high_hz,
                             templates.channel))
    return _tidy(out, templates, settings)


def _tidy(found: list, templates: Templates, settings: Settings) -> list:
    """Merge what is one event, drop what cannot be one at all."""
    if not found:
        return []
    found = sorted(found, key=lambda c: c.t0)
    # off unless asked for.  Suppressing a peak that is really the same
    # event seen twice is already the non-maximum step's job, and a merge
    # wide enough to be worth having is wider than the silence inside a
    # pulse train: at a gap of half a template the four pulses of a cricket
    # syllable, which sit 2-6 ms apart, become one 100 ms event that the
    # duration check then throws away.  Merging is for grouping, and
    # grouping is a decision the reader makes.
    gap = settings.merge_gap_s or 0.0
    if gap <= 0.0:
        return _within_duration(found, templates, settings)
    merged = [found[0]]
    for c in found[1:]:
        last = merged[-1]
        if c.t0 - last.t1 < gap:
            merged[-1] = last._replace(t1=max(last.t1, c.t1),
                                       score=max(last.score, c.score))
        else:
            merged.append(c)
    return _within_duration(merged, templates, settings)


def _within_duration(found: list, templates: Templates,
                     settings: Settings) -> list:
    """Drop what is too short or too long to be one of the marked events.

    Only ever bites after a merge, since an unmerged detection is exactly
    one template long by construction.  That is the point: it is the check
    that catches a merge which swallowed a whole pulse train.
    """
    tol = settings.duration_tolerance
    if not tol:
        return found
    lo = templates.duration_s / tol
    hi = templates.duration_s * tol
    return [c for c in found if lo <= (c.t1 - c.t0) <= hi]


def detect(samples: np.ndarray, rate: float, templates: Templates,
           settings: Settings, t_offset: float = 0.0) -> list:
    """Every place in `samples` that looked like the marked examples."""
    if not templates.ok:
        return []
    settings = settings.normalized()
    score, times, level = score_curve(samples, rate, templates, settings,
                                      t_offset)
    return _pick(score, times, level, templates, settings)
