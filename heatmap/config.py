from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from enum import StrEnum
from pathlib import Path

# Project root = parent of the `heatmap/` package directory.
# Anchoring paths here means `make run` works regardless of CWD.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ACTIVITIES_DIR = PROJECT_ROOT / "strava_export"
DEFAULT_CACHE_DIR = PROJECT_ROOT / "cache"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "outputs"

# Cap on output grid dimension (width or height in pixels). When the requested
# `meters_per_pixel` would produce a grid bigger than this, we bump m/px upward
# automatically. Prevents accidental gigabyte HTML files at world scale.
MAX_GRID_DIMENSION = 8192


class ActivityType(StrEnum):
    """Canonical Strava activity types after localization → English.

    Add more here as Strava introduces them. Values must match what Strava
    writes to activities.csv (in English) or what `localization.py` translates to.
    """

    RUN = "Run"
    TRAIL_RUN = "Trail Run"
    RIDE = "Ride"
    VIRTUAL_RIDE = "Virtual Ride"
    MOUNTAIN_BIKE_RIDE = "Mountain Bike Ride"
    GRAVEL_RIDE = "Gravel Ride"
    HIKE = "Hike"
    WALK = "Walk"
    SWIM = "Swim"
    ROW = "Rowing"
    SKI_ALPINE = "Alpine Ski"
    SKI_NORDIC = "Nordic Ski"
    SNOWBOARD = "Snowboard"
    KAYAK = "Kayaking"
    WEIGHT_TRAINING = "Weight Training"
    YOGA = "Yoga"
    WORKOUT = "Workout"


@dataclass
class Config:
    # --- Activity selection -------------------------------------------------
    # Path to your unzipped Strava export. None = <project_root>/strava_export.
    activities_dir: str | None = None

    # Which activity types to include. Use the ActivityType enum or raw strings.
    activity_types: list[str] = field(default_factory=lambda: [ActivityType.RUN])

    # --- Date filter (inclusive); None = unbounded --------------------------
    date_from: str | None = None
    date_to: str | None = None

    # --- Geographic filter --------------------------------------------------
    # Manual home center. None = auto-detected from densest start-point cluster.
    # Only used when radius_km or track_clip_radius_km is set.
    home_lat: float | None = None
    home_lon: float | None = None

    # Drop activities that START further than this from home. None = worldwide.
    radius_km: float | None = None

    # Drop individual GPS points further than this from home.
    # Useful to cap the output extent. None = no clipping.
    track_clip_radius_km: float | None = None

    # --- Treadmill / indoor filter ------------------------------------------
    gps_spread_min_m: float = 200.0

    # --- Raster resolution & padding ----------------------------------------
    # Web Mercator metres per pixel. Auto-bumped if grid would exceed
    # MAX_GRID_DIMENSION.
    meters_per_pixel: int = 3
    padding_m: int = 500

    # --- Rendering ----------------------------------------------------------
    blur_sigma_px: int = 10
    map_opacity: float = 0.85

    # --- Colour range — None = auto (percentile clipped) --------------------
    speed_min_ms: float | None = None
    speed_max_ms: float | None = None
    hr_min_bpm: float | None = None
    hr_max_bpm: float | None = None
    auto_range_pct: int = 5

    # --- Path helpers -------------------------------------------------------

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

    # --- Derived predicates -------------------------------------------------

    def needs_home(self) -> bool:
        """True if any setting needs a home reference point."""
        return (
            self.radius_km is not None
            or self.track_clip_radius_km is not None
            or (self.home_lat is not None and self.home_lon is not None)
        )
