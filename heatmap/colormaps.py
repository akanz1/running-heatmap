from __future__ import annotations

import matplotlib.colors as mcolors
from matplotlib import colormaps as _mpl_cmaps

from heatmap.constants import CMAP_COUNT_NODES
from heatmap.constants import CMAP_ELEV_NODES
from heatmap.constants import CMAP_HILL_NODES
from heatmap.constants import CMAP_HR_NODES
from heatmap.constants import CMAP_SPEED_NODES


def build_cmap(name: str, nodes: list) -> mcolors.LinearSegmentedColormap:
    """Build a LinearSegmentedColormap from [(position, (R, G, B, A)), ...] nodes."""
    pos = [n[0] for n in nodes]
    cdict: dict = {}
    for ci, ch in enumerate(("red", "green", "blue", "alpha")):
        vals = [n[1][ci] for n in nodes]
        cdict[ch] = [(pos[i], vals[i], vals[i]) for i in range(len(pos))]
    return mcolors.LinearSegmentedColormap(name, cdict, N=512)


CMAP_COUNT = build_cmap("count", CMAP_COUNT_NODES)
CMAP_SPEED = build_cmap("speed", CMAP_SPEED_NODES)
CMAP_HR = build_cmap("hr", CMAP_HR_NODES)
CMAP_ELEV = build_cmap("elev", CMAP_ELEV_NODES)
CMAP_HILL = build_cmap("hill", CMAP_HILL_NODES)
# Recency: matplotlib's viridis (dark blue = old → yellow = recent)
CMAP_RECENCY = _mpl_cmaps["viridis"]
