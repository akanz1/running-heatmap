from __future__ import annotations

import logging
import math
from datetime import date
from typing import TYPE_CHECKING

import pandas as pd

from heatmap.constants import EARTH_RADIUS_KM
from heatmap.sources import intervals_icu
from heatmap.sources import strava_export

if TYPE_CHECKING:
    from heatmap.config import Config

log = logging.getLogger(__name__)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return EARTH_RADIUS_KM * 2 * math.asin(math.sqrt(a))


def _detect_home(runs: pd.DataFrame) -> tuple[float, float, int]:
    """Bin start points to a ~1 km grid, return mean coords of the densest cell."""
    cell_lats: dict = {}
    cell_lons: dict = {}
    for lat, lon in zip(runs["start_lat"], runs["start_lon"], strict=False):
        cell = (round(lat, 2), round(lon, 2))
        cell_lats.setdefault(cell, []).append(lat)
        cell_lons.setdefault(cell, []).append(lon)
    best = max(cell_lats, key=lambda c: len(cell_lats[c]))
    home_lat = sum(cell_lats[best]) / len(cell_lats[best])
    home_lon = sum(cell_lons[best]) / len(cell_lons[best])
    return home_lat, home_lon, len(cell_lats[best])


def _dedup_key(day: pd.Timestamp, lat: float, lon: float, dist_bucket: int) -> str:
    return f"{day.date()}_{round(lat, 3)}_{round(lon, 3)}_{dist_bucket}"


def _merge(df_strava: pd.DataFrame, df_icu: pd.DataFrame) -> pd.DataFrame:
    """Concat strava + intervals. Drop intervals rows whose activity is
    already in strava_export.

    Match key: (day, start_lat, start_lon, distance_bucket).
    - coords rounded to 3 dp (~100 m grid)
    - distance bucketed to 100 m, with ±1 bucket tolerance for boundary
      cases where two platforms report distances straddling a bucket edge
    - ±1 day tolerance — Strava's date is UTC, intervals' is local with
      unknown TZ, so runs near midnight UTC fall on different days

    Within-source duplicates are preserved (running the same route every
    day in Strava is 365 distinct activities, not one).
    """
    if df_icu.empty:
        return df_strava.reset_index(drop=True)

    strava_keys: set[str] = set()
    for r in df_strava.itertuples(index=False):
        if pd.isna(r.start_lat) or pd.isna(r.distance_m):
            continue
        day = r.date.floor("D")
        bucket = round(r.distance_m / 100)
        # Pre-expand by ±2 buckets (~±200 m) — same activity often differs by
        # >100 m between platforms (different start/stop/pause trimming).
        for b_off in (-2, -1, 0, 1, 2):
            strava_keys.add(_dedup_key(day, r.start_lat, r.start_lon, bucket + b_off))

    keep_mask = []
    for r in df_icu.itertuples(index=False):
        if pd.isna(r.start_lat) or pd.isna(r.distance_m):
            keep_mask.append(True)
            continue
        base = r.date.floor("D")
        bucket = round(r.distance_m / 100)
        hit = any(
            _dedup_key(base + pd.Timedelta(days=d_off), r.start_lat, r.start_lon, bucket)
            in strava_keys
            for d_off in (-1, 0, 1)
        )
        keep_mask.append(not hit)

    n_drop = sum(1 for k in keep_mask if not k)
    if n_drop:
        log.info("Dedup: dropped %d intervals.icu duplicates (already in strava_export)", n_drop)
    return pd.concat([df_strava, df_icu[keep_mask]], ignore_index=True)


def _filter_by_type_and_date(
    df: pd.DataFrame, activity_types: list[str], date_from: str | None, date_to: str | None
) -> pd.DataFrame:
    runs = df[df["type"].isin(activity_types)].copy()
    log.info("Total matching activities: %d", len(runs))

    d_from = date.fromisoformat(date_from) if date_from else date.min
    d_to = date.fromisoformat(date_to) if date_to else date.today()
    # Compare on calendar day so date_to="2026-05-24" includes activities
    # later that day, not just those starting at 00:00.
    runs = runs[runs["date"].dt.date.between(d_from, d_to)].copy()
    log.info("After date filter (%s - %s): %d", d_from, d_to, len(runs))
    return runs


def _resolve_home(runs: pd.DataFrame, config: Config) -> tuple[float | None, float | None]:
    if config.home_lat is not None and config.home_lon is not None:
        log.info("Using manual home: %s, %s", config.home_lat, config.home_lon)
        return config.home_lat, config.home_lon

    if not config.needs_home():
        log.info("Worldwide mode — skipping home detection")
        return None, None

    home_lat, home_lon, n_home = _detect_home(runs)
    log.info(
        "Auto-detected home: %.4f, %.4f (%d of %d activities started there)",
        home_lat,
        home_lon,
        n_home,
        len(runs),
    )
    return home_lat, home_lon


def _filter_by_home_radius(runs: pd.DataFrame, home_lat: float, home_lon: float, radius_km: float) -> pd.DataFrame:
    runs["dist_from_home_km"] = runs.apply(
        lambda r: haversine_km(home_lat, home_lon, r["start_lat"], r["start_lon"]),
        axis=1,
    )
    filtered = runs[runs["dist_from_home_km"] <= radius_km].copy()
    log.info("After home-radius filter (≤%s km): %d activities", radius_km, len(filtered))
    return filtered


def load_and_filter(config: Config) -> tuple[pd.DataFrame, float | None, float | None]:
    """Load + merge all activity sources, filter by user config.

    Returns (filtered_runs, home_lat, home_lon).
    home_lat / home_lon are None in worldwide mode.
    """
    strava_dir = config.resolved_activities_dir()
    log.info("Source: strava_export at %s", strava_dir)
    df_strava = strava_export.load(strava_dir)

    icu_dir = config.resolved_intervals_icu_cache_dir()
    df_icu = intervals_icu.load(icu_dir)
    if not df_icu.empty:
        log.info("Source: intervals.icu cache at %s", icu_dir)

    df = _merge(df_strava, df_icu)
    runs = _filter_by_type_and_date(df, config.activity_types, config.date_from, config.date_to)

    runs = runs[runs["start_lat"].notna() & (runs["gps_spread_m"] >= config.gps_spread_min_m)].copy()
    log.info("After removing no-GPS / indoor: %d", len(runs))

    home_lat, home_lon = _resolve_home(runs, config)

    if config.radius_km is not None and home_lat is not None and home_lon is not None:
        runs = _filter_by_home_radius(runs, home_lat, home_lon, config.radius_km)

    return runs, home_lat, home_lon
