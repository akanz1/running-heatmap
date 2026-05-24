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
from typing import TYPE_CHECKING

import numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter
from tqdm import tqdm

from heatmap.colormaps import CMAP_COUNT
from heatmap.colormaps import CMAP_ELEV
from heatmap.colormaps import CMAP_HR
from heatmap.colormaps import CMAP_SPEED
from heatmap.format import pace_min_per_km

if TYPE_CHECKING:
    from pathlib import Path

    import matplotlib.colors as mcolors

    from heatmap.config import Config

log = logging.getLogger(__name__)

TILE_SIZE = 256
MIN_SEGMENT_DIST_M = 0.5
PRESENCE_PCT = 10

# Channels stored per tile. Order matches the SparseTile fields.
_CHANNELS = ("count", "speed_sum", "speed_n", "hr_sum", "hr_n", "grad_sum", "grad_n", "elev_sum", "elev_n")


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
    """A single tile's raw accumulator channels (just the 256x256 core)."""

    count: np.ndarray
    speed_sum: np.ndarray
    speed_n: np.ndarray
    hr_sum: np.ndarray
    hr_n: np.ndarray
    grad_sum: np.ndarray
    grad_n: np.ndarray
    elev_sum: np.ndarray
    elev_n: np.ndarray

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


def _paint_point(tiles: SparseTiles, gx: int, gy: int) -> None:
    tx, ty = gx // TILE_SIZE, gy // TILE_SIZE
    lx, ly = gx - tx * TILE_SIZE, gy - ty * TILE_SIZE
    key = (tx, ty)
    tile = tiles.get(key)
    if tile is None:
        tile = SparseTile.empty()
        tiles[key] = tile
    tile.count[ly, lx] += 1


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
) -> None:
    dx, dy = x2 - x1, y2 - y1
    n_steps = max(int(max(abs(dx), abs(dy))) + 1, 1)
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


def paint_tracks(tracks: list[tuple[str, list[list]]], zoom: int) -> SparseTiles:
    """Paint all tracks into sparse z=max tiles."""
    tiles: SparseTiles = {}

    for _label, pts in tqdm(tracks, desc=f"Painting z={zoom}", unit="track"):
        if not pts:
            continue
        lats = np.array([p[0] for p in pts])
        lons = np.array([p[1] for p in pts])
        gxs, gys = lonlat_to_global_px_array(lats, lons, zoom)

        # Per-point: count.
        for i in range(len(pts)):
            _paint_point(tiles, round(gxs[i]), round(gys[i]))

        if len(pts) < 2:  # noqa: PLR2004
            continue

        # Per-segment: speed/hr/grad/elev.
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


def _blur_tile_attr(
    tiles: SparseTiles,
    tx: int,
    ty: int,
    attr: str,
    sigma: float,
    margin: int,
) -> np.ndarray:
    """Blur this tile with neighbour context, return centre TILE_SIZE x TILE_SIZE."""
    assembled = _assemble_with_neighbors(tiles, tx, ty, attr, margin)
    blurred = gaussian_filter(assembled, sigma=sigma)
    return blurred[margin : margin + TILE_SIZE, margin : margin + TILE_SIZE]


# --------------------------------------------------------------------------- #
# Downsample to next zoom
# --------------------------------------------------------------------------- #


def _downsample_quadrant(arr: np.ndarray) -> np.ndarray:
    """256x256 → 128x128 by 2x2 sum."""
    return arr.reshape(128, 2, 128, 2).sum(axis=(1, 3))


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
    speed_means: list[np.ndarray] = []
    hr_means: list[np.ndarray] = []
    grad_means: list[np.ndarray] = []
    elev_means: list[np.ndarray] = []

    # One pass: blur each tile's count to find the true post-blur max, and
    # collect raw per-pixel means for percentile ranges. Raw means are a
    # close approximation to blurred means at the percentile boundaries.
    for (tx, ty), tile in tiles.items():
        b_count = _blur_tile_attr(tiles, tx, ty, "count", sigma, margin)
        if b_count.max() > blurred_count_max:
            blurred_count_max = float(b_count.max())

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

    return ZoomStats(
        count_max=count_max,
        speed_range=(s_lo, s_hi),
        hr_range=(h_lo, h_hi),
        grad_range=(g_lo, g_hi),
        elev_abs_hi=elev_abs_hi,
    )


# --------------------------------------------------------------------------- #
# Per-tile RGBA assembly + save
# --------------------------------------------------------------------------- #


def _to_rgba_u8(norm: np.ndarray, cmap: mcolors.LinearSegmentedColormap, alpha: np.ndarray | None = None) -> np.ndarray:
    rgba = cmap(norm).copy()
    if alpha is not None:
        rgba[:, :, 3] = alpha
    return (rgba * 255).clip(0, 255).astype(np.uint8)


def _white_alpha_u8(alpha: np.ndarray) -> np.ndarray:
    h, w = alpha.shape
    arr = np.zeros((h, w, 4), dtype=np.uint8)
    arr[:, :, :3] = 255
    arr[:, :, 3] = (alpha * 255).clip(0, 255).astype(np.uint8)
    return arr


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
) -> int:
    """Blur, normalize, colour-map, and save PNGs for every tile at this zoom."""
    s_lo, s_hi = stats.speed_range
    h_lo, h_hi = stats.hr_range
    g_lo, g_hi = stats.grad_range
    s_span = max(s_hi - s_lo, 1e-6)
    h_span = max(h_hi - h_lo, 1e-6)
    g_span = max(g_hi - g_lo, 1e-6)

    saved = 0
    iter_tiles = list(tiles.keys())
    for tx, ty in tqdm(iter_tiles, desc=f"Saving z={zoom}", unit="tile", leave=False):
        raw = tiles[(tx, ty)]

        # Frequency layers
        b_count = _blur_tile_attr(tiles, tx, ty, "count", sigma, margin)
        if b_count.max() == 0 and raw.count.max() == 0:
            continue
        cnorm = b_count / max(stats.count_max, 1e-9)
        cnorm_clip = np.clip(cnorm, 0, 1)
        clog = np.log1p(b_count) / np.log1p(max(stats.count_max, 1e-9))
        clog = np.clip(clog, 0, 1)

        # Speed
        b_speed_s = _blur_tile_attr(tiles, tx, ty, "speed_sum", sigma, margin)
        b_speed_n = _blur_tile_attr(tiles, tx, ty, "speed_n", sigma, margin)
        speed_mean = np.where(b_speed_n > 0, b_speed_s / b_speed_n, 0)
        speed_norm = np.clip((speed_mean - s_lo) / s_span, 0, 1)
        speed_norm = np.where(b_speed_n > 0, speed_norm, 0)
        alpha_speed = _presence_alpha_for_tile(b_speed_n, raw.speed_n)

        # HR
        b_hr_s = _blur_tile_attr(tiles, tx, ty, "hr_sum", sigma, margin)
        b_hr_n = _blur_tile_attr(tiles, tx, ty, "hr_n", sigma, margin)
        hr_mean = np.where(b_hr_n > 0, b_hr_s / b_hr_n, 0)
        hr_norm = np.clip((hr_mean - h_lo) / h_span, 0, 1)
        hr_norm = np.where(b_hr_n > 0, hr_norm, 0)
        alpha_hr = _presence_alpha_for_tile(b_hr_n, raw.hr_n)

        # Gradient (absolute)
        b_grad_s = _blur_tile_attr(tiles, tx, ty, "grad_sum", sigma, margin)
        b_grad_n = _blur_tile_attr(tiles, tx, ty, "grad_n", sigma, margin)
        grad_mean = np.where(b_grad_n > 0, b_grad_s / b_grad_n, 0)
        grad_norm = np.clip((grad_mean - g_lo) / g_span, 0, 1)
        grad_norm = np.where(b_grad_n > 0, grad_norm, 0)
        alpha_grad = _presence_alpha_for_tile(b_grad_n, raw.grad_n) * (0.15 + 0.85 * grad_norm)

        # Gradient change (signed)
        b_elev_s = _blur_tile_attr(tiles, tx, ty, "elev_sum", sigma, margin)
        b_elev_n = _blur_tile_attr(tiles, tx, ty, "elev_n", sigma, margin)
        elev_mean = np.where(b_elev_n > 0, b_elev_s / b_elev_n, 0)
        elev_norm = np.clip(elev_mean / max(stats.elev_abs_hi, 1e-6), -1, 1)
        elev_norm = np.where(b_elev_n > 0, elev_norm, 0)
        alpha_elev = _presence_alpha_for_tile(b_elev_n, raw.elev_n)

        # Colour-map and save
        layer_imgs = {
            "count": _to_rgba_u8(cnorm_clip, CMAP_COUNT),
            "count_log": _to_rgba_u8(clog, CMAP_COUNT),
            "speed": _to_rgba_u8(speed_norm, CMAP_SPEED, alpha_speed),
            "hr": _to_rgba_u8(hr_norm, CMAP_HR, alpha_hr),
            "grad": _white_alpha_u8(alpha_grad),
            "elev": _to_rgba_u8((elev_norm + 1) / 2, CMAP_ELEV, alpha_elev),
        }

        for layer_name, img in layer_imgs.items():
            if img[..., 3].max() == 0:
                continue
            out_dir = output_root / layer_name / str(zoom) / str(tx)
            out_dir.mkdir(parents=True, exist_ok=True)
            Image.fromarray(img, mode="RGBA").save(out_dir / f"{ty}.png")
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
    tracks: list[tuple[str, list[list]]],
    output_dir: Path,
    config: Config,
) -> PyramidResult:
    """End-to-end: paint sparse → for each zoom: stats → blur → save → downsample."""
    tiles_dir = output_dir / "tiles"
    if tiles_dir.exists():
        log.info("Wiping existing tiles dir: %s", tiles_dir)
        shutil.rmtree(tiles_dir)
    tiles_dir.mkdir(parents=True)

    z_max = config.max_zoom
    sigma = config.blur_sigma_px
    margin = math.ceil(sigma * 3)  # 3 * sigma Gaussian footprint

    log.info("Painting sparse z=%d (sigma=%d px, margin=%d px)…", z_max, sigma, margin)
    current_tiles = paint_tracks(tracks, z_max)
    log.info("z=%d painted: %d occupied tiles", z_max, len(current_tiles))

    if config.min_zoom is None:
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

        saved = _save_tile_pngs(current_tiles, z, sigma, margin, stats, tiles_dir)
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
    )


__all__ = [
    "PyramidResult",
    "build_pyramid",
    "global_px_to_lonlat",
    "load_pyramid_metadata",
    "lonlat_to_global_px",
]
