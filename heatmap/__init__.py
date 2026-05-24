from __future__ import annotations

import logging
import os
import warnings

import numpy as np

from heatmap.activities import load_and_filter
from heatmap.config import Config
from heatmap.constants import EARTH_RADIUS_KM
from heatmap.render import build_and_save
from heatmap.tiles import build_pyramid
from heatmap.tiles import load_pyramid_metadata
from heatmap.tracks import load_tracks

warnings.filterwarnings("ignore")

log = logging.getLogger(__name__)

__all__ = ["Config", "configure_logging", "run"]


def configure_logging(level: int = logging.INFO) -> None:
    """Initialise root logger formatting. Idempotent."""
    root = logging.getLogger()
    if root.handlers:
        return
    logging.basicConfig(level=level, format="%(levelname)-7s %(name)s: %(message)s")


def _clip_tracks(
    tracks: list[tuple[str, list[list]]],
    home_lat: float,
    home_lon: float,
    clip_m: float,
) -> list[tuple[str, list[list]]]:
    """Drop GPS points further than clip_m metres from home. Empty tracks removed."""
    clipped: list[tuple[str, list[list]]] = []
    home_lat_r = np.radians(home_lat)
    for label, pts in tracks:
        lats = np.array([p[0] for p in pts])
        lons = np.array([p[1] for p in pts])
        dlat = np.radians(lats - home_lat)
        dlon = np.radians(lons - home_lon)
        a = (
            np.sin(dlat / 2) ** 2
            + np.cos(home_lat_r) * np.cos(np.radians(lats)) * np.sin(dlon / 2) ** 2
        )
        dists = EARTH_RADIUS_KM * 1000 * 2 * np.arcsin(np.sqrt(a))
        mask = dists <= clip_m
        if mask.any():
            clipped.append((label, [pts[i] for i in range(len(pts)) if mask[i]]))
    log.info("Clipped tracks within %.1f km of home: %d → %d tracks",
             clip_m / 1000, len(tracks), len(clipped))
    return clipped


def run(config: Config) -> str:
    """Full pipeline: load → parse → tile-pyramid → render.

    Returns the path to the saved HTML file.

    Setting ``HEATMAP_HTML_ONLY=1`` in the environment skips the activity
    load, track parse, and tile-pyramid steps — instead, the existing tile
    pyramid metadata is loaded and the HTML is regenerated on top of it.
    Useful when iterating on render.py / legend.py / assets.py without
    waiting for the ~4 min pyramid rebuild.
    """
    configure_logging()

    if os.environ.get("HEATMAP_HTML_ONLY"):
        log.info("HEATMAP_HTML_ONLY set — skipping pyramid build")
        pyramid = load_pyramid_metadata(config.output_dir() / "tiles")
        return build_and_save(pyramid, config)

    runs, home_lat, home_lon, activities_dir = load_and_filter(config)
    if runs.empty:
        msg = "No activities after filtering — check config filters."
        raise ValueError(msg)

    tracks = load_tracks(runs, activities_dir, config.track_cache_path())
    if not tracks:
        msg_0 = "No tracks loaded — check activity_types and date filters."
        raise ValueError(msg_0)

    if (
        config.track_clip_radius_km is not None
        and home_lat is not None
        and home_lon is not None
    ):
        tracks = _clip_tracks(tracks, home_lat, home_lon, config.track_clip_radius_km * 1000)
        if not tracks:
            msg_1 = "No GPS points remain after track_clip_radius_km filter."
            raise ValueError(msg_1)

    pyramid = build_pyramid(tracks, config.output_dir(), config)
    return build_and_save(pyramid, config)
