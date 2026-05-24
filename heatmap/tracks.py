from __future__ import annotations

import gzip
import json
from pathlib import Path

import fitparse
import pandas as pd

from heatmap.constants import SEMICIRCLE_TO_DEG


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
                speed = (
                    d.get("enhanced_speed")
                    if d.get("enhanced_speed") is not None
                    else d.get("speed")
                )
                hr = d.get("heart_rate")
                alt = (
                    d.get("enhanced_altitude")
                    if d.get("enhanced_altitude") is not None
                    else d.get("altitude")
                )
                points.append([lat, lon, speed, hr, alt])
    except Exception as e:
        print(f"  Warning {filepath}: {e}")
    return points


def load_tracks(
    runs: pd.DataFrame,
    activities_dir: str,
    cache_path: str,
) -> list[tuple[str, list[list]]]:
    """Parse FIT files with disk caching.

    Returns list of (label, [[lat, lon, speed, hr, alt], ...]).
    """
    cache_file = Path(cache_path)
    cache_file.parent.mkdir(exist_ok=True)
    cache: dict = json.loads(cache_file.read_text()) if cache_file.exists() else {}

    # Invalidate entries missing the altitude field (old 4-field format)
    stale = [k for k, v in cache.items() if v and len(v[0]) < 5]
    if stale:
        print(f"  Clearing {len(stale)} stale cache entries (missing altitude field)")
        for k in stale:
            del cache[k]

    tracks = []
    for _, row in runs.iterrows():
        fn = str(row["Filename"])
        label = f"{row['Activity Date'].date()} {row['Activity Name']}"

        if fn in cache:
            pts = cache[fn]
        else:
            print(f"  Parsing {fn} …")
            pts = _load_fit_track(Path(activities_dir) / fn)
            cache[fn] = pts

        if pts:
            tracks.append((label, pts))

    cache_file.write_text(json.dumps(cache))

    total_pts = sum(len(pts) for _, pts in tracks)
    print(f"\nLoaded {len(tracks)} tracks, {total_pts:,} GPS points")
    return tracks
