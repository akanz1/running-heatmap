from __future__ import annotations

import json
import logging
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


def _is_pre_speed_xml(fn: str, pts: list[list]) -> bool:
    """True if an XML-format cache entry has no speed data on any point."""
    if not fn.lower().endswith(_XML_TRACK_EXTS):
        return False
    return all(p[2] is None for p in pts)


def _load_cache(cache_path: Path) -> dict:
    if not cache_path.exists():
        return {}
    cache = json.loads(cache_path.read_text())

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
    activities_dir: Path,
    cache_path: Path,
) -> list[tuple[str, list[list]]]:
    """Parse track files (FIT / GPX / TCX) with disk caching.

    Returns list of (label, [[lat, lon, speed, hr, alt], ...]).
    """
    cache_path.parent.mkdir(exist_ok=True)
    cache = _load_cache(cache_path)

    tracks: list[tuple[str, list[list]]] = []
    for _, row in tqdm(runs.iterrows(), total=len(runs), desc="Loading tracks", unit="run"):
        fn = str(row["Filename"])
        label = f"{row['Activity Date'].date()} {row['Activity Name']}"

        if fn not in cache:
            cache[fn] = parse_track(activities_dir / fn)

        if cache[fn]:
            tracks.append((label, cache[fn]))

    cache_path.write_text(json.dumps(cache))

    total_pts = sum(len(pts) for _, pts in tracks)
    log.info("Loaded %d tracks, %s GPS points", len(tracks), f"{total_pts:,}")
    return tracks
