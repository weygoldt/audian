"""Tests for :mod:`audian.data`'s multi-file loading.

The bug these exist for: `thunderlab.DataLoader` decides whether consecutive
files are one recording by comparing each file's metadata timestamp against
the end of the previous one, and on a mismatch it ``break``s -- dropping that
file **and every file after it**.  A TASCAM writes bext ``OriginationTime`` as
the moment a file was *closed*, so expected and actual are both shifted by one
file's duration and the two errors cancel -- for exactly as long as every file
is the same length.  They stop cancelling at the last file of a session, which
is the only short one.  The loader therefore drops precisely the tail, and
only the tail, and says nothing about it at the default verbosity.

Measured on the four-file exp3 session: 173,809,152 frames on disk,
134,203,392 returned -- the final 825.12 s absent.  A viewer that quietly
shows 46 minutes of a 60 minute session looks completely normal, which is what
makes it worth a test file of its own.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from audian import data as audian_data  # noqa: E402
from audian.data import file_frames, open_files  # noqa: E402


#: The four-file split session this bug was found on, if it is on this machine.
EXP3 = Path("/home/weygoldt/wrk/analyses/fakefish/experiments/exp3")
EXP3_FILES = sorted(EXP3.glob("DR0000_00*.wav"))
EXP3_FRAMES = 173_809_152

#: A single-file session, to prove the multi-file path did not break the
#: ordinary one.
EXP2 = Path("/home/weygoldt/wrk/analyses/fakefish/experiments/exp2/DR0000_0087.wav")
EXP2_FRAMES = 29_140_992

needs_exp3 = pytest.mark.skipif(
    len(EXP3_FILES) != 4, reason="the four-file exp3 session is not on this machine"
)
needs_exp2 = pytest.mark.skipif(
    not EXP2.is_file(), reason="the exp2 recording is not on this machine"
)


@needs_exp3
def test_a_split_recording_keeps_its_final_short_file():
    """The assertion this whole module exists to make.

    Four files, the last one short because it is the last one.  Every frame on
    disk has to come back, or the viewer is showing 46 minutes of a 60 minute
    recording with nothing on screen to say so.
    """
    loader = open_files(EXP3_FILES, 60.0, 10.0)
    try:
        assert loader.frames == EXP3_FRAMES, (
            f"got {loader.frames}, expected {EXP3_FRAMES}"
        )
        assert len(loader.file_paths) == 4
        assert loader.frames / loader.rate == pytest.approx(3621.02, abs=0.01)
    finally:
        loader.close()


@needs_exp3
def test_the_file_headers_agree_with_what_the_loader_returns():
    """`file_frames` is the authority the loader is checked against."""
    per_file = file_frames(EXP3_FILES)
    assert per_file == [44_734_464, 44_734_464, 44_734_464, 39_605_760]
    assert sum(per_file) == EXP3_FRAMES


@needs_exp3
def test_the_tail_of_a_split_recording_actually_reads():
    """A frame count is not the same as readable audio.

    The last file is the one the loader used to drop, so the last second of it
    is the sample most worth proving reachable.
    """
    import numpy as np

    loader = open_files(EXP3_FILES, 60.0, 10.0)
    try:
        block = loader[EXP3_FRAMES - 48_000 : EXP3_FRAMES, :]
        assert block.shape[0] == 48_000
        assert np.isfinite(block).all()
    finally:
        loader.close()


@needs_exp3
def test_equal_length_files_from_a_close_time_recorder_are_not_reported_as_gaps():
    """The join report must not cry wolf on an ordinary TASCAM session.

    Its timestamps are close times, so measured against file 0's own timestamp
    every join lines up.  A warning on every session is a warning nobody
    reads, which would leave a real discontinuity indistinguishable from the
    noise.
    """
    loader = open_files(EXP3_FILES, 60.0, 10.0)
    try:
        assert list(audian_data.join_gaps(loader)) == []
    finally:
        loader.close()


@needs_exp2
def test_a_single_file_session_is_unaffected():
    loader = open_files(os.fspath(EXP2), 60.0, 10.0)
    try:
        assert loader.frames == EXP2_FRAMES
    finally:
        loader.close()


@needs_exp2
def test_a_one_element_list_is_still_a_single_file():
    loader = open_files([EXP2], 60.0, 10.0)
    try:
        assert loader.frames == EXP2_FRAMES
    finally:
        loader.close()


@needs_exp3
def test_a_loader_that_comes_back_short_is_an_error_not_a_warning(monkeypatch):
    """The guard, tested by making the loader drop a file on purpose.

    This is the branch that must never regress: everything the loader returns
    is *correct*, which is exactly why a short return has to stop the open
    rather than colour a message somewhere.
    """
    real = audian_data.DataLoader

    class Truncating(real):
        def open_multiple(self, filepaths, *args, **kwargs):
            # drop the tail, the way the timestamp check used to
            return super().open_multiple(list(filepaths)[:-1], *args, **kwargs)

    monkeypatch.setattr(audian_data, "DataLoader", Truncating)
    with pytest.raises(ValueError) as excinfo:
        open_files(EXP3_FILES, 60.0, 10.0)
    message = str(excinfo.value)
    assert str(EXP3_FRAMES) in message
    assert "DR0000_0091.wav" in message, "the error must name what went missing"
