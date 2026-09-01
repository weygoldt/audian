"""Denoising through the running application, not just the array function.

Runs offscreen::

    QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_denoise_panel.py -q

`test_denoise.py` pins the arithmetic.  What it cannot see is the wiring:
that picking the menu entry reaches the buffer at all, that the recompute is
actually asked for, and -- the one most likely to ship broken -- that
picking `None` again *undoes* it.  A denoiser that turns on and never off
looks fine in every screenshot of it turned on.

The recording is an array in miniature: one tone that arrives equally on
every electrode, which is what pickup does, and one that falls off across
them, which is what a fish does.  A recording of unrelated noise would let a
denoiser that gates everything pass.
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


def choose(view, key):
    view.set_denoiser(key)
    pump(1.5)


class TestThroughTheApplication:
    def test_the_menu_entries_exist_and_none_is_the_default(self, view):
        window = view.window()
        for entry in denoise.DENOISERS:
            assert entry.key in window.acts.denoisers
        assert view.current_denoiser() == denoise.NONE_KEY

    def test_spatial_coherence_removes_the_common_mode_tone(self, view):
        choose(view, denoise.NONE_KEY)
        before = power_at(view, COMMON_HZ)
        choose(view, "spatial")
        after = power_at(view, COMMON_HZ)
        assert view.current_denoiser() == "spatial"
        assert after < 0.2*before, f"{after:g} is not well below {before:g}"

    def test_the_localised_tone_survives_it(self, view):
        choose(view, denoise.NONE_KEY)
        before = power_at(view, LOCAL_HZ)
        choose(view, "spatial")
        after = power_at(view, LOCAL_HZ)
        assert after > 0.8*before, f"{after:g} lost too much of {before:g}"

    def test_switching_back_to_none_restores_the_buffer(self, view):
        """The recompute that undoes denoising is a separate code path from
        the one that applies it, and the easy bug is for it not to happen."""
        choose(view, denoise.NONE_KEY)
        plain = power_at(view, COMMON_HZ)
        choose(view, "spatial")
        assert power_at(view, COMMON_HZ) < 0.2*plain
        choose(view, denoise.NONE_KEY)
        assert view.current_denoiser() == denoise.NONE_KEY
        assert power_at(view, COMMON_HZ) == pytest.approx(plain, rel=1e-6)

    def test_threshold_steps_are_enabled_only_while_denoising(self, view):
        window = view.window()
        choose(view, denoise.NONE_KEY)
        window.sync_denoise_actions(view)
        assert not window.acts.denoise_threshold_up.isEnabled()
        choose(view, "spatial")
        window.sync_denoise_actions(view)
        assert window.acts.denoise_threshold_up.isEnabled()

    def test_raising_the_threshold_removes_more(self, view):
        choose(view, "spatial")
        spec = view.data[view.spectrogram]
        spec.denoise_threshold_db = denoise.DEFAULT_THRESHOLD_DB
        view.step_denoise_threshold(0.0)
        pump(1.0)
        loose = power_at(view, LOCAL_HZ)
        for _ in range(30):
            view.denoise_threshold_up()
        pump(2.0)
        tight = power_at(view, LOCAL_HZ)
        assert tight < loose
        choose(view, denoise.NONE_KEY)

    def test_the_tick_follows_the_data_when_a_choice_is_refused(self, view):
        """A denoiser that needs more channels than the recording has must
        leave both the buffer and the tick alone."""
        window = view.window()
        choose(view, denoise.NONE_KEY)
        assert view.denoiser_usable("spatial") is True
        # pretend the recording is mono for the length of the check
        channels = view.data.channels
        try:
            view.data.channels = 1
            assert view.denoiser_usable("spatial") is False
            view.set_denoiser("spatial")
            assert view.current_denoiser() == denoise.NONE_KEY
            window.sync_denoise_actions(view)
            assert not window.acts.denoisers["spatial"].isEnabled()
        finally:
            view.data.channels = channels
            window.sync_denoise_actions(view)
