"""Basic PlotItem that can be managed by PlotRange."""

import pyqtgraph as pg

from . import theme
from .selectviewbox import SelectViewBox


class RangePlot(pg.PlotItem):
    def __init__(self, aspec, channel, browser, *args, **kwargs):
        self.aspec = aspec
        self.channel = channel
        self.data_items = []
        self.grid_on = False
        #: annotation overlay, attached by the browser once a table is
        #: loaded (see `eventoverlay.EventOverlay`).  Deliberately NOT a
        #: data item: annotations must never take part in fitting the
        #: amplitude range or in answering "what is under the pointer".
        self.annotations = None

        # view box:
        view = SelectViewBox(channel)

        # plot:
        pg.PlotItem.__init__(self, viewBox=view, *args, **kwargs)

        # design:
        self.getViewBox().setDefaultPadding(padding=0)
        theme.style_plotitem(self)

        # functionality:
        self.hideButtons()
        self.setMenuEnabled(False)
        # setMenuEnabled() only stops the menu from being raised - PlotItem
        # still built a QMenu tree with five submenus and their QWidgetAction
        # payloads.  With 16 channels that is hundreds of top level widgets,
        # and on Wayland pyqtgraph's parentless ExportDialog gets tiled by the
        # compositor.  Tear the whole thing down:
        theme.strip_pg_menus(self)
        self.enableAutoRange(False, False)
        self.getViewBox().init_zoom_history()

        # signals:
        # the view box needs the browser to resolve a modified drag into a
        # region mode (Shift = play, Alt = analyse):
        view.browser = browser
        self.sigRangeChanged.connect(browser.update_ranges)
        # region_menu_at() also gets the scene position of the drag, which is
        # the only way to place a popup menu under Wayland.  Connect just one
        # of the two, otherwise the region is acted on twice:
        if hasattr(browser, "region_menu_at"):
            self.getViewBox().sigSelectedRegionAt.connect(browser.region_menu_at)
        else:
            self.getViewBox().sigSelectedRegion.connect(browser.region_menu)

        # cross hair:
        self.xline = pg.InfiniteLine(angle=90, movable=False)
        self.xline.setPen(theme.crosshair_pen())
        self.xline.setZValue(100)
        self.xline.setValue(0)
        self.xline.setVisible(False)
        self.addItem(self.xline, ignoreBounds=True)

        self.yline = pg.InfiniteLine(angle=0, movable=False)
        self.yline.setPen(theme.crosshair_pen())
        self.yline.setZValue(100)
        self.yline.setValue(0)
        self.yline.setVisible(False)
        self.addItem(self.yline, ignoreBounds=True)

        # stored cross hair marker:
        self.stored_marker = pg.ScatterPlotItem(
            size=14,
            pen=theme.marker_pen(),
            brush=theme.marker_brush(),
            symbol="o",
            hoverable=False,
        )
        self.stored_marker.setZValue(20)
        self.addItem(self.stored_marker, ignoreBounds=True)

    def getMenu(self, *args, **kwargs):
        # context menus are disabled throughout audian:
        return None

    def getContextMenus(self, *args, **kwargs):
        return None

    def polish(self) -> None:
        """Apply the theme to this plot and all of its overlay items.

        Idempotent, so it can be called again after a live theme switch.
        Never read `self.palette()` here: polish() runs before the plot item is
        reparented into its figure, so the palette is the application default
        anyway.
        """
        theme.style_plotitem(self)
        self.xline.setPen(theme.crosshair_pen())
        self.yline.setPen(theme.crosshair_pen())
        self.stored_marker.setPen(theme.marker_pen())
        self.stored_marker.setBrush(theme.marker_brush())
        self.getViewBox().apply_theme()
        self.set_grid(bool(self.grid_on & 1), bool(self.grid_on & 2))

    def apply_theme(self) -> None:
        """Alias of polish() for live re-theming."""
        self.polish()

    def set_grid(self, xgrid: bool, ygrid: bool) -> None:
        """Show a quiet grid.

        With the grid enabled pyqtgraph draws the tick *marks* right across the
        view box - the grid lines are the tick lines - so the grid colour is
        the tick pen and it has to be swapped along with the grid.
        """
        self.grid_on = (1 if xgrid else 0) | (2 if ygrid else 0)
        self.showGrid(x=xgrid, y=ygrid, alpha=theme.GRID_ALPHA)
        for name, on in (("bottom", xgrid), ("left", ygrid)):
            if name not in self.axes:
                continue
            axis = self.getAxis(name)
            if on:
                axis.setTickPen(theme.grid_pen())
            else:
                axis.setTickPen(theme.pen("fg.faint", width=theme.LW_HAIRLINE))

    def x(self):
        return self.aspec[0]

    def y(self):
        return self.aspec[1]

    def z(self):
        return self.aspec[2] if len(self.aspec) > 2 else ""

    def add_item(self, item, is_data=False):
        if is_data:
            self.data_items.append(item)
            item.ax = self
        self.addItem(item)

    def range(self, axspec):
        return None, None, None

    def amplitudes(self, t0, t1):
        return None, None

    def get_marker_pos(self, x, dx, ym, dy):
        return x, ym, None

    def set_stored_marker(self, x, y):
        self.stored_marker.setData((x,), (y,))
        self.stored_marker.setVisible(True)

    def update_plot(self):
        for item in self.data_items:
            if item.isVisible():
                item.update_plot()
        if self.annotations is not None:
            self.annotations.update_plot()
