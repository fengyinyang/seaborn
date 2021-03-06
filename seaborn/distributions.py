"""Plotting functions for visualizing distributions."""
from __future__ import division
from textwrap import dedent
import colorsys
import numpy as np
from scipy import stats
import pandas as pd
from pandas.core.series import remove_na
import matplotlib as mpl
import matplotlib.pyplot as plt
import warnings

try:
    import statsmodels.nonparametric.api as smnp
    _has_statsmodels = True
except ImportError:
    _has_statsmodels = False

from .external.six.moves import range

from .utils import set_hls_values, desaturate, iqr, _kde_support
from .palettes import color_palette, husl_palette, blend_palette, light_palette
from .axisgrid import JointGrid


class _BoxPlotter(object):

    def __init__(self, x, y, hue, data, order, hue_order,
                 orient, color, palette, saturation,
                 width, fliersize, linewidth):

        self.establish_variables(x, y, hue, data, orient, order, hue_order)
        self.establish_colors(color, palette, saturation)

        self.width = width
        self.fliersize = fliersize

        if linewidth is None:
            linewidth = mpl.rcParams["lines.linewidth"]
        self.linewidth = linewidth

    def establish_variables(self, x=None, y=None, hue=None, data=None,
                            orient=None, order=None, hue_order=None):
        """Convert input specification into a common representation."""
        # Option 1:
        # We are plotting a wide-form dataset
        # -----------------------------------
        if x is None and y is None:

            # Do a sanity check on the inputs
            if hue is not None:
                error = "Cannot use `hue` without `x` or `y`"
                raise ValueError(error)

            # No hue grouping with wide inputs
            plot_hues = None
            hue_title = None
            hue_names = None

            # We also won't get a axes labels here
            value_label = None
            group_label = None

            # Option 1a:
            # The input data is a Pandas DataFrame
            # ------------------------------------

            if isinstance(data, pd.DataFrame):

                # Order the data correctly
                if order is None:
                    order = []
                    # Reduce to just numeric columns
                    for col in data:
                        try:
                            data[col].astype(np.float)
                            order.append(col)
                        except ValueError:
                            pass
                plot_data = data[order]
                group_names = order
                group_label = data.columns.name

                # Convert to a list of arrays, the common representation
                iter_data = plot_data.iteritems()
                plot_data = [np.asarray(s, np.float) for k, s in iter_data]

            # Option 1b:
            # The input data is an array or list
            # ----------------------------------

            else:

                # We can't reorder the data
                if order is not None:
                    error = "Input data must be a pandas object to reorder"
                    raise ValueError(error)

                # The input data is an array
                if hasattr(data, "shape"):
                    if len(data.shape) == 1:
                        if np.isscalar(data[0]):
                            plot_data = [data]
                        else:
                            plot_data = list(data)
                    elif len(data.shape) == 2:
                        nr, nc = data.shape
                        if nr == 1 or nc == 1:
                            plot_data = [data.ravel()]
                        else:
                            plot_data = [data[:, i] for i in range(nc)]
                    else:
                        error = ("Input `data` can have no "
                                 "more than 2 dimensions")
                        raise ValueError(error)

                # Check if `data` is None to let us bail out here (for testing)
                elif data is None:
                    plot_data = [[]]

                # The input data is a flat list
                elif np.isscalar(data[0]):
                    plot_data = [data]

                # The input data is a nested list
                # This will catch some things that might fail later
                # but exhaustive checks are hard
                else:
                    plot_data = data

                # Convert to a list of arrays, the common representation
                plot_data = [np.asarray(d, np.float) for d in plot_data]

                # The group names will just be numeric indices
                group_names = list(range((len(plot_data))))

            # Figure out the plotting orientation
            orient = "h" if str(orient).startswith("h") else "v"

        # Option 2:
        # We are plotting a long-form dataset
        # -----------------------------------

        else:

            # See if we need to get `x` and `y` or `hue` from `data`
            if data is not None:
                x = data.get(x, x)
                y = data.get(y, y)
                hue = data.get(hue, hue)

            # Figure out the plotting orientation
            orient = self.infer_orient(x, y, orient)

            # Option 2a:
            # We are plotting a single set of data
            # ------------------------------------
            if x is None or y is None:

                # Determine where the data are
                vals = y if x is None else x

                # Put them into the common representation
                plot_data = [np.asarray(vals)]

                # Get a label for the value axis
                if hasattr(vals, "name"):
                    value_label = vals.name
                else:
                    value_label = None

                # This plot will not have group labels or hue nesting
                groups = None
                group_label = None
                group_names = []
                plot_hues = None
                hue_names = None
                hue_title = None

            # Option 2b:
            # We are grouping the data values by another variable
            # ---------------------------------------------------
            else:

                # Determine which role each variable will play
                if orient == "v":
                    vals, groups = y, x
                else:
                    vals, groups = x, y

                # Make sure the groupby is going to work
                if not isinstance(vals, pd.Series):
                    vals = pd.Series(vals)

                # Get the order of the box groups
                if order is None:
                    try:
                        order = groups.unique()
                    except AttributeError:
                        order = pd.unique(groups)
                group_names = list(order)

                # Group the numeric data
                grouped_vals = vals.groupby(groups)
                plot_data = [grouped_vals.get_group(g) for g in order]
                plot_data = [d.values for d in plot_data]

                # Get the categorical axis label
                if hasattr(groups, "name"):
                    group_label = groups.name
                else:
                    group_label = None

                # Get the numerical axis label
                value_label = vals.name

                # Now handle the hue levels for nested ordering
                if hue is None:
                    plot_hues = None
                    hue_title = None
                    hue_names = None
                else:

                    # Make sure the groupby is going to work
                    if not isinstance(hue, pd.Series):
                        hue = pd.Series(hue)

                    # Get the order of the hue levels
                    if hue_order is None:
                        try:
                            hue_order = hue.unique()
                        except AttributeError:
                            hue_order = pd.unique(hue)
                    hue_names = list(hue_order)

                    # Group the hue categories
                    grouped_hues = hue.groupby(groups)
                    plot_hues = [grouped_hues.get_group(g) for g in order]
                    plot_hues = [h.values for h in plot_hues]

                    # Get the title for the hues (will title the legend)
                    hue_title = hue.name

        # Assign object attributes
        # ------------------------
        self.orient = orient
        self.plot_data = plot_data
        self.group_label = group_label
        self.value_label = value_label
        self.group_names = group_names
        self.plot_hues = plot_hues
        self.hue_title = hue_title
        self.hue_names = hue_names

    def establish_colors(self, color, palette, saturation):
        """Get a list of colors for the main component of the plots."""
        if self.hue_names is None:
            n_colors = len(self.plot_data)
        else:
            n_colors = len(self.hue_names)

        # Determine the main colors
        if color is None and palette is None:
            # Determine whether the current palette will have enough values
            # If not, we'll default to the husl palette so each is distinct
            current_palette = mpl.rcParams["axes.color_cycle"]
            if n_colors <= len(current_palette):
                colors = color_palette(n_colors=n_colors)
            else:
                colors = husl_palette(n_colors, l=.7)

        elif palette is None:
            # When passing a specific color, the interpretation depends
            # on whether there is a hue variable or not.
            # If so, we will make a blend palette so that the different
            # levels have some amount of variation.
            if self.hue_names is None:
                colors = [color] * n_colors
            else:
                colors = light_palette(color, n_colors)
        else:

            # Let `palette` be a dict mapping level to color
            if isinstance(palette, dict):
                if self.hue_names is None:
                    levels = self.group_names
                else:
                    levels = self.hue_names
                palette = [palette[l] for l in levels]

            colors = color_palette(palette, n_colors)

        # Conver the colors to a common rgb representation
        colors = [mpl.colors.colorConverter.to_rgb(c) for c in colors]

        # Desaturate a bit because these are patches
        if saturation < 1:
            colors = [desaturate(c, saturation) for c in colors]

        # Determine the gray color to use for the lines framing the plot
        light_vals = [colorsys.rgb_to_hls(*c)[1] for c in colors]
        l = min(light_vals) * .6
        gray = (l, l, l)

        # Assign object attributes
        self.colors = colors
        self.gray = gray

    def infer_orient(self, x, y, orient=None):
        """Determine how the plot should be oriented based on the data."""
        orient = str(orient)

        def is_categorical(s):
            try:
                # Correct way, but doesn't exist in older Pandas
                return pd.core.common.is_categorical_dtype(s)
            except AttributeError:
                # Also works, but feels hackier
                return str(s.dtype) == "categorical"

        if orient.startswith("v"):
            return "v"
        elif orient.startswith("h"):
            return "h"
        elif x is None:
            return "v"
        elif y is None:
            return "h"
        elif is_categorical(y):
            return "h"
        else:
            return "v"

    @property
    def hue_offsets(self):
        """A list of center positions for plots when hue nesting is used."""
        n_levels = len(self.hue_names)
        each_width = self.width / n_levels
        offsets = np.linspace(0, self.width - each_width, n_levels)
        offsets -= offsets.mean()

        return offsets

    @property
    def nested_width(self):
        """A float with the width of plot elements when hue nesting is used."""
        return self.width / len(self.hue_names) * .98

    def annotate_axes(self, ax):
        """Add descriptive labels to an Axes object."""
        if self.orient == "v":
            xlabel, ylabel = self.group_label, self.value_label
        else:
            xlabel, ylabel = self.value_label, self.group_label

        if xlabel is not None:
            ax.set_xlabel(xlabel)
        if ylabel is not None:
            ax.set_ylabel(ylabel)

        if self.orient == "v":
            ax.set_xticks(np.arange(len(self.plot_data)))
            ax.set_xticklabels(self.group_names)
        else:
            ax.set_yticks(np.arange(len(self.plot_data)))
            ax.set_yticklabels(self.group_names)

        if self.orient == "v":
            ax.xaxis.grid(False)
            ax.set_xlim(-.5, len(self.plot_data) - .5)
        else:
            ax.yaxis.grid(False)
            ax.set_ylim(-.5, len(self.plot_data) - .5)

        if self.hue_names is not None:
            leg = ax.legend(loc="best")
            if self.hue_title is not None:
                leg.set_title(self.hue_title)

                # Set the title size a roundabout way to maintain
                # compatability with matplotlib 1.1
                try:
                    title_size = mpl.rcParams["axes.labelsize"] * .85
                except TypeError:  # labelsize is something like "large"
                    title_size = mpl.rcParams["axes.labelsize"]
                prop = mpl.font_manager.FontProperties(size=title_size)
                leg._legend_title_box._text.set_font_properties(prop)

    def restyle_boxplot(self, artist_dict, color):
        """Take a drawn matplotlib boxplot and make it look nice."""
        for box in artist_dict["boxes"]:
            box.set_color(color)
            box.set_zorder(.9)
            box.set_edgecolor(self.gray)
            box.set_linewidth(self.linewidth)
        for whisk in artist_dict["whiskers"]:
            whisk.set_color(self.gray)
            whisk.set_linewidth(self.linewidth)
            whisk.set_linestyle("-")
        for cap in artist_dict["caps"]:
            cap.set_color(self.gray)
            cap.set_linewidth(self.linewidth)
        for med in artist_dict["medians"]:
            med.set_color(self.gray)
            med.set_linewidth(self.linewidth)
        for fly in artist_dict["fliers"]:
            fly.set_color(self.gray)
            fly.set_marker("d")
            fly.set_markeredgecolor(self.gray)
            fly.set_markersize(self.fliersize)

    def add_legend_data(self, ax, x, y, color, label):
        """Add a dummy patch object so we can get legend data."""
        rect = plt.Rectangle([x, y], 0, 0,
                             linewidth=self.linewidth / 2,
                             edgecolor=self.gray,
                             facecolor=color,
                             label=label, zorder=-1)
        ax.add_patch(rect)

    def draw_boxplot(self, ax, kws):
        """Use matplotlib to draw a boxplot on an Axes."""
        vert = self.orient == "v"

        for i, group_data in enumerate(self.plot_data):
            if self.plot_hues is None:
                # Draw a single box or a set of boxes
                # with a single level of grouping
                box_data = remove_na(group_data)
                artist_dict = ax.boxplot(box_data,
                                         vert=vert,
                                         patch_artist=True,
                                         positions=[i],
                                         widths=self.width,
                                         **kws)
                color = self.colors[i]
                self.restyle_boxplot(artist_dict, color)
            else:
                # Draw nested groups of boxes
                offsets = self.hue_offsets
                for j, hue_level in enumerate(self.hue_names):
                    hue_mask = self.plot_hues[i] == hue_level
                    if not hue_mask.any():
                        continue
                    box_data = remove_na(group_data[hue_mask])
                    center = i + offsets[j]
                    artist_dict = ax.boxplot(box_data,
                                             vert=vert,
                                             patch_artist=True,
                                             positions=[center],
                                             widths=self.nested_width,
                                             **kws)
                    color = self.colors[j]
                    self.restyle_boxplot(artist_dict, color)
                    # Add legend data, but just for one set of boxes
                    if not i:
                        self.add_legend_data(ax, center,
                                             np.median(box_data),
                                             color, hue_level)

    def plot(self, ax, boxplot_kws):
        """Make the plot."""
        self.draw_boxplot(ax, boxplot_kws)
        self.annotate_axes(ax)
        if self.orient == "h":
            ax.invert_yaxis()


class _ViolinPlotter(_BoxPlotter):

    def __init__(self, x, y, hue, data, order, hue_order,
                 bw, cut, scale, scale_hue, gridsize,
                 width, inner, split, orient, linewidth,
                 color, palette, saturation):

        self.establish_variables(x, y, hue, data, orient, order, hue_order)
        self.establish_colors(color, palette, saturation)
        self.estimate_densities(bw, cut, scale, scale_hue, gridsize)

        self.gridsize = gridsize
        self.width = width
        self.inner = inner
        if split and self.hue_names is not None and len(self.hue_names) != 2:
            raise ValueError("Cannot use `split` with more than 2 hue levels.")
        self.split = split

        if linewidth is None:
            linewidth = mpl.rcParams["lines.linewidth"]
        self.linewidth = linewidth

    def estimate_densities(self, bw, cut, scale, scale_hue, gridsize):
        """Find the support and density for all of the data."""
        # Initialize data structures to keep track of plotting data
        if self.hue_names is None:
            support = []
            density = []
            counts = np.zeros(len(self.plot_data))
            max_density = np.zeros(len(self.plot_data))
        else:
            support = [[] for _ in self.plot_data]
            density = [[] for _ in self.plot_data]
            size = len(self.group_names), len(self.hue_names)
            counts = np.zeros(size)
            max_density = np.zeros(size)

        for i, group_data in enumerate(self.plot_data):

            # Option 1: we have a single level of grouping
            # --------------------------------------------

            if self.plot_hues is None:

                # Strip missing datapoints
                kde_data = remove_na(group_data)

                # Handle special case of no data at this level
                if kde_data.size == 0:
                    support.append(np.array([]))
                    density.append(np.array([1.]))
                    counts[i] = 0
                    max_density[i] = 0
                    continue

                # Handle special case of a single unique datapoint
                elif np.unique(kde_data).size == 1:
                    support.append(np.unique(kde_data))
                    density.append(np.array([1.]))
                    counts[i] = 1
                    max_density[i] = 0
                    continue

                # Fit the KDE and get the used bandwidth size
                kde, bw_used = self.fit_kde(kde_data, bw)

                # Determine the support grid and get the density over it
                support_i = self.kde_support(kde_data, bw_used, cut, gridsize)
                density_i = kde.evaluate(support_i)

                # Update the data structures with these results
                support.append(support_i)
                density.append(density_i)
                counts[i] = kde_data.size
                max_density[i] = density_i.max()

            # Option 2: we have nested grouping by a hue variable
            # ---------------------------------------------------

            else:
                for j, hue_level in enumerate(self.hue_names):

                    # Select out the observations for this hue level
                    hue_mask = self.plot_hues[i] == hue_level

                    # Strip missing datapoints
                    kde_data = remove_na(group_data[hue_mask])

                    # Handle special case of no data at this level
                    if kde_data.size == 0:
                        support[i].append(np.array([]))
                        density[i].append(np.array([1.]))
                        counts[i, j] = 0
                        max_density[i, j] = 0
                        continue

                    # Handle special case of a single unique datapoint
                    elif np.unique(kde_data).size == 1:
                        support[i].append(np.unique(kde_data))
                        density[i].append(np.array([1.]))
                        counts[i, j] = 1
                        max_density[i, j] = 0
                        continue

                    # Fit the KDE and get the used bandwidth size
                    kde, bw_used = self.fit_kde(kde_data, bw)

                    # Determine the support grid and get the density over it
                    support_ij = self.kde_support(kde_data, bw_used,
                                                  cut, gridsize)
                    density_ij = kde.evaluate(support_ij)

                    # Update the data structures with these results
                    support[i].append(support_ij)
                    density[i].append(density_ij)
                    counts[i, j] = kde_data.size
                    max_density[i, j] = density_ij.max()

        # Scale the height of the density curve.
        # For a violinplot the density is non-quantitative.
        # The objective here is to scale the curves relative to 1 so that
        # they can be multiplied by the width parameter during plotting.

        if scale == "area":
            self.scale_area(density, max_density, scale_hue)

        elif scale == "width":
            self.scale_width(density)

        elif scale == "count":
            self.scale_count(density, counts, scale_hue)

        else:
            raise ValueError("scale method '{}' not recognized".format(scale))

        # Set object attributes that will be used while plotting
        self.support = support
        self.density = density

    def fit_kde(self, x, bw):
        """Estimate a KDE for a vector of data with flexible bandwidth."""
        # Allow for the use of old scipy where `bw` is fixed
        try:
            kde = stats.gaussian_kde(x, bw)
        except TypeError:
            kde = stats.gaussian_kde(x)
            if bw != "scott":  # scipy default
                msg = ("Ignoring bandwidth choice, "
                       "please upgrade scipy to use a different bandwidth.")
                warnings.warn(msg, UserWarning)

        # Extract the numeric bandwidth from the KDE object
        bw_used = kde.factor

        # At this point, bw will be a numeric scale factor.
        # To get the actual bandwidth of the kernel, we multiple by the
        # unbiased standard deviation of the data, which we will use
        # elsewhere to compute the range of the support.
        bw_used = bw_used * x.std(ddof=1)

        return kde, bw_used

    def kde_support(self, x, bw, cut, gridsize):
        """Define a grid of support for the violin."""
        support_min = x.min() - bw * cut
        support_max = x.max() + bw * cut
        return np.linspace(support_min, support_max, gridsize)

    def scale_area(self, density, max_density, scale_hue):
        """Scale the relative area under the KDE curve.

        This essentially preserves the "standard" KDE scaling, but the
        resulting maximum density will be 1 so that the curve can be
        properly multiplied by the violin width.

        """
        if self.hue_names is None:
            for d in density:
                if d.size > 1:
                    d /= max_density.max()
        else:
            for i, group in enumerate(density):
                for d in group:
                    if scale_hue:
                        max = max_density[i].max()
                    else:
                        max = max_density.max()
                    if d.size > 1:
                        d /= max

    def scale_width(self, density):
        """Scale each density curve to the same height."""
        if self.hue_names is None:
            for d in density:
                d /= d.max()
        else:
            for group in density:
                for d in group:
                    d /= d.max()

    def scale_count(self, density, counts, scale_hue):
        """Scale each density curve by the number of observations."""
        if self.hue_names is None:
            for count, d in zip(counts, density):
                d /= d.max()
                d *= count / counts.max()
        else:
            for i, group in enumerate(density):
                for j, d in enumerate(group):
                    count = counts[i, j]
                    if scale_hue:
                        scaler = count / counts[i].max()
                    else:
                        scaler = count / counts.max()
                    d /= d.max()
                    d *= scaler

    @property
    def dwidth(self):

        if self.hue_names is None:
            return self.width / 2
        elif self.split:
            return self.width / 2
        else:
            return self.width / (2 * len(self.hue_names))

    def draw_violins(self, ax):
        """Draw the violins onto `ax`."""
        fill_func = ax.fill_betweenx if self.orient == "v" else ax.fill_between
        for i, group_data in enumerate(self.plot_data):

            kws = dict(edgecolor=self.gray, linewidth=self.linewidth)

            # Option 1: we have a single level of grouping
            # --------------------------------------------

            if self.plot_hues is None:

                support, density = self.support[i], self.density[i]

                # Handle special case of no observations in this bin
                if support.size == 0:
                    continue

                # Handle special case of a single observation
                elif support.size == 1:
                    val = np.asscalar(support)
                    d = np.asscalar(density)
                    self.draw_single_observation(ax, i, val, d)
                    continue

                # Draw the violin for this group
                grid = np.ones(self.gridsize) * i
                fill_func(support,
                          grid - density * self.dwidth,
                          grid + density * self.dwidth,
                          color=self.colors[i],
                          **kws)

                # Draw the interior representation of the data
                if self.inner is None:
                    continue

                # Get a nan-free vector of datapoints
                violin_data = remove_na(group_data)

                # Draw box and whisker information
                if self.inner.startswith("box"):
                    self.draw_box_lines(ax, violin_data, support, density, i)

                # Draw quartile lines
                elif self.inner.startswith("quart"):
                    self.draw_quartiles(ax, violin_data, support, density, i)

                # Draw stick observations
                elif self.inner.startswith("stick"):
                    self.draw_stick_lines(ax, violin_data, support, density, i)

                # Draw point observations
                elif self.inner.startswith("point"):
                    self.draw_points(ax, violin_data, i)

            # Option 2: we have nested grouping by a hue variable
            # ---------------------------------------------------

            else:
                offsets = self.hue_offsets
                for j, hue_level in enumerate(self.hue_names):

                    support, density = self.support[i][j], self.density[i][j]
                    kws["color"] = self.colors[j]

                    # Add legend data, but just for one set of violins
                    if not i:
                        self.add_legend_data(ax, support[0], 0,
                                             self.colors[j],
                                             hue_level)

                    # Handle the special case where we have no observations
                    if support.size == 0:
                        continue

                    # Handle the special case where we have one observation
                    elif support.size == 1:
                        val = np.asscalar(support)
                        d = np.asscalar(density)
                        if self.split:
                            d = d / 2
                        at_group = i + offsets[j]
                        self.draw_single_observation(ax, at_group, val, d)
                        continue

                    # Option 2a: we are drawing a single split violin
                    # -----------------------------------------------

                    if self.split:

                        grid = np.ones(self.gridsize) * i
                        if j:
                            fill_func(support,
                                      grid,
                                      grid + density * self.dwidth,
                                      **kws)
                        else:
                            fill_func(support,
                                      grid - density * self.dwidth,
                                      grid,
                                      **kws)

                        # Draw the interior representation of the data
                        if self.inner is None:
                            continue

                        # Get a nan-free vector of datapoints
                        hue_mask = self.plot_hues[i] == hue_level
                        violin_data = remove_na(group_data[hue_mask])

                        # Draw quartile lines
                        if self.inner.startswith("quart"):
                            self.draw_quartiles(ax, violin_data,
                                                support, density, i,
                                                ["left", "right"][j])

                        # Draw stick observations
                        elif self.inner.startswith("stick"):
                            self.draw_stick_lines(ax, violin_data,
                                                  support, density, i,
                                                  ["left", "right"][j])

                        # The box and point interior plots are drawn for
                        # all data at the group level, so we just do that once
                        if not j:
                            continue

                        # Get the whole vector for this group level
                        violin_data = remove_na(group_data)

                        # Draw box and whisker information
                        if self.inner.startswith("box"):
                            self.draw_box_lines(ax, violin_data,
                                                support, density, i)

                        # Draw point observations
                        elif self.inner.startswith("point"):
                            self.draw_points(ax, violin_data, i)

                    # Option 2b: we are drawing full nested violins
                    # -----------------------------------------------

                    else:
                        grid = np.ones(self.gridsize) * (i + offsets[j])
                        fill_func(support,
                                  grid - density * self.dwidth,
                                  grid + density * self.dwidth,
                                  **kws)

                        # Draw the interior representation
                        if self.inner is None:
                            continue

                        # Get a nan-free vector of datapoints
                        hue_mask = self.plot_hues[i] == hue_level
                        violin_data = remove_na(group_data[hue_mask])

                        # Draw box and whisker information
                        if self.inner.startswith("box"):
                            self.draw_box_lines(ax, violin_data,
                                                support, density,
                                                i + offsets[j])

                        # Draw quartile lines
                        elif self.inner.startswith("quart"):
                            self.draw_quartiles(ax, violin_data,
                                                support, density,
                                                i + offsets[j])

                        # Draw stick observations
                        elif self.inner.startswith("stick"):
                            self.draw_stick_lines(ax, violin_data,
                                                  support, density,
                                                  i + offsets[j])

                        # Draw point observations
                        elif self.inner.startswith("point"):
                            self.draw_points(ax, violin_data, i + offsets[j])

    def draw_single_observation(self, ax, at_group, at_quant, density):
        """Draw a line to mark a single observation."""
        d_width = density * self.dwidth
        if self.orient == "v":
            ax.plot([at_group - d_width, at_group + d_width],
                    [at_quant, at_quant],
                    color=self.gray,
                    linewidth=self.linewidth)
        else:
            ax.plot([at_quant, at_quant],
                    [at_group - d_width, at_group + d_width],
                    color=self.gray,
                    linewidth=self.linewidth)

    def draw_box_lines(self, ax, data, support, density, center):
        """Draw boxplot information at center of the density."""
        # Compute the boxplot statistics
        q25, q50, q75 = np.percentile(data, [25, 50, 75])
        whisker_lim = 1.5 * iqr(data)
        h1 = np.min(data[data >= (q25 - whisker_lim)])
        h2 = np.max(data[data <= (q75 + whisker_lim)])

        # Draw a boxplot using lines and a point
        if self.orient == "v":
            ax.plot([center, center], [h1, h2],
                    linewidth=self.linewidth,
                    color=self.gray)
            ax.plot([center, center], [q25, q75],
                    linewidth=self.linewidth * 3,
                    color=self.gray)
            ax.scatter(center, q50,
                       zorder=3,
                       color="white",
                       edgecolor=self.gray,
                       s=np.square(self.linewidth * 2))
        else:
            ax.plot([h1, h2], [center, center],
                    linewidth=self.linewidth,
                    color=self.gray)
            ax.plot([q25, q75], [center, center],
                    linewidth=self.linewidth * 3,
                    color=self.gray)
            ax.scatter(q50, center,
                       zorder=3,
                       color="white",
                       edgecolor=self.gray,
                       s=np.square(self.linewidth * 2))

    def draw_quartiles(self, ax, data, support, density, center, split=False):
        """Draw the quartiles as lines at width of density."""
        q25, q50, q75 = np.percentile(data, [25, 50, 75])

        self.draw_to_density(ax, center, q25, support, density, split,
                             linewidth=self.linewidth,
                             dashes=[self.linewidth * 1.5] * 2)
        self.draw_to_density(ax, center, q50, support, density, split,
                             linewidth=self.linewidth,
                             dashes=[self.linewidth * 3] * 2)
        self.draw_to_density(ax, center, q75, support, density, split,
                             linewidth=self.linewidth,
                             dashes=[self.linewidth * 1.5] * 2)

    def draw_points(self, ax, data, center):
        """Draw individual observations as points at middle of the violin."""
        kws = dict(s=np.square(self.linewidth * 2),
                   c=self.gray,
                   edgecolor=self.gray)

        grid = np.ones(len(data)) * center

        if self.orient == "v":
            ax.scatter(grid, data, **kws)
        else:
            ax.scatter(data, grid, **kws)

    def draw_stick_lines(self, ax, data, support, density,
                         center, split=False):
        """Draw individual observations as sticks at width of density."""
        for val in data:
            self.draw_to_density(ax, center, val, support, density, split,
                                 linewidth=self.linewidth * .5)

    def draw_to_density(self, ax, center, val, support, density, split, **kws):
        """Draw a line orthogonal to the value axis at width of density."""
        idx = np.argmin(np.abs(support - val))
        width = self.dwidth * density[idx] * .99

        kws["color"] = self.gray

        if self.orient == "v":
            if split == "left":
                ax.plot([center - width, center], [val, val], **kws)
            elif split == "right":
                ax.plot([center, center + width], [val, val], **kws)
            else:
                ax.plot([center - width, center + width], [val, val], **kws)
        else:
            if split == "left":
                ax.plot([val, val], [center - width, center], **kws)
            elif split == "right":
                ax.plot([val, val], [center, center + width], **kws)
            else:
                ax.plot([val, val], [center - width, center + width], **kws)

    def plot(self, ax):
        """Make the violin plot."""
        self.draw_violins(ax)
        self.annotate_axes(ax)
        if self.orient == "h":
            ax.invert_yaxis()


class _StripPlotter(_BoxPlotter):
    """1-d scatterplot with categorical organization."""
    def __init__(self, x, y, hue, data, order, hue_order,
                 jitter, split, orient, color, palette):
        """Initialize the plotter."""
        self.establish_variables(x, y, hue, data, orient, order, hue_order)
        self.establish_colors(color, palette, 1)

        # Set object attributes
        self.split = split
        self.width = .8

        if jitter == 1:  # Use a good default for `jitter = True`
            jlim = 0.1
        else:
            jlim = float(jitter)
        if self.hue_names is not None and split:
            jlim /= len(self.hue_names)
        self.jitterer = stats.uniform(-jlim, jlim * 2).rvs

    def draw_stripplot(self, ax, kws):
        """Draw the points onto `ax`."""
        # Set the default zorder to 2.1, so that the points
        # will be drawn on top of line elements (like in a boxplot)
        kws.setdefault("zorder", 2.1)
        for i, group_data in enumerate(self.plot_data):
            if self.plot_hues is None:

                # Determine the positions of the points
                strip_data = remove_na(group_data)
                jitter = self.jitterer(len(strip_data))
                kws["color"] = self.colors[i]

                # Draw the plot
                if self.orient == "v":
                    ax.scatter(i + jitter, strip_data, **kws)
                else:
                    ax.scatter(strip_data, i + jitter, **kws)

            else:
                offsets = self.hue_offsets
                for j, hue_level in enumerate(self.hue_names):
                    hue_mask = self.plot_hues[i] == hue_level
                    if not hue_mask.any():
                        continue

                    # Determine the positions of the points
                    strip_data = remove_na(group_data[hue_mask])
                    pos = i + offsets[j] if self.split else i
                    jitter = self.jitterer(len(strip_data))
                    kws["color"] = self.colors[j]

                    # Only label one set of plots
                    if i:
                        kws.pop("label", None)
                    else:
                        kws["label"] = hue_level

                    # Draw the plot
                    if self.orient == "v":
                        ax.scatter(pos + jitter, strip_data, **kws)
                    else:
                        ax.scatter(strip_data, pos + jitter, **kws)

    def plot(self, ax, kws):
        """Make the plot."""
        self.draw_stripplot(ax, kws)
        self.annotate_axes(ax)
        if self.orient == "h":
            ax.invert_yaxis()


class _SwarmPlotter(_BoxPlotter):

    def __init__(self):

        pass

    def plot(self, ax):

        pass


_boxplot_docs = dict(

    # Shared narrative docs
    main_api_narrative=dedent("""\
    Input data can be passed in a variety of formats, including:

    - A "long-form" DataFrame, in which case the ``x``, ``y``, and ``hue``
      variables will determine how the data are plotted.
    - A "wide-form" DatFrame, such that each numeric column will be plotted.
    - Anything accepted by ``plt.boxplot`` (e.g. a 2d array or list of vectors)

    It is also possible to pass vector data directly to ``x``, ``y``, or
    ``hue``, and thus avoid passing a dataframe to ``data``.

    In all cases, it is possible to use numpy or Python objects, but pandas
    objects are preferable because the associated names will be used to
    annotate the axes. Additionally, you can use Categorical types for the
    grouping variables to control the order of plot elements.\
    """),

    # Shared function parameters
    main_api_params=dedent("""\
    x, y, hue : names of variable in ``data`` or vector data, optional
        Variables for plotting long-form data. See examples for interpretation.
    data : DataFrame, array, or list of arrays, optional
        Dataset for plotting. If ``x`` and ``y`` are absent, this is
        interpreted as wide-form. Otherwise it is expected to be long-form.
    order, hue_order : lists of strings, optional
        Order to plot the categorical levels in, otherwise the levels are
        inferred from the data objects.\
        """),
    orient=dedent("""\
    orient : "v" | "h", optional
        Orientation of the plot (vertical or horizontal). This can also be
        inferred when using long-form data and Categorical data types.\
    """),
    color=dedent("""\
    color : matplotlib color, optional
        Color for all of the elements, or seed for :func:`light_palette` when
        using hue nesting.\
    """),
    palette=dedent("""\
    palette : palette name, list, or dict, optional
        Color palette that maps either the grouping variable or the hue
        variable.\
    """),
    saturation=dedent("""\
    saturation : float, optional
        Proportion of the original saturation to draw colors at. Large patches
        often look better with slightly desaturated colors, but set this to
        ``1`` if you want the plot colors to perfectly match the input color
        spec.\
    """),
    width=dedent("""\
    width : float, optional
        Width of a full element when not using hue nesting, or width of all the
        elements for one level of the major grouping variable.\
    """),
    linewidth=dedent("""\
    linewidth : float, optional
        Width of the gray lines that frame the plot elements.\
    """),
    ax_in=dedent("""\
    ax : matplotlib Axes, optional
        Axes object to draw the plot onto, otherwise uses the current Axes.\
    """),
    ax_out=dedent("""\
    ax : matplotlib Axes
        Returns the Axes object with the boxplot drawn onto it.\
    """),

    # Shared see also
    boxplot=dedent("""\
    boxplot : A traditional box-and-whisker plot with a similar API.\
    """),
    violinplot=dedent("""\
    violinplot : A combination of boxplot and kernel density estimation.\
    """),
    stripplot=dedent("""\
    stripplot : A scatterplot where one variable is categorical. Can be used
                in conjunction with a boxplot to show each observation.\
    """),
    )


def boxplot(x=None, y=None, hue=None, data=None, order=None, hue_order=None,
            orient=None, color=None, palette=None, saturation=.75,
            width=.8, fliersize=5, linewidth=None, whis=1.5, notch=False,
            ax=None, **kwargs):

    plotter = _BoxPlotter(x, y, hue, data, order, hue_order,
                          orient, color, palette, saturation,
                          width, fliersize, linewidth)

    if ax is None:
        ax = plt.gca()

    kwargs.update(dict(whis=whis, notch=notch))
    plotter.plot(ax, kwargs)

    return ax

boxplot.__doc__ = dedent("""\
    Draw a box-and-whisker plot.

    {main_api_narrative}

    Parameters
    ----------
    {main_api_params}
    {orient}
    {color}
    {palette}
    {saturation}
    {width}
    fliersize : float, optional
        Size of the markers used to indicate outlier observations.
    {linewidth}
    whis : float, optional
        Proportion of the IQR past the low and high quartiles to extend the
        plot whiskers. Points outside this range will be identified as
        outliers.
    notch : boolean, optional
        Whether to "notch" the box to indicate a confidence interval for the
        median. There are several other parameters that can control how the
        notches are drawn; see the ``plt.boxplot`` help for more information
        on them.
    {ax_in}
    kwargs : key, value mappings
        Other keyword arguments are passed through to ``plt.boxplot`` at draw
        time.

    Returns
    -------
    {ax_out}

    See Also
    --------
    {violinplot}
    {stripplot}

    Examples
    --------

    Draw a single horizontal boxplot:

    .. plot::
        :context: close-figs

        >>> import seaborn as sns
        >>> sns.set_style("whitegrid")
        >>> tips = sns.load_dataset("tips")
        >>> ax = sns.boxplot(x=tips["total_bill"])

    Draw a vertical boxplot grouped by a categorical variable:

    .. plot::
        :context: close-figs

        >>> ax = sns.boxplot(x="day", y="total_bill", data=tips)

    Draw a boxplot with nested grouping by two categorical variables:

    .. plot::
        :context: close-figs

        >>> ax = sns.boxplot(x="day", y="total_bill", hue="smoker",
        ...                  data=tips, palette="Set3")

    Draw a boxplot with nested grouping when some bins are empty:

    .. plot::
        :context: close-figs

        >>> ax = sns.boxplot(x="day", y="total_bill", hue="time",
        ...                  data=tips, linewidth=2.5)

    Draw a boxplot for each numeric variable in a DataFrame:

    .. plot::
        :context: close-figs

        >>> iris = sns.load_dataset("iris")
        >>> ax = sns.boxplot(data=iris, orient="h", palette="Set2")

    Use :func:`stripplot` to show the datapoints on top of the boxes:

    .. plot::
        :context: close-figs

        >>> ax = sns.boxplot(x="day", y="total_bill", data=tips)
        >>> ax = sns.stripplot(x="day", y="total_bill", data=tips,
        ...                    size=4, jitter=True, edgecolor="gray")

    Draw a box plot on to a :class:`FacetGrid` to group within an additional
    categorical variable:

    .. plot::
        :context: close-figs

        >>> g = sns.FacetGrid(tips, col="time", size=4, aspect=.7)
        >>> (g.map(sns.boxplot, "sex", "total_bill", "smoker")
        ...   .despine(left=True)
        ...   .add_legend(title="smoker"))  #doctest: +ELLIPSIS
        <seaborn.axisgrid.FacetGrid object at 0x...>

    """).format(**_boxplot_docs)


def violinplot(x=None, y=None, hue=None, data=None, order=None, hue_order=None,
               bw="scott", cut=2, scale="area", scale_hue=True, gridsize=100,
               width=.8, inner="box", split=False, orient=None, linewidth=None,
               color=None, palette=None, saturation=.75, ax=None):

    plotter = _ViolinPlotter(x, y, hue, data, order, hue_order,
                             bw, cut, scale, scale_hue, gridsize,
                             width, inner, split, orient, linewidth,
                             color, palette, saturation)

    if ax is None:
        ax = plt.gca()

    plotter.plot(ax)

    return ax

violinplot.__doc__ = dedent("""\
    Draw a combination of boxplot and kernel density estimate.

    A violin plot plays a similar role as a box and whisker plot. It shows the
    distribution of quantitative data across several levels of one (or more)
    categorical variables such that those distributions can be compared. Unlike
    a boxplot, in which all of the plot components correspond to actual
    datapoints, the violin plot features a kernel density estimation of the
    underlying distribution.

    This can be an effective and attractive way to show multiple distributions
    of data at once, but keep in mind that the estimation procedure is
    influenced by the sample size, and violins for relatively small samples
    might look misleadingly smooth.

    {main_api_narrative}

    Parameters
    ----------
    {main_api_params}
    bw : {{'scott', 'silverman', float}}, optional
        Either the name of a reference rule or the scale factor to use when
        computing the kernel bandwidth. The actual kernel size will be
        determined by multiplying the scale factor by the standard deviation of
        the data within each bin.
    cut : float, optional
        Distance, in units of bandwidth size, to extend the density past the
        extreme datapoints. Set to 0 to limit the violin range within the range
        of the observed data (i.e., to have the same effect as ``trim=True`` in
        ``ggplot``.
    scale : {{"area", "count", "width"}}, optional
        The method used to scale the width of each violin. If ``area``, each
        violin will have the same area. If ``count``, the width of the violins
        will be scaled by the number of observations in that bin. If ``width``,
        each violin will have the same width.
    scale_hue : bool, optional
        When nesting violins using a ``hue`` variable, this parameter
        determines whether the scaling is computed within each level of the
        major grouping variable (``scale_hue=True``) or across all the violins
        on the plot (``scale_hue=False``).
    gridsize : int, optional
        Number of points in the discrete grid used to compute the kernel
        density estimate.
    {width}
    inner : {{"box", "quartile", "point", "stick", None}}, optional
        Representation of the datapoints in the violin interior. If ``box``,
        draw a miniature boxplot. If ``quartiles``, draw the quartiles of the
        distribution.  If ``points`` or ``sticks``, show each underlying
        datapoint. Using ``None`` will draw unadorned violins.
    split : bool, optional
        When using hue nesting with a variable that takes two levels, setting
        ``split`` to True will draw half of a violin for each level. This can
        make it easier to directly compare the distributions.
    {orient}
    {linewidth}
    {color}
    {palette}
    {saturation}
    {ax_in}

    Returns
    -------
    {ax_out}

    See Also
    --------
    {boxplot}
    {stripplot}

    Examples
    --------

    Draw a single horizontal violinplot:

    .. plot::
        :context: close-figs

        >>> import seaborn as sns
        >>> sns.set_style("whitegrid")
        >>> tips = sns.load_dataset("tips")
        >>> ax = sns.violinplot(x=tips["total_bill"])

    Draw a vertical violinplot grouped by a categorical variable:

    .. plot::
        :context: close-figs

        >>> ax = sns.violinplot(x="day", y="total_bill", data=tips)

    Draw a violinplot with nested grouping by two categorical variables:

    .. plot::
        :context: close-figs

        >>> ax = sns.violinplot(x="day", y="total_bill", hue="smoker",
        ...                     data=tips, palette="muted")

    Draw split violins to compare the across the hue variable:

    .. plot::
        :context: close-figs

        >>> ax = sns.violinplot(x="day", y="total_bill", hue="smoker",
        ...                     data=tips, palette="muted", split=True)

    Scale the violin width by the number of observations in each bin:

    .. plot::
        :context: close-figs

        >>> ax = sns.violinplot(x="day", y="total_bill", hue="sex",
        ...                     data=tips, palette="Set2", split=True,
        ...                     scale="count")

    Draw the quartiles as horizontal lines instead of a mini-box:

    .. plot::
        :context: close-figs

        >>> ax = sns.violinplot(x="day", y="total_bill", hue="sex",
        ...                     data=tips, palette="Set2", split=True,
        ...                     scale="count", inner="quartile")

    Show each observation with a stick inside the violin:

    .. plot::
        :context: close-figs

        >>> ax = sns.violinplot(x="day", y="total_bill", hue="sex",
        ...                     data=tips, palette="Set2", split=True,
        ...                     scale="count", inner="stick")

    Scale the density relative to the counts across all bins:

    .. plot::
        :context: close-figs

        >>> ax = sns.violinplot(x="day", y="total_bill", hue="sex",
        ...                     data=tips, palette="Set2", split=True,
        ...                     scale="count", inner="stick", scale_hue=False)

    Use a narrow bandwidth to reduce the amount of smoothing:

    .. plot::
        :context: close-figs

        >>> ax = sns.violinplot(x="day", y="total_bill", hue="sex",
        ...                     data=tips, palette="Set2", split=True,
        ...                     scale="count", inner="stick",
        ...                     scale_hue=False, bw=.2)

    Draw horizontal violins (if the grouping variable has a ``Categorical``
    dtype, the ``orient`` argument can be omitted):

    .. plot::
        :context: close-figs

        >>> planets = sns.load_dataset("planets")
        >>> ax = sns.violinplot(x="orbital_period", y="method",
        ...                     data=planets[planets.orbital_period < 1000],
        ...                     scale="width", orient="h", palette="Set3")

    Draw a violin plot on to a :class:`FacetGrid` to group within an additional
    categorical variable:

    .. plot::
        :context: close-figs

        >>> g = sns.FacetGrid(tips, col="time", size=4, aspect=.7)
        >>> (g.map(sns.violinplot, "sex", "total_bill", "smoker", split=True)
        ...   .despine(left=True)
        ...   .add_legend(title="smoker"))  # doctest: +ELLIPSIS
        <seaborn.axisgrid.FacetGrid object at 0x...>

    """).format(**_boxplot_docs)


def stripplot(x=None, y=None, hue=None, data=None, order=None, hue_order=None,
              jitter=False, split=True, orient=None, color=None, palette=None,
              size=7, edgecolor="w", linewidth=1, ax=None, **kwargs):

    plotter = _StripPlotter(x, y, hue, data, order, hue_order,
                            jitter, split, orient, color, palette)
    if ax is None:
        ax = plt.gca()

    kwargs.update(dict(s=size ** 2, edgecolor=edgecolor, linewidth=linewidth))
    if edgecolor == "gray":
        kwargs["edgecolor"] = plotter.gray

    plotter.plot(ax, kwargs)

    return ax


stripplot.__doc__ = dedent("""\
    Draw a scatterplot where one variable is categorical.

    A strip plot can be drawn on its own, but it is also a good complement
    to a box or violinplot in cases where you want to show all observations
    along with some representation of the underlying distribution.

    {main_api_narrative}

    Parameters
    ----------
    {main_api_params}
    jitter : float, ``True``/``1`` is special-cased, optional
        Amount of jitter (only along the categorical axis) to apply. This
        can be useful when you have many points and they overlap, so that
        it is easier to see the distribution. You can specify the amount
        of jitter (half the width of the uniform random variable support),
        or just use ``True`` for a good default.
    split : bool, optional
        When using ``hue`` nesting, setting this to ``True`` will separate
        the strips for different hue levels along the categorical axis.
        Otherwise, the points for each level will be plotted on top of
        each other.
    {orient}
    {color}
    {palette}
    size : float, optional
        Diameter of the markers, in points. (Although ``plt.scatter`` is used
        to draw the points, the ``size`` argument here takes a "normal"
        markersize and not size^2 like ``plt.scatter``.
    edgecolor : matplotlib color, "gray" is special-cased, optional
        Color of the lines around each point. If you pass ``"gray"``, the
        brightness is determined by the color palette used for the body
        of the points.
    {linewidth}
    {ax_in}

    Returns
    -------
    {ax_out}

    See Also
    --------
    {boxplot}
    {violinplot}

    Examples
    --------

    Draw a single horizontal strip plot:

    .. plot::
        :context: close-figs

        >>> import seaborn as sns
        >>> sns.set_style("whitegrid")
        >>> tips = sns.load_dataset("tips")
        >>> ax = sns.stripplot(x=tips["total_bill"])

    Group the strips by a categorical variable:

    .. plot::
        :context: close-figs

        >>> ax = sns.stripplot(x="day", y="total_bill", data=tips)

    Add jitter to bring out the distribution of values:

    .. plot::
        :context: close-figs

        >>> ax = sns.stripplot(x="day", y="total_bill", data=tips, jitter=True)

    Use a smaller amount of jitter:

    .. plot::
        :context: close-figs

        >>> ax = sns.stripplot(x="day", y="total_bill", data=tips, jitter=0.05)

    Draw horizontal strips (if the grouping variable has a ``Categorical``
    dtype, the ``orient`` argument can be omitted):

    .. plot::
        :context: close-figs

        >>> ax = sns.stripplot(x="total_bill", y="day", data=tips,
        ...                    jitter=True, orient="h")

    Nest the strips within a second categorical variable:

    .. plot::
        :context: close-figs

        >>> ax = sns.stripplot(x="sex", y="total_bill", hue="day",
        ...                    data=tips, jitter=True)

    Draw each level of the ``hue`` variable at the same location on the
    major categorical axis:

    .. plot::
        :context: close-figs

        >>> ax = sns.stripplot(x="day", y="total_bill", hue="smoker",
        ...                    data=tips, jitter=True,
        ...                    palette="Set2", split=False)

    Draw strips with large points and different aesthetics:

    .. plot::
        :context: close-figs

        >>> ax =  sns.stripplot("day", "total_bill", "smoker", data=tips,
        ...                    palette="Set2", size=20, marker="D",
        ...                    edgecolor="gray", alpha=.25)

    Draw strips of observations on top of a box plot:

    .. plot::
        :context: close-figs

        >>> ax = sns.boxplot(x="total_bill", y="day", data=tips,
        ...                  orient="h", whis=np.inf)
        >>> ax = sns.stripplot(x="total_bill", y="day", data=tips,
        ...                    jitter=True, orient="h")

    Draw strips of observations on top of a violin plot

    .. plot::
        :context: close-figs

        >>> ax = sns.violinplot(x="day", y="total_bill", data=tips, inner=None)
        >>> ax = sns.stripplot(x="day", y="total_bill", data=tips,
        ...                    jitter=True, color="white", edgecolor="gray")

    """).format(**_boxplot_docs)


def _freedman_diaconis_bins(a):
    """Calculate number of hist bins using Freedman-Diaconis rule."""
    # From http://stats.stackexchange.com/questions/798/
    a = np.asarray(a)
    h = 2 * iqr(a) / (len(a) ** (1 / 3))
    # fall back to 10 bins if iqr is 0
    if h == 0:
        return 10.
    else:
        return np.ceil((a.max() - a.min()) / h)


def distplot(a, bins=None, hist=True, kde=True, rug=False, fit=None,
             hist_kws=None, kde_kws=None, rug_kws=None, fit_kws=None,
             color=None, vertical=False, norm_hist=False, axlabel=None,
             label=None, ax=None):
    """Flexibly plot a distribution of observations.

    Parameters
    ----------

    a : (squeezable to) 1d array
        Observed data.
    bins : argument for matplotlib hist(), or None, optional
        Specification of hist bins, or None to use Freedman-Diaconis rule.
    hist : bool, optional
        Whether to plot a (normed) histogram.
    kde : bool, optional
        Whether to plot a gaussian kernel density estimate.
    rug : bool, optional
        Whether to draw a rugplot on the support axis.
    fit : random variable object, optional
        An object with `fit` method, returning a tuple that can be passed to a
        `pdf` method a positional arguments following an grid of values to
        evaluate the pdf on.
    {hist, kde, rug, fit}_kws : dictionaries, optional
        Keyword arguments for underlying plotting functions.
    color : matplotlib color, optional
        Color to plot everything but the fitted curve in.
    vertical : bool, optional
        If True, oberved values are on y-axis.
    norm_hist : bool, otional
        If True, the histogram height shows a density rather than a count.
        This is implied if a KDE or fitted density is plotted.
    axlabel : string, False, or None, optional
        Name for the support axis label. If None, will try to get it
        from a.namel if False, do not set a label.
    label : string, optional
        Legend label for the relevent component of the plot
    ax : matplotlib axis, optional
        if provided, plot on this axis

    Returns
    -------
    ax : matplotlib axis

    """
    if ax is None:
        ax = plt.gca()

    # Intelligently label the support axis
    label_ax = bool(axlabel)
    if axlabel is None and hasattr(a, "name"):
        axlabel = a.name
        if axlabel is not None:
            label_ax = True

    # Make a a 1-d array
    a = np.asarray(a).squeeze()

    # Decide if the hist is normed
    norm_hist = norm_hist or kde or (fit is not None)

    # Handle dictionary defaults
    if hist_kws is None:
        hist_kws = dict()
    if kde_kws is None:
        kde_kws = dict()
    if rug_kws is None:
        rug_kws = dict()
    if fit_kws is None:
        fit_kws = dict()

    # Get the color from the current color cycle
    if color is None:
        if vertical:
            line, = ax.plot(0, a.mean())
        else:
            line, = ax.plot(a.mean(), 0)
        color = line.get_color()
        line.remove()

    # Plug the label into the right kwarg dictionary
    if label is not None:
        if hist:
            hist_kws["label"] = label
        elif kde:
            kde_kws["label"] = label
        elif rug:
            rug_kws["label"] = label
        elif fit:
            fit_kws["label"] = label

    if hist:
        if bins is None:
            bins = _freedman_diaconis_bins(a)
        hist_kws.setdefault("alpha", 0.4)
        hist_kws.setdefault("normed", norm_hist)
        orientation = "horizontal" if vertical else "vertical"
        hist_color = hist_kws.pop("color", color)
        ax.hist(a, bins, orientation=orientation,
                color=hist_color, **hist_kws)
        if hist_color != color:
            hist_kws["color"] = hist_color

    if kde:
        kde_color = kde_kws.pop("color", color)
        kdeplot(a, vertical=vertical, ax=ax, color=kde_color, **kde_kws)
        if kde_color != color:
            kde_kws["color"] = kde_color

    if rug:
        rug_color = rug_kws.pop("color", color)
        axis = "y" if vertical else "x"
        rugplot(a, axis=axis, ax=ax, color=rug_color, **rug_kws)
        if rug_color != color:
            rug_kws["color"] = rug_color

    if fit is not None:
        fit_color = fit_kws.pop("color", "#282828")
        gridsize = fit_kws.pop("gridsize", 200)
        cut = fit_kws.pop("cut", 3)
        clip = fit_kws.pop("clip", (-np.inf, np.inf))
        bw = stats.gaussian_kde(a).scotts_factor() * a.std(ddof=1)
        x = _kde_support(a, bw, gridsize, cut, clip)
        params = fit.fit(a)
        pdf = lambda x: fit.pdf(x, *params)
        y = pdf(x)
        if vertical:
            x, y = y, x
        ax.plot(x, y, color=fit_color, **fit_kws)
        if fit_color != "#282828":
            fit_kws["color"] = fit_color

    if label_ax:
        if vertical:
            ax.set_ylabel(axlabel)
        else:
            ax.set_xlabel(axlabel)

    return ax


def _univariate_kdeplot(data, shade, vertical, kernel, bw, gridsize, cut,
                        clip, legend, ax, cumulative=False, **kwargs):
    """Plot a univariate kernel density estimate on one of the axes."""

    # Sort out the clipping
    if clip is None:
        clip = (-np.inf, np.inf)

    # Calculate the KDE
    if _has_statsmodels:
        # Prefer using statsmodels for kernel flexibility
        x, y = _statsmodels_univariate_kde(data, kernel, bw,
                                           gridsize, cut, clip,
                                           cumulative=cumulative)
    else:
        # Fall back to scipy if missing statsmodels
        if kernel != "gau":
            kernel = "gau"
            msg = "Kernel other than `gau` requires statsmodels."
            warnings.warn(msg, UserWarning)
        if cumulative:
            raise ImportError("Cumulative distributions are currently"
                              "only implemented in statsmodels."
                              "Please install statsmodels.")
        x, y = _scipy_univariate_kde(data, bw, gridsize, cut, clip)

    # Make sure the density is nonnegative
    y = np.amax(np.c_[np.zeros_like(y), y], axis=1)

    # Flip the data if the plot should be on the y axis
    if vertical:
        x, y = y, x

    # Check if a label was specified in the call
    label = kwargs.pop("label", None)

    # Otherwise check if the data object has a name
    if label is None and hasattr(data, "name"):
        label = data.name

    # Decide if we're going to add a legend
    legend = label is not None and legend
    label = "_nolegend_" if label is None else label

    # Use the active color cycle to find the plot color
    line, = ax.plot(x, y, **kwargs)
    color = line.get_color()
    line.remove()
    kwargs.pop("color", None)

    # Draw the KDE plot and, optionally, shade
    ax.plot(x, y, color=color, label=label, **kwargs)
    alpha = kwargs.get("alpha", 0.25)
    if shade:
        if vertical:
            ax.fill_betweenx(y, 1e-12, x, color=color, alpha=alpha)
        else:
            ax.fill_between(x, 1e-12, y, color=color, alpha=alpha)

    # Draw the legend here
    if legend:
        ax.legend(loc="best")

    return ax


def _statsmodels_univariate_kde(data, kernel, bw, gridsize, cut, clip,
                                cumulative=False):
    """Compute a univariate kernel density estimate using statsmodels."""
    fft = kernel == "gau"
    kde = smnp.KDEUnivariate(data)
    kde.fit(kernel, bw, fft, gridsize=gridsize, cut=cut, clip=clip)
    if cumulative:
        grid, y = kde.support, kde.cdf
    else:
        grid, y = kde.support, kde.density
    return grid, y


def _scipy_univariate_kde(data, bw, gridsize, cut, clip):
    """Compute a univariate kernel density estimate using scipy."""
    try:
        kde = stats.gaussian_kde(data, bw_method=bw)
    except TypeError:
        kde = stats.gaussian_kde(data)
        if bw != "scott":  # scipy default
            msg = ("Ignoring bandwidth choice, "
                   "please upgrade scipy to use a different bandwidth.")
            warnings.warn(msg, UserWarning)
    if isinstance(bw, str):
        bw = "scotts" if bw == "scott" else bw
        bw = getattr(kde, "%s_factor" % bw)()
    grid = _kde_support(data, bw, gridsize, cut, clip)
    y = kde(grid)
    return grid, y


def _bivariate_kdeplot(x, y, filled, kernel, bw, gridsize, cut, clip, axlabel,
                       ax, **kwargs):
    """Plot a joint KDE estimate as a bivariate contour plot."""

    # Determine the clipping
    if clip is None:
        clip = [(-np.inf, np.inf), (-np.inf, np.inf)]
    elif np.ndim(clip) == 1:
        clip = [clip, clip]

    # Calculate the KDE
    if _has_statsmodels:
        xx, yy, z = _statsmodels_bivariate_kde(x, y, bw, gridsize, cut, clip)
    else:
        xx, yy, z = _scipy_bivariate_kde(x, y, bw, gridsize, cut, clip)

    # Plot the contours
    n_levels = kwargs.pop("n_levels", 10)
    cmap = kwargs.get("cmap", "BuGn" if filled else "BuGn_d")
    if isinstance(cmap, str):
        if cmap.endswith("_d"):
            pal = ["#333333"]
            pal.extend(color_palette(cmap.replace("_d", "_r"), 2))
            cmap = blend_palette(pal, as_cmap=True)
    kwargs["cmap"] = cmap
    contour_func = ax.contourf if filled else ax.contour
    contour_func(xx, yy, z, n_levels, **kwargs)
    kwargs["n_levels"] = n_levels

    # Label the axes
    if hasattr(x, "name") and axlabel:
        ax.set_xlabel(x.name)
    if hasattr(y, "name") and axlabel:
        ax.set_ylabel(y.name)

    return ax


def _statsmodels_bivariate_kde(x, y, bw, gridsize, cut, clip):
    """Compute a bivariate kde using statsmodels."""
    if isinstance(bw, str):
        bw_func = getattr(smnp.bandwidths, "bw_" + bw)
        x_bw = bw_func(x)
        y_bw = bw_func(y)
        bw = [x_bw, y_bw]
    elif np.isscalar(bw):
        bw = [bw, bw]

    if isinstance(x, pd.Series):
        x = x.values
    if isinstance(y, pd.Series):
        y = y.values

    kde = smnp.KDEMultivariate([x, y], "cc", bw)
    x_support = _kde_support(x, kde.bw[0], gridsize, cut, clip[0])
    y_support = _kde_support(y, kde.bw[1], gridsize, cut, clip[1])
    xx, yy = np.meshgrid(x_support, y_support)
    z = kde.pdf([xx.ravel(), yy.ravel()]).reshape(xx.shape)
    return xx, yy, z


def _scipy_bivariate_kde(x, y, bw, gridsize, cut, clip):
    """Compute a bivariate kde using scipy."""
    data = np.c_[x, y]
    kde = stats.gaussian_kde(data.T)
    data_std = data.std(axis=0, ddof=1)
    if isinstance(bw, str):
        bw = "scotts" if bw == "scott" else bw
        bw_x = getattr(kde, "%s_factor" % bw)() * data_std[0]
        bw_y = getattr(kde, "%s_factor" % bw)() * data_std[1]
    elif np.isscalar(bw):
        bw_x, bw_y = bw, bw
    else:
        msg = ("Cannot specify a different bandwidth for each dimension "
               "with the scipy backend. You should install statsmodels.")
        raise ValueError(msg)
    x_support = _kde_support(data[:, 0], bw_x, gridsize, cut, clip[0])
    y_support = _kde_support(data[:, 1], bw_y, gridsize, cut, clip[1])
    xx, yy = np.meshgrid(x_support, y_support)
    z = kde([xx.ravel(), yy.ravel()]).reshape(xx.shape)
    return xx, yy, z


def kdeplot(data, data2=None, shade=False, vertical=False, kernel="gau",
            bw="scott", gridsize=100, cut=3, clip=None, legend=True, ax=None,
            cumulative=False, **kwargs):
    """Fit and plot a univariate or bivarate kernel density estimate.

    Parameters
    ----------
    data : 1d or 2d array-like
        Input data. If two-dimensional, assumed to be shaped (n_unit x n_var),
        and a bivariate contour plot will be drawn.
    data2: 1d array-like
        Second input data. If provided `data` must be one-dimensional, and
        a bivariate plot is produced.
    shade : bool, optional
        If true, shade in the area under the KDE curve (or draw with filled
        contours when data is bivariate).
    vertical : bool
        If True, density is on x-axis.
    kernel : {'gau' | 'cos' | 'biw' | 'epa' | 'tri' | 'triw' }, optional
        Code for shape of kernel to fit with. Bivariate KDE can only use
        gaussian kernel.
    bw : {'scott' | 'silverman' | scalar | pair of scalars }, optional
        Name of reference method to determine kernel size, scalar factor,
        or scalar for each dimension of the bivariate plot.
    gridsize : int, optional
        Number of discrete points in the evaluation grid.
    cut : scalar, optional
        Draw the estimate to cut * bw from the extreme data points.
    clip : pair of scalars, or pair of pair of scalars, optional
        Lower and upper bounds for datapoints used to fit KDE. Can provide
        a pair of (low, high) bounds for bivariate plots.
    legend : bool, optoinal
        If True, add a legend or label the axes when possible.
    ax : matplotlib axis, optional
        Axis to plot on, otherwise uses current axis.
    cumulative : bool
        If draw, draw the cumulative distribution estimated by the kde.
    kwargs : other keyword arguments for plot()

    Returns
    -------
    ax : matplotlib axis
        Axis with plot.

    """
    if ax is None:
        ax = plt.gca()

    data = data.astype(np.float64)
    if data2 is not None:
        data2 = data2.astype(np.float64)

    bivariate = False
    if isinstance(data, np.ndarray) and np.ndim(data) > 1:
        bivariate = True
        x, y = data.T
    elif isinstance(data, pd.DataFrame) and np.ndim(data) > 1:
        bivariate = True
        x = data.iloc[:, 0].values
        y = data.iloc[:, 1].values
    elif data2 is not None:
        bivariate = True
        x = data
        y = data2

    if bivariate and cumulative:
        raise TypeError("Cumulative distribution plots are not"
                        "supported for bivariate distributions.")
    if bivariate:
        ax = _bivariate_kdeplot(x, y, shade, kernel, bw, gridsize,
                                cut, clip, legend, ax, **kwargs)
    else:
        ax = _univariate_kdeplot(data, shade, vertical, kernel, bw,
                                 gridsize, cut, clip, legend, ax,
                                 cumulative=cumulative, **kwargs)

    return ax


def rugplot(a, height=None, axis="x", ax=None, **kwargs):
    """Plot datapoints in an array as sticks on an axis.

    Parameters
    ----------
    a : vector
        1D array of datapoints.
    height : scalar, optional
        Height of ticks, if None draw at 5% of axis range.
    axis : {'x' | 'y'}, optional
        Axis to draw rugplot on.
    ax : matplotlib axis
        Axis to draw plot into; otherwise grabs current axis.
    kwargs : other keyword arguments for plt.plot()

    Returns
    -------
    ax : matplotlib axis
        Axis with rugplot.

    """
    if ax is None:
        ax = plt.gca()
    a = np.asarray(a)
    vertical = kwargs.pop("vertical", None)
    if vertical is not None:
        axis = "y" if vertical else "x"
    other_axis = dict(x="y", y="x")[axis]
    min, max = getattr(ax, "get_%slim" % other_axis)()
    if height is None:
        range = max - min
        height = range * .05
    if axis == "x":
        ax.plot([a, a], [min, min + height], **kwargs)
    else:
        ax.plot([min, min + height], [a, a], **kwargs)
    return ax


def jointplot(x, y, data=None, kind="scatter", stat_func=stats.pearsonr,
              color=None, size=6, ratio=5, space=.2,
              dropna=True, xlim=None, ylim=None,
              joint_kws=None, marginal_kws=None, annot_kws=None):
    """Draw a plot of two variables with bivariate and univariate graphs.

    Parameters
    ----------
    x, y : strings or vectors
        Data or names of variables in `data`.
    data : DataFrame, optional
        DataFrame when `x` and `y` are variable names.
    kind : { "scatter" | "reg" | "resid" | "kde" | "hex" }, optional
        Kind of plot to draw.
    stat_func : callable or None
        Function used to calculate a statistic about the relationship and
        annotate the plot. Should map `x` and `y` either to a single value
        or to a (value, p) tuple. Set to ``None`` if you don't want to
        annotate the plot.
    color : matplotlib color, optional
        Color used for the plot elements.
    size : numeric, optional
        Size of the figure (it will be square).
    ratio : numeric, optional
        Ratio of joint axes size to marginal axes height.
    space : numeric, optional
        Space between the joint and marginal axes
    dropna : bool, optional
        If True, remove observations that are missing from `x` and `y`.
    {x, y}lim : two-tuples, optional
        Axis limits to set before plotting.
    {joint, marginal, annot}_kws : dicts
        Additional keyword arguments for the plot components.

    Returns
    -------
    grid : JointGrid
        JointGrid object with the plot on it.

    See Also
    --------
    JointGrid : The Grid class used for drawing this plot. Use it directly if
                you need more flexibility.

    """
    # Set up empty default kwarg dicts
    if joint_kws is None:
        joint_kws = {}
    if marginal_kws is None:
        marginal_kws = {}
    if annot_kws is None:
        annot_kws = {}

    # Make a colormap based off the plot color
    if color is None:
        color = color_palette()[0]
    color_rgb = mpl.colors.colorConverter.to_rgb(color)
    colors = [set_hls_values(color_rgb, l=l) for l in np.linspace(1, 0, 12)]
    cmap = blend_palette(colors, as_cmap=True)

    # Initialize the JointGrid object
    grid = JointGrid(x, y, data, dropna=dropna,
                     size=size, ratio=ratio, space=space,
                     xlim=xlim, ylim=ylim)

    # Plot the data using the grid
    if kind == "scatter":

        joint_kws.setdefault("color", color)
        grid.plot_joint(plt.scatter, **joint_kws)

        marginal_kws.setdefault("kde", False)
        marginal_kws.setdefault("color", color)
        grid.plot_marginals(distplot, **marginal_kws)

    elif kind.startswith("hex"):

        x_bins = _freedman_diaconis_bins(grid.x)
        y_bins = _freedman_diaconis_bins(grid.y)
        gridsize = int(np.mean([x_bins, y_bins]))

        joint_kws.setdefault("gridsize", gridsize)
        joint_kws.setdefault("cmap", cmap)
        grid.plot_joint(plt.hexbin, **joint_kws)

        marginal_kws.setdefault("kde", False)
        marginal_kws.setdefault("color", color)
        grid.plot_marginals(distplot, **marginal_kws)

    elif kind.startswith("kde"):

        joint_kws.setdefault("shade", True)
        joint_kws.setdefault("cmap", cmap)
        grid.plot_joint(kdeplot, **joint_kws)

        marginal_kws.setdefault("shade", True)
        marginal_kws.setdefault("color", color)
        grid.plot_marginals(kdeplot, **marginal_kws)

    elif kind.startswith("reg"):

        from .linearmodels import regplot

        marginal_kws.setdefault("color", color)
        grid.plot_marginals(distplot, **marginal_kws)

        joint_kws.setdefault("color", color)
        grid.plot_joint(regplot, **joint_kws)

    elif kind.startswith("resid"):

        from .linearmodels import residplot

        joint_kws.setdefault("color", color)
        grid.plot_joint(residplot, **joint_kws)

        x, y = grid.ax_joint.collections[0].get_offsets().T
        marginal_kws.setdefault("color", color)
        marginal_kws.setdefault("kde", False)
        distplot(x, ax=grid.ax_marg_x, **marginal_kws)
        distplot(y, vertical=True, fit=stats.norm, ax=grid.ax_marg_y,
                 **marginal_kws)
        stat_func = None
    else:
        msg = "kind must be either 'scatter', 'reg', 'resid', 'kde', or 'hex'"
        raise ValueError(msg)

    if stat_func is not None:
        grid.annotate(stat_func, **annot_kws)

    return grid
