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
ROUTES_VERSION = 5
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


def cumulative_time_weighted_metric(
    points: list[list], field_index: int, *, require_positive: bool = False
) -> tuple[list[float], list[float]]:
    """Return cumulative value-seconds and measured seconds for a point field."""
    value_seconds = [0.0]
    measured_seconds = [0.0]
    for index in range(1, len(points)):
        start = points[index - 1]
        finish = points[index]
        start_elapsed = _finite_number(start[5]) if len(start) > 5 else None
        finish_elapsed = _finite_number(finish[5]) if len(finish) > 5 else None
        start_value = _finite_number(start[field_index]) if len(start) > field_index else None
        finish_value = _finite_number(finish[field_index]) if len(finish) > field_index else None
        if require_positive:
            start_value = start_value if start_value is not None and start_value > 0 else None
            finish_value = finish_value if finish_value is not None and finish_value > 0 else None
        duration = None if start_elapsed is None or finish_elapsed is None else finish_elapsed - start_elapsed
        value = (
            (start_value + finish_value) / 2
            if start_value is not None and finish_value is not None
            else start_value if start_value is not None else finish_value
        )
        if duration is not None and duration > 0 and value is not None:
            value_seconds.append(value_seconds[-1] + value * duration)
            measured_seconds.append(measured_seconds[-1] + duration)
        else:
            value_seconds.append(value_seconds[-1])
            measured_seconds.append(measured_seconds[-1])
    return value_seconds, measured_seconds


def cumulative_elevation(points: list[list], smoothing_window: int = 15) -> tuple[list, list[float], list[float]]:
    """Return smoothed elevation and cumulative gain/loss for every point."""
    if not points:
        return [], [], []
    altitude = np.asarray([p[4] if len(p) > 4 and p[4] is not None else np.nan for p in points], dtype=float)
    valid = np.isfinite(altitude)
    if not valid.any():
        return [None] * len(points), [0.0] * len(points), [0.0] * len(points)
    window = min(smoothing_window, len(points))
    kernel = np.ones(window)
    counts = np.convolve(valid.astype(float), kernel, mode="same")
    smoothed = np.divide(
        np.convolve(np.where(valid, altitude, 0), kernel, mode="same"),
        counts,
        out=np.full(len(points), np.nan),
        where=counts > 0,
    )
    gain = [0.0]
    loss = [0.0]
    for start, finish in zip(smoothed[:-1], smoothed[1:], strict=True):
        change = finish - start
        gain.append(gain[-1] + (change if math.isfinite(change) and change > 0 else 0))
        loss.append(loss[-1] + (-change if math.isfinite(change) and change < 0 else 0))
    return [_finite_number(value) for value in smoothed], gain, loss


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
        metric_series = {
            "heart_rate": cumulative_time_weighted_metric(track.points, 3, require_positive=True),
            "cadence": cumulative_time_weighted_metric(track.points, 6, require_positive=True),
            "power": cumulative_time_weighted_metric(track.points, 7, require_positive=True),
            "temperature": cumulative_time_weighted_metric(track.points, 8),
        }
        elevation, elevation_gain, elevation_loss = cumulative_elevation(track.points)
        points = [
            [round(float(track.points[i][0]), 6), round(float(track.points[i][1]), 6)]
            for i in indices
        ]
        elapsed_s = [_finite_number(track.points[i][5]) if len(track.points[i]) > 5 else None for i in indices]
        progress_m = [round(cumulative_distances[i], 1) for i in indices]
        raw_point_count += len(track.points)
        simplified_point_count += len(points)
        activity = {
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
        metric_fields = {
            "heart_rate": ("heart_rate_bpm_seconds", "heart_rate_duration_s"),
            "cadence": ("cadence_spm_seconds", "cadence_duration_s"),
            "power": ("power_watt_seconds", "power_duration_s"),
            "temperature": ("temperature_c_seconds", "temperature_duration_s"),
        }
        for metric, (values, durations) in metric_series.items():
            if durations[-1] <= 0:
                continue
            value_field, duration_field = metric_fields[metric]
            activity[value_field] = [round(values[i], 1) for i in indices]
            activity[duration_field] = [round(durations[i], 1) for i in indices]
        if any(value is not None for value in elevation):
            activity["elevation_m"] = [None if elevation[i] is None else round(elevation[i], 1) for i in indices]
            activity["elevation_gain_progress_m"] = [round(elevation_gain[i], 1) for i in indices]
            activity["elevation_loss_progress_m"] = [round(elevation_loss[i], 1) for i in indices]
        activities.append(activity)

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
