"""Tests for the provenance guard on a recording written as several files.

Runs without Qt::

    .venv/bin/python -m pytest tests/test_alignment.py -q

`SplitCoverage` is the check that nothing else could make.  A bundle whose
recording is four WAVs is one timeline, and opening a proper subset of those
files puts every mark in the wrong minute while every other check passes: the
open file IS one of the four the bundle names, and the frame check accepts a
single file's own count on purpose.  What is asserted here is that the subset
is recognised BY NAME, that it is refused rather than corrected, and that the
refusal says which files are open, which are missing, and what to do.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "tests"))

from audian.alignment import SessionMeta  # noqa: E402

from test_session import (  # noqa: E402
    SPLIT_NAMES,
    simple,
    split_alignment,
)


@pytest.fixture
def fit(tmp_path):
    """The fit of a session written as four 10 s files, exp3's shape."""
    return simple(tmp_path, alignment=split_alignment()).meta.alignment


def test_one_file_of_a_split_recording_is_a_partial_open(fit):
    """The failure the guard exists for, and the one every other check passed.

    exp3's DR0000_0090.wav alone gave `RecordingCheck(name=True, frames=True,
    channel=True, problems=())` and 124 marks drawn 1764 s from where they
    belong, each of them entirely plausible.
    """
    coverage = fit.coverage(["/somewhere/part2.wav"])
    assert coverage.partial is True
    assert coverage.opened == ("part2.wav",)
    assert coverage.missing == ("part0.wav", "part1.wav", "part3.wav")


def test_the_whole_recording_open_is_not_a_partial_open(fit):
    assert fit.coverage([f"/x/{n}" for n in SPLIT_NAMES]).partial is False


def test_the_files_are_matched_by_name_and_never_by_frame_count(fit):
    """By NAME, like every other provenance check in this module.

    The frame counts cannot answer this question at all -- all four files of
    exp3 hold the same 44 734 464 frames, so counting them would call file 2
    file 0 -- and the bundle travels beside the recording, so the directory
    says nothing either.
    """
    coverage = fit.coverage(["/elsewhere/entirely/part1.wav"])
    assert coverage.opened == ("part1.wav",)
    assert coverage.extra == ()
    assert fit.coverage(["/x/part1.wav", "/x/stray.wav"]).extra == ("stray.wav",)


def test_a_bundle_whose_recording_is_one_file_never_reports_a_partial_open(tmp_path):
    """A single-file recording is either open or it is not, and the name
    check owns that question."""
    fit = simple(tmp_path).meta.alignment
    assert fit.coverage(["/x/rec.wav"]).partial is False
    assert fit.coverage(["/x/other.wav"]).partial is False


def test_none_of_the_declared_files_open_is_left_to_the_name_check(fit):
    """Refusing it here too would replace a message about the right thing
    ("this bundle was fitted against another recording") with one about the
    wrong thing."""
    coverage = fit.coverage(["/x/somethingelse.wav"])
    assert coverage.opened == ()
    assert coverage.partial is False


def test_the_refusal_says_how_far_out_the_marks_would_be(fit):
    """Computable, and stated -- but never applied.

    The offset is right there in `recording_file_frames`, and re-basing the
    marks on it would be a quiet repair resting on four declared numbers this
    viewer has never measured.  Saying the number is what lets a reader
    recognise the situation; using it is what makes a viewer subtly wrong.
    """
    coverage = fit.coverage(["/x/part2.wav"])
    assert coverage.shift_s == pytest.approx(20.0)
    message = coverage.message()
    assert "20.000 s" in message
    assert "Nothing is drawn, and no mark is re-based to fit." in message


def test_the_first_file_alone_is_refused_like_any_other_subset(fit):
    """Its marks would be right only by accident, and only for its own span."""
    coverage = fit.coverage(["/x/part0.wav"])
    assert coverage.partial is True
    assert coverage.shift_s == 0.0
    assert "only by accident" in coverage.message()


def test_the_refusal_names_the_open_files_the_missing_ones_and_the_remedy(fit):
    message = fit.coverage(["/x/part2.wav"]).message()
    assert "open:    part2.wav" in message
    assert "missing: part0.wav, part1.wav, part3.wav" in message
    assert "audian part0.wav part1.wav part2.wav part3.wav" in message


def test_the_subject_still_reads_as_a_whole_sentence_on_its_own(fit):
    """It is handed to `AnnotationLayer.recording_mismatch`, which builds a
    sentence around it, so it has to name all four files by itself."""
    subject = fit.coverage(["/x/part2.wav"]).subject()
    assert subject == (
        "all 4 of part0.wav, part1.wav, part2.wav, part3.wav as one recording"
    )


def test_the_file_start_times_are_the_joins_with_the_recording_start(fit):
    """The starts and the join markers are the same arithmetic, once."""
    assert fit.file_starts_s == (0.0, 10.0, 20.0, 30.0)
    assert fit.file_starts_s[1:] == fit.join_times_s


def test_a_bundle_that_declares_no_file_frames_says_nothing_about_the_shift(
    tmp_path,
):
    """`None` is not zero: a bundle that never said how long its files are
    cannot say where the open one starts, and the message has to fall back on
    what it does know."""
    fit = simple(
        tmp_path, alignment=split_alignment(recording_file_frames=None)
    ).meta.alignment
    coverage = fit.coverage(["/x/part2.wav"])
    assert coverage.partial is True
    assert coverage.shift_s is None
    assert "cannot even be stated" in coverage.message()
    assert "only by accident" not in coverage.message()


#: What exp3's own `*_metadata.toml` declares.  Copied here rather than read
#: from the session, because `coverage()` reads the TOML and nothing else: the
#: reproduction from the field needs the writer's numbers, not its 1.3 GB of
#: recordings.
EXP3_NAMES = ("DR0000_0088.wav", "DR0000_0089.wav", "DR0000_0090.wav",
              "DR0000_0091.wav")
EXP3_FILE_FRAMES = (44_734_464, 44_734_464, 44_734_464, 39_605_760)


def test_the_exp3_bundle_refuses_its_third_file_on_its_own(tmp_path):
    """The reproduction from the field, on the numbers the writer wrote.

    Opening file 3 of 4 alone puts every mark 1,863.936 s -- thirty-one
    minutes -- out of place while every other check passes, because the open
    file really is one of the four the bundle names.
    """
    fit = simple(
        tmp_path,
        alignment=split_alignment(
            recording_files="[" + ", ".join(f'"{n}"' for n in EXP3_NAMES) + "]",
            recording_file_frames="["
            + ", ".join(str(f) for f in EXP3_FILE_FRAMES)
            + "]",
            recording_frames=str(sum(EXP3_FILE_FRAMES)),
        ),
    ).meta.alignment
    coverage = fit.coverage(["/x/DR0000_0090.wav"])
    assert coverage.partial is True
    assert coverage.opened == ("DR0000_0090.wav",)
    assert coverage.shift_s == pytest.approx(1863.936)


@pytest.mark.realdata
def test_the_real_exp3_bundle_refuses_its_third_file_on_its_own(tmp_path):
    """The same reproduction, read off the session itself.

    Kept because it is the only thing here that checks the numbers above are
    still what the writer writes.
    """
    path = Path(
        "/home/weygoldt/wrk/analyses/fakefish/experiments/exp3/PULS0005_metadata.toml"
    )
    if not path.exists():
        pytest.skip("exp3 is not on this machine")
    fit = SessionMeta.from_toml(path).alignment
    assert fit.recording_files == EXP3_NAMES
    assert fit.recording_file_frames == EXP3_FILE_FRAMES
    coverage = fit.coverage([path.parent / "DR0000_0090.wav"])
    assert coverage.partial is True
    assert coverage.opened == ("DR0000_0090.wav",)
    assert coverage.shift_s == pytest.approx(1863.936)
