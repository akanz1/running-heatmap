from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from heatmap import run
from heatmap.layer_panel import LAYERS
from heatmap.layer_panel import build_layer_panel_html
from heatmap.routes import save_routes
from heatmap.stats_panel import StatsPanelData
from heatmap.stats_panel import build_stats_panel_html
from heatmap.config import Config
from heatmap.tiles import build_pyramid
from heatmap.tracks import Track
from heatmap.tracks import load_tracks
from main import config


class ProductChangeTests(unittest.TestCase):
    def test_routes_only_refresh_skips_tile_builder(self) -> None:
        test_config = Config(activity_type_profiles={"all": []})

        with (
            patch.dict("os.environ", {"HEATMAP_ROUTES_ONLY": "1"}, clear=True),
            patch("heatmap._refresh_routes") as refresh_routes,
            patch("heatmap._render_cached_outputs", return_value="outputs/heatmap.html") as render,
            patch("heatmap.build_pyramid") as tile_builder,
        ):
            result = run(test_config)

        refresh_routes.assert_called_once_with(test_config, test_config.resolved_profiles())
        render.assert_called_once_with(test_config, test_config.resolved_profiles())
        tile_builder.assert_not_called()
        self.assertEqual(result, "outputs/heatmap.html")

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

    def test_route_export_simplifies_geometry_and_keeps_metadata(self) -> None:
        track = Track(
            activity_id="strava-1",
            activity_type="Run",
            label="2026-06-17 Morning Run",
            date_days=20_000,
            distance_m=float("nan"),
            moving_time_s=300,
            elevation_gain_m=20,
            points=[
                [49.0, 8.0, 3.0, 140, 100.0],
                [49.0005, 8.0005, 3.0, 140, 102.0],
                [49.001, 8.001, 3.0, 140, 105.0],
            ],
        )

        with tempfile.TemporaryDirectory() as tmp:
            path = save_routes([track], Path(tmp), input_fingerprint="abc")
            payload = json.loads(path.read_text())

        self.assertEqual(payload["input_fingerprint"], "abc")
        self.assertIn("generated_at", payload)
        self.assertEqual(payload["raw_point_count"], 3)
        self.assertEqual(payload["point_count"], 2)
        self.assertEqual(payload["activities"][0]["id"], "strava-1")
        self.assertEqual(payload["activities"][0]["type"], "Run")
        self.assertIsNone(payload["activities"][0]["distance_m"])
        self.assertEqual(payload["activities"][0]["points"], [[49.0, 8.0], [49.001, 8.001]])

    def test_layer_panel_includes_heatmap_and_routes_modes(self) -> None:
        html = build_layer_panel_html(
            ["all", "runs", "trail_runs", "hikes"],
            "all",
            heatmap_updated_at={"all": "2026-07-11T10:00:00+00:00"},
        )

        self.assertIn('name="map-mode" value="heatmap"', html)
        self.assertIn('name="map-mode" value="routes" checked', html)
        self.assertIn('<div id="view-updated-at">Updated —</div>', html)
        self.assertIn('"all": "2026-07-11T10:00:00+00:00"', html)
        self.assertIn("routesUpdatedAt = payload.generated_at", html)
        self.assertIn("setInterval(updateLastUpdated, 60000)", html)
        self.assertIn('modeInput ? modeInput.value : "routes"', html)
        self.assertIn('name="route-type" value="Run" checked', html)
        self.assertIn('name="route-type" value="Trail Run" checked', html)
        self.assertIn('name="route-type" value="Hike" checked', html)
        self.assertIn('<div class="group-title">Metrics</div>', html)
        self.assertIn('<span>Pace</span>', html)
        self.assertIn('<span>Heart rate</span>', html)
        self.assertIn('<span>Uphill / downhill</span>', html)
        self.assertNotIn('<span>Average</span>', html)
        self.assertIn('fetch("routes.json")', html)
        self.assertIn("L.canvas({padding: 0.5, tolerance: 6})", html)
        self.assertIn("zoom <= 3 ? 8.0", html)
        self.assertIn("function routeGlowStyle(activityType)", html)
        self.assertIn("zoom <= 3 ? 24", html)
        self.assertIn("routesMaxZoom = heatmapMaxZoom + 1", html)
        self.assertIn("bindTooltip", html)
        self.assertIn("function kilometerPoints(activity)", html)
        self.assertIn("markerStepKm", html)
        self.assertIn('className: "km-marker"', html)
        self.assertIn('window.addEventListener("activityfilterschange"', html)
        self.assertNotIn("__ROUTE_TYPE_META__", html)

    def test_stats_panel_exposes_route_filter_state(self) -> None:
        data = StatsPanelData(
            activities=[{"d": 20_000, "km": 1000, "s": 300, "el": 20}],
            date_min_days=20_000,
            date_max_days=20_000,
            dist_max_km=1,
        )

        html = build_stats_panel_html({"all": data}, "all")

        self.assertIn("window.__statsPanelSetRouteActivities__", html)
        self.assertIn("window.__statsPanelGetFilters__", html)
        self.assertIn('new CustomEvent("activityfilterschange"', html)


if __name__ == "__main__":
    unittest.main()
