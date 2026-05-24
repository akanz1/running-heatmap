# Running Heatmap

Code from [this video](https://youtu.be/PA8d4u5T4BM?si=83GTMI449kCsgb4B) — shared by request.

Turns a Strava data export into an interactive heatmap. No API needed - just the zip file Strava lets you download.

The output is a single HTML file with six layers you can switch between:

| Layer | Colour | Shows |
|---|---|---|
| Frequency (linear) | Orange | How often you've run each path |
| Frequency (log) | Orange | Same, log scale - better when a few paths dominate |
| Pace (average) | Blue | Average pace - brighter = faster |
| Heart rate (average) | Red | Average HR - brighter = higher |
| Gradient (absolute) | White | Steepness - brighter = steeper |
| Gradient (change) | Green / purple | Direction - green = descending, purple = ascending |

## Setup

Requires [uv](https://docs.astral.sh/uv/getting-started/installation/).

```
make setup
```

| Command | What it does |
|---|---|
| `make setup` | Create `.venv/` and install deps |
| `make update` | Upgrade all deps to latest versions |
| `make run` | Generate `outputs/heatmap.html` |
| `make lint` | Run `ruff check` |
| `make format` | Run `ruff check --fix` + `ruff format` |
| `make clean` | Delete the venv |

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

4. `make run` — map is saved to `outputs/heatmap.html`.

### Recipes

```python
# Worldwide, all activities ever — slow on first run (parses every .fit.gz),
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
├── raster.py          rasterize + blur/normalise
├── colormaps.py       Matplotlib colormaps
├── encoding.py        PNG → base64 data-URI helpers
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
| `meters_per_pixel` | `3` | Raster resolution; auto-bumped if grid would exceed `MAX_GRID_DIMENSION` (8192 px) |
| `padding_m` | `500` | Padding around GPS extent before rasterizing |
| `blur_sigma_px` | `10` | Gaussian "glow" |
| `map_opacity` | `0.85` | Heat layer opacity over basemap |
| `speed_min_ms` / `speed_max_ms` | `None` / `None` | Fix pace colormap range; None = auto-percentile |
| `hr_min_bpm` / `hr_max_bpm` | `None` / `None` | Same for HR |
| `auto_range_pct` | `5` | Percentile clip for auto-ranges |

### Worldwide mode and the grid-size cap

When `radius_km` and `track_clip_radius_km` are both `None`, the grid is computed from the raw extent of every loaded GPS point. At world scale that would be billions of pixels, so `MAX_GRID_DIMENSION` (8192) caps the largest grid dimension and `meters_per_pixel` is auto-bumped to fit. Expect tracks to look thick at low zoom but smear into single pixels at street level — this is a known limitation of single-PNG rendering. **Tile pyramid output is on the roadmap.**

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
| `.gpx.gz` / `.gpx` | Older Strava activities, manual uploads | lat, lon, HR (if present), altitude |
| `.tcx.gz` | Garmin Training Center format | lat, lon, HR, altitude |

GPX and TCX don't carry a native speed field, so pace data is only available for FIT activities. Speed-from-timestamps is a possible future improvement.

### Caching

Parsing track files is slow so per-file data is cached. The export folder gets a `_gps_cache.json` (start points only) and the project root gets a `cache/track_cache.json` (full tracks). Changing the date range or config won't re-parse files already in either cache.

---

## Notes

### The frequency map measures time on path, not number of passes

GPS records at ~1 Hz, so the frequency layers count GPS samples per pixel rather than distinct activities. A slower run deposits more points on the same path than a faster one. In practice this means the map shows something closer to time spent on each road than how many times you've run it - which is arguably more useful, but worth knowing.

The log scale version exists because a few favourite routes tend to dominate completely on a linear scale, washing out everything else.

### Pace and HR are all-time averages

Each pixel is the mean across every activity that ever crossed it. A route you used to run slowly but now run fast will show somewhere in the middle. Narrow the date range if you want a specific period.

### The gradient layers are only as good as GPS altitude

GPS altitude is much noisier than horizontal position - typically ±10–20 m vertically versus ±3–5 m horizontally. The gradient layers are reliable on hilly terrain but can look noisy on flat routes where the signal-to-noise is poor.

### Why the code uses two different projections

The raster grid is built in Web Mercator (EPSG:3857) so it aligns directly to the map tile basemap without any reprojection. But Web Mercator distorts distances at higher latitudes, so anything involving real-world metres - the clip radius around home, and the rise/run calculation for gradient - uses a local UTM projection instead. The visual output is unaffected, it just means the underlying measurements are accurate.
