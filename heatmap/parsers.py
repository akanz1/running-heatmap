"""Multi-format track parsers.

Strava exports a mix of formats depending on how old the activity is and what
device recorded it:

- `.fit.gz` (current Garmin / most modern devices) — binary FIT
- `.gpx.gz` / `.gpx` — XML, used by older Strava activities and many manual uploads
- `.tcx.gz` — XML, Garmin's older Training Center format

Each parser returns a list of [lat, lon, speed_ms, hr_bpm, alt_m] points where
fields the format doesn't provide are `None`.
"""

from __future__ import annotations

import gzip
import io
import logging
import xml.etree.ElementTree as ET
from pathlib import Path

import fitparse
import gpxpy

from heatmap.constants import SEMICIRCLE_TO_DEG

log = logging.getLogger(__name__)


TrackPoint = list  # [lat, lon, speed_ms, hr_bpm, alt_m]


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
    points: list[TrackPoint] = []
    with _open_maybe_gz(filepath) as f:
        gpx = gpxpy.parse(f)
    for track in gpx.tracks:
        for seg in track.segments:
            for pt in seg.points:
                hr = _gpx_hr(pt)
                # GPX has no native speed field for most exports; leave None.
                points.append([pt.latitude, pt.longitude, None, hr, pt.elevation])
    return points


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


def _parse_tcx(filepath: Path) -> list[TrackPoint]:
    # Some Strava-exported TCX files have leading whitespace before <?xml…?>,
    # which strict XML parsers reject. Read fully, lstrip, then parse.
    with _open_maybe_gz(filepath) as f:
        raw = f.read().lstrip()
    tree = ET.ElementTree(ET.fromstring(raw))  # noqa: S314
    points: list[TrackPoint] = []
    for trkpt in tree.iter():
        if _local(trkpt.tag) != "Trackpoint":
            continue
        lat = lon = alt = hr = None
        for child in trkpt:
            tag = _local(child.tag)
            if tag == "Position":
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
        points.append([lat, lon, None, hr, alt])
    return points


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
