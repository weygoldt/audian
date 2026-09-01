"""What a spectrogram denoiser is, and the registry plugins add them to.

A spectrogram of an electrode array holds three kinds of thing, and only one
of them is noise.  Wave-type fish draw horizontal lines -- a fundamental held
for minutes or hours.  Pulse-type fish draw vertical ones -- a discharge
broadband enough to cross the whole panel in a millisecond.  Mains hum and
cable pickup draw horizontal lines too, which is exactly why they are hard:
on one channel a resting *Sternopygus* at 50 Hz and a mains harmonic at
50 Hz are the same picture, and no filter that reads one channel can tell
them apart without deleting the fish.

This module is the *contract*.  The denoisers themselves are a plugin --
`audian_plugins.denoisers` -- discovered exactly as the event detector is,
so one written elsewhere is the equal of the two that ship here.

Why a registry and not a chain of traces
----------------------------------------

audian already layers data: a trace names a source, `BufferedData` walks the
dependency chain, and `Spectrogram > Active` lists every spectrogram there
is.  Denoising deliberately does *not* use it, and the reason is worth
stating because the alternative looks so close.

A trace owns a buffer.  One spectrogram of sixteen channels at 20 kHz over
a 60 s buffer is 155 MB whatever the window length -- the figure `Data`
already calls a working set far outside L3 -- so two denoisers as two traces
is 465 MB where one picture is being looked at.  Run inside `process()`
instead, between the transform and the write into the buffer, they cost
nothing at all.

And `Spectrogram > Active` picks *one* spectrogram to draw.  Traces are
alternatives, which is the right shape for "the mean of these channels
instead of that one" and the wrong shape for "and also take the hum out":
denoisers compose, so what stacks is the transformation and not the
picture.

A trace derived from a spectrogram would also need machinery that does not
exist: `more_shape` is fixed at `open()` and nothing propagates a source's
shape to its destinations, so the frequency axis of such a trace would go
stale the moment the reader changed the Fourier window.

Writing one
-----------

A plugin exposes a callable named ``audian_*denoisers`` returning
`Denoiser` objects::

    from audian.pluginapi import Denoiser, Parameter

    def audian_myfilter_denoisers():
        return [Denoiser(key="mine", name="&My filter", apply=my_filter,
                         params=(Parameter("cut", "Cut", 3.0, 0.0, 20.0),),
                         order=50)]

`apply` is handed the chunk `BufferedSpectrogram.process()` just
transformed, shaped ``(time, channel, frequency)`` and holding power, the
frequency of each bin, and a dict of its own parameter values; it returns an
array of the same shape.

`order` and not registration order decides where in the chain it runs, so
that what the reader sees depends on which denoisers are enabled and never
on the order the plugins happened to load in.

The one hard requirement is that a denoiser be **pointwise in time**: it may
look across channels and across frequency, but it may not look at
neighbouring columns.  `process()` transforms the buffer in hop-aligned
chunks of `chunk_columns` and relies on the result being bit-identical to
transforming the whole buffer at once (`tests/test_chunked_dsp.py`); a
filter with any time extent would see a different neighbourhood at a chunk
boundary and break that.  `tests/test_denoise.py` asserts it of every
registered denoiser, so the constraint is enforced on the next one rather
than remembered.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Parameter:
    """One number a denoiser takes, and everything the panel needs to draw it.

    Declared rather than hand-built so that the side panel and the stored
    settings are both generated from the registry: a denoiser that grows a
    parameter grows a row, and one that is renamed does not leave a widget
    behind wired to nothing.

    Attributes
    ----------
    key
        Name in the denoiser's own value dict.  Persisted, so it may not
        change once released.
    label
        Row caption in the side panel.
    default, minimum, maximum, step
        The spin box's value, bounds and single step.
    suffix
        Unit shown in the box -- "Hz", "dB", "%", or nothing.
    decimals
        Significant digits the box shows.  `pg.SpinBox` formats with
        ``%g``, so this is significant digits and not places.
    integer
        Whether the value is a count.  Drawn without a slider, since a
        slider over a handful of integers cannot land where one keystroke
        does.
    """

    key: str
    label: str
    default: float
    minimum: float
    maximum: float
    step: float = 1.0
    suffix: str = ""
    decimals: int = 4
    integer: bool = False

    def clamp(self, value: float) -> float:
        value = float(np.clip(value, self.minimum, self.maximum))
        return float(round(value)) if self.integer else value


@dataclass(frozen=True)
class Denoiser:
    """One layer of the Spectrogram menu's denoising list.

    Attributes
    ----------
    key
        Stable identifier.  What the stored settings carry, so it may not
        change once released.
    name
        What the menu says.  ``&`` marks its accelerator.
    apply
        ``(block, frequencies, values) -> block``.  Pointwise in time; see
        the module docstring.
    params
        The numbers it takes, in the order the side panel draws them.
    order
        Position in the chain.  Lower runs first.  Explicit so that the
        picture does not depend on plugin load order; leave it at the
        default and a plugin's denoiser runs after the bundled ones.
    min_channels
        Below this many channels the entry is shown but disabled.  A
        spatial measure needs at least two electrodes; there is nothing
        useful to say about a mono recording.
    tip
        One line, for the menu's tool tip.
    """

    key: str
    name: str
    apply: Callable[[np.ndarray, np.ndarray, Mapping[str, float]], np.ndarray]
    params: tuple[Parameter, ...] = ()
    order: int = 100
    min_channels: int = 1
    tip: str = ""

    def defaults(self) -> dict[str, float]:
        return {p.key: p.default for p in self.params}

    def parameter(self, key: str) -> Parameter | None:
        for p in self.params:
            if p.key == key:
                return p
        return None


#: Every registered denoiser, kept sorted by `order`.  Written only through
#: `register`, read only through `all_denoisers`.
_REGISTRY: list[Denoiser] = []


def register(entry: Denoiser) -> None:
    """Add `entry` to the registry, replacing any with the same key.

    Replacing and not appending, for two reasons.  Registration has to be
    idempotent -- every `Plugins` instance runs the factories, and a test
    suite builds several -- and a plugin that deliberately names itself
    after a bundled denoiser should override it rather than sit beside it
    under the same key.
    """
    if not isinstance(entry, Denoiser):
        raise TypeError(f"not a Denoiser: {entry!r}")
    for i, existing in enumerate(_REGISTRY):
        if existing.key == entry.key:
            _REGISTRY[i] = entry
            break
    else:
        _REGISTRY.append(entry)
    _REGISTRY.sort(key=lambda d: (d.order, d.key))


def clear() -> None:
    """Empty the registry.  For tests that install a denoiser of their own."""
    _REGISTRY.clear()


def all_denoisers() -> tuple[Denoiser, ...]:
    """Every registered denoiser, in the order a chain applies them."""
    return tuple(_REGISTRY)


def keys() -> tuple[str, ...]:
    """Every registered key, in chain order."""
    return tuple(d.key for d in _REGISTRY)


def denoiser(key: str) -> Denoiser | None:
    """The entry named `key`, or None.

    Returning None rather than raising: `key` can come from stored settings
    written when a plugin was installed that no longer is, and losing the
    setting is a better answer than refusing to open the recording.
    """
    for d in _REGISTRY:
        if d.key == key:
            return d
    return None


def defaults() -> dict[str, dict[str, float]]:
    """Every denoiser's parameters at their default values."""
    return {d.key: d.defaults() for d in _REGISTRY}


def ordered(wanted) -> tuple[str, ...]:
    """`wanted` reduced to registered keys, deduplicated, in chain order.

    The chain's order is `order` and never the order a reader happened to
    tick the boxes in, so that the picture depends on what is enabled and
    not on how it came to be.
    """
    seen = set(wanted)
    return tuple(d.key for d in _REGISTRY if d.key in seen)


def apply_chain(
    block: np.ndarray,
    frequencies: np.ndarray,
    enabled: Sequence[str],
    params: Mapping[str, Mapping[str, float]],
) -> np.ndarray:
    """Run every enabled denoiser over `block`, in chain order.

    `block` is not modified; with nothing enabled it is returned as it came.
    """
    out = block
    for key in ordered(enabled):
        entry = denoiser(key)
        if entry is None:
            continue
        values = dict(entry.defaults())
        values.update(params.get(key, {}))
        out = entry.apply(out, frequencies, values)
    return out
