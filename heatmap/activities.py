from __future__ import annotations

import json
import logging
import math
from datetime import date
from typing import TYPE_CHECKING

import pandas as pd

from heatmap.constants import EARTH_RADIUS_KM
from heatmap.localization import normalize
from heatmap.parsers import parse_track

if TYPE_CHECKING:
    from pathlib import Path

    from heatmap.config import Config

log = logging.getLogger(__name__)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return EARTH_RADIUS_KM * 2 * math.asin(math.sqrt(a))


def _get_gps_start(filepath: Path) -> tuple[float | None, float | None, float | None]:
    """Return (start_lat, start_lon, spread_m) from any supported track format.

    Returns (None, None, None) if the file can't be parsed or has no GPS points.
    """
    points = parse_track(filepath)
    if not points:
        return None, None, None

    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    mid_lat = (min(lats) + max(lats)) / 2
    spread_m = max(
        (max(lats) - min(lats)) * 111_000,
        (max(lons) - min(lons)) * 111_000 * math.cos(math.radians(mid_lat)),
    )
    return lats[0], lons[0], spread_m


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


def _load_csv(activities_dir: Path) -> pd.DataFrame:
    df = pd.read_csv(activities_dir / "activities.csv")
    df = normalize(df)
    df["Activity Date"] = pd.to_datetime(df["Activity Date"], format="mixed", dayfirst=True)
    return df


def _filter_by_type_and_date(
    df: pd.DataFrame, activity_types: list[str], date_from: str | None, date_to: str | None
) -> pd.DataFrame:
    runs = df[df["Activity Type"].isin(activity_types)].copy()
    log.info("Total matching activities in export: %d", len(runs))

    d_from = pd.Timestamp(date_from) if date_from else pd.Timestamp.min
    d_to = pd.Timestamp(date_to) if date_to else pd.Timestamp(date.today())
    runs = runs[runs["Activity Date"].between(d_from, d_to)].copy()
    log.info("After date filter (%s - %s): %d", d_from.date(), d_to.date(), len(runs))
    return runs


def _resolve_gps_starts(runs: pd.DataFrame, activities_dir: Path) -> pd.DataFrame:
    """Augment each row with start_lat / start_lon / gps_spread_m. Disk-cached per export."""
    cache_path = activities_dir / "_gps_cache.json"
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}

    rows = []
    for _, row in runs.iterrows():
        fn = str(row["Filename"])
        cached = cache.get(fn)
        # Retry entries that previously failed (lat is None) — old parser may
        # have lacked support for this file's format.
        if cached is None or cached[0] is None:
            cache[fn] = list(_get_gps_start(activities_dir / fn))
        lat, lon, spread = cache[fn]
        rows.append({**row, "start_lat": lat, "start_lon": lon, "gps_spread_m": spread})

    cache_path.write_text(json.dumps(cache))
    return pd.DataFrame(rows)


def _resolve_home(runs: pd.DataFrame, config: Config) -> tuple[float | None, float | None]:
    """Return (home_lat, home_lon) or (None, None) if no home is needed."""
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


def load_and_filter(config: Config) -> tuple[pd.DataFrame, float | None, float | None, Path]:
    """Load activities CSV, filter by type/date/home radius.

    Returns (filtered_runs, home_lat, home_lon, activities_dir).
    home_lat / home_lon are None in worldwide mode.
    """
    activities_dir = config.resolved_activities_dir()
    log.info("Source: %s", activities_dir)

    df = _load_csv(activities_dir)
    runs = _filter_by_type_and_date(df, config.activity_types, config.date_from, config.date_to)
    runs = _resolve_gps_starts(runs, activities_dir)

    runs = runs[runs["start_lat"].notna() & (runs["gps_spread_m"] >= config.gps_spread_min_m)].copy()
    log.info("After removing no-GPS / indoor: %d", len(runs))

    home_lat, home_lon = _resolve_home(runs, config)

    if config.radius_km is not None and home_lat is not None and home_lon is not None:
        runs = _filter_by_home_radius(runs, home_lat, home_lon, config.radius_km)

    return runs, home_lat, home_lon, activities_dir
