"""Compact browser route export for future interactive activity layers."""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime
from datetime import timezone
from typing import TYPE_CHECKING

import numpy as np

from heatmap.constants import EARTH_RADIUS_KM

if TYPE_CHECKING:
    from pathlib import Path

    from heatmap.tracks import Track

log = logging.getLogger(__name__)

ROUTES_FILENAME = "routes.json"
ROUTES_VERSION = 3
SIMPLIFY_TOLERANCE_M = 5.0


def simplify_route_indices(points: list[list], tolerance_m: float = SIMPLIFY_TOLERANCE_M) -> list[int]:
    """Return point indices kept by iterative Ramer-Douglas-Peucker."""
    coords = np.asarray([[p[0], p[1]] for p in points], dtype=np.float64)
    if len(coords) <= 2:  # noqa: PLR2004
        return list(range(len(coords)))

    mean_lat_rad = math.radians(float(coords[:, 0].mean()))
    earth_radius_m = EARTH_RADIUS_KM * 1000
    projected = np.column_stack(
        (
            np.radians(coords[:, 1]) * earth_radius_m * math.cos(mean_lat_rad),
            np.radians(coords[:, 0]) * earth_radius_m,
        )
    )

    keep = np.zeros(len(coords), dtype=bool)
    keep[0] = True
    keep[-1] = True
    stack = [(0, len(coords) - 1)]
    tolerance_sq = tolerance_m**2

    while stack:
        start, end = stack.pop()
        if end <= start + 1:
            continue
        segment = projected[end] - projected[start]
        candidates = projected[start + 1 : end]
        segment_sq = float(np.dot(segment, segment))
        if segment_sq == 0:
            distance_sq = np.sum((candidates - projected[start]) ** 2, axis=1)
        else:
            offsets = candidates - projected[start]
            fractions = np.clip((offsets @ segment) / segment_sq, 0, 1)
            nearest = projected[start] + fractions[:, None] * segment
            distance_sq = np.sum((candidates - nearest) ** 2, axis=1)

        relative_index = int(np.argmax(distance_sq))
        if float(distance_sq[relative_index]) <= tolerance_sq:
            continue
        index = start + 1 + relative_index
        keep[index] = True
        stack.append((start, index))
        stack.append((index, end))

    return np.flatnonzero(keep).tolist()


def simplify_route(points: list[list], tolerance_m: float = SIMPLIFY_TOLERANCE_M) -> list[list[float]]:
    """Simplify lat/lon geometry with iterative Ramer-Douglas-Peucker."""
    indices = simplify_route_indices(points, tolerance_m)
    return [[round(float(points[i][0]), 6), round(float(points[i][1]), 6)] for i in indices]


def cumulative_route_distances(points: list[list]) -> list[float]:
    """Return cumulative GPS path distance in meters for every point."""
    if not points:
        return []
    coords = np.radians(np.asarray([[p[0], p[1]] for p in points], dtype=np.float64))
    dlat = np.diff(coords[:, 0])
    dlon = np.diff(coords[:, 1])
    a = np.sin(dlat / 2) ** 2 + np.cos(coords[:-1, 0]) * np.cos(coords[1:, 0]) * np.sin(dlon / 2) ** 2
    distances = EARTH_RADIUS_KM * 2000 * np.arcsin(np.sqrt(np.clip(a, 0, 1)))
    return np.concatenate(([0.0], np.cumsum(distances))).tolist()


def _finite_number(value: float | None) -> float | None:
    if value is None or not math.isfinite(float(value)):
        return None
    return float(value)


def build_routes_payload(tracks: list[Track], input_fingerprint: str | None = None) -> dict:
    """Build JSON-serialisable activities with simplified route geometry."""
    activities = []
    raw_point_count = 0
    simplified_point_count = 0
    for track in tracks:
        indices = simplify_route_indices(track.points)
        cumulative_distances = cumulative_route_distances(track.points)
        points = [
            [round(float(track.points[i][0]), 6), round(float(track.points[i][1]), 6)]
            for i in indices
        ]
        elapsed_s = [_finite_number(track.points[i][5]) if len(track.points[i]) > 5 else None for i in indices]
        progress_m = [round(cumulative_distances[i], 1) for i in indices]
        raw_point_count += len(track.points)
        simplified_point_count += len(points)
        activities.append(
            {
                "id": track.activity_id,
                "type": track.activity_type,
                "label": track.label,
                "date_days": track.date_days,
                "distance_m": _finite_number(track.distance_m),
                "moving_time_s": _finite_number(track.moving_time_s),
                "elevation_gain_m": _finite_number(track.elevation_gain_m),
                "points": points,
                "elapsed_s": elapsed_s,
                "progress_m": progress_m,
            }
        )

    return {
        "version": ROUTES_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input_fingerprint": input_fingerprint,
        "simplify_tolerance_m": SIMPLIFY_TOLERANCE_M,
        "raw_point_count": raw_point_count,
        "point_count": simplified_point_count,
        "activities": activities,
    }


def save_routes(tracks: list[Track], output_dir: Path, input_fingerprint: str | None = None) -> Path:
    """Write compact route payload beside heatmap HTML."""
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = build_routes_payload(tracks, input_fingerprint)
    path = output_dir / ROUTES_FILENAME
    path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    log.info(
        "Saved routes: %s (%d activities, %s → %s points)",
        path,
        len(tracks),
        f"{payload['raw_point_count']:,}",
        f"{payload['point_count']:,}",
    )
    return path
