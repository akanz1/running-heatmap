from __future__ import annotations

import warnings

from heatmap.activities import load_and_filter
from heatmap.config import Config
from heatmap.raster import blur_and_normalize
from heatmap.raster import rasterize
from heatmap.render import build_and_save
from heatmap.tracks import load_tracks

warnings.filterwarnings("ignore")

__all__ = ["run", "Config"]


def run(config: Config) -> str:
    """Full pipeline: load → parse → rasterize → render.

    Returns the path to the saved HTML file.
    """
    runs, home_lat, home_lon, activities_dir = load_and_filter(config)
    if runs.empty:
        raise ValueError("No activities after filtering — check config filters.")

    tracks = load_tracks(runs, activities_dir, config.track_cache)
    if not tracks:
        raise ValueError(
            "No tracks loaded — check ACTIVITIES_DIR, ACTIVITY_TYPES, and date filters."
        )

    grids = rasterize(tracks, home_lat, home_lon, config)
    normalized = blur_and_normalize(grids, config)
    return build_and_save(grids, normalized, tracks, config)
