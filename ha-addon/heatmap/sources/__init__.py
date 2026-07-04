"""Activity sources.

Each source loads its raw inputs and returns a canonical DataFrame with:

    activity_id    str   stable cross-source ID (e.g. "strava-12345", "icu-i678")
    strava_id      str | None  for cross-source dedup
    date           pd.Timestamp
    type           str   canonical English activity type
    name           str
    distance_m     float | NaN
    moving_time_s  float | NaN
    file_path      Path  absolute path to track file
    start_lat      float | NaN
    start_lon      float | NaN
    gps_spread_m   float | NaN

The merge step in activities.py concats the source DataFrames, dedups, and
applies user filters uniformly.
"""

from __future__ import annotations

CANONICAL_COLS = [
    "activity_id",
    "strava_id",
    "date",
    "type",
    "name",
    "distance_m",
    "moving_time_s",
    "elevation_gain_m",
    "file_path",
    "start_lat",
    "start_lon",
    "gps_spread_m",
]
