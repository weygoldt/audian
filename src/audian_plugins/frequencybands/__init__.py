"""Curating tracked frequency bands on a spectrogram.

A band is one signal followed through time -- a fish's electric organ
discharge, a carrier that drifts, a harmonic that comes and goes.  A tracker
finds them and gets some of them wrong, always in the same two ways: it
breaks one band into two where the signal changed too fast, and it swaps two
identities where they crossed.  Neither is fixable by tracking harder, and
both are obvious to a person looking at the picture.  So this is the
interface for that person: find the bands, then merge, split, delete and
label them with the mouse.

It replaces `wavetracker.EODsorter`, which did this job through eight
mutually exclusive toolbar modes over a matplotlib canvas, saved by
overwriting the tracker's own ``.npy`` files in place, and had one step of
undo that several of its destructive operations never pushed onto.  What is
kept is its data model, which was right; see `wavetracker` for the arrays and
`bands` for what is stored instead.

The four modules split the way the detector plugin's do, and for the same
reason -- the halves that import Qt are the halves that need a window to
test:

``bands``
    What a band is, every edit, the undo history, and the two sidecar files.
    No Qt.
``tracking``
    Peak picking and linking.  No Qt.
``wavetracker``
    Reading a wavetracker output directory, read-only.  No Qt.
``panel``
    The tab, the mouse, and the worker thread.
``overlay``
    The bands drawn on one spectrogram lane.

This file is the whole interface to audian: the callable named ``audian_*``
below is what `Plugins.load_plugins` binds, whether it reached this package
by walking `audian_plugins` or through an ``audian.plugins`` entry point.
`panel` is imported lazily so that a headless run of `bands` or `tracking`
never builds a Qt import chain.
"""

__all__ = ["audian_frequency_bands_panel"]


def audian_frequency_bands_panel(browser):
    """Open the band curation tab.

    The name is the contract.  `Plugins.bind` registers any callable here
    called ``audian_*`` that ends in ``panel``, so renaming this function is
    what unregisters the plugin -- not an import error, and not a
    registration call that would fail loudly.
    """
    from .panel import BandPanel

    return "Bands", BandPanel(browser)


#: **Plugins > Frequency bands**, at the top level rather than under a
#: heading.  The detector sits under "Event detection" because a second
#: detector is an obvious thing to write and the heading is what makes it
#: cheap to add; there is no second band curator in prospect, and a
#: submenu holding one entry is a click that buys nothing.
audian_frequency_bands_panel.menu_path = ("Frequency bands",)
