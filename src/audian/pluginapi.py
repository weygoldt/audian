"""What a plugin may depend on.

Everything here is what audian promises to keep working for code it does not
ship.  A plugin that imports only from this module can be lifted into a
repository of its own and keep running; one that reaches into
`databrowser`, `labels` or `tasks` directly is holding on to internals that
move whenever the window does, and will break on a release it was not
present for.

This is a re-export and not a wrapper.  Wrapping would mean a second surface
to keep in step with the first, and the point is to have fewer of those, not
more -- so what a plugin gets is the real class, named here so that moving
it later is a change with one obvious place to make it.

The window itself is not here.  A panel factory is handed a `DataBrowser`
and the useful half of it is too large and too much in motion to promise:
what a plugin can rely on is that a browser has `labels`, `data`, `notify`,
and the redraw calls a label edit needs.  `PLUGIN_BROWSER_ATTRS` names them,
so a plugin can check rather than discover the answer by raising in front of
a reader.

Building an interface
---------------------

`ParameterGroup` is the framed, captioned group the built-in tabs are made
of; `add_row` puts a labelled row in it, `expanding` marks the one field per
row that should take the leftover width, and `narrow_combo` stops a combo
box publishing its longest entry as a minimum width.  Panels in the side
bar are narrow, so build with ``ParameterGroup(title, self, caption=False,
narrow=True)`` -- the tab already carries the name, and stacked captions are
what keeps a group under the panel's 220 px floor.

Drawing on the spectrogram
--------------------------

`browser.spectrogram_axes()` is the lanes, one per channel.  A plugin that
marks something in *frequency* -- a tracked band, a ridge, a harmonic stack
-- needs them, and the walk that finds them (over `panels`, skipping the
spacers and the power plots, then over each `Panel.axs`) is internal shape
that would break the first time a panel kind is added.

Add to a lane the way `labeloverlay` does: ``ax.addItem(item,
ignoreBounds=True)``, because a bare `addItem` joins the lane's
`childrenBounds` and moves its auto-range somewhere no data is; and redraw
from the view box's ``sigRangeChanged`` rather than on a timer.  Grow the
items once and hide the spare ones, rather than clearing and rebuilding on
every pan -- a redraw that allocates is the difference between a plugin that
scrolls and one that stutters.

Reading a recording
-------------------

`open_files` is the opener the browser itself uses, and a plugin that reads
audio should use it rather than `thunderlab.DataLoader` directly: a session
is often several files joined by their timestamps, and the bare loader
applies a continuity heuristic that silently drops some of them.

Do **not** read `browser.data.buffer`.  It is a window that moves, and
`set_times` shifts it in place after cancelling whatever was reading it.

Denoising a spectrogram
-----------------------

`Denoiser` and `Parameter` are what an ``audian_*denoisers`` factory
returns.  A denoiser is handed a ``(time, channel, frequency)`` block of
power, the frequency of each bin, and its own parameter values; it must be
pointwise in time, because the spectrogram is transformed in chunks.  See
`audian.denoise` for the whole contract and `audian_plugins.denoisers` for
two written against it.

Working off the GUI thread
--------------------------

`CancelToken` and `Cancelled` are audian's cancellation vocabulary; a long
loop calls `token.check()` and lets `Cancelled` leave the worker.  The task
manager itself is deliberately absent: its job signal is connected to every
registered worker, so a plugin posting work there would have its jobs
delivered to audian's own compute worker as well.  A plugin that needs a
thread should own one.
"""

from . import theme
from .data import open_files
from .databrowser import ParameterGroup, caption_label, narrow_combo
from .denoise import Denoiser, Parameter
from .labels import KIND_POINT, KIND_SPAN, Label, LabelCategory
from .tasks.tokens import Cancelled, CancelToken

#: Attributes a panel factory may assume its browser has.  Everything else
#: about a `DataBrowser` is internal and may move.
PLUGIN_BROWSER_ATTRS = (
    "labels",           # the editable LabelSet: read examples, add results
    "data",             # the open recording: rate, channels, file_path
    "plot_ranges",      # the visible ranges, keyed by Panel.times/etc.
    "notify",           # (level, message) to the message log
    "redraw_labels",    # after changing the label set
    "schedule_label_save",
    "spectrogram_axes", # the lanes, for a plugin that draws in frequency
)

__all__ = [
    "KIND_POINT",
    "KIND_SPAN",
    "PLUGIN_BROWSER_ATTRS",
    "CancelToken",
    "Cancelled",
    "Denoiser",
    "Label",
    "LabelCategory",
    "Parameter",
    "ParameterGroup",
    "caption_label",
    "narrow_combo",
    "open_files",
    "theme",
]
