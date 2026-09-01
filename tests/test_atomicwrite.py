"""The one publish-or-nothing write, shared by the three files that need it."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from audian.atomicwrite import replace_atomically  # noqa: E402


def test_a_write_that_succeeds_publishes_the_whole_file(tmp_path):
    path = tmp_path / "thing.csv"
    replace_atomically(path, lambda tmp: tmp.write_text("first", encoding="utf-8"))
    assert path.read_text(encoding="utf-8") == "first"
    replace_atomically(path, lambda tmp: tmp.write_text("second", encoding="utf-8"))
    assert path.read_text(encoding="utf-8") == "second"
    assert [p.name for p in tmp_path.iterdir()] == ["thing.csv"]


def test_the_temporary_is_not_the_same_name_in_every_process(tmp_path):
    """What makes two writers safe rather than only two crashes.

    `labels.write` and `save_setting` both used ``<name>.tmp``.  That is
    safe against an interrupted process and not against a second one: two
    audian instances on one recording, or two browsers each with their own
    zero-delay save timer, interleave so that one truncates the temporary
    while the other is mid-write, and the loser's `os.replace` publishes
    the winner's partial content under the real name -- both reporting
    success.
    """
    path = tmp_path / "thing.csv"
    seen = []
    replace_atomically(path, lambda tmp: (seen.append(tmp), tmp.write_text("x")))
    tmp = seen[0]
    assert tmp != path.with_name(path.name + ".tmp")
    assert str(os.getpid()) in tmp.name
    assert tmp.parent == path.parent, "os.replace across a filesystem is not atomic"


def test_a_failure_leaves_the_old_file_and_no_temporary(tmp_path, monkeypatch):
    path = tmp_path / "thing.csv"
    replace_atomically(path, lambda tmp: tmp.write_text("keep me", encoding="utf-8"))

    monkeypatch.setattr(
        "audian.atomicwrite.os.replace",
        lambda *a, **k: (_ for _ in ()).throw(OSError("no space left on device")),
    )
    with pytest.raises(OSError):
        replace_atomically(path, lambda tmp: tmp.write_text("lost", encoding="utf-8"))

    assert path.read_text(encoding="utf-8") == "keep me"
    assert [p.name for p in tmp_path.iterdir()] == ["thing.csv"]


def test_a_writer_that_raises_leaves_nothing_behind(tmp_path):
    """The callback owns producing the bytes, so it is also a failure point."""
    path = tmp_path / "thing.csv"

    def explode(tmp):
        tmp.write_text("half", encoding="utf-8")
        raise ValueError("ran out of rows")

    with pytest.raises(ValueError):
        replace_atomically(path, explode)
    assert not path.exists()
    assert list(tmp_path.iterdir()) == []
