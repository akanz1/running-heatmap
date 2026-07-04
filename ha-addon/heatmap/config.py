from __future__ import annotations

import json
from dataclasses import dataclass
from dataclasses import field
from enum import StrEnum
from pathlib import Path

# Project root = parent of the `heatmap/` package directory.
# Anchoring paths here means `make run` works regardless of CWD.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ACTIVITIES_DIR = PROJECT_ROOT / "strava_export"
DEFAULT_CACHE_DIR = PROJECT_ROOT / "cache"
DEFAULT_INTERVALS_ICU_CACHE_DIR = DEFAULT_CACHE_DIR / "intervals_icu"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "outputs"


def _load_overrides(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


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

    # Local cache for activities synced from intervals.icu.
    # None = <project_root>/cache/intervals_icu/.
    intervals_icu_cache_dir: str | None = None

    # If False, skip the intervals.icu sync step (offline / CI runs).
    # The sync is also skipped when INTERVALS_ICU_API_KEY is unset.
    sync_enabled: bool = True

    # Strava activity IDs to drop from the load. Use when an activity's GPS
    # is broken in Strava but you've corrected it on intervals.icu — exclude
    # the Strava row so dedup keeps the intervals.icu version. The activity
    # admin UI (`make admin`) appends to this set via the overrides file.
    excluded_strava_ids: list[str] = field(default_factory=list)

    # Same idea for intervals.icu IDs (e.g. activities you don't want on
    # the heatmap even though they're cached).
    excluded_intervals_ids: list[str] = field(default_factory=list)

    # Which activity types to include. Use the ActivityType enum or raw strings.
    activity_types: list[str] = field(default_factory=lambda: [ActivityType.RUN])

    # Per-profile activity-type sets for the multi-type viewer. Each key
    # becomes a radio in the viewer's Activity section and gets its own tile
    # pyramid under outputs/tiles/<key>/. None = single profile "all" from
    # `activity_types` (back-compat). First key is the default visible one.
    # Example:
    #     activity_type_profiles={
    #         "runs":        [ActivityType.RUN],
    #         "trail_runs":  [ActivityType.TRAIL_RUN],
    #         "hikes":       [ActivityType.HIKE],
    #     }
    activity_type_profiles: dict[str, list[str]] | None = None

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

    # --- Tile pyramid -------------------------------------------------------
    # Zoom range to pre-render. Lower z = world view, higher z = street level.
    # z=5  ≈ 5 km/px   (sub-continent, multi-city)
    # z=17 ≈ 1.2 m/px  (sidewalk-width — good GPS can resolve this)
    # min_zoom=None → auto: pick the lowest zoom where the data fills at least
    # half a 1280 px viewport. Prevents zooming out to "data is a single dot".
    # Leaflet upscales tiles beyond max_zoom (slightly fuzzy when over-zoomed).
    # Note: each z step ≈ 4x tiles + 4x peak RAM. z=17 needs ~16 GB free.
    min_zoom: int | None = None
    max_zoom: int = 17

    # Auto-min-zoom: viewport width in px the data should fill at min_zoom.
    # 640 = half of a 1280 px screen. Tweak if you want more/less zoom-out.
    min_zoom_target_px: int = 640

    # Safety cap on the z=max grid dimension (pixels). If the data's padded
    # bbox at max_zoom would exceed this, max_zoom is auto-lowered by 1 until
    # it fits — protects against gigabyte allocations for worldwide-spread data.
    max_grid_dim: int = 8192

    # Padding around the data's bbox, in real-world metres (converted to
    # pixels at max_zoom). Prevents tracks from kissing tile edges.
    padding_m: int = 500

    # Gaussian blur sigma in pixels, applied at every zoom level. Constant
    # sigma in pixel space → visually similar track thickness across zooms.
    # At z=16 (~2.4 m/px), sigma=2 ≈ 5 m blur radius, tracks ≈ 10 m wide
    # — at GPS noise floor, can't go meaningfully sharper. Bump higher for
    # a softer "glow" look at the cost of street-level detail.
    blur_sigma_px: int = 2

    # --- Rendering ----------------------------------------------------------
    map_opacity: float = 0.85

    # Recency layer skew. norm = ((date - lo) / (hi - lo)) ** recency_gamma.
    # gamma > 1 compresses older dates into the dark end and gives recent
    # dates more of the viridis range. Useful when the date range spans many
    # years but most activity is recent. 1.0 = linear.
    recency_gamma: float = 3.0

    # Per-track altitude smoothing window (odd number of points, centered
    # moving average). GPS altitude jitter is the main source of false hills;
    # smoothing each track's altitude before computing segment deltas removes
    # most of it. Affects all elevation-derived layers. 1 = no smoothing.
    altitude_smoothing_window: int = 15

    # Minimum segment grade (rise / run) to count as ascent. Filters GPS
    # altitude noise (~0.5 m per second of jitter) on flat terrain.
    # 0.025 = 2.5 %, light slopes included.
    hill_min_grade: float = 0.025

    # Blur sigma for the hill layer specifically (px at each zoom). Slightly
    # larger than the global blur_sigma_px so parallel route variants within
    # a few metres merge into one line at z=max.
    hill_blur_sigma_px: int = 4

    # --- Colour range — None = auto (percentile clipped) --------------------
    speed_min_ms: float | None = None
    speed_max_ms: float | None = None
    hr_min_bpm: float | None = None
    hr_max_bpm: float | None = None
    auto_range_pct: int = 5

    # --- Path helpers -------------------------------------------------------

    def resolved_activities_dir(self) -> Path:
        """Return the export folder. Missing dir / activities.csv is allowed —
        an intervals.icu-only setup is valid; the Strava loader returns an
        empty frame in that case.
        """
        return Path(self.activities_dir) if self.activities_dir else DEFAULT_ACTIVITIES_DIR

    def resolved_intervals_icu_cache_dir(self) -> Path:
        return Path(self.intervals_icu_cache_dir) if self.intervals_icu_cache_dir else DEFAULT_INTERVALS_ICU_CACHE_DIR

    def track_cache_path(self) -> Path:
        return DEFAULT_CACHE_DIR / "track_cache.json"

    def overrides_path(self) -> Path:
        return DEFAULT_CACHE_DIR / "heatmap_overrides.json"

    def all_excluded_strava_ids(self) -> list[str]:
        return sorted(
            {*self.excluded_strava_ids, *_load_overrides(self.overrides_path()).get("excluded_strava_ids", [])}
        )

    def all_excluded_intervals_ids(self) -> list[str]:
        return sorted(
            {*self.excluded_intervals_ids, *_load_overrides(self.overrides_path()).get("excluded_intervals_ids", [])}
        )

    def resolved_profiles(self) -> dict[str, list[str]]:
        """Normalised activity-type profiles. Always returns ≥1 entry."""
        if self.activity_type_profiles:
            return self.activity_type_profiles
        return {"all": list(self.activity_types)}

    def output_dir(self) -> Path:
        return DEFAULT_OUTPUT_DIR

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
