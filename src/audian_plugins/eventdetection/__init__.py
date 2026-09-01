"""Few-shot event detection by normalised cross-correlation.

A reader marks three or four examples of something -- a chirp, a pulse, an
EOD -- and this finds the rest.  `engine` is the arithmetic and imports no
Qt, so it can be exercised without a window; `panel` is the half that has a
reader in it.

This file is the whole interface to audian: the callables named ``audian_*``
below are what `Plugins.load_plugins` binds, whether it reached this package
by walking `audian_plugins` or through an ``audian.plugins`` entry point.
Keeping it to that -- and importing `panel` lazily, so a headless run of
`engine` never builds a Qt import chain -- is what makes the package a
directory move away from its own repository.
"""

__all__ = ["audian_event_detection_panel"]


def audian_event_detection_panel(browser):
    """Open the detector's tab.

    The name is the contract.  `Plugins.load_plugins` binds any callable
    here called ``audian_*`` that ends in ``panel``, so renaming this
    function is what unregisters the plugin -- not an import error, and not
    a registration call that would fail loudly.
    """
    from .panel import DetectorPanel

    return "Detector", DetectorPanel(browser)


#: Where the entry sits: **Plugins > Event detection > Normalised
#: cross-correlation**.  Under a heading rather than at the top level
#: because "Detector" says nothing about which of several a reader is
#: turning on, and the next one to be written will be a detector too --
#: the heading is what makes the second one cheap to add.
audian_event_detection_panel.menu_path = ("Event detection",
                                         "Normalised cross-correlation")
