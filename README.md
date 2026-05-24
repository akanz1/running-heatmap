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
| `make clean` | Delete the venv |

## Usage

1. Request your data from Strava: **Settings → My Account → Download or Delete Your Account → Download Request**
2. Unzip the export anywhere on your machine
3. The script auto-detects the export in this order:
   - `./strava_export/` next to `main.py`
   - `~/Downloads/strava_export/`
   - A path you set explicitly: `activities_dir="/path/to/your/export"`
4. Edit `main.py` to set the date range and any other options:

```python
from heatmap import run
from heatmap.config import Config

config = Config(
    # activities_dir=None,        # None = auto-detect
    # activity_types=["Run"],     # Run, Ride, Hike, Walk, ...
    date_from="2026-01-01",
    # date_to=None,               # None = today
    # home_lat=None,              # None = auto-detect
    # home_lon=None,
    # radius_km=15.0,
)

if __name__ == "__main__":
    run(config)
```

5. `make run` — map is saved to `outputs/heatmap.html`.

## Project layout

```
main.py                entry point — edit Config here
heatmap/
├── __init__.py        run() pipeline
├── config.py          Config dataclass + path auto-detection
├── constants.py       math constants + colormap node lists
├── activities.py      CSV load, GPS start, home detect, filter
├── tracks.py          FIT parsing + on-disk cache
├── raster.py          rasterize + blur/normalise
├── colormaps.py       Matplotlib colormaps
└── render.py          Folium map + legend + save
```

### Home detection

Home is auto-detected from the most common activity start point in the date range, then only activities within `radius_km` of that point are included. It's a heuristic — if you started more runs from somewhere else (work, a club) than home in that period, that location wins. Override it with `home_lat` / `home_lon` if needed.

### Caching

Parsing `.fit.gz` files is slow so GPS data is cached after the first run. Changing the date range or config won't re-parse files you've already loaded.

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
