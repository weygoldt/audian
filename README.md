<div align="center">

# audian

**A fast, keyboard-driven viewer for recordings of animal vocalisations.**

Crickets, birds, bats, electric fish — single channel or a sixteen-electrode
array, a few seconds or a session split across a dozen files.

[![PyPI version](https://badge.fury.io/py/audian.svg)](https://pypi.python.org/pypi/audian/)
[![PyPI license](https://img.shields.io/pypi/l/audian.svg)](https://pypi.python.org/pypi/audian/)
[![Python versions](https://img.shields.io/pypi/pyversions/audian.svg)](https://pypi.python.org/pypi/audian/)

</div>

![audian](docs/shots/overview-dark.png)

## Install

```sh
pip install audian
```

## Use

```sh
audian recording.wav        # one file
audian session/*.wav        # a split session, joined by its timestamps
```

Press <kbd>Ctrl</kbd>+<kbd>K</kbd> for every shortcut, or <kbd>?</kbd> for the
cheat sheet. Almost everything has one.

## What it does

- **Long recordings, quickly.** Only the visible part is held in memory, so an
  hour-long multi-channel file opens as fast as a short one.
- **Traces, filters, envelopes, spectrograms** — per channel, in any
  combination, each panel hidden or shown with a key.
- **A spectrogram you can argue with.** Window length, overlap, colour map,
  level range, smoothing, and peaking to show where it clips — all live, all
  on the keyboard.
- **Band-pass filtering** with the cutoffs drawn on the spectrogram.
- **Two kinds of label, kept apart.** *Fixed* labels are what the instrument
  recorded; *editable* labels are your reading of it, drawn with the mouse
  into a CSV sidecar. audian never writes the first kind.
- **A navigator strip** showing the whole recording, so you always know where
  you are in it.
- **Plugins** for computed traces, analyses and side panels — including a
  few-shot event detector that ships with it.

## Detecting events

Mark a handful of examples, and let it find the rest.

![detector](docs/shots/detector.png)

**Plugins → Event detection → Normalised cross-correlation** learns templates
from the events you labelled and matches them against the recording, on the
spectrogram or on the waveform. Tune it on the window in front of you, then
run it over the whole file in the background. Results arrive as ordinary
editable labels — correct them, delete them, save them — and as a CSV beside
the recording.

The defaults were measured rather than guessed: which way of combining several
examples survives noise, why the threshold is relative to the noise floor
instead of an absolute score, and where the whole approach stops working. See
[`src/audian_plugins/eventdetection/engine.py`](src/audian_plugins/eventdetection/engine.py)
if you want the numbers.

## Two themes

Dark for a desk, daylight for a laptop in the field — high contrast, and a
colour map to match.

| Dark | Daylight |
| --- | --- |
| ![dark](docs/shots/overview-dark.png) | ![light](docs/shots/overview-light.png) |

```sh
audian --theme light recording.wav
```

## Writing a plugin

A plugin is a module exposing a callable named `audian_*panel`, `*analyzer` or
`*traces`. Bundled ones live in
[`src/audian_plugins/`](src/audian_plugins/); your own can sit in the
directory you launch from, or in its own installable package that declares:

```toml
[project.entry-points."audian.plugins"]
myplugin = "myplugin"
```

Import from [`audian.pluginapi`](src/audian/pluginapi.py) and nothing else —
that is the surface promised to keep working.

## More

- [User manual](docs/usermanual.md)
- [Internals](docs/architecture.md) — how it is put together
- `audian --help` for command-line options
- Built on [ThunderLab](https://github.com/bendalab/thunderlab),
  [AudioIO](https://github.com/bendalab/audioio) and
  [pyqtgraph](https://pyqtgraph.readthedocs.io)

---

<div align="center">
GPLv3 · by <a href="https://github.com/bendalab">Bendalab</a>, University of Tübingen
</div>
