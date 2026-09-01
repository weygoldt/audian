"""The data and DSP layer may not reach a widget.

Criterion 11 of the Qt6 migration is a property of the import graph, not of
a review: the modules that decode, filter and transform have to be callable
from a worker thread, and anything that touches a `QWidget`, a
`QGraphicsItem` or a pyqtgraph object is not.  Reviewing for that once is
worth nothing -- the next person to add a convenience import puts it back.
So it is a test.

`PySide6.QtCore` is deliberately *not* on the forbidden list: `QObject`,
`Signal` and `QTimer` are how a result crosses a thread boundary, and
forbidding them would forbid the fix rather than the problem.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src" / "audian"

#: Modules that must stay callable from a worker thread.
DATA_LAYER = [
    "buffereddata.py",
    "bufferedfilter.py",
    "bufferedenvelope.py",
    "bufferedspectrogram.py",
    "data.py",
    "compresseddata.py",
]

#: Whole packages under the data layer, checked file by file.
DATA_PACKAGES = ["tasks"]

FORBIDDEN_ROOTS = ("pyqtgraph", "PySide6.QtWidgets", "PySide6.QtGui")

#: `theme` is a table of colours, line widths and metrics.  It imports Qt
#: painting types to build them, so it can never itself be on the list above
#: -- but the data layer only ever reads plain values out of it, at
#: construction time, on the GUI thread.  That claim is checked rather than
#: asserted: `test_the_data_layer_only_reads_values_from_theme` pins the
#: attributes it is allowed to touch.
THEME_VALUE_ATTRIBUTES = {"trace_color", "LW_THIN", "LW_THICK"}


def data_layer_modules() -> list[Path]:
    paths = [SRC / name for name in DATA_LAYER]
    for package in DATA_PACKAGES:
        directory = SRC / package
        if directory.is_dir():
            paths.extend(sorted(directory.glob("*.py")))
    return paths


def imported_modules(tree: ast.AST) -> list[str]:
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.append(node.module)
    return names


@pytest.mark.parametrize("path", data_layer_modules(), ids=lambda p: p.name)
def test_the_data_layer_imports_no_widgets(path: Path) -> None:
    tree = ast.parse(path.read_text(), filename=str(path))
    offenders = [
        name
        for name in imported_modules(tree)
        if any(name == root or name.startswith(root + ".") for root in FORBIDDEN_ROOTS)
    ]
    assert not offenders, (
        f"{path.name} imports {offenders}. The data and DSP layer runs on a "
        f"worker thread; GUI types may only be touched from the thread that "
        f"owns them."
    )


@pytest.mark.parametrize("path", data_layer_modules(), ids=lambda p: p.name)
def test_the_data_layer_only_reads_values_from_theme(path: Path) -> None:
    """`theme` is allowed here only as a lookup table, so pin what is read."""
    tree = ast.parse(path.read_text(), filename=str(path))
    used = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "theme"
    }
    extra = used - THEME_VALUE_ATTRIBUTES
    assert not extra, (
        f"{path.name} reads theme.{sorted(extra)}. Only plain value lookups "
        f"{sorted(THEME_VALUE_ATTRIBUTES)} are allowed across this boundary."
    )


def test_no_trace_holds_a_plot_item() -> None:
    """`BufferedData.plot_items` is gone and must not come back."""
    from audian.buffereddata import BufferedData

    trace = BufferedData("x", "y")
    assert not hasattr(trace, "plot_items")
    assert trace.visible_channels.dtype == bool


def _methods_calling(tree: ast.AST, attr: str) -> set[str]:
    """Names of the functions whose body calls ``<something>.attr(...)``."""
    found = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for inner in ast.walk(node):
            if (
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Attribute)
                and inner.func.attr == attr
            ):
                found.add(node.name)
    return found


def test_every_caller_of_update_times_joins_the_compute_worker() -> None:
    """Moving the loader's buffer while a worker reads it is the one rule.

    `Data.update_times` shifts the buffer in place; `plan_chain` captured
    `t.source.buffer` and `run_job` slices that same array on the worker
    thread.  `tasks/manager.py` states the rule and both `set_times` and
    `set_panels` obeyed it -- `apply_time_ranges`, reached from twelve
    keyboard actions, did not, which is how a page-down inside the ~400 ms
    after a filter change could read a memmoved buffer.

    Pinned structurally rather than by exercising one method, because the
    rule lives in each caller and that is exactly why it drifted: the next
    method to call `update_times` has to join too, and nothing else would
    say so.
    """
    path = SRC / "databrowser.py"
    tree = ast.parse(path.read_text(), filename=str(path))
    movers = _methods_calling(tree, "update_times")
    joiners = _methods_calling(tree, "cancel_and_wait")
    assert movers, "no caller of update_times found; has it been renamed?"
    missing = sorted(movers - joiners)
    assert not missing, (
        f"{missing} call data.update_times without cancel_and_wait first. "
        "That moves the loader's buffer while the compute worker may be "
        "slicing it -- see tasks/manager.py."
    )
