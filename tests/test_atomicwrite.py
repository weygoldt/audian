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


def test_two_writers_of_one_file_do_not_share_a_temporary(tmp_path):
    """What makes two processes safe rather than only two crashes.

    `labels.write` and `save_setting` both used ``<name>.tmp``.  That is
    safe against an interrupted process and not against a second one: two
    audian instances open on one recording interleave so that A truncates
    the temporary while B is mid-write, and B's `os.replace` then publishes
    A's partial content under the real name -- both reporting success.

    Asserted by actually running two writers against one target with
    different identities, rather than by looking for a pid in the name: the
    latter restates the implementation and cannot fail for any version that
    mentions `os.getpid`, including a broken one.
    """
    path = tmp_path / "thing.csv"
    seen = []

    def record(tmp):
        seen.append(tmp)
        tmp.write_text("x", encoding="utf-8")

    replace_atomically(path, record)
    other = os.getpid() + 1
    with pytest.MonkeyPatch.context() as second_process:
        second_process.setattr("audian.atomicwrite.os.getpid", lambda: other)
        replace_atomically(path, record)

    assert len(seen) == 2
    assert seen[0] != seen[1], "two writers would have shared one temporary"
    assert all(t.parent == path.parent for t in seen), (
        "os.replace across a filesystem is not atomic"
    )
    assert all(t != path.with_name(path.name + ".tmp") for t in seen)


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
