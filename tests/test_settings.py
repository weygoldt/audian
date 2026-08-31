"""Tests for the preference store and for following the desktop's theme.

Runs offscreen::

    QT_QPA_PLATFORM=offscreen .venv-qt6/bin/python -m pytest tests/test_settings.py -q

Every test here writes into a `tmp_path` and points `audian.settings_path`
at it, because the alternative is writing the developer's real
``~/.config/audian/settings.json`` -- which holds their label vocabulary,
and which `tests/test_joinmarkers.py` records has already been clobbered
once by a test that forgot.

No window is built.  The claims are about what a value on disk means, and
answering them through a browser would cost two more top-level widgets in
the process, which is the accumulated state ``todo.md`` records
`theme.collect_orphan_widgets` segfaulting on.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from audian import smoothing, theme  # noqa: E402


@pytest.fixture(autouse=True)
def keep_the_theme():
    """Put the active theme back after every test in this module.

    Autouse, and not the job of the fixture that redirects the settings
    file, because the two are independent and the leak this prevents is
    silent and lands somewhere else entirely: a test here that ends on the
    daylight table left `test_controlpanel`'s theme-switch test comparing
    the light colour against itself, in another file, two hundred tests
    later.  Measured, not hypothesised -- that is how this fixture came to
    exist.
    """
    name = theme.current_theme()
    yield
    theme.set_theme(name)


@pytest.fixture
def store(tmp_path):
    """Redirect the settings file for one test, and put it back afterwards."""
    import audian.audian as audian_app

    original_path = audian_app.settings_path
    audian_app.settings_path = lambda: tmp_path / "settings.json"
    yield audian_app
    audian_app.settings_path = original_path


@pytest.fixture
def app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


# --- the theme preference --------------------------------------------------


def test_a_reader_who_never_chose_follows_the_desktop(store):
    """The default, and the one behaviour change for an existing settings file.

    A missing key used to mean dark.  It now means "ask the desktop", which
    is the whole point of the feature: an audian nobody has configured
    should not be the one window on a light desktop that opens dark.
    """
    assert store.theme_preference(None) == theme.THEME_SYSTEM


def test_an_older_settings_file_still_pins_its_theme(store):
    """`theme` is a bare string with no version gate, so both old values live.

    This is the compatibility claim that let the key keep its shape: a file
    written by an audian that only knew two themes still says exactly what
    it said, and nothing has to be migrated.
    """
    for pinned in (theme.THEME_DARK, theme.THEME_LIGHT):
        assert store.theme_preference(pinned) == pinned


def test_a_hand_edited_preference_does_not_raise(store):
    """This runs on the startup path, so it may not be the thing that fails."""
    for junk in ("mauve", "", 3, None, [], {}):
        assert store.theme_preference(junk) in theme.THEME_PREFERENCES


def test_resolve_never_returns_the_preference_itself(app):
    """'system' is a preference, never a theme: nothing downstream may see it.

    `set_theme` would raise `KeyError` on it, and the token tables are keyed
    by the two real names.
    """
    for preference in theme.THEME_PREFERENCES:
        assert theme.resolve_theme(preference) in theme.THEMES


def test_an_unknown_colour_scheme_resolves_to_dark(app):
    """Offscreen reports `Unknown`, and the suite runs offscreen.

    Mapping it to dark is what keeps every contrast and geometry assertion
    in `test_theme.py` measured against the theme it was written for.
    """
    assert theme.system_theme() == theme.THEME_DARK
    assert theme.resolve_theme(theme.THEME_SYSTEM) == theme.THEME_DARK


def test_pushing_a_colour_scheme_does_not_raise(app):
    """The setter is Qt 6.8; older builds must degrade rather than crash."""
    for preference in theme.THEME_PREFERENCES:
        theme.push_color_scheme(preference)


def test_the_palette_carries_our_accent_and_not_the_desktop_s(app):
    """A bare `QPalette()` seeds unset roles from the running application.

    Measured before this was fixed: both audian themes came out holding KDE
    Breeze's #308cc6, the daylight theme included, cached under our own
    theme's name.
    """
    from PySide6.QtGui import QPalette

    for name in theme.THEMES:
        theme.set_theme(name)
        accent = theme.palette().color(QPalette.ColorRole.Accent).name()
        assert accent.lower() == theme.token("primary").lower(), name


@pytest.fixture
def scheme(app):
    """Drive `QStyleHints.colorScheme` the way a desktop would, and undo it.

    Skips under the offscreen platform, which the suite forces and which has
    no colour scheme at all: `setColorScheme` is accepted there and changes
    nothing, so an unskipped test would assert against a hint that never
    moved.  Measured -- offscreen reports `Unknown` before and after, xcb
    reports Dark, then Light, then Dark again after `unsetColorScheme`.  To
    run these two, give the run a display::

        .venv-qt6/bin/python -m pytest tests/test_settings.py -q \\
            -k desktop --no-header -p no:cacheprovider

    with ``QT_QPA_PLATFORM`` unset.

    `setColorScheme` is what the platform theme effectively does when the
    reader changes their desktop from dark to light: it moves the hint and
    emits `colorSchemeChanged`.  Driving it here is the only way to test the
    following path without a human at a settings panel -- and it has to be
    unset again, or every later test in the run sees a scheme this one
    chose.

    It is asynchronous on xcb, so the helper pumps the loop before returning
    rather than letting a caller read a value that has not landed yet.
    """
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication

    hints = QGuiApplication.styleHints()
    if hints.colorScheme() == Qt.ColorScheme.Unknown:
        pytest.skip(f"{app.platformName()} has no colour scheme to drive")

    def set(name):
        hints.setColorScheme(
            Qt.ColorScheme.Light if name == theme.THEME_LIGHT else Qt.ColorScheme.Dark
        )
        for _ in range(10):
            app.processEvents()

    try:
        yield set
    finally:
        hints.unsetColorScheme()
        for _ in range(10):
            app.processEvents()


def test_the_desktop_s_choice_is_the_one_that_gets_painted(app, scheme):
    """The feature itself: what the desktop asks for is what resolves.

    The screenshot matrix cannot show this.  It runs offscreen, where
    `colorScheme()` is `Unknown` and resolves to dark by design, and its
    light and dark shots pass `--theme`, which pins.  So the following path
    has to be driven directly, here.
    """
    scheme(theme.THEME_LIGHT)
    assert theme.system_theme() == theme.THEME_LIGHT
    assert theme.resolve_theme(theme.THEME_SYSTEM) == theme.THEME_LIGHT

    scheme(theme.THEME_DARK)
    assert theme.system_theme() == theme.THEME_DARK
    assert theme.resolve_theme(theme.THEME_SYSTEM) == theme.THEME_DARK


def test_a_pinned_theme_ignores_the_desktop(app, scheme):
    """The other half of it, and the one a reader notices when it is wrong.

    Someone who chose the daylight theme for a field trip must keep it when
    their laptop decides it is night.
    """
    scheme(theme.THEME_LIGHT)
    assert theme.resolve_theme(theme.THEME_DARK) == theme.THEME_DARK
    scheme(theme.THEME_DARK)
    assert theme.resolve_theme(theme.THEME_LIGHT) == theme.THEME_LIGHT


# --- the spectrogram preferences -------------------------------------------


def stored(store) -> dict:
    return json.loads(store.settings_path().read_text())


def test_nothing_stored_reads_as_the_default_map(store, app):
    from audian.databrowser import DataBrowser

    assert DataBrowser.spectrogram_settings() == {}
    assert DataBrowser.read_color_map_setting() == theme.DEFAULT_SPECTROGRAM_MAP


def test_the_map_is_stored_by_name_and_per_theme(store, app):
    """The bug the old integer index had, stated as a test.

    The two pages offer different lists -- eight maps on the dark one, five
    on the daylight one, overlapping in three -- so the same index meant a
    different map on each, and an index past the end of the shorter list was
    silently clamped to zero.  A name cannot do either: read under a theme
    that does not offer it, it falls back to the default rather than to
    whatever happens to sit at that position.
    """
    from audian.databrowser import DataBrowser

    theme.set_theme(theme.THEME_DARK)
    dark_maps = theme.spectrogram_maps()
    theme.set_theme(theme.THEME_LIGHT)
    light_maps = theme.spectrogram_maps()
    dark_only = [name for name in dark_maps if name not in light_maps]
    assert dark_only, "every dark map is now offered by the daylight theme too"
    name = dark_only[0]

    theme.set_theme(theme.THEME_DARK)
    store.save_setting(
        DataBrowser.SPECTROGRAM_SETTING,
        {
            "version": DataBrowser.SPECTROGRAM_SETTING_VERSION,
            "colormap": {theme.THEME_DARK: name},
        },
    )
    assert DataBrowser.read_color_map_setting() == dark_maps.index(name)

    theme.set_theme(theme.THEME_LIGHT)
    assert DataBrowser.read_color_map_setting() == theme.DEFAULT_SPECTROGRAM_MAP


def test_writing_one_theme_s_map_keeps_the_other_s(store, app):
    """A reader who flips between the pages must not lose one every time.

    Now that the theme can follow the desktop, the flip is not even a
    gesture they made.
    """
    from audian.databrowser import DataBrowser

    theme.set_theme(theme.THEME_LIGHT)
    light_name = theme.spectrogram_maps()[1]
    theme.set_theme(theme.THEME_DARK)
    dark_name = theme.spectrogram_maps()[3]

    store.save_setting(
        DataBrowser.SPECTROGRAM_SETTING,
        {
            "version": DataBrowser.SPECTROGRAM_SETTING_VERSION,
            "colormap": {theme.THEME_LIGHT: light_name, theme.THEME_DARK: dark_name},
        },
    )
    assert DataBrowser.read_color_map_setting() == 3
    theme.set_theme(theme.THEME_LIGHT)
    assert DataBrowser.read_color_map_setting() == 1


def test_a_version_we_do_not_write_is_dropped_whole(store, app):
    """Whole-value-or-nothing, the rule every other key in the file follows."""
    from audian.databrowser import DataBrowser

    store.save_setting(
        DataBrowser.SPECTROGRAM_SETTING,
        {"version": 99, "colormap": {theme.THEME_DARK: "CET-L17"}, "nfft": 1024},
    )
    assert DataBrowser.spectrogram_settings() == {}
    assert DataBrowser.read_color_map_setting() == theme.DEFAULT_SPECTROGRAM_MAP


@pytest.mark.parametrize(
    "junk",
    [
        "nonsense",
        [],
        3,
        {"version": 1, "colormap": "CET-L17"},
        {"version": 1, "colormap": {"dark": 7}},
        {"version": 1},
        {"version": 1, "colormap": {"dark": "no-such-map"}},
    ],
)
def test_a_hand_edited_file_never_raises(store, app, junk):
    """The settings file is a file a reader may edit, so a wrong shape is data."""
    from audian.databrowser import DataBrowser

    store.save_setting(DataBrowser.SPECTROGRAM_SETTING, junk)
    index = DataBrowser.read_color_map_setting()
    assert 0 <= index < len(theme.spectrogram_maps())
    assert DataBrowser.read_smoothing_setting() in smoothing.keys()
    assert DataBrowser.read_cutoff_lines_setting() is True


# --- the smoothing ---------------------------------------------------------


def test_nothing_stored_opens_unsmoothed(store, app):
    from audian.databrowser import DataBrowser

    assert DataBrowser.read_smoothing_setting() == smoothing.DEFAULT
    assert not smoothing.changes_values(DataBrowser.read_smoothing_setting())


@pytest.mark.parametrize("key", smoothing.keys())
def test_every_offered_smoothing_survives_the_round_trip(store, app, key):
    from audian.databrowser import DataBrowser

    store.save_setting(
        DataBrowser.SPECTROGRAM_SETTING,
        {"version": DataBrowser.SPECTROGRAM_SETTING_VERSION, "smoothing": key},
    )
    assert DataBrowser.read_smoothing_setting() == key


def test_a_smoothing_this_audian_does_not_offer_falls_back(store, app):
    """Stored by key rather than by position, so a newer audian's entry is
    a name this one does not know -- and an unknown name is the default.

    An index would have been read as *some* filter, and the further apart
    the two versions the less related to the one that was chosen.
    """
    from audian.databrowser import DataBrowser

    for junk in ("synchrosqueezed", "", 2, None, []):
        store.save_setting(
            DataBrowser.SPECTROGRAM_SETTING,
            {"version": DataBrowser.SPECTROGRAM_SETTING_VERSION, "smoothing": junk},
        )
        assert DataBrowser.read_smoothing_setting() == smoothing.DEFAULT


def test_the_smoothing_rides_in_the_same_block_as_the_map(store, app):
    """One key, one version, so a colormap written beside it is not lost.

    `SPECTROGRAM_SETTING_VERSION` is deliberately *not* bumped for the new
    field: `spectrogram_settings` drops the whole block on a version it does
    not recognise, so a bump would take every reader's colormap away to add
    a preference they have not set yet.  A key an older audian does not know
    is simply ignored by it, which is the compatible direction.
    """
    from audian.databrowser import DataBrowser

    theme.set_theme(theme.THEME_DARK)
    name = theme.spectrogram_maps()[2]
    store.save_setting(
        DataBrowser.SPECTROGRAM_SETTING,
        {
            "version": DataBrowser.SPECTROGRAM_SETTING_VERSION,
            "colormap": {theme.THEME_DARK: name},
            "smoothing": "gaussian",
        },
    )
    assert DataBrowser.read_color_map_setting() == 2
    assert DataBrowser.read_smoothing_setting() == "gaussian"
    # a block written by an audian that predates smoothing still reads
    store.save_setting(
        DataBrowser.SPECTROGRAM_SETTING,
        {
            "version": DataBrowser.SPECTROGRAM_SETTING_VERSION,
            "colormap": {theme.THEME_DARK: name},
        },
    )
    assert DataBrowser.read_color_map_setting() == 2
    assert DataBrowser.read_smoothing_setting() == smoothing.DEFAULT


# --- the filter cutoff lines -----------------------------------------------


def test_nothing_stored_draws_the_cutoff_lines(store, app):
    """The lines have always been there, so never having chosen means yes."""
    from audian.databrowser import DataBrowser

    assert DataBrowser.read_cutoff_lines_setting() is True


@pytest.mark.parametrize("stored", [True, False])
def test_the_cutoff_line_switch_survives_the_round_trip(store, app, stored):
    from audian.databrowser import DataBrowser

    store.save_setting(
        DataBrowser.SPECTROGRAM_SETTING,
        {
            "version": DataBrowser.SPECTROGRAM_SETTING_VERSION,
            "cutoff-lines": stored,
        },
    )
    assert DataBrowser.read_cutoff_lines_setting() is stored


@pytest.mark.parametrize("junk", ["off", 0, 1, None, [], {}])
def test_a_cutoff_line_value_of_the_wrong_shape_draws_them(store, app, junk):
    """Only a real bool is believed; anything else is an unset preference.

    `0` and `1` are the interesting entries: they are what a hand-edited
    file or a JSON writer of another language produces, they compare equal
    to the booleans, and reading them as such would let a `1` written for
    something else switch a picture.
    """
    from audian.databrowser import DataBrowser

    store.save_setting(
        DataBrowser.SPECTROGRAM_SETTING,
        {"version": DataBrowser.SPECTROGRAM_SETTING_VERSION, "cutoff-lines": junk},
    )
    assert DataBrowser.read_cutoff_lines_setting() is True


def test_the_cutoff_lines_ride_in_the_same_block_as_the_map(store, app):
    """Same block, same version -- see the smoothing's own test above."""
    from audian.databrowser import DataBrowser

    theme.set_theme(theme.THEME_DARK)
    name = theme.spectrogram_maps()[2]
    store.save_setting(
        DataBrowser.SPECTROGRAM_SETTING,
        {
            "version": DataBrowser.SPECTROGRAM_SETTING_VERSION,
            "colormap": {theme.THEME_DARK: name},
            "smoothing": "gaussian",
            "cutoff-lines": False,
        },
    )
    assert DataBrowser.read_color_map_setting() == 2
    assert DataBrowser.read_smoothing_setting() == "gaussian"
    assert DataBrowser.read_cutoff_lines_setting() is False


# --- the write itself ------------------------------------------------------


def test_one_key_is_written_without_disturbing_the_others(store, app):
    from audian.databrowser import DataBrowser

    store.save_setting("theme", theme.THEME_SYSTEM)
    store.save_setting("labels", {"version": 1, "categories": []})
    store.save_setting(
        DataBrowser.SPECTROGRAM_SETTING,
        {"version": DataBrowser.SPECTROGRAM_SETTING_VERSION, "colormap": {}},
    )
    values = stored(store)
    assert values["theme"] == theme.THEME_SYSTEM
    assert values["labels"] == {"version": 1, "categories": []}
    assert DataBrowser.SPECTROGRAM_SETTING in values


def test_the_write_is_atomic_and_leaves_no_temporary_behind(store, app, tmp_path):
    """An interrupted whole-file rewrite used to take the label vocabulary too.

    The temporary has to live in the same directory as the target, or
    `os.replace` is a copy across filesystems and stops being atomic.
    """
    store.save_setting("theme", theme.THEME_DARK)
    assert not list(tmp_path.glob("*.tmp")), list(tmp_path.glob("*.tmp"))
    assert stored(store)["theme"] == theme.THEME_DARK


def test_an_unwritable_settings_file_is_survivable(store, app, tmp_path, monkeypatch):
    """`save_setting` promises never to raise, and it is called from paint paths."""

    def refuse(*args, **kwargs):
        raise OSError("no room")

    monkeypatch.setattr("builtins.open", refuse)
    store.save_setting("theme", theme.THEME_LIGHT)


def test_the_nfft_range_the_bar_offers_is_the_one_a_restore_snaps_to(app):
    """One source for both, so a restored window is always a window shown.

    `set_nfft_widget` returns silently when `findData` misses, so a window
    off the combo's list would leave the picture and the parameter bar
    disagreeing with nothing said about it.
    """
    from audian.databrowser import DataBrowser

    assert DataBrowser.NFFT_MIN == 2 ** DataBrowser.NFFT_EXPONENTS[0]
    assert DataBrowser.NFFT_MAX == 2 ** DataBrowser.NFFT_EXPONENTS[-1]
    for exponent in DataBrowser.NFFT_EXPONENTS:
        nfft = 2**exponent
        assert 1 << int(nfft).bit_length() - 1 == nfft
