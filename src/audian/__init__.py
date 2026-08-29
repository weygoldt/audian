"""
Python-based GUI for viewing and analyzing recordings of animal
vocalizations.
"""

# Choose the Qt binding before anything can choose it for us.
#
# pyqtgraph picks whichever binding is already in `sys.modules`, and failing
# that walks its own `libOrder`, which is [PyQt6, PySide6, PyQt5, PySide2] --
# PyQt6 first.  Eleven modules in this package do `import pyqtgraph as pg`,
# and `audian.py` does it at line 11, *above* its own PySide6 imports.  So on
# a machine with PyQt6 installed alongside, pyqtgraph would bind to PyQt6
# while audian binds to PySide6: two Qt libraries in one process, which
# segfaults rather than warning.
#
# It works here only because PyQt6 happens to be absent.  That is luck, not a
# decision, so make the decision: importing PySide6.QtCore first puts it in
# `sys.modules` before any `import pyqtgraph` can run, and pyqtgraph's first
# check finds it.
import PySide6.QtCore  # noqa: F401

from .audian import main
from .version import __version__

__all__ = ["__version__", "main"]
