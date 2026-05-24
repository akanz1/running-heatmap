from __future__ import annotations

import logging
import warnings

from heatmap.activities import load_and_filter
from heatmap.config import Config
from heatmap.raster import blur_and_normalize
from heatmap.raster import rasterize
from heatmap.render import build_and_save
from heatmap.tracks import load_tracks

warnings.filterwarnings("ignore")

__all__ = ["Config", "configure_logging", "run"]


def configure_logging(level: int = logging.INFO) -> None:
    """Initialise root logger formatting. Idempotent."""
    root = logging.getLogger()
    if root.handlers:
        return
    logging.basicConfig(level=level, format="%(levelname)-7s %(name)s: %(message)s")


def run(config: Config) -> str:
    """Full pipeline: load → parse → rasterize → render.

    Returns the path to the saved HTML file.
    """
    configure_logging()

    runs, home_lat, home_lon, activities_dir = load_and_filter(config)
    if runs.empty:
        msg = "No activities after filtering — check config filters."
        raise ValueError(msg)

    tracks = load_tracks(runs, activities_dir, config.track_cache_path())
    if not tracks:
        msg_0 = "No tracks loaded — check activity_types and date filters."
        raise ValueError(msg_0)

    grids = rasterize(tracks, home_lat, home_lon, config)
    normalized = blur_and_normalize(grids, config)
    return build_and_save(grids, normalized, tracks, config)
