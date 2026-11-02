# Todo

The working list. Finished items come off, and their reasoning stays in the
commit that finished them. Two are worth doing before the rest, in the order a
reader would feel them:

1. **The colour ramp is fitted over the whole frequency axis**, so a wideband
   recording opened at a narrow band is coloured by statistics nobody can see.
   Most likely of these to be reported as "the spectrogram is broken", and it is
   the oldest unaddressed one.
2. **F2 is a one-way door**: its first press forces the spectrogram on, and the
   state the window opens in cannot be reached with that key again.

## Spectrogram

- [ ] The colour ramp is fitted over the **whole** frequency axis, not over
  the band on screen: `visible_block` crops in time only
  (`spectrogramplot.py`), and `estimate_noiselevels` takes its floor from
  `db[:, -nf:]`, the top 1/16 of the full axis
  (`bufferedspectrogram.py`). So a 96 kHz recording opened at 0-2 kHz by
  the *Opens at* field is coloured by statistics dominated by 94 kHz
  the reader cannot see. This is the thing most likely to be reported as
  "the new setting broke the spectrogram", and it should be settled before
  a narrow band is recommended for wideband files. Not measured -- the test
  fixture is 8 kHz and the effect needs a wideband recording.

- [ ] **The large spec panel should take the MAX over channels, not the mean.** The
  complaint is right and the fix first proposed is not: a signal that only ever
  sits on one or two electrodes -- a weakly electric fish moving over the grid --
  is averaged away by a panel that stands for sixteen. But *sum divided by the
  channel count is the mean*, exactly, so that swap is a no-op;
  `6e1d38c` records the same fact from the other side, that the sum and the mean
  are "the same picture 12.04 dB apart", and 10*log10(16) = 12.04.

  Neither reduction helps the single-electrode case. With signal power `P` on one
  electrode and noise `n` on the other fifteen: the mean gives `(P + 15n)/16`, so
  the signal drops 12 dB and the floor stays put; the undivided sum gives
  `P + 15n`, so the signal stays and the floor climbs 15x. One lowers the signal
  and the other raises the floor.

  **Max over channels per time-frequency bin** is the one that does what is
  wanted: a single-electrode signal keeps its full amplitude and the floor only
  rises by the max of N noise draws. The reduction itself is small, in
  `bufferedspectrogram.channel_power`, but the things downstream were tuned to
  the mean and each needs re-measuring: `BufferedSpectrogram.estimate_noiselevels`,
  `SpecItem.noise_levels` (whose docstring records "the mean's floor lands 2.3 dB
  from a single channel's and its top 37.5 dB from it"), `NOISE_FLOOR_MARGIN_DB`,
  and `SpecItem.get_power`, which has to keep agreeing with the pixel under the
  cursor. The power curve takes a mean of cached column sums
  (`spectrogramplot.py`), and a per-bin max cannot be derived from sums. The
  panel caption says `MEAN 00-15` and would have to say something else.
  Worth considering whether the reduction should be a choice rather than a rule.
  Good test case: `logger09-20250916T164744.wav`, where electrodes 08-11 are
  recorded as exactly zero.

- [ ] Synchrosqueezing, and a way to play with its parameters.  Denoising itself
  has shipped (`denoise.py`, `audian_plugins/denoisers`).  ssqueezepy is not a
  dependency and would be the first one added for a single feature.  Two things
  the smoothing work settled that this should reuse rather than rediscover: a
  filter that changes the drawn numbers has to be fitted by
  `SpectrogramPlot._level_range` too, or the panel dims instead of sharpening
  (the loudest bin drops 5 dB under a Gaussian of one bin and 15 dB under two);
  and `SpecItem.get_power` has to read the drawn pixel rather than the raw bin,
  because the two are a median of 3.0 dB and up to 50.7 dB apart, the worst of
  it at the chirp onsets a reader points at.

- [ ] Check the spectrogram backend.  torch could be faster.

## Traces

- [ ] **Denoising on the trace itself**, not only on the spectrogram.  The
  reader's own words: "Add a drop down menu for spectrogram smoothing (None,
  gausisan, Bicubic, etc) **and also denoising on the trace itself**".  The
  spectrogram half shipped; this half is untouched, and it is a different
  problem rather than the same one applied to another panel.

  `smoothing` is a *display* filter: it is recomputed per view, thrown
  away on the next pan, and `SpecItem.get_power` was changed so that even
  the readout follows the pixels rather than the buffer.  Nothing
  downstream can mistake it for the recording.  A denoised **trace** is
  not like that -- it is the thing the reader then measures, labels, and
  plays -- so the first decision is whether it is a view (cheap,
  reversible, and visibly not the data) or a derived stage (its own
  buffer, its own name in the trace menu, and something the label overlay
  and the audio path have to be told about).

  If it is a stage, the machinery exists and should be copied rather than
  reinvented: `buffereddata.py` holds the base, `bufferedfilter.py` is the
  worked example of a stage whose parameters the side panel drives,
  `data.py` is where a stage is registered and `traceitem.py` draws the
  result.

## Keys and pointer

- [ ] **F2 is a one-way door.**  Low priority, and *not* the bug it was
  found under: the report was "I cannot toggle off the spec", and F3
  (`toggle_spectrograms`) does exactly that, from the mean mode too --
  measured, from ``traces=0 specs=1 mean=1`` it gives ``1/0/0``.  That part
  was a misremembered key, Shift+F2 being `toggle_mean_spec`.

  Driving the three keys to check it turned up something that is not a
  misremembering.  From the opening state, printing
  `show_traces` / `show_specs` / `mean_spec` after each press:

  ====================  =====================================================
  key, pressed 4x       states, from ``traces=1 specs=0 mean=0``
  ====================  =====================================================
  F2                    ``0/1/0``, ``1/1/0``, ``0/1/0``, ``1/1/0``
  F3                    ``1/1/0``, ``1/0/0``, ``1/1/0``, ``1/0/0``
  Shift+F2              ``0/1/1``, ``1/1/0``, ``0/1/1``, ``1/1/0``
  ====================  =====================================================

  **F2 never comes back.**  Its first press turns the traces off, and
  `toggle_traces` forces `show_specs = 1` so the lanes are not left empty;
  from then on F2 oscillates between "spectrograms only" and "both", and
  the opening state -- traces alone -- is unreachable with that key.  A
  reader who pressed F2 once has a spectrogram they did not ask for, and
  getting back to where they were needs a different key.

  The fix that fits what is already here is to make F2 a round trip the way
  Shift+F2 already is one: `set_mean_spectrogram` remembers
  `traces_before_mean` and restores it, and `toggle_traces` wants the same
  memo for the spectrogram it switched on.  Then F2 is traces-only ->
  spectrograms-only -> traces-only, F3 stays the explicit switch, and no new
  key is spent.

  One inconsistency to settle while in there: the "never leave the stack
  empty" rule lives in the *toggles* and not in `set_panels`, so it is not
  an invariant.  Measured, `set_panels(traces=False, specs=0)` returns
  ``0/0/0`` and draws the empty stack the toggles exist to prevent --
  reachable from a plugin or from a linked tab.

- [ ] `hover_panel` is never cleared once the pointer leaves the stack: it
  is set to None only inside `mouse_moved` (databrowser.py), which does not
  fire on a Leave. So `Ctrl++`/`Ctrl+-`, which go through
  `pointer_axes`, already act on the last lane the pointer visited after a
  move onto the side panel. Pre-existing; found while deciding that the
  frequency fit/reset keys should take `Panel.frequencies` rather than
  `pointer_axes`, which is why they do.

## Window and settings

- [ ] StartupPage is the window's floor: three fixed columns capped at 1100.
  Measured with `tests/measure_chrome.py` and four channels, the page's own
  minimum and the window's are both 695 px, against 381 for the tool bar and 341
  for the tabs.  It is next if audian ever has to tile into half a laptop panel.

- [ ] Only the light/dark bit is taken from the desktop, not its accent.
  Qt 6.6 exposes `QPalette.ColorRole.Accent` and it would be the one
  desktop colour worth adopting -- it is the reader's stated preference and
  it appears in exactly one audian role, `primary`.  It needs the contrast
  repair that already exists for it (measured, Breeze's accent scores 3.71
  on the window ground, under audian's 4.5 bar), and it needs a decision
  about whether a repaired accent that no longer matches the desktop's is
  better than one that does.

- [ ] Chrome margins are balanced for the tool bar and the status bar only.
  The plot rails were not looked at.  If anything else reads as crammed,
  `BAND_PAD_V` is the number to reach for, and the band's own height has to
  be derived from it: the tool bar's bug was that it was not, so the layout
  paid for the shortfall out of the padding.

- [ ] The rest of the unpersisted state, if it is wanted: panel visibility
  (`show_traces`, `show_specs`, `show_powers`, `mean_spec`, `overlay_traces`),
  the y-range policy, the grid mode, the cross hair, rail visibility, the audio
  group and the eight cross-tab `link_*` switches.  The side panel's own width
  and open state are done, under `side-panel` v1.  Filter cutoffs are the
  awkward ones and were left alone deliberately: `BufferedFilter.open`
  resets all three unconditionally, so the only correct place to restore
  them is `DataBrowser.open` after the loader, which is where -f/-l already
  land -- so persisting them means deciding the CLI-versus-settings
  precedence, and that is a decision rather than a mechanism.  Channel
  selection is per-file and needs the clamp `open()` already applies.

## Plugins

- [ ] An eeltracker plugin.  The interface exists (`audian.pluginapi`) and
  wavetracker runs through it; eeltracker is the other one to visualise
  interactively in audian.

## The test suite

- [ ] **`theme.collect_orphan_widgets` can segfault the test suite.** It
  walks a snapshot of `QApplication.topLevelWidgets()` and calls
  `setParent(holder)` inside that same loop, so a reparent can make Qt
  destroy a widget a later iteration then dereferences -- and a dangling
  wrapper is a segmentation fault, not a `RuntimeError`. Measured while
  adding the band tests: pristine master ran the full suite 747 passed with
  no crash; the same branch with the band tests in a module of their own,
  which cost two more windows in the process, segfaulted in two runs out of
  four, both times at `widget.parentWidget()` (theme.py, the
  `if widget.parentWidget() is not None or widget.isVisible():` line), once
  while building `test_parameterbar`'s fixture and once while building the
  band module's own. The same three modules run alone pass, 165 of them, so
  it is the whole suite's accumulated widget state and not any one module.
  Worked around rather than fixed -- the band tests reuse a fixture that
  already exists, so the suite builds no more windows than before -- because
  the fix is a two-pass rewrite of that function (read, then reparent) and it
  deserves its own commit and its own measurement.

  Re-measured on 2026-09-25: with `test_analyzer` adding a browser per test,
  three full runs were clean.  A `shiboken6.isValid` guard was considered and
  not added: a *segfault* at `parentWidget()` means shiboken did not know the
  object was gone, and then `isValid` returns True as well, so it could not
  catch this crash.  Leave the "do not add browser modules" comments until it
  reproduces.

## Decided, not bugs

Measured, decided, and recorded so they are not rediscovered as bugs.

- `Ctrl+Shift+V` reaches the window action while focus is in a text field
  (measured: `Ctrl+V` fires it 0 times, so paste survives; `Ctrl+Shift+V` fires
  it once).  The only visible editable field in the main window is
  `ChannelRailRow`'s electrode label, and the owner does not rename channels --
  numbers are enough -- so this is accepted rather than fixed.  Revisit only if
  electrode labels start being used.
