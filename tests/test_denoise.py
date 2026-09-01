"""Spectrogram denoising: the two gates, and the property that lets them chunk."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from audian import denoise  # noqa: E402
from audian_plugins.denoisers import audian_builtin_denoisers  # noqa: E402
from audian_plugins.denoisers.engine import (  # noqa: E402
    mains_comb,
    spatial_coherence,
)


@pytest.fixture(autouse=True)
def registered():
    """The bundled denoisers, in the registry.

    Registered here rather than left to plugin discovery: these tests are
    about the two denoisers and not about whether the loader found them,
    and `register` replaces by key, so doing it again costs nothing when
    the panel tests have already loaded the plugins for real.
    """
    for entry in audian_builtin_denoisers():
        denoise.register(entry)


# ---------------------------------------------------------------- spatial


def spatial_block(patterns, frames=4, freqs=3):
    """A ``(time, channel, frequency)`` block whose every bin has `patterns`.

    `patterns` is the per-channel power of one bin; it is broadcast over
    time and frequency so a test can talk about one spatial pattern at a
    time.
    """
    p = np.asarray(patterns, dtype=float)
    return np.tile(p[None, :, None], (frames, 1, freqs))


def kept(out, source):
    """Fraction of power the gate let through."""
    return float(out.sum()/source.sum())


def spatial(block, threshold=6.0, softness=3.0):
    freqs = np.arange(block.shape[2], dtype=float)
    return spatial_coherence(
        block, freqs, {"threshold": threshold, "softness": softness}
    )


class TestSpatialCoherence:
    def test_common_mode_is_suppressed(self):
        """Equal power on every electrode is pickup, not a fish."""
        b = spatial_block([1.0, 1.0, 1.0, 1.0])
        assert kept(spatial(b), b) < 0.15

    def test_localised_source_is_kept(self):
        b = spatial_block([1.0, 0.3, 0.05, 0.002])
        assert kept(spatial(b), b) > 0.95

    def test_the_weakest_synthetic_fish_clears_the_default(self):
        """9.8x spread is the weakest fish in wavefish_4ch_hard.wav.

        The 6 dB default exists because that fish is at 9.9 dB and the hum
        is at 0 dB.  If a change to the gate stops separating those two,
        the default is no longer defensible and this fails.
        """
        entry = denoise.denoiser("spatial")
        fish = spatial_block([1.0, 0.31, 0.69, 0.102])       # 9.8x max/min
        hum = spatial_block([1.0, 1.0, 1.0, 1.0])
        freqs = np.arange(fish.shape[2], dtype=float)
        f = entry.apply(fish, freqs, entry.defaults())
        h = entry.apply(hum, freqs, entry.defaults())
        assert kept(f, fish) > 0.7
        assert kept(h, hum) < 0.15

    def test_dead_electrode_does_not_defeat_the_gate(self):
        """A channel recorded as exactly zero must not read as contrast.

        Four of the sixteen channels of the flona block are exactly zero.
        Taken naively, ``max/min`` is then infinite in every bin and the
        gate passes everything -- the denoiser would silently do nothing on
        precisely the recordings it was written for.
        """
        b = spatial_block([1.0, 1.0, 1.0, 0.0])
        assert kept(spatial(b), b) < 0.2

    def test_all_channels_dead_is_not_a_crash(self):
        b = spatial_block([0.0, 0.0, 0.0, 0.0])
        assert np.all(np.isfinite(spatial(b)))

    def test_mono_is_returned_unchanged(self):
        b = spatial_block([1.0])
        assert spatial(b) is b

    def test_source_block_is_not_modified(self):
        b = spatial_block([1.0, 1.0, 1.0, 1.0])
        before = b.copy()
        spatial(b)
        assert np.array_equal(b, before)

    def test_threshold_moves_the_gate(self):
        b = spatial_block([1.0, 0.1, 0.1, 0.1])              # 10 dB of spread
        assert kept(spatial(b, threshold=0.0), b) > kept(
            spatial(b, threshold=30.0), b
        )

    def test_zero_softness_is_a_hard_cut(self):
        b = spatial_block([1.0, 0.1, 0.1, 0.1])              # 10 dB
        assert kept(spatial(b, threshold=20.0, softness=0.0), b) == pytest.approx(0.0)
        assert kept(spatial(b, threshold=5.0, softness=0.0), b) == pytest.approx(1.0)


# ------------------------------------------------------------------ mains

FREQS = np.arange(0.0, 500.0, 0.5)
FLOOR = 1.0
NEEDLE = 100.0


def comb_block(needle_hz, frames=6, channels=2, width=0.5):
    """A flat floor with a needle at each of `needle_hz`."""
    b = np.full((frames, channels, len(FREQS)), FLOOR)
    for hz in needle_hz:
        b[:, :, np.abs(FREQS - hz) <= width] = NEEDLE
    return b


def mains(block, **values):
    entry = denoise.denoiser("mains")
    v = dict(entry.defaults())
    v.update(values)
    return entry.apply(block, FREQS, v)


def at(block, hz, width=0.5):
    return float(block[:, :, np.abs(FREQS - hz) <= width].mean())


class TestMainsComb:
    def test_the_fundamental_and_its_harmonics_come_down(self):
        b = comb_block([50, 100, 150, 200])
        out = mains(b, frequency=50.0, harmonics=4)
        for hz in (50, 100, 150, 200):
            assert at(out, hz) == pytest.approx(FLOOR, abs=0.01), hz

    def test_the_floor_between_harmonics_is_untouched(self):
        b = comb_block([50, 100])
        out = mains(b, frequency=50.0, harmonics=2)
        assert at(out, 75) == pytest.approx(FLOOR)
        assert at(out, 123) == pytest.approx(FLOOR)

    def test_sixty_hertz_is_a_different_comb(self):
        """The reason `frequency` is a parameter: Europe is 50, the
        Americas 60, and pointing the comb at the wrong one both leaves the
        interference and damages a clean band."""
        b = comb_block([60, 120, 180])
        out = mains(b, frequency=60.0, harmonics=3)
        for hz in (60, 120, 180):
            assert at(out, hz) == pytest.approx(FLOOR, abs=0.01), hz

    def test_a_fifty_hertz_comb_leaves_a_sixty_hertz_needle_alone(self):
        b = comb_block([60])
        out = mains(b, frequency=50.0, harmonics=4)
        assert at(out, 60) == pytest.approx(NEEDLE)

    def test_a_broadband_transient_crossing_the_comb_survives(self):
        """The property that matters for pulse-type fish.

        An eel discharge is broadband: at the instant of a pulse the 50 Hz
        bin holds pulse energy at much the same level as its neighbours, so
        the floor measured beside it is just as high and nothing is taken
        away.  A hard notch would punch a hole through every pulse at every
        harmonic, which is the failure this design exists to avoid.
        """
        b = comb_block([50, 100, 150])
        pulse = 4                                   # one column, every bin
        b[pulse, :, :] = NEEDLE
        out = mains(b, frequency=50.0, harmonics=3)
        assert out[pulse].mean() == pytest.approx(NEEDLE, rel=1e-6)
        # ... while the steady hum in the other columns still goes
        steady = [t for t in range(b.shape[0]) if t != pulse]
        assert out[steady][:, :, np.abs(FREQS - 50) <= 0.5].mean() == pytest.approx(
            FLOOR, abs=0.01
        )

    def test_strength_scales_what_is_removed(self):
        b = comb_block([50])
        half = mains(b, frequency=50.0, harmonics=1, strength=50.0)
        full = mains(b, frequency=50.0, harmonics=1, strength=100.0)
        assert at(half, 50) == pytest.approx(FLOOR + 0.5*(NEEDLE - FLOOR), rel=1e-6)
        assert at(full, 50) == pytest.approx(FLOOR, abs=0.01)

    def test_zero_strength_is_a_no_op(self):
        b = comb_block([50])
        assert mains(b, strength=0.0) is b

    def test_harmonics_beyond_the_count_are_left_alone(self):
        b = comb_block([50, 100, 150])
        out = mains(b, frequency=50.0, harmonics=2)
        assert at(out, 50) == pytest.approx(FLOOR, abs=0.01)
        assert at(out, 100) == pytest.approx(FLOOR, abs=0.01)
        assert at(out, 150) == pytest.approx(NEEDLE)

    def test_harmonics_past_nyquist_do_not_raise(self):
        b = comb_block([50])
        out = mains(b, frequency=50.0, harmonics=200)
        assert np.all(np.isfinite(out))

    def test_a_notch_narrower_than_the_hum_removes_nothing(self):
        """`width` has to cover the interference, not just its centre.

        The floor is measured from a band just outside the notch.  If the
        hum is wider than the notch, that band lands on the hum, the floor
        comes back as high as the needle, and the excess is zero -- so a
        too-narrow width is a no-op rather than a partial cut.  Refusing to
        act on a floor it cannot see beats inventing one, but it does mean
        this is the first knob to widen when hum survives the filter.
        """
        b = comb_block([50], width=4.0)                 # hum spans 46-54 Hz
        narrow = mains(b, frequency=50.0, harmonics=1, width=1.0)
        assert at(narrow, 50, width=1.0) == pytest.approx(NEEDLE)

    def test_widening_the_notch_past_the_hum_removes_it(self):
        b = comb_block([50], width=4.0)
        wide = mains(b, frequency=50.0, harmonics=1, width=5.0)
        assert at(wide, 50, width=1.0) == pytest.approx(FLOOR, abs=0.01)

    def test_width_does_not_reach_beyond_its_own_notch(self):
        b = comb_block([50, 80])
        out = mains(b, frequency=50.0, harmonics=1, width=1.0)
        assert at(out, 80) == pytest.approx(NEEDLE)

    def test_source_block_is_not_modified(self):
        b = comb_block([50])
        before = b.copy()
        mains(b)
        assert np.array_equal(b, before)

    def test_per_channel_floors_are_independent(self):
        """A harmonic loud on one electrode and absent on another must be
        treated separately on each, which is what a real array does."""
        b = np.full((3, 2, len(FREQS)), FLOOR)
        b[:, 0, np.abs(FREQS - 50) <= 0.5] = NEEDLE      # only channel 0
        out = mains(b, frequency=50.0, harmonics=1)
        assert out[:, 0][:, np.abs(FREQS - 50) <= 0.5].mean() == pytest.approx(
            FLOOR, abs=0.01
        )
        assert out[:, 1][:, np.abs(FREQS - 50) <= 0.5].mean() == pytest.approx(FLOOR)


# ------------------------------------------------------------------ chain


class TestChain:
    def test_nothing_enabled_returns_the_block_itself(self):
        b = comb_block([50])
        assert denoise.apply_chain(b, FREQS, (), denoise.defaults()) is b

    def test_both_layers_run(self):
        b = comb_block([50])
        b[:, 1, :] *= 1.0                       # identical channels: common mode
        out = denoise.apply_chain(
            b, FREQS, ("mains", "spatial"), denoise.defaults()
        )
        # mains takes the needle down, spatial then gates the whole
        # common-mode picture
        assert out.sum() < 0.2*b.sum()

    def test_order_is_the_registry_order_not_the_tick_order(self):
        b = comb_block([50])
        params = denoise.defaults()
        one = denoise.apply_chain(b, FREQS, ("spatial", "mains"), params)
        two = denoise.apply_chain(b, FREQS, ("mains", "spatial"), params)
        assert np.array_equal(one, two)

    def test_unknown_keys_are_skipped(self):
        b = comb_block([50])
        out = denoise.apply_chain(b, FREQS, ("nope",), denoise.defaults())
        assert np.array_equal(out, b)


class TestChunkInvariance:
    """The property that lets `process()` denoise a chunk at a time.

    `BufferedSpectrogram.process()` transforms the buffer in blocks of
    `chunk_columns` and `tests/test_chunked_dsp.py` pins the result as
    bit-identical to transforming it whole.  A denoiser that looked at
    neighbouring columns would break that at every chunk boundary, so the
    registry only admits ones that are pointwise in time -- and this is
    what "pointwise in time" is worth as a test.
    """

    def test_every_entry_is_pointwise_in_time(self):
        rng = np.random.default_rng(11)
        b = rng.random((24, 4, len(FREQS))) + 1e-3
        for entry in denoise.all_denoisers():
            whole = entry.apply(b, FREQS, entry.defaults())
            halves = np.concatenate([
                entry.apply(b[:10], FREQS, entry.defaults()),
                entry.apply(b[10:], FREQS, entry.defaults()),
            ])
            assert np.array_equal(whole, halves), entry.key

    def test_the_whole_chain_is_pointwise_in_time(self):
        rng = np.random.default_rng(12)
        b = rng.random((30, 4, len(FREQS))) + 1e-3
        params = denoise.defaults()
        whole = denoise.apply_chain(b, FREQS, denoise.keys(), params)
        pieces = np.concatenate([
            denoise.apply_chain(b[i:i + 7], FREQS, denoise.keys(), params)
            for i in range(0, len(b), 7)
        ])
        assert np.array_equal(whole, pieces)


class TestRegistry:
    def test_keys_are_unique(self):
        keys = [d.key for d in denoise.all_denoisers()]
        assert len(keys) == len(set(keys))

    def test_unknown_key_is_none_rather_than_a_raise(self):
        """A settings file from a newer version must not stop a recording
        from opening."""
        assert denoise.denoiser("no-such-denoiser") is None

    def test_spatial_declares_the_channels_it_needs(self):
        assert denoise.denoiser("spatial").min_channels >= 2

    def test_mains_works_on_a_single_channel(self):
        """The point of having it: a recording with too few electrodes for
        a spatial measure still has a mains problem."""
        assert denoise.denoiser("mains").min_channels == 1
        b = np.full((3, 1, len(FREQS)), FLOOR)
        b[:, :, np.abs(FREQS - 50) <= 0.5] = NEEDLE
        assert at(mains(b, frequency=50.0, harmonics=1), 50) == pytest.approx(
            FLOOR, abs=0.01
        )

    def test_ordered_deduplicates_and_sorts(self):
        assert denoise.ordered(("spatial", "mains", "spatial")) == ("mains", "spatial")

    def test_defaults_cover_every_declared_parameter(self):
        values = denoise.defaults()
        for entry in denoise.all_denoisers():
            for param in entry.params:
                assert param.key in values[entry.key]

    def test_every_parameter_default_is_inside_its_bounds(self):
        for entry in denoise.all_denoisers():
            for param in entry.params:
                assert param.minimum <= param.default <= param.maximum
                assert param.clamp(param.default) == param.default

    def test_clamp_bounds_and_rounds(self):
        param = denoise.denoiser("mains").parameter("harmonics")
        assert param.clamp(1e9) == param.maximum
        assert param.clamp(-5) == param.minimum
        assert param.clamp(3.7) == 4


class TestPluginRegistration:
    """Denoisers arrive through the plugin loader, like the event detector."""

    def test_the_bundled_plugin_is_discovered(self):
        from audian.plugins import Plugins

        denoise.clear()
        try:
            plugins = Plugins()
            plugins.load_plugins()
            assert denoise.denoiser("mains") is not None
            assert denoise.denoiser("spatial") is not None
        finally:
            denoise.clear()
            for entry in audian_builtin_denoisers():
                denoise.register(entry)

    def test_order_and_not_registration_decides_the_chain(self):
        """A plugin loading first must not put its denoiser first.

        Discovery order is bundled, then installed, then the working
        directory -- so without an explicit order the picture would depend
        on what happened to be installed.
        """
        denoise.clear()
        try:
            late = denoise.Denoiser(key="late", name="Late", apply=lambda b, f, v: b,
                                    order=90)
            early = denoise.Denoiser(key="early", name="Early", apply=lambda b, f, v: b,
                                     order=10)
            denoise.register(late)
            denoise.register(early)
            assert denoise.keys() == ("early", "late")
            assert denoise.ordered(("late", "early")) == ("early", "late")
        finally:
            denoise.clear()
            for entry in audian_builtin_denoisers():
                denoise.register(entry)

    def test_registering_the_same_key_replaces_rather_than_duplicates(self):
        """Every `Plugins` instance runs the factories, and a suite builds
        several -- so registration has to be idempotent."""
        before = denoise.keys()
        for entry in audian_builtin_denoisers():
            denoise.register(entry)
            denoise.register(entry)
        assert denoise.keys() == before

    def test_a_plugin_can_override_a_bundled_denoiser(self):
        mine = denoise.Denoiser(key="mains", name="Mine", apply=lambda b, f, v: b)
        try:
            denoise.register(mine)
            assert denoise.denoiser("mains").name == "Mine"
        finally:
            for entry in audian_builtin_denoisers():
                denoise.register(entry)
        assert denoise.denoiser("mains").name == "&Mains hum"

    def test_a_bad_factory_does_not_lose_the_others(self, capsys):
        from audian.plugins import Plugins

        denoise.clear()
        try:
            plugins = Plugins()

            def broken():
                raise RuntimeError("no")

            plugins.add_denoiser_factory(broken)
            plugins.add_denoiser_factory(audian_builtin_denoisers)
            plugins.setup_denoisers()
            assert denoise.denoiser("mains") is not None
        finally:
            denoise.clear()
            for entry in audian_builtin_denoisers():
                denoise.register(entry)

    def test_something_that_is_not_a_denoiser_is_refused(self):
        with pytest.raises(TypeError):
            denoise.register("not a denoiser")
