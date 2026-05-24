from __future__ import annotations

import json
import math
from datetime import date
from pathlib import Path

import pandas as pd

from heatmap.config import Config
from heatmap.constants import EARTH_RADIUS_KM
from heatmap.constants import SEMICIRCLE_TO_DEG


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    return EARTH_RADIUS_KM * 2 * math.asin(math.sqrt(a))


def _get_gps_start(filepath: Path) -> tuple[float | None, float | None, float | None]:
    import gzip

    import fitparse

    lats, lons = [], []
    try:
        with gzip.open(filepath, "rb") as f:
            for msg in fitparse.FitFile(f).get_messages("record"):
                d = {x.name: x.value for x in msg}
                if (
                    d.get("position_lat") is not None
                    and d.get("position_long") is not None
                ):
                    lats.append(d["position_lat"] * SEMICIRCLE_TO_DEG)
                    lons.append(d["position_long"] * SEMICIRCLE_TO_DEG)
    except Exception:
        pass

    if not lats:
        return None, None, None

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
    for lat, lon in zip(runs["start_lat"], runs["start_lon"]):
        cell = (round(lat, 2), round(lon, 2))
        cell_lats.setdefault(cell, []).append(lat)
        cell_lons.setdefault(cell, []).append(lon)
    best = max(cell_lats, key=lambda c: len(cell_lats[c]))
    home_lat = sum(cell_lats[best]) / len(cell_lats[best])
    home_lon = sum(cell_lons[best]) / len(cell_lons[best])
    return home_lat, home_lon, len(cell_lats[best])


def load_and_filter(config: Config) -> tuple[pd.DataFrame, float, float, str]:
    """Load activities CSV, filter by type/date/home radius.

    Returns (filtered_runs, home_lat, home_lon, activities_dir).
    """
    activities_dir = config.resolved_activities_dir()
    print(f"Source:  {activities_dir}/")

    df = pd.read_csv(Path(activities_dir) / "activities.csv")
    df["Activity Date"] = pd.to_datetime(
        df["Activity Date"], format="mixed", dayfirst=True
    )

    runs = df[df["Activity Type"].isin(config.activity_types)].copy()
    print(f"Total matching activities in export: {len(runs)}")

    date_from = pd.Timestamp(config.date_from) if config.date_from else pd.Timestamp.min
    date_to = (
        pd.Timestamp(config.date_to) if config.date_to else pd.Timestamp(date.today())
    )
    runs = runs[runs["Activity Date"].between(date_from, date_to)].copy()
    print(f"After date filter ({date_from.date()} – {date_to.date()}): {len(runs)}")

    # Resolve GPS start points (cached per export folder)
    gps_cache_path = Path(activities_dir) / "_gps_cache.json"
    gps_cache = (
        json.loads(gps_cache_path.read_text()) if gps_cache_path.exists() else {}
    )

    rows = []
    for _, row in runs.iterrows():
        fn = str(row["Filename"])
        if fn in gps_cache:
            lat, lon, spread = gps_cache[fn]
        else:
            lat, lon, spread = _get_gps_start(Path(activities_dir) / fn)
            gps_cache[fn] = [lat, lon, spread]
        rows.append({**row, "start_lat": lat, "start_lon": lon, "gps_spread_m": spread})

    gps_cache_path.write_text(json.dumps(gps_cache))

    runs = pd.DataFrame(rows)
    runs = runs[
        runs["start_lat"].notna() & (runs["gps_spread_m"] >= config.gps_spread_min_m)
    ].copy()
    print(f"After removing no-GPS / indoor: {len(runs)}")

    if config.home_lat is None or config.home_lon is None:
        home_lat, home_lon, n_home = _detect_home(runs)
        print(
            f"Auto-detected home: {home_lat:.4f}, {home_lon:.4f}  "
            f"({n_home} of {len(runs)} activities started there)"
        )
    else:
        home_lat, home_lon = config.home_lat, config.home_lon
        print(f"Using manual home: {home_lat}, {home_lon}")

    runs["dist_from_home_km"] = runs.apply(
        lambda r: haversine_km(home_lat, home_lon, r["start_lat"], r["start_lon"]),
        axis=1,
    )
    runs = runs[runs["dist_from_home_km"] <= config.radius_km].copy()
    print(f"After home-radius filter (≤{config.radius_km} km): {len(runs)} activities")

    return runs, home_lat, home_lon, activities_dir
