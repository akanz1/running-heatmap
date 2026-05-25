from __future__ import annotations

import logging
import math as _math
import os
import sys
import warnings

import numpy as np
import pandas as _pd

from heatmap.activities import filter_for_profile
from heatmap.activities import load_all
from heatmap.config import Config
from heatmap.constants import EARTH_RADIUS_KM
from heatmap.render import build_and_save
from heatmap.sources import intervals_icu
from heatmap.stats_panel import load_stats_panel_data
from heatmap.stats_panel import save_stats_panel_data
from heatmap.stats_panel import stats_panel_data_from_tracks
from heatmap.tiles import build_pyramid
from heatmap.tiles import load_pyramid_metadata
from heatmap.tiles import lonlat_to_global_px
from heatmap.tiles import TILE_SIZE
from heatmap.tracks import load_tracks
from heatmap.tracks import Track

warnings.filterwarnings("ignore")

log = logging.getLogger(__name__)

__all__ = ["Config", "configure_logging", "run", "sync_intervals_icu"]


def sync_intervals_icu(config: Config) -> int:
    """Sync intervals.icu activities into the local cache.

    Skipped (returns 0) if `config.sync_enabled` is False, `HEATMAP_SKIP_SYNC=1`,
    or `INTERVALS_ICU_API_KEY` is unset.
    """
    if not config.sync_enabled:
        log.info("config.sync_enabled=False — skipping intervals.icu sync")
        return 0
    if os.environ.get("HEATMAP_SKIP_SYNC"):
        log.info("HEATMAP_SKIP_SYNC set — skipping intervals.icu sync")
        return 0

    api_key = os.environ.get("INTERVALS_ICU_API_KEY")
    athlete_id = os.environ.get("INTERVALS_ICU_ATHLETE_ID")
    if not api_key or not athlete_id:
        log.info("INTERVALS_ICU_API_KEY/ATHLETE_ID unset — skipping intervals.icu sync")
        return 0

    return intervals_icu.sync(
        config.resolved_intervals_icu_cache_dir(),
        athlete_id=athlete_id,
        api_key=api_key,
    )


def configure_logging(level: int = logging.INFO) -> None:
    """Initialise root logger formatting. Idempotent."""
    root = logging.getLogger()
    if root.handlers:
        return
    logging.basicConfig(level=level, format="%(levelname)-7s %(name)s: %(message)s")


def _union_min_zoom_from_df(df_all: object, config: Config) -> int | None:
    """Compute auto-min-zoom from the union bbox of all activities' start points.

    Cheap proxy for the per-profile painter heuristic, but yields the same
    answer for the widest-spread profile (which would be `runs` in a typical
    setup). Used so every profile builds down to the same bottom zoom.

    Returns None if `config.min_zoom` is set (user override wins).
    """
    if config.min_zoom is not None:
        return config.min_zoom
    if not isinstance(df_all, _pd.DataFrame) or df_all.empty:
        return None

    lat = df_all["start_lat"].dropna()
    lon = df_all["start_lon"].dropna()
    if lat.empty or lon.empty:
        return None

    z_max = config.max_zoom
    x_lo, y_hi = lonlat_to_global_px(float(lat.max()), float(lon.min()), z_max)
    x_hi, y_lo = lonlat_to_global_px(float(lat.min()), float(lon.max()), z_max)
    span_px = max(abs(x_hi - x_lo), abs(y_hi - y_lo))
    target_px = config.min_zoom_target_px

    if span_px <= target_px or span_px <= TILE_SIZE:
        return z_max
    return max(0, _math.ceil(z_max - _math.log2(span_px / target_px)))


def _confirm_full_rebuild(n_new: int) -> bool:
    """Prompt the user before the slow track-parse + tile-pyramid rebuild.

    Returns True to proceed, False to abort. Auto-confirms when stdin is not
    a TTY (CI, piped input) or when HEATMAP_YES=1 is set.
    """
    if os.environ.get("HEATMAP_YES") or not sys.stdin.isatty():
        return True
    print()
    print(f"  intervals.icu sync: {n_new} new activit{'y' if n_new == 1 else 'ies'}")
    print("  Full rebuild parses all tracks and re-rasterises the tile pyramid")
    print("  (typically a few minutes at z=17).")
    print("  For sync-only without rebuild, abort and run `make sync` instead.")
    print("  Set HEATMAP_YES=1 to skip this prompt.")
    try:
        ans = input("  Continue with full rebuild? [Y/n] ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        print()
        return False
    return ans in ("", "y", "yes")


def _clip_tracks(
    tracks: list[Track],
    home_lat: float,
    home_lon: float,
    clip_m: float,
) -> list[Track]:
    """Drop GPS points further than clip_m metres from home. Empty tracks removed."""
    clipped: list[Track] = []
    home_lat_r = np.radians(home_lat)
    for t in tracks:
        lats = np.array([p[0] for p in t.points])
        lons = np.array([p[1] for p in t.points])
        dlat = np.radians(lats - home_lat)
        dlon = np.radians(lons - home_lon)
        a = np.sin(dlat / 2) ** 2 + np.cos(home_lat_r) * np.cos(np.radians(lats)) * np.sin(dlon / 2) ** 2
        dists = EARTH_RADIUS_KM * 1000 * 2 * np.arcsin(np.sqrt(a))
        mask = dists <= clip_m
        if mask.any():
            clipped.append(
                Track(
                    label=t.label,
                    date_days=t.date_days,
                    distance_m=t.distance_m,
                    moving_time_s=t.moving_time_s,
                    elevation_gain_m=t.elevation_gain_m,
                    points=[t.points[i] for i in range(len(t.points)) if mask[i]],
                )
            )
    log.info("Clipped tracks within %.1f km of home: %d → %d tracks", clip_m / 1000, len(tracks), len(clipped))
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

    profiles = config.resolved_profiles()

    if os.environ.get("HEATMAP_HTML_ONLY"):
        log.info("HEATMAP_HTML_ONLY set — skipping pyramid build")
        pyramid_by_profile = {}
        stats_by_profile = {}
        for profile in profiles:
            pyramid_by_profile[profile] = load_pyramid_metadata(config.output_dir() / "tiles" / profile)
            stats_by_profile[profile] = load_stats_panel_data(config.output_dir(), profile)
        return build_and_save(pyramid_by_profile, config, stats_by_profile=stats_by_profile)

    n_new = sync_intervals_icu(config)

    if not _confirm_full_rebuild(n_new):
        log.info("Aborted before full rebuild. Run `make sync` for sync-only.")
        return ""

    df_all, home_lat, home_lon = load_all(config)
    if df_all.empty:
        msg = "No activities loaded — check sources."
        raise ValueError(msg)

    # Union min_zoom across all profiles: the lowest zoom any profile would
    # auto-pick if built alone. Forces every profile down to that depth so
    # the smaller-bbox profiles (e.g. trail runs) don't appear pixelated /
    # shrunk when the user zooms out below their natural min.
    forced_min_zoom = _union_min_zoom_from_df(df_all, config)

    pyramid_by_profile: dict[str, object] = {}
    stats_by_profile: dict[str, object] = {}
    for profile, types in profiles.items():
        log.info("==== profile %r (types=%s) ====", profile, types)
        runs = filter_for_profile(df_all, types, config, home_lat, home_lon)
        if runs.empty:
            log.warning("profile %r: no activities, skipping", profile)
            continue

        tracks = load_tracks(runs, config.track_cache_path())
        if not tracks:
            log.warning("profile %r: no tracks, skipping", profile)
            continue

        if config.track_clip_radius_km is not None and home_lat is not None and home_lon is not None:
            tracks = _clip_tracks(tracks, home_lat, home_lon, config.track_clip_radius_km * 1000)
            if not tracks:
                log.warning("profile %r: no GPS points after clip, skipping", profile)
                continue

        pyramid = build_pyramid(tracks, config.output_dir(), config, profile=profile, force_min_zoom=forced_min_zoom)
        stats_data = stats_panel_data_from_tracks(tracks)
        save_stats_panel_data(stats_data, config.output_dir(), profile=profile)
        pyramid_by_profile[profile] = pyramid
        stats_by_profile[profile] = stats_data

    if not pyramid_by_profile:
        msg = "No profile produced tiles — check filters."
        raise ValueError(msg)

    return build_and_save(pyramid_by_profile, config, stats_by_profile=stats_by_profile)
