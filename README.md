# Running Heatmap

Code from [this video](https://youtu.be/PA8d4u5T4BM?si=83GTMI449kCsgb4B) — shared by request, since extended.

Turns a Strava data export (and optionally an intervals.icu API key) into an interactive heatmap. Renders sharply at every zoom from continent view down to street level. No live API needed for the base case — just the zip Strava lets you download.

Output is a static HTML file plus a pre-baked tile pyramid (`outputs/tiles/{layer}/{z}/{x}/{y}.png`). Ten heatmap layers + five basemaps + an in-browser stats panel; layer/basemap switching is instant.

## Layers

Grouped in the layer panel (top-right of the map):

| Group | Layer | Colour | Shows |
|---|---|---|---|
| Frequency | Top routes | Orange | Visit count, linear — favourite routes dominate |
| Frequency | All routes (default) | Orange | Visit count, log scale — every path stays visible |
| Pace | Average | Blue | Pixel-averaged pace; brighter = faster |
| Heart rate | Average | Red | Pixel-averaged HR; brighter = higher |
| Elevation | Steepness | Green | `|grade|` — only the steep bits show |
| Elevation | Up vs down | Green / purple | Direction; flats fade out |
| Elevation | Hill training | Navy → red | Mean ascent per visit — where you've actually climbed |
| Time | Recency | Viridis | Date of the most recent activity per pixel |
| Time | Freshness 12 mo | Orange | Visits in the last 365 days |
| Time | Freshness 36 mo | Orange | Same, 3-year window |

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
| `make run` | Generate `outputs/heatmap.html` + tile pyramid (runs `sync` first, prompts before rebuild) |
| `make run-html-only` | Re-render HTML using the existing tile pyramid (~1 s) |
| `make serve` | Serve `outputs/` on `http://localhost:8000` (heatmap viewer) |
| `make admin` | Start the activity admin UI on `http://localhost:8001` |
| `make lint` | `ruff check` |
| `make format` | `ruff check --fix` + `ruff format` |
| `make clean` | Delete the venv |

## Usage

1. Request your data from Strava: **Settings → My Account → Download or Delete Your Account → Download Request**. Unzip into `<project_root>/strava_export/`.
2. (Optional) Set up intervals.icu sync — see below.
3. Edit `main.py` if you need to filter or move home (defaults render every run worldwide).
4. `make run` — heatmap, tiles, HTML written to `outputs/`. First run with ~500 tracks takes ~10 minutes; subsequent runs reuse the parse cache.
5. `make serve` — open `http://localhost:8000/heatmap.html`.

Basemap, heatmap layer, and stats panel are all in the viewer — no rebuild needed to switch.

### Optional: intervals.icu sync

Fills gaps when an activity exists on intervals.icu but not your Strava export (e.g. Garmin Connect uploads that never went through Strava).

1. `cp .env.example .env`
2. Set `INTERVALS_ICU_API_KEY` and `INTERVALS_ICU_ATHLETE_ID` from <https://intervals.icu/settings> (Developer section).
3. `make sync` to populate `cache/intervals_icu/`, or `make run` to sync + rebuild in one step.

Strava-sourced activities on intervals are skipped automatically (their files aren't served by the API). Across-source duplicates are deduped on `(day ±1, start coords, distance ±200 m)`.

`HEATMAP_SKIP_SYNC=1 make run` or `Config(sync_enabled=False)` disables the sync step.
`HEATMAP_YES=1` auto-confirms the rebuild prompt (also auto-confirms in CI / non-TTY).

### Optional: activity admin (`make admin`)

Web UI on `http://localhost:8001` to manage which activities feed the heatmap. Useful when:

- An activity's GPS is broken in Strava but you've fixed it on intervals.icu → exclude the Strava row so the intervals version wins dedup
- You don't want a particular activity on the heatmap at all → exclude
- An intervals.icu activity changed (you edited its track) → re-import button

Features:
- Filter by name / id / source / state (kept vs excluded)
- Sort by date / name / distance / time / elevation
- Per-row link to the activity on strava.com or intervals.icu
- One-click **exclude / include** (writes `cache/heatmap_overrides.json`)
- One-click **re-import** (intervals only — evicts caches + re-downloads the FIT for that day)

Changes are picked up by the next `make run`. Excluded IDs in the JSON are merged with anything you hardcode in `Config(excluded_strava_ids=..., excluded_intervals_ids=...)`.

### Basemaps

Five providers shipped, switchable in the layer panel:

| Basemap | Source | Default? | License |
|---|---|---|---|
| Stadia Alidade Smooth Dark | stadiamaps.com | ✓ | Free for localhost; API key needed for public deployment |
| Esri World Topo | arcgisonline.com | | Free for non-commercial |
| OSM Mapnik | tile.openstreetmap.org | | Free; OSM's policy disallows heavy public use of their tile server — fine for localhost |
| Dark (DarkMatter) | basemaps.cartocdn.com | | Free |
| Stadia Alidade Smooth | stadiamaps.com | | Same caveat as Smooth Dark |

Add more by appending to `_BASEMAPS` in [heatmap/basemaps.py](heatmap/basemaps.py). The panel + JS pick them up automatically.

### Stats panel + sliders

Floating panel (bottom-left). Shows count / total km / total hours / total ascent for the selected activity window. Two dual-handle range sliders (date and distance) filter the totals live — the heatmap tiles are pre-baked so the map itself doesn't change.

For build-time filtering of the actual map, use `Config(date_from=, date_to=, activity_types=)`.

## Config reference

Edit `main.py` for the common knobs. Full list (defaults shown):

| Field | Default | Notes |
|---|---|---|
| `activities_dir` | `None` | None ⇒ `<project_root>/strava_export` |
| `intervals_icu_cache_dir` | `None` | None ⇒ `<project_root>/cache/intervals_icu` |
| `sync_enabled` | `True` | Set False to skip the intervals.icu sync step |
| `excluded_strava_ids` | `[]` | Strava IDs to drop from the load; merged with `cache/heatmap_overrides.json` (managed by `make admin`) |
| `excluded_intervals_ids` | `[]` | Same for intervals IDs |
| `activity_types` | `[RUN]` | Mix any types from `ActivityType` enum |
| `date_from` / `date_to` | `None` / `None` | `YYYY-MM-DD` strings; None = unbounded |
| `home_lat` / `home_lon` | `None` / `None` | Auto-detected when any home-aware filter is set |
| `radius_km` | `None` | Activity-level filter (distance of start from home) |
| `track_clip_radius_km` | `None` | Point-level filter (output extent cap) |
| `gps_spread_min_m` | `200.0` | Treadmill / indoor filter |
| `min_zoom` / `max_zoom` | `None` / `17` | Tile pyramid range; None auto-fits to ≥640 px viewport |
| `min_zoom_target_px` | `640` | Auto-min-zoom heuristic |
| `padding_m` | `500` | Real-world metres padding |
| `blur_sigma_px` | `2` | Per-zoom Gaussian glow (≈ 2.4 m radius at z=17) |
| `map_opacity` | `0.85` | Heat layer opacity over basemap |
| `recency_gamma` | `3.0` | Compress old dates into the dark end of the viridis ramp |
| `altitude_smoothing_window` | `15` | Per-track centered moving-average over altitudes (filters GPS jitter) |
| `hill_min_grade` | `0.025` | Minimum segment grade (2.5%) to count toward hill ascent |
| `hill_blur_sigma_px` | `4` | Bigger blur for the hill layer specifically — merges parallel route variants |
| `speed_min_ms` / `speed_max_ms` | `None` / `None` | Pace colormap range; None = auto-percentile |
| `hr_min_bpm` / `hr_max_bpm` | `None` / `None` | Same for HR |
| `auto_range_pct` | `5` | Percentile clip for auto-ranges |

### Recipes

```python
from heatmap.config import ActivityType, Config

# Default — worldwide, every Run in the export.
Config()

# This year's runs near home.
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
main.py                  entry point — edit Config here
.env / .env.example      INTERVALS_ICU_API_KEY etc.
strava_export/           your unzipped Strava export (gitignored)
cache/                   parse caches + intervals.icu cache + overrides JSON (gitignored)
outputs/                 heatmap.html + tile pyramid (gitignored)
heatmap/
├── __init__.py          run() pipeline + sync wiring + TTY prompt
├── config.py            Config dataclass, ActivityType enum
├── constants.py         math constants + colormap node definitions
├── colormaps.py         Matplotlib colormaps (incl. viridis for recency, navy→red for hill)
├── localization.py      non-English column / activity-type translations
├── activities.py        merge + dedup of strava_export + intervals.icu rows
├── parsers.py           FIT / GPX / TCX track-file parsers
├── tracks.py            Track dataclass + cached track loader
├── sources/
│   ├── strava_export.py CSV loader → canonical schema
│   └── intervals_icu.py API client, incremental sync, cache loader
├── tiles.py             sparse tile pyramid: paint, downsample, blur, save
├── basemaps.py          basemap definitions (URL, attribution, env-var gating)
├── layer_panel.py       custom grouped radio panel (replaces Folium's flat control)
├── legend.py            HTML legend rows (one per layer)
├── stats_panel.py       floating count/km/time/elev panel + dual sliders
├── render.py            Folium map assembly + save
├── admin.py             activity admin backend (list / exclude / re-import)
├── admin_server.py      stdlib http.server wiring for `make admin`
├── admin.html           admin UI page (served by admin_server)
├── format.py            pace formatting helpers
└── assets.py            (legacy, unused since the custom layer panel)
```

## How it works

### Tile pyramid

For each zoom level from `max_zoom` down to `min_zoom`:

1. **Paint** (at `max_zoom` only): walk every GPS point, route it into a sparse `dict[(tx, ty)] → SparseTile`. Memory scales with **number of occupied tiles**, not the data's bounding box — multi-continent datasets are fine.
2. **Stats**: percentile-clipped global ranges for pace / HR / gradient / elev_gain plus min/max date and blurred maxima for count + recent_count.
3. **Blur + normalise + colour-map + save** each tile, pulling neighbours into a buffer so edges don't fade.
4. **Downsample** to the next-lower zoom: 2×2 sum for accumulators, 2×2 max for `date_max`.

Per-zoom stats mean each level autonomously uses its full dynamic range — a continent-wide z=4 view stays visually distinct from a city-level z=17 view.

The viewer clamps user navigation to `[pyramid.min_zoom, pyramid.max_zoom]` — no upscaling, no downscaling past the level where data fits the screen.

### Dedup across sources

Same activity might be in both `strava_export/` and `cache/intervals_icu/`. Match key is `(day ±1, start_lat to 3 dp, start_lon to 3 dp, distance bucketed to 200 m)`. Within-source duplicates are kept (running the same route every day is 365 distinct activities, not one).

### Activity admin → overrides → build

```
cache/heatmap_overrides.json   ←  written by `make admin` (and merged with Config fields)
        │
        ▼
heatmap.activities.load_and_filter
        │
        ▼
   excludes applied
        │
        ▼
   dedup → filter → home → painter
```

### Iterating on the HTML only

`make run-html-only` reuses the existing tile pyramid (and its `_pyramid.json` + `_activities.json` sidecars) and just regenerates `outputs/heatmap.html`. Takes ~1 s instead of the full ~10 min, useful when tweaking the panel / legend / colors.

### Logging

Every module logs through `logging.getLogger(__name__)`. `configure_logging()` (called automatically from `run()`) sets up a single root handler. Bump to `logging.DEBUG` for more detail.

### Non-English exports

Strava exports column names and activity types in your account's language. `localization.py` translates them to canonical English so the rest of the code stays language-agnostic. German is included; add other locales to `COLUMN_ALIASES` / `ACTIVITY_TYPE_ALIASES` as needed.

### Home detection

Home is auto-detected from the most common activity start point in the date range, then only activities within `radius_km` of that point are kept. It's a heuristic — if you started more activities from work or a club than from home, that location wins. Override with `home_lat` / `home_lon`.

### Supported track formats

| Format | Source | Fields recovered |
|---|---|---|
| `.fit.gz` / `.fit` | Modern Garmin / most devices, intervals.icu downloads | lat, lon, speed, HR, altitude |
| `.gpx.gz` / `.gpx` | Older Strava activities, manual uploads | lat, lon, speed (derived from timestamps), HR (if present), altitude |
| `.tcx.gz` | Garmin Training Center format | lat, lon, speed (derived), HR, altitude |

For GPX and TCX, speed isn't a native field, so it's derived from consecutive timestamps using Haversine distance. Outliers (>15 m/s) are dropped.

### Caching

- `<strava_export>/_gps_cache.json` — per-Strava-file start point + GPS spread (~3 min on first run, ~0 s after)
- `cache/intervals_icu/_gps_cache.json` — same for intervals.icu activities
- `cache/track_cache.json` — full parsed track points (the dominant cost on cold rebuilds)
- `cache/intervals_icu/index.json` — synced activity metadata
- `cache/intervals_icu/activities/<id>.fit` — synced track files
- `cache/heatmap_overrides.json` — exclude lists managed by `make admin`

Tiles themselves are **not** cached — `outputs/tiles/` is wiped and rebuilt every full run. The activities JSON for the stats panel sidecar is persisted at `outputs/_activities.json` so `make run-html-only` can reuse it.

---

## Notes

### The frequency map measures time on path, not number of passes

GPS records at ~1 Hz, so frequency layers count GPS samples per pixel rather than distinct activities. A slower run deposits more points on the same path than a faster one. In practice this means the map shows something closer to time spent on each road than how many times you've run it — arguably more useful, but worth knowing.

The log-scale version exists because a few favourite routes tend to dominate completely on a linear scale, washing out everything else.

### Pace and HR are all-time averages

Each pixel is the mean across every activity that ever crossed it. A route you used to run slowly but now run fast will show somewhere in the middle. Narrow the date range if you want a specific period.

The HR layer in particular shows **pixel-averaged** HR — a single hard effort gets averaged out by many easier visits to the same pixel, so the visual max is typically well below your actual peak HR.

### The gradient layers are only as good as GPS altitude

GPS altitude is much noisier than horizontal position — typically ±10–20 m vertically versus ±3–5 m horizontally. To compensate:

- `altitude_smoothing_window` (default 15) applies a centered moving average to each track's altitude before any segment delta is computed
- `hill_min_grade` (default 2.5%) gates the hill-training accumulator so flat-terrain jitter doesn't drift into "hills"
- Hill, Steepness, and Up-vs-down layers all use a quadratic alpha falloff so weak signal fades to transparent

Even with all that, the gradient layers are reliable on hilly terrain but can look noisy on flat routes where the signal-to-noise is poor.

### Coordinate systems

All raster work happens in Web Mercator (EPSG:3857) pixel space, the same coordinate system the basemap tiles use, so no reprojection is needed at render time. Real-world distances (clip radius, segment lengths for gradient) use the Haversine formula directly on lat/lon — accurate everywhere on the globe.
