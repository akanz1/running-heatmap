from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from heatmap.parsers import parse_track

if TYPE_CHECKING:
    from pathlib import Path

    import pandas as pd

log = logging.getLogger(__name__)

# Track points use 5 fields: [lat, lon, speed_ms, hr_bpm, alt_m]
TRACK_POINT_FIELDS = 5


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
    """Parse track files (FIT / GPX / TCX) with disk caching.

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
            cache[fn] = parse_track(activities_dir / fn)

        if cache[fn]:
            tracks.append((label, cache[fn]))

    cache_path.write_text(json.dumps(cache))

    total_pts = sum(len(pts) for _, pts in tracks)
    log.info("Loaded %d tracks, %s GPS points", len(tracks), f"{total_pts:,}")
    return tracks
