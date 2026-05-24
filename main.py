import logging

from heatmap import configure_logging
from heatmap import run
from heatmap.config import Config

config = Config(
    # activities_dir=None,        # None = <project_root>/strava_export
    # activity_types=["Run"],
    date_from="2026-01-01",
    # date_to=None,               # None = today
    # home_lat=None,              # None = auto-detect from most common start point
    # home_lon=None,
    # radius_km=15.0,
    # meters_per_pixel=3,
    # blur_sigma_px=10,
)

if __name__ == "__main__":
    configure_logging(level=logging.INFO)
    run(config)
