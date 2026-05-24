from __future__ import annotations

import gzip
import json
import logging
from typing import TYPE_CHECKING

import fitparse

from heatmap.constants import SEMICIRCLE_TO_DEG

if TYPE_CHECKING:
    from pathlib import Path

    import pandas as pd

log = logging.getLogger(__name__)

# Track points use 5 fields: [lat, lon, speed_ms, hr_bpm, alt_m]
TRACK_POINT_FIELDS = 5


def _load_fit_track(filepath: Path) -> list[list]:
    """Parse a .fit.gz and return [[lat, lon, speed_ms, hr_bpm, alt_m], ...].

    speed_ms, hr_bpm, alt_m are None where the sensor had no reading.
    """
    points = []
    try:
        with gzip.open(filepath, "rb") as f:
            for msg in fitparse.FitFile(f).get_messages("record"):
                d = {x.name: x.value for x in msg}
                # Use `is not None` not truthiness — lat/lon of 0° are valid coordinates
                if d.get("position_lat") is None or d.get("position_long") is None:
                    continue
                lat = d["position_lat"] * SEMICIRCLE_TO_DEG
                lon = d["position_long"] * SEMICIRCLE_TO_DEG
                # Use `is not None` not `or` — zero speed (stationary) is valid
                speed = d.get("enhanced_speed") if d.get("enhanced_speed") is not None else d.get("speed")
                hr = d.get("heart_rate")
                alt = d.get("enhanced_altitude") if d.get("enhanced_altitude") is not None else d.get("altitude")
                points.append([lat, lon, speed, hr, alt])
    except Exception as e:  # noqa: BLE001
        log.warning("Failed to parse %s: %s", filepath, e)
    return points


def _load_cache(cache_path: Path) -> dict:
    if not cache_path.exists():
        return {}
    cache = json.loads(cache_path.read_text())
    stale = [k for k, v in cache.items() if v and len(v[0]) < TRACK_POINT_FIELDS]
    if stale:
        log.info("Clearing %d stale cache entries (missing altitude field)", len(stale))
        for k in stale:
            del cache[k]
    return cache


def load_tracks(
    runs: pd.DataFrame,
    activities_dir: Path,
    cache_path: Path,
) -> list[tuple[str, list[list]]]:
    """Parse FIT files with disk caching.

    Returns list of (label, [[lat, lon, speed, hr, alt], ...]).
    """
    cache_path.parent.mkdir(exist_ok=True)
    cache = _load_cache(cache_path)

    tracks: list[tuple[str, list[list]]] = []
    for _, row in runs.iterrows():
        fn = str(row["Filename"])
        label = f"{row['Activity Date'].date()} {row['Activity Name']}"

        if fn not in cache:
            log.info("Parsing %s …", fn)
            cache[fn] = _load_fit_track(activities_dir / fn)

        if cache[fn]:
            tracks.append((label, cache[fn]))

    cache_path.write_text(json.dumps(cache))

    total_pts = sum(len(pts) for _, pts in tracks)
    log.info("Loaded %d tracks, %s GPS points", len(tracks), f"{total_pts:,}")
    return tracks
