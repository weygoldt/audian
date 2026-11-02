#!/usr/bin/env python
"""Regenerate the screenshots of the spectrogram detector in the docs.

    uv run python scripts/specdetector_shots.py --weights DIR [--out docs/shots]

Walks the loop on a copy of the cricket recording, offscreen, from empty settings,
with the real panel and the real models: a click outlined by SAM, candidates proposed
from the hand labels in `tests/data/gryllus-fewshot-labels.csv`, and a window under
review.  It stops with an error if a step produces nothing to show.  `--weights` is a
directory holding the model files, which the panel's Get models button would download.
"""

from __future__ import annotations

import argparse
import csv
import shutil
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

from wavetracker_shots import isolate  # noqa: E402

RECORDING = REPO / "data" / "Gryllus_campestris.wav"
LABELS = REPO / "tests" / "data" / "gryllus-fewshot-labels.csv"
WIDTH, HEIGHT, SCALE = 1280, 860, 1.5
CLASS = "syllable"


def pump(app, seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)


def wait(app, client, what: str, timeout: float = 300.0) -> None:
    end = time.monotonic() + timeout
    while client.busy():
        if time.monotonic() > end:
            raise SystemExit(f"timed out waiting for {what}:\n{client.stderr_tail()}")
        pump(app, 0.05)
    if client.last_error is not None:
        raise SystemExit(f"{what} failed: {client.last_error}")


def seed_labels(browser) -> None:
    """The reader's own hand labels of the cricket's syllables."""
    from audian.labels import KIND_SPAN, Label

    browser.labels.add_category(CLASS, KIND_SPAN, 0)
    with open(LABELS, newline="") as fp:
        for row in csv.DictReader(fp):
            if row["category"] != CLASS:
                continue
            browser.labels.add(
                Label(
                    CLASS,
                    KIND_SPAN,
                    None,
                    float(row["t_start_s"]),
                    float(row["t_end_s"]),
                    float(row["f_low_hz"]),
                    float(row["f_high_hz"]),
                )
            )
    browser.current_category = CLASS
    browser.sync_category_state()


def save(window, out: Path, name: str) -> None:
    path = out / f"{name}.png"
    if not window.grab().save(str(path)):
        raise SystemExit(f"could not write {path}")
    print("wrote", path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--weights", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=REPO / "docs" / "shots")
    parser.add_argument("--theme", default="dark")
    args = parser.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="audian-shots-"))
    isolate(tmp, WIDTH, HEIGHT, SCALE)
    sys.path.insert(0, str(REPO / "src"))
    recording = tmp / RECORDING.name
    shutil.copy2(RECORDING, recording)

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    import audian.audian as audian_app
    from audian.plugins import Plugins
    from audian_plugins.specdetector import panel as panel_module

    panel_module.weights_directory = lambda: args.weights
    audian_app.apply_theme_preference(app, args.theme)
    plugins = Plugins()
    plugins.load_plugins()
    window = audian_app.Audian(
        [str(recording)], {}, plugins, [], 0, None, False, 0, None, args.theme
    )
    window.resize(WIDTH, HEIGHT)
    window.show()
    window.activateWindow()
    pump(app, 2.0)
    browser = window.browser()
    browser.set_panels(specs=1)
    pump(app, 1.0)
    seed_labels(browser)
    if not browser.open_plugin_panel("Spectrogram detector"):
        raise SystemExit("the plugin did not open")
    panel = browser.plugin_panels["Spectrogram detector"]
    pump(app, 0.5)
    args.out.mkdir(parents=True, exist_ok=True)

    # A click, and SAM's answer.
    panel.goto(8.8, 9.3, 0.0, 24000.0)
    pump(app, 1.0)
    panel.arm(True)
    pump(app, 0.5)
    wait(app, panel.interactive, "encoding the view")
    ax = panel._lanes()[0]
    panel.lane_clicked(ax, 9.02, 9000.0, False)
    pump(app, 0.2)
    wait(app, panel.interactive, "SAM")
    if panel.mask is None:
        raise SystemExit("SAM answered with no outline")
    pump(app, 0.3)
    save(window, args.out, "specdetector-label")
    panel.clear_prompt()

    # What the examples find, with no training.
    panel.scopew.setCurrentIndex(panel.scopew.findData("all"))
    panel.project.class_index(CLASS)
    panel.find_more()
    pump(app, 0.2)
    wait(app, panel.batch, "Find more")
    if not panel.candidates:
        raise SystemExit("Find more proposed nothing")
    panel.next_window()
    pump(app, 1.5)
    save(window, args.out, "specdetector-review")
    print(panel.statusw.text())
    panel.about_to_flush_labels()
    window.close()
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
