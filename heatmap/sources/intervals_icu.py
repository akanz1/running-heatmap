"""intervals.icu source.

Syncs the user's intervals.icu activities into a local cache and loads them
into the canonical DataFrame defined in `heatmap.sources`.

intervals.icu API quirks worth knowing:
- Strava-sourced activities appear in the listing with `source="STRAVA"` but
  cannot be downloaded (the FIT-file endpoint 404s). We skip them — the user
  already has the FIT/GPX in their strava_export.
- Auth is HTTP Basic with username literal `API_KEY` and password = the key.
- Native (Garmin/manual/etc.) activities have IDs starting with "i" and are
  downloadable as FIT via `/api/v1/activity/{id}/fit-file`.
"""

from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass
from datetime import date
from datetime import timedelta
from typing import Self
from typing import TYPE_CHECKING

import httpx
import pandas as pd
from tqdm import tqdm

from heatmap.localization import ACTIVITY_TYPE_ALIASES
from heatmap.parsers import parse_track
from heatmap.sources import CANONICAL_COLS

if TYPE_CHECKING:
    from pathlib import Path

log = logging.getLogger(__name__)

BASE_URL = "https://intervals.icu/api/v1"
LISTING_OLDEST_FALLBACK = "2010-01-01"
RETRY_STATUSES = {429, 500, 502, 503, 504}


@dataclass(frozen=True)
class SyncResult:
    downloaded: int
    activity_types: frozenset[str]


class IntervalsIcuClient:
    def __init__(self, athlete_id: str, api_key: str, timeout: float = 30.0) -> None:
        self.athlete_id = athlete_id
        self._client = httpx.Client(
            base_url=BASE_URL,
            auth=("API_KEY", api_key),
            timeout=timeout,
            headers={"Accept": "application/json"},
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self._client.close()

    def _get(self, path: str, **params: object) -> httpx.Response:
        """GET with exponential backoff on 429/5xx (3 attempts: 1s, 2s, 4s)."""
        for attempt in range(3):
            r = self._client.get(path, params=params or None)
            if r.status_code in RETRY_STATUSES and attempt < 2:
                wait = 2**attempt
                log.warning("intervals.icu %s → %d, retrying in %ds", path, r.status_code, wait)
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r
        msg = "unreachable"
        raise RuntimeError(msg)

    def list_activities(self, oldest: str, newest: str) -> list[dict]:
        r = self._get(
            f"/athlete/{self.athlete_id}/activities",
            oldest=oldest,
            newest=newest,
        )
        return r.json()

    def download_fit(self, activity_id: str) -> bytes:
        r = self._get(f"/activity/{activity_id}/fit-file")
        return r.content


def _read_index(cache_dir: Path) -> list[dict]:
    p = cache_dir / "index.json"
    if not p.exists():
        return []
    return json.loads(p.read_text())


def _write_index(cache_dir: Path, entries: list[dict]) -> None:
    (cache_dir / "index.json").write_text(json.dumps(entries, indent=2))


_INDEX_FIELDS = (
    "id",
    "type",
    "name",
    "start_date_local",
    "distance",
    "moving_time",
    "total_elevation_gain",
    "source",
)


def _activity_to_entry(a: dict) -> dict:
    return {k: a.get(k) for k in _INDEX_FIELDS}


def sync(
    cache_dir: Path,
    athlete_id: str,
    api_key: str,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
) -> SyncResult:
    """Sync new intervals.icu activities into cache_dir.

    Listing range defaults to (latest cached start_date - 7 days) → today, or
    `date_from` → `date_to` if given. STRAVA-sourced activities are skipped
    (API can't serve their files).

    Returns download count plus canonical activity types for newly downloaded
    activities.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    activities_dir = cache_dir / "activities"
    activities_dir.mkdir(exist_ok=True)

    index = _read_index(cache_dir)
    known_ids = {e["id"] for e in index}

    if date_from is None:
        if index:
            latest = max(e["start_date_local"][:10] for e in index)
            # 7-day overlap to catch late-syncing activities
            date_from = (date.fromisoformat(latest) - timedelta(days=7)).isoformat()
        else:
            date_from = LISTING_OLDEST_FALLBACK
    if date_to is None:
        date_to = date.today().isoformat()

    log.info("intervals.icu sync: %s → %s", date_from, date_to)
    with IntervalsIcuClient(athlete_id, api_key) as client:
        listing = client.list_activities(date_from, date_to)
        log.info("intervals.icu returned %d activities in range", len(listing))

        to_download = [a for a in listing if a.get("source") != "STRAVA" and a["id"] not in known_ids]
        if not to_download:
            log.info("intervals.icu: nothing new")
            return SyncResult(0, frozenset())

        n_new = 0
        new_types: set[str] = set()
        for a in tqdm(to_download, desc="intervals.icu sync", unit="act"):
            fit_path = activities_dir / f"{a['id']}.fit"
            if not fit_path.exists():
                fit_path.write_bytes(client.download_fit(a["id"]))
            index.append(_activity_to_entry(a))
            raw_type = a.get("type") or ""
            new_types.add(ACTIVITY_TYPE_ALIASES.get(raw_type, raw_type))
            n_new += 1

    _write_index(cache_dir, index)
    log.info("intervals.icu: downloaded %d new activities", n_new)
    return SyncResult(n_new, frozenset(new_types))


def load(cache_dir: Path, excluded_ids: list[str] | None = None) -> pd.DataFrame:
    """Read the local intervals.icu cache into the canonical DataFrame.

    Returns an empty (well-typed) DataFrame if the cache doesn't exist yet.
    `excluded_ids` drops rows by intervals activity id (`iXXXX...`).
    """
    if not (cache_dir / "index.json").exists():
        return pd.DataFrame(columns=CANONICAL_COLS)

    index = _read_index(cache_dir)
    # STRAVA-sourced rows should not be in the index (sync skips them) but
    # filter defensively in case an older cache contains them.
    index = [e for e in index if e.get("source") != "STRAVA"]
    if excluded_ids:
        excl = set(excluded_ids)
        before = len(index)
        index = [e for e in index if e["id"] not in excl]
        log.info("intervals.icu: excluded %d activities by id (%d → %d)",
                 before - len(index), before, len(index))

    activities_dir = cache_dir / "activities"
    rows = []
    for e in index:
        fit_path = activities_dir / f"{e['id']}.fit"
        if not fit_path.exists():
            log.warning("intervals.icu: %s in index but file missing, skipping", e["id"])
            continue
        rows.append(
            {
                "strava_id": None,
                "activity_id": f"icu-{e['id']}",
                "date": pd.to_datetime(e["start_date_local"]),
                "type": ACTIVITY_TYPE_ALIASES.get(e.get("type") or "", e.get("type") or ""),
                "name": e.get("name") or "",
                "distance_m": e.get("distance"),
                "moving_time_s": e.get("moving_time"),
                "elevation_gain_m": e.get("total_elevation_gain"),
                "file_path": fit_path,
                "start_lat": None,
                "start_lon": None,
                "gps_spread_m": None,
            }
        )

    if not rows:
        return pd.DataFrame(columns=CANONICAL_COLS)
    df = pd.DataFrame(rows)
    df = _resolve_starts(df, cache_dir)
    log.info("intervals.icu cache: %d activities", len(df))
    return df[CANONICAL_COLS]


def _resolve_starts(df: pd.DataFrame, cache_dir: Path) -> pd.DataFrame:
    """Disk-cached GPS start resolution keyed by activity_id."""
    cache_path = cache_dir / "_gps_cache.json"
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}

    starts: list[tuple[float | None, float | None, float | None]] = []
    for aid, fp in tqdm(
        zip(df["activity_id"], df["file_path"], strict=True),
        total=len(df),
        desc="intervals.icu GPS starts",
        unit="run",
    ):
        cached = cache.get(aid)
        if cached is None or cached[0] is None:
            points = parse_track(fp)
            if not points:
                cache[aid] = [None, None, None]
            else:
                lats = [p[0] for p in points]
                lons = [p[1] for p in points]
                mid_lat = (min(lats) + max(lats)) / 2
                spread_m = max(
                    (max(lats) - min(lats)) * 111_000,
                    (max(lons) - min(lons)) * 111_000 * math.cos(math.radians(mid_lat)),
                )
                cache[aid] = [lats[0], lons[0], spread_m]
        lat, lon, spread = cache[aid]
        starts.append((lat, lon, spread))

    cache_path.write_text(json.dumps(cache))
    df["start_lat"] = [s[0] for s in starts]
    df["start_lon"] = [s[1] for s in starts]
    df["gps_spread_m"] = [s[2] for s in starts]
    return df
