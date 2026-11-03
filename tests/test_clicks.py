"""Click removal: what it repairs, what it leaves, and that it is stable.

`audian.clicks` is a numpy port of `wavetracker.clicks`.  Against the
original, on the same 60 s blocks of three Iriri field recordings (static,
crackle, a night with fish; 16 channels, 48 kHz), the samples the two repair
overlap at an IoU of 0.998 to 1.000.  These tests pin the behaviour the
port has to keep on its own, on synthetic recordings where the truth is
known:

* a click on a loud tone is repaired without cutting a hole in the tone,
* a tone that only starts after a click is not written into the hole,
* a long loud event is a signal and stays,
* a sample outside a click, and a channel without one, is not touched, and
* a block comes out the same whatever buffer it was computed in -- the
  property that lets the filter repair a buffer in pieces.

Each number asserted against was measured on these signals first; the
margins are a factor of three or more beyond it.
"""

from __future__ import annotations

from math import floor

import numpy as np
import pytest
from scipy.signal import butter, sosfiltfilt

from audian.bufferedfilter import BufferedFilter
from audian.clicks import ClickRemoval, clean_blocks, remove_clicks
from audian.tasks.tokens import Cancelled, CancelToken

RATE = 20000.0
NOISE = 1e-3
TONE = 0.2
TONE_HZ = 700.0


def click(n, at, amplitude=1.0, tau=0.0002):
    """An impulse with a 0.2 ms decay: a thousand times the noise."""
    k = np.arange(n) - at
    return np.where(
        k >= 0, amplitude * np.exp(-np.clip(k, 0, None) / (tau * RATE)), 0.0
    )


def noise(n, seed=3):
    return np.random.default_rng(seed).normal(0.0, NOISE, n)


def tone(n, start=0.0):
    t = np.arange(n) / RATE
    return np.where(t >= start, TONE * np.sin(2 * np.pi * TONE_HZ * t), 0.0)


N = int(6 * RATE)
CLICKS = (int(0.9 * RATE), int(2.3 * RATE), int(4.7 * RATE))


def holes(n, at):
    """The few milliseconds around each click: where a repair can go wrong."""
    hole = np.zeros(n, dtype=bool)
    for a in at:
        hole[a - int(0.003 * RATE) : a + int(0.006 * RATE)] = True
    return hole


class TestRepair:
    def test_every_click_is_found(self):
        x = noise(N) + tone(N) + sum(click(N, a) for a in CLICKS)
        _, mask = remove_clicks(x, RATE, ClickRemoval())
        assert all(mask[a] for a in CLICKS)

    def test_a_click_on_a_loud_tone_is_repaired_and_the_tone_kept(self):
        """Measured: the error in the holes drops from 1.0 to 0.012, and in
        the tone's own band from 0.035 to 0.0032.

        Bridging the raw samples instead -- what removing a click without
        setting the tone aside amounts to -- leaves a hole in the tone as
        deep as the tone, 0.2.
        """
        truth = noise(N) + tone(N)
        x = truth + sum(click(N, a) for a in CLICKS)
        out, _ = remove_clicks(x, RATE, ClickRemoval())
        hole = holes(N, CLICKS)
        assert np.abs(x - truth)[hole].max() > 0.9
        assert np.abs(out - truth)[hole].max() < 0.05
        sos = butter(4, [TONE_HZ - 50, TONE_HZ + 50], "bandpass", fs=RATE, output="sos")
        band = np.abs(sosfiltfilt(sos, out) - sosfiltfilt(sos, truth))[hole]
        assert band.max() < 0.01

    def test_a_tone_that_starts_later_is_not_written_into_the_hole(self):
        """The tonal part of a block is spread over the whole block by the
        transform; scaled to what the samples around a hole hold of it, it
        is nothing before the tone starts.  Measured: 0.0012 in the hole,
        where the noise is 0.001 and the unscaled tone would be 0.2.
        """
        at = int(2.5 * RATE)
        x = noise(N) + tone(N, start=3.0) + click(N, at)
        out, mask = remove_clicks(x, RATE, ClickRemoval())
        assert mask[at]
        hole = mask & (np.arange(N) < int(2.6 * RATE))
        assert np.abs(out[hole]).max() < 5 * NOISE

    def test_a_long_loud_event_is_a_signal_and_stays(self):
        t = np.arange(N) / RATE
        burst = np.where((t > 1.0) & (t < 2.0), 0.05, 0.0)
        x = noise(N) + burst * np.random.default_rng(5).normal(0.0, 1.0, N)
        out, mask = remove_clicks(x, RATE, ClickRemoval())
        assert not mask.any()
        assert out is x or np.array_equal(out, x)

    def test_nothing_outside_a_click_changes(self):
        x = noise(N) + tone(N) + sum(click(N, a) for a in CLICKS)
        out, mask = remove_clicks(x, RATE, ClickRemoval())
        assert 0 < mask.sum() < 0.01 * N
        assert np.array_equal(out[~mask], x[~mask])

    def test_a_channel_without_clicks_is_returned_as_it_is(self):
        x = noise(N) + tone(N)
        out, mask = remove_clicks(x, RATE, ClickRemoval())
        assert not mask.any()
        assert np.array_equal(out, x)

    def test_digital_silence_stays_silent(self):
        """The high band of a silent stretch is round-off of the transform,
        not noise, and must not be read as anything."""
        x = noise(N)
        x[: N // 2] = 0.0
        x += click(N, int(4 * RATE))
        out, mask = remove_clicks(x, RATE, ClickRemoval())
        assert mask[int(4 * RATE)]
        assert np.all(out[: N // 2] == 0.0)

    def test_a_higher_threshold_leaves_a_weaker_click(self):
        """Measured on this signal: the 0.05 click reaches 22.8 robust
        standard deviations of the high band it is searched in, so 12 finds
        it and 24 or 50 do not."""
        x = noise(N) + click(N, int(3 * RATE), amplitude=0.05)
        _, low = remove_clicks(x, RATE, ClickRemoval(threshold=12, extent_threshold=8))
        _, high = remove_clicks(
            x, RATE, ClickRemoval(threshold=50, extent_threshold=50 * 8 / 12)
        )
        assert low[int(3 * RATE)]
        assert not high.any()


def channels(n=N):
    """Three channels: clicks on a tone, clicks before a tone, plain noise.

    `n` frames, with the clicks of the first `N` repeated every `N`.
    """
    at = [a + k for k in range(0, n, N) for a in CLICKS if a + k < n]
    late = [int(2.5 * RATE) + k for k in range(0, n, N)]
    return np.stack(
        [
            noise(n, 1) + tone(n) + sum(click(n, a) for a in at),
            noise(n, 2) + tone(n, start=3.0) + sum(click(n, a) for a in late),
            noise(n, 4),
        ],
        axis=1,
    )


class TestBlocks:
    def test_a_slice_gives_the_same_samples_as_the_whole(self):
        """Blocks are fixed in absolute time and computed from themselves
        and a margin, so a frame comes out the same in a buffer that starts
        anywhere -- bit for bit, as long as the buffer reaches a block and a
        margin beyond it.  That is what `BufferedFilter.click_reserve`
        claims of the raw buffer.  Measured: within that, `array_equal`;
        nearer the ends of a slice, up to 0.05 apart.

        Twelve seconds, because the reserve is 2.5 s on each side: the
        first version of this test took slices shorter than twice that,
        compared two empty arrays, and passed with the blocks laid out from
        the start of the slice.
        """
        x = channels(2 * N)
        cfg = ClickRemoval()
        whole = np.concatenate([b for _, _, b in clean_blocks(x, 0, RATE, cfg)])
        reserve = cfg.block_frames(RATE) + cfg.margin_frames(RATE)
        for a, b in ((13_579, 201_234), (40_000, 240_000), (1, 177_777)):
            part = np.concatenate(
                [blk for _, _, blk in clean_blocks(x[a:b], a, RATE, cfg)]
            )
            inner = slice(reserve, (b - a) - reserve)
            repaired = whole[a:b][inner] != x[a:b][inner]
            assert repaired.sum() > 100, "no click inside what is compared"
            assert np.array_equal(part[inner], whole[a:b][inner]), (a, b)

    def test_the_pieces_cover_what_was_asked_for_in_order(self):
        x = channels()
        pieces = list(clean_blocks(x, 7_000, RATE, ClickRemoval(), lo=12_345))
        assert pieces[0][0] == 12_345
        assert pieces[-1][1] == len(x)
        for (_, j, _), (i, _, _) in zip(pieces, pieces[1:]):
            assert i == j
        for i, j, block in pieces:
            assert block.shape == (j - i, x.shape[1])

    def test_a_channel_without_clicks_comes_out_bit_for_bit(self):
        x = channels()
        out = np.concatenate(
            [b for _, _, b in clean_blocks(x, 0, RATE, ClickRemoval())]
        )
        assert np.array_equal(out[:, 2], x[:, 2])
        assert not np.array_equal(out[:, 0], x[:, 0])

    def test_threads_give_the_same_samples_as_one(self):
        from concurrent.futures import ThreadPoolExecutor

        x = channels()
        cfg = ClickRemoval()
        one = np.concatenate([b for _, _, b in clean_blocks(x, 0, RATE, cfg)])
        with ThreadPoolExecutor(3) as pool:
            three = np.concatenate(
                [b for _, _, b in clean_blocks(x, 0, RATE, cfg, pool=pool, batch=3)]
            )
        assert np.array_equal(one, three)


class FakeSource:
    """The surface of a loader that `BufferedData.open()` touches."""

    def __init__(self, signal):
        self.rate = RATE
        self.channels = signal.shape[1]
        self.frames = len(signal)
        self.offset = 0
        self.bufferframes = len(signal)
        self.backframes = 0
        self.ampl_min = -1.0
        self.ampl_max = 1.0
        self.unit = "V"
        self.dests = []
        self.buffer = signal


def filter_on(signal, clicks=True):
    filt = BufferedFilter()
    filt.set_click_removal(clicks)
    filt.open(FakeSource(signal))
    return filt


def load(filt, d0, d1):
    """What `load_buffer` would put in `[d0, d1)`, through `source_window`."""
    i0, i1, nbefore = filt.source_window(d0, d1 - d0, 0, len(filt.source.buffer))
    dest = np.empty((d1 - d0, filt.channels), dtype=filt.dtype)
    filt.process(filt.source.buffer[i0:i1], dest, nbefore, start=i0)
    return dest


class TestFilter:
    def test_the_filtered_trace_is_repaired(self):
        signal = channels()
        filt = filter_on(signal)
        dest = load(filt, 0, N)
        hole = holes(N, CLICKS)
        assert np.abs(dest[hole, 0]).max() < 0.25
        assert np.abs(signal[hole, 0]).max() > 0.9

    def test_two_loads_of_one_stretch_agree_bit_for_bit(self):
        """A buffer moved by a scroll is loaded in pieces, each from its own
        window of the raw buffer.  Without a band filter -- whose warm-up is
        another matter, and was never exact -- the click repair of a frame
        must not depend on which piece it was in."""
        filt = filter_on(channels())
        whole = load(filt, int(1.0 * RATE), int(5.0 * RATE))
        part = load(filt, int(2.7 * RATE), int(5.0 * RATE))
        assert np.array_equal(part, whole[int(1.7 * RATE) :])

    def test_off_the_reserve_does_not_change_the_filter(self):
        """Removing clicks claims more raw buffer, and that longer warm-up
        must not reach the band filter: it starts `warmup_time` before the
        buffer, exactly as it did before click removal existed.

        A high-pass at 0.5 Hz, because one at 300 Hz settles within
        milliseconds and gives the same float32 samples after any warm-up
        longer than that -- the first version of this test passed with the
        warm-up running over all of the reserve.
        """
        signal = channels()
        filt = filter_on(signal, clicks=False)
        filt.sos = butter(2, 0.5, "highpass", fs=RATE, output="sos")
        nbefore = int(3.0 * RATE)
        dest = np.empty((N - nbefore, signal.shape[1]), dtype=filt.dtype)
        filt.process(signal, dest, nbefore)
        warm = floor(BufferedFilter.warmup_time * RATE)
        from scipy.signal import sosfilt

        reference = sosfilt(filt.sos, signal[nbefore - warm :], axis=0)[warm:]
        assert np.array_equal(dest, reference.astype(filt.dtype))

    def test_the_band_filter_runs_on_the_repaired_samples(self):
        """With a band filter too, the clicks must be gone from what it
        passes: repaired first, filtered after."""
        signal = channels()
        filt = filter_on(signal)
        filt.sos = butter(2, 2000.0, "lowpass", fs=RATE, output="sos")
        clean = load(filt, 0, N)
        filt.set_click_removal(False)
        dirty = load(filt, 0, N)
        hole = holes(N, CLICKS)
        assert np.abs(dirty[hole, 0]).max() > 0.5
        assert np.abs(clean[hole, 0]).max() < 0.25

    def test_the_margins_grow_only_while_clicks_are_removed(self):
        filt = BufferedFilter()
        assert (filt.source_tbefore, filt.source_tafter) == (
            BufferedFilter.warmup_time,
            0,
        )
        filt.set_click_removal(True)
        reserve = BufferedFilter.click_reserve()
        cfg = ClickRemoval()
        assert reserve >= cfg.block + cfg.margin
        assert filt.source_tbefore == pytest.approx(
            BufferedFilter.warmup_time + reserve
        )
        assert filt.source_tafter == pytest.approx(reserve)
        filt.set_click_removal(False)
        assert (filt.source_tbefore, filt.source_tafter) == (
            BufferedFilter.warmup_time,
            0.0,
        )

    def test_a_cancelled_click_removal_stops_inside_the_buffer(self):
        filt = filter_on(channels())
        token = CancelToken()
        seen = []

        def progress(fraction):
            seen.append(fraction)
            token.cancel()

        dest = np.empty((N, 3), dtype=filt.dtype)
        with pytest.raises(Cancelled):
            filt.process(filt.source.buffer, dest, 0, token, progress, start=0)
        assert seen and seen[0] < 0.5


class TestBufferStart:
    """The raw buffer rarely starts at frame 0 of the recording.

    Both ways a filtered buffer is computed -- `load_buffer` on the GUI
    thread after a scroll, and `run_job` on the worker after a change --
    must hand the filter the frame of the recording its slice starts at.
    Without it the blocks are laid out from the start of the raw buffer,
    and the same frame is repaired differently from one scroll position to
    the next.
    """

    D0, D1 = int(3.1 * RATE), int(5.4 * RATE)

    def reference(self, signal):
        filt = filter_on(signal)
        return load(filt, self.D0, self.D1)

    def shifted(self, signal, at):
        """A filter whose raw buffer holds the recording from frame `at`."""
        filt = BufferedFilter()
        filt.set_click_removal(True)
        source = FakeSource(signal[at:])
        source.frames = len(signal)
        source.offset = at
        filt.open(source)
        return filt

    def test_load_buffer_places_the_blocks_in_the_recording(self):
        signal = channels()
        reference = self.reference(signal)
        filt = self.shifted(signal, int(0.37 * RATE))
        dest = np.empty((self.D1 - self.D0, signal.shape[1]), dtype=filt.dtype)
        filt.load_buffer(self.D0, self.D1 - self.D0, dest)
        assert np.array_equal(dest, reference)

    def test_the_worker_places_the_blocks_in_the_recording(self):
        from audian.tasks.compute import ComputeJob, TraceStep, run_job

        signal = channels()
        reference = self.reference(signal)
        filt = self.shifted(signal, int(0.37 * RATE))
        step = TraceStep(
            trace=filt,
            offset=self.D0,
            shape=(self.D1 - self.D0, signal.shape[1]),
            dtype=filt.dtype,
            source_buffer=filt.source.buffer,
            source_offset=filt.source.offset,
        )
        result = run_job(ComputeJob(1, CancelToken(), (step,)))
        assert result.error is None, result.error
        assert np.array_equal(result.updates[0].buffer, reference)
