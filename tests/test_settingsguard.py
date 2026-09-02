"""The guard that proves the settings redirect held.

`conftest._isolate_settings` points four persistent stores at a scratch
directory and then watches the reader's own config and cache directories for
writes *this process* makes.  A guard that cannot fail is not a guard, so
these fail it on purpose.

They fail it against a scratch directory standing in for the real one.  That
is not squeamishness: a test that proved the guard by writing to
`~/.config/audian` would be the exact write the guard exists to prevent, and
it would have to be excused from the guard to pass -- an exemption that then
sits in the suite waiting for a second caller.  Pointing the watch at
`tmp_path` instead exercises the same hook, on the same events, with nothing
of the reader's within reach.

The hook appends to a module-level list that the session-end check reads, so
`watching` removes whatever it recorded on the way out.  Proving the guard
fires must not fail the session that is proving it.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
from pathlib import Path

import conftest


@contextlib.contextmanager
def watching(directory: Path):
    """Point the guard at *directory*; yield a reader for what it records."""
    before = len(conftest._store_writes)
    original = conftest._REAL_STORE_DIRS
    conftest._REAL_STORE_DIRS = (os.fspath(directory) + os.sep,)
    try:
        yield lambda: list(conftest._store_writes[before:])
    finally:
        conftest._REAL_STORE_DIRS = original
        del conftest._store_writes[before:]


def two_directories(tmp_path: Path) -> tuple[Path, Path]:
    """One directory the guard watches and one it does not."""
    watched, other = tmp_path / "audian", tmp_path / "elsewhere"
    watched.mkdir()
    other.mkdir()
    return watched, other


def test_a_write_this_process_makes_is_recorded_against_the_test_that_made_it(tmp_path):
    watched, _ = two_directories(tmp_path)
    target = watched / "settings.json"

    with watching(watched) as recorded:
        target.write_text("{}")
        entries = recorded()

    assert [path for path, _ in entries] == [os.fspath(target)]
    assert entries[0][1].endswith(
        "::test_a_write_this_process_makes_is_recorded_against_the_test_that_made_it"
    )


def test_reading_a_store_is_not_writing_it(tmp_path):
    watched, _ = two_directories(tmp_path)
    target = watched / "settings.json"
    target.write_text("{}")

    with watching(watched) as recorded:
        assert target.read_text() == "{}"
        assert recorded() == []


def test_an_atomic_replace_is_caught_although_it_lands_under_another_name(tmp_path):
    """The one that matters: `replace_atomically` never opens the target.

    It writes `settings.json.tmp` beside it and renames over.  A guard
    watching only `open` would see the temporary name, and every settings
    write there is would go unnoticed.
    """
    watched, _ = two_directories(tmp_path)
    target = watched / "settings.json"

    with watching(watched) as recorded:
        temporary = watched / "settings.json.tmp"
        temporary.write_text("{}")
        os.replace(temporary, target)
        paths = [path for path, _ in recorded()]

    assert os.fspath(target) in paths


def test_deleting_a_store_is_caught_although_it_opens_nothing(tmp_path):
    watched, _ = two_directories(tmp_path)
    target = watched / "recent.json"
    target.write_text("[]")

    with watching(watched) as recorded:
        target.unlink()
        entries = recorded()

    assert [path for path, _ in entries] == [os.fspath(target)]


def test_a_write_outside_the_watched_directories_is_ignored(tmp_path):
    watched, other = two_directories(tmp_path)

    with watching(watched) as recorded:
        (other / "settings.json").write_text("{}")
        assert recorded() == []


def test_another_process_writing_there_is_not_this_suites_doing(tmp_path):
    """The false failure this guard replaced, reproduced deliberately.

    On 2026-09-01 the reader had audian open on their own recordings while
    the suite ran.  The GUI saved a preference at 21:11:08, inside a run that
    spanned 21:02 to 21:16, and the previous guard -- which compared the
    file's content against its content at session start -- reported that the
    suite had written the reader's settings.  It had not.  An audit hook sees
    only its own interpreter, so the same write is now correctly invisible.
    """
    watched, _ = two_directories(tmp_path)
    target = watched / "settings.json"

    with watching(watched) as recorded:
        subprocess.run(
            [sys.executable, "-c",
             f"from pathlib import Path; Path({os.fspath(target)!r}).write_text('{{}}')"],
            check=True,
        )
        assert target.is_file()
        assert recorded() == []
