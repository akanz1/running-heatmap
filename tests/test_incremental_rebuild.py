from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from heatmap import _profile_input_fingerprint
from heatmap import _profiles_changed_by_types
from heatmap.config import ActivityType
from heatmap.config import Config
from heatmap.sources import intervals_icu


class ProfileChangeTests(unittest.TestCase):
    def test_changed_type_rebuilds_matching_profiles(self) -> None:
        profiles = {
            "runs": [ActivityType.RUN],
            "trail_runs": [ActivityType.TRAIL_RUN],
            "hikes": [ActivityType.HIKE],
            "all": [ActivityType.RUN, ActivityType.TRAIL_RUN, ActivityType.HIKE],
            "everything": [],
        }

        changed = _profiles_changed_by_types(profiles, frozenset({"Run"}))

        self.assertEqual(changed, {"runs", "all", "everything"})

    def test_unknown_changed_type_reuses_filtered_profiles(self) -> None:
        profiles = {
            "runs": [ActivityType.RUN],
            "hikes": [ActivityType.HIKE],
        }

        changed = _profiles_changed_by_types(profiles, frozenset({"Ride"}))

        self.assertEqual(changed, set())

    def test_empty_changed_types_rebuilds_all_profiles(self) -> None:
        profiles = {
            "runs": [ActivityType.RUN],
            "hikes": [ActivityType.HIKE],
        }

        changed = _profiles_changed_by_types(profiles, frozenset())

        self.assertEqual(changed, {"runs", "hikes"})


class SyncResultTests(unittest.TestCase):
    def test_sync_returns_canonical_types_for_downloaded_activities(self) -> None:
        class FakeClient:
            def __init__(self, athlete_id: str, api_key: str) -> None:
                pass

            def __enter__(self) -> FakeClient:
                return self

            def __exit__(self, *exc: object) -> None:
                pass

            def list_activities(self, oldest: str, newest: str) -> list[dict]:
                return [
                    {
                        "id": "i1",
                        "type": "TrailRun",
                        "name": "woods",
                        "start_date_local": "2026-06-17T07:00:00",
                        "distance": 1000,
                        "moving_time": 300,
                        "total_elevation_gain": 20,
                        "source": "GARMIN",
                    },
                    {
                        "id": "s1",
                        "type": "Run",
                        "source": "STRAVA",
                    },
                ]

            def download_fit(self, activity_id: str) -> bytes:
                return b"fit"

        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            with patch.object(intervals_icu, "IntervalsIcuClient", FakeClient):
                result = intervals_icu.sync(
                    cache_dir,
                    athlete_id="athlete",
                    api_key="key",
                    date_from="2026-06-17",
                    date_to="2026-06-17",
                )

            self.assertEqual(result.downloaded, 1)
            self.assertEqual(result.activity_types, frozenset({"Trail Run"}))
            self.assertEqual(json.loads((cache_dir / "index.json").read_text())[0]["id"], "i1")
            self.assertTrue((cache_dir / "activities" / "i1.fit").exists())


class FingerprintTests(unittest.TestCase):
    def test_profile_fingerprint_changes_with_file_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            track = Path(tmp) / "activity.fit"
            track.write_bytes(b"old")
            df = pd.DataFrame(
                [
                    {
                        "activity_id": "icu-i1",
                        "date": pd.Timestamp("2026-06-17T07:00:00"),
                        "type": "Run",
                        "distance_m": 1000,
                        "moving_time_s": 300,
                        "elevation_gain_m": 20,
                        "file_path": track,
                    }
                ]
            )

            first = _profile_input_fingerprint("runs", [ActivityType.RUN], df, Config(), forced_min_zoom=12)
            track.write_bytes(b"new-content")
            second = _profile_input_fingerprint("runs", [ActivityType.RUN], df, Config(), forced_min_zoom=12)

        self.assertNotEqual(first, second)

    def test_profile_fingerprint_changes_with_tile_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            track = Path(tmp) / "activity.fit"
            track.write_bytes(b"same")
            df = pd.DataFrame(
                [
                    {
                        "activity_id": "icu-i1",
                        "date": pd.Timestamp("2026-06-17T07:00:00"),
                        "type": "Run",
                        "distance_m": 1000,
                        "moving_time_s": 300,
                        "elevation_gain_m": 20,
                        "file_path": track,
                    }
                ]
            )

            first = _profile_input_fingerprint("runs", [ActivityType.RUN], df, Config(max_zoom=17), 12)
            second = _profile_input_fingerprint("runs", [ActivityType.RUN], df, Config(max_zoom=16), 12)

        self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
