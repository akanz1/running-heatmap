"""Tile pyramid generation via sparse per-tile rendering.

Memory scales with the number of *occupied* tiles, not with the data's
bounding box. This is the only architecture that scales to worldwide-spread
data at z=14: a user with runs in Europe + USA + Asia might span 4 million
pixels horizontally at z=14, but only 1000-2000 tiles actually contain data.

Pipeline:
  1. Paint all tracks into a sparse dict[(tx, ty)] -> SparseTile at z=max.
  2. For each zoom from z=max down to z=min:
       a. Compute global stats (count_max, percentile ranges) across all
          occupied tiles at this zoom.
       b. For each tile: blur (with neighbor assembly), normalize, save PNG.
       c. Downsample (2x2 sum) to build the next-lower zoom's sparse dict.
       d. Drop the current zoom's data.

Tile coordinates follow OSM / Google convention:
  - NW origin
  - At zoom z, the world is 2^z x 2^z tiles of TILE_SIZE pixels each.
"""

from __future__ import annotations

import json
import logging
import math
import shutil
from collections import defaultdict
from dataclasses import asdict
from dataclasses import dataclass
from datetime import date as _date
from typing import TYPE_CHECKING

import numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter
from scipy.ndimage import maximum_filter
from scipy.ndimage import uniform_filter1d
from tqdm import tqdm

from heatmap.colormaps import CMAP_COUNT
from heatmap.colormaps import CMAP_ELEV
from heatmap.colormaps import CMAP_HILL
from heatmap.colormaps import CMAP_HR
from heatmap.colormaps import CMAP_RECENCY
from heatmap.colormaps import CMAP_SPEED
from heatmap.format import pace_min_per_km

if TYPE_CHECKING:
    from pathlib import Path

    import matplotlib.colors as mcolors

    from heatmap.config import Config
    from heatmap.tracks import Track

log = logging.getLogger(__name__)

TILE_SIZE = 256
MIN_SEGMENT_DIST_M = 0.5
PRESENCE_PCT = 10
RECENCY_WINDOW_DAYS_3MO = 90
RECENCY_WINDOW_DAYS_2W = 14
RECENCY_WINDOW_DAYS_12MO = 365
RECENCY_WINDOW_DAYS_36MO = 365 * 3

# Sum-semantic channels (downsample = sum, blur with Gaussian). Date_max has
# max semantics and is handled separately.
_CHANNELS = (
    "count",
    "speed_sum",
    "speed_n",
    "hr_sum",
    "hr_n",
    "grad_sum",
    "grad_n",
    "elev_sum",
    "elev_n",
    "elev_gain_sum",
    "recent_count_3mo",
    "recent_count",
    "recent_count_36mo",
)


# --------------------------------------------------------------------------- #
# Tile coordinate math
# --------------------------------------------------------------------------- #


def lonlat_to_global_px(lat: float, lon: float, z: int) -> tuple[float, float]:
    """Lat/lon → global pixel coordinates at zoom z (NW-origin)."""
    n = (2**z) * TILE_SIZE
    x = (lon + 180.0) / 360.0 * n
    lat_rad = math.radians(lat)
    y = (1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n
    return x, y


def lonlat_to_global_px_array(
    lats: np.ndarray,
    lons: np.ndarray,
    z: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Vectorised lat/lon → global pixel coordinates at zoom z."""
    n = (2**z) * TILE_SIZE
    x = (lons + 180.0) / 360.0 * n
    lat_rad = np.radians(lats)
    y = (1.0 - np.log(np.tan(lat_rad) + 1.0 / np.cos(lat_rad)) / math.pi) / 2.0 * n
    return x, y


def global_px_to_lonlat(x: float, y: float, z: int) -> tuple[float, float]:
    """Inverse of lonlat_to_global_px: pixel → lat/lon at zoom z."""
    n = (2**z) * TILE_SIZE
    lon = x / n * 360.0 - 180.0
    lat_rad = math.atan(math.sinh(math.pi * (1.0 - 2.0 * y / n)))
    lat = math.degrees(lat_rad)
    return lat, lon


# --------------------------------------------------------------------------- #
# Sparse tile storage
# --------------------------------------------------------------------------- #


@dataclass
class SparseTile:
    """A single tile's raw accumulator channels (just the 256x256 core).

    Sum-semantic channels listed in _CHANNELS combine via 2x2 sum on
    downsample. `date_max` is max-semantic (per-pixel: most recent activity
    that touched the pixel, in days-since-epoch; 0 = no visit).
    """

    count: np.ndarray
    speed_sum: np.ndarray
    speed_n: np.ndarray
    hr_sum: np.ndarray
    hr_n: np.ndarray
    grad_sum: np.ndarray
    grad_n: np.ndarray
    elev_sum: np.ndarray
    elev_n: np.ndarray
    elev_gain_sum: np.ndarray
    recent_count_3mo: np.ndarray
    recent_count: np.ndarray
    recent_count_36mo: np.ndarray
    date_max: np.ndarray

    @classmethod
    def empty(cls) -> SparseTile:
        def _z() -> np.ndarray:
            return np.zeros((TILE_SIZE, TILE_SIZE), dtype=np.float32)

        return cls(
            count=_z(),
            speed_sum=_z(),
            speed_n=_z(),
            hr_sum=_z(),
            hr_n=_z(),
            grad_sum=_z(),
            grad_n=_z(),
            elev_sum=_z(),
            elev_n=_z(),
            elev_gain_sum=_z(),
            recent_count_3mo=_z(),
            recent_count=_z(),
            recent_count_36mo=_z(),
            date_max=_z(),
        )


SparseTiles = dict[tuple[int, int], SparseTile]


# --------------------------------------------------------------------------- #
# Painting (z=max)
# --------------------------------------------------------------------------- #


def _segment_metrics(
    p0: list,
    p1: list,
    seg_dist_m: float,
) -> tuple[float | None, float | None, float | None, float | None]:
    """Return (speed, hr, abs_gradient, signed_elev_change) for a segment."""
    s0, s1 = p0[2], p1[2]
    h0, h1 = p0[3], p1[3]
    a0, a1 = p0[4], p1[4]

    seg_speed = (s0 + s1) / 2 if s0 is not None and s1 is not None else (s0 if s0 is not None else s1)
    seg_hr = (h0 + h1) / 2 if h0 is not None and h1 is not None else (h0 if h0 is not None else h1)

    if a0 is None or a1 is None or seg_dist_m < MIN_SEGMENT_DIST_M:
        return seg_speed, seg_hr, None, None

    return seg_speed, seg_hr, abs(a1 - a0) / seg_dist_m, a1 - a0


def _haversine_m(lat1: np.ndarray, lon1: np.ndarray, lat2: np.ndarray, lon2: np.ndarray) -> np.ndarray:
    earth_r_m = 6371000.0
    lat1_r, lat2_r = np.radians(lat1), np.radians(lat2)
    dlat = np.radians(lat2 - lat1)
    dlon = np.radians(lon2 - lon1)
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1_r) * np.cos(lat2_r) * np.sin(dlon / 2) ** 2
    return earth_r_m * 2 * np.arcsin(np.sqrt(a))


def _paint_point(  # noqa: PLR0913
    tiles: SparseTiles,
    gx: int,
    gy: int,
    date_days: int,
    in_recent_3: bool,
    in_recent_12: bool,
    in_recent_36: bool,
) -> None:
    tx, ty = gx // TILE_SIZE, gy // TILE_SIZE
    lx, ly = gx - tx * TILE_SIZE, gy - ty * TILE_SIZE
    key = (tx, ty)
    tile = tiles.get(key)
    if tile is None:
        tile = SparseTile.empty()
        tiles[key] = tile
    tile.count[ly, lx] += 1
    tile.date_max[ly, lx] = max(tile.date_max[ly, lx], date_days)
    if in_recent_3:
        tile.recent_count_3mo[ly, lx] += 1
    if in_recent_12:
        tile.recent_count[ly, lx] += 1
    if in_recent_36:
        tile.recent_count_36mo[ly, lx] += 1


def _paint_segment_sparse(  # noqa: PLR0913
    tiles: SparseTiles,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    speed: float | None,
    hr: float | None,
    grad: float | None,
    elev: float | None,
    date_days: int,
    in_recent_3: bool,
    in_recent_12: bool,
    in_recent_36: bool,
    hill_min_grade: float,
) -> None:
    dx, dy = x2 - x1, y2 - y1
    n_steps = max(int(max(abs(dx), abs(dy))) + 1, 1)
    # Only treat the segment as ascent if both the sign is positive AND the
    # grade exceeds a minimum — filters GPS altitude noise on flats.
    elev_pos = elev if (elev is not None and elev > 0 and grad is not None and grad >= hill_min_grade) else None
    for i in range(n_steps + 1):
        t = i / n_steps
        gx = round(x1 + t * dx)
        gy = round(y1 + t * dy)
        tx, ty = gx // TILE_SIZE, gy // TILE_SIZE
        lx, ly = gx - tx * TILE_SIZE, gy - ty * TILE_SIZE
        key = (tx, ty)
        tile = tiles.get(key)
        if tile is None:
            tile = SparseTile.empty()
            tiles[key] = tile
        if speed is not None:
            tile.speed_sum[ly, lx] += speed
            tile.speed_n[ly, lx] += 1
        if hr is not None:
            tile.hr_sum[ly, lx] += hr
            tile.hr_n[ly, lx] += 1
        if grad is not None:
            tile.grad_sum[ly, lx] += grad
            tile.grad_n[ly, lx] += 1
        if elev is not None:
            tile.elev_sum[ly, lx] += elev
            tile.elev_n[ly, lx] += 1
        if elev_pos is not None:
            tile.elev_gain_sum[ly, lx] += elev_pos
        tile.date_max[ly, lx] = max(tile.date_max[ly, lx], date_days)
        if in_recent_3:
            tile.recent_count_3mo[ly, lx] += 1
        if in_recent_12:
            tile.recent_count[ly, lx] += 1
        if in_recent_36:
            tile.recent_count_36mo[ly, lx] += 1


def _smooth_altitudes(pts: list[list], window: int) -> list[list]:
    """Return point copies with the altitude field replaced by a centered
    moving average over `window` points.

    GPS altitude has per-sample jitter (~0.5 m); raw a1-a0 deltas spike on
    flats. Smoothing pre-segment removes most of that noise. Points with
    no altitude (None) pass through; the window auto-shrinks at track ends.
    """
    if window <= 1 or len(pts) < window:
        return pts

    alts = np.array([(p[4] if p[4] is not None else np.nan) for p in pts], dtype=np.float32)
    valid = ~np.isnan(alts)
    if not valid.any():
        return pts

    filled = np.where(valid, alts, 0.0)
    # Sum over the window AND count of valid points, then divide → NaN-safe avg.
    sum_smooth = uniform_filter1d(filled, size=window, mode="nearest")
    cnt_smooth = uniform_filter1d(valid.astype(np.float32), size=window, mode="nearest")
    smoothed = np.where(cnt_smooth > 0, sum_smooth / np.maximum(cnt_smooth, 1e-6), np.nan)

    out = []
    for i, p in enumerate(pts):
        s = smoothed[i]
        new_alt = None if (np.isnan(s)) else float(s)
        out.append([p[0], p[1], p[2], p[3], new_alt])
    return out


def paint_tracks(  # noqa: PLR0913
    tracks: list[Track],
    zoom: int,
    recent_cutoff_3mo_days: int,
    recent_cutoff_12mo_days: int,
    recent_cutoff_36mo_days: int,
    hill_min_grade: float,
    altitude_smoothing_window: int = 1,
) -> SparseTiles:
    """Paint all tracks into sparse z=max tiles.

    Cutoffs gate the 3-, 12-, and 36-month freshness counters; a track
    crossing a shorter cutoff also crosses longer cutoffs.
    """
    tiles: SparseTiles = {}

    for t in tqdm(tracks, desc=f"Painting z={zoom}", unit="track"):
        pts = _smooth_altitudes(t.points, altitude_smoothing_window)
        if not pts:
            continue
        lats = np.array([p[0] for p in pts])
        lons = np.array([p[1] for p in pts])
        gxs, gys = lonlat_to_global_px_array(lats, lons, zoom)
        in_recent_3 = t.date_days >= recent_cutoff_3mo_days
        in_recent_12 = t.date_days >= recent_cutoff_12mo_days
        in_recent_36 = t.date_days >= recent_cutoff_36mo_days

        # Per-point: count + date_max + recent_count{,_36mo}.
        for i in range(len(pts)):
            _paint_point(tiles, round(gxs[i]), round(gys[i]), t.date_days, in_recent_3, in_recent_12, in_recent_36)

        if len(pts) < 2:  # noqa: PLR2004
            continue

        # Per-segment: speed/hr/grad/elev + elev_gain + date_max + freshness.
        seg_dists = _haversine_m(lats[:-1], lons[:-1], lats[1:], lons[1:])
        for i in range(len(pts) - 1):
            speed, hr, grad, elev = _segment_metrics(pts[i], pts[i + 1], float(seg_dists[i]))
            _paint_segment_sparse(
                tiles,
                gxs[i],
                gys[i],
                gxs[i + 1],
                gys[i + 1],
                speed,
                hr,
                grad,
                elev,
                t.date_days,
                in_recent_3,
                in_recent_12,
                in_recent_36,
                hill_min_grade,
            )

    return tiles


# --------------------------------------------------------------------------- #
# Blur with cross-tile neighbor assembly
# --------------------------------------------------------------------------- #


def _assemble_with_neighbors(
    tiles: SparseTiles,
    tx: int,
    ty: int,
    attr: str,
    margin: int,
) -> np.ndarray:
    """Build a (TILE_SIZE + 2*margin)² grid centred on tile (tx, ty)."""
    exp = TILE_SIZE + 2 * margin
    out = np.zeros((exp, exp), dtype=np.float32)
    for dtx in (-1, 0, 1):
        for dty in (-1, 0, 1):
            nb = tiles.get((tx + dtx, ty + dty))
            if nb is None:
                continue
            src = getattr(nb, attr)
            ax0 = margin + dtx * TILE_SIZE
            ay0 = margin + dty * TILE_SIZE
            ax_lo, ay_lo = max(0, ax0), max(0, ay0)
            ax_hi, ay_hi = min(exp, ax0 + TILE_SIZE), min(exp, ay0 + TILE_SIZE)
            sx_lo, sy_lo = ax_lo - ax0, ay_lo - ay0
            sx_hi, sy_hi = ax_hi - ax0, ay_hi - ay0
            out[ay_lo:ay_hi, ax_lo:ax_hi] = src[sy_lo:sy_hi, sx_lo:sx_hi]
    return out


def _blur_tile_channels(
    tiles: SparseTiles,
    tx: int,
    ty: int,
    attrs: tuple[str, ...],
    sigma: float,
    margin: int,
) -> dict[str, np.ndarray]:
    """Blur several same-(sigma, margin) channels through one shared neighbour
    assembly + a single Gaussian call.

    The 3x3 neighbour grid is the same for every channel, so assemble it once,
    stack the channels into a (C, exp, exp) array, and blur with
    ``sigma=(0, sigma, sigma)`` — the zero on the channel axis stops any blur leaking
    between channels. Bit-identical to blurring each channel separately, but
    skips C-1 redundant neighbour assemblies and Gaussian calls.

    Returns {attr: centre TILE_SIZE x TILE_SIZE blurred array}.
    """
    exp = TILE_SIZE + 2 * margin
    stack = np.zeros((len(attrs), exp, exp), dtype=np.float32)
    for dtx in (-1, 0, 1):
        for dty in (-1, 0, 1):
            nb = tiles.get((tx + dtx, ty + dty))
            if nb is None:
                continue
            ax0 = margin + dtx * TILE_SIZE
            ay0 = margin + dty * TILE_SIZE
            ax_lo, ay_lo = max(0, ax0), max(0, ay0)
            ax_hi, ay_hi = min(exp, ax0 + TILE_SIZE), min(exp, ay0 + TILE_SIZE)
            sx_lo, sy_lo = ax_lo - ax0, ay_lo - ay0
            sx_hi, sy_hi = ax_hi - ax0, ay_hi - ay0
            for i, attr in enumerate(attrs):
                stack[i, ay_lo:ay_hi, ax_lo:ax_hi] = getattr(nb, attr)[sy_lo:sy_hi, sx_lo:sx_hi]
    blurred = gaussian_filter(stack, sigma=(0, sigma, sigma))
    centre = blurred[:, margin : margin + TILE_SIZE, margin : margin + TILE_SIZE]
    return {attr: centre[i] for i, attr in enumerate(attrs)}


# Channel groups blurred together (one shared assembly per group).
_SIGMA_BLUR_ATTRS = (
    "count",
    "speed_sum",
    "speed_n",
    "hr_sum",
    "hr_n",
    "grad_sum",
    "grad_n",
    "elev_sum",
    "elev_n",
    "recent_count_3mo",
    "recent_count",
    "recent_count_36mo",
)
_HILL_BLUR_ATTRS = ("elev_gain_sum", "elev_n")
_STATS_BLUR_ATTRS = ("count", "recent_count_3mo", "recent_count", "recent_count_36mo")


def _max_filter_tile_attr(  # noqa: PLR0913
    tiles: SparseTiles,
    tx: int,
    ty: int,
    attr: str,
    size: int,
    margin: int,
) -> np.ndarray:
    """Max-filter this tile with neighbour context (for max-semantic fields).

    Spreads the per-pixel max across a small window so the layer matches the
    visual thickness of the Gaussian-blurred layers without averaging dates.
    """
    assembled = _assemble_with_neighbors(tiles, tx, ty, attr, margin)
    filtered = maximum_filter(assembled, size=size)
    return filtered[margin : margin + TILE_SIZE, margin : margin + TILE_SIZE]


# --------------------------------------------------------------------------- #
# Downsample to next zoom
# --------------------------------------------------------------------------- #


def _downsample_quadrant(arr: np.ndarray) -> np.ndarray:
    """256x256 → 128x128 by 2x2 sum."""
    return arr.reshape(128, 2, 128, 2).sum(axis=(1, 3))


def _downsample_quadrant_max(arr: np.ndarray) -> np.ndarray:
    """256x256 → 128x128 by 2x2 max (for max-semantic fields like date_max)."""
    return arr.reshape(128, 2, 128, 2).max(axis=(1, 3))


def downsample_tiles(child_tiles: SparseTiles) -> SparseTiles:
    """Build the next-lower-zoom sparse dict by combining 2x2 children into parents.

    Pops each child from `child_tiles` after use so it gets garbage-collected
    during the downsample rather than at function return. Cuts peak memory
    roughly in half at high zoom levels (matters at z=17+).
    """
    by_parent: dict[tuple[int, int], list[tuple[int, int]]] = defaultdict(list)
    for cx, cy in child_tiles:
        by_parent[(cx // 2, cy // 2)].append((cx, cy))

    parent_tiles: SparseTiles = {}
    for (px, py), kids in by_parent.items():
        parent = SparseTile.empty()
        for cx, cy in kids:
            child = child_tiles.pop((cx, cy))
            qdx, qdy = cx & 1, cy & 1
            qx_lo, qy_lo = qdx * 128, qdy * 128
            for attr in _CHANNELS:
                src = getattr(child, attr)
                ds = _downsample_quadrant(src)
                getattr(parent, attr)[qy_lo : qy_lo + 128, qx_lo : qx_lo + 128] = ds
            # date_max uses max semantics across the 2x2 quadrant.
            parent.date_max[qy_lo : qy_lo + 128, qx_lo : qx_lo + 128] = _downsample_quadrant_max(child.date_max)
            del child  # release the array refs now, not at function return
        parent_tiles[(px, py)] = parent
    return parent_tiles


# --------------------------------------------------------------------------- #
# Global stats (per zoom, from raw tile data)
# --------------------------------------------------------------------------- #


@dataclass
class ZoomStats:
    count_max: float
    speed_range: tuple[float, float]
    hr_range: tuple[float, float]
    grad_range: tuple[float, float]
    elev_abs_hi: float
    elev_gain_hi: float
    date_range: tuple[float, float]
    recent_count_3mo_max: float
    recent_count_max: float
    recent_count_36mo_max: float


def _compute_zoom_stats(  # noqa: PLR0913
    tiles: SparseTiles,
    auto_range_pct: int,
    sigma: float,
    margin: int,
    speed_min_ms: float | None,
    speed_max_ms: float | None,
    hr_min_bpm: float | None,
    hr_max_bpm: float | None,
) -> ZoomStats:
    """Approximate global ranges from raw (unblurred) tile data.

    Cheap: walks each tile once, never materialises a global array.
    The percentile distribution of raw means closely approximates the
    blurred one for our use case.
    """
    blurred_count_max = 0.0
    blurred_recent_count_3mo_max = 0.0
    blurred_recent_count_max = 0.0
    blurred_recent_count_36mo_max = 0.0
    date_min = math.inf
    date_max_val = -math.inf
    speed_means: list[np.ndarray] = []
    hr_means: list[np.ndarray] = []
    grad_means: list[np.ndarray] = []
    elev_means: list[np.ndarray] = []
    elev_gain_means: list[np.ndarray] = []

    # One pass: blur each tile's count to find the true post-blur max, and
    # collect raw per-pixel means for percentile ranges. Raw means are a
    # close approximation to blurred means at the percentile boundaries.
    for (tx, ty), tile in tiles.items():
        b = _blur_tile_channels(tiles, tx, ty, _STATS_BLUR_ATTRS, sigma, margin)
        blurred_count_max = max(blurred_count_max, float(b["count"].max()))
        blurred_recent_count_3mo_max = max(blurred_recent_count_3mo_max, float(b["recent_count_3mo"].max()))
        blurred_recent_count_max = max(blurred_recent_count_max, float(b["recent_count"].max()))
        blurred_recent_count_36mo_max = max(blurred_recent_count_36mo_max, float(b["recent_count_36mo"].max()))

        nonzero_dates = tile.date_max[tile.date_max > 0]
        if nonzero_dates.size:
            date_min = min(date_min, float(nonzero_dates.min()))
            date_max_val = max(date_max_val, float(nonzero_dates.max()))

        if (tile.speed_n > 0).any():
            mask = tile.speed_n > 0
            speed_means.append((tile.speed_sum[mask] / tile.speed_n[mask]).astype(np.float32))
        if (tile.hr_n > 0).any():
            mask = tile.hr_n > 0
            hr_means.append((tile.hr_sum[mask] / tile.hr_n[mask]).astype(np.float32))
        if (tile.grad_n > 0).any():
            mask = tile.grad_n > 0
            grad_means.append((tile.grad_sum[mask] / tile.grad_n[mask]).astype(np.float32))
        if (tile.elev_n > 0).any():
            mask = tile.elev_n > 0
            elev_means.append((tile.elev_sum[mask] / tile.elev_n[mask]).astype(np.float32))
            elev_gain_means.append((tile.elev_gain_sum[mask] / tile.elev_n[mask]).astype(np.float32))

    count_max = max(blurred_count_max, 1e-6)

    pct = auto_range_pct

    def _range(buckets: list[np.ndarray], lo: float | None, hi: float | None) -> tuple[float, float]:
        if not buckets:
            return 0.0, 1.0
        flat = np.concatenate(buckets)
        if flat.size == 0:
            return 0.0, 1.0
        auto_lo, auto_hi = float(np.percentile(flat, pct)), float(np.percentile(flat, 100 - pct))
        return (lo if lo is not None else auto_lo, hi if hi is not None else auto_hi)

    s_lo, s_hi = _range(speed_means, speed_min_ms, speed_max_ms)
    h_lo, h_hi = _range(hr_means, hr_min_bpm, hr_max_bpm)
    g_lo, g_hi = _range(grad_means, None, None)

    elev_abs_hi = 0.0
    if elev_means:
        flat = np.concatenate(elev_means)
        if flat.size:
            elev_abs_hi = max(
                abs(float(np.percentile(flat, pct))),
                abs(float(np.percentile(flat, 100 - pct))),
            )
        elev_abs_hi = max(elev_abs_hi, 1e-6)

    elev_gain_hi = 0.0
    if elev_gain_means:
        flat = np.concatenate(elev_gain_means)
        if flat.size:
            elev_gain_hi = float(np.percentile(flat, 100 - pct))
        elev_gain_hi = max(elev_gain_hi, 1e-6)

    if not math.isfinite(date_min):
        date_min, date_max_val = 0.0, 1.0

    return ZoomStats(
        count_max=count_max,
        speed_range=(s_lo, s_hi),
        hr_range=(h_lo, h_hi),
        grad_range=(g_lo, g_hi),
        elev_abs_hi=elev_abs_hi,
        elev_gain_hi=elev_gain_hi,
        date_range=(date_min, date_max_val),
        recent_count_3mo_max=max(blurred_recent_count_3mo_max, 1e-6),
        recent_count_max=max(blurred_recent_count_max, 1e-6),
        recent_count_36mo_max=max(blurred_recent_count_36mo_max, 1e-6),
    )


# --------------------------------------------------------------------------- #
# Per-tile RGBA assembly + save
# --------------------------------------------------------------------------- #


def _to_rgba_u8(norm: np.ndarray, cmap: mcolors.LinearSegmentedColormap, alpha: np.ndarray | None = None) -> np.ndarray:
    rgba = cmap(norm).copy()
    if alpha is not None:
        rgba[:, :, 3] = alpha
    return (rgba * 255).clip(0, 255).astype(np.uint8)


def _solid_color_alpha_u8(rgb: tuple[int, int, int], alpha: np.ndarray) -> np.ndarray:
    """Constant-RGB image with a per-pixel alpha channel."""
    h, w = alpha.shape
    arr = np.zeros((h, w, 4), dtype=np.uint8)
    arr[:, :, 0] = rgb[0]
    arr[:, :, 1] = rgb[1]
    arr[:, :, 2] = rgb[2]
    arr[:, :, 3] = (alpha * 255).clip(0, 255).astype(np.uint8)
    return arr


# Steepness layer colour: a deep forest green — saturated enough to pop on
# dark basemaps, dark enough to read on light basemaps.
_GRAD_COLOR_RGB = (20, 140, 60)


def _presence_alpha_for_tile(n_grid_blurred: np.ndarray, n_grid_raw: np.ndarray) -> np.ndarray:
    """Alpha = blurred presence normalised so the n=1 baseline is solid.

    Uses raw n_grid (this tile) to find a saturation point. For tile-local
    consistency; cross-tile differences are within ~5%.
    """
    binary_blurred = n_grid_blurred
    present = binary_blurred[n_grid_raw > 0]
    sat = float(np.percentile(present, PRESENCE_PCT)) if present.size else 0.0
    return np.clip(binary_blurred / sat, 0, 1) if sat > 0 else binary_blurred


def _save_tile_pngs(  # noqa: PLR0913
    tiles: SparseTiles,
    zoom: int,
    sigma: float,
    margin: int,
    stats: ZoomStats,
    output_root: Path,
    recency_gamma: float = 1.0,
    hill_sigma: float = 3.0,
) -> int:
    """Blur, normalize, colour-map, and save PNGs for every tile at this zoom."""
    s_lo, s_hi = stats.speed_range
    h_lo, h_hi = stats.hr_range
    g_lo, g_hi = stats.grad_range
    d_lo, d_hi = stats.date_range
    s_span = max(s_hi - s_lo, 1e-6)
    h_span = max(h_hi - h_lo, 1e-6)
    g_span = max(g_hi - g_lo, 1e-6)
    d_span = max(d_hi - d_lo, 1.0)
    # Spread date_max across a small window so the recency layer matches the
    # Gaussian-blurred layers' visual thickness. Size ≈ 2*margin + 1.
    date_filter_size = max(3, 2 * margin + 1)
    g_recency = max(recency_gamma, 0.1)
    hill_margin = max(margin, math.ceil(hill_sigma * 3))

    saved = 0
    iter_tiles = list(tiles.keys())
    for tx, ty in tqdm(iter_tiles, desc=f"Saving z={zoom}", unit="tile", leave=False):
        raw = tiles[(tx, ty)]

        # All same-sigma channels share one neighbour assembly + Gaussian call.
        b = _blur_tile_channels(tiles, tx, ty, _SIGMA_BLUR_ATTRS, sigma, margin)

        # Frequency layers
        b_count = b["count"]
        if b_count.max() == 0 and raw.count.max() == 0:
            continue
        cnorm = b_count / max(stats.count_max, 1e-9)
        cnorm_clip = np.clip(cnorm, 0, 1)
        top_routes_norm = cnorm_clip**0.75
        clog = np.log1p(b_count) / np.log1p(max(stats.count_max, 1e-9))
        clog = np.clip(clog, 0, 1)

        # Speed
        b_speed_s = b["speed_sum"]
        b_speed_n = b["speed_n"]
        speed_mean = np.where(b_speed_n > 0, b_speed_s / b_speed_n, 0)
        speed_norm = np.clip((speed_mean - s_lo) / s_span, 0, 1)
        speed_norm = np.where(b_speed_n > 0, speed_norm, 0)
        alpha_speed = _presence_alpha_for_tile(b_speed_n, raw.speed_n)

        # HR
        b_hr_s = b["hr_sum"]
        b_hr_n = b["hr_n"]
        hr_mean = np.where(b_hr_n > 0, b_hr_s / b_hr_n, 0)
        hr_norm = np.clip((hr_mean - h_lo) / h_span, 0, 1)
        hr_norm = np.where(b_hr_n > 0, hr_norm, 0)
        alpha_hr = _presence_alpha_for_tile(b_hr_n, raw.hr_n)

        # Gradient (absolute)
        b_grad_s = b["grad_sum"]
        b_grad_n = b["grad_n"]
        grad_mean = np.where(b_grad_n > 0, b_grad_s / b_grad_n, 0)
        grad_norm = np.clip((grad_mean - g_lo) / g_span, 0, 1)
        grad_norm = np.where(b_grad_n > 0, grad_norm, 0)
        # Quadratic falloff: flat pixels fade to near-zero alpha; steep
        # pixels stay fully saturated.
        alpha_grad = _presence_alpha_for_tile(b_grad_n, raw.grad_n) * grad_norm**2

        # Gradient change (signed)
        b_elev_s = b["elev_sum"]
        b_elev_n = b["elev_n"]
        elev_mean = np.where(b_elev_n > 0, b_elev_s / b_elev_n, 0)
        elev_norm = np.clip(elev_mean / max(stats.elev_abs_hi, 1e-6), -1, 1)
        elev_norm = np.where(b_elev_n > 0, elev_norm, 0)
        # Up-vs-down layer: fade pixels that are roughly flat (|elev_norm| ≈ 0)
        # so only segments with a clear direction stand out.
        directionality = np.abs(elev_norm)  # 0 at flat, 1 at the percentile extremes
        alpha_elev = _presence_alpha_for_tile(b_elev_n, raw.elev_n) * (0.05 + 0.95 * directionality)

        # Recency (max-filtered, viridis on normalised date_max).
        # Gamma > 1 compresses older dates into the dark end so the recent
        # half of the date span gets more of the colormap.
        r_date = _max_filter_tile_attr(tiles, tx, ty, "date_max", date_filter_size, margin)
        date_norm_raw = np.clip((r_date - d_lo) / d_span, 0, 1)
        date_norm = date_norm_raw**g_recency
        # alpha: presence of any activity (reuse blurred count) — recency
        # only meaningful where the heatmap has data
        alpha_recency = _presence_alpha_for_tile(b_count, raw.count) * (r_date > 0)

        # Freshness 3 mo. Last 14 days clamp high so brand-new runs stand out.
        b_recent_3 = b["recent_count_3mo"]
        fresh3_norm = np.log1p(b_recent_3) / np.log1p(max(stats.recent_count_3mo_max, 1e-9))
        fresh3_norm = np.clip(fresh3_norm, 0, 1)
        recent_2w_cutoff = (_date.today() - _date(1970, 1, 1)).days - RECENCY_WINDOW_DAYS_2W
        fresh3_norm = np.where(r_date >= recent_2w_cutoff, np.maximum(fresh3_norm, 0.72), fresh3_norm)

        # Freshness 12 mo (blurred recent_count, log scale)
        b_recent = b["recent_count"]
        fresh_norm = np.log1p(b_recent) / np.log1p(max(stats.recent_count_max, 1e-9))
        fresh_norm = np.clip(fresh_norm, 0, 1)

        # Freshness 36 mo
        b_recent_36 = b["recent_count_36mo"]
        fresh36_norm = np.log1p(b_recent_36) / np.log1p(max(stats.recent_count_36mo_max, 1e-9))
        fresh36_norm = np.clip(fresh36_norm, 0, 1)

        # Hill training: per-pixel mean ascent, presence-alpha (same shape as
        # Gradient absolute, just coloured and with a slightly bigger blur so
        # parallel tracks within a few metres merge into one line).
        bh = _blur_tile_channels(tiles, tx, ty, _HILL_BLUR_ATTRS, hill_sigma, hill_margin)
        b_elev_gain_s_h = bh["elev_gain_sum"]
        b_elev_n_h = bh["elev_n"]
        gain_mean = np.where(b_elev_n_h > 0, b_elev_gain_s_h / b_elev_n_h, 0)
        gain_norm = np.clip(gain_mean / max(stats.elev_gain_hi, 1e-6), 0, 1)
        gain_norm = np.where(b_elev_n_h > 0, gain_norm, 0)
        # Quadratic falloff: low-gain pixels go nearly transparent so hill
        # spots stand out cleanly.
        alpha_hill = _presence_alpha_for_tile(b_elev_n_h, raw.elev_n) * gain_norm**2

        # Colour-map and save
        layer_imgs = {
            "count": _to_rgba_u8(top_routes_norm, CMAP_COUNT),
            "count_log": _to_rgba_u8(clog, CMAP_COUNT),
            "speed": _to_rgba_u8(speed_norm, CMAP_SPEED, alpha_speed),
            "hr": _to_rgba_u8(hr_norm, CMAP_HR, alpha_hr),
            "grad": _solid_color_alpha_u8(_GRAD_COLOR_RGB, alpha_grad),
            "elev": _to_rgba_u8((elev_norm + 1) / 2, CMAP_ELEV, alpha_elev),
            "hill": _to_rgba_u8(gain_norm, CMAP_HILL, alpha_hill),
            "recency": _to_rgba_u8(date_norm, CMAP_RECENCY, alpha_recency),
            "freshness_3mo": _to_rgba_u8(fresh3_norm, CMAP_COUNT),
            "freshness": _to_rgba_u8(fresh_norm, CMAP_COUNT),
            "freshness_36mo": _to_rgba_u8(fresh36_norm, CMAP_COUNT),
        }

        for layer_name, img in layer_imgs.items():
            if img[..., 3].max() == 0:
                continue
            out_dir = output_root / layer_name / str(zoom) / str(tx)
            out_dir.mkdir(parents=True, exist_ok=True)
            Image.fromarray(img, mode="RGBA").save(out_dir / f"{ty}.png", compress_level=3)
            saved += 1
    return saved


# --------------------------------------------------------------------------- #
# Pyramid driver
# --------------------------------------------------------------------------- #


@dataclass
class PyramidResult:
    tiles_dir: Path
    min_zoom: int
    max_zoom: int
    bounds_latlon: list[list[float]]
    centre_latlon: list[float]
    speed_range: tuple[float, float]
    hr_range: tuple[float, float]
    grad_range: tuple[float, float]
    count_max: float
    elev_gain_hi: float
    date_range_days: tuple[float, float]
    recent_count_3mo_max: float
    recent_count_max: float
    recent_count_36mo_max: float
    input_fingerprint: str | None = None


def _occupied_bbox_latlon(
    tiles: SparseTiles,
    zoom: int,
) -> tuple[list[list[float]], list[float]]:
    """Return ([[south, west], [north, east]], [centre_lat, centre_lon])."""
    txs = [k[0] for k in tiles]
    tys = [k[1] for k in tiles]
    tx_min, tx_max = min(txs), max(txs)
    ty_min, ty_max = min(tys), max(tys)
    lat_nw, lon_nw = global_px_to_lonlat(tx_min * TILE_SIZE, ty_min * TILE_SIZE, zoom)
    lat_se, lon_se = global_px_to_lonlat((tx_max + 1) * TILE_SIZE, (ty_max + 1) * TILE_SIZE, zoom)
    return [[lat_se, lon_nw], [lat_nw, lon_se]], [(lat_nw + lat_se) / 2, (lon_nw + lon_se) / 2]


def _auto_min_zoom(tiles: SparseTiles, z_max: int, target_px: int) -> int:
    """Lowest zoom where the data span on screen >= target_px.

    Stops the user zooming out further than makes sense — a continent-spanning
    dataset at z=2 is still just a small dot on the screen.
    """
    txs = [k[0] for k in tiles]
    tys = [k[1] for k in tiles]
    span_tiles = max(max(txs) - min(txs) + 1, max(tys) - min(tys) + 1)
    span_px_at_zmax = span_tiles * TILE_SIZE
    if span_px_at_zmax <= target_px:
        return z_max
    return max(0, math.ceil(z_max - math.log2(span_px_at_zmax / target_px)))


def build_pyramid(
    tracks: list[Track],
    output_dir: Path,
    config: Config,
    profile: str = "all",
    force_min_zoom: int | None = None,
    input_fingerprint: str | None = None,
) -> PyramidResult:
    """End-to-end: paint sparse → for each zoom: stats → blur → save → downsample.

    Each profile's PNGs live under `outputs/tiles/<profile>/<layer>/...` so
    multiple profiles can coexist; the viewer swaps profile via radio.

    `force_min_zoom` overrides the auto-detect heuristic — used in
    multi-profile builds so every profile reaches the same bottom zoom and
    no profile gets upscaled-from-higher-zoom when the user zooms out.
    """
    tiles_dir = output_dir / "tiles" / profile
    if tiles_dir.exists():
        shutil.rmtree(tiles_dir)
    tiles_dir.mkdir(parents=True)

    z_max = config.max_zoom
    sigma = config.blur_sigma_px
    margin = math.ceil(sigma * 3)  # 3 * sigma Gaussian footprint

    today_days = (_date.today() - _date(1970, 1, 1)).days
    recent_cutoff_3mo = today_days - RECENCY_WINDOW_DAYS_3MO
    recent_cutoff_12mo = today_days - RECENCY_WINDOW_DAYS_12MO
    recent_cutoff_36mo = today_days - RECENCY_WINDOW_DAYS_36MO

    log.info("Painting sparse z=%d (sigma=%d px, margin=%d px)…", z_max, sigma, margin)
    current_tiles = paint_tracks(
        tracks,
        z_max,
        recent_cutoff_3mo,
        recent_cutoff_12mo,
        recent_cutoff_36mo,
        config.hill_min_grade,
        altitude_smoothing_window=config.altitude_smoothing_window,
    )
    log.info("z=%d painted: %d occupied tiles", z_max, len(current_tiles))

    if force_min_zoom is not None:
        z_min = force_min_zoom
        log.info("Forced min_zoom: %d", z_min)
    elif config.min_zoom is None:
        z_min = _auto_min_zoom(current_tiles, z_max, config.min_zoom_target_px)
        log.info("Auto min_zoom: %d (data fills ≥ %d px of viewport)", z_min, config.min_zoom_target_px)
    else:
        z_min = config.min_zoom

    bounds, centre = _occupied_bbox_latlon(current_tiles, z_max)

    base_stats: ZoomStats | None = None
    for z in tqdm(range(z_max, z_min - 1, -1), desc="Zoom levels", unit="zoom"):
        if not current_tiles:
            log.warning("z=%d: no occupied tiles, stopping pyramid build", z)
            break
        stats = _compute_zoom_stats(
            current_tiles,
            config.auto_range_pct,
            sigma,
            margin,
            config.speed_min_ms,
            config.speed_max_ms,
            config.hr_min_bpm,
            config.hr_max_bpm,
        )

        if z == z_max:
            base_stats = stats
            log.info(
                "z=%d Pace range: %.2f-%.2f m/s ≈ %s - %s",
                z,
                stats.speed_range[0],
                stats.speed_range[1],
                pace_min_per_km(stats.speed_range[1]),
                pace_min_per_km(stats.speed_range[0]),
            )
            log.info("z=%d HR range: %.0f-%.0f bpm", z, stats.hr_range[0], stats.hr_range[1])
            log.info(
                "z=%d Gradient: %.1f%%-%.1f%%",
                z,
                stats.grad_range[0] * 100,
                stats.grad_range[1] * 100,
            )

        saved = _save_tile_pngs(
            current_tiles,
            z,
            sigma,
            margin,
            stats,
            tiles_dir,
            recency_gamma=config.recency_gamma,
            hill_sigma=config.hill_blur_sigma_px,
        )
        log.info("z=%2d → %d PNGs written across %d tiles", z, saved, len(current_tiles))

        if z > z_min:
            current_tiles = downsample_tiles(current_tiles)

    if base_stats is None:
        msg = "No tiles produced — check input tracks."
        raise ValueError(msg)

    result = PyramidResult(
        tiles_dir=tiles_dir,
        min_zoom=z_min,
        max_zoom=z_max,
        bounds_latlon=bounds,
        centre_latlon=centre,
        speed_range=base_stats.speed_range,
        hr_range=base_stats.hr_range,
        grad_range=base_stats.grad_range,
        count_max=base_stats.count_max,
        elev_gain_hi=base_stats.elev_gain_hi,
        date_range_days=base_stats.date_range,
        recent_count_3mo_max=base_stats.recent_count_3mo_max,
        recent_count_max=base_stats.recent_count_max,
        recent_count_36mo_max=base_stats.recent_count_36mo_max,
        input_fingerprint=input_fingerprint,
    )
    _save_pyramid_metadata(result)
    return result


_METADATA_FILENAME = "_pyramid.json"


def _save_pyramid_metadata(result: PyramidResult) -> None:
    """Persist the legend ranges + zoom limits next to the tiles.

    Lets the HTML be regenerated without re-painting (see load_pyramid_metadata).
    """
    payload = {k: v for k, v in asdict(result).items() if k != "tiles_dir"}
    (result.tiles_dir / _METADATA_FILENAME).write_text(json.dumps(payload))


def load_pyramid_metadata(tiles_dir: Path) -> PyramidResult:
    """Reconstruct a PyramidResult from the JSON sidecar saved by build_pyramid."""
    meta_path = tiles_dir / _METADATA_FILENAME
    if not meta_path.exists():
        msg = f"No pyramid metadata at {meta_path}. Run a full build first."
        raise FileNotFoundError(msg)
    payload = json.loads(meta_path.read_text())
    return PyramidResult(
        tiles_dir=tiles_dir,
        min_zoom=payload["min_zoom"],
        max_zoom=payload["max_zoom"],
        bounds_latlon=payload["bounds_latlon"],
        centre_latlon=payload["centre_latlon"],
        speed_range=tuple(payload["speed_range"]),
        hr_range=tuple(payload["hr_range"]),
        grad_range=tuple(payload["grad_range"]),
        count_max=payload["count_max"],
        elev_gain_hi=payload.get("elev_gain_hi", 1.0),
        date_range_days=tuple(payload.get("date_range_days", (0, 1))),
        recent_count_3mo_max=payload.get("recent_count_3mo_max", 1.0),
        recent_count_max=payload.get("recent_count_max", 1.0),
        recent_count_36mo_max=payload.get("recent_count_36mo_max", 1.0),
        input_fingerprint=payload.get("input_fingerprint"),
    )


__all__ = [
    "PyramidResult",
    "build_pyramid",
    "global_px_to_lonlat",
    "load_pyramid_metadata",
    "lonlat_to_global_px",
]
