"""Multi-format track parsers.

Strava exports a mix of formats depending on how old the activity is and what
device recorded it:

- `.fit.gz` (current Garmin / most modern devices) — binary FIT
- `.gpx.gz` / `.gpx` — XML, used by older Strava activities and many manual uploads
- `.tcx.gz` — XML, Garmin's older Training Center format

Each parser returns a list of [lat, lon, speed_ms, hr_bpm, alt_m] points where
fields the format doesn't provide are `None`. For GPX/TCX (no native speed
field) speed is derived from timestamps + position deltas.
"""

from __future__ import annotations

import gzip
import io
import logging
import math
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import fitparse
import gpxpy

from heatmap.constants import EARTH_RADIUS_KM
from heatmap.constants import SEMICIRCLE_TO_DEG

log = logging.getLogger(__name__)


TrackPoint = list  # [lat, lon, speed_ms, hr_bpm, alt_m]

# Above this, treat as a GPS glitch (timestamps in GPX/TCX are 1s-resolution
# so a 30m jump = 30 m/s false reading).
MAX_DERIVED_SPEED_MS = 15.0  # ~54 km/h


# --------------------------------------------------------------------------- #
# Format detection
# --------------------------------------------------------------------------- #


def _format(filepath: Path) -> str:
    """Return canonical format key: 'fit' | 'gpx' | 'tcx' | 'unknown'."""
    suffixes = [s.lower() for s in filepath.suffixes]
    if ".fit" in suffixes:
        return "fit"
    if ".gpx" in suffixes:
        return "gpx"
    if ".tcx" in suffixes:
        return "tcx"
    return "unknown"


def _open_maybe_gz(filepath: Path) -> io.BufferedIOBase:
    """Open a file, transparently decompressing if it ends in .gz."""
    if filepath.suffix.lower() == ".gz":
        return gzip.open(filepath, "rb")
    return filepath.open("rb")


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    )
    return EARTH_RADIUS_KM * 1000 * 2 * math.asin(math.sqrt(a))


def _derive_speeds(
    coords: list[tuple[float, float]], times: list[datetime | None]
) -> list[float | None]:
    """Compute per-point speed in m/s from coords + timestamps.

    Speed at point i = distance(i, i+1) / time_delta. The last point reuses
    the previous speed. Returns None where timestamps are missing or invalid.
    """
    n = len(coords)
    speeds: list[float | None] = [None] * n
    for i in range(n - 1):
        t0, t1 = times[i], times[i + 1]
        if t0 is None or t1 is None:
            continue
        dt = (t1 - t0).total_seconds()
        if dt <= 0:
            continue
        dist = _haversine_m(coords[i][0], coords[i][1], coords[i + 1][0], coords[i + 1][1])
        v = dist / dt
        if v <= MAX_DERIVED_SPEED_MS:
            speeds[i] = v
    if n >= 2 and speeds[-2] is not None:
        speeds[-1] = speeds[-2]
    return speeds


# --------------------------------------------------------------------------- #
# FIT
# --------------------------------------------------------------------------- #


def _parse_fit(filepath: Path) -> list[TrackPoint]:
    points: list[TrackPoint] = []
    with _open_maybe_gz(filepath) as f:
        for msg in fitparse.FitFile(f).get_messages("record"):
            d = {x.name: x.value for x in msg}
            if d.get("position_lat") is None or d.get("position_long") is None:
                continue
            lat = d["position_lat"] * SEMICIRCLE_TO_DEG
            lon = d["position_long"] * SEMICIRCLE_TO_DEG
            speed = d.get("enhanced_speed") if d.get("enhanced_speed") is not None else d.get("speed")
            hr = d.get("heart_rate")
            alt = d.get("enhanced_altitude") if d.get("enhanced_altitude") is not None else d.get("altitude")
            points.append([lat, lon, speed, hr, alt])
    return points


# --------------------------------------------------------------------------- #
# GPX
# --------------------------------------------------------------------------- #


def _parse_gpx(filepath: Path) -> list[TrackPoint]:
    with _open_maybe_gz(filepath) as f:
        gpx = gpxpy.parse(f)

    coords: list[tuple[float, float]] = []
    times: list[datetime | None] = []
    extras: list[tuple[float | None, float | None]] = []  # (hr, alt)
    for track in gpx.tracks:
        for seg in track.segments:
            for pt in seg.points:
                coords.append((pt.latitude, pt.longitude))
                times.append(pt.time)
                extras.append((_gpx_hr(pt), pt.elevation))

    speeds = _derive_speeds(coords, times)
    return [
        [coords[i][0], coords[i][1], speeds[i], extras[i][0], extras[i][1]]
        for i in range(len(coords))
    ]


def _gpx_hr(pt) -> float | None:
    """Pull heart rate out of Garmin TrackPointExtension if present."""
    for ext in pt.extensions or []:
        for child in ext.iter():
            tag = child.tag.rsplit("}", 1)[-1]
            if tag == "hr" and child.text:
                try:
                    return float(child.text)
                except ValueError:
                    return None
    return None


# --------------------------------------------------------------------------- #
# TCX
# --------------------------------------------------------------------------- #


# Strip namespace prefixes since they vary by device vendor
def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _parse_iso_time(text: str) -> datetime | None:
    """Parse an ISO 8601 timestamp; tolerates trailing 'Z'."""
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_tcx(filepath: Path) -> list[TrackPoint]:
    # Some Strava-exported TCX files have leading whitespace before <?xml…?>,
    # which strict XML parsers reject. Read fully, lstrip, then parse.
    with _open_maybe_gz(filepath) as f:
        raw = f.read().lstrip()
    tree = ET.ElementTree(ET.fromstring(raw))  # noqa: S314

    coords: list[tuple[float, float]] = []
    times: list[datetime | None] = []
    extras: list[tuple[float | None, float | None]] = []  # (hr, alt)
    for trkpt in tree.iter():
        if _local(trkpt.tag) != "Trackpoint":
            continue
        lat = lon = alt = hr = None
        t: datetime | None = None
        for child in trkpt:
            tag = _local(child.tag)
            if tag == "Time" and child.text:
                t = _parse_iso_time(child.text)
            elif tag == "Position":
                for sub in child:
                    if _local(sub.tag) == "LatitudeDegrees" and sub.text:
                        lat = float(sub.text)
                    elif _local(sub.tag) == "LongitudeDegrees" and sub.text:
                        lon = float(sub.text)
            elif tag == "AltitudeMeters" and child.text:
                alt = float(child.text)
            elif tag == "HeartRateBpm":
                for sub in child:
                    if _local(sub.tag) == "Value" and sub.text:
                        hr = float(sub.text)
        if lat is None or lon is None:
            continue
        coords.append((lat, lon))
        times.append(t)
        extras.append((hr, alt))

    speeds = _derive_speeds(coords, times)
    return [
        [coords[i][0], coords[i][1], speeds[i], extras[i][0], extras[i][1]]
        for i in range(len(coords))
    ]


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def parse_track(filepath: Path) -> list[TrackPoint]:
    """Parse a track file in any supported format. Returns [] on failure."""
    fmt = _format(filepath)
    parsers = {"fit": _parse_fit, "gpx": _parse_gpx, "tcx": _parse_tcx}
    parser = parsers.get(fmt)
    if parser is None:
        log.warning("Unknown track format: %s", filepath)
        return []
    try:
        return parser(filepath)
    except Exception as e:  # noqa: BLE001
        log.warning("Failed to parse %s: %s", filepath, e)
        return []
