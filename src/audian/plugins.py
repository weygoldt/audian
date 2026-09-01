import importlib
import logging
import os
import sys

from pathlib import Path

from .bufferedfilter import BufferedFilter
from .bufferedspectrogram import BufferedSpectrogram

log = logging.getLogger(__name__)


def default_setup_traces(browser):
    browser.add_trace(BufferedFilter())
    browser.add_trace(BufferedSpectrogram())


class Plugins(object):
    def __init__(self):
        self.plugins = {}
        self.trace_factories = []
        self.add_trace_factory(default_setup_traces)
        self.analyzer_factories = []
        self.panel_factories = []

    def add_plugin(self, name, module):
        self.plugins[name] = module

    def add_trace_factory(self, factory_func):
        self.trace_factories.append(factory_func)

    def clear_trace_factories(self):
        self.trace_factories = []

    def add_analyzer_factory(self, factory_func):
        self.analyzer_factories.append(factory_func)

    def clear_analyzer_factories(self):
        self.analyzer_factories = []

    def add_panel_factory(self, factory_func):
        """Register a callable returning one side-panel tab.

        The factory is called with the browser and returns
        ``(title, widget)``, or ``None`` to add no panel::

            def audian_wavetracker_panel(browser):
                return "Wavetracker", WavetrackerPanel(browser)

        Discovered by the same naming convention the traces and the
        analyzers use, so a plugin author who has written one already knows
        how to write this.
        """
        self.panel_factories.append(factory_func)

    def clear_panel_factories(self):
        self.panel_factories = []

    def load_plugins(self):
        cwd = Path.cwd()
        sys.path.append(os.fspath(cwd))
        for module in cwd.glob("audian*.py"):
            x = importlib.import_module(module.stem)
            called = False
            for k in dir(x):
                if k.startswith("audian_") and callable(getattr(x, k)):
                    if k.endswith("traces"):
                        self.add_trace_factory(getattr(x, k))
                        called = True
                    elif k.endswith("analyzer"):
                        self.add_analyzer_factory(getattr(x, k))
                        called = True
                    elif k.endswith("panel"):
                        self.add_panel_factory(getattr(x, k))
                        called = True
            if called:
                self.add_plugin(k, x)
                print(f"loaded audian plugins from {module.stem}")
        sys.path.pop()

    def setup_traces(self, browser):
        for f in self.trace_factories:
            f(browser)

    def setup_analyzer(self, browser):
        for f in self.analyzer_factories:
            f(browser)

    def panel_entries(self) -> list:
        """``(label, factory)`` for everything that could open a panel.

        The label without building the widget, because the Plugins menu has
        to list what a reader *could* open, and building every plugin's
        panel to find out what it is called is what "could" was supposed to
        avoid.
        """
        return [(panel_label(f), f) for f in self.panel_factories]

    def setup_panels(self, browser):
        """Offer every registered factory to the browser, unopened.

        The factories used to be called here and their tabs added at once,
        so a plugin present was a plugin taking up the panel -- a reader who
        wanted the recording and not the plugin had nowhere to put it.  Now
        the offer is what arrives at startup and `DataBrowser` calls the
        factory when the reader asks for it from the menu.

        The plugin's own contract is unchanged: still a callable returning
        ``(title, widget)``, still discovered by its name.  Only *when* it
        is called moved.
        """
        for label, factory in self.panel_entries():
            browser.offer_plugin_panel(label, factory)


def panel_label(factory) -> str:
    """What the Plugins menu calls this factory's panel.

    From the factory's own name -- ``audian_detector_panel`` becomes
    "Detector" -- so that a plugin author who has followed the naming
    convention has already named their menu entry.  A plugin that wants a
    different wording sets ``menu_label`` on the function.
    """
    label = getattr(factory, "menu_label", "")
    if label:
        return str(label)
    name = getattr(factory, "__name__", "") or "plugin"
    if name.startswith("audian_"):
        name = name[len("audian_"):]
    if name.endswith("_panel"):
        name = name[: -len("_panel")]
    return name.replace("_", " ").strip().capitalize() or "Plugin"


def panel_menu_path(factory) -> tuple:
    """Where in the Plugins menu this panel's entry belongs.

    The last element is the entry itself; anything before it names a
    submenu.  A plugin sets ``menu_path`` on its factory to file itself
    under a heading -- ``("Event detection", "Normalised cross-correlation")``
    -- so that a menu with several detectors in it groups them by what they
    do rather than growing one flat list of product names.

    Without one the entry sits at the top level under `panel_label`, which
    is what a plugin that has not thought about it should get.
    """
    path = getattr(factory, "menu_path", None)
    if not path:
        return (panel_label(factory),)
    if isinstance(path, str):
        return (path,)
    parts = tuple(str(p).strip() for p in path if str(p).strip())
    return parts or (panel_label(factory),)


def build_panel(factory, browser):
    """Call one plugin's factory, or return `None` if it misbehaved.

    The one place in the panel that wraps a call it does not own.  A plugin
    is somebody else's code on the reader's own path, and a broken one has
    to cost its own tab and nothing else -- not the panel, and not the file
    the reader was opening when it raised.
    """
    name = getattr(factory, "__name__", repr(factory))
    try:
        made = factory(browser)
    except Exception as exc:  # noqa: BLE001 - somebody else's code
        log.exception("side panel factory %s failed", name)
        browser.notify("error", f"plugin panel {name} failed: {exc}")
        return None
    if made is None:
        return None
    try:
        title, widget = made
    except (TypeError, ValueError):
        log.error("side panel factory %s returned %r, not (title, widget)",
                  name, made)
        browser.notify(
            "error", f"plugin panel {name} returned {made!r}, not a pair"
        )
        return None
    return str(title), widget
