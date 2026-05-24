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
    "Aktivitätsdatum": "Activity Date",
    "Name der Aktivität": "Activity Name",
    "Aktivitätsart": "Activity Type",
    "Dateiname": "Filename",
    # English passes through unchanged but listed for clarity
    "Activity Date": "Activity Date",
    "Activity Name": "Activity Name",
    "Activity Type": "Activity Type",
    "Filename": "Filename",
}

# Localized → canonical activity-type values
ACTIVITY_TYPE_ALIASES: dict[str, str] = {
    # German
    "Lauf": "Run",
    "Radfahrt": "Ride",
    "Virtuelle Radfahrt": "Virtual Ride",
    "Schwimmen": "Swim",
    "Wandern": "Hike",
    "Spaziergang": "Walk",
    "Gewichtstraining": "Weight Training",
    "Yoga": "Yoga",
    "Training": "Workout",
}

REQUIRED_COLUMNS = ["Activity Date", "Activity Name", "Activity Type", "Filename"]


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
