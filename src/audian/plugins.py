import importlib
import logging
import os
import pkgutil
import sys

from importlib import metadata
from pathlib import Path

from . import denoise
from .bufferedfilter import BufferedFilter
from .bufferedspectrogram import BufferedSpectrogram

log = logging.getLogger(__name__)

#: The entry point group a plugin distribution advertises itself in.  The
#: contract between audian and a plugin that lives somewhere else: name a
#: module, and its ``audian_*`` callables are bound exactly as a bundled
#: plugin's are.
PLUGIN_ENTRY_POINT = "audian.plugins"


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
        self.denoiser_factories = []
        #: module names already bound, so overlapping discovery paths do
        #: not put the same plugin in the menu twice
        self._bound = set()

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

    def add_denoiser_factory(self, factory_func):
        """Register a callable returning `denoise.Denoiser` objects.

        Unlike the traces, the analyzers and the panels, a denoiser factory
        takes no browser and is called once: which denoisers *exist* is a
        property of what is installed, while which of them are *enabled* is
        a property of a browser and lives on its spectrogram.

            def audian_myfilter_denoisers():
                return [Denoiser(key="mine", ...)]

        Discovered by the same naming convention the others use.
        """
        self.denoiser_factories.append(factory_func)

    def clear_denoiser_factories(self):
        self.denoiser_factories = []

    def setup_denoisers(self) -> None:
        """Put every factory's denoisers in the registry.

        Called once, from `load_plugins`, before any browser is built --
        the Spectrogram menu and the side panel are both generated from the
        registry when a window opens, so a denoiser registered later would
        have no row and no menu entry.

        One bad factory loses its own denoisers and not everybody's, which
        is the rule `load_bundled` already follows for one bad import.
        """
        for factory in self.denoiser_factories:
            try:
                entries = factory()
            except Exception as exc:  # noqa: BLE001 - one bad plugin, not all
                log.exception("denoiser factory %r failed", factory)
                print(f"could not add denoisers from {factory}: {exc}")
                continue
            for entry in entries or []:
                try:
                    denoise.register(entry)
                except (TypeError, ValueError) as exc:
                    log.exception("bad denoiser from %r", factory)
                    print(f"ignoring denoiser from {factory}: {exc}")

    def bind(self, module, name: str = "") -> bool:
        """Register every ``audian_*`` callable a module exposes.

        The one rule the whole plugin system is built on, in one place, so
        that a plugin bundled with audian, one installed from its own
        distribution and one dropped in a working directory are all found
        the same way and cannot drift apart.

        A module is bound once.  The three discovery paths overlap on
        purpose -- a plugin part-way out of this tree is installed *and*
        still bundled, and a reader testing one keeps a copy in the working
        directory -- and binding it twice would put two identical entries in
        the Plugins menu, each opening its own tab.
        """
        if getattr(module, "__name__", None) in self._bound:
            return False
        found = False
        for key in dir(module):
            if not key.startswith("audian_"):
                continue
            value = getattr(module, key)
            if not callable(value):
                continue
            if key.endswith("traces"):
                self.add_trace_factory(value)
            elif key.endswith("analyzer"):
                self.add_analyzer_factory(value)
            elif key.endswith("denoisers"):
                self.add_denoiser_factory(value)
            elif key.endswith("panel"):
                self.add_panel_factory(value)
            else:
                continue
            found = True
        if found:
            self._bound.add(getattr(module, "__name__", None))
            self.add_plugin(name or getattr(module, "__name__", "plugin"), module)
        return found

    def load_bundled(self) -> None:
        """The plugins that ship inside audian's own tree.

        Walked rather than listed, so adding one is adding a directory.
        This is what makes a merged plugin work without being copied
        anywhere: discovery used to be the working directory alone, which
        meant installing a plugin was copying a file into every directory a
        reader ever launched from -- and a plugin that was present but not
        copied looked exactly like a feature that had not been installed at
        all, because the Plugins menu is absent when nothing registers.

        Each of these is a directory move from its own repository, at which
        point `load_installed` finds it instead and nothing else changes.
        """
        try:
            import audian_plugins
        except ImportError:
            return
        for info in pkgutil.iter_modules(audian_plugins.__path__):
            name = f"audian_plugins.{info.name}"
            try:
                module = importlib.import_module(name)
            except Exception as exc:  # noqa: BLE001 - one bad plugin, not all
                log.exception("bundled plugin %s failed to import", name)
                print(f"could not load {name}: {exc}")
                continue
            self.bind(module, name)

    def load_installed(self) -> None:
        """Plugins from any distribution that advertises one.

        The ``audian.plugins`` entry point group, which is how a plugin
        that has left this tree announces itself::

            [project.entry-points."audian.plugins"]
            eventdetection = "audian_plugins.eventdetection"

        The value names a *module*, and it is scanned by the same `bind`
        the other two paths use -- so extraction is a packaging change and
        not a rewrite.
        """
        try:
            found = metadata.entry_points(group=PLUGIN_ENTRY_POINT)
        except Exception:  # noqa: BLE001 - metadata is not always readable
            return
        for entry in found:
            try:
                module = entry.load()
            except Exception as exc:  # noqa: BLE001 - somebody else's code
                log.exception("plugin entry point %s failed", entry.name)
                print(f"could not load plugin {entry.name}: {exc}")
                continue
            if self.bind(module, entry.name):
                print(f"loaded audian plugin {entry.name}")

    def load_local(self) -> None:
        """Anything named ``audian*.py`` in the working directory.

        Kept for what it is good at: trying something out on one recording
        without installing anything.  It is not how a plugin is shipped --
        see `load_bundled` for why that was the whole problem.
        """
        cwd = Path.cwd()
        sys.path.append(os.fspath(cwd))
        try:
            for path in sorted(cwd.glob("audian*.py")):
                try:
                    module = importlib.import_module(path.stem)
                except Exception as exc:  # noqa: BLE001 - somebody else's code
                    log.exception("local plugin %s failed to import", path.stem)
                    print(f"could not load {path.name}: {exc}")
                    continue
                if self.bind(module, path.stem):
                    print(f"loaded audian plugins from {path.stem}")
        finally:
            sys.path.pop()

    def load_plugins(self):
        """Every plugin this installation can see.

        Bundled first, then installed, then the working directory, which is
        also the order of least to most surprising: a reader who dropped a
        file beside their recording is the one most likely to be overriding
        something on purpose.
        """
        self.load_bundled()
        self.load_installed()
        self.load_local()
        # After all three, so that a denoiser dropped in the working
        # directory can override a bundled one under the same key -- which
        # is the same precedence the discovery order above sets up.
        self.setup_denoisers()

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


def panel_menu_tip(factory) -> str:
    """One line of hover help for this panel's entry in the Plugins menu.

    A plugin sets ``menu_tip`` on its factory, the same way it sets
    ``menu_path``.  Failing that the factory's docstring speaks for it, and
    failing that the entry gets a sentence built from its own name -- which
    says nothing a reader could not already see, but is better than a menu
    where some items explain themselves and others do not.

    A plugin entry needs this more than most: its name is chosen by
    somebody else, it is the only entry in the bar whose wording audian
    cannot review, and it is the one a reader is least likely to recognise.
    """
    tip = getattr(factory, "menu_tip", None)
    if tip:
        return str(tip).strip()
    doc = (factory.__doc__ or "").strip()
    if doc:
        first = doc.split("\n\n", 1)[0].replace("\n", " ").strip()
        if first:
            return " ".join(first.split())
    return f"Show the {panel_menu_path(factory)[-1]} panel"


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
