"""Map localized Strava export columns + activity types to canonical English.

Strava exports column names and activity-type values in the user's account language.
We translate them to a canonical English form on load so the rest of the code can
always use the same names.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd

# Localized → canonical column names. Only columns the pipeline reads.
# Add new locale entries here as needed.
COLUMN_ALIASES: dict[str, str] = {
    # German
    "Aktivitäts-ID": "Activity ID",
    "Aktivitätsdatum": "Activity Date",
    "Name der Aktivität": "Activity Name",
    "Aktivitätsart": "Activity Type",
    "Dateiname": "Filename",
    "Bewegungszeit": "Moving Time",
    "Distanz.1": "Distance",
    "Höhenzunahme": "Elevation Gain",
    # English passes through unchanged but listed for clarity
    "Activity ID": "Activity ID",
    "Activity Date": "Activity Date",
    "Activity Name": "Activity Name",
    "Activity Type": "Activity Type",
    "Filename": "Filename",
    "Moving Time": "Moving Time",
    "Distance.1": "Distance",
    "Elevation Gain": "Elevation Gain",
}

# Localized → canonical activity-type values
ACTIVITY_TYPE_ALIASES: dict[str, str] = {
    # German (Strava export)
    "Lauf": "Run",
    "Radfahrt": "Ride",
    "Virtuelle Radfahrt": "Virtual Ride",
    "Schwimmen": "Swim",
    "Wandern": "Hike",
    "Spaziergang": "Walk",
    "Gewichtstraining": "Weight Training",
    "Yoga": "Yoga",
    "Training": "Workout",
    # intervals.icu (no spaces) → canonical
    "TrailRun": "Trail Run",
    "VirtualRide": "Virtual Ride",
    "VirtualRun": "Run",
    "MountainBikeRide": "Mountain Bike Ride",
    "GravelRide": "Gravel Ride",
    "WeightTraining": "Weight Training",
    "AlpineSki": "Alpine Ski",
    "NordicSki": "Nordic Ski",
    "Kayaking": "Kayaking",
}

REQUIRED_COLUMNS = ["Activity ID", "Activity Date", "Activity Name", "Activity Type", "Filename"]


def normalize(df: pd.DataFrame) -> pd.DataFrame:
    """Rename localized columns and translate activity-type values to English.

    Idempotent: a CSV already in English passes through unchanged.
    """
    rename_map = {src: dst for src, dst in COLUMN_ALIASES.items() if src in df.columns}
    df = df.rename(columns=rename_map)

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        msg = (
            f"activities.csv is missing required columns: {missing}. "
            f"If your export is in another language, add the column names to "
            f"heatmap/localization.py::COLUMN_ALIASES."
        )
        raise KeyError(msg)

    df["Activity Type"] = df["Activity Type"].replace(ACTIVITY_TYPE_ALIASES)
    return df
