# The spectrogram detector

**Plugins > Spectrogram detector** trains an event detector on the recording you have
open, starting from a few labels. It replaced a template matcher, which is in git
before the commit that removed it.

This document records how it works and why, with the measurements behind each
decision. Everything here is reproduced by `tests/measure_specdetector.py` and
`tests/measure_specdetector_labels.py`. The user's guide is in
[`usermanual.md`](usermanual.md).

## The loop

```
Label       audian's own label mode: a category, a rough box; SAM tightens and outlines it
Find more   candidates, from the labels (YOLOE) or from the last trained model
Review      go through windows; reject what is wrong; the rest is accepted
Train       YOLO26n on what has been looked at, about a minute on a GPU
Run         the whole recording, in the background
```

Everything after labelling can repeat. Labelling goes on during training, because SAM
has a runner process of its own.

**There is one way to label, and it is audian's.** The plugin first had a label tool of
its own: a Label button, a class picker, a SAM prompt that took clicks and waited for
Enter. That was a second scheme beside the one every other label is made with, and the
user asked for it to go. SAM is now an *assistant* of audian's label tool
(`audian.labelassist`): a drag in label mode stores the label as always, and SAM then
answers it with a tighter box and the event's outline, written onto the same label. It is
a toggle in the Labels tab, and with it off a box is exactly what was drawn. Every span
category with boxes on the spectrogram is a class, unless the reader leaves it out; a
category left out is masked rather than taught as background.

SAM's click prompts went with the old tool. A detector trained on click outlines scored
0.569 on chirps against 0.895 from rough drags (below), so the gesture worth keeping was
the drag.

## How it is built

```
__init__.py      the audian_* factory; imports nothing heavy
tiling.py        samples <-> image <-> seconds and hertz   numpy only
boxes.py         overlap, suppression, scoring             numpy only
dataset.py       labels and reviewed rectangles -> YOLO    numpy, OpenCV to write
review.py        which window to look at next              pure logic
project.py       what a label cannot say, on disk          pure data
recording.py     reading a recording without audian        thunderlab
models.py        everything that touches Ultralytics       child process only
modelrunner.py   JSON lines on stdin and stdout            child process only
client.py        the runner as Qt signals                  QtCore
assistant.py     SAM as audian's label assistant           QtCore
tools.py         review's click and keys on the lanes      Qt
panel.py         the tab                                   Qt
```

**The models run in a child process.** A training run holds the GIL for minutes. torch
and OpenCV take seconds and a gigabyte to import, on behalf of a plugin that may not be
used. And `opencv-python` carries a Qt of its own, which has no place in a PySide6
process. A test checks that importing the panel imports none of them, and another that
nothing on the runner's side can import audian or Qt. The protocol matches wavetracker's
runner. Cancelling kills the process, so a `detect` writes nothing until it finishes,
and then to a `.partial` file first.

**There are two runners,** the panel's for what takes minutes and the label assistant's
for SAM, which starts on the first drag with SAM on.

**Ultralytics is kept home and offline.** Left alone it writes under `~/.config`,
installs requirements with pip at run time, sends analytics, and downloads weights and a
font when it finds them absent. `models.configure` turns all of that off, and only an
explicit **Get models** goes online. The whole loop was run under `unshare -rn`, with
only the model files present.

### Where things are kept

A reader's labels are ordinary editable labels, in the ordinary sidecar. A box drawn for
this plugin is a label everywhere else in audian, and a label drawn long before it
existed is already a training example. SAM's outline is part of the label, in the
sidecar's last column, and audian draws it; moving the box drops it.

A machine's guesses are labels too, in `<class> (found)`, so the code that draws and
edits everything else draws and edits them. That category is **transient**
(`LabelSet.mark_transient`): no save writes its rows, and it never appears on the chips,
the digit keys or in the settings. Taking guesses out before each save cannot work,
because the autosave runs on every edit and a crash leaves whatever was last written.

The project directory (`<stem>-specdetector`, beside the recording) holds what a label
cannot say: reviewed rectangles, rejections, the classes and those left out, the last
candidates, the image settings and the models. Outlines it kept before labels could
carry them move onto their labels when a recording is opened. It can span several recordings, whose labels stay in their
own sidecars.

### The one rule: only what a person has looked at reaches the model

A detector is taught that whatever is on an image and not boxed is background. So an
event nobody has labelled yet teaches the model to ignore events. `dataset.py` enforces
this by masking. What a person has looked at is a rectangle in time and frequency,
because that is what a view is. Every pixel outside all such rectangles is overwritten
with this tile's own noise floor.

- A label outside any reviewed rectangle brings a small rectangle of its own: its box and
  a margin. That is what makes training from a few shots possible.
- A rejection is a reviewed rectangle with nothing in it: a hard negative.
- A rectangle records the classes it was reviewed for. A class added later has not been
  looked for there.
- A label with no band marks an event that cannot be boxed, so it is masked.
- A view cannot be marked done while a channel the images use is hidden, because a
  training image is the reduction of all of them, whichever lanes were on screen.

Measured on 5 chirp shots, the same 80 tiles either way (4 s tile, 100 ms window), scored on
regions never seen:

| | Precision | Recall | F1 | Highest recall at any cut |
|---|---|---|---|---|
| Masked | 0.875 | 0.921 | **0.897** | 1.000 |
| Unmasked | 0.748 | 0.781 | 0.764 | 0.816 |

Unmasked, a fifth of the events can never be recovered, because the model has learned that
they are background.

### Validation shares no time with training

The old chirp detector's five folds were drawn per tile, which measured 99 % recall in
cross-validation and 76 % on recordings it had not seen. `split_tiles` holds out stretches
of time and drops any training tile that overlaps a held-out one. Below five such
stretches with an event in them there is nothing to hold out, so everything trains, and
the panel says the score is a training score.

A reader can hold out more often, as often as one stretch in two, which needs only two
places labelled, or never. What cannot be offered is a split inside one stretch: its
tiles overlap, and holding some out would put near-identical pictures on both sides,
which is the very fault above. This came up on a real session: 13 chirps labelled in
one 60 s view of a fish grid were one stretch, nothing was held out, and the model
reported mAP50 0.995 after 200 epochs on its own 20 tiles.

### The reader chooses how a model trains

Under **Training settings**: the YOLO26 size (n to x), whether to start from pretrained
weights or go on from the project's last model, epochs, batch, how often to hold out,
and early stopping with its patience. Each "Auto" is the measured default: training
length in batches (`epochs_for`), patience a third of the epochs and at least 10,
batch 16 on a GPU. The settings are kept in the project.

**Early stopping watches held-out tiles or nothing.** With nothing held out, Ultralytics
would watch the score on the training tiles, which keeps rising, so stopping there would
end training at an arbitrary point and call it the best. `train_detector` turns it off
instead and says so, and the panel tells the reader how far apart to label for a
stretch to be held out.

**The curve is the run.** Each epoch's training loss, held-out loss and mAP50 go to the
panel as they happen, and the whole curve is kept in `model.json`, with the epoch
Ultralytics kept as `best.pt` (highest box mAP50-95). It is painted with `QPainter`: two
pyqtgraph plots in the tab crashed the panel tests in 3 of 4 runs as tabs were torn
down, and in none of 3 once painted.

**Preview** runs the trained model on the view alone and replaces the candidates in it,
keeping the rest of the queue: the quick look at how a model does before a Run over
hours of recording.

## The measurements

### Ground truth

- **Chirps:** 335 exhaustively labelled chirps in 6 regions of 4 recordings at 20 kHz (9-15
  channels), median about 174 ms x 134 Hz. Hand labels, from the chirp competition
  benchmark, in audian's label format.
- **Cricket:** `data/Gryllus_campestris.wav`, 18 s at 96 kHz. Three syllables are labelled
  by hand. The other 62 come from the envelope, which is clean enough to find them, and
  which is checked against those three before anything is scored on it. So this is close
  to the matcher's best case and is an easy test.
- **Rises:** track-derived, from the competition analysis, never checked by hand. Silver
  truth, used only for the two-class test.

Scoring is that benchmark's rule, so numbers can be set beside the old detector's:
one-to-one, centres within 100 ms (50 ms on the cricket), any overlap in frequency, and
only boxes centred in a region and overlapping its band.

**Read every table with these limits in mind.** "Best F1" chooses the cut with the answers
in hand, so it is optimistic. Most rows are few runs on one or two recordings, and seeds of
one setup differ by up to about 0.07, so a difference smaller than that means little.
Unless noted, the card was shared with other work, so timings are upper bounds.

### What to propose with, from a few shots

Best F1 on regions the shots did not come from, with no other labels:

| Proposer | Chirps, 5 shots | Cricket, 3 shots | Trains? |
|---|---|---|---|
| Template matcher (replaced) | 0.099 | 1.000, from 212 boxes at a cut of 0.28 | no |
| PerSAM-style, SAM's features | 0.519 (before the image settings below) | not run | no |
| **YOLOE visual prompts** | 0.777, 0.785 | 0.992 | no |
| **YOLO26n on the shots** | 0.828, 0.913 | 1.000, 82 boxes | about a minute |

The two chirp figures are two sets of shots. YOLOE is why **Find more** works before
anything is trained, and it is instant on any machine. A detector trained on the same few
labels is usually better and is worth doing early. The matcher equals the others on the clean
cricket, whose events are nearly identical, and fails on chirps, where its best precision was
0.055.

### What the image should be

From a few shots, by how many pixels an event spans along time and how many bandwidths of
margin the tile keeps around the labels. F1 at the best cut, and at a fixed cut of 0.5.

| Event, margin | YOLOE chirps | YOLOE cricket | Trained chirps | Trained at 0.5 | Trained cricket |
|---|---|---|---|---|---|
| 27 px, 1 | 0.426, 0.675 | 0.364 | 0.822, 0.860 | 0.697, 0.796 | 0.992 |
| 40 px, 1 | 0.539, 0.498 | 0.685 | 0.833, 0.871 | 0.639, 0.853 | 1.000 |
| **27 px, 3** | **0.777, 0.785** | **0.992** | **0.828, 0.913** | **0.822, 0.894** | **1.000** |
| 40 px, 3 | 0.781, 0.652 | 1.000 | 0.800, 0.924 | 0.665, 0.915 | 1.000 |

An event that fills the height of a tile is a poor picture, which matters most to YOLOE.

Tile scale and window on chirps, YOLO26n with one region's 90 labels, on regions it had not
seen:

| Tile, window | Px on a chirp | Same recording, F1 | Other recordings, F1 |
|---|---|---|---|
| 16 s, 205 ms (the old detector's) | 7 | 0.857 | 0.552 |
| 4 s, 205 ms | 27 | 0.929 | 0.708 |
| 4 s, 100 ms | 27 | 0.896 | 0.693 |
| 2 s, 50 ms | 54 | 0.941 | 0.518 |

These ran before the final image settings. `suggest_config` balances the window against an
event's two resolutions, as three times `sqrt(duration / bandwidth)`: 108 ms on chirps.

### Where to cut the scores

Validation cannot say. Its tiles are masked, so it hardly produces a false positive, and its
F1 curve is flat where it matters. Mean over the 32 models of the simulation below, on two
recordings, on regions no model saw:

| Cut | F1 | Precision | Recall |
|---|---|---|---|
| Best any cut could do, with the answers in hand | 0.935 | | |
| 0.15 | 0.888 | 0.86 | 0.94 |
| **0.25** | **0.896** | 0.89 | 0.91 |
| 0.35 | 0.892 | 0.91 | 0.88 |
| 0.50 | 0.887 | 0.94 | 0.85 |
| 0.65 | 0.830 | 0.97 | 0.75 |
| Otsu on the model's own scores | 0.876 | 0.84 | 0.94 |

From 0.15 to 0.5 it is one plateau, and an early look at 8 models from one recording had
wrongly picked 0.5 out of it. The default is 0.25: a false positive is on the screen to be
rejected and a miss is not. The other tables here were measured at 0.5, the default then, and
say so. YOLOE has no such scale, and its scores are cut by Otsu on their logarithm.

### SAM

Median IoU between SAM's box and a person's, from a loose drag (a quarter too large on every
side) and from one click at the centre:

| | Chirp, drag | Pulse, drag | Syllable, drag | Chirp, click | Pulse, click | Syllable, click |
|---|---|---|---|---|---|---|
| MobileSAM | 0.374 | 0.445 | 0.615 | 0.176 | 0.073 | 0.021 |
| **SAM 2.1 tiny** | 0.506 | 0.564 | 0.551 | 0.186 | 0.114 | 0.026 |

On 4 CPU threads of the desktop, MobileSAM took 564 ms to encode and 38 ms per prompt, and
SAM 2.1 tiny took 821 ms and 46 ms. No mask level and neither image brought a click above
0.25.

**A click does not label an event.** SAM knows nothing of what an event is on a spectrogram:
it finds the blob under the pointer, such as one harmonic of one pulse. IoU against a person's
loose box may be the wrong yardstick, though, so this asks what such labels are worth, with the
same shots training a detector, F1 at the best cut:

| Labels from | Chirps | Cricket |
|---|---|---|
| Hand boxes | 0.828 | 1.000 |
| **SAM, from a loose drag** | **0.895** | **1.000** |
| SAM, from one click | 0.569 | 0.814 |

So the tool leads with the drag. A click also came back with the neighbouring event in its
mask, so `keep_prompted_parts` holds a click to the connected part under it and a drag to its box.

### Active learning

An oracle reviews windows and answers perfectly. Five shots, then 4 rounds of 5 windows,
training after each, scored at a cut of 0.5 on regions nobody reviewed. Mean of 2 sets of shots:

| Recording | Round | Windows | Labels | Rejected | Uncertainty first | Random order |
|---|---|---|---|---|---|---|
| 2019-12-09 | YOLOE | 0 | 5 | 0 | 0.754 | 0.754 |
| | 1 | 5 | 30, 17 | 14, 8 | 0.879 | 0.879 |
| | 2 | 10 | 49, 23 | 16, 14 | 0.852 | 0.875 |
| | 4 | 20 | 70, 48 | 22, 18 | 0.897 | 0.871 |
| 2020-06-03 | YOLOE | 0 | 5 | 0 | 0.650 | 0.650 |
| | 1 | 5 | 25, 15 | 62, 32 | 0.916 | 0.859 |
| | 2 | 10 | 39, 26 | 74, 40 | 0.925 | 0.863 |
| | 4 | 20 | 47, 37 | 127, 66 | 0.891 | 0.901 |

Labels and Rejected give uncertainty first, then random.

- **Most of the gain comes from the first round.** Further windows do not clearly add to it,
  on these few runs and these small test sets.
- **Uncertainty ranking is not shown to help.** Over all 16 trained runs of each, F1 at the cut
  was 0.894 against 0.880, and best F1 0.939 against 0.932. It costs nothing and does no harm,
  so it stays, but nothing here says it earns its place. It does show the reader more
  candidates to answer per window.
- On 2020-06-03 precision at the cut is 1.00 with recall about 0.81, while best F1 is 0.97-0.99.
  Most of what is missed there sits below the cut.

One window in five is taken blind (`review.pick_window`), so that the model's own idea of where
events are does not decide everything that is ever looked at.

### Augmentation

One region's labels, regions it had not seen, 3 seeds. A fixed cut is what a reader gets.

| | F1 at 0.5 | Mean | Best F1, mean |
|---|---|---|---|
| **Shipping** (no flips, mild scale, mosaic 0.5) | 0.891, 0.947, 0.914 | **0.917** | 0.932 |
| No mosaic | 0.885, 0.925, 0.796 | 0.869 | 0.913 |
| Time flip (`fliplr=0.5`) | 0.820, 0.635, 0.947 | 0.801 | 0.928 |
| Ultralytics defaults | 0.700, 0.874, 0.906 | 0.827 | 0.918 |

At the best cut these cannot be told apart. What the shipping set buys is scores that stay
meaningful at a fixed cut.

### Several resolutions as channels

An idea of the user's: a spectrogram has one intensity channel and a model takes three, so
stack the same audio at short, medium and long windows, with exact alignment. The picture and
the alignment measurements are published separately. Three seeds, F1 at the best cut on regions
never seen, from a few shots:

| | One resolution | Three as channels |
|---|---|---|
| Chirps, first shots | 0.828, 0.843, 0.857 (mean 0.843) | 0.885, 0.868, 0.818 (mean 0.857) |
| Chirps, later shots | 0.913, 0.879, 0.864 (mean 0.885) | 0.908, 0.942, 0.886 (mean 0.912) |
| Cricket | 1.000, 1.000, 1.000 | 1.000, 0.992, 1.000 |

With one class it leans toward the stack, inside the seed spread. The user's point is that this
is the wrong test, since one well-chosen window suits one class. It should matter when one
network has to find classes with different dynamics, such as chirps (about 174 ms x 134 Hz) and
rises (a median 17 Hz on 2019-12-09, decaying over about 4 s).

So one network was trained on both, at 50, 200 and 800 ms and on all three as channels. Each arm
had the same 27 rises and 221 chirps from fully labelled stretches, and was scored on the first
7000 s of 2019-12-09: 204 hand-labelled chirps and 42 rises. Mean over 5 seeds, 3 for 50 ms:

| | Chirps, best F1 | Rises, best F1 | Rises, F1 at 0.25 |
|---|---|---|---|
| 50 ms | 0.924 | 0.277 | 0.160 |
| 200 ms | 0.934 | 0.359 | 0.323 |
| 800 ms | 0.935 | 0.385 | 0.307 |
| All three as channels | 0.935 | 0.385 | 0.334 |

**On this pair the stack does not pay.** It ties a plain 800 ms window on both classes, and so the
plugin ships one resolution. The trade-off it would solve exists on one side only. Rises do want
a long window, but chirps do not want a short one: they are found as often at 800 ms as at 200,
and placed as exactly, with a median centre error of 12 to 13 ms at every window. That a long
window smears a chirp does not move its centre.

The tie holds when the saved detections are rescored under other rules. Stack against 800 ms on
rises: 0.399 and 0.402 with a 3 s match tolerance, 0.485 and 0.490 with the track's step at 3 Hz,
0.320 and 0.300 at 8 Hz. Box overlap ties too. The one thing left in the stack's favour is a
smaller spread between seeds, 0.022 against 0.062 on rises, and five seeds are too few to rely on
that.

Getting a fair answer took three corrections, and the first run gave the opposite impression.
With rises anchored on the analysis's index the stack led 0.659 to 0.575, on three seeds. But the
index lies up to about 3 s from the step on the image, so a model that put its box on the step
was scored wrong, and the analysis does not list every rise, so plain rises counted as false. Now
boxes start where the track climbs, and a found rise is right if its own track steps by at least
5 Hz and holds that for about a second (`TrackJudge`). That judge passes 92.9% of the listed rises and 4.3% of
random stretches of track. Of 12 detections also checked by eye, it passed all 6 clear rises,
failed the chirp and the 3 unclear ones, and split the 2 small rises. Labelling a random part of
the chirps had also taught the model that their neighbours were nothing: labelling whole stretches
took chirps at 50 ms from about 0.81 to 0.92.

What this does not settle: it is one pair on one recording, and the rises are silver and few, so
at 0.39 a ceiling from the training labels could hide a difference. And it measures finding an
event. A task that turns on fine structure at one scale and slow structure at another, such as
telling chirp types apart, is not this one and has not been tried. For a mix like this the plugin
would choose a 138 ms window on a 16 s tile from the median duration, which was not itself tested;
the nearest arm, 200 ms, is within the seed spread of 800 ms.

### One recording against several

A model trained on one recording's 90 labels scored F1 about 0.70 on other recordings, against
0.895 at its best cut for the old chirp detector. That is not a fair comparison and says nothing
about the architecture: the old model learned from 17 recordings, 7416 hand boxes and simulations.

To see how far a little variety goes, each recording was held out in turn and the model trained
on the hand labels of the other three, by the same rule as the old detector's benchmark:

| Held out | Boxes trained on | Chirps | F1 at 0.25 | Precision | Recall | Best F1 | At cut |
|---|---|---|---|---|---|---|---|
| 2019-12-09 | 131 | 204 | 0.680 | 1.000 | 0.515 | 0.840 | lowest kept, 0.01 |
| 2020-06-03 | 247 | 88 | 0.775 | 0.633 | 1.000 | 0.967 | 0.49 |
| 2022-05-12 | 313 | 22 | 0.811 | 1.000 | 0.682 | 0.818 | 0.09 |
| 2022-06-01 | 314 | 21 | 0.769 | 0.833 | 0.714 | 0.844 | 0.04 |
| Pooled | | 335 | 0.729 | 0.805 | 0.666 | 0.734 | 0.36 |
| Old detector, pooled | 7416 and simulations | 335 | 0.895 at 0.30 | 0.993 | 0.815 | | |

Three recordings and a few hundred boxes close little of the gap to the old detector: 0.70 to
0.73. That is not a contest, and it does not say how much further more recordings would go; only
that this much is not enough to expect a model to carry over untouched.

The table's other column is the useful one. Within a recording the events are ranked well, at a
best F1 of 0.82 to 0.97 (optimistic for the last two, with 22 and 21 chirps), but the cut that
gets there moves from 0.01 to 0.49. So the ranking carries over and the scale of the scores does
not. At the default cut one recording loses half its chirps, and over a third of what another
finds is false. On a new recording, review a window and move the cut, or label a little of it
and add it to the project, rather than trust a model's first run.

### Speed

| | |
|---|---|
| Whole 4-file session, 60.35 min, 2 channels at 48 kHz, 449 tiles | 14.8 s, 244x real time, GPU |
| The cricket on 4 CPU threads | 54x real time |
| Training, GPU, in the simulation (10th, 50th, 90th percentile) | 28 s, 75 s, 211 s, on a shared card |
| Training from 3 shots on 4 CPU threads, 48 tiles, 34 epochs | 311 s |
| Rewriting and redrawing 5000 candidates | 62 ms and 54 ms |

CPU figures are 4 threads of a desktop, and a T470s will be slower. Training length is set in
batches (`epochs_for`): fifteen epochs of 24 tiles were 30 batches, and left a model with
mAP50 0.06.

## What it cannot do

- It sees the spectrogram only. The template matcher could also match on the waveform, and that
  is gone with it.
- It needs a model file and a first download of about 120 MB. The matcher needed neither.
- Its images ignore audian's spectrogram settings and denoisers. An event visible only after
  denoising may not show to the model.
- The image settings are chosen from the first labels. A class far from them may fit no tile,
  which `Train` reports. They can be changed only by editing the project file.
- Outlines hold one polygon each, so an event in several separate parts keeps only its largest
  part as an outline. Its box covers them all.
- A `Run` has nothing to resume from if it is stopped.
- Closing the tab stops what is running, without asking.

## Licences

Ultralytics and its YOLO weights are AGPL-3.0, and a model trained with it falls under that by
default. audian is GPL-3.0, which is compatible with it. The plugin runs them in a separate
process. Anyone distributing this, or offering it as a network service, should read the AGPL
first. SAM 2's weights are Apache-2.0, and SAM 3's are gated, which is one reason it was not used.

## Sources

- Ultralytics documentation: [SAM](https://docs.ultralytics.com/models/sam/),
  [SAM 2](https://docs.ultralytics.com/models/sam-2/),
  [SAM 3](https://docs.ultralytics.com/models/sam-3/),
  [YOLOE](https://docs.ultralytics.com/models/yoloe/),
  [YOLO26](https://docs.ultralytics.com/models/yolo26/).
- Few-shot bioacoustic event detection at DCASE, for the five shots:
  [2022](https://arxiv.org/abs/2207.07911), [2023](https://arxiv.org/abs/2306.09223).
- The chirp benchmark and its known issues are in the `chirp_competition` analysis repository.
