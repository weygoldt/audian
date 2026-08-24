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

Open files are listed down the **left** edge, not across the top. Vertical
space is the scarce axis for a stacked waveform view; a horizontal strip
spends about 30 px of height on what is usually one or two entries, and there
is more width to give. Labels stay horizontal — Qt's own vertical tabs rotate
them ninety degrees, which makes the file name, the entire point of the tab,
unreadable at a glance.

With a single file open the strip hides itself entirely.

`Ctrl+PgUp` / `Ctrl+PgDown` move between tabs, `Ctrl+W` closes the current
one, and each tab carries a close mark on its right.
