from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

from tqdm import tqdm

from heatmap.parsers import parse_track

if TYPE_CHECKING:
    from pathlib import Path

    import pandas as pd

log = logging.getLogger(__name__)

# Track points use 5 fields: [lat, lon, speed_ms, hr_bpm, alt_m]
TRACK_POINT_FIELDS = 5

# Extensions that used to be cached without speed (pre-derived-speed upgrade).
# These get cleared so they re-parse with timestamps → speed.
_XML_TRACK_EXTS = (".gpx", ".gpx.gz", ".tcx", ".tcx.gz")

_EPOCH = date(1970, 1, 1)


@dataclass
class Track:
    label: str
    date_days: int  # days since 1970-01-01, for per-pixel max-date tiles
    distance_m: float | None
    moving_time_s: float | None
    elevation_gain_m: float | None
    points: list[list]


def _is_pre_speed_xml(fn: str, pts: list[list]) -> bool:
    """True if an XML-format cache entry has no speed data on any point."""
    if not fn.lower().endswith(_XML_TRACK_EXTS):
        return False
    return all(p[2] is None for p in pts)


def _migrate_cache_keys(cache: dict) -> dict:
    """Rename legacy keys like 'activities/<id>.gpx.gz' → '<id>.gpx.gz'.

    Cache is now keyed by file basename so it works across multiple sources
    without storing absolute paths.
    """
    renamed = {}
    for k, v in cache.items():
        new_k = k.split("/")[-1] if "/" in k else k
        renamed[new_k] = v
    return renamed


def _load_cache(cache_path: Path) -> dict:
    if not cache_path.exists():
        return {}
    cache = json.loads(cache_path.read_text())
    cache = _migrate_cache_keys(cache)

    stale_fields = [k for k, v in cache.items() if v and len(v[0]) < TRACK_POINT_FIELDS]
    if stale_fields:
        log.info("Clearing %d stale cache entries (missing altitude field)", len(stale_fields))
        for k in stale_fields:
            del cache[k]

    stale_no_speed = [k for k, v in cache.items() if v and _is_pre_speed_xml(k, v)]
    if stale_no_speed:
        log.info("Clearing %d GPX/TCX cache entries to recompute speeds", len(stale_no_speed))
        for k in stale_no_speed:
            del cache[k]

    return cache


def load_tracks(
    runs: pd.DataFrame,
    cache_path: Path,
) -> list[Track]:
    """Parse track files (FIT / GPX / TCX) with disk caching.

    Reads each row's absolute `file_path`. Cache is keyed by file basename
    so the same cache works for strava_export + intervals.icu sources.

    Returns Track objects carrying activity metadata alongside the points.
    """
    cache_path.parent.mkdir(exist_ok=True)
    cache = _load_cache(cache_path)

    tracks: list[Track] = []
    for _, row in tqdm(runs.iterrows(), total=len(runs), desc="Loading tracks", unit="run"):
        fp = row["file_path"]
        key = fp.name

        if key not in cache:
            cache[key] = parse_track(fp)

        if not cache[key]:
            continue

        tracks.append(
            Track(
                label=f"{row['date'].date()} {row['name']}",
                date_days=(row["date"].date() - _EPOCH).days,
                distance_m=row.get("distance_m"),
                moving_time_s=row.get("moving_time_s"),
                elevation_gain_m=row.get("elevation_gain_m"),
                points=cache[key],
            )
        )

    cache_path.write_text(json.dumps(cache))

    total_pts = sum(len(t.points) for t in tracks)
    log.info("Loaded %d tracks, %s GPS points", len(tracks), f"{total_pts:,}")
    return tracks
