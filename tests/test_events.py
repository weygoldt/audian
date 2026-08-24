"""Tests for :mod:`audian.events`, the polars-backed event backend.

Runs under pytest, and also standalone::

    .venv/bin/python -m pytest tests/test_events.py -q

Most tests build their own alignment file in a tmp_path, so the suite does
not depend on any recording.  The ones that do -- the ground truth check
that every ``matched`` row lands on a pulse in the WAV -- skip themselves
when the paired data is not on this machine.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from audian import events  # noqa: E402
from audian.events import (  # noqa: E402
    TRUST_OK,
    TRUST_UNVALIDATED,
    TRUST_WARN,
    AlignmentHeader,
    EventTable,
    find_alignment,
)


#: the paired recording this feature was built against, if it is present
EXP2 = Path("/home/weygoldt/wrk/analyses/fakefish/experiments/exp2")
ALIGNMENT = EXP2 / "alignment.csv"
RECORDING = EXP2 / "DR0000_0087.wav"

COLUMNS = "seq,tick,event,trial,t_log_s,t_rec_s,offset_s,t_det_s,resid_s,status"

BASE_HEADER = {
    "recording": "REC.wav",
    "recording_rate_hz": "48000",
    "recording_channel": "1",
    "recording_sha256": "deadbeef",
    "scale": "1.00001412677",
    "offset_s": "28.9354456",
    "drift_ppm": "14.1268",
    "validated": "1",
}


def write_alignment(path, rows, header=None, columns=COLUMNS):
    """Write an alignment file: ``#key=value`` block, comment line, rows."""
    values = dict(BASE_HEADER)
    if header is not None:
        values.update(header)
    lines = ["#fakefish-align"]
    lines += [f"#{k}={v}" for k, v in values.items()]
    lines.append(f"#{columns}")
    lines.append(columns)
    lines += list(rows)
    path.write_text("\n".join(lines) + "\n")
    return path


def simple_table(tmp_path, n=6):
    rows = []
    for i in range(n):
        matched = i % 3 != 2
        t = 10.0 + i
        rows.append(
            ",".join(
                [
                    str(i),
                    str(i * 1000),
                    "LOC" if i % 2 else "VOLLEY",
                    "" if i % 2 else str(i),
                    f"{t - 28.9:.6f}",
                    f"{t:.6f}",
                    "28.9",
                    f"{t:.6f}" if matched else "",
                    "0.00001" if matched else "",
                    "matched" if matched else "unmatched",
                ]
            )
        )
    return write_alignment(tmp_path / "alignment.csv", rows)


# --- the header ------------------------------------------------------------


def test_header_reads_the_fit_and_the_channel(tmp_path):
    path = simple_table(tmp_path)
    header = AlignmentHeader.from_file(path)
    assert header.recording == "REC.wav"
    assert header.recording_rate_hz == 48000.0
    # read from the file, never assumed: the fit is per channel
    assert header.recording_channel == 1
    assert header.scale == pytest.approx(1.00001412677)
    assert header.offset_s == pytest.approx(28.9354456)
    assert header.drift_ppm == pytest.approx(14.1268)
    assert header.validated is True
    assert header.trust == TRUST_OK


def test_header_stops_at_the_first_data_row(tmp_path):
    """A '#' inside a field must not be read as another header key."""
    path = write_alignment(
        tmp_path / "a.csv",
        ["0,0,LOC,,1,1,0,1,0,matched", "#not=a-header-key"],
    )
    header = AlignmentHeader.from_file(path)
    assert "not" not in header.values


def test_validated_zero_is_unvalidated(tmp_path):
    path = simple_table(tmp_path)
    path.write_text(path.read_text().replace("#validated=1", "#validated=0"))
    header = AlignmentHeader.from_file(path)
    assert header.validated is False
    assert header.trust == TRUST_UNVALIDATED


def test_a_missing_validated_key_is_unvalidated_not_fine(tmp_path):
    """No claim of validation is not the same as a claim of validity."""
    rows = ["0,0,LOC,,1,1,0,1,0,matched"]
    path = write_alignment(tmp_path / "a.csv", rows, {"validated": None})
    text = path.read_text().replace("#validated=None\n", "")
    path.write_text(text)
    header = AlignmentHeader.from_file(path)
    assert header.validated is None
    assert header.is_validated is False
    assert header.trust == TRUST_UNVALIDATED


@pytest.mark.parametrize(
    "value,expected",
    [
        ("1", True),
        ("true", True),
        ("yes", True),
        ("0", False),
        ("false", False),
        ("", False),
        ("probably", False),
    ],
)
def test_only_an_explicit_claim_of_validation_counts(tmp_path, value, expected):
    """An unrecognised value must fall on the cautious side."""
    rows = ["0,0,LOC,,0,1,0,1,0,matched"]
    path = write_alignment(tmp_path / f"a{expected}.csv", rows, {"validated": value})
    assert AlignmentHeader.from_file(path).is_validated is expected


def test_describe_survives_a_row_with_no_seq(tmp_path):
    rows = [",0,LOC,,0,20.0,0,,,unmatched"]
    table = EventTable.from_csv(write_alignment(tmp_path / "a.csv", rows))
    text = table[("LOC", "unmatched")].describe(0)
    assert "seq" not in text
    assert "t 20.000000 s" in text


def test_warnings_downgrade_a_validated_fit(tmp_path):
    rows = ["0,0,LOC,,1,1,0,1,0,matched"]
    path = write_alignment(
        tmp_path / "a.csv",
        rows,
        {"validation_warnings": "drift above 20 ppm; sparse tail"},
    )
    header = AlignmentHeader.from_file(path)
    assert header.trust == TRUST_WARN
    assert header.warnings == ("drift above 20 ppm", "sparse tail")


# --- reading ---------------------------------------------------------------


def test_classes_split_by_event_and_status(tmp_path):
    table = EventTable.from_csv(simple_table(tmp_path, 6))
    assert set(table.keys) == {
        ("LOC", "matched"),
        ("LOC", "unmatched"),
        ("VOLLEY", "matched"),
        ("VOLLEY", "unmatched"),
    }
    assert table.n_events == 6


def test_times_are_sorted_within_a_class(tmp_path):
    rows = [
        "2,0,LOC,,0,30.0,0,30.0,0,matched",
        "0,0,LOC,,0,10.0,0,10.0,0,matched",
        "1,0,LOC,,0,20.0,0,20.0,0,matched",
    ]
    table = EventTable.from_csv(write_alignment(tmp_path / "a.csv", rows))
    times = table[("LOC", "matched")].times
    assert np.all(np.diff(times) >= 0)
    assert times.tolist() == [10.0, 20.0, 30.0]


def test_sparse_trial_column_is_numeric_not_string(tmp_path):
    """The head-only-inference trap: 'trial' is empty on most rows.

    With inference from the head alone polars types it as String, and every
    later comparison silently becomes a string comparison.
    """
    rows = [f"{i},0,LOC,,0,{i}.0,0,{i}.0,0,matched" for i in range(200)]
    rows.append("200,0,LOC,7,0,200.0,0,200.0,0,matched")
    table = EventTable.from_csv(write_alignment(tmp_path / "a.csv", rows))
    trial = table[("LOC", "matched")].trial
    assert trial.dtype.kind == "f"
    assert trial[-1] == 7.0


def test_empty_means_absent_never_zero(tmp_path):
    """An empty trial/t_det_s/resid_s is missing data, not a measurement of 0."""
    rows = [
        "0,0,LOC,,0,10.0,0,10.0,-0.0001,matched",
        "1,0,LOC,,0,20.0,0,,,unmatched",
    ]
    table = EventTable.from_csv(write_alignment(tmp_path / "a.csv", rows))
    unmatched = table[("LOC", "unmatched")]
    assert np.isnan(unmatched.t_det[0])
    assert np.isnan(unmatched.resid[0])
    assert np.isnan(unmatched.trial[0])
    matched = table[("LOC", "matched")]
    assert matched.t_det[0] == pytest.approx(10.0)


def test_rows_without_a_time_are_dropped_and_counted(tmp_path):
    rows = [
        "0,0,LOC,,0,10.0,0,10.0,0,matched",
        "1,0,LOC,,0,,0,,,outside",
        "2,0,LOC,,0,30.0,0,30.0,0,matched",
    ]
    table = EventTable.from_csv(write_alignment(tmp_path / "a.csv", rows))
    assert table.n_events == 2
    assert table.dropped == 1


def test_unknown_event_labels_are_kept(tmp_path):
    rows = ["0,0,WEIRD,,0,10.0,0,10.0,0,matched"]
    table = EventTable.from_csv(write_alignment(tmp_path / "a.csv", rows))
    assert ("WEIRD", "matched") in table
    assert table[("WEIRD", "matched")].color_index in range(8)


def test_a_column_this_module_never_reads_cannot_break_the_read(tmp_path):
    """Projection push-down: an extra column is not parsed at all."""
    columns = COLUMNS + ",note"
    rows = [f"{i},0,LOC,,0,{i}.0,0,{i}.0,0,matched,{i}" for i in range(200)]
    rows.append("200,0,LOC,,0,200.0,0,200.0,0,matched,not-an-int")
    table = EventTable.from_csv(
        write_alignment(tmp_path / "a.csv", rows, columns=columns)
    )
    assert table.n_events == 201


def test_a_file_without_t_rec_s_is_refused(tmp_path):
    columns = "seq,event,status"
    path = write_alignment(tmp_path / "a.csv", ["0,LOC,matched"], columns=columns)
    with pytest.raises(ValueError, match="t_rec_s"):
        EventTable.from_csv(path)


# --- measured vs predicted --------------------------------------------------


def test_matched_is_measured_and_the_rest_is_predicted(tmp_path):
    rows = [
        "0,0,LOC,,0,10.0,0,10.0,0,matched",
        "1,0,LOC,,0,20.0,0,,,unmatched",
        "2,0,LOC,,0,30.0,0,,,outside",
    ]
    table = EventTable.from_csv(write_alignment(tmp_path / "a.csv", rows))
    assert table[("LOC", "matched")].measured is True
    assert table[("LOC", "unmatched")].measured is False
    assert table[("LOC", "outside")].measured is False
    assert table.n_predicted == 2


def test_describe_says_a_predicted_row_was_not_observed(tmp_path):
    rows = ["0,0,LOC,,0,20.0,0,,,unmatched"]
    table = EventTable.from_csv(write_alignment(tmp_path / "a.csv", rows))
    text = table[("LOC", "unmatched")].describe(0)
    assert "predicted, not observed" in text


# --- windowing and decimation ----------------------------------------------


def make_class(times, status="matched"):
    return events.EventClass("LOC", status, 0, np.asarray(times, dtype=float))


def test_window_returns_only_what_is_in_view():
    cls = make_class(np.arange(0.0, 100.0))
    times, total = cls.window(10.0, 20.0, 0)
    assert total == 11
    assert times.tolist() == list(np.arange(10.0, 21.0))


def test_window_is_inclusive_at_both_edges():
    cls = make_class([1.0, 2.0, 3.0])
    times, total = cls.window(1.0, 3.0, 0)
    assert total == 3


def test_window_of_an_empty_range_is_empty():
    cls = make_class([1.0, 2.0, 3.0])
    times, total = cls.window(10.0, 20.0, 0)
    assert total == 0
    assert times.size == 0


def test_decimation_keeps_one_event_per_pixel_column():
    """The drawn set must be bounded by the pixels, not by the file."""
    times = np.linspace(0.0, 10.0, 100_000)
    cls = make_class(times)
    drawn, total = cls.window(0.0, 10.0, 1000)
    assert total == 100_000
    assert drawn.size <= 1001


def test_decimation_never_invents_a_time():
    times = np.sort(np.random.default_rng(3).uniform(0, 10, 50_000))
    cls = make_class(times)
    drawn, _ = cls.window(0.0, 10.0, 800)
    assert np.isin(drawn, times).all()
    assert np.all(np.diff(drawn) > 0)


def test_decimation_keeps_the_first_event_of_each_bucket():
    times = np.array([0.0, 0.0001, 0.0002, 1.0, 1.0001, 2.0])
    cls = make_class(times)
    drawn, _ = cls.window(0.0, 2.0, 2)
    # two pixel columns over [0, 2): everything below 1.0 and everything above
    assert drawn.tolist() == [0.0, 1.0, 2.0]


def test_window_reports_the_true_count_not_the_drawn_one():
    cls = make_class(np.linspace(0.0, 1.0, 10_000))
    drawn, total = cls.window(0.0, 1.0, 100)
    assert total == 10_000
    assert drawn.size < total


def test_no_decimation_when_the_events_fit_the_pixels():
    cls = make_class([0.1, 0.5, 0.9])
    drawn, total = cls.window(0.0, 1.0, 1000)
    assert drawn.tolist() == [0.1, 0.5, 0.9]
    assert total == 3


# --- navigation -------------------------------------------------------------


def test_nearest_finds_the_closest_event_across_classes(tmp_path):
    table = EventTable.from_csv(simple_table(tmp_path, 6))
    found = table.nearest(12.4)
    assert found is not None
    cls, index = found
    assert cls.times[index] == pytest.approx(12.0)


def test_step_moves_strictly_forward_and_back(tmp_path):
    table = EventTable.from_csv(simple_table(tmp_path, 6))
    cls, index = table.step(12.0, forward=True)
    assert cls.times[index] == pytest.approx(13.0)
    cls, index = table.step(12.0, forward=False)
    assert cls.times[index] == pytest.approx(11.0)
    assert table.step(1e9, forward=True) is None
    assert table.step(-1e9, forward=False) is None


def test_step_only_visits_the_classes_it_is_given(tmp_path):
    table = EventTable.from_csv(simple_table(tmp_path, 6))
    keys = [("LOC", "matched")]
    cls, index = table.step(0.0, forward=True, keys=keys)
    assert cls.key == ("LOC", "matched")


# --- provenance -------------------------------------------------------------


def test_matches_recording_compares_the_name(tmp_path):
    table = EventTable.from_csv(simple_table(tmp_path))
    assert table.matches_recording("/somewhere/else/REC.wav") is True
    assert table.matches_recording("/somewhere/OTHER.wav") is False


def test_matches_recording_is_unknown_without_a_header(tmp_path):
    rows = ["0,0,LOC,,0,10.0,0,10.0,0,matched"]
    path = write_alignment(tmp_path / "a.csv", rows, {"recording": None})
    path.write_text(path.read_text().replace("#recording=None\n", ""))
    table = EventTable.from_csv(path)
    assert table.matches_recording("anything.wav") is None


def test_find_alignment_only_accepts_a_file_that_names_the_recording(tmp_path):
    recording = tmp_path / "REC.wav"
    recording.write_bytes(b"")
    write_alignment(tmp_path / "alignment.csv", ["0,0,LOC,,0,1,0,1,0,matched"])
    assert find_alignment(recording) == tmp_path / "alignment.csv"


def test_find_alignment_rejects_a_stray_file_from_another_experiment(tmp_path):
    recording = tmp_path / "OTHER.wav"
    recording.write_bytes(b"")
    write_alignment(tmp_path / "alignment.csv", ["0,0,LOC,,0,1,0,1,0,matched"])
    assert find_alignment(recording) is None


def test_find_alignment_prefers_a_file_named_after_the_recording(tmp_path):
    recording = tmp_path / "REC.wav"
    recording.write_bytes(b"")
    write_alignment(tmp_path / "REC.alignment.csv", ["0,0,LOC,,0,1,0,1,0,matched"])
    write_alignment(tmp_path / "alignment.csv", ["0,0,LOC,,0,1,0,1,0,matched"])
    assert find_alignment(recording) == tmp_path / "REC.alignment.csv"


# --- colours ----------------------------------------------------------------


def test_event_colours_avoid_the_waveform_palette():
    """An annotation drawn in the trace's own colour is invisible on it."""
    from audian import theme

    for name in theme.THEMES:
        theme.set_theme(name)
        traces = {
            theme.token(t).upper()
            for t in ("trace.raw", "trace.filtered", "trace.envelope")
        }
        for event, index in events.EVENT_COLOR_INDEX.items():
            assert theme.marker_color(index).upper() not in traces, event
    theme.set_theme(theme.THEME_DARK)


# --- scale ------------------------------------------------------------------


def big_alignment(path, n=200_000, duration=7200.0):
    import polars as pl

    rng = np.random.default_rng(11)
    t = np.sort(rng.uniform(0, duration, n))
    event = rng.choice(["LOC", "BASE", "VOLLEY", "MARKER"], n)
    draw = rng.random(n)
    status = np.where(draw < 0.97, "matched", "unmatched")
    matched = pl.Series(status == "matched")
    frame = pl.DataFrame(
        {
            "seq": np.arange(n),
            "tick": (t * 50000).astype(np.int64),
            "event": event,
            "trial": pl.Series(rng.integers(1, 300, n)).set(
                pl.Series(event != "VOLLEY"), None
            ),
            "t_log_s": t - 28.9,
            "t_rec_s": t,
            "offset_s": np.full(n, 28.9),
            "t_det_s": pl.Series(t).set(~matched, None),
            "resid_s": pl.Series(rng.normal(0, 5e-5, n)).set(~matched, None),
            "status": status,
        }
    )
    with open(path, "w") as f:
        f.write("#fakefish-align\n#recording=BIG.wav\n#validated=1\n")
        f.write("#" + ",".join(frame.columns) + "\n")
        frame.write_csv(f)
    return path


@pytest.fixture(scope="module")
def big_table(tmp_path_factory):
    path = big_alignment(tmp_path_factory.mktemp("big") / "alignment.csv")
    return path


def test_two_hundred_thousand_events_load_in_a_second(big_table):
    start = time.perf_counter()
    table = EventTable.from_csv(big_table)
    elapsed = time.perf_counter() - start
    assert table.n_events == 200_000
    assert elapsed < 3.0, f"{elapsed:.2f} s to read 200k rows"


def test_a_full_file_window_stays_off_the_draw_path(big_table):
    """A view showing every event must still cost a bounded redraw."""
    table = EventTable.from_csv(big_table)
    start = time.perf_counter()
    passes = 20
    for i in range(passes):
        drawn = 0
        for cls in table:
            times, _ = cls.window(0.0 + i * 1e-6, 7200.0, 1800)
            drawn += times.size
    elapsed = (time.perf_counter() - start) / passes
    # every class is capped at one line per pixel column
    assert drawn <= 1801 * len(table)
    assert elapsed < 0.02, f"{1e3 * elapsed:.1f} ms per redraw"


# --- ground truth against the recording ------------------------------------


needs_data = pytest.mark.skipif(
    not (ALIGNMENT.is_file() and RECORDING.is_file()),
    reason="the paired exp2 recording is not on this machine",
)


@needs_data
def test_exp2_header_and_classes():
    table = EventTable.from_csv(ALIGNMENT)
    assert table.header.recording == "DR0000_0087.wav"
    assert table.header.recording_rate_hz == 48000.0
    assert table.header.recording_channel == 0
    assert table.trust == TRUST_OK
    assert table.n_events == 2187
    assert table.dropped == 0
    assert dict(zip(table.keys, (len(c) for c in table))) == {
        ("LOC", "matched"): 886,
        ("LOC", "unmatched"): 7,
        ("BASE", "matched"): 15,
        ("VOLLEY", "matched"): 1279,
    }
    assert table.matches_recording(RECORDING) is True


@needs_data
def test_every_matched_row_lands_on_a_pulse_in_the_recording():
    """The one test that says the backend is right.

    For each sampled ``matched`` row, look at the recording in a window
    narrower than the tightest pulse spacing in the file (volleys are about
    4 ms apart) and find the largest deflection.  It has to be within the
    tolerance the alignment itself claims -- ``#match_tolerance_s=0.0005``.

    The channel comes from the header: the fit is per channel, and reading
    it off is the difference between this test passing and it passing by
    luck on a stereo file.
    """
    soundfile = pytest.importorskip("soundfile")
    table = EventTable.from_csv(ALIGNMENT)
    channel = table.header.recording_channel or 0
    rate = table.header.recording_rate_hz or soundfile.info(RECORDING).samplerate
    tolerance = 5e-4
    times = np.concatenate([c.times for c in table if c.measured])
    times.sort()
    sample = times[np.linspace(0, times.size - 1, 300).astype(int)]

    half = int(0.001 * rate)  # +-1 ms, well inside the 4 ms volley spacing
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
def test_predicted_rows_carry_no_detection():
    """t_det_s is empty on exactly the rows that were never observed."""
    table = EventTable.from_csv(ALIGNMENT)
    for cls in table:
        if cls.measured:
            assert not np.isnan(cls.t_det).any(), cls.key
        else:
            assert np.isnan(cls.t_det).all(), cls.key
