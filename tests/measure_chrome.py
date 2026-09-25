"""The window's minimum width and the tool bar's share of it.

Run from the repo:  uv run python tests/measure_chrome.py

Not a test.  The T470s is the width budget for any chrome change: measure
before and after.
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"  # the shell exports wayland
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tests"))
sys.path.insert(0, str(REPO / "src"))
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])
import test_panelsplitter as tp  # noqa: E402

window = tp.build_window(app, Path(tempfile.mkdtemp()), 4)
tp.pump(0.5)
print("window minimumSizeHint", window.minimumSizeHint().width())
print("toolbar minimumSizeHint", window.toolbar.minimumSizeHint().width())
print("toolbar sizeHint", window.toolbar.sizeHint().width())
window.close()
tp.pump(0.3)
