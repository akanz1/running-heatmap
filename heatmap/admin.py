"""Activity admin backend.

Reads + writes `cache/heatmap_overrides.json` and exposes the operations the
admin UI calls into:

- `list_activities()` — every activity from strava_export + intervals.icu
  cache, with metadata + current exclude flag.
- `set_excluded(source, ids, excluded)` — bulk toggle.
- `reimport_intervals(activity_id)` — evict all caches for the activity,
  then re-download from the API for that day.

Used by `heatmap.admin_server` to handle UI clicks; can also be driven from
a REPL/script for one-off corrections.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd

from heatmap.localization import ACTIVITY_TYPE_ALIASES
from heatmap.localization import normalize
from heatmap.sources import intervals_icu

if TYPE_CHECKING:
    from heatmap.config import Config

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Overrides file (read/write)
# --------------------------------------------------------------------------- #


def _load_overrides(path: Path) -> dict:
    if not path.exists():
        return {"excluded_strava_ids": [], "excluded_intervals_ids": []}
    raw = json.loads(path.read_text())
    return {
        "excluded_strava_ids": list(raw.get("excluded_strava_ids", [])),
        "excluded_intervals_ids": list(raw.get("excluded_intervals_ids", [])),
    }


def _save_overrides(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True))


# --------------------------------------------------------------------------- #
# Activity listing
# --------------------------------------------------------------------------- #


def _strava_rows(strava_dir: Path) -> list[dict]:
    df = pd.read_csv(strava_dir / "activities.csv")
    df = normalize(df)
    df = df[df["Filename"].notna()].copy()
    df["date"] = pd.to_datetime(df["Activity Date"], format="mixed", dayfirst=True)
    rows = []
    for _, r in df.iterrows():
        fn = str(r["Filename"])
        rows.append(
            {
                "source": "strava",
                "id": str(r["Activity ID"]),
                "date": r["date"].isoformat(),
                "name": (str(r.get("Activity Name")) if pd.notna(r.get("Activity Name")) else ""),
                "type": str(r["Activity Type"]),
                "distance_m": _opt_float(r.get("Distance")),
                "moving_time_s": _opt_float(r.get("Moving Time")),
                "elevation_gain_m": _opt_float(r.get("Elevation Gain")),
                "has_file": (strava_dir / fn).exists(),
            }
        )
    return rows


def _intervals_rows(cache_dir: Path) -> list[dict]:
    index_path = cache_dir / "index.json"
    if not index_path.exists():
        return []
    idx = json.loads(index_path.read_text())
    rows = []
    for e in idx:
        if e.get("source") == "STRAVA":
            continue
        aid = e["id"]
        rows.append(
            {
                "source": "intervals",
                "id": aid,
                "date": e.get("start_date_local") or "",
                "name": e.get("name") or "",
                "type": ACTIVITY_TYPE_ALIASES.get(e.get("type") or "", e.get("type") or ""),
                "distance_m": _opt_float(e.get("distance")),
                "moving_time_s": _opt_float(e.get("moving_time")),
                "elevation_gain_m": _opt_float(e.get("total_elevation_gain")),
                "has_file": (cache_dir / "activities" / f"{aid}.fit").exists(),
            }
        )
    return rows


def _opt_float(v: object) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return None if f != f else f  # nan check


def list_activities(config: Config) -> list[dict]:
    """Return every activity across both sources with current exclude state.

    The `excluded` flag reflects the merged Config + overrides-file state.
    Toggling in the UI only writes the file, so a row excluded by Config but
    not the file stays excluded after an "include" click — rare and obvious.
    """
    excl_s = set(config.all_excluded_strava_ids())
    excl_i = set(config.all_excluded_intervals_ids())

    rows = _strava_rows(config.resolved_activities_dir())
    rows += _intervals_rows(config.resolved_intervals_icu_cache_dir())
    for r in rows:
        r["excluded"] = (
            r["id"] in excl_s if r["source"] == "strava" else r["id"] in excl_i
        )
    rows.sort(key=lambda r: r["date"], reverse=True)
    return rows


# --------------------------------------------------------------------------- #
# Mutations
# --------------------------------------------------------------------------- #


def set_excluded(config: Config, source: str, ids: list[str], excluded: bool) -> dict:
    """Add or remove activity ids from the per-source excluded set."""
    if source not in ("strava", "intervals"):
        msg = f"unknown source: {source}"
        raise ValueError(msg)
    key = "excluded_strava_ids" if source == "strava" else "excluded_intervals_ids"

    overrides = _load_overrides(config.overrides_path())
    current = set(overrides[key])
    if excluded:
        current.update(ids)
    else:
        current.difference_update(ids)
    overrides[key] = sorted(current)
    _save_overrides(config.overrides_path(), overrides)
    return overrides


# --------------------------------------------------------------------------- #
# Re-import (intervals only)
# --------------------------------------------------------------------------- #


def _evict_intervals(cache_dir: Path, activity_id: str) -> tuple[str | None, str | None]:
    """Remove an activity from the intervals.icu cache. Returns (date_iso, name)
    from the index entry before deletion so the caller can re-sync that date.
    """
    index_path = cache_dir / "index.json"
    if not index_path.exists():
        return None, None
    idx = json.loads(index_path.read_text())
    entry = next((e for e in idx if e["id"] == activity_id), None)
    date_iso = (entry or {}).get("start_date_local", "")[:10] or None
    name = (entry or {}).get("name")

    idx = [e for e in idx if e["id"] != activity_id]
    index_path.write_text(json.dumps(idx, indent=2))

    (cache_dir / "activities" / f"{activity_id}.fit").unlink(missing_ok=True)

    gps_path = cache_dir / "_gps_cache.json"
    if gps_path.exists():
        g = json.loads(gps_path.read_text())
        g.pop(f"icu-{activity_id}", None)
        gps_path.write_text(json.dumps(g))
    return date_iso, name


def _evict_track_cache(track_cache_path: Path, basename: str) -> None:
    if not track_cache_path.exists():
        return
    t = json.loads(track_cache_path.read_text())
    if t.pop(basename, None) is not None:
        track_cache_path.write_text(json.dumps(t))


def reimport_intervals(config: Config, activity_id: str) -> dict:
    """Evict all caches for an intervals activity and re-download the FIT.

    Requires INTERVALS_ICU_API_KEY + INTERVALS_ICU_ATHLETE_ID in env.
    """
    api_key = os.environ.get("INTERVALS_ICU_API_KEY")
    athlete_id = os.environ.get("INTERVALS_ICU_ATHLETE_ID")
    if not api_key or not athlete_id:
        return {"ok": False, "error": "INTERVALS_ICU_API_KEY/ATHLETE_ID unset"}

    cache_dir = config.resolved_intervals_icu_cache_dir()
    date_iso, name = _evict_intervals(cache_dir, activity_id)
    _evict_track_cache(config.track_cache_path(), f"{activity_id}.fit")

    # Sync the activity's day (±1 day to be safe).
    if date_iso:
        try:
            d = date.fromisoformat(date_iso)
            date_from = (d - timedelta(days=1)).isoformat()
            date_to = (d + timedelta(days=1)).isoformat()
        except ValueError:
            date_from, date_to = date_iso, date_iso
    else:
        date_from = date_to = date.today().isoformat()

    sync_result = intervals_icu.sync(
        cache_dir,
        athlete_id=athlete_id,
        api_key=api_key,
        date_from=date_from,
        date_to=date_to,
    )
    return {"ok": True, "downloaded": sync_result.downloaded, "name": name, "date": date_iso}
