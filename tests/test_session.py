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

from audian import session  # noqa: E402
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
    ground-truth re-check that cannot be written honestly.  Measured on exp2 at
    the TOML's own numbers (floor 0.018043927, refractory 0.002 s, SNR 8.0):
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


def test_the_layer_ids_and_tracks_are_the_ten_the_ribbon_expects(tmp_path):
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
    assert off == {session.LAYER_SESSION_EVENTS, session.LAYER_CONTROLS}


def test_localization_and_baseline_pulses_share_one_layer(tmp_path):
    """The user's ruling: one hue for the resting rate, both pulse types."""
    rows = [pulse(1.0, "localization"), pulse(2.0, "baseline")]
    bundle = simple(tmp_path, pulses=rows)
    layer = bundle["pulses.resting"]
    assert len(layer) == 2
    assert layer.role == "resting" == bundle["trials.baseline"].role
    # and the distinction is still in the data, and still in the readout
    assert layer.series[0].frame["pulse_type"].to_list() == ["localization", "baseline"]
    assert "baseline" in layer.describe(0, 1)


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
    roles = {s.role: s.times.tolist() for s in layer.series}
    assert roles == {"volley": [3.0], "resting": [1.0]}
    assert layer.unjoined == 0
    assert layer.role == "detection.novel"  # the fallback for an orphan


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
def test_exp2_loads_ten_layers_and_says_nothing_is_wrong(exp2):
    assert len(exp2.layers) == 10
    assert exp2.warnings == ()
    assert exp2.missing == frozenset()
    assert exp2.dropped == dict.fromkeys(session.CSV_KINDS, 0)


@needs_data
def test_exp2_is_validated_and_names_the_recording_that_is_open(exp2):
    assert exp2.trust == TRUST_OK
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
    assert [len(s) for s in volley.series] == [1279]
    assert volley.series[0].observed is True
    # the amplitude ruling: the resting train is one level, volleys are 3.6x it
    amplitudes = np.concatenate(
        [s.frame["amplitude"].to_numpy() for s in resting.series]
    )
    assert amplitudes.size == 908
    assert np.all(amplitudes == 0.25)
    volley_amp = volley.series[0].frame["amplitude"].to_numpy()
    assert (volley_amp.min(), volley_amp.max()) == (0.6, 1.0)
    assert np.median(volley_amp) == pytest.approx(0.91)


@needs_data
def test_exp2_detections_split_into_explained_and_the_eel(exp2):
    assert len(exp2["detections.explained"]) == 2180
    assert len(exp2["detections.unexplained"]) == 842
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
    assert novel["source_row"].null_count() == 842
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
    volley_t = exp2["pulses.volley"].series[0].times

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
    assert matched_t.size == explained.size == 2180
    assert np.abs(matched_t - explained).max() == 0.0
    assert exp2["detections.explained"].unjoined == 0
    roles = {s.role: len(s) for s in exp2["detections.explained"].series}
    assert roles == {"volley": 1279, "resting": 901}


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

    Inside a volley span the microphone hears 96-304 detections per second
    against a session-wide 5.0/s -- 19x to 61x.  The gate is 15x: enough
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
    assert session_rate == pytest.approx(4.98, abs=0.05)

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

    One of the 2180 explained detections lands inside a silence span: the
    pulse at 146.107541 s starts 20 µs AFTER trial 3 closes, and its detection
    at 146.107458 s therefore falls 63 µs inside it.  That is the fit residual
    crossing a bracket edge, not the stimulator firing during the control, so
    the assertion is bounded by ``match_tolerance_s`` rather than being zero.
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
    assert len(intruders) == 1
    assert max(intruders) <= tolerance

    # and the eel is unbothered by the control: 6 novel detections in there
    novel = exp2["detections.unexplained"].series[0].times
    heard = sum(
        int(np.searchsorted(novel, float(silence.ends[i]), side="right"))
        - int(np.searchsorted(novel, float(silence.starts[i]), side="left"))
        for i in range(len(silence))
    )
    assert heard == 6
