"""What the few-shot detector promises, pinned against a real recording.

Runs without a window::

    .venv-qt6/bin/python -m pytest tests/test_detection.py -q

`audian.detection` imports no Qt, so nothing here builds one -- the same
split `tests/test_smoothing.py` states its reasons for.  Every claim below
is about an array.

The fixture is the reader's own hand annotation of
``data/Gryllus_campestris.wav``, copied to ``tests/data`` rather than read
from ``data/`` because the sidecar beside the recording is rewritten by
``scripts/smoke_test.py --interact`` and a test that reads it would fail
whenever somebody had been clicking around.

Two categories, and the pair is the point.  ``pulse`` is eleven examples of
a single 23.9 ms sound pulse; ``syllable`` is three examples of the ~103 ms
chirp that four of those pulses make up.  They nest, so they check each
other: a pulse detector that works must find three or four pulses inside
every syllable, and both detectors must agree with each other about how
often this cricket sings.  They also differ enough to be a real test --
eleven small templates against three large ones, 2.3x spread in pulse
length -- which is where a detector tuned on one and applied to the other
comes apart.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import numpy as np
import pytest
from scipy.io import wavfile

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from audian import detection  # noqa: E402

RECORDING = REPO / "data" / "Gryllus_campestris.wav"
LABELS = Path(__file__).resolve().parent / "data" / "gryllus-fewshot-labels.csv"

#: Onsets agree to a third of a pulse.  Tighter measures the labeller's
#: hand rather than the detector.
ONSET_TOL_S = 0.008


# ------------------------------------------------------------- the fixtures


@pytest.fixture(scope="module")
def recording():
    """The reference recording, as the mean the labels were drawn on.

    `Label.on_channel` reads an empty channel as "made on the mean
    spectrogram, belongs on every lane", and every row of the fixture has
    one, so the mean is the signal these examples describe.
    """
    if not RECORDING.exists():
        pytest.skip(f"{RECORDING} is not in this checkout")
    rate, data = wavfile.read(RECORDING)
    data = data.astype(np.float64)
    data = data.mean(axis=1) if data.ndim > 1 else data
    return float(rate), data / np.max(np.abs(data))


@pytest.fixture(scope="module")
def examples():
    """The hand-drawn spans, by category."""
    out = {}
    with LABELS.open() as fh:
        for row in csv.DictReader(fh):
            out.setdefault(row["category"], []).append(
                detection.Example(float(row["t_start_s"]), float(row["t_end_s"]),
                                  float(row["f_low_hz"]), float(row["f_high_hz"])))
    return out


@pytest.fixture(scope="module")
def scored(recording, examples):
    """Score curves for both categories in both domains, computed once.

    Scoring the whole recording costs about 0.6 s on the spectrogram and
    1.3 s on the trace, and most of the claims below are about what a
    threshold does to a curve rather than about the curve itself.
    """
    rate, data = recording
    out = {}
    for category in ("pulse", "syllable"):
        for domain in detection.DOMAINS:
            settings = detection.Settings(domain=domain)
            templates = detection.learn(data, rate, examples[category], settings)
            score, times, level = detection.score_curve(
                data, rate, templates, settings)
            out[(category, domain)] = (templates, score, times, level)
    return out


def _recovered(found, wanted, tol=ONSET_TOL_S):
    return sum(any(abs(c.t0 - e.t0) < tol for c in found) for e in wanted)


# --------------------------------------------------------------- the choices


def test_the_trace_is_not_offered_the_combiners_that_fail_on_it():
    """Averaging waveforms cancels their carrier, so it is not a choice.

    Both exclusions measured badly rather than being unimplemented:
    mean-template 0.272 against 0.861, subspace 0.620 against 0.953.
    """
    trace = detection.combiners_for(detection.TRACE)
    assert detection.MEAN_TEMPLATE not in trace
    assert detection.SUBSPACE not in trace
    assert detection.MEAN_SCORES in trace
    spectrogram = detection.combiners_for(detection.SPECTROGRAM)
    assert detection.MEAN_TEMPLATE in spectrogram
    assert detection.SUBSPACE in spectrogram


def test_asking_for_a_combiner_the_domain_refuses_falls_back_rather_than_raises():
    """A stale setting restored from a file must not stop the panel."""
    settings = detection.Settings(domain=detection.TRACE,
                                  combiner=detection.MEAN_TEMPLATE)
    assert settings.normalized().combiner in detection.combiners_for(detection.TRACE)


@pytest.mark.parametrize("domain", detection.DOMAINS)
def test_the_middle_of_the_sensitivity_slider_is_the_domain_default(domain):
    """50% means the default in whichever domain the reader is in."""
    k = detection.k_from_sensitivity(50.0, domain)
    assert k == pytest.approx(detection.default_k(domain))
    assert detection.sensitivity_from_k(k, domain) == pytest.approx(50.0)


@pytest.mark.parametrize("sensitivity", [0.0, 12.5, 37.0, 50.0, 88.0, 100.0])
@pytest.mark.parametrize("domain", detection.DOMAINS)
def test_sensitivity_and_k_are_inverses_of_each_other(domain, sensitivity):
    """The slider and the number beside it must never disagree."""
    k = detection.k_from_sensitivity(sensitivity, domain)
    assert detection.sensitivity_from_k(k, domain) == pytest.approx(sensitivity)


def test_more_sensitivity_means_a_lower_cut():
    """The control has to move the way its name says."""
    ks = [detection.k_from_sensitivity(s) for s in (0.0, 25.0, 50.0, 75.0, 100.0)]
    assert ks == sorted(ks, reverse=True)


def test_the_trace_does_not_inherit_the_spectrograms_threshold():
    """The two domains want `k` a factor of two apart.

    Inheriting 4.5 put the cut at 1.397 on a curve that cannot exceed 1.0,
    and the detector found nothing at all -- silently, which is the part
    that makes it worth a test.
    """
    trace = detection.Settings(domain=detection.TRACE).normalized()
    spectrogram = detection.Settings(domain=detection.SPECTROGRAM).normalized()
    assert trace.k == detection.DEFAULT_K_TRACE
    assert spectrogram.k == detection.DEFAULT_K_SPECTROGRAM
    assert trace.k != spectrogram.k
    # and an explicit k is still the reader's to set
    assert detection.Settings(domain=detection.TRACE, k=3.0).normalized().k == 3.0


def test_normalizing_settings_twice_changes_nothing():
    """Every entry point normalises, so it must be idempotent."""
    once = detection.Settings(domain=detection.TRACE,
                              combiner=detection.MEAN_TEMPLATE).normalized()
    assert once.normalized() == once


# ------------------------------------------------------- the correlation


def test_a_template_scores_one_against_itself():
    """The normalisation has to make the score a correlation coefficient."""
    rng = np.random.default_rng(4)
    signal = rng.normal(size=4000)
    template = signal[1000:1200]
    curve = detection._scores_1d(signal, [template])[0]
    assert curve.max() == pytest.approx(1.0, abs=1e-6)
    assert np.argmax(curve) == 1000


def test_the_score_never_leaves_minus_one_to_one():
    """A six-figure "correlation" is a denominator bug, and was one."""
    rng = np.random.default_rng(5)
    signal = rng.normal(size=8000) * 30.0 + 11.0
    image = rng.normal(size=(24, 700)) * 5.0 - 3.0
    flat = detection._scores_1d(signal, [signal[200:400]])[0]
    patch = detection._scores_patch(image, [image[:, 100:140]])[0]
    for curve in (flat, patch):
        assert curve.min() >= -1.0 - 1e-9
        assert curve.max() <= 1.0 + 1e-9


def test_the_score_ignores_how_loud_the_recording_is():
    """A template cut from a loud event must still find the quiet ones.

    This is the whole reason for the sliding denominator: without it the
    threshold is a level control wearing a different name.
    """
    rng = np.random.default_rng(6)
    signal = rng.normal(size=6000)
    template = signal[2000:2300].copy()
    signal[2000:2300] *= 0.01
    curve = detection._scores_1d(signal, [template])[0]
    assert curve[2000] == pytest.approx(1.0, abs=1e-6)


def test_a_flat_stretch_does_not_divide_by_its_own_silence():
    """Digital silence has no shape, and must not produce a match."""
    signal = np.concatenate([np.zeros(2000), np.random.default_rng(7).normal(size=2000)])
    curve = detection._scores_1d(signal, [np.ones(200)])[0]
    assert np.isfinite(curve).all()


def test_the_subspace_score_is_bounded_like_the_others():
    """It is a cosine onto a span, so it shares the [0, 1] the rest use."""
    rng = np.random.default_rng(8)
    image = rng.normal(size=(20, 900))
    patches = [image[:, i:i + 30] for i in (100, 300, 500, 700)]
    basis = detection._subspace_basis(patches)
    assert len(basis) == detection.SUBSPACE_COMPONENTS
    curves = detection._scores_patch(image, basis)
    combined = detection._combine(curves, detection.SUBSPACE)
    assert combined.min() >= 0.0
    assert combined.max() <= 1.0 + 1e-9


# ------------------------------------------------------ the analysis window


def test_the_spectrogram_honours_the_resolution_it_is_asked_for():
    """thunderlab's `freq_resolution` overrides `n_fft` unless silenced.

    Left at its default it substituted 1 Hz bins: 65537 of them, and 25
    frames across eighteen seconds instead of the 23922 wanted.  Nothing
    raised -- the templates simply came back empty.
    """
    rng = np.random.default_rng(9)
    samples = rng.normal(size=200_000)
    power, freqs, times = detection._spectrogram_of(samples, 96000.0, 256, 64)
    assert freqs.size == 129, f"asked for nfft 256, got {freqs.size} bins"
    assert times.size > 3000, f"asked for hop 64, got {times.size} frames"


def test_the_window_is_short_enough_to_resolve_the_event():
    """Three windows across an event measured worse than eight.

    A window a third as long as the event spends resolution on frequency
    detail the correlation did not ask for, and every spectrogram combiner
    lost about five points of F1 to it.
    """
    nfft, hop = detection._resolution_for(0.0239, 96000.0)
    assert nfft / 96000.0 < 0.0239 / 4, "the window is longer than a quarter event"
    assert 0.0239 * 96000.0 / hop >= 16, "too few frames across the event"


# ------------------------------------------------- against the real labels


@pytest.mark.parametrize("domain", detection.DOMAINS)
def test_the_examples_are_cut_out_with_the_length_and_band_they_were_drawn_with(
        recording, examples, scored, domain):
    """Almost every other parameter is derived from these two numbers."""
    templates, _, _, _ = scored[("pulse", domain)]
    assert len(templates) == len(examples["pulse"]) == 11
    assert templates.duration_s == pytest.approx(0.0239, abs=0.001)
    assert 3800 < templates.f_low_hz < 4100
    assert 14000 < templates.f_high_hz < 15200


@pytest.mark.parametrize("domain", detection.DOMAINS)
def test_every_hand_labelled_pulse_is_found_again(recording, examples, scored, domain):
    """The weakest possible claim, and the one that catches most breakage.

    A detector that cannot find the events it was handed is not a detector,
    whatever else it finds.
    """
    templates, score, times, level = scored[("pulse", domain)]
    k = detection.calibrate_k(score, times, examples["pulse"], templates)
    settings = detection.Settings(domain=domain, k=k)
    found = detection._pick(score, times, level, templates, settings)
    assert _recovered(found, examples["pulse"]) == 11


@pytest.mark.parametrize("domain", detection.DOMAINS)
def test_the_detected_pulses_fall_into_the_syllables_the_reader_drew(
        recording, examples, scored, domain):
    """The two categories nest, so each is a check on the other.

    The reader marked four pulses in the first syllable, four in the second
    and three in the third; a pulse detector that has understood the song
    reports the same, without having been shown the syllables at all.
    """
    templates, score, times, level = scored[("pulse", domain)]
    k = detection.calibrate_k(score, times, examples["pulse"], templates)
    found = detection._pick(score, times, level, templates,
                            detection.Settings(domain=domain, k=k))
    for syllable in examples["syllable"]:
        inside = [c for c in found
                  if syllable.t0 - 0.02 <= c.t0 <= syllable.t1 + 0.02]
        drawn = [e for e in examples["pulse"]
                 if syllable.t0 - 1e-3 <= e.t0 and e.t1 <= syllable.t1 + 1e-3]
        assert len(inside) == pytest.approx(len(drawn), abs=1), (
            f"syllable at {syllable.t0:.3f}s: found {len(inside)}, "
            f"reader drew {len(drawn)}")


@pytest.mark.parametrize("domain", detection.DOMAINS)
def test_both_categories_agree_about_how_often_the_cricket_sings(
        recording, examples, scored, domain):
    """Two template sets, two event sizes, one song.

    Detecting syllables directly and grouping detected pulses into chirps
    are independent routes to the same number, so their disagreeing is a
    real signal that one of them is wrong.
    """
    rate, data = recording
    duration = data.size / rate

    templates, score, times, level = scored[("syllable", domain)]
    k = detection.calibrate_k(score, times, examples["syllable"], templates)
    syllables = detection._pick(score, times, level, templates,
                                detection.Settings(domain=domain, k=k))

    templates, score, times, level = scored[("pulse", domain)]
    k = detection.calibrate_k(score, times, examples["pulse"], templates)
    pulses = detection._pick(score, times, level, templates,
                             detection.Settings(domain=domain, k=k))
    starts = np.array(sorted(c.t0 for c in pulses))
    chirps = 1 + int(np.sum(np.diff(starts) > 0.050))

    assert syllables, "no syllables detected at the calibrated threshold"
    direct = len(syllables) / duration
    grouped = chirps / duration
    assert direct == pytest.approx(grouped, rel=0.35), (
        f"{direct:.2f}/s from syllable templates against "
        f"{grouped:.2f}/s from grouping pulses")
    assert 1.5 < direct < 8.0, f"{direct:.2f} chirps/s is not a cricket"


# --------------------------------------------------------- the threshold


@pytest.mark.parametrize("category", ["pulse", "syllable"])
def test_the_threshold_calibrated_from_the_examples_finds_the_examples(
        recording, examples, scored, category):
    """The setting that suits one template set does not suit another.

    Eleven small pulse templates peak at 0.821 and three large syllable
    ones at 0.546, so a `k` carried from the first finds one syllable in
    three.  Calibration is what makes a fresh category work without the
    reader discovering that by dragging a slider.
    """
    domain = detection.SPECTROGRAM
    templates, score, times, level = scored[(category, domain)]
    k = detection.calibrate_k(score, times, examples[category], templates)
    assert k is not None
    found = detection._pick(score, times, level, templates,
                            detection.Settings(domain=domain, k=k))
    tol = 0.030 if category == "syllable" else ONSET_TOL_S
    assert _recovered(found, examples[category], tol) == len(examples[category])


def test_calibration_reaches_the_same_threshold_the_sweep_chose(
        recording, examples, scored):
    """Two unrelated routes to `k`, which ought to agree where both apply.

    A leave-one-syllable-out sweep across an injected-noise range chose 4.5
    on the spectrogram and 2.0 on the trace for the pulses.  Reading the cut
    off the examples instead arrives at 4.46 and 1.99.
    """
    for domain in detection.DOMAINS:
        templates, score, times, level = scored[("pulse", domain)]
        k = detection.calibrate_k(score, times, examples["pulse"], templates)
        assert k == pytest.approx(detection.default_k(domain), rel=0.25), (
            f"{domain}: calibrated {k:.2f} against default "
            f"{detection.default_k(domain):.2f}")


def test_a_threshold_relative_to_the_noise_survives_noise_that_an_absolute_one_does_not(
        recording, examples):
    """The measurement the whole module is shaped around.

    An absolute correlation cut tuned on a clean window returns *zero*
    detections once the recording gets noisier -- not fewer, none, because
    the entire curve slides down beneath it.  The same cut expressed as `k`
    deviations above the curve's own floor keeps working.
    """
    rate, clean = recording
    window = slice(0, int(6.0 * rate))
    here = [e for e in examples["pulse"] if e.t1 < 6.0]
    assert here, "the fixture should hold pulses in the first six seconds"

    settings = detection.Settings(domain=detection.TRACE)
    templates = detection.learn(clean, rate, examples["pulse"], settings)
    score, times, level = detection.score_curve(
        clean[window], rate, templates, settings)
    absolute_cut = detection.threshold_of(score, settings.normalized().k)
    relative_k = detection.calibrate_k(score, times, here, templates)

    rng = np.random.default_rng(11)
    loud = np.concatenate([clean[int(e.t0 * rate):int(e.t1 * rate)] for e in here])
    noisy = clean + rng.normal(0.0, np.sqrt(np.mean(loud ** 2)), clean.size)
    noisy /= np.max(np.abs(noisy))
    score_n, times_n, level_n = detection.score_curve(
        noisy[window], rate, templates, settings)

    kept_absolute = int(np.sum(score_n >= absolute_cut))
    relative_cut = detection.threshold_of(score_n, relative_k)
    kept_relative = int(np.sum(score_n >= relative_cut))

    assert kept_absolute == 0, (
        "the fixture is meant to be noisy enough to sink a fixed cut; "
        f"{kept_absolute} offsets still cleared {absolute_cut:.3f}")
    assert kept_relative > 0, "the relative cut sank with it"


def test_taking_the_best_of_many_templates_raises_the_floor_it_has_to_clear():
    """Why `MAX_TEMPLATES` is offered but is not the default.

    The maximum over K curves is also the maximum over K draws of the
    noise, so the background climbs with every example added while the
    mean's falls.  That is a threshold eroding itself as the reader marks
    more events, which is the opposite of what marking more should do.

    What is pinned here is the *divergence*, not the size of the rise.  How
    far the floor actually climbs depends on how alike the templates are:
    against real cricket templates on the reference recording the 99th
    percentile went from 0.062 at ``K=1`` to 0.388 at ``K=11``, six times,
    while independent random templates on noise -- the case built here,
    because it has no signal to be confounded by -- give a milder rise.
    """
    rng = np.random.default_rng(12)
    signal = rng.normal(size=120_000)
    patches = [rng.normal(size=600) for _ in range(11)]
    curves = detection._scores_1d(signal, patches)
    counts = (1, 2, 4, 8, 11)
    floors = {}
    for count in counts:
        subset = curves[:count]
        floors[count] = (
            float(np.percentile(detection._combine(subset, detection.MAX_TEMPLATES), 99)),
            float(np.percentile(detection._combine(subset, detection.MEAN_SCORES), 99)),
        )
    highest = [floors[c][0] for c in counts]
    averaged = [floors[c][1] for c in counts]
    assert highest == sorted(highest), f"max floor should only rise: {floors}"
    assert averaged == sorted(averaged, reverse=True), (
        f"mean floor should only fall: {floors}")
    widened = (highest[-1] / averaged[-1]) / (highest[0] / averaged[0])
    assert widened > 3.0, (
        f"the two combiners should pull apart as examples are added, "
        f"but the gap grew only {widened:.2f}x: {floors}")


# ------------------------------------------------------- the awkward inputs


def test_a_category_with_nothing_in_it_detects_nothing_rather_than_raising(
        recording):
    """The panel will ask before the reader has drawn anything."""
    rate, data = recording
    settings = detection.Settings()
    templates = detection.learn(data[:1000], rate, [], settings)
    assert not templates.ok
    assert detection.detect(data[:1000], rate, templates, settings) == []


def test_one_example_is_enough_to_run(recording, examples):
    """Few-shot has to include one-shot, even if one shot is a poor one."""
    rate, data = recording
    for domain in detection.DOMAINS:
        settings = detection.Settings(domain=domain)
        templates = detection.learn(data, rate, examples["pulse"][:1], settings)
        assert len(templates) == 1
        found = detection.detect(data[:int(4.0 * rate)], rate, templates, settings)
        assert isinstance(found, list)


def test_a_block_shorter_than_the_template_yields_no_score_rather_than_an_error(
        recording, examples):
    """Streaming hands out whatever is left at the end of a file."""
    rate, data = recording
    for domain in detection.DOMAINS:
        settings = detection.Settings(domain=domain)
        templates = detection.learn(data, rate, examples["syllable"], settings)
        score, times, _ = detection.score_curve(data[:64], rate, templates, settings)
        assert score.size == 0 and times.size == 0


def test_the_streaming_margin_covers_an_event_on_a_block_edge(recording, examples):
    """A template straddling a boundary is a detection nobody would get."""
    rate, data = recording
    templates = detection.learn(data, rate, examples["syllable"],
                                detection.Settings())
    assert detection.margin_s(templates) >= templates.duration_s


def test_detections_carry_the_band_and_channel_the_examples_had(
        recording, examples, scored):
    """They are about to become label rows, which need both."""
    templates, score, times, level = scored[("pulse", detection.SPECTROGRAM)]
    k = detection.calibrate_k(score, times, examples["pulse"], templates)
    found = detection._pick(score, times, level, templates,
                            detection.Settings(k=k))
    assert found
    for candidate in found[:20]:
        assert candidate.f_low_hz == templates.f_low_hz
        assert candidate.f_high_hz == templates.f_high_hz
        assert candidate.t1 > candidate.t0
        assert -1.0 <= candidate.score <= 1.0
