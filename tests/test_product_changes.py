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
from heatmap.parsers import _elapsed_seconds
from heatmap.parsers import _parse_gpx
from heatmap.parsers import _parse_tcx
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
                [49.0, 8.0, 3.0, 140, 100.0, 0.0, 160, 250, 20],
                [49.0005, 8.0005, 3.0, 140, 102.0, 10.0, 170, 300, 21],
                [49.001, 8.001, 3.0, 140, 105.0, 20.0, 180, 350, 22],
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
        self.assertEqual(payload["activities"][0]["elapsed_s"], [0.0, 20.0])
        self.assertEqual(len(payload["activities"][0]["progress_m"]), 2)
        self.assertEqual(payload["activities"][0]["progress_m"][0], 0.0)
        self.assertGreater(payload["activities"][0]["progress_m"][1], 100)
        self.assertEqual(payload["activities"][0]["heart_rate_bpm_seconds"], [0.0, 2800.0])
        self.assertEqual(payload["activities"][0]["heart_rate_duration_s"], [0.0, 20.0])
        self.assertEqual(payload["activities"][0]["cadence_spm_seconds"], [0.0, 3400.0])
        self.assertEqual(payload["activities"][0]["power_watt_seconds"], [0.0, 6000.0])
        self.assertEqual(payload["activities"][0]["temperature_c_seconds"], [0.0, 420.0])
        self.assertEqual(payload["activities"][0]["elevation_m"], [101.0, 103.5])
        self.assertEqual(payload["activities"][0]["elevation_gain_progress_m"], [0.0, 2.5])
        self.assertEqual(payload["activities"][0]["elevation_loss_progress_m"], [0.0, 0.0])

    def test_xml_parsers_keep_extended_metrics(self) -> None:
        gpx = """<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1"
        xmlns:x="http://www.garmin.com/xmlschemas/TrackPointExtension/v1"><trk><trkseg>
        <trkpt lat="49" lon="8"><ele>100</ele><time>2026-01-01T00:00:00Z</time><extensions>
        <x:TrackPointExtension><x:hr>140</x:hr><x:cad>85</x:cad><x:power>300</x:power>
        <x:atemp>20</x:atemp></x:TrackPointExtension></extensions></trkpt></trkseg></trk></gpx>"""
        tcx = """<TrainingCenterDatabase><Activities><Activity><Lap><Track><Trackpoint>
        <Time>2026-01-01T00:00:00Z</Time><Position><LatitudeDegrees>49</LatitudeDegrees>
        <LongitudeDegrees>8</LongitudeDegrees></Position><AltitudeMeters>100</AltitudeMeters>
        <HeartRateBpm><Value>140</Value></HeartRateBpm><Cadence>85</Cadence>
        <Extensions><TPX><Watts>300</Watts><Temperature>20</Temperature></TPX></Extensions>
        </Trackpoint></Track></Lap></Activity></Activities></TrainingCenterDatabase>"""
        with tempfile.TemporaryDirectory() as tmp:
            gpx_path = Path(tmp) / "track.gpx"
            tcx_path = Path(tmp) / "track.tcx"
            gpx_path.write_text(gpx)
            tcx_path.write_text(tcx)
            gpx_point = _parse_gpx(gpx_path)[0]
            tcx_point = _parse_tcx(tcx_path)[0]

        self.assertEqual(gpx_point[3:], [140.0, 100.0, 0.0, 170.0, 300.0, 20.0])
        self.assertEqual(tcx_point[3:], [140.0, 100.0, 0.0, 170.0, 300.0, 20.0])

    def test_elapsed_seconds_are_relative_and_keep_missing_samples(self) -> None:
        times = [
            pd.Timestamp("2026-06-17T07:00:00Z").to_pydatetime(),
            None,
            pd.Timestamp("2026-06-17T07:00:12.500Z").to_pydatetime(),
        ]

        self.assertEqual(_elapsed_seconds(times), [0.0, None, 12.5])

    def test_layer_panel_includes_heatmap_and_routes_modes(self) -> None:
        html = build_layer_panel_html(
            ["all", "runs", "trail_runs", "hikes"],
            "all",
            heatmap_updated_at={"all": "2026-07-11T10:00:00+00:00"},
        )

        self.assertIn('name="map-mode" value="heatmap"', html)
        self.assertIn('name="map-mode" value="routes" checked', html)
        self.assertLess(
            html.index('name="map-mode" value="routes"'),
            html.index('name="map-mode" value="heatmap"'),
        )
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
        self.assertIn('id="segment-draw"', html)
        self.assertIn('id="segment-results"', html)
        self.assertIn("function intersectionFraction", html)
        self.assertIn("function segmentEffort", html)
        self.assertIn("localStorage.setItem", html)
        self.assertIn('data-sort="time"', html)
        self.assertIn('data-sort="distance"', html)
        self.assertIn('data-sort="pace"', html)
        self.assertIn('data-sort="heart_rate"', html)
        self.assertIn("function formatSegmentPace", html)
        self.assertIn("average_hr_bpm", html)
        for column in ("elevation_gain", "elevation_loss", "grade", "cadence", "power", "temperature"):
            self.assertIn(f'data-sort="{column}"', html)
            self.assertIn(f'data-segment-column-toggle="{column}"', html)
        self.assertIn("running-heatmap-segment-columns", html)
        self.assertIn('id="segment-chart"', html)
        self.assertIn("function renderSegmentChart", html)
        self.assertIn("progress_m", html)
        self.assertIn("function focusRoute", html)
        self.assertIn("getBoundsZoom", html)
        self.assertIn("Math.min(routesMaxZoom, fitZoom)", html)
        self.assertNotIn("fitZoom - 1", html)
        self.assertIn("autoPan: false", html)
        self.assertIn('id="segment-chart-tooltip"', html)
        self.assertIn("function setEffortHover", html)
        self.assertIn("function setEffortSelected", html)
        self.assertIn("function showSegmentTooltip", html)
        self.assertIn("is-selected", html)
        self.assertIn("function clearRouteSelection", html)
        self.assertIn("selectedRoute && record !== selectedRoute", html)
        self.assertIn("style.opacity *= 0.2", html)
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
