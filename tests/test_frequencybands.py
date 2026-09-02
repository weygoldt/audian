"""The frequency band plugin: the store, the tracker, the importer, the tab.

Most of this needs no window.  `bands`, `tracking` and `wavetracker` import no
Qt, which is the point of splitting them out, and the invariants worth pinning
are all in them: that an undo restores exactly, that a merge keeps every
vertex, that a damaged file is reported rather than raised, and that an
inconsistent wavetracker directory is refused rather than guessed at.  Each of
those is a way `wavetracker.EODsorter` lost somebody's data, so each gets a
test naming it.

The Qt half is checked at the seams only -- that the plugin registers, that
opening the tab puts one overlay on each spectrogram lane, and that closing it
takes them off again -- because that is where a plugin breaks against a host
it does not control.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from audian_plugins.frequencybands import bands as B  # noqa: E402
from audian_plugins.frequencybands import tracking as T  # noqa: E402
from audian_plugins.frequencybands import wavetracker as W  # noqa: E402


def steady(t0=0.0, t1=10.0, n=101, hz=700.0):
    times = np.linspace(t0, t1, n)
    return times, np.full(n, float(hz))


# ---------------------------------------------------------------- the store


def test_a_band_needs_both_coordinates():
    with pytest.raises(ValueError, match="a vertex needs both"):
        B.Band(1, [0.0, 1.0], [700.0])


def test_a_band_needs_a_vertex():
    with pytest.raises(ValueError, match="at least one vertex"):
        B.Band(1, [], [])


def test_a_band_is_sorted_into_time_order():
    band = B.Band(1, [2.0, 0.0, 1.0], [3.0, 1.0, 2.0])
    assert list(band.times) == [0.0, 1.0, 2.0]
    assert list(band.freqs) == [1.0, 2.0, 3.0], "the pairing must survive the sort"


def test_ids_are_never_reused():
    """A deleted id must not come back on a different band.

    `EODsorter.new_assign` took ``max(ident_v) + 1``, so the id of a deleted
    identity was re-issued to the next new one and a note saying "band 7 is
    the female" silently came to mean another animal.
    """
    store = B.BandSet()
    store.add(*steady())
    store.delete(1)
    store.add(*steady())
    assert store.ids() == [2]


def test_undo_restores_a_deleted_band_exactly():
    store = B.BandSet()
    times, freqs = steady()
    store.add(times, freqs, category="male", note="the loud one")
    before = store.get(1)
    store.delete(1)
    assert store.ids() == []
    store.undo()
    after = store.get(1)
    assert after.category == "male" and after.note == "the loud one"
    assert np.array_equal(after.times, before.times)
    assert np.array_equal(after.freqs, before.freqs)


def test_a_split_keeps_every_vertex_and_undoes_whole():
    store = B.BandSet()
    store.add(*steady(n=101))
    store.split(1, 5.0)
    kept = sum(len(b) for b in store)
    assert kept == 101, "a cut must not consume the vertex it cuts at"
    assert len(store) == 2
    store.undo()
    assert store.ids() == [1] and len(store.get(1)) == 101


def test_a_split_outside_the_band_is_refused():
    store = B.BandSet()
    store.add(*steady(0.0, 10.0))
    with pytest.raises(ValueError, match="would leave one side of it empty"):
        store.split(1, 50.0)


def test_a_merge_keeps_every_vertex_even_when_they_overlap():
    """The invariant this plugin exists for.

    `EODsorter.connect` set to NaN every detection the first trace held at a
    time step the second also held -- discarding real data to make the result
    a function of time, and doing it silently, exactly when a reader reached
    for the tool to repair an overlap.
    """
    store = B.BandSet()
    store.add(*steady(0.0, 10.0, 101, 700.0))
    store.add(*steady(5.0, 15.0, 101, 705.0))
    before = sum(len(b) for b in store)
    store.merge([1, 2])
    assert len(store) == 1
    assert sum(len(b) for b in store) == before == 202


def test_a_merge_says_what_it_would_decide():
    store = B.BandSet()
    store.add(*steady(0.0, 10.0), category="male")
    store.add(*steady(5.0, 15.0), category="female")
    notes = B.merge_conflicts(list(store))
    assert any("different labels" in n for n in notes)
    assert any("overlap by" in n for n in notes)


def test_an_unambiguous_merge_asks_nothing():
    store = B.BandSet()
    store.add(*steady(0.0, 10.0), channel=0)
    store.add(*steady(20.0, 30.0), channel=0)
    assert B.merge_conflicts(list(store)) == []


def test_labelling_a_selection_is_one_undo_step():
    """Every multi-band edit is one step; this was the one that was not."""
    store = B.BandSet()
    for i in range(4):
        store.add(*steady(t0=10.0 * i, t1=10.0 * i + 5.0))
    store.forget_history()
    store.set_category_many(store.ids(), "male")
    assert [b.category for b in store] == ["male"] * 4
    store.undo()
    assert [b.category for b in store] == [""] * 4
    assert not store.can_undo(), "it must have been a single step"


def test_redo_is_dropped_when_a_new_edit_branches():
    store = B.BandSet()
    store.add(*steady())
    store.add(*steady(20.0, 30.0))
    store.delete(1)
    store.undo()
    assert store.can_redo()
    store.delete(2)
    assert not store.can_redo(), "a new edit must not leave a stale redo"


def test_history_is_bounded():
    store = B.BandSet()
    store.add(*steady())
    for i in range(B.HISTORY_DEPTH + 50):
        store.set_note(1, f"note {i}")
    assert len(store._undo) == B.HISTORY_DEPTH


# ----------------------------------------------------------------- the files


def test_a_round_trip_is_exact(tmp_path):
    store = B.BandSet()
    times, freqs = steady()
    store.add(times, freqs + np.arange(times.size), channel=2, category="male")
    store.add(*steady(20.0, 30.0), category="female", note="quiet")
    recording = tmp_path / "rec.wav"
    B.write(store, recording)
    back, complaints = B.read(recording)
    assert complaints == []
    assert back.ids() == store.ids()
    for a, b in zip(store, back):
        assert (a.bid, a.category, a.note, a.channel) == (
            b.bid, b.category, b.note, b.channel,
        )
        assert np.allclose(a.times, b.times) and np.allclose(a.freqs, b.freqs)


def test_saving_clears_the_unsaved_mark(tmp_path):
    store = B.BandSet()
    store.add(*steady())
    assert store.is_dirty()
    B.write(store, tmp_path / "rec.wav")
    assert not store.is_dirty()
    store.delete(1)
    assert store.is_dirty()


def test_an_empty_set_still_writes_both_files(tmp_path):
    """Deleting every band must survive a reopen.

    Leaving the previous files, or removing them, both mean the reader finds
    the bands back where they deliberately removed them -- which from outside
    is indistinguishable from the save having failed.
    """
    recording = tmp_path / "rec.wav"
    B.write(B.BandSet(), recording)
    assert B.csv_path(recording).exists() and B.npz_path(recording).exists()
    back, complaints = B.read(recording)
    assert len(back) == 0 and complaints == []


def test_a_damaged_geometry_file_is_reported_not_raised(tmp_path):
    recording = tmp_path / "rec.wav"
    store = B.BandSet()
    store.add(*steady())
    B.write(store, recording)
    B.npz_path(recording).write_bytes(b"not an npz at all")
    back, complaints = B.read(recording)
    assert len(back) == 0
    assert any("could not be read" in c for c in complaints)


def test_a_csv_naming_a_band_the_geometry_lacks_is_reported(tmp_path):
    recording = tmp_path / "rec.wav"
    store = B.BandSet()
    store.add(*steady())
    B.write(store, recording)
    with B.csv_path(recording).open("a", encoding="utf-8") as stream:
        stream.write("99,ghost,,0.0,1.0,1.0,1.0,1.0,2,\n")
    back, complaints = B.read(recording)
    assert back.ids() == [1]
    assert any("which the geometry does not contain" in c for c in complaints)


def test_a_non_finite_band_id_costs_its_row_and_not_the_geometry(tmp_path):
    """"nan" parses as a float, and `int(nan)` raises out of `read`.

    `read`'s guard named only OSError and csv.Error, so the exception left
    the reader and took the recording's bands with it.  The geometry is
    already loaded by then; a bad claims row must cost at most its label.
    """
    recording = tmp_path / "rec.wav"
    store = B.BandSet()
    store.add(*steady())
    B.write(store, recording)
    with B.csv_path(recording).open("a", encoding="utf-8") as stream:
        stream.write("nan,ghost,,0.0,1.0,1.0,1.0,1.0,2,\n")
    back, complaints = B.read(recording)
    assert back.ids() == [1]
    assert len(back) == 1


def test_a_labels_only_sidecar_is_reported(tmp_path):
    recording = tmp_path / "rec.wav"
    B.csv_path(recording).write_text("band,category\n1,male\n", encoding="utf-8")
    back, complaints = B.read(recording)
    assert len(back) == 0
    assert any("and no shape" in c for c in complaints)


def test_a_newer_format_is_refused_rather_than_misread(tmp_path):
    recording = tmp_path / "rec.wav"
    store = B.BandSet()
    store.add(*steady())
    B.write(store, recording)
    with np.load(B.npz_path(recording)) as data:
        fields = {k: data[k] for k in data.files}
    fields["version"] = np.array(B.FORMAT_VERSION + 1, dtype=np.int64)
    np.savez_compressed(B.npz_path(recording), **fields)
    back, complaints = B.read(recording)
    assert len(back) == 0
    assert any("newer version" in c for c in complaints)


def test_a_save_leaves_no_temporary_behind(tmp_path):
    recording = tmp_path / "rec.wav"
    store = B.BandSet()
    store.add(*steady())
    B.write(store, recording)
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".")]


def test_the_sidecar_never_collides_with_the_editable_labels():
    """Two different files must not want the same name.

    `labels.SIDECAR_SUFFIX` is audian's own; a plugin writing over it would
    take a recording's hand-drawn labels with it.
    """
    from audian import labels

    recording = Path("/tmp/rec.wav")
    assert B.csv_path(recording) != labels.sidecar_path(recording)


# --------------------------------------------------------------- the tracker


def spectrogram_of(tones, duration=4.0, rate=200.0, n_freqs=200, noise=1.0):
    """A synthetic (times, freqs, power_db) block holding steady tones."""
    times = np.arange(0.0, duration, 1.0 / rate)
    freqs = np.linspace(0.0, 1000.0, n_freqs)
    rng = np.random.default_rng(4)
    power = rng.normal(0.0, noise, size=(times.size, freqs.size))
    for hz, level, t_on, t_off in tones:
        i = int(np.argmin(np.abs(freqs - hz)))
        live = (times >= t_on) & (times < t_off)
        power[np.ix_(live, [i])] += level
    return times, freqs, power


def test_the_tracker_finds_a_steady_tone():
    times, freqs, power = spectrogram_of([(500.0, 40.0, 0.0, 4.0)])
    found = T.track(times, freqs, power, threshold_db=10.0, tolerance_hz=20.0,
                    max_gap_s=0.1, min_duration_s=0.5)
    assert len(found) == 1
    t, f = found[0]
    assert abs(np.median(f) - 500.0) < 10.0
    assert t[0] < 0.1 and t[-1] > 3.8


def test_the_tracker_separates_two_tones():
    times, freqs, power = spectrogram_of(
        [(300.0, 40.0, 0.0, 4.0), (700.0, 40.0, 0.0, 4.0)]
    )
    found = T.track(times, freqs, power, threshold_db=10.0, tolerance_hz=20.0,
                    max_gap_s=0.1, min_duration_s=0.5)
    centres = sorted(float(np.median(f)) for _t, f in found)
    assert len(found) == 2
    assert abs(centres[0] - 300.0) < 10.0 and abs(centres[1] - 700.0) < 10.0


def test_a_gap_shorter_than_the_frame_spacing_still_links():
    """The frame spacing is not the band going unseen.

    Comparing bare elapsed time against `max_gap_s` made the spectrogram's
    own hop count as a gap, so any gap below one hop closed every band after
    a single frame and the tracker returned nothing -- with no setting on the
    panel that explained why.
    """
    frames = [(i * 0.5, np.array([500.0])) for i in range(10)]
    assert T.link(frames, tolerance_hz=5.0, max_gap_s=0.0, min_duration_s=1.0)


def test_a_tone_that_stops_and_restarts_becomes_two_bands():
    """The conservative way round: merging is one gesture, unbridging is not."""
    times, freqs, power = spectrogram_of(
        [(500.0, 40.0, 0.0, 1.5), (500.0, 40.0, 2.5, 4.0)]
    )
    found = T.track(times, freqs, power, threshold_db=10.0, tolerance_hz=20.0,
                    max_gap_s=0.2, min_duration_s=0.5)
    assert len(found) == 2


def test_the_global_floor_keeps_the_silences_empty():
    """A per-frame threshold alone promotes the loudest noise of a quiet frame.

    Measured on real audio it turned 18 s of cricket song into 2484 bands.
    """
    times, freqs, power = spectrogram_of([(500.0, 40.0, 0.0, 2.0)])
    found = T.track(times, freqs, power, threshold_db=12.0, tolerance_hz=20.0,
                    max_gap_s=0.1, min_duration_s=0.3)
    assert len(found) == 1, f"the silent half produced {len(found) - 1} phantoms"


# ------------------------------------------------- the harmonic group finder


def harmonic_spectrogram(fundamentals, duration=8.0, rate=4000.0, nfft=4096,
                         n_harmonics=4, noise=0.01):
    """A real spectrogram of a synthetic signal, in *linear* power.

    A signal and then a transform of it, rather than spikes painted into an
    array: `harmonic_groups` estimates its own thresholds from the shape of
    the noise floor, and a floor of uniform random numbers with delta
    functions standing on it is not a shape any recording has -- it produced
    fundamentals at 248 and 386 Hz from one 62 Hz fish.
    """
    from thunderlab.powerspectrum import spectrogram

    t = np.arange(0.0, duration, 1.0 / rate)
    rng = np.random.default_rng(7)
    signal = rng.normal(0.0, noise, t.size)
    for f0 in fundamentals:
        for h in range(1, n_harmonics + 1):
            if f0 * h >= 0.45 * rate:
                break
            signal += (1.0 / h) * np.sin(2 * np.pi * f0 * h * t)
    freqs, times, spec = spectrogram(
        signal, rate, freq_resolution=rate / nfft, overlap_frac=0.5
    )
    return times, freqs, spec.T


def test_harmonics_are_available_here():
    """thunderfish is this plugin's extra; the suite pins that it loads."""
    assert T.harmonics_available(), "thunderfish is not installed"


def test_an_installation_without_thunderfish_still_finds_bands():
    """`pip install audian` must still curate; only `[bands]` curates well.

    The README says so and the Find combo disables the entry it cannot
    offer, so the absence has to be a reported state rather than an
    ImportError in front of a reader.
    """

    class Blocker:
        def find_spec(self, name, path=None, target=None):
            if name == "thunderfish" or name.startswith("thunderfish."):
                raise ImportError("thunderfish is not installed")
            return None

    blocker = Blocker()
    hidden = {m: sys.modules.pop(m) for m in list(sys.modules)
              if m.startswith("thunderfish")}
    sys.meta_path.insert(0, blocker)
    try:
        assert not T.harmonics_available()
        times, freqs, power = spectrogram_of([(500.0, 40.0, 0.0, 4.0)])
        found = T.track(times, freqs, power, threshold_db=10.0,
                        tolerance_hz=20.0, max_gap_s=0.1, min_duration_s=0.5)
        assert len(found) == 1, "the peak finder must still work"
    finally:
        sys.meta_path.remove(blocker)
        sys.modules.update(hidden)


def test_a_fish_with_harmonics_is_one_band_not_four():
    """The reason the harmonic finder is the default.

    `peaks_of_block` returns every strong peak, so one fish with four
    audible harmonics becomes four tracks and the reader's first job is
    deleting three of them.
    """
    pytest.importorskip("thunderfish")
    times, freqs, power = harmonic_spectrogram([62.0])
    frames = T.harmonic_frames(times, freqs, power, mains_hz=0.0,
                               min_hz=20.0, max_hz=1000.0)
    assert frames, "no fundamentals found at all"
    found = np.concatenate([hz for _t, hz in frames])
    # the fundamental itself, in most frames
    hits = np.abs(found - 62.0) < 3.0
    assert hits.sum() >= 0.5 * len(frames), f"62 Hz found in only {hits.sum()} frames"
    # and none of its own multiples reported as a fundamental of their own,
    # which is exactly what the peak finder does and why this is the default
    for h in (2, 3, 4):
        near = np.abs(found - 62.0 * h) < 3.0
        assert not near.any(), f"harmonic {h} came back as a fundamental"


def test_two_fish_are_two_fundamentals():
    pytest.importorskip("thunderfish")
    times, freqs, power = harmonic_spectrogram([62.0, 287.0])
    frames = T.harmonic_frames(times, freqs, power, mains_hz=0.0,
                               min_hz=20.0, max_hz=1000.0)
    # a gap wide enough to bridge the frames where the weaker fish's third
    # harmonic dips under the floor -- which is what the panel's Max gap is
    # for, and why its default is a second rather than a hop
    bands = T.link(frames, tolerance_hz=8.0, max_gap_s=2.0, min_duration_s=0.5)
    centres = sorted(float(np.median(f)) for _t, f in bands)
    assert len(bands) == 2, f"expected two bands, got {centres}"
    assert abs(centres[0] - 62.0) < 4.0 and abs(centres[1] - 287.0) < 6.0


def test_the_fundamental_is_not_pinned_to_the_frequency_grid():
    """A band must be able to move by less than a bin.

    `harmonic_groups` builds its fundamental from peaks that sit on bins, so
    without refinement it lands on a grid of ``df / n`` and a drifting fish
    is reported at one constant frequency -- the modulation is quantised
    away, and the line drawn from it climbs in visible steps.
    """
    pytest.importorskip("thunderfish")
    times, freqs, power = harmonic_spectrogram([62.0])
    frames = T.harmonic_frames(times, freqs, power, mains_hz=0.0,
                               min_hz=20.0, max_hz=1000.0)
    found = np.concatenate([hz for _t, hz in frames])
    near = found[np.abs(found - 62.0) < 3.0]
    assert near.size > 4
    df = float(freqs[1] - freqs[0])
    off_grid = np.abs(near / df - np.round(near / df))
    assert off_grid.max() > 1e-6, "every value sits exactly on a bin"


def test_refinement_never_moves_a_fundamental_more_than_half_a_bin():
    """A refinement that big means the harmonic order was wrong."""
    freqs = np.linspace(0.0, 1000.0, 1001)
    frame = np.zeros(freqs.size)
    frame[124] = 10.0
    group = np.array([[62.0, 1.0], [124.0, 9.0]])
    out = T.refine_fundamental(freqs, frame, group, 62.0)
    assert abs(out - 62.0) <= 0.5 * (freqs[1] - freqs[0])


def test_a_degenerate_group_refines_to_itself():
    freqs = np.linspace(0.0, 1000.0, 1001)
    frame = np.zeros(freqs.size)
    assert T.refine_fundamental(freqs, frame, np.zeros((0, 2)), 62.0) == 62.0
    assert T.refine_fundamental(freqs, frame, np.array([[0.0, 0.0]]), 0.0) == 0.0


def test_the_harmonic_finder_refuses_a_mismatched_block():
    pytest.importorskip("thunderfish")
    with pytest.raises(ValueError, match="but there are"):
        T.harmonic_frames(np.zeros(5), np.zeros(7), np.zeros((5, 9)))


def test_the_tracker_refuses_a_mismatched_block():
    with pytest.raises(ValueError, match="but there are"):
        T.track(np.zeros(5), np.zeros(7), np.zeros((5, 9)))


def test_a_cancelled_run_stops():
    from audian.pluginapi import Cancelled, CancelToken

    times, freqs, power = spectrogram_of([(500.0, 40.0, 0.0, 4.0)])
    token = CancelToken()
    token.cancel()
    with pytest.raises(Cancelled):
        T.track(times, freqs, power, token=token)


# -------------------------------------------------------------- the importer


def write_wavetracker(folder: Path, prefix="all_", n_frames=50, idents=(0, 1)):
    folder.mkdir(parents=True, exist_ok=True)
    times = np.arange(n_frames) * 0.5
    idx, fund, ident = [], [], []
    for i, name in enumerate(idents):
        idx.extend(range(n_frames))
        fund.extend(700.0 + 100.0 * i + np.zeros(n_frames))
        ident.extend([float(name)] * n_frames)
    np.save(folder / f"{prefix}times.npy", times)
    np.save(folder / f"{prefix}idx_v.npy", np.array(idx, dtype=np.int64))
    np.save(folder / f"{prefix}fund_v.npy", np.array(fund, dtype=np.float64))
    np.save(folder / f"{prefix}ident_v.npy", np.array(ident, dtype=np.float64))
    return folder


def test_a_wavetracker_directory_imports(tmp_path):
    folder = write_wavetracker(tmp_path / "run")
    found, complaints = W.import_directory(folder)
    assert len(found) == 2
    assert complaints == []
    assert abs(np.median(found[0][1]) - 700.0) < 1e-6


@pytest.mark.parametrize("prefix", ["all_", ""])
def test_both_namings_are_accepted(tmp_path, prefix):
    folder = write_wavetracker(tmp_path / f"run{prefix or 'bare'}", prefix=prefix)
    found, _complaints = W.import_directory(folder)
    assert len(found) == 2


def test_an_unrelated_directory_is_reported_not_raised(tmp_path):
    found, complaints = W.import_directory(tmp_path)
    assert found == []
    assert any("no wavetracker arrays" in c for c in complaints)


def test_arrays_of_different_lengths_are_refused(tmp_path):
    """The failure `EODsorter.open` never checked for.

    A stale ``all_ident_v.npy`` beside a recomputed ``all_fund_v.npy`` drew
    identities at other detections' frequencies, confidently and silently.
    """
    folder = write_wavetracker(tmp_path / "run")
    np.save(folder / "all_ident_v.npy", np.zeros(7))
    found, complaints = W.import_directory(folder)
    assert found == []
    assert any("is inconsistent" in c for c in complaints)


def test_detections_indexing_past_the_time_axis_are_skipped(tmp_path):
    folder = write_wavetracker(tmp_path / "run")
    idx = np.load(folder / "all_idx_v.npy")
    idx[:5] = 10_000
    np.save(folder / "all_idx_v.npy", idx)
    found, complaints = W.import_directory(folder)
    assert found, "the sound detections must still import"
    assert any("index outside" in c for c in complaints)


def test_an_untracked_directory_says_so(tmp_path):
    folder = write_wavetracker(tmp_path / "run")
    ident = np.load(folder / "all_ident_v.npy")
    np.save(folder / "all_ident_v.npy", np.full(ident.size, np.nan))
    found, complaints = W.import_directory(folder)
    assert found == []
    assert any("never tracked" in c for c in complaints)


def test_unassigned_detections_are_counted_in_the_complaint(tmp_path):
    folder = write_wavetracker(tmp_path / "run")
    ident = np.load(folder / "all_ident_v.npy")
    ident[:10] = np.nan
    np.save(folder / "all_ident_v.npy", ident)
    found, complaints = W.import_directory(folder)
    assert found
    assert any("10 unassigned detections" in c for c in complaints)


def test_the_importer_writes_nothing(tmp_path):
    """A wavetracker directory is an input, and this must not touch it."""
    folder = write_wavetracker(tmp_path / "run")
    before = {p.name: (p.stat().st_mtime_ns, p.stat().st_size)
              for p in folder.iterdir()}
    time.sleep(0.01)
    W.import_directory(folder)
    after = {p.name: (p.stat().st_mtime_ns, p.stat().st_size)
             for p in folder.iterdir()}
    assert before == after


# ------------------------------------------------------------- the reference


def boxes(category, note, t0, t1, hz, step=1.0, height=3.0):
    """A chain of one-second labels along one frequency, as the truth is kept."""
    from audian.labels import KIND_SPAN, Label

    made = []
    t = t0
    while t < t1 - 1e-9:
        made.append(
            Label(category=category, kind=KIND_SPAN, channel=None,
                  t0=t, t1=min(t + step, t1),
                  f0=hz - 0.5 * height, f1=hz + 0.5 * height, note=note)
        )
        t += step
    return made


def test_a_chain_of_boxes_becomes_one_band():
    """The whole point: 120 boxes along one frequency are one animal.

    The synthetic recording's ground truth is 323 of them, and the track --
    the thing the file is about -- existed only as the reader's eye joining
    them up.
    """
    from audian_plugins.frequencybands import reference as R

    made, complaints = R.bands_from_labels(boxes("Sternopygus", "resident",
                                                 0.0, 120.0, 62.0))
    assert complaints == []
    assert len(made) == 1
    band = next(iter(made))
    assert band.category == "Sternopygus" and band.note == "resident"
    assert len(band) == 120
    assert abs(np.median(band.freqs) - 62.0) < 1e-6
    assert band.t0 == pytest.approx(0.5) and band.t1 == pytest.approx(119.5)


def test_two_individuals_are_two_bands():
    from audian_plugins.frequencybands import reference as R

    rows = boxes("Eigenmannia", "one", 0.0, 20.0, 287.0)
    rows += boxes("Eigenmannia", "two", 0.0, 20.0, 293.0)
    made, _complaints = R.bands_from_labels(rows)
    assert len(made) == 2
    centres = sorted(float(np.median(b.freqs)) for b in made)
    assert centres == pytest.approx([287.0, 293.0])


def test_an_animal_that_leaves_and_returns_is_two_bands():
    """A hole longer than the labelling's own resolution is being asserted."""
    from audian_plugins.frequencybands import reference as R

    rows = boxes("Alepto", "a", 0.0, 10.0, 800.0)
    rows += boxes("Alepto", "a", 60.0, 70.0, 800.0)
    made, _complaints = R.bands_from_labels(rows)
    assert len(made) == 2


def test_one_missing_box_does_not_cut_the_band():
    """A dropout in the labelling is not the animal leaving."""
    from audian_plugins.frequencybands import reference as R

    rows = boxes("Alepto", "a", 0.0, 20.0, 800.0)
    del rows[7]
    made, _complaints = R.bands_from_labels(rows)
    assert len(made) == 1


def test_a_single_long_box_becomes_a_line_not_a_dot():
    """The mains harmonics are labelled one box across the whole recording.

    Its centre alone would be a dot at 60 s where a line across the file was
    meant, and a one-vertex band draws as a single point.
    """
    from audian.labels import KIND_SPAN, Label
    from audian_plugins.frequencybands import reference as R

    made, _complaints = R.bands_from_labels([
        Label(category="mains_hum", kind=KIND_SPAN, t0=0.0, t1=120.0,
              f0=49.0, f1=51.0, note="harmonic 1")
    ])
    assert len(made) == 1
    band = next(iter(made))
    assert len(band) == 2
    assert band.t0 == pytest.approx(0.0) and band.t1 == pytest.approx(120.0)
    assert np.allclose(band.freqs, 50.0)


def test_boxes_with_silence_between_them_are_separate_events():
    """Contiguity, not regularity.

    Three pulses evenly spread across a minute are evenly *spaced*, so a
    rule that compares each gap against the median gap never fires and calls
    them one band a minute long with three vertices in it.
    """
    from audian.labels import KIND_SPAN, Label
    from audian_plugins.frequencybands import reference as R

    rows = [
        Label(category="pulse", kind=KIND_SPAN, t0=t, t1=t + 2.0,
              f0=180.0, f1=190.0)
        for t in (10.0, 35.0, 60.0)
    ]
    made, _complaints = R.bands_from_labels(rows)
    assert len(made) == 3, "evenly spaced is not the same as contiguous"
    assert all(len(b) == 2 for b in made)


def test_labels_without_a_frequency_are_skipped_and_counted():
    from audian.labels import KIND_SPAN, Label
    from audian_plugins.frequencybands import reference as R

    rows = boxes("Sternopygus", "resident", 0.0, 10.0, 62.0)
    rows.append(Label(category="noise", kind=KIND_SPAN, t0=1.0, t1=2.0))
    made, complaints = R.bands_from_labels(rows)
    assert len(made) == 1
    assert any("no frequency" in c for c in complaints)


def test_nothing_at_all_is_not_an_error():
    from audian_plugins.frequencybands import reference as R

    made, complaints = R.bands_from_labels([])
    assert len(made) == 0 and complaints == []


def test_a_reference_has_its_own_file(tmp_path):
    """Read-only, so it must not be able to overwrite the reader's bands."""
    from audian_plugins.frequencybands import reference as R

    recording = tmp_path / "rec.wav"
    mine = B.BandSet()
    mine.add(*steady(0.0, 10.0), category="mine")
    B.write(mine, recording)

    truth, _c = R.bands_from_labels(boxes("Sternopygus", "resident",
                                          0.0, 20.0, 62.0))
    B.write(truth, recording, reference=True)

    assert B.csv_path(recording) != B.csv_path(recording, reference=True)
    back_mine, _c = B.read(recording)
    back_truth, _c = B.read(recording, reference=True)
    assert [b.category for b in back_mine] == ["mine"]
    assert [b.category for b in back_truth] == ["Sternopygus"]


def test_a_ground_truth_of_four_animals_converts_through_the_sidecar(tmp_path):
    """Four chains, hundreds of boxes, read back off disk the way the file is.

    `test_a_chain_of_boxes_becomes_one_band` above hands `bands_from_labels` a
    list it just built, for one animal.  This writes the same shape as a
    sidecar and reads it with `LabelSet`, so the conversion is asked the
    question the reader actually asks it: four species drawn box by box in one
    file, which is what the real ground truth is and was the only thing that
    exercised the reader on the way in.
    """
    from audian.labels import LabelSet
    from audian_plugins.frequencybands import reference as R

    species = {
        "Alepto": 62.0,
        "Eigenmannia": 340.0,
        "Sternarchella": 720.0,
        "Sternopygus": 105.0,
    }
    made = []
    for name, hz in species.items():
        made.extend(boxes(name, "resident", 0.0, 90.0, hz))
    assert len(made) > 300, "the point is that it was hundreds of boxes"

    sidecar = tmp_path / "truth-editable-labels.csv"
    lines = ["category,kind,channel,t_start_s,t_end_s,f_low_hz,f_high_hz,note"]
    for label in made:
        lines.append(
            f"{label.category},span,,{label.t0:.6f},{label.t1:.6f},"
            f"{label.f0:.3f},{label.f1:.3f},{label.note}"
        )
    sidecar.write_text("\n".join(lines) + "\n", encoding="utf-8")

    store = LabelSet()
    store.read(sidecar)
    bands, complaints = R.bands_from_labels(store)
    assert complaints == []
    assert sorted(b.category for b in bands) == sorted(species)
    assert len(bands) == len(species), "one band per animal, not one per box"


@pytest.mark.realdata
def test_the_real_ground_truth_converts(tmp_path):
    """The file this feature exists for, if it is on this machine."""
    from audian.labels import LabelSet
    from audian_plugins.frequencybands import reference as R

    source = Path(
        "/home/weygoldt/wrk/data/fakefish/wavefish_4ch_clean-editable-labels.csv"
    )
    if not source.exists():
        pytest.skip("the synthetic recording is not on this machine")
    store = LabelSet()
    store.read(source)
    made, _complaints = R.bands_from_labels(store)
    names = sorted(b.category for b in made)
    assert names == ["Alepto", "Eigenmannia", "Sternarchella", "Sternopygus"]
    assert len(store) > 300, "the point is that it was hundreds of boxes"


# ------------------------------------------------- tracking what is displayed


def four_channel_block(rate=4000.0, duration=4.0, hz=300.0):
    """Four channels, the tone loud on one of them and faint on the rest."""
    t = np.arange(0.0, duration, 1.0 / rate)
    rng = np.random.default_rng(3)
    block = rng.normal(0.0, 0.02, (t.size, 4))
    for c in range(4):
        block[:, c] += (1.0 if c == 0 else 0.08) * np.sin(2 * np.pi * hz * t)
    return block


def test_the_denoise_chain_sees_every_channel_not_just_the_tracked_one():
    """A cross-channel denoiser is a no-op on one channel, silently.

    `denoisers/engine.py` returns the block untouched below two channels, so
    handing it only the channel being tracked would drop the denoising the
    reader can see on screen and never say so.
    """
    from audian import denoise
    from audian_plugins.denoisers import audian_builtin_denoisers
    from audian_plugins.frequencybands.panel import power_of_block

    for entry in audian_builtin_denoisers():
        denoise.register(entry)

    block = four_channel_block()
    common = dict(nfft=1024, overlap_frac=0.5, channel=0, denoise_params={})
    _t, _f, plain = power_of_block(block, 4000.0, {**common, "denoisers": ()})
    _t, _f, cleaned = power_of_block(
        block, 4000.0, {**common, "denoisers": ("spatial",)}
    )
    assert plain.shape == cleaned.shape
    assert not np.allclose(plain, cleaned), (
        "the denoiser did nothing, which is what happens when it is handed "
        "a single channel"
    )


def test_the_displayed_filter_is_applied_before_the_transform():
    """The band-pass the reader set has to reach the tracker."""
    from scipy.signal import butter

    from audian_plugins.frequencybands.panel import power_of_block

    block = four_channel_block(hz=300.0)
    common = dict(nfft=1024, overlap_frac=0.5, channel=0, denoisers=(),
                  denoise_params={})
    _t, freqs, plain = power_of_block(block, 4000.0, common)
    sos = butter(4, 1000.0, "highpass", fs=4000.0, output="sos")
    _t, _f, filtered = power_of_block(block, 4000.0, {**common, "sos": sos})
    at_tone = int(np.argmin(np.abs(freqs - 300.0)))
    assert filtered[:, at_tone].mean() < 0.01 * plain[:, at_tone].mean(), (
        "a high-pass well above the tone must remove it"
    )


def test_a_block_of_displayed_power_tracks_without_touching_the_recording():
    """`frames_of_power` takes the lane's own block, in linear power."""
    from audian_plugins.frequencybands.panel import (
        METHOD_PEAKS,
        frames_of_power,
    )

    times, freqs, power_db = spectrogram_of([(500.0, 40.0, 0.0, 4.0)])
    linear = 10.0 ** (power_db / 10.0)
    frames = frames_of_power(
        times, freqs, linear,
        {"method": METHOD_PEAKS, "max_hz": 1000.0, "threshold_db": 10.0,
         "max_peaks": 8},
    )
    found = T.link(frames, tolerance_hz=20.0, max_gap_s=0.1, min_duration_s=0.5)
    assert len(found) == 1
    assert abs(np.median(found[0][1]) - 500.0) < 10.0


# ------------------------------------------------------------- registration


def test_the_plugin_registers_itself_by_name():
    """The naming convention is the whole contract; a rename unregisters."""
    from audian.plugins import Plugins, panel_menu_path

    plugins = Plugins()
    plugins.load_bundled()
    entries = dict(
        (panel_menu_path(f)[0], f) for _label, f in plugins.panel_entries()
    )
    assert "Frequency bands" in entries


def test_the_browser_promises_the_lanes_this_plugin_draws_on():
    """`spectrogram_axes` is the one thing audian had to grow for this."""
    from audian import pluginapi
    from audian.databrowser import DataBrowser

    assert "spectrogram_axes" in pluginapi.PLUGIN_BROWSER_ATTRS
    assert callable(DataBrowser.spectrogram_axes)


# ---------------------------------------------------------------- the drawing


def test_joined_separates_polylines_with_a_break():
    from audian_plugins.frequencybands.overlay import joined

    x, y = joined([(np.array([0.0, 1.0]), np.array([5.0, 5.0])),
                   (np.array([2.0, 3.0]), np.array([7.0, 7.0]))])
    assert x.size == 5 and np.isnan(x[2])
    assert not np.isnan(x[-1]), "a trailing separator poisons the bounding box"


def test_the_selection_colour_opposes_the_map_and_never_vanishes():
    """Derived from the map, so it is right for all eight of them."""
    from PySide6.QtGui import QColor

    from audian_plugins.frequencybands.overlay import (
        ACHROMATIC_SELECTION,
        opposed,
    )

    # a hue gets its opposite
    assert opposed(QColor("#ffff00")).lower() == "#0000ff"
    # white and black have no hue to oppose, and must not yield themselves
    for flat in ("#ffffff", "#000000", "#fcf9f3"):
        assert opposed(QColor(flat)).upper() == ACHROMATIC_SELECTION


def test_decimation_bounds_the_points_drawn():
    from audian_plugins.frequencybands.overlay import PIXEL_DENSITY, stride_for

    assert stride_for(10, 1000.0) == 1, "never subsample what already fits"
    stride = stride_for(100_000, 1000.0)
    assert 100_000 / stride <= 1000.0 * PIXEL_DENSITY + 1


# ----------------------------------------------------------------- the tab


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication

    instance = QApplication.instance()
    if instance is None:
        instance = QApplication([])
    return instance


def pump(seconds):
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QApplication

    end = time.monotonic() + seconds
    application = QApplication.instance()
    while time.monotonic() < end:
        application.processEvents()
        application.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        time.sleep(0.005)


@pytest.fixture(scope="module")
def browser(app, tmp_path_factory):
    """A real window on a two channel recording showing a spectrogram.

    One window for the whole module, not one per test.  Building and tearing
    down a full `Audian` costs a couple of seconds and, more to the point,
    leaves Qt objects for Python's collector to free at a moment Qt did not
    choose -- the race `conftest` exists to contain.  Each test here opens its
    own plugin tab and closes it again, so there is no state to share.
    """
    soundfile = pytest.importorskip("soundfile")

    import audian.audian as audian_app
    from audian import theme
    from audian.plugins import Plugins

    tmp_path = tmp_path_factory.mktemp("bands-browser")
    rate = 8000
    frames = rate * 4
    signal = np.zeros((frames, 2), dtype=np.float32)
    step = np.arange(frames) / rate
    for c in range(2):
        signal[:, c] = 0.2 * np.sin(2 * np.pi * (600.0 + 200.0 * c) * step)
    recording = tmp_path / "rec.wav"
    soundfile.write(recording, signal, rate)

    theme.apply(app)
    plugins = Plugins()
    plugins.load_plugins()
    window = audian_app.Audian(
        [str(recording)], {}, plugins, [], 0, None, False, 0, None
    )
    window.resize(1200, 800)
    window.show()
    pump(2.0)
    view = window.browser()
    view.set_panels(specs=1)
    pump(1.0)
    yield view
    window.close()
    window.setParent(None)
    window.deleteLater()
    pump(0.3)


@pytest.fixture(autouse=True)
def _clean_sidecar(request):
    """Take the band sidecar away before and after each windowed test.

    The window is built once for the module, so the tests share one recording
    -- and the panel saves automatically, which would otherwise leave one
    test's bands lying beside the recording for the next one to load.
    """
    if "browser" not in request.fixturenames:
        yield
        return
    view = request.getfixturevalue("browser")
    path = Path(view.data.file_path)

    def clear():
        for sidecar in (B.csv_path(path), B.npz_path(path)):
            if sidecar.exists():
                sidecar.unlink()

    clear()
    yield
    clear()


def test_a_sweep_covers_every_file_of_a_split_recording(browser):
    """`Data.file_path` is the first file, not the recording.

    `data.py` reduces a list of paths to its first element on the way in, so
    a sweep that took it would track file one of four and report the answer
    as the whole recording -- silently, which is the same class of failure
    `open_files` exists to prevent.  The complete list is on the loader.
    """
    from audian_plugins.frequencybands import audian_frequency_bands_panel

    _title, panel = audian_frequency_bands_panel(browser)
    try:
        class _Loader:
            file_paths = ["/one.wav", "/two.wav", "/three.wav"]

        class _Data:
            data = _Loader()
            file_path = "/one.wav"

        panel.browser = type("B", (), {"data": _Data()})()
        assert panel.source_paths() == ["/one.wav", "/two.wav", "/three.wav"]

        # and a browser whose loader has gone still names the anchor
        class _Bare:
            data = None
            file_path = "/one.wav"

        panel.browser = type("B", (), {"data": _Bare()})()
        assert panel.source_paths() == ["/one.wav"]
    finally:
        panel.browser = browser
        panel.close()
        pump(0.2)


def test_the_channel_to_track_is_the_recording_s_channels(browser):
    """A grid recording is the case this plugin is for; channel 0 is a default.

    The combo is filled from the open recording rather than at construction,
    because a panel is built once and recordings are opened many times.
    """
    from audian_plugins.frequencybands import audian_frequency_bands_panel

    _title, panel = audian_frequency_bands_panel(browser)
    panel.show()
    pump(0.3)
    assert panel.channelw.count() == 2, "the fixture recording has two channels"
    assert panel.channel() == 0
    panel.channelw.setCurrentIndex(1)
    assert panel.channel() == 1
    panel.close()
    pump(0.2)


def test_the_tracker_follows_the_displayed_spectrogram_by_default(browser):
    """The reader tunes one spectrogram, not two."""
    from audian_plugins.frequencybands import audian_frequency_bands_panel

    _title, panel = audian_frequency_bands_panel(browser)
    panel.show()
    pump(0.3)
    try:
        assert panel.follows_display()
        spec = panel.spectrogram_trace()
        assert spec is not None, "no spectrogram trace to follow"
        pipeline = panel.display_pipeline()
        assert pipeline["nfft"] == int(spec.nfft)
        assert pipeline["overlap_frac"] == float(spec.overlap_frac)
        # and the settings the run uses take those, not the panel's own
        settings = panel._settings()
        assert settings["nfft"] == int(spec.nfft)
        # the line says which spectrogram, so "as displayed" is checkable
        assert str(spec.nfft) in panel.pipelinew.text()
    finally:
        panel.close()
        pump(0.2)


def test_choosing_raw_audio_uses_the_panels_own_window(browser):
    from audian_plugins.frequencybands import audian_frequency_bands_panel
    from audian_plugins.frequencybands.panel import SOURCE_OWN

    _title, panel = audian_frequency_bands_panel(browser)
    panel.show()
    pump(0.3)
    try:
        panel.sourcew.setCurrentIndex(panel.sourcew.findData(SOURCE_OWN))
        pump(0.1)
        assert not panel.follows_display()
        assert panel._settings()["nfft"] == int(panel.nfftw.currentData())
        assert "raw audio" in panel.pipelinew.text()
    finally:
        panel.close()
        pump(0.2)


def test_a_window_too_coarse_for_the_tolerance_is_reported(browser):
    """A 78 Hz bin cannot answer a 6 Hz question, and must not pretend to."""
    from audian_plugins.frequencybands import audian_frequency_bands_panel

    _title, panel = audian_frequency_bands_panel(browser)
    panel.show()
    pump(0.3)
    said = []
    panel.browser = type(
        "B", (), {"data": browser.data, "notify": lambda _s, lvl, msg: said.append((lvl, msg))}
    )()
    try:
        panel.warn_if_too_coarse({"nfft": 64, "tolerance_hz": 6.0})
        assert said and said[0][0] == "warning"
        assert "wider than" in said[0][1]
        said.clear()
        panel.warn_if_too_coarse({"nfft": 1 << 20, "tolerance_hz": 6.0})
        assert not said, "a fine window must not be complained about"
    finally:
        panel.browser = browser
        panel.close()
        pump(0.2)


def test_a_recording_has_lanes_to_draw_on(browser):
    assert browser.spectrogram_axes(), "no spectrogram lane to hang a band on"


def test_the_tab_puts_one_overlay_on_each_lane(browser):
    from audian_plugins.frequencybands import audian_frequency_bands_panel

    title, panel = audian_frequency_bands_panel(browser)
    assert title == "Bands"
    panel.show()
    pump(0.3)
    assert len(panel.overlays) == len(browser.spectrogram_axes())
    panel.close()
    pump(0.2)
    assert panel.overlays == [], "closing the tab must take the marks off"


def test_bands_are_drawn_and_selected_by_id(browser):
    from audian_plugins.frequencybands import audian_frequency_bands_panel

    _title, panel = audian_frequency_bands_panel(browser)
    panel.show()
    pump(0.3)
    panel.bands.add(*steady(0.5, 3.5, 60, 600.0))
    panel._changed(None)
    pump(0.2)
    overlay = panel.overlays[0]
    assert overlay.bands is panel.bands
    panel.set_selection([1])
    assert overlay.selection == (1,)
    assert panel.mergew.isEnabled() is False, "one band is not a merge"
    panel.bands.add(*steady(0.5, 3.5, 60, 900.0))
    panel._changed(None)
    panel.set_selection([1, 2])
    assert panel.mergew.isEnabled()
    panel.close()
    pump(0.2)


def test_a_click_selects_the_nearest_band(browser):
    from audian_plugins.frequencybands import audian_frequency_bands_panel

    _title, panel = audian_frequency_bands_panel(browser)
    panel.show()
    pump(0.3)
    panel.bands.add(*steady(0.5, 3.5, 60, 600.0))
    panel._changed(None)
    pump(0.2)
    overlay = panel.overlays[0]
    (x0, x1), (y0, y1) = overlay.view_range()
    tol_t, tol_f = panel._tolerances(overlay)
    assert overlay.band_near(2.0, 600.0, tol_t, tol_f) == 1
    assert overlay.band_near(2.0, 600.0 + 50 * tol_f, tol_t, tol_f) is None
    panel.close()
    pump(0.2)


def test_edits_survive_a_close_and_reopen(browser):
    """Closing the tab saves, and reopening reads it back."""
    from audian_plugins.frequencybands import audian_frequency_bands_panel

    _title, panel = audian_frequency_bands_panel(browser)
    panel.show()
    pump(0.3)
    panel.bands.add(*steady(0.5, 3.5, 60, 600.0), category="male")
    panel._changed(None)
    panel.close()
    pump(0.3)

    _title, again = audian_frequency_bands_panel(browser)
    again.show()
    pump(0.3)
    assert len(again.bands) == 1
    assert next(iter(again.bands)).category == "male"
    again.close()
    pump(0.2)


class _NeverCancelled:
    def check(self) -> None:
        pass


def test_a_sweep_leaves_no_gap_in_the_frame_grid_at_a_chunk_boundary(tmp_path, monkeypatch):
    """The seam used to swallow every window straddling it.

    `_sweep` walked the recording in `CHUNK_S` steps with `start = stop`, so
    the windows needing samples from both sides of a boundary were never
    transformed: with the panel's own settings -- nfft 16384, overlap 0.75,
    hop 4096 -- four frames, 0.61 s of a 20 kHz recording, at every seam.
    A fish singing across one had no vertices there.

    It also shifted the phase, because `step` is not a multiple of `hop`:
    every frame after a seam moved by `(step % hop) / rate`.

    `frames_of_block` is stubbed with a left-edge grid.  What is under test
    is the *walk* -- which windows the sweep visits -- and a stub makes the
    expected answer exact rather than a property of thunderfish.
    """
    soundfile = pytest.importorskip("soundfile")
    from audian_plugins.frequencybands import panel as P

    rate = 2000
    seconds = 150.0  # two chunk boundaries at CHUNK_S = 60
    nframes = int(rate * seconds)
    signal = np.zeros((nframes, 1), dtype=np.float32)
    recording = tmp_path / "long.wav"
    soundfile.write(recording, signal, rate)

    nfft, hop = 256, 128

    def fake_frames_of_block(block, block_rate, settings, token=None):
        n = (len(block) - nfft) // hop + 1
        return [(j * hop / block_rate, np.array([100.0])) for j in range(n)]

    monkeypatch.setattr(P, "frames_of_block", fake_frames_of_block)

    worker = P.SweepWorker(
        [str(recording)],
        {"nfft": nfft, "overlap_frac": 0.5},
        0,
        _NeverCancelled(),
    )
    times = np.array([t for t, _hz in worker._sweep()])

    assert times.size > 0
    steps = np.diff(times)
    expected = hop / rate
    assert np.allclose(steps, expected), (
        "the frame grid is not uniform across a chunk boundary: "
        f"largest step {steps.max():.6f} s against one hop of {expected:.6f} s"
    )
    # and the sweep reaches the end rather than stopping a window short of it
    assert times[-1] >= seconds - (nfft / rate) - expected
