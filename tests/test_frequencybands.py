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


@pytest.fixture
def browser(app, tmp_path):
    """A real window on a two channel recording showing a spectrogram."""
    soundfile = pytest.importorskip("soundfile")
    from PySide6.QtCore import QSettings

    import audian.audian as audian_app
    from audian import theme
    from audian.plugins import Plugins

    rate = 8000
    frames = rate * 4
    signal = np.zeros((frames, 2), dtype=np.float32)
    step = np.arange(frames) / rate
    for c in range(2):
        signal[:, c] = 0.2 * np.sin(2 * np.pi * (600.0 + 200.0 * c) * step)
    recording = tmp_path / "rec.wav"
    soundfile.write(recording, signal, rate)

    original = audian_app.settings_path
    home = Path(QSettings("audian", "audian").fileName()).parent.parent
    audian_app.settings_path = lambda: tmp_path / "settings.json"
    for fmt in (QSettings.Format.NativeFormat, QSettings.Format.IniFormat):
        for scope in (QSettings.Scope.UserScope, QSettings.Scope.SystemScope):
            QSettings.setPath(fmt, scope, os.fspath(tmp_path))

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
    audian_app.settings_path = original
    for fmt in (QSettings.Format.NativeFormat, QSettings.Format.IniFormat):
        for scope in (QSettings.Scope.UserScope, QSettings.Scope.SystemScope):
            QSettings.setPath(fmt, scope, os.fspath(home))


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


def test_edits_survive_a_close_and_reopen(browser, tmp_path):
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
