"""Tests for :mod:`audian.session`, the fakefish session-bundle reader.

Runs under pytest, and also standalone::

    .venv/bin/python -m pytest tests/test_session.py -q

Most tests write their own bundle into ``tmp_path``, so the suite does not
depend on any recording.  The ones that check the reader against the real exp2
session -- the partition that licenses the colour scheme, the identity between
an explained detection and its parent pulse, and the two ground-truth checks
against the WAV -- skip themselves when that data is not on this machine.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import polars as pl
import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from audian import session, windowing  # noqa: E402
from audian.session import (  # noqa: E402
    TRUST_OK,
    TRUST_UNVALIDATED,
    TRUST_WARN,
    Alignment,
    SessionBundle,
    find_bundle,
    find_bundles,
)

#: the paired session this feature was built against, if it is present
EXP2 = Path("/home/weygoldt/wrk/analyses/fakefish/experiments/exp2")
METADATA = EXP2 / "PULS0002_metadata.toml"
RECORDING = EXP2 / "DR0000_0087.wav"

needs_data = pytest.mark.skipif(
    not (METADATA.is_file() and RECORDING.is_file()),
    reason="the paired exp2 session is not on this machine",
)


# --- writing a bundle to read back ------------------------------------------


DEFAULT_ALIGNMENT = {
    "recording_file": '"rec.wav"',
    "recording_rate_hz": "48000",
    "recording_frames": "480000",
    "recording_channel": "0",
    "scale": "1.0000141267722116",
    "offset_s": "28.93544558001899",
    "drift_ppm": "14.126772",
    "match_tolerance_s": "0.0005",
    "match_fraction": "0.997255",
    "residual_median_s": "1.991225153119558e-05",
    "validated": "true",
    "validation_warnings": "[]",
    "fit_warnings": "[]",
}


def _csv(path: Path, types, rows) -> None:
    """Write `rows` (dicts) with every column of `types`, blank where absent."""
    columns = list(types)
    lines = [",".join(columns)]
    for row in rows:
        lines.append(
            ",".join("" if row.get(c) is None else str(row[c]) for c in columns)
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def drop_columns(path: Path, *names: str) -> None:
    """Rewrite a written CSV without `names`, as a tool that never wrote them.

    Absence of a column is a different bundle from a column full of nulls, and
    it is the case that used to take the whole load down, so the tests that
    care have to produce a file that genuinely lacks the header.
    """
    rows = [line.split(",") for line in path.read_text().splitlines()]
    keep = [i for i, name in enumerate(rows[0]) if name not in names]
    path.write_text(
        "\n".join(",".join(row[i] for i in keep) for row in rows) + "\n",
        encoding="utf-8",
    )


def write_bundle(
    directory: Path,
    *,
    session_id: str = "TEST",
    alignment: dict | None = None,
    counts: dict | None = None,
    integrity: dict | None = None,
    pulses: list | None = None,
    trials: list | None = None,
    session_events: list | None = None,
    detections: list | None = None,
    controls: list | None = None,
) -> Path:
    """A complete little bundle on disk.  Pass ``None`` to leave a CSV out."""
    directory.mkdir(parents=True, exist_ok=True)
    fit = dict(DEFAULT_ALIGNMENT)
    fit.update(alignment or {})
    body = [f'[session]\nsession_id = "{session_id}"\n', "[counts]"]
    for key, value in (counts or {}).items():
        body.append(f"{key} = {value}")
    body.append("\n[integrity]")
    for key, value in (integrity or {}).items():
        body.append(f"{key} = {value}")
    body.append("\n[alignment]")
    for key, value in fit.items():
        # A None override means "the writer left this key out", which is the
        # case the tri-state fields exist for; it is not `key = None`.
        if value is not None:
            body.append(f"{key} = {value}")
    metadata = directory / f"{session_id}_metadata.toml"
    metadata.write_text("\n".join(body) + "\n", encoding="utf-8")

    tables = {
        "pulses": (session.PULSE_TYPES, pulses),
        "trials": (session.TRIAL_TYPES, trials),
        "session_events": (session.EVENT_TYPES, session_events),
        "detections": (session.DETECTION_TYPES, detections),
        "controls": (session.CONTROL_TYPES, controls),
    }
    for kind, (types, rows) in tables.items():
        if rows is None:
            continue
        _csv(directory / f"{session_id}_{kind}.csv", types, rows)
    return metadata


def pulse(t, kind="localization", **extra):
    row = {
        "time_s": t - 28.9,
        "recording_time_s": t,
        "pulse_type": kind,
        "amplitude": 0.25 if kind != "volley" else 0.91,
        "polarity": -1,
        "detected_time_s": t,
        "residual_s": 0.0,
        "match_status": "matched",
    }
    row.update(extra)
    return row


def trial(number, treatment, start, end, emitted=0, **extra):
    row = {
        "trial_number": number,
        "treatment": treatment,
        "requested": "random",
        "was_blinded": "true",
        "time_s": start - 28.9,
        "recording_time_s": start,
        "ended_s": end - 28.9,
        "recording_ended_s": end,
        "duration_s": end - start,
        "pulses_emitted": emitted,
        "polarity": 1,
    }
    row.update(extra)
    return row


def simple(directory: Path, **kwargs) -> SessionBundle:
    """The smallest bundle that exercises every layer, plus any overrides."""
    defaults = dict(
        pulses=[pulse(1.0), pulse(2.0, "baseline"), pulse(3.0, "volley")],
        trials=[
            trial(1, "volley", 2.9, 3.1, 1),
            trial(2, "baseline", 1.9, 2.1, 0),
            trial(3, "silence", 5.0, 5.6, 0),
        ],
        session_events=[
            {"time_s": 0.0, "recording_time_s": 0.5, "event": "boot", "file_index": 1},
            {"time_s": 0.1, "recording_time_s": 1.5, "event": "localization_started"},
            {"time_s": 0.2, "recording_time_s": 4.5, "event": "localization_stopped"},
        ],
        detections=[
            {
                "recording_time_s": 1.0,
                "device_time_s": -27.9,
                "amplitude": 0.05,
                "explained_by_log": "true",
                "source_row": 9,
            },
            {
                "recording_time_s": 7.25,
                "device_time_s": -21.6,
                "amplitude": 0.09,
                "explained_by_log": "false",
                "source_row": None,
            },
        ],
        controls=[
            {
                "time_s": 0.0,
                "recording_time_s": 0.5,
                "tick_hz": 5.0,
                "randomness": 1.0,
                "volley_amplitude": 1.0,
                "tick_interval_s": 0.2,
            },
            {
                "time_s": 4.0,
                "recording_time_s": 4.5,
                "tick_hz": 0.5,
                "randomness": 0.5,
                "volley_amplitude": 1.0,
                "tick_interval_s": 2.0,
            },
        ],
    )
    defaults.update(kwargs)
    metadata = write_bundle(directory, **defaults)
    return SessionBundle.load(metadata)


# --- the validated gate -----------------------------------------------------


def test_a_missing_validated_key_is_unvalidated_not_fine():
    assert Alignment().trust == TRUST_UNVALIDATED


def test_a_string_that_says_true_is_not_a_claim_of_validation():
    assert Alignment(validated="true").trust == TRUST_UNVALIDATED


def test_an_integer_one_is_not_a_claim_of_validation():
    assert Alignment(validated=1).trust == TRUST_UNVALIDATED


def test_a_real_boolean_with_no_warnings_is_the_only_way_to_be_trusted():
    fit = Alignment(validated=True, validation_warnings=(), fit_warnings=())
    assert fit.trust == TRUST_OK
    assert fit.is_validated is True


def test_a_validated_fit_that_carries_warnings_is_flagged_not_trusted():
    fit = Alignment(validated=True, fit_warnings=("residual wander",))
    assert fit.trust == TRUST_WARN
    assert fit.warnings == ("residual wander",)


def test_a_quoted_validated_flag_in_the_toml_loads_as_unvalidated_and_warns(tmp_path):
    bundle = simple(tmp_path, alignment={"validated": '"true"'})
    assert bundle.trust == TRUST_UNVALIDATED
    assert any("not a boolean" in w for w in bundle.warnings)


def test_an_absent_validated_key_loads_as_unvalidated_and_says_it_is_absent(tmp_path):
    """A writer that forgot the key must not look like a fit nobody validated.

    Both read as TRUST_UNVALIDATED, which is right, and for a while both also
    produced an empty `warnings` -- so the one bundle a human could fix by
    re-running the aligner was indistinguishable from the one they could not.
    """
    bundle = simple(tmp_path, alignment={"validated": None})
    assert bundle.meta.alignment.validated is None
    assert bundle.trust == TRUST_UNVALIDATED
    assert any("validated is absent" in w for w in bundle.warnings)
    # still the wrong-type message's own case, and not confused with it
    assert not any("not a boolean" in w for w in bundle.warnings)


def test_the_alignment_carries_no_detector_field_that_nothing_reads(tmp_path):
    """Spec 9.6(b)'s WAV burst check is not reproducible, so its fields are gone.

    `detect_absolute_floor` and `detect_refractory_s` were parsed for a
    ground-truth re-check that cannot be written honestly.  Measured on the
    exp2 bundle of 2026-08-24, at the TOML's own numbers then (floor
    0.018043927, refractory 0.002 s, SNR 8.0; the 08-25 refit moved the floor
    to 0.004995961 and the count to 3398, and none of the reasoning below
    turns on either):
    8 x the file's noise floor is 0.0011, sixteen times BELOW the absolute
    floor, so the SNR threshold never binds and adds nothing; forward-scanning
    above the floor yields 5263 peaks against the TOML's own 3022 detections,
    306 inside volley trial 0 against its `pulses_emitted` of 83, and 13 inside
    the 12 silence spans that spec 9.6(b) requires to hold zero.  The median
    inter-peak interval inside every volley span comes out at exactly 0.0020 s
    -- the refractory itself -- so the "<= 0.010 s" assertion would be testing
    arithmetic, not the recording.  Reproducing the real detector needs the
    matched filter named in `[alignment].method`, which the TOML does not carry.

    So the density check against the detections CSV stays and these two fields
    go.  A parsed field whose docstring claims a purpose no caller has is a
    promise the next reader will believe.
    """
    fields = set(Alignment.__dataclass_fields__)
    assert not [name for name in fields if name.startswith("detect_")]
    assert not hasattr(simple(tmp_path).meta.alignment, "detect_absolute_floor")


def test_the_duration_comes_from_the_fits_own_frames_and_rate():
    fit = Alignment(recording_frames=29140992, recording_rate_hz=48000.0)
    assert fit.duration_s == pytest.approx(607.104)
    assert Alignment(recording_frames=29140992).duration_s is None


def test_the_fit_summary_names_the_channel_it_was_fitted_on():
    fit = Alignment(scale=1.0, offset_s=28.9, recording_channel=1)
    assert "fitted on channel 1" in fit.fit_summary()


# --- pinned dtypes: null is a value, never missing data ---------------------


def test_a_treatment_that_is_null_on_every_leading_row_stays_a_string(tmp_path):
    """893 exp2 pulses have a null treatment: the ambient train, in no trial."""
    rows = [pulse(float(i)) for i in range(1, 40)]
    rows.append(pulse(40.0, "volley", treatment="volley", trial_number=1))
    bundle = simple(tmp_path, pulses=rows)
    frame = bundle["pulses.volley"].series[0].frame
    assert frame["treatment"].dtype == pl.String
    assert frame["treatment"][-1] == "volley"
    resting = bundle["pulses.resting"].series[0].frame
    assert resting["treatment"].null_count() == 39


def test_a_trial_number_that_is_null_on_every_leading_row_is_an_integer(tmp_path):
    rows = [pulse(float(i)) for i in range(1, 40)]
    rows.append(pulse(40.0, "volley", trial_number=7))
    bundle = simple(tmp_path, pulses=rows)
    frame = bundle["pulses.volley"].series[0].frame
    assert frame["trial_number"].dtype == pl.Int64
    assert frame["trial_number"][-1] == 7


def test_stimulus_item_and_pulse_index_survive_a_leading_null_block(tmp_path):
    rows = [pulse(float(i)) for i in range(1, 40)]
    rows.append(pulse(40.0, "volley", stimulus_item=113, pulse_index_in_item=82))
    frame = simple(tmp_path, pulses=rows)["pulses.volley"].series[0].frame
    assert frame["stimulus_item"].dtype == pl.Int64
    assert frame["pulse_index_in_item"].dtype == pl.Int64
    assert frame["stimulus_item"][-1] == 113
    assert frame["pulse_index_in_item"][-1] == 82


def test_records_lost_is_an_integer_even_when_every_value_is_null(tmp_path):
    """The trap: unpinned, `records_lost > 0` is true for '10' and false for '9'."""
    events = [
        {"time_s": 0.0, "recording_time_s": float(i), "event": "clock_anchor"}
        for i in range(1, 40)
    ]
    events.append(
        {"time_s": 0.0, "recording_time_s": 40.0, "event": "boot", "records_lost": 9}
    )
    bundle = simple(tmp_path, session_events=events)
    layer = bundle["session_events"]
    frames = [s.frame for s in layer.series]
    assert all(f["records_lost"].dtype == pl.Int64 for f in frames)
    fault = [s for s in layer.series if s.role == "fault"][0]
    assert fault.frame["records_lost"].to_list() == [9]
    quiet = [s for s in layer.series if s.role != "fault"][0]
    assert quiet.frame["records_lost"].null_count() == 39


def test_a_null_records_lost_is_not_zero_records_lost(tmp_path):
    bundle = simple(tmp_path)
    frame = bundle["session_events"].series[0].frame
    assert frame["records_lost"].null_count() == frame.height
    assert frame["records_lost"].to_list() == [None] * frame.height


def test_radio_link_up_is_a_boolean_with_its_nulls_intact(tmp_path):
    events = [
        {"time_s": 0.0, "recording_time_s": float(i), "event": "clock_anchor"}
        for i in range(1, 40)
    ]
    events.append(
        {
            "time_s": 0.0,
            "recording_time_s": 40.0,
            "event": "radio_link",
            "radio_link_up": "false",
        }
    )
    layer = simple(tmp_path, session_events=events)["session_events"]
    fault = [s for s in layer.series if s.role == "fault"][0]
    assert fault.frame["radio_link_up"].dtype == pl.Boolean
    assert fault.frame["radio_link_up"].to_list() == [False]
    quiet = [s for s in layer.series if s.role != "fault"][0]
    assert quiet.frame["radio_link_up"].null_count() == 39


def test_a_detection_no_log_row_explains_keeps_a_null_source_row(tmp_path):
    rows = [
        {
            "recording_time_s": float(i),
            "device_time_s": float(i) - 28.9,
            "amplitude": 0.05,
            "explained_by_log": "false",
            "source_row": None,
        }
        for i in range(1, 40)
    ]
    rows.append(
        {
            "recording_time_s": 40.0,
            "device_time_s": 11.1,
            "amplitude": 0.05,
            "explained_by_log": "true",
            "source_row": 1234,
        }
    )
    bundle = simple(tmp_path, detections=rows, pulses=[pulse(40.0)])
    novel = bundle["detections.unexplained"].series[0].frame
    assert novel["source_row"].dtype == pl.Int64
    assert novel["source_row"].null_count() == 39
    known = bundle["detections.explained"].series[0].frame
    assert known["source_row"][-1] == 1234


def test_the_receiver_columns_are_integers_despite_the_boot_row(tmp_path):
    """The five *_us columns are null on row 0 only: the radio is not read yet."""
    rows = [{"time_s": 0.0, "recording_time_s": 0.5, "tick_hz": 5.0, "randomness": 1.0}]
    rows += [
        {
            "time_s": float(i),
            "recording_time_s": float(i) + 0.5,
            "tick_hz": 5.0 + i,
            "randomness": 0.5,
            "throttle_pulse_us": 1500 + i,
            "trigger_pulse_us": 1100,
            "randomness_pulse_us": 1900,
            "amplitude_pulse_us": 1000,
            "receiver_zero_us": 1500,
        }
        for i in range(1, 40)
    ]
    frame = simple(tmp_path, controls=rows)["controls"].frame
    for column in (
        "throttle_pulse_us",
        "trigger_pulse_us",
        "randomness_pulse_us",
        "amplitude_pulse_us",
        "receiver_zero_us",
    ):
        assert frame[column].dtype == pl.Int64, column
        assert frame[column][0] is None, column
    assert frame["throttle_pulse_us"][-1] == 1539


def test_explained_by_log_is_a_real_boolean(tmp_path):
    frame = simple(tmp_path)["detections.unexplained"].series[0].frame
    assert frame["explained_by_log"].dtype == pl.Boolean
    assert frame["explained_by_log"].to_list() == [False]


# --- predicted is not observed ---------------------------------------------


def test_a_pulse_with_no_detection_is_a_separate_predicted_series(tmp_path):
    rows = [pulse(float(i)) for i in range(1, 20)]
    rows.append(
        pulse(20.0, detected_time_s=None, residual_s=None, match_status="unmatched")
    )
    layer = simple(tmp_path, pulses=rows)["pulses.resting"]
    assert [len(s) for s in layer.series] == [19, 1]
    assert [s.observed for s in layer.series] == [True, False]
    assert layer.series[1].times.tolist() == [20.0]
    assert layer.series[1].frame["detected_time_s"][0] is None


def test_a_predicted_mark_says_so_at_the_end_of_its_own_description(tmp_path):
    rows = [pulse(1.0), pulse(2.0, detected_time_s=None, match_status="unmatched")]
    layer = simple(tmp_path, pulses=rows)["pulses.resting"]
    assert layer.describe(0, 0).endswith("s")
    assert layer.describe(1, 0).endswith("predicted, not observed")


def test_without_a_match_status_column_a_pulse_is_observed_only_if_it_was_detected(
    tmp_path,
):
    """detected_time_s answers the same question and is the harder evidence.

    Fabricating `observed=True` for the whole file drew a position the
    recording never confirmed with a solid pen and dropped describe()'s
    closing clause -- spec 7.2's distinction lost to a column that was absent,
    not to a column that disagreed.
    """
    rows = [
        pulse(1.0),
        pulse(2.0, detected_time_s=None, residual_s=None),
        pulse(3.0),
    ]
    metadata = write_bundle(tmp_path, pulses=rows, trials=[])
    drop_columns(tmp_path / "TEST_pulses.csv", "match_status")
    layer = SessionBundle.load(metadata)["pulses.resting"]
    assert [(len(x), x.observed) for x in layer.series] == [(2, True), (1, False)]
    assert layer.series[1].times.tolist() == [2.0]
    assert layer.describe(1, 0).endswith("predicted, not observed")


def test_a_pulses_csv_with_no_evidence_column_at_all_says_it_is_guessing(tmp_path):
    """All-observed is the only thing left to say, so it is said out loud."""
    metadata = write_bundle(tmp_path, pulses=[pulse(1.0)], trials=[])
    drop_columns(tmp_path / "TEST_pulses.csv", "match_status", "detected_time_s")
    bundle = SessionBundle.load(metadata)
    assert [x.observed for x in bundle["pulses.resting"].series] == [True]
    assert any("on no evidence" in w for w in bundle.warnings)


def test_a_disagreement_about_observation_is_reported_never_resolved(tmp_path):
    """match_status is a word and detected_time_s is a number; check both."""
    rows = [pulse(1.0, match_status="unmatched")]  # but detected_time_s is set
    bundle = simple(tmp_path, pulses=rows)
    assert any("disagree" in w and "match_status" in w for w in bundle.warnings)


def test_the_predicted_class_keeps_its_own_sorted_array(tmp_path):
    """Merging it into the observed train would cost it its pixel bucket."""
    rows = [pulse(i / 100.0) for i in range(1, 400)]
    rows.append(pulse(1.505, detected_time_s=None, match_status="unmatched"))
    layer = simple(tmp_path, pulses=sorted(rows, key=lambda r: r["recording_time_s"]))[
        "pulses.resting"
    ]
    predicted = layer.series[1]
    assert predicted.times.size == 1
    assert np.all(np.diff(layer.series[0].times) > 0)


# --- the silence control ----------------------------------------------------


def test_a_silence_trial_with_no_pulses_survives_loading_as_a_span(tmp_path):
    trials = [trial(i, "silence", 10.0 * i, 10.0 * i + 0.54, 0) for i in range(1, 13)]
    bundle = simple(tmp_path, trials=trials)
    layer = bundle["trials.silence"]
    assert len(layer) == 12
    assert layer.starts.size == 12
    assert np.allclose(layer.durations, 0.54)
    assert layer.frame["pulses_emitted"].to_list() == [0] * 12


def test_a_silence_trial_is_loaded_exactly_like_a_volley_trial(tmp_path):
    bundle = simple(tmp_path)
    silence = bundle["trials.silence"]
    volley = bundle["trials.volley"]
    assert type(silence) is type(volley)
    assert silence.kind == volley.kind
    assert silence.track == volley.track
    assert silence.default_on == volley.default_on is True


def test_a_pulse_inside_a_silence_trial_is_named_as_a_broken_control(tmp_path):
    bundle = simple(
        tmp_path,
        pulses=[pulse(5.3, "volley")],
        trials=[trial(3, "silence", 5.0, 5.6, 0)],
    )
    assert any("silence trial" in w and "not silent" in w for w in bundle.warnings)


def test_a_silence_trial_that_claims_to_have_emitted_pulses_is_named(tmp_path):
    bundle = simple(tmp_path, trials=[trial(3, "silence", 5.0, 5.6, emitted=4)])
    assert any("pulses_emitted" in w for w in bundle.warnings)


def test_a_silence_trial_with_no_pulses_emitted_recorded_is_unverified_not_silent(
    tmp_path,
):
    """A fill_null(0) certified the control on a number nobody wrote down.

    No pulse layer can re-derive it: not finding a pulse inside the span is not
    the same statement as the stimulator reporting that it emitted none, which
    is the whole reason the field is cross-checked at all.
    """
    bundle = simple(
        tmp_path,
        pulses=[],
        trials=[trial(3, "silence", 5.0, 5.6, emitted=None)],
    )
    silence = bundle["trials.silence"]
    assert silence.frame["pulses_emitted"].to_list() == [None]
    assert any("unverified" in w and "pulses_emitted" in w for w in bundle.warnings)
    assert silence.describe(0).endswith("pulses emitted not recorded")


def test_a_recorded_zero_and_an_absent_column_read_differently_on_a_span(tmp_path):
    """Zero is a measurement; a column that was never written is not."""
    told = simple(tmp_path, trials=[trial(3, "silence", 5.0, 5.6, 0)])
    assert "0 pulses emitted" in told["trials.silence"].describe(0)

    metadata = write_bundle(tmp_path, trials=[trial(3, "silence", 5.0, 5.6, 0)])
    drop_columns(tmp_path / "TEST_trials.csv", "pulses_emitted")
    absent = SessionBundle.load(metadata)["trials.silence"]
    assert "pulses emitted" not in absent.describe(0)


# --- empty means absent, never zero ----------------------------------------


def test_a_missing_kind_is_absent_not_empty(tmp_path):
    bundle = simple(tmp_path, session_events=None)
    assert "session_events" in bundle.missing
    assert bundle.get("session_events") is None
    assert bundle.get("localization") is None
    assert "pulses" not in bundle.missing


def test_a_kind_that_exists_with_no_rows_is_a_layer_with_no_rows(tmp_path):
    bundle = simple(tmp_path, trials=[])
    assert "trials" not in bundle.missing
    assert len(bundle["trials.silence"]) == 0
    assert bundle["trials.silence"].t_min is None


def test_a_row_with_no_recording_time_is_dropped_and_counted(tmp_path):
    """time_s drifts 14 ppm off a 28.9 s offset; it is never a fallback."""
    rows = [pulse(1.0), pulse(2.0)]
    rows.append({"time_s": 3.0, "recording_time_s": None, "pulse_type": "volley"})
    bundle = simple(tmp_path, pulses=rows)
    assert bundle.dropped["pulses"] == 1
    assert len(bundle["pulses.volley"]) == 0
    assert any("cannot be placed" in w for w in bundle.warnings)


# --- cross-checks -----------------------------------------------------------


def test_a_row_count_that_disagrees_with_the_toml_is_a_warning(tmp_path):
    bundle = simple(tmp_path, counts={"rows_pulses": 99})
    assert any("rows_pulses says 99" in w for w in bundle.warnings)


def test_an_incomplete_log_warns_independently_of_the_fit(tmp_path):
    bundle = simple(tmp_path, integrity={"records_lost": 12, "drop_events": 0})
    assert bundle.trust == TRUST_OK
    assert bundle.meta.integrity.complete is False
    assert any("12 log records lost" in w for w in bundle.warnings)


def test_a_trial_that_ends_before_it_starts_is_flagged_not_swapped(tmp_path):
    bundle = simple(tmp_path, trials=[trial(1, "volley", 5.0, 4.0, 3)])
    layer = bundle["trials.volley"]
    assert layer.starts.tolist() == [5.0]
    assert layer.ends.tolist() == [4.0]
    assert any("end before they start" in w for w in bundle.warnings)


def test_a_detection_the_log_cannot_explain_after_all_is_named(tmp_path):
    bundle = simple(
        tmp_path,
        pulses=[pulse(1.0)],
        detections=[
            {
                "recording_time_s": 500.0,
                "device_time_s": 471.1,
                "amplitude": 0.05,
                "explained_by_log": "true",
                "source_row": 4,
            }
        ],
    )
    assert bundle["detections.explained"].unjoined == 1
    assert any("no matched pulse" in w for w in bundle.warnings)


def test_a_source_row_that_disagrees_with_explained_by_log_is_named(tmp_path):
    bundle = simple(
        tmp_path,
        detections=[
            {
                "recording_time_s": 1.0,
                "device_time_s": -27.9,
                "amplitude": 0.05,
                "explained_by_log": "true",
                "source_row": None,
            }
        ],
    )
    assert any("source_row" in w and "disagree" in w for w in bundle.warnings)


def test_an_unknown_treatment_is_named_rather_than_dropped_in_silence(tmp_path):
    bundle = simple(tmp_path, trials=[trial(1, "chirp", 1.0, 2.0)])
    assert any("unknown treatment (chirp)" in w for w in bundle.warnings)


def test_a_clean_bundle_produces_no_warnings_at_all(tmp_path):
    assert simple(tmp_path).warnings == ()


# --- a recording written as several files -----------------------------------

#: The shape exp3 (PULS0005) is written in: four WAVs treated as one recording,
#: 10 s of 48 kHz each, with the three joins the recorder lost time at.
SPLIT_NAMES = ("part0.wav", "part1.wav", "part2.wav", "part3.wav")
SPLIT_FILE_FRAMES = 480000
SPLIT_DIGESTS = tuple(c * 64 for c in "0123")


def split_alignment(**overrides) -> dict:
    """``[alignment]`` as the writer of a split session writes it: plural keys."""
    fit = {
        # The writer of a split session emits no singular key at all, which is
        # what left this bundle with no name to check.
        "recording_file": None,
        "recording_files": "[" + ", ".join(f'"{n}"' for n in SPLIT_NAMES) + "]",
        "recording_sha256": "[" + ", ".join(f'"{d}"' for d in SPLIT_DIGESTS) + "]",
        "recording_file_frames": "[" + ", ".join(["480000"] * 4) + "]",
        "recording_join_gaps_s": "[0.032, 0.032, -0.12]",
        "recording_frames": str(4 * SPLIT_FILE_FRAMES),
    }
    fit.update(overrides)
    return fit


class _Header:
    """A soundfile header, without a file: what `check_recording` reads."""

    def __init__(self, frames, samplerate=48000, channels=2):
        self.frames = frames
        self.samplerate = samplerate
        self.channels = channels


def test_a_recording_written_as_four_files_parses_from_the_plural_keys(tmp_path):
    """`recording_files` and `recording_file` are the same fact, one shape.

    Reading only the singular key left exp3's `recording_file` as None, and
    the provenance guard -- the one that exists so a stray bundle cannot put
    every mark in the wrong place -- then did not fail, it had no opinion.
    """
    fit = simple(tmp_path, alignment=split_alignment()).meta.alignment
    assert fit.recording_files == SPLIT_NAMES
    assert fit.is_split is True
    assert fit.recording_file == "part0.wav"
    assert fit.recording_file_frames == (SPLIT_FILE_FRAMES,) * 4
    assert fit.recording_sha256s == SPLIT_DIGESTS


def test_a_single_file_bundle_is_the_same_shape_with_one_entry(tmp_path):
    fit = simple(tmp_path).meta.alignment
    assert fit.recording_files == ("rec.wav",)
    assert fit.is_split is False
    assert fit.recording_file == "rec.wav"


def test_the_provenance_check_has_an_opinion_about_a_split_recording(tmp_path):
    """The guard that refuses a bundle belonging to a different recording.

    It could not run at all while only the singular key was read: `name` came
    back None, which `RecordingCheck.ok` counts as passing.
    """
    meta = simple(tmp_path, alignment=split_alignment()).meta
    for name in SPLIT_NAMES:
        check = meta.check_recording(tmp_path / name, info=_Header(SPLIT_FILE_FRAMES))
        assert check.name is True, name
        assert check.frames is True, name
        assert check.ok is True, name
    stray = meta.check_recording(tmp_path / "elsewhere.wav", info=_Header(480000))
    assert stray.name is False
    assert stray.ok is False
    assert any("part3.wav" in problem for problem in stray.problems)


def test_a_split_recording_checks_against_one_file_or_against_the_whole(tmp_path):
    """The caller may hand over one WAV's header or the loader over all four.

    Both are true statements about the same recording, so both pass and
    anything else is a bundle that does not belong to what is on screen.
    """
    meta = simple(tmp_path, alignment=split_alignment()).meta
    one = meta.check_recording(tmp_path / "part1.wav", info=_Header(SPLIT_FILE_FRAMES))
    assert one.frames is True
    whole = meta.check_recording(
        tmp_path / "part1.wav", info=_Header(4 * SPLIT_FILE_FRAMES)
    )
    assert whole.frames is True
    wrong = meta.check_recording(tmp_path / "part1.wav", info=_Header(123456))
    assert wrong.frames is False
    assert any("123456" in problem for problem in wrong.problems)


def test_the_digest_of_a_split_recording_is_looked_up_by_file(tmp_path):
    """One digest per file.  Hashing file 3 against file 0's is a false alarm,
    and a false alarm is how a provenance check gets switched off."""
    fit = simple(tmp_path, alignment=split_alignment()).meta.alignment
    assert fit.sha256_for("part0.wav") == SPLIT_DIGESTS[0]
    assert fit.sha256_for("part3.wav") == SPLIT_DIGESTS[3]
    assert fit.sha256_for("elsewhere.wav") is None


def test_the_join_gaps_are_carried_through_as_a_declared_fact(tmp_path):
    """Position from the loader, gap from the bundle -- and never a correction.

    exp3 declares +32 ms, +32 ms, -120 ms, and 120 ms is about thirty pulses
    of a 4 ms volley interval.  This viewer states it and shifts nothing.
    """
    fit = simple(tmp_path, alignment=split_alignment()).meta.alignment
    assert fit.recording_join_gaps_s == (0.032, 0.032, -0.12)
    assert fit.join_times_s == (10.0, 20.0, 30.0)
    assert fit.joins() == ((10.0, 0.032), (20.0, 0.032), (30.0, -0.12))


def test_a_single_file_recording_declares_no_joins(tmp_path):
    fit = simple(tmp_path).meta.alignment
    assert fit.join_times_s == () and fit.joins() == ()


def test_split_keys_that_disagree_with_each_other_are_reported(tmp_path):
    """Four keys describe one recording from four directions.

    When they disagree the fit was made against something other than what the
    TOML describes, and every check below is then checking the wrong thing
    while looking like it passed.
    """
    bundle = simple(
        tmp_path,
        alignment=split_alignment(
            recording_sha256='["' + SPLIT_DIGESTS[0] + '"]',
            recording_join_gaps_s="[0.032]",
            recording_frames="999999",
        ),
    )
    joined = " | ".join(bundle.warnings)
    assert "4 recording file(s) and 1 SHA-256" in joined
    assert "3 join(s), and 1 join gap" in joined
    assert "sum to 1920000" in joined and "999999" in joined


def test_discovery_offers_a_split_bundle_for_any_of_its_files(tmp_path):
    for name in SPLIT_NAMES:
        _wav(tmp_path / name)
    write_bundle(tmp_path, alignment=split_alignment(), pulses=[pulse(1.0)])
    for name in SPLIT_NAMES:
        ref = find_bundle(tmp_path / name)
        assert ref is not None, name
        assert ref.recording_files == SPLIT_NAMES
        assert ref.recording_file == "part0.wav"
    _wav(tmp_path / "stranger.wav")
    assert find_bundle(tmp_path / "stranger.wav") is None


# --- the writer's own warnings ----------------------------------------------


def test_the_writers_fit_warnings_reach_the_bundles_warning_list(tmp_path):
    """The badge said `warn` and nothing said why.

    `fit_warnings` already reached `trust`, so exp3's badge warned, while
    `bundle.warnings` stayed empty and the status bar had nothing to show.
    """
    bundle = simple(
        tmp_path,
        alignment={"fit_warnings": '["segment 9 correlates +1339.762 s"]'},
    )
    assert bundle.trust == TRUST_WARN
    assert any("segment 9 correlates" in w for w in bundle.warnings)


def test_a_clean_fit_adds_no_warning_of_its_own(tmp_path):
    bundle = simple(tmp_path)
    assert bundle.trust == TRUST_OK
    assert not any("warned about this fit" in w for w in bundle.warnings)


# --- residuals, per region --------------------------------------------------


def _split_pulses(times, shift=0.0):
    """Resting pulses at `times`, heard `shift` seconds from where the fit says."""
    return [pulse(t, detected_time_s=t + shift) for t in times]


def test_residuals_are_measured_per_file_when_the_bundle_declares_joins(tmp_path):
    """A join is where the recorder lost time, so it is where the fit stops
    holding: exp3's residual steps at every one of its three."""
    times = [6.0, 7.0, 16.0, 17.0, 26.0, 27.0, 36.0, 37.0]
    bundle = simple(
        tmp_path,
        alignment=split_alignment(),
        pulses=_split_pulses(times),
        trials=[trial(1, "silence", 39.0, 39.5)],
    )
    stats = bundle.residuals
    assert stats.split is True
    assert [r.label for r in stats] == [f"file {i} of 4" for i in (1, 2, 3, 4)]
    assert [(r.t0, r.t1) for r in stats] == [
        (0.0, 10.0),
        (10.0, 20.0),
        (20.0, 30.0),
        (30.0, 40.0),
    ]
    assert [r.total for r in stats] == [2, 2, 2, 2]
    assert [r.matched for r in stats] == [2, 2, 2, 2]


def test_residuals_fall_back_to_a_fixed_number_of_bins_without_joins(tmp_path):
    bundle = simple(tmp_path, pulses=_split_pulses([1.0, 2.0, 3.0]))
    stats = bundle.residuals
    assert stats.split is False
    assert len(stats) == session.RESIDUAL_BINS
    assert stats.regions[0].label == "region 1 of 8"
    assert stats.regions[0].t0 == 0.0
    assert stats.regions[-1].t1 == pytest.approx(10.0)  # 480000 frames / 48 kHz


def test_a_region_far_outside_the_match_tolerance_says_so_at_load(tmp_path):
    """The whole reason this exists.

    exp3's header reported a session-wide residual median of 0.95 us, true
    only because its first two files hold 3203 of the 4652 matched pulses.  A
    global median is not a promise about the region on screen, so the region
    that is off is named at load rather than averaged away.
    """
    clean = [6.0, 7.0, 16.0, 17.0, 26.0, 27.0]
    bundle = simple(
        tmp_path,
        alignment=split_alignment(),
        pulses=_split_pulses(clean) + _split_pulses([36.0, 37.0], shift=0.02),
        trials=[trial(1, "silence", 39.0, 39.5)],
    )
    stats = bundle.residuals
    assert stats.regions[3].median_s == pytest.approx(0.02)
    assert stats.regions[3].iqr_s == pytest.approx(0.0, abs=1e-9)
    # 0.02 s is 40x the fit's own 0.5 ms match tolerance, well past the 10x
    # gate; the other three files sit at zero and say nothing.
    assert len(stats.warnings) == 1
    assert "file 4 of 4" in stats.warnings[0]
    assert "40x" in stats.warnings[0]
    assert any("file 4 of 4" in w for w in bundle.warnings)


def test_a_region_inside_the_match_tolerance_is_not_worth_a_warning(tmp_path):
    """10x, not 2x: a median of one match tolerance still puts every mark on
    the pulse it names, just early within it."""
    bundle = simple(
        tmp_path,
        alignment=split_alignment(),
        pulses=_split_pulses([6.0, 7.0, 16.0, 26.0, 36.0], shift=0.001),
        trials=[trial(1, "silence", 39.0, 39.5)],
    )
    assert bundle.residuals.warnings == ()


def test_the_residual_lookup_answers_for_the_region_on_screen(tmp_path):
    bundle = simple(
        tmp_path,
        alignment=split_alignment(),
        pulses=_split_pulses([6.0, 16.0, 26.0]) + _split_pulses([36.0], shift=0.02),
        trials=[trial(1, "silence", 39.0, 39.5)],
    )
    stats = bundle.residuals
    assert stats.at(0.0).label == "file 1 of 4"
    assert stats.at(9.999).label == "file 1 of 4"
    assert stats.at(10.0).label == "file 2 of 4"
    assert stats.at(35.0).label == "file 4 of 4"
    assert stats.at(-1.0) is None
    assert stats.at(40.0) is None, "past the end of the recording is not a region"
    assert stats.worst.label == "file 4 of 4"


def test_a_region_where_nothing_matched_reports_that_instead_of_a_median(tmp_path):
    """A NaN median is an answer.  A region can also have a lovely median over
    the few of its pulses that matched, so the counts travel with it."""
    bundle = simple(
        tmp_path,
        alignment=split_alignment(),
        pulses=[
            pulse(6.0),
            pulse(
                36.0, detected_time_s=None, residual_s=None, match_status="unmatched"
            ),
        ],
        trials=[trial(1, "silence", 39.0, 39.5)],
    )
    last = bundle.residuals.regions[3]
    assert (last.total, last.matched) == (1, 0)
    assert np.isnan(last.median_s)
    assert last.match_fraction == 0.0
    assert "none of its 1 pulses matched" in last.summary()
    assert bundle.residuals.warnings == ()


def test_a_bundle_with_no_pulses_still_carries_a_residual_answer(tmp_path):
    bundle = simple(tmp_path, pulses=None)
    assert len(bundle.residuals) == 0
    assert bundle.residuals.at(1.0) is None
    assert bundle.residuals.warnings == ()


# --- the recording check ----------------------------------------------------


def _wav(path: Path, *, seconds=1.0, rate=48000, channels=2):
    soundfile = pytest.importorskip("soundfile")
    frames = int(seconds * rate)
    soundfile.write(str(path), np.zeros((frames, channels), dtype="float32"), rate)
    return path


def test_a_recording_with_the_wrong_name_is_a_named_problem(tmp_path):
    wav = _wav(tmp_path / "other.wav")
    bundle = simple(tmp_path)
    check = bundle.meta.check_recording(wav)
    assert check.name is False
    assert check.ok is False
    assert any("rec.wav" in p for p in check.problems)


def test_a_recording_frames_mismatch_is_caught_without_hashing(tmp_path, monkeypatch):
    """Tier 2 is a header read; tier 3 is 175 MB and never runs on open."""

    def explode(*args, **kwargs):
        raise AssertionError("verify_sha256 must never be called from a load path")

    monkeypatch.setattr(session, "verify_sha256", explode)
    wav = _wav(tmp_path / "rec.wav", seconds=0.5)
    metadata = write_bundle(tmp_path, pulses=[pulse(1.0)])
    bundle = SessionBundle.load(metadata, recording=wav)
    check = bundle.recording_check
    assert check.name is True
    assert check.frames is False
    assert check.sha256 is None
    assert check.ok is False
    assert any("frames" in p for p in check.problems)
    assert any("frames" in w for w in bundle.warnings)


def test_a_fit_channel_the_file_does_not_have_is_caught(tmp_path):
    wav = _wav(tmp_path / "rec.wav", channels=1)
    bundle = simple(tmp_path, alignment={"recording_channel": "3"})
    check = bundle.meta.check_recording(wav)
    assert check.channel is False


def test_a_check_that_did_not_run_is_not_a_check_that_passed(tmp_path):
    wav = _wav(tmp_path / "rec.wav")
    bundle = simple(tmp_path, alignment={"recording_frames": None})
    check = bundle.meta.check_recording(wav)
    assert check.frames is None
    assert check.sha256 is None
    assert check.ok is True


def test_verify_sha256_answers_none_when_the_bundle_records_no_digest(tmp_path):
    wav = _wav(tmp_path / "rec.wav")
    bundle = simple(tmp_path)
    assert session.verify_sha256(bundle.meta, wav) is None


def test_verify_sha256_compares_the_real_content(tmp_path):
    import hashlib

    wav = _wav(tmp_path / "rec.wav")
    digest = hashlib.sha256(wav.read_bytes()).hexdigest()
    good = simple(tmp_path, alignment={"recording_sha256": f'"{digest}"'})
    assert session.verify_sha256(good.meta, wav) is True
    session._SHA_CACHE.clear()
    bad = simple(tmp_path, alignment={"recording_sha256": '"' + "0" * 64 + '"'})
    assert session.verify_sha256(bad.meta, wav) is False


# --- discovery --------------------------------------------------------------


def test_find_bundle_finds_the_one_that_names_this_recording(tmp_path):
    _wav(tmp_path / "rec.wav")
    write_bundle(tmp_path, pulses=[pulse(1.0)])
    ref = find_bundle(tmp_path / "rec.wav")
    assert ref is not None
    assert ref.session_id == "TEST"
    assert ref.kinds == frozenset({"pulses"})
    assert ref.path("pulses").name == "TEST_pulses.csv"
    assert ref.path("controls") is None


def test_a_bundle_from_a_neighbouring_experiment_is_refused(tmp_path):
    _wav(tmp_path / "rec.wav")
    write_bundle(
        tmp_path,
        session_id="OTHER",
        alignment={"recording_file": '"somewhere_else.wav"'},
        pulses=[pulse(1.0)],
    )
    assert find_bundles(tmp_path / "rec.wav") == []
    assert find_bundle(tmp_path / "rec.wav") is None


def test_two_bundles_naming_one_recording_refuse_to_guess(tmp_path, caplog):
    _wav(tmp_path / "rec.wav")
    write_bundle(tmp_path, session_id="ONE", pulses=[pulse(1.0)])
    write_bundle(tmp_path, session_id="TWO", pulses=[pulse(1.0)])
    assert len(find_bundles(tmp_path / "rec.wav")) == 2
    with caplog.at_level("WARNING"):
        assert find_bundle(tmp_path / "rec.wav") is None
    assert "refusing to guess" in caplog.text


def test_a_bundle_loads_from_its_metadata_path_or_its_directory(tmp_path):
    metadata = write_bundle(tmp_path, pulses=[pulse(1.0)])
    assert SessionBundle.load(metadata).meta.session_id == "TEST"
    assert SessionBundle.load(tmp_path).meta.session_id == "TEST"


# --- the layer model --------------------------------------------------------


def test_spans_are_marked_disjoint_only_when_they_really_are(tmp_path):
    apart = simple(
        tmp_path, trials=[trial(1, "volley", 1.0, 2.0), trial(2, "volley", 3.0, 4.0)]
    )
    assert apart["trials.volley"].disjoint is True
    nested = simple(
        tmp_path, trials=[trial(1, "volley", 1.0, 9.0), trial(2, "volley", 3.0, 4.0)]
    )
    assert nested["trials.volley"].disjoint is False
    assert nested["trials.volley"].max_end.tolist() == [9.0, 9.0]


def test_a_span_covers_its_own_start_and_not_its_own_end(tmp_path):
    layer = simple(tmp_path, trials=[trial(1, "volley", 2.0, 3.0)])["trials.volley"]
    assert layer.at(2.0) == 0
    assert layer.at(2.5) == 0
    assert layer.at(3.0) is None
    assert layer.at(1.999) is None


def test_a_nested_span_wins_over_the_one_it_sits_inside(tmp_path):
    layer = simple(
        tmp_path, trials=[trial(1, "volley", 1.0, 9.0), trial(2, "volley", 3.0, 4.0)]
    )["trials.volley"]
    assert layer.at(3.5) == 1
    assert layer.at(8.0) == 0


def test_a_held_control_value_survives_a_gap_of_minutes(tmp_path):
    rows = [
        {"time_s": 0.0, "recording_time_s": 28.9, "tick_hz": 5.0, "randomness": 1.0},
        {"time_s": 1.0, "recording_time_s": 30.0, "tick_hz": 20.0, "randomness": 0.5},
    ]
    track = simple(tmp_path, controls=rows)["controls"]
    assert track.value_at("tick_hz", 530.0) == 20.0
    assert np.isnan(track.value_at("tick_hz", 10.0))
    assert track.first_valid("tick_hz") == 5.0


def test_a_control_channel_that_never_changes_is_not_offered(tmp_path):
    track = simple(tmp_path)["controls"]
    assert "volley_amplitude" not in track.channels
    assert "volley_amplitude" in track.tip
    assert set(track.channels) == {"tick_hz", "randomness"}


def test_each_control_channel_keeps_its_own_frozen_range(tmp_path):
    track = simple(tmp_path)["controls"]
    assert track.ranges["tick_hz"] == (0.5, 5.0)
    assert track.ranges["randomness"] == (0.5, 1.0)
    assert track.units["tick_hz"] == "Hz"


def test_a_run_with_no_stopped_row_is_clamped_to_the_recording_and_flagged(tmp_path):
    events = [
        {"time_s": 0.0, "recording_time_s": 1.5, "event": "localization_started"},
    ]
    bundle = simple(tmp_path, session_events=events)
    runs = bundle["localization"]
    assert len(runs) == 1
    assert runs.open_right.tolist() == [True]
    assert np.isfinite(runs.starts).all() and np.isfinite(runs.ends).all()
    assert runs.ends[0] == pytest.approx(10.0)  # 480000 frames at 48000 Hz
    assert any("never stopped" in w for w in bundle.warnings)


def test_a_run_problem_quotes_the_session_events_row_it_came_from(tmp_path):
    events = [
        {"time_s": 0.0, "recording_time_s": 0.5, "event": "boot"},
        {"time_s": 0.1, "recording_time_s": 1.5, "event": "localization_stopped"},
        {"time_s": 0.2, "recording_time_s": 2.5, "event": "localization_started"},
        {"time_s": 0.3, "recording_time_s": 3.5, "event": "localization_stopped"},
    ]
    bundle = simple(tmp_path, session_events=events)
    assert any("session_events row 1" in w for w in bundle.warnings)


def test_nearest_and_step_cross_every_layer(tmp_path):
    bundle = simple(tmp_path)
    layer, series, row = bundle.nearest(3.02, ids=["pulses.volley", "pulses.resting"])
    assert layer.id == "pulses.volley" and (series, row) == (0, 0)
    layer, _, row = bundle.step(1.0, forward=True, ids=["pulses.resting"])
    assert float(layer.series[0].times[row]) == 2.0
    layer, _, row = bundle.step(3.0, forward=False, ids=["pulses.resting"])
    assert float(layer.series[0].times[row]) == 2.0


def test_spans_at_reports_every_layer_covering_an_instant(tmp_path):
    bundle = simple(tmp_path)
    hits = {layer.id for layer, _ in bundle.spans_at(3.0)}
    assert hits == {"trials.volley", "localization"}


def test_pulses_in_keeps_the_evidence_classes_apart(tmp_path):
    rows = [
        pulse(3.0, "volley"),
        pulse(3.05, "volley", detected_time_s=None, match_status="unmatched"),
    ]
    bundle = simple(tmp_path, pulses=rows, trials=[trial(1, "volley", 2.9, 3.1, 2)])
    inside = bundle.pulses_in(bundle["trials.volley"], 0)
    assert inside == {"pulses.volley#0": (0, 0, 1), "pulses.volley#1": (1, 0, 1)}


def test_count_between_reports_what_is_in_the_file(tmp_path):
    rows = [pulse(float(i)) for i in range(1, 10)]
    layer = simple(tmp_path, pulses=rows)["pulses.resting"]
    assert layer.count_between(2.0, 5.0) == 4
    assert layer.count_between(100.0, 200.0) == 0


def test_a_description_names_the_trial_and_the_residual(tmp_path):
    rows = [
        pulse(3.0, "volley", trial_number=1, treatment="volley", residual_s=-8.1e-5)
    ]
    text = simple(tmp_path, pulses=rows)["pulses.volley"].describe(0, 0)
    assert "trial 1" in text and "volley" in text and "-81 µs" in text


def test_a_span_description_names_its_extent_and_what_it_emitted(tmp_path):
    text = simple(tmp_path)["trials.volley"].describe(0)
    assert "2.900-3.100 s (0.200 s)" in text
    assert "1 pulses emitted" in text


def test_the_summary_names_every_populated_layer(tmp_path):
    text = simple(tmp_path).summary()
    assert "Silence 1" in text and "Volley 1" in text


def test_the_layer_ids_and_tracks_are_the_ten_the_overlay_expects(tmp_path):
    bundle = simple(tmp_path)
    assert [layer.id for layer in bundle] == [
        session.LAYER_TRIALS_VOLLEY,
        session.LAYER_TRIALS_BASELINE,
        session.LAYER_TRIALS_SILENCE,
        session.LAYER_PULSES_RESTING,
        session.LAYER_PULSES_VOLLEY,
        session.LAYER_DET_UNEXPLAINED,
        session.LAYER_DET_EXPLAINED,
        session.LAYER_RUNS,
        session.LAYER_SESSION_EVENTS,
        session.LAYER_CONTROLS,
    ]
    off = {layer.id for layer in bundle if not layer.default_on}
    assert off == {
        session.LAYER_RUNS,
        session.LAYER_SESSION_EVENTS,
        session.LAYER_CONTROLS,
    }


def test_localization_and_baseline_pulses_share_one_layer(tmp_path):
    """The user's ruling: one layer for the resting rate, both pulse types."""
    rows = [pulse(1.0, "localization"), pulse(2.0, "baseline")]
    bundle = simple(tmp_path, pulses=rows)
    layer = bundle["pulses.resting"]
    assert len(layer) == 2
    # and the distinction is still in the data, and still in the readout
    assert layer.series[0].frame["pulse_type"].to_list() == ["localization", "baseline"]
    assert "baseline" in layer.describe(0, 1)


def test_colour_carries_the_kind_and_never_the_treatment(tmp_path):
    """The encoding ruling, stated where the roles are assigned.

    The reading order is the user's: any trial's onset and offset first, every
    played pulse second, which treatment it was only third.  So all three
    treatments share one role and both pulse types share another -- three hues
    in the default view instead of seven -- and treatment moves to the letter.
    """
    bundle = simple(
        tmp_path,
        pulses=[pulse(1.0), pulse(3.0, "volley")],
        trials=[
            trial(1, "volley", 2.9, 3.1, 1),
            trial(2, "baseline", 4.0, 4.5, 0),
            trial(3, "silence", 5.0, 5.6, 0),
        ],
    )
    trials = ["trials.volley", "trials.baseline", "trials.silence"]
    assert {bundle[i].role for i in trials} == {"trial"}
    assert {bundle[i].role for i in ("pulses.resting", "pulses.volley")} == {"pulse"}
    # three, and the third is the ink of an unexplained detection
    assert bundle["detections.unexplained"].role == "detection.novel"
    assert len({bundle[i].role for i in [*trials, "pulses.volley"]}) == 2


def test_every_treatment_and_type_keeps_its_own_layer_and_switch(tmp_path):
    """Sharing a hue is not sharing a layer.

    Stage 3 of the field workflow is "solo one treatment", so collapsing the
    palette must not collapse the layer set: filtering is a toggle concern
    where colour is an encoding one.  This is the test that fails if a future
    tidy-up merges the three trial layers because they look the same.
    """
    bundle = simple(
        tmp_path,
        pulses=[pulse(1.0), pulse(3.0, "volley")],
        trials=[
            trial(1, "volley", 2.9, 3.1, 1),
            trial(2, "baseline", 4.0, 4.5, 0),
            trial(3, "silence", 5.0, 5.6, 0),
        ],
    )
    ids = {layer.id for layer in bundle}
    assert {
        "trials.volley",
        "trials.baseline",
        "trials.silence",
        "pulses.resting",
        "pulses.volley",
    } <= ids
    assert len(bundle["trials.silence"]) == 1
    assert len(bundle["trials.volley"]) == 1


def test_each_trial_layer_carries_the_letter_that_names_its_treatment(tmp_path):
    """Treatment is third tier and is carried by a letter, not by a hue.

    Per LAYER, because a trial layer is one treatment by construction -- which
    is what keeps the drawing path free of per-row Python and lets a merged
    bar standing for several trials still carry the right letter.
    """
    bundle = simple(
        tmp_path,
        trials=[
            trial(1, "volley", 2.9, 3.1, 1),
            trial(2, "baseline", 4.0, 4.5, 0),
            trial(3, "silence", 5.0, 5.6, 0),
        ],
    )
    letters = {
        layer.id: layer.letter for layer in bundle if layer.id.startswith("trials.")
    }
    assert letters == {
        "trials.volley": "V",
        "trials.baseline": "B",
        "trials.silence": "S",
    }
    # and nothing else claims one: a letter refines a hue that carries a kind,
    # and the localization runs are a kind of their own
    assert bundle["localization"].letter == ""


def test_an_explained_detection_takes_the_hue_of_the_pulse_that_explains_it(tmp_path):
    rows = [pulse(1.0, "localization"), pulse(3.0, "volley")]
    detections = [
        {
            "recording_time_s": 1.0,
            "device_time_s": -27.9,
            "amplitude": 0.05,
            "explained_by_log": "true",
            "source_row": 1,
        },
        {
            "recording_time_s": 3.0,
            "device_time_s": -25.9,
            "amplitude": 0.9,
            "explained_by_log": "true",
            "source_row": 2,
        },
    ]
    layer = simple(tmp_path, pulses=rows, detections=detections)["detections.explained"]
    # ONE hue for both, because an explained detection IS the played pulse
    # heard back -- volley or resting, it is the same claim as the pulse train
    # beside it and not a third category.
    assert {s.role for s in layer.series} == {"pulse"}
    assert sorted(t for s in layer.series for t in s.times.tolist()) == [1.0, 3.0]
    # the join to the parent pulse still happened, and is still what tells a
    # detection with no pulse behind it from one with
    assert len(layer.series) == 2
    assert layer.unjoined == 0
    # The layer's own role is now the CHIP's colour, and it has to be one of
    # the hues the layer draws.  It was `detection.novel`, which this layer
    # never draws once every series has a parent -- see the test below.
    assert layer.role == "pulse"


def test_the_explained_chip_takes_a_colour_that_layer_actually_draws(tmp_path):
    """The chips are the only legend, so two layers must not share one.

    `detections.explained` carried `role="detection.novel"` -- the ink whose
    meaning is "the log does not account for this" -- while every series it
    holds draws in its parent pulse's hue.  The chip was therefore painted in
    a colour no mark of the layer uses, and pixel-identical to the Unexplained
    chip beside it.
    """
    bundle = simple(tmp_path)
    explained = bundle["detections.explained"]
    unexplained = bundle["detections.unexplained"]
    drawn = {s.role or explained.role for s in explained.series}
    assert explained.role in drawn
    assert explained.role != unexplained.role


def test_an_explained_detection_with_no_parent_pulse_keeps_the_unexplained_ink(
    tmp_path,
):
    """An orphan says "nothing accounts for this" whatever the chip shows.

    Its series states the role rather than inheriting it, so giving the layer
    a chip colour cannot recolour the one class that must not move.
    """
    detections = [
        {
            "recording_time_s": 400.0,
            "device_time_s": 371.1,
            "amplitude": 0.05,
            "explained_by_log": "true",
            "source_row": 3,
        }
    ]
    bundle = simple(tmp_path, detections=detections)
    layer = bundle["detections.explained"]
    assert layer.unjoined == 1
    assert [s.role for s in layer.series] == ["detection.novel"]
    assert any("no matched pulse" in w for w in bundle.warnings)


def test_the_unexplained_layer_names_the_fact_and_not_an_interpretation(tmp_path):
    """`explained_by_log` is what the bundle states; a fish is a reading of it.

    The recording holds the playback and whatever was really in the water, so
    a detection the log does not account for is normal content -- and which
    animal, if any, made it is the reader's call, not this layer's label.
    """
    layer = simple(tmp_path)["detections.unexplained"]
    for text in (layer.label, layer.short, layer.micro, layer.tip):
        low = text.lower()
        for forbidden in ("animal", "fish", "eel", "response", "novel", "anomal"):
            assert forbidden not in low, (text, forbidden)
    assert "no log row accounts for" in layer.tip


def test_an_inverted_trial_is_findable_at_the_zoom_that_would_show_it(tmp_path):
    """It was drawn zoomed out and vanished zoomed in -- the worst of both.

    `_build_trials` keeps a trial whose ``recording_ended_s`` precedes its
    ``recording_time_s`` as written, and says so; `windowing.merge_spans` puts
    its bar at ``[start, start + one pixel]``.  `SpanLayer.max_end` therefore
    has to carry the REACH rather than the end, or the slice disagrees with
    the draw and the mark exists only at the zoom where it cannot be read.
    """
    bundle = simple(
        tmp_path,
        pulses=[pulse(1.0)],
        trials=[trial(1, "volley", 5.0, 2.0)],
    )
    assert any("end before they start" in w for w in bundle.warnings)
    layer = bundle["trials.volley"]
    assert layer.max_end.tolist() == [5.0], "the reach, not the earlier end"
    assert layer.t_max == 5.0
    for t0, t1 in ((0.0, 8.0), (4.5, 5.5), (4.9, 5.2)):
        _, _, _, total = windowing.window_spans(
            layer.starts, layer.ends, layer.max_end, t0, t1, layer.disjoint
        )
        assert total == 1, (t0, t1)
    for t0, t1 in ((1.0, 3.0), (5.5, 6.0)):
        _, _, _, total = windowing.window_spans(
            layer.starts, layer.ends, layer.max_end, t0, t1, layer.disjoint
        )
        assert total == 0, (t0, t1)


def test_an_ordinary_span_layer_still_reaches_exactly_as_far_as_its_ends(tmp_path):
    layer = simple(tmp_path)["trials.silence"]
    assert layer.max_end.tolist() == np.maximum.accumulate(layer.ends).tolist()


# --- performance ------------------------------------------------------------


def test_two_hundred_thousand_pulses_load_in_under_a_second(tmp_path):
    """The reader is a load-time cost, not a per-redraw one."""
    n = 200_000
    times = np.arange(n, dtype=np.float64) * 0.003 + 28.9
    frame = pl.DataFrame(
        {
            "time_s": times - 28.9,
            "recording_time_s": times,
            "pulse_type": ["localization"] * n,
            "amplitude": np.full(n, 0.25),
            "polarity": np.full(n, -1, dtype=np.int8),
            "detected_time_s": times,
            "residual_s": np.zeros(n),
            "match_status": ["matched"] * n,
        }
    )
    write_bundle(tmp_path, pulses=[])
    frame.write_csv(tmp_path / "TEST_pulses.csv")
    start = time.perf_counter()
    bundle = SessionBundle.load(tmp_path / "TEST_metadata.toml")
    elapsed = time.perf_counter() - start
    assert len(bundle["pulses.resting"]) == n
    assert elapsed < 1.0, f"{elapsed:.3f} s to load {n} rows"


# --- exp2 ground truth ------------------------------------------------------


@pytest.fixture(scope="module")
def exp2() -> SessionBundle:
    return SessionBundle.load(METADATA, recording=RECORDING)


@needs_data
def test_exp2_loads_ten_layers_and_reports_exactly_what_the_file_says(exp2):
    """The warnings must be the file's, neither invented nor swallowed.

    This asserted `warnings == ()` until the bundle was refit twice in one
    afternoon and picked up a `fit_warnings` entry both times.  Pinning the
    file's current content makes the suite fail whenever the writer improves,
    which trains everyone to edit the number rather than read it.  What must
    hold is the relationship: every warning on the bundle traces to something
    the TOML actually says, and nothing the TOML says is dropped.
    """
    assert len(exp2.layers) == 10
    assert exp2.missing == frozenset()
    assert exp2.dropped == dict.fromkeys(session.CSV_KINDS, 0)
    declared = tuple(exp2.meta.alignment.fit_warnings) + tuple(
        exp2.meta.alignment.validation_warnings
    )
    for text in declared:
        assert any(text in w for w in exp2.warnings), (
            f"the TOML declares {text!r} and the bundle never surfaced it"
        )


@needs_data
def test_exp2_is_validated_and_names_the_recording_that_is_open(exp2):
    # trust follows the file, not a snapshot of it: `validated = true` with a
    # fit warning is TRUST_WARN, and exp2 has been refit into and out of that
    # state twice.  What is pinned is the rule.
    alignment = exp2.meta.alignment
    assert alignment.validated is True
    expected = TRUST_OK if not alignment.warnings else TRUST_WARN
    assert exp2.trust == expected
    check = exp2.recording_check
    assert (check.name, check.rate, check.frames, check.channel) == (True,) * 4
    assert check.sha256 is None, "tier 3 must never run on load"
    assert exp2.meta.alignment.recording_channel == 0
    assert exp2.meta.alignment.duration_s == pytest.approx(607.104)
    assert exp2.meta.sample_rate_hz == 50000, "the DEVICE clock, not the recording's"


@needs_data
def test_exp2_trials_are_sorted_disjoint_and_the_right_shape(exp2):
    counts = {
        "trials.volley": 11,
        "trials.baseline": 13,
        "trials.silence": 12,
    }
    for layer_id, n in counts.items():
        layer = exp2[layer_id]
        assert len(layer) == n, layer_id
        assert layer.disjoint is True, layer_id
        assert np.all(np.diff(layer.starts) > 0), layer_id
    starts = np.sort(np.concatenate([exp2[i].starts for i in counts]))
    ends = np.concatenate([exp2[i].ends for i in counts])[
        np.argsort(np.concatenate([exp2[i].starts for i in counts]))
    ]
    assert np.all(starts[1:] >= ends[:-1])
    assert (starts[1:] - ends[:-1]).min() == pytest.approx(0.0545, abs=1e-4)
    durations = ends - starts
    # These are the spans as the reader builds them -- end minus start in
    # RECORDING seconds -- so they run a fit-scale factor (1.0000141) longer
    # than the device-clock duration_s column, up to 31 us on the 2.16 s trial.
    assert durations.min() == pytest.approx(0.15406, abs=1e-4)
    assert durations.max() == pytest.approx(2.16252, abs=1e-4)
    assert np.median(durations) == pytest.approx(0.5442, abs=1e-4)


@needs_data
def test_exp2_pulses_split_into_the_measured_evidence_classes(exp2):
    resting = exp2["pulses.resting"]
    assert [len(s) for s in resting.series] == [901, 7]
    assert [s.observed for s in resting.series] == [True, False]
    volley = exp2["pulses.volley"]
    assert [len(s) for s in volley.series] == [1278, 1]
    assert [s.observed for s in volley.series] == [True, False]
    # the amplitude ruling: the resting train is one level, volleys are 3.6x it
    amplitudes = np.concatenate(
        [s.frame["amplitude"].to_numpy() for s in resting.series]
    )
    assert amplitudes.size == 908
    assert np.all(amplitudes == 0.25)
    volley_amp = np.concatenate(
        [s.frame["amplitude"].to_numpy() for s in volley.series]
    )
    assert (volley_amp.min(), volley_amp.max()) == (0.6, 1.0)
    assert np.median(volley_amp) == pytest.approx(0.91)


@needs_data
def test_exp2_detections_split_into_explained_and_unexplained(exp2):
    assert len(exp2["detections.explained"]) == 2179
    assert len(exp2["detections.unexplained"]) == 1219
    assert exp2["detections.explained"].unjoined == 0


@needs_data
def test_exp2_localization_runs_pair_cleanly(exp2):
    runs = exp2["localization"]
    assert len(runs) == 31
    assert not runs.open_left.any() and not runs.open_right.any()
    assert np.isfinite(runs.starts).all() and np.isfinite(runs.ends).all()
    assert runs.durations.min() == pytest.approx(0.0545, abs=1e-4)
    assert runs.durations.max() == pytest.approx(58.008, abs=1e-3)
    assert runs.durations.sum() == pytest.approx(360.015, abs=1e-3)


@needs_data
def test_exp2_controls_offer_only_the_channels_that_move(exp2):
    track = exp2["controls"]
    assert len(track) == 1373
    assert set(track.channels) == {"tick_hz", "randomness"}
    assert np.unique(track.channels["tick_hz"]).size == 21
    assert track.ranges["tick_hz"] == (0.5, 20.0)
    assert np.unique(track.channels["randomness"]).size == 9
    assert track.ranges["randomness"] == (0.067, 1.0)
    assert "volley_amplitude" not in track.channels
    assert track.t_end == pytest.approx(607.104)


@needs_data
def test_exp2_pins_every_dtype_the_head_would_get_wrong(exp2):
    """Each trap column, and a real value from the end of the file."""
    resting = exp2["pulses.resting"].series[0].frame
    assert resting["trial_number"].dtype == pl.Int64
    assert resting["treatment"].dtype == pl.String
    assert resting["stimulus_item"].dtype == pl.Int64
    assert resting["pulse_index_in_item"].dtype == pl.Int64
    volley = exp2["pulses.volley"].series[0].frame
    assert volley["trial_number"][-1] == 36
    assert volley["treatment"][-1] == "volley"
    assert volley["stimulus_item"][-1] is not None

    events = exp2["session_events"].series[0].frame
    assert events["records_lost"].dtype == pl.Int64
    assert events["records_lost"].null_count() == events.height
    assert events["radio_link_up"].dtype == pl.Boolean

    novel = exp2["detections.unexplained"].series[0].frame
    assert novel["source_row"].dtype == pl.Int64
    assert novel["source_row"].null_count() == 1219
    assert novel["explained_by_log"].dtype == pl.Boolean

    controls = exp2["controls"].frame
    for column in ("throttle_pulse_us", "receiver_zero_us"):
        assert controls[column].dtype == pl.Int64, column
        assert controls[column][0] is None, column
        assert controls[column][-1] is not None, column


@needs_data
def test_exp2_keeps_the_ambient_train_null_rather_than_filling_it(exp2):
    resting = pl.concat([s.frame for s in exp2["pulses.resting"].series])
    localization = resting.filter(pl.col("pulse_type") == "localization")
    assert localization.height == 893
    assert localization["treatment"].null_count() == 893
    assert localization["trial_number"].null_count() == 893


# --- the partition that justifies one hue per treatment (spec 7.3, 9.4) -----


def _brute_inside(times: np.ndarray, starts: np.ndarray, ends: np.ndarray):
    """Reference membership test: no searchsorted, no merge, just compare."""
    if times.size == 0 or starts.size == 0:
        return np.zeros(times.size, dtype=bool)
    hit = (times[:, None] >= starts[None, :]) & (times[:, None] < ends[None, :])
    return hit.any(axis=1)


@needs_data
def test_the_treatment_partition_that_lets_one_hue_serve_a_span_and_its_pulses(exp2):
    """Break this and a red bracket sits over teal pulses and lies about it."""
    resting = pl.concat([s.frame for s in exp2["pulses.resting"].series])
    resting_t = np.sort(
        np.concatenate([s.times for s in exp2["pulses.resting"].series])
    )
    order = np.argsort(resting["recording_time_s"].to_numpy())
    kinds = resting["pulse_type"].to_numpy()[order]
    localization = resting_t[kinds == "localization"]
    baseline = resting_t[kinds == "baseline"]
    volley_t = np.sort(np.concatenate([s.times for s in exp2["pulses.volley"].series]))

    trials = [exp2[i] for i in ("trials.volley", "trials.baseline", "trials.silence")]
    all_starts = np.concatenate([t.starts for t in trials])
    all_ends = np.concatenate([t.ends for t in trials])

    assert localization.size == 893
    assert _brute_inside(localization, all_starts, all_ends).sum() == 0
    assert baseline.size == 15
    base = exp2["trials.baseline"]
    assert _brute_inside(baseline, base.starts, base.ends).sum() == 15
    vol = exp2["trials.volley"]
    assert volley_t.size == 1279
    assert _brute_inside(volley_t, vol.starts, vol.ends).sum() == 1279
    silence = exp2["trials.silence"]
    every = np.sort(np.concatenate([resting_t, volley_t]))
    assert _brute_inside(every, silence.starts, silence.ends).sum() == 0
    assert (silence.frame["pulses_emitted"] == 0).all()
    assert silence.frame.height == 12


@needs_data
def test_every_explained_detection_is_bit_identical_to_its_parent_pulse(exp2):
    """What licenses the HEARD row borrowing the PULSES row's hue.

    The offset a reader sees between a pulse tick and its heard stub is then
    the literal fit residual and nothing else.
    """
    pulses = pl.concat(
        [
            s.frame
            for layer in exp2.points()
            for s in layer.series
            if "match_status" in s.frame.columns
        ]
    )
    matched = pulses.filter(pl.col("match_status") == "matched")
    matched_t = np.sort(matched["detected_time_s"].to_numpy())
    explained = np.sort(
        np.concatenate([s.times for s in exp2["detections.explained"].series])
    )
    assert matched_t.size == explained.size == 2179
    assert np.abs(matched_t - explained).max() == 0.0
    assert exp2["detections.explained"].unjoined == 0
    # 1278 with a volley pulse behind them and 901 with a resting one, drawn
    # in one hue because both are the played pulse heard back
    assert sorted(len(s) for s in exp2["detections.explained"].series) == [901, 1278]
    assert {s.role for s in exp2["detections.explained"].series} == {"pulse"}


# --- exp3: the split recording ----------------------------------------------

#: exp3 (PULS0005) is four WAVs treated as one recording, and it is the better
#: regression fixture: 629 of its 5281 pulses are predicted (11.9%) against
#: exp2's 7 of 2187 (0.3%), which is the difference between a rendering path
#: that is exercised and one that is technically covered.
EXP3 = Path("/home/weygoldt/wrk/analyses/fakefish/experiments/exp3")
METADATA3 = EXP3 / "PULS0005_metadata.toml"

needs_exp3 = pytest.mark.skipif(
    not METADATA3.is_file(),
    reason="the paired exp3 session is not on this machine",
)


@pytest.fixture(scope="module")
def exp3() -> SessionBundle:
    return SessionBundle.load(METADATA3)


@needs_exp3
def test_exp3_names_all_four_of_its_files(exp3):
    fit = exp3.meta.alignment
    assert fit.is_split is True
    assert len(fit.recording_files) == 4
    assert len(fit.recording_sha256s) == 4
    assert len(set(fit.recording_sha256s)) == 4, "one digest per file, not four copies"
    assert sum(fit.recording_file_frames) == fit.recording_frames


@needs_exp3
def test_exp3_can_be_asked_whether_a_bundle_belongs_to_what_is_open(exp3):
    """The check that had no opinion at all while only the singular key was read."""
    for name in exp3.meta.alignment.recording_files:
        wav = EXP3 / name
        if not wav.is_file():
            pytest.skip("the exp3 recordings are not on this machine")
        check = exp3.meta.check_recording(wav)
        assert check.name is True, name
        assert check.frames is True, name
        assert check.ok is True, name


@needs_exp3
def test_exp3_declares_a_gap_at_every_join_and_this_viewer_only_states_it(exp3):
    joins = exp3.meta.alignment.joins()
    assert len(joins) == 3
    assert [round(t, 3) for t, _ in joins] == [931.968, 1863.936, 2795.904]
    assert [gap for _, gap in joins] == [0.032, 0.032, -0.12]


@needs_exp3
def test_exp3_says_out_loud_why_its_badge_warns(exp3):
    """A badge that warns must be accompanied by the reason.

    Not "exp3 warns": this bundle has been refit four times while this feature
    was being written and has been through every trust state there is -- it
    carried eleven fit warnings one evening and none the next.  Pinning which
    state it is in makes the suite fail whenever the alignment improves, which
    is the opposite of what a test should reward.  The rule is what holds: the
    badge follows `validated` plus the declared warnings, and every warning
    the TOML declares reaches the status bar.
    """
    alignment = exp3.meta.alignment
    expected = (
        TRUST_OK
        if alignment.validated and not alignment.warnings
        else (TRUST_WARN if alignment.validated else TRUST_UNVALIDATED)
    )
    assert exp3.trust == expected
    for warning in alignment.warnings:
        assert any(warning in w for w in exp3.warnings), (
            f"the TOML declares {warning!r} and the bundle never surfaced it"
        )


@needs_exp3
def test_exp3_measures_its_residual_once_per_file(exp3):
    stats = exp3.residuals
    assert stats.split is True
    assert [r.label for r in stats] == [f"file {i} of 4" for i in (1, 2, 3, 4)]
    assert [round(r.t0, 3) for r in stats] == [0.0, 931.968, 1863.936, 2795.904]
    assert sum(r.total for r in stats) == len(
        np.concatenate(
            [
                s.times
                for i in ("pulses.resting", "pulses.volley")
                for s in exp3[i].series
            ]
        )
    )
    # The reason a per-region figure is worth having at all is that a
    # session-wide match_fraction is not a promise about the region on
    # screen -- in either direction.  When this was written the last file
    # confirmed 259 of its 874 pulses against a session figure of 0.881; a
    # refit the same afternoon took that region to 0.82.  Pinning either
    # number would make the suite fail whenever the alignment changes, which
    # is exactly the thing this statistic exists to observe rather than to
    # legislate.  What is pinned is that the regions are measured separately
    # and can therefore disagree with the whole.
    fractions = [r.match_fraction for r in stats.regions]
    assert all(0.0 <= f <= 1.0 for f in fractions)
    assert len(set(round(f, 6) for f in fractions)) > 1, (
        "four regions reporting one identical fraction means they are not "
        "being measured apart"
    )
    # and the lookup a readout would use lands in the right region
    assert stats.at(3000.0) is stats.regions[-1]


@needs_exp3
def test_exp3_builds_every_layer_and_exercises_the_predicted_path(exp3):
    assert len(exp3.layers) == 10
    predicted = [
        s
        for i in ("pulses.resting", "pulses.volley")
        for s in exp3[i].series
        if not s.observed
    ]
    # However many predicted pulses this refit leaves -- 629 when this was
    # written, 17 after the next one -- they must arrive in their OWN series,
    # never folded in with the observed ones.  That separation is the whole
    # mechanism by which a position the recording never confirmed cannot be
    # drawn as though it had been, so it is what gets pinned; the count is
    # the alignment's business and moves whenever fakefish improves.
    assert predicted, "a predicted series must exist even when it is empty"
    for series in predicted:
        assert not series.observed
        if len(series) and "detected_time_s" in series.frame.columns:
            detected = series.frame["detected_time_s"].to_numpy().astype(float)
            assert np.isnan(detected).all(), (
                "a predicted pulse is predicted precisely because nothing "
                "was detected for it"
            )


# --- ground truth against the WAV -------------------------------------------


@needs_data
def test_every_matched_pulse_lands_on_a_deflection_in_the_recording(exp2):
    """The one test that says the backend is right.

    For each sampled matched pulse, look at the recording in a window narrower
    than the tightest pulse spacing in the file -- volleys are about 4.1 ms
    apart -- and find the largest deflection.  It has to be within the
    tolerance the alignment itself claims, ``match_tolerance_s = 0.5 ms``.

    The channel comes from the TOML: the fit is per channel, and reading it
    off is the difference between this passing and passing by luck on a
    stereo file.  Measured: 100% within tolerance, median error 62 µs.
    """
    soundfile = pytest.importorskip("soundfile")
    fit = exp2.meta.alignment
    channel = fit.recording_channel or 0
    rate = fit.recording_rate_hz or soundfile.info(str(RECORDING)).samplerate
    tolerance = fit.match_tolerance_s
    assert tolerance == 0.0005

    observed = np.sort(
        np.concatenate(
            [
                s.times
                for layer_id in ("pulses.resting", "pulses.volley")
                for s in exp2[layer_id].series
                if s.observed
            ]
        )
    )
    sample = observed[np.linspace(0, observed.size - 1, 300).astype(int)]

    half = int(0.001 * rate)  # +-1 ms, well inside the 4.1 ms volley spacing
    errors = []
    with soundfile.SoundFile(str(RECORDING)) as f:
        for t in sample:
            centre = int(round(t * rate))
            start = max(0, centre - half)
            f.seek(start)
            block = f.read(2 * half, dtype="float64", always_2d=True)
            if block.shape[0] < 2 * half:
                continue
            peak = int(np.argmax(np.abs(block[:, channel])))
            errors.append((start + peak - centre) / rate)

    errors = np.abs(np.asarray(errors))
    assert errors.size > 250
    assert np.median(errors) <= tolerance
    within = float(np.mean(errors <= tolerance))
    assert within > 0.98, f"only {100 * within:.1f}% within {1e3 * tolerance} ms"


@needs_data
def test_every_volley_trial_brackets_a_dense_burst(exp2):
    """A volley bracket is not a label, it is a claim about the recording.

    Inside a volley span the microphone hears 94-305 detections per second
    against a session-wide 5.6/s -- 17x to 54x.  The gate is 15x: enough
    headroom that a single mispositioned bracket fails it, and far enough
    below the measured minimum that a quieter session does not.
    """
    detections = np.sort(
        np.concatenate(
            [
                s.times
                for layer_id in ("detections.explained", "detections.unexplained")
                for s in exp2[layer_id].series
            ]
        )
    )
    duration = exp2.meta.alignment.duration_s
    session_rate = detections.size / duration
    assert session_rate == pytest.approx(5.60, abs=0.05)

    volley = exp2["trials.volley"]
    rates = []
    for i in range(len(volley)):
        t0, t1 = float(volley.starts[i]), float(volley.ends[i])
        i0 = int(np.searchsorted(detections, t0, side="left"))
        i1 = int(np.searchsorted(detections, t1, side="right"))
        rates.append((i1 - i0) / (t1 - t0))
    rates = np.asarray(rates)
    assert rates.size == 11
    assert (rates / session_rate > 15.0).all(), rates / session_rate
    assert rates.min() > 90.0 and rates.max() < 320.0


@needs_data
def test_a_silence_trial_holds_no_pulse_the_stimulator_can_be_blamed_for(exp2):
    """The control really is silent, to within the fit's own tolerance.

    A handful of the 2179 explained detections land inside a silence span --
    two on the fit as it stands, 63 µs and 10 µs inside their brackets -- and
    every one of them is a pulse just outside the bracket whose detection the
    fit residual carried across the edge.  So the claim is not "zero", it is
    "within ``match_tolerance_s`` of an edge", and that is what is asserted:
    an exact count would be an anchor on the FIT rather than on the session,
    and it moves whenever the bundle is refitted.

    The count is still bounded, because the bound is what carries the meaning.
    A stimulator that really fired during the control would not leave two
    detections at the rim: the volley trials next door run 90-320 detections
    per second, so a control that had fired at all would hold dozens.
    """
    tolerance = exp2.meta.alignment.match_tolerance_s
    silence = exp2["trials.silence"]
    explained = np.sort(
        np.concatenate([s.times for s in exp2["detections.explained"].series])
    )
    intruders = []
    for i in range(len(silence)):
        t0, t1 = float(silence.starts[i]), float(silence.ends[i])
        i0 = int(np.searchsorted(explained, t0, side="left"))
        i1 = int(np.searchsorted(explained, t1, side="right"))
        for t in explained[i0:i1]:
            intruders.append(min(t - t0, t1 - t))
    assert intruders, "no explained detection at all is a broken join, not silence"
    assert max(intruders) <= tolerance
    assert len(intruders) <= 5, intruders

    # and the animal is unbothered by the control: 7 unexplained in there
    novel = exp2["detections.unexplained"].series[0].times
    heard = sum(
        int(np.searchsorted(novel, float(silence.ends[i]), side="right"))
        - int(np.searchsorted(novel, float(silence.starts[i]), side="left"))
        for i in range(len(silence))
    )
    assert heard == 7
