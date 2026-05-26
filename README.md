# Running Heatmap

Writeup: [akanz.de/posts/running-heatmap](https://www.akanz.de/posts/running-heatmap/). Original [video](https://youtu.be/PA8d4u5T4BM?si=83GTMI449kCsgb4B) — shared by request, since extended.

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
| `make sync` | Sync new intervals.icu activities into `cache/intervals_icu/` (no-op without an API key) |
| `make run` | Generate `outputs/heatmap.html` + tile pyramid (runs `sync` first, prompts before rebuild) |
| `make run-html-only` | Re-render HTML using the existing tile pyramid (~1 s) |
| `make serve` | Serve `outputs/` on `http://localhost:8000` (heatmap viewer) |
| `make admin` | Start the activity admin UI on `http://localhost:8001` |

## Usage

Two sources, in any combination:

- **Strava bulk export** (no live API). Request via **Settings → My Account → Download or Delete Your Account → Download Request**, unzip into `<project_root>/strava_export/`.
- **intervals.icu sync** (live, see below). Fills in activities missing from Strava, or runs as the sole source — if `strava_export/activities.csv` is absent the Strava loader is skipped and the pipeline renders the intervals.icu cache on its own.

1. Set up at least one of the two sources above.
2. (Optional) Set up intervals.icu sync — see below.
3. Edit `main.py` if you need to filter or move home (defaults render every run worldwide).
4. `make run` — heatmap, tiles, HTML written to `outputs/`. First run with ~500 tracks takes ~10 minutes; subsequent runs reuse the parse cache.
5. `make serve` — open `http://localhost:8000/heatmap.html`.

Basemap, heatmap layer, and stats panel are all in the viewer — no rebuild needed to switch.

### Optional: intervals.icu sync

Two roles:

- **Gap filler** alongside a Strava export — covers activities that exist on intervals.icu but not in your Strava bulk export (e.g. Garmin Connect uploads that never went through Strava).
- **Sole source** when you don't have or don't want a Strava export — leave `strava_export/` empty (or absent) and the pipeline runs on the intervals.icu cache only.

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

## How it works

### Tile pyramid

At `max_zoom`, every GPS point is painted into a sparse `dict[(tx, ty)] → SparseTile` — memory scales with occupied tiles, not bounding box, so multi-continent datasets are fine. Each tile is blurred (with neighbours buffered so edges don't fade), colour-mapped, and saved. Lower zooms are produced by 2×2 downsampling (sum for accumulators, max for `date_max`). Per-zoom percentile-clipped stats keep every level visually distinct from continent down to street.

The viewer clamps navigation to `[min_zoom, max_zoom]` — no upscaling, no zooming out past where data fills the screen.

### Dedup across sources

The same activity may be in both `strava_export/` and `cache/intervals_icu/`. Match key: `(day ±1, start_lat to 3 dp, start_lon to 3 dp, distance bucketed to 200 m)`. Within-source duplicates are kept (same route every day = N activities, not one).

### Iterating on the HTML only

`make run-html-only` reuses the tile pyramid and `_activities.json` sidecar, regenerating just `outputs/heatmap.html` in ~1 s — useful when tweaking the panel / legend / colours.

### Track formats

`.fit(.gz)`, `.gpx(.gz)`, `.tcx.gz`. FIT carries speed/HR/altitude natively; for GPX/TCX, speed is derived from consecutive timestamps via Haversine, outliers (>15 m/s) dropped.

### Caching

Parse caches in `<strava_export>/_gps_cache.json`, `cache/intervals_icu/_gps_cache.json`, `cache/track_cache.json` (dominant cost on cold rebuilds). Tiles are not cached — `outputs/tiles/` is wiped and rebuilt each full run.

### Non-English exports

Strava localises column names and activity types. `localization.py` maps them to canonical English. German included; add locales to `COLUMN_ALIASES` / `ACTIVITY_TYPE_ALIASES`.

### Home detection

Auto-detected as the most common start point in the date range. Heuristic — if you start more often from work, that wins. Override with `home_lat` / `home_lon`.

---

## Notes

### Frequency = time on path, not number of passes

GPS records at ~1 Hz, so frequency counts samples per pixel. A slow run deposits more points than a fast one on the same path — the map shows time on each road more than visit count. Log-scale variant exists because a few favourite routes dominate on linear.

### Pace and HR are all-time pixel averages

Each pixel = mean across every activity that crossed it. A single hard effort gets averaged out by easy visits, so visual max sits well below true peak HR. Narrow the date range for a specific period.

### Gradient layers depend on GPS altitude quality

GPS altitude is noisy (±10–20 m vs ±3–5 m horizontal). `altitude_smoothing_window` (default 15) applies a centered moving average; `hill_min_grade` (default 2.5%) gates the hill accumulator. Reliable on hilly terrain, noisy on flats.

### Coordinate systems

Raster work in Web Mercator (EPSG:3857) pixel space — same as basemap tiles, no reprojection. Real-world distances use Haversine on lat/lon directly.

---

## Origin & License

Originally forked from [moresamwilson/running-heatmap](https://github.com/moresamwilson/running-heatmap) (MIT, Copyright (c) 2026 Sam Wilson). The codebase has since been substantially rewritten and extended, but the original MIT terms are preserved verbatim in [LICENSE.upstream](LICENSE.upstream) as the license requires.

This fork is licensed under **[PolyForm Noncommercial 1.0.0](LICENSE)** — free for personal, hobby, research, and educational use; commercial use is prohibited.
