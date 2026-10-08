"""The tracking range's pure half: times as text, clamping, and the frames
and samples a range run asks wavetracker for (design 4.2)."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from audian_plugins.eodsorter import trackrange as TR  # noqa: E402
from audian_plugins.eodsorter.model import FrameGrid  # noqa: E402

RATE = 8000.0
NFFT = 2048
OVERLAP = 0.9
STEP = 204


@pytest.mark.parametrize(
    "text, seconds",
    [
        ("00:04:30.0", 270.0),
        ("1:02:03.4", 3723.4),
        ("04:30", 270.0),
        ("90:00", 5400.0),
        ("12.5", 12.5),
        (" 0 ", 0.0),
        ("0:00:59.9", 59.9),
    ],
)
def test_parse_time(text, seconds):
    assert TR.parse_time(text) == pytest.approx(seconds)


@pytest.mark.parametrize(
    "text", ["", "abc", "1:2:3:4", "-5", "00:60", "1:60:00", "1.5:00", "nan", "inf"]
)
def test_parse_time_rejects(text):
    with pytest.raises(TR.RangeError):
        TR.parse_time(text)


def test_parse_stop_end_and_empty_are_the_end():
    assert TR.parse_stop("") is None
    assert TR.parse_stop(" End ") is None
    assert TR.parse_stop("01:00") == 60.0


def test_format_round_trips():
    assert TR.format_time(270.04) == "00:04:30.0"
    assert TR.format_time(3723.44) == "01:02:03.4"
    assert TR.format_time(59.96) == "00:01:00.0"
    assert TR.format_time(65.0, hours=False) == "01:05.0"
    assert TR.format_time(3725.0, hours=False) == "62:05.0"
    for t in (0.0, 12.3, 270.0, 3723.4, 7322.1):
        for hours in (True, False):
            assert TR.parse_time(TR.format_time(t, hours)) == pytest.approx(t)
    assert TR.format_clock(270.4) == "00:04:30"
    assert TR.format_clock(270.4, hours=False) == "04:30"


def test_range_label():
    assert TR.range_label(None) == "Track recording"
    r = TR.TrackRange(270.0, 3490.0)
    assert TR.range_label(r) == "Track 00:04:30–00:58:10"
    assert TR.range_label(r, duration=4000.0) == "Track 00:04:30–00:58:10"
    assert TR.range_label(r, duration=3500.0) == "Track 04:30–58:10"
    assert TR.range_label(TR.TrackRange(5.0, None), 20.0) == "Track 00:05–end"
    assert TR.needs_hours(None) and TR.needs_hours(3600.0)
    assert not TR.needs_hours(3599.0)


def test_clamp_range():
    assert TR.clamp_range(None, None, 100.0) is None
    assert TR.clamp_range(0.0, 100.0, 100.0) is None, "the whole recording"
    assert TR.clamp_range(0.0, 150.0, 100.0) is None
    assert TR.clamp_range(-3.0, 50.0, 100.0) == (0.0, 50.0)
    assert TR.clamp_range(10.0, 150.0, 100.0) == (10.0, None)
    assert TR.clamp_range(10.0, 20.0, None) == (10.0, 20.0)
    with pytest.raises(TR.RangeError, match="before"):
        TR.clamp_range(20.0, 10.0, 100.0)
    with pytest.raises(TR.RangeError, match="before"):
        TR.clamp_range(20.0, 20.0, 100.0)
    with pytest.raises(TR.RangeError, match="past the end"):
        TR.clamp_range(120.0, None, 100.0)


def whole(n_samples=int(RATE * 20)):
    return TR.recording_grid(RATE, n_samples, NFFT, OVERLAP)


def test_recording_grid_is_a_whole_run_and_follows_a_session():
    g = whole()
    assert g == FrameGrid.for_recording(RATE, int(RATE * 20), NFFT, OVERLAP)
    # a session that started at an odd sample: its frames, over everything
    session = FrameGrid(RATE, NFFT, STEP, 5 * STEP + 17, 40)
    s = TR.recording_grid(RATE, int(RATE * 20), 4096, 0.5, session)
    assert (s.nfft, s.step, s.s0) == (NFFT, STEP, 17)
    k = 5
    assert s.times()[k] == pytest.approx(session.times()[0])


def test_detect_window_is_on_the_whole_runs_frames():
    g = whole()
    r = TR.TrackRange(4.3, 12.71)
    w = TR.detect_window(g, r)
    assert (w.k0, w.k1) == g.frame_range(4.3, 12.71)
    t = g.times()
    assert t[w.k0] >= 4.3 > t[w.k0 - 1]
    assert t[w.k1 - 1] <= 12.71 < t[w.k1]
    # wavetracker's own arithmetic on start/duration (pipeline.detect)
    s0 = round(w.start * RATE)
    s1 = s0 + round(w.duration * RATE)
    assert s0 == w.k0 * STEP, "start is on a frame of the whole run"
    n = (s1 - s0 - NFFT) // STEP + 1
    assert n == w.k1 - w.k0
    times = (s0 + np.arange(n) * STEP + NFFT / 2) / RATE
    np.testing.assert_array_equal(times, t[w.k0 : w.k1])


def test_detect_window_to_the_end_and_whole():
    g = whole()
    w = TR.detect_window(g, TR.TrackRange(10.0, None))
    assert w.duration is None and w.k1 == g.n_frames
    w = TR.detect_window(g, None)
    assert (w.k0, w.k1, w.start, w.duration) == (0, g.n_frames, 0.0, None)


def test_detect_window_needs_two_frames():
    g = whole()
    t = g.times()
    with pytest.raises(TR.RangeError, match="two FFT windows"):
        TR.detect_window(g, TR.TrackRange(float(t[3]), float(t[3]) + 1e-4))
    w = TR.detect_window(g, TR.TrackRange(float(t[3]), float(t[4])))
    assert w.k1 - w.k0 == 2


def test_range_of_grid_maps_back_onto_the_same_frames():
    g = whole()
    w = TR.detect_window(g, TR.TrackRange(4.3, 12.71))
    results = FrameGrid(RATE, NFFT, STEP, w.k0 * STEP, w.k1 - w.k0)
    r = TR.range_of_grid(results, g)
    assert r is not None and r.stop is not None
    assert TR.detect_window(g, r)[:2] == (w.k0, w.k1)
    # results of a whole run have no range; one to the end has no stop
    assert TR.range_of_grid(g, g) is None
    tail = FrameGrid(RATE, NFFT, STEP, 30 * STEP, g.n_frames - 30)
    r = TR.range_of_grid(tail, g)
    assert r.stop is None and TR.detect_window(g, r)[:2] == (30, g.n_frames)
    head = FrameGrid(RATE, NFFT, STEP, 0, 100)
    r = TR.range_of_grid(head, g)
    assert r.start == 0.0 and TR.detect_window(g, r)[:2] == (0, 100)


def test_range_key_is_the_files_in_order(tmp_path):
    a, b = tmp_path / "a.wav", tmp_path / "b.wav"
    assert TR.range_key([str(a)]) == str(a)
    assert TR.range_key([a, b]) != TR.range_key([b, a])
    assert "\n" in TR.range_key([a, b])
