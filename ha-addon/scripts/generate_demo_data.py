"""Generate a synthetic Strava-export-shaped dataset for screenshots / demos.

Produces ~150 activities in and around Munich. Most activities repeat one of
a handful of pre-generated "favourite" routes (with small jitter so they don't
overlap pixel-perfect), so the frequency layer shows the clear dominance of
favourites that any real runner's heatmap has. A smaller pool of one-off
random walks adds variety.

Elevation is sampled from a single shared spatial field f(lat, lon), so every
track passing through the same area sees the same hills. Hill / steepness /
elevation layers therefore aggregate cleanly across visits, the way they do
on real terrain.

Each activity gets:
- a GPX track with lat / lon / elevation / heart-rate / timestamps
- a matching row in `activities.csv` with id, date, type, distance, time, ascent

Usage:

    uv run python scripts/generate_demo_data.py --out strava_export_demo

Then in main.py:

    Config(activities_dir="strava_export_demo")
"""

from __future__ import annotations

import argparse
import math
import random
from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from pathlib import Path

# --------------------------------------------------------------------------- #
# Location
# --------------------------------------------------------------------------- #

MUNICH_CENTER = (48.1351, 11.5820)
ELEV_BASE_M = 520.0
ELEV_AMP_M = 60.0  # gentle Munich-area topography

EARTH_R_M = 6_371_000.0
GPX_NS = "http://www.topografix.com/GPX/1/1"
TPX_NS = "http://www.garmin.com/xmlschemas/TrackPointExtension/v1"

# Activity-type profile: (speed_min_ms, speed_max_ms, dist_min_m, dist_max_m,
# base_hr, climb_hr_boost)
TYPE_PROFILE: dict[str, tuple[float, float, int, int, int, int]] = {
    "Run": (2.7, 4.0, 5000, 14000, 148, 18),
    "Trail Run": (2.2, 3.4, 7000, 22000, 152, 22),
    "Hike": (0.9, 1.5, 6000, 18000, 118, 14),
    "Ride": (5.5, 9.5, 18000, 70000, 132, 16),
}

# Favourite-route mix: (visit_count, activity_types_allowed, weights)
ROUTE_PLAN: list[tuple[int, list[str], list[float]]] = [
    (38, ["Run"], [1.0]),  # English Garden loop
    (28, ["Run"], [1.0]),  # Olympiapark / Isar
    (22, ["Run", "Trail Run"], [0.6, 0.4]),  # Riverside trail
    (16, ["Run"], [1.0]),  # Short weeknight loop
    (12, ["Trail Run", "Hike"], [0.5, 0.5]),  # Forest trail
    (10, ["Hike"], [1.0]),  # Long weekend hike
    (8, ["Ride"], [1.0]),  # Bike commute
    (6, ["Ride", "Run"], [0.7, 0.3]),  # Long ride / occasional long run
]
ONE_OFF_COUNT = 18  # fresh random walks
ONE_OFF_TYPES = ["Run", "Trail Run", "Hike", "Ride"]
ONE_OFF_WEIGHTS = [0.55, 0.2, 0.15, 0.1]


# --------------------------------------------------------------------------- #
# Shared elevation field. Deterministic f(lat, lon) → metres.
# Sum of a few low-frequency sines so any two tracks crossing the same point
# see the same altitude; the hill layer thus accumulates consistently.
# --------------------------------------------------------------------------- #


def elevation_at(lat: float, lon: float) -> float:
    dlat = lat - MUNICH_CENTER[0]
    dlon = lon - MUNICH_CENTER[1]
    a = ELEV_AMP_M * math.sin(dlat * 130.0) * math.cos(dlon * 95.0)
    b = 0.6 * ELEV_AMP_M * math.sin((dlat + dlon) * 210.0)
    c = 0.4 * ELEV_AMP_M * math.cos(dlat * 380.0 + dlon * 240.0)
    return ELEV_BASE_M + a + b + c


# --------------------------------------------------------------------------- #
# Geometry helpers
# --------------------------------------------------------------------------- #


@dataclass
class TrackPoint:
    lat: float
    lon: float
    ele: float
    hr: int
    t: datetime


def _meters_to_deg(lat: float, dx_m: float, dy_m: float) -> tuple[float, float]:
    dlat = dy_m / 111_320.0
    dlon = dx_m / (111_320.0 * math.cos(math.radians(lat)))
    return dlat, dlon


def _haversine_m(lat1, lon1, lat2, lon2) -> float:
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return EARTH_R_M * 2 * math.asin(math.sqrt(a))


# --------------------------------------------------------------------------- #
# Route templates: a list of (lat, lon) waypoints. Activities follow a route
# by walking from one waypoint to the next with small per-step noise. Sampling
# rate is 1 Hz, so points-per-segment depends on activity speed.
# --------------------------------------------------------------------------- #


def _build_route(rng: random.Random, n_waypoints: int, span_m: float) -> list[tuple[float, float]]:
    """Random-walk a chain of waypoints starting at a jittered Munich centre.
    `span_m` controls the avg distance between consecutive waypoints.
    """
    lat, lon = MUNICH_CENTER
    # Anchor near city centre, jittered a few km.
    dlat0, dlon0 = _meters_to_deg(lat, rng.uniform(-4000, 4000), rng.uniform(-4000, 4000))
    lat += dlat0
    lon += dlon0
    waypoints = [(lat, lon)]
    heading = rng.uniform(0, 2 * math.pi)
    for _ in range(n_waypoints - 1):
        heading += rng.gauss(0, 0.6)
        d = span_m * rng.uniform(0.6, 1.4)
        dx = d * math.sin(heading)
        dy = d * math.cos(heading)
        dlat, dlon = _meters_to_deg(lat, dx, dy)
        lat += dlat
        lon += dlon
        waypoints.append((lat, lon))
    return waypoints


def _trace_route(
    rng: random.Random,
    waypoints: list[tuple[float, float]],
    speed_ms: float,
    jitter_m: float,
    base_hr: int,
    climb_boost: int,
    start_time: datetime,
) -> list[TrackPoint]:
    """Walk waypoint to waypoint at 1 Hz, with small per-step perpendicular
    jitter so repeated visits don't lay down pixel-identical lines.
    """
    pts: list[TrackPoint] = []
    t = start_time
    lat, lon = waypoints[0]
    prev_ele = elevation_at(lat, lon)

    for i in range(len(waypoints) - 1):
        wp_next = waypoints[i + 1]
        # Heading toward next waypoint, recomputed each step so we always make
        # progress despite jitter.
        while True:
            d = _haversine_m(lat, lon, wp_next[0], wp_next[1])
            if d < speed_ms * 1.2:
                break  # close enough; move on
            # Bearing.
            phi1 = math.radians(lat)
            phi2 = math.radians(wp_next[0])
            dlon = math.radians(wp_next[1] - lon)
            y = math.sin(dlon) * math.cos(phi2)
            x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlon)
            heading = math.atan2(y, x)
            # Per-step perpendicular jitter (~jitter_m).
            heading += rng.gauss(0, jitter_m / max(d, 1) * 0.4)
            step = speed_ms * (1.0 + rng.gauss(0, 0.04))
            dx = step * math.sin(heading)
            dy = step * math.cos(heading)
            dlat_step, dlon_step = _meters_to_deg(lat, dx, dy)
            # Lateral jitter, in metres, orthogonal to heading.
            lat_n = lat + dlat_step
            lon_n = lon + dlon_step
            if jitter_m > 0:
                lj = rng.gauss(0, jitter_m)
                jdx = lj * math.cos(heading)
                jdy = -lj * math.sin(heading)
                jdlat, jdlon = _meters_to_deg(lat_n, jdx, jdy)
                lat_n += jdlat
                lon_n += jdlon
            ele = elevation_at(lat_n, lon_n) + rng.gauss(0, 0.8)  # GPS altitude noise
            grade = (ele - prev_ele) / max(step, 0.1)
            hr = base_hr + int(climb_boost * max(grade, 0) * 25) + rng.randint(-4, 4)
            hr = max(95, min(185, hr))
            pts.append(TrackPoint(lat_n, lon_n, ele, hr, t))
            lat, lon = lat_n, lon_n
            prev_ele = ele
            t += timedelta(seconds=1)
    return pts


# --------------------------------------------------------------------------- #
# GPX writer
# --------------------------------------------------------------------------- #


def _write_gpx(path: Path, name: str, pts: list[TrackPoint]) -> None:
    lines: list[str] = []
    lines.append('<?xml version="1.0" encoding="UTF-8"?>')
    lines.append(f'<gpx version="1.1" creator="generate_demo_data" xmlns="{GPX_NS}" xmlns:gpxtpx="{TPX_NS}">')
    lines.append("<trk>")
    lines.append(f"<name>{name}</name>")
    lines.append("<trkseg>")
    for p in pts:
        lines.append(f'<trkpt lat="{p.lat:.6f}" lon="{p.lon:.6f}">')
        lines.append(f"<ele>{p.ele:.1f}</ele>")
        lines.append(f"<time>{p.t.strftime('%Y-%m-%dT%H:%M:%SZ')}</time>")
        lines.append("<extensions><gpxtpx:TrackPointExtension>")
        lines.append(f"<gpxtpx:hr>{p.hr}</gpxtpx:hr>")
        lines.append("</gpxtpx:TrackPointExtension></extensions>")
        lines.append("</trkpt>")
    lines.append("</trkseg></trk></gpx>")
    path.write_text("\n".join(lines))


def _track_stats(pts: list[TrackPoint]) -> tuple[float, float, float]:
    """(distance_m, moving_time_s, elev_gain_m)."""
    dist = 0.0
    gain = 0.0
    prev_ele = pts[0].ele
    for i in range(1, len(pts)):
        dist += _haversine_m(pts[i - 1].lat, pts[i - 1].lon, pts[i].lat, pts[i].lon)
        d = pts[i].ele - prev_ele
        if d > 0:
            gain += d
        prev_ele = pts[i].ele
    moving = (pts[-1].t - pts[0].t).total_seconds()
    return dist, moving, gain


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("strava_export_demo"))
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--years",
        type=float,
        default=3.0,
        help="Date span: most recent activity = today, oldest ≈ today - years",
    )
    args = parser.parse_args()

    rng = random.Random(args.seed)
    out = args.out
    activities_dir = out / "activities"
    activities_dir.mkdir(parents=True, exist_ok=True)

    end = datetime.now().replace(hour=8, minute=0, second=0, microsecond=0)
    start = end - timedelta(days=int(365 * args.years))

    # Pre-generate favourite routes. Distance roughly proportional to waypoint
    # count, so longer activity types get more waypoints.
    routes: list[tuple[list[tuple[float, float]], list[str], list[float]]] = []
    for _visits, types, weights in ROUTE_PLAN:
        # Pick route length from the longest allowed activity type, so the same
        # template can carry both runs and hikes without overshooting.
        max_dist = max(TYPE_PROFILE[t][3] for t in types)
        avg_dist = max_dist * 0.7
        n_wp = max(4, int(avg_dist / 1200))  # ~1.2 km between waypoints
        routes.append((_build_route(rng, n_wp, 1200.0), types, weights))

    csv_rows: list[str] = [
        "Activity ID,Activity Date,Activity Name,Activity Type,Distance,Moving Time,Elevation Gain,Filename"
    ]
    activity_id = 1_000_000_000

    def _emit(act_type: str, waypoints: list[tuple[float, float]], jitter_m: float, label: str) -> None:
        nonlocal activity_id
        speed_min, speed_max, _dmin, _dmax, base_hr, climb_boost = TYPE_PROFILE[act_type]
        speed = rng.uniform(speed_min, speed_max)
        ts = start + (end - start) * rng.random()
        ts = ts.replace(hour=rng.randint(6, 17), minute=rng.randint(0, 59), second=0)
        pts = _trace_route(rng, waypoints, speed, jitter_m, base_hr, climb_boost, ts)
        if len(pts) < 2:
            return
        dist_m, moving_s, gain_m = _track_stats(pts)
        filename = f"activities/{activity_id}.gpx"
        _write_gpx(out / filename, f"{label} {act_type}", pts)
        csv_rows.append(
            f"{activity_id},"
            f"{ts.strftime('%Y-%m-%d %H:%M:%S')},"
            f'"{label} {act_type}",'
            f"{act_type},"
            f"{dist_m:.1f},"
            f"{int(moving_s)},"
            f"{gain_m:.1f},"
            f"{filename}"
        )
        activity_id += 1

    # Favourite routes — repeated with small lateral jitter so the frequency
    # layer shows them as clear hot lines without becoming pixel-identical.
    for idx, ((plan_visits, types, weights), (wps, _t, _w)) in enumerate(zip(ROUTE_PLAN, routes, strict=False)):
        label = f"Route {idx + 1}"
        for _ in range(plan_visits):
            act_type = rng.choices(types, weights=weights, k=1)[0]
            _emit(act_type, wps, jitter_m=6.0, label=label)

    # One-off random walks for variety.
    for _ in range(ONE_OFF_COUNT):
        act_type = rng.choices(ONE_OFF_TYPES, weights=ONE_OFF_WEIGHTS, k=1)[0]
        n_wp = rng.randint(6, 14)
        wps = _build_route(rng, n_wp, 1000.0)
        _emit(act_type, wps, jitter_m=2.0, label="Exploration")

    (out / "activities.csv").write_text("\n".join(csv_rows) + "\n")
    n = len(csv_rows) - 1
    print(f"Wrote {n} activities to {out}/")
    print(f"  CSV: {out}/activities.csv")
    print(f"  GPX: {out}/activities/*.gpx")
    print()
    print("Point your heatmap at it:")
    print(f'    Config(activities_dir="{out}")')


if __name__ == "__main__":
    main()
