# Running Heatmap

Code from [this video](https://youtu.be/PA8d4u5T4BM?si=83GTMI449kCsgb4B) — shared by request.

Turns a Strava data export into an interactive heatmap. No API needed - just the zip file Strava lets you download.

The output is a single HTML file plus a tile pyramid (`outputs/tiles/{layer}/{z}/{x}/{y}.png`) with six layers you can switch between:

| Layer | Colour | Shows |
|---|---|---|
| Frequency (linear) | Orange | How often you've run each path |
| Frequency (log) | Orange | Same, log scale - better when a few paths dominate |
| Pace (average) | Blue | Average pace - brighter = faster |
| Heart rate (average) | Red | Average HR - brighter = higher |
| Gradient (absolute) | White | Steepness - brighter = steeper |
| Gradient (change) | Green / purple | Direction - green = descending, purple = ascending |

The map renders sharply at every zoom from continent view (z=2) down to street level (z=14), with Leaflet upscaling beyond that.

## Setup

Requires [uv](https://docs.astral.sh/uv/getting-started/installation/).

```
make setup
```

| Command | What it does |
|---|---|
| `make setup` | Create `.venv/` and install deps |
| `make update` | Upgrade all deps to latest versions |
| `make sync` | Sync new intervals.icu activities into `cache/intervals_icu/` (no-op without an API key) |
| `make run` | Generate `outputs/heatmap.html` + tile pyramid (runs `sync` first) |
| `make serve` | Serve `outputs/` on `http://localhost:8000` |
| `make lint` | Run `ruff check` |
| `make format` | Run `ruff check --fix` + `ruff format` |
| `make clean` | Delete the venv |

### Optional: intervals.icu sync

Fills gaps in your Strava export with activities only on [intervals.icu](https://intervals.icu) (e.g. Garmin uploads that never went to Strava). Skip if you don't need it — the pipeline works fine from `strava_export/` alone.

1. Copy `.env.example` to `.env`.
2. Set `INTERVALS_ICU_API_KEY` and `INTERVALS_ICU_ATHLETE_ID` from <https://intervals.icu/settings> (Developer section).
3. `make sync` to populate `cache/intervals_icu/`, or `make run` to sync + render in one step.

Strava-sourced activities on intervals are skipped automatically (their files aren't served by the API). Across-source duplicates are deduped on `(day, start coords, distance)`.

`HEATMAP_SKIP_SYNC=1 make run` or `Config(sync_enabled=False)` disables the sync step for offline / CI runs.

## Usage

1. Request your data from Strava: **Settings → My Account → Download or Delete Your Account → Download Request**
2. Unzip the export. By default the script looks for `<project_root>/strava_export/`. To use a different location, set `activities_dir` in the `Config`.
3. Edit `main.py` — the defaults render a **worldwide** map of every run in your export. Override fields as needed:

```python
from heatmap.config import ActivityType, Config

config = Config(
    activities_dir=None,            # None = <project_root>/strava_export
    activity_types=[ActivityType.RUN],
    date_from=None,                 # None = unbounded
    date_to=None,                   # None = today
    home_lat=None,                  # None = auto-detect (only when needed)
    home_lon=None,
    radius_km=None,                 # None = no activity-level filter
    track_clip_radius_km=None,      # None = keep every GPS point
)
```

4. `make run` — heatmap, tiles and HTML are written to `outputs/`.
5. `make serve` — start a local HTTP server (tile-based maps can't be opened directly from `file://` in modern browsers; the server is required).
6. Open `http://localhost:8000/heatmap.html`.

### Recipes

```python
# Worldwide, all activities ever — slow on first run (parses every track file),
# fast on subsequent runs (uses the cache).
Config()

# Just this year's runs near home, auto-detected.
Config(date_from="2026-01-01", radius_km=15.0, track_clip_radius_km=12.0)

# Multiple activity types, fixed home, last 5 years.
Config(
    activity_types=[ActivityType.RUN, ActivityType.RIDE, ActivityType.HIKE],
    date_from="2021-01-01",
    home_lat=48.99, home_lon=8.45, radius_km=50.0,
)
```

## Project layout

```
main.py                entry point — edit Config here
heatmap/
├── __init__.py        run() pipeline + configure_logging()
├── config.py          Config dataclass, ActivityType enum, path resolution
├── constants.py       math constants + colormap node definitions
├── localization.py    map non-English column names + activity types to English
├── activities.py      CSV load, GPS start, home detect, filter
├── parsers.py         FIT / GPX / TCX track-file parsers
├── tracks.py          per-activity track loader + on-disk cache
├── tiles.py           sparse tile pyramid: paint, downsample, blur, save
├── colormaps.py       Matplotlib colormaps
├── format.py          pace formatting helpers
├── legend.py          HTML legend assembly
├── assets.py          static CSS + JS strings injected into the map
└── render.py          Folium map assembly + save
```

### Config reference

Only the headline fields are exposed in `main.py`. The full list of tunables (defaults shown):

| Field | Default | Notes |
|---|---|---|
| `activities_dir` | `None` | None ⇒ `<project_root>/strava_export` |
| `activity_types` | `[ActivityType.RUN]` | Mix any types from the `ActivityType` enum |
| `date_from` / `date_to` | `None` / `None` | `YYYY-MM-DD` strings; None = unbounded |
| `home_lat` / `home_lon` | `None` / `None` | Auto-detected when any home-aware filter is set |
| `radius_km` | `None` | Activity-level filter |
| `track_clip_radius_km` | `None` | Point-level filter / output extent cap |
| `gps_spread_min_m` | `200.0` | Treadmill filter |
| `min_zoom` / `max_zoom` | `None` / `17` | Tile pyramid range. `None` ⇒ auto-fit data to ≥640 px viewport |
| `min_zoom_target_px` | `640` | Auto-min-zoom heuristic: viewport width data should fill |
| `max_grid_dim` | `8192` | Safety cap — unused by the sparse renderer in normal use |
| `padding_m` | `500` | Real-world metres padding around tracks (mostly a safety buffer) |
| `blur_sigma_px` | `2` | Gaussian "glow" in pixels, applied at every zoom (≈ 2.4 m radius @ z=17) |
| `map_opacity` | `0.85` | Heat layer opacity over basemap |
| `speed_min_ms` / `speed_max_ms` | `None` / `None` | Fix pace colormap range; None = auto-percentile |
| `hr_min_bpm` / `hr_max_bpm` | `None` / `None` | Same for HR |
| `auto_range_pct` | `5` | Percentile clip for auto-ranges |

### How the tile pyramid works

For each zoom level from `max_zoom` down to `min_zoom`:

1. **Paint** (at `max_zoom` only): walk every GPS point, route it into one of ~250–2000 sparse 256×256 tiles based on its global pixel coords. Memory scales with the **number of occupied tiles**, not the data's bounding box, so multi-continent datasets are fine.
2. **Stats**: compute global ranges for pace / HR / gradient and the actual blurred frequency maximum across every tile.
3. **Blur + normalise + colour-map + save** each tile, pulling neighbours into a temporary buffer so edges don't fade.
4. **Downsample 2×2 sum** to build the next-lower zoom's sparse dict, then repeat.

Per-zoom stats mean each level autonomously uses its full dynamic range — a continent-wide z=4 view stays visually distinct from a city-level z=17 view.

The viewer clamps user navigation to exactly `[pyramid.min_zoom, pyramid.max_zoom]` — no upscaling (would look mushy past native z=17, since GPS accuracy is around 1–3 m ≈ z=17 anyway) and no downscaling past the level where data fits the screen.

### Iterating on the HTML only

`make run-html-only` reuses the existing `outputs/tiles/` (and its `_pyramid.json` sidecar) and just regenerates `outputs/heatmap.html`. Takes ~1 s instead of the full ~4 min, useful when tweaking `render.py`, `legend.py`, or `assets.py`. Falls back gracefully — re-run `make run` first if the sidecar is missing or the data changed.

### Logging

Every module logs through `logging.getLogger(__name__)`. `configure_logging()` (called from `main.py` or automatically from `run()`) sets up a single root handler. Bump to `logging.DEBUG` for more detail.

### Non-English exports

Strava exports column names and activity types in the user's account language. `localization.py` translates them to canonical English so the rest of the code stays language-agnostic. German is included; add other locales to `COLUMN_ALIASES` / `ACTIVITY_TYPE_ALIASES` as needed.

### Home detection

Home is auto-detected from the most common activity start point in the date range, then only activities within `radius_km` of that point are included. It's a heuristic — if you started more runs from somewhere else (work, a club) than home in that period, that location wins. Override it with `home_lat` / `home_lon` if needed.

### Supported track formats

Strava exports a mix of formats depending on activity age and the recording device:

| Format | Source | Fields recovered |
|---|---|---|
| `.fit.gz` | Modern Garmin / most devices | lat, lon, speed, HR, altitude |
| `.gpx.gz` / `.gpx` | Older Strava activities, manual uploads | lat, lon, speed (derived from timestamps), HR (if present), altitude |
| `.tcx.gz` | Garmin Training Center format | lat, lon, speed (derived), HR, altitude |

For GPX and TCX, speed isn't a native field, so it's derived from consecutive timestamps using Haversine distance. Outliers (>15 m/s) are dropped.

### Caching

Parsing track files is slow so per-file data is cached. The export folder gets a `_gps_cache.json` (start points only) and the project root gets a `cache/track_cache.json` (full tracks). Changing the date range or config won't re-parse files already in either cache.

Tiles themselves are **not** cached — the `outputs/tiles/` directory is wiped and rebuilt every run. Tile rebuilding is the new dominant cost (~30–60 s for ~500 tracks) but is parallelisable later if needed.

---

## Notes

### The frequency map measures time on path, not number of passes

GPS records at ~1 Hz, so the frequency layers count GPS samples per pixel rather than distinct activities. A slower run deposits more points on the same path than a faster one. In practice this means the map shows something closer to time spent on each road than how many times you've run it - which is arguably more useful, but worth knowing.

The log scale version exists because a few favourite routes tend to dominate completely on a linear scale, washing out everything else.

### Pace and HR are all-time averages

Each pixel is the mean across every activity that ever crossed it. A route you used to run slowly but now run fast will show somewhere in the middle. Narrow the date range if you want a specific period.

The HR layer in particular shows **pixel-averaged** HR — a single hard effort gets averaged out by many easier visits to the same pixel, so the visual max is typically well below your actual peak HR. The log output reports raw HR percentiles so you can sanity-check.

### The gradient layers are only as good as GPS altitude

GPS altitude is much noisier than horizontal position - typically ±10–20 m vertically versus ±3–5 m horizontally. The gradient layers are reliable on hilly terrain but can look noisy on flat routes where the signal-to-noise is poor.

### Coordinate systems

All raster work happens in Web Mercator (EPSG:3857) pixel space, the same coordinate system the basemap tiles use, so no reprojection is needed at render time. Real-world distances (clip radius, segment lengths for gradient) use the Haversine formula directly on lat/lon — accurate everywhere on the globe without picking a UTM zone.
