from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from heatmap.layer_panel import LAYERS
from heatmap.config import Config
from heatmap.tiles import build_pyramid
from heatmap.tracks import Track
from heatmap.tracks import load_tracks
from main import config


class ProductChangeTests(unittest.TestCase):
    def test_all_is_default_activity_profile(self) -> None:
        self.assertEqual(next(iter(config.resolved_profiles())), "all")

    def test_hill_training_layer_is_removed(self) -> None:
        self.assertNotIn("hill", {subdir for _, _, subdir, _, _ in LAYERS})

    def test_pyramid_build_has_no_hill_output(self) -> None:
        track = Track(
            activity_id="icu-i1",
            activity_type="Run",
            label="Run",
            date_days=20_000,
            distance_m=1000,
            moving_time_s=300,
            elevation_gain_m=20,
            points=[
                [49.0, 8.0, 3.0, 140, 100.0],
                [49.001, 8.001, 3.0, 140, 105.0],
            ],
        )

        with tempfile.TemporaryDirectory() as tmp:
            result = build_pyramid(
                [track],
                Path(tmp),
                Config(min_zoom=12, max_zoom=12, altitude_smoothing_window=1),
            )

            self.assertFalse((result.tiles_dir / "hill").exists())

    def test_tracks_keep_activity_identity_and_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            track_path = Path(tmp) / "activity.fit"
            track_path.write_bytes(b"fit")
            runs = pd.DataFrame(
                [
                    {
                        "activity_id": "icu-i1",
                        "type": "Trail Run",
                        "date": pd.Timestamp("2026-06-17T07:00:00"),
                        "name": "Woods",
                        "distance_m": 1000,
                        "moving_time_s": 300,
                        "elevation_gain_m": 20,
                        "file_path": track_path,
                    }
                ]
            )

            with patch("heatmap.tracks.parse_track", return_value=[[49.0, 8.0, 3.0, 140, 100.0]]):
                tracks = load_tracks(runs, Path(tmp) / "track-cache.json")

        self.assertEqual(tracks[0].activity_id, "icu-i1")
        self.assertEqual(tracks[0].activity_type, "Trail Run")


if __name__ == "__main__":
    unittest.main()
