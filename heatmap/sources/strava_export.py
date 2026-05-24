"""Strava export source.

Reads `activities.csv` + track files from a user's Strava bulk export.
Returns the canonical DataFrame defined in `heatmap.sources`.
"""

from __future__ import annotations

import json
import logging
import math
from typing import TYPE_CHECKING

import pandas as pd
from tqdm import tqdm

from heatmap.localization import normalize
from heatmap.parsers import parse_track
from heatmap.sources import CANONICAL_COLS

if TYPE_CHECKING:
    from pathlib import Path

log = logging.getLogger(__name__)


def _gps_start(filepath: Path) -> tuple[float | None, float | None, float | None]:
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


def _resolve_starts(df: pd.DataFrame, strava_dir: Path) -> pd.DataFrame:
    """Augment df with start_lat / start_lon / gps_spread_m. Disk-cached."""
    cache_path = strava_dir / "_gps_cache.json"
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}

    starts: list[tuple[float | None, float | None, float | None]] = []
    for fn in tqdm(df["Filename"], total=len(df), desc="Strava GPS starts", unit="run"):
        cached = cache.get(fn)
        if cached is None or cached[0] is None:
            cache[fn] = list(_gps_start(strava_dir / fn))
        lat, lon, spread = cache[fn]
        starts.append((lat, lon, spread))

    cache_path.write_text(json.dumps(cache))
    df["start_lat"] = [s[0] for s in starts]
    df["start_lon"] = [s[1] for s in starts]
    df["gps_spread_m"] = [s[2] for s in starts]
    return df


def load(strava_dir: Path) -> pd.DataFrame:
    """Load all Strava activities with a track file into the canonical schema."""
    raw = pd.read_csv(strava_dir / "activities.csv")
    raw = normalize(raw)
    raw["Activity Date"] = pd.to_datetime(raw["Activity Date"], format="mixed", dayfirst=True)
    raw = raw[raw["Filename"].notna()].copy()  # drop indoor / manual entries

    out = pd.DataFrame()
    out["strava_id"] = raw["Activity ID"].astype(str)
    out["activity_id"] = "strava-" + out["strava_id"]
    out["date"] = raw["Activity Date"]
    out["type"] = raw["Activity Type"]
    out["name"] = raw["Activity Name"].fillna("")
    out["distance_m"] = pd.to_numeric(raw.get("Distance"), errors="coerce")
    out["moving_time_s"] = pd.to_numeric(raw.get("Moving Time"), errors="coerce")
    out["file_path"] = [strava_dir / fn for fn in raw["Filename"]]
    out["Filename"] = raw["Filename"].to_numpy()  # transient: used by GPS cache key

    out = _resolve_starts(out, strava_dir)
    out = out.drop(columns=["Filename"])
    log.info("Strava export: %d activities", len(out))
    return out[CANONICAL_COLS]
