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

Every test here used to be gated on that session being present under
``/home/weygoldt``, so the module ran on one machine and reported `skipped`
everywhere else.  `write_split_recording` builds the same shape in `tmp_path`
-- three parts of one length and a short one last, stamped the way a recorder
stamps them -- and the replica reproduces the mechanism rather than merely
resembling it: stock thunderlab returns 288,000 of its 312,000 frames, which is
the same drop, three orders of magnitude smaller.  The real session is still
here, behind ``--realdata``, because the numbers in the paragraph above are
worth being able to check against the recording they were measured on.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "tests"))

from audian import data as audian_data  # noqa: E402
from audian.data import file_frames, open_files  # noqa: E402
from test_session import write_split_recording  # noqa: E402


#: The four-file split session this bug was found on, if it is on this machine.
EXP3 = Path("/home/weygoldt/wrk/analyses/fakefish/experiments/exp3")
EXP3_FILES = sorted(EXP3.glob("DR0000_00*.wav"))
EXP3_FRAMES = 173_809_152

needs_exp3 = pytest.mark.skipif(
    len(EXP3_FILES) != 4, reason="the four-file exp3 session is not on this machine"
)


@pytest.fixture(scope="module")
def split(tmp_path_factory):
    """Four parts on disk, the last one short, stamped as a recorder stamps them."""
    return write_split_recording(tmp_path_factory.mktemp("split"))


@pytest.fixture(scope="module")
def split_gap(tmp_path_factory):
    """The same recording with a real discontinuity at its first join."""
    return write_split_recording(
        tmp_path_factory.mktemp("split-gap"), gaps=(2.0, 0.032, -0.12)
    )


@pytest.fixture(scope="module")
def single(tmp_path_factory):
    """One part, to prove the multi-file path did not break the ordinary one."""
    return write_split_recording(
        tmp_path_factory.mktemp("single"), seconds=(1.0,), gaps=()
    )


def paths_of(recording):
    return [str(p) for p in recording.paths]


# --- the assertion this whole module exists to make -------------------------


def test_a_split_recording_keeps_its_final_short_file(split):
    """Every frame on disk has to come back.

    The last part is short because it is the last one, which is the only
    reason the loader ever dropped it.
    """
    loader = open_files(paths_of(split), 60.0, 10.0)
    try:
        assert loader.frames == split.total_frames, (
            f"got {loader.frames}, expected {split.total_frames}"
        )
        assert len(loader.file_paths) == len(split.paths)
        assert loader.frames / loader.rate == pytest.approx(
            sum(split.frames) / split.rate
        )
    finally:
        loader.close()


def test_the_loader_would_drop_the_tail_if_it_were_left_to_itself(split):
    """The replica reproduces the bug, not merely the shape of it.

    Without this the module proves that `open_files` returns everything on a
    recording nothing would have taken anything from -- which is a test that
    cannot fail.  Stock `DataLoader` at its default one-second tolerance drops
    the short last part here exactly as it drops exp3's final 825 s, and
    `audian.data.open_files` differs from it in precisely the one line that
    widens `_max_time_diff`.
    """
    from thunderlab.dataloader import DataLoader

    stock = DataLoader()
    stock.open_multiple(paths_of(split), 60.0, 10.0, verbose=0)
    try:
        dropped = split.total_frames - stock.frames
        assert dropped == split.frames[-1], (
            "the replica has stopped reproducing the drop this module guards: "
            f"stock returned {stock.frames} of {split.total_frames}"
        )
    finally:
        stock.close()


def test_the_file_headers_agree_with_what_the_loader_returns(split):
    """`file_frames` is the authority the loader is checked against."""
    per_file = file_frames(paths_of(split))
    assert per_file == list(split.frames)
    assert sum(per_file) == split.total_frames
    assert per_file[-1] < per_file[0], "the last part must be the short one"


def test_the_tail_of_a_split_recording_actually_reads(split):
    """A frame count is not the same as readable audio.

    The last part is the one the loader used to drop, so its last samples are
    the ones most worth proving reachable.
    """
    import numpy as np

    loader = open_files(paths_of(split), 60.0, 10.0)
    try:
        want = min(int(split.rate), split.frames[-1])
        block = loader[split.total_frames - want : split.total_frames, :]
        assert block.shape[0] == want
        assert np.isfinite(block).all()
    finally:
        loader.close()


def test_a_part_begins_where_the_frames_before_it_end(split):
    """The timeline is cumulative frames and nothing else.

    Not the file names, not the stamps -- which here disagree with a tidy
    layout by the gaps the recorder left.
    """
    loader = open_files(paths_of(split), 60.0, 10.0)
    try:
        starts, running = [], 0
        for count in split.frames:
            starts.append(running)
            running += count
        assert list(loader.start_indices) == starts
    finally:
        loader.close()


def test_equal_length_files_from_a_close_time_recorder_are_not_reported_as_gaps(split):
    """The join report must not cry wolf on an ordinary TASCAM session.

    Its timestamps are close times, so measured against part 0's own timestamp
    every join lines up.  A warning on every session is a warning nobody
    reads, which would leave a real discontinuity indistinguishable from the
    noise.
    """
    loader = open_files(paths_of(split), 60.0, 10.0)
    try:
        assert list(audian_data.join_gaps(loader)) == []
    finally:
        loader.close()


def test_a_join_the_recorder_really_lost_time_at_is_reported(split_gap):
    """And the report must still fire when there is something to report.

    A test that only ever sees the quiet case cannot tell a guard that is
    correct from one that is switched off.
    """
    loader = open_files(paths_of(split_gap), 60.0, 10.0)
    try:
        reported = list(audian_data.join_gaps(loader))
        assert reported, "a two-second hole between parts has to be named"
    finally:
        loader.close()


# --- the ordinary single-file path ------------------------------------------


def test_a_single_file_session_is_unaffected(single):
    loader = open_files(str(single.paths[0]), 60.0, 10.0)
    try:
        assert loader.frames == single.total_frames
    finally:
        loader.close()


def test_a_one_element_list_is_still_a_single_file(single):
    loader = open_files([str(single.paths[0])], 60.0, 10.0)
    try:
        assert loader.frames == single.total_frames
    finally:
        loader.close()


# --- the guard -------------------------------------------------------------


def test_a_loader_that_comes_back_short_is_an_error_not_a_warning(split, monkeypatch):
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
        open_files(paths_of(split), 60.0, 10.0)
    message = str(excinfo.value)
    assert str(split.total_frames) in message
    assert split.names[-1] in message, "the error must name what went missing"


# --- the recording the numbers above were measured on -----------------------


@pytest.mark.realdata
@needs_exp3
def test_the_real_exp3_session_comes_back_whole():
    """The original evidence, kept so the figures in the docstring can be checked.

    Everything it asserts is covered synthetically above; what it adds is that
    the numbers are the real session's, on the recording where the drop was
    found.
    """
    per_file = file_frames(EXP3_FILES)
    assert per_file == [44_734_464, 44_734_464, 44_734_464, 39_605_760]
    assert sum(per_file) == EXP3_FRAMES

    loader = open_files(EXP3_FILES, 60.0, 10.0)
    try:
        assert loader.frames == EXP3_FRAMES
        assert len(loader.file_paths) == 4
        assert loader.frames / loader.rate == pytest.approx(3621.02, abs=0.01)
        assert list(audian_data.join_gaps(loader)) == []
    finally:
        loader.close()
