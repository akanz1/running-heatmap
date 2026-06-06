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


# Strava's "Activity Type" is sometimes less specific than intervals.icu's —
# e.g. trail runs get tagged as plain "Run" in Strava but "TrailRun" on
# intervals. When dedup matches, we promote Strava's type to the intervals
# value if it falls in this set.
_TYPE_PROMOTIONS: dict[tuple[str, str], str] = {
    ("Run", "Trail Run"): "Trail Run",
    ("Ride", "Mountain Bike Ride"): "Mountain Bike Ride",
    ("Ride", "Gravel Ride"): "Gravel Ride",
}


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

    Type promotion: when a Strava row matches an intervals row, and the
    intervals type is more specific (per `_TYPE_PROMOTIONS`), the Strava
    row's type is updated. This catches the common case where Strava
    silently classifies trail runs as plain Run.
    """
    if df_icu.empty:
        return df_strava.reset_index(drop=True)

    df_strava = df_strava.reset_index(drop=True).copy()

    # Build dedup-key → strava row index, so we can both drop matching
    # intervals rows AND propagate intervals types back onto strava rows.
    strava_key_to_idx: dict[str, int] = {}
    for i, r in enumerate(df_strava.itertuples(index=False)):
        if pd.isna(r.start_lat) or pd.isna(r.distance_m):
            continue
        day = r.date.floor("D")
        bucket = round(r.distance_m / 100)
        # Pre-expand by ±2 buckets (~±200 m) — same activity often differs by
        # >100 m between platforms (different start/stop/pause trimming).
        for b_off in (-2, -1, 0, 1, 2):
            strava_key_to_idx.setdefault(_dedup_key(day, r.start_lat, r.start_lon, bucket + b_off), i)

    keep_mask: list[bool] = []
    promotions = 0
    for r in df_icu.itertuples(index=False):
        if pd.isna(r.start_lat) or pd.isna(r.distance_m):
            keep_mask.append(True)
            continue
        base = r.date.floor("D")
        bucket = round(r.distance_m / 100)
        matched_idx: int | None = None
        for d_off in (-1, 0, 1):
            k = _dedup_key(base + pd.Timedelta(days=d_off), r.start_lat, r.start_lon, bucket)
            if k in strava_key_to_idx:
                matched_idx = strava_key_to_idx[k]
                break
        if matched_idx is None:
            keep_mask.append(True)
            continue

        # Promote the strava row's type if intervals has a finer label.
        cur = df_strava.at[matched_idx, "type"]
        promoted = _TYPE_PROMOTIONS.get((cur, r.type))
        if promoted and promoted != cur:
            df_strava.at[matched_idx, "type"] = promoted
            promotions += 1
        keep_mask.append(False)

    n_drop = sum(1 for k in keep_mask if not k)
    if n_drop:
        log.info("Dedup: dropped %d intervals.icu duplicates (already in strava_export)", n_drop)
    if promotions:
        log.info("Type promotion: %d strava rows updated from intervals.icu type", promotions)
    return pd.concat([df_strava, df_icu[keep_mask]], ignore_index=True)


def _filter_by_type_and_date(
    df: pd.DataFrame, activity_types: list[str], date_from: str | None, date_to: str | None
) -> pd.DataFrame:
    # Empty type list = no type filter (the "all activities" profile).
    runs = (df if not activity_types else df[df["type"].isin(activity_types)]).copy()
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


def load_all(config: Config) -> tuple[pd.DataFrame, float | None, float | None]:
    """Load + merge + dedup + GPS-filter both sources. No activity-type filter.

    Returns (df_all, home_lat, home_lon). Home is detected from the full set
    (independent of activity-type profile).
    """
    strava_dir = config.resolved_activities_dir()
    log.info("Source: strava_export at %s", strava_dir)
    df_strava = strava_export.load(strava_dir, excluded_ids=config.all_excluded_strava_ids())

    icu_dir = config.resolved_intervals_icu_cache_dir()
    df_icu = intervals_icu.load(icu_dir, excluded_ids=config.all_excluded_intervals_ids())
    if not df_icu.empty:
        log.info("Source: intervals.icu cache at %s", icu_dir)

    df = _merge(df_strava, df_icu)
    df = df[df["start_lat"].notna() & (df["gps_spread_m"] >= config.gps_spread_min_m)].copy()
    log.info("After removing no-GPS / indoor: %d", len(df))

    home_lat, home_lon = _resolve_home(df, config)
    return df, home_lat, home_lon


def filter_for_profile(
    df_all: pd.DataFrame,
    activity_types: list[str],
    config: Config,
    home_lat: float | None,
    home_lon: float | None,
) -> pd.DataFrame:
    """Apply type/date/home-radius filters for a single profile."""
    runs = _filter_by_type_and_date(df_all, activity_types, config.date_from, config.date_to)
    if config.radius_km is not None and home_lat is not None and home_lon is not None:
        runs = _filter_by_home_radius(runs, home_lat, home_lon, config.radius_km)
    return runs


def load_and_filter(config: Config) -> tuple[pd.DataFrame, float | None, float | None]:
    """Single-profile convenience wrapper (back-compat).

    Uses `config.activity_types`. For the multi-profile flow, call
    `load_all()` + `filter_for_profile()` per profile.
    """
    df_all, home_lat, home_lon = load_all(config)
    runs = filter_for_profile(df_all, config.activity_types, config, home_lat, home_lon)
    return runs, home_lat, home_lon
