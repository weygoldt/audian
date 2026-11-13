"""The spectrogram's window, offered and stored as a resolution in Hz.

Runs offscreen::

    QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_window_resolution.py -q

The Window combo used to list powers of two in samples and the settings file
kept the window in samples, so a window chosen on a 20 kHz recording opened a
48 kHz one at bins 2.4 times as wide, and "press Shift+R five times" meant a
different picture on every recording.  Now the combo lists resolutions that
halve (`fftwindow.LADDER`), the window at any rate is the fast FFT size
closest to ``rate / resolution``, and the file holds the resolution.

Every expectation here is derived independently of `audian.fftwindow`: the
fast FFT sizes by a brute-force scan, the ladder as ``20000 / 2**i``.  The
browser tests do not import `fftwindow` at all, so that run against the
code before this change they fail on what the bar shows, not on an import.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "tests"))

from test_panelsplitter import open_stack, pump, settle  # noqa: E402

#: the resolutions the bar offers, coarsest first: 2**3 .. 2**19 samples at
#: 20 kHz
LADDER = [20000.0 / 2**i for i in range(3, 20)]


def five_smooth(n: int) -> bool:
    for p in (2, 3, 5):
        while n % p == 0:
            n //= p
    return n == 1


def fast_sizes_near(target: float) -> set[int]:
    """Every even 5-smooth number at the smallest distance from `target`,
    found by scanning, not by the search under test."""
    best, found = math.inf, set()
    for n in range(2, int(2 * target) + 4, 2):
        if not five_smooth(n):
            continue
        d = abs(n - target)
        if d < best - 1e-9:
            best, found = d, {n}
        elif abs(d - best) <= 1e-9:
            found.add(n)
    return found


def hz_text(hz: float) -> str:
    return f"{0.001 * hz:.3g} kHz" if hz >= 1000 else f"{hz:.3g} Hz"


def seconds_text(seconds: float) -> str:
    return f"{seconds:.3g} s" if seconds >= 1 else f"{1000 * seconds:.3g} ms"


def stack(app, directory, rate, seconds, settings=None):
    """A one-channel browser at `rate`, its settings file seeded first."""
    directory.mkdir(parents=True, exist_ok=True)
    if settings is not None:
        (directory / "settings.json").write_text(json.dumps({"spectrogram": settings}))
    frames = int(rate * seconds)
    signal = (0.1 * np.sin(2 * np.pi * 440.0 * np.arange(frames) / rate)).astype(
        np.float32
    )[:, None]
    return open_stack(app, directory, 1, signal, rate)


def stored(directory) -> dict:
    return json.loads((directory / "settings.json").read_text())["spectrogram"]


def choose(view, index: int) -> int:
    """Pick combo entry `index` the way a click does; the window it set."""
    view.nfftw.setCurrentIndex(index)
    view.apply_resolution()  # the debounce timer, without the 200 ms wait
    settle()
    return int(view.data["spectrogram"].nfft)


def press(view, action: str, times: int) -> None:
    """Trigger audian's own action, as its shortcut does, and let the burst
    settle into one recompute."""
    for _ in range(times):
        getattr(view.window().acts, action).trigger()
        settle()
    pump(0.5)


# --------------------------------------------------------------- the list


def test_at_20_khz_the_bar_offers_the_windows_it_always_did(app, tmp_path):
    """The same seventeen windows, 8 .. 524288 samples, now named by their
    resolution.  A recording of 2**20 frames, so that none is too long."""
    rate = 20000
    gen = stack(app, tmp_path / "w20", rate, (2**20 + 2000) / rate)
    view = next(gen)
    try:
        texts = [view.nfftw.itemText(i) for i in range(view.nfftw.count())]
        want = [
            f"{hz_text(rate / 2**i)} · {seconds_text(2**i / rate)}"
            for i in range(3, 20)
        ]
        assert texts == want
        data = [view.nfftw.itemData(i) for i in range(view.nfftw.count())]
        assert data == pytest.approx(LADDER)
        # and the entry named 0.61 Hz is the 2**15 window it always was
        k = texts.index("0.61 Hz · 1.64 s")
        assert choose(view, k) == 2**15
    finally:
        next(gen, None)


@pytest.mark.parametrize("rate", [44100, 48000])
def test_at_other_rates_each_entry_is_the_fast_window_of_its_resolution(
    app, tmp_path, rate
):
    """Every entry a resolution of the ladder, every window the even
    5-smooth size closest to ``rate / resolution``, and every resolution
    offered whose window fits half of the 4 s recording."""
    seconds = 4.0
    gen = stack(app, tmp_path / f"w{rate}", rate, seconds)
    view = next(gen)
    try:
        frames = int(rate * seconds)
        offered = [view.nfftw.itemData(i) for i in range(view.nfftw.count())]
        expected = []
        for resolution in LADDER:
            sizes = fast_sizes_near(rate / resolution)
            if 8 <= min(sizes) and max(sizes) <= frames // 2:
                expected.append(resolution)
        assert expected, "the fixture has to offer something to check"
        assert offered == pytest.approx(expected)
        for k, resolution in enumerate(offered):
            nfft = choose(view, k)
            assert nfft % 2 == 0 and five_smooth(nfft), nfft
            assert nfft in fast_sizes_near(rate / resolution), (resolution, nfft)
            # the bin width is the resolution within that rounding: one
            # 5-smooth step, a few per cent here
            assert rate / nfft == pytest.approx(resolution, rel=0.05)
            assert f"{nfft} samples" in view.nfftw.toolTip()
    finally:
        next(gen, None)


@pytest.mark.parametrize("rate", [20000, 48000])
def test_five_shift_r_from_the_default_is_the_same_resolution_at_any_rate(
    app, tmp_path, rate
):
    """What the quickstart tells a reader to do now means one picture.

    The default is 78 Hz, 12.8 ms at any rate (256 samples at 20 kHz, 600 at
    48 kHz); five Shift+R make it 2.44 Hz, 0.41 s."""
    gen = stack(app, tmp_path / f"r{rate}", rate, 4.0)
    view = next(gen)
    try:
        spectrogram = view.data["spectrogram"]
        assert spectrogram.nfft in fast_sizes_near(rate / 78.125)
        assert view.nfftw.currentText().startswith("78.1 Hz · ")
        press(view, "frequency_resolution_up", 5)
        assert spectrogram.nfft in fast_sizes_near(rate / (20000 / 2**13))
        assert view.nfftw.currentText().startswith("2.44 Hz · ")
        # and R takes it back one step
        press(view, "frequency_resolution_down", 1)
        assert view.nfftw.currentText().startswith("4.88 Hz · ")
    finally:
        next(gen, None)


# --------------------------------------------------------- the preference


def test_an_old_nfft_is_converted_once_and_left_in_the_file(app, tmp_path):
    """4096 samples from an older audian, first read at 48 kHz: 11.7 Hz
    there, snapped to the 9.77 Hz entry (4860 samples).  The file then
    holds that resolution, and the next recording -- at 20 kHz -- opens at
    it (2048 samples), not at 4096 converted again.  "nfft" stays 4096 for
    an older audian to read."""
    directory = tmp_path / "old"
    gen = stack(app, directory, 48000, 4.0, {"version": 1, "nfft": 4096})
    view = next(gen)
    try:
        assert view.data["spectrogram"].nfft in fast_sizes_near(48000 / 9.765625)
        pump(0.3)  # the queued write
        saved = stored(directory)
        assert saved["resolution"] == pytest.approx(20000 / 2**11)
        assert saved["nfft"] == 4096
    finally:
        next(gen, None)
    gen = stack(app, directory, 20000, 4.0)
    view = next(gen)
    try:
        assert view.data["spectrogram"].nfft == 2048
        assert view.nfftw.currentText().startswith("9.77 Hz · ")
        assert stored(directory)["nfft"] == 4096
    finally:
        next(gen, None)


def test_a_stored_resolution_wins_and_a_gesture_leaves_the_old_key(app, tmp_path):
    """The resolution is read, the old window ignored; R stores the new
    resolution and never touches "nfft"."""
    directory = tmp_path / "both"
    gen = stack(
        app,
        directory,
        48000,
        4.0,
        {"version": 1, "resolution": 20000 / 2**15, "nfft": 256, "overlap": 0.5},
    )
    view = next(gen)
    try:
        spectrogram = view.data["spectrogram"]
        assert view.nfftw.currentText().startswith("0.61 Hz · ")
        assert spectrogram.nfft in fast_sizes_near(48000 / (20000 / 2**15))
        press(view, "frequency_resolution_down", 1)
        assert spectrogram.nfft in fast_sizes_near(48000 / (20000 / 2**14))
        pump(0.3)
        saved = stored(directory)
        assert saved["resolution"] == pytest.approx(20000 / 2**14)
        assert saved["nfft"] == 256
        assert saved["overlap"] == pytest.approx(0.5)
    finally:
        next(gen, None)


@pytest.mark.parametrize("junk", ["fast", -1.0, True, math.inf])
def test_a_hand_edited_resolution_opens_at_the_default(app, tmp_path, junk):
    """A file a reader may edit: anything but a positive number is ignored,
    and 48 kHz opens at the default, 78 Hz (600 samples, 12.5 ms)."""
    gen = stack(app, tmp_path / "junk", 48000, 4.0, {"version": 1, "resolution": junk})
    view = next(gen)
    try:
        assert view.data["spectrogram"].nfft in fast_sizes_near(48000 / 78.125)
        assert view.nfftw.currentText().startswith("78.1 Hz · ")
    finally:
        next(gen, None)


# -------------------------------------------------------- the pure module


def test_the_fast_size_search_matches_a_scan():
    from audian import fftwindow

    for target in np.linspace(3.0, 5000.0, 400):
        assert fftwindow.fast_fft_size(target) in fast_sizes_near(target), target


def test_every_window_offered_is_on_the_ladder_and_fits():
    """One source for the combo and the restore: a snapped preference is
    always an entry, so the picture and the bar cannot disagree."""
    from audian import fftwindow

    assert list(fftwindow.LADDER) == pytest.approx(LADDER)
    assert fftwindow.DEFAULT == pytest.approx(78.125)
    for value in (1e-6, 0.05, 0.6, 3.0, 100.0, 1e5):
        assert fftwindow.snap(value) in fftwindow.LADDER
    for rate in (8000, 20000, 44100, 96000, 250000):
        for resolution, nfft in fftwindow.windows(rate, 10**9):
            assert resolution in fftwindow.LADDER
            assert fftwindow.NFFT_MIN <= nfft <= fftwindow.NFFT_MAX
    assert [n for _, n in fftwindow.windows(20000)] == [2**i for i in range(3, 20)]
