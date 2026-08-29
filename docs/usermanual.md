# Audian user manual

...

## Full trace overview

In the bottom the full traces are displayed for navigation. Some
computations are needed before they can be displayed. For long
recordings, this processing can last quite a long time. This is
annoying, since this slows down any user interaction.

Two mechanisms help to avoid this:

1. Once the data are processed, the result is stored in the cache
   folder. When you later open the same recording, then no more 
   processing is needed since the processed data is simply loeded from
   the cache.

2. You may generate the processed data in advance via the
   `audian-compress`command line tool. Simply call it with the data
   file(s) as argument(s) and it will generate a file with
   `-fulltrace.wav` added to the file's name inside the same folder
   as the data file. `audian` then uses this file for displaying
   the full traces.

Note, that for short files no processed file will be produced, since
it can be computed quickly enough.

Note, although the `-fulltrace.wav` are wave files, the information
they store are the minima and maxima within data segments. Since they
heavily downsample the data, the sampling rate can drop blow 1Hz - the
smallest rate a wave file can store. Therefore the sampling rate is
multiplied with 1e3 or 1e6.


## Screenshots

With audian you can easily generate screenshots of interesting
snippets of the data and you can use screenshot files to navigate to
these snippets later on.

Pressing `Ctrl + Alt + S` takes a screen shot of the audian window . A
file dialog pops up that allows you to set the name of the resulting
png file. The default file name for the screenshot file is
`screenshot.png`.  You may change the file name to any name you want
to describe the screenshot. But it needs to be a PNG file.

Audian stores the name and position of the displayed data file in the
PNG file's metadata.


### Go to the position shown in a screenshot file

If you drag a screenshot file onto the audian window, then audian
moves the displayed window to the position shown in the
screenshot. The window position is taken from the screenshot's
metadata.

## Navigator: waveform or activity

The strip below the data plots summarises the whole recording. It has two
overviews, toggled with `Alt+F6` (*Panels -> Navigator: activity*).

**Waveform** (default) draws the true min/max envelope of every pixel column.

**Activity** answers a different question: *where in this recording did
something happen, and what kind of something?* A min/max envelope cannot tell
you that, because a single transient — an eel pulse, a bat click — saturates
its bin exactly as a continuous signal of the same peak amplitude does. One
pulse and a thousand pulses draw the same bar.

The activity overview plots two quantities per bin instead, both in dB above
**one global noise floor** estimated from the whole recording:

- a filled band up to the bin's **RMS excess** — sustained energy: a cricket
  chirp, a bird phrase, a wave-type EOD. A lone transient contributes only
  `A²/N` to a bin of `N` samples, so it barely moves this;
- a spike up to the bin's **peak excess**, drawn only where the bin is
  classified transient — the crest a delta-like event produces and a
  continuous signal does not.

So a raised band is sustained activity, thin spikes over a flat band are
transients, and both together are both.

The reference level is deliberately global rather than per-bin. A per-bin
baseline renormalises every bin to look equally busy, which destroys the one
comparison the strip exists to support: a quiet stretch must stay visibly
quiet.

This assumes at least ~10% of the recording is baseline, which holds for field
recordings. A recording that is wall-to-wall signal has no noise floor to find,
and will measure as quiet; the navigator falls back to the waveform envelope
whenever the statistics are unavailable.

The per-bin statistics are cached beside the min/max overview as a `.stats.npy`
sidecar. An overview cached before this existed is recompressed once, and both
are written together from then on.


## Daylight mode

Audian ships two themes. The default is dark. `Ctrl+Shift+L` (*View → Daylight
mode*) switches to the high-contrast light theme; the choice is remembered
between sessions, and `audian --theme light` sets it for one run.

Daylight mode is not a polite inversion of the dark theme — it is built for
reading a laptop screen in direct sun, where the display is competing with the
sky:

- plot and page grounds are pure white, because under glare luminance is the
  entire budget and any tint spends it;
- there are no mid-greys. Even the faintest text is held above 6:1, where the
  dark theme lets decoration sit lower;
- traces are dark and saturated rather than bright. The dark theme's cyan
  scores 1.4:1 on white; the daylight blue scores 8.4:1;
- unselected traces are dimmed only as far as 4.5:1, against 3:1 in the dark
  theme. Glare raises the effective black level, so a line that measures 3:1
  on the bench is less than that on a riverbank;
- spectrograms use a separate set of colormaps that run from a neutral white
  noise floor to a dark high end — the long-standing convention for printed
  spectrograms, and the only orientation that stays readable in sun. The dark
  theme's maps are not simply flipped: reversing viridis or magma would put
  their saturated yellow end at the noise floor, which is most of the image.

Everything re-themes live, including the plots, the navigator, the spectrogram
colormaps and the toolbar icons. Nothing needs restarting.


## Tabs

Open files are listed down the **left** edge as a narrow upright spine, not
across the top. Two axes are bought this way: the strip comes off the top,
where a stacked waveform view is short of room, and turning the tabs upright
costs about 28 px of width instead of the ~180 px a column of horizontal tabs
needs for its labels. On a sixteen-channel file that is worth two extra pixels
of lane height per channel and the full window width for the data.

Labels read bottom-to-top, the usual direction for a left-hand spine.

With a single file open the strip hides itself entirely.

`Ctrl+PgUp` / `Ctrl+PgDown` move between tabs, `Ctrl+W` closes the current
one, and each tab carries a close mark on its right.


## Playback

`Space` plays the visible window; dragging a region in Play mode plays that
region.

**What gets played** is set by *Audio → Source* in the parameter bar, or
`Shift+P`:

- **selected channel** (default) — the current channel alone, in mono. This
  is what you want on an electrode array: averaging eight electrodes into one
  ear is not a signal anybody needs to hear.
- **channel pair (L/R)** — one channel in each ear, chosen explicitly in the
  **Pair** row that appears below. Nothing is averaged, and the pair does not
  follow visibility: hiding a lane never silently changes what you are
  hearing, which is the point of choosing it by hand.
- **all shown (stereo mix)** — every visible channel, the first half averaged
  into the left ear and the second half into the right. Useful for a stereo
  field recording, which is what the original behaviour assumed. On a
  16-electrode file this is the mean of eight electrodes per ear, so it is
  rarely what you want there.

`Shift+P` steps through the three.

The playback cursor runs only on the channels actually being heard.


## Axis labels

The amplitude axis is labelled with the unit the recording carries in its
metadata — `amplitude (mV)`, `amplitude (a.u.)` and so on. A wav with no unit
metadata reads as `a.u.`, which is a real statement about the recording rather
than a missing value, so it is shown.

A spectrogram's y axis is labelled `frequency (kHz)` the same way, rescaled by
pyqtgraph as you zoom. Its *amplitude* is its colour, so that scale lives on
the colour bar, in dB.

In a dense stack the per-lane axis is collapsed to reclaim its width, and the
unit rides on the stack's single amplitude readout in the bottom-left corner
instead — or, for a spectrogram, in its corner caption.

Note that only genuine SI units are rescaled and prefixed (`V` → `mV`).
Anything else is shown verbatim: pyqtgraph will happily prefix any string it
is handed, which turned `a.u.` into `ma.u.` with every tick multiplied by a
thousand.


## Showing a channel that `-c` left out

`-c 0,8,15` sets which channels are *shown*, not which exist. Every channel
in the file is still there, listed in the channel menu behind the **ch**
button at the right of the tool bar, and `Alt+0` … `Alt+9` toggle the first
ten directly.

When some channels are hidden the button says so — `ch 0  3/16` — so the
other thirteen are not invisible in both senses at once.


## Selecting a channel

Click anywhere in a channel's row — the trace, the spectrogram, or its card
in the rail — and that channel becomes the current one. Shift-click extends
the selection to a range, exactly as it does in the rail.

Right-click and drag are unaffected: dragging still selects a region and
right-click still opens the region menu.


## The mean spectrogram

`F2` gives the lanes over to spectrograms and takes the traces away. On a
sixteen channel array that is sixteen readable spectrograms, 1952 px of
stack in a 500 px viewport, four on screen at a time — and a hundred
milliseconds of `decibel()` every time the view leaves what is uploaded.

`Shift+F2` replaces them with **one** spectrogram, full height, of the mean
power over the channels. It costs 8.7 ms instead of 103, the stack is 500 px
with nothing left to scroll, and on an electrode array it is sharper than
any of the panels it replaces: averaging pushes the noise floor down while a
signal that is coherent across the array stays where it is. Measured on a
16 channel eel recording, 99.9th percentile minus median is 23.50 dB for one
channel and 46.25 dB for the mean.

The panel is captioned `MEAN 00-15` rather than with a channel number, and
the colour bar is refitted — same noise floor, twice the span. The pointer
readout under it reports the same average the picture is drawn from.

**Solo and mute choose what is averaged.** Solo channels 3 and 5 and the
caption becomes `MEAN 03,05`; mute the electrodes that were not in the water
and they leave the average. The channel rail goes off screen while the mean
is showing — its rows are per-channel controls and there are no per-channel
lanes left — so narrow the selection first, or press `Shift+F2` again to get
the rail back. Your `F7` setting is not touched either way.

`Shift+F2` from the ordinary view turns the traces off on the way in and
puts them back on the way out, so pressing it twice returns you exactly
where you started. Turning the traces back on with `F2`, or the
spectrograms off with `F3`, leaves the mode: a mean over the array beside
one channel's waveform would be two pictures of two different things in one
lane.


## Amplitude operations and the Y mode

Reset (`Shift+V`), Center (`C`), Fit Y (`v`) and the amplitude zoom steps
apply to **every channel** while the Y axis is shared — which it is by
default, and which the tool bar reports as *Y: shared*. Under a shared Y
every lane shows the same span by definition, so an operation on one lane is
an operation on all of them.

Switch the Y axis to per-channel and the same operations apply only to the
selected channels instead, which is what selecting a range of lanes with
shift-click is for.


## The frequency axis, and the band a spectrogram opens at

The two y axes now have the same vocabulary. `v` fits the **amplitude** axis
and `Shift+V` takes it out to the recording format's full scale; `Ctrl+V`
puts the **frequency** axis back to the band the spectrogram opened at and
`Ctrl+Shift+V` takes it out to the whole band, 0 Hz to Nyquist. That is the
`Ctrl` pairing the zoom keys already use — `+`/`-` for amplitude,
`Ctrl++`/`Ctrl+-` for frequency.

**Double clicking a y axis does whatever that lane's bare key does.** On a
trace it fits, the way `v` does. On a spectrogram it returns to the opening
band, the way `Ctrl+V` does. There is one exception and it is deliberate: in
*Y: fixed ±1* the lane opened at ±1, so the gesture goes back to ±1 rather
than refitting and quietly dropping you out of a mode the tool bar is still
claiming.

There is no `Ctrl+C` to centre a frequency axis. `Ctrl+C` is the cross hair,
and centring a range whose floor is 0 Hz can only widen it — measured, both
1200–2400 Hz and 3000–3500 Hz "centre" to 0–4000.

### Opens at

Most recordings are opened to look at one band and no other. The
**Spectrogram** tab's *Opens at* field says which: set it to 2 kHz and every
spectrogram lane opens showing 0–2 kHz instead of 0–Nyquist, in this window
and in every window afterwards. It is remembered in `settings.json`, so it
is typed once.

**It is a default view and not a limit.** The axis still zooms, pans and
`Ctrl+→`s all the way to Nyquist, the deepest zoom is unchanged, and
`Ctrl+Shift+V` shows the whole band in one key — which the field's own tool
tip says, because a preference that hid part of a recording with no visible
way back would be a trap rather than a convenience.

The number is absolute hertz, not a fraction of Nyquist. 0–2 kHz is a
statement about the fish, so it should mean the same 2 kHz on an 8 kHz
recording and on a 96 kHz one. A band larger than the recording's Nyquist is
clamped to it rather than refused, because a preference outlives the
recording it was written beside.

Audian ships with no band set, so nothing changes until you type a number.

> **One caveat for wideband recordings.** The colour ramp is still fitted
> over the *whole* frequency axis, not over the band you are looking at, so
> a 96 kHz recording opened at 0–2 kHz is coloured by statistics dominated
> by the 94 kHz you cannot see. Narrow bands on wideband files may want the
> colour bar dragged by hand until that is settled.


## Annotations from a session bundle

A **session bundle** is what a fakefish run writes beside its recording: a
`*_metadata.toml` naming the recording and carrying the fit that maps the
device's clock onto it, plus one CSV per kind of row — the trials of the
protocol, every pulse the stimulator emitted, every pulse the recording was
found to hold, the localization runs, the session events, and the control
settings. Audian reads the lot and draws it over the recording.

```sh
audian -a PULS0002_metadata.toml DR0000_0087.wav
audian -a /data/exp2/PULS0002 DR0000_0087.wav      # the directory works too
```

Without `-a`, Audian looks beside the recording for a `*_metadata.toml` whose
`[alignment]` block names *that* recording, and opens it only then. A stray
bundle from a neighbouring experiment is exactly the mistake that would put
every mark in the wrong place while looking entirely normal, so the check is
not optional. It is never silent either: the status bar says what was opened,
what it holds, and anything that did not add up.

Positions come from the `recording_time_s` column — seconds from frame 0 of
the recording — and from nothing else. Everything else in a row is either
provenance or the evidence behind it, and both reach you through the pointer
readout and the tool tips rather than through the drawing.

**Every mark is full height.** A span is bounded in x and in x only: its
interior is tinted under the waveform and its start and end are drawn as two
full-height lines over it. A point is one full-height line. There are no
tracks, no lanes within a lane and no stacking, on any surface — layers
overlap, which is why switching them on and off is the gesture the panel is
built around.

### The Annotations panel

The bottom bar grows an **Annotations** group while a bundle is loaded:

- **Source** — the session, the channel the fit was made against, and a badge
  saying how far its positions may be believed. Hover the badge for the fit
  itself: scale, offset and drift.
- **Show** — the master switch, then one chip per place the marks can go:
  **Traces**, **Spectrograms**, **Navigator**. The same three sit in
  *View ▸ Annotations*, and the two always agree. The rest of the row is the
  **pointer readout**: the span the pointer is standing *inside* and how far
  into it you are, then the nearest instant and how far away it is. A trial
  you are in the middle of is reported as a trial you are in the middle of,
  never as a mark tens of seconds away.
- **Sent** and **Heard** — one chip per layer of the bundle. Sent is what the
  stimulator was told to do, Heard is what the recording turned out to hold
  and what the device logged about itself. The chips are drawn with the pens
  and brushes the plot uses, so a span layer's chip looks like a span and a
  point layer's like a tick, and the strip doubles as the legend. Their
  counts are in the tool tips.

You will usually want one or two layers at a time, so the chips work the way
the channel rail does:

- **Click** a chip and that layer is the only one drawn.
- **Click it again** and the set you had before the solo comes back — exactly
  that set, not every layer there is. Three of the ten are off to begin with
  for good reasons (localization runs alone cover 59 % of a session), and a
  round trip through a solo must not be the thing that switches them on.
- **Ctrl- or shift-click** to switch one layer on or off beside the others.
- **All**, at the head of the Sent row, is the way to every layer at once;
  `Shift+F8` does the same from the keyboard, and its tool tip says how many
  layers are currently hidden.

Every layer also has an entry in *View ▸ Annotations ▸ Layers*, carrying its
name and its row count, and the menu and the chips always agree.

Which layers you left on is remembered between sessions, per layer, together
with the three surface chips. A layer the settings have never seen — one a
newer audian added, or one this bundle carries and the last one did not —
comes up as the bundle says it should rather than silently off. The `F8`
master is deliberately *not* remembered: it is a glance, not a setting.

`F8` hides and shows the whole overlay. `n` and `Shift+N` step the view to
the next and previous annotation of a layer that is shown — which is how you
check the overlay against the audio: step to an event, zoom in, and see
whether the line sits on the pulse.

### The control track

The **Controls** chip is not an overlay. The stimulator's settings are not
events, they are values held between changes, so switching that chip on opens
a strip of its own between the lanes and the time axis, sharing the same time
axis and nothing else. It is off to begin with and takes no room at all until
you ask for it.

Each setting the session actually varied gets a band with its own scale, and
the band's label carries the real numbers, because a tick rate and a
randomness share no axis with each other or with a waveform. A setting that
never changed gets no band and the line under the strip says which one and
what it was held at — the difference between a channel the device never
wrote and a channel that simply held still is worth seeing.

The line is a **staircase**: a value stands until the next change row, however
far away that is. A window in the middle of a quiet half-minute shows the
setting that was in force there, not a blank. Where the line is **missing**,
nothing was in force — at the very start, before the device had read the
receiver, or wherever the log recorded no value. That is not the same as a
setting of zero, and it is not drawn like one.

### The navigator strip

The navigator shows the whole session at once, and it draws exactly what the
lanes draw: the same full-height marks, in the same hues, spanning the whole
row. That is what makes it readable as a map — a stretch of session with no
volley trials in it is a stretch with no volley ink in it, and the region
handle tells you where you are looking.

Turn it off with the **Navigator** chip if you would rather see the bare
envelope.

### Observed and predicted are drawn differently

A **matched** row was found in the recording. It is drawn as a solid
full-height line.

An **unmatched** row was not. Its time is where the fit says the pulse should
be and nothing in the recording confirms it — which is exactly why its
`detected_time_s` column is empty. It is drawn full height as well, but
**dashed**, with a hollow diamond cap at the top, so it can never be mistaken
for something that was seen. Full height rather than a short stub: the marks
are bounded in x and only in x, everywhere, and the difference is carried by
the dash and the cap.

exp2 has 7 predicted pulses out of 2187; exp3 has 629 out of 5281, so on a
long session this is not a rare corner.

### Where a recording's files are joined

A long recording usually arrives as several files, and Audian opens them as
one. Where two of them butt together it draws **one thin quiet rule down the
lane**, in the same ink as the zero line, on every lane and on the navigator.
It is chrome, not data: it sits under every annotation, and it is there
whether or not any bundle is loaded, because a join is a fact about the files.

Its position comes from the files themselves, never from a bundle. If the
loaded bundle declares what the recorder lost at each join, that figure is
printed beside the rule on the lane you are reading — exp3 declares +32 ms,
+32 ms and −120 ms, and 120 ms is about thirty pulses of a volley. Audian
states the gap and does not correct for it: no mark is ever shifted by it, and
a join the loader did not report is never invented. If the bundle names a
different number of joins than the recording has, nothing is labelled and the
status bar says why.

### How far to trust the marks in front of you

The badge in the **Source** row carries the fit, and its tool tip carries the
fit's residual **per region of the recording** — per file where there are
joins, in eight bins otherwise, with the spread and how many of that region's
pulses the recording actually confirmed. The one number in the header is not a
promise about the part you are looking at: exp3 reports a median residual of
about a microsecond for the whole session, and only 259 of the 874 pulses in
its last file matched at all. A region whose residual is far outside the fit's
own match tolerance is said out loud in the status bar when the bundle is
opened, as well as sitting in the tool tip afterwards.

### An unvalidated alignment is never shown quietly

Every mark's position comes from the fit in `[alignment]`. If that fit is
wrong, every mark is in the wrong place *and still looks fine*. So when
`validated` is anything other than a real `true` — absent, `false`, or the
string `"true"`, which is a writer that did not follow the format — Audian
does two things: it badges the panel **UNVALIDATED** in red and warns in the
status bar, and it draws every mark broken instead of solid. Observed and
predicted stay distinguishable from each other; both stop looking like
statements of fact. A bundle that is validated but whose writer recorded
warnings badges **WARNINGS**, and the warnings themselves are in the tool tip
and in the status bar.

A bundle fitted against a different recording badges louder still, as **WRONG
RECORDING**, and nothing is drawn at all.

### Long sessions

Only what is in the visible time range is drawn, and inside that range marks
closer together than one screen pixel are collapsed to the one line they would
have painted anyway. A session with half a million rows costs a few
milliseconds a frame, and the count in a chip's tool tip is always the number
of rows the layer holds, not the number of marks on screen.
