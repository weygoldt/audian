"""Publish a file all at once, or not at all.

Three places in audian write a file the reader cannot afford to lose half
of: their labels, their preferences -- which hold the label vocabulary, so
they are not only preferences -- and the bands plugin's two sidecars.  All
three had written to a temporary neighbour and `os.replace`d it, which is
the right shape and was implemented three times with three different levels
of care.

Two of them named the temporary ``<name>.tmp``, with no pid and no
randomness.  That is safe against a crash and not against a second writer:
two audian processes on one recording, or two browsers each with their own
zero-delay save timer, interleave so that one truncates the temporary while
the other is mid-write, and the second `os.replace` then publishes the
first's partial content under the real name -- with both writers reporting
success.  `frequencybands.bands` already got this right; this is that
implementation, with the durability the other two had and it did not.

Not imported by `frequencybands.bands` yet, which is where it came from.
That module is one of only four in the tree that import without dragging in
PySide6, and reaching into `audian` would end that, because
`audian/__init__` imports the main window.  When that is fixed the third
copy goes.

`os.replace` is atomic within a filesystem and raises across one, so the
temporary is always a neighbour of its target: a recording on a mounted
drive with ``/tmp`` on the root disk is the ordinary case here.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable


def _fsync_path(path: Path) -> None:
    """Flush one path to the disk, ignoring platforms that cannot."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        # a directory fsync is not permitted on every platform, and a
        # failure here costs durability rather than correctness
        pass
    finally:
        os.close(fd)


def replace_atomically(path: Path, write: Callable[[Path], None]) -> None:
    """Write through `write` and move the result onto `path`.

    `write` is handed the temporary and must write the whole file to it.
    A reader either sees the entire previous file or the entire new one.

    Durability is the part worth spelling out, because "atomic" is often
    taken to cover it and does not.  `os.replace` orders the rename against
    other renames; it says nothing about whether the *bytes* reached the
    disk.  Without the first fsync the new file survives a killed process
    and not a power cut, and without the second the rename itself is the
    thing that may not survive -- which is how a directory ends up naming a
    file whose contents were never written.
    """
    path = Path(path)
    tmp = path.with_name(f".{path.name}.tmp{os.getpid()}")
    try:
        write(tmp)
        _fsync_path(tmp)
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    _fsync_path(path.parent)
