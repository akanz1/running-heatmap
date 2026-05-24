from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from pyproj import Transformer
from scipy.ndimage import gaussian_filter

from heatmap.config import Config


@dataclass
class RasterGrids:
    count_grid: np.ndarray
    speed_sum: np.ndarray
    speed_n: np.ndarray
    hr_sum: np.ndarray
    hr_n: np.ndarray
    grad_sum: np.ndarray
    grad_n: np.ndarray
    elev_sum: np.ndarray
    elev_n: np.ndarray
    # Web Mercator bounding box of the raster
    x_min_wm: float
    x_max_wm: float
    y_min_wm: float
    y_max_wm: float


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
    # Scale metadata for legend labels
    speed_range: tuple[float, float]
    hr_range: tuple[float, float]
    grad_range: tuple[float, float]
    count_max: float


def _make_transformers(home_lat: float, home_lon: float):
    to_wm = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    from_wm = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
    utm_zone = int((home_lon + 180) / 6) + 1
    utm_base = 32700 if home_lat < 0 else 32600
    utm_crs = f"EPSG:{utm_base + utm_zone}"
    to_utm = Transformer.from_crs("EPSG:4326", utm_crs, always_xy=True)
    return to_wm, from_wm, to_utm, utm_crs


def rasterize(
    tracks: list[tuple[str, list[list]]],
    home_lat: float,
    home_lon: float,
    config: Config,
) -> RasterGrids:
    """Paint GPS tracks onto value grids in Web Mercator pixel space."""
    to_wm, _from_wm, to_utm, utm_crs = _make_transformers(home_lat, home_lon)
    print(f"Rasterising in EPSG:3857; clip check via {utm_crs}")

    home_x_utm, home_y_utm = to_utm.transform(home_lon, home_lat)
    clip_m = (
        config.track_clip_radius_km * 1000
        if config.track_clip_radius_km is not None
        else None
    )

    # Grid bounds
    if clip_m is not None:
        clipped_xs, clipped_ys = [], []
        for _, pts in tracks:
            lats_a = np.array([p[0] for p in pts])
            lons_a = np.array([p[1] for p in pts])
            xs_utm, ys_utm = to_utm.transform(lons_a, lats_a)
            mask = (
                (xs_utm - home_x_utm) ** 2 + (ys_utm - home_y_utm) ** 2
            ) <= clip_m**2
            if mask.any():
                xs_wm, ys_wm = to_wm.transform(lons_a[mask], lats_a[mask])
                clipped_xs.extend(xs_wm.tolist())
                clipped_ys.extend(ys_wm.tolist())
        x_min_wm = min(clipped_xs) - config.padding_m
        x_max_wm = max(clipped_xs) + config.padding_m
        y_min_wm = min(clipped_ys) - config.padding_m
        y_max_wm = max(clipped_ys) + config.padding_m
        print(
            f"Grid from clipped GPS extents (clip radius: {config.track_clip_radius_km} km)"
        )
    else:
        all_lats = np.array([p[0] for _, pts in tracks for p in pts])
        all_lons = np.array([p[1] for _, pts in tracks for p in pts])
        xs_wm_all, ys_wm_all = to_wm.transform(all_lons, all_lats)
        x_min_wm = xs_wm_all.min() - config.padding_m
        x_max_wm = xs_wm_all.max() + config.padding_m
        y_min_wm = ys_wm_all.min() - config.padding_m
        y_max_wm = ys_wm_all.max() + config.padding_m
        print("Grid from raw GPS extents (no clip radius set)")

    grid_w = int((x_max_wm - x_min_wm) / config.meters_per_pixel) + 1
    grid_h = int((y_max_wm - y_min_wm) / config.meters_per_pixel) + 1
    print(f"Grid: {grid_w} × {grid_h} px at {config.meters_per_pixel} Mercator-m/px")

    count_grid = np.zeros((grid_h, grid_w), dtype=np.float32)
    speed_sum = np.zeros((grid_h, grid_w), dtype=np.float32)
    speed_n = np.zeros((grid_h, grid_w), dtype=np.float32)
    hr_sum = np.zeros((grid_h, grid_w), dtype=np.float32)
    hr_n = np.zeros((grid_h, grid_w), dtype=np.float32)
    grad_sum = np.zeros((grid_h, grid_w), dtype=np.float32)
    grad_n = np.zeros((grid_h, grid_w), dtype=np.float32)
    elev_sum = np.zeros((grid_h, grid_w), dtype=np.float32)
    elev_n = np.zeros((grid_h, grid_w), dtype=np.float32)

    def _paint_segment(x1, y1, x2, y2, speed_val, hr_val, grad_val, elev_val):
        dx, dy = x2 - x1, y2 - y1
        n_steps = max(int(max(abs(dx), abs(dy))) + 1, 1)
        h, w = speed_sum.shape
        for i in range(n_steps + 1):
            t = i / n_steps
            xi = int(round(x1 + t * dx))
            yi = int(round(y1 + t * dy))
            if not (0 <= xi < w and 0 <= yi < h):
                continue
            if speed_val is not None:
                speed_sum[yi, xi] += speed_val
                speed_n[yi, xi] += 1
            if hr_val is not None:
                hr_sum[yi, xi] += hr_val
                hr_n[yi, xi] += 1
            if grad_val is not None:
                grad_sum[yi, xi] += grad_val
                grad_n[yi, xi] += 1
            if elev_val is not None:
                elev_sum[yi, xi] += elev_val
                elev_n[yi, xi] += 1

    for _label, pts in tracks:
        lats_a = np.array([p[0] for p in pts])
        lons_a = np.array([p[1] for p in pts])
        xs_utm, ys_utm = to_utm.transform(lons_a, lats_a)
        xs_wm, ys_wm = to_wm.transform(lons_a, lats_a)

        if clip_m is not None:
            mask = (
                (xs_utm - home_x_utm) ** 2 + (ys_utm - home_y_utm) ** 2
            ) <= clip_m**2
            if not mask.any():
                continue
            pts = [pts[i] for i in range(len(pts)) if mask[i]]
            xs_utm = xs_utm[mask]
            ys_utm = ys_utm[mask]
            xs_wm = xs_wm[mask]
            ys_wm = ys_wm[mask]

        px = (xs_wm - x_min_wm) / config.meters_per_pixel
        py = (y_max_wm - ys_wm) / config.meters_per_pixel

        for i in range(len(pts)):
            xi = int(round(px[i]))
            yi = int(round(py[i]))
            if 0 <= xi < grid_w and 0 <= yi < grid_h:
                count_grid[yi, xi] += 1

        for i in range(len(pts) - 1):
            s0, s1 = pts[i][2], pts[i + 1][2]
            h0, h1 = pts[i][3], pts[i + 1][3]
            a0, a1 = pts[i][4], pts[i + 1][4]

            seg_speed = (
                (s0 + s1) / 2
                if s0 is not None and s1 is not None
                else (s0 if s0 is not None else s1)
            )
            seg_hr = (
                (h0 + h1) / 2
                if h0 is not None and h1 is not None
                else (h0 if h0 is not None else h1)
            )

            if a0 is not None and a1 is not None:
                d_dist = math.sqrt(
                    (xs_utm[i + 1] - xs_utm[i]) ** 2 + (ys_utm[i + 1] - ys_utm[i]) ** 2
                )
                if d_dist >= 0.5:
                    seg_grad = abs(a1 - a0) / d_dist
                    seg_elev = a1 - a0
                else:
                    seg_grad = seg_elev = None
            else:
                seg_grad = seg_elev = None

            _paint_segment(
                px[i],
                py[i],
                px[i + 1],
                py[i + 1],
                seg_speed,
                seg_hr,
                seg_grad,
                seg_elev,
            )

    print(
        f"Count grid — max GPS pts/px: {count_grid.max():.0f}, "
        f"non-zero: {(count_grid > 0).sum():,}"
    )
    print(f"Speed data — {(speed_n > 0).sum():,} pixels")
    print(f"HR data    — {(hr_n > 0).sum():,} pixels")
    print(f"Gradient   — {(grad_n > 0).sum():,} pixels")
    print(f"Elev change — {(elev_n > 0).sum():,} pixels")

    return RasterGrids(
        count_grid=count_grid,
        speed_sum=speed_sum,
        speed_n=speed_n,
        hr_sum=hr_sum,
        hr_n=hr_n,
        grad_sum=grad_sum,
        grad_n=grad_n,
        elev_sum=elev_sum,
        elev_n=elev_n,
        x_min_wm=x_min_wm,
        x_max_wm=x_max_wm,
        y_min_wm=y_min_wm,
        y_max_wm=y_max_wm,
    )


def _presence_alpha(grid: np.ndarray, blur_sigma: int, pct: int = 10) -> np.ndarray:
    binary = (grid > 0).astype(np.float32)
    blurred = gaussian_filter(binary, sigma=blur_sigma)
    present = blurred[binary > 0]
    sat = np.percentile(present, pct) if present.size else 0
    return np.clip(blurred / sat, 0, 1) if sat > 0 else blurred


def blur_and_normalize(grids: RasterGrids, config: Config) -> NormalizedLayers:
    """Gaussian blur + per-channel normalisation. Returns scale metadata for legend."""
    sigma = config.blur_sigma_px
    pct = config.auto_range_pct

    # Frequency
    b_count = gaussian_filter(grids.count_grid, sigma=sigma)
    count_norm = b_count / b_count.max()
    count_log_norm = np.log1p(b_count) / np.log1p(b_count.max())

    # Pace (speed)
    b_speed_sum = gaussian_filter(grids.speed_sum, sigma=sigma)
    b_speed_n = gaussian_filter(grids.speed_n, sigma=sigma)
    mean_speed = np.where(b_speed_n > 0, b_speed_sum / b_speed_n, 0)
    visited_speeds = mean_speed[b_speed_n > 0.01]
    if visited_speeds.size:
        s_lo = (
            config.speed_min_ms
            if config.speed_min_ms is not None
            else float(np.percentile(visited_speeds, pct))
        )
        s_hi = (
            config.speed_max_ms
            if config.speed_max_ms is not None
            else float(np.percentile(visited_speeds, 100 - pct))
        )
        speed_norm = np.clip((mean_speed - s_lo) / (s_hi - s_lo), 0, 1)
        speed_norm = np.where(b_speed_n > 0, speed_norm, 0)
        _sw = gaussian_filter(
            speed_norm * (b_speed_n > 0.01).astype(float), sigma=sigma
        )
        _sn = gaussian_filter((b_speed_n > 0.01).astype(float), sigma=sigma)
        speed_norm = np.where(_sn > 0, _sw / _sn, 0)
        print(
            f"Pace range: {s_lo:.2f}–{s_hi:.2f} m/s  ≈ {1000 / s_hi:.0f}–{1000 / s_lo:.0f} sec/km"
        )
    else:
        s_lo, s_hi = 1.0, 5.0
        speed_norm = np.zeros_like(mean_speed)
        print("Pace: no speed data")

    # Heart rate
    b_hr_sum = gaussian_filter(grids.hr_sum, sigma=sigma)
    b_hr_n = gaussian_filter(grids.hr_n, sigma=sigma)
    mean_hr = np.where(b_hr_n > 0, b_hr_sum / b_hr_n, 0)
    visited_hrs = mean_hr[grids.hr_n > 0]
    if visited_hrs.size:
        hr_lo = (
            config.hr_min_bpm
            if config.hr_min_bpm is not None
            else float(np.percentile(visited_hrs, pct))
        )
        hr_hi = (
            config.hr_max_bpm
            if config.hr_max_bpm is not None
            else float(np.percentile(visited_hrs, 100 - pct))
        )
        hr_norm = np.clip((mean_hr - hr_lo) / (hr_hi - hr_lo), 0, 1)
        hr_norm = np.where(b_hr_n > 0, hr_norm, 0)
        _hw = gaussian_filter(hr_norm * (grids.hr_n > 0).astype(float), sigma=sigma)
        _hn = gaussian_filter((grids.hr_n > 0).astype(float), sigma=sigma)
        hr_norm = np.where(_hn > 0, _hw / _hn, 0)
        print(f"HR range: {hr_lo:.0f}–{hr_hi:.0f} bpm")
    else:
        hr_lo, hr_hi = 100.0, 180.0
        hr_norm = np.zeros_like(mean_hr)
        print("HR: no heart rate data")

    # Gradient (absolute)
    b_grad_sum = gaussian_filter(grids.grad_sum, sigma=sigma)
    b_grad_n = gaussian_filter(grids.grad_n, sigma=sigma)
    mean_grad = np.where(b_grad_n > 0, b_grad_sum / b_grad_n, 0)
    visited_grads = mean_grad[b_grad_n > 0.01]
    n_grad_px = int((grids.grad_n > 0).sum())
    if n_grad_px and visited_grads.size:
        g_lo = float(np.percentile(visited_grads, pct))
        g_hi = float(np.percentile(visited_grads, 100 - pct))
        grad_norm = np.clip((mean_grad - g_lo) / (g_hi - g_lo), 0, 1)
        grad_norm = np.where(b_grad_n > 0, grad_norm, 0)
        print(f"Gradient: {g_lo * 100:.1f}%–{g_hi * 100:.1f}%")
    else:
        grad_norm = np.zeros_like(mean_grad)
        g_lo = g_hi = 0.0
        print("Gradient: no altitude data")

    # Gradient (signed elevation change)
    b_elev_sum = gaussian_filter(grids.elev_sum, sigma=sigma)
    b_elev_n = gaussian_filter(grids.elev_n, sigma=sigma)
    mean_elev = np.where(b_elev_n > 0, b_elev_sum / b_elev_n, 0)
    n_elev_px = int((grids.elev_n > 0).sum())
    if n_elev_px:
        visited_elevs = mean_elev[b_elev_n > 0.01]
        e_abs_hi = max(
            abs(float(np.percentile(visited_elevs, pct))),
            abs(float(np.percentile(visited_elevs, 100 - pct))),
        )
        elev_norm = np.clip(mean_elev / e_abs_hi, -1, 1)
        elev_norm = np.where(b_elev_n > 0, elev_norm, 0)
        _ew = gaussian_filter(elev_norm * (b_elev_n > 0.01).astype(float), sigma=sigma)
        _en = gaussian_filter((b_elev_n > 0.01).astype(float), sigma=sigma)
        elev_norm = np.where(_en > 0, _ew / _en, 0)
        print(f"Elev change: ±{e_abs_hi * 100:.1f}%")
    else:
        elev_norm = np.zeros_like(mean_elev)
        print("Elev change: no altitude data")

    # Alpha masks
    alpha_speed = _presence_alpha(grids.speed_n, sigma)
    alpha_hr = _presence_alpha(grids.hr_n, sigma)
    _pg = (
        _presence_alpha(grids.grad_n, sigma) if n_grad_px else np.zeros_like(grad_norm)
    )
    alpha_grad = _pg * (0.15 + 0.85 * grad_norm)
    alpha_elev = (
        _presence_alpha(grids.elev_n, sigma) if n_elev_px else np.zeros_like(elev_norm)
    )

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
        speed_range=(s_lo, s_hi),
        hr_range=(hr_lo, hr_hi),
        grad_range=(g_lo, g_hi),
        count_max=float(grids.count_grid.max()),
    )
