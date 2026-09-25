"""The whole tree holds the lint rules in ``pyproject.toml`` and is formatted.

This used to check theme.py alone, with the rules passed on the command
line.  The rules now live in ``[tool.ruff.lint]``, so ``ruff check`` run by
hand, by CI and by this test all return the same verdict.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TREE = ("src", "tests", "scripts")


def _find_ruff():
    """The ruff belonging to the interpreter running this test, else PATH's.

    The suite runs as ``.venv/bin/python -m pytest``, which leaves the venv
    off PATH, so a bare ``"ruff"`` would miss the ruff the dev group
    installed beside that very interpreter.
    """
    for name in ("ruff", "ruff.exe"):
        beside = Path(sys.executable).parent / name
        if beside.exists():
            return str(beside)
    return shutil.which("ruff")


def test_the_tree_is_lint_clean_and_formatted():
    # A failure rather than a skip: ruff is in the dev group, so a missing
    # one is an environment that was never synced, and a skip there is a
    # lint nobody notices has stopped running.
    ruff = _find_ruff()
    assert ruff is not None, (
        "ruff not found beside the interpreter at "
        f"{Path(sys.executable).parent / 'ruff'} or on PATH; run `uv sync`"
    )
    for command in (["check"], ["format", "--check"]):
        result = subprocess.run(
            [ruff, *command, *TREE],
            cwd=REPO,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
