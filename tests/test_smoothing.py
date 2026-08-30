"""What the spectrogram smoothing menu promises, pinned.

Runs offscreen::

    QT_QPA_PLATFORM=offscreen .venv-qt6/bin/python -m pytest tests/test_smoothing.py -q

No window is built, for the reason ``tests/test_settings.py`` states: every
claim here is about an array or a lookup table, and answering it through a
browser would cost two more top-level widgets in the process, which is the
accumulated state ``todo.md`` records `theme.collect_orphan_widgets`
segfaulting on.  Importing `audian.smoothing` pulls in PySide6 -- the
package's ``__init__`` binds it deliberately -- but constructs nothing.

The behaviour *through* a running stack is pinned next door, in
``tests/test_meanspectrogram.py``: that module already owns the invariant
this feature is most able to break, that the pointer readout agrees with the
pixel it is standing on.
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

from audian import smoothing  # noqa: E402


# ------------------------------------------------------------- the registry


def test_every_method_is_reachable_by_its_key():
    """Keys are unique, and `index` and `keys` agree about the order."""
    keys = smoothing.keys()
    assert len(keys) == len(set(keys)), f"duplicate keys: {keys}"
    assert len(keys) == len(smoothing.METHODS)
    for position, key in enumerate(keys):
        assert smoothing.index(key) == position
        assert smoothing.method(key).key == key
    labels = smoothing.labels()
    assert len(labels) == len(set(labels)), f"duplicate labels: {labels}"
    assert all(label for label in labels)
    assert all(method.tip for method in smoothing.METHODS)


def test_a_key_this_audian_does_not_know_resolves_to_the_default():
    """The settings file can have been written by a newer audian.

    `DataBrowser.read_smoothing_setting` leans on this: a preference nobody
    can read is a reason to draw the picture plainly, not a reason to fail
    to draw it.
    """
    assert smoothing.DEFAULT in smoothing.keys()
    for unknown in (None, "", "synchrosqueezed", 3, object()):
        assert smoothing.resolve(unknown) == smoothing.DEFAULT
        assert smoothing.method(unknown).key == smoothing.DEFAULT
    # and the default itself is the one that changes nothing
    assert not smoothing.changes_values(smoothing.DEFAULT)
    assert not smoothing.interpolates(smoothing.DEFAULT)


def test_everything_that_filters_also_interpolates():
    """A reader who asked for a smoother picture did not ask for blocks.

    Stated in `smoothing`'s own docstring; pinned here because it is a rule
    about the table rather than about any one entry, so a new entry can
    break it without touching a line this suite otherwise reads.
    """
    for method in smoothing.METHODS:
        if method.filter is not None:
            assert method.interpolate, f"{method.key} filters but draws blocks"
    plain = [m for m in smoothing.METHODS if not m.interpolate]
    assert [m.key for m in plain] == ["none"]


# --------------------------------------------------------------- the arrays


def noisy_db(shape=(64, 400), seed=20250830) -> np.ndarray:
    """A dB image with the shape and spread of a real uploaded block."""
    rng = np.random.default_rng(seed)
    return rng.standard_normal(shape) * 8.0 - 90.0


@pytest.mark.parametrize("key", [m.key for m in smoothing.METHODS if m.filter is None])
def test_a_method_that_does_not_filter_hands_the_array_straight_back(key):
    """The same object, not an equal one: the common case is one lookup."""
    db = noisy_db()
    assert smoothing.smooth(db, key) is db


@pytest.mark.parametrize(
    "key", [m.key for m in smoothing.METHODS if m.filter is not None]
)
def test_a_filter_leaves_its_input_alone(key):
    """`SpecItem` still holds the array it handed over."""
    db = noisy_db()
    before = db.copy()
    out = smoothing.smooth(db, key)
    assert out is not db
    assert np.array_equal(db, before)
    assert out.shape == db.shape


@pytest.mark.parametrize(
    "key", [m.key for m in smoothing.METHODS if m.filter is not None]
)
def test_a_filter_is_what_it_says_it_is(key):
    """Less speckle, a lower peak, and a floor that stays where it was.

    All three matter downstream.  The variance is the point of the feature.
    The peak dropping is why `SpectrogramPlot._level_range` has to fit to
    the smoothed numbers -- a ramp fitted to the raw block tops out above
    anything in the image.  The median holding still is why that same
    function can leave the floor to the 5 dB snap.
    """
    db = noisy_db()
    out = smoothing.smooth(db, key)
    assert np.std(out) < np.std(db), "a smoothing that does not smooth"
    assert np.max(out) < np.max(db), "a smoothing that does not lower the peak"
    assert abs(np.median(out) - np.median(db)) < 2.0, "the floor moved"


# ------------------------------------------------- the dead-electrode trap


@pytest.mark.parametrize(
    "key", [m.key for m in smoothing.METHODS if m.filter is not None]
)
def test_one_dead_bin_does_not_empty_the_panel(key):
    """``decibel(0)`` is ``-inf`` and a kernel would smear it.

    The failure this prevents is the one `bufferedspectrogram.channel_power`
    records from the other side: four of the sixteen electrodes of the flona
    block are written as exactly zero.  Unguarded, every bin within the
    kernel's reach of one of them comes back non-finite, and a panel drawn
    from that is empty rather than smooth.
    """
    db = noisy_db()
    db[:, 100] = -np.inf
    db[7, 200] = np.nan
    out = smoothing.smooth(db, key)
    assert np.isfinite(out).all(), (
        f"{key} let a non-finite bin spread: "
        f"{(~np.isfinite(out)).sum()} of {out.size} bins"
    )
    # the dead column is still the dark one -- a blur lifts it towards its
    # live neighbours, which is what a blur is, but it cannot invert them
    assert out[:, 100].mean() < out[:, 300].mean() - 10.0
    # and a column well clear of it is untouched: the deadness stays inside
    # the kernel's reach instead of reaching the whole panel
    clean = smoothing.smooth(noisy_db(), key)
    assert out[:, 300] == pytest.approx(clean[:, 300], abs=1e-9)


@pytest.mark.parametrize(
    "key", [m.key for m in smoothing.METHODS if m.filter is not None]
)
def test_a_wholly_dead_channel_is_still_a_finite_picture(key):
    """The all-zero electrode, which is a whole panel of ``-inf``."""
    out = smoothing.smooth(np.full((32, 64), -np.inf), key)
    assert np.isfinite(out).all()
    assert out == pytest.approx(smoothing.FLOOR_DB)


def test_the_floor_is_the_one_the_rest_of_the_application_already_uses():
    """-200 dB is the colour bar's lower limit and the power curve's clamp.

    Named here so that moving one of the three is noticed rather than
    leaving a filtered image with a floor of its own.
    """
    assert smoothing.FLOOR_DB == -200.0
