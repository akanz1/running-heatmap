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


def _empty() -> pd.DataFrame:
    """Empty canonical frame with typed columns so concat with intervals.icu
    data preserves dtypes.
    """
    return pd.DataFrame(
        {
            "activity_id": pd.Series(dtype="object"),
            "strava_id": pd.Series(dtype="object"),
            "date": pd.Series(dtype="datetime64[ns]"),
            "type": pd.Series(dtype="object"),
            "name": pd.Series(dtype="object"),
            "distance_m": pd.Series(dtype="float64"),
            "moving_time_s": pd.Series(dtype="float64"),
            "elevation_gain_m": pd.Series(dtype="float64"),
            "file_path": pd.Series(dtype="object"),
            "start_lat": pd.Series(dtype="float64"),
            "start_lon": pd.Series(dtype="float64"),
            "gps_spread_m": pd.Series(dtype="float64"),
        }
    )


def load(strava_dir: Path, excluded_ids: list[str] | None = None) -> pd.DataFrame:
    """Load all Strava activities with a track file into the canonical schema.

    `excluded_ids` drops the matching activities (by Strava Activity ID, as a
    string) before any further processing. Use when an activity's GPS is
    broken in Strava but corrected on another source.

    Returns an empty (but correctly-typed) frame when `activities.csv` is
    absent — supports intervals.icu-only setups with no Strava export at all.
    """
    csv_path = strava_dir / "activities.csv"
    if not csv_path.exists():
        log.info("No Strava export at %s — skipping (intervals.icu-only mode)", strava_dir)
        return _empty()
    raw = pd.read_csv(csv_path)
    raw = normalize(raw)
    raw["Activity Date"] = pd.to_datetime(raw["Activity Date"], format="mixed", dayfirst=True)
    raw = raw[raw["Filename"].notna()].copy()  # drop indoor / manual entries
    if excluded_ids:
        before = len(raw)
        raw = raw[~raw["Activity ID"].astype(str).isin(excluded_ids)].copy()
        log.info("Strava: excluded %d activities by id (%d → %d)", before - len(raw), before, len(raw))

    out = pd.DataFrame()
    out["strava_id"] = raw["Activity ID"].astype(str)
    out["activity_id"] = "strava-" + out["strava_id"]
    out["date"] = raw["Activity Date"]
    out["type"] = raw["Activity Type"]
    out["name"] = raw["Activity Name"].fillna("")
    out["distance_m"] = pd.to_numeric(raw.get("Distance"), errors="coerce")
    out["moving_time_s"] = pd.to_numeric(raw.get("Moving Time"), errors="coerce")
    out["elevation_gain_m"] = pd.to_numeric(raw.get("Elevation Gain"), errors="coerce")
    out["file_path"] = [strava_dir / fn for fn in raw["Filename"]]
    out["Filename"] = raw["Filename"].to_numpy()  # transient: used by GPS cache key

    out = _resolve_starts(out, strava_dir)
    out = out.drop(columns=["Filename"])
    log.info("Strava export: %d activities", len(out))
    return out[CANONICAL_COLS]
