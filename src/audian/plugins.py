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

    def setup_panels(self, browser):
        """Give every registered factory a chance at a side-panel tab.

        The one place in the panel that wraps a call it does not own.  A
        plugin is somebody else's code on the reader's own path, and a
        broken one has to cost its own tab and nothing else -- not the
        panel, and not the file the reader was opening when it raised.
        """
        for factory in self.panel_factories:
            try:
                made = factory(browser)
            except Exception as exc:  # noqa: BLE001 - somebody else's code
                name = getattr(factory, "__name__", repr(factory))
                log.exception("side panel factory %s failed", name)
                browser.notify("error", f"plugin panel {name} failed: {exc}")
                continue
            if made is None:
                continue
            try:
                title, widget = made
            except (TypeError, ValueError):
                name = getattr(factory, "__name__", repr(factory))
                log.error("side panel factory %s returned %r, not (title, widget)",
                          name, made)
                browser.notify(
                    "error", f"plugin panel {name} returned {made!r}, not a pair"
                )
                continue
            browser.add_plugin_panel(str(title), widget)
