import logging

from dotenv import load_dotenv

from heatmap import configure_logging
from heatmap import run
from heatmap.config import ActivityType
from heatmap.config import Config

load_dotenv()

config = Config(
    # Path to your unzipped Strava export folder.
    # None = <project_root>/strava_export.
    activities_dir=None,
    # Activity types to include. Use ActivityType enum members or raw strings.
    # e.g. [ActivityType.RUN, ActivityType.RIDE]
    activity_types=[ActivityType.RUN],
    # Date filter (YYYY-MM-DD strings); None = unbounded.
    date_from=None,
    date_to=None,
    # Manual home center; None = auto-detect (only when needed).
    # Override e.g. when you've moved cities and auto-detect picks the wrong cluster.
    home_lat=49.00000,
    home_lon=8.23000,
    # Drop activities that started more than this far from home.
    # None = no filter (worldwide).
    radius_km=None,
    # Drop individual GPS points further than this from home.
    # None = no clipping (worldwide).
    track_clip_radius_km=None,
)

if __name__ == "__main__":
    configure_logging(level=logging.INFO)
    run(config)
