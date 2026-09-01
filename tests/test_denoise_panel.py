"""Denoising through the running application, not just the array functions.

Runs offscreen::

    QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_denoise_panel.py -q

`test_denoise.py` pins the arithmetic.  What it cannot see is the wiring:
that ticking a layer reaches the buffer at all, that its rows appear in the
side panel and its parameters get through, and -- the one most likely to
ship broken -- that unticking it *undoes* the denoising.  A denoiser that
turns on and never off looks fine in every screenshot of it turned on.

The recording is an array in miniature: one tone that arrives equally on
every electrode, which is what pickup does, and one that falls off across
them, which is what a fish does.  A recording of unrelated noise would let
a denoiser that gates everything pass.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from test_panelsplitter import (  # noqa: E402
    FRAMES,
    RATE,
    app,  # noqa: F401  - the session QApplication fixture
    open_stack,
    pump,
)

from audian import denoise  # noqa: E402

CHANNELS = 4

#: Equal on every electrode -- cable pickup, and the thing to remove.
COMMON_HZ = 250.0
#: Falls off across the array -- a source in the water, and the thing to keep.
LOCAL_HZ = 1000.0


def array_signal(seed: int = 20260901) -> np.ndarray:
    rng = np.random.default_rng(seed)
    t = np.arange(FRAMES)/RATE
    common = 0.30*np.sin(2*np.pi*COMMON_HZ*t)
    local = 0.30*np.sin(2*np.pi*LOCAL_HZ*t)
    # 1.0, 0.25, 0.06, 0.016 -- about 36 dB across four electrodes
    gains = 0.25**np.arange(CHANNELS)
    signal = np.empty((FRAMES, CHANNELS))
    for c in range(CHANNELS):
        signal[:, c] = common + gains[c]*local
    signal += rng.normal(0.0, 2e-4, signal.shape)
    return signal


@pytest.fixture(scope="module")
def view(app, tmp_path_factory):  # noqa: F811
    yield from open_stack(
        app, tmp_path_factory.mktemp("denoise"), CHANNELS, array_signal()
    )


def power_at(view, hz):
    """Mean power of the bin nearest `hz`, on the loudest channel."""
    spec = view.data[view.spectrogram]
    buffer = np.asarray(spec.buffer)
    assert buffer.size > 0, "spectrogram buffer is empty"
    k = int(np.argmin(np.abs(np.asarray(spec.frequencies) - hz)))
    return float(buffer[:, 0, k].mean())


def enable(view, key, on=True):
    view.set_denoiser_enabled(key, on)
    pump(1.5)


def all_off(view):
    for key in denoise.keys():
        view.set_denoiser_enabled(key, False)
    pump(1.5)


class TestSpatialLayer:
    def test_nothing_is_enabled_to_begin_with(self, view):
        assert view.denoisers_enabled() == ()

    def test_it_removes_the_common_mode_tone(self, view):
        all_off(view)
        before = power_at(view, COMMON_HZ)
        enable(view, "spatial")
        assert view.denoiser_is_on("spatial")
        assert power_at(view, COMMON_HZ) < 0.2*before
        all_off(view)

    def test_the_localised_tone_survives_it(self, view):
        all_off(view)
        before = power_at(view, LOCAL_HZ)
        enable(view, "spatial")
        assert power_at(view, LOCAL_HZ) > 0.8*before
        all_off(view)

    def test_switching_it_off_restores_the_buffer(self, view):
        """The recompute that undoes denoising is a separate code path from
        the one that applies it, and the easy bug is for it not to happen."""
        all_off(view)
        plain = power_at(view, COMMON_HZ)
        enable(view, "spatial")
        assert power_at(view, COMMON_HZ) < 0.2*plain
        enable(view, "spatial", False)
        assert view.denoisers_enabled() == ()
        assert power_at(view, COMMON_HZ) == pytest.approx(plain, rel=1e-6)


class TestMainsLayer:
    def test_it_removes_a_tone_sitting_on_its_comb(self, view):
        """250 Hz is the fifth harmonic of 50, so the default comb reaches
        it -- which is exactly the Sternopygus-on-a-harmonic case the
        docstring warns about, here used to prove the comb works."""
        all_off(view)
        before = power_at(view, COMMON_HZ)
        enable(view, "mains")
        view.request_recompute(
            view.data[view.spectrogram],
            denoise_params={"mains": {"frequency": 50.0, "width": 4.0,
                                      "harmonics": 8}},
        )
        pump(1.5)
        assert power_at(view, COMMON_HZ) < 0.5*before
        all_off(view)

    def test_pointing_the_comb_elsewhere_leaves_it_alone(self, view):
        """The reason `frequency` is adjustable: 60 Hz has no harmonic at
        250, so the same tone must survive."""
        all_off(view)
        before = power_at(view, COMMON_HZ)
        enable(view, "mains")
        view.request_recompute(
            view.data[view.spectrogram],
            denoise_params={"mains": {"frequency": 60.0, "width": 4.0,
                                      "harmonics": 8}},
        )
        pump(1.5)
        assert power_at(view, COMMON_HZ) > 0.9*before
        all_off(view)

    def test_a_parameter_out_of_bounds_is_clamped_not_stored(self, view):
        view.request_recompute(
            view.data[view.spectrogram],
            denoise_params={"mains": {"harmonics": 10**6}},
        )
        param = denoise.denoiser("mains").parameter("harmonics")
        assert view.denoise_value("mains", "harmonics") == param.maximum
        view.request_recompute(
            view.data[view.spectrogram],
            denoise_params={"mains": {"harmonics": param.default}},
        )


class TestLayering:
    def test_both_can_run_at_once(self, view):
        all_off(view)
        enable(view, "mains")
        enable(view, "spatial")
        assert view.denoisers_enabled() == ("mains", "spatial")
        all_off(view)

    def test_the_chain_is_in_registry_order_whatever_the_tick_order(self, view):
        all_off(view)
        enable(view, "spatial")
        enable(view, "mains")
        assert view.denoisers_enabled() == ("mains", "spatial")
        all_off(view)

    def test_parameters_survive_being_switched_off_and_on(self, view):
        all_off(view)
        view.request_recompute(
            view.data[view.spectrogram],
            denoise_params={"mains": {"frequency": 60.0}},
        )
        enable(view, "mains")
        enable(view, "mains", False)
        assert view.denoise_value("mains", "frequency") == 60.0
        view.request_recompute(
            view.data[view.spectrogram],
            denoise_params={"mains": {"frequency": 50.0}},
        )


class TestPanelAndMenu:
    def test_every_denoiser_has_a_menu_entry_and_a_switch(self, view):
        window = view.window()
        for entry in denoise.all_denoisers():
            assert entry.key in window.acts.denoisers
            assert entry.key in view.denoise_widgets

    def test_every_declared_parameter_has_a_row(self, view):
        for entry in denoise.all_denoisers():
            rows = view.denoise_widgets[entry.key]["params"]
            for param in entry.params:
                assert param.key in rows, (entry.key, param.key)

    def test_parameter_rows_are_hidden_until_the_layer_is_on(self, view):
        all_off(view)
        view.sync_denoise_rows()
        rows = view.denoise_widgets["mains"]["params"]["frequency"]["row"]
        # isHidden() and not isVisible(): the rows live on a page of a
        # stacked layout, so isVisible() is False for every one of them
        # whenever another tab is in front, whatever the sync did.
        assert all(w.isHidden() for w in rows)
        enable(view, "mains")
        assert not any(w.isHidden() for w in rows)
        all_off(view)
        assert all(w.isHidden() for w in rows)

    def test_the_boxes_show_what_the_buffer_holds(self, view):
        view.request_recompute(
            view.data[view.spectrogram],
            denoise_params={"mains": {"frequency": 60.0}},
        )
        view.sync_denoise_widgets()
        box = view.denoise_widgets["mains"]["params"]["frequency"]["box"]
        assert box.value() == pytest.approx(60.0)
        view.request_recompute(
            view.data[view.spectrogram],
            denoise_params={"mains": {"frequency": 50.0}},
        )
        view.sync_denoise_widgets()
        assert box.value() == pytest.approx(50.0)

    def test_integer_parameters_get_no_slider(self, view):
        holder = view.denoise_widgets["mains"]["params"]["harmonics"]
        assert holder["slider"] is None
        assert view.denoise_widgets["mains"]["params"]["width"]["slider"] is not None

    def test_threshold_steps_are_enabled_only_while_spatial_runs(self, view):
        window = view.window()
        all_off(view)
        window.sync_denoise_actions(view)
        assert not window.acts.denoise_threshold_up.isEnabled()
        enable(view, "spatial")
        window.sync_denoise_actions(view)
        assert window.acts.denoise_threshold_up.isEnabled()
        all_off(view)

    def test_the_keyboard_step_moves_the_spatial_threshold(self, view):
        all_off(view)
        enable(view, "spatial")
        before = view.denoise_value("spatial", "threshold")
        view.denoise_threshold_up()
        pump(0.5)
        after = view.denoise_value("spatial", "threshold")
        step = denoise.denoiser("spatial").parameter("threshold").step
        assert after == pytest.approx(before + step)
        view.denoise_threshold_down()
        pump(0.5)
        assert view.denoise_value("spatial", "threshold") == pytest.approx(before)
        all_off(view)

    def test_a_layer_needing_more_channels_is_refused_and_untouched(self, view):
        """A denoiser that needs more channels than the recording has must
        leave both the buffer and the tick alone."""
        window = view.window()
        all_off(view)
        assert view.denoiser_usable("spatial") is True
        channels = view.data.channels
        try:
            view.data.channels = 1
            assert view.denoiser_usable("spatial") is False
            assert view.denoiser_usable("mains") is True
            view.set_denoiser_enabled("spatial", True)
            assert view.denoisers_enabled() == ()
            window.sync_denoise_actions(view)
            assert not window.acts.denoisers["spatial"].isEnabled()
            assert window.acts.denoisers["mains"].isEnabled()
        finally:
            view.data.channels = channels
            window.sync_denoise_actions(view)
            view.sync_denoise_rows()
