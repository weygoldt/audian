"""The plugins that ship with audian.

A separate top-level package rather than a subpackage of `audian`, and that
is the whole point of it: nothing in `audian` imports anything from here, so
every module below can be lifted into a repository of its own without the
core noticing.  What holds them together is the discovery contract, not an
import.

The shape of a plugin
---------------------

One package per plugin, named for what it does rather than for what it is::

    audian_plugins/
        eventdetection/
            __init__.py     the ``audian_*`` callables, and nothing else
            engine.py       the arithmetic, importing no Qt
            panel.py        the interface

`__init__.py` is the only file audian looks at.  It exposes callables named
``audian_<something>_panel``, ``_analyzer`` or ``_traces``, which is the same
naming convention a loose ``audian*.py`` in a working directory uses -- a
plugin written one way is already written the other.

Everything a plugin needs from audian comes from `audian.pluginapi`.  Not as
a rule about tidiness: it is the list of things that will not move under a
plugin living in a different repository on a different release cycle.

Taking one out of the tree
--------------------------

Extraction is meant to be mechanical, and is three steps:

1. Move the package directory into its own repository.
2. Give it a ``pyproject.toml`` declaring the entry point audian scans::

       [project.entry-points."audian.plugins"]
       eventdetection = "audian_plugins.eventdetection"

3. Delete it from here, and add its distribution to audian's extras.

No code changes, in either repository.  `Plugins.load_plugins` finds a
bundled plugin by walking this package and an installed one through that
entry point group, and binds both by the same names -- so the move is
invisible to everything except the packaging.
"""
