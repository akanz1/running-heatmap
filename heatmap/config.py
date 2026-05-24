from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from pathlib import Path

# Project root = parent of the `heatmap/` package directory.
# Anchoring paths here means `make run` works regardless of CWD.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ACTIVITIES_DIR = PROJECT_ROOT / "strava_export"
DEFAULT_CACHE_DIR = PROJECT_ROOT / "cache"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "outputs"


@dataclass
class Config:
    # Activity source — None uses DEFAULT_ACTIVITIES_DIR
    activities_dir: str | None = None
    activity_types: list[str] = field(default_factory=lambda: ["Run"])

    # Date filter (inclusive); None = unbounded
    date_from: str | None = None
    date_to: str | None = None

    # Home location; None = auto-detected from most common start point
    home_lat: float | None = None
    home_lon: float | None = None
    radius_km: float = 15.0

    # Exclude indoor / treadmill activities
    gps_spread_min_m: float = 200.0

    # Raster
    meters_per_pixel: int = 3
    padding_m: int = 500
    track_clip_radius_km: float | None = 12.0

    # Rendering
    blur_sigma_px: int = 10
    map_opacity: float = 0.85

    # Colour range — None = auto (percentile clipped)
    speed_min_ms: float | None = None
    speed_max_ms: float | None = None
    hr_min_bpm: float | None = None
    hr_max_bpm: float | None = None
    auto_range_pct: int = 5

    def resolved_activities_dir(self) -> Path:
        """Return the export folder, validating that activities.csv exists."""
        path = Path(self.activities_dir) if self.activities_dir else DEFAULT_ACTIVITIES_DIR
        if not (path / "activities.csv").exists():
            msg = f"activities.csv not found in {path}. Set activities_dir in Config to override the default."
            raise FileNotFoundError(msg)
        return path

    def track_cache_path(self) -> Path:
        return DEFAULT_CACHE_DIR / "track_cache.json"

    def output_html_path(self) -> Path:
        return DEFAULT_OUTPUT_DIR / "heatmap.html"
