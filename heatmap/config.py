from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Optional


@dataclass
class Config:
    # Activity source — None triggers auto-detection
    activities_dir: Optional[str] = None
    activity_types: list[str] = field(default_factory=lambda: ["Run"])

    # Date filter (inclusive); None = unbounded
    date_from: Optional[str] = None
    date_to: Optional[str] = None

    # Home location; None = auto-detected from most common start point
    home_lat: Optional[float] = None
    home_lon: Optional[float] = None
    radius_km: float = 15.0

    # Exclude indoor / treadmill activities
    gps_spread_min_m: float = 200.0

    # Raster
    meters_per_pixel: int = 3
    padding_m: int = 500
    track_clip_radius_km: Optional[float] = 12.0

    # Rendering
    blur_sigma_px: int = 10
    map_opacity: float = 0.85

    # Colour range — None = auto (percentile clipped)
    speed_min_ms: Optional[float] = None
    speed_max_ms: Optional[float] = None
    hr_min_bpm: Optional[float] = None
    hr_max_bpm: Optional[float] = None
    auto_range_pct: int = 5

    # Paths
    track_cache: str = "cache/track_cache.json"
    output_html: str = "outputs/heatmap.html"

    def resolved_activities_dir(self) -> str:
        """Return the first candidate path that contains activities.csv."""
        candidates = [
            Path(self.activities_dir) if self.activities_dir else None,
            Path("strava_export"),
            Path.home() / "Downloads" / "strava_export",
        ]
        resolved = next(
            (
                p
                for p in candidates
                if p is not None and (p / "activities.csv").exists()
            ),
            None,
        )
        if resolved is None:
            raise FileNotFoundError(
                "Export folder not found. Set activities_dir to its path.\n"
                "Searched: ./strava_export, ~/Downloads/strava_export"
            )
        return str(resolved)
