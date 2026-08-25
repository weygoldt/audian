"""Tests for the smoke harness's promise not to touch the user's settings.

Runs offscreen::

    QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_smoketest.py -q

The harness clicks every toggle in the application, and several of those are
persisted.  It said so and covered ONE of the two stores they persist through
-- the JSON file behind `audian.settings_path()` -- while `QSettings("audian",
"audian")` went on writing ``~/.config/audian/audian.conf``.  A harness that
claims to be isolated and is not is worse than one that never claimed it: a
previous run clobbered the user's own preferences.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from PyQt5.QtCore import QSettings  # noqa: E402

import audian.audian as audian_app  # noqa: E402


@pytest.fixture
def smoke(monkeypatch):
    """The harness module, with everything it redirects put back afterwards.

    `redirect_persistence` writes over a module attribute and over Qt's own
    global search path, so a test that called it and walked away would leave
    the rest of the suite pointing at a deleted temporary directory.
    """
    path = REPO / "scripts" / "smoke_test.py"
    spec = importlib.util.spec_from_file_location("audian_smoke_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # recorded before the harness replaces it, restored by monkeypatch
    monkeypatch.setattr(audian_app, "settings_path", audian_app.settings_path)
    home = Path(QSettings("audian", "audian").fileName()).parent.parent
    yield module
    for fmt in (QSettings.NativeFormat, QSettings.IniFormat):
        for scope in (QSettings.UserScope, QSettings.SystemScope):
            QSettings.setPath(fmt, scope, os.fspath(home))


def test_the_smoke_harness_redirects_the_json_settings_file(smoke, tmp_path):
    smoke.redirect_persistence(tmp_path)
    assert audian_app.settings_path() == tmp_path / "settings.json"


def test_the_smoke_harness_redirects_the_qsettings_store_as_well(smoke, tmp_path):
    """The store `settings_path()` never covered.

    The spectrogram colour map is written through it today; the point of
    moving the whole store rather than the one key is that whatever reaches
    for QSettings next is covered without anyone remembering to come back
    here.
    """
    before = Path(QSettings("audian", "audian").fileName())
    smoke.redirect_persistence(tmp_path)
    after = Path(QSettings("audian", "audian").fileName())
    assert before != after
    assert tmp_path in after.parents


def test_a_setting_written_after_the_redirect_lands_in_the_scratch_directory(
    smoke, tmp_path
):
    """The claim, exercised: written through both channels, read back from
    the sandbox, and nothing of it outside."""
    smoke.redirect_persistence(tmp_path)
    audian_app.save_setting("theme", "light")
    QSettings("audian", "audian").setValue("spectrogram/colormap", 3)
    QSettings("audian", "audian").sync()
    written = {p.name for p in tmp_path.rglob("*") if p.is_file()}
    assert "settings.json" in written
    assert "audian.conf" in written
