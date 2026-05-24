from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
from pyproj import Transformer
from scipy.ndimage import gaussian_filter

if TYPE_CHECKING:
    from heatmap.config import Config

log = logging.getLogger(__name__)

# Minimum segment distance (in projected metres) before computing gradient.
# Below this the rise/run ratio is dominated by GPS noise.
MIN_SEGMENT_DIST_M = 0.5

# Presence-mask saturation percentile — lower = brighter, more saturated alpha
PRESENCE_PCT = 10


# --------------------------------------------------------------------------- #
# Data containers
# --------------------------------------------------------------------------- #


@dataclass
class RasterGrids:
    """Raw value grids accumulated from GPS samples, in Web Mercator pixel space."""

    count_grid: np.ndarray
    speed_sum: np.ndarray
    speed_n: np.ndarray
    hr_sum: np.ndarray
    hr_n: np.ndarray
    grad_sum: np.ndarray
    grad_n: np.ndarray
    elev_sum: np.ndarray
    elev_n: np.ndarray
    x_min_wm: float
    x_max_wm: float
    y_min_wm: float
    y_max_wm: float

    @classmethod
    def empty(cls, grid_w: int, grid_h: int, bounds: tuple[float, float, float, float]) -> RasterGrids:
        z = lambda: np.zeros((grid_h, grid_w), dtype=np.float32)  # noqa: E731
        x_min, x_max, y_min, y_max = bounds
        return cls(
            count_grid=z(),
            speed_sum=z(),
            speed_n=z(),
            hr_sum=z(),
            hr_n=z(),
            grad_sum=z(),
            grad_n=z(),
            elev_sum=z(),
            elev_n=z(),
            x_min_wm=x_min,
            x_max_wm=x_max,
            y_min_wm=y_min,
            y_max_wm=y_max,
        )


@dataclass
class NormalizedLayers:
    count_norm: np.ndarray
    count_log_norm: np.ndarray
    speed_norm: np.ndarray
    alpha_speed: np.ndarray
    hr_norm: np.ndarray
    alpha_hr: np.ndarray
    grad_norm: np.ndarray
    alpha_grad: np.ndarray
    elev_norm: np.ndarray
    alpha_elev: np.ndarray
    speed_range: tuple[float, float]
    hr_range: tuple[float, float]
    grad_range: tuple[float, float]
    count_max: float


# --------------------------------------------------------------------------- #
# Projections
# --------------------------------------------------------------------------- #


@dataclass
class Projections:
    """Bundle of pyproj transformers anchored to a home point."""

    to_wm: Transformer
    from_wm: Transformer
    to_utm: Transformer
    utm_crs: str

    @classmethod
    def for_home(cls, home_lat: float, home_lon: float) -> Projections:
        to_wm = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
        from_wm = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
        utm_zone = int((home_lon + 180) / 6) + 1
        utm_base = 32700 if home_lat < 0 else 32600
        utm_crs = f"EPSG:{utm_base + utm_zone}"
        to_utm = Transformer.from_crs("EPSG:4326", utm_crs, always_xy=True)
        return cls(to_wm=to_wm, from_wm=from_wm, to_utm=to_utm, utm_crs=utm_crs)


# --------------------------------------------------------------------------- #
# Rasterization
# --------------------------------------------------------------------------- #


def _compute_grid_bounds(
    tracks: list[tuple[str, list[list]]],
    proj: Projections,
    home_x_utm: float,
    home_y_utm: float,
    clip_m: float | None,
    padding_m: int,
) -> tuple[float, float, float, float]:
    if clip_m is None:
        all_lats = np.array([p[0] for _, pts in tracks for p in pts])
        all_lons = np.array([p[1] for _, pts in tracks for p in pts])
        xs, ys = proj.to_wm.transform(all_lons, all_lats)
        log.info("Grid from raw GPS extents (no clip radius set)")
        return xs.min() - padding_m, xs.max() + padding_m, ys.min() - padding_m, ys.max() + padding_m

    clipped_xs, clipped_ys = [], []
    for _, pts in tracks:
        lats_a = np.array([p[0] for p in pts])
        lons_a = np.array([p[1] for p in pts])
        xs_utm, ys_utm = proj.to_utm.transform(lons_a, lats_a)
        mask = ((xs_utm - home_x_utm) ** 2 + (ys_utm - home_y_utm) ** 2) <= clip_m**2
        if mask.any():
            xs_wm, ys_wm = proj.to_wm.transform(lons_a[mask], lats_a[mask])
            clipped_xs.extend(xs_wm.tolist())
            clipped_ys.extend(ys_wm.tolist())
    log.info("Grid from clipped GPS extents (clip radius: %.1f km)", clip_m / 1000)
    return (
        min(clipped_xs) - padding_m,
        max(clipped_xs) + padding_m,
        min(clipped_ys) - padding_m,
        max(clipped_ys) + padding_m,
    )


def _paint_segment(
    grids: RasterGrids,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    speed_val: float | None,
    hr_val: float | None,
    grad_val: float | None,
    elev_val: float | None,
) -> None:
    dx, dy = x2 - x1, y2 - y1
    n_steps = max(int(max(abs(dx), abs(dy))) + 1, 1)
    h, w = grids.speed_sum.shape
    for i in range(n_steps + 1):
        t = i / n_steps
        xi = round(x1 + t * dx)
        yi = round(y1 + t * dy)
        if not (0 <= xi < w and 0 <= yi < h):
            continue
        if speed_val is not None:
            grids.speed_sum[yi, xi] += speed_val
            grids.speed_n[yi, xi] += 1
        if hr_val is not None:
            grids.hr_sum[yi, xi] += hr_val
            grids.hr_n[yi, xi] += 1
        if grad_val is not None:
            grids.grad_sum[yi, xi] += grad_val
            grids.grad_n[yi, xi] += 1
        if elev_val is not None:
            grids.elev_sum[yi, xi] += elev_val
            grids.elev_n[yi, xi] += 1


def _segment_metrics(
    p0: list,
    p1: list,
    x0_utm: float,
    y0_utm: float,
    x1_utm: float,
    y1_utm: float,
) -> tuple[float | None, float | None, float | None, float | None]:
    """Return (speed, hr, abs_gradient, signed_elev_change) for a segment."""
    s0, s1 = p0[2], p1[2]
    h0, h1 = p0[3], p1[3]
    a0, a1 = p0[4], p1[4]

    seg_speed = (s0 + s1) / 2 if s0 is not None and s1 is not None else (s0 if s0 is not None else s1)
    seg_hr = (h0 + h1) / 2 if h0 is not None and h1 is not None else (h0 if h0 is not None else h1)

    if a0 is None or a1 is None:
        return seg_speed, seg_hr, None, None

    d_dist = math.sqrt((x1_utm - x0_utm) ** 2 + (y1_utm - y0_utm) ** 2)
    if d_dist < MIN_SEGMENT_DIST_M:
        return seg_speed, seg_hr, None, None

    return seg_speed, seg_hr, abs(a1 - a0) / d_dist, a1 - a0


def _paint_track(
    grids: RasterGrids,
    pts: list[list],
    proj: Projections,
    home_x_utm: float,
    home_y_utm: float,
    clip_m: float | None,
    meters_per_pixel: int,
    grid_w: int,
    grid_h: int,
) -> None:
    lats_a = np.array([p[0] for p in pts])
    lons_a = np.array([p[1] for p in pts])
    xs_utm, ys_utm = proj.to_utm.transform(lons_a, lats_a)
    xs_wm, ys_wm = proj.to_wm.transform(lons_a, lats_a)

    if clip_m is not None:
        mask = ((xs_utm - home_x_utm) ** 2 + (ys_utm - home_y_utm) ** 2) <= clip_m**2
        if not mask.any():
            return
        pts = [pts[i] for i in range(len(pts)) if mask[i]]
        xs_utm, ys_utm, xs_wm, ys_wm = xs_utm[mask], ys_utm[mask], xs_wm[mask], ys_wm[mask]

    px = (xs_wm - grids.x_min_wm) / meters_per_pixel
    py = (grids.y_max_wm - ys_wm) / meters_per_pixel

    for i in range(len(pts)):
        xi, yi = round(px[i]), round(py[i])
        if 0 <= xi < grid_w and 0 <= yi < grid_h:
            grids.count_grid[yi, xi] += 1

    for i in range(len(pts) - 1):
        speed, hr, grad, elev = _segment_metrics(
            pts[i],
            pts[i + 1],
            xs_utm[i],
            ys_utm[i],
            xs_utm[i + 1],
            ys_utm[i + 1],
        )
        _paint_segment(grids, px[i], py[i], px[i + 1], py[i + 1], speed, hr, grad, elev)


def rasterize(
    tracks: list[tuple[str, list[list]]],
    home_lat: float,
    home_lon: float,
    config: Config,
) -> RasterGrids:
    """Paint GPS tracks onto value grids in Web Mercator pixel space."""
    proj = Projections.for_home(home_lat, home_lon)
    log.info("Rasterising in EPSG:3857; clip check via %s", proj.utm_crs)

    home_x_utm, home_y_utm = proj.to_utm.transform(home_lon, home_lat)
    clip_m = config.track_clip_radius_km * 1000 if config.track_clip_radius_km is not None else None

    x_min_wm, x_max_wm, y_min_wm, y_max_wm = _compute_grid_bounds(
        tracks,
        proj,
        home_x_utm,
        home_y_utm,
        clip_m,
        config.padding_m,
    )
    grid_w = int((x_max_wm - x_min_wm) / config.meters_per_pixel) + 1
    grid_h = int((y_max_wm - y_min_wm) / config.meters_per_pixel) + 1
    log.info("Grid: %d x %d px at %d Mercator-m/px", grid_w, grid_h, config.meters_per_pixel)

    grids = RasterGrids.empty(grid_w, grid_h, (x_min_wm, x_max_wm, y_min_wm, y_max_wm))

    for _label, pts in tracks:
        _paint_track(
            grids,
            pts,
            proj,
            home_x_utm,
            home_y_utm,
            clip_m,
            config.meters_per_pixel,
            grid_w,
            grid_h,
        )

    log.info(
        "Count grid — max GPS pts/px: %d, non-zero: %s",
        int(grids.count_grid.max()),
        f"{int((grids.count_grid > 0).sum()):,}",
    )
    log.info("Speed data — %s pixels", f"{int((grids.speed_n > 0).sum()):,}")
    log.info("HR data    — %s pixels", f"{int((grids.hr_n > 0).sum()):,}")
    log.info("Gradient   — %s pixels", f"{int((grids.grad_n > 0).sum()):,}")
    log.info("Elev change— %s pixels", f"{int((grids.elev_n > 0).sum()):,}")
    return grids


# --------------------------------------------------------------------------- #
# Normalisation
# --------------------------------------------------------------------------- #


def _presence_alpha(grid: np.ndarray, blur_sigma: int) -> np.ndarray:
    binary = (grid > 0).astype(np.float32)
    blurred = gaussian_filter(binary, sigma=blur_sigma)
    present = blurred[binary > 0]
    sat = float(np.percentile(present, PRESENCE_PCT)) if present.size else 0.0
    return np.clip(blurred / sat, 0, 1) if sat > 0 else blurred


def _autorange(values: np.ndarray, pct: int) -> tuple[float, float]:
    return float(np.percentile(values, pct)), float(np.percentile(values, 100 - pct))


def _mean(value_sum: np.ndarray, n: np.ndarray) -> np.ndarray:
    return np.where(n > 0, value_sum / n, 0)


def _normalize_count(count_grid: np.ndarray, sigma: int) -> tuple[np.ndarray, np.ndarray, float]:
    b_count = gaussian_filter(count_grid, sigma=sigma)
    return b_count / b_count.max(), np.log1p(b_count) / np.log1p(b_count.max()), float(count_grid.max())


def _normalize_speed(
    speed_sum: np.ndarray,
    speed_n: np.ndarray,
    sigma: int,
    lo: float | None,
    hi: float | None,
    pct: int,
) -> tuple[np.ndarray, tuple[float, float]]:
    b_sum = gaussian_filter(speed_sum, sigma=sigma)
    b_n = gaussian_filter(speed_n, sigma=sigma)
    mean = _mean(b_sum, b_n)
    visited = mean[b_n > 0.01]

    if not visited.size:
        log.info("Pace: no speed data")
        return np.zeros_like(mean), (1.0, 5.0)

    auto_lo, auto_hi = _autorange(visited, pct)
    s_lo = lo if lo is not None else auto_lo
    s_hi = hi if hi is not None else auto_hi

    norm = np.clip((mean - s_lo) / (s_hi - s_lo), 0, 1)
    norm = np.where(b_n > 0, norm, 0)
    # Re-blur to soften hard edges between visited / unvisited pixels
    weight = (b_n > 0.01).astype(float)
    norm = np.where(
        gaussian_filter(weight, sigma=sigma) > 0,
        gaussian_filter(norm * weight, sigma=sigma) / np.maximum(gaussian_filter(weight, sigma=sigma), 1e-9),
        0,
    )
    log.info(
        "Pace range: %.2f-%.2f m/s  ≈ %d-%d sec/km",
        s_lo,
        s_hi,
        1000 / s_hi,
        1000 / s_lo,
    )
    return norm, (s_lo, s_hi)


def _normalize_hr(
    hr_sum: np.ndarray,
    hr_n: np.ndarray,
    sigma: int,
    lo: float | None,
    hi: float | None,
    pct: int,
) -> tuple[np.ndarray, tuple[float, float]]:
    b_sum = gaussian_filter(hr_sum, sigma=sigma)
    b_n = gaussian_filter(hr_n, sigma=sigma)
    mean = _mean(b_sum, b_n)
    # NB: gating on raw hr_n (not blurred) here matches original behavior
    visited = mean[hr_n > 0]

    if not visited.size:
        log.info("HR: no heart rate data")
        return np.zeros_like(mean), (100.0, 180.0)

    auto_lo, auto_hi = _autorange(visited, pct)
    h_lo = lo if lo is not None else auto_lo
    h_hi = hi if hi is not None else auto_hi

    norm = np.clip((mean - h_lo) / (h_hi - h_lo), 0, 1)
    norm = np.where(b_n > 0, norm, 0)
    weight = (hr_n > 0).astype(float)
    blurred_weight = gaussian_filter(weight, sigma=sigma)
    norm = np.where(
        blurred_weight > 0,
        gaussian_filter(norm * weight, sigma=sigma) / np.maximum(blurred_weight, 1e-9),
        0,
    )
    log.info("HR range: %.0f-%.0f bpm", h_lo, h_hi)
    return norm, (h_lo, h_hi)


def _normalize_gradient(
    grad_sum: np.ndarray,
    grad_n: np.ndarray,
    raw_n: np.ndarray,
    sigma: int,
    pct: int,
) -> tuple[np.ndarray, tuple[float, float]]:
    b_sum = gaussian_filter(grad_sum, sigma=sigma)
    b_n = gaussian_filter(grad_n, sigma=sigma)
    mean = _mean(b_sum, b_n)
    visited = mean[b_n > 0.01]

    if not (raw_n > 0).any() or not visited.size:
        log.info("Gradient: no altitude data")
        return np.zeros_like(mean), (0.0, 0.0)

    g_lo, g_hi = _autorange(visited, pct)
    norm = np.where(b_n > 0, np.clip((mean - g_lo) / (g_hi - g_lo), 0, 1), 0)
    log.info("Gradient: %.1f%%-%.1f%%", g_lo * 100, g_hi * 100)
    return norm, (g_lo, g_hi)


def _normalize_elev(
    elev_sum: np.ndarray,
    elev_n: np.ndarray,
    raw_n: np.ndarray,
    sigma: int,
    pct: int,
) -> np.ndarray:
    b_sum = gaussian_filter(elev_sum, sigma=sigma)
    b_n = gaussian_filter(elev_n, sigma=sigma)
    mean = _mean(b_sum, b_n)

    if not (raw_n > 0).any():
        log.info("Elev change: no altitude data")
        return np.zeros_like(mean)

    visited = mean[b_n > 0.01]
    abs_hi = max(
        abs(float(np.percentile(visited, pct))),
        abs(float(np.percentile(visited, 100 - pct))),
    )
    norm = np.clip(mean / abs_hi, -1, 1)
    norm = np.where(b_n > 0, norm, 0)
    weight = (b_n > 0.01).astype(float)
    blurred_weight = gaussian_filter(weight, sigma=sigma)
    norm = np.where(
        blurred_weight > 0,
        gaussian_filter(norm * weight, sigma=sigma) / np.maximum(blurred_weight, 1e-9),
        0,
    )
    log.info("Elev change: ±%.1f%%", abs_hi * 100)
    return norm


def blur_and_normalize(grids: RasterGrids, config: Config) -> NormalizedLayers:
    sigma = config.blur_sigma_px
    pct = config.auto_range_pct

    count_norm, count_log_norm, count_max = _normalize_count(grids.count_grid, sigma)
    speed_norm, speed_range = _normalize_speed(
        grids.speed_sum,
        grids.speed_n,
        sigma,
        config.speed_min_ms,
        config.speed_max_ms,
        pct,
    )
    hr_norm, hr_range = _normalize_hr(
        grids.hr_sum,
        grids.hr_n,
        sigma,
        config.hr_min_bpm,
        config.hr_max_bpm,
        pct,
    )
    grad_norm, grad_range = _normalize_gradient(
        grids.grad_sum,
        grids.grad_n,
        grids.grad_n,
        sigma,
        pct,
    )
    elev_norm = _normalize_elev(grids.elev_sum, grids.elev_n, grids.elev_n, sigma, pct)

    alpha_speed = _presence_alpha(grids.speed_n, sigma)
    alpha_hr = _presence_alpha(grids.hr_n, sigma)
    alpha_grad = (
        _presence_alpha(grids.grad_n, sigma) * (0.15 + 0.85 * grad_norm)
        if (grids.grad_n > 0).any()
        else np.zeros_like(grad_norm)
    )
    alpha_elev = _presence_alpha(grids.elev_n, sigma) if (grids.elev_n > 0).any() else np.zeros_like(elev_norm)

    return NormalizedLayers(
        count_norm=count_norm,
        count_log_norm=count_log_norm,
        speed_norm=speed_norm,
        alpha_speed=alpha_speed,
        hr_norm=hr_norm,
        alpha_hr=alpha_hr,
        grad_norm=grad_norm,
        alpha_grad=alpha_grad,
        elev_norm=elev_norm,
        alpha_elev=alpha_elev,
        speed_range=speed_range,
        hr_range=hr_range,
        grad_range=grad_range,
        count_max=count_max,
    )
