"""Moving the view: what a pan step, a page and a jump cost, and that they
change nothing on screen.

The machinery this file pins (see the commit that added it):

* `Data.place_buffer` keeps the raw buffer at a fixed length with a lead on
  either side of the view, so a pan inside it moves nothing and a pan out of
  it shifts the buffers in place (`recycle_buffer`) and reads only the lead;
* `BufferedData.allocate_buffer` keeps a backing array, so a derived buffer
  whose length flips by one frame is not reallocated;
* `BufferedSpectrogram.column_sums` caches the power curve's sums per block;
* `SpecItem` uploads the visible band, on absolute stride columns, and keeps
  the columns of frames that stayed in the buffer;
* `fast_decibel` is `thunderlab`'s `decibel`, bit for bit;
* `TimeAxisItem` draws its tick-only rulings itself;
* pan drags and keyboard time steps are applied once per event-loop turn.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "tests"))

from audian.buffereddata import BufferedData, recycle_buffer  # noqa: E402
from audian.bufferedspectrogram import (  # noqa: E402
    BufferedSpectrogram,
    fast_decibel,
)
from test_chunked_dsp import FakeSource  # noqa: E402
from thunderlab.powerspectrum import decibel  # noqa: E402


# ------------------------------------------------------------------ buffers


class Ramp(BufferedData):
    """A derived trace whose every frame says which frame it is."""

    def load_buffer(self, offset, nframes, buffer):
        buffer[:] = np.arange(offset, offset + nframes)[:, None, None] + np.zeros(
            buffer.shape[1:]
        )


def ramp(frames=10**6, bufferframes=1000):
    t = Ramp("ramp", "data")
    t.frames = frames
    t.channels = 3
    t.shape = (frames, 3, 5)
    t.buffer = np.zeros((0, 3, 5), np.float32)
    t.offset = 0
    t.bufferframes = bufferframes
    t.backframes = 0
    t.buffer_changed = np.zeros(3, bool)
    return t


def test_moving_a_buffer_in_place_keeps_every_frame_where_it_belongs():
    """Shifting through the shared backing array is still a correct shift.

    The surviving frames are moved inside one array -- the same one, or a
    longer backing array the buffer is a view of -- and the length flips by
    a frame or two as a derived trace's does.  Every frame must still say
    its own index afterwards, in both directions and across resizes.
    """
    t = ramp()
    rng = np.random.default_rng(0)
    offset = 5000
    t.move_buffer(offset, 1000)
    for _ in range(3000):
        offset = max(0, offset + int(rng.integers(-300, 300)))
        t.move_buffer(offset, 1000 + int(rng.integers(-20, 20)))
        want = np.arange(t.offset, t.offset + len(t.buffer))
        assert np.array_equal(t.buffer[:, 0, 0], want)
        assert np.array_equal(t.buffer[:, 2, 4], want)


def test_a_length_that_flips_by_a_frame_reuses_the_backing_array():
    t = ramp()
    t.move_buffer(5000, 1000)
    backing = t._backing
    for k in range(1, 40):
        t.move_buffer(5000 + 7 * k, 1000 - (k % 2))
    assert t._backing is backing, "a one-frame length change reallocated"


def test_the_raw_loader_recycle_matches_audioio_semantics():
    """`recycle_buffer` on a plain `BufferedArray` returns what to load."""
    t = ramp()
    t.allocate_buffer = lambda nframes=None, force=False: BufferedData.allocate_buffer(
        t, nframes, force
    )
    t.move_buffer(1000, 500)
    assert recycle_buffer(t, 1100, 500) == (1500, 100)
    t.offset = 1100
    assert recycle_buffer(t, 1000, 500) == (1000, 100)


def test_a_move_is_logged_and_a_recompute_forgets_the_log():
    t = ramp()
    t.move_buffer(5000, 1000)
    gen, seq = t.content_generation, t.move_seq
    t.move_buffer(5200, 1000)
    t.move_buffer(5100, 1000)
    # frames 5200-6100 stayed through both moves
    assert t.stable_extent(gen, seq) == (5200, 6100)
    t.reload_buffer()
    assert t.stable_extent(gen, seq) is None


# -------------------------------------------------------------- placement


@pytest.fixture
def recording(tmp_path):
    import soundfile

    rate = 8000
    x = np.random.default_rng(1).standard_normal((rate * 600, 2)).astype(np.float32)
    path = tmp_path / "rec.wav"
    soundfile.write(path, 0.1 * x, rate)
    return path


def opened(path):
    from audian.data import Data

    data = Data(str(path))
    data.add_trace(BufferedSpectrogram(source="data"))
    data.setup_traces()
    data.open(False, 0)
    for trace in data.traces:
        trace.visible_channels[:] = True
    data.set_need_update()
    return data


def test_a_pan_inside_the_buffer_moves_nothing(recording):
    data = opened(recording)
    raw = data.data
    data.update_times(100.0, 110.0)
    before = (raw.offset, len(raw.buffer), raw.buffer_generation)
    for k in range(20):
        data.update_times(100.0 + 0.1 * k, 110.0 + 0.1 * k)
    assert (raw.offset, len(raw.buffer), raw.buffer_generation) == before


def test_a_long_pan_keeps_the_buffer_length_and_reads_only_ahead(recording):
    """The buffer keeps its length, so nothing is reallocated, and each move
    reads a lead beyond the view rather than re-centring the whole buffer.
    """
    data = opened(recording)
    raw = data.data
    span = 120.0  # longer than the base buffer: the old code had no slack
    data.update_times(100.0, 100.0 + span)
    length = len(raw.buffer)
    assert length > span * raw.rate, "no lead around a view longer than the base"
    loaded = []
    load = raw.load_buffer

    def counted(offset, nframes, buffer):
        loaded.append(nframes)
        return load(offset, nframes, buffer)

    raw.load_buffer = counted
    t = 100.0
    for _ in range(200):
        t += 0.4
        data.update_times(t, t + span)
        assert len(raw.buffer) == length
        assert raw.offset <= int(t * raw.rate)
        assert raw.offset + length >= int((t + span) * raw.rate)
    assert loaded, "a pan of 80 s never read anything"
    assert max(loaded) < 0.2 * length, "a move re-read most of the buffer"
    # the spectrogram followed, without a full recompute
    spec = data["spectrogram"]
    assert spec.offset <= int(t * spec.rate)


def test_the_buffer_shrinks_back_when_the_view_does(recording):
    data = opened(recording)
    raw = data.data
    data.update_times(100.0, 400.0)
    big = len(raw.buffer)
    data.update_times(500.0, 505.0)
    assert len(raw.buffer) < big / 2


def test_the_power_curve_sums_are_the_block_sums(recording):
    data = opened(recording)
    spec = data["spectrogram"]
    data.update_times(100.0, 160.0)
    n = len(spec.buffer)
    for i0, i1 in ((0, n), (17, n - 3), (100, 101), (64, 128), (5, 5)):
        got = spec.column_sums(i0, i1)
        want = spec.buffer[i0:i1].sum(axis=0)
        assert np.allclose(got, want, rtol=1e-12, atol=0)
    # a recompute is seen
    spec.buffer[10:20] *= 2
    spec.load_buffer(spec.offset + 10, 10, spec.buffer[10:20])
    assert np.allclose(
        spec.column_sums(0, n), spec.buffer[:n].sum(axis=0), rtol=1e-12, atol=0
    )


# -------------------------------------------------------------- decibel


def test_fast_decibel_is_decibel_bit_for_bit():
    rng = np.random.default_rng(3)
    for dtype in (np.float64, np.float32):
        p = (10.0 ** rng.uniform(-30, 3, (37, 129))).astype(dtype)
        p[0, :5] = [0.0, -1.0, np.nan, 1e-20, 1e-21]
        want = decibel(p)
        got = fast_decibel(p)
        assert got.dtype == want.dtype
        assert got.flags.c_contiguous
        assert np.array_equal(got, want, equal_nan=True)
        # a transposed (strided) input, as SpecItem hands it over
        assert np.array_equal(fast_decibel(p.T), decibel(p.T), equal_nan=True)


# -------------------------------------------------------------- SpecItem


@pytest.fixture
def spec_trace():
    source = FakeSource(nframes=200_000, channels=2)
    spec = BufferedSpectrogram(nfft=256, overlap_frac=0.5)
    spec.open(source)
    spec.frames = len(spec)
    spec.bufferframes = 1200
    spec.visible_channels[:] = True
    spec.move_buffer(100, 1200)
    return spec


def test_specitem_uploads_the_band_and_keeps_moved_columns(app, spec_trace):
    """The image is the visible band of the visible crop, and a pan that
    only moved the buffer gives the same image a fresh conversion would."""
    from audian.specitem import SpecItem

    item = SpecItem(spec_trace, 1)
    rate = spec_trace.rate
    fres = spec_trace.fresolution

    def check():
        i0, i1, stride = item._image_range
        k0, k1 = item._image_band
        rows = spec_trace.buffer[i0:i1:stride, 1, k0:k1]
        assert np.array_equal(item.image, decibel(rows.T), equal_nan=True)
        return i0, i1, stride, k0, k1

    t0 = (spec_trace.offset + 300) / rate
    item.set_view_range(t0, t0 + 200 / rate)
    item.set_band(2000.0, 4000.0)
    item.update_plot()
    i0, i1, stride, k0, k1 = check()
    assert k0 > 0 and k1 < spec_trace.buffer.shape[2], "the band was not cropped"
    assert k0 <= int(2000 / fres) and int(4000 / fres) < k1
    assert (spec_trace.offset + i0) % stride == 0
    for shift in (37, 300, -120, 900):
        spec_trace.move_buffer(spec_trace.offset + shift, 1200)
        t0 = (spec_trace.offset + 300) / rate
        item.set_view_range(t0, t0 + 200 / rate)
        item.update_plot()
        check()


# -------------------------------------------------------------- the axis


def test_a_tick_only_time_axis_draws_what_pyqtgraph_would(app):
    """`TimeAxisItem.paint`'s own path against pyqtgraph's, pixel for pixel."""
    import pyqtgraph as pg
    from PySide6.QtGui import QImage, QPainter

    from audian.timeaxisitem import TimeAxisItem

    def render(own: bool):
        win = pg.GraphicsLayoutWidget()
        win.resize(1600, 300)
        axes = {
            name: TimeAxisItem(np.zeros(1), ["x.wav"], 0, orientation=name)
            for name in ("top", "bottom")
        }
        for axis in axes.values():
            axis.setStyle(showValues=False)
            if not own:
                axis._plain_ticks = lambda: False
        plot = win.addPlot(axisItems=axes)
        plot.setXRange(12.3, 71.9, padding=0)
        win.show()
        app.processEvents()
        image = QImage(win.size(), QImage.Format.Format_ARGB32)
        image.fill(0)
        painter = QPainter(image)
        win.render(painter)
        painter.end()
        win.close()
        bits = image.constBits()
        return np.frombuffer(bits, np.uint8).reshape(image.height(), -1).copy()

    assert np.array_equal(render(True), render(False))


# ---------------------------------------------------- coalesced view steps


def test_a_pan_drag_applies_its_moves_once_per_event_loop_turn(app):
    import pyqtgraph as pg
    from PySide6.QtCore import QPointF

    from audian.selectviewbox import SelectViewBox

    view = pg.GraphicsView()
    vb = SelectViewBox(0)
    view.setCentralItem(vb)
    view.resize(1000, 200)
    view.show()
    app.processEvents()
    vb.setMouseMode(pg.ViewBox.PanMode)
    vb.setRange(xRange=(0, 100), yRange=(0, 10), padding=0)
    app.processEvents()
    width = vb.width()
    changes = []
    vb.sigRangeChanged.connect(lambda *a: changes.append(vb.viewRange()[0]))
    for _ in range(5):
        vb._pan_by(pg.Point(QPointF(-0.01 * width, 0)), np.array([1.0, 1.0]))
    assert not changes, "a pan move was applied before the event loop ran"
    app.processEvents()
    assert len(changes) == 1
    x0, x1 = vb.viewRange()[0]
    # five steps of 1 % of the width each, applied as one
    assert x0 == pytest.approx(-5.0) and x1 == pytest.approx(95.0)
